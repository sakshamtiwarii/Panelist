# Panelist — Build Guide
### (Placement Week Scheduler)

**Assignment:** Mirai Labs, Software Developer Intern — Assignment A
**Purpose of this doc:** single source of truth for what to build, what to avoid, and what makes this submission stand out. Written to be handed directly to Claude Code as a spec — it can be read top to bottom and used to scaffold and implement the project section by section.

---

## 0. What is actually being graded

Before any code: internalize that this assignment is not "build a scheduler that works." It's testing four things, roughly in this order of weight:

1. **Constraint modeling judgment** — did you correctly identify what's a hard constraint vs. a soft preference vs. a policy decision that should be exposed to the coordinator?
2. **Graceful degradation** — when the problem is infeasible (it always will be), does the system fail loudly and specifically, or silently/vaguely?
3. **Replan quality** — is a fix to a small disruption a small change, or does it reshuffle half the schedule? This is explicitly called out as "the heart of the assignment."
4. **Communication under pressure** — in the live defense, can you explain your tradeoffs crisply when they inject a compound disruption live?

A visually polished dashboard sitting on top of a weak solver will fail. A correct solver with a bare-bones but honest dashboard will pass. Effort should be allocated in that ratio: **solver + replan logic (60%), data realism (15%), dashboard (15%), metrics/README/defense prep (10%)**.

---

## 1. Tech stack (decided, don't relitigate this)

| Layer | Choice | Why |
|---|---|---|
| Backend | **FastAPI** (Python) | Matches existing stack, fast to iterate, good for exposing schedule/replan/metrics endpoints |
| Solver | **OR-Tools CP-SAT** (`ortools`, Python) | This is the single highest-leverage decision. Hand-rolled backtracking is slow to build and has unverifiable edge cases. A constraint solver is fast to build correctly AND is a strong, defensible engineering choice in the interview |
| Database | **PostgreSQL** | Persist schedule state, students, companies, rooms, interviews. Needed for "who is affected" queries and for replan to read prior state |
| Cache/lock (optional) | **Redis** | Only if time permits — could hold "in-progress replan" lock state during live defense. Not essential, skip if behind schedule |
| Frontend | **Next.js + React** | Coordinator dashboard: schedule grid, disruption trigger panel, diff viewer, metrics panel |
| Data generation | Plain Python (`random`, optionally `faker`) | Seeded generator (`--seed`) for reproducibility — you want the exact same dataset available on defense day |
| Orchestration | **Docker Compose** | Postgres + FastAPI + Next.js wired together. Makes `docker compose up` trivially reproduce the whole demo live |

Do not introduce new frameworks you haven't used before under deadline pressure. The solver library is the one new dependency worth the learning curve — everything else should be stack you already trust.

---

## 2. What the system must do — four components

### 2.1 Data generator

Command-line script, e.g. `python generate.py --seed 42 --companies 35 --students 800 --rooms 20 --days 4`.

Must produce, as JSON (and/or seed directly into Postgres):

**Companies**
- Number of slots needed, panel count, per-interview duration, CGPA cutoff, priority tier
- Priority tiers should correlate with scheduling day: mass recruiters (high shortlist volume, lower cutoffs) cluster on Day 1; niche/high-paying companies with small headcounts appear later
- Shortlist size distribution should be **power-law, not uniform** — a handful of companies shortlist 300+ students, most shortlist 20–40. This is explicitly called out as graded ("realism of this data is graded")

**Students**
- CGPA (skewed/normal distribution, not uniform)
- Branch
- List of companies that shortlisted them — must be generated so that **high-CGPA students appear on many overlapping shortlists** (this is what creates the actual scheduling conflicts the assignment is about — without this correlation, the problem is trivially easy and misses the point)

**Rooms**
- Fixed count, possibly with some blocked/reserved windows to add realism

**Non-negotiable:** the generator must be seeded and reproducible. You will re-run the exact same dataset during the live defense — don't let this be nondeterministic.

### 2.2 Scheduler (core engine)

Input: generated dataset. Output: an assignment of every interview to a `(room, panel, time slot)` such that:

