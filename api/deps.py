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
_cache = {"dataset": None, "name": None}


def dataset_name():
    """Name of the dataset with a live schedule, or None."""
    if _cache["name"]:
        return _cache["name"]
    name = store.current_dataset()
    if name:
        _cache["name"] = name
    return name


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
    if _cache["dataset"] is None:
        _cache["dataset"] = store.get_dataset(name)
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
    ds = store.get_dataset(name) if _cache["dataset"] is None else _cache["dataset"]
    return not timegrid.missing_keys((ds or {}).get("config"))


def set_loaded(name, dataset):
    """Adopt a dataset as the live one."""
    _cache.update({"name": name, "dataset": dataset})


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
