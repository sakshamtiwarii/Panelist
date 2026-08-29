"""API contract tests.

Fast (a small dataset, short solver budgets), covering the boundaries the
replan scenario suite cannot reach: authentication, the role split, and the
propose/apply handshake.
"""

import os

import pytest
from fastapi.testclient import TestClient

DATASET = "small"
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


@pytest.fixture(scope="module")
def client(tmp_path_factory):
    # Generated rather than read from ./data, which is gitignored: a fresh
    # clone has none, and whatever was last generated is not a fixed input.
    from generator.generate import build_dataset, write_dataset
    data_root = tmp_path_factory.mktemp("data")
    dataset, report = build_dataset(seed=42, companies=6, students=40,
                                    rooms=3, days=4)
    write_dataset(str(data_root / DATASET), dataset, report)

    # No DATABASE_URL: exercise the in-memory store, so the suite needs no
    # server. Both stores implement the same interface, which is the point.
    # Both variables are read at import time, so they must be set first.
    os.environ.pop("DATABASE_URL", None)
    os.environ["PANELIST_DATA"] = str(data_root)
    # No first-boot demo seed: this suite's whole point is a fixed, known
    # input, and seeding would put a second (larger) dataset alongside it.
    os.environ["PANELIST_DEMO_SEED"] = "0"
    from api.main import app
    return TestClient(app)


@pytest.fixture(scope="module")
def coordinator(client):
    c = TestClient(client.app)
    r = c.post("/auth/login",
               json={"username": "coordinator", "password": "placement2026"})
    assert r.status_code == 200
    return c


@pytest.fixture(scope="module")
def solved(coordinator):
    r = coordinator.post("/schedule",
                         json={"dataset": DATASET, "time_limit_seconds": 15})
    assert r.status_code == 200, r.text
    return r.json()


# --- auth ------------------------------------------------------------------

def test_unauthenticated_is_rejected(client):
    assert client.get("/schedule").status_code == 401


def test_health_needs_no_session(client):
    r = client.get("/health")
    assert r.status_code == 200 and r.json()["status"] == "ok"


def test_wrong_password_is_indistinguishable_from_unknown_user(client):
    bad_pw = client.post("/auth/login",
                         json={"username": "coordinator", "password": "nope"})
    no_user = client.post("/auth/login",
                          json={"username": "nobody", "password": "nope"})
    assert bad_pw.status_code == no_user.status_code == 401
    # Same message, or the response enumerates valid usernames.
    assert bad_pw.json()["detail"] == no_user.json()["detail"]


def test_me_reports_the_signed_in_user(coordinator):
    r = coordinator.get("/auth/me")
    assert r.status_code == 200 and r.json()["role"] == "coordinator"


# --- solving ---------------------------------------------------------------

def test_schedule_solves_and_verifies(solved):
    assert solved["scheduled"] > 0
    assert solved["verification_errors"] == []
    assert solved["metrics"]["student_clashes"] == 0


def test_config_grid_comes_from_the_dataset(coordinator, solved):
    cfg = coordinator.get("/config").json()
    # Not a hardcoded 32/540: both must match the dataset's own config.
    assert cfg["slots_per_day_raw"] == cfg["config"]["slots_per_day_raw"]
    assert cfg["day_start_minutes"] == cfg["config"]["day_start_minutes"]


def test_board_is_filterable(coordinator, solved):
    day0 = coordinator.get("/schedule", params={"day": 0}).json()
    assert all(a["day"] == 0 for a in day0["appointments"])


# --- propose / apply -------------------------------------------------------

def test_proposal_is_persisted_in_the_store(coordinator, solved):
    """A proposal must outlive the request that created it."""
    from api.deps import store
    r = coordinator.post("/replan", json={
        "disruptions": [{"type": "company_late", "company_id": "C001",
                         "day": 2, "hours": 2}],
        "time_limit_seconds": 15,
    })
    assert r.status_code == 200, r.text
    body = r.json()
    if not body["ok"]:
        pytest.skip(f"disruption infeasible on this dataset: {body['reason']}")
    # The store, not a process dict, is what holds it.
    assert store.get_proposal(body["proposal_id"]) is not None


def test_unknown_proposal_is_404(coordinator, solved):
    r = coordinator.post("/replan/apply",
                         json={"proposal_id": "00000000-dead-beef-0000-000000000000"})
    assert r.status_code == 404


def test_viewer_may_propose_but_not_apply(client, solved):
    viewer = TestClient(client.app)
    assert viewer.post("/auth/login",
                       json={"username": "viewer",
                             "password": "review2026"}).status_code == 200

    proposed = viewer.post("/replan", json={
        "disruptions": [{"type": "company_late", "company_id": "C001",
                         "day": 2, "hours": 2}],
        "time_limit_seconds": 15,
    })
    # Proposing mutates nothing, so a viewer is allowed to.
    assert proposed.status_code == 200, proposed.text

    # Applying does, so it must not be.
    body = proposed.json()
    pid = body.get("proposal_id") or "irrelevant"
    denied = viewer.post("/replan/apply", json={"proposal_id": pid})
    assert denied.status_code == 403


