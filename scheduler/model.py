"""
Panelist — CP-SAT scheduling model.

Shared model-building logic used by both the initial scheduler and the
replanner (see PLACEMENT_SCHEDULER_GUIDE.md section 8: "don't duplicate
solver logic between initial-schedule and replan paths").

FORMULATION NOTE — deliberate deviation from guide section 2.2
--------------------------------------------------------------
The guide suggests `assign[interview][room][slot][panel]` booleans. That does
not scale to the guide's own numbers: 2770 interviews x 20 rooms x 112 slots
is ~6.2M booleans before panels are indexed at all, and CP-SAT will not build
that model in reasonable time or memory.

This uses the idiomatic scheduling encoding instead:

  - Two variables per interview: `start` (an IntVar restricted to starts where
    the interview fits inside one contiguous work block) and `present` (a
    BoolVar, so an interview may go unscheduled rather than forcing
    infeasibility). No room or panel decision variables at all.
  - Rooms are interchangeable, so room capacity is ONE global Cumulative
    constraint (capacity = room count) rather than per-room booleans. Blocked
    room windows enter as fixed intervals consuming that capacity.
  - Panels are interchangeable within a company, so each company's panel limit
    is ONE Cumulative (capacity = panel_count).
  - Students get a NoOverlap over their own optional intervals.

Concrete room and panel identities are recovered after the solve by greedy
interval-graph colouring, which is exact here: for interval graphs greedy
left-to-right colouring uses exactly `max overlap` colours, and the Cumulative
constraints guarantee max overlap never exceeds the available count. The
assignment is verified post-hoc regardless (see `verify_schedule`).

Because `present` is optional and the objective maximises scheduled
interviews, an oversubscribed instance does not come back INFEASIBLE — it
comes back with unscheduled interviews, each of which gets an attributed
reason (see `diagnose_unscheduled`). That is a more specific and more useful
diagnostic than a solver-level infeasibility certificate.
"""

from collections import defaultdict

from ortools.sat.python import cp_model

from scheduler import timegrid

# Coordinator priority overrides (guide section 4, question 2).
#
# The algorithm's DEFAULT is the tier weighting below: when an oversubscribed
# instance cannot place everything, tier-1 mass recruiters are protected ahead
# of tier-3 niche ones. That default is a policy decision, and the guide is
# explicit that policy exceptions belong to the coordinator rather than to the
# code — so a company can be lifted above the tier order or dropped below it.
#
# PROTECT exceeds any tier difference (16 at best) and any churn penalty
# (40 at the heaviest), so a protected interview is kept even when keeping it
# costs a reshuffle. DEPRIORITISE leaves a weight of 1 rather than 0: the
# company still gets scheduled wherever there is room, it simply yields to
# everything else. Zero would tell the solver it is free to leave the company
# out even on an empty grid, which is not what "lower priority" means.
PRIORITY_LEVELS = ("protect", "normal", "deprioritise")
PROTECT_BONUS = 100
DEPRIORITISED_WEIGHT = 1


def panels_available(company, at):
    """Panels a company still has running at one instant.

    A blackout removes a panel from part of the week rather than lowering the
    count for all of it, so "how many panels" is a question about a moment.
    Shared with the replanner's lock validation, which asks exactly the same
    question of an already-run schedule — the arithmetic was duplicated, and
    the two copies have to agree or a replan is validated against different
    capacity than it is verified against.
    """
    out = sum(1 for w0, w1 in company.get("panel_blackouts", []) if w0 <= at < w1)
    return max(0, company["panel_count"] - out)


