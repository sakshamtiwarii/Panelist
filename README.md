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
  timegrid.py the slot grid, read from a dataset's config
replanner/    disruption handling: warm-started re-solve, minimal-churn diff
store/        Postgres persistence: versioned schedules + replan audit trail
api/          FastAPI endpoints wrapping generator/scheduler/replanner
  deps.py     the store and the live-dataset accessors
  schemas.py  request bodies
  routes/     auth · schedule · roster · replan
dashboard/    Next.js coordinator UI: schedule view, disruption triggers, diff viewer
```

Dependencies run one way — `generator` and `store` depend on nothing internal,
`scheduler` on nothing but itself, `replanner` on `scheduler`, `api` on all of
them, and `dashboard` talks HTTP only. Everything installs as one package
(`pip install -e .`), so imports resolve by their real dotted names rather than
by patching `sys.path` at import time.

`scheduler/model.py` is shared by both the initial scheduler and the
replanner — the replanner calls it with `prior_schedule` set and a churn
penalty weight, rather than duplicating constraint logic.

**The time grid has one source.** The generator decides the working day and
writes the whole model — slot length, day origin, lunch, raw slots per day —
into the dataset's `config`; every other module reads it back through
`scheduler/timegrid.py`. A private copy of `SLOTS_PER_DAY_RAW = 32` cannot fail
loudly, only quietly: nothing crashes, every appointment just renders at the
wrong clock time.

## Signing in

The console is gated — a replan changes hundreds of people's days, so it is not
an open endpoint. Two demo accounts are seeded on first start:

| Username | Password | Role | Can do |
|---|---|---|---|
| `coordinator` | `placement2026` | Coordinator | Everything — build the schedule, replan, **apply** a fix |
| `viewer` | `review2026` | Viewer | Read the board, metrics and diagnostics; **request** a fix but not apply one |

Both are shown on the sign-in screen and fill the form on click, so there is
nothing to look up. They exist for evaluation against a synthetic dataset;
`PANELIST_SEED_USERS=0` disables seeding.

**The permission boundary sits at the state change, not at the feature.**
A viewer can ask for a proposal — that computes a fix and mutates nothing — but
cannot commit one. That is the line worth drawing: proposing is free, applying
moves real appointments.

Passwords are scrypt hashes with a per-user salt. Sessions are HMAC-SHA256
signed tokens in an httpOnly cookie, so page JavaScript cannot read the session
and an XSS bug cannot exfiltrate it. Login failures return one message for both
unknown users and wrong passwords, so the endpoint cannot be used to enumerate
accounts.

Set `PANELIST_SECRET_KEY` in any real deployment. Unset, the API generates a
per-process key and says so — sessions then die on restart, which is deliberate:
an unset secret should be noticed, not silently insecure.

## Roster changes

A coordinator can register a late company, withdraw one, or edit a single
shortlist entry:

```
POST   /roster/companies              add a company
DELETE /roster/companies/{id}         withdraw one
POST   /roster/shortlist              add one student to one shortlist
DELETE /roster/shortlist              remove one
```

**These return a proposal, not a committed change** — they go through exactly
the same propose → diff → apply path as a disruption, and commit via
`POST /replan/apply`.

That is the whole point. Adding a company means its interviews need slots that
are already taken; removing one frees capacity the plan cannot use; adding a
shortlist entry can create a clash. A direct database write would leave the
live schedule wrong while every metric still read zero clashes, because nothing
re-checked it. Routing roster edits through the replanner means each one is
costed, capped and approved like any other change:

```
Jane Street Capital registered late as C035: 12 students shortlisted
at CGPA 8.8+, 2 panel(s), 45min interviews.
  cost 18 moved · 12 placed · within 10% cap · verify PASS
