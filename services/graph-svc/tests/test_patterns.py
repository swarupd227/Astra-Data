"""The Pattern Library — specification §4.3/§9.3, story S5.5.1.

Pure logic only: template substitution in both directions, and the promotion arithmetic.
Everything that touches the graph or `pattern_observation` (generalising a real proof,
promotion eligibility against real history, deterministic application) is
`test_integration_patterns.py`'s own -- the same split `test_classify.py`/
`test_integration_classify.py` already established.
"""

from __future__ import annotations

from astra_graph.patterns import PromotionStatus, _abstract_template, render_target


def _op(name: str, *children: dict[str, object]) -> dict[str, object]:
    return {"kind": "OPERATOR", "name": name, "value": None, "children": list(children), "detail": []}


def _ref(name: str) -> dict[str, object]:
    return {"kind": "REFERENCE", "name": name, "value": None, "children": [], "detail": []}


# ------------------------------------------------------------------------------ render_target


def test_render_target_substitutes_every_capture() -> None:
    dax = render_target("DIVIDE(SUM({a}), SUM({b}))", {"a": "Notional", "b": "Margin"})
    assert dax == "DIVIDE(SUM([Notional]), SUM([Margin]))"


def test_render_target_leaves_the_table_placeholder_untouched() -> None:
    """§4.3's own worked example ships `{table}` as an unresolved model-context token --
    it names no capture of any AST, so a renderer must never guess at it."""
    dax = render_target("CALCULATE(SUM({a}), ALLEXCEPT({table}, {dims}))", {"a": "Notional"})
    assert dax == "CALCULATE(SUM([Notional]), ALLEXCEPT({table}, {dims}))"


def test_render_target_with_no_captures_is_a_no_op() -> None:
    assert render_target("SUM([Notional])", {}) == "SUM([Notional])"


# --------------------------------------------------------------------------- _abstract_template


def test_abstract_template_is_the_reverse_of_render_target() -> None:
    captures = {"a": "Notional", "b": "Margin"}
    dax = "DIVIDE(SUM([Notional]), SUM([Margin]))"
    template = _abstract_template(dax, captures)
    assert template == "DIVIDE(SUM({a}), SUM({b}))"
    assert render_target(template, captures) == dax


def test_abstract_template_substitutes_longest_identifiers_first() -> None:
    """`Notional` is a substring of `NotionalTotal` -- abstracting the shorter name first
    would corrupt the longer one's own bracketed occurrence."""
    captures = {"a": "NotionalTotal", "b": "Notional"}
    dax = "([NotionalTotal] - [Notional])"
    template = _abstract_template(dax, captures)
    assert template == "({a} - {b})"


def test_abstract_template_leaves_a_literal_table_name_the_model_wrote_alone() -> None:
    """What this cannot do, honestly: an identifier the model's own DAX never referenced
    in the platform's `[Name]` bracket form stays literal text, not a placeholder."""
    dax = 'CALCULATE(SUM([Notional]), FactTrades[Desk] = "EQ")'
    template = _abstract_template(dax, {"a": "Notional"})
    assert template == 'CALCULATE(SUM({a}), FactTrades[Desk] = "EQ")'


# -------------------------------------------------------------------------- PromotionStatus


def test_promotion_status_as_dict_round_trips_every_field() -> None:
    status = PromotionStatus(
        pattern_id="pat_1", promotion_state="CANDIDATE", distinct_passing_calcs=3,
        has_failure=False, threshold=5, eligible=False, reason="only 3 of 5 required distinct proof passes",
    )
    assert status.as_dict() == {
        "pattern_id": "pat_1",
        "promotion_state": "CANDIDATE",
        "distinct_passing_calcs": 3,
        "has_failure": False,
        "threshold": 5,
        "eligible": False,
        "reason": "only 3 of 5 required distinct proof passes",
    }