- No student is double-booked
- No room is double-booked
- No panel is double-booked
- CGPA cutoffs respected
- Interview duration fits within the assigned slot
- Company's total interviews fit within its allotted rooms/panels/time window

**Model this as CP-SAT** (OR-Tools), not greedy-fill-then-patch. Variables: boolean `assign[interview][room][slot][panel]`. Constraints as above. This gets you correctness guarantees a hand-rolled heuristic can't promise.

**When infeasible (it will be):**
- Never fail silently or with a generic "couldn't schedule everyone."
- Report **exactly which constraint caused which failure** — e.g. "Room capacity exceeded for Day 2, 14:00–16:00 window by 6 interviews" or "Student X shortlisted by 3 companies whose only compatible slots overlap."
- This diagnostic quality is one of the easiest places to differentiate your submission — most candidates will just print a list of unscheduled IDs.

### 2.3 Replanner — the heart of the assignment

Input: existing schedule + a disruption event. Output: a new schedule that resolves the disruption while disturbing the existing schedule **as little as possible**.

Disruption types to support (minimum, per spec):
1. Company arrives N hours late
2. A panel drops out
3. A student withdraws
4. A room becomes unavailable

**Implementation approach:** re-run the CP-SAT solve, but:
- Use the existing schedule as a warm start
- Add a penalty term to the objective for every assignment that differs from the prior schedule (i.e., minimize disruption count as a secondary/weighted objective, not just "find any feasible schedule")
- Optionally hard-cap churn: reject a solution (or explicitly ask the coordinator to authorize it) if it would move more than X% of appointments — this directly addresses the assignment's own warning: *"Moving 200 appointments to fix a 2-hour delay is technically valid and practically a disaster."*

