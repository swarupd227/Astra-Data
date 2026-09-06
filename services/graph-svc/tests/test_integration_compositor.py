"""The Compositor, against real PostgreSQL + Apache AGE -- story S6.1.1, spec §8.8.

What only the real stack can answer: that a workbook's real Dashboard/Worksheet/Field
graph resolves into real Visual nodes bound through real MAPS_TO edges, that a dashboard
zone's own geometry survives onto its visual, that an unmapped mark type really does land
as a placeholder rather than blocking the rest of the report, that a re-compose really
retires the previous report rather than accumulating duplicates, and that the visual-
mapping ruleset and compose/report routes drive their real roles over HTTP.
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

from astra_graph.compositor import (  # noqa: E402
    Compositor,
    CompositorError,
    compose_report,
    read_report,
)
from astra_graph.config import Settings  # noqa: E402
from astra_graph.errors import ElementNotFoundError  # noqa: E402
from astra_graph.events import source_for  # noqa: E402
from astra_graph.graph import AgeGraphRepository, create_pool  # noqa: E402
from astra_graph.graph.queries import accessor  # noqa: E402
from astra_graph.ids import new_ulid  # noqa: E402
from astra_graph.migrations import run as run_migrations  # noqa: E402
from astra_graph.modeller import Modeller  # noqa: E402
from astra_graph.ontology import EDGE_LABELS, NODE_LABELS  # noqa: E402
from astra_graph.principal import Principal  # noqa: E402
from astra_graph.provenance import PostgresProvenanceStore  # noqa: E402
from astra_graph.visual_mapping import (  # noqa: E402
    DEFAULT_MAPPINGS,
    PostgresVisualMappingRulesetStore,
)
from astra_graph.writes import EdgeWrite, GraphWriter, NodeWrite  # noqa: E402

PRINCIPAL = Principal("agent:modeller", run_id="run-modeller")
ENGINEER = Principal("user:engineer@artizent.example")
ARCHITECT = Principal("user:architect@artizent.example")


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
    config = _settings(f"astra_compositor_{new_ulid()[10:22].lower()}")

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
                "public.visual_mapping_ruleset",
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


@pytest.fixture
async def estate(settings: Settings):
    """One workbook: a dashboard containing one mapped ('bar') sheet with a real, bound
    calculated-field measure and two unbound plain-field wells, plus one standalone sheet
    with an unmapped mark type -- everything `compose_report` needs to exercise every
    branch this story's own acceptance criteria names."""
    pool = await create_pool(settings)
    try:
        repository = AgeGraphRepository(pool, graph_name=settings.graph_name)
        writer = GraphWriter(repository, event_source=source_for(settings.graph_name))
        provenance_store = PostgresProvenanceStore(pool, graph_name=settings.graph_name)
        modeller = Modeller(
            pool, graph_name=settings.graph_name, writer=writer, provenance_store=provenance_store
        )
        mapping_store = PostgresVisualMappingRulesetStore(pool, graph_name=settings.graph_name)
        compositor = Compositor(pool, graph_name=settings.graph_name, writer=writer)
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
        region = await _write(writer, "Field", name="Region", datatype="string", role="dimension")
        await _edge(writer, "HAS_FIELD", datasource, desk)
        await _edge(writer, "HAS_FIELD", datasource, region)

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

        bar_sheet = await _write(
            writer, "Worksheet", name="Bar sheet", mark_type="bar",
            rows_shelf=["MarginCalc"], cols_shelf=["Desk"], marks_shelf=["color:Region"],
            sort=[{"field": "Desk", "direction": "asc", "using": ""}],
            filters=[{"field_ref": "Desk", "type": "categorical", "values": {"included": ["EMEA"]}, "context_flag": False}],
        )
        await _edge(writer, "CONTAINS", book, bar_sheet)
        await _edge(writer, "USES_DATASOURCE", bar_sheet, datasource)

        weird_sheet = await _write(
            writer, "Worksheet", name="Weird sheet", mark_type="hexbin",
            rows_shelf=[], cols_shelf=[], marks_shelf=[],
        )
        await _edge(writer, "CONTAINS", book, weird_sheet)
        await _edge(writer, "USES_DATASOURCE", weird_sheet, datasource)

        dashboard = await _write(
            writer, "Dashboard", name="VaR Dashboard", size={"width": 800, "height": 600},
            contained_sheets=["Bar sheet"],
            layout_json=[
                {
                    "type": "layout-basic", "name": "", "x": 0, "y": 0, "w": 800, "h": 600,
                    "children": [
                        {"type": "worksheet", "name": "Bar sheet", "x": 10, "y": 20, "w": 300, "h": 200, "children": []},
                    ],
                }
            ],
        )
        await _edge(writer, "CONTAINS", book, dashboard)

        family = await _write(
            writer, "ModelFamily", name=f"Risk Positions {suffix}", state="PROPOSED",
            grain="Desk", conformed_dims=[],
        )
        await _edge(writer, "IN_FAMILY", book, family, confidence=1.0)
        await modeller.run(family, principal=PRINCIPAL)

        yield {
            "pool": pool,
            "settings": settings,
            "writer": writer,
            "compositor": compositor,
            "mapping_store": mapping_store,
            "workbook": book,
            "family": family,
            "bar_sheet": bar_sheet,
            "weird_sheet": weird_sheet,
            "margin_calc": margin_calc,
            "measure": measure,
            "datasource": datasource,
        }
    finally:
        await pool.close()


