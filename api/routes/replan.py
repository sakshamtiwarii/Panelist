"""
Panelist — propose a fix, review it, then commit it.

/replan never mutates state. The proposal it returns carries a token; only
POST /replan/apply with that token changes the live schedule (guide section
2.4: "apply or reject, not auto-applied").

Proposals are held in the store, not in a process dict. A proposal is the one
piece of state that gates a schedule mutation: kept in memory it is a 404 on
any other worker, and every pending approval dies on restart while the
schedule it was computed against survives.
"""

import uuid

from fastapi import APIRouter, Depends, HTTPException

from api.auth import current_user, require_coordinator
from api.deps import require_dataset, require_schedule, set_loaded, store
from api.routes.schedule import check_overrides
from api.schemas import ApplyRequest, ReplanRequest
from replanner.replan import DisruptionError, apply_proposal, replan
from scheduler.metrics import compute_metrics

router = APIRouter(tags=["replan"])

# A proposal is only valid against the schedule it was built from, so it is
# given a bounded life rather than lingering until something else clears it.
PROPOSAL_TTL_MINUTES = 30


def propose(events, churn_cap_pct, time_limit_seconds, now_slot=None,
            priority_overrides=None):
    """Build a proposal from any list of events. Mutates nothing.

    Shared with the roster router: adding a company and reacting to a burst
    pipe are the same operation once both are expressed as events.
    """
    ds, schedule = require_schedule()
    overrides = check_overrides(priority_overrides or {}, ds["companies"])
    try:
        proposal = replan(
            ds, schedule, events,
            churn_cap_pct=churn_cap_pct,
            time_limit_seconds=time_limit_seconds,
            now_slot=now_slot,
            priority_overrides=overrides,
        )
    except DisruptionError as e:
        raise HTTPException(400, str(e))

    if not proposal["ok"]:
        return {**proposal, "proposal_id": None}

    proposal["_events"] = events
    pid = str(uuid.uuid4())
    store.put_proposal(pid, require_dataset(), proposal,
                       ttl_minutes=PROPOSAL_TTL_MINUTES)
    # The full schedule is large and the coordinator reviews the diff, not the
    # 1000-row board; it stays server-side until the proposal is applied.
    return {
        "proposal_id": pid,
        "ok": True,
        "label": proposal["label"],
        "disruptions_applied": proposal["disruptions_applied"],
        "solver": proposal["solver"],
        "diff": {k: v for k, v in proposal["diff"].items() if k != "reseated"},
        "notify": proposal["notify"],
        "churn_cap_pct": proposal["churn_cap_pct"],
        "cap_exceeded": proposal["cap_exceeded"],
        "churn_irreducible": proposal.get("churn_irreducible", False),
        "authorization_prompt": proposal["authorization_prompt"],
        "verification_errors": proposal["verification_errors"],
        # How many interviews this fix leaves unplaced — the other half of the
        # trade-off against churn, and the number the alternative usually
        # pays in exchange for moving less.
        "unscheduled": len(proposal["unscheduled"]),
        "has_alternative": "alternative" in proposal,
        "alternative": _summarise_alternative(proposal.get("alternative")),
        "priority_overrides": overrides,
    }


def _summarise_alternative(alt):
    """The lower-churn option, as much as the coordinator needs to choose.

    The full alternative schedule stays server-side with the proposal, exactly
    like the primary one — this is the summary the choice is made on.
    """
    if alt is None:
        return None
    return {
        "label": alt["label"],
        "elective_churn_count": alt["diff"]["elective_churn_count"],
        "elective_churn_pct": alt["diff"]["elective_churn_pct"],
        "forced_churn_count": alt["diff"]["forced_churn_count"],
        "unscheduled": len(alt["unscheduled"]),
        "solver": alt["solver"],
        "notify_count": alt["notify"]["total_people_to_contact"],
        "verification_errors": alt["verification_errors"],
    }


@router.post("/replan")
def propose_replan(req: ReplanRequest, _=Depends(current_user)):
    """Propose a fix. Never mutates the live schedule."""
    events = [d.model_dump(exclude_none=True) for d in req.disruptions]
    return propose(events, req.churn_cap_pct, req.time_limit_seconds,
                   req.now_slot, req.priority_overrides)


@router.post("/replan/apply")
def commit_replan(req: ApplyRequest, _=Depends(require_coordinator)):
    """Commit a proposal the coordinator accepted.

    When the first fix exceeded the churn cap the replanner also solved for a
    lower-churn one, and `use_alternative` commits that instead. Both were
    computed against the same schedule and the same events, so the choice
    between them is the coordinator's alone: fewer interviews moved, usually
    at the cost of a few more left unplaced.
    """
    stored = store.get_proposal(req.proposal_id)
    if stored is None:
        raise HTTPException(404, "unknown or expired proposal_id")

    proposal = stored
    if req.use_alternative:
        alternative = stored.get("alternative")
        if alternative is None:
            raise HTTPException(409, {
                "error": "no_alternative",
                "message": (
                    "This proposal has no lower-churn alternative — either it "
                    "stayed within the cap, or re-solving found no cheaper "
                    "fix. Apply it as it stands, or reject it."
                ),
            })
        proposal = alternative
        # The audit trail records what CAUSED the replan, and the cause is the
        # same either way; the alternative is built by the same function but
        # never carries the events itself.
        proposal["_events"] = stored.get("_events", [])
    try:
        schedule = apply_proposal(proposal)
    except ValueError as e:
        raise HTTPException(409, str(e))

    name = require_dataset()
    # Persist the amended roster BEFORE the schedule. A proposal that added or
    # removed a company changes the problem input, and saving only the schedule
    # leaves appointments referencing a company the dataset no longer has.
    ds = proposal.get("dataset")
    if ds:
        store.amend_dataset(name, ds)
        set_loaded(name, ds)
    else:
        ds, _ = require_schedule()

    metrics = compute_metrics(
        schedule, [{"id": i} for i in proposal["unscheduled"]],
        ds["students"], ds["rooms"], ds["config"],
    )
    version = store.put_schedule(
        name, schedule, proposal["unscheduled"],
        proposal.get("solver"), metrics, origin="replan",
    )
    store.record_replan(name, proposal.get("_events", []), proposal, version)

    # Every other proposal was computed against a schedule that no longer
    # exists; applying one would write a plan built from stale state.
    store.clear_proposals(name)
    return {
        "applied": True,
        "appointments": len(schedule),
        "churn": proposal["diff"]["elective_churn_count"],
        "version": version,
        "metrics": metrics,
        "notify": proposal["notify"],
        # Which of the two was committed, so the response is unambiguous in a
        # log as well as on screen.
        "applied_alternative": bool(req.use_alternative),
        "label": proposal.get("label", "proposal"),
    }


@router.get("/replan/history")
def replan_history(limit: int = 20, _=Depends(current_user)):
    """Audit trail: every applied replan, its cause and its cost."""
    return {"events": store.replan_history(require_dataset(), limit=limit)}