**Output format — a structured diff, not just a new schedule:**
- `added` / `removed` / `moved` interviews
- List of affected students and companies
- A generated "who needs to be notified" list (this is explicitly requested — don't skip it)
- The churn count/percentage, shown prominently

### 2.4 Coordinator dashboard

A web UI for a stressed person making decisions in real time. Requirements:

- Current schedule state, filterable by day / room / company
- An "at-risk" or upcoming-conflicts view
- A disruption trigger panel (buttons: late arrival, panel drop, student withdrawal, room unavailable) — this is what you'll literally use during the live defense injection
- One-click replan → shows the diff → **apply or reject**, not auto-applied
- A metrics panel (see section 3) visible at all times, not buried in a report

Design bias: **clarity over polish.** A clean table and an unambiguous diff view beats an aesthetically impressive but confusing interface. The persona you're designing for is explicitly "a stressed person," not a design reviewer.

---

## 3. Metrics — define these explicitly, report them every run

Do not leave "what does a good schedule mean" undefined — this is one of the three things you're required to defend. Suggested set:

- **% of interviews successfully scheduled**
- **Student clash count** (should be 0 by construction — report it anyway, as a sanity check)
- **Room utilization** (%, per room and aggregate)
- **Average student waiting time** (gap between a student's consecutive interviews)
- **Replan churn** — number and % of appointments changed per replan event

Print these after every `schedule` and `replan` run. Put them in the dashboard, not just the README.

---

## 4. The three decisions you must explicitly defend

Write your answers into the README, not just in your head — you will be asked these live.

**1. What does "good" mean?**
State your metric set (above) and, importantly, state which one you'd sacrifice first if they trade off against each other (e.g., "I prioritize zero clashes and cutoff compliance over room utilization").

**2. When infeasible, which constraint bends first — and who decides?**
Recommended stance: CGPA cutoffs and hard capacity constraints (room/panel/student double-booking) are **never** violated — these are business rules and safety constraints. Exact time-slot placement is the soft constraint that can shift. When even that isn't enough, expose the choice to the coordinator via a priority-tier override rather than having your algorithm silently deprioritize a company — **you decide the algorithm's default behavior, the coordinator decides exceptions.** This is a strong, defensible position: it shows you understand which decisions carry business risk and shouldn't be made unilaterally by code.

**3. How much reshuffling is acceptable during a replan?**
Recommended stance: cap churn explicitly (e.g., a hard solver constraint or a post-hoc rejection threshold, configurable, defaulting to something like 10% of the day's appointments). If a fix requires exceeding that, surface it to the coordinator as "this fix requires moving 47 appointments — exceeding your cap, confirm or let me search for a looser fix" rather than either auto-applying a disruptive fix or failing outright.

---

## 5. What will make this submission stand out

Ranked by leverage (do the top ones first if time is short):

1. **Honest, capped replans.** Most candidates will get a working full re-solve and call it done. A re-solve that respects prior schedule as a soft constraint and reports/caps churn is the single most differentiating thing you can build, because the spec calls this out explicitly as the heart of the assignment.
2. **Specific infeasibility diagnostics.** "Couldn't schedule 12 interviews" vs. "Room capacity exceeded Day 2 14:00-16:00 by 6; Student #482 has 3 unavoidable overlapping shortlist slots" — the second is a completely different quality bar and is cheap to build once your solver model tracks constraint provenance.
3. **A clearly reasoned, written position on the three "decide and defend" questions**, stated as design decisions in the README, not improvised in the room.
4. **Realistic data generation with actual correlation structure** (power-law shortlists, CGPA/shortlist correlation, Day-1 mass recruiter clustering) — most people will generate uniform random data because it's easier, and it will make their scheduling problem artificially easy and unconvincing.
5. **A "notify" list generated automatically from a replan diff** — small feature, easy to forget, explicitly requested in the spec.
6. **Reproducible seeded demo** — walking into the defense and confidently re-running the exact dataset/schedule you tested against, then injecting the live disruption on top of it, versus fumbling with fresh random data live.

---

## 6. What to avoid

- **Don't hand-roll backtracking search from scratch** unless you have a strong reason — it's slower to build, harder to prove correct, and CP-SAT is a better answer both practically and as an interview talking point.
- **Don't silently drop unscheduled interviews.** Every failure needs a stated reason. This is explicitly called out in the spec ("never fail silently").
- **Don't auto-apply replans without a diff/confirm step.** The dashboard should show the diff before committing — this matters both for UX and for the live defense (you want to narrate the diff, not just say "done").
- **Don't build uniform-random / unrealistic data.** Explicitly graded down per the spec. If your top student is shortlisted by 2 companies instead of 15, your "conflict" scenario isn't real.
- **Don't over-invest in dashboard visuals before the solver is correct.** Sequence matters — see section 7.
- **Don't ignore churn.** A full re-solve on every disruption (even if internally correct) misses the point of the assignment and will get called out directly ("moving 200 appointments to fix a 2-hour delay").
- **Don't skip the metrics.** "Define your own metrics" is a direct instruction, not decoration — leaving it vague is an easy way to lose credit you could have gotten cheaply.
- **Don't introduce unfamiliar frontend/backend frameworks under deadline pressure.** Stick to your existing stack (FastAPI/Next.js/Postgres) and spend the new-tool budget entirely on OR-Tools.
- **Don't over-scope the dashboard into a "product."** Coordinator-usable clarity, not consumer-app polish, is the bar.

---

## 7. Suggested build order

1. **Data generator** — get realistic, seeded, correlated data first. Everything downstream depends on this being right; fix it before writing a line of solver code.
2. **Initial scheduler (CP-SAT)** — hard constraints only, with infeasibility diagnostics from day one, not bolted on later.
3. **Metrics reporting** — wire this in right after the scheduler works, not at the end.
4. **Replanner** — warm-start + churn-penalized re-solve, structured diff output, churn cap/confirm flow.
5. **Dashboard** — schedule view, disruption trigger panel, diff viewer, metrics panel. Build this last, once the engine is trustworthy.
6. **README** — write up your answers to the three defend-this questions as actual design decisions, plus how to run the generator/scheduler/replanner/dashboard end to end (`docker compose up` should just work).
7. **Defense rehearsal** — run a compound disruption yourself (e.g., "Day-1 mass recruiter 3 hours late + one panel drops + 15 students withdraw") before the live session, to confirm your replanner and dashboard hold up under a messier-than-single-event scenario, since that's the style of injection they've told you to expect.

---

## 8. Notes for Claude Code (if used to implement this)

- Repo should be structured as: `generator/`, `scheduler/` (CP-SAT model + infeasibility reporting), `replanner/` (delta-solve + diff), `api/` (FastAPI endpoints wrapping the above), `dashboard/` (Next.js app), `docker-compose.yml`, `README.md`.
- The scheduler and replanner should share the same CP-SAT model-building code, parameterized by an optional "prior schedule" input and a churn-penalty weight — don't duplicate solver logic between initial-schedule and replan paths.
- Infeasibility/diagnostic messages should be generated from constraint metadata (tag each constraint with a human-readable reason at construction time), not reverse-engineered after the solve fails — this is the cleanest way to get specific diagnostics without a second manual analysis pass.
- Keep the diff/notify-list generation as a pure function operating on `(old_schedule, new_schedule)` — this makes it independently testable and reusable in both API responses and dashboard rendering.
- Prioritize implementation in the order given in section 7 — do not build dashboard UI before the solver's infeasibility and replan-diff behavior is verified with test data.

---

## 9. Solver performance — plan for this, don't discover it late

800 students × 35 companies × 4 days × 20 rooms is a non-trivial CP-SAT problem. Do not assume the first full-scale run will solve instantly.

- Set an explicit time limit: `solver.parameters.max_time_in_seconds = 30` (tune this — start higher while testing, tighten once you know real solve times).
- Always check `solver.StatusName()`. CP-SAT gives you three outcomes that matter here: `OPTIMAL` (best possible), `FEASIBLE` (a valid schedule found within the time limit, possibly not optimal), and `INFEASIBLE` (genuinely no valid assignment exists under current constraints). Your code and your diagnostics need to handle all three differently — `FEASIBLE` is not a bug, it's an expected outcome at this scale and should be reported as such (e.g., "schedule found, solver stopped at time limit, may not be optimal") rather than silently treated the same as `OPTIMAL`.
- Test at full scale (800 students, not a toy 20-student dataset) early — solver behavior at scale is qualitatively different, and you don't want to discover a multi-minute solve time the night before the defense.
- The replanner's warm-start should generally solve much faster than the initial full solve, since it starts from a near-feasible point — verify this is actually true in your implementation, since a warm start that isn't wired correctly will just re-solve from scratch and lose you the performance benefit.

## 10. Git history as a signal

Commit incrementally along the build order in section 7 (generator → basic scheduler → infeasibility handling → replanner → dashboard), rather than one large commit at the end. This costs nothing and gives graders who check commit history evidence that the project was actually built iteratively rather than assembled from a found solution.

## 11. Live defense — practical prep

- **Have the environment already running before the injection.** Baseline dataset generated, initial schedule already solved, dashboard already up. Don't spend defense time on `docker compose up` or waiting on the initial CP-SAT solve — that's dead air in a session that's timed and being evaluated.
- **Narrate your reasoning as you click, not just the result.** The defense is evaluating your decision process (which constraint you're relaxing, why the diff looks the way it does) as much as the final output. Silent clicking followed by "done" wastes the part of the exercise they're actually grading.
- **Test compound disruptions specifically, not just each type in isolation.** Their own example stacks three disruption types at once (a Day-1 mass recruiter running 3 hours late, one of its panels dropping, and 15 students withdrawing — all together). It's easy to build and test each of the four disruption types independently and never verify they compose correctly when triggered simultaneously. Do this before the defense, not during it.
- **Edge case to handle explicitly: withdrawal due to mid-day offer.** The spec calls this scenario out directly — a student accepts an offer and is done for the day. A withdrawal event should remove *all* of that student's remaining interviews for the rest of the day, not just the one interview slot the disruption event technically references. Getting this wrong (only cancelling one interview) is a subtle bug that looks fine in a simple test but is wrong the moment someone asks "so what happens to their other 3 interviews that afternoon?"