```

CGPA cutoffs are enforced here as a business rule, not a preference — an
ineligible student is refused with the reason, never quietly scheduled.

There is deliberately no student create/delete: the cohort is fixed by the
university, so it is not an operation a placement coordinator has.

## Persistence

Schedule state lives in Postgres (`store/schema.sql`). Two decisions are worth
naming:

**Schedules are versioned, not mutated.** A replan writes a new version and
flips `is_current`; the plan that existed before the disruption stays queryable.
Mutating in place would destroy exactly the prior state the replanner needs to
diff against, and would make rollback a re-solve instead of a lookup.

**The shortlist relation is a table, not a JSON column**, because it is what
answers contention questions. `GET /affected?company_id=…&day=…` returns the
students a disruption touches *and how many other interviews each still has
that day* — a correlated count over the same student's other appointments.
That join is the reason the database earns its place; in Python it means
loading the whole week into memory first.

`replan_events` keeps an audit trail: every applied replan, what caused it,
what it cost, how many people needed telling.

**The API never depends on the database being up.** If `DATABASE_URL` is unset
or unreachable, it logs once and falls back to an in-memory store with the same
interface. `GET /health` reports which is active, so a downgrade is visible
rather than silent — a live demo should not open with a connection error.

On startup the API adopts any schedule that outlived the last process, so a
restart comes back with the week already planned rather than needing a re-solve.

## Running it

```bash
docker compose up
```

- API: http://localhost:8000
- Dashboard: http://localhost:3000
- Postgres: localhost:55432

**Check the port before you open it.** Those are the defaults; if any was
already taken on your machine, Docker published elsewhere and the URL above is
wrong for you. `docker compose ps` is authoritative — the `dashboard` row shows
the host port on the left of `->3000`:

```
dashboard   running   0.0.0.0:3001->3000/tcp     # open :3001, not :3000
```

The API allows requests from exactly the origin it published the dashboard on,
so opening the wrong port fails every fetch with a CORS error rather than a
404 — the console detects this case and says so, but it is worth knowing.

These are the defaults; if you have a `.env` with port overrides (see below),
use the ports it sets instead.

Then `POST /schedule` (or press **Build schedule** in the dashboard) to solve.
A fresh clone has no dataset — `data/` is gitignored — so run the generator
first, or `POST /generate`.

**Port overrides.** Dev machines routinely already run something on 3000, 8000
or 5432, and Docker publishes on `0.0.0.0`, so it collides with any local
service bound to all interfaces even when localhost-only tools report the port
free. Every published port is overridable — copy `.env.example` to `.env` and
change what clashes:

```bash
PANELIST_WEB_PORT=3001   # dashboard
PANELIST_API_PORT=8000   # API
PANELIST_DB_PORT=55432   # Postgres (host side only)
```

Defaults match the URLs above, so a clean machine needs no `.env` at all. The
API always reaches the database as `db:5432` on the compose network; the
published port exists only for host tooling like `psql`.

Running without Docker:

```bash
pip install -e .                          # from the repo root, once
createdb panelist
export DATABASE_URL="postgresql://$USER@127.0.0.1:5432/panelist"
uvicorn api.main:app --port 8000          # from the repo root
npm run dev                               # from dashboard/
```

The project installs as a package, so `api.main`, `scheduler.model` and the
rest resolve by their real dotted names in every context — container, bare
checkout, and test run alike. The CLIs are module entry points for the same
reason: use `python -m scheduler.run`, not `python scheduler/run.py`.

The schema is created automatically on first connect. Omit `DATABASE_URL`
entirely and everything still runs, in memory.

## Tests

```bash
pip install -e ".[dev]"
pytest tests/test_api.py              # ~3s, generates its own dataset
pytest tests/test_store_parity.py     # ~1s, both stores
python -m tests.test_replan_scenarios # ~3.5min, every disruption type
ruff check .
```

`tests/test_store_parity.py` runs every case against **both** stores and
asserts the same outcome. The in-memory fallback exists so the app survives an
unreachable database, which means any behavioural difference between the two
surfaces as a bug that appears only when the database is down — or only when it
is up. It found one: `/schedule/versions` was returning a different order *and
a different shape* depending on which store was active. Point
`PANELIST_TEST_DATABASE_URL` at a server to include the Postgres half; without
it, only the memory half runs.

`tests/test_api.py` covers the boundaries the scenario suite cannot reach:
authentication, the coordinator/viewer split, and the propose → apply
handshake. It generates its own dataset rather than reading `data/`, which is
gitignored, so it runs on a fresh clone.

`tests/test_replan_scenarios.py` runs every disruption type plus the compound
injection against the primary dataset with a mid-day lock, and asserts what
must hold whatever the solver decides: zero student clashes (recomputed
independently), the model's own hard-constraint verification, and that
interviews already under way are neither moved nor cancelled.

All of these run in CI (`.github/workflows/ci.yml`), which stands up a Postgres
service for the parity suite and runs the replan scenarios as their own job
with a larger solver budget — a CI runner has fewer cores than a dev machine,
so the solver needs more wall-clock for the same result
(`PANELIST_REPLAN_TIME_LIMIT`).

To regenerate the dataset with a different seed/size:

```bash
python -m generator.generate --seed 42 --companies 35 --students 800 --rooms 20 --days 4
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
python -m scheduler.run --data ./data/primary --time-limit 30
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
python -m replanner.run --data ./data/primary --scenario compound
python -m replanner.run --data ./data/primary --scenario room --now-slot 48
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