def _ruleset():
    from astra_graph.visual_mapping import VisualMappingRuleset

    return VisualMappingRuleset(version=0, rules=DEFAULT_MAPPINGS, updated_by="system", updated_at=None)


# ------------------------------------------------------------------------------- composing


async def test_composing_a_workbook_produces_one_page_per_dashboard_and_per_standalone_sheet(estate) -> None:
    result = await compose_report(
        estate["pool"], estate["settings"].graph_name, estate["writer"],
        workbook_id=estate["workbook"], ruleset=_ruleset(), principal=ENGINEER,
    )
    assert set(result["pages"]) == {"VaR Dashboard", "Weird sheet"}
    assert result["visual_count"] == 2
    assert result["redesign_count"] == 1
    assert result["validation_state"] == "SCHEMA_VALID"


async def test_the_mapped_sheet_resolves_encodings_and_binds_the_calculated_field(estate) -> None:
    result = await compose_report(
        estate["pool"], estate["settings"].graph_name, estate["writer"],
        workbook_id=estate["workbook"], ruleset=_ruleset(), principal=ENGINEER,
    )
    visual = next(v for v in result["visuals"] if v["source_sheet_ref"] == estate["bar_sheet"])
    assert visual["redesign_flag"] is False
    # rows=measure (calc, bound), cols=dimension, color=dimension -> not horizontal, coloured -> stacked column
    assert visual["type"] == "stackedColumnChart"
    wells = {w["sourceName"]: w for w in visual["encodings"]["field_wells"]}
    assert wells["MarginCalc"]["sourceKind"] == "CalculatedField"
    assert wells["MarginCalc"]["bound"] is True
    assert wells["MarginCalc"]["measureId"] == estate["measure"]
    assert wells["Desk"]["bound"] is False
    assert "MAPS_TO" in wells["Desk"]["reason"] or "does not resolve" in wells["Desk"]["reason"] or wells["Desk"]["reason"]
    assert wells["Region"]["role"] == "legend"
    assert visual["encodings"]["sort"][0]["field"] == "Desk"
    assert visual["encodings"]["filters"][0]["field_ref"] == "Desk"


async def test_the_dashboard_zone_geometry_survives_onto_the_visual(estate) -> None:
    result = await compose_report(
        estate["pool"], estate["settings"].graph_name, estate["writer"],
        workbook_id=estate["workbook"], ruleset=_ruleset(), principal=ENGINEER,
    )
    visual = next(v for v in result["visuals"] if v["source_sheet_ref"] == estate["bar_sheet"])
    assert visual["layout"] == {"x": 10, "y": 20, "width": 300, "height": 200}
    assert visual["page"] == "VaR Dashboard"


async def test_an_unmapped_mark_type_is_a_placeholder_with_no_layout_and_its_own_page(estate) -> None:
    result = await compose_report(
        estate["pool"], estate["settings"].graph_name, estate["writer"],
        workbook_id=estate["workbook"], ruleset=_ruleset(), principal=ENGINEER,
    )
    visual = next(v for v in result["visuals"] if v["source_sheet_ref"] == estate["weird_sheet"])
    assert visual["type"] == "placeholder"
    assert visual["redesign_flag"] is True
    assert "hexbin" in visual["redesign_reason"]
    assert visual["layout"] is None
    assert visual["page"] == "Weird sheet"


async def test_the_report_definition_binds_to_the_familys_current_design(estate) -> None:
    result = await compose_report(
        estate["pool"], estate["settings"].graph_name, estate["writer"],
        workbook_id=estate["workbook"], ruleset=_ruleset(), principal=ENGINEER,
    )
    report = await read_report(estate["pool"], estate["settings"].graph_name, estate["workbook"])
    assert report is not None
    assert report["mu_ref"] == estate["workbook"]
    assert report["model_ref"] == result["model_ref"]
    assert report["validation_state"] == "SCHEMA_VALID"
    assert len(report["visuals"]) == 2


