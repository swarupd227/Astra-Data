"""§11.2/§8.10 the bounded repair loop -- story S8.2.1, continuing F8.2/E8.

    "As a parity engineer, I want the Mender to repair failures in at most three passes
    with a pattern-first strategy, so that the loop cannot spin, cannot silently accept
    and every pass is in evidence.

    Acceptance criteria:
    - Pass 1: if an ACTIVE pattern matches the failure class and AST shape, apply it
      deterministically; pass 2: model repair with the failing evidence and the
      class-specific instructions; pass 3: same with a widened context contract
    - Every repair is re-validated up the ladder and re-proved on the affected cases only
    - Bound (default 3) is configurable per tenant; exhaustion routes to the Exception
      Desk with all passes attached; the MU never transitions to PASSED from within the
      loop without a proof PASS
    - Passes consumed per MU is stored and reported (mean passes to pass)"

§11.2 itself, verbatim: *"Classify every failing case; group by artefact (a measure used
by several sheets is repaired once). Pattern first. If an ACTIVE pattern matches the
failure class and the artefact's AST shape, apply it deterministically and re-run only
the affected cases. Model repair otherwise: assemble the repair context (§8.10), request
a corrected artefact under the same output schema as generation, run the validation
ladder, re-run affected cases. Bound. Each MU has a pass budget (default 3). Each pass
may touch any number of artefacts but re-runs only affected cases. A pass that produces
no change in the failing set ends the loop early. Escalate on exhaustion, on an UNKNOWN
class after one model diagnosis, on any KEY_MISSING (which is a model defect, not a
report defect), or on a repair that makes a previously passing case fail (the artefact is
reverted first). Patternise. Every successful repair is written as a Pattern candidate
with the failure class as part of its signature."* §11.2 itself describes "pattern
first, else model repair" as the general shape; the backlog's own three-pass breakdown
(pattern / model / model-widened) is a real, disclosed elaboration of that shape, not a
contradiction of it -- §11.2 never says passes 2 and 3 must behave identically, and this
module treats the backlog's own specificity as the concrete instruction to follow.

**No `MenderPass`/repair-record concept existed anywhere before this story** -- confirmed
by direct research: E8 was entirely unbuilt except for S8.1.1's own classification. A
`MenderPass` is a new, real node type (§8.10/§11.2, not a JSON blob on `ExceptionCase`)
so a future Exception Desk case page (F8.3, "Mender pass history with diffs") can read
each pass directly.

**Every repair writes a brand-new `Measure`, never edits one in place** -- the identical
convention `generation._write_measure`/`patterns.apply_active_pattern` already both
established (confirmed: neither ever mutates an existing `Measure.dax`). A "revert" is
therefore also a new write (the prior dax, written again), not an undo -- keeping the
same full, real history every other artefact in this codebase already keeps.

**Re-proving re-executes the target side only, never the source.** A repair changes what
the target computes; the source data a case's own `expected_ref` already holds has not
changed at all, so re-executing it again would be wasted work and a real, if small, risk
of manufacturing spurious `SOURCE_DRIFT` noise from nothing. `reprove_cases` reads the
already-stored source `ResultSet` back and only re-evaluates the target side -- the
identical "only what changed needs re-running" reasoning "re-run only the affected
cases" already gives one level up.

**Re-proving writes real `Verdict` nodes, but never a new `ParityRun`.** `ParityRun` is a
whole-workbook concept (`run_parity_for_workbook`, S7.4.1, diffs every executed case for
the workbook); `parity_dashboard.py`'s own docstring already discloses this assumption
explicitly and flags that "a future story that lets a Parity Engineer re-run a narrower
subset of cases would need to revisit" it. Rather than revisit it (real risk to the
Parity Dashboard's own per-sheet counts, which assume the *latest* `ParityRun` covers the
*whole* live case set), the Mender's own re-proof stays a parallel, real fact: fresh
`Verdict` nodes for exactly the cases touched, not part of any `ParityRun.verdicts` list.
The Parity Dashboard's own picture is refreshed for real the next time `:run-parity` is
called for the whole workbook, in the normal way -- unaffected by anything this module
does.

**"Configurable per tenant" is a new, real, Postgres-backed store, not a module
constant.** Confirmed by direct research: no per-tenant configuration concept exists
anywhere in this codebase beyond "the graph is the tenant" (every per-tenant store scopes
rows by `graph_name` alone -- `GatewayPolicyStore`, `RulesEngine`, `ProvenanceStore`, and
now `MenderConfigStore`). A bare module constant (`regression.py`'s own
`MAX_CONSECUTIVE_FAILURES`, say) would not satisfy "configurable" literally; `mender_
config` (migration v0030) is scoped and versioned the identical way `tolerance_charter_
version` already is -- an edit is a new version, never an overwrite.

**Pass 2/3's own model call is real, but genuinely unroutable in this deployment.**
`gateway.MENDER_REPAIR` is a real, registered task class; `POST /v1/model-gateway:
run-eval` (S5.3.2) is hard-coded to `generation.run_transpile_c3_eval`, so no eval set
exists for this task class and `GatewayPolicyStore.routable_providers` always returns
empty for it -- the identical disclosed-absent footing `TRANSPILE_C3_SMALL_MODEL`
already has. A real `GatewayRoutingError` is therefore the honest, expected outcome of
every model-repair pass in this deployment today; the loop treats it as a real,
recorded, non-retryable attempt (the identical "the fault isn't this attempt's, and
won't become routable within a single run" reasoning `generation._run_ladder` already
gives its own identical error) rather than a crash.

**No `ContextContract`/`ContextAssembler` registration for `ContractName.MENDER_REPAIR`
-- the identical "name only" choice `generation.GenerationRequest` already made for
`TRANSPILER_CALC`.** `RepairContext` is a bespoke dataclass with its own
`context_hash()`, never routed through the generic contract machinery -- building that
machinery is real, separate scope this story does not attempt (see `context/contract.py`'s
own docstring for the full reasoning).

**"Class-specific instructions" and a "widened context contract" are backlog phrases
with no spec or codebase precedent -- both are new, disclosed designs.** Class-specific
instructions are a real, per-§11.1-class sentence appended to the repair request's own
`constraints` (`_CLASS_INSTRUCTIONS`, keyed by failure class, invented and disclosed,
the identical "invented, disclosed" footing `generation.CONSTRAINTS` already has for its
own constant instruction list). "Widened" (pass 3 only) means the failing-cell sample is
no longer bounded (§8.10's own "a bounded sample" becomes the full set) and the
calculation's own real dependency closure (fields/parameters/nested calcs, the identical
`generation._dependency_closure` shape) is included -- strictly more evidence, never
different evidence.

**Revert-on-regression is scoped to cases sharing the repaired artefact, not the whole
workbook.** §11.2's own "a repair that makes a previously passing case fail" is checked
against every other live, executed `ParityCase` in the *same workbook* that resolves to
the *same* `artefact_ref` (the identical name-to-id resolution S8.1.1's own
`classify_run` already performs) and already has a real prior `Verdict(result="PASS")` --
not every case in the workbook regardless of artefact, which would need re-diffing
everything after every pass for a check the spec's own wording ("a measure used by
several sheets") already scopes to the artefact itself.

**KEY_MISSING never enters the repair loop at all -- §11.2's own literal words.** "Any
KEY_MISSING (which is a model defect, not a report defect)" escalates immediately, never
attempted as a report-side repair. Story S8.2.2 (`foundry_routing.py`) checks first
whether either KEY_MISSING or AGGREGATION carries real, confirmable graph evidence of a
model-level defect and, when it does, routes it to the Foundry for real (a `MenderPass
(strategy="ROUTE_TO_FOUNDRY")`) rather than only escalating. A KEY_MISSING case S8.2.2's
own check cannot confirm evidence for falls back to this story's own original behaviour:
one `MenderPass(strategy="ESCALATE_IMMEDIATE")` written for a complete evidence trail
even though no repair was attempted -- consistent with "every pass is in evidence"
covering the *decision* not to attempt one, not only the attempts themselves.

**Pattern matching is AST-shape-only today, not failure-class-aware.** `Pattern.class`
(§4.3) is the Transpiler's own C1-C4 taxonomy, not §11.1's -- confirmed directly, no
property on `Pattern` carries a failure class at all. Backlog story S8.2.3 (not this
one) is what "generalises a repair into a CANDIDATE pattern keyed by (failure class, AST
shape)" -- until it exists, pass 1 matches purely by AST shape (`patterns.
find_matching_pattern`, reused verbatim), a real, disclosed, narrower reading of the
AC's own "matches the failure class and AST shape" than the AC's own words promise.
Patternising a successful repair (§11.2's own last bullet) is that same S8.2.3's own
scope, not built here.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Protocol

import asyncpg
from astra_adapter import ExecutionStrategy
from astra_adapter import ParityCase as SdkParityCase
from astra_adapter.target_contract import TargetAdapter
from pydantic import BaseModel, ConfigDict, ValidationError
from pydantic import Field as PydanticField

from .artefacts import ArtefactStore
from .case_derivation import (
    _worksheet_field_index,  # cross-epic private helper; see module docstring
)
from .case_execution import (  # cross-epic private helper; see module docstring
    _table_map_for_sheet,
    result_set_from_parquet,
)
from .case_execution_query import build_dax_query, to_sdk_filters, to_sdk_parameters
from .context.canonical import context_hash
from .context.contract import ContractName
from .context.signature import capture_identifiers
from .diff import diff_result_sets
from .foundry_routing import detect_model_defect, route_to_foundry
from .gateway import MENDER_REPAIR, Gateway, GatewayRoutingError
from .graph.queries import EDGE_INDEX_TABLE, NODE_INDEX_TABLE
from .ids import new_ulid
from .lineage import children, hydrate
from .ontology.types import BASE_NODE_PROPERTIES
from .patterns import PatternMatch, find_matching_pattern, render_target
from .principal import Principal
from .provenance import AgentMode, ProvenanceStore, new_record
from .rules import dax_sanity_check
from .tolerance_charter import ToleranceCharter, ToleranceCharterStore
from .writes import EdgeWrite, GraphWriter, NodeWrite

logger = logging.getLogger(__name__)

DEFAULT_PASS_BUDGET = 3
MAX_PASS_BUDGET_APPLIED = 3
"""§11.2's own three named strategies (pattern / model / model-widened) are all this
module knows how to run; a tenant configuring a budget above this repeats the pass-3
(MODEL_WIDENED) strategy for any further pass rather than inventing a fourth, undefined
one -- disclosed, not silently capped."""

MENDER_CONFIG_TABLE = "public.mender_config"

EVIDENCE_KIND = "mender_pass_evidence"
EVIDENCE_MEDIA_TYPE = "application/json"

#: §11.2's own literal escalation rule -- checked before any pass is ever attempted.
_ESCALATE_WITHOUT_REPAIR = frozenset({"KEY_MISSING"})

#: Invented, disclosed per-class guidance appended to a model repair request's own
#: `constraints` -- the AC's own "class-specific instructions", which neither the spec
#: nor the backlog gives literal text for. Mirrors `generation.CONSTRAINTS`'s own
#: "a real, disclosed, invented instruction list" footing.
_CLASS_INSTRUCTIONS: dict[str, str] = {
    "FILTER_CONTEXT": "The failing cells are off by a consistent factor or cover a "
        "subset of rows -- check whether a filter is applied at the wrong CALCULATE "
        "scope (ALL/ALLEXCEPT) rather than the sheet's own filter context.",
    "NULL_HANDLING": "At least one failing cell pairs a null/blank source value against "
        "a zero or non-null target value -- check division-by-zero and ZN/IFNULL-style "
        "null semantics; DIVIDE(...) or COALESCE(...) are the usual DAX idioms.",
    "DATE_GRAIN": "The failing cells are date/datetime comparisons -- check date "
        "truncation, fiscal calendar alignment, and week-start convention against the "
        "source's own grain.",
    "AGGREGATION": "Totals and row-level cells disagree with each other -- check "
        "whether the measure aggregates at the wrong grain, or sums a ratio that should "
        "be recomputed from its own parts at the target grain.",
    "TYPE_COERCION": "The failing cells are string/number formatting mismatches -- "
        "check implicit casts and FORMAT/VALUE-style conversions.",
    "LOD_SCOPE": "The source calculation is a level-of-detail expression (FIXED/"
        "INCLUDE/EXCLUDE) -- check that the DAX CALCULATE filter scope matches the "
        "source LOD's own scope, not the sheet's visible grain.",
    "TABLE_CALC": "The source calculation is a table calculation -- check that the "
        "DAX window function's own partitioning/addressing reproduces the sheet's own "
        "partition boundary, which the sheet context supplies at query time.",
    "SORT_LIMIT": "The failing rows differ only by membership under a top-N -- check "
        "the tie-break rule used when ranking ties at the boundary.",
    "UNKNOWN": "No §11.1 signature matched this failure's own evidence -- diagnose "
        "from the raw failing cells and result-set headers alone; there is no more "
        "specific instruction to give.",
}


class MenderError(Exception):
    """A repair could not be attempted or completed for a stated, real reason."""


# ------------------------------------------------------------------------------ config


@dataclass(frozen=True, slots=True)
class MenderConfig:
    pass_budget: int = DEFAULT_PASS_BUDGET

    def as_dict(self) -> dict[str, Any]:
        return {"pass_budget": self.pass_budget}


class MenderConfigStore(Protocol):
    async def latest(self) -> MenderConfig: ...

    async def save(self, config: MenderConfig, *, updated_by: str) -> MenderConfig: ...


class PostgresMenderConfigStore:
    """Versioned, per-graph ('per tenant') -- see this module's own docstring."""

    def __init__(self, pool: asyncpg.Pool, *, graph_name: str) -> None:
        self._pool = pool
        self._graph = graph_name

    async def latest(self) -> MenderConfig:
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                f"SELECT pass_budget FROM {MENDER_CONFIG_TABLE} WHERE graph = $1 "
                f"ORDER BY version DESC LIMIT 1",
                self._graph,
            )
        return MenderConfig(pass_budget=row["pass_budget"]) if row else MenderConfig()

    async def save(self, config: MenderConfig, *, updated_by: str) -> MenderConfig:
        async with self._pool.acquire() as conn, conn.transaction():
            current = await conn.fetchval(
                f"SELECT MAX(version) FROM {MENDER_CONFIG_TABLE} WHERE graph = $1", self._graph,
            )
            version = (current or 0) + 1
            await conn.execute(
                f"""INSERT INTO {MENDER_CONFIG_TABLE} (id, graph, version, pass_budget, updated_by)
                    VALUES ($1, $2, $3, $4, $5)""",
                f"menderconf_{new_ulid()}", self._graph, version, config.pass_budget, updated_by,
            )
        return config


