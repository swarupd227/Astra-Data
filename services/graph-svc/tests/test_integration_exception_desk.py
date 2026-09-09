"""The Exception Desk, against real PostgreSQL + Apache AGE -- story S8.3.1, opening
F8.3, continuing E8.

What only the real stack can answer: that the queue really enriches a real
`ExceptionCase` with its real train position (a real `IN_TRAIN` edge, reversed), its
real site (the real Site -> Project -> Workbook chain) and its real age, and really
filters and orders by them; that the case page really assembles a real evidence bundle
(failing cells, the real key-set diff, filter context, parameter values), the real
current artefact (DAX/M alongside its real source calc) and the real `MenderPass`
history; that bulk assign really writes `ExceptionCase.assignee` for the first time this
property has ever been driven; that a real patch really writes a new `Measure`, really
re-proves, and really closes the case only when every one of its own cases passes; that
redesign really closes with a real Desktop commit hash or really routes to the Foundry
(the identical mechanism S8.2.2 already built); that a model-defect decision routes the
same way; that a source-defect decision really notifies (locally, honestly) and really
requires the owner's own real sign-off before a fix is recorded; and that every decision
really writes a real `GateDecision(gate="G3")` with a real rationale, readable by the
report owner's own real role gate.
"""

from __future__ import annotations

import asyncio
import json
import os
import threading
from collections.abc import Callable, Coroutine
from pathlib import Path
from typing import Any

import pytest

pytestmark = pytest.mark.integration

asyncpg = pytest.importorskip("asyncpg")

from astra_adapter.target_fake import FixtureTargetAdapter  # noqa: E402

from astra_graph.artefacts import PostgresArtefactStore  # noqa: E402
from astra_graph.case_execution import RESULT_SET_MEDIA_TYPE, result_set_to_parquet  # noqa: E402
from astra_graph.case_execution_query import (  # noqa: E402
    build_dax_query,
    to_sdk_filters,
    to_sdk_parameters,
)
from astra_graph.config import Settings  # noqa: E402
from astra_graph.errors import ElementNotFoundError, InvalidRequestError  # noqa: E402
from astra_graph.exception_desk import ExceptionDeskError, ExceptionDeskService  # noqa: E402
from astra_graph.graph import AgeGraphRepository, create_pool  # noqa: E402
from astra_graph.graph.queries import NODE_INDEX_TABLE, accessor  # noqa: E402
from astra_graph.ids import new_ulid  # noqa: E402
from astra_graph.lineage import hydrate  # noqa: E402
from astra_graph.migrations import run as run_migrations  # noqa: E402
from astra_graph.ontology import EDGE_LABELS, NODE_LABELS  # noqa: E402
from astra_graph.principal import Principal  # noqa: E402
from astra_graph.provenance import PostgresProvenanceStore  # noqa: E402
from astra_graph.tolerance_charter import PostgresToleranceCharterStore  # noqa: E402
from astra_graph.writes import EdgeWrite, GraphWriter, NodeWrite  # noqa: E402

PRINCIPAL = Principal("agent:harvester", run_id="run-harvester")
MIGRATION_ENGINEER = Principal("user:engineer@artizent.example")
REPORT_OWNER = Principal("user:owner@client.example")

_WORKSPACE = "dev"

_MARGIN_CALC_AST: dict[str, Any] = {
    "kind": "AGGREGATE", "name": "SUM",
    "children": [{"kind": "REFERENCE", "name": "Margin", "children": []}],
}


def _settings(graph_name: str) -> Settings:
    return Settings(
        postgres_host=os.environ.get("ASTRA_POSTGRES_HOST", "localhost"),
        postgres_port=int(os.environ.get("ASTRA_POSTGRES_PORT", "5432")),
        postgres_db=os.environ.get("ASTRA_POSTGRES_DB", "astra"),
        postgres_user=os.environ.get("ASTRA_POSTGRES_USER", "astra"),
        postgres_password=os.environ.get("ASTRA_POSTGRES_PASSWORD", "astra_local_dev_only"),
        graph_name=graph_name,
        env="test",
        log_level="WARNING",
        pool_min_size=1,
        pool_max_size=6,
        scheduler_enabled=False,
    )


def _run_off_loop(factory: Callable[[], Coroutine[Any, Any, Any]]) -> Any:
    box: dict[str, Any] = {}

    def runner() -> None:
        try:
            box["value"] = asyncio.run(factory())
        except BaseException as exc:
            box["error"] = exc

    thread = threading.Thread(target=runner)
    thread.start()
    thread.join()
    if "error" in box:
        raise box["error"]
    return box.get("value")


