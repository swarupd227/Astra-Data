"""The 200-case fixture corpus -- story S7.4.1's own AC: "Fixture set of 200
hand-verified pairs covering each charter rule; CI runs them on every change."

Each `DiffFixtureCase` is a real `(expected ResultSet, candidate ResultSet, charter)`
triple with a real, asserted outcome -- "hand-verified" here means what it can mean in
an automated system: every case's own expected result is a deliberate, reviewed
assertion about what `diff_result_sets` must return for that input, not a value copied
from whatever the code under test happened to produce. Cases are grouped into named
generator functions, one per charter rule or algorithm step the AC asks to be covered,
so `test_diff_fixtures.py` can assert real per-category coverage rather than only a
total count.

**Two of the charter's nine blocks have no category here, disclosed, not an oversight.**
`ParamRule` (parameter-combination enumeration) is S7.2.1's own case-*derivation*-time
concern -- `diff_result_sets` never reads it, since by the time two ResultSets exist to
diff, enumeration has already happened. `WaiverRule` is a policy `tolerance_charter.py`
itself already disclosed as unread by any mechanism yet (F8.3's own later Exception
Desk scope) -- the diff algorithm does not consult it either. `OrderingRule` gets a
small category confirming row order never affects the verdict (§10.3's own key-set
comparison is inherently order-independent) rather than one exercising
`sort_sensitive`/`top_n_tie_break` directly, since neither field is read by this
story's own algorithm -- top-N tie-breaking is §10.1's own row-retention concern, not
part of the comparison itself. `SamplingRule` similarly gets a small category
confirming today's algorithm always does a full compare regardless of its fields'
values, not a real stratified-sample test -- §10.4 sampling is F7.5's own later, unbuilt
scope, the identical boundary `InconclusiveReason.SAMPLING_SHORTFALL` already disclosed
as unproducible (S7.3.2, ADR 0054).
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field, replace
from datetime import date, datetime
from typing import Any

from astra_adapter import (
    Column,
    ColumnRole,
    ExecutionOutcome,
    ExecutionStrategy,
    InconclusiveReason,
    ResultSet,
)

from astra_graph.diff import DiffResult
from astra_graph.tolerance_charter import (
    DEFAULT_CHARTER,
    DateRule,
    NullRule,
    NumericRule,
    RowRule,
    SamplingRule,
    StringRule,
    ToleranceCharter,
)

# ------------------------------------------------------------------------------ helpers


def _dim(name: str, type_: str = "string") -> Column:
    return Column(name, ColumnRole.DIMENSION, type_)


def _measure(name: str, type_: str = "double") -> Column:
    return Column(name, ColumnRole.MEASURE, type_)


def _rs(
    columns: tuple[Column, ...],
    rows: tuple[tuple[Any, ...], ...],
    *,
    outcome: ExecutionOutcome = ExecutionOutcome.OK,
    reason: str = "",
    reason_class: InconclusiveReason | None = None,
) -> ResultSet:
    return ResultSet(
        case_id="fixture", columns=columns, rows=rows, strategy=ExecutionStrategy.EXTRACT_READ,
        interface_version="1.0", adapter_name="fixture", adapter_version="0.1.0",
        outcome=outcome, reason=reason, reason_class=reason_class,
    )


@dataclass(frozen=True, slots=True)
class DiffFixtureCase:
    name: str
    category: str
    expected: ResultSet
    candidate: ResultSet
    expected_result: str
    charter: ToleranceCharter = DEFAULT_CHARTER
    check: Any = None
    """Optional ``Callable[[DiffResult], None]`` for a category-specific assertion
    beyond ``.result`` -- raises on failure, same as a plain ``assert``."""

    column_target_map: dict[str, str] = field(default_factory=dict)
    """§10.3's own "column mapping via MAPS_TO" -- empty for every category except
    ``"column_mapping"`` itself, matching `diff_result_sets`'s own real default."""


_GRAIN_MEASURE = (_dim("Desk"), _measure("Margin"))


def _one_row_case(
    name: str, category: str, expected_value: Any, candidate_value: Any, expected_result: str,
    *, charter: ToleranceCharter = DEFAULT_CHARTER, key: str = "EMEA", check: Any = None,
    measure_type: str = "double",
) -> DiffFixtureCase:
    """``measure_type`` must match what the two values actually are -- the measure
    column's own declared type is what `diff.py`'s type lattice classifies to pick
    `compare_numeric` vs `compare_string`, and a string value sent through
    `compare_numeric` raises rather than comparing (correctly: a genuinely mistyped
    result set is a real problem, not something to silently paper over)."""
    columns = (_dim("Desk"), _measure("Margin", measure_type))
    expected = _rs(columns, ((key, expected_value),))
    candidate = _rs(columns, ((key, candidate_value),))
    return DiffFixtureCase(name, category, expected, candidate, expected_result, charter, check)


# --------------------------------------------------------------------------- numeric (25)


def _numeric_cases() -> list[DiffFixtureCase]:
    cases: list[DiffFixtureCase] = []
    # DEFAULT_CHARTER.numeric: abs_epsilon=0.005, rel_epsilon=1e-6.
    abs_pass_offsets = [0.0, 0.001, 0.004, 0.005]
    for i, offset in enumerate(abs_pass_offsets):
        cases.append(_one_row_case(f"numeric_abs_pass_{i}", "numeric", 100.0, 100.0 + offset, "PASS"))

    abs_fail_offsets = [0.0051, 0.01, 1.0, -1.0]
    for i, offset in enumerate(abs_fail_offsets):
        cases.append(_one_row_case(f"numeric_abs_fail_{i}", "numeric", 100.0, 100.0 + offset, "FAIL"))

    # Large values: within abs_epsilon is now implausible, so rel_epsilon carries it.
    rel_pass = [(1_000_000.0, 1_000_000.5), (1_000_000.0, 999_999.6)]
    for i, (e, c) in enumerate(rel_pass):
        cases.append(_one_row_case(f"numeric_rel_pass_{i}", "numeric", e, c, "PASS"))

    rel_fail = [(1_000_000.0, 1_100_000.0), (1_000_000.0, 900_000.0)]
    for i, (e, c) in enumerate(rel_fail):
        cases.append(_one_row_case(f"numeric_rel_fail_{i}", "numeric", e, c, "FAIL"))

    # A custom, tighter charter -- confirms the charter's own epsilon is really read,
    # not a hardcoded default.
    tight = replace(DEFAULT_CHARTER, numeric=NumericRule(abs_epsilon=0.0001, rel_epsilon=1e-9))
    cases.append(_one_row_case("numeric_tight_charter_fails", "numeric", 100.0, 100.0005, "FAIL", charter=tight))
    loose = replace(DEFAULT_CHARTER, numeric=NumericRule(abs_epsilon=5.0, rel_epsilon=0.1))
    cases.append(_one_row_case("numeric_loose_charter_passes", "numeric", 100.0, 104.0, "PASS", charter=loose))

    # Zero and negative-zero boundary.
    cases.append(_one_row_case("numeric_zero_equal", "numeric", 0.0, 0.0, "PASS"))
    cases.append(_one_row_case("numeric_zero_vs_small", "numeric", 0.0, 0.006, "FAIL"))
    cases.append(_one_row_case("numeric_negative_equal", "numeric", -50.0, -50.0, "PASS"))
    cases.append(_one_row_case("numeric_negative_within_epsilon", "numeric", -50.0, -50.003, "PASS"))
    cases.append(_one_row_case("numeric_negative_beyond_epsilon", "numeric", -50.0, -50.5, "FAIL"))
    cases.append(_one_row_case("numeric_sign_flip", "numeric", 10.0, -10.0, "FAIL"))

    # Integers presented as floats (the lattice's own integer<=decimal<=double chain).
    cases.append(_one_row_case("numeric_integer_exact", "numeric", 42.0, 42.0, "PASS"))
    cases.append(_one_row_case("numeric_integer_off_by_one", "numeric", 42.0, 43.0, "FAIL"))

    def _check_delta_five(result: DiffResult) -> None:
        assert result.failing_cells[0].delta == 5.0

    cases.append(
        _one_row_case(
            "numeric_delta_recorded", "numeric", 100.0, 105.0, "FAIL", check=_check_delta_five,
        )
    )
    return cases


