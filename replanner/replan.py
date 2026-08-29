"""Replanner: resolve disruptions to a live schedule with minimal disturbance.

Disruption types:
- company_late:      company arrives N hours late on a given day
- panel_drop:        a panel drops out
- student_withdraw:  a student withdraws
- room_unavailable:  a room becomes unavailable
plus the roster amendments company_add / company_remove / shortlist_add /
shortlist_remove.

A replan is the same CP-SAT model as the initial solve, re-run with
`prior_schedule` set and a churn penalty in the objective, warm-started from
the existing schedule. This module only translates disruption events into
problem-input changes and turns the two schedules into an actionable diff.

Nothing is auto-applied: `replan()` returns a proposal, and `apply_proposal()`
commits it. Churn is capped (default 10% of the prior day's appointments);
a fix needing more is re-solved under a much heavier churn penalty so the
coordinator gets both options.
"""

import copy

from scheduler import timegrid
from scheduler.metrics import compute_churn
from scheduler.model import SchedulingModel, panels_available

# Light enough that the solver still reshuffles to keep interviews scheduled,
# heavy enough to prefer stability.
DEFAULT_CHURN_WEIGHT = 4
# Used for the "looser fix" retry when the first proposal blows the cap.
STABILITY_CHURN_WEIGHT = 40


class DisruptionError(ValueError):
    """Raised when a disruption event does not name a real entity."""


# --- disruption application ------------------------------------------------

