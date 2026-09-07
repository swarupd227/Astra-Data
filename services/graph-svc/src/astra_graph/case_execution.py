"""Dual execution -- stories S7.3.1 and S7.3.2, closing F7.3, spec §10.2.

    S7.3.1, "As a parity engineer, I want each case executed on the source via the
    adapter and on the target via XMLA and the results stored, so that the comparison is
    between two executions, never between an execution and a re-implementation.

    Acceptance criteria:
    - Target side: DAX EVALUATE over XMLA against the dev or test model, with filters
      and parameter values applied per §10.2; query text stored
    - Source side: adapter execute_case with the chosen strategy; strategy stored
    - Both ResultSets stored as Parquet in the artefact store with content hash;
      retention per charter
    - Execution is parallel per MU with a configurable concurrency per Fabric workspace
      (default 8) and per Tableau site (default 4)"

    S7.3.2, "As a platform engineer, I want INCONCLUSIVE to be a first-class outcome
    distinct from FAIL, so that infrastructure problems do not look like migration
    defects.

    Acceptance criteria:
    - Timeout, adapter error, executor error and sampling shortfall produce
      INCONCLUSIVE with the reason class; the orchestrator retries once with a longer
      budget
    - Inconclusive rate is a Platform Health metric with an alert threshold (default 2%)"

§10.2 itself, verbatim: *"The expected side is produced by the source adapter's
executeCase... and the candidate side by the target executor as a DAX query over XMLA.
Both return a ResultSet: an ordered list of column descriptors (name, role, type) and
rows... Both executions are scheduled by the orchestrator with retry and timeout; a
timeout on either side yields INCONCLUSIVE, not FAIL, and is retried once with a longer
budget before being surfaced."* The identical, symmetric shape (`astra_adapter.
ResultSet`) is used for both sides here, on purpose -- it is what lets a future diff
(F7.4) treat expected and candidate the same way.

**This module does not build the diff engine or a verdict.** §10.3-§10.6
(normalisation, the row/key diff, sampling, visual parity, regression) are F7.4's own
later, explicit scope -- confirmed directly against the backlog's own F7.3 section,
which has exactly these two stories. A case that fails on either side, even after its
one retry, is recorded as `INCONCLUSIVE`, honestly, and stored -- exactly what §10.2
itself already gives a failed execution ("a timeout... yields INCONCLUSIVE, not FAIL"),
without a diff or a verdict, which stay F7.4's own later scope.

**The retry-with-a-longer-budget rule is broadened from §10.2's own timeout-only wording
to every orchestrator-classified reason class -- a backlog elaboration, not a
contradiction.** §10.2's own prose ties the retry to a timeout specifically; the
backlog's own S7.3.2 AC reads as one retry rule for the whole sentence ("Timeout,
adapter error, executor error and sampling shortfall produce INCONCLUSIVE... the
orchestrator retries once with a longer budget"), covering every one of its own four
reason classes. Implemented the broader way -- the identical "broaden the spec's one
worked scenario to the general case" reading S7.2.1's own filter-context elaboration
already used (ADR 0051). **The one exception**: an adapter's own honest capability
decline (e.g. `FixtureSourceAdapter.execute_case` returning `INCONCLUSIVE` because
neither `extract_read` nor `live_query` is claimed, no exception raised at all) is never
retried -- a longer budget fixes a slow warehouse, not a capability the deployment does
not have, and retrying it would only repeat the identical decline for no benefit. This
is also why that case's own `ResultSet.reason_class` is left `None`: it was never one of
this story's own four orchestrator-classified causes to begin with (see
`InconclusiveReason`'s own docstring in `astra_adapter.proof`).

**"Sampling shortfall" is declared but never produced today.** §10.4 (sampling) is
F7.4's own later, unbuilt scope, so no path through this module can fail that way yet --
the fourth `InconclusiveReason` member exists for the AC's own completeness and for
F7.4 to raise the moment it exists, not because anything here raises it.

**Every side-execution is recorded as an observation, win or lose.** `record_execution_
observation` appends one row per side per case per execution to `public.
execution_observation` (migration v0028) -- the identical "append-only, an inconclusive
rate is always computed live from the complete history this platform has actually seen,
never a maintained counter" discipline `patterns.record_observation`/`calibration.
PostgresCalibrationStore.record` already established for their own metrics. `inconclusive_
rate` is the AC's own "Platform Health metric": total executions versus INCONCLUSIVE
ones over a trailing window (an operational alert should reflect what the platform is
doing now, not a spike from months ago), against `DEFAULT_INCONCLUSIVE_RATE_THRESHOLD =
0.02` -- the AC's own literal default. Surfaced from `GET /v1/platform/health`'s own
existing computed-on-read shape (`routes_platform.py`), the same footing every other
section of that route already has -- not a new metrics-exposition mechanism, since none
exists anywhere in this codebase yet (a real Prometheus/OTel exporter is S12.3.1's own
later, explicitly-scoped-elsewhere work) and the *screen* called Platform Health is
S12.3.2's own later, unbuilt console surface (E12/F12.3) -- this route is only, as its
own docstring already says, "the graph service's contribution."

**Neither `SourceAdapter.execute_case` nor `TargetAdapter.evaluate` takes a charter --
this orchestrator does, but only `astra_adapter.proof.ExecutionCharter`, never the
Tolerance Charter.** Source-side strategy selection is entirely the adapter's own
capability-driven decision (confirmed directly: `FixtureSourceAdapter.execute_case`
chooses from its own declared capabilities, not a passed-in charter); target-side
execution has exactly one strategy (`ExecutionStrategy.XMLA_DAX`, added to
`astra_adapter.proof` by S7.3.1). `CaseExecutionService` now holds one `ExecutionCharter`
(story S7.3.2, for its own `timeout_seconds`) -- a real, disclosed gap on its own: no
route or store persists one anywhere yet, so it is always the dataclass's own default
(`ExecutionCharter()`, `DEFAULT_TIMEOUT_SECONDS = 120.0`) unless a caller constructs the
service with a different one directly. §4.4's own Tolerance Charter (diff tolerances) is
a completely different document this module still never reads -- S7.2.1's own case
derivation is the only place `params.enumerate_max_values` matters.

**The query text is built here, not by the adapter.** `TargetAdapter.evaluate` takes an
already-built DAX string; building it needs graph access (field names, and whatever real
`Field -> ModelTable` binding exists) an adapter has no business having -- the identical
"platform decides what, adapter decides how" split `TmdlBundle`/`Compositor` already draw
for TMDL emission.

**Real DAX text, honestly unqualified where no real table binding exists.**
`build_dax_query` produces §10.2's own worked-example shape (`EVALUATE SUMMARIZECOLUMNS`,
`TREATAS`/`FILTER` for filters and parameter values, named measure expressions, an
`ORDER BY`) for real, from a case's own real grain/measures/filters/parameters. Column
references are table-qualified via a real `Field -> ModelTable` `MAPS_TO` lookup when one
exists; the identical, already-disclosed gap this codebase has found six times over
(`compositor.py`'s own field-well binding, most recently) means that lookup is honestly
empty in every real deployment today, so every reference falls back to using the field's
own name as its own table -- a placeholder, not a guess dressed up as a real binding. The
query text is stored either way (`ResultSet.detail["dax_query"]`), so what was actually
asked is always auditable, table-qualified or not.

**"Retention per charter" is a real, disclosed gap this module does not close.**
Confirmed by direct research: neither §10 nor §4.4's own Tolerance Charter schema
(`tolerance_charter.py` -- numeric, nulls, dates, strings, ordering, rows, sampling,
params, waiver) has a retention concept at all; the only "retention" anywhere in the spec
is an unrelated, tenant-wide Data Handling setting for the AI inference boundary. This
module stores Parquet artefacts and never prunes them -- the identical "nothing prunes
today, and that is deliberate" posture `retention.py` already established for a
different kind of retention, extended honestly to a charter field that does not exist
yet rather than inventing one.

**Parquet, via pyarrow directly.** `result_set_to_parquet` writes a `ResultSet`'s own
columns/rows straight to Parquet bytes -- no pandas, which would be a second dependency
for what `pyarrow.parquet.write_table` already does alone. `ArtefactStore.store`'s own
`content_hash` (S2.4.2, sha256 over the stored bytes) is the AC's own "content hash" --
already free, no new logic needed for it.

**Concurrency is two independent, persistent pools -- a first for this codebase.**
`harvest/runner.py`'s own `_harvest_all` bounds parallel work with one
`asyncio.Semaphore` per run. This story needs two independent named bounds instead (a
Fabric workspace's own XMLA concurrency, a Tableau site's own adapter concurrency), and
they must persist across calls -- two different MUs executing at once against the same
workspace share the same real bound, not a fresh one each time. `CaseExecutionService`
holds both pools for its own lifetime; each side of a case's own dual execution acquires
only the one semaphore relevant to it (the source call under the site's own pool, the
target call under the workspace's own pool), not both, since the two calls already run
concurrently via `asyncio.gather`.

**`ParityCase.state` is left alone.** It already carries S7.2.1/S7.2.2's own origin tag
(`"DERIVED"`/`"MANUAL"`); execution populates `expected_ref`/`candidate_ref` instead --
both already declared by §4.1.1 ("Result set from the source/target side") and unused
until this story -- which is already a real, sufficient "has this case been executed"
signal on its own, without overwriting a fact a different property already owns.
"""