# ----------------------------------------------------------------------------- nulls (24)


def _nulls_cases() -> list[DiffFixtureCase]:
    cases: list[DiffFixtureCase] = []

    cases.append(_one_row_case("nulls_both_none", "nulls", None, None, "PASS"))
    cases.append(_one_row_case("nulls_both_empty_string", "nulls", "", "", "PASS"))
    cases.append(_one_row_case("nulls_none_vs_empty_string", "nulls", None, "", "PASS"))

    # source_null_vs_target_zero: default FAIL.
    cases.append(_one_row_case("nulls_source_null_target_zero_fails_by_default", "nulls", None, 0.0, "FAIL"))
    lenient_zero = replace(DEFAULT_CHARTER, nulls=NullRule(source_null_vs_target_zero="PASS"))
    cases.append(
        _one_row_case(
            "nulls_source_null_target_zero_passes_when_allowed", "nulls", None, 0.0, "PASS", charter=lenient_zero,
        )
    )

    # source_null_vs_target_blank: default PASS. It only ever fires when
    # empty_string_is_null=False -- with the default True, a target "" is already
    # collapsed into "both sides are null" before this rule is ever consulted (see
    # `compare_null`'s own docstring on why the two fields are deliberately independent).
    cases.append(_one_row_case("nulls_source_null_target_blank_passes_by_default", "nulls", None, "", "PASS"))
    strict_blank = replace(
        DEFAULT_CHARTER, nulls=NullRule(source_null_vs_target_blank="FAIL", empty_string_is_null=False)
    )
    cases.append(
        _one_row_case(
            "nulls_source_null_target_blank_fails_when_strict", "nulls", None, "", "FAIL", charter=strict_blank,
        )
    )

    # target null, source has a value -- always a FAIL, no charter field softens this.
    cases.append(_one_row_case("nulls_target_null_source_value_fails", "nulls", 100.0, None, "FAIL"))
    cases.append(_one_row_case("nulls_target_empty_source_value_fails", "nulls", "EMEA", "", "FAIL"))

    # empty_string_is_null=False: an empty string is a real, comparable value, not null.
    literal_empty = replace(DEFAULT_CHARTER, nulls=NullRule(empty_string_is_null=False))
    cases.append(
        _one_row_case(
            "nulls_empty_string_not_null_matches_empty", "nulls", "", "", "PASS", charter=literal_empty,
            measure_type="string",
        )
    )
    cases.append(
        _one_row_case(
            "nulls_empty_string_not_null_mismatches_value", "nulls", "", "EMEA", "FAIL", charter=literal_empty,
            measure_type="string",
        )
    )
    cases.append(
        _one_row_case(
            "nulls_empty_string_not_null_vs_real_null", "nulls", "", None, "FAIL", charter=literal_empty,
        )
    )

    # source has an unmatched non-zero, non-blank value on the target side.
    cases.append(_one_row_case("nulls_source_null_target_unmatched_value_fails", "nulls", None, "unexpected", "FAIL"))
    cases.append(_one_row_case("nulls_source_null_target_nonzero_number_fails", "nulls", None, 5.0, "FAIL"))

    # Null in a grain (dimension) column, not a measure -- exercised via the key path.
    def _null_grain_expected() -> ResultSet:
        return _rs(_GRAIN_MEASURE, ((None, 100.0),))

    e = _null_grain_expected()
    c = _rs(_GRAIN_MEASURE, ((None, 100.0),))
    cases.append(DiffFixtureCase("nulls_null_grain_matches", "nulls", e, c, "PASS"))

    e2 = _null_grain_expected()
    c2 = _rs(_GRAIN_MEASURE, ((None, 100.0),))  # same key -- both null grains collapse to one key
    cases.append(DiffFixtureCase("nulls_null_grain_collapses_to_one_key", "nulls", e2, c2, "PASS"))

    # A mix of null and non-null measures across several shared keys.
    e3 = _rs(_GRAIN_MEASURE, (("EMEA", None), ("APAC", 50.0), ("APJ", 0.0)))
    c3 = _rs(_GRAIN_MEASURE, (("EMEA", None), ("APAC", 50.0), ("APJ", 0.0)))
    cases.append(DiffFixtureCase("nulls_mixed_row_all_pass", "nulls", e3, c3, "PASS"))

    e4 = _rs(_GRAIN_MEASURE, (("EMEA", None), ("APAC", 50.0)))
    c4 = _rs(_GRAIN_MEASURE, (("EMEA", 0.0), ("APAC", 50.0)))
    cases.append(DiffFixtureCase("nulls_mixed_row_one_fails", "nulls", e4, c4, "FAIL"))

    # Boolean-ish zero (0 as int, not float) still counts as "zero" for the null matrix.
    cases.append(_one_row_case("nulls_source_null_target_int_zero", "nulls", None, 0, "FAIL"))

    # Both empty_string_is_null True (default) and a genuinely blank source with a
    # populated target -- covered above; add the reverse direction too.
    cases.append(_one_row_case("nulls_empty_source_target_value_fails", "nulls", "", 5.0, "FAIL"))

    # Every combination of the two independent null-matrix verdicts, exhaustively.
    # blank_verdict needs empty_string_is_null=False to be reached at all -- see this
    # function's own comment above `strict_blank`.
    for zero_verdict in ("PASS", "FAIL"):
        for blank_verdict in ("PASS", "FAIL"):
            charter = replace(
                DEFAULT_CHARTER,
                nulls=NullRule(
                    source_null_vs_target_zero=zero_verdict, source_null_vs_target_blank=blank_verdict,
                    empty_string_is_null=False,
                ),
            )
            cases.append(
                _one_row_case(
                    f"nulls_matrix_zero_{zero_verdict}_blank_{blank_verdict}_zero_case",
                    "nulls", None, 0.0, zero_verdict, charter=charter,
                )
            )
            cases.append(
                _one_row_case(
                    f"nulls_matrix_zero_{zero_verdict}_blank_{blank_verdict}_blank_case",
                    "nulls", None, "", blank_verdict, charter=charter, measure_type="string",
                )
            )
    return cases


# ------------------------------------------------------------------------------ dates (22)