async def _create_graph(conn: asyncpg.Connection, graph: str) -> None:
    await conn.execute("LOAD 'age'")
    await conn.execute('SET search_path = ag_catalog, "$user", public')
    await conn.execute("SELECT ag_catalog.create_graph($1)", graph)
    for label in sorted(NODE_LABELS):
        await conn.execute("SELECT ag_catalog.create_vlabel($1, $2)", graph, label)
        await conn.execute(f'CREATE INDEX ON {graph}."{label}" USING BTREE ({accessor("id")})')
    for label in sorted(EDGE_LABELS):
        await conn.execute("SELECT ag_catalog.create_elabel($1, $2)", graph, label)


@pytest.fixture
def settings() -> Settings:
    config = _settings(f"astra_exdesk_{new_ulid()[10:22].lower()}")

    async def setup() -> bool:
        try:
            conn = await asyncpg.connect(dsn=config.dsn, timeout=3)
        except Exception:
            return False
        try:
            await run_migrations(conn)
            await _create_graph(conn, config.graph_name)
        finally:
            await conn.close()
        return True

    async def teardown() -> None:
        conn = await asyncpg.connect(dsn=config.dsn)
        try:
            await conn.execute("LOAD 'age'")
            for table in (
                "public.estate_edge_index", "public.estate_element_index", "public.estate_event",
                "public.artefacts", "public.provenance",
            ):
                await conn.execute(f"DELETE FROM {table} WHERE graph = $1", config.graph_name)
            await conn.execute("SELECT ag_catalog.drop_graph($1, true)", config.graph_name)
        finally:
            await conn.close()

    if not _run_off_loop(setup):
        pytest.skip("PostgreSQL with Apache AGE not reachable")

    yield config
    _run_off_loop(teardown)


async def _write(writer: GraphWriter, type_: str, **properties: Any) -> str:
    created = await writer.write_nodes([NodeWrite(type=type_, properties=properties)], principal=PRINCIPAL)
    return str(created[0]["properties"]["id"])


async def _write_props(writer: GraphWriter, type_: str, properties: dict[str, Any]) -> str:
    """The identical write `_write` performs, for a property set carrying a reserved
    Python keyword (`class`) that cannot be spelled as a `**kwargs` name at a call site."""
    created = await writer.write_nodes([NodeWrite(type=type_, properties=properties)], principal=PRINCIPAL)
    return str(created[0]["properties"]["id"])


async def _edge(writer: GraphWriter, type_: str, source: str, target: str, **props: Any) -> None:
    await writer.write_edge(
        EdgeWrite(type=type_, from_id=source, to_id=target, properties=props), principal=PRINCIPAL
    )


@pytest.fixture
async def estate(settings: Settings, tmp_path: Path):
    """One workbook (with a real Site/Project chain), one sheet, one CalculatedField
    ("SUM(a)" shape) mapped to a real, deliberately "broken" Measure -- the shared base
    every test in this file needs. No ReleaseTrain/ModelFamily by default -- each test
    that needs one adds it, so "no train"/"no family" stays a real, tested case too.
    """
    pool = await create_pool(settings)
    try:
        repository = AgeGraphRepository(pool, graph_name=settings.graph_name)
        writer = GraphWriter(repository, event_source="/astra/graph-svc")
        artefact_store = PostgresArtefactStore(pool, graph_name=settings.graph_name)
        provenance_store = PostgresProvenanceStore(pool, graph_name=settings.graph_name)
        charter_store = PostgresToleranceCharterStore(pool, graph_name=settings.graph_name)
        target_adapter = FixtureTargetAdapter(repo_path=tmp_path / "repo", workspace_root=tmp_path / "workspaces")

        suffix = new_ulid()[10:18].lower()
        site = await _write(writer, "Site", luid=f"s-{suffix}", name=f"rqa-{suffix}")
        project = await _write(writer, "Project", luid=f"p-{suffix}", name="Risk Core")
        await _edge(writer, "CONTAINS", site, project)
        book = await _write(writer, "Workbook", luid=f"wb-{suffix}", name="Daily VaR", revision="1")
        await _edge(writer, "CONTAINS", project, book)

        datasource = await _write(
            writer, "Datasource", name="VaR ds", type="published", luid=f"ds-{suffix}", extract_flag=True,
        )
        desk = await _write(writer, "Field", name="Desk", datatype="string", role="dimension")
        await _edge(writer, "HAS_FIELD", datasource, desk)
        margin_calc = await _write(
            writer, "CalculatedField", name="MarginCalc", formula="SUM([Margin])", formula_ast=_MARGIN_CALC_AST,
        )
        await _edge(writer, "HAS_FIELD", datasource, margin_calc)

        sheet = await _write(
            writer, "Worksheet", name="Bar sheet", mark_type="bar",
            rows_shelf=["Desk"], cols_shelf=["MarginCalc"], marks_shelf=[],
        )
        await _edge(writer, "CONTAINS", book, sheet)
        await _edge(writer, "USES_DATASOURCE", sheet, datasource)

        measure = await _write(
            writer, "Measure", name="MarginCalc", dax="SUM([WrongField])", provenance_ref=f"prov_seed_{suffix}",
        )
        await _edge(writer, "MAPS_TO", margin_calc, measure)

        yield {
            "pool": pool, "settings": settings, "writer": writer, "artefact_store": artefact_store,
            "provenance_store": provenance_store, "charter_store": charter_store, "target_adapter": target_adapter,
            "site": site, "site_name": f"rqa-{suffix}", "project": project, "workbook": book,
            "datasource": datasource, "desk": desk, "margin_calc": margin_calc, "sheet": sheet, "measure": measure,
        }
    finally:
        await pool.close()


