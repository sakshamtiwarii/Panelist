"""
Panelist — FastAPI backend.

Thin HTTP wrapper over the generator, scheduler and replanner. All scheduling
logic lives in those modules; this layer only loads state, calls them, and
shapes responses.

    POST /generate       -> generate a dataset (seeded, reproducible)
    POST /schedule       -> run the initial scheduler, return schedule + metrics
    GET  /schedule       -> current schedule state (filterable)
    GET  /metrics        -> metrics for the current schedule
    GET  /diagnostics    -> why interviews are unscheduled
    POST /replan         -> propose a fix for a disruption; returns a diff
    POST /replan/apply   -> commit a previously-returned proposal

/replan never mutates state. The proposal it returns carries a token; only
POST /replan/apply with that token changes the live schedule (guide section
2.4: "apply or reject, not auto-applied").
"""

import os
import subprocess
import sys
import uuid
from typing import Any, Dict, List, Optional

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from replanner.replan import (  # noqa: E402
    DisruptionError, apply_proposal, replan,
)
from scheduler.metrics import compute_metrics  # noqa: E402
from scheduler.model import SchedulingModel  # noqa: E402

app = FastAPI(title="Panelist API")

DATA_ROOT = os.environ.get("PANELIST_DATA", "./data")
DEFAULT_DATASET = os.environ.get("PANELIST_DATASET", "primary")

# In-memory state. Postgres is wired in docker-compose for persistence; the
# demo path runs off the generated JSON so the defense does not depend on a
# database being seeded.
_state: Dict[str, Any] = {"dataset": None, "schedule": None, "name": None}
_proposals: Dict[str, Any] = {}


# --- request models --------------------------------------------------------

class GenerateRequest(BaseModel):
    seed: int = 42
    companies: int = 35
    students: int = 800
    rooms: int = 20
    days: int = 4
    load_factor: Optional[float] = None
    name: str = "primary"


class ScheduleRequest(BaseModel):
    dataset: str = DEFAULT_DATASET
    time_limit_seconds: float = 30.0


class Disruption(BaseModel):
    type: str = Field(..., description="company_late | panel_drop | "
                                       "student_withdraw | room_unavailable")
    company_id: Optional[str] = None
    student_id: Optional[str] = None
    room_id: Optional[str] = None
    day: Optional[int] = None
    hours: Optional[float] = None
    count: Optional[int] = None
    scope: Optional[str] = None
    from_slot: Optional[int] = None
    to_slot: Optional[int] = None
    reason: Optional[str] = None


class ReplanRequest(BaseModel):
    disruptions: List[Disruption]
    churn_cap_pct: float = 10.0
    time_limit_seconds: float = 60.0
    now_slot: Optional[int] = None


class ApplyRequest(BaseModel):
    proposal_id: str


# --- helpers ---------------------------------------------------------------

def _load(name: str):
    import json
    path = os.path.join(DATA_ROOT, name, "dataset.json")
    if not os.path.exists(path):
        raise HTTPException(404, f"dataset {name!r} not found at {path}")
    with open(path) as f:
        return json.load(f)


def _require_schedule():
    if _state["schedule"] is None:
        raise HTTPException(
            409, "No schedule yet — POST /schedule first."
        )
    return _state["dataset"], _state["schedule"]


# --- endpoints -------------------------------------------------------------

@app.get("/health")
def health():
    return {
        "status": "ok",
        "dataset_loaded": _state["name"],
        "has_schedule": _state["schedule"] is not None,
    }


@app.post("/generate")
def generate(req: GenerateRequest):
    out = os.path.join(DATA_ROOT, req.name)
    cmd = [
        sys.executable, "generator/generate.py",
        "--seed", str(req.seed), "--companies", str(req.companies),
        "--students", str(req.students), "--rooms", str(req.rooms),
        "--days", str(req.days), "--out", out,
    ]
    if req.load_factor is not None:
        cmd += ["--load-factor", str(req.load_factor)]
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        raise HTTPException(500, f"generator failed: {proc.stderr[-2000:]}")
    return {
        "dataset": req.name, "seed": req.seed, "path": out,
        "density_report": proc.stdout,
    }