def apply_disruption(dataset, disruption, prior_schedule, now_slot=None):
    """Translate one disruption into changes to the problem input.

    Returns (description, dropped_interview_ids). The dropped ids are what the
    disruption itself removes -- a withdrawing student's interviews, say -- so
    churn accounting must not charge them against the coordinator's cap.

    `dataset` is mutated in place; `replan` copies before calling.
    """
    kind = disruption.get("type")
    companies = {c["id"]: c for c in dataset["companies"]}
    rooms = {r["id"]: r for r in dataset["rooms"]}
    slot_minutes = dataset["config"]["slot_minutes"]

    if kind == "company_late":
        cid = disruption["company_id"]
        if cid not in companies:
            raise DisruptionError(f"unknown company {cid!r}")
        company = companies[cid]
        day = disruption.get("day", 0)
        hours = disruption["hours"]
        slots_late = round(hours * 60 / slot_minutes)
        day_start = timegrid.absolute(dataset["config"], day, 0)
        # Blocks only the delayed part of that day; lateness events on
        # different days stack as separate windows.
        company.setdefault("unavailable_windows", []).append(
            (day_start, day_start + slots_late)
        )
        return (
            f"{company['name']} arrives {hours}h late on Day {day + 1}; "
            f"unavailable that day until slot {slots_late}.",
            [],
        )

    if kind == "panel_drop":
        cid = disruption["company_id"]
        if cid not in companies:
            raise DisruptionError(f"unknown company {cid!r}")
        company = companies[cid]
        count = disruption.get("count", 1)
        from_slot = disruption.get("from_slot")
        horizon = timegrid.horizon(dataset["config"])

        if from_slot is None:
            # No time given: the panel was never available this week.
            before = company["panel_count"]
            company["panel_count"] = max(0, before - count)
            return (
                f"{company['name']} loses {count} panel(s) for the whole "
                f"week: {before} -> {company['panel_count']}.",
                [],
            )

        # A panel that walks out at 14:00 does not retroactively un-run the
        # interviews it held at 10:00, so the loss is a blackout consuming
        # capacity from that moment on rather than a lower week-wide count.
        #
        # Clamped to the panels still running: each blackout is a fixed
        # interval against a Cumulative of capacity `panel_count`, so writing
        # more of them than there are panels makes the model INFEASIBLE rather
        # than over-dropping the company.
        existing = company.setdefault("panel_blackouts", [])
        already_out = sum(1 for _w0, w1 in existing if w1 > from_slot)
        available = max(0, company["panel_count"] - already_out)
        dropped = min(count, available)
        for _ in range(dropped):
            existing.append((from_slot, horizon))

        when = timegrid.stamp_absolute(dataset["config"], from_slot)
        if dropped < count:
            had = (f"it had only {available} still running"
                   if available else "all of its panels were already out")
            return (
                f"{company['name']} was asked to stand down {count} panel(s) "
                f"from {when}, but {had} — it interviews no one for the rest "
                f"of the week.",
                [],
            )
        return (
            f"{company['name']} loses {dropped} panel(s) from {when} onward "
            f"({available} -> {available - dropped} for the rest of the week).",
            [],
        )

    if kind == "student_withdraw":
        sid = disruption["student_id"]
        scope = disruption.get("scope", "day")
        from_slot = disruption.get("from_slot")
        return _withdraw_student(
            dataset, prior_schedule, sid, scope, from_slot, now_slot
        )

    if kind == "room_unavailable":
        rid = disruption["room_id"]
        if rid not in rooms:
            raise DisruptionError(f"unknown room {rid!r}")
        room = rooms[rid]
        day = disruption.get("day", 0)
        from_slot = disruption.get("from_slot", 0)
        to_slot = disruption.get(
            "to_slot", timegrid.slots_per_day_raw(dataset["config"]))
        room.setdefault("blocked_windows", []).append({
            "day": day,
            "from_slot": from_slot,
            "to_slot": to_slot,
            "reason": disruption.get("reason", "became unavailable"),
        })
        return (
            f"{room['name']} unavailable on Day {day + 1} "
            f"slots {from_slot}-{to_slot}.",
            [],
        )

    # --- roster amendments -------------------------------------------------
    # A roster edit invalidates the schedule just as a disruption does, so it
    # goes through the same propose/diff/apply path and is costed and approved
    # rather than left to silently make the live schedule wrong.

    if kind == "company_add":
        return _add_company(dataset, disruption)

    if kind == "company_remove":
        cid = disruption["company_id"]
        if cid not in companies:
            raise DisruptionError(f"unknown company {cid!r}")
        company = companies[cid]
        dropped = _drop_interviews(
            dataset, [f"{cid}~{sid}" for sid in list(company["shortlist"])])
        dataset["companies"] = [
            c for c in dataset["companies"] if c["id"] != cid]
        return (
            f"{company['name']} withdraws from placement week; "
            f"{len(dropped)} interview(s) cancelled and its "
            f"{company['panel_count']} panel(s) released.",
            dropped,
        )

    if kind == "shortlist_add":
        cid, sid = disruption["company_id"], disruption["student_id"]
        if cid not in companies:
            raise DisruptionError(f"unknown company {cid!r}")
        students = {s["id"]: s for s in dataset["students"]}
        if sid not in students:
            raise DisruptionError(f"unknown student {sid!r}")
        company, student = companies[cid], students[sid]
        if student["cgpa"] < company["cgpa_cutoff"]:
            raise DisruptionError(
                f"{sid} has CGPA {student['cgpa']}, below {company['name']}'s "
                f"cutoff of {company['cgpa_cutoff']} — cannot be shortlisted."
            )
        if sid in company["shortlist"]:
            raise DisruptionError(
                f"{sid} is already on {company['name']}'s shortlist.")
        company["shortlist"].append(sid)
        company["shortlist_size"] = len(company["shortlist"])
        student.setdefault("shortlisted_by", []).append(cid)
        return (
            f"{sid} added to {company['name']}'s shortlist "
            f"(CGPA {student['cgpa']} vs cutoff {company['cgpa_cutoff']}).",
            [],
        )

    if kind == "shortlist_remove":
        cid, sid = disruption["company_id"], disruption["student_id"]
        if cid not in companies:
            raise DisruptionError(f"unknown company {cid!r}")
        if sid not in companies[cid]["shortlist"]:
            raise DisruptionError(
                f"{sid} is not on {companies[cid]['name']}'s shortlist.")
        dropped = _drop_interviews(dataset, [f"{cid}~{sid}"])
        return (
            f"{sid} removed from {companies[cid]['name']}'s shortlist.",
            dropped,
        )

    raise DisruptionError(f"unknown disruption type {kind!r}")


