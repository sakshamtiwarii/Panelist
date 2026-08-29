"""FastAPI backend: app assembly.

A thin HTTP wrapper over the generator, scheduler and replanner — all
scheduling logic lives in those modules. The surface is split by area:

    api/routes/auth.py      sign in, sign out, whoami
    api/routes/schedule.py  generate, solve, board, metrics, diagnostics
    api/routes/roster.py    companies and shortlists arriving mid-week
    api/routes/replan.py    propose a fix, review it, commit it
    api/deps.py             the store and the live-dataset accessors
    api/schemas.py          request bodies

State is persisted by `store/`. Schedules are versioned, not mutated, so the
plan that existed before a disruption survives it.
"""

import os

from fastapi import Depends, FastAPI
from fastapi.middleware.cors import CORSMiddleware

from api.auth import current_user, seed_demo_users
from api.deps import (
    DATA_ROOT,
    DEFAULT_DATASET,
    current_schedule,
    dataset_is_usable,
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
from api.routes.schedule import solve_and_store
from generator.generate import build_dataset, write_dataset
from scheduler import timegrid

app = FastAPI(title="Panelist API")

# The dashboard runs on a different origin (:3000) than the API (:8000), so
# every browser request is cross-origin. Server-side clients (curl, TestClient)
# never exercise this path.
DASHBOARD_ORIGINS = [
    o.strip() for o in os.environ.get(
        "PANELIST_CORS_ORIGINS",
        "http://localhost:3000,http://127.0.0.1:3000",
    ).split(",") if o.strip()
]

app.add_middleware(
    CORSMiddleware,
    allow_origins=DASHBOARD_ORIGINS,
    # Required for the session cookie, and only legal against an explicit
    # origin list rather than a wildcard.
    allow_credentials=True,
    # DELETE is for the /roster endpoints.
    allow_methods=["GET", "POST", "DELETE", "OPTIONS"],
    allow_headers=["Content-Type"],
)

app.include_router(auth_routes.router)
app.include_router(schedule_routes.router)
app.include_router(roster_routes.router)
app.include_router(replan_routes.router)


@app.get("/health")
def health():
    # The version alone answers both questions, without pulling every
    # appointment out of the database on a call made on every page load.
    name = dataset_name()
    version = store.current_version(name) if name else None
    return {
        "status": "ok",
        "dataset_loaded": name,
        "has_schedule": version is not None,
        "schedule_version": version,
        "store": store.kind,
        # False when the stored dataset predates the current time model:
        # a schedule exists but must be re-solved before it can be read.
        "schedule_usable": dataset_is_usable(),
        "store_detail": store.describe(),
    }


@app.get("/config")
def get_config(_=Depends(current_user)):
    """Everything the UI needs to render a grid: time model, rooms, companies.

    Served from the API so the dashboard never hardcodes slot arithmetic that
    could drift from the backend's.
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

    Without this, persistence is write-only: the tables hold a current schedule
    but a restarted API reports "no schedule" until someone re-solves.
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

        # A dataset persisted by an older build can be missing time-model
        # keys. Adopting it would 500 every grid-touching endpoint; starting
        # clean leaves the console in its "no schedule yet" state instead.
        stale = timegrid.missing_keys(ds.get("config"))
        if stale:
            print(f"[store] ignoring stored schedule for {name!r}: its config "
                  f"predates the current time model (missing "
                  f"{', '.join(stale)}). Re-solve to rebuild it.")
            return

        set_loaded(name, ds)
        print(f"[store] restored schedule v{current['version']} "
              f"({len(current['scheduled'])} appointments) for {name!r}")
    except Exception as e:
        # Never let a restore problem stop the API from starting.
        print(f"[store] could not restore previous schedule: {e}")


# The seeded week a fresh deployment opens on. Matches the dataset the README
# and CI describe.
DEMO_DATASET = {
    "seed": 42, "companies": 35, "students": 800,
    "rooms": 20, "days": 4, "load_factor": 0.9,
}
DEMO_SOLVE_SECONDS = 60


def _seed_demo_schedule():
    """Put a solved week on the board before anyone signs in.

    `data/` is generated rather than committed, so a fresh deployment would
    otherwise open on an empty board. Skipped once a schedule exists, so a
    restart adopts the real one via `_restore_on_startup`; set
    PANELIST_DEMO_SEED=0 for a deployment that carries its own data.
    """
    if os.environ.get("PANELIST_DEMO_SEED", "1") != "1":
        return
    if current_schedule() is not None:
        return
    try:
        path = os.path.join(DATA_ROOT, DEFAULT_DATASET, "dataset.json")
        if os.path.exists(path):
            ds = read_dataset_file(DEFAULT_DATASET)
            print(f"[seed] using the dataset already on disk at {path}")
        else:
            ds, report = build_dataset(**DEMO_DATASET)
            print(f"[seed] generated {DEFAULT_DATASET!r} "
                  f"({DEMO_DATASET['companies']} companies, "
                  f"{DEMO_DATASET['students']} students)")
            # So the CLIs and the replan scenarios work against the same week
            # the console shows. Best-effort: a serverless filesystem is
            # read-only outside /tmp, and the schedule is in the store anyway.
            try:
                write_dataset(
                    os.path.join(DATA_ROOT, DEFAULT_DATASET), ds, report)
            except OSError as e:
                print(f"[seed] not writing {DEFAULT_DATASET!r} to disk "
                      f"({e.strerror or e}); the schedule is in the store")

        out = solve_and_store(DEFAULT_DATASET, ds, DEMO_SOLVE_SECONDS)
        if not out["usable"]:
            print(f"[seed] solver returned {out['report']['status']}; "
                  f"the console will open on an empty board")
            return
        m = out["metrics"]
        print(f"[seed] solved v{out['version']}: {m['interviews_scheduled']}"
              f"/{m['interviews_total']} interviews placed "
              f"({m['pct_scheduled']}%), {m['student_clashes']} clashes — "
              f"the console opens ready to use")
    except Exception as e:
        # A demo convenience must never be why the API fails to start.
        print(f"[seed] could not prepare a demo schedule: {e}")


_seed_accounts()
_restore_on_startup()
_seed_demo_schedule()