`POST /auth/login` · `POST /auth/logout` · `GET /auth/me` ·
`POST /roster/companies` · `DELETE /roster/companies/{id}` ·
`POST|DELETE /roster/shortlist` ·
`POST /generate` · `POST /schedule` · `GET /schedule` (filter by day, room,
company, student) · `GET /metrics` · `GET /diagnostics` · `GET /affected` ·
`GET /schedule/versions` · `POST /replan` · `POST /replan/apply` ·
`GET /replan/history`

## Status

- **Generator** — done. Seeded, reproducible, with conflict-density readout.
- **Scheduler** — done. CP-SAT model, metrics, capacity diagnostics,
  independent verification.
- **Replanner** — done. Four disruption types, compound events, minimal-churn
  re-solve, structured diff, notify list, churn cap with authorization flow.
- **API** — done. Propose/apply separation verified end to end.
- **Persistence** — done. Postgres-backed versioned schedules, impact queries
  and replan audit trail, with an in-memory fallback.
- **Auth** — done. Scrypt password hashing, signed httpOnly session cookies,
  coordinator/viewer roles gating mutation.
- **Roster editing** — done. Add/withdraw a company, edit shortlist entries,
  all costed through the replanner rather than written directly.
- **Dashboard** — done. Next.js coordinator console (see below).

## Dashboard

```bash
docker compose up          # API :8000, dashboard :3000
```

A single-screen operations console — the board, the events being injected and
the proposed fix all stay visible at once, because routing between them means
losing the schedule from view exactly when it matters most.

**Room × time board.** The main view is a grid, not a table. A coordinator's
questions are spatial — what's free at 2pm, how bad is the 11am crunch, what
does this delay push into — and a grid answers them without the reader
assembling anything mentally. Blocked room windows are hatched; interviews
already under way are tinted and pinned.

**Proposals preview on the board.** A replan recolours moved and cancelled
interviews *in place* before anything is committed, so the change is read
against the real schedule rather than inferred from a list. Cancelled
interviews stay visible in their old slot so the loss is legible rather than
a silent absence.

**Churn is shown against the cap as a bar** before any list of changes,
because "is this fix proportionate" is the actual question. Forced churn (what
the disruption cancelled) is separated from elective churn (what the replanner
chose to move) — blending them makes a modest fix look reckless.

Events are queued rather than fired singly: the compound injection this is
built for costs far less churn solved once than the same events resolved one
at a time.

Design bias is the guide's own (§2.4) — clarity over decoration. Colour carries
meaning and nothing else: neutral is the entire interface, and amber/red/green
appear only on moved/cancelled/added.
# Panelist
