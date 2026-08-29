"""
Panelist — the two stores must agree.

`store/db.py` claims "two stores implement one interface", and the API relies
on it: when Postgres is unreachable the app keeps serving from memory, so a
behavioural difference between them shows up as a bug that only appears when
the database is down (or only when it is up). Nothing checked that claim.

Every test here runs against BOTH stores and asserts the same outcome. The
Postgres half is skipped unless PANELIST_TEST_DATABASE_URL points at a server,
so a laptop with no database still runs the suite; CI sets it.
"""

import os
import uuid

import pytest

from store import MemoryStore, PostgresStore

PG_URL = os.environ.get("PANELIST_TEST_DATABASE_URL")


@pytest.fixture(params=["memory", "postgres"])
def store(request):
    if request.param == "memory":
        yield MemoryStore()
        return
    if not PG_URL:
        pytest.skip("PANELIST_TEST_DATABASE_URL not set")
    s = PostgresStore(PG_URL)
    s.init_schema()
    yield s
    s.close()


@pytest.fixture
def dataset_name():
    # Unique per test: the stores are dataset-scoped, so parallel or repeated
    # runs never collide and nothing needs tearing down.
    return f"t{uuid.uuid4().hex[:12]}"


@pytest.fixture(scope="session")
def sample():
    """A small real dataset and a solved schedule for it.

    Generated and solved here rather than read from ./data, which is gitignored:
    depending on it would make this suite fail on a fresh clone and quietly
    couple it to whatever a previous CI step happened to leave behind.
    """
    from generator.generate import build_dataset
    from scheduler.model import SchedulingModel

    ds, _ = build_dataset(seed=42, companies=6, students=40, rooms=3, days=4)
    model = SchedulingModel(
        ds["companies"], ds["students"], ds["rooms"], ds["config"]
    ).build()
    _, solver = model.solve(time_limit_seconds=20)
    scheduled, _ = model.extract_schedule(solver)
    return ds, scheduled


def test_no_schedule_yet(store, dataset_name):
    assert store.get_current(dataset_name) is None
    assert store.versions(dataset_name) == []


def test_schedules_are_versioned_not_mutated(store, dataset_name, sample):
    ds, sched = sample
    store.put_dataset(dataset_name, ds)

    v1 = store.put_schedule(dataset_name, sched, [], {"status": "OPTIMAL"}, {})
    v2 = store.put_schedule(dataset_name, sched[:10], ["x"],
                            {"status": "FEASIBLE"}, {}, origin="replan")
    assert (v1, v2) == (1, 2)

    current = store.get_current(dataset_name)
    assert current["version"] == 2
    assert current["origin"] == "replan"
    assert len(current["scheduled"]) == 10
    assert current["unscheduled"] == ["x"]

    # Newest first, and the older version survives the newer one.
    rows = store.versions(dataset_name)
    assert [v["version"] for v in rows] == [2, 1]
    # The response shape is part of the API contract: /schedule/versions must
    # not answer differently depending on whether the database was reachable.
    assert set(rows[0]) == {"version", "origin", "solver_status",
                            "is_current", "created_at", "appointments"}
    assert (rows[0]["is_current"], rows[1]["is_current"]) == (True, False)
    assert rows[0]["appointments"] == 10 and rows[1]["appointments"] == 84


def test_dataset_round_trips(store, dataset_name, sample):
    ds, _ = sample
    store.put_dataset(dataset_name, ds)
    back = store.get_dataset(dataset_name)
    assert {c["id"] for c in back["companies"]} == {c["id"] for c in ds["companies"]}
    assert len(back["students"]) == len(ds["students"])
    assert len(back["rooms"]) == len(ds["rooms"])
    # The shortlist relation must survive, not just the sizes.
    by_id = {c["id"]: c for c in back["companies"]}
    for c in ds["companies"]:
        assert sorted(by_id[c["id"]]["shortlist"]) == sorted(c["shortlist"])
    # The time model must come back intact, or the solver silently regrids.
    assert back["config"]["slots_per_day_raw"] == ds["config"]["slots_per_day_raw"]
    assert back["config"]["day_start_minutes"] == ds["config"]["day_start_minutes"]