async def _query_text_and_result(
    pool, graph_name, target_adapter, *, workbook_id, sheet_ref, case_key, filter_ctx, param_values,
):
    from astra_adapter import ParityCase as SdkParityCase

    from astra_graph.case_execution import _table_map_for_sheet

    grain, measures = ("Desk",), ("MarginCalc",)
    sdk_filters = to_sdk_filters(filter_ctx)
    sdk_parameters = to_sdk_parameters(param_values)
    async with pool.acquire() as conn:
        table_map = await _table_map_for_sheet(conn, graph_name, sheet_ref, grain)
    query_text = build_dax_query(
        grain=grain, measures=measures, sdk_filters=sdk_filters, sdk_parameters=sdk_parameters, table_map=table_map,
    )
    sdk_case = SdkParityCase(
        id=case_key, workbook_luid=workbook_id, sheet=sheet_ref, grain=grain, measures=measures,
        filters=sdk_filters, parameters=sdk_parameters,
    )
    return await target_adapter.evaluate(query_text=query_text, case=sdk_case, workspace=_WORKSPACE)


async def _write_case(estate: dict[str, Any], *, matching: bool) -> tuple[str, str]:
    """A real `ParityCase` naming `grain=(Desk,)`. `matching=True` stores the exact real
    `evaluate()` output as `expected_ref` -- a guaranteed real PASS on re-proof (the
    identical determinism trick `test_integration_mender.py` already established, since
    the fixture target's own `evaluate()` is seeded by the query text alone, never by a
    Measure's own DAX). `matching=False` stores an altered copy -- a guaranteed FAIL."""
    from dataclasses import replace

    writer, artefact_store = estate["writer"], estate["artefact_store"]
    pool, graph_name = estate["pool"], estate["settings"].graph_name
    case_key = f"case_{new_ulid()}"
    filter_ctx: dict[str, Any] = {"kind": "default"}
    param_values: dict[str, Any] = {"AsOf": "2026-01-01"}
    case_id = await _write(
        writer, "ParityCase", mu_ref=estate["workbook"], sheet_ref=estate["sheet"], grain=["Desk"],
        measures=["MarginCalc"], filter_ctx=filter_ctx, param_values=param_values,
        case_key=case_key, state="EXECUTED",
    )
    real = await _query_text_and_result(
        pool, graph_name, estate["target_adapter"], workbook_id=estate["workbook"],
        sheet_ref=estate["sheet"], case_key=case_key, filter_ctx=filter_ctx, param_values=param_values,
    )
    if matching:
        stored = real
    else:
        altered_rows = tuple((*row[:-1], float(row[-1]) + 999_999.0) for row in real.rows)
        stored = replace(real, rows=altered_rows)
    expected_bytes = result_set_to_parquet(stored)
    expected_artefact = await artefact_store.store(
        kind="exception_desk_test_expected", mu_ref=estate["workbook"], case_id=case_id,
        content=expected_bytes, media_type=RESULT_SET_MEDIA_TYPE, created_by=PRINCIPAL.value,
    )
    await writer.set_node_properties(case_id, {"expected_ref": expected_artefact.id}, principal=PRINCIPAL)
    return case_id, case_key


