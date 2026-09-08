"""§4.4's own charter schema and §10.3's own cell comparators -- a standalone,
dependency-free module.

Split out of `tolerance_charter.py` (S7.1.1) rather than merely called from there: this
module is genuinely pure (stdlib-only) -- the nine rule dataclasses, `ToleranceCharter`
itself, `ToleranceCharterVersion`, and the comparator functions `diff.py`'s own
`compare_cell` dispatches through -- while `tolerance_charter.py` itself imports
`asyncpg` and this platform's own graph/writer machinery at module level for the G1 gate
and the charter store. A regression export (`regression_export.py`, S7.7.1) vendors this
file verbatim into the handover bundle so `run_suite.py` can diff under a real
`ToleranceCharter` standalone, without pulling in either of those. One definition,
imported by both `tolerance_charter.py` and the exported bundle, rather than a second
copy that could drift from it.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from typing import Any


class ToleranceCharterError(Exception):
    """A charter change or gate action does not satisfy the acceptance criteria."""


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")


# ------------------------------------------------------------------------------- schema


@dataclass(frozen=True, slots=True)
class NumericRule:
    abs_epsilon: float = 0.005
    rel_epsilon: float = 1e-6
    rounding: str = "HALF_EVEN"
    currency_scale: int = 2

    def as_dict(self) -> dict[str, Any]:
        return {
            "abs_epsilon": self.abs_epsilon, "rel_epsilon": self.rel_epsilon,
            "rounding": self.rounding, "currency_scale": self.currency_scale,
        }


@dataclass(frozen=True, slots=True)
class NullRule:
    #: "PASS" or "FAIL" — the verdict when the source side is null.
    source_null_vs_target_zero: str = "FAIL"
    source_null_vs_target_blank: str = "PASS"
    empty_string_is_null: bool = True

    def as_dict(self) -> dict[str, Any]:
        return {
            "source_null_vs_target_zero": self.source_null_vs_target_zero,
            "source_null_vs_target_blank": self.source_null_vs_target_blank,
            "empty_string_is_null": self.empty_string_is_null,
        }


@dataclass(frozen=True, slots=True)
class DateRule:
    grain_alignment: str = "TRUNCATE_TO_SOURCE_GRAIN"
    timezone: str = "UTC"
    fiscal_year_start: int = 1

    def as_dict(self) -> dict[str, Any]:
        return {
            "grain_alignment": self.grain_alignment, "timezone": self.timezone,
            "fiscal_year_start": self.fiscal_year_start,
        }


@dataclass(frozen=True, slots=True)
class StringRule:
    trim: bool = True
    case_sensitive: bool = False
    collation: str = "en-US"

    def as_dict(self) -> dict[str, Any]:
        return {"trim": self.trim, "case_sensitive": self.case_sensitive, "collation": self.collation}


@dataclass(frozen=True, slots=True)
class OrderingRule:
    sort_sensitive: bool = False
    top_n_tie_break: str = "SOURCE_ORDER"

    def as_dict(self) -> dict[str, Any]:
        return {"sort_sensitive": self.sort_sensitive, "top_n_tie_break": self.top_n_tie_break}


@dataclass(frozen=True, slots=True)
class RowRule:
    #: "FAIL" or "PASS" — whether a key present on one side only fails the run.
    missing_key: str = "FAIL"
    extra_key: str = "FAIL"
    row_count_tolerance: int = 0
    max_failing_cells: int = 50
    """§10.3's own AC, story S7.4.1: "the first N failing cells (default 50)" in the
    evidence bundle. Homed here, not a new charter block, since it is the same kind of
    row/cell-evidence-scope fact `missing_key`/`extra_key`/`row_count_tolerance` already
    are -- neither §4.4 nor §10.3 names a tenth block for it."""

    def as_dict(self) -> dict[str, Any]:
        return {
            "missing_key": self.missing_key, "extra_key": self.extra_key,
            "row_count_tolerance": self.row_count_tolerance,
            "max_failing_cells": self.max_failing_cells,
        }


@dataclass(frozen=True, slots=True)
class SamplingRule:
    full_compare_max_rows: int = 200_000
    sample_rows: int = 50_000
    stratify_by: str = "grain"

    def as_dict(self) -> dict[str, Any]:
        return {
            "full_compare_max_rows": self.full_compare_max_rows,
            "sample_rows": self.sample_rows, "stratify_by": self.stratify_by,
        }


@dataclass(frozen=True, slots=True)
class ParamRule:
    enumerate_max_values: int = 12
    enumerate_strategy: str = "DEFAULT_PLUS_OBSERVED"

    def as_dict(self) -> dict[str, Any]:
        return {
            "enumerate_max_values": self.enumerate_max_values,
            "enumerate_strategy": self.enumerate_strategy,
        }


@dataclass(frozen=True, slots=True)
class WaiverRule:
    allowed_classes: tuple[str, ...] = ("C4",)
    requires: tuple[str, ...] = ("engineer", "client_owner")
    justification_min_chars: int = 120

    def as_dict(self) -> dict[str, Any]:
        return {
            "allowed_classes": list(self.allowed_classes), "requires": list(self.requires),
            "justification_min_chars": self.justification_min_chars,
        }


@dataclass(frozen=True, slots=True)
class ToleranceCharter:
    """§4.4's own nine blocks, verbatim."""

    numeric: NumericRule = field(default_factory=NumericRule)
    nulls: NullRule = field(default_factory=NullRule)
    dates: DateRule = field(default_factory=DateRule)
    strings: StringRule = field(default_factory=StringRule)
    ordering: OrderingRule = field(default_factory=OrderingRule)
    rows: RowRule = field(default_factory=RowRule)
    sampling: SamplingRule = field(default_factory=SamplingRule)
    params: ParamRule = field(default_factory=ParamRule)
    waiver: WaiverRule = field(default_factory=WaiverRule)

    def as_dict(self) -> dict[str, Any]:
        return {
            "numeric": self.numeric.as_dict(), "nulls": self.nulls.as_dict(),
            "dates": self.dates.as_dict(), "strings": self.strings.as_dict(),
            "ordering": self.ordering.as_dict(), "rows": self.rows.as_dict(),
            "sampling": self.sampling.as_dict(), "params": self.params.as_dict(),
            "waiver": self.waiver.as_dict(),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ToleranceCharter:
        def waiver_from(raw: dict[str, Any]) -> WaiverRule:
            return WaiverRule(
                allowed_classes=tuple(raw.get("allowed_classes", WaiverRule().allowed_classes)),
                requires=tuple(raw.get("requires", WaiverRule().requires)),
                justification_min_chars=raw.get(
                    "justification_min_chars", WaiverRule().justification_min_chars
                ),
            )

        return cls(
            numeric=NumericRule(**data.get("numeric", {})),
            nulls=NullRule(**data.get("nulls", {})),
            dates=DateRule(**data.get("dates", {})),
            strings=StringRule(**data.get("strings", {})),
            ordering=OrderingRule(**data.get("ordering", {})),
            rows=RowRule(**data.get("rows", {})),
            sampling=SamplingRule(**data.get("sampling", {})),
            params=ParamRule(**data.get("params", {})),
            waiver=waiver_from(data.get("waiver", {})),
        )


#: §4.4's own worked example, field-for-field — a real, defensible floor, not a guess.
DEFAULT_CHARTER = ToleranceCharter()

#: Inline explanation of every field's own effect — the console editor's own "explanation
#: of each rule's effect", and this module's own single source of truth for it.
CHARTER_FIELD_METADATA: dict[str, dict[str, str]] = {
    "numeric": {
        "abs_epsilon": "Two numbers pass if they differ by no more than this absolute amount.",
        "rel_epsilon": "Two numbers also pass if they differ by no more than this fraction of the larger magnitude — catches proportional drift on large values that a fixed absolute epsilon would miss.",
        "rounding": "How a value is rounded before comparison (HALF_EVEN avoids systematic bias on .5 boundaries).",
        "currency_scale": "Decimal places a currency value is rounded to before comparison.",
    },
    "nulls": {
        "source_null_vs_target_zero": "Verdict when the source is null and the target is zero.",
        "source_null_vs_target_blank": "Verdict when the source is null and the target is blank/empty.",
        "empty_string_is_null": "Treat an empty string as null before every other null rule is applied.",
    },
    "dates": {
        "grain_alignment": "How a date is truncated to the case's own grain before comparison.",
        "timezone": "The timezone both sides are normalised to before comparing a date/time value.",
        "fiscal_year_start": "The calendar month (1-12) a fiscal year begins in, for any fiscal-grain comparison.",
    },
    "strings": {
        "trim": "Strip leading/trailing whitespace from both sides before comparing.",
        "case_sensitive": "Whether case differences fail the comparison.",
        "collation": "The collation used to compare and sort strings.",
    },
    "ordering": {
        "sort_sensitive": "Whether row order itself is part of what is compared.",
        "top_n_tie_break": "How a tie at the boundary of a top-N result is resolved.",
    },
    "rows": {
        "missing_key": "Verdict when a grain key present on the source side is absent on the target.",
        "extra_key": "Verdict when a grain key present on the target side is absent on the source.",
        "row_count_tolerance": "How many rows the two sides' row counts may differ by and still pass.",
        "max_failing_cells": "How many failing cells a FAIL verdict's evidence bundle keeps, first N.",
    },
    "sampling": {
        "full_compare_max_rows": "Below this row count, every row is compared.",
        "sample_rows": "Above the full-compare threshold, this many rows are sampled instead.",
        "stratify_by": "The field a sample is stratified by, so no one grain value dominates it.",
    },
    "params": {
        "enumerate_max_values": "The most parameter-value combinations a case derivation will enumerate.",
        "enumerate_strategy": "Which combinations are chosen when there are more than the maximum.",
    },
    "waiver": {
        "allowed_classes": "Which failure classes may ever be waived rather than fixed.",
        "requires": "Which parties must all sign a waiver before it is accepted.",
        "justification_min_chars": "The minimum length a waiver's own written justification must have.",
    },
}


# ------------------------------------------------------------------------------- versioning


@dataclass(frozen=True, slots=True)
class ToleranceCharterVersion:
    version: int
    charter: ToleranceCharter
    updated_by: str
    updated_at: str | None

    def as_dict(self) -> dict[str, Any]:
        return {
            "version": self.version, "charter": self.charter.as_dict(),
            "updated_by": self.updated_by, "updated_at": self.updated_at,
        }


DEFAULT_VERSION = ToleranceCharterVersion(
    version=0, charter=DEFAULT_CHARTER, updated_by="system", updated_at=None
)


# ---------------------------------------------------------------------------- comparators


@dataclass(frozen=True, slots=True)
class CellComparison:
    result: str
    reason: str


def compare_numeric(expected: float | None, candidate: float | None, rule: NumericRule) -> CellComparison:
    """§4.4's own numeric rule: pass on either an absolute or a relative epsilon match."""
    if expected is None or candidate is None:
        return CellComparison("FAIL", "a numeric comparison needs a value on both sides")
    diff = abs(expected - candidate)
    if diff <= rule.abs_epsilon:
        return CellComparison("PASS", f"within abs_epsilon ({diff:.6g} <= {rule.abs_epsilon})")
    denominator = max(abs(expected), abs(candidate))
    if denominator and diff / denominator <= rule.rel_epsilon:
        return CellComparison("PASS", f"within rel_epsilon ({diff / denominator:.6g} <= {rule.rel_epsilon})")
    return CellComparison(
        "FAIL",
        f"exceeds both abs_epsilon={rule.abs_epsilon} and rel_epsilon={rule.rel_epsilon} (diff {diff:.6g})",
    )


def compare_null(expected: Any, candidate: Any, rule: NullRule) -> CellComparison | None:
    """The null-comparison matrix. Returns ``None`` when neither side is null, meaning
    "not a null case — compare normally" (numeric/string/date, whichever the cell is).

    ``empty_string_is_null`` and ``source_null_vs_target_blank`` are deliberately
    independent: the first decides whether an empty string counts as null *at all*
    (on either side); the second only ever fires for a target that is literally an
    empty string while the source is null and empty strings are *not* being treated as
    null — the one case ``empty_string_is_null=True`` would otherwise absorb into "both
    sides are null" before this rule ever got a chance to apply."""

    def is_null(value: Any) -> bool:
        return value is None or (rule.empty_string_is_null and value == "")

    if not is_null(expected):
        if is_null(candidate):
            return CellComparison("FAIL", "target is null, source has a value")
        return None

    if candidate is None or (rule.empty_string_is_null and candidate == ""):
        return CellComparison("PASS", "both sides are null")
    if candidate == 0:
        return CellComparison(rule.source_null_vs_target_zero, "source is null, target is zero")
    if candidate == "":
        return CellComparison(rule.source_null_vs_target_blank, "source is null, target is blank")
    return CellComparison("FAIL", "source is null, target has an unmatched value")


def compare_string(expected: Any, candidate: Any, rule: StringRule) -> CellComparison:
    e, c = str(expected), str(candidate)
    if rule.trim:
        e, c = e.strip(), c.strip()
    if not rule.case_sensitive:
        e, c = e.casefold(), c.casefold()
    if e == c:
        return CellComparison("PASS", "equal")
    return CellComparison("FAIL", f"{e!r} != {c!r}")


def _coerce_date(value: Any) -> date | None:
    if value is None:
        return None
    if isinstance(value, date):  # datetime is-a date; caught here too
        return value
    if isinstance(value, str):
        try:
            return datetime.fromisoformat(value)
        except ValueError:
            return None
    return None


def compare_date(expected: Any, candidate: Any, rule: DateRule) -> CellComparison:
    """§10.3: "dates truncated to the source grain" then "pass on normalised equality"
    (story S7.4.1). ``TRUNCATE_TO_SOURCE_GRAIN`` reduces the *candidate* to whatever
    grain the *source* (``expected``) value itself carries: a plain-date source
    truncates a datetime candidate down to its own date; a source that already carries
    a time component is compared at full precision, so a date-only candidate correctly
    fails as a genuine grain mismatch rather than being silently widened to match."""
    e, c = _coerce_date(expected), _coerce_date(candidate)
    if e is None or c is None:
        return CellComparison("FAIL", "a date comparison needs a parseable value on both sides")
    if rule.grain_alignment == "TRUNCATE_TO_SOURCE_GRAIN" and not isinstance(e, datetime) and isinstance(c, datetime):
        c = c.date()
    if e == c:
        return CellComparison("PASS", "equal after truncation to the source grain")
    return CellComparison("FAIL", f"{e} != {c}")


def compare_cell(
    kind: str, expected: Any, candidate: Any, charter: ToleranceCharter
) -> CellComparison:
    """One cell, one charter, one verdict — the real logic backing both the console's own
    inline explanation and `simulate`'s own recompute, and (story S7.4.1) `diff.py`'s own
    real §10.3 cell comparison. ``kind`` is ``"numeric"``, ``"string"`` or ``"date"``
    (any kind is first checked against the null matrix, since a null can appear in any
    of the three alike)."""
    null_verdict = compare_null(expected, candidate, charter.nulls)
    if null_verdict is not None:
        return null_verdict
    if kind == "numeric":
        return compare_numeric(expected, candidate, charter.numeric)
    if kind == "string":
        return compare_string(expected, candidate, charter.strings)
    if kind == "date":
        return compare_date(expected, candidate, charter.dates)
    return CellComparison("FAIL", f"unrecognised cell kind {kind!r}")


__all__ = [
    "CHARTER_FIELD_METADATA",
    "DEFAULT_CHARTER",
    "DEFAULT_VERSION",
    "CellComparison",
    "DateRule",
    "NullRule",
    "NumericRule",
    "OrderingRule",
    "ParamRule",
    "RowRule",
    "SamplingRule",
    "StringRule",
    "ToleranceCharter",
    "ToleranceCharterError",
    "ToleranceCharterVersion",
    "WaiverRule",
    "compare_cell",
    "compare_date",
    "compare_null",
    "compare_numeric",
    "compare_string",
]
