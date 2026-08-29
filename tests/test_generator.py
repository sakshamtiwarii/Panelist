"""
Panelist — the generator's input contract.

`POST /generate` takes its numbers straight from a request body, so every
degenerate combination is reachable from outside. They used to surface from
deep inside the density report — `days=1` as `randint(1, 0)`, an empty cohort
as an IndexError on `sizes[0]` — which reaches the caller as a 500 with a
traceback when the real answer is "that instance has no companies".
"""

import pytest

from generator.generate import build_dataset


def test_a_single_day_week_is_legitimate():
    """Niche companies are biased to later days; a one-day week has none.

    This was `randint(1, days - 1)`, which is an error rather than a choice
    when days is 1.
    """
    ds, _ = build_dataset(seed=1, companies=6, students=40, rooms=2, days=1)
    assert ds["config"]["days"] == 1
    assert all(c["preferred_day"] == 0 for c in ds["companies"])


@pytest.mark.parametrize("field", ["companies", "students", "rooms", "days"])
def test_an_empty_dimension_is_refused_by_name(field):
    args = dict(seed=1, companies=6, students=40, rooms=2, days=4)
    args[field] = 0
    with pytest.raises(ValueError, match=f"{field} must be at least 1"):
        build_dataset(**args)


def test_a_non_positive_load_factor_is_refused():
    with pytest.raises(ValueError, match="load_factor must be positive"):
        build_dataset(seed=1, companies=6, students=40, rooms=2, days=4,
                      load_factor=0)


def test_the_ordinary_case_is_unaffected():
    ds, report = build_dataset(seed=42, companies=6, students=40, rooms=3,
                               days=4)
    assert len(ds["companies"]) == 6
    assert len(ds["students"]) == 40
    assert "CONFLICT DENSITY REPORT" in report