async def _write_fail_verdict(estate: dict[str, Any], *, case_id: str) -> None:
    writer, artefact_store = estate["writer"], estate["artefact_store"]
    bundle: dict[str, Any] = {
        "diff": {
            "failing_cells": [{"row": {"Desk": "Desk-0"}, "measure": "MarginCalc", "expected": 100.0, "candidate": None}],
            "missing_keys": [["Desk-9"]], "extra_keys": [["Desk-8"]],
        },
        "filter_ctx": {"kind": "default"},
        "expected_columns": [{"name": "MarginCalc", "type": "double"}],
        "candidate_columns": [{"name": "MarginCalc", "type": "double"}],
    }
    evidence = await artefact_store.store(
        kind="exception_desk_test_verdict_evidence", mu_ref=estate["workbook"], case_id=case_id,
        content=json.dumps(bundle).encode("utf-8"), media_type="application/json", created_by=PRINCIPAL.value,
    )
    await _write(writer, "Verdict", case_ref=case_id, result="FAIL", failing_cells=bundle["diff"]["failing_cells"], evidence_ref=evidence.id)


async def _open_exception(
    estate: dict[str, Any], *, failure_class: str, case_ids: list[str], artefact_ref: str | None = None,
) -> str:
    writer, artefact_store = estate["writer"], estate["artefact_store"]
    evidence = await artefact_store.store(
        kind="exception_desk_test_classification_evidence", mu_ref=estate["workbook"], case_id=case_ids[0],
        content=b"{}", media_type="application/json", created_by=PRINCIPAL.value,
    )
    return await _write_props(writer, "ExceptionCase", {
        "mu_ref": estate["workbook"], "class": failure_class, "state": "OPEN",
        "evidence_ref": evidence.id, "artefact_ref": artefact_ref or estate["margin_calc"], "case_refs": case_ids,
        "classification_signals": {"note": "test-seeded"},
    })


def _service(estate: dict[str, Any]) -> ExceptionDeskService:
    return ExceptionDeskService(
        estate["pool"], graph_name=estate["settings"].graph_name, writer=estate["writer"],
        artefact_store=estate["artefact_store"], provenance_store=estate["provenance_store"],
        target_adapter=estate["target_adapter"], charter_store=estate["charter_store"],
    )


async def _hydrate_one(pool: asyncpg.Pool, graph_name: str, label: str, node_id: str) -> dict[str, Any]:
    async with pool.acquire() as conn:
        hydrated = await hydrate(conn, graph_name, label, [node_id])
    return hydrated[node_id]


async def _gate_decisions_for(pool: asyncpg.Pool, graph_name: str, subject_ref: str) -> list[dict[str, Any]]:
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            f"""SELECT id FROM {NODE_INDEX_TABLE}
                 WHERE graph = $1 AND kind = 'node' AND label = 'GateDecision' AND retired_at IS NULL""",
            graph_name,
        )
        decisions = await hydrate(conn, graph_name, "GateDecision", [row["id"] for row in rows])
    return [{"id": did, **props} for did, props in decisions.items() if props.get("subject_ref") == subject_ref]


async def _write_mender_pass(estate: dict[str, Any], *, exception_case_id: str, pass_number: int, strategy: str, result: str) -> str:
    return await _write(
        estate["writer"], "MenderPass", exception_case_ref=exception_case_id, pass_number=pass_number,
        strategy=strategy, result=result, cases_reproved=[], cases_still_failing=[],
        started_at="2026-01-01T00:00:00.000Z", finished_at="2026-01-01T00:00:01.000Z",
    )


# ------------------------------------------------------------------------------- queue


async def test_queue_lists_open_and_blocked_but_not_closed(estate) -> None:
    open_case, _k = await _write_case(estate, matching=True)
    blocked_case, _k2 = await _write_case(estate, matching=True)
    closed_case, _k3 = await _write_case(estate, matching=True)
    open_id = await _open_exception(estate, failure_class="NULL_HANDLING", case_ids=[open_case])
    blocked_id = await _open_exception(estate, failure_class="AGGREGATION", case_ids=[blocked_case])
    closed_id = await _open_exception(estate, failure_class="DATE_GRAIN", case_ids=[closed_case])
    await estate["writer"].set_node_properties(blocked_id, {"state": "BLOCKED"}, principal=PRINCIPAL)
    await estate["writer"].set_node_properties(closed_id, {"state": "CLOSED"}, principal=PRINCIPAL)

    entries = await _service(estate).queue()
    ids = {e["id"] for e in entries}
    assert open_id in ids
    assert blocked_id in ids
    assert closed_id not in ids

    open_entry = next(e for e in entries if e["id"] == open_id)
    assert open_entry["mu_ref"] == estate["workbook"]
    assert open_entry["class"] == "NULL_HANDLING"
    assert open_entry["site"] == estate["site_name"]
    assert open_entry["age_seconds"] is not None and open_entry["age_seconds"] >= 0
    assert open_entry["train_id"] is None  # no ReleaseTrain in this estate