async def test_recomposing_retires_the_previous_reports_own_visuals(estate) -> None:
    first = await compose_report(
        estate["pool"], estate["settings"].graph_name, estate["writer"],
        workbook_id=estate["workbook"], ruleset=_ruleset(), principal=ENGINEER,
    )
    second = await compose_report(
        estate["pool"], estate["settings"].graph_name, estate["writer"],
        workbook_id=estate["workbook"], ruleset=_ruleset(), principal=ENGINEER,
    )
    assert second["report_id"] != first["report_id"]
    first_ids = {v["id"] for v in first["visuals"]}
    second_ids = {v["id"] for v in second["visuals"]}
    assert first_ids.isdisjoint(second_ids)

    report = await read_report(estate["pool"], estate["settings"].graph_name, estate["workbook"])
    assert {v["id"] for v in report["visuals"]} == second_ids


async def test_a_dashboard_shaped_like_the_fixture_adapters_own_layout_json_does_not_crash(estate) -> None:
    """Found live, composing a real fixture-harvested workbook by hand: the fixture source
    adapter (`astra_adapter.fake.source`) writes `Dashboard.layout_json` as `{"zones":
    [{"sheet": ref}, ...]}`, not the real Tableau adapter's own bare list of zone dicts
    (`sheets.py`'s `Dashboard.as_properties`) -- and the fixture's own zone entries carry
    no geometry at all. The compose must still succeed, with `layout: None` rather than a
    500."""
    writer = estate["writer"]
    fixture_sheet = await _write(
        writer, "Worksheet", name="Fixture sheet", mark_type="bar",
        rows_shelf=["Desk"], cols_shelf=[], marks_shelf=[],
    )
    await _edge(writer, "CONTAINS", estate["workbook"], fixture_sheet)
    await _edge(writer, "USES_DATASOURCE", fixture_sheet, estate["datasource"])
    dashboard = await _write(
        writer, "Dashboard", name="Fixture Dashboard", size={"width": 800, "height": 600},
        contained_sheets=["Fixture sheet"],
        layout_json={"zones": [{"sheet": "workbook:x/worksheet:0"}]},
    )
    await _edge(writer, "CONTAINS", estate["workbook"], dashboard)

    result = await compose_report(
        estate["pool"], estate["settings"].graph_name, estate["writer"],
        workbook_id=estate["workbook"], ruleset=_ruleset(), principal=ENGINEER,
    )
    visual = next(v for v in result["visuals"] if v["source_sheet_ref"] == fixture_sheet)
    assert visual["layout"] is None
    assert visual["page"] == "Fixture Dashboard"


async def test_composing_a_workbook_with_no_family_is_refused(estate) -> None:
    lone = await _write(estate["writer"], "Workbook", luid="wb-lone", name="Lone book", revision="1")
    with pytest.raises(CompositorError, match="ModelFamily"):
        await compose_report(
            estate["pool"], estate["settings"].graph_name, estate["writer"],
            workbook_id=lone, ruleset=_ruleset(), principal=ENGINEER,
        )


async def test_composing_an_unknown_workbook_is_a_clean_404(estate) -> None:
    with pytest.raises(ElementNotFoundError):
        await compose_report(
            estate["pool"], estate["settings"].graph_name, estate["writer"],
            workbook_id="not-a-real-workbook", ruleset=_ruleset(), principal=ENGINEER,
        )


# ---------------------------------------------------------------------------------- the API


@pytest.fixture
async def http_client(estate):
    from httpx import ASGITransport, AsyncClient

    from astra_graph.main import create_app

    app = create_app()
    app.state.compositor = estate["compositor"]
    app.state.visual_mapping_store = estate["mapping_store"]

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://graph-svc") as async_client:
        yield async_client


def _headers(role: str, principal: Principal) -> dict[str, str]:
    from astra_graph.principal import PRINCIPAL_HEADER
    from astra_graph.roles import ROLES_HEADER

    return {PRINCIPAL_HEADER: principal.value, ROLES_HEADER: role}


async def test_compose_over_http_requires_the_migration_engineer_role(estate, http_client) -> None:
    response = await http_client.post(
        f"/v1/workbooks/{estate['workbook']}:compose",
        headers=_headers("programme_manager", ENGINEER),
    )
    assert response.status_code == 403


