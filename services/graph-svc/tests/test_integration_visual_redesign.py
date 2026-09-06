"""Redesign flags as work items, against real PostgreSQL + Apache AGE -- story S6.2.1.

What only the real stack can answer: that composing a workbook with an unmapped sheet
really does open a real `ExceptionCase(class=VISUAL_REDESIGN)`, that a recompose retires
the old case rather than orphaning it against a retired `Visual`, that the proving-
readiness check correctly separates a workbook's own ready and blocked sheets, that closing
a case really does record the engineer/commit/date and refuses to be closed twice, and that
every new route drives its own real role gate.
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
from astra_graph.compositor import Compositor, compose_report  # noqa: E402
from astra_graph.config import Settings  # noqa: E402
from astra_graph.errors import ElementNotFoundError  # noqa: E402
from astra_graph.events import source_for  # noqa: E402
from astra_graph.graph import AgeGraphRepository, create_pool  # noqa: E402
from astra_graph.graph.queries import NODE_INDEX_TABLE, accessor  # noqa: E402
from astra_graph.ids import new_ulid  # noqa: E402
from astra_graph.migrations import run as run_migrations  # noqa: E402
from astra_graph.modeller import Modeller  # noqa: E402
from astra_graph.ontology import EDGE_LABELS, NODE_LABELS  # noqa: E402
from astra_graph.principal import Principal  # noqa: E402
from astra_graph.provenance import PostgresProvenanceStore  # noqa: E402
from astra_graph.visual_mapping import DEFAULT_MAPPINGS, VisualMappingRuleset  # noqa: E402
from astra_graph.visual_redesign import (  # noqa: E402
    RedesignExceptionError,
    can_enter_proving,
    close_redesign_exception,
)
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
    config = _settings(f"astra_redesign_{new_ulid()[10:22].lower()}")

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
    """One workbook: a mapped ('bar') sheet, and an unmapped ('hexbin') sheet that will
    always be flagged for redesign -- everything a redesign-exception test needs."""
    pool = await create_pool(settings)
    try:
        repository = AgeGraphRepository(pool, graph_name=settings.graph_name)
        writer = GraphWriter(repository, event_source=source_for(settings.graph_name))
        provenance_store = PostgresProvenanceStore(pool, graph_name=settings.graph_name)
        modeller = Modeller(
            pool, graph_name=settings.graph_name, writer=writer, provenance_store=provenance_store
        )
        artefact_store = PostgresArtefactStore(pool, graph_name=settings.graph_name)
        compositor = Compositor(
            pool, graph_name=settings.graph_name, writer=writer, artefact_store=artefact_store
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

        bar_sheet = await _write(
            writer, "Worksheet", name="Bar sheet", mark_type="bar",
            rows_shelf=["Desk"], cols_shelf=[], marks_shelf=[],
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

        yield {
            "pool": pool,
            "settings": settings,
            "writer": writer,
            "compositor": compositor,
            "artefact_store": artefact_store,
            "workbook": book,
            "family": family,
            "bar_sheet": bar_sheet,
            "weird_sheet": weird_sheet,
        }
    finally:
        await pool.close()


async def _compose(estate: dict[str, Any]) -> dict[str, Any]:
    return await compose_report(
        estate["pool"], estate["settings"].graph_name, estate["writer"],
        workbook_id=estate["workbook"], ruleset=_ruleset(), principal=ENGINEER,
        artefact_store=estate["artefact_store"],
    )


# ------------------------------------------------------------------------------- opening


async def test_a_redesign_flagged_visual_opens_a_real_exception_case(estate) -> None:
    result = await _compose(estate)
    weird_visual = next(v for v in result["visuals"] if v["source_sheet_ref"] == estate["weird_sheet"])
    assert weird_visual["redesign_flag"] is True
    assert weird_visual["exception_case_id"] is not None

    bar_visual = next(v for v in result["visuals"] if v["source_sheet_ref"] == estate["bar_sheet"])
    assert bar_visual["redesign_flag"] is False
    assert bar_visual["exception_case_id"] is None


async def test_the_exception_case_carries_the_mapping_reason_and_location(estate) -> None:
    from astra_graph.lineage import hydrate

    result = await _compose(estate)
    weird_visual = next(v for v in result["visuals"] if v["source_sheet_ref"] == estate["weird_sheet"])
    case_id = weird_visual["exception_case_id"]

    async with estate["pool"].acquire() as conn:
        case = (await hydrate(conn, estate["settings"].graph_name, "ExceptionCase", [case_id]))[case_id]

    assert case["class"] == "VISUAL_REDESIGN"
    assert case["state"] == "OPEN"
    assert case["mu_ref"] == estate["workbook"]
    assert case["visual_ref"] == weird_visual["id"]
    assert case["mapping_reason"] == weird_visual["redesign_reason"]
    assert case["placeholder_location"]["source_sheet_ref"] == estate["weird_sheet"]
    assert "screenshot_ref" not in case or case["screenshot_ref"] is None


async def test_a_real_screenshot_is_found_by_worksheet_name(estate) -> None:
    await estate["artefact_store"].store(
        kind="visual_capture", mu_ref=estate["workbook"], case_id="Weird sheet",
        content=b"\x89PNG-fixture-bytes", media_type="image/png", created_by=ENGINEER.value,
    )
    result = await _compose(estate)
    weird_visual = next(v for v in result["visuals"] if v["source_sheet_ref"] == estate["weird_sheet"])

    from astra_graph.lineage import hydrate

    case_id = weird_visual["exception_case_id"]
    async with estate["pool"].acquire() as conn:
        case = (await hydrate(conn, estate["settings"].graph_name, "ExceptionCase", [case_id]))[case_id]
    assert case["screenshot_ref"] is not None


async def test_recomposing_retires_the_previous_exception_case(estate) -> None:
    first = await _compose(estate)
    first_case_id = next(
        v for v in first["visuals"] if v["source_sheet_ref"] == estate["weird_sheet"]
    )["exception_case_id"]

    second = await _compose(estate)
    second_case_id = next(
        v for v in second["visuals"] if v["source_sheet_ref"] == estate["weird_sheet"]
    )["exception_case_id"]

    assert first_case_id != second_case_id
    async with estate["pool"].acquire() as conn:
        rows = await conn.fetch(
            f"SELECT retired_at FROM {NODE_INDEX_TABLE} WHERE graph = $1 AND id = $2",
            estate["settings"].graph_name, first_case_id,
        )
    assert rows[0]["retired_at"] is not None


# ------------------------------------------------------------------------ proving gate


async def test_proving_readiness_separates_ready_from_blocked_sheets(estate) -> None:
    await _compose(estate)
    readiness = await can_enter_proving(estate["pool"], estate["settings"].graph_name, estate["workbook"])
    assert estate["bar_sheet"] in readiness.ready_worksheet_ids
    assert estate["weird_sheet"] in readiness.blocked_worksheet_ids
    assert readiness.fully_blocked is False


async def test_proving_readiness_before_any_compose_has_nothing_blocked(estate) -> None:
    readiness = await can_enter_proving(estate["pool"], estate["settings"].graph_name, estate["workbook"])
    assert readiness.blocked_worksheet_ids == ()


# ---------------------------------------------------------------------------- closing


async def test_closing_a_redesign_exception_records_engineer_commit_and_date(estate) -> None:
    result = await _compose(estate)
    case_id = next(
        v for v in result["visuals"] if v["source_sheet_ref"] == estate["weird_sheet"]
    )["exception_case_id"]

    closed = await close_redesign_exception(
        estate["pool"], estate["settings"].graph_name, estate["writer"],
        case_id=case_id, desktop_commit_hash="abc1234", principal=ENGINEER,
    )
    assert closed["state"] == "CLOSED"
    assert closed["closed_by"] == ENGINEER.value
    assert closed["closed_at"]
    assert closed["desktop_commit_hash"] == "abc1234"


async def test_closing_with_a_blank_commit_hash_is_refused(estate) -> None:
    result = await _compose(estate)
    case_id = next(
        v for v in result["visuals"] if v["source_sheet_ref"] == estate["weird_sheet"]
    )["exception_case_id"]
    with pytest.raises(RedesignExceptionError, match="commit hash"):
        await close_redesign_exception(
            estate["pool"], estate["settings"].graph_name, estate["writer"],
            case_id=case_id, desktop_commit_hash="   ", principal=ENGINEER,
        )


async def test_closing_an_already_closed_case_is_refused(estate) -> None:
    result = await _compose(estate)
    case_id = next(
        v for v in result["visuals"] if v["source_sheet_ref"] == estate["weird_sheet"]
    )["exception_case_id"]
    await close_redesign_exception(
        estate["pool"], estate["settings"].graph_name, estate["writer"],
        case_id=case_id, desktop_commit_hash="abc1234", principal=ENGINEER,
    )
    with pytest.raises(RedesignExceptionError, match="already closed"):
        await close_redesign_exception(
            estate["pool"], estate["settings"].graph_name, estate["writer"],
            case_id=case_id, desktop_commit_hash="def5678", principal=ENGINEER,
        )


async def test_closing_an_unknown_case_is_a_clean_404(estate) -> None:
    with pytest.raises(ElementNotFoundError):
        await close_redesign_exception(
            estate["pool"], estate["settings"].graph_name, estate["writer"],
            case_id="not-a-real-case", desktop_commit_hash="abc1234", principal=ENGINEER,
        )


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


async def test_list_exceptions_over_http(estate, http_client) -> None:
    await _compose(estate)
    response = await http_client.get("/v1/exceptions", headers=_headers("programme_manager", ENGINEER))
    assert response.status_code == 200
    body = response.json()
    assert any(e["mu_ref"] == estate["workbook"] for e in body["exceptions"])


async def test_list_exceptions_filters_by_state_and_mu_ref(estate, http_client) -> None:
    await _compose(estate)
    response = await http_client.get(
        "/v1/exceptions",
        params={"state": "OPEN", "mu_ref": estate["workbook"]},
        headers=_headers("programme_manager", ENGINEER),
    )
    assert response.status_code == 200
    body = response.json()
    assert body["count"] >= 1
    assert all(e["state"] == "OPEN" and e["mu_ref"] == estate["workbook"] for e in body["exceptions"])


async def test_close_exception_over_http_requires_the_migration_engineer_role(estate, http_client) -> None:
    result = await _compose(estate)
    case_id = next(
        v for v in result["visuals"] if v["source_sheet_ref"] == estate["weird_sheet"]
    )["exception_case_id"]
    response = await http_client.post(
        f"/v1/exceptions/{case_id}:close",
        json={"desktop_commit_hash": "abc1234"},
        headers=_headers("programme_manager", ENGINEER),
    )
    assert response.status_code == 403


async def test_close_exception_over_http_succeeds_for_the_migration_engineer(estate, http_client) -> None:
    result = await _compose(estate)
    case_id = next(
        v for v in result["visuals"] if v["source_sheet_ref"] == estate["weird_sheet"]
    )["exception_case_id"]
    response = await http_client.post(
        f"/v1/exceptions/{case_id}:close",
        json={"desktop_commit_hash": "abc1234"},
        headers=_headers("migration_engineer", ENGINEER),
    )
    assert response.status_code == 200
    assert response.json()["state"] == "CLOSED"


async def test_proving_readiness_over_http(estate, http_client) -> None:
    await _compose(estate)
    response = await http_client.get(
        f"/v1/workbooks/{estate['workbook']}:proving-readiness",
        headers=_headers("programme_manager", ENGINEER),
    )
    assert response.status_code == 200
    body = response.json()
    assert estate["bar_sheet"] in body["ready_worksheet_ids"]
    assert estate["weird_sheet"] in body["blocked_worksheet_ids"]