from __future__ import annotations

import asyncio
import io
from collections.abc import Awaitable, Callable
from typing import Any

import asyncpg
import pyarrow as pa
import pyarrow.parquet as pq
from astra_adapter import (
    ExecutionCharter,
    ExecutionOutcome,
    ExecutionStrategy,
    InconclusiveReason,
    ResultSet,
)
from astra_adapter import ParityCase as SdkParityCase
from astra_adapter.contract import SourceAdapter
from astra_adapter.target_contract import TargetAdapter

from .artefacts import ArtefactStore
from .case_derivation import _worksheet_field_index  # same epic (E7); see module docstring
from .graph.queries import EDGE_INDEX_TABLE, NODE_INDEX_TABLE
from .ids import new_ulid
from .lineage import hydrate
from .principal import Principal
from .writes import GraphWriter

#: §10.2's own bullet, R1 defaults.
DEFAULT_FABRIC_CONCURRENCY = 8
DEFAULT_TABLEAU_CONCURRENCY = 4

RESULT_SET_MEDIA_TYPE = "application/vnd.apache.parquet"

#: "Retried once with a longer budget" (§10.2, story S7.3.2) -- neither the spec nor the
#: backlog says how much longer, so this module owns the number, the same "invented,
#: disclosed bound" footing `MAX_FILTER_VALUES_PER_FILTER` (S7.2.1) and `DEPLOY_RETRY_
#: DEFAULT`'s own backoff schedule (S6.1.2) already set for their own unspecified knobs.
DEFAULT_RETRY_TIMEOUT_MULTIPLIER = 2.0

