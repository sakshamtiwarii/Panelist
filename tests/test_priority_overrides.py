"""
Panelist — the coordinator's exceptions to the tier default.

Guide section 4, question 2: "you decide the algorithm's default behavior, the
coordinator decides exceptions." The default is the tier weighting — when an
oversubscribed instance cannot place everything, tier-1 mass recruiters win.
That is a policy decision, and the guide is explicit that a company must not be
deprioritised silently by code with no way for a human to say otherwise.

The README claimed this control existed before it did; these tests are what
make the claim true.

Most of them assert on `interview_weight` rather than on a solve. The claims
are exact — a protected interview outranks any tier difference, a deprioritised
one yields to everything — and an oversubscribed instance does not prove
optimality inside a test-sized budget, so asserting on placement counts would
test the solver's time limit as much as the feature. One integration test
covers the wiring: that the weights really do reach the schedule.
"""

import pytest

from generator.generate import build_dataset
from scheduler.model import DEPRIORITISED_WEIGHT, SchedulingModel


@pytest.fixture(scope="module")
def dataset():
    ds, _ = build_dataset(seed=42, companies=6, students=50, rooms=1, days=2)
    return ds


def _model(ds, overrides=None):
    return SchedulingModel(ds["companies"], ds["students"], ds["rooms"],
                           ds["config"], priority_overrides=overrides)


def _iv(company):
    return {"company_id": company["id"], "tier": company["tier"]}


def _of_tier(ds, tier):
    return next(c for c in ds["companies"] if c["tier"] == tier)


def test_protect_outranks_every_tier_difference(dataset):
    mass, niche = _of_tier(dataset, 1), _of_tier(dataset, 3)
    default = _model(dataset)
    protected = _model(dataset, {niche["id"]: "protect"})
    # A protected tier-3 company must beat an unprotected tier-1 one, or the
    # override is only a tie-breaker within the order it claims to overrule.
    assert (protected.interview_weight(_iv(niche))
            > default.interview_weight(_iv(mass)))


def test_deprioritise_yields_to_the_whole_tier_order(dataset):
    mass, niche = _of_tier(dataset, 1), _of_tier(dataset, 3)
    default = _model(dataset)
    lowered = _model(dataset, {mass["id"]: "deprioritise"})
    # The crux: a coordinator can push a tier-1 mass recruiter below a tier-3
    # niche one. Without this the algorithm's priority is not overridable.
    assert (lowered.interview_weight(_iv(mass))
            < default.interview_weight(_iv(niche)))


def test_a_deprioritised_interview_is_still_worth_scheduling(dataset):
    """Lower priority means "yields", not "excluded".

    A weight of zero would let the solver leave the company out even on an
    empty grid, which is not what the coordinator asked for.
    """
    mass = _of_tier(dataset, 1)
    lowered = _model(dataset, {mass["id"]: "deprioritise"})
    assert lowered.interview_weight(_iv(mass)) == DEPRIORITISED_WEIGHT
    assert DEPRIORITISED_WEIGHT > 0


def test_saying_nothing_and_saying_normal_are_the_same(dataset):
    """The exception must not change the rule when nobody invoked it."""
    company = dataset["companies"][0]
    plain = _model(dataset).interview_weight(_iv(company))
    assert _model(dataset, {}).interview_weight(_iv(company)) == plain
    assert (_model(dataset, {company["id"]: "normal"})
            .interview_weight(_iv(company)) == plain)


def test_an_override_is_recorded_as_a_stated_reason(dataset):
    """Diagnostics come from constraint metadata, not post-hoc guesswork."""
    niche = _of_tier(dataset, 3)
    model = _model(dataset, {niche["id"]: "protect"}).build()
    reason = model.constraint_reasons.get(f"priority_override:{niche['id']}")
    assert reason and "protected" in reason and niche["name"] in reason


def test_protecting_a_dropped_company_actually_places_it(dataset):
    """The integration test: weights reach the schedule.

    Asserted against a company the default drops ENTIRELY, so the check is
    0 -> more-than-0 and holds whether the solver proved optimality or merely
    ran out of time.
    """
    def placed(overrides):
        model = _model(dataset, overrides).build()
        _, solver = model.solve(time_limit_seconds=10)
        scheduled, _ = model.extract_schedule(solver)
        return {c["id"]: sum(1 for a in scheduled if a["company_id"] == c["id"])
                for c in dataset["companies"]}

    before = placed({})
    dropped = [cid for cid, n in before.items() if n == 0]
    if not dropped:
        pytest.skip("this instance places every company; nothing to protect")

    target = dropped[0]
    assert placed({target: "protect"})[target] > 0
