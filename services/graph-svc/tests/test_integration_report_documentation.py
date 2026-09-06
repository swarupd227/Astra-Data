"""Report documentation, against real PostgreSQL + Apache AGE -- story S6.2.2, spec §8.8/§8.11.

What only the real stack can answer: that a real, already-composed report's own Visual/
CalculatedField/Measure/SemanticModel/Datasource graph renders into a real markdown page
naming the right source calc, the right redesigned sheet, the right C4 guidance and the
right refresh schedule; that the generated page is really stored as an artefact with a
real ASSISTED-mode provenance record; that the link from `ReportDefinition` is real and
survives a fresh read; and that the new routes drive their own real role gates.
"""

from __future__ import annotations

import asyncio
import os
import threading
from collections.abc import Callable, Coroutine
from typing import Any

import pytest

pytestmark = pytest.mark.integration

asyncpg = pytest.importorskip("asyncpg")

from astra_graph.artefacts import PostgresArtefactStore  # noqa: E402
from astra_graph.compositor import Compositor, CompositorError  # noqa: E402
from astra_graph.config import Settings  # noqa: E402
from astra_graph.context.contract import ContractName  # noqa: E402
from astra_graph.events import source_for  # noqa: E402
from astra_graph.graph import AgeGraphRepository, create_pool  # noqa: E402
from astra_graph.graph.queries import accessor  # noqa: E402
from astra_graph.ids import new_ulid  # noqa: E402
from astra_graph.migrations import run as run_migrations  # noqa: E402
from astra_graph.modeller import Modeller  # noqa: E402
from astra_graph.ontology import EDGE_LABELS, NODE_LABELS  # noqa: E402
from astra_graph.principal import Principal  # noqa: E402
from astra_graph.provenance import AgentMode, PostgresProvenanceStore  # noqa: E402
from astra_graph.visual_mapping import DEFAULT_MAPPINGS, VisualMappingRuleset  # noqa: E402
from astra_graph.writes import EdgeWrite, GraphWriter, NodeWrite  # noqa: E402

PRINCIPAL = Principal("agent:modeller", run_id="run-modeller")
ENGINEER = Principal("user:engineer@artizent.example")


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
    config = _settings(f"astra_report_doc_{new_ulid()[10:22].lower()}")

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
                "public.artefacts",
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
    created = await writer.write_nodes(
        [NodeWrite(type=type_, properties=properties)], principal=PRINCIPAL
    )
    return str(created[0]["properties"]["id"])


async def _edge(writer: GraphWriter, type_: str, source: str, target: str, **props: Any) -> None:
    await writer.write_edge(
        EdgeWrite(type=type_, from_id=source, to_id=target, properties=props), principal=PRINCIPAL
    )


def _ruleset() -> VisualMappingRuleset:
    return VisualMappingRuleset(version=0, rules=DEFAULT_MAPPINGS, updated_by="system", updated_at=None)