EXECUTION_OBSERVATION_TABLE = "public.execution_observation"

#: The AC's own literal default: "an alert threshold (default 2%)".
DEFAULT_INCONCLUSIVE_RATE_THRESHOLD = 0.02

#: Not specified by the AC; an operational alert should reflect what the platform is
#: doing now, not an inconclusive spike from months ago that has long since stopped
#: recurring -- a disclosed, invented window, the same footing the retry multiplier above
#: already has.
DEFAULT_INCONCLUSIVE_WINDOW_HOURS = 24.0


class CaseExecutionError(Exception):
    """Cases could not be executed for this workbook."""


# --------------------------------------------------------------------------- conversion


def to_sdk_filters(filter_ctx: dict[str, Any]) -> tuple[tuple[str, str], ...]:
    """The case's own filter context (S7.2.1/S7.2.2) as §6.2's flat ``(field, value)``
    pairs -- "applied as the sheet applies them ... through vf_ parameters"
    (`astra_adapter.proof.ParityCase.filters`'s own docstring). A `categorical_value`
    context is exactly one pair; the default context re-states every categorical
    filter's own harvested members as repeated pairs on the same field -- the same
    repeated-parameter shape a real Tableau `vf_` call already uses for a multi-select
    filter, so no richer shape was needed here."""
    kind = filter_ctx.get("kind")
    if kind == "categorical_value":
        field_ref = filter_ctx.get("field_ref")
        value = filter_ctx.get("value")
        if field_ref and value is not None:
            return ((str(field_ref), str(value)),)
        return ()

    pairs: list[tuple[str, str]] = []
    for filter_properties in filter_ctx.get("filters") or ():
        field_ref = filter_properties.get("field_ref")
        if not field_ref:
            continue
        if filter_properties.get("type") == "categorical":
            members = (filter_properties.get("values") or {}).get("members") or ()
            for member in members:
                pairs.append((str(field_ref), str(member)))
        else:
            # A non-categorical filter's own concrete value, when the harvester
            # recorded one -- disclosed as a best-effort read, since range/relative-
            # date/top_n/condition filters each carry a differently-shaped `values`
            # document §4.1.1 does not standardise further.
            values = filter_properties.get("values") or {}
            for key in ("value", "min", "anchor"):
                if key in values:
                    pairs.append((str(field_ref), str(values[key])))
                    break
    return tuple(pairs)


def to_sdk_parameters(param_values: dict[str, Any]) -> tuple[tuple[str, str], ...]:
    return tuple((str(name), str(value)) for name, value in param_values.items() if value is not None)


# ------------------------------------------------------------------------------- DAX


