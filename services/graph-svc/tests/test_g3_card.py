"""The G3 gate card -- story S9.1.1, opening F9.1/E9. Pure pieces only
(`_clean_rationale`, `_visual_summary`, `to_adaptive_card`, the result dataclasses' own
`as_dict()`); `g3_card`/`approve`/`request_changes`/`ask_question` are graph-coupled
throughout and are covered end to end in `test_integration_g3_card.py` instead, the same
"pure core, graph-coupled shell" split this epic's own prior stories already established.
"""

from __future__ import annotations

import pytest

from astra_graph.errors import InvalidRequestError
from astra_graph.g3_card import (
    DEFAULT_PARALLEL_WINDOW_WEEKS,
    GATE,
    MIN_RATIONALE_LENGTH,
    G3DecisionResult,
    G3Question,
    _clean_rationale,
    _visual_summary,
    to_adaptive_card,
)


def test_gate_is_g3() -> None:
    assert GATE == "G3"


def test_min_rationale_length_matches_exception_desk_precedent() -> None:
    """"A reason of at least one sentence" (§15.5) is the identical bar `exception_desk.
    MIN_RATIONALE_LENGTH` (S8.3.1) already set for the identical AC wording."""
    assert MIN_RATIONALE_LENGTH == 20


def test_default_parallel_window_matches_the_spec_literal() -> None:
    assert DEFAULT_PARALLEL_WINDOW_WEEKS == 4


# ---------------------------------------------------------------------- _clean_rationale


def test_clean_rationale_strips_and_accepts_a_real_sentence() -> None:
    assert _clean_rationale("  This report is accurate and ready for release.  ") == (
        "This report is accurate and ready for release."
    )


def test_clean_rationale_refuses_a_blank_string() -> None:
    with pytest.raises(InvalidRequestError, match="at least 20 characters"):
        _clean_rationale("   ")


def test_clean_rationale_refuses_a_too_short_reason() -> None:
    with pytest.raises(InvalidRequestError, match="at least 20 characters"):
        _clean_rationale("looks fine")


# ------------------------------------------------------------------------ _visual_summary


def test_visual_summary_averages_real_scores() -> None:
    visuals = [
        {"id": "v1", "structural_score": 0.9, "image_score": 0.8},
        {"id": "v2", "structural_score": 0.7, "image_score": None},
    ]
    result = _visual_summary(visuals)
    assert result["structural_score"] == pytest.approx(0.8)
    assert result["image_score"] == pytest.approx(0.8)


def test_visual_summary_is_honest_when_nothing_has_been_scored_yet() -> None:
    result = _visual_summary([{"id": "v1", "structural_score": None, "image_score": None}])
    assert result["structural_score"] is None
    assert result["image_score"] is None
    assert result["human_review_status"] == "not yet reviewed"
    assert result["reviewed"] == []


def test_visual_summary_lists_real_human_reviews() -> None:
    visuals = [
        {"id": "v1", "reviewed_by": "user:s.iyer@artizent.example", "reviewed_at": "2027-01-14T00:00:00.000Z"},
        {"id": "v2"},
    ]
    result = _visual_summary(visuals)
    assert result["human_review_status"] == "reviewed"
    assert result["reviewed"] == [
        {"visual_id": "v1", "reviewed_by": "user:s.iyer@artizent.example", "reviewed_at": "2027-01-14T00:00:00.000Z"},
    ]


# ------------------------------------------------------------------------ to_adaptive_card


def _card(**overrides: object) -> dict:
    base = {
        "workbook_id": "wb_1",
        "what": {"name": "Daily VaR", "site": "RQA", "pages": 2, "visuals": 6},
        "proof": {
            "cases_run": 41, "cases_pass": 41, "charter_version": "3", "sampled": False,
            "passes_the_charter": True, "waivers": [],
        },
        "visual": {"structural_score": 0.96, "image_score": 0.91, "reviewed": [], "human_review_status": "not yet reviewed"},
        "changes": {"c4_decisions": [], "redesigns": [], "model": None},
        "next": {"on_approval": "promote to test, then a parallel run", "parallel_window_weeks": 4},
        "latest_decision": None,
    }
    base.update(overrides)
    return base


def test_adaptive_card_is_a_real_adaptive_card_1_5_document() -> None:
    card = to_adaptive_card(_card())
    assert card["type"] == "AdaptiveCard"
    assert card["version"] == "1.5"
    assert card["$schema"] == "http://adaptivecards.io/schemas/adaptive-card.json"


def test_adaptive_card_carries_all_four_buttons() -> None:
    card = to_adaptive_card(_card())
    titles = [action["title"] for action in card["actions"]]
    assert titles == ["Approve", "Request changes", "Ask a question", "Open report"]


def test_adaptive_card_fact_set_carries_every_card_section() -> None:
    card = to_adaptive_card(_card())
    facts = {fact["title"]: fact["value"] for fact in card["body"][1]["facts"]}
    assert set(facts) == {"What", "Proof", "Visual", "Changes", "Next"}
    assert "41/41" in facts["Proof"]
    assert "full compare" in facts["Proof"]


def test_adaptive_card_shows_a_real_waiver_count_when_there_are_any() -> None:
    card = to_adaptive_card(_card(proof={
        "cases_run": 41, "cases_pass": 41, "charter_version": "3", "sampled": False,
        "passes_the_charter": True, "waivers": [{"subject_ref": "case_1"}],
    }))
    facts = {fact["title"]: fact["value"] for fact in card["body"][1]["facts"]}
    assert "1 waiver(s)" in facts["Proof"]


def test_adaptive_card_shows_sampled_when_the_run_was_sampled() -> None:
    card = to_adaptive_card(_card(proof={
        "cases_run": 41, "cases_pass": 41, "charter_version": "3", "sampled": True,
        "passes_the_charter": True, "waivers": [],
    }))
    facts = {fact["title"]: fact["value"] for fact in card["body"][1]["facts"]}
    assert "sampled" in facts["Proof"]
    assert "full compare" not in facts["Proof"]


# ---------------------------------------------------------------------- result dataclasses


def test_g3_decision_result_as_dict_round_trips() -> None:
    result = G3DecisionResult(
        workbook_id="wb_1", gate_decision_id="gd_1", decision="APPROVED",
        invoiced=True, tier="COMPLEX", unit_price=28_000.0,
    )
    assert result.as_dict() == {
        "workbook_id": "wb_1", "gate_decision_id": "gd_1", "decision": "APPROVED",
        "invoiced": True, "tier": "COMPLEX", "unit_price": 28_000.0,
    }


def test_g3_decision_result_defaults_to_not_invoiced() -> None:
    """Request changes/Ask a question never invoice -- `G3DecisionResult`'s own default
    construction (no invoicing fields passed) is what those two decisions still use."""
    result = G3DecisionResult(workbook_id="wb_1", gate_decision_id="gd_1", decision="CHANGES_REQUESTED")
    assert result.as_dict() == {
        "workbook_id": "wb_1", "gate_decision_id": "gd_1", "decision": "CHANGES_REQUESTED",
        "invoiced": False, "tier": None, "unit_price": None,
    }


def test_g3_question_as_dict_round_trips() -> None:
    question = G3Question(
        id="q_1", workbook_id="wb_1", question="Why was this visual redesigned?",
        asked_by="user:owner@client.example", asked_at="2027-01-14T00:00:00.000Z",
    )
    assert question.as_dict() == {
        "id": "q_1", "workbook_id": "wb_1", "question": "Why was this visual redesigned?",
        "asked_by": "user:owner@client.example", "asked_at": "2027-01-14T00:00:00.000Z",
    }
