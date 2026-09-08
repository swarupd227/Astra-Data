"""§10.5 Visual parity (advisory), against real PostgreSQL + Apache AGE -- story S7.6.1,
opening F7.6.

What only the real stack can answer: that a real composed `Visual` really gets scored
against its own real source `Worksheet`, that a real source screenshot (via a source
adapter that claims the capability) and a real target render really get captured,
stored as real artefacts, and compared into a real `image_score`; that an adapter
honestly declining the screenshot capability leaves `image_score` a real, honest `None`
rather than a fabricated number; and that none of this ever touches a `Verdict` or a
gate.
"""

from __future__ import annotations

import asyncio
import base64
import os
import threading
from collections.abc import Callable, Coroutine
from typing import Any

import pytest

pytestmark = pytest.mark.integration

asyncpg = pytest.importorskip("asyncpg")

from astra_adapter import Capabilities  # noqa: E402
from astra_adapter.fake.source import (  # noqa: E402
    FixtureSite,
    FixtureSourceAdapter,
    FixtureWorkbook,
)
from astra_adapter.target_fake import FixtureTargetAdapter  # noqa: E402

from astra_graph.artefacts import PostgresArtefactStore  # noqa: E402
from astra_graph.compositor import compose_report  # noqa: E402
from astra_graph.config import Settings  # noqa: E402
from astra_graph.events import source_for  # noqa: E402
from astra_graph.graph import AgeGraphRepository, create_pool  # noqa: E402
from astra_graph.graph.queries import accessor  # noqa: E402
from astra_graph.ids import new_ulid  # noqa: E402
from astra_graph.lineage import hydrate  # noqa: E402
from astra_graph.migrations import run as run_migrations  # noqa: E402
from astra_graph.modeller import Modeller  # noqa: E402
from astra_graph.ontology import EDGE_LABELS, NODE_LABELS  # noqa: E402
from astra_graph.principal import Principal  # noqa: E402
from astra_graph.provenance import PostgresProvenanceStore  # noqa: E402
from astra_graph.visual_mapping import DEFAULT_MAPPINGS, VisualMappingRuleset  # noqa: E402
from astra_graph.visual_parity import (  # noqa: E402
    VisualParityError,
    get_visual_captures,
    run_visual_parity_for_workbook,
)
from astra_graph.writes import EdgeWrite, GraphWriter, NodeWrite  # noqa: E402

PRINCIPAL = Principal("agent:modeller", run_id="run-modeller")
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
    config = _settings(f"astra_visual_parity_{new_ulid()[10:22].lower()}")

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
    created = await writer.write_nodes([NodeWrite(type=type_, properties=properties)], principal=PRINCIPAL)
    return str(created[0]["properties"]["id"])


async def _edge(writer: GraphWriter, type_: str, source: str, target: str, **props: Any) -> None:
    await writer.write_edge(
        EdgeWrite(type=type_, from_id=source, to_id=target, properties=props), principal=PRINCIPAL
    )


def _ruleset() -> VisualMappingRuleset:
    return VisualMappingRuleset(version=0, rules=DEFAULT_MAPPINGS, updated_by="system", updated_at=None)


async def _node_properties(pool: asyncpg.Pool, graph_name: str, label: str, node_id: str) -> dict[str, Any]:
    async with pool.acquire() as conn:
        hydrated = await hydrate(conn, graph_name, label, [node_id])
    return hydrated[node_id]


