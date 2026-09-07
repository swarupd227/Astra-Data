"""The Parity Dashboard, against real PostgreSQL + Apache AGE -- story S7.4.2, closing
F7.4, spec §15.3.5.

What only the real stack can answer: that a real dashboard is really assembled from real
`ParityRun`/`Verdict`/`ParityCase` nodes after a real derive -> execute -> run-parity
pipeline; that running parity twice under two real charters really produces two real
runs a real trend can be read back from, and that a case's own real first-ever verdict
(not its latest) really drives the first-pass rate; that a real `GateDecision(decision=
"WAIVED")` really counts toward a sheet's own waived count; and that both
`GET .../parity-run` and `GET .../parity-dashboard` really drive the new "Artizent role
or report owner" gate, not the old Artizent-only one.
"""

from __future__ import annotations

import asyncio
import os
import threading
from collections.abc import Callable, Coroutine
from dataclasses import replace
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
from astra_graph.graph.queries import accessor  # noqa: E402
from astra_graph.ids import new_ulid  # noqa: E402
from astra_graph.migrations import run as run_migrations  # noqa: E402
from astra_graph.ontology import EDGE_LABELS, NODE_LABELS  # noqa: E402
from astra_graph.principal import Principal  # noqa: E402
from astra_graph.tolerance_charter import (  # noqa: E402
    DEFAULT_CHARTER,
    NumericRule,
    ParamRule,
    ToleranceCharter,
)
from astra_graph.verdicts import VerdictsService  # noqa: E402
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
    config = _settings(f"astra_parity_dash_{new_ulid()[10:22].lower()}")

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
    (S7.2.1 derive, S7.3.1 execute) this story's own dashboard reads from."""
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
            "verdicts": verdicts_service, "workbook": book, "sheet": sheet,
        }
    finally:
        await pool.close()


async def _case_id_for_workbook(estate: dict[str, Any]) -> str:
    run = await estate["verdicts"].run(
        estate["workbook"], charter=DEFAULT_CHARTER, charter_version="0", principal=PARITY_ENGINEER,
    )
    return str(run["results"][0]["case_id"])


# --------------------------------------------------------------------------- dashboard


async def test_dashboard_reflects_a_real_single_run(estate) -> None:
    await estate["verdicts"].run(estate["workbook"], charter=DEFAULT_CHARTER, charter_version="0", principal=PARITY_ENGINEER)
    dashboard = await estate["verdicts"].dashboard(estate["workbook"])
    assert dashboard is not None
    assert dashboard["workbook_id"] == estate["workbook"]
    assert len(dashboard["sheets"]) == 1
    sheet = dashboard["sheets"][0]
    assert sheet["sheet_name"] == "Bar sheet"
    assert sheet["cases_run"] == 1
    assert sheet["pass"] + sheet["fail"] + sheet["inconclusive"] == 1
    assert len(dashboard["trend"]["runs"]) == 1
    assert dashboard["trend"]["mender_passes"]["available"] is False


async def test_dashboard_is_none_before_any_run(estate) -> None:
    dashboard = await estate["verdicts"].dashboard(estate["workbook"])
    assert dashboard is None


async def test_two_real_runs_under_different_charters_produce_a_real_trend_and_first_pass_history(estate) -> None:
    strict = DEFAULT_CHARTER
    # A deliberately looser charter than the first run's -- if the two runs' own
    # independent adapters disagree only on a numeric value, this run recovers; if they
    # disagree on keys or row counts instead, it will not, and the test judges the
    # dashboard against whichever really happened rather than assuming a recovery.
    loose = replace(DEFAULT_CHARTER, numeric=NumericRule(abs_epsilon=1_000_000.0, rel_epsilon=1.0))

    first = await estate["verdicts"].run(estate["workbook"], charter=strict, charter_version="1", principal=PARITY_ENGINEER)
    second = await estate["verdicts"].run(estate["workbook"], charter=loose, charter_version="2", principal=PARITY_ENGINEER)

    dashboard = await estate["verdicts"].dashboard(estate["workbook"])
    assert dashboard is not None
    assert dashboard["latest_run_id"] == second["run_id"]
    assert dashboard["charter_version"] == "2"

    trend = dashboard["trend"]["runs"]
    assert [entry["run_id"] for entry in trend] == [first["run_id"], second["run_id"]]
    assert trend[0]["charter_version"] == "1"
    assert trend[1]["charter_version"] == "2"
    assert trend[0]["pass"] == sum(1 for one in first["results"] if one["result"] == "PASS")
    assert trend[1]["pass"] == sum(1 for one in second["results"] if one["result"] == "PASS")

    # The dashboard's own "passes the charter" statement reflects the *latest* run alone.
    second_all_pass = all(one["result"] == "PASS" for one in second["results"])
    assert dashboard["passes_the_charter"] is second_all_pass

    # First-pass rate remembers each case's own *first* verdict, even after the latest
    # run's own result for that same case moved on.
    sheet = dashboard["sheets"][0]
    first_all_pass = all(one["result"] == "PASS" for one in first["results"])
    assert sheet["first_pass_rate"] == (1.0 if first_all_pass else 0.0)


