"""The §10.3 diff algorithm -- story S7.4.1, closing F7.4.

    "As a parity engineer, I want the diff algorithm from §10.3 implemented exactly and
    tested against a fixture set, so that every verdict is explainable in terms of the
    charter.

    Acceptance criteria:
    - Normalisation: column mapping via MAPS_TO, type coercion to the lattice, date
      truncation, string folding, null canonicalisation; key by grain tuple
    - Key-set comparison, cell comparison with numeric epsilon per charter, row count
      and totals check; verdict PASS / FAIL / INCONCLUSIVE with failing cells (first N,
      default 50) and the delta
    - Fixture set of 200 hand-verified pairs covering each charter rule; CI runs them
      on every change
    - Evidence bundle per run: charter version, both queries, both result hashes, key
      differences, failing cells, timings"

§10.3 itself, verbatim: *"Both result sets are normalised under the charter before
comparison: column names mapped through MAPS_TO edges; types coerced to a common
lattice (integer ⊂ decimal ⊂ double; date ⊂ datetime; everything ⊂ string); dates
truncated to the source grain; strings trimmed and case-folded if the charter says so;
nulls canonicalised per the charter's null rules. Rows are keyed by the grain tuple.
Key set comparison. Missing keys (in expected, not candidate) and extra keys (in
candidate, not expected) are collected. Either is a FAIL under the default charter,
with the keys listed in evidence. Cell comparison. For each shared key and each
measure: numeric cells pass if |e-c| <= abs_epsilon or |e-c|/max(|e|,|c|) <=
rel_epsilon; string and date cells pass on normalised equality; null pairs pass or fail
per the charter's null matrix. Row-count and total check. Row counts compared under
row_count_tolerance; grand totals per measure recomputed on both sides and compared
under the numeric rule as a cheap early signal. Verdict. PASS if no key differences and
no failing cells; FAIL otherwise; INCONCLUSIVE if either execution did not complete or
the sample could not be stratified."*

**This module is pure -- no database, no artefact store.** It takes two already-loaded
`astra_adapter.ResultSet`s and a `ToleranceCharter` and returns a `DiffResult`; nothing
here awaits anything. That is deliberate: it is what lets the 200-case fixture set
(`tests/diff_fixtures.py`) exercise the real algorithm directly, at unit-test speed,
with no Postgres involved -- the graph-coupled parts (reading a case's own stored
Parquet artefacts, resolving a real `Field -> ModelTable` `MAPS_TO` binding, writing the
`ParityRun`/`Verdict` nodes and the evidence-bundle artefact) live in `verdicts.py`
instead, which calls this module's own `diff_result_sets` as its one pure step.

**The type lattice is exactly the spec's own three chains, nothing invented beyond
alias recognition.** §10.3 names three families and says where each one collapses:
integer/decimal/double all become one "numeric" comparison; date/datetime both become
one "date" comparison; anything else -- including a numeric-vs-date mismatch, which the
lattice gives no shared class for -- collapses to "string" (`"everything ⊂ string"` is
read literally: string is the lattice's own top element, the fallback whenever the two
sides do not already agree on a family). `classify_type` recognises a broad, disclosed
alias list per family (`int`/`bigint`/`smallint` alongside `integer`, `real`/`float`
alongside `double`, `timestamp`/`time` alongside `datetime`) since neither the source
adapter's own type strings nor a Parquet-derived type name are standardised anywhere in
this codebase; an unrecognised name is classified `"string"`, the same safe fallback.

**Column mapping via MAPS_TO is a parameter here, not a query this module runs.**
`diff_result_sets` takes an already-resolved `column_target_map: Mapping[str, str]`
(expected column name -> candidate column name) built by `verdicts.py` the identical
way `case_execution._table_map_for_sheet` already resolves a real `Field -> ModelTable`
binding for DAX table-qualification -- honestly empty in every real deployment today
(the same already-disclosed binding gap this codebase has found repeatedly), so an
unmapped column falls back to its own source name, unchanged.

**Date normalisation for *keying* is a disclosed simplification of `compare_date`'s own
asymmetric "truncate to the source's grain" rule.** Cell comparison keeps the full,
asymmetric rule (`tolerance_charter.compare_date`): the source's own grain is
authoritative, and a candidate with finer precision is truncated down to it. Building a
*key* from a grain column happens independently on each side, before the two sides are
ever paired up, so there is no "source" to truncate "to" yet -- both sides are
truncated to date-only whenever either one carries a `datetime`, which is the reading
that lets a midnight-timestamped grain value on one side still match a plain date on
the other. Disclosed as a real, if narrow, difference from the cell-level rule.

**The row-count-and-totals check is evidence, not a third verdict condition.** §10.3's
own "Verdict" bullet names exactly two conditions -- "PASS if no key differences and no
failing cells; FAIL otherwise" -- and calls the row-count/totals check "a cheap early
signal" in the bullet immediately above it, language that describes a diagnostic, not a
gate. Implemented literally: `DiffResult.row_count_within_tolerance` and `.totals` are
always computed and always included in the evidence bundle, but never independently
flip an otherwise-PASS verdict to FAIL. This is the same "spec wins, implemented
exactly as written" discipline this codebase has already applied to a disagreement
between the spec's own wording and a looser paraphrase.

**`RowRule.max_failing_cells` (§10.3's own "first N failing cells, default 50") lives on
the charter, not a module constant** -- see `tolerance_charter.py`'s own docstring for
why. Failing cells are sorted by `(grain_key, measure)` before truncation, so "first N"
is deterministic and reproducible across runs of the identical two result sets, not an
accident of row order.
"""

