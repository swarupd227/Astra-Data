"""C4 redesign — Appendix B guidance, the redesign decision itself, and the provenance
idempotency `c4_properties` promises — story S5.4.1.

No database: `redesign.py` is pure logic plus an in-memory provenance write, the same
footing `test_classify.py` (S5.1.1) already established for the classifier it feeds.
"""

from __future__ import annotations

import pytest

from astra_graph.classify import ClassificationContext, classify
from astra_graph.principal import Principal
from astra_graph.provenance import InMemoryProvenanceStore
from astra_graph.redesign import (
    APPENDIX_B_GUIDANCE,
    REDESIGN_DECISIONS,
    RedesignDecisionError,
    c4_properties,
    validate_decision,
)

PRINCIPAL = Principal("user:migration@artizent.example")


def _lit(value: object = 1) -> dict[str, object]:
    return {"kind": "LITERAL", "name": "integer", "value": value, "children": [], "detail": []}


def _ref(name: str) -> dict[str, object]:
    return {"kind": "REFERENCE", "name": name, "value": None, "children": [], "detail": []}


def _fn(name: str, family: str, *children: dict[str, object], recognised: bool = True) -> dict[str, object]:
    detail = [["family", family]]
    if not recognised:
        detail.append(["recognised", "false"])
    return {"kind": "FUNCTION", "name": name, "value": None, "children": list(children), "detail": detail}


def _window(name: str, family: str, *children: dict[str, object]) -> dict[str, object]:
    return {
        "kind": "WINDOW", "name": name, "value": None, "children": list(children),
        "detail": [["family", family], ["addressing", "unresolved"]],
    }


# ---------------------------------------------------------- every C4 rule_id has guidance


_C4_ASTS: dict[str, object] = {
    "b1:no_ast": "not a dict",
    "b1:unrecognised_construct": {"kind": "UNKNOWN", "name": "", "value": None, "children": [], "detail": []},
    "b1:table_calc_complex_unresolved": _window("RANK", "table_calc_complex"),
    "b1:unrecognised_function": _fn("MADE_UP", "unknown", recognised=False),
    "b1:regexp": _fn("REGEXP_MATCH", "string"),
    "b1:unmapped_family": _fn("SOMETHING", "no_such_family"),
    "b1:rawsql": _fn("RAWSQL_INT", "rawsql", _lit("select 1")),
    "b1:unknown": _fn("MYSTERY", "unknown"),
}


@pytest.mark.parametrize("rule_id", sorted(_C4_ASTS))
def test_every_c4_rule_id_classify_can_emit_has_appendix_b_guidance(rule_id: str) -> None:
    """A drift-detection guard between `classify.py` and `redesign.py`: a rule_id that can
    reach C4 but has no entry here would otherwise only fail at `c4_properties` call time,
    against a live estate, which is a far worse place to discover it."""
    result = classify(_C4_ASTS[rule_id], context=ClassificationContext())
    assert result.class_ == "C4"
    assert result.rule_id == rule_id
    assert rule_id in APPENDIX_B_GUIDANCE


def test_appendix_b_guidance_has_no_entries_classify_can_never_produce() -> None:
    # The inverse direction: every guidance entry earns its keep by matching a real rule_id
    # this test file itself proves `classify()` can emit.
    assert set(APPENDIX_B_GUIDANCE) == set(_C4_ASTS)


# --------------------------------------------------------------------------- validate_decision


@pytest.mark.parametrize("decision", REDESIGN_DECISIONS)
def test_validate_decision_accepts_every_ac_named_outcome(decision: str) -> None:
    validate_decision(decision, reason="a real rationale")


def test_validate_decision_rejects_an_outcome_the_ac_never_named() -> None:
    with pytest.raises(RedesignDecisionError):
        validate_decision("SKIP", reason="a real rationale")


@pytest.mark.parametrize("reason", ["", "   "])
def test_validate_decision_rejects_an_empty_reason(reason: str) -> None:
    # DROP specifically needs the report-owner agreement recorded here — enforced as "any
    # decision needs a real reason", since this platform has no separate co-sign workflow.
    with pytest.raises(RedesignDecisionError):
        validate_decision("DROP", reason=reason)


# --------------------------------------------------------------------------- c4_properties


async def test_c4_properties_writes_guidance_suggestion_and_a_real_provenance_record() -> None:
    store = InMemoryProvenanceStore()
    properties = await c4_properties(
        store, calc_id="calc_1", rule_id="b1:rawsql", existing={}, graph_version=1, principal=PRINCIPAL,
    )
    guidance = APPENDIX_B_GUIDANCE["b1:rawsql"]
    assert properties["appendix_b_guidance"] == guidance.appendix_b_guidance
    assert properties["redesign_suggestion"] == guidance.suggestion
    ref = properties["redesign_suggestion_provenance_ref"]
    record = await store.get(ref)
    assert record is not None
    assert record.mode.value == "ASSISTED"
    assert record.subject_id == "calc_1"


async def test_c4_properties_is_idempotent_for_an_unchanged_rule_id() -> None:
    store = InMemoryProvenanceStore()
    first = await c4_properties(
        store, calc_id="calc_1", rule_id="b1:rawsql", existing={}, graph_version=1, principal=PRINCIPAL,
    )
    existing = {"pattern_ref": "b1:rawsql", **first}
    second = await c4_properties(
        store, calc_id="calc_1", rule_id="b1:rawsql", existing=existing, graph_version=2, principal=PRINCIPAL,
    )
    assert second["redesign_suggestion_provenance_ref"] == first["redesign_suggestion_provenance_ref"]
    assert len(store.records) == 1


async def test_c4_properties_writes_a_fresh_record_when_the_rule_id_changes() -> None:
    store = InMemoryProvenanceStore()
    first = await c4_properties(
        store, calc_id="calc_1", rule_id="b1:rawsql", existing={}, graph_version=1, principal=PRINCIPAL,
    )
    existing = {"pattern_ref": "b1:rawsql", **first}
    second = await c4_properties(
        store, calc_id="calc_1", rule_id="b1:unknown", existing=existing, graph_version=2, principal=PRINCIPAL,
    )
    assert second["redesign_suggestion_provenance_ref"] != first["redesign_suggestion_provenance_ref"]
    assert len(store.records) == 2


async def test_c4_properties_carries_through_an_already_recorded_decision_untouched() -> None:
    store = InMemoryProvenanceStore()
    existing = {
        "redesign_decision": "DROP",
        "redesign_decision_reason": "report owner agreed",
        "redesign_decision_by": PRINCIPAL.value,
        "redesign_decision_at": "2026-01-01T00:00:00.000Z",
    }
    properties = await c4_properties(
        store, calc_id="calc_1", rule_id="b1:rawsql", existing=existing, graph_version=1, principal=PRINCIPAL,
    )
    assert properties["redesign_decision"] == "DROP"
    assert properties["redesign_decision_reason"] == "report owner agreed"


async def test_c4_properties_raises_on_a_rule_id_it_has_no_guidance_for() -> None:
    # A real drift guard: this only fires if classify.py starts emitting a C4 rule_id that
    # redesign.py has not been taught about.
    store = InMemoryProvenanceStore()
    with pytest.raises(RedesignDecisionError):
        await c4_properties(
            store, calc_id="calc_1", rule_id="b1:not_a_real_rule", existing={}, graph_version=1,
            principal=PRINCIPAL,
        )