def _dates_cases() -> list[DiffFixtureCase]:
    cases: list[DiffFixtureCase] = []
    date_measure = (_dim("D", "date"), _measure("M"))

    def _date_case(name: str, e_val: Any, c_val: Any, expected_result: str, *, charter: ToleranceCharter = DEFAULT_CHARTER) -> DiffFixtureCase:
        """Places the date value in the *grain* -- exercises key-normalisation
        (`diff.py`'s own symmetric simplification), not `compare_date`'s own asymmetric
        per-pair rule. Use `_date_cell_case` for the latter."""
        e = _rs(date_measure, ((e_val, 10.0),))
        c = _rs(date_measure, ((c_val, 10.0),))
        return DiffFixtureCase(name, "dates", e, c, expected_result, charter)

    string_grain_date_measure = (_dim("Desk"), _measure("D", "date"))

    def _date_cell_case(name: str, e_val: Any, c_val: Any, expected_result: str, *, charter: ToleranceCharter = DEFAULT_CHARTER) -> DiffFixtureCase:
        """Places the date value in a *measure* -- exercises `compare_date`'s own real,
        asymmetric "truncate the candidate to the source's own grain" cell rule."""
        e = _rs(string_grain_date_measure, (("EMEA", e_val),))
        c = _rs(string_grain_date_measure, (("EMEA", c_val),))
        return DiffFixtureCase(name, "dates", e, c, expected_result, charter)

    cases.append(_date_case("dates_exact_date_match", date(2026, 1, 1), date(2026, 1, 1), "PASS"))
    cases.append(_date_case("dates_exact_date_mismatch", date(2026, 1, 1), date(2026, 1, 2), "FAIL"))
    cases.append(_date_case("dates_date_source_datetime_candidate_same_day", date(2026, 1, 1), datetime(2026, 1, 1, 14, 30), "PASS"))
    cases.append(_date_case("dates_date_source_datetime_candidate_different_day", date(2026, 1, 1), datetime(2026, 1, 2, 0, 0), "FAIL"))
    cases.append(_date_case("dates_datetime_source_datetime_candidate_exact", datetime(2026, 1, 1, 9, 0), datetime(2026, 1, 1, 9, 0), "PASS"))
    cases.append(_date_case("dates_iso_string_both_sides", "2026-01-01", "2026-01-01", "PASS"))
    cases.append(_date_case("dates_iso_string_mismatch", "2026-01-01", "2026-01-02", "FAIL"))
    cases.append(_date_case("dates_iso_string_source_date_object_candidate", date(2026, 1, 1), "2026-01-01", "PASS"))
    cases.append(_date_case("dates_iso_datetime_string_candidate", date(2026, 1, 1), "2026-01-01T23:59:59", "PASS"))
    cases.append(_date_case("dates_unparseable_candidate_fails", date(2026, 1, 1), "not-a-date", "FAIL"))
    cases.append(_date_case("dates_unparseable_expected_fails", "not-a-date", date(2026, 1, 1), "FAIL"))
    cases.append(_date_case("dates_leap_day_match", date(2028, 2, 29), date(2028, 2, 29), "PASS"))
    cases.append(_date_case("dates_leap_day_mismatch", date(2028, 2, 29), date(2028, 3, 1), "FAIL"))
    cases.append(_date_case("dates_year_boundary_match", date(2025, 12, 31), date(2025, 12, 31), "PASS"))
    cases.append(_date_case("dates_year_boundary_mismatch", date(2025, 12, 31), date(2026, 1, 1), "FAIL"))

    # A cell-level (measure) date comparison: two datetimes with the same day but a
    # different time are a genuine mismatch under `compare_date`'s own real rule
    # (unlike grain/key normalisation's own symmetric date-only simplification above).
    cases.append(_date_cell_case("dates_cell_datetime_different_time_fails", datetime(2026, 1, 1, 9, 0), datetime(2026, 1, 1, 9, 1), "FAIL"))
    cases.append(_date_cell_case("dates_cell_date_source_datetime_candidate_same_day_passes", date(2026, 1, 1), datetime(2026, 1, 1, 23, 59), "PASS"))

    # grain_alignment turned off: no truncation at all, so a datetime candidate with any
    # time component no longer matches a plain-date source (since equality is then
    # between a date and a datetime with the identical day but different types --
    # correctly a mismatch under Python's own date/datetime equality).
    no_truncate = replace(DEFAULT_CHARTER, dates=DateRule(grain_alignment="EXACT"))
    cases.append(_date_cell_case("dates_no_truncation_rule_time_component_fails", date(2026, 1, 1), datetime(2026, 1, 1, 8, 0), "FAIL", charter=no_truncate))
    cases.append(_date_cell_case("dates_no_truncation_rule_exact_datetimes_still_pass", datetime(2026, 1, 1, 8, 0), datetime(2026, 1, 1, 8, 0), "PASS", charter=no_truncate))

    # Dates as a grain/key value, not only a measure -- keying must also truncate.
    date_grain = (_dim("D", "date"), _measure("M"))
    e_key = _rs(date_grain, ((date(2026, 1, 1), 10.0),))
    c_key = _rs(date_grain, ((datetime(2026, 1, 1, 13, 0), 10.0),))
    cases.append(DiffFixtureCase("dates_grain_key_truncation_matches", "dates", e_key, c_key, "PASS"))

    e_key2 = _rs(date_grain, ((date(2026, 1, 1), 10.0),))
    c_key2 = _rs(date_grain, ((datetime(2026, 1, 2, 0, 0), 10.0),))
    cases.append(DiffFixtureCase("dates_grain_key_truncation_mismatches", "dates", e_key2, c_key2, "FAIL"))

    cases.append(_date_case("dates_null_both_sides", None, None, "PASS"))
    cases.append(_date_case("dates_null_source_value_candidate_fails", None, date(2026, 1, 1), "FAIL"))
    cases.append(_date_case("dates_value_source_null_candidate_fails", date(2026, 1, 1), None, "FAIL"))
    return cases


# --------------------------------------------------------------------------- strings (21)


