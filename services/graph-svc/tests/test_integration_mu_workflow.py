"""Story S12.1.1's own real workflow, against real PostgreSQL + Apache AGE and a real,
embedded Temporal test server -- what only the full real stack can answer: that a real
`MigrationUnitWorkflow`, run through a real `Worker` registered with real `MuActivities`,
really persists `Workbook.mu_state` onto the real graph node (`write_mu_state`, via
`GraphWriter.set_node_properties` like every other real property write in this codebase),
that a real `run_generate`/`run_mend` activity really emits real `ACTIVITY_STARTED`/
`ACTIVITY_FINISHED` bus events into the real outbox (`estate_event`), and that the real
`generate_c3_field`/`mend_exception` functions this story's own activities wrap keep
behaving exactly as `test_integration_generation.py`/`test_integration_mender.py` already
prove them to -- now reached through a real workflow instead of called directly.

**Scope, disclosed.** `generate_c3_field`'s own `ExceptionCase` (a *pre-proof* generation
failure) carries no real `case_refs` and a synthetic `mu_ref` (`calc:{calc_id}`, see its
own docstring) -- a real, pre-existing mismatch with `mend_exception`'s own *post-proof*
parity-failure `ExceptionCase` shape, not something this story introduces or is scoped to
fix. So the real generate-then-mend chain is exercised end to end only for the case that
chain actually reaches for real without hitting that mismatch (a missing/non-C3 calc,
escalating directly, `exception_case_id is None`); `run_mend`'s own real wrapper is
proven separately, called directly against a properly-seeded parity-failure case built
the identical way `test_integration_mender.py` already does.
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
pytest.importorskip("temporalio")

from temporalio.testing import WorkflowEnvironment  # noqa: E402
from temporalio.worker import Worker  # noqa: E402

from astra_adapter.target_fake import FixtureTargetAdapter  # noqa: E402

from astra_graph.artefacts import PostgresArtefactStore  # noqa: E402
from astra_graph.case_execution import (  # noqa: E402
    RESULT_SET_MEDIA_TYPE,
    _table_map_for_sheet,
    result_set_to_parquet,
)
from astra_graph.case_execution_query import build_dax_query, to_sdk_filters, to_sdk_parameters  # noqa: E402
from astra_graph.config import Settings  # noqa: E402
from astra_graph.events import EventType, source_for  # noqa: E402
from astra_graph.gateway import RawModelResponse, StaticGateway, null_gateway  # noqa: E402
from astra_graph.generation import FixtureModelCaller  # noqa: E402
from astra_graph.graph import AgeGraphRepository, create_pool  # noqa: E402
from astra_graph.graph.queries import accessor  # noqa: E402
from astra_graph.ids import new_ulid  # noqa: E402
from astra_graph.lineage import hydrate  # noqa: E402
from astra_graph.mender import InMemoryMenderConfigStore, MenderConfig  # noqa: E402
from astra_graph.migrations import run as run_migrations  # noqa: E402
from astra_graph.mu_workflow import (  # noqa: E402
    MendActivityInput,
    MigrationUnitWorkflow,
    MigrationUnitWorkflowInput,
    MuActivities,
)
from astra_graph.ontology import EDGE_LABELS, NODE_LABELS  # noqa: E402
from astra_graph.principal import Principal  # noqa: E402
from astra_graph.provenance import PostgresProvenanceStore  # noqa: E402
from astra_graph.tolerance_charter import PostgresToleranceCharterStore  # noqa: E402
from astra_graph.writes import EdgeWrite, GraphWriter, NodeWrite  # noqa: E402

PRINCIPAL = Principal("agent:harvester", run_id="run-mu-workflow")
PLATFORM_ENGINEER = "user:platform@artizent.example"
TASK_QUEUE = "mu-workflow-integration-test"

from astra_adapter import ParityCase as SdkParityCase  # noqa: E402


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


@pytest.fixture(scope="module")
def settings() -> Settings:
    config = _settings(f"astra_mu_workflow_{new_ulid()[10:22].lower()}")

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
                "public.estate_edge_index",
                "public.estate_element_index",
                "public.estate_event",
                "public.provenance",
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


async def _edge(writer: GraphWriter, type_: str, source: str, target: str, **props: Any) -> None:
    await writer.write_edge(
        EdgeWrite(type=type_, from_id=source, to_id=target, properties=props), principal=PRINCIPAL
    )


def _ref(name: str) -> dict[str, object]:
    return {"kind": "REFERENCE", "name": name, "value": None, "children": [], "detail": []}


def _aggregate(name: str, *children: dict[str, object]) -> dict[str, object]:
    return {"kind": "AGGREGATE", "name": name, "value": None, "children": list(children), "detail": [["family", "aggregate"]]}


def _window(name: str, family: str, *children: dict[str, object]) -> dict[str, object]:
    return {
        "kind": "WINDOW", "name": name, "value": None, "children": list(children),
        "detail": [["family", family], ["addressing", "unresolved"], ["partitioning", "unresolved"]],
    }


class _ScriptedCaller:
    """A `ModelCaller` test double returning one scripted, valid candidate -- the
    identical device `test_integration_generation.py`'s own `_ScriptedCaller` uses to
    drive a genuine `generate_c3_field` success write."""

    provider = "test_provider"
    model = "test-model-1"

    def __init__(self, dax: str) -> None:
        self._dax = dax

    async def generate(self, request: Any, *, previous_error: str | None) -> RawModelResponse:
        return RawModelResponse(
            raw={"dax": self._dax, "m": None, "assumptions": ["test assumption"], "confidence": 0.87, "notes": "generated by test double"},
            gateway_request_id="gw_req_test_1", provider=self.provider, model=self.model,
            prompt_hash="prompt_hash_test", temperature=0.0, tokens_in=42, tokens_out=17,
        )


@pytest.fixture
async def estate(settings: Settings, tmp_path: Path):
    """One real Workbook, containing one real Worksheet that ENCODES a real, table-calc
    (C3) `CalculatedField` -- `test_integration_generation.py`'s own `estate` shape, with
    a real `Workbook`/`Worksheet` wrapped around it so `write_mu_state` has a real node
    to persist onto and a `run_mend`-ready `FixtureTargetAdapter`/tolerance-charter store
    alongside, matching `test_integration_mender.py`'s own recipe."""
    pool = await create_pool(settings)
    try:
        repository = AgeGraphRepository(pool, graph_name=settings.graph_name)
        writer = GraphWriter(repository, event_source=source_for(settings.graph_name))
        provenance_store = PostgresProvenanceStore(pool, graph_name=settings.graph_name)
        artefact_store = PostgresArtefactStore(pool, graph_name=settings.graph_name)
        charter_store = PostgresToleranceCharterStore(pool, graph_name=settings.graph_name)
        target_adapter = FixtureTargetAdapter(repo_path=tmp_path / "repo", workspace_root=tmp_path / "workspaces")

        suffix = new_ulid()[10:18].lower()
        book = await _write(writer, "Workbook", luid=f"wb-{suffix}", name="Daily VaR", revision="1")

        field = await _write(writer, "Field", name=f"Notional {suffix}", datatype="real", role="measure")
        parameter = await _write(writer, "Parameter", name=f"Window Size {suffix}", datatype="integer", domain="range")
        c3_calc = await _write(
            writer, "CalculatedField", name=f"Running Total {suffix}",
            formula="RUNNING_SUM(SUM([Notional]))",
            formula_ast=_window("RUNNING_SUM", "table_calc_simple", _aggregate("SUM", _ref("Notional"))),
            table_calc_flag=True,
        )
        await _edge(writer, "DEPENDS_ON", c3_calc, field, position_in_ast="args[0]")
        await _edge(writer, "DEPENDS_ON", c3_calc, parameter, position_in_ast="args[1]")

        worksheet = await _write(
            writer, "Worksheet", name=f"Desk View {suffix}", rows_shelf=[], cols_shelf=[], marks_shelf=[],
            filters=[{"field": "Region", "op": "in", "values": ["East"]}],
            sort=[{"field": "Date", "dir": "asc"}],
        )
        await _edge(writer, "CONTAINS", book, worksheet)
        await _edge(writer, "ENCODES", worksheet, c3_calc, shelf="rows")

        yield {
            "pool": pool, "graph_name": settings.graph_name, "writer": writer,
            "provenance_store": provenance_store, "artefact_store": artefact_store,
            "charter_store": charter_store, "target_adapter": target_adapter,
            "workbook": book, "worksheet": worksheet, "c3_calc": c3_calc,
        }
    finally:
        await pool.close()