def build_dax_query(
    *,
    grain: tuple[str, ...],
    measures: tuple[str, ...],
    sdk_filters: tuple[tuple[str, str], ...],
    sdk_parameters: tuple[tuple[str, str], ...],
    table_map: dict[str, str],
) -> str:
    """§10.2's own worked-example shape, from real grain/measures/filters/parameters.
    ``table_map`` is field name -> DAX table name, from a real `Field -> ModelTable`
    binding when one exists (honestly empty today -- see this module's own docstring);
    a field absent from it is qualified against its own name, a disclosed placeholder,
    not a guess."""

    def column_ref(field: str) -> str:
        table = table_map.get(field, field)
        return f"'{table}'[{field}]"

    body: list[str] = [f"    {column_ref(dim)}," for dim in grain]

    grouped: dict[str, list[str]] = {}
    for field, value in (*sdk_filters, *sdk_parameters):
        grouped.setdefault(field, []).append(value)
    for field, values in grouped.items():
        if len(values) == 1:
            body.append(f'    TREATAS({{"{values[0]}"}}, {column_ref(field)}),')
        else:
            quoted = ", ".join(f'"{v}"' for v in values)
            body.append(f"    FILTER(ALL({column_ref(field)}), {column_ref(field)} IN {{{quoted}}}),")

    for measure in measures:
        body.append(f'    "{measure}", [{measure}],')

    if body:
        body[-1] = body[-1].rstrip(",")

    lines = ["EVALUATE", "SUMMARIZECOLUMNS(", *body, ")"]
    if grain:
        lines.append(f"ORDER BY {', '.join(column_ref(dim) for dim in grain)}")
    return "\n".join(lines)


# ----------------------------------------------------------------------------- Parquet


def result_set_to_parquet(result: ResultSet) -> bytes:
    """The AC's own storage format. Content hash is already free -- `ArtefactStore.
    store`'s own sha256 over these exact bytes."""
    names = [column.name for column in result.columns]
    arrays = [
        pa.array([row[index] if index < len(row) else None for row in result.rows])
        for index in range(len(names))
    ]
    table = pa.table(dict(zip(names, arrays, strict=True))) if names else pa.table({})
    buffer = io.BytesIO()
    pq.write_table(table, buffer)
    return buffer.getvalue()


# ------------------------------------------------------------------------------- graph reads


async def _maps_to(
    conn: asyncpg.Connection, graph: str, from_ids: list[str], to_label: str
) -> dict[str, list[str]]:
    """Every live `MAPS_TO` edge out of `from_ids` whose target is `to_label` -- the
    identical query `compositor._maps_to` (E6) already runs, written again rather than
    imported since this is a different epic's own module."""
    if not from_ids:
        return {}
    rows = await conn.fetch(
        f"""
        SELECT e.from_id AS parent, e.to_id AS target
          FROM {EDGE_INDEX_TABLE} e
          JOIN {NODE_INDEX_TABLE} n ON n.graph = e.graph AND n.id = e.to_id
             AND n.kind = 'node' AND n.label = $3 AND n.retired_at IS NULL
         WHERE e.graph = $1 AND e.label = 'MAPS_TO' AND e.from_id = ANY($2::text[])
           AND e.retired_at IS NULL
        """,
        graph, list(dict.fromkeys(from_ids)), to_label,
    )
    out: dict[str, list[str]] = {}
    for row in rows:
        out.setdefault(row["parent"], []).append(row["target"])
    return out


async def _table_map_for_sheet(
    conn: asyncpg.Connection, graph: str, sheet_ref: str, field_names: tuple[str, ...]
) -> dict[str, str]:
    """Real DAX table qualification for this sheet's own field names, where a real
    `Field -> ModelTable` binding exists -- honestly empty in every real deployment
    today (see this module's own docstring)."""
    index = await _worksheet_field_index(conn, graph, sheet_ref)
    field_ids = [index[name][1] for name in field_names if name in index and index[name][0] == "Field"]
    if not field_ids:
        return {}
    bindings = await _maps_to(conn, graph, field_ids, "ModelTable")
    table_ids = sorted({tid for ids in bindings.values() for tid in ids})
    tables = await hydrate(conn, graph, "ModelTable", table_ids)

    table_map: dict[str, str] = {}
    for name in field_names:
        found = index.get(name)
        if found is None or found[0] != "Field":
            continue
        bound = bindings.get(found[1])
        if not bound:
            continue
        table_properties = tables.get(bound[0])
        if table_properties:
            table_map[name] = str(table_properties.get("name") or name)
    return table_map


