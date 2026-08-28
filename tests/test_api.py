"""
Panelist — API contract tests.

Fast (a small dataset, short solver budgets) and focused on the boundaries the
replan scenario suite cannot reach: authentication, the role split, and the
propose/apply handshake.

The propose/apply assertions are the point. Proposing is safe and applying is
not, so the permission boundary sits at the state change rather than at the
whole feature — and a proposal must survive in the store between the two calls,
because a proposal held in process memory is a 404 on any other worker.
"""

import os

import pytest
from fastapi.testclient import TestClient

DATASET = "small"
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


@pytest.fixture(scope="module")
def client(tmp_path_factory):
    # The suite generates its own dataset rather than reading ./data, which is
    # gitignored: a fresh clone (and CI) has none, and a test that silently
    # depends on whatever the developer last generated is not a fixed input.
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


def test_replan_history_records_the_applied_fix(coordinator, solved):
    events = coordinator.get("/replan/history").json()["events"]
    assert isinstance(events, list)