class InMemoryMenderConfigStore:
    def __init__(self, config: MenderConfig | None = None) -> None:
        self._config = config or MenderConfig()

    async def latest(self) -> MenderConfig:
        return self._config

    async def save(self, config: MenderConfig, *, updated_by: str) -> MenderConfig:
        self._config = config
        return config


# --------------------------------------------------------------------------- pass shape


def strategy_for_pass(pass_number: int) -> str:
    """§11.2/backlog S8.2.1's own three named strategies, in order -- see this module's
    own docstring on why a budget above three repeats MODEL_WIDENED rather than
    inventing a fourth one."""
    if pass_number <= 1:
        return "PATTERN"
    if pass_number == 2:
        return "MODEL"
    return "MODEL_WIDENED"


@dataclass(frozen=True, slots=True)
class RepairContext:
    """§8.10's own repair context: "the failing cells, filter context, both result
    sets' headers and a bounded sample, the current measure and its source calc, the
    model definition excerpt" -- assembled directly, not through `ContextAssembler` (see
    this module's own docstring)."""

    failure_class: str
    classification_signals: dict[str, Any]
    failing_cells: tuple[dict[str, Any], ...]
    filter_ctx: dict[str, Any]
    expected_columns: tuple[dict[str, Any], ...]
    candidate_columns: tuple[dict[str, Any], ...]
    current_dax: str
    source_formula: str
    source_formula_ast: Any
    class_instruction: str
    dependency_closure: dict[str, Any]
    widened: bool
    output_schema: dict[str, str]

    def as_dict(self) -> dict[str, Any]:
        return {
            "task": "MENDER_REPAIR",
            "failure_class": self.failure_class,
            "classification_signals": self.classification_signals,
            "failing_cells": [dict(c) for c in self.failing_cells],
            "filter_ctx": self.filter_ctx,
            "expected_columns": [dict(c) for c in self.expected_columns],
            "candidate_columns": [dict(c) for c in self.candidate_columns],
            "current_dax": self.current_dax,
            "source_formula": self.source_formula,
            "source_formula_ast": self.source_formula_ast,
            "class_instruction": self.class_instruction,
            "dependency_closure": self.dependency_closure,
            "widened": self.widened,
            "output_schema": self.output_schema,
        }

    def context_hash(self) -> str:
        return context_hash(json.dumps(self.as_dict(), sort_keys=True, default=str).encode("utf-8"))


