"""Dual execution's own pure(-ish) logic -- stories S7.3.1/S7.3.2, spec §10.2.

    "Target side: DAX EVALUATE over XMLA against the dev or test model, with filters
    and parameter values applied per §10.2; query text stored... Source side: adapter
    execute_case with the chosen strategy; strategy stored... Both ResultSets stored as
    Parquet in the artefact store with content hash." (S7.3.1)

    "Timeout, adapter error, executor error and sampling shortfall produce
    INCONCLUSIVE with the reason class; the orchestrator retries once with a longer
    budget." (S7.3.2)

`to_sdk_filters`/`to_sdk_parameters`/`build_dax_query`/`result_set_to_parquet` are pure;
`_attempt_once`/`_run_with_retry` are plain asyncio with no database -- both are testable
here. The graph reads (`_table_map_for_sheet`, `_resolve_site`), the orchestration that
calls both adapters and writes artefacts/`ParityCase` properties, and `record_execution_
observation`/`inconclusive_rate` (real Postgres) are graph-coupled and covered by the
integration suite instead.
"""

from __future__ import annotations

import asyncio

import pyarrow.parquet as pq
from astra_adapter import (
    Column,
    ColumnRole,
    ExecutionOutcome,
    ExecutionStrategy,
    InconclusiveReason,
    ResultSet,
)

from astra_graph.case_execution import (
    _attempt_once,
    _run_with_retry,
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


# --------------------------------------------------------------------- retry and timeout


class _Counter:
    """A callable standing in for the adapter/executor call, counting invocations so a
    test can assert *how many times* it was actually tried."""

    def __init__(self, *behaviours: object) -> None:
        self._behaviours = list(behaviours)
        self.calls = 0

    async def __call__(self) -> ResultSet:
        self.calls += 1
        behaviour = self._behaviours[min(self.calls, len(self._behaviours)) - 1]
        if isinstance(behaviour, Exception):
            raise behaviour
        if isinstance(behaviour, int | float):
            await asyncio.sleep(behaviour)
        return behaviour if isinstance(behaviour, ResultSet) else _result_set()


async def test_attempt_once_returns_the_call_s_own_result_on_success() -> None:
    ok = _result_set(outcome=ExecutionOutcome.OK)
    result = await _attempt_once(
        _Counter(ok), 1.0, attempt=1, case_id="c1", strategy=ExecutionStrategy.EXTRACT_READ,
        reason_class_on_error=InconclusiveReason.ADAPTER_ERROR, detail=None,
    )
    assert result is ok


async def test_attempt_once_classifies_a_hang_as_timeout() -> None:
    result = await _attempt_once(
        _Counter(10.0), 0.05, attempt=1, case_id="c1", strategy=ExecutionStrategy.EXTRACT_READ,
        reason_class_on_error=InconclusiveReason.ADAPTER_ERROR, detail=None,
    )
    assert result.outcome is ExecutionOutcome.INCONCLUSIVE
    assert result.reason_class is InconclusiveReason.TIMEOUT
    assert "timed out" in result.reason
    assert result.detail["attempt"] == 1


async def test_attempt_once_classifies_a_raised_exception_with_the_given_reason_class() -> None:
    result = await _attempt_once(
        _Counter(RuntimeError("adapter is down")), 1.0, attempt=2, case_id="c1",
        strategy=ExecutionStrategy.XMLA_DAX, reason_class_on_error=InconclusiveReason.EXECUTOR_ERROR, detail=None,
    )
    assert result.outcome is ExecutionOutcome.INCONCLUSIVE
    assert result.reason_class is InconclusiveReason.EXECUTOR_ERROR
    assert result.reason == "adapter is down"
    assert result.detail["attempt"] == 2


async def test_run_with_retry_succeeds_on_the_first_attempt_without_retrying() -> None:
    call = _Counter(_result_set(outcome=ExecutionOutcome.OK))
    result, attempts = await _run_with_retry(
        call, case_id="c1", strategy=ExecutionStrategy.EXTRACT_READ,
        reason_class_on_error=InconclusiveReason.ADAPTER_ERROR,
        timeout_seconds=1.0, retry_timeout_seconds=2.0,
    )
    assert result.outcome is ExecutionOutcome.OK
    assert attempts == 1
    assert call.calls == 1


async def test_run_with_retry_retries_once_with_the_longer_budget_after_a_timeout() -> None:
    # The first budget (0.05s) is too short for a 0.15s call; the retry budget (1s) is not.
    call = _Counter(0.15, _result_set(outcome=ExecutionOutcome.OK))
    result, attempts = await _run_with_retry(
        call, case_id="c1", strategy=ExecutionStrategy.EXTRACT_READ,
        reason_class_on_error=InconclusiveReason.ADAPTER_ERROR,
        timeout_seconds=0.05, retry_timeout_seconds=1.0,
    )
    assert result.outcome is ExecutionOutcome.OK
    assert attempts == 2
    assert call.calls == 2


async def test_run_with_retry_surfaces_inconclusive_when_the_retry_also_fails() -> None:
    call = _Counter(RuntimeError("first"), RuntimeError("second"))
    result, attempts = await _run_with_retry(
        call, case_id="c1", strategy=ExecutionStrategy.XMLA_DAX,
        reason_class_on_error=InconclusiveReason.EXECUTOR_ERROR,
        timeout_seconds=1.0, retry_timeout_seconds=2.0,
    )
    assert result.outcome is ExecutionOutcome.INCONCLUSIVE
    assert result.reason_class is InconclusiveReason.EXECUTOR_ERROR
    assert result.reason == "second"  # the retry's own failure is what is surfaced
    assert attempts == 2
    assert call.calls == 2


async def test_run_with_retry_never_retries_an_adapter_s_own_honest_decline() -> None:
    decline = _result_set(
        outcome=ExecutionOutcome.INCONCLUSIVE, reason="no capability", reason_class=None,
    )
    call = _Counter(decline)
    result, attempts = await _run_with_retry(
        call, case_id="c1", strategy=ExecutionStrategy.EXTRACT_READ,
        reason_class_on_error=InconclusiveReason.ADAPTER_ERROR,
        timeout_seconds=1.0, retry_timeout_seconds=2.0,
    )
    assert result is decline
    assert attempts == 1
    assert call.calls == 1  # never retried -- see this module's own docstring


async def test_run_with_retry_surfaces_the_retry_s_own_reason_class_when_it_differs_from_the_first() -> None:
    # A timeout on the first attempt (reason_class TIMEOUT), a raised exception on the
    # retry (reason_class ADAPTER_ERROR) -- the surfaced result reflects what actually
    # happened last, not the first attempt's own cause.
    call = _Counter(10.0, RuntimeError("retry failed too"))
    result, attempts = await _run_with_retry(
        call, case_id="c1", strategy=ExecutionStrategy.EXTRACT_READ,
        reason_class_on_error=InconclusiveReason.ADAPTER_ERROR,
        timeout_seconds=0.05, retry_timeout_seconds=1.0,
    )
    assert result.reason_class is InconclusiveReason.ADAPTER_ERROR
    assert result.reason == "retry failed too"
    assert attempts == 2
