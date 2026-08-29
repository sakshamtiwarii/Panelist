"""The time grid, read from a dataset's config.

Day boundaries are decided once by the generator and written into `config`.
Every other module reads them back from here rather than keeping a private
copy — a stale constant would not crash anything, it would just render every
appointment at the wrong clock time.

For the same reason a config missing these keys raises instead of falling back
to a default. This module depends on nothing else.
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


REQUIRED_KEYS = (
    "days", "slot_minutes", "usable_slots_per_day",
    "slots_per_day_count", "slots_per_day_raw", "day_start_minutes",
)


def missing_keys(config):
    """Which time-model keys a config lacks — empty tuple if it is usable.

    Lets a caller test a config before building anything from it, rather than
    discovering a stale dataset as a 500 at request time.
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
    """'Day 2 12:00' — how a coordinator reads a moment."""
    return f"Day {day + 1} {clock(config, slot_in_day)}"


def stamp_absolute(config, absolute_slot):
    """`stamp` for an absolute slot."""
    return stamp(config, *split(config, absolute_slot))


def overlaps(a0, a1, b0, b1):
    """Do the half-open slot ranges [a0, a1) and [b0, b1) intersect?"""
    return a0 < b1 and a1 > b0
