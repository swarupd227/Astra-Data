"""Parity case derivation's own pure logic -- story S7.2.1, spec §10.1.

    "Cases = sheet x (parameter combinations from charter enumeration strategy) x
    (filter contexts: default, and each categorical filter's top-N values); each case
    has grain, measures, filters, parameter values and a stable id."

`derive_filter_contexts`, `derive_parameter_combinations`, `derive_sheet_cases` and
`compute_case_key` are pure and testable without a database -- the graph reads
(`_worksheet_field_index`, `_worksheet_filters`, the orchestration that writes/retires
`ParityCase` nodes and the suite record) are graph-coupled and covered by the
integration suite instead.
"""

from __future__ import annotations

from astra_graph.case_derivation import (
    MAX_FILTER_VALUES_PER_FILTER,
    compute_case_key,
    derive_filter_contexts,
    derive_parameter_combinations,
    derive_sheet_cases,
)
from astra_graph.tolerance_charter import ParamRule, ToleranceCharter

# --------------------------------------------------------------------- filter contexts


def test_no_filters_produces_only_the_default_context() -> None:
    contexts = derive_filter_contexts([])
    assert contexts == [{"kind": "default", "filters": []}]


def test_a_categorical_filter_adds_one_context_per_member() -> None:
    filters = [{"field_ref": "Region", "type": "categorical", "values": {"members": ["EMEA", "APAC"]}}]
    contexts = derive_filter_contexts(filters)
    assert contexts[0] == {"kind": "default", "filters": filters}
    assert {"kind": "categorical_value", "field_ref": "Region", "value": "EMEA", "filters": filters} in contexts
    assert {"kind": "categorical_value", "field_ref": "Region", "value": "APAC", "filters": filters} in contexts
    assert len(contexts) == 3


def test_non_categorical_filters_add_no_extra_context() -> None:
    filters = [{"field_ref": "Date", "type": "relative_date", "values": {"anchor": "today"}}]
    contexts = derive_filter_contexts(filters)
    assert contexts == [{"kind": "default", "filters": filters}]


def test_categorical_filter_members_are_capped_at_the_invented_bound() -> None:
    members = [f"member-{i}" for i in range(MAX_FILTER_VALUES_PER_FILTER + 10)]
    filters = [{"field_ref": "Desk", "type": "categorical", "values": {"members": members}}]
    contexts = derive_filter_contexts(filters)
    # one default + at most the bound's worth of member variants
    assert len(contexts) == 1 + MAX_FILTER_VALUES_PER_FILTER


def test_a_categorical_filter_with_no_members_adds_nothing() -> None:
    filters = [{"field_ref": "Region", "type": "categorical", "values": {"members": []}}]
    assert derive_filter_contexts(filters) == [{"kind": "default", "filters": filters}]


def test_multiple_categorical_filters_add_additively_not_as_a_cross_product() -> None:
    filters = [
        {"field_ref": "Region", "type": "categorical", "values": {"members": ["EMEA", "APAC"]}},
        {"field_ref": "Desk", "type": "categorical", "values": {"members": ["FX"]}},
    ]
    contexts = derive_filter_contexts(filters)
    # default + 2 Region variants + 1 Desk variant = 4, not 2*1 cross-multiplied with default
    assert len(contexts) == 4


# ---------------------------------------------------------------- parameter combinations


def test_no_parameters_produces_one_empty_combination() -> None:
    assert derive_parameter_combinations([]) == [{}]


def test_default_and_observed_values_are_combined() -> None:
    parameters = [{"name": "Growth Rate", "default": "0.05", "current_values_seen": ["0.05", "0.10"]}]
    combinations = derive_parameter_combinations(parameters)
    assert {"Growth Rate": "0.05"} in combinations
    assert {"Growth Rate": "0.10"} in combinations
    assert len(combinations) == 2  # deduplicated, default not repeated


def test_multiple_parameters_produce_the_full_cross_product() -> None:
    parameters = [
        {"name": "A", "default": "a1", "current_values_seen": ["a1", "a2"]},
        {"name": "B", "default": "b1", "current_values_seen": ["b1", "b2", "b3"]},
    ]
    combinations = derive_parameter_combinations(parameters)
    assert len(combinations) == 2 * 3
    assert {"A": "a1", "B": "b1"} in combinations
    assert {"A": "a2", "B": "b3"} in combinations


def test_a_parameter_with_no_default_or_observed_values_still_yields_one_combination() -> None:
    parameters = [{"name": "Unset", "default": None, "current_values_seen": []}]
    combinations = derive_parameter_combinations(parameters)
    assert combinations == [{"Unset": None}]


# --------------------------------------------------------------------------- case_key


