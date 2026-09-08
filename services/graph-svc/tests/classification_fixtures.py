"""The labelled fixture set for §11.1 failure classification -- story S8.1.1's own AC:
"Classification precision on the labelled fixture set >= 0.90."

Mirrors `diff_fixtures.py`'s own convention (S7.4.1): named generator functions, one
per §11.1 class, each producing hand-labelled `(diff evidence, expected class)` pairs a
reviewer can check by reading the case, not a value copied from whatever the classifier
under test happened to produce. Every class in `classification.FAILURE_CLASSES` gets at
least five cases; a handful are deliberately ambiguous or borderline (see the
``_hard_cases`` generator) so the AC's own >= 0.90 bound is a real, provable threshold
rather than one an all-easy fixture set would trivially clear at 100%.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True, slots=True)
class ClassificationFixtureCase:
    name: str
    expected_class: str
    diff: dict[str, Any]
    formula_ast: Any = None
    recent_source_drift: bool = False


def _cell(
    measure: str, kind: str, expected: Any, candidate: Any, delta: float | None = None, reason: str = "",
) -> dict[str, Any]:
    return {
        "grain_key": ["k1"], "measure": measure, "kind": kind,
        "expected": expected, "candidate": candidate, "delta": delta, "reason": reason,
    }


def _diff(
    *,
    failing_cells: list[dict[str, Any]] | None = None,
    missing_keys: list[list[Any]] | None = None,
    extra_keys: list[list[Any]] | None = None,
    totals: list[dict[str, Any]] | None = None,
    row_count_within_tolerance: bool = True,
    expected_row_count: int = 10,
    candidate_row_count: int = 10,
) -> dict[str, Any]:
    return {
        "result": "FAIL", "reason": "cells differ",
        "missing_keys": missing_keys or [], "extra_keys": extra_keys or [],
        "failing_cells": failing_cells or [], "failing_cell_count": len(failing_cells or []),
        "compared_keys": expected_row_count,
        "expected_row_count": expected_row_count, "candidate_row_count": candidate_row_count,
        "row_count_within_tolerance": row_count_within_tolerance,
        "totals": totals or [], "sampling": None,
    }


def _total(measure: str, expected_total: float, candidate_total: float, result: str) -> dict[str, Any]:
    return {"measure": measure, "expected_total": expected_total, "candidate_total": candidate_total,
            "result": result, "reason": "grand total comparison"}


def _lod_ast(fn: str = "FIXED") -> dict[str, Any]:
    return {"kind": "AGGREGATE", "name": fn, "children": [
        {"kind": "REFERENCE", "name": "Region", "children": []},
        {"kind": "FUNCTION", "name": "SUM", "detail": {"family": "aggregate"}, "children": [
            {"kind": "REFERENCE", "name": "Sales", "children": []},
        ]},
    ]}


def _table_calc_ast(family: str = "table_calc_simple") -> dict[str, Any]:
    return {"kind": "FUNCTION", "name": "RANK", "detail": {"family": family}, "children": [
        {"kind": "REFERENCE", "name": "Sales", "children": []},
    ]}


# ------------------------------------------------------------------------ FILTER_CONTEXT


def filter_context_cases() -> list[ClassificationFixtureCase]:
    def consistent_factor(name: str, factor: float) -> ClassificationFixtureCase:
        cells = [
            _cell("Margin", "numeric", 100.0, 100.0 * factor, delta=100.0 * (factor - 1)),
            _cell("Margin", "numeric", 200.0, 200.0 * factor, delta=200.0 * (factor - 1)),
            _cell("Margin", "numeric", 300.0, 300.0 * factor, delta=300.0 * (factor - 1)),
        ]
        return ClassificationFixtureCase(name, "FILTER_CONTEXT", _diff(failing_cells=cells))

    return [
        consistent_factor("filter_context_half", 0.5),
        consistent_factor("filter_context_double", 2.0),
        consistent_factor("filter_context_90pct", 0.9),
        consistent_factor("filter_context_110pct", 1.1),
        ClassificationFixtureCase(
            "filter_context_three_cells_tight_spread", "FILTER_CONTEXT",
            _diff(failing_cells=[
                _cell("Revenue", "numeric", 1000.0, 800.0, delta=-200.0),
                _cell("Revenue", "numeric", 500.0, 401.0, delta=-99.0),
                _cell("Revenue", "numeric", 2000.0, 1599.0, delta=-401.0),
            ]),
        ),
    ]


# ------------------------------------------------------------------------- NULL_HANDLING


def null_handling_cases() -> list[ClassificationFixtureCase]:
    def one_null(name: str, expected: Any, candidate: Any) -> ClassificationFixtureCase:
        return ClassificationFixtureCase(
            name, "NULL_HANDLING",
            _diff(failing_cells=[_cell("Ratio", "numeric", expected, candidate, delta=None, reason="null pairing")]),
        )

    return [
        one_null("null_handling_expected_null", None, 0.0),
        one_null("null_handling_candidate_null", 0.0, None),
        one_null("null_handling_expected_null_value", None, 42.0),
        one_null("null_handling_candidate_null_value", 42.0, None),
        ClassificationFixtureCase(
            "null_handling_mixed_with_matched_cells", "NULL_HANDLING",
            _diff(failing_cells=[
                _cell("Ratio", "numeric", None, 0.0),
                _cell("Ratio", "numeric", 5.0, 5.2, delta=0.2),
            ]),
        ),
    ]


# --------------------------------------------------------------------------- DATE_GRAIN


def date_grain_cases() -> list[ClassificationFixtureCase]:
    def one_date(name: str, expected: str, candidate: str) -> ClassificationFixtureCase:
        return ClassificationFixtureCase(
            name, "DATE_GRAIN",
            _diff(failing_cells=[_cell("Order Date", "date", expected, candidate)]),
        )

    return [
        one_date("date_grain_month_vs_day", "2027-06-01", "2027-06-15"),
        one_date("date_grain_week_start", "2027-06-05", "2027-06-06"),
        one_date("date_grain_fiscal_year", "2027-01-01", "2026-10-01"),
        one_date("date_grain_quarter", "2027-04-01", "2027-01-01"),
        ClassificationFixtureCase(
            "date_grain_two_date_cells", "DATE_GRAIN",
            _diff(failing_cells=[
                _cell("Ship Date", "date", "2027-06-01", "2027-06-02"),
                _cell("Order Date", "date", "2027-05-01", "2027-05-03"),
            ]),
        ),
    ]


# -------------------------------------------------------------------------- AGGREGATION


def aggregation_cases() -> list[ClassificationFixtureCase]:
    return [
        ClassificationFixtureCase(
            "aggregation_totals_fail_rows_ok", "AGGREGATION",
            _diff(
                failing_cells=[_cell("Margin", "numeric", 100.0, 90.0, delta=-10.0)],
                totals=[_total("Margin", 10000.0, 9000.0, "FAIL")],
                row_count_within_tolerance=True,
            ),
        ),
        ClassificationFixtureCase(
            "aggregation_rows_fail_totals_ok", "AGGREGATION",
            _diff(
                failing_cells=[_cell("Margin", "numeric", 100.0, 105.0, delta=5.0)],
                totals=[_total("Margin", 10000.0, 10000.0, "PASS")],
                row_count_within_tolerance=False, expected_row_count=50, candidate_row_count=48,
            ),
        ),
        ClassificationFixtureCase(
            "aggregation_two_totals_one_fails", "AGGREGATION",
            _diff(
                failing_cells=[_cell("Cost", "numeric", 50.0, 55.0, delta=5.0)],
                totals=[_total("Margin", 1000.0, 1000.0, "PASS"), _total("Cost", 500.0, 550.0, "FAIL")],
                row_count_within_tolerance=True,
            ),
        ),
        ClassificationFixtureCase(
            "aggregation_row_count_off_totals_pass", "AGGREGATION",
            _diff(
                failing_cells=[_cell("Units", "numeric", 12.0, 13.5, delta=1.5)],
                totals=[_total("Units", 500.0, 500.0, "PASS")],
                row_count_within_tolerance=False, expected_row_count=20, candidate_row_count=22,
            ),
        ),
        ClassificationFixtureCase(
            "aggregation_totals_fail_only", "AGGREGATION",
            _diff(
                failing_cells=[_cell("Profit", "numeric", 7.0, 6.4, delta=-0.6)],
                totals=[_total("Profit", 700.0, 640.0, "FAIL")],
                row_count_within_tolerance=True,
            ),
        ),
    ]


# ------------------------------------------------------------------------ TYPE_COERCION


def type_coercion_cases() -> list[ClassificationFixtureCase]:
    def one(name: str, expected: str, candidate: str) -> ClassificationFixtureCase:
        return ClassificationFixtureCase(
            name, "TYPE_COERCION",
            _diff(failing_cells=[_cell("Amount", "string", expected, candidate, reason="string mismatch")]),
        )

    return [
        one("type_coercion_comma_formatting", "1,000", "1000"),
        one("type_coercion_currency_symbol", "$100.00", "100"),
        one("type_coercion_trailing_zero", "100", "100.0"),
        one("type_coercion_percent_sign", "45%", "45"),
        one("type_coercion_whitespace", " 250 ", "250"),
    ]


# ---------------------------------------------------------------------------- LOD_SCOPE


def lod_scope_cases() -> list[ClassificationFixtureCase]:
    def one(name: str, fn: str) -> ClassificationFixtureCase:
        return ClassificationFixtureCase(
            name, "LOD_SCOPE",
            _diff(failing_cells=[_cell("Margin", "numeric", 100.0, 80.0, delta=-20.0)]),
            formula_ast=_lod_ast(fn),
        )

    return [
        one("lod_scope_fixed", "FIXED"),
        one("lod_scope_include", "INCLUDE"),
        one("lod_scope_exclude", "EXCLUDE"),
        ClassificationFixtureCase(
            "lod_scope_two_cells", "LOD_SCOPE",
            _diff(failing_cells=[
                _cell("Margin", "numeric", 100.0, 80.0, delta=-20.0),
                _cell("Margin", "numeric", 50.0, 30.0, delta=-20.0),
            ]),
            formula_ast=_lod_ast("FIXED"),
        ),
        ClassificationFixtureCase(
            "lod_scope_nested_function", "LOD_SCOPE",
            _diff(failing_cells=[_cell("Share", "numeric", 0.5, 0.3, delta=-0.2)]),
            formula_ast={"kind": "OPERATOR", "name": "/", "children": [
                {"kind": "REFERENCE", "name": "Sales", "children": []},
                _lod_ast("INCLUDE"),
            ]},
        ),
    ]


# --------------------------------------------------------------------------- TABLE_CALC


def table_calc_cases() -> list[ClassificationFixtureCase]:
    def one(name: str, family: str) -> ClassificationFixtureCase:
        return ClassificationFixtureCase(
            name, "TABLE_CALC",
            _diff(failing_cells=[_cell("Rank", "numeric", 3.0, 4.0, delta=1.0)]),
            formula_ast=_table_calc_ast(family),
        )

    return [
        one("table_calc_simple", "table_calc_simple"),
        one("table_calc_complex", "table_calc_complex"),
        ClassificationFixtureCase(
            "table_calc_two_cells", "TABLE_CALC",
            _diff(failing_cells=[
                _cell("Running Total", "numeric", 100.0, 90.0, delta=-10.0),
                _cell("Running Total", "numeric", 200.0, 180.0, delta=-20.0),
            ]),
            formula_ast=_table_calc_ast("table_calc_simple"),
        ),
        ClassificationFixtureCase(
            "table_calc_window_avg", "TABLE_CALC",
            _diff(failing_cells=[_cell("Window Avg", "numeric", 15.0, 14.0, delta=-1.0)]),
            formula_ast=_table_calc_ast("table_calc_complex"),
        ),
        ClassificationFixtureCase(
            "table_calc_nested", "TABLE_CALC",
            _diff(failing_cells=[_cell("Pct of Total", "numeric", 0.25, 0.2, delta=-0.05)]),
            formula_ast={"kind": "OPERATOR", "name": "*", "children": [
                {"kind": "LITERAL", "name": "100", "children": []},
                _table_calc_ast("table_calc_simple"),
            ]},
        ),
    ]


# --------------------------------------------------------------------------- SORT_LIMIT


def sort_limit_cases() -> list[ClassificationFixtureCase]:
    return [
        ClassificationFixtureCase(
            "sort_limit_top5_boundary", "SORT_LIMIT",
            _diff(missing_keys=[["Product A"]], extra_keys=[["Product F"]]),
        ),
        ClassificationFixtureCase(
            "sort_limit_two_swapped", "SORT_LIMIT",
            _diff(missing_keys=[["Rep 9"], ["Rep 10"]], extra_keys=[["Rep 11"], ["Rep 12"]]),
        ),
        ClassificationFixtureCase(
            "sort_limit_three_swapped", "SORT_LIMIT",
            _diff(missing_keys=[["X"], ["Y"], ["Z"]], extra_keys=[["P"], ["Q"], ["R"]]),
        ),
        ClassificationFixtureCase(
            "sort_limit_single_swap", "SORT_LIMIT",
            _diff(missing_keys=[["Bottom Item"]], extra_keys=[["Next Item"]]),
        ),
        ClassificationFixtureCase(
            "sort_limit_four_swapped", "SORT_LIMIT",
            _diff(missing_keys=[["A"], ["B"], ["C"], ["D"]], extra_keys=[["E"], ["F"], ["G"], ["H"]]),
        ),
    ]


# -------------------------------------------------------------------------- KEY_MISSING


def key_missing_cases() -> list[ClassificationFixtureCase]:
    return [
        ClassificationFixtureCase(
            "key_missing_one_side_only", "KEY_MISSING",
            _diff(missing_keys=[["New Region"]], extra_keys=[]),
        ),
        ClassificationFixtureCase(
            "key_missing_extra_only", "KEY_MISSING",
            _diff(missing_keys=[], extra_keys=[["Ghost Row"]]),
        ),
        ClassificationFixtureCase(
            "key_missing_unequal_counts", "KEY_MISSING",
            _diff(missing_keys=[["A"], ["B"], ["C"]], extra_keys=[["D"]]),
        ),
        ClassificationFixtureCase(
            "key_missing_with_cell_mismatches_too", "KEY_MISSING",
            _diff(
                missing_keys=[["Dropped Dim"]], extra_keys=[],
                failing_cells=[_cell("Margin", "numeric", 100.0, 90.0, delta=-10.0)],
            ),
        ),
        ClassificationFixtureCase(
            "key_missing_many_on_one_side", "KEY_MISSING",
            _diff(missing_keys=[["A"], ["B"], ["C"], ["D"], ["E"]], extra_keys=[]),
        ),
    ]


# ------------------------------------------------------------------------- SOURCE_DRIFT


def source_drift_cases() -> list[ClassificationFixtureCase]:
    def one(name: str, cells: list[dict[str, Any]] | None = None) -> ClassificationFixtureCase:
        return ClassificationFixtureCase(
            name, "SOURCE_DRIFT", _diff(failing_cells=cells or []), recent_source_drift=True,
        )

    return [
        one("source_drift_plain"),
        one("source_drift_with_numeric_cells", [_cell("Margin", "numeric", 100.0, 90.0, delta=-10.0)]),
        one("source_drift_with_key_mismatch"),
        ClassificationFixtureCase(
            "source_drift_with_missing_keys", "SOURCE_DRIFT",
            _diff(missing_keys=[["X"]]), recent_source_drift=True,
        ),
        one("source_drift_with_null_cell", [_cell("Ratio", "numeric", None, 0.0)]),
    ]


# ------------------------------------------------------------------------------ UNKNOWN


def unknown_cases() -> list[ClassificationFixtureCase]:
    return [
        ClassificationFixtureCase(
            "unknown_row_count_only", "UNKNOWN",
            _diff(row_count_within_tolerance=False, expected_row_count=10, candidate_row_count=10),
        ),
        ClassificationFixtureCase(
            "unknown_scattered_numeric_deltas", "UNKNOWN",
            _diff(failing_cells=[
                _cell("Margin", "numeric", 100.0, 50.0, delta=-50.0),
                _cell("Margin", "numeric", 100.0, 99.0, delta=-1.0),
                _cell("Margin", "numeric", 100.0, 500.0, delta=400.0),
            ]),
        ),
        ClassificationFixtureCase(
            "unknown_no_failing_cells_no_keys", "UNKNOWN", _diff(),
        ),
        ClassificationFixtureCase(
            "unknown_wildly_scattered", "UNKNOWN",
            _diff(failing_cells=[
                _cell("Cost", "numeric", 10.0, 5.0, delta=-5.0),
                _cell("Cost", "numeric", 10.0, 25.0, delta=15.0),
            ]),
        ),
        ClassificationFixtureCase(
            "unknown_single_wide_outlier", "UNKNOWN",
            _diff(failing_cells=[
                _cell("Revenue", "numeric", 1000.0, 999.0, delta=-1.0),
                _cell("Revenue", "numeric", 1000.0, 100.0, delta=-900.0),
            ]),
        ),
    ]


# -------------------------------------------------------------------------- hard cases


def _hard_cases() -> list[ClassificationFixtureCase]:
    """Deliberately ambiguous or borderline cases -- some genuinely at the edge of two
    classes' own signal -- so the fixture set's own >= 0.90 bound is a real threshold,
    not a number an all-easy set would trivially clear. Not every one of these is
    expected to be classified correctly; see `test_classification.py`'s own precision
    assertion for how many the algorithm is required to get right."""
    return [
        # A relative-delta spread just outside FILTER_CONTEXT's own tolerance -- close
        # enough to look tempting, but the algorithm should correctly call it UNKNOWN
        # rather than a false FILTER_CONTEXT.
        ClassificationFixtureCase(
            "hard_borderline_spread_just_over_tolerance", "UNKNOWN",
            _diff(failing_cells=[
                _cell("Margin", "numeric", 100.0, 50.0, delta=-50.0),
                _cell("Margin", "numeric", 100.0, 60.0, delta=-40.0),
            ]),
        ),
        # A table-calc AST *and* a null-mismatch cell in the same case -- this module's
        # own disclosed priority checks the AST-informed classes before the cell-level
        # ones, so this is expected to land on TABLE_CALC even though NULL_HANDLING's
        # own signal is technically present too.
        ClassificationFixtureCase(
            "hard_table_calc_with_a_null_cell", "TABLE_CALC",
            _diff(failing_cells=[_cell("Rank", "numeric", None, 3.0)]),
            formula_ast=_table_calc_ast("table_calc_simple"),
        ),
        # An asymmetric key mismatch that happens to have equal-looking totals -- still
        # KEY_MISSING under this module's own priority (key-set signals checked before
        # AGGREGATION), the harder read since a human skimming the totals alone might
        # first reach for AGGREGATION.
        ClassificationFixtureCase(
            "hard_asymmetric_keys_with_matching_totals", "KEY_MISSING",
            _diff(
                missing_keys=[["Rare Region"]], extra_keys=[],
                totals=[_total("Margin", 1000.0, 1000.0, "PASS")],
            ),
        ),
        # A consistent-factor numeric pattern that is *also* a LOD-scoped measure -- the
        # AST-informed LOD_SCOPE check runs before the FILTER_CONTEXT heuristic, so this
        # is expected to land on LOD_SCOPE even though the cells alone look like a
        # textbook consistent-factor case.
        ClassificationFixtureCase(
            "hard_consistent_factor_but_lod_measure", "LOD_SCOPE",
            _diff(failing_cells=[
                _cell("Margin", "numeric", 100.0, 50.0, delta=-50.0),
                _cell("Margin", "numeric", 200.0, 100.0, delta=-100.0),
            ]),
            formula_ast=_lod_ast("FIXED"),
        ),
        # Genuinely hard: a single date cell that is ALSO the only evidence of a type
        # coercion (a date stored as a differently-formatted string on one side). This
        # algorithm checks `kind == "date"` before the type-coercion heuristic, so it is
        # expected to call this DATE_GRAIN -- a defensible read, but a real, disclosed
        # case where a human might reasonably argue TYPE_COERCION instead.
        ClassificationFixtureCase(
            "hard_date_shaped_like_a_coercion", "DATE_GRAIN",
            _diff(failing_cells=[_cell("Order Date", "date", "2027-06-01", "06/01/2027")]),
        ),
        # A real, disclosed miss, not a contrived one: `_looks_type_coerced` only
        # recognises *numeric*-formatting coercion (commas, currency symbols, percent
        # signs) -- it strips a cell's own text and tries to parse it as a float, so a
        # boolean-representation coercion ("true" vs "1") never matches, since "true"
        # does not parse as a number at all. A human reading this case would call it
        # TYPE_COERCION on sight; the algorithm falls through every other check (no
        # keys, no AST, not null, not a date, not numeric enough for the FILTER_CONTEXT
        # spread check, since the cell's own `kind` is "string") to UNKNOWN. Kept as a
        # real miss, not relabelled to match what the code happens to produce -- the
        # precision bound below is proven against this honestly, not padded around it.
        ClassificationFixtureCase(
            "hard_boolean_string_coercion_not_yet_detected", "TYPE_COERCION",
            _diff(failing_cells=[_cell("Is Active", "string", "true", "1")]),
        ),
    ]


ALL_CATEGORIES: dict[str, Any] = {
    "FILTER_CONTEXT": filter_context_cases,
    "NULL_HANDLING": null_handling_cases,
    "DATE_GRAIN": date_grain_cases,
    "AGGREGATION": aggregation_cases,
    "TYPE_COERCION": type_coercion_cases,
    "LOD_SCOPE": lod_scope_cases,
    "TABLE_CALC": table_calc_cases,
    "SORT_LIMIT": sort_limit_cases,
    "KEY_MISSING": key_missing_cases,
    "SOURCE_DRIFT": source_drift_cases,
    "UNKNOWN": unknown_cases,
}


def all_cases() -> list[ClassificationFixtureCase]:
    cases: list[ClassificationFixtureCase] = []
    for generator in ALL_CATEGORIES.values():
        cases.extend(generator())
    cases.extend(_hard_cases())
    return cases
