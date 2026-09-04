"""The Wave Board's pure logic — story S3.2.2.

    "WIP limit per train and per state is configurable and shown; exceeding it warns and
    requires a reason."

The graph reads/writes (move, resequence, WIP-limit configuration, family-dependency
refusal) are exercised against real PostgreSQL in the integration suite. What is tested
here is ``WipStatus`` and reason validation — the parts a caller reads to decide whether a
move needs a reason at all.
"""

from __future__ import annotations

import pytest

from astra_graph.errors import InvalidRequestError
from astra_graph.train_overrides import MIN_OVERRIDE_REASON_LENGTH, WipStatus, _validate_reason


def test_no_limit_configured_means_never_exceeded() -> None:
    status = WipStatus(train_limit=None, train_count=999, state_limit=None, state_count=999)
    assert not status.exceeded


def test_a_train_limit_is_exceeded_once_the_new_member_would_push_past_it() -> None:
    status = WipStatus(train_limit=10, train_count=11, state_limit=None, state_count=0)
    assert status.train_exceeded
    assert status.exceeded


def test_landing_exactly_on_the_limit_is_not_exceeding_it() -> None:
    status = WipStatus(train_limit=10, train_count=9, state_limit=None, state_count=0)
    assert not status.train_exceeded


def test_a_state_limit_is_independent_of_the_train_limit() -> None:
    status = WipStatus(train_limit=None, train_count=500, state_limit=5, state_count=6)
    assert status.state_exceeded
    assert not status.train_exceeded
    assert status.exceeded


def test_validate_reason_accepts_a_reason_that_clears_the_floor() -> None:
    assert _validate_reason("client asked to prioritise treasury") == (
        "client asked to prioritise treasury"
    )


def test_validate_reason_rejects_one_below_the_floor() -> None:
    with pytest.raises(InvalidRequestError):
        _validate_reason("why")


def test_the_floor_is_eight_characters() -> None:
    assert MIN_OVERRIDE_REASON_LENGTH == 8
