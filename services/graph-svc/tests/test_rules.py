"""The deterministic rules engine — story S5.2.1.

    "Rules are AST-pattern -> target-template with guards ... Each rule ships with at
    least three golden-corpus cases that must pass proof in CI."

Every rule's own golden cases are checked here, in CI, against two things: exact-match
rendered DAX text, and a structural DAX sanity check standing in for a real parser (see
`rules.py`'s own module docstring for why — no live DAX engine or Arbiter exists yet). This
is not spec §16.1's rung-4 "Proof"; it is the honest floor this platform can verify today.
"""

from __future__ import annotations

import pytest

from astra_graph.rules import (
    KNOWN_DAX_FUNCTIONS,
    RULES,
    ChangedArtefact,
    RegressedArtefact,
    RegressionReport,
    dax_sanity_check,
    render_calc,
)

# ---------------------------------------------------------------------- every rule ships >=3


def test_every_rule_ships_at_least_three_golden_cases() -> None:
    for rule in RULES:
        assert len(rule.golden_cases) >= 3, f"{rule.id} ships only {len(rule.golden_cases)}"


def test_every_rule_id_is_unique() -> None:
    ids = [rule.id for rule in RULES]
    assert len(ids) == len(set(ids))


def test_every_rule_declares_at_least_one_family_and_class() -> None:
    for rule in RULES:
        assert rule.family
        assert rule.class_ in {"C1", "C2"}


# --------------------------------------------------------------- the golden corpus, in CI

_GOLDEN_CASES = [(rule.id, case.name, case.ast, case.expected_dax) for rule in RULES for case in rule.golden_cases]


@pytest.mark.parametrize("rule_id,case_name,ast,expected", _GOLDEN_CASES, ids=[f"{r}:{n}" for r, n, _, _ in _GOLDEN_CASES])
def test_golden_case_renders_to_the_expected_dax(rule_id: str, case_name: str, ast: object, expected: str) -> None:
    outcome = render_calc(ast)
    assert outcome.ok, f"{rule_id}:{case_name} did not render: {outcome.reason}"
    assert outcome.dax == expected


@pytest.mark.parametrize("rule_id,case_name,ast,expected", _GOLDEN_CASES, ids=[f"{r}:{n}" for r, n, _, _ in _GOLDEN_CASES])
def test_golden_case_passes_the_dax_sanity_check(rule_id: str, case_name: str, ast: object, expected: str) -> None:
    """Standing in for validation-ladder rung 2 ("parses under the target grammar") — see
    `rules.py`'s own module docstring for why this is a structural check, not a real parse."""
    problem = dax_sanity_check(expected)
    assert problem is None, f"{rule_id}:{case_name}: {problem}"


def test_golden_case_reports_the_rule_that_produced_it() -> None:
    for rule in RULES:
        for case in rule.golden_cases:
            outcome = render_calc(case.ast)
            assert outcome.rule_id == rule.id, (
                f"{rule.id}:{case.name} was actually rendered by {outcome.rule_id} "
                "(a more specific rule matched first — expected for a nested golden case, "
                "otherwise the golden case belongs under the rule that actually fires)"
            )
            assert outcome.rule_version == rule.version


# --------------------------------------------------------------------------- dax_sanity_check


def test_balanced_dax_passes() -> None:
    assert dax_sanity_check("SUM([Notional])") is None
    assert dax_sanity_check("CALCULATE(SUM([Notional]), ALLEXCEPT({table}, [Desk]))") is None


def test_unbalanced_parens_fail() -> None:
    assert dax_sanity_check("SUM([Notional]") is not None
    assert dax_sanity_check("SUM[Notional])") is not None


def test_an_unknown_function_name_fails() -> None:
    assert dax_sanity_check("MADEUPFUNC([Notional])") is not None


def test_every_known_dax_function_is_a_real_word() -> None:
    # DAX itself uses dotted names for some functions (e.g. PERCENTILE.INC), so a dot is
    # allowed alongside the usual identifier characters.
    for name in KNOWN_DAX_FUNCTIONS:
        assert name.replace("_", "").replace(".", "").isalnum()


# ------------------------------------------------------------------------ composition / C3


def _ref(name: str) -> dict[str, object]:
    return {"kind": "REFERENCE", "name": name, "value": None, "children": [], "detail": []}


