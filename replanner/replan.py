"""
Panelist — replanner.

Takes an existing schedule + a disruption event, produces a new schedule
that resolves it with minimal disturbance (guide section 2.3, "the heart
of the assignment").

Disruption types (minimum, per assignment spec):
- company_late: company arrives N hours late
- panel_drop: a panel drops out
- student_withdraw: a student withdraws
  NOTE (guide section 11): withdrawal due to a mid-day offer must cancel
  ALL of that student's remaining interviews for the day, not just one.
- room_unavailable: a room becomes unavailable

Uses scheduler.model.SchedulingModel with prior_schedule set, so the
solve is warm-started and penalizes deviation from the existing schedule.
Reuses the same constraint-building code as the initial scheduler --
do not duplicate solver logic here.
"""

from scheduler.model import SchedulingModel


def apply_disruption(schedule, disruption):
    """Mutate the problem input (remove/modify companies, students, rooms,
    panels, or slots) according to the disruption event, before re-solving."""
    raise NotImplementedError


def replan(prior_schedule, disruption, companies, students, rooms, churn_cap_pct=10):
    """
    Returns (new_schedule, diff, status).

    diff should contain:
        - added / removed / moved interviews
        - affected students and companies
        - a generated "notify" list
        - churn count and percentage

    If churn_cap_pct would be exceeded, do not silently apply -- surface
    this back to the caller so the coordinator can confirm or request a
    looser fix (guide section 4, question 3).
    """
    raise NotImplementedError


def compute_diff(old_schedule, new_schedule):
    raise NotImplementedError


def compute_notify_list(diff):
    raise NotImplementedError
