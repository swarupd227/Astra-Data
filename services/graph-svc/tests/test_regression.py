"""§10.6 regression -- story S7.7.1, closing F7.7/E7. Pure logic and the in-memory
schedule store; the scheduler's real orchestration (execute-then-diff, drift detection,
exception-opening, notification, auto-pause) and the export bundle's own real content are
covered in `test_integration_regression.py`, the identical split
`test_integration_scheduling.py`'s own docstring already draws for harvest schedules.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from astra_graph.harvest.schedule import Cadence
from astra_graph.regression import (
    DEFAULT_CADENCE_MINUTES,
    InMemoryRegressionScheduleStore,
    LocalNotificationChannel,
    RegressionError,
    new_regression_schedule,
)

NOW = datetime(2027, 6, 1, 12, 0, tzinfo=UTC)


# ------------------------------------------------------------------------ new_regression_schedule


def test_new_regression_schedule_defaults_to_weekly() -> None:
    schedule = new_regression_schedule(workbook_id="wb-1", created_by="user:pm@artizent.example", now=NOW)
    assert schedule.cadence.every_minutes == DEFAULT_CADENCE_MINUTES
    assert schedule.cadence.describe() == "every 10080 minutes"
    assert schedule.workspace == "dev"
    assert schedule.enabled is True
    assert schedule.last_result is None
    assert schedule.last_seen_drift_seq == 0


def test_new_regression_schedule_does_not_fire_immediately() -> None:
    """The identical 'creating a schedule does not start a run' reasoning
    `harvest.schedule.new_schedule` already gives."""
    schedule = new_regression_schedule(
        workbook_id="wb-1", cadence=Cadence(every_minutes=60), created_by="user:pm@artizent.example", now=NOW,
    )
    assert schedule.next_run_at > NOW.isoformat()


def test_new_regression_schedule_honours_an_explicit_cadence_and_workspace() -> None:
    schedule = new_regression_schedule(
        workbook_id="wb-1", cadence=Cadence(daily_at="02:00"), workspace="prod",
        created_by="user:pm@artizent.example", now=NOW,
    )
    assert schedule.cadence.daily_at == "02:00"
    assert schedule.workspace == "prod"


def test_schedule_as_dict_hides_next_run_at_when_disabled() -> None:
    schedule = new_regression_schedule(workbook_id="wb-1", created_by="user:pm@artizent.example", now=NOW)
    schedule.enabled = False
    schedule.paused_reason = "paused automatically after 5 consecutive failed checks"
    body = schedule.as_dict()
    assert body["next_run_at"] is None
    assert body["paused_reason"] == "paused automatically after 5 consecutive failed checks"


# --------------------------------------------------------------------- InMemoryRegressionScheduleStore


@pytest.fixture
def store() -> InMemoryRegressionScheduleStore:
    return InMemoryRegressionScheduleStore()


async def test_create_and_get(store: InMemoryRegressionScheduleStore) -> None:
    schedule = new_regression_schedule(workbook_id="wb-1", created_by="user:pm@artizent.example", now=NOW)
    created = await store.create(schedule)
    assert created is schedule
    fetched = await store.get(schedule.id)
    assert fetched is schedule
    assert await store.get("no-such-id") is None


async def test_get_by_workbook(store: InMemoryRegressionScheduleStore) -> None:
    schedule = new_regression_schedule(workbook_id="wb-1", created_by="user:pm@artizent.example", now=NOW)
    await store.create(schedule)
    assert await store.get_by_workbook("wb-1") is schedule
    assert await store.get_by_workbook("wb-2") is None


async def test_a_second_schedule_for_the_same_workbook_is_refused(store: InMemoryRegressionScheduleStore) -> None:
    """The identical 'one schedule per scope' reasoning `harvest.schedule`'s own unique
    index already enforces -- a second schedule over the same workbook would race the
    first and could double-open ExceptionCases on the same FAIL."""
    first = new_regression_schedule(workbook_id="wb-1", created_by="user:pm@artizent.example", now=NOW)
    await store.create(first)
    second = new_regression_schedule(workbook_id="wb-1", created_by="user:pm2@artizent.example", now=NOW)
    with pytest.raises(RegressionError, match="already exists"):
        await store.create(second)


async def test_list_schedules_is_sorted_by_created_at(store: InMemoryRegressionScheduleStore) -> None:
    first = new_regression_schedule(workbook_id="wb-1", created_by="a", now=NOW)
    from datetime import timedelta

    second = new_regression_schedule(workbook_id="wb-2", created_by="b", now=NOW + timedelta(minutes=1))
    await store.create(second)
    await store.create(first)
    listed = await store.list_schedules()
    assert [s.workbook_id for s in listed] == ["wb-1", "wb-2"]


async def test_due_claims_and_advances(store: InMemoryRegressionScheduleStore) -> None:
    schedule = new_regression_schedule(
        workbook_id="wb-1", cadence=Cadence(every_minutes=60), created_by="user:pm@artizent.example", now=NOW,
    )
    await store.create(schedule)

    from datetime import timedelta

    not_yet = await store.due(now=NOW + timedelta(minutes=30))
    assert not_yet == []

    ready = await store.due(now=NOW + timedelta(minutes=61))
    assert [s.id for s in ready] == [schedule.id]
    # Claiming advances next_run_at, so an immediate second poll finds nothing due.
    again = await store.due(now=NOW + timedelta(minutes=61))
    assert again == []


async def test_due_skips_disabled_schedules(store: InMemoryRegressionScheduleStore) -> None:
    schedule = new_regression_schedule(
        workbook_id="wb-1", cadence=Cadence(every_minutes=5), created_by="user:pm@artizent.example", now=NOW,
    )
    await store.create(schedule)
    await store.set_enabled(schedule.id, enabled=False, reason="paused for maintenance")

    from datetime import timedelta

    assert await store.due(now=NOW + timedelta(hours=1)) == []


async def test_due_for_drift_orders_by_watermark(store: InMemoryRegressionScheduleStore) -> None:
    behind = new_regression_schedule(workbook_id="wb-behind", created_by="a", now=NOW)
    caught_up = new_regression_schedule(workbook_id="wb-caught-up", created_by="b", now=NOW)
    await store.create(behind)
    await store.create(caught_up)
    await store.mark_drift_checked(caught_up.id, seq=100)

    candidates = await store.due_for_drift(latest_seq=100)
    assert [s.workbook_id for s in candidates] == ["wb-behind"]


async def test_mark_drift_checked_updates_the_watermark(store: InMemoryRegressionScheduleStore) -> None:
    schedule = new_regression_schedule(workbook_id="wb-1", created_by="a", now=NOW)
    await store.create(schedule)
    await store.mark_drift_checked(schedule.id, seq=42)
    assert schedule.last_seen_drift_seq == 42


async def test_touch_next_run_only_ever_brings_a_firing_forward(store: InMemoryRegressionScheduleStore) -> None:
    """`trigger_after_publish`'s own contract: nudge a firing sooner, never push it later."""
    schedule = new_regression_schedule(
        workbook_id="wb-1", cadence=Cadence(every_minutes=60), created_by="a", now=NOW,
    )
    await store.create(schedule)
    original_next_run = schedule.next_run_at

    from datetime import timedelta

    later = NOW + timedelta(hours=5)
    await store.touch_next_run(schedule.id, next_run_at=later)
    assert schedule.next_run_at == original_next_run  # unchanged: later than the original

    sooner = NOW + timedelta(minutes=1)
    await store.touch_next_run(schedule.id, next_run_at=sooner)
    assert schedule.next_run_at < original_next_run