def test_case_key_is_stable_for_identical_inputs() -> None:
    key1 = compute_case_key(
        sheet_ref="ws1", grain=("Desk",), measures=("Margin",),
        filter_ctx={"kind": "default", "filters": []}, param_values={},
    )
    key2 = compute_case_key(
        sheet_ref="ws1", grain=("Desk",), measures=("Margin",),
        filter_ctx={"kind": "default", "filters": []}, param_values={},
    )
    assert key1 == key2
    assert key1.startswith("sha256:")


def test_case_key_differs_when_any_input_differs() -> None:
    base = {
        "sheet_ref": "ws1", "grain": ("Desk",), "measures": ("Margin",),
        "filter_ctx": {"kind": "default", "filters": []}, "param_values": {},
    }
    key = compute_case_key(**base)
    assert compute_case_key(**{**base, "sheet_ref": "ws2"}) != key
    assert compute_case_key(**{**base, "param_values": {"a": "1"}}) != key
    assert compute_case_key(**{**base, "filter_ctx": {"kind": "default", "filters": [{}]}}) != key


def test_case_key_is_order_independent_for_grain_and_measures() -> None:
    key1 = compute_case_key(
        sheet_ref="ws1", grain=("Desk", "Date"), measures=("Margin",),
        filter_ctx={}, param_values={},
    )
    key2 = compute_case_key(
        sheet_ref="ws1", grain=("Date", "Desk"), measures=("Margin",),
        filter_ctx={}, param_values={},
    )
    assert key1 == key2


# ---------------------------------------------------------------------- sheet derivation


def _charter(max_values: int) -> ToleranceCharter:
    return ToleranceCharter(params=ParamRule(enumerate_max_values=max_values, enumerate_strategy="DEFAULT_PLUS_OBSERVED"))


def test_derive_sheet_cases_produces_one_case_per_candidate_within_the_bound() -> None:
    derivation = derive_sheet_cases(
        sheet_ref="ws1", grain=("Desk",), measures=("Margin",),
        filters=[], parameters=[{"name": "A", "default": "a1", "current_values_seen": ["a1", "a2"]}],
        charter=_charter(max_values=12),
    )
    assert derivation.total_candidates == 2  # 1 filter context x 2 param combos
    assert len(derivation.cases) == 2
    assert derivation.not_enumerated == ()


def test_derive_sheet_cases_caps_at_the_charter_bound_and_records_the_rest() -> None:
    parameters = [
        {"name": "A", "default": "a1", "current_values_seen": ["a1", "a2", "a3", "a4"]},
        {"name": "B", "default": "b1", "current_values_seen": ["b1", "b2", "b3"]},
        {"name": "C", "default": "c1", "current_values_seen": ["c1", "c2"]},
    ]
    derivation = derive_sheet_cases(
        sheet_ref="ws1", grain=("Desk",), measures=("Margin",),
        filters=[], parameters=parameters, charter=_charter(max_values=12),
    )
    assert derivation.total_candidates == 24  # the spec's own worked example
    assert len(derivation.cases) == 12
    assert len(derivation.not_enumerated) == 12


def test_derive_sheet_cases_prioritises_the_default_combination_first() -> None:
    parameters = [{"name": "A", "default": "a1", "current_values_seen": ["a1", "a2", "a3"]}]
    derivation = derive_sheet_cases(
        sheet_ref="ws1", grain=("Desk",), measures=("Margin",),
        filters=[], parameters=parameters, charter=_charter(max_values=1),
    )
    assert len(derivation.cases) == 1
    assert derivation.cases[0].param_values == {"A": "a1"}


def test_derive_sheet_cases_every_case_carries_its_own_stable_key() -> None:
    derivation = derive_sheet_cases(
        sheet_ref="ws1", grain=("Desk",), measures=("Margin",),
        filters=[], parameters=[], charter=_charter(max_values=12),
    )
    assert len(derivation.cases) == 1
    case = derivation.cases[0]
    assert case.case_key.startswith("sha256:")
    properties = case.as_properties(mu_ref="wb1")
    assert properties["case_key"] == case.case_key
    assert properties["mu_ref"] == "wb1"
    assert properties["state"] == "DERIVED"


def test_derive_sheet_cases_combines_filter_contexts_and_parameter_combinations() -> None:
    filters = [{"field_ref": "Region", "type": "categorical", "values": {"members": ["EMEA", "APAC"]}}]
    parameters = [{"name": "A", "default": "a1", "current_values_seen": ["a1", "a2"]}]
    derivation = derive_sheet_cases(
        sheet_ref="ws1", grain=("Desk",), measures=("Margin",),
        filters=filters, parameters=parameters, charter=_charter(max_values=100),
    )
    # 3 filter contexts (default + 2 members) x 2 param combos = 6
    assert derivation.total_candidates == 6
    assert len(derivation.cases) == 6
    keys = {case.case_key for case in derivation.cases}
    assert len(keys) == 6  # every case is genuinely distinct