async def _resolve_site(conn: asyncpg.Connection, graph: str, workbook_id: str) -> str | None:
    """The Tableau site this workbook belongs to, via its real Site -> Project ->
    Workbook containment chain."""
    project_row = await conn.fetchrow(
        f"""SELECT e.from_id AS project_id FROM {EDGE_INDEX_TABLE} e
             WHERE e.graph = $1 AND e.label = 'CONTAINS' AND e.to_id = $2 AND e.retired_at IS NULL""",
        graph, workbook_id,
    )
    if project_row is None:
        return None
    site_row = await conn.fetchrow(
        f"""SELECT e.from_id AS site_id FROM {EDGE_INDEX_TABLE} e
             WHERE e.graph = $1 AND e.label = 'CONTAINS' AND e.to_id = $2 AND e.retired_at IS NULL""",
        graph, project_row["project_id"],
    )
    if site_row is None:
        return None
    site_properties = (await hydrate(conn, graph, "Site", [site_row["site_id"]])).get(site_row["site_id"])
    return str(site_properties["name"]) if site_properties else None


# --------------------------------------------------------------------------- orchestration


def _inconclusive(
    *,
    case_id: str,
    strategy: ExecutionStrategy,
    reason_class: InconclusiveReason,
    reason: str,
    detail: dict[str, Any] | None = None,
) -> ResultSet:
    """A case's own execution never propagates an exception -- the identical per-item
    failure isolation `harvest/runner.py`'s own `_harvest_workbook` already established,
    applied to one side of one case instead of one workbook. `reason_class` is required
    here (story S7.3.2's own AC: "the reason class") -- every INCONCLUSIVE this function
    produces is one this orchestrator itself classified, never an adapter's own honest
    decline (see this module's own docstring)."""
    return ResultSet(
        case_id=case_id, columns=(), rows=(), strategy=strategy,
        interface_version="", adapter_name="none", adapter_version="",
        outcome=ExecutionOutcome.INCONCLUSIVE, reason=reason, reason_class=reason_class,
        detail=detail or {},
    )


async def _attempt_once(
    call: Callable[[], Awaitable[ResultSet]],
    budget_seconds: float,
    *,
    attempt: int,
    case_id: str,
    strategy: ExecutionStrategy,
    reason_class_on_error: InconclusiveReason,
    detail: dict[str, Any] | None,
) -> ResultSet:
    """One bounded call. A hang becomes `TIMEOUT`; any other raise becomes
    `reason_class_on_error` (`ADAPTER_ERROR` for the source, `EXECUTOR_ERROR` for the
    target) -- §10.2's own two named causes for a failed execution, made concrete."""
    try:
        return await asyncio.wait_for(call(), timeout=budget_seconds)
    except TimeoutError:
        return _inconclusive(
            case_id=case_id, strategy=strategy, reason_class=InconclusiveReason.TIMEOUT,
            reason=f"timed out after {budget_seconds:.0f}s (attempt {attempt} of 2)",
            detail={**(detail or {}), "attempt": attempt, "budget_seconds": budget_seconds},
        )
    except Exception as exc:  # a broken adapter/executor is INCONCLUSIVE, not a crash
        return _inconclusive(
            case_id=case_id, strategy=strategy, reason_class=reason_class_on_error, reason=str(exc),
            detail={**(detail or {}), "attempt": attempt},
        )


async def _run_with_retry(
    call: Callable[[], Awaitable[ResultSet]],
    *,
    case_id: str,
    strategy: ExecutionStrategy,
    reason_class_on_error: InconclusiveReason,
    timeout_seconds: float,
    retry_timeout_seconds: float,
    detail: dict[str, Any] | None = None,
) -> tuple[ResultSet, int]:
    """§10.2: "retried once with a longer budget before being surfaced" -- broadened to
    every orchestrator-classified reason class, not only a timeout (see this module's
    own docstring). Returns the surfaced result and how many attempts it took (1 or 2),
    the latter for `record_execution_observation`'s own audit trail."""
    first = await _attempt_once(
        call, timeout_seconds, attempt=1, case_id=case_id, strategy=strategy,
        reason_class_on_error=reason_class_on_error, detail=detail,
    )
    if first.outcome is ExecutionOutcome.OK or first.reason_class is None:
        # A success, or the adapter's own honest decline -- neither is retried; see this
        # module's own docstring for why an unclassified INCONCLUSIVE is left alone.
        return first, 1
    second = await _attempt_once(
        call, retry_timeout_seconds, attempt=2, case_id=case_id, strategy=strategy,
        reason_class_on_error=reason_class_on_error, detail=detail,
    )
    return second, 2


