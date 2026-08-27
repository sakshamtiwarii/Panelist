# Panelist

A placement-week scheduling and disruption-replanning system, built for the
Mirai Labs Software Developer Intern take-home assignment.

Full design spec, rationale, and defense prep: see `PLACEMENT_SCHEDULER_GUIDE.md`.

## What this is

Placement week scheduling is a constraint-satisfaction problem that breaks
constantly: companies run late, panels drop, students withdraw, rooms go
unavailable. Panelist generates a realistic dataset, produces a feasible
interview schedule under hard constraints, and — the core of the project —
replans around live disruptions while disturbing the existing schedule as
little as possible.

## Architecture

```
generator/    seeded, realistic dataset generation (companies, students, rooms)
scheduler/    CP-SAT model: hard constraints + infeasibility diagnostics
replanner/    disruption handling: warm-started re-solve, minimal-churn diff
api/          FastAPI endpoints wrapping generator/scheduler/replanner
dashboard/    Next.js coordinator UI: schedule view, disruption triggers, diff viewer
```

`scheduler/model.py` is shared by both the initial scheduler and the
replanner — the replanner calls it with `prior_schedule` set and a churn
penalty weight, rather than duplicating constraint logic.

## Running it

```bash
docker compose up
```

- API: http://localhost:8000
- Dashboard: http://localhost:3000

To regenerate the dataset with a different seed/size:

```bash
python generator/generate.py --seed 42 --companies 35 --students 800 --rooms 20 --days 4
```

## Design decisions (defended)

**What does "good" mean?**
Zero student/room/panel clashes and CGPA-cutoff compliance are treated as
non-negotiable. Beyond that: % scheduled, room utilization, average student
wait time, and (for replans) churn are reported every run. If these trade
off against each other, zero clashes and cutoff compliance win first.

**When infeasible, which constraint bends first?**
CGPA cutoffs and no-double-booking constraints are never violated — they're
business rules and safety constraints. Exact time-slot placement is the
soft constraint that shifts first. If that's still not enough, the choice
is surfaced to the coordinator via priority-tier override rather than
silently decided by the algorithm.

**How much reshuffling is acceptable during a replan?**
Churn is capped (default 10% of the day's appointments, configurable). If a
fix requires exceeding that, it's surfaced to the coordinator to confirm or
request a looser fix — never auto-applied silently.

## Status

Scaffold stage — see TODOs in `scheduler/model.py`, `replanner/replan.py`,
and `api/main.py` for what's implemented vs. pending. Build order follows
`PLACEMENT_SCHEDULER_GUIDE.md` section 7.
# Panelist
