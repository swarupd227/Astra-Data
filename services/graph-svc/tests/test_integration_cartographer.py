"""The Cartographer, against real PostgreSQL + Apache AGE — story S3.1.1.

What only the real store can answer: that the whole chain (Workbook → Worksheet →
Datasource → Connection → Table, and Datasource → HAS_FIELD → CalculatedField) reaches the
right things, that SHARES_LINEAGE and ModelFamily land in the graph exactly as the pure
algorithm decided, and that a re-run retires the Cartographer's own prior proposals without
touching a family a human has already accepted.

Deliberately writes **no ENCODES edges** — the adapter does not emit them yet (see the
module docstring in ``cartographer.py``), and this suite exists partly to prove the
workaround actually works against a graph shaped the way a real harvest leaves it, not
against a fixture that happens to have the edges the workaround was written to avoid
needing.
"""

from __future__ import annotations

import asyncio
import os
import threading
from collections.abc import Callable, Coroutine
from datetime import UTC, datetime
from typing import Any

import pytest

pytestmark = pytest.mark.integration

asyncpg = pytest.importorskip("asyncpg")

from astra_graph.cartographer import Cartographer  # noqa: E402
from astra_graph.config import Settings  # noqa: E402
from astra_graph.events import source_for  # noqa: E402
from astra_graph.graph import AgeGraphRepository, create_pool  # noqa: E402
from astra_graph.graph.queries import accessor  # noqa: E402
from astra_graph.ids import new_ulid  # noqa: E402
from astra_graph.migrations import run as run_migrations  # noqa: E402
from astra_graph.ontology import EDGE_LABELS, NODE_LABELS  # noqa: E402
from astra_graph.principal import Principal  # noqa: E402
from astra_graph.retention import PostgresProgrammeStore  # noqa: E402
from astra_graph.writes import EdgeWrite, GraphWriter, NodeWrite  # noqa: E402

PRINCIPAL = Principal("agent:cartographer", run_id="run-cartographer")


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
    config = _settings(f"astra_cart_{new_ulid()[10:22].lower()}")

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


async def _write(writer: GraphWriter, type_: str, **properties: Any) -> str:
    created = await writer.write_nodes(
        [NodeWrite(type=type_, properties=properties)], principal=PRINCIPAL
    )
    return str(created[0]["properties"]["id"])


async def _edge(writer: GraphWriter, type_: str, source: str, target: str, **props: Any) -> None:
    await writer.write_edge(
        EdgeWrite(type=type_, from_id=source, to_id=target, properties=props), principal=PRINCIPAL
    )


def _family_of(result, workbook_id: str):
    """The one family a workbook belongs to, found by membership rather than by "the
    first PROPOSED family" — this suite's estate fixture is function-scoped but the graph
    it writes into is module-scoped (the shared-graph convention every integration suite in
    this service follows), so by the time later tests run, earlier tests' own workbooks and
    families are still there too."""
    return next(f for f in result.families if workbook_id in f.members)