def _activities(estate: dict[str, Any], *, gateway: Any) -> MuActivities:
    return MuActivities(
        pool=estate["pool"], graph_name=estate["graph_name"], writer=estate["writer"],
        provenance_store=estate["provenance_store"], artefact_store=estate["artefact_store"],
        gateway=gateway, target_adapter=estate["target_adapter"],
        config_store=InMemoryMenderConfigStore(MenderConfig(pass_budget=3)),
        charter_store=estate["charter_store"],
    )


async def _events(pool: asyncpg.Pool, graph_name: str, *, type_: EventType, subject: str) -> list[dict[str, Any]]:
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT data FROM public.estate_event WHERE graph = $1 AND type = $2 AND subject = $3",
            graph_name, type_.value, subject,
        )
    return [json.loads(row["data"]) if isinstance(row["data"], str) else row["data"] for row in rows]


async def _workbook_mu_state(pool: asyncpg.Pool, graph_name: str, workbook_id: str) -> str | None:
    async with pool.acquire() as conn:
        hydrated = await hydrate(conn, graph_name, "Workbook", [workbook_id])
    return hydrated[workbook_id].get("mu_state")


# ------------------------------------------------------------- the real workflow, end to end


async def test_a_successful_generation_drives_the_real_workflow_to_passed(estate: dict[str, Any]) -> None:
    activities = _activities(estate, gateway=StaticGateway(_ScriptedCaller(dax="Running Total = CALCULATE(SUM([Notional]))")))
    workflow_input = MigrationUnitWorkflowInput(
        workbook_id=estate["workbook"], calc_ids=(estate["c3_calc"],), principal=PLATFORM_ENGINEER,
    )

    async with await WorkflowEnvironment.start_time_skipping() as env:
        async with Worker(
            env.client, task_queue=TASK_QUEUE, workflows=[MigrationUnitWorkflow],
            activities=[activities.write_mu_state, activities.run_generate, activities.run_mend],
        ):
            final_state = await env.client.execute_workflow(
                MigrationUnitWorkflow.run, workflow_input,
                id=f"mu-{estate['workbook']}", task_queue=TASK_QUEUE,
            )

    assert final_state == "PASSED"
    assert await _workbook_mu_state(estate["pool"], estate["graph_name"], estate["workbook"]) == "PASSED"

    started = await _events(estate["pool"], estate["graph_name"], type_=EventType.ACTIVITY_STARTED, subject=estate["workbook"])
    finished = await _events(estate["pool"], estate["graph_name"], type_=EventType.ACTIVITY_FINISHED, subject=estate["workbook"])
    assert any(e["activity"] == "run_generate" for e in started)
    assert any(e["activity"] == "run_generate" and e["outcome"] == "OK" for e in finished)


