"""
Panelist — a disruption must never dead-end the coordinator.

The replanner's contract is that any event a coordinator can express gets an
answer: a proposal, or a named reason it is impossible. A bare INFEASIBLE is
neither — it tells them the week cannot be saved when the truth is usually
that one company cannot interview for the rest of the day.

`panel_drop` broke that contract twice. Each blackout is a fixed interval
against a Cumulative of capacity `panel_count`, so asking a one-panel company
to stand down three panels wrote three intervals into a model that could hold
one, and the whole week came back INFEASIBLE. Laying a blackout over an hour
the company had already interviewed in did the same, and the lock validator
did not catch it because it only compared against `panel_count`.
"""

import copy

import pytest

from generator.generate import build_dataset
from replanner.replan import replan
from scheduler.model import SchedulingModel


@pytest.fixture(scope="module")
def solved():
    ds, _ = build_dataset(seed=42, companies=6, students=40, rooms=3, days=4)
    model = SchedulingModel(
        ds["companies"], ds["students"], ds["rooms"], ds["config"]
    ).build()
    _, solver = model.solve(time_limit_seconds=20)
    scheduled, _ = model.extract_schedule(solver)
    return ds, scheduled


def _replan(ds, schedule, event, **kw):
    return replan(copy.deepcopy(ds), schedule, [event],
                  time_limit_seconds=20, **kw)


def test_dropping_more_panels_than_exist_still_proposes(solved):
    ds, schedule = solved
    smallest = min(ds["companies"], key=lambda c: c["panel_count"])
    mid = ds["config"]["slots_per_day_raw"]  # start of Day 2

    p = _replan(ds, schedule, {
        "type": "panel_drop", "company_id": smallest["id"],
        "count": smallest["panel_count"] + 2, "from_slot": mid,
    })
    assert p["ok"], p.get("reason")
    # Clamped to what was actually running, and said so.
    assert "interviews no one for the rest of the week" in p["disruptions_applied"][0]


def test_an_ordinary_panel_drop_is_unchanged(solved):
    """The clamp must not alter the case that already worked."""
    ds, schedule = solved
    biggest = max(ds["companies"], key=lambda c: c["panel_count"])
    mid = ds["config"]["slots_per_day_raw"]

    p = _replan(ds, schedule, {
        "type": "panel_drop", "company_id": biggest["id"],
        "count": 1, "from_slot": mid,
    })
    assert p["ok"], p.get("reason")
    said = p["disruptions_applied"][0]
    assert "loses 1 panel(s)" in said
    # Slots are reported on the clock, not as a raw grid index.
    assert "Day 2" in said and "slot" not in said


def test_a_blackout_over_completed_interviews_is_named_not_infeasible(solved):
    """Retroactively impossible must read differently from merely hard."""
    ds, schedule = solved
    raw = ds["config"]["slots_per_day_raw"]
    now_slot = raw  # everything on Day 1 has already happened

    # A company that actually ran interviews before `now_slot`.
    ran = {a["company_id"] for a in schedule if a["start"] < now_slot}
    company = next(c for c in ds["companies"] if c["id"] in ran)

    # Stand every panel down from the start of the week — which contradicts
    # the interviews that company has already held.
    p = _replan(ds, schedule, {
        "type": "panel_drop", "company_id": company["id"],
        "count": company["panel_count"], "from_slot": 0,
    }, now_slot=now_slot)

    assert not p["ok"]
    assert p.get("lock_conflicts"), "the contradiction must be named"
    assert company["name"] in " ".join(p["lock_conflicts"])
    # And it must point at the fix, rather than reporting a dead week.
    assert "from_slot" in p["reason"]
    assert "No valid schedule exists" not in p["reason"]