#: §16.1 rung 1: "the same output schema as generation" (§11.2's own literal words) --
#: `generation.ModelResponseSchema`'s own exact shape, reused rather than a second,
#: near-identical schema.
OUTPUT_SCHEMA: dict[str, str] = {
    "dax": "string -- the corrected measure expression",
    "m": "string | null -- present only if the fix belongs in Power Query, not DAX",
    "assumptions": "string[] -- anything assumed about data not visible in the evidence",
    "confidence": "number 0-1 -- the model's own declared confidence",
    "notes": "string -- free text explaining the fix",
}


class RepairResponseSchema(BaseModel):
    """§16.1 rung 1 (schema): the AC's own "request a corrected artefact under the
    same output schema as generation" -- `generation.ModelResponseSchema`'s own exact
    field set, a real, working Pydantic check, not a disclosed stand-in."""

    model_config = ConfigDict(extra="forbid")

    dax: str
    m: str | None = None
    assumptions: list[str] = PydanticField(default_factory=list)
    confidence: float
    notes: str


@dataclass(frozen=True, slots=True)
class MenderPassOutcome:
    """One pass's own full record -- the AC's own "every pass is in evidence", returned
    to the caller and written as a real `MenderPass` node."""

    pass_number: int
    strategy: str
    result: str
    pattern_ref: str | None
    measure_id: str | None
    cases_reproved: tuple[str, ...]
    cases_still_failing: tuple[str, ...]
    evidence: dict[str, Any]
    started_at: str
    finished_at: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "pass_number": self.pass_number, "strategy": self.strategy, "result": self.result,
            "pattern_ref": self.pattern_ref, "measure_id": self.measure_id,
            "cases_reproved": list(self.cases_reproved), "cases_still_failing": list(self.cases_still_failing),
            "evidence": self.evidence, "started_at": self.started_at, "finished_at": self.finished_at,
        }


def _iso(moment: datetime) -> str:
    return moment.astimezone(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _writable_node_properties(properties: dict[str, Any]) -> dict[str, Any]:
    managed = frozenset(p.name for p in BASE_NODE_PROPERTIES if p.server_managed) | {"id", "side"}
    return {k: v for k, v in properties.items() if k not in managed}


# ------------------------------------------------------------------------ graph reads


async def _resolve_calculated_field(
    pool: asyncpg.Pool, graph_name: str, artefact_ref: str | None,
) -> tuple[str, dict[str, Any]] | None:
    """`artefact_ref` is a `CalculatedField` id when S8.1.1's own grouping could resolve
    one -- but it can also be a plain `Field` id, or absent (a sheet-level grouping
    fallback), neither of which carries a `formula_ast` there is anything to repair.
    `None` in either case: the caller falls straight to a model-repair attempt with
    whatever evidence it already has, never a pattern attempt against nothing."""
    if not artefact_ref:
        return None
    async with pool.acquire() as conn:
        hydrated = await hydrate(conn, graph_name, "CalculatedField", [artefact_ref])
    properties = hydrated.get(artefact_ref)
    return (artefact_ref, properties) if properties else None


async def _current_measure(
    pool: asyncpg.Pool, graph_name: str, calc_id: str,
) -> tuple[str, dict[str, Any]] | None:
    """The live `Measure` a `CalculatedField` currently `MAPS_TO` -- the artefact a
    repair actually corrects. Ties broken by `created_at`, latest wins, the same
    "an edit is a new version" reading every other artefact in this codebase already has
    (there is no `Measure.version`/`supersedes_id` pair to order by explicitly)."""
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            f"""SELECT e.to_id AS measure_id FROM {EDGE_INDEX_TABLE} e
                 JOIN {NODE_INDEX_TABLE} n ON n.id = e.to_id AND n.kind = 'node'
                  AND n.graph = $1 AND n.label = 'Measure' AND n.retired_at IS NULL
                 WHERE e.graph = $1 AND e.label = 'MAPS_TO' AND e.from_id = $2 AND e.retired_at IS NULL""",
            graph_name, calc_id,
        )
        if not rows:
            return None
        measures = await hydrate(conn, graph_name, "Measure", [row["measure_id"] for row in rows])
    if not measures:
        return None
    latest_id = max(measures, key=lambda mid: str(measures[mid].get("created_at") or ""))
    return latest_id, measures[latest_id]


