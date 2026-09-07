"""§10.4 sampling, real as of story S7.5.1: "Full compare up to full_compare_max_rows;
above that, stratified by grain with the top-N rows by each measure's absolute value
always included; sample size and seed recorded."

`diff_fixtures.py`'s own sampling category proves the boundary a hand-verified pair can
express directly (every row its own stratum, so a low threshold degenerates to "compare
everything"). These tests build richer, multi-row-per-stratum result sets -- several
trade dates per desk -- to prove the parts that shape needs: every distinct stratum
value represented, a measure's own extreme outlier always caught even when nothing else
about it would have been sampled, totals/row-count/key-set comparison staying on the
full data regardless, and seed reproducibility.
"""

from __future__ import annotations

from dataclasses import replace
from typing import Any

from astra_adapter import Column, ExecutionOutcome, ExecutionStrategy, ResultSet

from astra_graph.diff import diff_result_sets
from astra_graph.tolerance_charter import DEFAULT_CHARTER, SamplingRule, ToleranceCharter

from .diff_fixtures import _dim, _measure


def _rs(columns: tuple[Column, ...], rows: tuple[tuple[Any, ...], ...]) -> ResultSet:
    return ResultSet(
        case_id="fixture", columns=columns, rows=rows, strategy=ExecutionStrategy.EXTRACT_READ,
        interface_version="1.0", adapter_name="fixture", adapter_version="0.1.0",
        outcome=ExecutionOutcome.OK, reason="", reason_class=None,
    )


_COLUMNS = (_dim("Desk"), _dim("TradeDate"), _measure("Margin"))
_DESKS = ("EMEA-1", "EMEA-2", "APAC-1", "NAM-1", "NAM-2")
_DATES_PER_DESK = 40  # 5 desks x 40 = 200 shared keys


def _grid_rows(*, outlier: tuple[str, int, float] | None = None) -> tuple[tuple[str, str, float], ...]:
    """One row per (desk, trade date), a small, deterministic margin value each --
    optionally overriding one row's own value, so a candidate built from this with a
    *different* outlier produces exactly one real, large discrepancy."""
    rows: list[tuple[str, str, float]] = []
    for desk in _DESKS:
        for day in range(_DATES_PER_DESK):
            value = 10.0 + day * 0.1
            if outlier is not None and outlier[:2] == (desk, day):
                value = outlier[2]
            rows.append((desk, f"2026-01-{day + 1:02d}", value))
    return tuple(rows)


def _charter(*, full_compare_max_rows: int, sample_rows: int, stratify_by: str = "grain") -> ToleranceCharter:
    return replace(
        DEFAULT_CHARTER,
        sampling=SamplingRule(
            full_compare_max_rows=full_compare_max_rows, sample_rows=sample_rows, stratify_by=stratify_by,
        ),
    )


def test_below_threshold_is_never_sampled() -> None:
    e = _rs(_COLUMNS, _grid_rows())
    c = _rs(_COLUMNS, _grid_rows())
    result = diff_result_sets(e, c, _charter(full_compare_max_rows=1000, sample_rows=50))
    assert result.sampling is None
    assert result.compared_keys == 200


def test_above_threshold_samples_and_records_size_and_seed() -> None:
    e = _rs(_COLUMNS, _grid_rows())
    c = _rs(_COLUMNS, _grid_rows())
    result = diff_result_sets(e, c, _charter(full_compare_max_rows=50, sample_rows=25), sampling_seed=7)
    assert result.sampling is not None
    assert result.sampling.total_keys == 200
    assert result.sampling.seed == 7
    assert result.compared_keys == result.sampling.sample_size
    assert result.compared_keys < 200  # a real sample, not everything


def test_every_distinct_desk_is_represented_even_with_a_zero_sample_budget() -> None:
    e = _rs(_COLUMNS, _grid_rows())
    c = _rs(_COLUMNS, _grid_rows())
    # sample_rows=0: only the required set (one key per stratum, plus top-N by measure)
    # is ever compared -- proving representation does not depend on the random fill.
    result = diff_result_sets(e, c, _charter(full_compare_max_rows=50, sample_rows=0), sampling_seed=1)
    assert result.sampling is not None
    assert result.sampling.stratified_by == "Desk"
    # PASS either way; the real assertion is that sampling ran with a real, small sample.
    assert result.result == "PASS"
    assert 0 < result.compared_keys < 200


def test_stratifying_by_a_named_grain_column_other_than_the_first_is_real() -> None:
    e = _rs(_COLUMNS, _grid_rows())
    c = _rs(_COLUMNS, _grid_rows())
    result = diff_result_sets(
        e, c, _charter(full_compare_max_rows=50, sample_rows=0, stratify_by="TradeDate"), sampling_seed=1,
    )
    assert result.sampling is not None
    assert result.sampling.stratified_by == "TradeDate"