def _op(name: str, *kids: dict[str, object]) -> dict[str, object]:
    return {"kind": "OPERATOR", "name": name, "value": None, "children": list(kids), "detail": []}


def _aggregate(name: str, *kids: dict[str, object]) -> dict[str, object]:
    return {"kind": "AGGREGATE", "name": name, "value": None, "children": list(kids), "detail": [["family", "aggregate"]]}


def _fn(name: str, family: str, *kids: dict[str, object]) -> dict[str, object]:
    return {"kind": "FUNCTION", "name": name, "value": None, "children": list(kids), "detail": [["family", family]]}


def test_a_composed_expression_renders_through_every_level() -> None:
    # DIV(SUM(a), SUM(b)) -- a plain C1 composition of an operator over two aggregates.
    ast = _op("/", _aggregate("SUM", _ref("Margin")), _aggregate("SUM", _ref("Revenue")))
    outcome = render_calc(ast)
    assert outcome.ok
    assert outcome.dax == "(SUM([Margin]) / SUM([Revenue]))"
    assert outcome.rule_id == "c1_operator"


def test_a_null_idiom_nested_inside_an_operator_still_renders() -> None:
    ast = _op("+", _fn("ZN", "logical", _ref("Notional")), _ref("Fee"))
    outcome = render_calc(ast)
    assert outcome.ok
    assert outcome.dax == "(COALESCE([Notional], 0) + [Fee])"


def test_an_unrecognised_function_does_not_render() -> None:
    ast = _fn("RAWSQL_INT", "rawsql", {"kind": "LITERAL", "name": "string", "value": "select 1", "children": [], "detail": []})
    outcome = render_calc(ast)
    assert not outcome.ok
    assert outcome.dax is None
    assert outcome.rule_id is None


def test_an_unknown_construct_does_not_render() -> None:
    ast = {"kind": "UNKNOWN", "name": "", "value": "::weird::", "children": [], "detail": []}
    outcome = render_calc(ast)
    assert not outcome.ok


def test_no_ast_at_all_does_not_render() -> None:
    outcome = render_calc(None)
    assert not outcome.ok


def test_determinism_the_same_ast_always_renders_the_same_way() -> None:
    ast = _aggregate("SUM", _ref("Notional"))
    first = render_calc(ast)
    second = render_calc(ast)
    assert (first.ok, first.dax, first.rule_id) == (second.ok, second.dax, second.rule_id)


# ---------------------------------------------------------------- regression report (S5.2.2)


def test_a_report_with_no_regressions_is_ok() -> None:
    report = RegressionReport(checked=3, unchanged=2, changed=(_changed(),), regressed=())
    assert report.ok is True


def test_a_report_with_any_regression_is_not_ok() -> None:
    report = RegressionReport(checked=3, unchanged=2, changed=(), regressed=(_regressed(),))
    assert report.ok is False


def test_as_dict_carries_every_field_of_a_regression() -> None:
    report = RegressionReport(checked=1, unchanged=0, changed=(), regressed=(_regressed(),))
    body = report.as_dict()
    assert body["ok"] is False
    assert body["regressed"] == [
        {
            "calculated_field_id": "calc_one",
            "measure_id": "msr_one",
            "rule_id": "c1_aggregate",
            "reason": "no shipped rule matches this calculation any longer",
        }
    ]


def test_as_dict_carries_every_field_of_a_change() -> None:
    report = RegressionReport(checked=1, unchanged=0, changed=(_changed(),), regressed=())
    body = report.as_dict()
    assert body["changed"] == [
        {
            "calculated_field_id": "calc_two",
            "measure_id": "msr_two",
            "previous_rule_id": "c1_aggregate",
            "previous_dax": "SUM ( [Notional] )",
            "current_rule_id": "c1_aggregate",
            "current_dax": "SUM([Notional])",
        }
    ]


def _regressed() -> RegressedArtefact:
    return RegressedArtefact(
        calculated_field_id="calc_one", measure_id="msr_one", rule_id="c1_aggregate",
        reason="no shipped rule matches this calculation any longer",
    )


def _changed() -> ChangedArtefact:
    return ChangedArtefact(
        calculated_field_id="calc_two", measure_id="msr_two",
        previous_rule_id="c1_aggregate", previous_dax="SUM ( [Notional] )",
        current_rule_id="c1_aggregate", current_dax="SUM([Notional])",
    )