async def _latest_verdict(
    pool: asyncpg.Pool, graph_name: str, case_id: str,
) -> dict[str, Any] | None:
    """The most recent real `Verdict` for one `ParityCase` -- found directly, since no
    property on `ParityCase` itself points back to its own latest verdict. Ties broken
    by `created_at`, latest wins, the identical reading `_current_measure` already
    gives its own artefact."""
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            f"""SELECT id FROM {NODE_INDEX_TABLE}
                 WHERE graph = $1 AND kind = 'node' AND label = 'Verdict' AND retired_at IS NULL""",
            graph_name,
        )
        verdicts = await hydrate(conn, graph_name, "Verdict", [row["id"] for row in rows])
    matches = {vid: props for vid, props in verdicts.items() if props.get("case_ref") == case_id}
    if not matches:
        return None
    latest_id = max(matches, key=lambda vid: str(matches[vid].get("created_at") or ""))
    return matches[latest_id]


async def _gather_parity_evidence(
    pool: asyncpg.Pool, graph_name: str, artefact_store: ArtefactStore, *, case_refs: tuple[str, ...],
) -> dict[str, Any]:
    """Real failing-cell evidence for a repair request -- read from each case's own
    latest `Verdict.evidence_ref` (the real §10.3 evidence bundle, S7.4.1), not from
    `ExceptionCase.evidence_ref` (S8.1.1's own classification bundle, which never
    carries failing cells at all -- only which class matched and why). Merged across
    every case this exception covers, each cell tagged with its own `case_ref` so a
    model reading several sheets' worth of evidence at once can still tell them apart."""
    failing_cells: list[dict[str, Any]] = []
    filter_ctx: dict[str, Any] = {}
    expected_columns: tuple[dict[str, Any], ...] = ()
    candidate_columns: tuple[dict[str, Any], ...] = ()
    for case_id in case_refs:
        verdict = await _latest_verdict(pool, graph_name, case_id)
        if verdict is None or not verdict.get("evidence_ref"):
            continue
        content = await artefact_store.content(str(verdict["evidence_ref"]))
        if content is None:
            continue
        bundle = json.loads(content)
        for cell in (bundle.get("diff") or {}).get("failing_cells") or ():
            failing_cells.append({**cell, "case_ref": case_id})
        if not filter_ctx:
            filter_ctx = bundle.get("filter_ctx") or {}
        if not expected_columns:
            expected_columns = tuple(bundle.get("expected_columns") or ())
        if not candidate_columns:
            candidate_columns = tuple(bundle.get("candidate_columns") or ())
    return {
        "failing_cells": failing_cells, "filter_ctx": filter_ctx,
        "expected_columns": expected_columns, "candidate_columns": candidate_columns,
    }


async def assemble_repair_context(
    pool: asyncpg.Pool,
    graph_name: str,
    artefact_store: ArtefactStore,
    *,
    exception_properties: dict[str, Any],
    calc: tuple[str, dict[str, Any]] | None,
    current_dax: str,
    widened: bool,
) -> RepairContext:
    """§8.10's own repair context ("the failing cells, filter context, both result
    sets' headers and a bounded sample, the current measure and its source calc"),
    assembled directly from the graph -- see this module's own docstring for why this
    bypasses `ContextAssembler`."""
    case_refs = tuple(exception_properties.get("case_refs") or ())
    parity_evidence = await _gather_parity_evidence(pool, graph_name, artefact_store, case_refs=case_refs)
    signals = exception_properties.get("classification_signals") or {}
    failing_cells = tuple(parity_evidence["failing_cells"])
    if not widened:
        failing_cells = failing_cells[:20]

    dependency_closure: dict[str, Any] = {}
    calc_id: str | None = None
    formula = ""
    formula_ast: Any = None
    if calc is not None:
        calc_id, calc_properties = calc
        formula = str(calc_properties.get("formula") or "")
        formula_ast = calc_properties.get("formula_ast")
        if widened:
            async with pool.acquire() as conn:
                field_hits = await children(conn, graph_name, [calc_id], "DEPENDS_ON", "Field")
                calc_hits = await children(conn, graph_name, [calc_id], "DEPENDS_ON", "CalculatedField")
                field_ids = sorted(field_hits.get(calc_id, set()))
                calc_ids = sorted(calc_hits.get(calc_id, set()))
                fields = await hydrate(conn, graph_name, "Field", field_ids) if field_ids else {}
                calcs = await hydrate(conn, graph_name, "CalculatedField", calc_ids) if calc_ids else {}
            dependency_closure = {
                "fields": [props.get("name") for props in fields.values()],
                "calculations": [
                    {"name": props.get("name"), "formula": props.get("formula")} for props in calcs.values()
                ],
            }

    failure_class = str(exception_properties.get("class"))
    return RepairContext(
        failure_class=failure_class,
        classification_signals=dict(signals),
        failing_cells=failing_cells,
        filter_ctx=dict(parity_evidence["filter_ctx"]),
        expected_columns=tuple(parity_evidence["expected_columns"]),
        candidate_columns=tuple(parity_evidence["candidate_columns"]),
        current_dax=current_dax,
        source_formula=formula,
        source_formula_ast=formula_ast,
        class_instruction=_CLASS_INSTRUCTIONS.get(failure_class, _CLASS_INSTRUCTIONS["UNKNOWN"]),
        dependency_closure=dependency_closure,
        widened=widened,
        output_schema=OUTPUT_SCHEMA,
    )


# --------------------------------------------------------------------- writing a repair


