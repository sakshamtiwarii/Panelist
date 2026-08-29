"""
Panelist — the API's dataset cache must not outlive its dataset.

`api/deps` caches the live dataset as plain dicts because the solver wants
dicts and rebuilding them from the tables on every solve is pure overhead. The
cache had no invalidation at all: once a worker had read a dataset it held that
copy forever.

Single-worker that is invisible, because the worker that applies a replan is
also the one that updates its own cache. Run under `uvicorn --workers N` — the
configuration the store exists to support — and every OTHER worker serves the
pre-amendment roster for the rest of its life: a company added by a replan
never appears in its /config, and a replan it computes is built from a roster
that no longer exists.

These drive `api.deps` against a store that changes underneath it, which is
what a second worker looks like from inside the first.
"""

import pytest

from generator.generate import build_dataset
from store import MemoryStore

DATASET = "cachetest"


@pytest.fixture
def deps(monkeypatch):
    """`api.deps` with a private store and a cold cache."""
    from api import deps as module

    store = MemoryStore()
    monkeypatch.setattr(module, "store", store)
    for key in ("dataset", "name", "version"):
        monkeypatch.setitem(module._cache, key, None)
    return module, store


@pytest.fixture(scope="module")
def sample():
    ds, _ = build_dataset(seed=42, companies=6, students=40, rooms=3, days=4)
    return ds


def _publish(store, name, ds, origin="solve"):
    """What a worker does when it commits a schedule."""
    store.amend_dataset(name, ds)
    store.put_schedule(name, [], [], {"status": "OPTIMAL"}, {}, origin=origin)


def test_a_roster_amended_by_another_worker_is_picked_up(deps, sample):
    module, store = deps
    _publish(store, DATASET, sample)
    assert len(module.loaded_dataset()["companies"]) == 6   # warms the cache

    # Another worker applies a replan that registers a late company.
    amended = {**sample, "companies": sample["companies"] + [{
        "id": "C900", "name": "Newcomer Ltd", "tier": 3, "cgpa_cutoff": 7.0,
        "panel_count": 1, "interview_minutes": 30, "duration_slots": 2,
        "shortlist_size": 0, "shortlist": [],
    }]}
    _publish(store, DATASET, amended, origin="replan")

    assert len(module.loaded_dataset()["companies"]) == 7
    assert any(c["id"] == "C900" for c in module.loaded_dataset()["companies"])


def test_an_unchanged_dataset_is_not_refetched(deps, sample):
    """Invalidation must not become "reload the roster on every request"."""
    module, store = deps
    _publish(store, DATASET, sample)

    reads = {"n": 0}
    original = store.get_dataset

    def counted(name):
        reads["n"] += 1
        return original(name)

    store.get_dataset = counted
    module.loaded_dataset()
    module.loaded_dataset()
    module.loaded_dataset()
    assert reads["n"] == 1, "the version stamp should have matched"


def test_a_different_dataset_becoming_live_clears_the_cache(deps, sample):
    module, store = deps
    _publish(store, DATASET, sample)
    assert module.dataset_name() == DATASET
    module.loaded_dataset()

    smaller = {**sample, "companies": sample["companies"][:2]}
    _publish(store, "other", smaller)
    # MemoryStore reports whichever dataset holds a schedule; either way the
    # cached dict must not be served for a dataset it does not belong to.
    name = module.dataset_name()
    assert len(module.loaded_dataset()["companies"]) == (
        2 if name == "other" else 6
    )


def test_set_loaded_without_a_version_still_verifies(deps, sample):
    """An unstamped adoption is safe, not sticky."""
    module, store = deps
    _publish(store, DATASET, sample)
    stale = {**sample, "companies": sample["companies"][:1]}
    module.set_loaded(DATASET, stale)          # no version given

    # The store is the source of truth, so the next read reconciles.
    assert len(module.loaded_dataset()["companies"]) == 6
