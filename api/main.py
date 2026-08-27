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
    GET  /affected       -> who a disruption touches (SQL impact query)
    GET  /schedule/versions -> every schedule version, newest first
    POST /replan         -> propose a fix for a disruption; returns a diff
    POST /replan/apply   -> commit a previously-returned proposal
    GET  /replan/history -> audit trail of applied replans

Schedule state is persisted by `store/` — Postgres when DATABASE_URL reaches a
server, an in-memory stand-in otherwise. Schedules are versioned, not mutated,
so the plan that existed before a disruption survives it.

/replan never mutates state. The proposal it returns carries a token; only
POST /replan/apply with that token changes the live schedule (guide section
2.4: "apply or reject, not auto-applied").
"""

import os
import subprocess
import sys
import uuid
from typing import Any, Dict, List, Optional

from fastapi import Depends, FastAPI, HTTPException, Response
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from replanner.replan import (  # noqa: E402
    DisruptionError, apply_proposal, replan,
)
from scheduler.metrics import compute_metrics  # noqa: E402
from scheduler.model import SchedulingModel  # noqa: E402
from store import open_store  # noqa: E402
from auth import (  # noqa: E402
    COOKIE_NAME, SESSION_HOURS, current_user, issue_token,
    require_coordinator, seed_demo_users, verify_password,
)

app = FastAPI(title="Panelist API")

# The dashboard is served from a different origin (:3000) than the API
# (:8000), so every browser request is cross-origin and is blocked outright
# without this — including the preflight OPTIONS, which returns 405 from a
# bare FastAPI app. Server-side clients (curl, TestClient) never exercise
# this path, so it stays invisible until a browser actually hits the API.
DASHBOARD_ORIGINS = [
    o.strip() for o in os.environ.get(
        "PANELIST_CORS_ORIGINS",
        "http://localhost:3000,http://127.0.0.1:3000",
    ).split(",") if o.strip()
]

app.add_middleware(
    CORSMiddleware,
    allow_origins=DASHBOARD_ORIGINS,
    # Required for the session cookie to cross from the dashboard origin.
    # Only legal against an explicit origin list, never a wildcard.
    allow_credentials=True,
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["Content-Type"],
)

DATA_ROOT = os.environ.get("PANELIST_DATA", "./data")
DEFAULT_DATASET = os.environ.get("PANELIST_DATASET", "primary")

# Schedule state lives in the store — Postgres when DATABASE_URL points at a
# reachable server, an in-memory stand-in otherwise. Which one is active is
# reported by GET /health, so a silent downgrade is impossible.
store = open_store()

# The solver wants the dataset as plain dicts, so the loaded one is cached here
# rather than re-read from the tables on every solve. The store remains the
# source of truth for SCHEDULES, which is what actually has to survive.
_state: Dict[str, Any] = {"dataset": None, "name": None}
_proposals: Dict[str, Any] = {}


def _current():
    """The live schedule, or None."""
    if not _state["name"]:
        return None
    return store.get_current(_state["name"])


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
    cur = _current()
    if cur is None:
        raise HTTPException(409, "No schedule yet — POST /schedule first.")
    return _state["dataset"], cur["scheduled"]


# --- endpoints -------------------------------------------------------------

def _seed_accounts():
    created = seed_demo_users(store)
    if created:
        print(f"[auth] seeded demo accounts: {', '.join(created)}")


def _restore_on_startup():
    """Adopt a schedule that outlived the last process.

    Without this, persistence is write-only: the tables hold a current
    schedule but a restarted API reports "no schedule" until someone re-solves,
    which is exactly the situation the database was added to prevent. It also
    matters for the live defense — a restarted container should come back with
    the week already planned rather than needing a 30s solve.
    """
    if store.kind != "postgres":
        return
    try:
        current = store.get_current(DEFAULT_DATASET)
        if current is None:
            return
        ds = store.get_dataset(DEFAULT_DATASET)
        if ds is None:
            return
        _state.update({"dataset": ds, "name": DEFAULT_DATASET})
        print(f"[store] restored schedule v{current['version']} "
              f"({len(current['scheduled'])} appointments) for "
              f"{DEFAULT_DATASET!r}")
    except Exception as e:
        # Never let a restore problem stop the API from starting.
        print(f"[store] could not restore previous schedule: {e}")


@app.get("/health")
def health():
    cur = _current()
    return {
        "status": "ok",
        "dataset_loaded": _state["name"],
        "has_schedule": cur is not None,
        "schedule_version": cur["version"] if cur else None,
        "store": store.kind,
        "store_detail": store.describe(),
    }


class LoginRequest(BaseModel):
    username: str
    password: str


@app.post("/auth/login")
def login(req: LoginRequest, response: Response):
    user = store.get_user(req.username.strip().lower())
    # One message for both "no such user" and "wrong password", so the response
    # cannot be used to enumerate valid usernames.
    if not user or not verify_password(
        req.password, user["salt"], user["password_hash"]
    ):
        raise HTTPException(401, "Incorrect username or password.")

    store.touch_login(user["username"])
    response.set_cookie(
        COOKIE_NAME,
        issue_token(user["username"], user["role"]),
        httponly=True,          # page JavaScript can never read the session
        samesite="lax",
        max_age=SESSION_HOURS * 3600,
        path="/",
    )
    return {
        "username": user["username"],
        "display_name": user["display_name"],
        "role": user["role"],
    }


@app.post("/auth/logout")
def logout(response: Response):
    response.delete_cookie(COOKIE_NAME, path="/")
    return {"signed_out": True}


@app.get("/auth/me")
def me(user=Depends(current_user)):
    stored = store.get_user(user["username"])
    return {
        "username": user["username"],
        "role": user["role"],
        "display_name": stored["display_name"] if stored else user["username"],
    }


@app.get("/config")
def get_config(_=Depends(current_user)):
    """Everything the UI needs to render a grid: time model, rooms, companies.

    Served from the API so the dashboard never hardcodes the slot arithmetic —
    a frontend copy of SLOTS_PER_DAY_RAW that drifts from the backend produces
    a schedule board that is subtly, silently wrong.
    """
    ds = _state["dataset"] or _load(DEFAULT_DATASET)
    return {
        "config": ds["config"],
        "slots_per_day_raw": 32,
        "day_start_minutes": 9 * 60,
        "rooms": [
            {"id": r["id"], "name": r["name"],
             "blocked_windows": r.get("blocked_windows", [])}
            for r in ds["rooms"]
        ],
        "companies": [
            {"id": c["id"], "name": c["name"], "tier": c["tier"],
             "panel_count": c["panel_count"],
             "interview_minutes": c["interview_minutes"],
             "shortlist_size": c["shortlist_size"]}
            for c in ds["companies"]
        ],
    }


@app.post("/generate")
def generate(req: GenerateRequest, _=Depends(require_coordinator)):
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
def create_schedule(req: ScheduleRequest, _=Depends(require_coordinator)):
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
    _state.update({"dataset": ds, "name": req.dataset})
    store.put_dataset(req.dataset, ds)
    version = store.put_schedule(
        req.dataset, scheduled, [u["id"] for u in unscheduled],
        report, metrics, origin="solve",
    )
    return {
        "solver": report,
        "metrics": metrics,
        "verification_errors": errors,
        "scheduled": len(scheduled),
        "unscheduled": len(unscheduled),
        "version": version,
        "store": store.kind,
    }


@app.get("/schedule")
def get_schedule(day: Optional[int] = None, room: Optional[str] = None,
                 company_id: Optional[str] = None,
                 student_id: Optional[str] = None, _=Depends(current_user)):
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
def get_metrics(_=Depends(current_user)):
    ds, _ = _require_schedule()
    cur = _current()
    return compute_metrics(
        cur["scheduled"], [{"id": i} for i in cur["unscheduled"]],
        ds["students"], ds["rooms"], ds["config"],
    )


@app.get("/diagnostics")
def diagnostics(_=Depends(current_user)):
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


@app.get("/affected")
def affected(company_id: Optional[str] = None, room: Optional[str] = None,
             day: Optional[int] = None, _=Depends(current_user)):
    """Who a disruption would touch — the query the database exists for.

    Answers "which students does this hit, and what ELSE do they have that
    day", which is the question a coordinator asks before deciding whether a
    fix is acceptable. The second figure is a correlated count over the same
    student's other interviews: a join, not a lookup.
    """
    _require_schedule()
    rows = store.affected(_state["name"], company_id=company_id,
                          room=room, day=day)
    return {
        "students_affected": len(rows),
        "interviews_hit": sum(r["interviews_hit"] for r in rows),
        "also_have_other_interviews": sum(
            1 for r in rows if r["other_interviews_that_day"] > 0),
        "students": rows[:200],
    }


@app.get("/schedule/versions")
def schedule_versions(_=Depends(current_user)):
    """Every schedule version for this dataset, newest first.

    Replans write a new version rather than mutating, so the plan that existed
    before a disruption is still queryable — and rollback is a data question
    rather than a re-solve.
    """
    if not _state["name"]:
        raise HTTPException(409, "No dataset loaded — POST /schedule first.")
    return {"versions": store.versions(_state["name"])}


@app.get("/replan/history")
def replan_history(limit: int = 20, _=Depends(current_user)):
    """Audit trail: every applied replan, its cause and its cost."""
    if not _state["name"]:
        raise HTTPException(409, "No dataset loaded — POST /schedule first.")
    return {"events": store.replan_history(_state["name"], limit=limit)}


@app.post("/replan")
def propose_replan(req: ReplanRequest, _=Depends(current_user)):
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

    proposal["_events"] = events
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
def commit_replan(req: ApplyRequest, _=Depends(require_coordinator)):
    """Commit a proposal the coordinator accepted."""
    proposal = _proposals.get(req.proposal_id)
    if proposal is None:
        raise HTTPException(404, "unknown or expired proposal_id")
    try:
        schedule = apply_proposal(proposal)
    except ValueError as e:
        raise HTTPException(409, str(e))

    ds = _state["dataset"]
    name = _state["name"]
    metrics = compute_metrics(
        schedule, [{"id": i} for i in proposal["unscheduled"]],
        ds["students"], ds["rooms"], ds["config"],
    )
    version = store.put_schedule(
        name, schedule, proposal["unscheduled"],
        proposal.get("solver"), metrics, origin="replan",
    )
    store.record_replan(
        name, proposal.get("_events", []), proposal, version)

    _proposals.clear()  # every other proposal was computed against stale state
    return {
        "applied": True,
        "appointments": len(schedule),
        "churn": proposal["diff"]["elective_churn_count"],
        "version": version,
        "metrics": metrics,
        "notify": proposal["notify"],
    }


_seed_accounts()
_restore_on_startup()
