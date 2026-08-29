"""
Panelist — shared API state and dependencies.

The routers are thin; everything they share lives here: the store, the loaded
dataset, and the two guards (`require_schedule`, `require_dataset`) that every
endpoint needing a live schedule goes through.

Which dataset is live is asked of the store rather than remembered in a module
variable, so a worker that did not serve the request that solved the schedule
can still find it. Only the dataset *dicts* are cached in-process, because the
solver wants plain dicts and rebuilding them from the tables on every solve is
pure overhead — the store stays the source of truth for what actually has to
survive.
"""

import json
import os

from fastapi import HTTPException

from scheduler import timegrid
from store import open_store

DATA_ROOT = os.environ.get("PANELIST_DATA", "./data")
DEFAULT_DATASET = os.environ.get("PANELIST_DATASET", "primary")

# Postgres when DATABASE_URL points at a reachable server, an in-memory
# stand-in otherwise. Which one is active is reported by GET /health, so a
# silent downgrade is impossible.
store = open_store()

# Cache only. Never the source of truth for anything that must survive.
#
# `version` is what makes the cache safe to hold. FastAPI can be run with
# several workers, and a replan that amends the roster is applied by exactly
# one of them: without a stamp to check, every other worker keeps serving the
# pre-amendment dataset for the rest of its life — a company added by a replan
# never appears in its /config, and a replan it computes is built from a roster
# that no longer exists.
_cache = {"dataset": None, "name": None, "version": None}


def _forget():
    _cache.update({"dataset": None, "version": None})


def dataset_name():
    """Name of the dataset with a live schedule, or None.

    Asked of the store rather than remembered, so a worker notices when a
    different dataset becomes live. Falls back to what this process adopted
    only while the store has no current schedule at all — the window between
    loading a dataset and solving it.
    """
    name = store.current_dataset()
    if name:
        if _cache["name"] != name:
            # A different dataset is live now; everything held about the old
            # one describes a schedule that is no longer the current one.
            _cache["name"] = name
            _forget()
        return name
    return _cache["name"]


def loaded_dataset():
    """The live dataset as plain dicts, rebuilt from the store on a miss.

    A dataset written by an older build can be missing time-model keys. Every
    route reaches the store through here, so this is the one place that can
    turn that into an actionable 409 — otherwise it surfaces as a bare 500
    from whichever endpoint happens to touch the time grid first, with no hint
    that re-solving fixes it.
    """
    name = dataset_name()
    if not name:
        return None
    # One indexed row, against holding a roster that another worker replaced.
    version = store.current_version(name)
    if _cache["dataset"] is None or _cache["version"] != version:
        _cache["dataset"] = store.get_dataset(name)
        _cache["version"] = version
    ds = _cache["dataset"]
    if ds is not None:
        stale = timegrid.missing_keys(ds.get("config"))
        if stale:
            raise HTTPException(409, {
                "error": "stored_dataset_outdated",
                "message": (
                    f"The stored dataset {name!r} predates the current time "
                    f"model (missing {', '.join(stale)}). Re-solve to rebuild "
                    f"it — POST /schedule, or press Build schedule."
                ),
                "missing": list(stale),
            })
    return ds


def dataset_is_usable():
    """Whether the live dataset can be read without re-solving."""
    name = dataset_name()
    if not name:
        return True
    cached = _cache["dataset"]
    if cached is not None and _cache["version"] == store.current_version(name):
        ds = cached
    else:
        ds = store.get_dataset(name)
    return not timegrid.missing_keys((ds or {}).get("config"))


def set_loaded(name, dataset, version=None):
    """Adopt a dataset as the live one.

    `version` is the schedule version this dict belongs to. Callers that do
    not yet know it leave it None, which simply means the next read verifies
    against the store instead of trusting the cache — safe either way.
    """
    _cache.update({"name": name, "dataset": dataset, "version": version})


def current_schedule():
    """The live schedule record, or None."""
    name = dataset_name()
    return store.get_current(name) if name else None


def read_dataset_file(name):
    """Load a generated dataset from disk."""
    path = os.path.join(DATA_ROOT, name, "dataset.json")
    if not os.path.exists(path):
        raise HTTPException(404, f"dataset {name!r} not found at {path}")
    with open(path) as f:
        return json.load(f)


def require_dataset():
    """The name of the live dataset, or 409."""
    name = dataset_name()
    if not name:
        raise HTTPException(409, "No dataset loaded — POST /schedule first.")
    return name


def require_schedule():
    """(dataset, appointments) for the live schedule, or 409."""
    cur = current_schedule()
    if cur is None:
        raise HTTPException(409, "No schedule yet — POST /schedule first.")
    return loaded_dataset(), cur["scheduled"]
