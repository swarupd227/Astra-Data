"""Dual execution's own pure logic -- story S7.3.1, spec §10.2.

    "Target side: DAX EVALUATE over XMLA against the dev or test model, with filters
    and parameter values applied per §10.2; query text stored... Source side: adapter
    execute_case with the chosen strategy; strategy stored... Both ResultSets stored as
    Parquet in the artefact store with content hash."

`to_sdk_filters`/`to_sdk_parameters`/`build_dax_query`/`result_set_to_parquet` are pure
and testable without a database -- the graph reads (`_table_map_for_sheet`,
`_resolve_site`, the orchestration that calls both adapters and writes artefacts/
`ParityCase` properties) are graph-coupled and covered by the integration suite instead.
"""

from __future__ import annotations

import pyarrow.parquet as pq
from astra_adapter import Column, ColumnRole, ExecutionOutcome, ExecutionStrategy, ResultSet

from astra_graph.case_execution import (
    build_dax_query,
    result_set_to_parquet,
    to_sdk_filters,
    to_sdk_parameters,
)

# --------------------------------------------------------------------------- to_sdk_filters


def test_no_filters_produces_no_pairs() -> None:
    assert to_sdk_filters({"kind": "default", "filters": []}) == ()


def test_default_context_expands_categorical_members_to_repeated_pairs() -> None:
    filter_ctx = {
        "kind": "default",
        "filters": [{"field_ref": "Region", "type": "categorical", "values": {"members": ["EMEA", "APAC"]}}],
    }
    assert to_sdk_filters(filter_ctx) == (("Region", "EMEA"), ("Region", "APAC"))


def test_default_context_reads_a_best_effort_value_for_non_categorical_filters() -> None:
    filter_ctx = {
        "kind": "default",
        "filters": [{"field_ref": "Date", "type": "relative_date", "values": {"anchor": "today"}}],
    }
    assert to_sdk_filters(filter_ctx) == (("Date", "today"),)


def test_categorical_value_context_is_exactly_one_pair() -> None:
    filter_ctx = {"kind": "categorical_value", "field_ref": "Region", "value": "EMEA"}
    assert to_sdk_filters(filter_ctx) == (("Region", "EMEA"),)


def test_categorical_value_context_with_no_value_produces_nothing() -> None:
    assert to_sdk_filters({"kind": "categorical_value", "field_ref": "Region"}) == ()


# ------------------------------------------------------------------------ to_sdk_parameters


def test_parameter_values_become_flat_pairs() -> None:
    assert to_sdk_parameters({"Growth Rate": "0.05"}) == (("Growth Rate", "0.05"),)


def test_a_null_parameter_value_is_dropped() -> None:
    assert to_sdk_parameters({"Growth Rate": "0.05", "Unset": None}) == (("Growth Rate", "0.05"),)


def test_no_parameters_produces_no_pairs() -> None:
    assert to_sdk_parameters({}) == ()


# ----------------------------------------------------------------------------- build_dax_query


def test_dax_query_names_every_grain_and_measure() -> None:
    query = build_dax_query(
        grain=("Desk",), measures=("Margin",), sdk_filters=(), sdk_parameters=(), table_map={},
    )
    assert query.startswith("EVALUATE\nSUMMARIZECOLUMNS(")
    assert "'Desk'[Desk]," in query
    assert '"Margin", [Margin]' in query
    assert query.endswith("ORDER BY 'Desk'[Desk]")


def test_dax_query_uses_a_real_table_binding_when_one_is_given() -> None:
    query = build_dax_query(
        grain=("Desk",), measures=("Margin",), sdk_filters=(), sdk_parameters=(),
        table_map={"Desk": "Geography"},
    )
    assert "'Geography'[Desk]" in query


def test_dax_query_falls_back_to_the_field_name_as_its_own_table_when_unbound() -> None:
    query = build_dax_query(
        grain=("Desk",), measures=(), sdk_filters=(), sdk_parameters=(), table_map={},
    )
    assert "'Desk'[Desk]" in query


def test_a_single_valued_filter_becomes_treatas() -> None:
    query = build_dax_query(
        grain=(), measures=(), sdk_filters=(("Region", "EMEA"),), sdk_parameters=(), table_map={},
    )
    assert 'TREATAS({"EMEA"}, \'Region\'[Region])' in query


def test_a_multi_valued_filter_on_one_field_becomes_a_filter_in_clause() -> None:
    query = build_dax_query(
        grain=(), measures=(), sdk_filters=(("Region", "EMEA"), ("Region", "APAC")),
        sdk_parameters=(), table_map={},
    )
    assert "FILTER(ALL('Region'[Region]), 'Region'[Region] IN {\"EMEA\", \"APAC\"})" in query


def test_parameters_are_applied_the_same_way_as_single_valued_filters() -> None:
    query = build_dax_query(
        grain=(), measures=(), sdk_filters=(), sdk_parameters=(("Growth Rate", "0.05"),), table_map={},
    )
    assert 'TREATAS({"0.05"}, \'Growth Rate\'[Growth Rate])' in query


def test_no_grain_omits_the_order_by_clause() -> None:
    query = build_dax_query(grain=(), measures=("Margin",), sdk_filters=(), sdk_parameters=(), table_map={})
    assert "ORDER BY" not in query


def test_the_last_named_expression_has_no_trailing_comma() -> None:
    query = build_dax_query(grain=("Desk",), measures=("Margin",), sdk_filters=(), sdk_parameters=(), table_map={})
    body_lines = query.splitlines()
    closing_index = body_lines.index(")")
    assert not body_lines[closing_index - 1].rstrip().endswith(",")


# ------------------------------------------------------------------------ result_set_to_parquet


def _result_set(**overrides: object) -> ResultSet:
    defaults: dict[str, object] = {
        "case_id": "case_1",
        "columns": (Column("Desk", ColumnRole.DIMENSION, "string"), Column("Margin", ColumnRole.MEASURE, "double")),
        "rows": (("EMEA", 1.5), ("APAC", None)),
        "strategy": ExecutionStrategy.EXTRACT_READ,
        "interface_version": "1.1",
        "adapter_name": "fixture",
        "adapter_version": "0.1.0",
        "outcome": ExecutionOutcome.OK,
    }
    return ResultSet(**{**defaults, **overrides})


def test_parquet_bytes_round_trip_the_columns_and_rows() -> None:
    content = result_set_to_parquet(_result_set())
    table = pq.read_table(__import__("io").BytesIO(content))
    assert table.column_names == ["Desk", "Margin"]
    assert table.to_pylist() == [{"Desk": "EMEA", "Margin": 1.5}, {"Desk": "APAC", "Margin": None}]


def test_parquet_preserves_a_null_as_null_not_zero_or_blank() -> None:
    content = result_set_to_parquet(_result_set())
    table = pq.read_table(__import__("io").BytesIO(content))
    assert table.to_pylist()[1]["Margin"] is None


def test_an_empty_result_set_still_produces_valid_parquet_bytes() -> None:
    content = result_set_to_parquet(_result_set(columns=(), rows=(), outcome=ExecutionOutcome.INCONCLUSIVE))
    assert content  # non-empty bytes -- ArtefactStore.store refuses empty content
    table = pq.read_table(__import__("io").BytesIO(content))
    assert table.num_rows == 0