async def test_a_missing_calc_escalates_the_real_workflow_directly(estate: dict[str, Any]) -> None:
    """`generate_c3_field` refuses a calc it cannot find with `exception_case_id=None`
    (`test_integration_generation.py::test_generate_c3_field_refuses_a_missing_calc`) --
    the one real failure shape the workflow reaches without ever calling `run_mend`, so
    this stays clear of the disclosed pre-proof/post-proof `ExceptionCase` mismatch (see
    this module's own docstring) while still proving a real failure drives the real
    workflow to a real terminal state end to end."""
    activities = _activities(estate, gateway=StaticGateway(FixtureModelCaller()))
    workflow_input = MigrationUnitWorkflowInput(
        workbook_id=estate["workbook"], calc_ids=("not-a-real-calc-id",), principal=PLATFORM_ENGINEER,
    )

    async with await WorkflowEnvironment.start_time_skipping() as env:
        async with Worker(
            env.client, task_queue=TASK_QUEUE, workflows=[MigrationUnitWorkflow],
            activities=[activities.write_mu_state, activities.run_generate, activities.run_mend],
        ):
            final_state = await env.client.execute_workflow(
                MigrationUnitWorkflow.run, workflow_input,
                id=f"mu-{estate['workbook']}-missing-calc", task_queue=TASK_QUEUE,
            )

    assert final_state == "ESCALATED"
    assert await _workbook_mu_state(estate["pool"], estate["graph_name"], estate["workbook"]) == "ESCALATED"


# --------------------------------------------------------- run_mend's own real activity wrapper


async def _query_text_and_result(
    pool: asyncpg.Pool, graph_name: str, target_adapter: FixtureTargetAdapter, *,
    workbook_id: str, sheet_ref: str, case_key: str, grain: tuple[str, ...], measures: tuple[str, ...],
) -> Any:
    async with pool.acquire() as conn:
        table_map = await _table_map_for_sheet(conn, graph_name, sheet_ref, grain)
    query_text = build_dax_query(
        grain=grain, measures=measures, sdk_filters=to_sdk_filters({}), sdk_parameters=to_sdk_parameters({}),
        table_map=table_map,
    )
    sdk_case = SdkParityCase(
        id=case_key, workbook_luid=workbook_id, sheet=sheet_ref, grain=grain, measures=measures,
        filters=to_sdk_filters({}), parameters=to_sdk_parameters({}),
    )
    return await target_adapter.evaluate(query_text=query_text, case=sdk_case, workspace="dev")


