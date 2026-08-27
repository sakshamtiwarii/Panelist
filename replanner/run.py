"""
Panelist — replanner CLI.

    python replanner/run.py --data ./data/primary --scenario late
    python replanner/run.py --data ./data/primary --scenario compound

Applies a disruption to an existing schedule, re-solves with a churn penalty,
and prints the diff, the notify list and the churn against the cap. Nothing is
committed unless --apply is passed.
"""

import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from replanner.replan import apply_proposal, replan  # noqa: E402
from scheduler.metrics import compute_metrics, format_metrics  # noqa: E402
from scheduler.model import SLOTS_PER_DAY_RAW  # noqa: E402


def build_scenarios(schedule, dataset):
    """Scenarios derived from the actual schedule, so they always bite.

    Picking "the busiest company" and then making it late on Day 1 is not
    enough — the busiest company may have nothing scheduled that morning, and
    the replan comes back with zero churn, which looks like a working
    minimal-churn replan but is really a no-op. Each scenario below targets
    the (entity, day) pair that the current schedule actually loads.
    """
    lateness_slots = 12  # 3 hours at 15-minute slots

    # Late arrival: the (company, day) with the most interviews that would be
    # displaced by a 3h delay.
    displaced = {}
    for a in schedule:
        if a["slot"] < lateness_slots:
            displaced[(a["company_id"], a["day"])] = displaced.get(
                (a["company_id"], a["day"]), 0
            ) + 1
    (late_company, late_day), late_hits = max(
        displaced.items(), key=lambda kv: kv[1]
    ) if displaced else ((schedule[0]["company_id"], 0), 0)

    # Panel drop: the company running the most concurrent interviews.
    concurrency = {}
    for a in schedule:
        key = a["company_id"]
        concurrency[key] = max(
            concurrency.get(key, 0), (a.get("panel") or 0) + 1
        )
    panel_company = max(concurrency, key=concurrency.get)

    # Room outage: the busiest room on the busiest day.
    room_load = {}
    for a in schedule:
        if a.get("room"):
            room_load[(a["room"], a["day"])] = room_load.get(
                (a["room"], a["day"]), 0
            ) + 1
    (busy_room, busy_day), _ = max(room_load.items(), key=lambda kv: kv[1])

    # Withdrawals: students with the most interviews still ahead of them on a
    # single day — the mid-day-offer case from guide section 11.
    per_student_day = {}
    for a in schedule:
        per_student_day.setdefault((a["student_id"], a["day"]), []).append(a)
    busy_students = sorted(
        per_student_day.items(), key=lambda kv: -len(kv[1])
    )
    (victim, victim_day), victim_items = busy_students[0]

    def withdraw_event(items):
        """Withdraw from the second interview on, so there is a real 'rest of
        the day' left to cancel."""
        starts = sorted(a["start"] for a in items)
        return starts[1] if len(starts) > 1 else starts[0]

    return {
        "late": [{
            "type": "company_late", "company_id": late_company,
            "day": late_day, "hours": 3,
        }],
        "panel": [{
            "type": "panel_drop", "company_id": panel_company, "count": 1,
            "from_slot": 48,  # mid Day 2 — a panel walking out, not absent
        }],
        "withdraw": [{
            "type": "student_withdraw", "student_id": victim,
            "scope": "day", "from_slot": withdraw_event(victim_items),
        }],
        "room": [{
            "type": "room_unavailable", "room_id": busy_room,
            "day": busy_day, "from_slot": 0, "to_slot": SLOTS_PER_DAY_RAW,
            "reason": "burst pipe",
        }],
        # Guide section 11: their own injection stacks three kinds at once.
        "compound": [
            {"type": "company_late", "company_id": late_company,
             "day": late_day, "hours": 3},
            {"type": "panel_drop", "company_id": late_company, "count": 1,
             "from_slot": late_day * SLOTS_PER_DAY_RAW + 12},
        ] + [
            {"type": "student_withdraw", "student_id": sid,
             "scope": "day", "from_slot": withdraw_event(items)}
            for (sid, _day), items in busy_students[:15]
        ],
    }


def parse_args():
    p = argparse.ArgumentParser(description="Run a Panelist replan")
    p.add_argument("--data", default="./data/primary")
    p.add_argument("--scenario", default="late")
    p.add_argument("--churn-cap", type=float, default=10.0)
    p.add_argument("--time-limit", type=float, default=60.0,
                   help="Locked mid-day replans need more headroom than a "
                        "cold solve; 60s covers the observed worst case.")
    p.add_argument("--now-slot", type=int, default=None,
                   help="lock interviews that already started before this slot")
    p.add_argument("--apply", action="store_true")
    return p.parse_args()


