"""AST shapes — specification §4.3, and the real-wire-shape fix story S5.5.1 needed.

`ast_shape()` had no dedicated test file before this story: every existing caller
(`lineage.calc_shapes`, `generation._matching_patterns`, `context.assembler._patterns`)
was only ever exercised indirectly, against real `formula_ast` values, and none of their
own tests happened to catch that the generic walk could not tell the real adapter-sdk wire
shape's `kind`/`name` apart from any other string field — confirmed by direct execution
before this fix, not assumed. These tests pin the fix and the generic fallback both.
"""

from __future__ import annotations

from astra_graph.context.signature import (
    SignatureError,
    ast_shape,
    capture_identifiers,
    matches,
    signature_of,
)


def _ref(name: str) -> dict[str, object]:
    return {"kind": "REFERENCE", "name": name, "value": None, "children": [], "detail": []}


def _lit(value: object, kind: str = "integer") -> dict[str, object]:
    return {"kind": "LITERAL", "name": kind, "value": value, "children": [], "detail": []}


def _op(name: str, *children: dict[str, object]) -> dict[str, object]:
    return {"kind": "OPERATOR", "name": name, "value": None, "children": list(children), "detail": []}


def _aggregate(name: str, *children: dict[str, object]) -> dict[str, object]:
    return {"kind": "AGGREGATE", "name": name, "value": None, "children": list(children), "detail": [["family", "aggregate"]]}


def _fn(name: str, family: str, *children: dict[str, object]) -> dict[str, object]:
    return {"kind": "FUNCTION", "name": name, "value": None, "children": list(children), "detail": [["family", family]]}


def _window(name: str, family: str, *children: dict[str, object]) -> dict[str, object]:
    return {
        "kind": "WINDOW", "name": name, "value": None, "children": list(children),
        "detail": [["family", family], ["addressing", "unresolved"]],
    }


def test_two_different_operators_over_the_same_fields_are_different_shapes() -> None:
    division = _op("/", _aggregate("SUM", _ref("Notional")), _aggregate("SUM", _ref("Margin")))
    addition = _op("+", _aggregate("SUM", _ref("Notional")), _aggregate("SUM", _ref("Margin")))
    assert ast_shape(division) != ast_shape(addition)


def test_the_same_operator_over_different_fields_is_the_same_shape() -> None:
    """The whole point of a shape: which *field* is summed does not change the template."""
    a = _op("/", _aggregate("SUM", _ref("Notional")), _aggregate("SUM", _ref("Margin")))
    b = _op("/", _aggregate("SUM", _ref("Revenue")), _aggregate("SUM", _ref("Cost")))
    assert ast_shape(a) == ast_shape(b)


def test_a_repeated_identifier_is_a_different_shape_from_two_distinct_ones() -> None:
    dup = _op("/", _ref("a"), _ref("a"))
    distinct = _op("/", _ref("a"), _ref("b"))
    assert ast_shape(dup) != ast_shape(distinct)


def test_detail_never_affects_the_shape() -> None:
    """§9.1's own family tag is classifier metadata, not structure -- two calls to the same
    function must look identical whether or not one happened to resolve a fact the other
    did not (e.g. table-calc addressing)."""
    resolved = _window("RANK", "table_calc_complex", _aggregate("SUM", _ref("Notional")))
    unresolved = {**resolved, "detail": [["family", "table_calc_complex"], ["addressing", "resolved"]]}
    assert ast_shape(resolved) == ast_shape(unresolved)


def test_literal_values_never_affect_the_shape() -> None:
    a = _op(">", _ref("Notional"), _lit(0))
    b = _op(">", _ref("Notional"), _lit(1_000_000))
    assert ast_shape(a) == ast_shape(b)


def test_shape_matches_the_specs_own_worked_example_format() -> None:
    lod = _aggregate("FIXED", _aggregate("SUM", _ref("b")))
    division = _op("/", _aggregate("SUM", _ref("a")), lod)
    assert ast_shape(division) == "/(SUM(a), FIXED(SUM(b)))"


