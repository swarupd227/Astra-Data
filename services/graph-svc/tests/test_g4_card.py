"""G4 decommission -- story S9.3.1, opening F9.3.

Pure pieces only (`_clean_rationale`, `_parse_target_date`, `_window_elapsed`'s own date
math, the result dataclasses' own `as_dict()`); `readiness_checklist`/`g4_card`/
`approve`/`defer` are graph-and-adapter-coupled throughout and are covered end to end in
`test_integration_g4_card.py` instead, the same "pure core, graph-coupled shell" split
this epic's own prior stories already established.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from astra_graph.errors import InvalidRequestError
from astra_graph.g4_card import (
    GATE,
    MIN_RATIONALE_LENGTH,
    DecommissionConfirmation,
    G4DecisionResult,
    ReadinessItem,
    _clean_rationale,
    _parse_target_date,
    _window_elapsed,
)


def test_gate_is_g4() -> None:
    assert GATE == "G4"


# ------------------------------------------------------------------------ _clean_rationale


def test_clean_rationale_strips_and_accepts_a_real_sentence() -> None:
    assert _clean_rationale("  a real sentence, long enough to pass  ") == "a real sentence, long enough to pass"


def test_clean_rationale_refuses_a_placeholder() -> None:
    with pytest.raises(InvalidRequestError, match=f"at least {MIN_RATIONALE_LENGTH}"):
        _clean_rationale("too short")


# ------------------------------------------------------------------------ _parse_target_date


def test_parse_target_date_accepts_a_real_future_date() -> None:
    now = datetime(2027, 6, 1, tzinfo=UTC)
    future = now + timedelta(days=30)
    expected = future.isoformat(timespec="milliseconds").replace("+00:00", "Z")
    assert _parse_target_date(future.isoformat(), now=now) == expected


def test_parse_target_date_refuses_an_unparseable_string() -> None:
    with pytest.raises(InvalidRequestError, match="not a real date"):
        _parse_target_date("not-a-date")


def test_parse_target_date_refuses_a_date_in_the_past() -> None:
    now = datetime(2027, 6, 1, tzinfo=UTC)
    past = (now - timedelta(days=1)).isoformat()
    with pytest.raises(InvalidRequestError, match="must be in the future"):
        _parse_target_date(past, now=now)


def test_parse_target_date_refuses_the_present_moment() -> None:
    now = datetime(2027, 6, 1, tzinfo=UTC)
    with pytest.raises(InvalidRequestError, match="must be in the future"):
        _parse_target_date(now.isoformat(), now=now)


# ------------------------------------------------------------------------ _window_elapsed


def test_window_elapsed_is_honestly_false_with_no_window() -> None:
    assert _window_elapsed(None) is False


def test_window_elapsed_is_false_before_the_end() -> None:
    now = datetime(2027, 6, 1, tzinfo=UTC)
    end = (now + timedelta(days=1)).isoformat().replace("+00:00", "Z")
    assert _window_elapsed(end, now=now) is False


def test_window_elapsed_is_true_after_the_end() -> None:
    now = datetime(2027, 6, 1, tzinfo=UTC)
    end = (now - timedelta(days=1)).isoformat().replace("+00:00", "Z")
    assert _window_elapsed(end, now=now) is True


def test_window_elapsed_is_true_exactly_at_the_end() -> None:
    now = datetime(2027, 6, 1, tzinfo=UTC)
    end = now.isoformat().replace("+00:00", "Z")
    assert _window_elapsed(end, now=now) is True


# ------------------------------------------------------------------------ result dataclasses


def test_readiness_item_as_dict_round_trips() -> None:
    item = ReadinessItem(
        key="all_released", label="All in-scope MUs released", met=True,
        evidence={"released": 3, "total": 3},
    )
    assert item.as_dict() == {
        "key": "all_released", "label": "All in-scope MUs released", "met": True,
        "evidence": {"released": 3, "total": 3},
    }


def test_decommission_confirmation_as_dict_round_trips() -> None:
    confirmation = DecommissionConfirmation(
        workbook_id="wb_1", confirmed_by="user:owner@client.example",
        confirmed_at="2027-06-01T09:00:00.000Z",
    )
    assert confirmation.as_dict() == {
        "workbook_id": "wb_1", "confirmed_by": "user:owner@client.example",
        "confirmed_at": "2027-06-01T09:00:00.000Z",
    }


def test_g4_decision_result_as_dict_round_trips() -> None:
    result = G4DecisionResult(
        site_id="site_1", gate_decision_id="gd_1", decision="APPROVED",
        archived_count=3, licence_release_value=12000.0, decommissioned_at="2027-06-01T09:00:00.000Z",
    )
    assert result.as_dict() == {
        "site_id": "site_1", "gate_decision_id": "gd_1", "decision": "APPROVED",
        "archived_count": 3, "licence_release_value": 12000.0,
        "decommissioned_at": "2027-06-01T09:00:00.000Z", "target_date": None,
    }


def test_g4_decision_result_defaults_are_honest() -> None:
    result = G4DecisionResult(site_id="site_1", gate_decision_id="gd_1", decision="DEFERRED", target_date="2027-07-01T00:00:00.000Z")
    assert result.archived_count == 0
    assert result.licence_release_value is None
    assert result.decommissioned_at is None