@pytest.fixture
async def estate(settings: Settings):
    """One composed workbook: a bar sheet with a real bound measure -- the real
    prerequisite pipeline (S6.1.1 compose) this story's own scoring reads from."""
    pool = await create_pool(settings)
    try:
        repository = AgeGraphRepository(pool, graph_name=settings.graph_name)
        writer = GraphWriter(repository, event_source=source_for(settings.graph_name))
        provenance_store = PostgresProvenanceStore(pool, graph_name=settings.graph_name)
        modeller = Modeller(pool, graph_name=settings.graph_name, writer=writer, provenance_store=provenance_store)
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
        measure = await _write(
            writer, "Measure", name="Margin", dax="CALCULATE(SUM(Positions[Margin]))",
            provenance_ref="prov_fixture_1",
        )
        await _edge(writer, "MAPS_TO", margin_calc, measure, **{"class": "C2"})

        sheet = await _write(
            writer, "Worksheet", name="Bar sheet", mark_type="bar",
            rows_shelf=["Desk"], cols_shelf=["MarginCalc"], marks_shelf=[],
            sort=[{"field": "Desk", "direction": "asc"}],
            reference_lines=[{"axis": "y", "value": 100, "label": "Target"}],
        )
        await _edge(writer, "CONTAINS", book, sheet)
        await _edge(writer, "USES_DATASOURCE", sheet, datasource)

        family = await _write(
            writer, "ModelFamily", name=f"Risk Positions {suffix}", state="PROPOSED",
            grain="Desk", conformed_dims=[],
        )
        await _edge(writer, "IN_FAMILY", book, family, confidence=1.0)
        await modeller.run(family, principal=PRINCIPAL)

        compose_result = await compose_report(
            pool, settings.graph_name, writer, workbook_id=book, ruleset=_ruleset(), principal=PARITY_ENGINEER,
        )
        assert compose_result["visual_count"] > 0
        visual_id = next(
            v["id"] for v in compose_result["visuals"] if v["source_sheet_ref"] == sheet
        )

        yield {
            "pool": pool, "settings": settings, "writer": writer, "artefact_store": artefact_store,
            "workbook": book, "workbook_luid": workbook_luid, "site_name": site_name,
            "sheet": sheet, "visual_id": visual_id,
        }
    finally:
        await pool.close()


def _source_adapter(estate: dict[str, Any], *, screenshot: bool) -> FixtureSourceAdapter:
    return FixtureSourceAdapter(
        [FixtureSite(name=estate["site_name"], workbooks=[
            FixtureWorkbook(name="Daily VaR", luid=estate["workbook_luid"], project="Risk Core"),
        ])],
        capabilities=Capabilities(
            live_query=False, extract_read=True, usage=True, ownership=True, screenshot=screenshot,
        ),
    )


async def test_a_composed_visual_gets_a_real_structural_score(estate) -> None:
    result = await run_visual_parity_for_workbook(
        estate["pool"], estate["settings"].graph_name, estate["writer"], estate["artefact_store"],
        workbook_id=estate["workbook"], source_adapter=None, target_adapter=None, principal=PARITY_ENGINEER,
    )
    assert result["visuals_scored"] == 1
    scored = result["results"][0]
    assert scored["visual_id"] == estate["visual_id"]
    assert 0.0 <= scored["structural_score"] <= 1.0
    assert scored["image_score"] is None  # no adapters supplied

    visual = await _node_properties(estate["pool"], estate["settings"].graph_name, "Visual", estate["visual_id"])
    assert visual["structural_score"] == scored["structural_score"]
    assert visual["structural_score_breakdown"]["mark_type"] == 1.0  # a real bar->column mapping, not redesign-flagged
    # This sheet has a real reference line the target never carries -- a real, honest gap.
    assert visual["structural_score_breakdown"]["reference_lines"] == 0.0
    assert visual["visual_score_computed_at"]


async def test_a_workbook_with_no_composed_report_is_refused(estate) -> None:
    lone_book = await _write(estate["writer"], "Workbook", luid="wb-lonely", name="Lonely", revision="1")
    with pytest.raises(VisualParityError, match="no composed visuals"):
        await run_visual_parity_for_workbook(
            estate["pool"], estate["settings"].graph_name, estate["writer"], estate["artefact_store"],
            workbook_id=lone_book, source_adapter=None, target_adapter=None, principal=PARITY_ENGINEER,
        )