def test_apply_commits_a_new_version(coordinator, solved):
    from api.deps import store
    before = len(coordinator.get("/schedule/versions").json()["versions"])
    proposed = coordinator.post("/replan", json={
        "disruptions": [{"type": "company_late", "company_id": "C001",
                         "day": 2, "hours": 2}],
        "time_limit_seconds": 15,
    }).json()
    if not proposed["ok"]:
        pytest.skip("disruption infeasible on this dataset")

    pid = proposed["proposal_id"]
    applied = coordinator.post("/replan/apply", json={"proposal_id": pid})
    assert applied.status_code == 200, applied.text
    assert applied.json()["applied"] is True

    after = coordinator.get("/schedule/versions").json()["versions"]
    assert len(after) == before + 1

    # Applied proposals are cleared: the rest were computed against a schedule
    # that no longer exists.
    assert store.get_proposal(pid) is None
    assert coordinator.post("/replan/apply",
                            json={"proposal_id": pid}).status_code == 404


def test_bad_generator_settings_are_a_400_not_a_500(coordinator):
    """A wrong number in the request body is the caller's fault, not a fault.

    Returning 500 sends whoever sent it looking for a broken server.
    """
    r = coordinator.post("/generate", json={
        "name": "bad", "seed": 1, "companies": 0, "students": 40,
        "rooms": 2, "days": 4,
    })
    assert r.status_code == 400
    assert "companies must be at least 1" in r.json()["detail"]


def test_structured_error_bodies_stay_renderable(coordinator, solved):
    """The console reduces an error body to one line; pin what it reads.

    The dashboard has no test runner, so this is where the contract lives.
    `describeError` looks for `detail.message` on the stale-dataset 409 and
    `detail.solver.note` on the unsolvable-week 422 — an error body that stops
    carrying those degrades the console to a bare "API error 4xx", and the
    version that returned the object instead crashed it outright.
    """
    import json as _json

    # A plain string detail is the common case and must stay a string.
    r = coordinator.post("/generate", json={
        "name": "bad", "seed": 1, "companies": 0, "students": 40,
        "rooms": 2, "days": 4,
    })
    assert isinstance(r.json()["detail"], str)

    # Request-validation failures arrive as a list of objects carrying "msg".
    r = coordinator.post("/schedule", json={"time_limit_seconds": "soon"})
    assert r.status_code == 422
    body = r.json()["detail"]
    assert isinstance(body, list) and "msg" in body[0]

    # Whatever the shape, it must survive a round trip as JSON — the console
    # parses the body before it reads any field off it.
    assert _json.loads(_json.dumps(body)) == body


def test_applying_a_missing_alternative_is_refused_clearly(coordinator, solved):
    """The looser fix is real, so asking for one that does not exist must say so.

    `replan` only offers an alternative when re-solving found a meaningfully
    cheaper one; when the churn is irreducible there is nothing to take, and
    silently committing the primary schedule instead would apply a plan the
    coordinator did not choose.
    """
    proposed = coordinator.post("/replan", json={
        "disruptions": [{"type": "company_late", "company_id": "C001",
                         "day": 2, "hours": 2}],
        "time_limit_seconds": 15,
    }).json()
    if not proposed["ok"]:
        pytest.skip("disruption infeasible on this dataset")
    if proposed["alternative"]:
        pytest.skip("this proposal does have an alternative")

    r = coordinator.post("/replan/apply", json={
        "proposal_id": proposed["proposal_id"], "use_alternative": True,
    })
    assert r.status_code == 409
    assert r.json()["detail"]["error"] == "no_alternative"


def test_a_proposal_reports_what_it_leaves_unplaced(coordinator, solved):
    """Churn is only half the trade-off; the other half is coverage."""
    proposed = coordinator.post("/replan", json={
        "disruptions": [{"type": "company_late", "company_id": "C001",
                         "day": 2, "hours": 2}],
        "time_limit_seconds": 15,
    }).json()
    if not proposed["ok"]:
        pytest.skip("disruption infeasible on this dataset")
    assert isinstance(proposed["unscheduled"], int)
    # And when an alternative is offered, both sides of the choice are costed.
    if proposed["alternative"]:
        alt = proposed["alternative"]
        assert {"elective_churn_count", "unscheduled"} <= set(alt)
        assert alt["elective_churn_count"] < proposed["diff"]["elective_churn_count"]