async def test_record_run_resets_consecutive_failures_on_pass(store: InMemoryRegressionScheduleStore) -> None:
    schedule = new_regression_schedule(workbook_id="wb-1", created_by="a", now=NOW)
    await store.create(schedule)
    await store.record_run(schedule.id, run_id="run-1", result="FAIL", error=None, finished_at=NOW)
    await store.record_run(schedule.id, run_id="run-2", result="FAIL", error=None, finished_at=NOW)
    assert schedule.consecutive_failures == 2

    await store.record_run(schedule.id, run_id="run-3", result="PASS", error=None, finished_at=NOW)
    assert schedule.consecutive_failures == 0
    assert schedule.last_result == "PASS"
    assert schedule.last_run_id == "run-3"


async def test_set_enabled_clears_the_reason_on_resume(store: InMemoryRegressionScheduleStore) -> None:
    schedule = new_regression_schedule(workbook_id="wb-1", created_by="a", now=NOW)
    await store.create(schedule)
    await store.set_enabled(schedule.id, enabled=False, reason="maintenance window")
    assert schedule.paused_reason == "maintenance window"

    resumed = await store.set_enabled(schedule.id, enabled=True, reason=None)
    assert resumed is not None
    assert resumed.enabled is True
    assert resumed.paused_reason is None


async def test_set_enabled_on_an_unknown_schedule_returns_none(store: InMemoryRegressionScheduleStore) -> None:
    assert await store.set_enabled("no-such-id", enabled=False, reason="x") is None


# --------------------------------------------------------------------------- notification


async def test_local_notification_channel_never_raises(caplog: pytest.LogCaptureFixture) -> None:
    """The honest 'no outward channel, notify the role not a person' disclosure -- see
    this module's own docstring."""
    import logging

    caplog.set_level(logging.INFO, logger="astra_graph.regression")
    channel = LocalNotificationChannel()
    await channel.notify_regression(workbook_id="wb-1", exception_case_id="case-1", fail_count=3)
    assert "client_report_owner" in caplog.text
    assert "wb-1" in caplog.text
