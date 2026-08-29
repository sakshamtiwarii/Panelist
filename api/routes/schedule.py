"""Dataset generation, solving, and reading the resulting board.

Thin over the solver: these endpoints load state, call `scheduler/`, and shape
the response. No scheduling logic lives here.
"""

import os
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException

from api.auth import current_user, require_coordinator
from api.deps import (
    DATA_ROOT,
    current_schedule,
    read_dataset_file,
    require_dataset,
    require_schedule,
    set_loaded,
    store,
)
from api.schemas import GenerateRequest, ScheduleRequest
from generator.generate import build_dataset, write_dataset
from scheduler.metrics import compute_metrics
from scheduler.model import SchedulingModel

router = APIRouter(tags=["schedule"])


@router.post("/generate")
def generate(req: GenerateRequest, _=Depends(require_coordinator)):
    out = os.path.join(DATA_ROOT, req.name)
    try:
        dataset, report = build_dataset(
            seed=req.seed, companies=req.companies, students=req.students,
            rooms=req.rooms, days=req.days, load_factor=req.load_factor,
        )
        write_dataset(out, dataset, report)
    except ValueError as e:
        # The settings are wrong, not the server.
        raise HTTPException(400, f"invalid generator settings: {e}")
    except OSError as e:
        raise HTTPException(500, f"could not write dataset to {out}: {e}")
    return {
        "dataset": req.name, "seed": req.seed, "path": out,
        "density_report": report,
    }


def check_overrides(overrides, companies):
    """Refuse an override that names a company the dataset does not have.

    Dropping it silently would leave the coordinator believing they had
    protected a company while the solver applied the tier default.
    """
    known = {c["id"] for c in companies}
    unknown = sorted(set(overrides) - known)
    if unknown:
        raise HTTPException(400, {
            "error": "unknown_company_in_priority_overrides",
            "message": (
                f"No company with id {', '.join(unknown[:5])} in this "
                f"dataset, so that priority override cannot be applied."
            ),
            "unknown": unknown,
        })
    return {cid: level for cid, level in overrides.items() if level != "normal"}


def solve_and_store(name, ds, time_limit_seconds, priority_overrides=None):
    """Solve a dataset and adopt the result as the live schedule.

    Shared by POST /schedule and the first-boot demo seed, so both persist the
    same things in the same order: the dataset before the schedule, the cache
    stamped with the version the write produced.

    `usable` is False when the solver found nothing, leaving the caller to
    decide whether that is a 422 or a logged warning.
    """
    model = SchedulingModel(
        ds["companies"], ds["students"], ds["rooms"], ds["config"],
        priority_overrides=priority_overrides,
    ).build()
    status, solver = model.solve(time_limit_seconds=time_limit_seconds)
    report = model.status_report(status, solver)
    if not report["usable"]:
        return {"usable": False, "report": report, "model": model}

    scheduled, unscheduled = model.extract_schedule(solver)
    errors = model.verify_schedule(scheduled)
    metrics = compute_metrics(scheduled, unscheduled, ds["rooms"], ds["config"])
    store.put_dataset(name, ds)
    version = store.put_schedule(
        name, scheduled, [u["id"] for u in unscheduled],
        report, metrics, origin="solve",
    )
    # Stamped with the version it belongs to, so the next read trusts the
    # cache rather than re-fetching.
    set_loaded(name, ds, version)
    return {
        "usable": True, "report": report, "model": model, "metrics": metrics,
        "scheduled": scheduled, "unscheduled": unscheduled,
        "errors": errors, "version": version,
    }


@router.post("/schedule")
def create_schedule(req: ScheduleRequest, _=Depends(require_coordinator)):
    ds = read_dataset_file(req.dataset)
    overrides = check_overrides(req.priority_overrides, ds["companies"])
    out = solve_and_store(req.dataset, ds, req.time_limit_seconds, overrides)
    if not out["usable"]:
        raise HTTPException(422, {
            "solver": out["report"],
            "constraints": out["model"].constraint_reasons,
        })
    report, model = out["report"], out["model"]
    scheduled, unscheduled = out["scheduled"], out["unscheduled"]
    errors, metrics, version = out["errors"], out["metrics"], out["version"]
    return {
        "solver": report,
        "metrics": metrics,
        "verification_errors": errors,
        "scheduled": len(scheduled),
        "unscheduled": len(unscheduled),
        "version": version,
        "store": store.kind,
        # Echoed back so the coordinator can see which exceptions were in
        # force for this solve.
        "priority_overrides": overrides,
        "priority_reasons": {
            cid: model.constraint_reasons[f"priority_override:{cid}"]
            for cid in overrides
            if f"priority_override:{cid}" in model.constraint_reasons
        },
    }


@router.get("/schedule")
def get_schedule(day: Optional[int] = None, room: Optional[str] = None,
                 company_id: Optional[str] = None,
                 student_id: Optional[str] = None, _=Depends(current_user)):
    _, schedule = require_schedule()
    rows = schedule
    if day is not None:
        rows = [a for a in rows if a["day"] == day]
    if room:
        rows = [a for a in rows if a.get("room") == room]
    if company_id:
        rows = [a for a in rows if a["company_id"] == company_id]
    if student_id:
        rows = [a for a in rows if a["student_id"] == student_id]
    return {"count": len(rows), "appointments": rows}


@router.get("/metrics")
def get_metrics(_=Depends(current_user)):
    ds, _ = require_schedule()
    cur = current_schedule()
    return compute_metrics(
        cur["scheduled"], [{"id": i} for i in cur["unscheduled"]],
        ds["rooms"], ds["config"],
    )


@router.get("/diagnostics")
def diagnostics(_=Depends(current_user)):
    """Why interviews are unscheduled — structural headline first."""
    ds, schedule = require_schedule()
    model = SchedulingModel(
        ds["companies"], ds["students"], ds["rooms"], ds["config"]
    )
    model._build_interviews()
    scheduled_ids = {a["id"] for a in schedule}
    unscheduled = [iv for iv in model.interviews
                   if iv["id"] not in scheduled_ids]
    if not unscheduled:
        return {"unscheduled": 0, "capacity": None, "per_company": []}
    return {
        "unscheduled": len(unscheduled),
        "capacity": model.capacity_analysis(schedule, unscheduled),
        "per_company": model.diagnose_unscheduled(schedule, unscheduled),
    }


@router.get("/affected")
def affected(company_id: Optional[str] = None, room: Optional[str] = None,
             day: Optional[int] = None, _=Depends(current_user)):
    """Which students a disruption hits, and what else they have that day."""
    require_schedule()
    rows = store.affected(require_dataset(), company_id=company_id,
                          room=room, day=day)
    return {
        "students_affected": len(rows),
        "interviews_hit": sum(r["interviews_hit"] for r in rows),
        "also_have_other_interviews": sum(
            1 for r in rows if r["other_interviews_that_day"] > 0),
        "students": rows[:200],
    }


@router.get("/schedule/versions")
def schedule_versions(_=Depends(current_user)):
    """Every schedule version for this dataset, newest first.

    Replans write a new version rather than mutating, so the plan that existed
    before a disruption stays queryable.
    """
    return {"versions": store.versions(require_dataset())}
