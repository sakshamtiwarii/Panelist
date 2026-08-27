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

## Datasets

The generator emits three datasets, all from seed 42, all reproducible:

| Path | Interviews | Load | Purpose |
|---|---|---|---|
| `data/small` | 114 | 0.67 | fast solver iteration |
| `data/primary` | 1013 | 0.90 | hard but fully solvable |
| `data/oversubscribed` | 2770 | 2.46 | realistic sizes; infeasible by construction |

Each run writes a `density_report.txt` confirming the instance is actually
hard before any solver runs: shortlist distribution, the CGPA/shortlist
Pearson correlation, per-company panel ceilings, and the most contended
company pairs.

## Solver results

```bash
python scheduler/run.py --data ./data/primary --time-limit 30
```

| Dataset | Interviews | Status | Time | Scheduled | Clashes | Room util |
|---|---|---|---|---|---|---|
| `small` | 114 | OPTIMAL | 0.1s | 100% | 0 | 67% |
| `primary` | 1013 | OPTIMAL | 1.2s | 100% | 0 | 90% |
| `oversubscribed` | 2770 | FEASIBLE (120s cap) | 120s | 39.8% | 0 | 93% |

Every run is independently re-verified after extraction: room, panel and
student double-booking and CGPA cutoffs are re-checked against the emitted
schedule rather than trusted from the solver.

### Solver formulation

The model uses interval variables with `Cumulative` capacity constraints, not
the `assign[interview][room][slot][panel]` boolean grid — that encoding is
~6.2M booleans at full scale and will not build. Two variables per interview
(`start`, `present`); rooms and panels are recovered after the solve by
interval colouring. See the module docstring in `scheduler/model.py` for the
full rationale.

`present` being optional is what lets one model serve both a solvable and an
oversubscribed instance: an oversubscribed instance returns the best partial
schedule plus an attributed shortfall, rather than a bare INFEASIBLE.

## Replanning

```bash
python replanner/run.py --data ./data/primary --scenario compound
python replanner/run.py --data ./data/primary --scenario room --now-slot 48
```

Scenarios (`late`, `panel`, `withdraw`, `room`, `compound`) are derived from
the *current* schedule, so they always bite — targeting "the busiest company"
and then delaying it on a day it isn't scheduled produces a zero-churn replan
that looks like success and is really a no-op.

**Churn is split two ways.** *Forced* churn is what the disruption removed
outright — a withdrawn student's interviews. *Elective* churn is what the
replanner chose to move to absorb it. Only elective churn counts against the
cap: charging the coordinator for cancellations they had no say in fires the
authorization prompt on events nobody chose, and makes "reduce churn" mean
"un-withdraw them".

Absorbing a three-way compound disruption (mass recruiter 3h late + a panel
walking out + 15 mid-day withdrawals) costs **35 elective moves, 3.5%**, with
the 77 forced cancellations reported separately.

**Disruptions are time-scoped.** A panel that leaves at 14:00 does not
retroactively un-run its 10:00 interviews, and a company late on Day 4 is not
unavailable on Day 1. Both are modelled as blackout windows consuming capacity
from a moment onward, not as week-long capacity reductions.

**Nothing is auto-applied.** `replan()` returns a proposal; `/replan` hands
back a `proposal_id` and leaves the live schedule untouched until
`/replan/apply` commits it. When churn exceeds the cap, the replanner
re-solves at a 40x stability weight and offers the cheaper option — or, when
the churn is genuinely irreducible, says so rather than presenting two
near-identical choices as though they were a decision.

## API

`POST /generate` · `POST /schedule` · `GET /schedule` (filter by day, room,
company, student) · `GET /metrics` · `GET /diagnostics` · `POST /replan` ·
`POST /replan/apply`

## Status

- **Generator** — done. Seeded, reproducible, with conflict-density readout.
- **Scheduler** — done. CP-SAT model, metrics, capacity diagnostics,
  independent verification.
- **Replanner** — done. Four disruption types, compound events, minimal-churn
  re-solve, structured diff, notify list, churn cap with authorization flow.
- **API** — done. Propose/apply separation verified end to end.
- **Dashboard** — pending (build order step 5).
# Panelist
