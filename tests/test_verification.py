"""
Panelist — the independent re-check of a produced schedule.

`verify_schedule` exists because the solver is trusted but the greedy
room/panel colouring that recovers concrete assignments is not. It claimed to
"independently re-check every hard constraint" while checking only overlap and
CGPA cutoffs — so the two constraints the colouring is most likely to get
wrong, blocked room windows and company availability, were the two it never
looked at. Panel blackouts were invisible for the same reason: the colouring
assigns indices without knowing a panel walked out.

Each test drives a violation the solver would never produce, because the point
is to catch the extraction disagreeing with the model, not to re-test the
solver.
"""

import pytest

from generator.generate import build_dataset
from scheduler.model import SchedulingModel


@pytest.fixture(scope="module")
def solved():
    ds, _ = build_dataset(seed=42, companies=6, students=40, rooms=3, days=4)
    model = SchedulingModel(
        ds["companies"], ds["students"], ds["rooms"], ds["config"]
    ).build()
    _, solver = model.solve(time_limit_seconds=20)
    scheduled, _ = model.extract_schedule(solver)
    return ds, scheduled, model


def _verifier(ds, companies=None, rooms=None):
    model = SchedulingModel(companies or ds["companies"], ds["students"],
                            rooms or ds["rooms"], ds["config"])
    model._build_interviews()
    return model


def test_a_real_schedule_passes(solved):
    """Guard against a check that fires on everything."""
    _ds, scheduled, model = solved
    assert model.verify_schedule(scheduled) == []


def test_an_appointment_in_a_blocked_room_is_caught(solved):
    ds, scheduled, _ = solved
    a = scheduled[0]
    rooms = [
        dict(r, blocked_windows=r["blocked_windows"] + [{
            "day": a["day"], "from_slot": a["slot"],
            "to_slot": a["slot"] + a["duration_slots"], "reason": "test",
        }]) if r["id"] == a["room"] else r
        for r in ds["rooms"]
    ]
    errors = _verifier(ds, rooms=rooms).verify_schedule([a])
    assert any("is blocked over" in e for e in errors)


def test_an_appointment_while_the_company_is_away_is_caught(solved):
    ds, scheduled, _ = solved
    a = scheduled[0]
    companies = [
        dict(c, unavailable_windows=[(a["start"], a["end"])])
        if c["id"] == a["company_id"] else c
        for c in ds["companies"]
    ]
    errors = _verifier(ds, companies=companies).verify_schedule([a])
    assert any("is unavailable over" in e for e in errors)


def test_running_more_interviews_than_panels_left_standing_is_caught(solved):
    ds, scheduled, _ = solved
    a = scheduled[0]
    company = next(c for c in ds["companies"] if c["id"] == a["company_id"])
    # Every panel out from the start of the week: one interview is one too many.
    companies = [
        dict(c, panel_blackouts=[(0, 9999)] * c["panel_count"])
        if c["id"] == company["id"] else c
        for c in ds["companies"]
    ]
    errors = _verifier(ds, companies=companies).verify_schedule([a])
    assert any("still standing" in e for e in errors)


def test_a_blackout_that_leaves_enough_panels_is_not_flagged(solved):
    """The check is about capacity, not about the presence of a blackout."""
    ds, scheduled, _ = solved
    a = scheduled[0]
    company = next(c for c in ds["companies"] if c["id"] == a["company_id"])
    if company["panel_count"] < 2:
        pytest.skip("needs a company with a panel to spare")
    companies = [
        dict(c, panel_blackouts=[(0, 9999)] * (c["panel_count"] - 1))
        if c["id"] == company["id"] else c
        for c in ds["companies"]
    ]
    errors = _verifier(ds, companies=companies).verify_schedule([a])
    assert not any("still standing" in e for e in errors)
