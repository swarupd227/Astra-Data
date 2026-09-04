"""Calculation classification — story S5.1.1.

    "Classifier is deterministic: C1 when a direct-map rule matches the whole AST; C2 when
    a structural rewrite rule matches; C3 when the AST is within grammar but no rule
    matches or context ... is required; C4 when a construct has no Power BI equivalent per
    Appendix B."

Every rule is a pure function of one AST (plus, where §9.1 itself says context is needed, a
`ClassificationContext`) — no database, the same footing `test_conformance_rules.py` (S4.3.2)
already established for the Modeller's own rule functions.

ASTs are built in the real wire shape (`astra_adapter.rpc.wire.encode_calc_node`'s own
output: ``kind``/``name``/``children``/``detail``) rather than a shorthand, since that shape
— not `context/signature.py`'s generic dict walk — is exactly what `formula_ast` holds on a
real `CalculatedField` node.
"""

from __future__ import annotations

from astra_graph.classify import ClassificationContext, classify


def _lit(value: object = 1) -> dict[str, object]:
    return {"kind": "LITERAL", "name": "integer", "value": value, "children": [], "detail": []}


def _ref(name: str) -> dict[str, object]:
    return {"kind": "REFERENCE", "name": name, "value": None, "children": [], "detail": []}


def _fn(name: str, family: str, *children: dict[str, object], recognised: bool = True) -> dict[str, object]:
    detail = [["family", family]]
    if not recognised:
        detail.append(["recognised", "false"])
    return {"kind": "FUNCTION", "name": name, "value": None, "children": list(children), "detail": detail}


def _op(name: str, *children: dict[str, object]) -> dict[str, object]:
    return {"kind": "OPERATOR", "name": name, "value": None, "children": list(children), "detail": []}


def _if(*children: dict[str, object]) -> dict[str, object]:
    return {"kind": "CONDITIONAL", "name": "IF", "value": None, "children": list(children), "detail": []}


def _cast(target: str, inner: dict[str, object]) -> dict[str, object]:
    return {"kind": "CAST", "name": target, "value": None, "children": [inner], "detail": []}


def _aggregate(name: str, *children: dict[str, object]) -> dict[str, object]:
    """A plain aggregate FUNCTION that the parser upgrades to AGGREGATE kind (e.g. SUM)."""
    return {"kind": "AGGREGATE", "name": name, "value": None, "children": list(children), "detail": [["family", "aggregate"]]}


def _lod(grain: str, measure: dict[str, object], *dims: dict[str, object]) -> dict[str, object]:
    return {
        "kind": "AGGREGATE",
        "name": grain,
        "value": None,
        "children": [measure, *dims],
        "detail": [["grain", grain]],
    }


def _window(name: str, family: str, *children: dict[str, object]) -> dict[str, object]:
    return {
        "kind": "WINDOW",
        "name": name,
        "value": None,
        "children": list(children),
        "detail": [["family", family], ["addressing", "unresolved"], ["partitioning", "unresolved"]],
    }


def _unknown(text: str) -> dict[str, object]:
    return {"kind": "UNKNOWN", "name": "", "value": text, "children": [], "detail": []}


RESOLVED = ClassificationContext(table_calc_addressing_resolved=True)
WITH_PARAMETER = ClassificationContext(has_parameter_dependency=True)


# --------------------------------------------------------------------------------------- C1


def test_a_sum_of_a_field_is_c1() -> None:
    result = classify(_aggregate("SUM", _ref("Notional")))
    assert result.class_ == "C1"
    assert result.rule_id == "b1:aggregate"


def test_arithmetic_operators_over_aggregates_are_c1() -> None:
    ast = _op("/", _aggregate("SUM", _ref("Notional")), _aggregate("SUM", _ref("Risk")))
    assert classify(ast).class_ == "C1"


def test_plain_if_is_c1() -> None:
    ast = _if(_op(">", _ref("Notional"), _lit(0)), _lit("positive"), _lit("non-positive"))
    result = classify(ast)
    assert result.class_ == "C1"
    assert result.rule_id == "b1:conditional"


def test_a_type_cast_is_c1() -> None:
    assert classify(_cast("INT", _ref("Code"))).class_ == "C1"


def test_a_string_function_is_c1() -> None:
    assert classify(_fn("UPPER", "string", _ref("Desk"))).class_ == "C1"


def test_a_date_type_cast_function_stays_c1_for_the_type_family() -> None:
    assert classify(_fn("INT", "type", _ref("Code"))).class_ == "C1"


def test_a_bare_literal_or_reference_is_c1() -> None:
    assert classify(_ref("Desk")).class_ == "C1"
    assert classify(_lit(42)).class_ == "C1"


# --------------------------------------------------------------------------------------- C2


def test_a_null_idiom_function_is_c2() -> None:
    result = classify(_fn("ZN", "logical", _ref("Notional")))
    assert result.class_ == "C2"
    assert result.rule_id == "b1:null_idiom"


def test_a_date_function_is_c2() -> None:
    result = classify(_fn("DATETRUNC", "date", _lit("month"), _ref("TradeDate")))
    assert result.class_ == "C2"
    assert result.rule_id == "b1:date"