@app.post("/schedule")
def create_schedule(req: ScheduleRequest):
    ds = _load(req.dataset)
    model = SchedulingModel(
        ds["companies"], ds["students"], ds["rooms"], ds["config"]
    ).build()
    status, solver = model.solve(time_limit_seconds=req.time_limit_seconds)
    report = model.status_report(status, solver)
    if not report["usable"]:
        raise HTTPException(422, {
            "solver": report,
            "constraints": model.constraint_reasons,
        })

    scheduled, unscheduled = model.extract_schedule(solver)
    errors = model.verify_schedule(scheduled)
    metrics = compute_metrics(
        scheduled, unscheduled, ds["students"], ds["rooms"], ds["config"]
    )
    _state.update({
        "dataset": ds, "schedule": scheduled, "name": req.dataset,
        "unscheduled": [u["id"] for u in unscheduled],
    })
    return {
        "solver": report,
        "metrics": metrics,
        "verification_errors": errors,
        "scheduled": len(scheduled),
        "unscheduled": len(unscheduled),
    }


@app.get("/schedule")
def get_schedule(day: Optional[int] = None, room: Optional[str] = None,
                 company_id: Optional[str] = None,
                 student_id: Optional[str] = None):
    _, schedule = _require_schedule()
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


@app.get("/metrics")
def get_metrics():
    ds, schedule = _require_schedule()
    unscheduled = [{"id": i} for i in _state.get("unscheduled", [])]
    return compute_metrics(
        schedule, unscheduled, ds["students"], ds["rooms"], ds["config"]
    )


@app.get("/diagnostics")
def diagnostics():
    """Why interviews are unscheduled — structural headline first."""
    ds, schedule = _require_schedule()
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


@app.post("/replan")
def propose_replan(req: ReplanRequest):
    """Propose a fix. Never mutates the live schedule."""
    ds, schedule = _require_schedule()
    events = [d.model_dump(exclude_none=True) for d in req.disruptions]
    try:
        proposal = replan(
            ds, schedule, events,
            churn_cap_pct=req.churn_cap_pct,
            time_limit_seconds=req.time_limit_seconds,
            now_slot=req.now_slot,
        )
    except DisruptionError as e:
        raise HTTPException(400, str(e))

    if not proposal["ok"]:
        return {**proposal, "proposal_id": None}

    pid = str(uuid.uuid4())
    _proposals[pid] = proposal
    # The full schedule is large and the coordinator reviews the diff, not the
    # 1000-row board; it stays server-side until the proposal is applied.
    return {
        "proposal_id": pid,
        "ok": True,
        "label": proposal["label"],
        "disruptions_applied": proposal["disruptions_applied"],
        "solver": proposal["solver"],
        "diff": {k: v for k, v in proposal["diff"].items()
                 if k != "reseated"},
        "notify": proposal["notify"],
        "churn_cap_pct": proposal["churn_cap_pct"],
        "cap_exceeded": proposal["cap_exceeded"],
        "churn_irreducible": proposal.get("churn_irreducible", False),
        "authorization_prompt": proposal["authorization_prompt"],
        "verification_errors": proposal["verification_errors"],
        "has_alternative": "alternative" in proposal,
    }


@app.post("/replan/apply")
def commit_replan(req: ApplyRequest):
    """Commit a proposal the coordinator accepted."""
    proposal = _proposals.get(req.proposal_id)
    if proposal is None:
        raise HTTPException(404, "unknown or expired proposal_id")
    try:
        schedule = apply_proposal(proposal)
    except ValueError as e:
        raise HTTPException(409, str(e))

    _state["schedule"] = schedule
    _state["unscheduled"] = proposal["unscheduled"]
    _proposals.clear()  # every other proposal was computed against stale state
    return {
        "applied": True,
        "appointments": len(schedule),
        "churn": proposal["diff"]["elective_churn_count"],
        "notify": proposal["notify"],
    }