from __future__ import annotations

from collections.abc import Hashable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any, cast

from astra_adapter import Column, ExecutionOutcome, ResultSet

from .tolerance_charter import ToleranceCharter, compare_cell

#: §10.3's own three lattice chains, collapsed to the family `compare_cell` dispatches
#: on. Anything not listed here is classified "string" -- the lattice's own top element.
_NUMERIC_TYPE_NAMES = frozenset(
    {"integer", "int", "bigint", "smallint", "tinyint", "decimal", "numeric", "double", "float", "real"}
)
_DATE_TYPE_NAMES = frozenset({"date", "datetime", "timestamp", "time"})


def classify_type(type_name: str) -> str:
    """A `Column.type` string -> one of ``"numeric"``, ``"date"``, ``"string"`` (§10.3's
    own type lattice). Case- and whitespace-insensitive; unrecognised names fall back to
    ``"string"``, the lattice's own safe top element -- see this module's own docstring."""
    normalised = type_name.strip().lower()
    if normalised in _NUMERIC_TYPE_NAMES:
        return "numeric"
    if normalised in _DATE_TYPE_NAMES:
        return "date"
    return "string"


def join_kind(expected_kind: str, candidate_kind: str) -> str:
    """The lattice's own join of two classified kinds: identical families stay that
    family; anything else collapses to ``"string"`` (`"everything ⊂ string"`)."""
    return expected_kind if expected_kind == candidate_kind else "string"


def _is_null(value: Any, charter: ToleranceCharter) -> bool:
    return value is None or (charter.nulls.empty_string_is_null and value == "")


def _key_component(value: Any, kind: str, charter: ToleranceCharter) -> Hashable:
    """One grain value, normalised for hashing -- not `compare_date`'s own asymmetric
    per-pair rule, see this module's own docstring."""
    if _is_null(value, charter):
        return None
    if kind == "date":
        if isinstance(value, datetime):
            return value.date()
        if isinstance(value, date):
            return value
        if isinstance(value, str):
            try:
                parsed = datetime.fromisoformat(value)
            except ValueError:
                return cast(Hashable, value)
            return parsed.date()
        return cast(Hashable, value)
    if kind == "string":
        text = str(value)
        if charter.strings.trim:
            text = text.strip()
        if not charter.strings.case_sensitive:
            text = text.casefold()
        return text
    return cast(Hashable, value)


@dataclass(frozen=True, slots=True)
class FailingCell:
    """One failing measure cell, the evidence bundle's own unit (§10.3)."""

    grain_key: tuple[Any, ...]
    measure: str
    kind: str
    expected: Any
    candidate: Any
    delta: float | None
    reason: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "grain_key": list(self.grain_key), "measure": self.measure, "kind": self.kind,
            "expected": self.expected, "candidate": self.candidate, "delta": self.delta,
            "reason": self.reason,
        }


@dataclass(frozen=True, slots=True)
class TotalCheck:
    """One measure's own grand-total comparison -- §10.3's own "cheap early signal"."""

    measure: str
    expected_total: float | None
    candidate_total: float | None
    result: str
    reason: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "measure": self.measure, "expected_total": self.expected_total,
            "candidate_total": self.candidate_total, "result": self.result, "reason": self.reason,
        }