def test_a_set_function_is_c2() -> None:
    assert classify(_fn("IN", "set", _ref("Desk"))).class_ == "C2"


def test_an_unnested_lod_expression_is_c2() -> None:
    ast = _lod("FIXED", _aggregate("SUM", _ref("Notional")), _ref("Desk"))
    result = classify(ast)
    assert result.class_ == "C2"
    assert result.rule_id == "b1:lod"


def test_a_table_calc_with_addressing_resolved_from_the_sheet_is_c2() -> None:
    ast = _window("RUNNING_SUM", "table_calc_simple", _aggregate("SUM", _ref("Notional")))
    result = classify(ast, context=RESOLVED)
    assert result.class_ == "C2"
    assert result.rule_id == "b1:table_calc_simple_resolved"


def test_a_parameter_dependency_floors_an_otherwise_c1_calculation_at_c2() -> None:
    result = classify(_op("+", _ref("Notional"), _lit(1)), context=WITH_PARAMETER)
    assert result.class_ == "C2"
    assert result.rule_id == "b1:parameter"


def test_a_parameter_dependency_does_not_downgrade_an_already_worse_class() -> None:
    ast = _op("+", _fn("RAWSQL_INT", "rawsql", _lit("select 1")), _lit(1))
    result = classify(ast, context=WITH_PARAMETER)
    assert result.class_ == "C4"
    assert result.rule_id == "b1:rawsql"


# --------------------------------------------------------------------------------------- C3


def test_a_table_calc_with_unresolved_addressing_is_c3() -> None:
    ast = _window("RUNNING_SUM", "table_calc_simple", _aggregate("SUM", _ref("Notional")))
    result = classify(ast)
    assert result.class_ == "C3"
    assert result.rule_id == "b1:table_calc_simple_unresolved"


def test_a_complex_table_calc_with_resolved_addressing_is_c3() -> None:
    ast = _window("RANK", "table_calc_complex", _aggregate("SUM", _ref("Notional")))
    result = classify(ast, context=RESOLVED)
    assert result.class_ == "C3"
    assert result.rule_id == "b1:table_calc_complex_resolved"


def test_a_nested_lod_expression_is_c3() -> None:
    inner = _lod("FIXED", _aggregate("SUM", _ref("Notional")), _ref("Desk"))
    outer = _lod("INCLUDE", inner, _ref("Region"))
    result = classify(outer)
    assert result.class_ == "C3"
    assert result.rule_id == "b1:nested_lod"


def test_attr_is_c3() -> None:
    result = classify(_fn("ATTR", "attr", _ref("Category")))
    assert result.class_ == "C3"
    assert result.rule_id == "b1:attr"


def test_a_user_context_function_is_c3() -> None:
    result = classify(_fn("ISMEMBEROF", "user", _lit("Managers")))
    assert result.class_ == "C3"
    assert result.rule_id == "b1:user"


# --------------------------------------------------------------------------------------- C4


def test_rawsql_is_c4_by_default() -> None:
    result = classify(_fn("RAWSQL_INT", "rawsql", _lit("select 1")))
    assert result.class_ == "C4"
    assert result.rule_id == "b1:rawsql"


def test_regexp_is_c4_with_no_m_pass_through_path() -> None:
    result = classify(_fn("REGEXP_MATCH", "string", _ref("Code"), _lit("^A")))
    assert result.class_ == "C4"
    assert result.rule_id == "b1:regexp"


def test_an_unrecognised_function_is_c4() -> None:
    result = classify(_fn("SOME_FUTURE_FUNCTION", "unknown", recognised=False))
    assert result.class_ == "C4"
    assert result.rule_id == "b1:unrecognised_function"


def test_an_unparseable_construct_is_c4() -> None:
    result = classify(_unknown("::weird::"))
    assert result.class_ == "C4"
    assert result.rule_id == "b1:unrecognised_construct"


def test_a_complex_table_calc_with_unresolved_addressing_is_c4() -> None:
    ast = _window("RANK", "table_calc_complex", _aggregate("SUM", _ref("Notional")))
    result = classify(ast)
    assert result.class_ == "C4"
    assert result.rule_id == "b1:table_calc_complex_unresolved"


def test_no_ast_at_all_is_c4() -> None:
    result = classify(None)
    assert result.class_ == "C4"
    assert result.rule_id == "b1:no_ast"


# ---------------------------------------------------------------------- worst-node-wins

def test_the_whole_calculation_takes_its_worst_nodes_class_not_its_roots() -> None:
    # DIV(SUM(a), RAWSQL_INT('select 1')) — the root operator is C1 on its own, but one
    # argument is C4, and §9.1's own C1 definition requires *every* node to qualify.
    ast = _op(
        "/",
        _aggregate("SUM", _ref("Notional")),
        _fn("RAWSQL_INT", "rawsql", _lit("select 1")),
    )
    result = classify(ast)
    assert result.class_ == "C4"
    assert result.rule_id == "b1:rawsql"


def test_determinism_the_same_ast_always_classifies_the_same_way() -> None:
    ast = _lod("FIXED", _aggregate("SUM", _ref("Notional")), _ref("Desk"))
    first = classify(ast)
    second = classify(ast)
    assert (first.class_, first.rule_id, first.reason) == (second.class_, second.rule_id, second.reason)