async def _write_repaired_measure(
    pool: asyncpg.Pool,
    graph_name: str,
    writer: GraphWriter,
    provenance_store: ProvenanceStore,
    *,
    calc_id: str | None,
    name: str,
    dax: str,
    mode: AgentMode,
    pattern_ref: str | None,
    context_hash_value: str,
    subject_id: str,
    validation_state: str,
    model: str | None,
    gateway_request_id: str | None,
    tokens_in: int | None,
    tokens_out: int | None,
    confidence: float | None,
    principal: Principal,
) -> str:
    """A brand-new `Measure`, never an in-place edit -- see this module's own docstring.
    Re-points `MAPS_TO` from `calc_id` (when one resolved) to the new measure; the prior
    edge is retired the identical way `visual_redesign`/`report_deploy` already retire a
    superseded relationship rather than leaving two live `MAPS_TO` edges disagreeing."""
    measure_id = new_ulid()
    provenance_id = f"prov_{new_ulid()}"
    await writer.write_nodes(
        [NodeWrite(type="Measure", id=measure_id, properties={
            "name": name, "dax": dax, "source_calc_ref": calc_id,
            "pattern_ref": pattern_ref, "provenance_ref": provenance_id,
            "validation_state": validation_state,
        })],
        principal=principal,
    )
    if calc_id:
        async with pool.acquire() as conn:
            existing = await conn.fetch(
                f"""SELECT id FROM {EDGE_INDEX_TABLE}
                     WHERE graph = $1 AND label = 'MAPS_TO' AND from_id = $2 AND retired_at IS NULL""",
                graph_name, calc_id,
            )
        for row in existing:
            await writer.retire_edge(str(row["id"]), reason="superseded by a Mender repair", principal=principal)
        await writer.write_edge(
            EdgeWrite(type="MAPS_TO", from_id=calc_id, to_id=measure_id, properties={"pattern_ref": pattern_ref}),
            principal=principal,
        )
    record = new_record(
        id=provenance_id, artefact_kind="MEASURE", artefact_ref=measure_id,
        artefact_content_hash=context_hash(dax.encode("utf-8")),
        agent="mender", agent_version="0.1.0", mode=mode, contract=ContractName.MENDER_REPAIR,
        subject_id=subject_id, context_hash=context_hash_value, graph_version=0,
        model=model, pattern_ref=pattern_ref, gateway_request_id=gateway_request_id,
        tokens_in=tokens_in, tokens_out=tokens_out, confidence=confidence, created_by=principal.value,
    )
    await provenance_store.record(record)
    return measure_id


async def apply_pattern_repair(
    pool: asyncpg.Pool,
    graph_name: str,
    writer: GraphWriter,
    provenance_store: ProvenanceStore,
    *,
    calc_id: str,
    calc_properties: dict[str, Any],
    principal: Principal,
) -> tuple[str, PatternMatch] | None:
    """Pass 1 -- reuses `patterns.find_matching_pattern`/`render_target`/
    `dax_sanity_check` verbatim (the real, already-proven AST-shape match and render),
    but writes the corrected `Measure` under the Mender's own attribution rather than
    calling `patterns.apply_active_pattern` itself, which is wired for the Transpiler's
    own first-generation moment (it also reclassifies the source `CalculatedField` to
    C2 -- a Transpiler classification fact this repair has no business changing). `None`
    when no ACTIVE pattern matches this AST shape, or the render fails even the
    structural check -- either way the caller falls through to a model repair."""
    formula_ast = calc_properties.get("formula_ast")
    if not isinstance(formula_ast, dict):
        return None
    pattern = await find_matching_pattern(pool, graph_name, formula_ast)
    if pattern is None or pattern.promotion_state != "ACTIVE":
        return None
    captures = capture_identifiers(formula_ast)
    dax = render_target(pattern.target_template, captures)
    if dax_sanity_check(dax) is not None:
        return None
    measure_id = await _write_repaired_measure(
        pool, graph_name, writer, provenance_store,
        calc_id=calc_id, name=str(calc_properties.get("name") or calc_id), dax=dax,
        mode=AgentMode.DETERMINISTIC, pattern_ref=pattern.pattern_id,
        context_hash_value=context_hash(str(formula_ast).encode("utf-8")), subject_id=calc_id,
        validation_state="rung 2 (structural): applied deterministically from an ACTIVE "
                          "Pattern via the Mender (§11.2), ahead of any model call",
        model=None, gateway_request_id=None, tokens_in=None, tokens_out=None, confidence=None,
        principal=principal,
    )
    return measure_id, pattern


async def call_model_repair(
    gateway: Gateway, request: RepairContext,
) -> tuple[str | None, str, dict[str, Any]]:
    """Passes 2/3 -- calls the real gateway under `MENDER_REPAIR` (genuinely unroutable
    in this deployment today, see this module's own docstring), checks §16.1 rungs 1-2
    (schema, then parse via `dax_sanity_check`) the identical way `generation._run_
    ladder` already does for its own two real rungs. Returns `(dax_or_none, result,
    detail)` -- `result` is one of `MODEL_UNAVAILABLE`/`SCHEMA_ERROR`/`PARSE_ERROR`/`OK`.
    Never retried within one call: a schema failure is the prompt contract's own fault,
    and an unroutable gateway will not become routable within the same pass, the
    identical reasoning `_run_ladder` already gives both outcomes."""
    try:
        response = await gateway.generate(task_class=MENDER_REPAIR, request=request, previous_error=None)
    except GatewayRoutingError as exc:
        return None, "MODEL_UNAVAILABLE", {"gateway_error": str(exc)}

    detail: dict[str, Any] = {
        "raw_response": dict(response.raw), "gateway_request_id": response.gateway_request_id,
        "provider": response.provider, "model": response.model, "prompt_hash": response.prompt_hash,
        "temperature": response.temperature, "tokens_in": response.tokens_in, "tokens_out": response.tokens_out,
    }
    try:
        parsed = RepairResponseSchema.model_validate(dict(response.raw))
    except ValidationError as exc:
        detail["schema_error"] = str(exc)
        return None, "SCHEMA_ERROR", detail

    detail["confidence"] = parsed.confidence
    detail["notes"] = parsed.notes
    detail["assumptions"] = parsed.assumptions
    parse_error = dax_sanity_check(parsed.dax)
    if parse_error is not None:
        detail["parse_error"] = parse_error
        detail["dax"] = parsed.dax
        return None, "PARSE_ERROR", detail

    detail["dax"] = parsed.dax
    return parsed.dax, "OK", detail


# --------------------------------------------------------------------------- re-proof