def _strings_cases() -> list[DiffFixtureCase]:
    cases: list[DiffFixtureCase] = []

    def _s(name: str, e: str, c: str, expected_result: str, *, charter: ToleranceCharter = DEFAULT_CHARTER) -> DiffFixtureCase:
        return _one_row_case(name, "strings", e, c, expected_result, charter=charter, measure_type="string")

    cases.append(_s("strings_exact_match", "EMEA", "EMEA", "PASS"))
    cases.append(_s("strings_case_fold_default_passes", "EMEA", "emea", "PASS"))
    cases.append(_s("strings_case_fold_mixed_passes", "EmEa", "eMeA", "PASS"))
    cases.append(_s("strings_trim_default_passes", "EMEA", "  EMEA  ", "PASS"))
    cases.append(_s("strings_trim_and_case_fold_together", "EMEA", "  emea  ", "PASS"))
    cases.append(_s("strings_genuine_mismatch_fails", "EMEA", "APAC", "FAIL"))
    cases.append(_s("strings_substring_is_not_equal", "EME", "EMEA", "FAIL"))
    cases.append(_s("strings_numeric_string_exact", "12345", "12345", "PASS"))
    cases.append(_s("strings_numeric_string_mismatch", "12345", "12346", "FAIL"))

    case_sensitive = replace(DEFAULT_CHARTER, strings=StringRule(case_sensitive=True))
    cases.append(_s("strings_case_sensitive_charter_exact_passes", "EMEA", "EMEA", "PASS", charter=case_sensitive))
    cases.append(_s("strings_case_sensitive_charter_case_diff_fails", "EMEA", "emea", "FAIL", charter=case_sensitive))

    no_trim = replace(DEFAULT_CHARTER, strings=StringRule(trim=False))
    cases.append(_s("strings_no_trim_charter_whitespace_fails", "EMEA", "EMEA ", "FAIL", charter=no_trim))
    cases.append(_s("strings_no_trim_charter_exact_still_passes", "EMEA", "EMEA", "PASS", charter=no_trim))

    no_trim_no_fold = replace(DEFAULT_CHARTER, strings=StringRule(trim=False, case_sensitive=True))
    cases.append(_s("strings_strictest_charter_exact_passes", "EMEA", "EMEA", "PASS", charter=no_trim_no_fold))
    cases.append(_s("strings_strictest_charter_any_diff_fails", "EMEA", "Emea", "FAIL", charter=no_trim_no_fold))

    # Two sides whose declared types disagree (kind resolves to "string" -- the
    # lattice's own fallback) compare as strings, not numbers -- `str(100.0)` is
    # ``"100.0"``, so the *stringified* values must actually match, not the numbers.
    mixed_type_cols = (_dim("Desk"), _measure("M", "string"))
    e_mixed = _rs(mixed_type_cols, (("EMEA", "100.0"),))
    c_mixed = _rs((_dim("Desk"), _measure("M", "double")), (("EMEA", 100.0),))
    cases.append(DiffFixtureCase("strings_type_mismatch_falls_back_to_string_pass", "strings", e_mixed, c_mixed, "PASS"))

    e_mixed2 = _rs(mixed_type_cols, (("EMEA", "100.0"),))
    c_mixed2 = _rs((_dim("Desk"), _measure("M", "double")), (("EMEA", 100.5),))
    cases.append(DiffFixtureCase("strings_type_mismatch_falls_back_to_string_fail", "strings", e_mixed2, c_mixed2, "FAIL"))

    # A grain value that differs only by case/whitespace still keys to the same row.
    grain_fold = (_dim("Desk"), _measure("Margin"))
    e_g = _rs(grain_fold, (("EMEA", 100.0),))
    c_g = _rs(grain_fold, (("  emea  ", 100.0),))
    cases.append(DiffFixtureCase("strings_grain_key_case_fold_matches", "strings", e_g, c_g, "PASS"))

    e_g2 = _rs(grain_fold, (("EMEA", 100.0),))
    c_g2 = _rs(grain_fold, (("APAC", 100.0),))
    cases.append(DiffFixtureCase("strings_grain_key_genuine_mismatch", "strings", e_g2, c_g2, "FAIL"))

    unicode_pairs = [("Café", "café"), ("Straße", "STRASSE".casefold())]
    for i, (e_val, c_val) in enumerate(unicode_pairs):
        cases.append(_s(f"strings_unicode_case_fold_{i}", e_val, c_val, "PASS"))

    # A non-empty source that trims down to empty, matching an already-empty candidate --
    # a genuine string-trim case, not the null matrix. Needs `empty_string_is_null=False`:
    # with the default True, either side being (or trimming to, for the null check
    # itself, which never trims) a literal "" is intercepted by `compare_null` before
    # `compare_string`'s own trim logic ever runs.
    literal_strings = replace(DEFAULT_CHARTER, nulls=NullRule(empty_string_is_null=False))
    cases.append(_s("strings_whitespace_only_trims_to_match_empty", " ", "", "PASS", charter=literal_strings))
    return cases


# --------------------------------------------------------------------------- ordering (8)


def _ordering_cases() -> list[DiffFixtureCase]:
    """§10.3's own key-set comparison is inherently order-independent -- these confirm
    row order never changes the verdict, rather than exercising `OrderingRule`'s own
    fields directly (neither is read by `diff_result_sets` -- see this module's own
    docstring)."""
    cases: list[DiffFixtureCase] = []
    rows_forward = (("EMEA", 100.0), ("APAC", 200.0), ("APJ", 300.0))
    rows_reversed = tuple(reversed(rows_forward))
    e = _rs(_GRAIN_MEASURE, rows_forward)
    c_same_order = _rs(_GRAIN_MEASURE, rows_forward)
    c_reversed = _rs(_GRAIN_MEASURE, rows_reversed)
    cases.append(DiffFixtureCase("ordering_same_order_passes", "ordering", e, c_same_order, "PASS"))
    cases.append(DiffFixtureCase("ordering_reversed_order_still_passes", "ordering", e, c_reversed, "PASS"))

    rows_shuffled = (("APJ", 300.0), ("EMEA", 100.0), ("APAC", 200.0))
    c_shuffled = _rs(_GRAIN_MEASURE, rows_shuffled)
    cases.append(DiffFixtureCase("ordering_shuffled_order_still_passes", "ordering", e, c_shuffled, "PASS"))

    # A real mismatch is still caught regardless of order.
    rows_shuffled_bad = (("APJ", 999.0), ("EMEA", 100.0), ("APAC", 200.0))
    c_shuffled_bad = _rs(_GRAIN_MEASURE, rows_shuffled_bad)
    cases.append(DiffFixtureCase("ordering_shuffled_order_still_fails_on_real_diff", "ordering", e, c_shuffled_bad, "FAIL"))

    for i in range(4):
        rng = random.Random(i)
        shuffled = list(rows_forward)
        rng.shuffle(shuffled)
        c = _rs(_GRAIN_MEASURE, tuple(shuffled))
        cases.append(DiffFixtureCase(f"ordering_random_shuffle_{i}_passes", "ordering", e, c, "PASS"))
    return cases


# --------------------------------------------------------------------------- rows (26)