async def _execute_one_case(
    pool: asyncpg.Pool,
    graph_name: str,
    writer: GraphWriter,
    artefact_store: ArtefactStore,
    source_adapter: SourceAdapter,
    target_adapter: TargetAdapter,
    *,
    case_id: str,
    case_properties: dict[str, Any],
    workbook_id: str,
    workbook_luid: str,
    workspace: str,
    site_semaphore: asyncio.Semaphore,
    workspace_semaphore: asyncio.Semaphore,
    charter: ExecutionCharter,
    principal: Principal,
) -> dict[str, Any]:
    sheet_ref = str(case_properties.get("sheet_ref") or "")
    grain = tuple(case_properties.get("grain") or ())
    measures = tuple(case_properties.get("measures") or ())
    sdk_filters = to_sdk_filters(case_properties.get("filter_ctx") or {})
    sdk_parameters = to_sdk_parameters(case_properties.get("param_values") or {})

    async with pool.acquire() as conn:
        table_map = await _table_map_for_sheet(conn, graph_name, sheet_ref, grain)

    query_text = build_dax_query(
        grain=grain, measures=measures, sdk_filters=sdk_filters,
        sdk_parameters=sdk_parameters, table_map=table_map,
    )

    # A stable id (case_key, S7.2.1), not the graph node's own ULID: a re-derivation
    # changes the node id but never the conceptual case, and a per-case charter
    # execution-strategy override (`ExecutionCharter.per_case`) is keyed on this id.
    sdk_case = SdkParityCase(
        id=str(case_properties.get("case_key") or case_id),
        workbook_luid=workbook_luid, sheet=sheet_ref or None,
        grain=grain, measures=measures, filters=sdk_filters, parameters=sdk_parameters,
    )

    retry_timeout_seconds = charter.timeout_seconds * DEFAULT_RETRY_TIMEOUT_MULTIPLIER

    async def run_source() -> tuple[ResultSet, int]:
        async with site_semaphore:
            return await _run_with_retry(
                lambda: source_adapter.execute_case(sdk_case),
                case_id=sdk_case.id, strategy=ExecutionStrategy.EXTRACT_READ,
                reason_class_on_error=InconclusiveReason.ADAPTER_ERROR,
                timeout_seconds=charter.timeout_seconds, retry_timeout_seconds=retry_timeout_seconds,
            )

    async def run_target() -> tuple[ResultSet, int]:
        async with workspace_semaphore:
            return await _run_with_retry(
                lambda: target_adapter.evaluate(query_text=query_text, case=sdk_case, workspace=workspace),
                case_id=sdk_case.id, strategy=ExecutionStrategy.XMLA_DAX,
                reason_class_on_error=InconclusiveReason.EXECUTOR_ERROR,
                timeout_seconds=charter.timeout_seconds, retry_timeout_seconds=retry_timeout_seconds,
                detail={"dax_query": query_text},
            )

    (expected, expected_attempts), (candidate, candidate_attempts) = await asyncio.gather(
        run_source(), run_target()
    )

    await record_execution_observation(
        pool, graph_name, mu_ref=workbook_id, case_id=case_id, side="source",
        strategy=expected.strategy, outcome=expected.outcome, reason_class=expected.reason_class,
        attempts=expected_attempts, created_by=principal.value,
    )
    await record_execution_observation(
        pool, graph_name, mu_ref=workbook_id, case_id=case_id, side="target",
        strategy=candidate.strategy, outcome=candidate.outcome, reason_class=candidate.reason_class,
        attempts=candidate_attempts, created_by=principal.value,
    )

    expected_artefact = await artefact_store.store(
        kind="result_set_expected", mu_ref=workbook_id, case_id=case_id,
        content=result_set_to_parquet(expected), media_type=RESULT_SET_MEDIA_TYPE,
        adapter_name=expected.adapter_name, adapter_version=expected.adapter_version,
        interface_version=expected.interface_version, created_by=principal.value,
    )
    candidate_artefact = await artefact_store.store(
        kind="result_set_candidate", mu_ref=workbook_id, case_id=case_id,
        content=result_set_to_parquet(candidate), media_type=RESULT_SET_MEDIA_TYPE,
        adapter_name=candidate.adapter_name, adapter_version=candidate.adapter_version,
        interface_version=candidate.interface_version, created_by=principal.value,
    )

    await writer.set_node_properties(
        case_id, {"expected_ref": expected_artefact.id, "candidate_ref": candidate_artefact.id},
        principal=principal,
    )

    return {
        "case_id": case_id,
        "expected_ref": expected_artefact.id,
        "candidate_ref": candidate_artefact.id,
        "expected_outcome": expected.outcome.value,
        "candidate_outcome": candidate.outcome.value,
        "expected_reason_class": expected.reason_class.value if expected.reason_class else None,
        "candidate_reason_class": candidate.reason_class.value if candidate.reason_class else None,
        "expected_attempts": expected_attempts,
        "candidate_attempts": candidate_attempts,
        "source_strategy": expected.strategy.value,
        "query_text": query_text,
    }