@dataclass(frozen=True, slots=True)
class DiffResult:
    """§10.3's own verdict, plus everything the AC's own evidence bundle needs."""

    result: str
    """``PASS``, ``FAIL`` or ``INCONCLUSIVE``."""

    reason: str
    missing_keys: tuple[tuple[Any, ...], ...] = ()
    extra_keys: tuple[tuple[Any, ...], ...] = ()
    failing_cells: tuple[FailingCell, ...] = ()
    failing_cell_count: int = 0
    """The true count before truncation to `RowRule.max_failing_cells` -- distinct from
    ``len(failing_cells)``, so evidence never silently understates how much failed."""

    compared_keys: int = 0
    expected_row_count: int = 0
    candidate_row_count: int = 0
    row_count_within_tolerance: bool = True
    totals: tuple[TotalCheck, ...] = ()

    def as_dict(self) -> dict[str, Any]:
        return {
            "result": self.result, "reason": self.reason,
            "missing_keys": [list(k) for k in self.missing_keys],
            "extra_keys": [list(k) for k in self.extra_keys],
            "failing_cells": [c.as_dict() for c in self.failing_cells],
            "failing_cell_count": self.failing_cell_count,
            "compared_keys": self.compared_keys,
            "expected_row_count": self.expected_row_count,
            "candidate_row_count": self.candidate_row_count,
            "row_count_within_tolerance": self.row_count_within_tolerance,
            "totals": [t.as_dict() for t in self.totals],
        }


def _incomplete(expected: ResultSet, candidate: ResultSet) -> str | None:
    if expected.outcome is not ExecutionOutcome.OK:
        return f"expected side did not complete: {expected.reason or expected.outcome.value}"
    if candidate.outcome is not ExecutionOutcome.OK:
        return f"candidate side did not complete: {candidate.reason or candidate.outcome.value}"
    return None


def _column_kind_map(
    expected_columns: Sequence[Column], candidate_by_name: Mapping[str, Column], column_target_map: Mapping[str, str]
) -> dict[str, str | None]:
    """name (on the expected side) -> joined kind, or ``None`` when no candidate column
    matches after mapping -- a real problem, recorded per-cell as a FAIL, never silently
    skipped (see this module's own docstring)."""
    kinds: dict[str, str | None] = {}
    for column in expected_columns:
        candidate_name = column_target_map.get(column.name, column.name)
        candidate_column = candidate_by_name.get(candidate_name)
        if candidate_column is None:
            kinds[column.name] = None
            continue
        kinds[column.name] = join_kind(classify_type(column.type), classify_type(candidate_column.type))
    return kinds


def _grand_total(rows: Sequence[tuple[Any, ...]], index: int) -> float | None:
    total = 0.0
    saw_value = False
    for row in rows:
        value = row[index] if index < len(row) else None
        if value is None:
            continue
        try:
            total += float(value)
        except (TypeError, ValueError):
            return None
        saw_value = True
    return total if saw_value else None