def print_proposal(p, dataset, indent="  "):
    d = p["diff"]
    print(f"{indent}solver: {p['solver']['status']} "
          f"({p['solver']['wall_time_seconds']}s), "
          f"churn weight {p['churn_weight']}")
    print(f"{indent}churn:  {d['elective_churn_count']} elective of "
          f"{d['baseline_appointments']} appointments "
          f"({d['elective_churn_pct']}%)  cap {p['churn_cap_pct']}%"
          f"{'  ** OVER CAP' if p['cap_exceeded'] else '  (within cap)'}")
    if d["forced_churn_count"]:
        print(f"{indent}        + {d['forced_churn_count']} cancelled by the "
              f"disruption itself (not counted against the cap)")
    print(f"{indent}        {len(d['added'])} added, "
          f"{len(d['elective_removed'])} dropped, {len(d['moved'])} moved, "
          f"{len(d['reseated'])} reseated (same time, different room)")
    print(f"{indent}affects {len(d['affected_students'])} students, "
          f"{len(d['affected_companies'])} companies")
    ver = p["verification_errors"]
    print(f"{indent}verify: "
          f"{'PASS' if not ver else f'FAIL ({len(ver)} violations)'}")

    if d["moved_detail"]:
        print(f"{indent}sample moves:")
        for item in d["moved_detail"][:4]:
            f, t = item["from"], item["to"]
            print(f"{indent}  {item['id']}: D{f['day'] + 1} slot {f['slot']} "
                  f"-> D{t['day'] + 1} slot {t['slot']}")

    n = p["notify"]
    print(f"{indent}notify: {n['total_people_to_contact']} to contact "
          f"({len(n['students'])} students, {len(n['companies'])} companies)")
    for e in n["students"][:3]:
        print(f"{indent}  [{e['urgency']}] {e['student_id']}: "
              f"{e['changes'][0]}")
        for extra in e["changes"][1:2]:
            print(f"{indent}          {extra}")


def main():
    args = parse_args()
    with open(os.path.join(args.data, "dataset.json")) as f:
        dataset = json.load(f)
    schedule_path = os.path.join(args.data, "schedule.json")
    if not os.path.exists(schedule_path):
        print(f"No schedule at {schedule_path} — run scheduler/run.py first.")
        return 1
    with open(schedule_path) as f:
        prior = json.load(f)["scheduled"]

    scenarios = build_scenarios(prior, dataset)
    if args.scenario not in scenarios:
        print(f"Unknown scenario. Choose from: {', '.join(scenarios)}")
        return 1
    disruptions = scenarios[args.scenario]

    print("=" * 66)
    print(f"REPLAN — scenario '{args.scenario}' "
          f"({len(disruptions)} disruption event(s))")
    print("=" * 66)

    proposal = replan(
        dataset, prior, disruptions,
        churn_cap_pct=args.churn_cap,
        time_limit_seconds=args.time_limit,
        now_slot=args.now_slot,
    )

    print("\nDisruptions applied:")
    for line in proposal["disruptions_applied"][:6]:
        print(f"  - {line}")
    if len(proposal["disruptions_applied"]) > 6:
        print(f"  ... and {len(proposal['disruptions_applied']) - 6} more")

    if not proposal["ok"]:
        print(f"\nREPLAN FAILED: {proposal['reason']}")
        return 1

    print(f"\n-- {proposal['label']} " + "-" * (60 - len(proposal['label'])))
    print_proposal(proposal, dataset)

    m = compute_metrics(
        proposal["schedule"],
        [{"id": i} for i in proposal["unscheduled"]],
        dataset["students"], dataset["rooms"], dataset["config"],
    )
    print()
    print(format_metrics(m))

    if proposal["cap_exceeded"]:
        print(f"\n  ** {proposal['authorization_prompt']}")
        alt = proposal.get("alternative")
        if alt:
            print(f"\n-- {alt['label']} " + "-" * (60 - len(alt['label'])))
            print_proposal(alt, dataset)
        elif proposal.get("churn_irreducible"):
            pass  # already explained in the authorization prompt
        else:
            print("\n  No lower-churn alternative found within the time limit.")

    if args.apply:
        chosen = apply_proposal(proposal)
        with open(schedule_path) as f:
            payload = json.load(f)
        payload["scheduled"] = chosen
        payload["unscheduled"] = proposal["unscheduled"]
        with open(schedule_path, "w") as f:
            json.dump(payload, f, indent=2)
        print(f"\nApplied. Schedule updated at {schedule_path}")
    else:
        print("\n(Proposal only — nothing committed. Pass --apply to commit.)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