def _add_company(dataset, spec):
    """Register a company that arrived after the dataset was generated.

    The shortlist may be given explicitly, or a size given and the eligible
    students picked by CGPA.
    """
    companies = {c["id"]: c for c in dataset["companies"]}
    students = {s["id"]: s for s in dataset["students"]}
    slot_minutes = dataset["config"]["slot_minutes"]

    cid = spec.get("company_id")
    if not cid:
        used = {c["id"] for c in dataset["companies"]}
        n = 0
        while f"C{n:03d}" in used:
            n += 1
        cid = f"C{n:03d}"
    if cid in companies:
        raise DisruptionError(f"company {cid!r} already exists")

    name = (spec.get("name") or "").strip()
    if not name:
        raise DisruptionError("a company needs a name")

    cutoff = float(spec.get("cgpa_cutoff", 7.0))
    minutes = int(spec.get("interview_minutes", 30))
    if minutes % slot_minutes:
        raise DisruptionError(
            f"interview length must be a multiple of {slot_minutes} minutes")

    explicit = spec.get("shortlist")
    if explicit:
        missing = [s for s in explicit if s not in students]
        if missing:
            raise DisruptionError(f"unknown students: {', '.join(missing[:5])}")
        below = [s for s in explicit if students[s]["cgpa"] < cutoff]
        if below:
            raise DisruptionError(
                f"{len(below)} of the named students are below the "
                f"{cutoff} cutoff (e.g. {below[0]})")
        shortlist = list(explicit)
    else:
        eligible = sorted(
            (s for s in dataset["students"] if s["cgpa"] >= cutoff),
            key=lambda s: -s["cgpa"],
        )
        size = int(spec.get("shortlist_size", 20))
        if size > len(eligible):
            raise DisruptionError(
                f"only {len(eligible)} students meet a {cutoff} cutoff, "
                f"but {size} were requested")
        shortlist = [s["id"] for s in eligible[:size]]

    company = {
        "id": cid,
        "name": name,
        "tier": int(spec.get("tier", 3)),
        "preferred_day": spec.get("day", 0),
        "cgpa_cutoff": round(cutoff, 2),
        "panel_count": int(spec.get("panel_count", 2)),
        "interview_minutes": minutes,
        "duration_slots": minutes // slot_minutes,
        "tech_focused": bool(spec.get("tech_focused", True)),
        "shortlist_size": len(shortlist),
        "shortlist": shortlist,
    }
    dataset["companies"].append(company)
    for sid in shortlist:
        students[sid].setdefault("shortlisted_by", []).append(cid)

    return (
        f"{name} registered late as {cid}: {len(shortlist)} students "
        f"shortlisted at CGPA {cutoff}+, {company['panel_count']} panel(s), "
        f"{minutes}min interviews.",
        [],
    )


def _withdraw_student(dataset, prior_schedule, sid, scope, from_slot,
                      now_slot=None):
    """Remove a withdrawing student's interviews.

    A student who accepts a mid-day offer is done for the whole day, not just
    the interview the event references.

    scope="day"  -> drop their remaining interviews from `from_slot` to the
                    end of that day; interviews on later days survive.
    scope="all"  -> the student leaves placement week entirely.
    """
    students = {s["id"]: s for s in dataset["students"]}
    if sid not in students:
        raise DisruptionError(f"unknown student {sid!r}")

    if scope == "all":
        already_ran = {
            iid for iid, a in prior_schedule.items()
            if a["student_id"] == sid
            and now_slot is not None and a["start"] < now_slot
        }
        dropped = _drop_interviews(
            dataset, [
                iid for iid in prior_schedule
                if prior_schedule[iid]["student_id"] == sid
                and iid not in already_ran
            ] + [
                f"{cid}~{sid}" for cid in students[sid]["shortlisted_by"]
                if f"{cid}~{sid}" not in already_ran
            ]
        )
        students[sid]["withdrawn"] = True
        return (
            f"Student {sid} withdraws from placement week entirely; "
            f"{len(dropped)} interview(s) cancelled.",
            dropped,
        )

    if from_slot is None:
        raise DisruptionError(
            "student_withdraw with scope='day' needs from_slot "
            "(the moment the offer was accepted)"
        )
    day, _ = timegrid.split(dataset["config"], from_slot)
    day_end = timegrid.absolute(dataset["config"], day + 1, 0)

    # A withdrawal cancels what is still ahead of the student, never what has
    # already been sat.
    effective_from = from_slot
    if now_slot is not None:
        effective_from = max(from_slot, now_slot)

    doomed = [
        iid for iid, a in prior_schedule.items()
        if a["student_id"] == sid and effective_from <= a["start"] < day_end
    ]
    already_ran = sum(
        1 for a in prior_schedule.values()
        if a["student_id"] == sid and from_slot <= a["start"] < effective_from
    )
    dropped = _drop_interviews(dataset, doomed)
    note = (
        f" ({already_ran} earlier interview(s) that day had already taken "
        f"place and stand.)" if already_ran else ""
    )
    return (
        f"Student {sid} accepted an offer and is done for Day {day + 1}; "
        f"all {len(dropped)} remaining interview(s) that day cancelled "
        f"(later days retained).{note}",
        dropped,
    )