@pytest.fixture
async def estate(settings: Settings):
    """One workbook: a mapped ('bar') sheet with a real, bound calculated-field measure
    and a C4 calculation with no redesign decision yet, plus one unmapped ('hexbin') sheet
    -- everything this story's own acceptance criteria names a section for."""
    pool = await create_pool(settings)
    try:
        repository = AgeGraphRepository(pool, graph_name=settings.graph_name)
        writer = GraphWriter(repository, event_source=source_for(settings.graph_name))
        provenance_store = PostgresProvenanceStore(pool, graph_name=settings.graph_name)
        artefact_store = PostgresArtefactStore(pool, graph_name=settings.graph_name)
        modeller = Modeller(
            pool, graph_name=settings.graph_name, writer=writer, provenance_store=provenance_store
        )
        compositor = Compositor(
            pool, graph_name=settings.graph_name, writer=writer,
            artefact_store=artefact_store, provenance_store=provenance_store,
        )
        suffix = new_ulid()[10:18].lower()

        site = await _write(writer, "Site", luid=f"s-{suffix}", name=f"RQA {suffix}")
        project = await _write(writer, "Project", luid=f"p-{suffix}", name="Risk Core")
        await _edge(writer, "CONTAINS", site, project)
        table = await _write(writer, "Table", name="positions", schema="risk", row_estimate=1000)
        connection = await _write(writer, "Connection", **{"class": "snowflake"}, server="warehouse", db="risk")
        await _edge(writer, "CONNECTS_TO", connection, table)
        book = await _write(writer, "Workbook", luid=f"wb-{suffix}", name="Daily VaR", revision="1")
        await _edge(writer, "CONTAINS", project, book)

        datasource = await _write(
            writer, "Datasource", name="VaR ds", type="published", luid=f"ds-{suffix}",
            extract_flag=True, refresh_schedule="daily",
        )
        await _edge(writer, "CONNECTS_TO", datasource, connection)
        desk = await _write(writer, "Field", name="Desk", datatype="string", role="dimension")
        await _edge(writer, "HAS_FIELD", datasource, desk)

        margin_calc = await _write(
            writer, "CalculatedField", name="MarginCalc", formula="SUM([Margin])",
            formula_ast={"kind": "FUNCTION", "name": "SUM", "children": [], "detail": {}},
        )
        await _edge(writer, "HAS_FIELD", datasource, margin_calc)
        measure = await _write(
            writer, "Measure", name="Margin", dax="CALCULATE(SUM(Positions[Margin]))",
            provenance_ref="prov_fixture_1",
        )
        await _edge(writer, "MAPS_TO", margin_calc, measure, **{"class": "C2"})

        weird_calc = await _write(
            writer, "CalculatedField", name="WeirdCalc", formula="RAWSQL_INT('SELECT 1')",
            formula_ast={"kind": "FUNCTION", "name": "RAWSQL_INT", "children": [], "detail": {}},
            **{"class": "C4"},
            reason="Appendix B.1, 'RAWSQL': M pass-through where supported, otherwise C4.",
            appendix_b_guidance="Appendix B.1, 'RAWSQL': M pass-through where the source "
            "dialect is supported, otherwise C4 by default.",
            redesign_suggestion="Replace with native model relationships/measures where "
            "possible; keep a native query step only if the source dialect is supported.",
        )
        await _edge(writer, "HAS_FIELD", datasource, weird_calc)

        growth_rate = await _write(
            writer, "Parameter", name="Growth Rate", datatype="real", domain="list",
            default="0.05", current_values_seen=["0.05", "0.10"],
        )
        await _edge(writer, "DEPENDS_ON", margin_calc, growth_rate)

        bar_sheet = await _write(
            writer, "Worksheet", name="Bar sheet", mark_type="bar",
            rows_shelf=["MarginCalc"], cols_shelf=["Desk", "WeirdCalc"], marks_shelf=[],
        )
        await _edge(writer, "CONTAINS", book, bar_sheet)
        await _edge(writer, "USES_DATASOURCE", bar_sheet, datasource)

        weird_sheet = await _write(
            writer, "Worksheet", name="Weird sheet", mark_type="hexbin",
            rows_shelf=[], cols_shelf=[], marks_shelf=[],
        )
        await _edge(writer, "CONTAINS", book, weird_sheet)
        await _edge(writer, "USES_DATASOURCE", weird_sheet, datasource)

        family = await _write(
            writer, "ModelFamily", name=f"Risk Positions {suffix}", state="PROPOSED",
            grain="Desk", conformed_dims=[],
        )
        await _edge(writer, "IN_FAMILY", book, family, confidence=1.0)
        await modeller.run(family, principal=PRINCIPAL)

        report = await compositor.compose(book, ruleset=_ruleset(), principal=ENGINEER)

        yield {
            "pool": pool,
            "settings": settings,
            "writer": writer,
            "compositor": compositor,
            "artefact_store": artefact_store,
            "provenance_store": provenance_store,
            "workbook": book,
            "family": family,
            "bar_sheet": bar_sheet,
            "weird_sheet": weird_sheet,
            "margin_calc": margin_calc,
            "weird_calc": weird_calc,
            "measure": measure,
            "report": report,
        }
    finally:
        await pool.close()


# ------------------------------------------------------------------------------ generating


async def test_generating_documentation_stores_a_markdown_artefact(estate) -> None:
    result = await estate["compositor"].generate_documentation(estate["workbook"], principal=ENGINEER)
    assert result["report_id"] == estate["report"]["report_id"]

    content = await estate["artefact_store"].content(result["artefact_id"])
    assert content is not None
    markdown = content.decode("utf-8")
    assert markdown.startswith("# Daily VaR — Report Documentation")

    record = await estate["artefact_store"].get(result["artefact_id"])
    assert record is not None
    assert record.kind == "report_documentation"
    assert record.mu_ref == estate["workbook"]
    assert record.media_type == "text/markdown"


async def test_the_page_names_the_source_calc_and_the_redesigned_sheet(estate) -> None:
    result = await estate["compositor"].generate_documentation(estate["workbook"], principal=ENGINEER)
    markdown = (await estate["artefact_store"].content(result["artefact_id"])).decode("utf-8")

    assert "Margin" in markdown and "MarginCalc" in markdown
    assert "Bar sheet" in markdown
    assert "Weird sheet" in markdown
    assert "hexbin" in markdown or "no mapping rule" in markdown
    assert "Growth Rate" in markdown
    assert "WeirdCalc" in markdown
    assert "M pass-through" in markdown
    assert "_not yet recorded (blocked)_" in markdown
    assert "daily" in markdown