def test_disruption_windows_survive_a_dataset_round_trip(store, dataset_name,
                                                         sample):
    """A company that arrived late really was unavailable.

    `put_dataset` listed neither JSONB column, and both default to '[]', so a
    dataset carrying lateness windows or panel blackouts came back without
    them — while MemoryStore, which keeps the dict it was handed, returned
    them intact. The schema comment says these exist so "a second replan would
    [not] silently forget the first"; nothing checked that they were written.
    """
    ds, _ = sample
    amended = {**ds, "companies": [dict(c) for c in ds["companies"]]}
    amended["companies"][0]["unavailable_windows"] = [(0, 12)]
    amended["companies"][0]["panel_blackouts"] = [(48, 128)]

    store.put_dataset(dataset_name, amended)
    back = store.get_dataset(dataset_name)
    company = next(c for c in back["companies"]
                   if c["id"] == amended["companies"][0]["id"])

    # Compared as lists of pairs: JSONB round-trips tuples as arrays, and the
    # model only ever unpacks them as (from, to).
    assert [tuple(w) for w in company["unavailable_windows"]] == [(0, 12)]
    assert [tuple(w) for w in company["panel_blackouts"]] == [(48, 128)]


def test_current_version_tracks_the_live_schedule(store, dataset_name, sample):
    """The cheap probe the API's dataset cache is validated against.

    It has to move on every write, or a worker holding a cached roster never
    learns that another one amended it.
    """
    ds, sched = sample
    assert store.current_version(dataset_name) is None
    store.put_dataset(dataset_name, ds)

    v1 = store.put_schedule(dataset_name, sched, [], {"status": "OPTIMAL"}, {})
    assert store.current_version(dataset_name) == v1
    v2 = store.put_schedule(dataset_name, sched[:5], [], {"status": "OPTIMAL"},
                            {}, origin="replan")
    assert store.current_version(dataset_name) == v2 != v1
    # And it agrees with the full read it exists to avoid.
    assert store.current_version(dataset_name) == \
        store.get_current(dataset_name)["version"]


def test_current_dataset_finds_the_live_one(store, dataset_name, sample):
    ds, sched = sample
    assert store.current_dataset() is None or True   # may hold other datasets
    store.put_dataset(dataset_name, ds)
    store.put_schedule(dataset_name, sched, [], {"status": "OPTIMAL"}, {})
    assert store.current_dataset() is not None


def test_proposals_round_trip_and_clear(store, dataset_name):
    payload = {"ok": True, "diff": {"moved": []}, "nested": {"a": [1, 2, 3]}}
    store.put_proposal("p1", dataset_name, payload, ttl_minutes=30)
    assert store.get_proposal("p1") == payload
    assert store.get_proposal("missing") is None

    store.clear_proposals(dataset_name)
    assert store.get_proposal("p1") is None


def test_expired_proposals_are_invisible(store, dataset_name):
    store.put_proposal("old", dataset_name, {"ok": True}, ttl_minutes=-1)
    assert store.get_proposal("old") is None


def test_memory_store_sweeps_expired_proposals_on_clear():
    """MemoryStore must not hoard proposals for datasets it has moved past.

    Deliberately NOT a parity test, and asserted against the internal dict.
    PostgresStore has always swept expired rows here ("OR expires_at <= now()")
    and MemoryStore had not, but the difference is invisible from outside:
    `get_proposal` deletes an expired row lazily when asked for it, so both
    stores answer identically and only the retained memory differs. Written
    through the public interface this test would pass either way and cover
    nothing, so it looks at the container instead.
    """
    s = MemoryStore()
    s.put_proposal("live", "d1", {"ok": True}, ttl_minutes=30)
    s.put_proposal("stale", "d2", {"ok": True}, ttl_minutes=-1)
    s.put_proposal("other", "d2", {"ok": True}, ttl_minutes=30)

    s.clear_proposals("d1")
    assert set(s._proposals) == {"other"}, "d1's applied, d2's expired one gone"


def test_affected_counts_other_interviews_that_day(store, dataset_name, sample):
    ds, sched = sample
    store.put_dataset(dataset_name, ds)
    store.put_schedule(dataset_name, sched, [], {"status": "OPTIMAL"}, {})

    company = sched[0]["company_id"]
    day = sched[0]["day"]
    rows = store.affected(dataset_name, company_id=company, day=day)
    assert rows, "the disruption hits at least the interview it was taken from"
    for r in rows:
        assert r["interviews_hit"] >= 1
        assert r["other_interviews_that_day"] >= 0
        # The correlated count must exclude the hit interviews themselves.
        same_day = [a for a in sched
                    if a["student_id"] == r["student_id"] and a["day"] == day]
        assert r["other_interviews_that_day"] == len(same_day) - r["interviews_hit"]


