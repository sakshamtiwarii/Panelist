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

A dataset generated before these keys existed raises rather than falling back
to the old 32/09:00 defaults. Substituting a default here would reintroduce
exactly the failure this module removes: nothing crashes, and every appointment
quietly renders at the wrong clock time. Regenerating the dataset is a second's
work; a schedule that is subtly wrong is not detectable at all.
"""


def _require(config, key):
    try:
        return config[key]
    except KeyError:
        raise KeyError(
            f"dataset config has no {key!r} — it predates the time model being "
            f"written into config. Regenerate it: "
            f"python -m generator.generate --out <dir> [...]"
        ) from None


# Every key the time model needs. Kept beside _require so the two cannot drift.
REQUIRED_KEYS = (
    "days", "slot_minutes", "usable_slots_per_day",
    "slots_per_day_count", "slots_per_day_raw", "day_start_minutes",
)


def missing_keys(config):
    """Which time-model keys a config lacks — empty tuple if it is usable.

    Lets a caller test a config BEFORE building anything from it. Without this
    the only way to discover a stale dataset is to hit the KeyError at request
    time, which surfaces as a 500 on whichever endpoint happens to touch the
    grid first.
    """
    return tuple(k for k in REQUIRED_KEYS if k not in (config or {}))


def slots_per_day_raw(config):
    """Slots per day on the raw grid, including the (unusable) lunch band, so
    that absolute_slot = day * raw + slot_in_day stays simple."""
    return _require(config, "slots_per_day_raw")


def day_start_minutes(config):
    """Minutes past midnight at which each day's slot 0 begins."""
    return _require(config, "day_start_minutes")


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


def stamp(config, day, slot_in_day):
    """'Day 2 12:00' — how a coordinator reads a moment.

    Lives here because four modules were each building this string from
    `clock()` by hand, and a raw slot index is not something anyone can check
    against a clock on the wall.
    """
    return f"Day {day + 1} {clock(config, slot_in_day)}"


def stamp_absolute(config, absolute_slot):
    """`stamp` for an absolute slot."""
    return stamp(config, *split(config, absolute_slot))


def overlaps(a0, a1, b0, b1):
    """Do the half-open slot ranges [a0, a1) and [b0, b1) intersect?

    The codebase's most-repeated test, and it had been written three different
    ways — `a < w1 and b > w0`, `not (hi <= b0 or lo >= b1)`, and inline
    variants. They agree, but only if every inequality is right in every copy,
    and an interval test that is wrong by one sign fails silently: interviews
    land in blocked rooms and nothing raises.
    """
    return a0 < b1 and a1 > b0