def _drop_interviews(dataset, interview_ids):
    """Remove (company, student) pairs from the shortlists that create them."""
    targets = set(interview_ids)
    dropped = []
    by_company = {c["id"]: c for c in dataset["companies"]}
    students = {s["id"]: s for s in dataset["students"]}

    for iid in targets:
        if "~" not in iid:
            continue
        cid, sid = iid.split("~", 1)
        company = by_company.get(cid)
        if company and sid in company["shortlist"]:
            company["shortlist"].remove(sid)
            company["shortlist_size"] = len(company["shortlist"])
            dropped.append(iid)
        student = students.get(sid)
        if student and cid in student.get("shortlisted_by", []):
            student["shortlisted_by"].remove(cid)
    return dropped


# --- the replan itself -----------------------------------------------------

def replan(
    dataset,
    prior_scheduled,
    disruptions,
    churn_cap_pct=10,
    time_limit_seconds=30,
    now_slot=None,
    churn_penalty_weight=DEFAULT_CHURN_WEIGHT,
    priority_overrides=None,
):
    """Resolve disruptions with minimal disturbance.

    Returns a proposal dict. Nothing is committed — see `apply_proposal`.

    `now_slot` is the current moment: interviews that already started are
    locked, so a replan can never rewrite the past.

    `priority_overrides` carries the coordinator's exceptions to the tier
    default. They belong here rather than in the dataset: they are a decision
    about this fix, not a standing property of the company.
    """
    if isinstance(disruptions, dict):
        disruptions = [disruptions]

    prior_schedule = {a["id"]: a for a in prior_scheduled}
    # Captured before amendments so a company removed by this replan can still
    # be named in the notifications about its own cancellation.
    original_names = {c["id"]: c["name"] for c in dataset["companies"]}
    working = copy.deepcopy(dataset)

    applied, forced_removed = [], set()
    for d in disruptions:
        description, dropped = apply_disruption(
            working, d, prior_schedule, now_slot
        )
        applied.append(description)
        forced_removed.update(dropped)

    locked = set()
    if now_slot is not None:
        locked = {
            iid for iid, a in prior_schedule.items() if a["start"] < now_slot
        }

    # A disruption can contradict the past — dropping a panel that already ran
    # interviews, say. The solver would report that as a bare INFEASIBLE, so
    # check the locked set first and name the actual conflict.
    lock_conflicts = _validate_locks(working, prior_schedule, locked)
    if lock_conflicts:
        return {
            "ok": False,
            "disruptions_applied": applied,
            "lock_conflicts": lock_conflicts,
            "reason": (
                "This disruption contradicts interviews that have already "
                "happened, so no replan can satisfy it as stated: "
                + " ".join(lock_conflicts[:3])
                + (f" (+{len(lock_conflicts) - 3} more)"
                   if len(lock_conflicts) > 3 else "")
                + " Give the event a from_slot so it applies going forward "
                  "only, rather than for the whole week."
            ),
        }

    attempt, _last_report = _solve_attempt(
        working, prior_schedule, locked,
        churn_penalty_weight, time_limit_seconds,
        with_report=True, priority_overrides=priority_overrides,
    )
    if attempt is None:
        # "Proved impossible" and "ran out of time" are different answers.
        timed_out = _last_report.get("timed_out")
        return {
            "ok": False,
            "disruptions_applied": applied,
            "solver": _last_report,
            "timed_out": bool(timed_out),
            "reason": (
                "Solver ran out of time before finding any schedule for these "
                "disruptions. This is NOT proof that no fix exists — retry "
                "with a longer --time-limit."
                if timed_out else
                "No valid schedule exists after these disruptions, even "
                "allowing interviews to go unscheduled. The disruption "
                "removed capacity that hard constraints require."
            ),
        }

    proposal = _build_proposal(
        working, prior_scheduled, attempt, applied, churn_cap_pct,
        forced_removed, original_names,
    )

    # Over the cap: look for a lower-churn alternative rather than either
    # auto-applying a disruptive fix or failing outright.
    if proposal["cap_exceeded"]:
        looser = _solve_attempt(
            working, prior_schedule, locked,
            STABILITY_CHURN_WEIGHT, time_limit_seconds,
            priority_overrides=priority_overrides,
        )
        if looser is not None:
            alt = _build_proposal(
                working, prior_scheduled, looser, applied, churn_cap_pct,
                forced_removed, original_names,
            )
            base = proposal["diff"]["elective_churn_count"]
            reduced = alt["diff"]["elective_churn_count"]
            # Only offer the alternative if it is meaningfully cheaper —
            # sometimes the churn is irreducible at any penalty weight, and
            # two near-identical options are not a choice.
            if reduced < base * 0.9:
                alt["label"] = "minimal-churn alternative"
                proposal["label"] = "best-coverage proposal"
                proposal["alternative"] = alt
                proposal["authorization_prompt"] += (
                    f" Confirm to apply, or take the minimal-churn "
                    f"alternative below ({reduced} changes)."
                )
            else:
                proposal["churn_irreducible"] = True
                proposal["authorization_prompt"] += (
                    f" No cheaper fix exists: re-solving with a "
                    f"{STABILITY_CHURN_WEIGHT}x stability weight still needs "
                    f"{reduced} changes, so {base} is the floor for this "
                    f"disruption, not a solver preference. The real choice is "
                    f"to confirm or to relax a constraint."
                )
        else:
            proposal["authorization_prompt"] += (
                " No alternative could be solved within the time limit."
            )
    return proposal


