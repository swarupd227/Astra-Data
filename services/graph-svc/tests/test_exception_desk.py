"""The Exception Desk -- story S8.3.1, opening F8.3. Pure pieces only (`_age_seconds`,
`_clean_rationale`, the result dataclasses' own `as_dict()`, `LocalNotificationChannel`,
which touches no database at all). `queue`/`case_detail`/`bulk_assign`/every `decide_*`
function is graph-coupled throughout and is covered end to end in
`test_integration_exception_desk.py` instead, the same "pure core, graph-coupled shell"
split this epic's own prior stories already established.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from astra_graph.errors import InvalidRequestError
from astra_graph.exception_desk import (
    GATE,
    MIN_RATIONALE_LENGTH,
    LocalNotificationChannel,
    PatchResult,
    RedesignResult,
    _age_seconds,
    _clean_rationale,
)


def test_gate_is_g3() -> None:
    """§11.3's own "the choice is a G3 matter" -- see the module's own docstring for why
    this is a real, deliberate write, not an accidental early use of an unbuilt gate."""
    assert GATE == "G3"


def test_min_rationale_length_is_higher_than_a_bare_reason() -> None:
    """"A sentence" is a fuller bar than g2.MIN_RATIONALE_LENGTH's own 8 characters or
    model_lifecycle.MIN_CHANGE_REQUEST_REASON's own 10 -- disclosed as deliberately
    higher, not a reused smaller number."""
    assert MIN_RATIONALE_LENGTH == 20


# ------------------------------------------------------------------------- _age_seconds


def test_age_seconds_is_none_for_no_created_at() -> None:
    assert _age_seconds(None) is None
    assert _age_seconds("") is None


def test_age_seconds_is_none_for_an_unparseable_value() -> None:
    assert _age_seconds("not-a-timestamp") is None


def test_age_seconds_computes_real_elapsed_time() -> None:
    one_hour_ago = (datetime.now(UTC) - timedelta(hours=1)).isoformat().replace("+00:00", "Z")
    age = _age_seconds(one_hour_ago)
    assert age is not None
    assert 3590 < age < 3610  # ~3600s, with a little slack for real wall-clock jitter


# ---------------------------------------------------------------------- _clean_rationale


def test_clean_rationale_strips_and_accepts_a_real_sentence() -> None:
    assert _clean_rationale("  This measure double-counts returns after a schema change.  ") == (
        "This measure double-counts returns after a schema change."
    )


def test_clean_rationale_refuses_a_blank_string() -> None:
    with pytest.raises(InvalidRequestError, match="at least 20 characters"):
        _clean_rationale("   ")


def test_clean_rationale_refuses_a_too_short_reason() -> None:
    with pytest.raises(InvalidRequestError, match="at least 20 characters"):
        _clean_rationale("fixed it")


# ------------------------------------------------------------------------ result dataclasses


def test_patch_result_as_dict_round_trips_every_field() -> None:
    result = PatchResult(
        exception_case_id="exc_1", gate_decision_id="gd_1", measure_id="measure_1",
        outcome="closed", cases_reproved=("case_1",), cases_still_failing=(),
    )
    assert result.as_dict() == {
        "exception_case_id": "exc_1", "gate_decision_id": "gd_1", "measure_id": "measure_1",
        "outcome": "closed", "cases_reproved": ["case_1"], "cases_still_failing": [],
    }


def test_redesign_result_as_dict_round_trips_every_field() -> None:
    result = RedesignResult(
        exception_case_id="exc_1", gate_decision_id="gd_1", route="desktop",
        detail={"desktop_commit_hash": "abc123"},
    )
    assert result.as_dict() == {
        "exception_case_id": "exc_1", "gate_decision_id": "gd_1", "route": "desktop",
        "detail": {"desktop_commit_hash": "abc123"},
    }


# --------------------------------------------------------------------- LocalNotificationChannel


async def test_local_notification_channel_is_a_real_honest_no_op(caplog: pytest.LogCaptureFixture) -> None:
    """The identical disclosure `regression.LocalNotificationChannel`/`g2_reminders.
    LocalNotificationChannel` already give -- no outward channel, a real local log."""
    channel = LocalNotificationChannel()
    assert channel.kind == "local"
    with caplog.at_level("INFO"):
        await channel.notify_source_defect(
            workbook_id="wb_1", exception_case_id="exc_1", resolution="REPRODUCE",
        )
    assert "source-defect notification" in caplog.text
    assert "wb_1" in caplog.text
