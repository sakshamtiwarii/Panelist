"""
Panelist — every interview is accounted for.

An interview whose company has no remaining availability never enters the CP
model: there is no start slot to give it. It was then dropped from the model's
interview list and never reappeared, so `extract_schedule` returned it as
neither scheduled nor unscheduled and it vanished from every total.

That is the failure mode this project is most exposed to. Nothing crashed;
`pct_scheduled` simply read 100% for a week that had silently dropped a
company's entire shortlist, and the diagnostics blamed the cohort's calendars
for the absence. A wrong answer delivered confidently is worse than an error.

These tests assert the accounting identity directly — scheduled + unscheduled
covers the demand, whatever the solver decided — and that the shortfall is
attributed to the company's availability rather than to contention.
"""

import copy

import pytest

from generator.generate import build_dataset
from scheduler.metrics import compute_metrics
from scheduler.model import SchedulingModel


@pytest.fixture(scope="module")
def dataset():
    ds, _ = build_dataset(seed=42, companies=6, students=40, rooms=3, days=4)
    return ds


@pytest.fixture(scope="module")
def grounded(dataset):
    """A dataset whose largest company cannot attend at all that week.

    The whole-week window is the honest way to force the condition: it is what
    an extreme `company_late` produces, and it makes every one of that
    company's interviews unplaceable rather than merely hard to place.
    """
    ds = copy.deepcopy(dataset)
    span = ds["config"]["slots_per_day_raw"] * ds["config"]["days"]
    ds["companies"][0]["unavailable_windows"] = [(0, span)]
    return ds


def _solve(ds, limit=20):
    model = SchedulingModel(
        ds["companies"], ds["students"], ds["rooms"], ds["config"]
    ).build()
    _, solver = model.solve(time_limit_seconds=limit)
    scheduled, unscheduled = model.extract_schedule(solver)
    return model, scheduled, unscheduled


def test_grounded_company_is_actually_unplaceable(grounded):
    """Guard the fixture: if this stops biting, the tests below prove nothing."""
    model, _, _ = _solve(grounded)
    assert len(model.unplaceable) == len(grounded["companies"][0]["shortlist"])


def test_nothing_is_dropped_from_the_totals(grounded):
    model, scheduled, unscheduled = _solve(grounded)
    # The accounting identity: every interview the instance has to place is
    # either placed or reported as unplaced. Nothing falls between.
    assert len(scheduled) + len(unscheduled) == len(model.demand())
    ids = {a["id"] for a in scheduled} | {iv["id"] for iv in unscheduled}
    assert ids == {iv["id"] for iv in model.demand()}


def test_metrics_do_not_report_success_on_a_dropped_shortlist(grounded):
    model, scheduled, unscheduled = _solve(grounded)
    m = compute_metrics(scheduled, unscheduled, grounded["students"],
                        grounded["rooms"], grounded["config"])
    grounded_count = len(grounded["companies"][0]["shortlist"])

    assert m["interviews_unscheduled"] >= grounded_count
    assert m["interviews_total"] == len(model.demand())
    # The headline number is the one that lied: it read 100.0 while a whole
    # company's shortlist had been dropped.
    assert m["pct_scheduled"] < 100.0


def test_shortfall_is_blamed_on_availability_not_on_students(grounded):
    model, scheduled, unscheduled = _solve(grounded)
    grounded_id = grounded["companies"][0]["id"]

    rows = model.diagnose_unscheduled(scheduled, unscheduled)
    row = next(r for r in rows if r["company_id"] == grounded_id)

    assert row["dominant_cause"] == "company_window"
    # The old message blamed the cohort for the company's absence, and carried
    # the room-capacity constraint tag while doing it.
    assert "unavailable" in row["reason"]
    assert "Students already have interviews" not in row["reason"]
    assert "rooms are occupied" not in row["reason"]
    assert row["constraint_tag"] and "room count" not in row["constraint_tag"]


def test_capacity_analysis_sizes_the_whole_problem(grounded):
    """The load ratio must include demand that never reached the solver."""
    model, scheduled, unscheduled = _solve(grounded)
    cap = model.capacity_analysis(scheduled, unscheduled)
    expected = sum(iv["duration_slots"] for iv in model.demand())
    assert cap["demand_slot_units"] == expected


def test_an_undisrupted_instance_is_unaffected(dataset):
    """The fix must not change the ordinary path: nothing is unplaceable."""
    model, scheduled, unscheduled = _solve(dataset)
    assert model.unplaceable == []
    assert len(scheduled) + len(unscheduled) == len(model.interviews)