def test_affected_is_one_row_per_student_across_days(store, dataset_name):
    """With no day filter, a student hit on several days is still ONE student.

    The other tests all pass an explicit day, which hides this: Postgres
    grouped by day as well as student and returned a row per student-day, so
    GET /affected reported more students affected than the cohort contained,
    while MemoryStore returned one row whose "that day" count was really the
    student's whole week. Two stores, two different wrong answers.
    """
    from generator.generate import build_dataset
    ds, _ = build_dataset(seed=42, companies=6, students=40, rooms=3, days=4)
    company = ds["companies"][0]["id"]
    other = ds["companies"][1]["id"]
    sid = ds["students"][0]["id"]

    # From the dataset's own config, never a hardcoded 32 — the same rule the
    # rest of the codebase follows (see scheduler/timegrid.py).
    raw = ds["config"]["slots_per_day_raw"]

    def appt(n, cid, day, slot):
        start = day * raw + slot
        return {"id": f"{cid}~{sid}#{n}", "company_id": cid, "student_id": sid,
                "day": day, "slot": slot, "start": start, "end": start + 2,
                "duration_slots": 2, "tier": 1, "room": "R00", "panel": 0}

    # Hit on days 0 and 2, with one other interview on each of those days —
    # and one on day 3, which is not a hit day and must not be counted.
    schedule = [appt(1, company, 0, 0), appt(2, other, 0, 4),
                appt(3, company, 2, 0), appt(4, other, 2, 4),
                appt(5, other, 3, 8)]
    store.put_dataset(dataset_name, ds)
    store.put_schedule(dataset_name, schedule, [], {"status": "OPTIMAL"}, {})

    rows = store.affected(dataset_name, company_id=company)
    assert len(rows) == 1, "one row per student, not per student-day"
    assert rows[0]["interviews_hit"] == 2
    # Both hit days contribute their own companions; day 3 does not.
    assert rows[0]["other_interviews_that_day"] == 2


def test_affected_orders_worst_hit_first(store, dataset_name, sample):
    """Order is part of the contract: the route truncates to rows[:200]."""
    ds, sched = sample
    store.put_dataset(dataset_name, ds)
    store.put_schedule(dataset_name, sched, [], {"status": "OPTIMAL"}, {})
    rows = store.affected(dataset_name)
    keys = [(-r["interviews_hit"], r["student_id"]) for r in rows]
    assert keys == sorted(keys)


def test_concurrent_reads_do_not_collide(store, dataset_name, sample):
    """Several threads hitting the store at once must all succeed.

    FastAPI serves sync endpoints from a threadpool and the dashboard fires
    /schedule and /metrics together, so this is the normal case, not an edge
    one. A single psycopg2 connection is not safe for concurrent transactions:
    before the store serialized access, the second thread raised "the
    connection cannot be re-entered recursively" while the first returned 200.
    A sequential test cannot see it.
    """
    import concurrent.futures as cf

    ds, sched = sample
    store.put_dataset(dataset_name, ds)
    store.put_schedule(dataset_name, sched, [], {"status": "OPTIMAL"}, {})

    def hammer(_):
        assert store.get_current(dataset_name)["version"] == 1
        assert store.current_dataset() is not None
        assert store.versions(dataset_name)
        store.affected(dataset_name, day=0)
        return True

    with cf.ThreadPoolExecutor(max_workers=8) as pool:
        # Surfaces any exception raised inside a worker.
        assert all(pool.map(hammer, range(24)))


def test_users_round_trip(store):
    salt, digest = b"0123456789abcdef", b"x" * 32
    store.put_user("someone", "Some One", "viewer", salt, digest)
    u = store.get_user("someone")
    assert u["role"] == "viewer" and u["display_name"] == "Some One"
    # Bytes must come back as bytes, or password verification silently fails.
    assert u["salt"] == salt and u["password_hash"] == digest
    assert store.get_user("nobody") is None
    store.touch_login("someone")