class SchedulingModel:
    def __init__(
        self,
        companies,
        students,
        rooms,
        config,
        prior_schedule=None,
        churn_penalty_weight=0,
        tier_bonus=2,
        locked=None,
        priority_overrides=None,
    ):
        self.companies = companies
        self.students = students
        self.rooms = rooms
        self.config = config
        # Read from the dataset, never hardcoded: see scheduler/timegrid.py.
        self.slots_raw = timegrid.slots_per_day_raw(config)
        self.prior_schedule = prior_schedule or {}
        self.churn_penalty_weight = churn_penalty_weight
        self.tier_bonus = tier_bonus
        # company_id -> "protect" | "deprioritise". Absent means the tier
        # default applies, so an empty mapping reproduces the old behaviour
        # exactly.
        self.priority_overrides = dict(priority_overrides or {})
        # Interviews that already happened: pinned to their prior time so a
        # replan cannot rewrite the past.
        self.locked = set(locked or ())

        self.model = cp_model.CpModel()
        self.constraint_reasons = {}   # constraint_id -> human-readable string

        self.interviews = []           # list of interview dicts
        # Interviews with no legal start slot at all. They never enter the CP
        # model, but they are still real demand: initialised here so callers
        # that skip build() (the diagnostics route) can read them safely.
        self.unplaceable = []
        self.start = {}                # interview_id -> IntVar
        self.present = {}              # interview_id -> BoolVar
        self.intervals = {}            # interview_id -> OptionalIntervalVar
        self.moved = {}                # interview_id -> BoolVar (replan only)

        self.company_by_id = {c["id"]: c for c in companies}
        self.student_by_id = {s["id"]: s for s in students}
        self.valid_starts = self._compute_valid_starts()

    # -- constraint provenance ---------------------------------------------

    def _tag(self, kind, scope, reason):
        """Record a human-readable reason at construction time (guide s.8),
        so diagnostics never have to be reverse-engineered after a failure."""
        cid = f"{kind}:{scope}"
        self.constraint_reasons[cid] = reason
        return cid

    # -- time grid ----------------------------------------------------------

    def _work_blocks(self):
        """Contiguous runs of usable slots within a day (morning, afternoon).

        An interview may not span lunch or a day boundary, so it must fit
        entirely inside one block.
        """
        usable = sorted(self.config["usable_slots_per_day"])
        blocks, run = [], [usable[0]]
        for s in usable[1:]:
            if s == run[-1] + 1:
                run.append(s)
            else:
                blocks.append((run[0], run[-1] + 1))
                run = [s]
        blocks.append((run[0], run[-1] + 1))
        return blocks

    def _compute_valid_starts(self):
        """Absolute start slots, per duration, where the interview fits."""
        blocks = self._work_blocks()
        by_duration = defaultdict(list)
        durations = {c["duration_slots"] for c in self.companies}
        for dur in durations:
            for day in range(self.config["days"]):
                for lo, hi in blocks:
                    for s in range(lo, hi - dur + 1):
                        by_duration[dur].append(day * self.slots_raw + s)
        return by_duration

    def horizon(self):
        return timegrid.horizon(self.config)

    def demand(self):
        """Every interview the instance has to place, modelled or not.

        `self.interviews` holds only what reached the CP model; interviews
        whose company has no remaining window are moved to `self.unplaceable`
        and would otherwise disappear from every total. They are still demand,
        so anything reporting on the size of the problem counts both.
        """
        return self.interviews + self.unplaceable

    def valid_starts_for(self, interview):
        """Start slots legal for one interview, narrowed by its company's
        unavailable windows.

        Windows are explicit (from_slot, to_slot) absolute ranges rather than a
        single "available from" watermark. A company arriving three hours late
        on Day 4 is unavailable for Day 4 up to 12:00 and nothing else — a
        watermark would forbid every earlier day too, silently dragging Day 1
        interviews across the week. Windows also compose, so several lateness
        events on different days stack correctly.

        Used for both the model domain and the diagnostics, so the two never
        disagree about what was actually possible.
        """
        starts = self.valid_starts[interview["duration_slots"]]
        company = self.company_by_id[interview["company_id"]]
        windows = company.get("unavailable_windows")
        if not windows:
            return starts
        dur = interview["duration_slots"]
        return [
            s for s in starts
            if not any(timegrid.overlaps(s, s + dur, w0, w1)
                       for w0, w1 in windows)
        ]

    # -- model construction -------------------------------------------------

    def _build_interviews(self):
        """One interview per (company, shortlisted student) pair.

        CGPA cutoffs are enforced here rather than as a solver constraint: an
        interview that would violate a cutoff is never created. Violations are
        counted so the guarantee is verified rather than assumed.
        """
        cutoff_violations = []
        for c in self.companies:
            for sid in c["shortlist"]:
                student = self.student_by_id.get(sid)
                if student is None:
                    continue
                if student["cgpa"] < c["cgpa_cutoff"]:
                    cutoff_violations.append((c["id"], sid))
                    continue
                self.interviews.append({
                    "id": f"{c['id']}~{sid}",
                    "company_id": c["id"],
                    "student_id": sid,
                    "duration_slots": c["duration_slots"],
                    "tier": c["tier"],
                })
        self._tag(
            "cgpa_cutoff", "all",
            "Interviews are only created for students meeting the company's "
            "CGPA cutoff; this constraint is never violated by construction.",
        )
        self.cutoff_violations = cutoff_violations

    def build(self):
        self._build_interviews()
        horizon = self.horizon()

        # --- decision variables -------------------------------------------
        self.unplaceable = []
        for iv in self.interviews:
            iid, dur = iv["id"], iv["duration_slots"]
            allowed = self.valid_starts_for(iv)
            if not allowed:
                # The company has no remaining window this interview fits in.
                # Recorded rather than silently dropped, so the diagnostics can
                # say so explicitly.
                self.unplaceable.append(iv)
                continue
            prior = self.prior_schedule.get(iid)
            if iid in self.locked and prior is not None:
                # Pin by narrowing the domain to a single value rather than
                # adding `start == prior` as a constraint. Hundreds of equality
                # constraints make the solve markedly harder (it timed out at
                # UNKNOWN with 345 locked); a one-value domain propagates
                # immediately and shrinks the search space instead of growing
                # the constraint graph.
                start = self.model.NewIntVar(
                    prior["start"], prior["start"], f"start_{iid}"
                )
            else:
                domain = cp_model.Domain.FromValues(allowed)
                start = self.model.NewIntVarFromDomain(domain, f"start_{iid}")
            present = self.model.NewBoolVar(f"present_{iid}")
            end = self.model.NewIntVar(0, horizon, f"end_{iid}")
            self.model.Add(end == start + dur)
            interval = self.model.NewOptionalIntervalVar(
                start, dur, end, present, f"iv_{iid}"
            )
            self.start[iid] = start
            self.present[iid] = present
            self.intervals[iid] = interval

            if iid in self.locked and prior is not None:
                self.model.Add(present == 1)

        # Interviews with no legal slot never enter the model at all.
        self.interviews = [
            iv for iv in self.interviews if iv["id"] in self.start
        ]
        if self.unplaceable:
            self._tag(
                "company_window", "unplaceable",
                f"{len(self.unplaceable)} interviews have no start slot inside "
                f"their company's remaining availability window.",
            )
        if self.locked:
            self._tag(
                "locked", "past",
                f"{len(self.locked)} interviews already under way or completed "
                f"are pinned to their original time and cannot be replanned.",
            )

        self._tag(
            "duration_fit", "all",
            "Every interview starts only at a slot where its full duration "
            "fits inside one contiguous work block (no spanning lunch or "
            "a day boundary).",
        )

        # --- students: no double-booking ----------------------------------
        by_student = defaultdict(list)
        for iv in self.interviews:
            by_student[iv["student_id"]].append(iv["id"])
        for sid, iids in by_student.items():
            if len(iids) > 1:
                self.model.AddNoOverlap([self.intervals[i] for i in iids])
                self._tag(
                    "student_no_overlap", sid,
                    f"Student {sid} is on {len(iids)} shortlists; their "
                    f"interviews may not overlap in time.",
                )

        # --- companies: panel capacity ------------------------------------
        by_company = defaultdict(list)
        for iv in self.interviews:
            by_company[iv["company_id"]].append(iv["id"])
        for cid, iids in by_company.items():
            company = self.company_by_id[cid]
            panels = company["panel_count"]
            panel_intervals = [self.intervals[i] for i in iids]
            demands = [1] * len(iids)

            # A panel lost partway through the week consumes capacity from
            # that moment on, the same way a blocked room does. Lowering
            # panel_count for the whole week instead would retroactively
            # invalidate interviews that already ran on that panel.
            for n, (w0, w1) in enumerate(company.get("panel_blackouts", [])):
                panel_intervals.append(
                    self.model.NewIntervalVar(
                        w0, w1 - w0, w1, f"panel_out_{cid}_{n}"
                    )
                )
                demands.append(1)
                self._tag(
                    "panel_blackout", f"{cid}#{n}",
                    f"{company['name']} is down one panel from slot {w0} "
                    f"onward.",
                )

            self.model.AddCumulative(panel_intervals, demands, panels)
            self._tag(
                "panel_capacity", cid,
                f"{company['name']} runs at most {panels} interviews "
                f"concurrently ({panels} panels).",
            )

        # --- rooms: global capacity, minus blocked windows ----------------
        room_intervals = [self.intervals[iv["id"]] for iv in self.interviews]
        demands = [1] * len(room_intervals)
        for room in self.rooms:
            for w in room.get("blocked_windows", []):
                lo = w["day"] * self.slots_raw + w["from_slot"]
                hi = w["day"] * self.slots_raw + w["to_slot"]
                room_intervals.append(
                    self.model.NewIntervalVar(
                        lo, hi - lo, hi, f"blocked_{room['id']}_{w['day']}"
                    )
                )
                demands.append(1)
                self._tag(
                    "room_blocked", f"{room['id']}@d{w['day']}",
                    f"{room['name']} unavailable on day {w['day']} "
                    f"slots {w['from_slot']}-{w['to_slot']} ({w['reason']}).",
                )
        self.model.AddCumulative(room_intervals, demands, len(self.rooms))
        self._tag(
            "room_capacity", "global",
            f"At most {len(self.rooms)} interviews run concurrently "
            f"(room count), less any rooms blocked at that time.",
        )

        # --- objective ------------------------------------------------------
        self._build_objective()
        return self

    def interview_weight(self, interview):
        """What one scheduled interview is worth to the objective.

        The tier term is the algorithm's own policy; the override is the
        coordinator's exception to it. Kept as one function so the two cannot
        drift apart, and so the diagnostics can report the same number the
        solver optimised against.
        """
        level = self.priority_overrides.get(interview["company_id"])
        if level == "deprioritise":
            return DEPRIORITISED_WEIGHT
        weight = 10 + self.tier_bonus * (4 - interview["tier"])
        if level == "protect":
            weight += PROTECT_BONUS
        return weight

    def _build_objective(self):
        """Maximise scheduled interviews; penalise churn against a prior plan.

        Maximisation (rather than pure feasibility) is what lets one model
        serve both a solvable instance and an oversubscribed one: a feasible
        instance schedules everything, an oversubscribed one returns the best
        partial schedule plus an attributed shortfall.
        """
        terms = [
            self.interview_weight(iv) * self.present[iv["id"]]
            for iv in self.interviews
        ]

        for cid, level in sorted(self.priority_overrides.items()):
            company = self.company_by_id.get(cid)
            if company is None or level not in PRIORITY_LEVELS:
                continue
            if level == "protect":
                self._tag(
                    "priority_override", cid,
                    f"The coordinator protected {company['name']}: its "
                    f"interviews are kept ahead of the tier order, and ahead "
                    f"of the cost of moving others to make room.",
                )
            elif level == "deprioritise":
                self._tag(
                    "priority_override", cid,
                    f"The coordinator deprioritised {company['name']}: it is "
                    f"scheduled wherever there is room, but yields to every "
                    f"other company when capacity is short.",
                )

        if self.prior_schedule and self.churn_penalty_weight:
            for iv in self.interviews:
                iid = iv["id"]
                prior = self.prior_schedule.get(iid)
                if prior is None:
                    continue
                same = self.model.NewBoolVar(f"same_{iid}")
                self.model.Add(
                    self.start[iid] == prior["start"]
                ).OnlyEnforceIf(same)
                self.model.Add(
                    self.start[iid] != prior["start"]
                ).OnlyEnforceIf(same.Not())
                moved = self.model.NewBoolVar(f"moved_{iid}")
                self.model.Add(moved == 1 - same)
                self.moved[iid] = moved
                terms.append(-self.churn_penalty_weight * moved)
            self._tag(
                "churn_penalty", "global",
                f"Each interview moved from its prior time costs "
                f"{self.churn_penalty_weight}, so the solver prefers the "
                f"smallest change that resolves the disruption.",
            )

        self.model.Maximize(sum(terms))

    # -- warm start ---------------------------------------------------------

    def add_warm_start(self):
        """Seed the solver with the prior schedule (guide section 2.3).

        Only hints that are still legal are given. After a disruption some
        prior placements are no longer in their variable's domain (the company
        is now unavailable then, say); hinting those hands CP-SAT a partially
        infeasible starting point it has to discover and unwind, which costs
        more than the hint saves.
        """
        if not self.prior_schedule:
            return 0
        legal = {
            iv["id"]: set(self.valid_starts_for(iv)) for iv in self.interviews
        }
        seeded = 0
        for iid, prior in self.prior_schedule.items():
            if iid not in self.start:
                continue
            if iid not in self.locked and prior["start"] not in legal.get(iid, ()):
                continue
            self.model.AddHint(self.start[iid], prior["start"])
            self.model.AddHint(self.present[iid], 1)
            seeded += 1
        return seeded

    # -- solving ------------------------------------------------------------

    def solve(self, time_limit_seconds=30, workers=8):
        solver = cp_model.CpSolver()
        solver.parameters.max_time_in_seconds = time_limit_seconds
        solver.parameters.num_search_workers = workers
        status = solver.Solve(self.model)
        return status, solver

    def status_report(self, status, solver):
        """Distinguish OPTIMAL / FEASIBLE / INFEASIBLE (guide section 9).

        FEASIBLE is an expected outcome at this scale, not a bug, and must not
        be reported as if it were OPTIMAL.
        """
        name = solver.StatusName(status)
        if status == cp_model.OPTIMAL:
            note = "Optimal schedule found."
        elif status == cp_model.FEASIBLE:
            note = (
                f"Valid schedule found, but the solver hit its "
                f"{solver.parameters.max_time_in_seconds:.0f}s time limit — "
                f"this schedule is feasible but may not be optimal."
            )
        elif status == cp_model.INFEASIBLE:
            note = (
                "No valid schedule exists under the current hard constraints. "
                "This is a genuine model-level infeasibility, not a timeout."
            )
        elif status == cp_model.UNKNOWN:
            note = (
                f"Solver found no schedule within its "
                f"{solver.parameters.max_time_in_seconds:.0f}s limit. This is "
                f"a timeout, NOT proof that no schedule exists — retry with a "
                f"longer limit before concluding the problem is infeasible."
            )
        else:
            note = f"Solver returned {name} without a usable schedule."
        return {
            "status": name,
            "usable": status in (cp_model.OPTIMAL, cp_model.FEASIBLE),
            "optimal": status == cp_model.OPTIMAL,
            "note": note,
            "timed_out": status == cp_model.UNKNOWN,
            "wall_time_seconds": round(solver.WallTime(), 2),
        }

    # -- extraction ---------------------------------------------------------

    def extract_schedule(self, solver):
        """Recover concrete (day, slot, room, panel) assignments.

        Rooms and panels are assigned by greedy interval colouring; the
        Cumulative constraints guarantee enough of each exist at every instant.
        """
        # Interviews with no legal slot never entered the model, so the solver
        # has nothing to say about them — but they are unscheduled in exactly
        # the sense the caller means, and omitting them makes pct_scheduled
        # report 100% while interviews were silently dropped.
        scheduled, unscheduled = [], list(self.unplaceable)
        for iv in self.interviews:
            iid = iv["id"]
            if not solver.Value(self.present[iid]):
                unscheduled.append(iv)
                continue
            abs_start = solver.Value(self.start[iid])
            scheduled.append({
                **iv,
                "start": abs_start,
                "end": abs_start + iv["duration_slots"],
                "day": abs_start // self.slots_raw,
                "slot": abs_start % self.slots_raw,
            })

        self._assign_panels(scheduled)
        self._assign_rooms(scheduled)
        return scheduled, unscheduled

    def _assign_panels(self, scheduled):
        """Greedy colouring within each company (panels are interchangeable)."""
        by_company = defaultdict(list)
        for a in scheduled:
            by_company[a["company_id"]].append(a)
        for items in by_company.values():
            free_at = []  # panel index -> slot it frees up
            for a in sorted(items, key=lambda x: x["start"]):
                # Same stability preference as rooms: keep the prior panel
                # where it is still free.
                prior = self.prior_schedule.get(a["id"], {}).get("panel")
                order = list(range(len(free_at)))
                if prior is not None and prior in order:
                    order.remove(prior)
                    order.insert(0, prior)

                placed = False
                for p in order:
                    if free_at[p] <= a["start"]:
                        free_at[p] = a["end"]
                        a["panel"] = p
                        placed = True
                        break
                if not placed:
                    a["panel"] = len(free_at)
                    free_at.append(a["end"])

    def _blocked_windows(self):
        blocked = defaultdict(list)
        for room in self.rooms:
            for w in room.get("blocked_windows", []):
                lo = w["day"] * self.slots_raw + w["from_slot"]
                hi = w["day"] * self.slots_raw + w["to_slot"]
                blocked[room["id"]].append((lo, hi))
        return blocked

    def _assign_rooms(self, scheduled):
        """Assign concrete rooms, greedy first with an exact fallback.

        Greedy interval colouring is exact only while rooms are fully
        interchangeable. Blocked windows break that: a room free by time may
        still be unavailable, so greedy can strand an interview that a
        different assignment would have placed. Greedy stays as the fast path
        (it succeeds on most instances), and when it strands anything the
        whole assignment is redone exactly as a colouring-with-forbidden-
        colours CP-SAT solve. Times are already fixed at this point, so the
        fallback is small and fast.
        """
        if self._greedy_rooms(scheduled):
            return
        self._exact_rooms(scheduled)

    def _room_free(self, blocked, rid, lo, hi):
        """Is room `rid` clear of a blocking window over [lo, hi)?

        A method rather than a closure defined identically in both the greedy
        and the exact assignment: the two must agree about what "blocked"
        means, and two copies of the predicate is how they stop agreeing.
        """
        return not any(timegrid.overlaps(lo, hi, b0, b1) for b0, b1 in blocked[rid])

    def _greedy_rooms(self, scheduled):
        blocked = self._blocked_windows()

        free_at = {r["id"]: 0 for r in self.rooms}
        ok = True
        for a in sorted(scheduled, key=lambda x: x["start"]):
            # Prefer the room this interview was already in. Without this a
            # replan reshuffles nearly every room even where the time did not
            # change, and the coordinator sees the whole board move for a
            # two-hour delay.
            prior = self.prior_schedule.get(a["id"], {}).get("room")
            candidates = [r["id"] for r in self.rooms]
            if prior in free_at:
                candidates.remove(prior)
                candidates.insert(0, prior)

            for rid in candidates:
                if free_at[rid] <= a["start"] and self._room_free(
                    blocked, rid, a["start"], a["end"]
                ):
                    free_at[rid] = a["end"]
                    a["room"] = rid
                    break
            else:
                a["room"] = None
                ok = False
        return ok

    def _exact_rooms(self, scheduled):
        """Exact room assignment: one IntVar per interview, domain restricted
        to rooms free of a blocking window, plus pairwise inequality over
        interviews that overlap in time."""
        blocked = self._blocked_windows()
        room_ids = [r["id"] for r in self.rooms]

        m = cp_model.CpModel()
        var = {}
        for a in scheduled:
            allowed = [
                i for i, rid in enumerate(room_ids)
                if self._room_free(blocked, rid, a["start"], a["end"])
            ]
            var[a["id"]] = m.NewIntVarFromDomain(
                cp_model.Domain.FromValues(allowed), f"room_{a['id']}"
            )

        # Sweep line: pair each interview only with those still active.
        active = []
        for a in sorted(scheduled, key=lambda x: x["start"]):
            active = [b for b in active if b["end"] > a["start"]]
            for b in active:
                m.Add(var[a["id"]] != var[b["id"]])
            active.append(a)

        solver = cp_model.CpSolver()
        solver.parameters.max_time_in_seconds = 30
        solver.parameters.num_search_workers = 8
        status = solver.Solve(m)
        if status in (cp_model.OPTIMAL, cp_model.FEASIBLE):
            for a in scheduled:
                a["room"] = room_ids[solver.Value(var[a["id"]])]
        else:
            # Genuinely unassignable; verify_schedule will surface it.
            for a in scheduled:
                a.setdefault("room", None)

    # -- diagnostics --------------------------------------------------------

    def capacity_analysis(self, scheduled, unscheduled):
        """Structural headline: is this instance short on capacity, and where?

        Per-company attribution is only meaningful once the coordinator knows
        whether the shortfall is structural (no schedule could place these) or
        local (a specific window is jammed). This answers that first, and names
        the specific saturated windows the way guide section 2.2 asks for
        ("Room capacity exceeded for Day 2, 14:00-16:00 by 6 interviews")
        rather than repeating "rooms are full" per company.
        """
        days = self.config["days"]
        slots_per_day = self.config["slots_per_day_count"]
        n_rooms = len(self.rooms)
        slot_minutes = self.config["slot_minutes"]

        demand = self.demand()
        demand_slots = sum(iv["duration_slots"] for iv in demand)
        capacity_slots = n_rooms * slots_per_day * days

        load = defaultdict(int)
        for a in scheduled:
            for t in range(a["start"], a["end"]):
                load[t] += 1
        for room in self.rooms:
            for w in room.get("blocked_windows", []):
                base = w["day"] * self.slots_raw
                for t in range(base + w["from_slot"], base + w["to_slot"]):
                    load[t] += 1

        usable = set(self.config["usable_slots_per_day"])

        # Contiguous runs of fully-saturated slots, as clock windows.
        saturated = sorted(
            t for t, n in load.items()
            if n >= n_rooms and (t % self.slots_raw) in usable
        )
        # Runs must break at a day boundary as well as at a gap, or a window
        # can wrap from one day's afternoon into the next day's morning and
        # report a nonsense clock range.
        windows, run = [], []
        for t in saturated:
            same_day = run and (t // self.slots_raw) == (
                run[-1] // self.slots_raw
            )
            if same_day and t == run[-1] + 1:
                run.append(t)
            else:
                if run:
                    windows.append(run)
                run = [t]
        if run:
            windows.append(run)

        # Contention per window: how many *distinct* unscheduled interviews
        # could legally have started inside it. Counting every saturated slot
        # an interview could use would just re-report the global unscheduled
        # total on every window.
        window_report = []
        for w in sorted(windows, key=len, reverse=True)[:6]:
            day = w[0] // self.slots_raw
            frm = timegrid.clock(self.config, w[0] % self.slots_raw)
            to = timegrid.clock(self.config, (w[-1] % self.slots_raw) + 1)
            window_report.append({
                "day": day + 1,
                "from": frm,
                "to": to,
                "slots": len(w),
                "minutes": len(w) * slot_minutes,
                "text": (
                    f"Room capacity exceeded on Day {day + 1}, {frm}-{to} "
                    f"({len(w) * slot_minutes}min): all {n_rooms} rooms "
                    f"committed for every slot in the window."
                ),
            })

        per_day = {}
        for d in range(days):
            used = sum(
                n for t, n in load.items() if t // self.slots_raw == d
            )
            per_day[d + 1] = round(100.0 * used / (n_rooms * slots_per_day), 1)

        ratio = demand_slots / capacity_slots if capacity_slots else 0.0
        structural = ratio > 1.0
        return {
            "structural_shortfall": structural,
            "unscheduled_count": len(unscheduled),
            "demand_slot_units": demand_slots,
            "capacity_slot_units": capacity_slots,
            "load_ratio": round(ratio, 2),
            "max_schedulable_estimate": (
                int(len(demand) / ratio) if structural else None
            ),
            "headline": (
                f"Instance is oversubscribed {ratio:.2f}x: {demand_slots} "
                f"slot-units of interview demand against {capacity_slots} "
                f"room-slot-units of capacity. No schedule can place all "
                f"{len(demand)} interviews — the shortfall is "
                f"structural, not a solver failure."
                if structural else
                f"Capacity is sufficient in aggregate (load {ratio:.2f}x); "
                f"any shortfall is a placement conflict, not a capacity limit."
            ),
            "room_utilization_per_day_pct": per_day,
            "saturated_windows": window_report,
        }

    def _window_label(self, w0, w1):
        """An absolute (from, to) slot pair as a readable clock range.

        End-exclusive, so a window that stops on a day boundary is reported as
        the end of the day it actually covers rather than 00:00 of the next
        one — the same wrap `timegrid.clock` exists to avoid.
        """
        d0, s0 = timegrid.split(self.config, w0)
        d1, s1 = timegrid.split(self.config, w1)
        if s1 == 0 and d1 > d0:
            d1, s1 = d1 - 1, self.slots_raw
        start = timegrid.stamp(self.config, d0, s0)
        end = timegrid.stamp(self.config, d1, s1)
        return start if start == end else f"{start}\u2009\u2013\u2009{end}"

    def diagnose_unscheduled(self, scheduled, unscheduled):
        """Attribute each unscheduled interview to a specific saturated resource.

        This is the difference between "couldn't schedule 12 interviews" and
        "Vandelay Corp: 44 interviews, 1 panel, ceiling is 37 — short by 7"
        (guide section 5, item 2). Causes come from the constraint tags
        recorded at construction time, not from post-hoc guesswork.
        """
        if not unscheduled:
            return []

        slots_per_day = self.config["slots_per_day_count"]
        days = self.config["days"]

        # Occupancy of the shared room pool, per absolute slot.
        room_load = defaultdict(int)
        for a in scheduled:
            for t in range(a["start"], a["end"]):
                room_load[t] += 1

        # Per-company panel occupancy.
        panel_load = defaultdict(lambda: defaultdict(int))
        for a in scheduled:
            for t in range(a["start"], a["end"]):
                panel_load[a["company_id"]][t] += 1

        # Per-student busy slots.
        student_busy = defaultdict(set)
        for a in scheduled:
            student_busy[a["student_id"]].update(range(a["start"], a["end"]))

        findings = defaultdict(lambda: {
            "count": 0, "causes": defaultdict(int), "students": []
        })

        for iv in unscheduled:
            cid = iv["company_id"]
            company = self.company_by_id[cid]
            dur = iv["duration_slots"]
            starts = self.valid_starts_for(iv)
            if not starts:
                entry = findings[cid]
                entry["count"] += 1
                entry["causes"]["company_window"] += 1
                if len(entry["students"]) < 3:
                    entry["students"].append(iv["student_id"])
                continue

            panel_blocked = room_blocked = student_blocked = 0
            for s in starts:
                window = range(s, s + dur)
                if any(
                    panel_load[cid][t] >= company["panel_count"] for t in window
                ):
                    panel_blocked += 1
                elif any(room_load[t] >= len(self.rooms) for t in window):
                    room_blocked += 1
                elif any(t in student_busy[iv["student_id"]] for t in window):
                    student_blocked += 1

            entry = findings[cid]
            entry["count"] += 1
            if panel_blocked >= max(room_blocked, student_blocked):
                entry["causes"]["panel_capacity"] += 1
            elif room_blocked >= student_blocked:
                entry["causes"]["room_capacity"] += 1
            else:
                entry["causes"]["student_conflict"] += 1
            if len(entry["students"]) < 3:
                entry["students"].append(iv["student_id"])

        # Per-company panel ceiling — an exact, checkable bound.
        report = []
        for cid, entry in sorted(
            findings.items(), key=lambda kv: -kv[1]["count"]
        ):
            company = self.company_by_id[cid]
            ceiling = (
                company["panel_count"] * slots_per_day * days
            ) // company["duration_slots"]
            demand = len(company["shortlist"])
            dominant = max(entry["causes"].items(), key=lambda kv: kv[1])[0]

            if dominant == "panel_capacity" and demand > ceiling:
                reason = (
                    f"{company['name']} needs {demand} interviews but "
                    f"{company['panel_count']} panel(s) x {slots_per_day} slots "
                    f"x {days} days / {company['duration_slots']} slots each "
                    f"= {ceiling} max — short by {demand - ceiling}."
                )
            elif dominant == "panel_capacity":
                reason = (
                    f"{company['name']}'s {company['panel_count']} panel(s) are "
                    f"saturated at every remaining slot that fits a "
                    f"{company['interview_minutes']}min interview."
                )
            elif dominant == "room_capacity":
                reason = (
                    f"All {len(self.rooms)} rooms are occupied across every "
                    f"slot where {company['name']} could still place these "
                    f"interviews."
                )
            elif dominant == "company_window":
                # These never reached the solver at all: the company is
                # unavailable for every slot a full interview would fit in.
                # Without its own branch this fell through to the student
                # message below and blamed the cohort for the company's
                # absence — with the room-capacity tag attached, no less.
                windows = company.get("unavailable_windows") or []
                when = "; ".join(
                    self._window_label(w0, w1) for w0, w1 in windows[:3]
                ) or "the whole week"
                reason = (
                    f"{company['name']} is unavailable for every slot that "
                    f"fits a {company['interview_minutes']}min interview "
                    f"({when}). These {entry['count']} interview(s) have no "
                    f"legal time left, whatever the rooms and panels are "
                    f"doing — the cause is the company's availability, not "
                    f"contention."
                )
            else:
                reason = (
                    f"Students already have interviews filling every slot "
                    f"compatible with {company['name']} "
                    f"(e.g. {', '.join(entry['students'])})."
                )

            if dominant == "panel_capacity":
                tag = f"panel_capacity:{cid}"
            elif dominant == "company_window":
                # Recorded here rather than in build(): unplaceability is only
                # known once a company's windows are consulted, and the
                # diagnostics route reaches this without building the model.
                # The tag states the CONSTRAINT; `reason` states this
                # company's situation under it, so the two do not duplicate.
                tag = self._tag(
                    "company_window", cid,
                    f"An interview may only start inside its company's "
                    f"remaining availability; {company['name']} has no such "
                    f"slot wide enough for {company['interview_minutes']}min.",
                )
            else:
                tag = "room_capacity:global"
            report.append({
                "company_id": cid,
                "company": company["name"],
                "unscheduled": entry["count"],
                "demand": demand,
                "dominant_cause": dominant,
                "cause_breakdown": dict(entry["causes"]),
                "constraint_tag": self.constraint_reasons.get(tag),
                "reason": reason,
                "example_students": entry["students"],
            })
        return report

    # -- verification -------------------------------------------------------

    def verify_schedule(self, scheduled):
        """Independently re-check every hard constraint on the output.

        The solver is trusted, but the greedy room/panel colouring is not —
        this proves the recovered assignment is actually valid.
        """
        errors = []
        seen = defaultdict(list)

        for a in scheduled:
            if a.get("room") is None:
                errors.append(
                    f"{a['id']}: no room could be assigned at slot {a['start']}"
                )
            seen["room", a.get("room")].append(a)
            seen["student", a["student_id"]].append(a)
            seen["panel", (a["company_id"], a.get("panel"))].append(a)

        for (kind, key), items in seen.items():
            if key is None:
                continue
            items.sort(key=lambda x: x["start"])
            for a, b in zip(items, items[1:]):
                if b["start"] < a["end"]:
                    errors.append(
                        f"{kind} {key} double-booked: {a['id']} "
                        f"({a['start']}-{a['end']}) overlaps {b['id']} "
                        f"({b['start']}-{b['end']})"
                    )

        # Cutoffs (guaranteed by construction — verified, not assumed).
        for a in scheduled:
            company = self.company_by_id[a["company_id"]]
            student = self.student_by_id[a["student_id"]]
            if student["cgpa"] < company["cgpa_cutoff"]:
                errors.append(
                    f"{a['id']}: cgpa {student['cgpa']} below cutoff "
                    f"{company['cgpa_cutoff']}"
                )

        # Availability windows. Overlap and cutoffs were checked above; a room
        # that is free by time can still be unavailable, and a company can be
        # absent from an hour it has capacity in. Both enter the model as
        # constraints and both are re-derived by the greedy colouring, which is
        # exactly the code this method exists not to trust.
        blocked = self._blocked_windows()
        for a in scheduled:
            rid = a.get("room")
            if rid is not None:
                for b0, b1 in blocked[rid]:
                    if timegrid.overlaps(a["start"], a["end"], b0, b1):
                        errors.append(
                            f"{a['id']}: room {rid} is blocked over "
                            f"{self._window_label(b0, b1)}"
                        )
            company = self.company_by_id[a["company_id"]]
            for w0, w1 in company.get("unavailable_windows", []):
                if timegrid.overlaps(a["start"], a["end"], w0, w1):
                    errors.append(
                        f"{a['id']}: {company['name']} is unavailable over "
                        f"{self._window_label(w0, w1)}"
                    )

        # Panel capacity against mid-week blackouts. `_assign_panels` colours
        # by index without knowing a panel walked out, so the index alone
        # cannot show this: what matters is that the number running at once
        # never exceeds the number still standing.
        by_company = defaultdict(list)
        for a in scheduled:
            by_company[a["company_id"]].append(a)
        for cid, items in by_company.items():
            company = self.company_by_id[cid]
            if not company.get("panel_blackouts"):
                continue
            for probe in items:
                at = probe["start"]
                concurrent = sum(1 for o in items if o["start"] <= at < o["end"])
                available = panels_available(company, at)
                if concurrent > available:
                    errors.append(
                        f"{company['name']}: {concurrent} interview(s) "
                        f"running at {self._window_label(at, at)} with only "
                        f"{available} panel(s) still standing"
                    )
                    break
        return errors