async def test_queue_filters_by_class_and_assignee(estate) -> None:
    case_a, _ka = await _write_case(estate, matching=True)
    case_b, _kb = await _write_case(estate, matching=True)
    id_a = await _open_exception(estate, failure_class="NULL_HANDLING", case_ids=[case_a])
    id_b = await _open_exception(estate, failure_class="AGGREGATION", case_ids=[case_b])
    await _service(estate).bulk_assign(exception_case_ids=(id_a,), assignee="alex@artizent.example", principal=MIGRATION_ENGINEER)

    by_class = await _service(estate).queue(failure_class="AGGREGATION")
    assert {e["id"] for e in by_class} == {id_b}

    by_assignee = await _service(estate).queue(assignee="alex@artizent.example")
    assert {e["id"] for e in by_assignee} == {id_a}


async def test_queue_orders_by_train_sequence_then_age(estate) -> None:
    train = await _write(estate["writer"], "ReleaseTrain", name="Train A")
    await _edge(estate["writer"], "IN_TRAIN", estate["workbook"], train, sequence=2, state="IN_PROGRESS")

    # A second workbook, sequenced ahead (sequence=1) in the same train.
    suffix = new_ulid()[10:18].lower()
    second_book = await _write(estate["writer"], "Workbook", luid=f"wb2-{suffix}", name="Weekly VaR", revision="1")
    await _edge(estate["writer"], "CONTAINS", estate["project"], second_book)
    await _edge(estate["writer"], "IN_TRAIN", second_book, train, sequence=1, state="IN_PROGRESS")

    case_a, _ka = await _write_case(estate, matching=True)
    id_a = await _open_exception(estate, failure_class="NULL_HANDLING", case_ids=[case_a])  # sequence 2

    case_b, _kb = await _write_case(estate, matching=True)
    id_b = await _write_props(estate["writer"], "ExceptionCase", {
        "mu_ref": second_book, "class": "NULL_HANDLING", "state": "OPEN",
        "artefact_ref": estate["margin_calc"], "case_refs": [case_b],
        "classification_signals": {},
    })  # sequence 1

    entries = await _service(estate).queue()
    ordered_ids = [e["id"] for e in entries if e["id"] in (id_a, id_b)]
    assert ordered_ids == [id_b, id_a]  # sequence 1 before sequence 2


# --------------------------------------------------------------------------- case detail


async def test_case_detail_assembles_evidence_artefact_and_pass_history(estate) -> None:
    case_id, _k = await _write_case(estate, matching=True)
    await _write_fail_verdict(estate, case_id=case_id)
    exception_id = await _open_exception(estate, failure_class="NULL_HANDLING", case_ids=[case_id])
    await _write_mender_pass(estate, exception_case_id=exception_id, pass_number=1, strategy="PATTERN", result="NO_PATTERN_MATCH")
    await _write_mender_pass(estate, exception_case_id=exception_id, pass_number=2, strategy="MODEL", result="MODEL_UNAVAILABLE")

    detail = await _service(estate).case_detail(exception_id)
    assert detail["id"] == exception_id
    assert detail["class"] == "NULL_HANDLING"
    assert detail["site"] == estate["site_name"]

    evidence = detail["evidence"]
    assert evidence["failing_cells"][0]["measure"] == "MarginCalc"
    assert evidence["missing_keys"] == [["Desk-9", case_id]]
    assert evidence["extra_keys"] == [["Desk-8", case_id]]
    assert evidence["filter_ctx"] == {"kind": "default"}
    assert evidence["param_values"][case_id] == {"AsOf": "2026-01-01"}

    artefact = detail["artefact"]
    assert artefact["calc_id"] == estate["margin_calc"]
    assert artefact["calc_name"] == "MarginCalc"
    assert artefact["source_formula"] == "SUM([Margin])"
    assert artefact["current_dax"] == "SUM([WrongField])"

    passes = detail["mender_passes"]
    assert [p["pass_number"] for p in passes] == [1, 2]
    assert [p["strategy"] for p in passes] == ["PATTERN", "MODEL"]


async def test_case_detail_refuses_an_unknown_case(estate) -> None:
    with pytest.raises(ElementNotFoundError):
        await _service(estate).case_detail(new_ulid())


# ------------------------------------------------------------------------- bulk assign


async def test_bulk_assign_sets_assignee_and_skips_unknown_ids(estate) -> None:
    case_id, _k = await _write_case(estate, matching=True)
    exception_id = await _open_exception(estate, failure_class="NULL_HANDLING", case_ids=[case_id])
    unknown_id = new_ulid()

    updated = await _service(estate).bulk_assign(
        exception_case_ids=(exception_id, unknown_id), assignee="jamie@artizent.example", principal=MIGRATION_ENGINEER,
    )
    assert updated == (exception_id,)

    properties = await _hydrate_one(estate["pool"], estate["settings"].graph_name, "ExceptionCase", exception_id)
    assert properties["assignee"] == "jamie@artizent.example"


