"""The family count becomes a measured value — story S3.1.3, against real PostgreSQL +
Apache AGE.

    "A 'Confirm family count' action writes the count, the date and the confirming user to
    the programme record ... Programme Board shows planned (150) against measured with the
    delta."

What only the real store can answer: that the confirmed count is read live from the graph
(a retired ModelFamily does not count, an unconfirmed one does), not a number a caller
supplies, and that the action is refused to anyone who is not the Programme Manager.
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

from astra_graph.cartographer import Cartographer, count_families  # noqa: E402
from astra_graph.config import Settings  # noqa: E402
from astra_graph.events import source_for  # noqa: E402
from astra_graph.graph import AgeGraphRepository, create_pool  # noqa: E402
from astra_graph.graph.queries import accessor  # noqa: E402
from astra_graph.ids import new_ulid  # noqa: E402
from astra_graph.migrations import run as run_migrations  # noqa: E402
from astra_graph.ontology import EDGE_LABELS, NODE_LABELS  # noqa: E402
from astra_graph.principal import Principal  # noqa: E402
from astra_graph.retention import PLANNED_FAMILY_COUNT, PostgresProgrammeStore  # noqa: E402
from astra_graph.writes import GraphWriter, NodeWrite  # noqa: E402

PRINCIPAL = Principal("agent:programme-manager", run_id="run-family-count")


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
    config = _settings(f"astra_fcc_{new_ulid()[10:22].lower()}")

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
                "public.programme",
            ):
                await conn.execute(f"DELETE FROM {table} WHERE graph = $1", config.graph_name)
            await conn.execute("SELECT ag_catalog.drop_graph($1, true)", config.graph_name)
        finally:
            await conn.close()

    if not _run_off_loop(setup):
        pytest.skip("PostgreSQL with Apache AGE not reachable")

    yield config
    _run_off_loop(teardown)


async def _family(writer: GraphWriter, name: str, *, retired: bool = False) -> str:
    created = await writer.write_nodes(
        [NodeWrite(type="ModelFamily", properties={"name": name, "state": "PROPOSED"})],
        principal=PRINCIPAL,
    )
    family_id = str(created[0]["properties"]["id"])
    if retired:
        await writer.retire_node(family_id, reason="test cleanup", principal=PRINCIPAL)
    return family_id


@pytest.fixture
async def estate(settings: Settings):
    """Integration tests share one graph across the whole module (the established
    convention — see ``test_integration_family_overrides.py``'s own ``estate`` docstring),
    so a test cannot assume its own three families are the *only* live ones. Every
    assertion below is against ``baseline + 3``, not a bare ``3``.
    """
    pool = await create_pool(settings)
    try:
        repository = AgeGraphRepository(pool, graph_name=settings.graph_name)
        writer = GraphWriter(repository, event_source=source_for(settings.graph_name))
        programme_store = PostgresProgrammeStore(pool, graph_name=settings.graph_name)

        baseline = await count_families(pool, settings.graph_name)

        await _family(writer, "F1")
        await _family(writer, "F2")
        await _family(writer, "F3")
        await _family(writer, "F4 (retired)", retired=True)

        programme = await programme_store.open_programme(
            name="RQA migration",
            started_at="2027-01-01T00:00:00Z",
            created_by="user:pm@artizent.example",
        )

        yield {
            "pool": pool,
            "settings": settings,
            "writer": writer,
            "programme_store": programme_store,
            "programme": programme,
            "cartographer": Cartographer(pool, graph_name=settings.graph_name, writer=writer),
            "expected_measured": baseline + 3,
        }
    finally:
        await pool.close()


# ------------------------------------------------------------------- count_families


async def test_count_families_excludes_retired_families(estate) -> None:
    count = await count_families(estate["pool"], estate["settings"].graph_name)
    assert count == estate["expected_measured"]


# ------------------------------------------------------------------------- the API


@pytest.fixture
async def http_client(estate):
    from httpx import ASGITransport, AsyncClient

    from astra_graph.main import create_app

    app = create_app()
    app.state.cartographer = estate["cartographer"]
    app.state.programme_store = estate["programme_store"]

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://graph-svc") as async_client:
        yield async_client


def _headers(*, roles: str = "programme_manager") -> dict[str, str]:
    from astra_graph.principal import PRINCIPAL_HEADER
    from astra_graph.roles import ROLES_HEADER

    return {PRINCIPAL_HEADER: PRINCIPAL.value, ROLES_HEADER: roles}


async def test_get_programmes_lists_planned_against_unconfirmed_measured(estate, http_client) -> None:
    response = await http_client.get("/v1/programmes", headers=_headers())
    assert response.status_code == 200
    body = response.json()
    listed = next(p for p in body["programmes"] if p["id"] == estate["programme"].id)
    assert listed["planned_family_count"] == PLANNED_FAMILY_COUNT
    assert listed["family_count"] is None
    assert listed["family_count_delta"] is None


async def test_confirm_family_count_over_http(estate, http_client) -> None:
    response = await http_client.post(
        f"/v1/programmes/{estate['programme'].id}:confirm-family-count", headers=_headers()
    )
    assert response.status_code == 200
    body = response.json()
    measured = estate["expected_measured"]
    assert body["family_count"] == measured
    assert body["planned_family_count"] == PLANNED_FAMILY_COUNT
    assert body["family_count_delta"] == measured - PLANNED_FAMILY_COUNT
    assert body["family_count_confirmed_by"] == PRINCIPAL.value
    assert body["family_count_confirmed_at"] is not None

    listed = await http_client.get("/v1/programmes", headers=_headers())
    persisted = next(
        p for p in listed.json()["programmes"] if p["id"] == estate["programme"].id
    )
    assert persisted["family_count"] == measured


async def test_confirm_family_count_refuses_a_non_programme_manager(estate, http_client) -> None:
    response = await http_client.post(
        f"/v1/programmes/{estate['programme'].id}:confirm-family-count",
        headers=_headers(roles="semantic_model_engineer"),
    )
    assert response.status_code == 403


async def test_confirm_family_count_reports_an_unknown_programme_as_404(estate, http_client) -> None:
    response = await http_client.post(
        "/v1/programmes/prg_not-a-programme:confirm-family-count", headers=_headers()
    )
    assert response.status_code == 404
