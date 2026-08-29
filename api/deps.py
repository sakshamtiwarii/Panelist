"""Shared API state and dependencies.

The store, the loaded dataset, and the guards (`require_schedule`,
`require_dataset`) that every endpoint needing a live schedule goes through.

Which dataset is live is asked of the store rather than remembered in a module
variable, so a worker that did not serve the request that solved the schedule
can still find it. Only the dataset dicts are cached in-process; the store
stays the source of truth.
"""

import json
import os

from fastapi import HTTPException

from scheduler import timegrid
from store import open_store

DATA_ROOT = os.environ.get("PANELIST_DATA", "./data")
DEFAULT_DATASET = os.environ.get("PANELIST_DATASET", "primary")

# Postgres when DATABASE_URL points at a reachable server, an in-memory
# stand-in otherwise. GET /health reports which is active.
store = open_store()

# Cache only, never the source of truth. `version` is what makes it safe to
# hold: with several workers, a replan that amends the roster is applied by one
# of them, and the rest need a stamp to notice their copy is stale.
_cache = {"dataset": None, "name": None, "version": None}


def _forget():
    _cache.update({"dataset": None, "version": None})


def dataset_name():
    """Name of the dataset with a live schedule, or None.

    Falls back to what this process adopted only while the store has no current
    schedule — the window between loading a dataset and solving it.
    """
    name = store.current_dataset()
    if name:
        if _cache["name"] != name:
            # A different dataset is live; everything cached describes the old
            # one.
            _cache["name"] = name
            _forget()
        return name
    return _cache["name"]


def loaded_dataset():
    """The live dataset as plain dicts, rebuilt from the store on a miss.

    Every route reaches the store through here, so this is where a dataset
    missing time-model keys becomes an actionable 409 rather than a bare 500.
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

    `version` is the schedule version this dict belongs to; None makes the next
    read verify against the store instead of trusting the cache.
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