def _rows_cases() -> list[DiffFixtureCase]:
    cases: list[DiffFixtureCase] = []

    e = _rs(_GRAIN_MEASURE, (("EMEA", 100.0), ("APAC", 200.0)))
    c_missing = _rs(_GRAIN_MEASURE, (("EMEA", 100.0),))
    cases.append(DiffFixtureCase("rows_missing_key_fails_by_default", "rows", e, c_missing, "FAIL"))

    permissive_missing = replace(DEFAULT_CHARTER, rows=RowRule(missing_key="PASS"))
    cases.append(
        DiffFixtureCase("rows_missing_key_passes_when_charter_allows", "rows", e, c_missing, "PASS", permissive_missing)
    )

    c_extra = _rs(_GRAIN_MEASURE, (("EMEA", 100.0), ("APAC", 200.0), ("APJ", 300.0)))
    cases.append(DiffFixtureCase("rows_extra_key_fails_by_default", "rows", e, c_extra, "FAIL"))

    permissive_extra = replace(DEFAULT_CHARTER, rows=RowRule(extra_key="PASS"))
    cases.append(
        DiffFixtureCase("rows_extra_key_passes_when_charter_allows", "rows", e, c_extra, "PASS", permissive_extra)
    )

    c_both = _rs(_GRAIN_MEASURE, (("APAC", 200.0), ("APJ", 300.0)))
    cases.append(DiffFixtureCase("rows_missing_and_extra_both_fail", "rows", e, c_both, "FAIL"))

    permissive_both = replace(DEFAULT_CHARTER, rows=RowRule(missing_key="PASS", extra_key="PASS"))
    cases.append(
        DiffFixtureCase(
            "rows_missing_and_extra_both_pass_when_allowed", "rows", e, c_both, "PASS", permissive_both,
        )
    )

    # row_count_tolerance is evidence-only -- it never flips the verdict on its own
    # (this module's own disclosed reading of "a cheap early signal").
    tolerant = replace(DEFAULT_CHARTER, rows=RowRule(row_count_tolerance=5))

    def _check_within_tolerance(result: DiffResult) -> None:
        assert result.row_count_within_tolerance is True

    cases.append(
        DiffFixtureCase(
            "rows_row_count_tolerance_recorded_but_key_diff_still_fails",
            "rows", e, c_missing, "FAIL", tolerant, _check_within_tolerance,
        )
    )

    def _check_outside_tolerance(result: DiffResult) -> None:
        assert result.row_count_within_tolerance is False

    strict_tolerance = replace(DEFAULT_CHARTER, rows=RowRule(row_count_tolerance=0))
    cases.append(
        DiffFixtureCase(
            "rows_row_count_outside_zero_tolerance_recorded",
            "rows", e, c_missing, "FAIL", strict_tolerance, _check_outside_tolerance,
        )
    )

    # max_failing_cells truncation -- 10 failing measures, capped to 3.
    many_measures = tuple(_measure(f"M{i}") for i in range(10))
    columns = (_dim("Desk"), *many_measures)
    e_row = ("EMEA", *[100.0 + i for i in range(10)])
    c_row = ("EMEA", *[999.0 + i for i in range(10)])  # every measure fails
    e_many = _rs(columns, (e_row,))
    c_many = _rs(columns, (c_row,))
    capped = replace(DEFAULT_CHARTER, rows=RowRule(max_failing_cells=3))

    def _check_capped_at_three(result: DiffResult) -> None:
        assert len(result.failing_cells) == 3
        assert result.failing_cell_count == 10

    cases.append(
        DiffFixtureCase(
            "rows_max_failing_cells_truncates_to_charter_bound",
            "rows", e_many, c_many, "FAIL", capped, _check_capped_at_three,
        )
    )

    def _check_default_fifty_keeps_all_ten(result: DiffResult) -> None:
        assert len(result.failing_cells) == 10
        assert result.failing_cell_count == 10

    cases.append(
        DiffFixtureCase(
            "rows_default_fifty_does_not_truncate_ten_failures",
            "rows", e_many, c_many, "FAIL", DEFAULT_CHARTER, _check_default_fifty_keeps_all_ten,
        )
    )

    def _check_deterministic_order(result: DiffResult) -> None:
        measures_in_order = [c.measure for c in result.failing_cells]
        assert measures_in_order == sorted(measures_in_order)

    cases.append(
        DiffFixtureCase(
            "rows_failing_cells_sorted_deterministically",
            "rows", e_many, c_many, "FAIL", DEFAULT_CHARTER, _check_deterministic_order,
        )
    )

    # No differences at all: zero missing, zero extra, zero failing cells.
    cases.append(DiffFixtureCase("rows_no_differences_passes", "rows", e, _rs(_GRAIN_MEASURE, e.rows), "PASS"))

    # A completely empty result set on both sides.
    empty = _rs(_GRAIN_MEASURE, ())
    cases.append(DiffFixtureCase("rows_both_empty_passes", "rows", empty, _rs(_GRAIN_MEASURE, ()), "PASS"))

    # Expected has rows, candidate is empty -- every expected key is missing.
    cases.append(DiffFixtureCase("rows_candidate_empty_all_missing", "rows", e, empty, "FAIL"))
    cases.append(DiffFixtureCase("rows_expected_empty_all_extra", "rows", empty, e, "FAIL"))

    for i in range(1, 13):
        n_extra = i
        extra_rows = tuple((f"KEY{j}", float(j)) for j in range(n_extra))
        c_variable = _rs(_GRAIN_MEASURE, e.rows + extra_rows)
        cases.append(
            DiffFixtureCase(f"rows_extra_key_count_{n_extra}", "rows", e, c_variable, "FAIL")
        )
    return cases


# ------------------------------------------------------------------------- sampling (6)


def _sampling_cases() -> list[DiffFixtureCase]:
    """§10.4 stratified sampling is F7.5's own later, unbuilt scope -- these confirm
    today's algorithm always fully compares regardless of `SamplingRule`'s own field
    values, not a real sampling test (this module's own docstring)."""
    cases: list[DiffFixtureCase] = []
    rows = tuple((f"KEY{i}", float(i)) for i in range(20))
    e = _rs(_GRAIN_MEASURE, rows)
    c = _rs(_GRAIN_MEASURE, rows)

    tiny_sample = replace(DEFAULT_CHARTER, sampling=SamplingRule(full_compare_max_rows=1, sample_rows=1))
    cases.append(
        DiffFixtureCase("sampling_small_full_compare_max_rows_still_compares_all", "sampling", e, c, "PASS", tiny_sample)
    )

    c_one_diff = _rs(_GRAIN_MEASURE, (*rows[:19], ("KEY19", 999.0)))
    cases.append(
        DiffFixtureCase(
            "sampling_small_full_compare_max_rows_still_catches_the_one_diff",
            "sampling", e, c_one_diff, "FAIL", tiny_sample,
        )
    )

    huge_sample = replace(DEFAULT_CHARTER, sampling=SamplingRule(full_compare_max_rows=1_000_000))
    cases.append(DiffFixtureCase("sampling_large_full_compare_max_rows_passes", "sampling", e, c, "PASS", huge_sample))

    def _check_all_twenty_compared(result: DiffResult) -> None:
        assert result.compared_keys == 20

    cases.append(
        DiffFixtureCase(
            "sampling_compared_keys_reflects_the_full_set_not_a_sample",
            "sampling", e, c, "PASS", DEFAULT_CHARTER, _check_all_twenty_compared,
        )
    )

    stratify_by_measure = replace(DEFAULT_CHARTER, sampling=SamplingRule(stratify_by="Margin"))
    cases.append(
        DiffFixtureCase("sampling_stratify_by_field_does_not_affect_todays_result", "sampling", e, c, "PASS", stratify_by_measure)
    )
    cases.append(
        DiffFixtureCase(
            "sampling_stratify_by_field_still_catches_a_real_diff",
            "sampling", e, c_one_diff, "FAIL", stratify_by_measure,
        )
    )
    return cases


# --------------------------------------------------------------------- type lattice (16)


