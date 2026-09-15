"""The Evidence Chain -- story S11.3.1, opens F11.3. Pure unit tests for the parts that
need no Postgres; see test_integration_evidence_chain.py for the real advance/verify/
daily-root lifecycle against real `estate_event`/`svid_record`/`provenance` rows."""

from __future__ import annotations

import pytest

from astra_graph.evidence_chain import (
    GENESIS_HASH,
    ChainAnchorUnavailable,
    NullChainAnchor,
    _category_for_estate_event,
    _entry_hash,
)


class TestCategoryForEstateEvent:
    def test_a_gate_decision_node_is_categorised_as_a_gate_decision(self) -> None:
        assert _category_for_estate_event("estate.node.upserted", {"type": "GateDecision"}) == "gate_decision"

    def test_a_verdict_node_is_categorised_as_a_verdict(self) -> None:
        assert _category_for_estate_event("estate.node.upserted", {"type": "Verdict"}) == "verdict"

    def test_a_parity_run_node_is_categorised_as_a_verdict(self) -> None:
        assert _category_for_estate_event("estate.node.upserted", {"type": "ParityRun"}) == "verdict"

    def test_any_other_node_is_a_state_transition(self) -> None:
        assert _category_for_estate_event("estate.node.upserted", {"type": "Workbook"}) == "state_transition"

    def test_a_notice_with_no_node_type_is_a_state_transition(self) -> None:
        assert _category_for_estate_event("estate.source.drift", {}) == "state_transition"


class TestEntryHash:
    def test_the_same_prev_hash_and_payload_always_hash_the_same(self) -> None:
        assert _entry_hash(GENESIS_HASH, b"payload") == _entry_hash(GENESIS_HASH, b"payload")

    def test_a_different_prev_hash_changes_the_result(self) -> None:
        assert _entry_hash(GENESIS_HASH, b"payload") != _entry_hash("a" * 64, b"payload")

    def test_a_different_payload_changes_the_result(self) -> None:
        assert _entry_hash(GENESIS_HASH, b"payload-a") != _entry_hash(GENESIS_HASH, b"payload-b")

    def test_the_result_is_a_real_sha256_hex_digest(self) -> None:
        result = _entry_hash(GENESIS_HASH, b"payload")
        assert len(result) == 64
        int(result, 16)  # raises ValueError if not valid hex


class TestNullChainAnchor:
    async def test_refuses_with_a_real_explanation(self) -> None:
        with pytest.raises(ChainAnchorUnavailable, match="no external anchor is configured"):
            await NullChainAnchor().anchor(graph="g", day="2027-01-01", root_hash="abc")