async def execute_cases_for_workbook(
    pool: asyncpg.Pool,
    graph_name: str,
    writer: GraphWriter,
    artefact_store: ArtefactStore,
    source_adapter: SourceAdapter,
    target_adapter: TargetAdapter,
    *,
    workbook_id: str,
    workspace: str,
    site_semaphore: asyncio.Semaphore,
    workspace_semaphore: asyncio.Semaphore,
    charter: ExecutionCharter,
    principal: Principal,
) -> dict[str, Any]:
    """Execute every live `ParityCase` for a workbook, source and target sides in
    parallel per case, cases parallel per MU under the two named concurrency pools."""
    async with pool.acquire() as conn:
        workbook_properties = (await hydrate(conn, graph_name, "Workbook", [workbook_id])).get(workbook_id)
        if workbook_properties is None:
            raise CaseExecutionError(f"no Workbook '{workbook_id}'")
        workbook_luid = str(workbook_properties.get("luid") or "")

        case_rows = await conn.fetch(
            f"""SELECT id FROM {NODE_INDEX_TABLE}
             WHERE graph = $1 AND kind = 'node' AND label = 'ParityCase' AND retired_at IS NULL""",
            graph_name,
        )
        all_cases = await hydrate(conn, graph_name, "ParityCase", [row["id"] for row in case_rows])

    cases = {cid: props for cid, props in all_cases.items() if props.get("mu_ref") == workbook_id}
    if not cases:
        raise CaseExecutionError(
            f"workbook '{workbook_id}' has no live parity cases to execute -- derive them first"
        )

    async def one(case_id: str, case_properties: dict[str, Any]) -> dict[str, Any]:
        return await _execute_one_case(
            pool, graph_name, writer, artefact_store, source_adapter, target_adapter,
            case_id=case_id, case_properties=case_properties, workbook_id=workbook_id,
            workbook_luid=workbook_luid, workspace=workspace,
            site_semaphore=site_semaphore, workspace_semaphore=workspace_semaphore,
            charter=charter, principal=principal,
        )

    results = list(await asyncio.gather(*(one(cid, props) for cid, props in cases.items())))
    return {"workbook_id": workbook_id, "cases_executed": len(results), "results": results}


# --------------------------------------------------------------------------- observability


async def record_execution_observation(
    pool: asyncpg.Pool,
    graph_name: str,
    *,
    mu_ref: str,
    case_id: str,
    side: str,
    strategy: ExecutionStrategy,
    outcome: ExecutionOutcome,
    reason_class: InconclusiveReason | None,
    attempts: int,
    created_by: str,
) -> None:
    """Append-only, the identical footing `patterns.record_observation`/`calibration.
    PostgresCalibrationStore.record` already set -- `inconclusive_rate` is always
    computed live from the complete history this platform has actually seen, never a
    maintained counter. One row per side per case per execution, win or lose, so the
    rate has a real denominator, not only a count of failures."""
    async with pool.acquire() as conn:
        await conn.execute(
            f"""INSERT INTO {EXECUTION_OBSERVATION_TABLE}
             (id, graph, mu_ref, case_id, side, strategy, outcome, reason_class, attempts,
              created_by, recorded_at)
             VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, now())""",
            f"exeobs_{new_ulid()}", graph_name, mu_ref, case_id, side, strategy.value,
            outcome.value, reason_class.value if reason_class else None, attempts, created_by,
        )


async def inconclusive_rate(
    pool: asyncpg.Pool,
    graph_name: str,
    *,
    window_hours: float = DEFAULT_INCONCLUSIVE_WINDOW_HOURS,
    threshold: float = DEFAULT_INCONCLUSIVE_RATE_THRESHOLD,
) -> dict[str, Any]:
    """The AC's own Platform Health metric: total executions versus INCONCLUSIVE ones,
    recomputed live over the trailing window -- see this module's own docstring for why
    a window, not all-time."""
    async with pool.acquire() as conn:
        totals = await conn.fetchrow(
            f"""SELECT count(*) AS total,
                       count(*) FILTER (WHERE outcome = 'INCONCLUSIVE') AS inconclusive
                  FROM {EXECUTION_OBSERVATION_TABLE}
                 WHERE graph = $1 AND recorded_at >= now() - ($2 * interval '1 hour')""",
            graph_name, window_hours,
        )
        by_reason_rows = await conn.fetch(
            f"""SELECT reason_class, count(*) AS n
                  FROM {EXECUTION_OBSERVATION_TABLE}
                 WHERE graph = $1 AND recorded_at >= now() - ($2 * interval '1 hour')
                   AND outcome = 'INCONCLUSIVE' AND reason_class IS NOT NULL
              GROUP BY reason_class""",
            graph_name, window_hours,
        )
    total = (totals["total"] if totals else 0) or 0
    inconclusive = (totals["inconclusive"] if totals else 0) or 0
    rate = (inconclusive / total) if total else 0.0
    return {
        "window_hours": window_hours,
        "total": total,
        "inconclusive": inconclusive,
        "rate": rate,
        "threshold": threshold,
        "alert": rate > threshold,
        "by_reason": {row["reason_class"]: row["n"] for row in by_reason_rows},
    }


