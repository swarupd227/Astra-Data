"""The G2 state machine's pure logic — story S4.1.2.

    "State machine PROPOSED -> DRAFT -> IN_REVIEW -> APPROVED -> BUILT -> PUBLISHED
    enforced."

The graph reads and writes (accepting a family, freezing a version hash, editing a table's
mode) are exercised against real PostgreSQL in the integration suite. What is tested here
is the transition table itself — legal moves, illegal ones, and that the version hash
excludes exactly the audit-timestamp fields that would otherwise make it change without the
design itself changing.
"""

from __future__ import annotations

import pytest

from astra_graph.errors import InvalidRequestError
from astra_graph.model_lifecycle import (
    FAMILY_TRANSITIONS,
    hashable_document,
    regression_status,
    require_transition,
)

# ------------------------------------------------------------------------ require_transition


def test_proposed_may_move_to_draft() -> None:
    require_transition("PROPOSED", "DRAFT")  # does not raise


def test_singleton_may_move_to_draft() -> None:
    require_transition("SINGLETON", "DRAFT")


def test_draft_may_move_to_in_review() -> None:
    require_transition("DRAFT", "IN_REVIEW")


def test_in_review_may_move_to_approved_or_back_to_draft() -> None:
    require_transition("IN_REVIEW", "APPROVED")
    require_transition("IN_REVIEW", "DRAFT")


def test_approved_may_move_to_built() -> None:
    require_transition("APPROVED", "BUILT")


def test_built_may_move_to_published() -> None:
    require_transition("BUILT", "PUBLISHED")


def test_proposed_cannot_skip_straight_to_in_review() -> None:
    with pytest.raises(InvalidRequestError, match="DRAFT"):
        require_transition("PROPOSED", "IN_REVIEW")


def test_draft_cannot_move_backwards_to_proposed() -> None:
    with pytest.raises(InvalidRequestError):
        require_transition("DRAFT", "PROPOSED")


def test_published_may_move_to_draft_a_change_request_story_s4_3_3() -> None:
    require_transition("PUBLISHED", "DRAFT")


def test_deprecated_is_terminal_for_this_transition_table() -> None:
    with pytest.raises(InvalidRequestError, match="terminal state"):
        require_transition("DEPRECATED", "DRAFT")


def test_an_unknown_current_state_has_no_legal_moves() -> None:
    with pytest.raises(InvalidRequestError):
        require_transition(None, "DRAFT")


def test_every_declared_family_state_has_a_transition_table_entry() -> None:
    # ModelFamily.state's own closed enum (ontology/nodes.py, §12.2) is the set of values a
    # family can actually hold; every one of them must be a key here, even if terminal (an
    # empty set of legal next states), or a family could land in a state this module cannot
    # reason about at all.
    from astra_graph.ontology import node_type

    declared_states = set(node_type("ModelFamily").property_spec("state").enum or ())
    assert set(FAMILY_TRANSITIONS) == declared_states


# ---------------------------------------------------------------------------- hashable_document


def test_the_generation_timestamp_is_excluded_from_the_hashed_document() -> None:
    document = {"grain_statement": "One row per Region.", "design_generated_at": "2027-01-01T00:00:00Z"}
    assert "design_generated_at" not in hashable_document(document)


def test_an_existing_version_is_excluded_so_re_hashing_is_idempotent() -> None:
    document = {"grain_statement": "One row per Region.", "version": "sha256:deadbeef"}
    assert "version" not in hashable_document(document)


def test_everything_else_is_kept() -> None:
    document = {"grain_statement": "One row per Region.", "tables": [{"id": "t1"}]}
    assert hashable_document(document) == document


def test_two_documents_differing_only_in_generation_time_hash_the_same() -> None:
    from astra_graph.context.canonical import canonical_json, context_hash

    first = {"grain_statement": "One row per Region.", "design_generated_at": "2027-01-01T00:00:00Z"}
    second = {"grain_statement": "One row per Region.", "design_generated_at": "2027-06-01T00:00:00Z"}
    assert context_hash(canonical_json(hashable_document(first))) == context_hash(
        canonical_json(hashable_document(second))
    )


# --------------------------------------------------------------------------- regression_status


async def test_regression_status_is_vacuously_true_with_no_mus() -> None:
    status = await regression_status(None, "some-graph", "some-family-id")
    assert status.passed is True
    assert status.released_mu_count == 0