async def test_generation_records_a_real_assisted_provenance_record(estate) -> None:
    result = await estate["compositor"].generate_documentation(estate["workbook"], principal=ENGINEER)
    record = await estate["provenance_store"].get(result["provenance_id"])
    assert record is not None
    assert record.agent == "compositor"
    assert record.mode is AgentMode.ASSISTED
    assert record.contract is ContractName.COMPOSITOR_REPORT_DOC
    assert record.subject_id == estate["report"]["report_id"]
    assert record.model is None


async def test_generation_links_the_artefact_and_provenance_from_the_report(estate) -> None:
    result = await estate["compositor"].generate_documentation(estate["workbook"], principal=ENGINEER)
    report = await estate["compositor"].read(estate["workbook"])
    assert report is not None
    assert report["documentation_artefact_ref"] == result["artefact_id"]
    assert report["documentation_provenance_ref"] == result["provenance_id"]


async def test_generating_before_a_compose_is_refused(estate) -> None:
    with pytest.raises(CompositorError, match="has not been composed"):
        await estate["compositor"].generate_documentation("not-a-real-workbook", principal=ENGINEER)


# ------------------------------------------------------------------------------- reading


async def test_reading_before_any_generation_returns_none(estate) -> None:
    assert await estate["compositor"].read_documentation(estate["workbook"]) is None


async def test_reading_returns_the_generated_content(estate) -> None:
    generated = await estate["compositor"].generate_documentation(estate["workbook"], principal=ENGINEER)
    documentation = await estate["compositor"].read_documentation(estate["workbook"])
    assert documentation is not None
    assert documentation["artefact_id"] == generated["artefact_id"]
    assert documentation["content"].startswith("# Daily VaR — Report Documentation")


async def test_regenerating_produces_a_fresh_artefact_and_updates_the_link(estate) -> None:
    first = await estate["compositor"].generate_documentation(estate["workbook"], principal=ENGINEER)
    second = await estate["compositor"].generate_documentation(estate["workbook"], principal=ENGINEER)
    assert first["artefact_id"] != second["artefact_id"]
    report = await estate["compositor"].read(estate["workbook"])
    assert report is not None
    assert report["documentation_artefact_ref"] == second["artefact_id"]


# ---------------------------------------------------------------------------------- API


@pytest.fixture
async def http_client(estate):
    from httpx import ASGITransport, AsyncClient

    from astra_graph.main import create_app

    app = create_app()
    app.state.compositor = estate["compositor"]

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://graph-svc") as async_client:
        yield async_client


def _headers(role: str, principal: Principal) -> dict[str, str]:
    from astra_graph.principal import PRINCIPAL_HEADER
    from astra_graph.roles import ROLES_HEADER

    return {PRINCIPAL_HEADER: principal.value, ROLES_HEADER: role}


async def test_generate_documentation_over_http_requires_the_migration_engineer_role(
    estate, http_client
) -> None:
    response = await http_client.post(
        f"/v1/workbooks/{estate['workbook']}:generate-documentation",
        headers=_headers("programme_manager", ENGINEER),
    )
    assert response.status_code == 403


async def test_generate_documentation_over_http_succeeds_for_the_migration_engineer(
    estate, http_client
) -> None:
    response = await http_client.post(
        f"/v1/workbooks/{estate['workbook']}:generate-documentation",
        headers=_headers("migration_engineer", ENGINEER),
    )
    assert response.status_code == 200
    assert response.json()["workbook_id"] == estate["workbook"]


async def test_get_documentation_over_http_is_open_to_the_report_owner(estate, http_client) -> None:
    await http_client.post(
        f"/v1/workbooks/{estate['workbook']}:generate-documentation",
        headers=_headers("migration_engineer", ENGINEER),
    )
    response = await http_client.get(
        f"/v1/workbooks/{estate['workbook']}:documentation",
        headers=_headers("client_report_owner", ENGINEER),
    )
    assert response.status_code == 200
    assert "Daily VaR" in response.json()["content"]


async def test_get_documentation_over_http_refuses_an_unrelated_client_role(estate, http_client) -> None:
    await http_client.post(
        f"/v1/workbooks/{estate['workbook']}:generate-documentation",
        headers=_headers("migration_engineer", ENGINEER),
    )
    response = await http_client.get(
        f"/v1/workbooks/{estate['workbook']}:documentation",
        headers=_headers("client_licence_admin", ENGINEER),
    )
    assert response.status_code == 403


async def test_get_documentation_over_http_before_generation_is_a_clean_404(estate, http_client) -> None:
    response = await http_client.get(
        f"/v1/workbooks/{estate['workbook']}:documentation",
        headers=_headers("programme_manager", ENGINEER),
    )
    assert response.status_code == 404