def _lattice_cases() -> list[DiffFixtureCase]:
    cases: list[DiffFixtureCase] = []
    numeric_aliases = ["integer", "int", "bigint", "smallint", "decimal", "double", "float", "real"]
    for type_name in numeric_aliases:
        cols = (_dim("Desk"), _measure("M", type_name))
        e = _rs(cols, (("EMEA", 100.0),))
        c = _rs(cols, (("EMEA", 100.0),))
        cases.append(DiffFixtureCase(f"lattice_numeric_alias_{type_name}_passes", "lattice", e, c, "PASS"))

    # Two different numeric-family names on either side both classify "numeric" (the
    # chain's own join).
    e_int = _rs((_dim("Desk"), _measure("M", "integer")), (("EMEA", 100.0),))
    c_double = _rs((_dim("Desk"), _measure("M", "double")), (("EMEA", 100.0),))
    cases.append(DiffFixtureCase("lattice_integer_vs_double_join_is_numeric_pass", "lattice", e_int, c_double, "PASS"))
    c_double_diff = _rs((_dim("Desk"), _measure("M", "double")), (("EMEA", 105.0),))
    cases.append(DiffFixtureCase("lattice_integer_vs_double_join_is_numeric_fail", "lattice", e_int, c_double_diff, "FAIL"))

    date_aliases = ["date", "datetime", "timestamp"]
    for type_name in date_aliases:
        cols = (_dim("D", type_name), _measure("M"))
        e = _rs(cols, ((date(2026, 1, 1), 10.0),))
        c = _rs(cols, ((date(2026, 1, 1), 10.0),))
        cases.append(DiffFixtureCase(f"lattice_date_alias_{type_name}_passes", "lattice", e, c, "PASS"))

    # date vs datetime alias join is "date".
    e_date = _rs((_dim("D", "date"), _measure("M")), ((date(2026, 1, 1), 10.0),))
    c_datetime = _rs((_dim("D", "datetime"), _measure("M")), ((date(2026, 1, 1), 10.0),))
    cases.append(DiffFixtureCase("lattice_date_vs_datetime_join_is_date", "lattice", e_date, c_datetime, "PASS"))

    # Unrecognised type names fall back to "string" -- the lattice's own top element.
    cols_unknown = (_dim("Desk"), _measure("M", "geography"))
    e_unknown = _rs(cols_unknown, (("EMEA", "POINT(0 0)"),))
    c_unknown = _rs(cols_unknown, (("EMEA", "POINT(0 0)"),))
    cases.append(DiffFixtureCase("lattice_unrecognised_type_falls_back_to_string_pass", "lattice", e_unknown, c_unknown, "PASS"))
    c_unknown_diff = _rs(cols_unknown, (("EMEA", "POINT(1 1)"),))
    cases.append(DiffFixtureCase("lattice_unrecognised_type_falls_back_to_string_fail", "lattice", e_unknown, c_unknown_diff, "FAIL"))

    # A numeric/date mismatch collapses to string comparison, per the lattice's own top.
    e_num = _rs((_dim("Desk"), _measure("M", "double")), (("EMEA", 100.0),))
    c_date = _rs((_dim("Desk"), _measure("M", "date")), (("EMEA", 100.0),))
    cases.append(DiffFixtureCase("lattice_numeric_vs_date_collapses_to_string", "lattice", e_num, c_date, "PASS"))
    return cases


# --------------------------------------------------------------------- column mapping (10)


def _column_mapping_cases() -> list[DiffFixtureCase]:
    cases: list[DiffFixtureCase] = []
    full_map = {"Desk": "DeskName", "Margin": "MarginCalc"}

    e = _rs((_dim("Desk"), _measure("Margin")), (("EMEA", 100.0),))
    c_renamed = _rs((_dim("DeskName"), _measure("MarginCalc")), (("EMEA", 100.0),))
    cases.append(
        DiffFixtureCase(
            "column_mapping_renamed_grain_and_measure_pass", "column_mapping", e, c_renamed, "PASS",
            column_target_map=full_map,
        )
    )

    # Without the mapping, the same pair looks like every key is missing (no column
    # named "Desk"/"Margin" exists on the candidate side).
    cases.append(
        DiffFixtureCase("column_mapping_without_map_looks_entirely_missing", "column_mapping", e, c_renamed, "FAIL")
    )

    # A mapping for only one of the two columns.
    c_partial = _rs((_dim("DeskName"), _measure("Margin")), (("EMEA", 100.0),))
    cases.append(
        DiffFixtureCase(
            "column_mapping_partial_map_grain_only", "column_mapping", e, c_partial, "PASS",
            column_target_map={"Desk": "DeskName"},
        )
    )

    e2 = _rs((_dim("Desk"), _measure("Margin")), (("EMEA", 100.0),))
    c_mapped_diff = _rs((_dim("DeskName"), _measure("MarginCalc")), (("EMEA", 999.0),))
    cases.append(
        DiffFixtureCase(
            "column_mapping_mapped_columns_still_catch_a_real_diff", "column_mapping", e2, c_mapped_diff, "FAIL",
            column_target_map=full_map,
        )
    )

    # A mapping that points at a column the candidate does not actually have.
    c_wrong_target = _rs((_dim("SomeOtherName"), _measure("Margin")), (("EMEA", 100.0),))
    cases.append(
        DiffFixtureCase(
            "column_mapping_dangling_target_treated_as_unmapped", "column_mapping", e, c_wrong_target, "FAIL",
            column_target_map=full_map,
        )
    )

    # A mapping on the measure only, grain matches by name already.
    e3 = _rs((_dim("Desk"), _measure("Margin")), (("EMEA", 100.0), ("APAC", 200.0)))
    c_measure_renamed = _rs((_dim("Desk"), _measure("MarginCalc")), (("EMEA", 100.0), ("APAC", 200.0)))
    cases.append(
        DiffFixtureCase(
            "column_mapping_measure_only_rename_passes", "column_mapping", e3, c_measure_renamed, "PASS",
            column_target_map={"Margin": "MarginCalc"},
        )
    )

    # Composite grain, both dimensions renamed.
    composite = (_dim("Desk"), _dim("Region"), _measure("Margin"))
    composite_renamed = (_dim("DeskName"), _dim("RegionName"), _measure("MarginCalc"))
    e4 = _rs(composite, (("EMEA", "North", 100.0),))
    c4 = _rs(composite_renamed, (("EMEA", "North", 100.0),))
    cases.append(
        DiffFixtureCase(
            "column_mapping_composite_grain_both_renamed_pass", "column_mapping", e4, c4, "PASS",
            column_target_map={"Desk": "DeskName", "Region": "RegionName", "Margin": "MarginCalc"},
        )
    )

    # A composite grain with only one of the two dimensions renamed -- the unmapped one
    # still matches because its own name happens to be identical on both sides.
    composite_partial = (_dim("DeskName"), _dim("Region"), _measure("Margin"))
    c5 = _rs(composite_partial, (("EMEA", "North", 100.0),))
    cases.append(
        DiffFixtureCase(
            "column_mapping_composite_grain_one_renamed_pass", "column_mapping", e4, c5, "PASS",
            column_target_map={"Desk": "DeskName"},
        )
    )
    return cases


# ------------------------------------------------------------------- verdict/outcome (14)


