"""Dual execution -- story S7.3.1, opening F7.3, spec §10.2.

    "As a parity engineer, I want each case executed on the source via the adapter and
    on the target via XMLA and the results stored, so that the comparison is between two
    executions, never between an execution and a re-implementation.

    Acceptance criteria:
    - Target side: DAX EVALUATE over XMLA against the dev or test model, with filters
      and parameter values applied per §10.2; query text stored
    - Source side: adapter execute_case with the chosen strategy; strategy stored
    - Both ResultSets stored as Parquet in the artefact store with content hash;
      retention per charter
    - Execution is parallel per MU with a configurable concurrency per Fabric workspace
      (default 8) and per Tableau site (default 4)"

§10.2 itself, verbatim: *"The expected side is produced by the source adapter's
executeCase... and the candidate side by the target executor as a DAX query over XMLA.
Both return a ResultSet: an ordered list of column descriptors (name, role, type) and
rows."* The identical, symmetric shape (`astra_adapter.ResultSet`) is used for both
sides here, on purpose -- it is what lets a future diff (F7.4) treat expected and
candidate the same way.

**This module does not build the diff engine or a verdict.** §10.3-§10.6
(normalisation, the row/key diff, sampling, visual parity, regression) are F7.4's own
later, explicit scope -- confirmed directly against the backlog's own F7.3 section,
which has exactly two stories (S7.3.1, this one, and S7.3.2, "INCONCLUSIVE... the
orchestrator retries once with a longer budget"). Retry-with-a-longer-budget is
S7.3.2's own scope too, not built here: a case that fails or times out on either side is
recorded as `INCONCLUSIVE` once, honestly, and stored -- exactly what §10.2 itself
already gives a failed execution ("a timeout... yields INCONCLUSIVE, not FAIL"), without
the retry loop the next story owns.

**Neither `SourceAdapter.execute_case` nor the new `TargetAdapter.evaluate` takes the
Tolerance Charter.** Source-side strategy selection is entirely the adapter's own
capability-driven decision (confirmed directly: `FixtureSourceAdapter.execute_case`
chooses from its own declared capabilities, not a passed-in charter); target-side
execution has exactly one strategy (`ExecutionStrategy.XMLA_DAX`, added to
`astra_adapter.proof` by this story). This module therefore never reads the charter at
all -- S7.2.1's own case derivation is the only place `params.enumerate_max_values`
matters.

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
from typing import Any

import asyncpg
import pyarrow as pa
import pyarrow.parquet as pq
from astra_adapter import ExecutionOutcome, ExecutionStrategy, ResultSet
from astra_adapter import ParityCase as SdkParityCase
from astra_adapter.contract import SourceAdapter
from astra_adapter.target_contract import TargetAdapter

from .artefacts import ArtefactStore
from .case_derivation import _worksheet_field_index  # same epic (E7); see module docstring
from .graph.queries import EDGE_INDEX_TABLE, NODE_INDEX_TABLE
from .lineage import hydrate
from .principal import Principal
from .writes import GraphWriter

#: §10.2's own bullet, R1 defaults.
DEFAULT_FABRIC_CONCURRENCY = 8
DEFAULT_TABLEAU_CONCURRENCY = 4

RESULT_SET_MEDIA_TYPE = "application/vnd.apache.parquet"


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
    *, case_id: str, strategy: ExecutionStrategy, reason: str, detail: dict[str, Any] | None = None
) -> ResultSet:
    """A case's own execution never propagates an exception -- the identical per-item
    failure isolation `harvest/runner.py`'s own `_harvest_workbook` already established,
    applied to one side of one case instead of one workbook."""
    return ResultSet(
        case_id=case_id, columns=(), rows=(), strategy=strategy,
        interface_version="", adapter_name="none", adapter_version="",
        outcome=ExecutionOutcome.INCONCLUSIVE, reason=reason, detail=detail or {},
    )


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

    async def run_source() -> ResultSet:
        async with site_semaphore:
            try:
                return await source_adapter.execute_case(sdk_case)
            except Exception as exc:
                return _inconclusive(case_id=sdk_case.id, strategy=ExecutionStrategy.EXTRACT_READ, reason=str(exc))

    async def run_target() -> ResultSet:
        async with workspace_semaphore:
            try:
                return await target_adapter.evaluate(query_text=query_text, case=sdk_case, workspace=workspace)
            except Exception as exc:
                return _inconclusive(
                    case_id=sdk_case.id, strategy=ExecutionStrategy.XMLA_DAX, reason=str(exc),
                    detail={"dax_query": query_text},
                )

    expected, candidate = await asyncio.gather(run_source(), run_target())

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
            site_semaphore=site_semaphore, workspace_semaphore=workspace_semaphore, principal=principal,
        )

    results = list(await asyncio.gather(*(one(cid, props) for cid, props in cases.items())))
    return {"workbook_id": workbook_id, "cases_executed": len(results), "results": results}


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
    ) -> None:
        self._pool = pool
        self._graph = graph_name
        self._writer = writer
        self._artefact_store = artefact_store
        self._source_adapter = source_adapter
        self._target_adapter = target_adapter
        self._fabric_concurrency = max(1, fabric_concurrency)
        self._tableau_concurrency = max(1, tableau_concurrency)
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
            principal=principal,
        )


__all__ = [
    "DEFAULT_FABRIC_CONCURRENCY",
    "DEFAULT_TABLEAU_CONCURRENCY",
    "RESULT_SET_MEDIA_TYPE",
    "CaseExecutionError",
    "CaseExecutionService",
    "build_dax_query",
    "execute_cases_for_workbook",
    "result_set_to_parquet",
    "to_sdk_filters",
    "to_sdk_parameters",
]
