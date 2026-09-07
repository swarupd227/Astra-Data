"""Diffing and verdicts, against real PostgreSQL + Apache AGE -- story S7.4.1, closing
F7.4, spec §10.3.

What only the real stack can answer: that a real, already-executed `ParityCase`'s own
stored Parquet artefacts really get read back and really diffed under the real current
Tolerance Charter; that a real `Verdict` and a real `ParityRun` really get written --
both declared in the ontology since its very first §4.1.1 declaration and never once
written by any story before this one; that `PROVED_BY` really links a real
`ReportDefinition` to the run when one exists, and is honestly skipped when none does;
that the evidence bundle artefact is really stored and really readable; that a
source-side failure (an unmatched workbook luid, S7.3.1's own precedent) really produces
a real INCONCLUSIVE verdict, not a crash; and that both new routes drive their own real
role gate.
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

from astra_adapter.fake.source import (  # noqa: E402
    FixtureSite,
    FixtureSourceAdapter,
    FixtureWorkbook,
)
from astra_adapter.target_fake import FixtureTargetAdapter  # noqa: E402

from astra_graph.artefacts import PostgresArtefactStore  # noqa: E402
from astra_graph.case_derivation import (  # noqa: E402
    CaseDerivationService,
    PostgresParitySuiteStore,
)
from astra_graph.case_execution import CaseExecutionService  # noqa: E402
from astra_graph.config import Settings  # noqa: E402
from astra_graph.events import source_for  # noqa: E402
from astra_graph.graph import AgeGraphRepository, create_pool  # noqa: E402
from astra_graph.graph.queries import EDGE_INDEX_TABLE, accessor  # noqa: E402
from astra_graph.ids import new_ulid  # noqa: E402
from astra_graph.lineage import hydrate  # noqa: E402
from astra_graph.migrations import run as run_migrations  # noqa: E402
from astra_graph.ontology import EDGE_LABELS, NODE_LABELS  # noqa: E402
from astra_graph.principal import Principal  # noqa: E402
from astra_graph.tolerance_charter import DEFAULT_CHARTER, ParamRule, ToleranceCharter  # noqa: E402
from astra_graph.verdicts import VerdictError, VerdictsService, latest_parity_run  # noqa: E402
from astra_graph.writes import EdgeWrite, GraphWriter, NodeWrite  # noqa: E402

PRINCIPAL = Principal("agent:harvester", run_id="run-harvester")
PARITY_ENGINEER = Principal("user:parity@artizent.example")


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
    """Function-scoped -- see test_integration_case_derivation.py's own identical
    fixture for why a shared graph would let one test's cases pollute another's."""
    config = _settings(f"astra_verdicts_{new_ulid()[10:22].lower()}")

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
                "public.parity_suite", "public.artefacts", "public.execution_observation",
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


def _charter(max_values: int = 12) -> ToleranceCharter:
    return ToleranceCharter(params=ParamRule(enumerate_max_values=max_values, enumerate_strategy="DEFAULT_PLUS_OBSERVED"))


@pytest.fixture
async def estate(settings: Settings, tmp_path: Path):
    """One workbook, one derived-and-executed case -- the real prerequisite pipeline
    (S7.2.1 derive, S7.3.1 execute) this story's own diff runs against. No
    `ReportDefinition` is created here; `test_run_parity_writes_proved_by_when_a_report_
    exists` adds one deliberately, to cover both the presence and honest absence of the
    `PROVED_BY` edge."""
    pool = await create_pool(settings)
    try:
        repository = AgeGraphRepository(pool, graph_name=settings.graph_name)
        writer = GraphWriter(repository, event_source=source_for(settings.graph_name))
        suite_store = PostgresParitySuiteStore(pool, graph_name=settings.graph_name)
        derivation = CaseDerivationService(
            pool, graph_name=settings.graph_name, writer=writer, suite_store=suite_store
        )
        artefact_store = PostgresArtefactStore(pool, graph_name=settings.graph_name)
        suffix = new_ulid()[10:18].lower()
        site_name = f"rqa-{suffix}"
        workbook_luid = f"wb-{suffix}"

        site = await _write(writer, "Site", luid=f"s-{suffix}", name=site_name)
        project = await _write(writer, "Project", luid=f"p-{suffix}", name="Risk Core")
        await _edge(writer, "CONTAINS", site, project)
        book = await _write(writer, "Workbook", luid=workbook_luid, name="Daily VaR", revision="1")
        await _edge(writer, "CONTAINS", project, book)

        datasource = await _write(
            writer, "Datasource", name="VaR ds", type="published", luid=f"ds-{suffix}", extract_flag=True,
        )
        desk = await _write(writer, "Field", name="Desk", datatype="string", role="dimension")
        await _edge(writer, "HAS_FIELD", datasource, desk)

        margin_calc = await _write(
            writer, "CalculatedField", name="MarginCalc", formula="SUM([Margin])",
            formula_ast={"kind": "FUNCTION", "name": "SUM", "children": [], "detail": {}},
        )
        await _edge(writer, "HAS_FIELD", datasource, margin_calc)

        sheet = await _write(
            writer, "Worksheet", name="Bar sheet", mark_type="bar",
            rows_shelf=["Desk"], cols_shelf=["MarginCalc"], marks_shelf=[],
        )
        await _edge(writer, "CONTAINS", book, sheet)
        await _edge(writer, "USES_DATASOURCE", sheet, datasource)

        derive_result = await derivation.derive(
            book, charter_version="1", charter=_charter(), principal=PARITY_ENGINEER,
        )
        assert derive_result["cases_written"] > 0

        source_adapter = FixtureSourceAdapter(
            [FixtureSite(name=site_name, workbooks=[FixtureWorkbook(name="Daily VaR", luid=workbook_luid, project="Risk Core")])]
        )
        target_adapter = FixtureTargetAdapter(repo_path=tmp_path / "repo", workspace_root=tmp_path / "workspaces")
        execution_service = CaseExecutionService(
            pool, graph_name=settings.graph_name, writer=writer, artefact_store=artefact_store,
            source_adapter=source_adapter, target_adapter=target_adapter,
        )
        execute_result = await execution_service.execute(book, workspace="dev", principal=PARITY_ENGINEER)
        assert execute_result["cases_executed"] > 0

        verdicts_service = VerdictsService(
            pool, graph_name=settings.graph_name, writer=writer, artefact_store=artefact_store,
        )

        yield {
            "pool": pool, "settings": settings, "writer": writer, "artefact_store": artefact_store,
            "verdicts": verdicts_service, "execution_service": execution_service, "source_adapter": source_adapter,
            "target_adapter": target_adapter, "workbook": book, "workbook_luid": workbook_luid,
            "sheet": sheet, "desk": desk, "site": site, "project": project,
        }
    finally:
        await pool.close()


async def _node_properties(pool: asyncpg.Pool, graph_name: str, label: str, node_id: str) -> dict[str, Any]:
    async with pool.acquire() as conn:
        hydrated = await hydrate(conn, graph_name, label, [node_id])
    return hydrated[node_id]


# --------------------------------------------------------------------------- run_parity


async def test_run_parity_writes_a_real_verdict_and_run(estate) -> None:
    result = await estate["verdicts"].run(
        estate["workbook"], charter=DEFAULT_CHARTER, charter_version="0", principal=PARITY_ENGINEER,
    )
    assert result["cases_diffed"] > 0
    assert result["pass"] + result["fail"] + result["inconclusive"] == result["cases_diffed"]
    assert result["proved_by_report"] is None  # no ReportDefinition exists for this MU yet

    for one in result["results"]:
        verdict = await _node_properties(estate["pool"], estate["settings"].graph_name, "Verdict", one["verdict_id"])
        assert verdict["case_ref"] == one["case_id"]
        assert verdict["result"] == one["result"]
        assert verdict["evidence_ref"]

    run = await _node_properties(estate["pool"], estate["settings"].graph_name, "ParityRun", result["run_id"])
    assert run["suite_ref"] == estate["workbook"]
    assert run["charter_version"] == "0"
    assert len(run["verdicts"]) == result["cases_diffed"]


async def test_run_parity_stores_a_real_readable_evidence_bundle(estate) -> None:
    result = await estate["verdicts"].run(
        estate["workbook"], charter=DEFAULT_CHARTER, charter_version="7", principal=PARITY_ENGINEER,
    )
    one = result["results"][0]
    verdict = await _node_properties(estate["pool"], estate["settings"].graph_name, "Verdict", one["verdict_id"])

    content = await estate["artefact_store"].content(verdict["evidence_ref"])
    assert content is not None
    bundle = json.loads(content)
    assert bundle["charter_version"] == "7"
    assert bundle["case_id"] == one["case_id"]
    assert "EVALUATE" in bundle["candidate_query"]
    assert bundle["expected_result_hash"]
    assert bundle["candidate_result_hash"]
    assert bundle["diff"]["result"] == one["result"]
    assert "timings" in bundle and bundle["timings"]["duration_ms"] >= 0


async def test_run_parity_writes_proved_by_when_a_report_exists(estate) -> None:
    report_id = await _write(
        estate["writer"], "ReportDefinition", mu_ref=estate["workbook"], model_ref="fam-1",
    )
    result = await estate["verdicts"].run(
        estate["workbook"], charter=DEFAULT_CHARTER, charter_version="1", principal=PARITY_ENGINEER,
    )
    assert result["proved_by_report"] == report_id

    async with estate["pool"].acquire() as conn:
        edge_rows = await conn.fetch(
            f"""SELECT id FROM {EDGE_INDEX_TABLE}
                 WHERE graph = $1 AND label = 'PROVED_BY' AND from_id = $2 AND to_id = $3
                   AND retired_at IS NULL""",
            estate["settings"].graph_name, report_id, result["run_id"],
        )
        assert len(edge_rows) == 1
        edge_id = edge_rows[0]["id"]
        edge_properties = await hydrate(conn, estate["settings"].graph_name, "PROVED_BY", [edge_id])
    assert edge_properties[edge_id]["charter_version"] == "1"


async def test_run_parity_on_an_unexecuted_case_is_refused(settings: Settings, tmp_path: Path) -> None:
    pool = await create_pool(settings)
    try:
        repository = AgeGraphRepository(pool, graph_name=settings.graph_name)
        writer = GraphWriter(repository, event_source=source_for(settings.graph_name))
        suite_store = PostgresParitySuiteStore(pool, graph_name=settings.graph_name)
        derivation = CaseDerivationService(pool, graph_name=settings.graph_name, writer=writer, suite_store=suite_store)
        artefact_store = PostgresArtefactStore(pool, graph_name=settings.graph_name)

        site = await _write(writer, "Site", luid="s-x", name="rqa-x")
        project = await _write(writer, "Project", luid="p-x", name="Risk Core")
        await _edge(writer, "CONTAINS", site, project)
        book = await _write(writer, "Workbook", luid="wb-x", name="Unexecuted", revision="1")
        await _edge(writer, "CONTAINS", project, book)
        datasource = await _write(writer, "Datasource", name="ds", type="published", luid="ds-x", extract_flag=True)
        desk = await _write(writer, "Field", name="Desk", datatype="string", role="dimension")
        await _edge(writer, "HAS_FIELD", datasource, desk)
        margin = await _write(
            writer, "CalculatedField", name="Margin", formula="SUM([M])",
            formula_ast={"kind": "FUNCTION", "name": "SUM", "children": [], "detail": {}},
        )
        await _edge(writer, "HAS_FIELD", datasource, margin)
        sheet = await _write(
            writer, "Worksheet", name="Sheet", mark_type="bar", rows_shelf=["Desk"], cols_shelf=["Margin"], marks_shelf=[],
        )
        await _edge(writer, "CONTAINS", book, sheet)
        await _edge(writer, "USES_DATASOURCE", sheet, datasource)
        await derivation.derive(book, charter_version="1", charter=_charter(), principal=PARITY_ENGINEER)

        verdicts_service = VerdictsService(
            pool, graph_name=settings.graph_name, writer=writer, artefact_store=artefact_store,
        )
        with pytest.raises(VerdictError, match="no executed parity cases"):
            await verdicts_service.run(book, charter=DEFAULT_CHARTER, charter_version="0", principal=PARITY_ENGINEER)
    finally:
        await pool.close()


async def test_a_source_side_luid_mismatch_produces_a_real_inconclusive_verdict(estate) -> None:
    mismatched_adapter = FixtureSourceAdapter(
        [FixtureSite(name="other-site", workbooks=[FixtureWorkbook(name="Other", luid="different-luid", project="Risk Core")])]
    )
    service = CaseExecutionService(
        estate["pool"], graph_name=estate["settings"].graph_name, writer=estate["writer"],
        artefact_store=estate["artefact_store"], source_adapter=mismatched_adapter,
        target_adapter=estate["target_adapter"],
    )
    await service.execute(estate["workbook"], workspace="dev", principal=PARITY_ENGINEER)

    result = await estate["verdicts"].run(
        estate["workbook"], charter=DEFAULT_CHARTER, charter_version="0", principal=PARITY_ENGINEER,
    )
    assert all(one["result"] == "INCONCLUSIVE" for one in result["results"])
    assert result["inconclusive"] == result["cases_diffed"]


async def test_latest_parity_run_reads_back_the_real_run_and_verdicts(estate) -> None:
    await _write(
        estate["writer"], "ReportDefinition", mu_ref=estate["workbook"], model_ref="fam-1",
    )
    result = await estate["verdicts"].run(
        estate["workbook"], charter=DEFAULT_CHARTER, charter_version="0", principal=PARITY_ENGINEER,
    )
    run = await latest_parity_run(estate["pool"], estate["settings"].graph_name, workbook_id=estate["workbook"])
    assert run is not None
    assert run["run_id"] == result["run_id"]
    assert len(run["verdicts"]) == result["cases_diffed"]


async def test_latest_parity_run_is_none_before_any_run(estate) -> None:
    run = await latest_parity_run(estate["pool"], estate["settings"].graph_name, workbook_id=estate["workbook"])
    assert run is None


# ---------------------------------------------------------------------------------- API


@pytest.fixture
async def http_client(estate):
    from httpx import ASGITransport, AsyncClient

    from astra_graph.main import create_app

    app = create_app()
    app.state.verdicts = estate["verdicts"]

    class _FakeCharterStore:
        async def latest(self) -> Any:
            class _Version:
                version = 0
                charter = DEFAULT_CHARTER

            return _Version()

    app.state.tolerance_charter_store = _FakeCharterStore()

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://graph-svc") as async_client:
        yield async_client


def _headers(role: str, principal: Principal) -> dict[str, str]:
    from astra_graph.principal import PRINCIPAL_HEADER
    from astra_graph.roles import ROLES_HEADER

    return {PRINCIPAL_HEADER: principal.value, ROLES_HEADER: role}


async def test_run_parity_over_http_requires_the_parity_engineer_role(estate, http_client) -> None:
    response = await http_client.post(
        f"/v1/workbooks/{estate['workbook']}:run-parity", headers=_headers("programme_manager", PARITY_ENGINEER),
    )
    assert response.status_code == 403


async def test_run_parity_over_http_succeeds_for_the_parity_engineer(estate, http_client) -> None:
    response = await http_client.post(
        f"/v1/workbooks/{estate['workbook']}:run-parity", headers=_headers("parity_engineer", PARITY_ENGINEER),
    )
    assert response.status_code == 200
    body = response.json()
    assert body["cases_diffed"] > 0


async def test_get_parity_run_over_http_before_any_run_is_a_clean_404(estate, http_client) -> None:
    response = await http_client.get(
        f"/v1/workbooks/{estate['workbook']}/parity-run", headers=_headers("platform_engineer", PARITY_ENGINEER),
    )
    assert response.status_code == 404


async def test_get_parity_run_over_http_after_a_run(estate, http_client) -> None:
    await http_client.post(
        f"/v1/workbooks/{estate['workbook']}:run-parity", headers=_headers("parity_engineer", PARITY_ENGINEER),
    )
    response = await http_client.get(
        f"/v1/workbooks/{estate['workbook']}/parity-run", headers=_headers("platform_engineer", PARITY_ENGINEER),
    )
    assert response.status_code == 200
    assert response.json()["workbook_id"] == estate["workbook"]