class CaseExecutionService:
    """Binds dual execution to one pool/graph/writer/artefact store/pair of adapters --
    the identical "pre-bound object on app.state" shape `Compositor`/
    `ToleranceCharterService`/`CaseDerivationService` already take. Also the one place
    the AC's own two concurrency pools live, persistently, across every call -- see this
    module's own docstring."""

    def __init__(
        self,
        pool: asyncpg.Pool,
        *,
        graph_name: str,
        writer: GraphWriter,
        artefact_store: ArtefactStore,
        source_adapter: SourceAdapter | None,
        target_adapter: TargetAdapter,
        fabric_concurrency: int = DEFAULT_FABRIC_CONCURRENCY,
        tableau_concurrency: int = DEFAULT_TABLEAU_CONCURRENCY,
        execution_charter: ExecutionCharter | None = None,
    ) -> None:
        self._pool = pool
        self._graph = graph_name
        self._writer = writer
        self._artefact_store = artefact_store
        self._source_adapter = source_adapter
        self._target_adapter = target_adapter
        self._fabric_concurrency = max(1, fabric_concurrency)
        self._tableau_concurrency = max(1, tableau_concurrency)
        self._charter = execution_charter or ExecutionCharter()
        self._workspace_semaphores: dict[str, asyncio.Semaphore] = {}
        self._site_semaphores: dict[str, asyncio.Semaphore] = {}

    def _workspace_semaphore(self, workspace: str) -> asyncio.Semaphore:
        semaphore = self._workspace_semaphores.get(workspace)
        if semaphore is None:
            semaphore = asyncio.Semaphore(self._fabric_concurrency)
            self._workspace_semaphores[workspace] = semaphore
        return semaphore

    def _site_semaphore(self, site: str) -> asyncio.Semaphore:
        semaphore = self._site_semaphores.get(site)
        if semaphore is None:
            semaphore = asyncio.Semaphore(self._tableau_concurrency)
            self._site_semaphores[site] = semaphore
        return semaphore

    async def execute(self, workbook_id: str, *, workspace: str, principal: Principal) -> dict[str, Any]:
        if self._source_adapter is None:
            raise CaseExecutionError("no source adapter is enabled on this deployment")
        async with self._pool.acquire() as conn:
            site = await _resolve_site(conn, self._graph, workbook_id)
        if site is None:
            raise CaseExecutionError(f"workbook '{workbook_id}' has no resolvable Tableau site")
        return await execute_cases_for_workbook(
            self._pool, self._graph, self._writer, self._artefact_store,
            self._source_adapter, self._target_adapter,
            workbook_id=workbook_id, workspace=workspace,
            site_semaphore=self._site_semaphore(site),
            workspace_semaphore=self._workspace_semaphore(workspace),
            charter=self._charter, principal=principal,
        )

    async def inconclusive_rate(
        self,
        *,
        window_hours: float = DEFAULT_INCONCLUSIVE_WINDOW_HOURS,
        threshold: float = DEFAULT_INCONCLUSIVE_RATE_THRESHOLD,
    ) -> dict[str, Any]:
        """The AC's own Platform Health metric -- see the module-level `inconclusive_
        rate` function for what this computes."""
        return await inconclusive_rate(
            self._pool, self._graph, window_hours=window_hours, threshold=threshold
        )


__all__ = [
    "DEFAULT_FABRIC_CONCURRENCY",
    "DEFAULT_INCONCLUSIVE_RATE_THRESHOLD",
    "DEFAULT_INCONCLUSIVE_WINDOW_HOURS",
    "DEFAULT_RETRY_TIMEOUT_MULTIPLIER",
    "DEFAULT_TABLEAU_CONCURRENCY",
    "EXECUTION_OBSERVATION_TABLE",
    "RESULT_SET_MEDIA_TYPE",
    "CaseExecutionError",
    "CaseExecutionService",
    "build_dax_query",
    "execute_cases_for_workbook",
    "inconclusive_rate",
    "record_execution_observation",
    "result_set_to_parquet",
    "to_sdk_filters",
    "to_sdk_parameters",
]