async def test_a_real_waived_gate_decision_counts_toward_the_sheet(estate) -> None:
    case_id = await _case_id_for_workbook(estate)
    await _write(
        estate["writer"], "GateDecision", gate="G3", subject_ref=case_id, decision="WAIVED",
        approver=PARITY_ENGINEER.value, timestamp="2026-01-01T00:00:00Z",
    )
    dashboard = await estate["verdicts"].dashboard(estate["workbook"])
    assert dashboard is not None
    assert dashboard["sheets"][0]["waived_count"] == 1


async def test_a_non_waived_gate_decision_does_not_count(estate) -> None:
    case_id = await _case_id_for_workbook(estate)
    await _write(
        estate["writer"], "GateDecision", gate="G3", subject_ref=case_id, decision="APPROVED",
        approver=PARITY_ENGINEER.value, timestamp="2026-01-01T00:00:00Z",
    )
    dashboard = await estate["verdicts"].dashboard(estate["workbook"])
    assert dashboard is not None
    assert dashboard["sheets"][0]["waived_count"] == 0


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


REPORT_OWNER = Principal("user:owner@client.example")


async def test_parity_dashboard_over_http_is_open_to_the_report_owner(estate, http_client) -> None:
    await http_client.post(
        f"/v1/workbooks/{estate['workbook']}:run-parity", headers=_headers("parity_engineer", PARITY_ENGINEER),
    )
    response = await http_client.get(
        f"/v1/workbooks/{estate['workbook']}/parity-dashboard", headers=_headers("client_report_owner", REPORT_OWNER),
    )
    assert response.status_code == 200
    assert response.json()["workbook_id"] == estate["workbook"]


async def test_parity_dashboard_over_http_refuses_a_client_role_that_is_not_the_report_owner(estate, http_client) -> None:
    await http_client.post(
        f"/v1/workbooks/{estate['workbook']}:run-parity", headers=_headers("parity_engineer", PARITY_ENGINEER),
    )
    response = await http_client.get(
        f"/v1/workbooks/{estate['workbook']}/parity-dashboard",
        headers=_headers("client_data_owner", Principal("user:owner@client.example")),
    )
    assert response.status_code == 403


async def test_parity_dashboard_over_http_before_any_run_is_a_clean_404(estate, http_client) -> None:
    response = await http_client.get(
        f"/v1/workbooks/{estate['workbook']}/parity-dashboard", headers=_headers("client_report_owner", REPORT_OWNER),
    )
    assert response.status_code == 404


async def test_parity_run_over_http_is_now_open_to_the_report_owner_too(estate, http_client) -> None:
    await http_client.post(
        f"/v1/workbooks/{estate['workbook']}:run-parity", headers=_headers("parity_engineer", PARITY_ENGINEER),
    )
    response = await http_client.get(
        f"/v1/workbooks/{estate['workbook']}/parity-run", headers=_headers("client_report_owner", REPORT_OWNER),
    )
    assert response.status_code == 200


async def test_parity_run_over_http_still_refuses_a_client_role_that_is_not_the_report_owner(estate, http_client) -> None:
    await http_client.post(
        f"/v1/workbooks/{estate['workbook']}:run-parity", headers=_headers("parity_engineer", PARITY_ENGINEER),
    )
    response = await http_client.get(
        f"/v1/workbooks/{estate['workbook']}/parity-run",
        headers=_headers("client_data_owner", Principal("user:owner@client.example")),
    )
    assert response.status_code == 403
