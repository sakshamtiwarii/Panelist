"""
Panelist — FastAPI backend.

Thin HTTP wrapper over the generator, scheduler and replanner. All scheduling
logic lives in those modules; this layer only loads state, calls them, and
shapes responses. This file is now only app assembly — the surface itself is
split by area:

    api/routes/auth.py      sign in, sign out, whoami
    api/routes/schedule.py  generate, solve, board, metrics, diagnostics
    api/routes/roster.py    companies and shortlists arriving mid-week
    api/routes/replan.py    propose a fix, review it, commit it
    api/deps.py             the store and the live-dataset accessors
    api/schemas.py          request bodies

Schedule state is persisted by `store/` — Postgres when DATABASE_URL reaches a
server, an in-memory stand-in otherwise. Schedules are versioned, not mutated,
so the plan that existed before a disruption survives it.
"""

import os

from fastapi import Depends, FastAPI
from fastapi.middleware.cors import CORSMiddleware

from api.auth import current_user, seed_demo_users
from api.deps import (
    DEFAULT_DATASET,
    current_schedule,
    dataset_name,
    loaded_dataset,
    read_dataset_file,
    set_loaded,
    store,
)
from api.routes import auth as auth_routes
from api.routes import replan as replan_routes
from api.routes import roster as roster_routes
from api.routes import schedule as schedule_routes
from scheduler import timegrid

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
    # DELETE is here for the /roster endpoints; a method missing from this
    # list fails at the preflight, which reads like a backend error rather
    # than a CORS one.
    allow_methods=["GET", "POST", "DELETE", "OPTIONS"],
    allow_headers=["Content-Type"],
)

app.include_router(auth_routes.router)
app.include_router(schedule_routes.router)
app.include_router(roster_routes.router)
app.include_router(replan_routes.router)


@app.get("/health")
def health():
    cur = current_schedule()
    return {
        "status": "ok",
        "dataset_loaded": dataset_name(),
        "has_schedule": cur is not None,
        "schedule_version": cur["version"] if cur else None,
        "store": store.kind,
        "store_detail": store.describe(),
    }


@app.get("/config")
def get_config(_=Depends(current_user)):
    """Everything the UI needs to render a grid: time model, rooms, companies.

    Served from the API so the dashboard never hardcodes the slot arithmetic —
    a frontend copy of the slot grid that drifts from the backend produces a
    schedule board that is subtly, silently wrong. Both values are read from
    the dataset's own config rather than restated here, for the same reason.
    """
    ds = loaded_dataset() or read_dataset_file(DEFAULT_DATASET)
    return {
        "config": ds["config"],
        "slots_per_day_raw": timegrid.slots_per_day_raw(ds["config"]),
        "day_start_minutes": timegrid.day_start_minutes(ds["config"]),
        "rooms": [
            {"id": r["id"], "name": r["name"],
             "blocked_windows": r.get("blocked_windows", [])}
            for r in ds["rooms"]
        ],
        "companies": [
            {"id": c["id"], "name": c["name"], "tier": c["tier"],
             "panel_count": c["panel_count"],
             "interview_minutes": c["interview_minutes"],
             "shortlist_size": c["shortlist_size"],
             # The UI shows the cutoff beside the company picker so a
             # coordinator sees why a student is ineligible before submitting.
             "cgpa_cutoff": c["cgpa_cutoff"]}
            for c in ds["companies"]
        ],
    }


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
        name = store.current_dataset() or DEFAULT_DATASET
        current = store.get_current(name)
        if current is None:
            return
        ds = store.get_dataset(name)
        if ds is None:
            return
        set_loaded(name, ds)
        print(f"[store] restored schedule v{current['version']} "
              f"({len(current['scheduled'])} appointments) for {name!r}")
    except Exception as e:
        # Never let a restore problem stop the API from starting.
        print(f"[store] could not restore previous schedule: {e}")


_seed_accounts()
_restore_on_startup()