async def reprove_cases(
    pool: asyncpg.Pool,
    graph_name: str,
    writer: GraphWriter,
    artefact_store: ArtefactStore,
    target_adapter: TargetAdapter,
    *,
    case_ids: tuple[str, ...],
    workspace: str,
    charter: ToleranceCharter,
    principal: Principal,
) -> dict[str, dict[str, Any]]:
    """§11.2's own 're-run only the affected cases' -- re-executes the *target* side
    alone (the source has not changed, see this module's own docstring) and re-diffs
    against the already-stored source `ResultSet`. Writes a real `Verdict` per case,
    never a `ParityRun` -- a parallel, real fact, not a substitute for a real
    `POST .../:run-parity`. Returns `{case_id: {"result": ..., "verdict_id": ...,
    "evidence_ref": ...}}`, skipping (not raising for) a case with no stored source
    result to compare against, or one that no longer exists live."""
    if not case_ids:
        return {}
    async with pool.acquire() as conn:
        cases = await hydrate(conn, graph_name, "ParityCase", list(case_ids))

    results: dict[str, dict[str, Any]] = {}
    for case_id in case_ids:
        properties = cases.get(case_id)
        if properties is None or not properties.get("expected_ref"):
            continue
        sheet_ref = str(properties.get("sheet_ref") or "")
        grain = tuple(properties.get("grain") or ())
        measures = tuple(properties.get("measures") or ())
        filter_ctx = properties.get("filter_ctx") or {}
        param_values = properties.get("param_values") or {}
        sdk_filters = to_sdk_filters(filter_ctx)
        sdk_parameters = to_sdk_parameters(param_values)

        async with pool.acquire() as conn:
            table_map = await _table_map_for_sheet(conn, graph_name, sheet_ref, grain)

        query_text = build_dax_query(
            grain=grain, measures=measures, sdk_filters=sdk_filters, sdk_parameters=sdk_parameters,
            table_map=table_map,
        )
        sdk_case = SdkParityCase(
            id=str(properties.get("case_key") or case_id), workbook_luid=str(properties.get("mu_ref") or ""),
            sheet=sheet_ref or None, grain=grain, measures=measures, filters=sdk_filters, parameters=sdk_parameters,
        )

        try:
            candidate = await target_adapter.evaluate(query_text=query_text, case=sdk_case, workspace=workspace)
        except Exception as exc:  # a broken target is evidence, not a crash -- the same posture case_execution.py already takes
            logger.warning("re-proof of case %s could not re-execute the target side: %s", case_id, exc)
            continue

        expected_content = await artefact_store.content(str(properties["expected_ref"]))
        if expected_content is None:
            continue
        expected = result_set_from_parquet(
            expected_content, case_id=case_id, grain=grain, measures=measures,
            strategy=ExecutionStrategy.EXTRACT_READ,
        )

        diff_result = diff_result_sets(expected, candidate, charter, column_target_map={})
        evidence_artefact = await artefact_store.store(
            kind="mender_reproof_evidence", mu_ref=str(properties.get("mu_ref") or ""), case_id=case_id,
            content=json.dumps({
                "case_id": case_id, "sheet_ref": sheet_ref, "candidate_query": query_text,
                "diff": diff_result.as_dict(),
            }, default=str).encode("utf-8"),
            media_type=EVIDENCE_MEDIA_TYPE, created_by=principal.value,
        )
        verdict_id = new_ulid()
        await writer.write_nodes(
            [NodeWrite(type="Verdict", id=verdict_id, properties={
                "case_ref": case_id, "result": diff_result.result,
                "failing_cells": [c.as_dict() for c in diff_result.failing_cells],
                "evidence_ref": evidence_artefact.id, "sampled": diff_result.sampling is not None,
            })],
            principal=principal,
        )
        results[case_id] = {
            "result": diff_result.result, "verdict_id": verdict_id, "evidence_ref": evidence_artefact.id,
        }
    return results


# ------------------------------------------------------------------- regression check


async def _other_cases_sharing_artefact(
    pool: asyncpg.Pool, graph_name: str, *, workbook_id: str, calc_id: str, exclude: frozenset[str],
) -> tuple[str, ...]:
    """Every other live `ParityCase` in this workbook whose own measures resolve (by
    name, the identical resolution `classify_run`'s own grouping already performs) to
    `calc_id` -- §11.2's own 'a measure used by several sheets', scoped to the artefact
    a repair actually touched, not the whole workbook regardless of artefact (see this
    module's own docstring)."""
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            f"""SELECT id FROM {NODE_INDEX_TABLE}
                 WHERE graph = $1 AND kind = 'node' AND label = 'ParityCase' AND retired_at IS NULL""",
            graph_name,
        )
        all_cases = await hydrate(conn, graph_name, "ParityCase", [row["id"] for row in rows])
        candidates = {
            cid: props for cid, props in all_cases.items()
            if props.get("mu_ref") == workbook_id and cid not in exclude and props.get("expected_ref")
        }
        field_index_by_sheet: dict[str, dict[str, tuple[str, str, dict[str, Any]]]] = {}
        matches: list[str] = []
        for case_id, properties in candidates.items():
            sheet_ref = str(properties.get("sheet_ref"))
            if sheet_ref not in field_index_by_sheet:
                field_index_by_sheet[sheet_ref] = await _worksheet_field_index(conn, graph_name, sheet_ref)
            field_index = field_index_by_sheet[sheet_ref]
            for measure_name in properties.get("measures") or ():
                resolved = field_index.get(str(measure_name))
                if resolved and resolved[0] == "CalculatedField" and resolved[1] == calc_id:
                    matches.append(case_id)
                    break
    return tuple(matches)


async def check_and_revert_regressions(
    pool: asyncpg.Pool,
    graph_name: str,
    writer: GraphWriter,
    artefact_store: ArtefactStore,
    provenance_store: ProvenanceStore,
    target_adapter: TargetAdapter,
    *,
    workbook_id: str,
    calc_id: str | None,
    calc_name: str,
    new_measure_id: str,
    prior_dax: str | None,
    excluded_cases: frozenset[str],
    workspace: str,
    charter: ToleranceCharter,
    principal: Principal,
) -> tuple[str, ...]:
    """§11.2's own 'a repair that makes a previously passing case fail, the artefact is
    reverted first'. `None`/empty return means no regression; otherwise the artefact has
    already been reverted (a fresh `Measure` written with the prior dax) by the time
    this returns, and the tuple names which other cases regressed."""
    if calc_id is None:
        return ()
    other_case_ids = await _other_cases_sharing_artefact(
        pool, graph_name, workbook_id=workbook_id, calc_id=calc_id, exclude=excluded_cases,
    )
    if not other_case_ids:
        return ()

    previously_passing: list[str] = []
    for case_id in other_case_ids:
        verdict = await _latest_verdict(pool, graph_name, case_id)
        if verdict is not None and verdict.get("result") == "PASS":
            previously_passing.append(case_id)
    if not previously_passing:
        return ()

    reproved = await reprove_cases(
        pool, graph_name, writer, artefact_store, target_adapter,
        case_ids=tuple(previously_passing), workspace=workspace, charter=charter, principal=principal,
    )
    regressed = tuple(cid for cid in previously_passing if reproved.get(cid, {}).get("result") != "PASS")
    if not regressed:
        return ()

    if prior_dax is not None:
        await _write_repaired_measure(
            pool, graph_name, writer, provenance_store,
            calc_id=calc_id, name=calc_name, dax=prior_dax, mode=AgentMode.DETERMINISTIC, pattern_ref=None,
            context_hash_value=context_hash(prior_dax.encode("utf-8")), subject_id=calc_id,
            validation_state=f"reverted: the previous repair (measure {new_measure_id}) regressed "
                              f"{len(regressed)} previously-passing case(s)",
            model=None, gateway_request_id=None, tokens_in=None, tokens_out=None, confidence=None,
            principal=principal,
        )
    return regressed


# --------------------------------------------------------------------------- the loop


async def _write_mender_pass(
    pool: asyncpg.Pool, graph_name: str, writer: GraphWriter, *,
    exception_case_id: str, outcome: MenderPassOutcome, evidence_ref: str, principal: Principal,
) -> str:
    pass_id = new_ulid()
    await writer.write_nodes(
        [NodeWrite(type="MenderPass", id=pass_id, properties={
            "exception_case_ref": exception_case_id, "pass_number": outcome.pass_number,
            "strategy": outcome.strategy, "pattern_ref": outcome.pattern_ref, "measure_ref": outcome.measure_id,
            "result": outcome.result, "cases_reproved": list(outcome.cases_reproved),
            "cases_still_failing": list(outcome.cases_still_failing), "evidence_ref": evidence_ref,
            "started_at": outcome.started_at, "finished_at": outcome.finished_at,
        })],
        principal=principal,
    )
    return pass_id