# ----------------------------------------------------------------------------- patch


async def test_decide_patch_closes_the_case_when_it_reproves_pass(estate) -> None:
    case_id, _k = await _write_case(estate, matching=True)
    await _write_fail_verdict(estate, case_id=case_id)
    exception_id = await _open_exception(estate, failure_class="NULL_HANDLING", case_ids=[case_id])

    result = await _service(estate).patch(
        exception_id, dax="COALESCE([Margin], 0)",
        rationale="Wrapped the measure in COALESCE so a null margin reads as zero.",
        workspace=_WORKSPACE, principal=MIGRATION_ENGINEER,
    )
    assert result.outcome == "closed"
    assert result.cases_still_failing == ()

    properties = await _hydrate_one(estate["pool"], estate["settings"].graph_name, "ExceptionCase", exception_id)
    assert properties["state"] == "CLOSED"
    assert properties["decision"] == "PATCHED"
    assert properties["closed_by"] == MIGRATION_ENGINEER.value

    new_measure = await _hydrate_one(estate["pool"], estate["settings"].graph_name, "Measure", result.measure_id)
    assert new_measure["dax"] == "COALESCE([Margin], 0)"

    decisions = await _gate_decisions_for(estate["pool"], estate["settings"].graph_name, exception_id)
    assert len(decisions) == 1
    assert decisions[0]["gate"] == "G3"
    assert decisions[0]["decision"] == "PATCHED"
    assert decisions[0]["approver"] == MIGRATION_ENGINEER.value
    assert "COALESCE" in decisions[0]["rationale"]


async def test_decide_patch_stays_open_when_still_failing(estate) -> None:
    case_id, _k = await _write_case(estate, matching=False)
    await _write_fail_verdict(estate, case_id=case_id)
    exception_id = await _open_exception(estate, failure_class="NULL_HANDLING", case_ids=[case_id])

    result = await _service(estate).patch(
        exception_id, dax="COALESCE([Margin], 0)",
        rationale="Wrapped the measure in COALESCE so a null margin reads as zero.",
        workspace=_WORKSPACE, principal=MIGRATION_ENGINEER,
    )
    assert result.outcome == "still_failing"
    assert result.cases_still_failing == (case_id,)

    properties = await _hydrate_one(estate["pool"], estate["settings"].graph_name, "ExceptionCase", exception_id)
    assert properties["state"] == "OPEN"
    assert properties["decision"] == "PATCHED"  # the decision is real even though the fix has not landed yet


async def test_decide_patch_refuses_invalid_dax(estate) -> None:
    case_id, _k = await _write_case(estate, matching=True)
    exception_id = await _open_exception(estate, failure_class="NULL_HANDLING", case_ids=[case_id])
    with pytest.raises(InvalidRequestError, match="does not validate"):
        await _service(estate).patch(
            exception_id, dax="NOTAREALFUNCTION([Margin]", rationale="A rationale long enough to pass the check.",
            workspace=_WORKSPACE, principal=MIGRATION_ENGINEER,
        )


async def test_decide_patch_refuses_a_short_rationale(estate) -> None:
    case_id, _k = await _write_case(estate, matching=True)
    exception_id = await _open_exception(estate, failure_class="NULL_HANDLING", case_ids=[case_id])
    with pytest.raises(InvalidRequestError, match="at least 20 characters"):
        await _service(estate).patch(
            exception_id, dax="SUM([Margin])", rationale="fixed it",
            workspace=_WORKSPACE, principal=MIGRATION_ENGINEER,
        )


async def test_decide_patch_refuses_a_non_open_case(estate) -> None:
    case_id, _k = await _write_case(estate, matching=True)
    exception_id = await _open_exception(estate, failure_class="NULL_HANDLING", case_ids=[case_id])
    await estate["writer"].set_node_properties(exception_id, {"state": "CLOSED"}, principal=PRINCIPAL)
    with pytest.raises(ExceptionDeskError, match="not OPEN"):
        await _service(estate).patch(
            exception_id, dax="SUM([Margin])", rationale="A rationale long enough to pass the check.",
            workspace=_WORKSPACE, principal=MIGRATION_ENGINEER,
        )


# --------------------------------------------------------------------------- redesign