def _validate_locks(working, prior_schedule, locked):
    """Check already-completed interviews against the post-disruption rules.

    Returns human-readable conflicts: cases where the disruption is
    retroactively impossible rather than merely hard, so extra solver time
    will not help but re-issuing the event from a later moment will.
    """
    if not locked:
        return []
    companies = {c["id"]: c for c in working["companies"]}
    conflicts = []

    # Panel count reduced below what already ran concurrently.
    by_company = {}
    for iid in locked:
        a = prior_schedule.get(iid)
        if a:
            by_company.setdefault(a["company_id"], []).append(a)

    for cid, items in by_company.items():
        company = companies.get(cid)
        if not company:
            continue
        # Checked at each already-run moment rather than against a week-wide
        # peak, because a blackout removes a panel from part of the week only.
        worst = None
        for probe in items:
            at = probe["start"]
            concurrent = sum(
                1 for other in items if other["start"] <= at < other["end"]
            )
            available = panels_available(company, at)
            if concurrent > available:
                shortfall = concurrent - available
                if worst is None or shortfall > worst[0]:
                    worst = (shortfall, concurrent, available, probe)
        if worst:
            _, concurrent, available, probe = worst
            when = timegrid.stamp_absolute(working["config"], probe["start"])
            conflicts.append(
                f"{company['name']} already had {concurrent} interview(s) "
                f"running at {when}, but the disruption leaves it "
                f"{available} panel(s) at that moment."
            )

        # Company made unavailable during an hour it has already interviewed.
        for w0, w1 in company.get("unavailable_windows", []):
            clashing = [a for a in items
                        if timegrid.overlaps(a["start"], a["end"], w0, w1)]
            if clashing:
                first = timegrid.stamp_absolute(
                    working["config"], clashing[0]["start"])
                conflicts.append(
                    f"{company['name']} is marked unavailable over a window "
                    f"in which {len(clashing)} of its interviews have already "
                    f"taken place (e.g. {first})."
                )
    return conflicts