def test_cast_and_conditional_and_unknown_all_render_without_raising() -> None:
    cast = {"kind": "CAST", "name": "INT", "value": None, "children": [_ref("Code")], "detail": []}
    conditional = {
        "kind": "CONDITIONAL", "name": "IF", "value": None,
        "children": [_op(">", _ref("Notional"), _lit(0)), _lit("y", "string")], "detail": [],
    }
    unknown = {"kind": "UNKNOWN", "name": "", "value": "garbled(", "children": [], "detail": []}
    assert ast_shape(cast) == "INT(a)"
    assert "IF(" in ast_shape(conditional)
    assert ast_shape(unknown) == "<unknown_construct>"


def test_no_ast_at_all_and_none_still_raise_or_render_a_null() -> None:
    assert ast_shape(None) == "<null>"
    assert ast_shape(42) == "<num>"


def test_a_calculation_nested_past_max_depth_raises() -> None:
    node: dict[str, object] = _ref("leaf")
    for _ in range(70):
        node = _op("+", node, _lit(1))
    try:
        ast_shape(node)
    except SignatureError:
        return
    raise AssertionError("expected SignatureError for an AST nested past MAX_DEPTH")


# ------------------------------------------------------------------------- capture_identifiers


def test_capture_identifiers_names_captures_in_first_appearance_order() -> None:
    ast = _op("/", _aggregate("SUM", _ref("Notional")), _aggregate("SUM", _ref("Margin")))
    assert capture_identifiers(ast) == {"a": "Notional", "b": "Margin"}


def test_capture_identifiers_gives_a_repeated_identifier_one_placeholder() -> None:
    ast = _op("/", _ref("Notional"), _ref("Notional"))
    assert capture_identifiers(ast) == {"a": "Notional"}


# ---------------------------------------------------------------------------- signature_of / matches


def test_signature_of_carries_the_adapter_when_given_one() -> None:
    ast = _ref("Notional")
    assert signature_of(ast, adapter="tableau") == {"ast_shape": "a", "adapter": "tableau"}
    assert signature_of(ast) == {"ast_shape": "a"}


def test_matches_requires_shape_equality() -> None:
    signature = signature_of(_op("/", _ref("a"), _ref("b")), adapter="tableau")
    same_shape = ast_shape(_op("/", _ref("x"), _ref("y")))
    different_shape = ast_shape(_op("+", _ref("x"), _ref("y")))
    assert matches(signature, shape=same_shape, adapter="tableau")
    assert not matches(signature, shape=different_shape, adapter="tableau")


def test_matches_is_adapter_agnostic_only_when_neither_side_declares_one() -> None:
    signature = signature_of(_ref("a"), adapter="tableau")
    shape = ast_shape(_ref("x"))
    assert matches(signature, shape=shape, adapter="tableau")
    assert not matches(signature, shape=shape, adapter="powerbi_desktop")
    assert matches(signature_of(_ref("a")), shape=shape, adapter="tableau")


def test_matches_accepts_a_json_encoded_signature_string() -> None:
    import json

    signature = signature_of(_ref("a"), adapter="tableau")
    assert matches(json.dumps(signature), shape=ast_shape(_ref("x")), adapter="tableau")


def test_matches_rejects_a_non_signature_value() -> None:
    assert not matches(None, shape="a", adapter="tableau")
    assert not matches("not json", shape="a", adapter="tableau")


# ------------------------------------------------------------- the generic (pre-wire) fallback


def test_the_generic_key_walk_still_works_unchanged_for_a_non_wire_ast() -> None:
    """`test_integration_cartographer.py`'s own fixture predates the real Tableau grammar
    and still uses this shape -- the fix must not disturb it."""
    generic = {
        "op": "DIV",
        "args": [{"fn": "SUM", "arg": {"field": "Margin"}}, {"fn": "SUM", "arg": {"field": "Revenue"}}],
    }
    same_fields_different_op = {
        "op": "MUL",
        "args": [{"fn": "SUM", "arg": {"field": "Margin"}}, {"fn": "SUM", "arg": {"field": "Revenue"}}],
    }
    assert ast_shape(generic) == "DIV(SUM(a), SUM(b))"
    assert ast_shape(generic) != ast_shape(same_fields_different_op)