async def test_decide_redesign_desktop_closes_with_a_real_commit_hash(estate) -> None:
    case_id, _k = await _write_case(estate, matching=True)
    exception_id = await _open_exception(estate, failure_class="TABLE_CALC", case_ids=[case_id])

    result = await _service(estate).redesign_desktop(
        exception_id, rationale="This table calc needs a full rework in Desktop, not a DAX patch.",
        desktop_commit_hash="abc123def", principal=MIGRATION_ENGINEER,
    )
    assert result.route == "desktop"

    properties = await _hydrate_one(estate["pool"], estate["settings"].graph_name, "ExceptionCase", exception_id)
    assert properties["state"] == "CLOSED"
    assert properties["decision"] == "REDESIGN_DESKTOP"
    assert properties["desktop_commit_hash"] == "abc123def"

    decisions = await _gate_decisions_for(estate["pool"], estate["settings"].graph_name, exception_id)
    assert decisions[0]["decision"] == "REDESIGN"


async def test_decide_redesign_desktop_refuses_a_blank_commit_hash(estate) -> None:
    case_id, _k = await _write_case(estate, matching=True)
    exception_id = await _open_exception(estate, failure_class="TABLE_CALC", case_ids=[case_id])
    with pytest.raises(InvalidRequestError, match="Desktop commit hash"):
        await _service(estate).redesign_desktop(
            exception_id, rationale="This table calc needs a full rework in Desktop.",
            desktop_commit_hash="   ", principal=MIGRATION_ENGINEER,
        )


async def test_decide_redesign_foundry_routes_and_records_redesign(estate) -> None:
    family = await _write_props(estate["writer"], "ModelFamily", {"name": "Risk Family", "state": "DRAFT", "grain": "Desk"})
    await _edge(estate["writer"], "IN_FAMILY", estate["workbook"], family, confidence=1.0)

    case_id, _k = await _write_case(estate, matching=True)
    exception_id = await _open_exception(estate, failure_class="LOD_SCOPE", case_ids=[case_id])

    result = await _service(estate).redesign_foundry(
        exception_id, rationale="The LOD scope needs a real model relationship, not a report fix.",
        principal=MIGRATION_ENGINEER,
    )
    assert result.route == "foundry"
    assert result.detail["family_id"] == family

    properties = await _hydrate_one(estate["pool"], estate["settings"].graph_name, "ExceptionCase", exception_id)
    assert properties["state"] == "BLOCKED"
    assert properties["decision"] == "REDESIGN_FOUNDRY"
    assert properties["family_ref"] == family

    decisions = await _gate_decisions_for(estate["pool"], estate["settings"].graph_name, exception_id)
    assert decisions[0]["decision"] == "REDESIGN"


async def test_decide_redesign_foundry_refuses_when_no_family_exists(estate) -> None:
    case_id, _k = await _write_case(estate, matching=True)
    exception_id = await _open_exception(estate, failure_class="LOD_SCOPE", case_ids=[case_id])
    with pytest.raises(ExceptionDeskError, match="never been clustered"):
        await _service(estate).redesign_foundry(
            exception_id, rationale="The LOD scope needs a real model relationship.", principal=MIGRATION_ENGINEER,
        )


# ------------------------------------------------------------------------ model defect


async def test_decide_model_defect_routes_to_the_foundry(estate) -> None:
    family = await _write_props(estate["writer"], "ModelFamily", {"name": "Risk Family", "state": "DRAFT", "grain": "Desk"})
    await _edge(estate["writer"], "IN_FAMILY", estate["workbook"], family, confidence=1.0)

    case_id, _k = await _write_case(estate, matching=True)
    exception_id = await _open_exception(estate, failure_class="KEY_MISSING", case_ids=[case_id])

    result = await _service(estate).model_defect(
        exception_id, rationale="The dimension this measure needs was never modelled at all.",
        principal=MIGRATION_ENGINEER,
    )
    assert result["route_result"]["family_id"] == family

    properties = await _hydrate_one(estate["pool"], estate["settings"].graph_name, "ExceptionCase", exception_id)
    assert properties["state"] == "BLOCKED"
    assert properties["decision"] == "MODEL_DEFECT_FOUNDRY"  # route_to_foundry's own real write

    decisions = await _gate_decisions_for(estate["pool"], estate["settings"].graph_name, exception_id)
    assert decisions[0]["decision"] == "MODEL_DEFECT"


# ------------------------------------------------------------------------ source defect


