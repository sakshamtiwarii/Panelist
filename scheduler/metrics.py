"""
Panelist — schedule quality metrics (guide section 3).

Computed after every schedule/replan run:
- % of interviews successfully scheduled
- student clash count (should be 0 by construction; report as sanity check)
- room utilization (%, per room and aggregate)
- average student waiting time
- replan churn (count and % of appointments changed) -- replan runs only

"Good" is defined in the README: zero clashes and CGPA-cutoff compliance are
non-negotiable and win any trade-off; utilization and wait time are the
metrics sacrificed first.
"""

from collections import defaultdict

SLOTS_PER_DAY_RAW = 32


def compute_metrics(scheduled, unscheduled, students, rooms, config):
    total = len(scheduled) + len(unscheduled)
    slot_minutes = config["slot_minutes"]
    capacity_slots = (
        len(rooms) * config["slots_per_day_count"] * config["days"]
    )
    used_slots = sum(a["duration_slots"] for a in scheduled)

    # Per-room utilization.
    per_room = defaultdict(int)
    for a in scheduled:
        if a.get("room"):
            per_room[a["room"]] += a["duration_slots"]
    room_capacity = config["slots_per_day_count"] * config["days"]
    room_util = {
        r["id"]: round(100.0 * per_room.get(r["id"], 0) / room_capacity, 1)
        for r in rooms
    }

    # Student clashes — must be 0; recomputed independently of the solver.
    by_student = defaultdict(list)
    for a in scheduled:
        by_student[a["student_id"]].append(a)
    clashes = 0
    gaps = []
    for _sid, items in by_student.items():
        items.sort(key=lambda x: x["start"])
        for a, b in zip(items, items[1:]):
            if b["start"] < a["end"]:
                clashes += 1
            elif a["day"] == b["day"]:
                gaps.append((b["start"] - a["end"]) * slot_minutes)

    scheduled_students = len(by_student)
    return {
        "interviews_total": total,
        "interviews_scheduled": len(scheduled),
        "interviews_unscheduled": len(unscheduled),
        "pct_scheduled": round(100.0 * len(scheduled) / total, 1) if total else 0.0,
        "student_clashes": clashes,
        "room_utilization_pct": round(100.0 * used_slots / capacity_slots, 1)
        if capacity_slots else 0.0,
        "room_utilization_per_room": room_util,
        "avg_student_wait_minutes": round(sum(gaps) / len(gaps), 1) if gaps else 0.0,
        "max_student_wait_minutes": max(gaps) if gaps else 0,
        "students_with_interviews": scheduled_students,
        "avg_interviews_per_student": round(len(scheduled) / scheduled_students, 2)
        if scheduled_students else 0.0,
    }


def compute_churn(old_schedule, new_schedule):
    """Churn between two schedules, keyed by interview id.

    An interview counts as *moved* only if its start time changed — a room or
    panel reassignment at the same time is invisible to the student and is
    reported separately so it does not inflate the headline churn number.
    """
    old = {a["id"]: a for a in old_schedule}
    new = {a["id"]: a for a in new_schedule}

    added = sorted(set(new) - set(old))
    removed = sorted(set(old) - set(new))
    moved, reseated = [], []
    for iid in set(old) & set(new):
        if old[iid]["start"] != new[iid]["start"]:
            moved.append(iid)
        elif (
            old[iid].get("room") != new[iid].get("room")
            or old[iid].get("panel") != new[iid].get("panel")
        ):
            reseated.append(iid)

    baseline = len(old) or 1
    churn_count = len(added) + len(removed) + len(moved)
    return {
        "added": added,
        "removed": removed,
        "moved": sorted(moved),
        "reseated": sorted(reseated),
        "churn_count": churn_count,
        "churn_pct": round(100.0 * churn_count / baseline, 1),
        "baseline_appointments": len(old),
    }


def format_metrics(m):
    lines = [
        "-- Schedule metrics -----------------------------------------------",
        f"  scheduled          {m['interviews_scheduled']}/{m['interviews_total']}"
        f"  ({m['pct_scheduled']}%)",
        f"  student clashes    {m['student_clashes']}"
        f"{'  <-- MUST BE 0' if m['student_clashes'] else '  (ok)'}",
        f"  room utilization   {m['room_utilization_pct']}%",
        f"  avg student wait   {m['avg_student_wait_minutes']} min"
        f"   (max {m['max_student_wait_minutes']} min)",
        f"  students placed    {m['students_with_interviews']}"
        f"  ({m['avg_interviews_per_student']} interviews each)",
    ]
    return "\n".join(lines)