async def mend_exception(
    pool: asyncpg.Pool,
    graph_name: str,
    writer: GraphWriter,
    artefact_store: ArtefactStore,
    provenance_store: ProvenanceStore,
    gateway: Gateway,
    target_adapter: TargetAdapter,
    config_store: MenderConfigStore,
    charter_store: ToleranceCharterStore,
    *,
    exception_case_id: str,
    workspace: str,
    principal: Principal,
) -> dict[str, Any]:
    """The bounded repair loop itself -- §11.2, implemented pass by pass. See this
    module's own docstring for the full account of every design decision below."""
    async with pool.acquire() as conn:
        hydrated = await hydrate(conn, graph_name, "ExceptionCase", [exception_case_id])
    exception_properties = hydrated.get(exception_case_id)
    if exception_properties is None:
        raise MenderError(f"no ExceptionCase '{exception_case_id}'")
    if exception_properties.get("state") != "OPEN":
        raise MenderError(
            f"ExceptionCase '{exception_case_id}' is not OPEN "
            f"(state={exception_properties.get('state')!r}) -- nothing to mend"
        )

    failure_class = str(exception_properties.get("class"))
    workbook_id = str(exception_properties.get("mu_ref"))
    case_refs = tuple(exception_properties.get("case_refs") or ())
    artefact_ref = exception_properties.get("artefact_ref")

    # Story S8.2.2: a real model defect (KEY_MISSING with graph evidence of a missing
    # dimension member, or AGGREGATION with a grain mismatch at the model) is routed to
    # the Foundry before any repair pass is ever attempted -- checked ahead of the (now
    # fallback-only) unconditional KEY_MISSING escalation below, see foundry_routing.py's
    # own docstring for the full reasoning.
    model_defect = await detect_model_defect(
        pool, graph_name, failure_class=failure_class, workbook_id=workbook_id, case_refs=case_refs,
    )
    if model_defect is not None:
        started_at = datetime.now(UTC)
        route_result = await route_to_foundry(
            pool, graph_name, writer, exception_case_id=exception_case_id,
            evidence=model_defect, principal=principal,
        )
        evidence_artefact = await artefact_store.store(
            kind=EVIDENCE_KIND, mu_ref=workbook_id, case_id=(case_refs[0] if case_refs else exception_case_id),
            content=json.dumps({
                "model_defect": model_defect.as_dict(), "route_result": route_result,
            }, default=str).encode("utf-8"),
            media_type=EVIDENCE_MEDIA_TYPE, created_by=principal.value,
        )
        outcome = MenderPassOutcome(
            pass_number=1, strategy="ROUTE_TO_FOUNDRY", result=route_result["outcome"],
            pattern_ref=None, measure_id=None, cases_reproved=(), cases_still_failing=case_refs,
            evidence={"routed_without_a_repair_attempt": True},
            started_at=_iso(started_at), finished_at=_iso(datetime.now(UTC)),
        )
        await _write_mender_pass(
            pool, graph_name, writer, exception_case_id=exception_case_id,
            outcome=outcome, evidence_ref=evidence_artefact.id, principal=principal,
        )
        await writer.set_node_properties(exception_case_id, {"passes_consumed": 1}, principal=principal)
        return {
            "exception_case_id": exception_case_id, "outcome": "routed_to_foundry",
            "route_result": route_result, "passes_consumed": 1, "passes": [outcome.as_dict()],
        }

    # §11.2's own literal escalation rule -- the fallback for a KEY_MISSING case S8.2.2's
    # own model-defect check (above) could not confirm real graph evidence for; never
    # even attempted as a report-side repair either way.
    if failure_class in _ESCALATE_WITHOUT_REPAIR:
        started_at = datetime.now(UTC)
        evidence_artefact = await artefact_store.store(
            kind=EVIDENCE_KIND, mu_ref=workbook_id, case_id=(case_refs[0] if case_refs else exception_case_id),
            content=json.dumps({
                "reason": "KEY_MISSING is a model defect, not a report defect (§11.2) -- "
                          "no real graph evidence of a missing dimension member could be "
                          "confirmed for this case (foundry_routing.py, story S8.2.2), so "
                          "it escalates without a repair attempt instead of routing to "
                          "the Foundry",
            }).encode("utf-8"),
            media_type=EVIDENCE_MEDIA_TYPE, created_by=principal.value,
        )
        outcome = MenderPassOutcome(
            pass_number=1, strategy="ESCALATE_IMMEDIATE", result="KEY_MISSING_MODEL_DEFECT",
            pattern_ref=None, measure_id=None, cases_reproved=(), cases_still_failing=case_refs,
            evidence={"escalated_without_a_repair_attempt": True},
            started_at=_iso(started_at), finished_at=_iso(datetime.now(UTC)),
        )
        await _write_mender_pass(
            pool, graph_name, writer, exception_case_id=exception_case_id,
            outcome=outcome, evidence_ref=evidence_artefact.id, principal=principal,
        )
        await writer.set_node_properties(exception_case_id, {"passes_consumed": 1}, principal=principal)
        return {
            "exception_case_id": exception_case_id, "outcome": "escalated",
            "reason": "key_missing_model_defect", "passes_consumed": 1, "passes": [outcome.as_dict()],
        }

    config = await config_store.latest()
    budget = max(1, config.pass_budget)

    calc = await _resolve_calculated_field(pool, graph_name, artefact_ref)
    calc_id = calc[0] if calc else None
    calc_name = str(calc[1].get("name")) if calc else str(artefact_ref or exception_case_id)
    current_measure = await _current_measure(pool, graph_name, calc_id) if calc_id else None
    current_dax = str(current_measure[1].get("dax") or "") if current_measure else ""

    charter_version = await charter_store.latest()
    charter = charter_version.charter

    cases_still_failing = frozenset(case_refs)
    passes: list[MenderPassOutcome] = []
    pass_number = 0

    for pass_number in range(1, budget + 1):
        strategy = strategy_for_pass(min(pass_number, MAX_PASS_BUDGET_APPLIED))
        started_at = datetime.now(UTC)
        measure_id: str | None = None
        pattern_ref: str | None = None
        evidence: dict[str, Any] = {"strategy": strategy}
        result = "STILL_FAILING"

        if strategy == "PATTERN":
            if calc is not None:
                applied = await apply_pattern_repair(
                    pool, graph_name, writer, provenance_store,
                    calc_id=calc[0], calc_properties=calc[1], principal=principal,
                )
                if applied is None:
                    result = "NO_PATTERN_MATCH"
                else:
                    measure_id, pattern_match = applied
                    pattern_ref = pattern_match.pattern_id
                    evidence["pattern_id"] = pattern_ref
            else:
                result = "NO_PATTERN_MATCH"
                evidence["note"] = "no CalculatedField could be resolved from this exception's own artefact_ref"
        else:
            widened = strategy == "MODEL_WIDENED"
            request = await assemble_repair_context(
                pool, graph_name, artefact_store, exception_properties=exception_properties,
                calc=calc, current_dax=current_dax, widened=widened,
            )
            dax, model_result, detail = await call_model_repair(gateway, request)
            evidence["request"] = request.as_dict()
            evidence["response"] = detail
            if model_result != "OK" or dax is None:
                result = model_result
            else:
                measure_id = await _write_repaired_measure(
                    pool, graph_name, writer, provenance_store,
                    calc_id=calc_id, name=calc_name, dax=dax, mode=AgentMode.GENERATED_PROVED, pattern_ref=None,
                    context_hash_value=request.context_hash(), subject_id=calc_id or exception_case_id,
                    validation_state="rung 1-2 (schema, parse) checked; rung 4 (proof) is this same pass's own re-proof",
                    model=detail.get("model"), gateway_request_id=detail.get("gateway_request_id"),
                    tokens_in=detail.get("tokens_in"), tokens_out=detail.get("tokens_out"),
                    confidence=detail.get("confidence"), principal=principal,
                )

        cases_reproved_this_pass: tuple[str, ...] = ()
        new_still_failing = cases_still_failing
        if measure_id is not None:
            reproved = await reprove_cases(
                pool, graph_name, writer, artefact_store, target_adapter,
                case_ids=case_refs, workspace=workspace, charter=charter, principal=principal,
            )
            evidence["reproved"] = {cid: r["result"] for cid, r in reproved.items()}
            newly_passing = frozenset(cid for cid, r in reproved.items() if r["result"] == "PASS")
            new_still_failing = frozenset(case_refs) - newly_passing

            if calc_id is not None:
                regressed = await check_and_revert_regressions(
                    pool, graph_name, writer, artefact_store, provenance_store, target_adapter,
                    workbook_id=workbook_id, calc_id=calc_id, calc_name=calc_name, new_measure_id=measure_id,
                    prior_dax=current_dax or None, excluded_cases=frozenset(case_refs),
                    workspace=workspace, charter=charter, principal=principal,
                )
                if regressed:
                    evidence["regressed_cases"] = list(regressed)
                    result = "REGRESSED"
                    new_still_failing = cases_still_failing
                    newly_passing = frozenset()

            if result != "REGRESSED":
                cases_reproved_this_pass = tuple(sorted(newly_passing))
                result = "PROVED" if not new_still_failing else "STILL_FAILING"

        finished_at = datetime.now(UTC)
        evidence_artefact = await artefact_store.store(
            kind=EVIDENCE_KIND, mu_ref=workbook_id, case_id=(case_refs[0] if case_refs else exception_case_id),
            content=json.dumps(evidence, default=str).encode("utf-8"),
            media_type=EVIDENCE_MEDIA_TYPE, created_by=principal.value,
        )
        outcome = MenderPassOutcome(
            pass_number=pass_number, strategy=strategy, result=result, pattern_ref=pattern_ref, measure_id=measure_id,
            cases_reproved=cases_reproved_this_pass, cases_still_failing=tuple(sorted(new_still_failing)),
            evidence={}, started_at=_iso(started_at), finished_at=_iso(finished_at),
        )
        await _write_mender_pass(
            pool, graph_name, writer, exception_case_id=exception_case_id,
            outcome=outcome, evidence_ref=evidence_artefact.id, principal=principal,
        )
        passes.append(outcome)
        await writer.set_node_properties(exception_case_id, {"passes_consumed": pass_number}, principal=principal)

        if result == "PROVED":
            await writer.set_node_properties(
                exception_case_id, {"state": "CLOSED", "passes_consumed": pass_number}, principal=principal,
            )
            return {
                "exception_case_id": exception_case_id, "outcome": "closed",
                "passes_consumed": pass_number, "passes": [p.as_dict() for p in passes],
            }

        # An unroutable gateway will not become routable within this same run -- the
        # identical "not retried" reasoning `generation._run_ladder` already gives its
        # own identical error; further passes would only repeat the same failure.
        if result == "MODEL_UNAVAILABLE":
            break

        # §11.2's own "a pass that produces no change in the failing set ends the loop
        # early" -- only once a real repair was actually attempted and re-proved; a pass
        # that never produced a measure at all (no pattern match, a schema/parse
        # failure) always falls through to the next strategy instead.
        if measure_id is not None and new_still_failing == cases_still_failing:
            break

        # §11.2's own "escalate ... on an UNKNOWN class after one model diagnosis" --
        # pass 2 is that one diagnosis; never spend pass 3 repeating it.
        if failure_class == "UNKNOWN" and strategy == "MODEL" and result != "PROVED":
            break

        cases_still_failing = new_still_failing

    return {
        "exception_case_id": exception_case_id, "outcome": "escalated",
        "passes_consumed": pass_number, "passes": [p.as_dict() for p in passes],
    }