def _solve_attempt(working, prior_schedule, locked, churn_weight, time_limit,
                   with_report=False, priority_overrides=None):
    model = SchedulingModel(
        working["companies"], working["students"], working["rooms"],
        working["config"],
        prior_schedule=prior_schedule,
        churn_penalty_weight=churn_weight,
        locked=locked,
        priority_overrides=priority_overrides,
    ).build()
    model.add_warm_start()
    status, solver = model.solve(time_limit_seconds=time_limit)
    report = model.status_report(status, solver)
    if not report["usable"]:
        return (None, report) if with_report else None
    scheduled, unscheduled = model.extract_schedule(solver)
    result = {
        "model": model,
        "solver": solver,
        "report": report,
        "scheduled": scheduled,
        "unscheduled": unscheduled,
        "churn_weight": churn_weight,
    }
    return (result, report) if with_report else result


def _build_proposal(working, prior_scheduled, attempt, applied, churn_cap_pct,
                    forced_removed=(), original_names=None):
    diff = compute_diff(prior_scheduled, attempt["scheduled"], forced_removed)
    errors = attempt["model"].verify_schedule(attempt["scheduled"])
    # The cap governs elective churn only: cancelling a withdrawn student's
    # interviews is the disruption, not the fix.
    cap_exceeded = diff["elective_churn_pct"] > churn_cap_pct

    return {
        "ok": True,
        "label": "proposal",
        # The amended problem input travels with the proposal, or applying a
        # roster change would leave appointments pointing at a company the
        # dataset no longer contains.
        "dataset": working,
        "disruptions_applied": applied,
        "solver": attempt["report"],
        "churn_weight": attempt["churn_weight"],
        "schedule": attempt["scheduled"],
        "unscheduled": [u["id"] for u in attempt["unscheduled"]],
        "diff": diff,
        "notify": compute_notify_list(diff, working,
                                      extra_names=original_names),
        "verification_errors": errors,
        "churn_cap_pct": churn_cap_pct,
        "cap_exceeded": cap_exceeded,
        # Stated without a remedy: whether a cheaper fix exists is unknown
        # until the retry has run. `replan` appends the outcome.
        "authorization_prompt": (
            f"This fix requires moving {diff['elective_churn_count']} "
            f"appointments the disruption did not itself cancel "
            f"({diff['elective_churn_pct']}% of "
            f"{diff['baseline_appointments']}), exceeding your "
            f"{churn_cap_pct}% cap."
            if cap_exceeded else None
        ),
    }