@pytest.fixture
async def estate(settings: Settings):
    """Three workbooks sharing one table, one shelf field and one calc shape, and a fourth
    that shares nothing — no ENCODES edges anywhere, only what a real harvest writes today.
    """
    pool = await create_pool(settings)
    try:
        repository = AgeGraphRepository(pool, graph_name=settings.graph_name)
        writer = GraphWriter(repository, event_source=source_for(settings.graph_name))
        suffix = new_ulid()[10:18].lower()

        site = await _write(writer, "Site", luid=f"s-{suffix}", name=f"RQA {suffix}")
        project = await _write(writer, "Project", luid=f"p-{suffix}", name="Risk Core")
        await _edge(writer, "CONTAINS", site, project)

        shared_table = await _write(writer, "Table", name="positions", schema="risk")
        other_table = await _write(writer, "Table", name="prices", schema="mkt")

        shared_ast = {
            "op": "DIV",
            "args": [
                {"fn": "SUM", "arg": {"field": "Margin"}},
                {"fn": "SUM", "arg": {"field": "Revenue"}},
            ],
        }

        async def workbook(name: str, table: str, ast: Any) -> str:
            book = await _write(
                writer, "Workbook", luid=f"{name}-{suffix}", name=name, revision="1"
            )
            await _edge(writer, "CONTAINS", project, book)
            sheet = await _write(
                writer,
                "Worksheet",
                name=f"{name} sheet",
                rows_shelf=["Desk"],
                cols_shelf=["Trade Date"],
                marks_shelf=["color:Book"],
            )
            await _edge(writer, "CONTAINS", book, sheet)
            datasource = await _write(
                writer, "Datasource", name=f"{name} ds", type="published",
                luid=f"ds-{name}-{suffix}",
            )
            await _edge(writer, "USES_DATASOURCE", sheet, datasource)
            connection = await _write(
                writer, "Connection", **{"class": "postgres"}, server="warehouse", db="risk"
            )
            await _edge(writer, "CONNECTS_TO", datasource, connection)
            await _edge(writer, "CONNECTS_TO", connection, table)
            calc = await _write(
                writer, "CalculatedField", name=f"{name} margin",
                formula="SUM([M]) / SUM([R])", formula_ast=ast,
            )
            # HAS_FIELD, not ENCODES: §12.1 says a workbook *defines* a calc shape, which is
            # what its datasource HAS_FIELD-ing the calc means, whether or not any sheet
            # ever places it on a shelf.
            await _edge(writer, "HAS_FIELD", datasource, calc)
            return book

        alpha = await workbook("Alpha", shared_table, shared_ast)
        bravo = await workbook("Bravo", shared_table, shared_ast)
        charlie = await workbook("Charlie", shared_table, shared_ast)
        delta = await workbook("Delta", other_table, {"fn": "SUM", "arg": {"field": "X"}})

        programmes = PostgresProgrammeStore(pool, graph_name=settings.graph_name)
        # A real, current timestamp — not a fixed literal — so that whichever test in this
        # module runs last opens the most-recently-started programme and unambiguously owns
        # "the open programme" `_open_programme` picks; the fixture is function-scoped but
        # the graph and its programme rows are not (see the module's own note on that).
        programme = await programmes.open_programme(
            name=f"R1 BlackRock {suffix}",
            started_at=datetime.now(UTC).isoformat(),
            created_by=PRINCIPAL.value,
        )

        yield {
            "pool": pool,
            "settings": settings,
            "writer": writer,
            "repository": repository,
            "programmes": programmes,
            "programme_id": programme.id,
            "cartographer": Cartographer(
                pool, graph_name=settings.graph_name, writer=writer, programme_store=programmes
            ),
            "alpha": alpha,
            "bravo": bravo,
            "charlie": charlie,
            "delta": delta,
            "shared_table": shared_table,
        }
    finally:
        await pool.close()


async def test_three_similar_workbooks_propose_one_family(estate) -> None:
    result = await estate["cartographer"].run(principal=PRINCIPAL)

    family = _family_of(result, estate["alpha"])
    assert family.state == "PROPOSED"
    assert set(family.members) == {estate["alpha"], estate["bravo"], estate["charlie"]}
    assert family.grain == ("Desk", "Trade Date")


async def test_the_unrelated_workbook_is_held_as_singleton(estate) -> None:
    result = await estate["cartographer"].run(principal=PRINCIPAL)

    singletons = [f for f in result.families if f.state == "SINGLETON"]
    assert any(f.members == (estate["delta"],) for f in singletons)


async def test_the_family_carries_its_evidence(estate) -> None:
    result = await estate["cartographer"].run(principal=PRINCIPAL)

    family = _family_of(result, estate["alpha"])
    assert estate["shared_table"] in family.evidence.shared_tables
    assert "Desk" in family.evidence.shared_fields
    assert family.evidence.shared_calc_shapes == 1


async def test_shares_lineage_edges_land_in_the_graph(estate) -> None:
    await estate["cartographer"].run(principal=PRINCIPAL)

    repository = estate["repository"]
    labels = await repository.labels_for([estate["alpha"], estate["bravo"]])
    assert labels[estate["alpha"]] == "Workbook"

    async with estate["pool"].acquire() as conn:
        from astra_graph.graph.queries import EDGE_INDEX_TABLE

        row = await conn.fetchrow(
            f"""
            SELECT id FROM {EDGE_INDEX_TABLE}
            WHERE graph = $1 AND label = 'SHARES_LINEAGE'
              AND from_id = ANY($2::text[]) AND to_id = ANY($2::text[])
            """,
            estate["settings"].graph_name,
            [estate["alpha"], estate["bravo"]],
        )
    assert row is not None


async def test_a_re_run_upserts_the_same_shares_lineage_edge(estate) -> None:
    """No duplicate evidence edges for the same pair across runs."""
    await estate["cartographer"].run(principal=PRINCIPAL)
    await estate["cartographer"].run(principal=PRINCIPAL)

    async with estate["pool"].acquire() as conn:
        from astra_graph.graph.queries import EDGE_INDEX_TABLE

        rows = await conn.fetch(
            f"""
            SELECT id FROM {EDGE_INDEX_TABLE}
            WHERE graph = $1 AND label = 'SHARES_LINEAGE'
              AND from_id = ANY($2::text[]) AND to_id = ANY($2::text[])
            """,
            estate["settings"].graph_name,
            [estate["alpha"], estate["bravo"]],
        )
    assert len(rows) == 1