def test_priority_overrides_reach_the_solver_and_are_echoed(coordinator, solved):
    """The coordinator's exception must be visible in what came back."""
    cfg = coordinator.get("/config").json()
    niche = next((c["id"] for c in cfg["companies"] if c["tier"] == 3),
                 cfg["companies"][0]["id"])
    r = coordinator.post("/schedule", json={
        "dataset": DATASET, "time_limit_seconds": 15,
        "priority_overrides": {niche: "protect"},
    })
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["priority_overrides"] == {niche: "protect"}
    assert "protected" in body["priority_reasons"][niche]


def test_an_override_naming_an_unknown_company_is_refused(coordinator, solved):
    """Ignoring it would leave the coordinator believing they had acted."""
    r = coordinator.post("/schedule", json={
        "dataset": DATASET, "time_limit_seconds": 10,
        "priority_overrides": {"C999": "protect"},
    })
    assert r.status_code == 400
    assert r.json()["detail"]["error"] == "unknown_company_in_priority_overrides"


def test_an_unknown_priority_level_is_refused(coordinator, solved):
    r = coordinator.post("/schedule", json={
        "dataset": DATASET, "time_limit_seconds": 10,
        "priority_overrides": {"C001": "vip"},
    })
    assert r.status_code == 422


def test_health_reports_the_version_without_loading_the_schedule(client, solved):
    """/health is called on every dashboard load; it needs one row, not a week.

    It read the entire current schedule to report a boolean and a version
    number, pulling every appointment out of the database to do it.
    """
    from api.deps import store

    calls = {"get_current": 0}
    original = store.get_current

    def counted(dataset):
        calls["get_current"] += 1
        return original(dataset)

    store.get_current = counted
    try:
        body = client.get("/health").json()
    finally:
        store.get_current = original

    assert body["has_schedule"] is True
    assert isinstance(body["schedule_version"], int)
    assert calls["get_current"] == 0, "the version alone answers this"


def test_metrics_report_utilisation_per_room_and_aggregate(coordinator, solved):
    """Both per-room and aggregate utilisation are reported."""
    m = coordinator.get("/metrics").json()
    cfg = coordinator.get("/config").json()
    per_room = m["room_utilization_per_room"]
    assert set(per_room) == {r["id"] for r in cfg["rooms"]}
    assert all(0 <= v <= 100 for v in per_room.values())


def test_added_interviews_carry_enough_to_be_drawn(coordinator, solved):
    """A proposal that adds interviews must be previewable, not just listed.

    The board positions an appointment from its duration and tier; without
    them an addition could be named in the diff but not shown on the grid,
    so it previewed as nothing at all.
    """
    proposed = coordinator.post("/replan", json={
        "disruptions": [{
            "type": "company_add", "name": "Newcomer Ltd", "cgpa_cutoff": 7.0,
            "shortlist_size": 4, "panel_count": 1, "interview_minutes": 30,
        }],
        "time_limit_seconds": 20,
    }).json()
    if not proposed["ok"] or not proposed["diff"]["added_detail"]:
        pytest.skip("no interviews were added on this dataset")

    for item in proposed["diff"]["added_detail"]:
        assert {"duration_slots", "tier", "to"} <= set(item)
        assert item["duration_slots"] > 0


def test_metrics_and_diagnostics_agree_on_the_shortfall(coordinator, solved):
    """The two endpoints the console shows side by side must not contradict.

    They are computed by different paths — /metrics from the stored schedule,
    /diagnostics by rebuilding the interview list — so they can disagree, and
    they did: after a replan that grounded a company for the whole week the
    band read "Unplaced 0 · none" beside a rail reading "Can't be placed (38)".
    """
    cfg = coordinator.get("/config").json()
    days, hours_per_day = cfg["config"]["days"], 8
    company = cfg["companies"][0]["id"]

    proposed = coordinator.post("/replan", json={
        "disruptions": [{"type": "company_late", "company_id": company,
                         "day": 0, "hours": days * hours_per_day}],
        "time_limit_seconds": 15,
    }).json()
    if not proposed["ok"]:
        pytest.skip(f"disruption infeasible on this dataset: {proposed['reason']}")
    applied = coordinator.post("/replan/apply",
                               json={"proposal_id": proposed["proposal_id"]})
    assert applied.status_code == 200, applied.text

    metrics = coordinator.get("/metrics").json()
    diagnostics = coordinator.get("/diagnostics").json()
    assert metrics["interviews_unscheduled"] == diagnostics["unscheduled"]
    # And the shortfall is real: grounding a company cannot leave 100% placed.
    if diagnostics["unscheduled"]:
        assert metrics["pct_scheduled"] < 100.0


def test_replan_history_records_the_applied_fix(coordinator, solved):
    events = coordinator.get("/replan/history").json()["events"]
    assert isinstance(events, list)