def compute_diff(old_schedule, new_schedule, forced_removed=()):
    """Structured diff between two schedules, with the affected parties.

    Churn is split two ways: forced churn is what the disruption removed
    outright (a withdrawn student's interviews), elective churn is what the
    replanner chose to move or drop to absorb it. Only elective churn is
    meaningful to cap — one blended number hides the real cost of the fix and
    trips the cap on events nobody chose.
    """
    forced_removed = set(forced_removed)
    churn = compute_churn(old_schedule, new_schedule)
    old = {a["id"]: a for a in old_schedule}
    new = {a["id"]: a for a in new_schedule}

    def describe(iid):
        a, b = old.get(iid), new.get(iid)
        rec = b or a
        entry = {
            "id": iid,
            "company_id": rec["company_id"],
            "student_id": rec["student_id"],
            # Enough for the board to draw a newly placed interview, not just
            # name it.
            "duration_slots": rec["duration_slots"],
            "tier": rec["tier"],
        }
        if a:
            entry["from"] = {
                "day": a["day"], "slot": a["slot"],
                "room": a.get("room"), "panel": a.get("panel"),
            }
        if b:
            entry["to"] = {
                "day": b["day"], "slot": b["slot"],
                "room": b.get("room"), "panel": b.get("panel"),
            }
        return entry

    affected_students, affected_companies = set(), set()
    for iid in churn["added"] + churn["removed"] + churn["moved"]:
        rec = new.get(iid) or old.get(iid)
        affected_students.add(rec["student_id"])
        affected_companies.add(rec["company_id"])

    forced = [i for i in churn["removed"] if i in forced_removed]
    elective_removed = [i for i in churn["removed"] if i not in forced_removed]
    elective_count = (
        len(churn["added"]) + len(elective_removed) + len(churn["moved"])
    )
    baseline = churn["baseline_appointments"] or 1

    return {
        **churn,
        "forced_removed": sorted(forced),
        "elective_removed": sorted(elective_removed),
        "forced_churn_count": len(forced),
        "elective_churn_count": elective_count,
        "elective_churn_pct": round(100.0 * elective_count / baseline, 1),
        "added_detail": [describe(i) for i in churn["added"]],
        "removed_detail": [describe(i) for i in churn["removed"]],
        "moved_detail": [describe(i) for i in churn["moved"]],
        "affected_students": sorted(affected_students),
        "affected_companies": sorted(affected_companies),
    }


def compute_notify_list(diff, dataset, extra_names=None):
    """Who needs to be told what, derived from the diff and the roster.

    One entry per person rather than per changed interview, so a student with
    three moved interviews gets one message.
    """
    # A withdrawn company is gone from the amended dataset, but its cancelled
    # interviews still need naming, so fall back to the pre-amendment names.
    names = dict(extra_names or {})
    names.update({c["id"]: c["name"] for c in dataset["companies"]})

    def company_name(cid):
        return names.get(cid, cid)

    def clock(rec):
        return timegrid.stamp(dataset["config"], rec["day"], rec["slot"])

    per_student = {}

    def note(sid, urgency, text):
        entry = per_student.setdefault(
            sid, {"student_id": sid, "urgency": urgency, "changes": []}
        )
        entry["changes"].append(text)
        if urgency == "high":
            entry["urgency"] = "high"

    for item in diff["removed_detail"]:
        cname = company_name(item["company_id"])
        note(item["student_id"], "high",
             f"CANCELLED: {cname} interview at {clock(item['from'])}.")
    for item in diff["moved_detail"]:
        cname = company_name(item["company_id"])
        note(item["student_id"], "high",
             f"MOVED: {cname} interview {clock(item['from'])} "
             f"-> {clock(item['to'])} ({item['to']['room']}).")
    for item in diff["added_detail"]:
        cname = company_name(item["company_id"])
        note(item["student_id"], "normal",
             f"NEW: {cname} interview at {clock(item['to'])} "
             f"({item['to']['room']}).")

    company_counts = {}
    for key in ("removed_detail", "moved_detail", "added_detail"):
        for item in diff[key]:
            c = company_counts.setdefault(
                item["company_id"],
                {"company_id": item["company_id"],
                 "company": company_name(item["company_id"]),
                 "cancelled": 0, "moved": 0, "added": 0},
            )
            c[{"removed_detail": "cancelled",
               "moved_detail": "moved",
               "added_detail": "added"}[key]] += 1

    return {
        "students": sorted(
            per_student.values(),
            key=lambda e: (e["urgency"] != "high", e["student_id"]),
        ),
        "companies": sorted(
            company_counts.values(), key=lambda e: -(
                e["cancelled"] + e["moved"] + e["added"]
            )
        ),
        "total_people_to_contact": len(per_student) + len(company_counts),
    }


def apply_proposal(proposal):
    """Commit a proposal the coordinator accepted.

    Separate from `replan` so a schedule never changes as a side effect of
    asking what a fix would cost.
    """
    if not proposal.get("ok"):
        raise ValueError("cannot apply a failed replan")
    if proposal["verification_errors"]:
        raise ValueError(
            f"refusing to apply: {len(proposal['verification_errors'])} "
            f"hard-constraint violations in the proposed schedule"
        )
    return proposal["schedule"]
