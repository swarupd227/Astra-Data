"""G2 cycle time and reminders — the pure decision logic. Story S4.2.2.

    "Board tile shows families awaiting G2, days waiting, and the approver; SLA breach
    (default 5 working days) is highlighted. Reminder notifications are sent at 3 and 5
    days."

The graph/event-log read (days waiting since `IN_REVIEW`), the question-count read, and
the reminder store's own idempotency are exercised against real PostgreSQL in the
integration suite. What is tested here is the two off-by-one-prone decisions: when a wait
counts as an SLA breach, and which reminder thresholds are due for a given wait.
"""

from __future__ import annotations

from astra_graph.g2_reminders import (
    DEFAULT_SLA_WORKING_DAYS,
    REMINDER_DAYS,
    due_reminder_days,
    is_breached,
)

# ------------------------------------------------------------------------------ is_breached


def test_default_sla_is_five_working_days() -> None:
    assert DEFAULT_SLA_WORKING_DAYS == 5


def test_waiting_exactly_the_sla_is_not_yet_a_breach() -> None:
    assert is_breached(5) is False


def test_one_working_day_past_the_sla_is_a_breach() -> None:
    assert is_breached(6) is True


def test_well_under_the_sla_is_not_a_breach() -> None:
    assert is_breached(1) is False


def test_a_custom_sla_is_honoured() -> None:
    assert is_breached(3, sla_working_days=2) is True
    assert is_breached(2, sla_working_days=2) is False


def test_no_measured_wait_is_never_reported_as_a_breach() -> None:
    # A family IN_REVIEW with no recorded transition into it (nothing in this codebase
    # produces this, but the tile must not fabricate a breach from an absent figure).
    assert is_breached(None) is False


# -------------------------------------------------------------------------- due_reminder_days


def test_reminder_thresholds_are_three_and_five_days() -> None:
    assert REMINDER_DAYS == (3, 5)


def test_nothing_is_due_before_day_three() -> None:
    assert due_reminder_days(0) == ()
    assert due_reminder_days(2) == ()


def test_the_day_three_reminder_is_due_from_day_three() -> None:
    assert due_reminder_days(3) == (3,)
    assert due_reminder_days(4) == (3,)


def test_both_reminders_are_due_from_day_five() -> None:
    assert due_reminder_days(5) == (3, 5)
    assert due_reminder_days(9) == (3, 5)


def test_no_measured_wait_has_nothing_due() -> None:
    assert due_reminder_days(None) == ()
