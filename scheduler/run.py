"""
Panelist — scheduler CLI.

    python scheduler/run.py --data ./data/primary --time-limit 30

Solves an initial schedule, verifies it independently, prints metrics and
(when interviews go unscheduled) the attributed infeasibility diagnostics.
"""

import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from scheduler.metrics import compute_metrics, format_metrics  # noqa: E402
from scheduler.model import SLOTS_PER_DAY_RAW, SchedulingModel  # noqa: E402


def parse_args():
    p = argparse.ArgumentParser(description="Run the Panelist scheduler")
    p.add_argument("--data", default="./data/primary")
    p.add_argument("--time-limit", type=float, default=30.0)
    p.add_argument("--out", default=None, help="write schedule.json here")
    return p.parse_args()


def load_dataset(path):
    with open(os.path.join(path, "dataset.json")) as f:
        return json.load(f)


def slot_to_clock(day, slot, slot_minutes=15, day_start_min=9 * 60):
    minutes = day_start_min + slot * slot_minutes
    return f"D{day + 1} {minutes // 60:02d}:{minutes % 60:02d}"


def main():
    args = parse_args()
    ds = load_dataset(args.data)

    model = SchedulingModel(
        ds["companies"], ds["students"], ds["rooms"], ds["config"]
    ).build()

    print(f"Model built: {len(model.interviews)} interviews, "
          f"{len(ds['rooms'])} rooms, {ds['config']['days']} days")
    print(f"Solving (limit {args.time_limit}s)...\n")

    status, solver = model.solve(time_limit_seconds=args.time_limit)
    report = model.status_report(status, solver)

    print("=" * 66)
    print(f"SOLVER: {report['status']}  ({report['wall_time_seconds']}s)")
    print(f"  {report['note']}")
    print("=" * 66)

    if not report["usable"]:
        print("\nNo schedule produced.")
        for cid, reason in list(model.constraint_reasons.items())[:10]:
            print(f"  [{cid}] {reason}")
        return 1

    scheduled, unscheduled = model.extract_schedule(solver)

    errors = model.verify_schedule(scheduled)
    print(f"\nIndependent verification: "
          f"{'PASS — all hard constraints hold' if not errors else 'FAIL'}")
    for e in errors[:10]:
        print(f"  ! {e}")

    m = compute_metrics(
        scheduled, unscheduled, ds["students"], ds["rooms"], ds["config"]
    )
    print()
    print(format_metrics(m))

    cap = None
    if unscheduled:
        cap = model.capacity_analysis(scheduled, unscheduled)
        print()
        print("-- Why interviews went unscheduled --------------------------------")
        print(f"  {cap['headline']}")
        if cap["max_schedulable_estimate"]:
            print(f"  Ceiling on any schedule: about "
                  f"{cap['max_schedulable_estimate']} of "
                  f"{m['interviews_total']} interviews.")
        print(f"  Room utilization by day: {cap['room_utilization_per_day_pct']}")
        if cap["saturated_windows"]:
            print()
            print("  Saturated windows:")
            for w in cap["saturated_windows"]:
                print(f"    {w['text']}")
        print()
        print("  Per-company shortfall:")
        diags = model.diagnose_unscheduled(scheduled, unscheduled)
        for d in diags[:8]:
            print(f"    {d['company_id']} {d['company']}: "
                  f"{d['unscheduled']}/{d['demand']} unscheduled "
                  f"[{d['dominant_cause']}]")
            print(f"       {d['reason']}")
        if len(diags) > 8:
            print(f"    ... and {len(diags) - 8} more companies affected")

    print("\n-- Sample of the schedule -----------------------------------------")
    for a in sorted(scheduled, key=lambda x: x["start"])[:8]:
        print(f"  {slot_to_clock(a['day'], a['slot'])}  "
              f"{a['room']}  panel {a['panel']}  "
              f"{a['company_id']} x {a['student_id']}")

    out = args.out or os.path.join(args.data, "schedule.json")
    with open(out, "w") as f:
        json.dump({
            "meta": {**ds["meta"], "solver": report},
            "metrics": m,
            "scheduled": scheduled,
            "unscheduled": [u["id"] for u in unscheduled],
            "capacity_analysis": cap,
        }, f, indent=2)
    print(f"\nWrote schedule to {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
