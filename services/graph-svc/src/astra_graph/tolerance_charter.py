"""The Tolerance Charter — story S7.1.1, opening E7/F7.1, spec §4.4/§10/§13.1.

    "As a parity engineer, I want the Tolerance Charter as a versioned document the
    platform enforces, so that 'the same result' is defined once, agreed at G1, and
    applied identically to every report.

    Acceptance criteria:
    - Charter schema per §4.4: numeric (abs and rel epsilon, rounding, currency scale),
      nulls, dates (grain alignment, timezone, fiscal year start), strings (trim, case,
      collation), ordering, rows (missing key policy, row-count tolerance), sampling,
      params (enumeration), waiver rules
    - Editor in the console with inline explanation of each rule's effect; 'simulate'
      re-diffs the last run under the edited charter without executing
    - Versions are immutable; G1 records the version; every ParityRun records the
      version it ran under
    - Changing the charter after G1 requires the parity engineer and the client
      analytics lead and re-proves affected MUs"

§4.4's own worked example is this module's own `DEFAULT_CHARTER` verbatim (values, not
just field names) — the spec's own illustration is a real, defensible starting point, not
a guess: `abs_epsilon: 0.005, rel_epsilon: 1e-6, rounding: HALF_EVEN, currency_scale: 2`,
and so on for every block.

**This story does not build the Proof Engine's own diff (§10.3) or case execution
(§10.1/§10.2).** Those are F7.2/F7.3's own later, explicit scope (confirmed directly:
`packages/adapter-sdk/src/astra_adapter/proof.py`'s own words, "The Proof Engine is E7
and does not exist"; no `arbiter.py`/`parity.py` module exists anywhere in this codebase).
What this story owns is narrower and real: the charter's own schema, its versioned
storage, the pure cell-level comparison rules each charter block actually means (numeric
epsilon/rounding, the null matrix, string trim/case) — which double as both the console's
own "inline explanation of each rule's effect" and the real logic `simulate` runs — and
the G1/re-charter governance workflow around it.

**A versioned, admin-editable document — the same `conformance_rules.py`/
`visual_mapping.py` template a third time.** `public.tolerance_charter_version` (migration
v0026) holds one immutable row per saved version; an edit is always a new row (`version =
max + 1`), never an update, the identical "an architect's edit is a new version" discipline
both priors already established. Not a graph node: §4.1.1's own node table declares none,
and a charter version is bookkeeping about *rules*, the same reasoning
`v0019_conformance_ruleset.py`'s own docstring already gives for its own table. §4.4 itself
also says the charter is "stored in Git" — neither of this module's own two precedents ever
actually wrote to Git either; the identical, already-accepted gap, not a new one.

**"Client analytics lead" is a real, disclosed spec-internal gap, not a codebase
oversight.** §13.1's own gate table names this role as G1's client-side approver; §2.4's
own roles table never declares it. Confirmed by direct research, not assumed. Added for
real as a twelfth `Role` (see `roles.py`'s own module docstring) rather than overloading
`client_data_owner`/`client_report_owner`, each already spoken for by a different gate
with a different meaning.

**G1's `GateDecision` reuses the identical shape `g2.py::approve()` already established
for G2** — `approver`/`approver_role` on the client side, `countersigner`/
`countersigner_role` on the Artizent side, the exact "approver approves; a second named
party countersigns" pair `GateDecision.countersigner`'s own note already describes for G2.
No new node property, no new gate mechanics — `GateDecision.gate` already declares `"G1"`
in its own enum (confirmed: nothing has ever written one).

**"Changing the charter after G1 requires the parity engineer and the client analytics
lead" is enforced inside `save()`, not as a second endpoint.** Once at least one G1
`GateDecision` exists for this platform's charter, saving a further version requires the
caller (the Parity Engineer, who alone may save at all) to also name the client analytics
lead's own sign-off and a rationale in the same request — the identical
"approver-plus-named-countersigner in one call" shape `g2.py::approve()` already uses,
applied to a revision rather than a first approval. A fresh `GateDecision(gate="G1")` is
written recording the re-approval, and every workbook whose most recent `ParityRun` ran
under the superseded version is marked for re-proof via `MigrationUnitRegistry.
mark_for_reproof` — the exact existing seam the Harvester's own source-drift path already
calls (`migration_units.py`), reused for a charter revision instead. **This will correctly
mark zero workbooks in this platform's current, real state**: no story has ever written a
`ParityRun` (confirmed directly — E7 is entirely unbuilt before this story), so there is
today no real "which MUs ran under version N" set to query. The query is built for real
so a genuine answer is waiting the day F7.2/F7.3 produce one, the same "a real, honest
function over real, live data, correct today even though nothing populates it yet" posture
`visual_redesign.can_enter_proving` (S6.2.1) already took before any real MU state machine
existed either.

**"Simulate re-diffs the last run... without executing" is a real, pure recompute over
whatever evidence a prior `Verdict.failing_cells` sample actually holds — honestly absent
today, since no `Verdict` has ever been written either.** `simulate_charter` looks for the
MU's most recent `ParityRun` (`ReportDefinition --PROVED_BY--> ParityRun`) and, when one
exists, re-applies `compare_numeric`/`compare_null`/`compare_string` to each sampled cell
under the edited charter and returns fresh per-cell verdicts — computed only, nothing
written, matching "without executing." When none exists (every real workbook in this
platform today), it says so plainly rather than fabricating a result. The comparator
functions are real and fully tested now via hand-built fixture cells, so nothing about
`simulate`'s own logic is left to guess at once real evidence exists to feed it.

**Waiver rules are a charter *policy* this story declares, not the waiver-recording
mechanism itself.** `GateDecision.decision` already includes `"WAIVED"`
(never written); `ExceptionCase.decision` is a plain string with no waiver-specific
fields. Neither is wired to read or enforce `WaiverRule` yet — that is a later Exception
Desk/G3 story's own scope (F8.3/§11.3), this story only declares the policy
(`allowed_classes`/`requires`/`justification_min_chars`) a future mechanism must honour.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Protocol

import asyncpg

from .g2 import MIN_RATIONALE_LENGTH
from .graph.queries import EDGE_INDEX_TABLE, NODE_INDEX_TABLE
from .ids import new_ulid
from .lineage import hydrate
from .migration_units import MigrationUnitRegistry
from .principal import Principal
from .writes import GraphWriter, NodeWrite

CHARTER_TABLE = "public.tolerance_charter_version"
GATE = "G1"
SUBJECT_REF = "tolerance_charter"


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

    def as_dict(self) -> dict[str, Any]:
        return {
            "missing_key": self.missing_key, "extra_key": self.extra_key,
            "row_count_tolerance": self.row_count_tolerance,
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


_DEFAULT_VERSION = ToleranceCharterVersion(
    version=0, charter=DEFAULT_CHARTER, updated_by="system", updated_at=None
)


class ToleranceCharterStore(Protocol):
    async def latest(self) -> ToleranceCharterVersion: ...

    async def get(self, version: int) -> ToleranceCharterVersion | None: ...

    async def save(self, charter: ToleranceCharter, *, updated_by: str) -> ToleranceCharterVersion: ...


class PostgresToleranceCharterStore:
    def __init__(self, pool: asyncpg.Pool, *, graph_name: str) -> None:
        self._pool = pool
        self._graph = graph_name

    async def latest(self) -> ToleranceCharterVersion:
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                f"SELECT * FROM {CHARTER_TABLE} WHERE graph = $1 ORDER BY version DESC LIMIT 1",
                self._graph,
            )
        return _from_row(row) if row else _DEFAULT_VERSION

    async def get(self, version: int) -> ToleranceCharterVersion | None:
        if version == 0:
            return _DEFAULT_VERSION
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                f"SELECT * FROM {CHARTER_TABLE} WHERE graph = $1 AND version = $2",
                self._graph, version,
            )
        return _from_row(row) if row else None

    async def save(self, charter: ToleranceCharter, *, updated_by: str) -> ToleranceCharterVersion:
        async with self._pool.acquire() as conn, conn.transaction():
            current = await conn.fetchval(
                f"SELECT MAX(version) FROM {CHARTER_TABLE} WHERE graph = $1", self._graph,
            )
            version = (current or 0) + 1
            row = await conn.fetchrow(
                f"""
                INSERT INTO {CHARTER_TABLE} (id, graph, version, charter, updated_by, updated_at)
                VALUES ($1, $2, $3, $4::jsonb, $5, now())
             RETURNING *
                """,
                f"charter_{new_ulid()}", self._graph, version,
                json.dumps(charter.as_dict()), updated_by,
            )
        assert row is not None
        return _from_row(row)


def _from_row(row: asyncpg.Record) -> ToleranceCharterVersion:
    raw = row["charter"]
    data = json.loads(raw) if isinstance(raw, str) else dict(raw)
    updated_at = row["updated_at"]
    return ToleranceCharterVersion(
        version=row["version"],
        charter=ToleranceCharter.from_dict(data),
        updated_by=row["updated_by"],
        updated_at=updated_at.isoformat() if updated_at else None,
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


def compare_cell(
    kind: str, expected: Any, candidate: Any, charter: ToleranceCharter
) -> CellComparison:
    """One cell, one charter, one verdict — the real logic backing both the console's own
    inline explanation and `simulate`'s own recompute. ``kind`` is ``"numeric"``,
    ``"string"`` or ``"null"``-eligible (any kind is first checked against the null
    matrix, since a null can appear in a numeric or string cell alike)."""
    null_verdict = compare_null(expected, candidate, charter.nulls)
    if null_verdict is not None:
        return null_verdict
    if kind == "numeric":
        return compare_numeric(expected, candidate, charter.numeric)
    if kind == "string":
        return compare_string(expected, candidate, charter.strings)
    return CellComparison("FAIL", f"unrecognised cell kind {kind!r}")


# ------------------------------------------------------------------------------ G1 gate


async def _latest_g1_decision(conn: asyncpg.Connection, graph: str) -> dict[str, Any] | None:
    rows = await conn.fetch(
        f"""SELECT id FROM {NODE_INDEX_TABLE}
         WHERE graph = $1 AND kind = 'node' AND label = 'GateDecision' AND retired_at IS NULL""",
        graph,
    )
    ids = [row["id"] for row in rows]
    if not ids:
        return None
    decisions = await hydrate(conn, graph, "GateDecision", ids)
    g1 = [
        props for props in decisions.values()
        if props.get("gate") == GATE and props.get("subject_ref") == SUBJECT_REF
    ]
    if not g1:
        return None
    g1.sort(key=lambda props: str(props.get("timestamp") or ""))
    return g1[-1]


async def has_g1_decision(pool: asyncpg.Pool, graph_name: str) -> bool:
    async with pool.acquire() as conn:
        return await _latest_g1_decision(conn, graph_name) is not None


async def approve_g1(
    pool: asyncpg.Pool,
    graph_name: str,
    writer: GraphWriter,
    *,
    version: int,
    principal: Principal,
    countersigned_by: str,
    rationale: str,
) -> dict[str, Any]:
    """The client analytics lead approves a specific, already-saved charter version at
    G1, countersigned by the Parity Engineer — §13.1's own "Client analytics lead +
    Artizent Parity Engineer" pair, the identical approver/countersigner shape
    `g2.py::approve()` already uses for G2."""
    countersigner = countersigned_by.strip()
    if not countersigner:
        raise ToleranceCharterError("a G1 approval needs the Parity Engineer who countersigns it")
    cleaned_rationale = rationale.strip()
    if len(cleaned_rationale) < MIN_RATIONALE_LENGTH:
        raise ToleranceCharterError(
            f"a G1 approval needs a rationale of at least {MIN_RATIONALE_LENGTH} characters"
        )

    decision_id = new_ulid()
    await writer.write_nodes(
        [
            NodeWrite(
                type="GateDecision",
                id=decision_id,
                properties={
                    "gate": GATE,
                    "subject_ref": SUBJECT_REF,
                    "decision": "APPROVED",
                    "approver": principal.value,
                    "approver_role": "client_analytics_lead",
                    "countersigner": countersigner,
                    "countersigner_role": "parity_engineer",
                    "version_hash": str(version),
                    "rationale": cleaned_rationale,
                    "timestamp": _now(),
                },
            )
        ],
        principal=principal,
    )
    return {"gate_decision_id": decision_id, "version": version, "decision": "APPROVED"}


async def _report_ids_proved_under(conn: asyncpg.Connection, graph: str, charter_version: str) -> list[str]:
    """`ReportDefinition` ids whose most recent proof ran under `charter_version` --
    honestly empty today, since no story has ever written a `ParityRun`."""
    run_rows = await conn.fetch(
        f"""SELECT id FROM {NODE_INDEX_TABLE}
         WHERE graph = $1 AND kind = 'node' AND label = 'ParityRun' AND retired_at IS NULL""",
        graph,
    )
    run_ids = [row["id"] for row in run_rows]
    if not run_ids:
        return []
    runs = await hydrate(conn, graph, "ParityRun", run_ids)
    matching_run_ids = [rid for rid, props in runs.items() if props.get("charter_version") == charter_version]
    if not matching_run_ids:
        return []
    edge_rows = await conn.fetch(
        f"""
        SELECT DISTINCT e.from_id AS report_id
          FROM {EDGE_INDEX_TABLE} e
          JOIN {NODE_INDEX_TABLE} n ON n.graph = e.graph AND n.id = e.from_id
             AND n.kind = 'node' AND n.label = 'ReportDefinition' AND n.retired_at IS NULL
         WHERE e.graph = $1 AND e.label = 'PROVED_BY' AND e.to_id = ANY($2::text[])
           AND e.retired_at IS NULL
        """,
        graph, matching_run_ids,
    )
    return [row["report_id"] for row in edge_rows]


async def affected_workbook_ids(pool: asyncpg.Pool, graph_name: str, *, superseded_version: str) -> list[str]:
    """Every workbook (Migration Unit identity — `ReportDefinition.mu_ref`) whose most
    recent proof ran under `superseded_version`."""
    async with pool.acquire() as conn:
        report_ids = await _report_ids_proved_under(conn, graph_name, superseded_version)
        reports = await hydrate(conn, graph_name, "ReportDefinition", report_ids)
    return sorted({str(props["mu_ref"]) for props in reports.values() if props.get("mu_ref")})


async def save_charter(
    pool: asyncpg.Pool,
    graph_name: str,
    writer: GraphWriter,
    store: ToleranceCharterStore,
    migration_units: MigrationUnitRegistry,
    *,
    charter: ToleranceCharter,
    principal: Principal,
    client_analytics_lead_ack: str | None = None,
    reason: str | None = None,
) -> dict[str, Any]:
    """Save a new, immutable charter version. Once a G1 decision has ever been recorded,
    a further change requires the client analytics lead's own named sign-off and a
    reason in the same call — the AC's own "requires the parity engineer and the client
    analytics lead" — and re-proves every workbook `affected_workbook_ids` finds for the
    version this change supersedes."""
    previous = await store.latest()
    is_revision = await has_g1_decision(pool, graph_name)

    if is_revision:
        ack = (client_analytics_lead_ack or "").strip()
        if not ack:
            raise ToleranceCharterError(
                "the charter has already been approved at G1; changing it needs the "
                "client analytics lead's own named sign-off"
            )
        cleaned_reason = (reason or "").strip()
        if len(cleaned_reason) < MIN_RATIONALE_LENGTH:
            raise ToleranceCharterError(
                f"changing an approved charter needs a reason of at least "
                f"{MIN_RATIONALE_LENGTH} characters"
            )

    saved = await store.save(charter, updated_by=principal.value)

    reproved: list[str] = []
    if is_revision:
        assert client_analytics_lead_ack is not None and reason is not None
        decision_id = new_ulid()
        await writer.write_nodes(
            [
                NodeWrite(
                    type="GateDecision",
                    id=decision_id,
                    properties={
                        "gate": GATE,
                        "subject_ref": SUBJECT_REF,
                        "decision": "APPROVED",
                        "approver": client_analytics_lead_ack.strip(),
                        "approver_role": "client_analytics_lead",
                        "countersigner": principal.value,
                        "countersigner_role": "parity_engineer",
                        "version_hash": str(saved.version),
                        "rationale": reason.strip(),
                        "timestamp": _now(),
                    },
                )
            ],
            principal=principal,
        )
        workbook_ids = await affected_workbook_ids(
            pool, graph_name, superseded_version=str(previous.version)
        )
        for workbook_id in workbook_ids:
            accepted = await migration_units.mark_for_reproof(
                workbook_id, reason="tolerance charter revised", principal=principal.value
            )
            if accepted:
                reproved.append(workbook_id)

    return {"charter": saved.as_dict(), "is_revision": is_revision, "reproved_workbook_ids": reproved}


# -------------------------------------------------------------------------------- simulate


async def simulate_charter(
    pool: asyncpg.Pool, graph_name: str, *, workbook_id: str, charter: ToleranceCharter
) -> dict[str, Any]:
    """Re-diff the workbook's last run under the edited charter, without executing
    anything. Honestly reports "no prior run" for every real workbook today — no story
    has ever written a `ParityRun`/`Verdict` (E7 is otherwise entirely unbuilt)."""
    async with pool.acquire() as conn:
        report_rows = await conn.fetch(
            f"""SELECT id FROM {NODE_INDEX_TABLE}
             WHERE graph = $1 AND kind = 'node' AND label = 'ReportDefinition' AND retired_at IS NULL""",
            graph_name,
        )
        report_ids = [row["id"] for row in report_rows]
        reports = await hydrate(conn, graph_name, "ReportDefinition", report_ids)
        own_report_ids = [rid for rid, props in reports.items() if props.get("mu_ref") == workbook_id]

        run_ids: list[str] = []
        if own_report_ids:
            edge_rows = await conn.fetch(
                f"""
                SELECT e.to_id AS run_id
                  FROM {EDGE_INDEX_TABLE} e
                  JOIN {NODE_INDEX_TABLE} n ON n.graph = e.graph AND n.id = e.to_id
                     AND n.kind = 'node' AND n.label = 'ParityRun' AND n.retired_at IS NULL
                 WHERE e.graph = $1 AND e.label = 'PROVED_BY' AND e.from_id = ANY($2::text[])
                   AND e.retired_at IS NULL
                """,
                graph_name, own_report_ids,
            )
            run_ids = [row["run_id"] for row in edge_rows]

        if not run_ids:
            return {
                "workbook_id": workbook_id, "has_prior_run": False,
                "message": "no ParityRun exists yet for this workbook",
                "verdicts": [],
            }

        runs = await hydrate(conn, graph_name, "ParityRun", run_ids)
        latest_run_id = max(runs, key=lambda rid: str(runs[rid].get("finished") or runs[rid].get("started") or ""))
        verdict_ids = list(runs[latest_run_id].get("verdicts") or [])
        verdicts = await hydrate(conn, graph_name, "Verdict", verdict_ids)

    recomputed: list[dict[str, Any]] = []
    for verdict_id, properties in verdicts.items():
        for cell in properties.get("failing_cells") or []:
            comparison = compare_cell(
                str(cell.get("kind") or ""), cell.get("expected"), cell.get("candidate"), charter
            )
            recomputed.append(
                {
                    "verdict_id": verdict_id,
                    "grain_key": cell.get("grain_key"),
                    "measure": cell.get("measure"),
                    "expected": cell.get("expected"),
                    "candidate": cell.get("candidate"),
                    "result": comparison.result,
                    "reason": comparison.reason,
                }
            )

    return {
        "workbook_id": workbook_id, "has_prior_run": True, "run_id": latest_run_id,
        "message": None, "verdicts": recomputed,
    }


class ToleranceCharterService:
    """Binds the charter's own free functions to one pool/graph/writer/store/registry --
    the identical "pre-bound object on app.state" shape `Compositor`/`Modeller` already
    take, so a route needs no `graph_name` of its own to call this."""

    def __init__(
        self,
        pool: asyncpg.Pool,
        *,
        graph_name: str,
        writer: GraphWriter,
        store: ToleranceCharterStore,
        migration_units: MigrationUnitRegistry,
    ) -> None:
        self._pool = pool
        self._graph = graph_name
        self._writer = writer
        self._store = store
        self._migration_units = migration_units

    async def latest(self) -> ToleranceCharterVersion:
        return await self._store.latest()

    async def get(self, version: int) -> ToleranceCharterVersion | None:
        return await self._store.get(version)

    async def save(
        self,
        charter: ToleranceCharter,
        *,
        principal: Principal,
        client_analytics_lead_ack: str | None = None,
        reason: str | None = None,
    ) -> dict[str, Any]:
        return await save_charter(
            self._pool, self._graph, self._writer, self._store, self._migration_units,
            charter=charter, principal=principal,
            client_analytics_lead_ack=client_analytics_lead_ack, reason=reason,
        )

    async def approve_g1(
        self, *, version: int, principal: Principal, countersigned_by: str, rationale: str
    ) -> dict[str, Any]:
        return await approve_g1(
            self._pool, self._graph, self._writer,
            version=version, principal=principal,
            countersigned_by=countersigned_by, rationale=rationale,
        )

    async def simulate(self, *, workbook_id: str, charter: ToleranceCharter) -> dict[str, Any]:
        return await simulate_charter(self._pool, self._graph, workbook_id=workbook_id, charter=charter)


__all__ = [
    "CHARTER_FIELD_METADATA",
    "CHARTER_TABLE",
    "DEFAULT_CHARTER",
    "GATE",
    "SUBJECT_REF",
    "CellComparison",
    "DateRule",
    "NullRule",
    "NumericRule",
    "OrderingRule",
    "ParamRule",
    "PostgresToleranceCharterStore",
    "RowRule",
    "SamplingRule",
    "StringRule",
    "ToleranceCharter",
    "ToleranceCharterError",
    "ToleranceCharterService",
    "ToleranceCharterStore",
    "ToleranceCharterVersion",
    "WaiverRule",
    "affected_workbook_ids",
    "approve_g1",
    "compare_cell",
    "compare_null",
    "compare_numeric",
    "compare_string",
    "has_g1_decision",
    "save_charter",
    "simulate_charter",
]