def _verdict_cases() -> list[DiffFixtureCase]:
    cases: list[DiffFixtureCase] = []

    e_inconclusive = _rs(_GRAIN_MEASURE, (), outcome=ExecutionOutcome.INCONCLUSIVE, reason="timed out", reason_class=InconclusiveReason.TIMEOUT)
    c_ok = _rs(_GRAIN_MEASURE, (("EMEA", 100.0),))
    cases.append(DiffFixtureCase("verdict_expected_inconclusive_propagates", "verdict", e_inconclusive, c_ok, "INCONCLUSIVE"))

    e_ok = _rs(_GRAIN_MEASURE, (("EMEA", 100.0),))
    c_inconclusive = _rs(_GRAIN_MEASURE, (), outcome=ExecutionOutcome.INCONCLUSIVE, reason="adapter error", reason_class=InconclusiveReason.ADAPTER_ERROR)
    cases.append(DiffFixtureCase("verdict_candidate_inconclusive_propagates", "verdict", e_ok, c_inconclusive, "INCONCLUSIVE"))

    both_inconclusive = _rs(_GRAIN_MEASURE, (), outcome=ExecutionOutcome.INCONCLUSIVE)
    cases.append(DiffFixtureCase("verdict_both_inconclusive_propagates", "verdict", both_inconclusive, both_inconclusive, "INCONCLUSIVE"))

    e_no_grain = _rs((_measure("Margin"),), ((100.0,),))
    c_no_grain = _rs((_measure("Margin"),), ((100.0,),))
    cases.append(DiffFixtureCase("verdict_no_grain_is_inconclusive", "verdict", e_no_grain, c_no_grain, "INCONCLUSIVE"))

    # A clean pass and a clean fail, one more time, at the "verdict" category itself
    # rather than folded into "numeric"/"rows" -- confirms the top-level dispatch.
    e_pass = _rs(_GRAIN_MEASURE, (("EMEA", 100.0), ("APAC", 200.0)))
    c_pass = _rs(_GRAIN_MEASURE, (("EMEA", 100.0), ("APAC", 200.0)))
    cases.append(DiffFixtureCase("verdict_clean_pass", "verdict", e_pass, c_pass, "PASS"))

    c_fail = _rs(_GRAIN_MEASURE, (("EMEA", 100.0), ("APAC", 999.0)))
    cases.append(DiffFixtureCase("verdict_clean_fail_from_cell", "verdict", e_pass, c_fail, "FAIL"))

    c_fail_key = _rs(_GRAIN_MEASURE, (("EMEA", 100.0),))
    cases.append(DiffFixtureCase("verdict_clean_fail_from_key", "verdict", e_pass, c_fail_key, "FAIL"))

    # Both a key difference and a cell difference at once -- still one FAIL, not two.
    c_fail_both = _rs(_GRAIN_MEASURE, (("EMEA", 999.0),))
    cases.append(DiffFixtureCase("verdict_fail_from_both_key_and_cell", "verdict", e_pass, c_fail_both, "FAIL"))

    def _check_reason_mentions_inconclusive_side(result: DiffResult) -> None:
        assert "did not complete" in result.reason

    cases.append(
        DiffFixtureCase(
            "verdict_inconclusive_reason_is_explanatory",
            "verdict", e_inconclusive, c_ok, "INCONCLUSIVE", check=_check_reason_mentions_inconclusive_side,
        )
    )

    # Multiple measures, only one of which fails.
    two_measures = (_dim("Desk"), _measure("A"), _measure("B"))
    e_two = _rs(two_measures, (("EMEA", 1.0, 2.0),))
    c_two_one_fails = _rs(two_measures, (("EMEA", 1.0, 999.0),))
    cases.append(DiffFixtureCase("verdict_one_of_two_measures_fails", "verdict", e_two, c_two_one_fails, "FAIL"))

    def _check_only_measure_b_failed(result: DiffResult) -> None:
        assert [c.measure for c in result.failing_cells] == ["B"]

    cases.append(
        DiffFixtureCase(
            "verdict_only_the_failing_measure_is_reported",
            "verdict", e_two, c_two_one_fails, "FAIL", check=_check_only_measure_b_failed,
        )
    )

    # Multiple grain dimensions (a composite key).
    composite = (_dim("Desk"), _dim("Region"), _measure("Margin"))
    e_composite = _rs(composite, (("EMEA", "North", 100.0), ("EMEA", "South", 200.0)))
    c_composite = _rs(composite, (("EMEA", "North", 100.0), ("EMEA", "South", 200.0)))
    cases.append(DiffFixtureCase("verdict_composite_grain_passes", "verdict", e_composite, c_composite, "PASS"))

    c_composite_partial = _rs(composite, (("EMEA", "North", 100.0),))
    cases.append(DiffFixtureCase("verdict_composite_grain_partial_match_fails", "verdict", e_composite, c_composite_partial, "FAIL"))

    # A larger, more realistic sheet: five dimension values x three measures, all agreeing.
    wide_measures = (_dim("Desk"), _measure("Revenue"), _measure("Cost"), _measure("Margin"))
    wide_rows = tuple((f"Desk{i}", float(i) * 10, float(i) * 4, float(i) * 6) for i in range(5))
    e_wide = _rs(wide_measures, wide_rows)
    c_wide = _rs(wide_measures, wide_rows)
    cases.append(DiffFixtureCase("verdict_wide_sheet_all_agree_passes", "verdict", e_wide, c_wide, "PASS"))

    c_wide_one_off = _rs(wide_measures, (*wide_rows[:4], ("Desk4", 40.0, 16.0, 999.0)))
    cases.append(DiffFixtureCase("verdict_wide_sheet_one_cell_off_fails", "verdict", e_wide, c_wide_one_off, "FAIL"))
    return cases


# ------------------------------------------------------------------------- totals (12)


def _totals_cases() -> list[DiffFixtureCase]:
    """§10.3's own "cheap early signal" -- always computed, never a verdict determinant
    on its own (this module's own disclosed reading, see `diff.py`'s own docstring)."""
    cases: list[DiffFixtureCase] = []

    e = _rs(_GRAIN_MEASURE, (("EMEA", 100.0), ("APAC", 200.0)))
    c = _rs(_GRAIN_MEASURE, (("EMEA", 100.0), ("APAC", 200.0)))

    def _check_totals_match(result: DiffResult) -> None:
        assert len(result.totals) == 1
        assert result.totals[0].result == "PASS"
        assert result.totals[0].expected_total == 300.0
        assert result.totals[0].candidate_total == 300.0

    cases.append(DiffFixtureCase("totals_matching_grand_totals", "totals", e, c, "PASS", check=_check_totals_match))

    c_total_mismatch_but_keys_match = _rs(_GRAIN_MEASURE, (("EMEA", 50.0), ("APAC", 250.0)))

    def _check_totals_still_match_despite_redistribution(result: DiffResult) -> None:
        # 50 + 250 == 100 + 200: totals agree even though individual cells do not.
        assert result.totals[0].expected_total == result.totals[0].candidate_total

    cases.append(
        DiffFixtureCase(
            "totals_can_match_even_when_individual_cells_dont",
            "totals", e, c_total_mismatch_but_keys_match, "FAIL", check=_check_totals_still_match_despite_redistribution,
        )
    )

    c_total_off = _rs(_GRAIN_MEASURE, (("EMEA", 100.0), ("APAC", 200.5)))

    def _check_total_mismatch_recorded_but_verdict_still_from_cells(result: DiffResult) -> None:
        assert result.totals[0].result == "FAIL"

    cases.append(
        DiffFixtureCase(
            "totals_mismatch_recorded_alongside_the_real_cell_failure",
            "totals", e, c_total_off, "FAIL", check=_check_total_mismatch_recorded_but_verdict_still_from_cells,
        )
    )

    # A totals mismatch with NO cell-level failure and NO key difference still passes
    # overall -- confirms totals never independently gates the verdict (only possible
    # if two different rows' worth of measure movement happens to cancel differently
    # per-cell but not in aggregate; constructed directly via unequal per-row values
    # whose grand total nonetheless would only differ if a cell differed too, so this
    # case instead demonstrates the reverse: totals PASS is consistent with, but does
    # not by itself guarantee, a PASS verdict).
    cases.append(
        DiffFixtureCase(
            "totals_pass_alone_does_not_imply_overall_pass",
            "totals", e, _rs(_GRAIN_MEASURE, (("EMEA", 100.0), ("APAC", 200.0), ("APJ", 0.0))), "FAIL",
        )
    )

    # A non-numeric measure is skipped by the totals check entirely.
    string_measure = (_dim("Desk"), _measure("Notes", "string"))
    e_str = _rs(string_measure, (("EMEA", "ok"),))
    c_str = _rs(string_measure, (("EMEA", "ok"),))

    def _check_no_totals_for_string_measure(result: DiffResult) -> None:
        assert result.totals == ()

    cases.append(
        DiffFixtureCase(
            "totals_skipped_for_a_non_numeric_measure", "totals", e_str, c_str, "PASS", check=_check_no_totals_for_string_measure,
        )
    )

    # Multiple numeric measures each get their own totals entry.
    two_measures = (_dim("Desk"), _measure("A"), _measure("B"))
    e_two = _rs(two_measures, (("EMEA", 10.0, 20.0), ("APAC", 30.0, 40.0)))
    c_two = _rs(two_measures, (("EMEA", 10.0, 20.0), ("APAC", 30.0, 40.0)))

    def _check_two_totals_entries(result: DiffResult) -> None:
        assert {t.measure for t in result.totals} == {"A", "B"}

    cases.append(
        DiffFixtureCase("totals_one_entry_per_numeric_measure", "totals", e_two, c_two, "PASS", check=_check_two_totals_entries)
    )

    # Row-count-and-totals together: candidate has fewer rows, both checks reflect it.
    c_fewer_rows = _rs(_GRAIN_MEASURE, (("EMEA", 100.0),))

    def _check_row_count_and_totals_both_reflect_the_shortfall(result: DiffResult) -> None:
        assert result.candidate_row_count < result.expected_row_count
        assert result.totals[0].candidate_total != result.totals[0].expected_total

    cases.append(
        DiffFixtureCase(
            "totals_and_row_count_both_reflect_a_dropped_row",
            "totals", e, c_fewer_rows, "FAIL", check=_check_row_count_and_totals_both_reflect_the_shortfall,
        )
    )

    # Empty result sets: totals is empty, not a crash.
    empty = _rs(_GRAIN_MEASURE, ())

    def _check_empty_totals(result: DiffResult) -> None:
        assert result.totals[0].expected_total is None
        assert result.totals[0].candidate_total is None

    cases.append(
        DiffFixtureCase("totals_both_empty_result_sets", "totals", empty, empty, "PASS", check=_check_empty_totals)
    )

    for i, (e_val, c_val) in enumerate([(1.0, 1.0), (1000.0, 1000.001), (0.0, 0.0)]):
        e_i = _rs(_GRAIN_MEASURE, ((f"KEY{i}", e_val),))
        c_i = _rs(_GRAIN_MEASURE, ((f"KEY{i}", c_val),))
        cases.append(DiffFixtureCase(f"totals_boundary_{i}", "totals", e_i, c_i, "PASS"))
    return cases