async def test_run_mend_closes_a_real_case_via_an_active_pattern(estate: dict[str, Any]) -> None:
    """The identical real recipe `test_integration_mender.py::test_an_active_pattern_
    closes_the_case_in_one_pass` uses, driving `MuActivities.run_mend` directly (a plain
    instance method -- `activity.defn` only tags metadata, it does not require a live
    Temporal activity-execution context for a method that never calls `activity.info()`/
    `activity.heartbeat()`) rather than through the full workflow's own generate-then-
    mend chain, for the reason this module's own docstring discloses."""
    writer, artefact_store, pool, graph_name = estate["writer"], estate["artefact_store"], estate["pool"], estate["graph_name"]

    margin_calc = await _write(
        writer, "CalculatedField", name="MarginCalc", formula="SUM([Margin])",
        formula_ast=_aggregate("SUM", _ref("Margin")),
    )
    sheet = await _write(
        writer, "Worksheet", name="Bar sheet", mark_type="bar",
        rows_shelf=["Desk"], cols_shelf=["MarginCalc"], marks_shelf=[],
    )
    await _edge(writer, "CONTAINS", estate["workbook"], sheet)
    measure = await _write(writer, "Measure", name="MarginCalc", dax="SUM([WrongField])", provenance_ref="prov_seed")
    await _edge(writer, "MAPS_TO", margin_calc, measure)

    case_key = f"case_{new_ulid()}"
    case_id = await _write(
        writer, "ParityCase", mu_ref=estate["workbook"], sheet_ref=sheet, grain=["Desk"],
        measures=["MarginCalc"], filter_ctx={}, param_values={}, case_key=case_key, state="EXECUTED",
    )
    real = await _query_text_and_result(
        pool, graph_name, estate["target_adapter"], workbook_id=estate["workbook"],
        sheet_ref=sheet, case_key=case_key, grain=("Desk",), measures=("MarginCalc",),
    )
    expected_artefact = await artefact_store.store(
        kind="mu_workflow_test_expected", mu_ref=estate["workbook"], case_id=case_id,
        content=result_set_to_parquet(real), media_type=RESULT_SET_MEDIA_TYPE, created_by=PRINCIPAL.value,
    )
    await writer.set_node_properties(case_id, {"expected_ref": expected_artefact.id}, principal=PRINCIPAL)
    evidence = await artefact_store.store(
        kind="mu_workflow_test_verdict_evidence", mu_ref=estate["workbook"], case_id=case_id,
        content=json.dumps({
            "diff": {"failing_cells": []}, "filter_ctx": {},
            "expected_columns": [{"name": "MarginCalc", "type": "double"}],
            "candidate_columns": [{"name": "MarginCalc", "type": "double"}],
        }).encode("utf-8"), media_type="application/json", created_by=PRINCIPAL.value,
    )
    await _write(writer, "Verdict", case_ref=case_id, result="FAIL", failing_cells=[], evidence_ref=evidence.id)
    await _write(
        writer, "Pattern", name="sum-margin-repair", **{"class": "C2"},
        source_signature={"ast_shape": "SUM(a)", "adapter": "tableau"},
        target_template="SUM({a})", promotion_state="ACTIVE",
    )
    case_evidence = await artefact_store.store(
        kind="mu_workflow_test_classification_evidence", mu_ref=estate["workbook"], case_id=case_id,
        content=b"{}", media_type="application/json", created_by=PRINCIPAL.value,
    )
    exception_id = await _write(
        writer, "ExceptionCase", mu_ref=estate["workbook"], **{"class": "NULL_HANDLING"}, state="OPEN",
        evidence_ref=case_evidence.id, artefact_ref=margin_calc, case_refs=[case_id],
        classification_signals={"note": "test-seeded"},
    )

    activities = _activities(estate, gateway=null_gateway())
    result = await activities.run_mend(
        MendActivityInput(
            exception_case_id=exception_id, workbook_id=estate["workbook"], workflow_id="probe-run-mend",
            workspace="dev", principal=PLATFORM_ENGINEER,
        )
    )

    assert result.outcome == "closed"

    async with pool.acquire() as conn:
        cases = await hydrate(conn, graph_name, "ExceptionCase", [exception_id])
    assert cases[exception_id]["state"] == "CLOSED"

    started = await _events(pool, graph_name, type_=EventType.ACTIVITY_STARTED, subject=estate["workbook"])
    finished = await _events(pool, graph_name, type_=EventType.ACTIVITY_FINISHED, subject=estate["workbook"])
    assert any(e["activity"] == "run_mend" for e in started)
    assert any(e["activity"] == "run_mend" and e["outcome"] == "OK" for e in finished)
