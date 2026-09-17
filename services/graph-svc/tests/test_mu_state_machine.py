"""The §3.2 MU state machine -- story S12.1.1. Pure logic, no Temporal, no Postgres."""

from __future__ import annotations

import pytest

from astra_graph.migration_units import MU_STATES
from astra_graph.mu_state_machine import (
    MU_TRANSITIONS,
    TERMINAL_STATES,
    InvalidTransitionError,
    is_terminal,
    validate_transition,
)


def test_every_real_state_has_a_transition_row() -> None:
    assert set(MU_TRANSITIONS) == set(MU_STATES)


def test_the_real_chain_is_walkable_start_to_finish() -> None:
    chain = [
        "HARVESTED", "CLUSTERED", "MODEL_READY", "GENERATED", "PROVING", "PASSED",
        "ACCEPTED", "RELEASED", "DECOMMISSIONED",
    ]
    for from_state, to_state in zip(chain[:-1], chain[1:], strict=True):
        validate_transition(from_state, to_state)  # raises on failure


def test_clustered_can_go_to_blocked_or_model_ready() -> None:
    validate_transition("CLUSTERED", "BLOCKED")
    validate_transition("CLUSTERED", "MODEL_READY")


def test_blocked_can_only_exit_to_model_ready() -> None:
    validate_transition("BLOCKED", "MODEL_READY")
    with pytest.raises(InvalidTransitionError):
        validate_transition("BLOCKED", "GENERATED")


def test_proving_can_pass_or_fail() -> None:
    validate_transition("PROVING", "PASSED")
    validate_transition("PROVING", "FAILED")


def test_failed_can_mend_or_escalate() -> None:
    validate_transition("FAILED", "MENDING")
    validate_transition("FAILED", "ESCALATED")


def test_mending_can_return_to_proving_or_escalate() -> None:
    validate_transition("MENDING", "PROVING")
    validate_transition("MENDING", "ESCALATED")


def test_adjudicated_can_re_prove_or_pass_under_waiver() -> None:
    validate_transition("ADJUDICATED", "PROVING")
    validate_transition("ADJUDICATED", "PASSED")


def test_withdrawn_is_always_legal_from_a_non_terminal_state() -> None:
    for state in MU_STATES:
        if state in TERMINAL_STATES:
            continue
        validate_transition(state, "WITHDRAWN")  # never raises


def test_withdrawn_is_illegal_from_a_terminal_state() -> None:
    with pytest.raises(InvalidTransitionError):
        validate_transition("DECOMMISSIONED", "WITHDRAWN")
    with pytest.raises(InvalidTransitionError):
        validate_transition("WITHDRAWN", "WITHDRAWN")


def test_an_illegal_jump_is_refused() -> None:
    with pytest.raises(InvalidTransitionError) as exc_info:
        validate_transition("HARVESTED", "ACCEPTED")
    assert "HARVESTED" in str(exc_info.value)
    assert "ACCEPTED" in str(exc_info.value)


def test_a_terminal_state_has_no_further_real_exit() -> None:
    with pytest.raises(InvalidTransitionError):
        validate_transition("DECOMMISSIONED", "HARVESTED")


def test_is_terminal() -> None:
    assert is_terminal("DECOMMISSIONED") is True
    assert is_terminal("WITHDRAWN") is True
    assert is_terminal("PROVING") is False


def test_an_unrecognised_state_is_refused_not_a_key_error() -> None:
    with pytest.raises(InvalidTransitionError):
        validate_transition("NOT_A_REAL_STATE", "HARVESTED")