# ------------------------------------------------------ declared-but-unconsumed fields (12)


def _unconsumed_fields_cases() -> list[DiffFixtureCase]:
    """`NumericRule.rounding`/`.currency_scale` and `DateRule.timezone`/
    `.fiscal_year_start` are real, declared §4.4 fields `compare_numeric`/`compare_date`
    do not yet read -- confirmed by direct inspection of both functions, not assumed.
    These cases pin today's real behaviour (the field is present on the charter and
    changing it has no effect on the comparison) so a future change that *does* wire
    one of them up fails a test here rather than silently changing behaviour unnoticed
    -- the same "a real, disclosed gap, not a silent one" posture this codebase already
    applies to every other unimplemented charter corner."""
    cases: list[DiffFixtureCase] = []

    half_up = replace(DEFAULT_CHARTER, numeric=NumericRule(rounding="HALF_UP"))
    cases.append(_one_row_case("unconsumed_rounding_half_up_no_effect", "unconsumed", 100.0, 100.0, "PASS", charter=half_up))
    cases.append(_one_row_case("unconsumed_rounding_half_up_still_fails_beyond_epsilon", "unconsumed", 100.0, 105.0, "FAIL", charter=half_up))

    scale_zero = replace(DEFAULT_CHARTER, numeric=NumericRule(currency_scale=0))
    cases.append(_one_row_case("unconsumed_currency_scale_zero_no_effect", "unconsumed", 100.004, 100.0, "PASS", charter=scale_zero))
    scale_six = replace(DEFAULT_CHARTER, numeric=NumericRule(currency_scale=6))
    cases.append(_one_row_case("unconsumed_currency_scale_six_no_effect", "unconsumed", 100.004, 100.0, "PASS", charter=scale_six))

    other_tz = replace(DEFAULT_CHARTER, dates=DateRule(timezone="America/New_York"))
    date_measure = (_dim("D", "date"), _measure("M"))
    e_tz = _rs(date_measure, ((date(2026, 1, 1), 10.0),))
    c_tz = _rs(date_measure, ((date(2026, 1, 1), 10.0),))
    cases.append(DiffFixtureCase("unconsumed_timezone_no_effect_on_match", "unconsumed", e_tz, c_tz, "PASS", other_tz))
    c_tz_diff = _rs(date_measure, ((date(2026, 1, 2), 10.0),))
    cases.append(DiffFixtureCase("unconsumed_timezone_no_effect_on_mismatch", "unconsumed", e_tz, c_tz_diff, "FAIL", other_tz))

    fiscal_april = replace(DEFAULT_CHARTER, dates=DateRule(fiscal_year_start=4))
    cases.append(DiffFixtureCase("unconsumed_fiscal_year_start_no_effect_on_match", "unconsumed", e_tz, c_tz, "PASS", fiscal_april))
    cases.append(DiffFixtureCase("unconsumed_fiscal_year_start_no_effect_on_mismatch", "unconsumed", e_tz, c_tz_diff, "FAIL", fiscal_april))

    collation = replace(DEFAULT_CHARTER, strings=StringRule(collation="fr-FR"))
    cases.append(_one_row_case("unconsumed_collation_no_effect_on_fold", "unconsumed", "EMEA", "emea", "PASS", charter=collation, measure_type="string"))
    cases.append(_one_row_case("unconsumed_collation_no_effect_on_mismatch", "unconsumed", "EMEA", "APAC", "FAIL", charter=collation, measure_type="string"))

    top_n = replace(DEFAULT_CHARTER)  # OrderingRule.top_n_tie_break: no top-N logic exists in diff.py at all
    cases.append(DiffFixtureCase("unconsumed_top_n_tie_break_no_effect_on_match", "unconsumed", e_tz, c_tz, "PASS", top_n))
    cases.append(DiffFixtureCase("unconsumed_top_n_tie_break_no_effect_on_mismatch", "unconsumed", e_tz, c_tz_diff, "FAIL", top_n))
    return cases


# --------------------------------------------------------------------------- all cases


def all_fixture_cases() -> list[DiffFixtureCase]:
    return [
        *_numeric_cases(),
        *_nulls_cases(),
        *_dates_cases(),
        *_strings_cases(),
        *_ordering_cases(),
        *_rows_cases(),
        *_sampling_cases(),
        *_lattice_cases(),
        *_column_mapping_cases(),
        *_verdict_cases(),
        *_totals_cases(),
        *_unconsumed_fields_cases(),
    ]


ALL_CASES: tuple[DiffFixtureCase, ...] = tuple(all_fixture_cases())

__all__ = ["ALL_CASES", "DiffFixtureCase", "all_fixture_cases"]
