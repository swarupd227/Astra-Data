"""Running the §10.3 diff for real, and writing what it finds -- story S7.4.1, closing
F7.4.

This module is `diff.py`'s own graph-coupled other half: it reads a workbook's live
`ParityCase`s and their already-stored `expected_ref`/`candidate_ref` Parquet artefacts
(S7.3.1), resolves a real `Field -> ModelTable` `MAPS_TO` binding for column mapping
when one exists, calls `diff_result_sets` (the one pure step), and writes the result as
a real `Verdict` per case and one `ParityRun` per call -- both already declared in the
ontology since its very first §4.1.1 declaration and never once written by any story
before this one (confirmed by direct research), so **this story needs zero ontology or
migration change**.

**The evidence bundle's own "candidate DAX" is recomputed, not persisted separately.**
S7.3.1/S7.3.2 never persisted the query text or filter/parameter pairs anywhere beyond
the one execution response — only the Parquet rows/columns survive. Rather than reopen
that already-shipped ontology to add a new `ParityCase` property, the evidence bundle's
own query text is recomputed fresh via the identical, deterministic `build_dax_query`
(and a fresh `_table_map_for_sheet` read) the original execution used — a real,
disclosed choice: a `MAPS_TO` binding added or removed between execution and diffing
would change what the evidence bundle shows versus what was literally asked at
execution time. **The executed strategy itself is not recomputed** — it is read back
from `public.execution_observation` (story S7.3.2's own real, persisted execution
history), so "the source strategy used" in evidence is always the true historical fact.

**A case is skipped, not failed, when it has not been executed yet.** `expected_ref`/
`candidate_ref` are absent on a case nobody has run `POST .../:execute-parity-cases`
against — that is not evidence of anything, so it is left out of the run rather than
reported as a manufactured INCONCLUSIVE.

**`ParityRun.suite_ref` is the workbook id directly**, the identical anchor
`ParityCase.mu_ref` already uses (S7.2.1's own "mu_ref anchors directly, not
ReportDefinition" precedent) — not `public.parity_suite`'s own relational row id, which
would need a second query to resolve for no benefit `suite_ref`'s own stated purpose
("which coverage universe was this run over") does not already get from the workbook id
alone.

**`PROVED_BY` is written only when a `ReportDefinition` already exists for this MU** —
honestly skipped otherwise, the identical gap `simulate_charter` (S7.1.1) already
disclosed for the same edge, since case derivation and execution can both run before
any report is ever composed (S7.2.1's own "reads only the source side" precedent).

`VerdictsService.dashboard` (story S7.4.2) is this module's own thin binding onto
`parity_dashboard.py`'s own read-only aggregation — see that module's own docstring.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any

import asyncpg
from astra_adapter import ExecutionStrategy

from .artefacts import ArtefactStore
from .case_derivation import _worksheet_field_index  # same epic (E7); see module docstring
from .case_execution import (
    _table_map_for_sheet,  # same epic (E7); see module docstring
    build_dax_query,
    result_set_from_parquet,
    to_sdk_filters,
    to_sdk_parameters,
)
from .diff import DiffResult, diff_result_sets
from .graph.queries import EDGE_INDEX_TABLE, NODE_INDEX_TABLE
from .lineage import hydrate
from .parity_dashboard import parity_dashboard
from .principal import Principal
from .tolerance_charter import ToleranceCharter
from .writes import EdgeWrite, GraphWriter, NodeWrite

EVIDENCE_MEDIA_TYPE = "application/json"


class VerdictError(Exception):
    """A parity run could not be produced for this workbook."""


# --------------------------------------------------------------------------- graph reads


async def _column_target_map_for_sheet(
    conn: asyncpg.Connection,
    graph: str,
    index: dict[str, tuple[str, str, dict[str, Any]]],
    field_names: tuple[str, ...],
) -> dict[str, str]:
    """§10.3's own "column mapping via MAPS_TO": a real `Field -> ModelTable` binding's
    own `target_column` property, when one exists -- honestly empty in every real
    deployment today, the identical already-disclosed gap `case_execution.
    _table_map_for_sheet` already found for DAX table qualification (`diff.py`'s own
    docstring). A field absent from it keeps its own source name unchanged in
    `diff_result_sets`. Takes the sheet's own already-fetched field ``index`` rather
    than re-querying it, since the caller already has one."""
    field_ids = {name: index[name][1] for name in field_names if name in index and index[name][0] == "Field"}
    if not field_ids:
        return {}
    rows = await conn.fetch(
        f"""SELECT e.id AS edge_id, e.from_id AS field_id
              FROM {EDGE_INDEX_TABLE} e
             WHERE e.graph = $1 AND e.label = 'MAPS_TO' AND e.from_id = ANY($2::text[])
               AND e.retired_at IS NULL""",
        graph, list(field_ids.values()),
    )
    if not rows:
        return {}
    edge_properties = await hydrate(conn, graph, "MAPS_TO", [row["edge_id"] for row in rows])
    id_to_name = {fid: name for name, fid in field_ids.items()}
    out: dict[str, str] = {}
    for row in rows:
        target_column = (edge_properties.get(row["edge_id"]) or {}).get("target_column")
        name = id_to_name.get(row["field_id"])
        if target_column and name:
            out[name] = str(target_column)
    return out


async def _executed_strategy(
    conn: asyncpg.Connection, graph: str, *, case_id: str, side: str
) -> str | None:
    """The real strategy `public.execution_observation` (S7.3.2) recorded the last time
    this side of this case was executed -- the historical fact, not a recompute."""
    value = await conn.fetchval(
        """SELECT strategy FROM public.execution_observation
            WHERE graph = $1 AND case_id = $2 AND side = $3
         ORDER BY recorded_at DESC LIMIT 1""",
        graph, case_id, side,
    )
    return str(value) if value is not None else None


async def _report_definition_for_workbook(
    conn: asyncpg.Connection, graph: str, workbook_id: str
) -> str | None:
    """The identical lookup `simulate_charter` (S7.1.1) already runs -- a workbook can
    have zero, one, or (rarely) more than one `ReportDefinition`; the first live one
    found is used, since `PROVED_BY` names no distinguishing property beyond
    `charter_version`."""
    rows = await conn.fetch(
        f"""SELECT id FROM {NODE_INDEX_TABLE}
         WHERE graph = $1 AND kind = 'node' AND label = 'ReportDefinition' AND retired_at IS NULL""",
        graph,
    )
    reports = await hydrate(conn, graph, "ReportDefinition", [row["id"] for row in rows])
    own_ids = [rid for rid, props in reports.items() if props.get("mu_ref") == workbook_id]
    return own_ids[0] if own_ids else None


# --------------------------------------------------------------------------- one case


async def _diff_one_case(
    pool: asyncpg.Pool,
    graph_name: str,
    artefact_store: ArtefactStore,
    *,
    case_id: str,
    case_properties: dict[str, Any],
    charter: ToleranceCharter,
) -> tuple[DiffResult, dict[str, Any]]:
    """One case's own real diff, and the evidence facts `run_parity_for_workbook` needs
    to assemble its bundle. Returns the `DiffResult` plus a dict of everything else the
    bundle wants that is not already on `DiffResult` itself."""
    sheet_ref = str(case_properties.get("sheet_ref") or "")
    grain = tuple(case_properties.get("grain") or ())
    measures = tuple(case_properties.get("measures") or ())
    filter_ctx = case_properties.get("filter_ctx") or {}
    param_values = case_properties.get("param_values") or {}
    expected_ref = case_properties.get("expected_ref")
    candidate_ref = case_properties.get("candidate_ref")

    expected_content = await artefact_store.content(expected_ref) if expected_ref else None
    candidate_content = await artefact_store.content(candidate_ref) if candidate_ref else None
    if expected_content is None or candidate_content is None:
        raise VerdictError(f"case '{case_id}' is missing a stored result set on one or both sides")

    expected = result_set_from_parquet(
        expected_content, case_id=case_id, grain=grain, measures=measures, strategy=ExecutionStrategy.EXTRACT_READ,
    )
    candidate = result_set_from_parquet(
        candidate_content, case_id=case_id, grain=grain, measures=measures, strategy=ExecutionStrategy.XMLA_DAX,
    )

    async with pool.acquire() as conn:
        field_index = await _worksheet_field_index(conn, graph_name, sheet_ref)
        column_target_map = await _column_target_map_for_sheet(conn, graph_name, field_index, grain + measures)
        # S7.3.1's own real Field -> ModelTable table-qualification lookup, reused
        # verbatim so the recomputed query text matches what execution originally
        # built -- see this module's own docstring on why it is a recompute at all.
        dax_table_map = await _table_map_for_sheet(conn, graph_name, sheet_ref, grain)
        expected_strategy = await _executed_strategy(conn, graph_name, case_id=case_id, side="source")
        candidate_strategy = await _executed_strategy(conn, graph_name, case_id=case_id, side="target")

    query_text = build_dax_query(
        grain=grain, measures=measures,
        sdk_filters=to_sdk_filters(filter_ctx), sdk_parameters=to_sdk_parameters(param_values),
        table_map=dax_table_map,
    )

    diff_result = diff_result_sets(expected, candidate, charter, column_target_map=column_target_map)

    evidence_facts = {
        "sheet_ref": sheet_ref, "grain": list(grain), "measures": list(measures),
        "filter_ctx": filter_ctx, "param_values": param_values,
        "candidate_query": query_text, "expected_strategy": expected_strategy, "candidate_strategy": candidate_strategy,
        "expected_ref": expected_ref, "candidate_ref": candidate_ref,
        "expected_columns": [{"name": c.name, "role": c.role.value, "type": c.type} for c in expected.columns],
        "candidate_columns": [{"name": c.name, "role": c.role.value, "type": c.type} for c in candidate.columns],
    }
    return diff_result, evidence_facts


# --------------------------------------------------------------------------- orchestration


async def run_parity_for_workbook(
    pool: asyncpg.Pool,
    graph_name: str,
    writer: GraphWriter,
    artefact_store: ArtefactStore,
    *,
    workbook_id: str,
    charter: ToleranceCharter,
    charter_version: str,
    principal: Principal,
) -> dict[str, Any]:
    """Diff every already-executed live `ParityCase` for a workbook, write a real
    `Verdict` per case and one `ParityRun`, and `PROVED_BY` the workbook's own
    `ReportDefinition` when one exists (see this module's own docstring)."""
    started_at = datetime.now(UTC)

    async with pool.acquire() as conn:
        case_rows = await conn.fetch(
            f"""SELECT id FROM {NODE_INDEX_TABLE}
             WHERE graph = $1 AND kind = 'node' AND label = 'ParityCase' AND retired_at IS NULL""",
            graph_name,
        )
        all_cases = await hydrate(conn, graph_name, "ParityCase", [row["id"] for row in case_rows])

    cases = {cid: props for cid, props in all_cases.items() if props.get("mu_ref") == workbook_id}
    executed = {cid: props for cid, props in cases.items() if props.get("expected_ref") and props.get("candidate_ref")}
    if not executed:
        raise VerdictError(
            f"workbook '{workbook_id}' has no executed parity cases to diff -- run "
            "POST .../:execute-parity-cases first"
        )

    verdict_ids: list[str] = []
    results: list[dict[str, Any]] = []
    for case_id, case_properties in executed.items():
        diff_result, evidence_facts = await _diff_one_case(
            pool, graph_name, artefact_store, case_id=case_id, case_properties=case_properties, charter=charter,
        )

        expected_record = await artefact_store.get(str(evidence_facts["expected_ref"]))
        candidate_record = await artefact_store.get(str(evidence_facts["candidate_ref"]))
        finished_at = datetime.now(UTC)

        evidence_bundle = {
            "charter_version": charter_version,
            "case_id": case_id, "case_key": case_properties.get("case_key"),
            "sheet_ref": evidence_facts["sheet_ref"],
            "filter_ctx": evidence_facts["filter_ctx"], "param_values": evidence_facts["param_values"],
            "candidate_query": evidence_facts["candidate_query"],
            "expected_strategy": evidence_facts["expected_strategy"],
            "candidate_strategy": evidence_facts["candidate_strategy"],
            "expected_result_hash": expected_record.content_hash if expected_record else None,
            "candidate_result_hash": candidate_record.content_hash if candidate_record else None,
            "expected_columns": evidence_facts["expected_columns"],
            "candidate_columns": evidence_facts["candidate_columns"],
            "diff": diff_result.as_dict(),
            "timings": {
                "started_at": started_at.isoformat(), "finished_at": finished_at.isoformat(),
                "duration_ms": int((finished_at - started_at).total_seconds() * 1000),
            },
        }

        evidence_artefact = await artefact_store.store(
            kind="parity_evidence", mu_ref=workbook_id, case_id=case_id,
            content=json.dumps(evidence_bundle, default=str).encode("utf-8"),
            media_type=EVIDENCE_MEDIA_TYPE, created_by=principal.value,
        )

        verdict_properties = {
            "case_ref": case_id, "result": diff_result.result,
            "failing_cells": [cell.as_dict() for cell in diff_result.failing_cells],
            "evidence_ref": evidence_artefact.id,
        }
        created = await writer.write_nodes(
            [NodeWrite(type="Verdict", properties=verdict_properties)], principal=principal,
        )
        verdict_id = str(created[0]["properties"]["id"])
        verdict_ids.append(verdict_id)
        results.append({
            "case_id": case_id, "verdict_id": verdict_id, "result": diff_result.result,
            "failing_cell_count": diff_result.failing_cell_count, "evidence_ref": evidence_artefact.id,
        })

    finished_at = datetime.now(UTC)
    run_properties = {
        "suite_ref": workbook_id, "charter_version": charter_version,
        "started": started_at.isoformat(), "finished": finished_at.isoformat(),
        "verdicts": verdict_ids,
    }
    created_run = await writer.write_nodes([NodeWrite(type="ParityRun", properties=run_properties)], principal=principal)
    run_id = str(created_run[0]["properties"]["id"])

    async with pool.acquire() as conn:
        report_id = await _report_definition_for_workbook(conn, graph_name, workbook_id)
    if report_id is not None:
        await writer.write_edge(
            EdgeWrite(
                type="PROVED_BY", from_id=report_id, to_id=run_id,
                properties={"charter_version": charter_version},
            ),
            principal=principal,
        )

    outcomes = [r["result"] for r in results]
    return {
        "workbook_id": workbook_id, "run_id": run_id, "charter_version": charter_version,
        "cases_diffed": len(results), "pass": outcomes.count("PASS"), "fail": outcomes.count("FAIL"),
        "inconclusive": outcomes.count("INCONCLUSIVE"), "proved_by_report": report_id,
        "results": results,
    }


async def latest_parity_run(
    pool: asyncpg.Pool, graph_name: str, *, workbook_id: str
) -> dict[str, Any] | None:
    """The workbook's own most recent `ParityRun` and its `Verdict`s -- found directly
    by `ParityRun.suite_ref == workbook_id` (this module's own decision
    "`suite_ref` is the workbook id directly"), not via `PROVED_BY`/`ReportDefinition`.
    A workbook can be derived, executed and diffed long before any report is ever
    composed (S7.2.1's own "reads only the source side" precedent) -- requiring a
    `ReportDefinition` first would wrongly gate this read on report composition having
    already happened, the identical reasoning ADR 0051 already gave for anchoring
    `ParityCase` on `mu_ref` directly."""
    async with pool.acquire() as conn:
        run_rows = await conn.fetch(
            f"""SELECT id FROM {NODE_INDEX_TABLE}
             WHERE graph = $1 AND kind = 'node' AND label = 'ParityRun' AND retired_at IS NULL""",
            graph_name,
        )
        all_runs = await hydrate(conn, graph_name, "ParityRun", [row["id"] for row in run_rows])
        runs = {rid: props for rid, props in all_runs.items() if props.get("suite_ref") == workbook_id}
        if not runs:
            return None
        latest_run_id = max(runs, key=lambda rid: str(runs[rid].get("finished") or runs[rid].get("started") or ""))
        run_properties = runs[latest_run_id]
        verdict_ids = list(run_properties.get("verdicts") or [])
        verdicts = await hydrate(conn, graph_name, "Verdict", verdict_ids)

    return {
        "run_id": latest_run_id, "workbook_id": workbook_id, **run_properties,
        "verdicts": [{"id": vid, **props} for vid, props in verdicts.items()],
    }


class VerdictsService:
    """Binds `run_parity_for_workbook`/`latest_parity_run` to one pool/graph/writer/
    artefact store -- the identical "pre-bound object on app.state" shape
    `CaseExecutionService`/`CaseDerivationService` already take."""

    def __init__(
        self, pool: asyncpg.Pool, *, graph_name: str, writer: GraphWriter, artefact_store: ArtefactStore,
    ) -> None:
        self._pool = pool
        self._graph = graph_name
        self._writer = writer
        self._artefact_store = artefact_store

    async def run(
        self, workbook_id: str, *, charter: ToleranceCharter, charter_version: str, principal: Principal
    ) -> dict[str, Any]:
        return await run_parity_for_workbook(
            self._pool, self._graph, self._writer, self._artefact_store,
            workbook_id=workbook_id, charter=charter, charter_version=charter_version, principal=principal,
        )

    async def latest(self, workbook_id: str) -> dict[str, Any] | None:
        return await latest_parity_run(self._pool, self._graph, workbook_id=workbook_id)

    async def dashboard(self, workbook_id: str) -> dict[str, Any] | None:
        """Story S7.4.2's own Parity Dashboard -- see `parity_dashboard.py`."""
        return await parity_dashboard(self._pool, self._graph, workbook_id=workbook_id)


__all__ = [
    "EVIDENCE_MEDIA_TYPE",
    "VerdictError",
    "VerdictsService",
    "latest_parity_run",
    "run_parity_for_workbook",
]