async def test_a_re_run_retires_its_own_prior_proposal(estate) -> None:
    first = await estate["cartographer"].run(principal=PRINCIPAL)
    first_family_id = _family_of(first, estate["alpha"]).id

    second = await estate["cartographer"].run(principal=PRINCIPAL)
    second_family_id = _family_of(second, estate["alpha"]).id

    assert first_family_id != second_family_id, "a fresh proposal, not the same node re-scored"

    record = await estate["repository"].get_node_record(first_family_id)
    assert record is not None
    assert record.properties.get("retired_at") is not None


async def test_a_re_run_never_touches_a_family_a_human_has_accepted(estate) -> None:
    """S3.1.2 is the split/merge/move story; this only has to prove the Cartographer stays
    out of its way. A DRAFT family is a decision this module did not make."""
    accepted_id = await _write(
        estate["writer"], "ModelFamily", name="Accepted by hand", state="DRAFT",
    )
    await _edge(estate["writer"], "IN_FAMILY", estate["alpha"], accepted_id, confidence=1.0)

    await estate["cartographer"].run(principal=PRINCIPAL)

    record = await estate["repository"].get_node_record(accepted_id)
    assert record is not None
    assert record.properties.get("retired_at") is None
    assert record.properties["state"] == "DRAFT"


async def test_the_run_is_recorded_on_the_open_programme(estate) -> None:
    result = await estate["cartographer"].run(principal=PRINCIPAL)

    programmes = await estate["programmes"].programmes()
    programme = next(p for p in programmes if p.id == estate["programme_id"])

    assert programme.clustering is not None
    assert programme.clustering["family_count"] == result.family_count
    assert programme.clustering["singleton_count"] == result.singleton_count
    assert "1" in programme.clustering["histogram"] or "3" in programme.clustering["histogram"]


# ----------------------------------------------------------------------------- the API


@pytest.fixture
async def http_client(estate):
    """The real app (real exception handling included), with only ``cartographer`` wired
    to this fixture's pool — the same "lifespan bypassed, state set directly" pattern
    ``conftest.py``'s own ``client`` fixture uses, but pointed at Postgres instead of the
    in-memory fakes."""
    from httpx import ASGITransport, AsyncClient

    from astra_graph.api.routes_families import ClusteringStatus
    from astra_graph.main import create_app

    app = create_app()
    app.state.cartographer = estate["cartographer"]
    app.state.cartographer_status = ClusteringStatus()

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://graph-svc") as async_client:
        yield async_client


async def test_a_clustering_run_started_over_http_lands_in_the_graph(estate, http_client) -> None:
    from .conftest import ARTIZENT_HEADERS

    response = await http_client.post(
        "/v1/families:cluster", json={}, headers=ARTIZENT_HEADERS
    )
    assert response.status_code == 202

    # No polling loop: the fixture's estate is tiny, so by the time the client gets a
    # second response the background task has almost always finished. If it has not,
    # the retry below waits for it rather than asserting on a still-running task.
    import asyncio

    status_body: dict = {}
    for _ in range(50):
        status_response = await http_client.get(
            "/v1/families:cluster/status", headers=ARTIZENT_HEADERS
        )
        status_body = status_response.json()
        if not status_body["running"]:
            break
        await asyncio.sleep(0.1)

    assert status_body["running"] is False
    assert status_body["last_result"] is not None
    assert status_body["last_error"] is None

    families_response = await http_client.get("/v1/families", headers=ARTIZENT_HEADERS)
    assert families_response.status_code == 200
    members = {
        member for family in families_response.json()["families"] for member in family["members"]
    }
    assert estate["alpha"] in members


async def test_a_second_concurrent_run_is_refused(estate, http_client) -> None:
    from .conftest import ARTIZENT_HEADERS

    first = await http_client.post("/v1/families:cluster", json={}, headers=ARTIZENT_HEADERS)
    assert first.status_code == 202

    second = await http_client.post("/v1/families:cluster", json={}, headers=ARTIZENT_HEADERS)
    assert second.status_code == 400
    assert "already in progress" in second.json()["message"]


async def test_one_family_is_readable_by_id_over_http(estate, http_client) -> None:
    from .conftest import ARTIZENT_HEADERS

    result = await estate["cartographer"].run(principal=PRINCIPAL)
    family_id = _family_of(result, estate["alpha"]).id

    response = await http_client.get(f"/v1/families/{family_id}", headers=ARTIZENT_HEADERS)
    assert response.status_code == 200
    assert estate["alpha"] in response.json()["members"]


async def test_an_unknown_family_id_is_a_404_over_http(estate, http_client) -> None:
    from .conftest import ARTIZENT_HEADERS

    response = await http_client.get(
        "/v1/families/01HZZZZZZZZZZZZZZZZZZZZZZZ", headers=ARTIZENT_HEADERS
    )
    assert response.status_code == 404