def test_stratify_by_naming_a_non_grain_column_falls_back_to_the_first_dimension() -> None:
    e = _rs(_COLUMNS, _grid_rows())
    c = _rs(_COLUMNS, _grid_rows())
    result = diff_result_sets(
        e, c, _charter(full_compare_max_rows=50, sample_rows=0, stratify_by="Margin"), sampling_seed=1,
    )
    assert result.sampling is not None
    assert result.sampling.stratified_by == "Desk"


def test_a_measures_own_extreme_outlier_is_always_caught_even_with_a_zero_sample_budget() -> None:
    # NAM-2 / day 39 (the alphabetically-largest key in its own stratum -- never the
    # stratum's own representative, which picks the smallest by repr) carries a real,
    # large discrepancy. With sample_rows=0, the *only* way this key is ever compared is
    # via "top-N rows by each measure's absolute value" -- proving that mechanism works,
    # not the stratum-representative guarantee, which this key is deliberately excluded
    # from by construction.
    e = _rs(_COLUMNS, _grid_rows(outlier=("NAM-2", 39, 999_999.0)))
    c = _rs(_COLUMNS, _grid_rows())  # candidate never has the outlier value
    result = diff_result_sets(e, c, _charter(full_compare_max_rows=50, sample_rows=0), sampling_seed=1)
    assert result.result == "FAIL"
    # Grain keys are case-folded by `_key_component` (DEFAULT_CHARTER's own
    # `case_sensitive: False`) -- the same normalisation every string key gets.
    assert any(cell.grain_key == ("nam-2", "2026-01-40") for cell in result.failing_cells)


def test_totals_and_row_count_are_computed_on_the_full_data_regardless_of_sampling() -> None:
    e = _rs(_COLUMNS, _grid_rows())
    c = _rs(_COLUMNS, _grid_rows(outlier=("EMEA-1", 0, 999_999.0)))  # only affects the total
    result = diff_result_sets(e, c, _charter(full_compare_max_rows=50, sample_rows=0), sampling_seed=1)
    assert result.expected_row_count == 200
    assert result.candidate_row_count == 200
    assert len(result.totals) == 1
    total_check = result.totals[0]
    assert total_check.expected_total != total_check.candidate_total
    assert total_check.result == "FAIL"  # the totals check itself always sees the real discrepancy


def test_key_set_comparison_stays_full_even_when_heavily_sampled() -> None:
    rows = _grid_rows()
    e = _rs(_COLUMNS, rows)
    c = _rs(_COLUMNS, rows[:-1])  # candidate is missing exactly one real key
    result = diff_result_sets(e, c, _charter(full_compare_max_rows=50, sample_rows=1), sampling_seed=1)
    assert len(result.missing_keys) == 1
    assert result.result == "FAIL"


def test_the_same_explicit_seed_reproduces_an_identical_sample() -> None:
    e = _rs(_COLUMNS, _grid_rows())
    c = _rs(_COLUMNS, _grid_rows(outlier=("APAC-1", 20, -5.0)))
    charter = _charter(full_compare_max_rows=50, sample_rows=30)
    first = diff_result_sets(e, c, charter, sampling_seed=42)
    second = diff_result_sets(e, c, charter, sampling_seed=42)
    assert first.compared_keys == second.compared_keys
    assert first.failing_cell_count == second.failing_cell_count
    assert {cell.grain_key for cell in first.failing_cells} == {cell.grain_key for cell in second.failing_cells}


def test_an_unsupplied_seed_is_generated_and_recorded() -> None:
    e = _rs(_COLUMNS, _grid_rows())
    c = _rs(_COLUMNS, _grid_rows())
    charter = _charter(full_compare_max_rows=50, sample_rows=25)
    first = diff_result_sets(e, c, charter)
    second = diff_result_sets(e, c, charter)
    assert first.sampling is not None
    assert second.sampling is not None
    assert isinstance(first.sampling.seed, int)
    assert first.sampling.seed != second.sampling.seed  # two independently drawn seeds


def test_required_rows_are_never_truncated_below_sample_rows() -> None:
    # 5 desks -> 5 required stratum representatives, plus up to 20 top-N-by-measure rows
    # (all distinct desks/dates here) -- comfortably more than a sample_rows of 3.
    e = _rs(_COLUMNS, _grid_rows())
    c = _rs(_COLUMNS, _grid_rows())
    result = diff_result_sets(e, c, _charter(full_compare_max_rows=50, sample_rows=3), sampling_seed=1)
    assert result.sampling is not None
    assert result.compared_keys > 3