async def test_decide_source_defect_reproduce_closes_and_notifies(estate, caplog) -> None:
    case_id, _k = await _write_case(estate, matching=True)
    exception_id = await _open_exception(estate, failure_class="UNKNOWN", case_ids=[case_id])

    with caplog.at_level("INFO"):
        result = await _service(estate).source_defect(
            exception_id, rationale="The Tableau report itself double-counts this desk; reproduce it.",
            resolution="REPRODUCE", owner_sign_off=None, principal=MIGRATION_ENGINEER,
        )
    assert result["resolution"] == "REPRODUCE"
    assert "source-defect notification" in caplog.text

    properties = await _hydrate_one(estate["pool"], estate["settings"].graph_name, "ExceptionCase", exception_id)
    assert properties["state"] == "CLOSED"
    assert properties["decision"] == "SOURCE_DEFECT_REPRODUCE"

    decisions = await _gate_decisions_for(estate["pool"], estate["settings"].graph_name, exception_id)
    assert decisions[0]["decision"] == "SOURCE_DEFECT"


async def test_decide_source_defect_fix_requires_owner_sign_off(estate) -> None:
    case_id, _k = await _write_case(estate, matching=True)
    exception_id = await _open_exception(estate, failure_class="UNKNOWN", case_ids=[case_id])
    with pytest.raises(InvalidRequestError, match="written sign-off"):
        await _service(estate).source_defect(
            exception_id, rationale="The Tableau report itself is wrong here; fix it for real this time.",
            resolution="FIX_WITH_SIGN_OFF", owner_sign_off=None, principal=MIGRATION_ENGINEER,
        )


async def test_decide_source_defect_fix_with_sign_off_closes(estate) -> None:
    case_id, _k = await _write_case(estate, matching=True)
    exception_id = await _open_exception(estate, failure_class="UNKNOWN", case_ids=[case_id])
    result = await _service(estate).source_defect(
        exception_id, rationale="The Tableau report itself is wrong here; fix it for real this time.",
        resolution="FIX_WITH_SIGN_OFF", owner_sign_off="Approved -- Jamie, report owner, 2026-09-09.",
        principal=MIGRATION_ENGINEER,
    )
    assert result["owner_sign_off"] == "Approved -- Jamie, report owner, 2026-09-09."

    properties = await _hydrate_one(estate["pool"], estate["settings"].graph_name, "ExceptionCase", exception_id)
    assert properties["state"] == "CLOSED"
    assert properties["decision"] == "SOURCE_DEFECT_FIX_WITH_SIGN_OFF"


# ---------------------------------------------------------------------------------- API


@pytest.fixture
async def http_client(estate):
    from httpx import ASGITransport, AsyncClient

    from astra_graph.main import create_app

    app = create_app()
    app.state.exception_desk = _service(estate)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://graph-svc") as async_client:
        yield async_client


def _headers(role: str, principal: Principal) -> dict[str, str]:
    from astra_graph.principal import PRINCIPAL_HEADER
    from astra_graph.roles import ROLES_HEADER

    return {PRINCIPAL_HEADER: principal.value, ROLES_HEADER: role}


async def test_queue_over_http_is_open_to_the_report_owner(estate, http_client) -> None:
    response = await http_client.get("/v1/exception-desk", headers=_headers("client_report_owner", REPORT_OWNER))
    assert response.status_code == 200


async def test_queue_over_http_refuses_an_unrelated_client_role(estate, http_client) -> None:
    response = await http_client.get(
        "/v1/exception-desk", headers=_headers("client_data_owner", Principal("user:other@client.example")),
    )
    assert response.status_code == 403


async def test_patch_over_http_requires_the_migration_engineer_role(estate, http_client) -> None:
    case_id, _k = await _write_case(estate, matching=True)
    exception_id = await _open_exception(estate, failure_class="NULL_HANDLING", case_ids=[case_id])
    response = await http_client.post(
        f"/v1/exceptions/{exception_id}:patch",
        json={"dax": "SUM([Margin])", "rationale": "A rationale long enough to pass the check."},
        headers=_headers("client_report_owner", REPORT_OWNER),
    )
    assert response.status_code == 403


async def test_bulk_assign_over_http_succeeds_for_the_migration_engineer(estate, http_client) -> None:
    case_id, _k = await _write_case(estate, matching=True)
    exception_id = await _open_exception(estate, failure_class="NULL_HANDLING", case_ids=[case_id])
    response = await http_client.post(
        "/v1/exceptions:bulk-assign",
        json={"exception_case_ids": [exception_id], "assignee": "jamie@artizent.example"},
        headers=_headers("migration_engineer", MIGRATION_ENGINEER),
    )
    assert response.status_code == 200
    body = response.json()
    assert body["updated"] == [exception_id]


async def test_case_detail_over_http_is_a_clean_400_for_an_unknown_case(estate, http_client) -> None:
    response = await http_client.get(
        f"/v1/exceptions/{new_ulid()}", headers=_headers("migration_engineer", MIGRATION_ENGINEER),
    )
    assert response.status_code == 400
