"""
Panelist — the time grid, read from a dataset's config.

The day boundaries are decided once, by the generator, and written into
`config`. Every other module reads them back from here rather than keeping a
private copy, because a stale `SLOTS_PER_DAY_RAW = 32` in the solver cannot
fail loudly — nothing crashes, every appointment simply renders at the wrong
clock time. Generate a dataset over 08:00-18:00 and a hardcoded copy still
believes 32 slots from 09:00.

This module deliberately depends on nothing. The generator owns what the time
model *is*; everything downstream only reads it back.

The DEFAULT_* values exist for datasets generated before `slots_per_day_raw`
and `day_start_minutes` were written into config, and can be dropped once
those are regenerated.
"""

DEFAULT_SLOTS_PER_DAY_RAW = 32
DEFAULT_DAY_START_MINUTES = 9 * 60


def slots_per_day_raw(config):
    """Slots per day on the raw grid, including the (unusable) lunch band, so
    that absolute_slot = day * raw + slot_in_day stays simple."""
    return config.get("slots_per_day_raw", DEFAULT_SLOTS_PER_DAY_RAW)


def day_start_minutes(config):
    """Minutes past midnight at which each day's slot 0 begins."""
    return config.get("day_start_minutes", DEFAULT_DAY_START_MINUTES)


def horizon(config):
    """One past the last absolute slot in the week."""
    return config["days"] * slots_per_day_raw(config)


def absolute(config, day, slot_in_day):
    return day * slots_per_day_raw(config) + slot_in_day


def split(config, absolute_slot):
    """(day, slot_in_day) for an absolute slot."""
    raw = slots_per_day_raw(config)
    return absolute_slot // raw, absolute_slot % raw


def clock(config, slot_in_day):
    """Clock time for a slot index *within a day*.

    Takes slot-in-day rather than an absolute slot on purpose: an
    end-exclusive boundary at the last slot of a day would otherwise wrap to
    the following day's start and report a nonsense range.
    """
    mins = day_start_minutes(config) + slot_in_day * config["slot_minutes"]
    return f"{mins // 60:02d}:{mins % 60:02d}"