async def test_compose_over_http_succeeds_for_the_migration_engineer(estate, http_client) -> None:
    response = await http_client.post(
        f"/v1/workbooks/{estate['workbook']}:compose",
        headers=_headers("migration_engineer", ENGINEER),
    )
    assert response.status_code == 200
    assert response.json()["visual_count"] == 2


async def test_get_report_before_any_compose_is_404(estate, http_client) -> None:
    response = await http_client.get(
        f"/v1/workbooks/{estate['workbook']}:report", headers=_headers("programme_manager", ENGINEER),
    )
    assert response.status_code == 404


async def test_get_report_after_compose_over_http(estate, http_client) -> None:
    await http_client.post(
        f"/v1/workbooks/{estate['workbook']}:compose", headers=_headers("migration_engineer", ENGINEER),
    )
    response = await http_client.get(
        f"/v1/workbooks/{estate['workbook']}:report", headers=_headers("programme_manager", ENGINEER),
    )
    assert response.status_code == 200
    assert len(response.json()["visuals"]) == 2


async def test_get_visual_mappings_over_http(http_client) -> None:
    response = await http_client.get(
        "/v1/compositor/visual-mappings", headers=_headers("programme_manager", ENGINEER),
    )
    assert response.status_code == 200
    body = response.json()
    assert body["ruleset"]["version"] == 0
    assert any(r["mark_type"] == "bar" for r in body["ruleset"]["rules"])


async def test_save_visual_mappings_requires_the_architect_role(http_client) -> None:
    response = await http_client.post(
        "/v1/compositor/visual-mappings",
        json={"rules": [{"mark_type": "bar", "target_visual_type": "clusteredColumnChart"}]},
        headers=_headers("migration_engineer", ENGINEER),
    )
    assert response.status_code == 403


async def test_save_visual_mappings_creates_a_new_version(http_client) -> None:
    response = await http_client.post(
        "/v1/compositor/visual-mappings",
        json={"rules": [{"mark_type": "bar", "target_visual_type": "areaChart", "notes": "custom"}]},
        headers=_headers("migration_architect", ARCHITECT),
    )
    assert response.status_code == 200
    body = response.json()
    assert body["ruleset"]["version"] == 1
    assert body["ruleset"]["updated_by"] == ARCHITECT.value
    assert body["ruleset"]["rules"][0]["target_visual_type"] == "areaChart"


async def test_save_visual_mappings_rejects_a_rule_with_both_target_and_reason(http_client) -> None:
    response = await http_client.post(
        "/v1/compositor/visual-mappings",
        json={"rules": [{"mark_type": "bar", "target_visual_type": "x", "redesign_reason": "y"}]},
        headers=_headers("migration_architect", ARCHITECT),
    )
    assert response.status_code == 400


async def test_save_visual_mappings_rejects_a_non_whitelisted_visual_type(http_client) -> None:
    response = await http_client.post(
        "/v1/compositor/visual-mappings",
        json={"rules": [{"mark_type": "bar", "target_visual_type": "ribbonChart"}]},
        headers=_headers("migration_architect", ARCHITECT),
    )
    assert response.status_code == 400
    assert "whitelisted" in response.json()["message"]


async def test_save_visual_mappings_rejects_a_duplicate_mark_type(http_client) -> None:
    response = await http_client.post(
        "/v1/compositor/visual-mappings",
        json={
            "rules": [
                {"mark_type": "bar", "target_visual_type": "clusteredColumnChart"},
                {"mark_type": "bar", "target_visual_type": "stackedColumnChart"},
            ]
        },
        headers=_headers("migration_architect", ARCHITECT),
    )
    assert response.status_code == 400
    assert "duplicate" in response.json()["message"]


async def test_a_saved_mapping_change_is_used_by_the_next_compose(estate, http_client) -> None:
    await http_client.post(
        "/v1/compositor/visual-mappings",
        json={
            "rules": [
                {"mark_type": rule.mark_type, "target_visual_type": rule.target_visual_type, "redesign_reason": rule.redesign_reason, "notes": rule.notes}
                for rule in DEFAULT_MAPPINGS
                if rule.mark_type != "bar"
            ]
            + [{"mark_type": "bar", "target_visual_type": "areaChart", "notes": "custom architect choice"}],
        },
        headers=_headers("migration_architect", ARCHITECT),
    )
    response = await http_client.post(
        f"/v1/workbooks/{estate['workbook']}:compose", headers=_headers("migration_engineer", ENGINEER),
    )
    assert response.status_code == 200, response.text
    visual = next(v for v in response.json()["visuals"] if v["source_sheet_ref"] == estate["bar_sheet"])
    assert visual["type"] == "areaChart"