def diff_result_sets(
    expected: ResultSet,
    candidate: ResultSet,
    charter: ToleranceCharter,
    *,
    column_target_map: Mapping[str, str] | None = None,
) -> DiffResult:
    """§10.3, implemented exactly -- see this module's own docstring for the disclosed
    reading of the row-count/totals bullet and the key-normalisation simplification."""
    column_target_map = column_target_map or {}

    incomplete = _incomplete(expected, candidate)
    if incomplete is not None:
        return DiffResult(result="INCONCLUSIVE", reason=incomplete)

    grain = expected.grain
    measures = expected.measures
    if not grain:
        return DiffResult(result="INCONCLUSIVE", reason="the expected side has no grain to key rows by")

    candidate_by_name = {column.name: column for column in candidate.columns}
    candidate_index_by_name = {column.name: i for i, column in enumerate(candidate.columns)}
    expected_index_by_name = {column.name: i for i, column in enumerate(expected.columns)}
    kind_by_name = _column_kind_map(expected.columns, candidate_by_name, column_target_map)

    grain_indices = [expected_index_by_name[name] for name in grain]
    candidate_grain_indices: dict[str, int | None] = {
        name: candidate_index_by_name.get(column_target_map.get(name, name)) for name in grain
    }

    def key_of(row: tuple[Any, ...], indices: Sequence[int | None], names: Sequence[str]) -> tuple[Any, ...] | None:
        values: list[Hashable] = []
        for index, name in zip(indices, names, strict=True):
            if index is None:
                return None
            kind = kind_by_name.get(name) or "string"
            values.append(_key_component(row[index], kind, charter))
        return tuple(values)

    expected_by_key: dict[tuple[Any, ...], tuple[Any, ...]] = {}
    for row in expected.rows:
        key = key_of(row, grain_indices, grain)
        if key is not None:
            expected_by_key[key] = row

    candidate_grain_index_list = [candidate_grain_indices[name] for name in grain]
    candidate_by_key: dict[tuple[Any, ...], tuple[Any, ...]] = {}
    for row in candidate.rows:
        key = key_of(row, candidate_grain_index_list, grain)
        if key is not None:
            candidate_by_key[key] = row

    expected_keys = set(expected_by_key)
    candidate_keys = set(candidate_by_key)
    missing_keys = tuple(sorted(expected_keys - candidate_keys, key=repr))
    extra_keys = tuple(sorted(candidate_keys - expected_keys, key=repr))
    shared_keys = expected_keys & candidate_keys

    measure_expected_index = {name: expected_index_by_name[name] for name in measures}
    measure_candidate_index = {
        name: candidate_index_by_name[column_target_map.get(name, name)]
        for name in measures
        if column_target_map.get(name, name) in candidate_index_by_name
    }

    failing_cells: list[FailingCell] = []
    for key in shared_keys:
        expected_row = expected_by_key[key]
        candidate_row = candidate_by_key[key]
        for measure in measures:
            e_index = measure_expected_index.get(measure)
            c_index = measure_candidate_index.get(measure)
            e_value = expected_row[e_index] if e_index is not None else None
            if c_index is None:
                failing_cells.append(
                    FailingCell(key, measure, "string", e_value, None, None, "no matching candidate column")
                )
                continue
            c_value = candidate_row[c_index]
            kind = kind_by_name.get(measure) or "string"
            comparison = compare_cell(kind, e_value, c_value, charter)
            if comparison.result == "FAIL":
                delta = None
                if kind == "numeric" and isinstance(e_value, int | float) and isinstance(c_value, int | float):
                    delta = float(c_value) - float(e_value)
                failing_cells.append(FailingCell(key, measure, kind, e_value, c_value, delta, comparison.reason))

    failing_cells.sort(key=lambda c: (repr(c.grain_key), c.measure))
    failing_cell_count = len(failing_cells)
    kept_cells = tuple(failing_cells[: charter.rows.max_failing_cells])

    expected_rows = len(expected.rows)
    candidate_rows = len(candidate.rows)
    row_count_within_tolerance = abs(expected_rows - candidate_rows) <= charter.rows.row_count_tolerance

    totals: list[TotalCheck] = []
    for measure in measures:
        e_index = measure_expected_index.get(measure)
        c_index = measure_candidate_index.get(measure)
        if e_index is None or c_index is None or (kind_by_name.get(measure) or "string") != "numeric":
            continue
        expected_total = _grand_total(expected.rows, e_index)
        candidate_total = _grand_total(candidate.rows, c_index)
        comparison = compare_cell("numeric", expected_total, candidate_total, charter)
        totals.append(TotalCheck(measure, expected_total, candidate_total, comparison.result, comparison.reason))

    key_verdicts = []
    if missing_keys:
        key_verdicts.append(charter.rows.missing_key)
    if extra_keys:
        key_verdicts.append(charter.rows.extra_key)
    key_check_failed = "FAIL" in key_verdicts

    if key_check_failed or failing_cell_count:
        result, reason = "FAIL", "key differences and/or failing cells; see evidence"
    else:
        result, reason = "PASS", "no key differences and no failing cells"

    return DiffResult(
        result=result, reason=reason, missing_keys=missing_keys, extra_keys=extra_keys,
        failing_cells=kept_cells, failing_cell_count=failing_cell_count, compared_keys=len(shared_keys),
        expected_row_count=expected_rows, candidate_row_count=candidate_rows,
        row_count_within_tolerance=row_count_within_tolerance, totals=tuple(totals),
    )


__all__ = [
    "DiffResult",
    "FailingCell",
    "TotalCheck",
    "classify_type",
    "diff_result_sets",
    "join_kind",
]