class MenderService:
    """Binds `mend_exception` to one pool/graph/writer/artefact store/provenance store/
    gateway/target adapter/config store/charter store -- the identical "pre-bound
    object on app.state" shape `ClassificationService`/`RegressionService` already
    take."""

    def __init__(
        self,
        pool: asyncpg.Pool,
        *,
        graph_name: str,
        writer: GraphWriter,
        artefact_store: ArtefactStore,
        provenance_store: ProvenanceStore,
        gateway: Gateway,
        target_adapter: TargetAdapter,
        config_store: MenderConfigStore,
        charter_store: ToleranceCharterStore,
    ) -> None:
        self._pool = pool
        self._graph = graph_name
        self._writer = writer
        self._artefact_store = artefact_store
        self._provenance_store = provenance_store
        self._gateway = gateway
        self._target_adapter = target_adapter
        self._config_store = config_store
        self._charter_store = charter_store

    async def mend(self, exception_case_id: str, *, workspace: str, principal: Principal) -> dict[str, Any]:
        return await mend_exception(
            self._pool, self._graph, self._writer, self._artefact_store, self._provenance_store,
            self._gateway, self._target_adapter, self._config_store, self._charter_store,
            exception_case_id=exception_case_id, workspace=workspace, principal=principal,
        )


__all__ = [
    "DEFAULT_PASS_BUDGET",
    "EVIDENCE_KIND",
    "EVIDENCE_MEDIA_TYPE",
    "MAX_PASS_BUDGET_APPLIED",
    "OUTPUT_SCHEMA",
    "InMemoryMenderConfigStore",
    "MenderConfig",
    "MenderConfigStore",
    "MenderError",
    "MenderPassOutcome",
    "MenderService",
    "PostgresMenderConfigStore",
    "RepairContext",
    "RepairResponseSchema",
    "apply_pattern_repair",
    "assemble_repair_context",
    "call_model_repair",
    "check_and_revert_regressions",
    "mend_exception",
    "reprove_cases",
    "strategy_for_pass",
]
