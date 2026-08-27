"""
Panelist — replan scenario regression.

    python tests/test_replan_scenarios.py

Runs every disruption type plus the compound injection against the primary
dataset, with a mid-day lock in place, and asserts the properties that must
hold no matter what the solver decides:

  - zero student clashes (recomputed here, independently of the solver)
  - the schedule passes the model's own hard-constraint verification
  - locked interviews (already under way) are neither moved nor cancelled

The lock assertion is the one that keeps catching real bugs: a replan that
quietly rewrites the morning looks fine in every headline metric.
"""

import json
import os
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from replanner.replan import replan  # noqa: E402
from replanner.run import build_scenarios  # noqa: E402

NOW_SLOT = 48  # mid Day 2
DATA = os.path.join(ROOT, "data", "primary")


def main():
    with open(os.path.join(DATA, "dataset.json")) as f:
        dataset = json.load(f)
    with open(os.path.join(DATA, "schedule.json")) as f:
        prior = json.load(f)["scheduled"]

    prior_by_id = {a["id"]: a for a in prior}
    locked = {i for i, a in prior_by_id.items() if a["start"] < NOW_SLOT}
    scenarios = build_scenarios(prior, dataset)

    print(f"{'scenario':<10} {'status':<9} {'elect%':>7} {'forced':>7} "
          f"{'moved':>6} {'sched':>6} {'clash':>6} {'verify':>7} "
          f"{'lockOK':>7} {'secs':>6}")
    print("-" * 80)

    failures = []
    for name in ("late", "panel", "withdraw", "room", "compound"):
        t0 = time.time()
        p = replan(dataset, prior, scenarios[name], churn_cap_pct=10,
                   time_limit_seconds=45, now_slot=NOW_SLOT)
        if not p["ok"]:
            print(f"{name:<10} FAILED: {p['reason'][:60]}")
            failures.append(name)
            continue

        d = p["diff"]
        new_by_id = {a["id"]: a for a in p["schedule"]}
        lock_violations = [
            i for i in locked
            if i not in new_by_id
            or new_by_id[i]["start"] != prior_by_id[i]["start"]
        ]
        clashes, seen = 0, set()
        for a in p["schedule"]:
            for t in range(a["start"], a["end"]):
                key = (a["student_id"], t)
                if key in seen:
                    clashes += 1
                seen.add(key)

        ok = not (clashes or p["verification_errors"] or lock_violations)
        if not ok:
            failures.append(name)
        print(f"{name:<10} {p['solver']['status']:<9} "
              f"{d['elective_churn_pct']:>6.1f}% {d['forced_churn_count']:>7} "
              f"{len(d['moved']):>6} {len(p['schedule']):>6} {clashes:>6} "
              f"{'PASS' if not p['verification_errors'] else 'FAIL':>7} "
              f"{'PASS' if not lock_violations else f'FAIL({len(lock_violations)})':>7} "
              f"{time.time() - t0:>5.0f}s")

    print()
    if failures:
        print(f"FAILED: {', '.join(failures)}")
        return 1
    print("All scenarios passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