async def test_a_source_adapter_that_claims_screenshot_produces_a_real_image_score(estate, tmp_path) -> None:
    source_adapter = _source_adapter(estate, screenshot=True)
    target_adapter = FixtureTargetAdapter(repo_path=tmp_path / "repo", workspace_root=tmp_path / "workspaces")

    result = await run_visual_parity_for_workbook(
        estate["pool"], estate["settings"].graph_name, estate["writer"], estate["artefact_store"],
        workbook_id=estate["workbook"], source_adapter=source_adapter, target_adapter=target_adapter,
        principal=PARITY_ENGINEER,
    )
    scored = result["results"][0]
    assert scored["image_score"] is not None
    assert 0.0 <= scored["image_score"] <= 1.0

    visual = await _node_properties(estate["pool"], estate["settings"].graph_name, "Visual", estate["visual_id"])
    assert visual["source_screenshot_ref"]
    assert visual["target_render_ref"]

    stored_source = await estate["artefact_store"].content(visual["source_screenshot_ref"])
    stored_target = await estate["artefact_store"].content(visual["target_render_ref"])
    assert stored_source is not None
    assert stored_target is not None

    captures = await get_visual_captures(
        estate["pool"], estate["settings"].graph_name, estate["artefact_store"], visual_id=estate["visual_id"],
    )
    assert captures is not None
    assert captures["source"]["media_type"] == "image/png"
    assert captures["target"]["media_type"] == "image/png"
    assert base64.b64decode(captures["source"]["content_base64"]) == stored_source
    assert base64.b64decode(captures["target"]["content_base64"]) == stored_target


async def test_captures_are_honestly_absent_before_any_image_score_is_computed(estate) -> None:
    captures = await get_visual_captures(
        estate["pool"], estate["settings"].graph_name, estate["artefact_store"], visual_id=estate["visual_id"],
    )
    assert captures is None


async def test_a_source_adapter_that_does_not_claim_screenshot_leaves_image_score_honestly_none(
    estate, tmp_path,
) -> None:
    source_adapter = _source_adapter(estate, screenshot=False)
    target_adapter = FixtureTargetAdapter(repo_path=tmp_path / "repo", workspace_root=tmp_path / "workspaces")

    result = await run_visual_parity_for_workbook(
        estate["pool"], estate["settings"].graph_name, estate["writer"], estate["artefact_store"],
        workbook_id=estate["workbook"], source_adapter=source_adapter, target_adapter=target_adapter,
        principal=PARITY_ENGINEER,
    )
    scored = result["results"][0]
    assert scored["image_score"] is None

    visual = await _node_properties(estate["pool"], estate["settings"].graph_name, "Visual", estate["visual_id"])
    assert visual.get("source_screenshot_ref") is None
    assert visual.get("target_render_ref") is None


async def test_visual_parity_never_touches_a_verdict_or_a_case(estate) -> None:
    # No ParityCase/Verdict exists anywhere in this estate at all -- proving this run
    # neither needs nor creates one is the AC's own "it never gates" made concrete.
    from astra_graph.graph.queries import NODE_INDEX_TABLE

    async def _live_case_ids() -> list[Any]:
        async with estate["pool"].acquire() as conn:
            rows = await conn.fetch(
                f"""SELECT id FROM {NODE_INDEX_TABLE}
                 WHERE graph = $1 AND kind = 'node' AND label = 'ParityCase' AND retired_at IS NULL""",
                estate["settings"].graph_name,
            )
        return [row["id"] for row in rows]

    assert await _live_case_ids() == []
    await run_visual_parity_for_workbook(
        estate["pool"], estate["settings"].graph_name, estate["writer"], estate["artefact_store"],
        workbook_id=estate["workbook"], source_adapter=None, target_adapter=None, principal=PARITY_ENGINEER,
    )
    assert await _live_case_ids() == []


async def test_running_visual_parity_over_http_requires_the_parity_engineer_role(estate) -> None:
    from httpx import ASGITransport, AsyncClient

    from astra_graph.main import create_app
    from astra_graph.principal import PRINCIPAL_HEADER
    from astra_graph.roles import ROLES_HEADER
    from astra_graph.visual_parity import VisualParityService

    app = create_app()
    app.state.visual_parity = VisualParityService(
        estate["pool"], graph_name=estate["settings"].graph_name, writer=estate["writer"],
        artefact_store=estate["artefact_store"], source_adapter=None, target_adapter=None,
    )
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://graph-svc") as client:
        refused = await client.post(
            f"/v1/workbooks/{estate['workbook']}:run-visual-parity",
            headers={PRINCIPAL_HEADER: PARITY_ENGINEER.value, ROLES_HEADER: "programme_manager"},
        )
        assert refused.status_code == 403

        allowed = await client.post(
            f"/v1/workbooks/{estate['workbook']}:run-visual-parity",
            headers={PRINCIPAL_HEADER: PARITY_ENGINEER.value, ROLES_HEADER: "parity_engineer"},
        )
    assert allowed.status_code == 200
    assert allowed.json()["visuals_scored"] == 1
