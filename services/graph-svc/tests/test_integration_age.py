"""Against a real PostgreSQL + Apache AGE.

Everything above runs on an in-memory store because the story is about enforcement, not
storage. These tests cover what only the real store can answer: that the migration builds
the graph, that AGE round-trips every property type the ontology declares, and that a
failed write in a batch rolls back both the graph and the element index.

Skipped when no database is reachable. `make dev-up` starts one.
"""

from __future__ import annotations

import os

import pytest

pytestmark = pytest.mark.integration

asyncpg = pytest.importorskip("asyncpg")

from astra_graph.config import Settings  # noqa: E402
from astra_graph.errors import DuplicateElementError  # noqa: E402
from astra_graph.graph import AgeGraphRepository, create_pool  # noqa: E402
from astra_graph.ids import new_ulid  # noqa: E402
from astra_graph.migrations import run as run_migrations  # noqa: E402
from astra_graph.ontology import EDGE_LABELS, NODE_LABELS  # noqa: E402
from astra_graph.principal import Principal  # noqa: E402
from astra_graph.writes import EdgeWrite, GraphWriter, NodeWrite  # noqa: E402

PRINCIPAL = Principal("agent:harvester", run_id="run-integration")


def _settings() -> Settings:
    return Settings(
        postgres_host=os.environ.get("ASTRA_POSTGRES_HOST", "localhost"),
        postgres_port=int(os.environ.get("ASTRA_POSTGRES_PORT", "5432")),
        postgres_db=os.environ.get("ASTRA_POSTGRES_DB", "astra"),
        postgres_user=os.environ.get("ASTRA_POSTGRES_USER", "astra"),
        postgres_password=os.environ.get("ASTRA_POSTGRES_PASSWORD", "astra_local_dev_only"),
        graph_name=os.environ.get("ASTRA_GRAPH_NAME", "astra_estate_test"),
        env="test",
        log_level="WARNING",
        pool_min_size=1,
        pool_max_size=4,
    )


@pytest.fixture(scope="module")
async def settings() -> Settings:
    config = _settings()
    try:
        conn = await asyncpg.connect(dsn=config.dsn, timeout=3)
    except Exception as exc:
        pytest.skip(f"PostgreSQL with Apache AGE not reachable: {exc}")
    await conn.close()
    return config


@pytest.fixture(scope="module")
async def migrated(settings: Settings) -> Settings:
    conn = await asyncpg.connect(dsn=settings.dsn)
    try:
        await run_migrations(conn)
    finally:
        await conn.close()
    return settings


@pytest.fixture
async def repository(migrated: Settings):
    pool = await create_pool(migrated)
    try:
        yield AgeGraphRepository(pool, graph_name=migrated.graph_name)
    finally:
        await pool.close()


@pytest.fixture
def writer(repository) -> GraphWriter:
    return GraphWriter(repository)


async def test_migration_creates_the_graph_and_every_label(migrated: Settings) -> None:
    conn = await asyncpg.connect(dsn=migrated.dsn)
    try:
        await conn.execute("LOAD 'age'")
        labels = {
            row["name"]
            for row in await conn.fetch(
                """
                SELECT l.name FROM ag_catalog.ag_label l
                JOIN ag_catalog.ag_graph g ON g.graphid = l.graph
                WHERE g.name = $1
                """,
                migrated.graph_name,
            )
        }
    finally:
        await conn.close()
    assert labels >= NODE_LABELS
    assert labels >= EDGE_LABELS


async def test_health_reports_the_graph_exists(repository) -> None:
    await repository.health()


async def test_round_trip_every_property_type(writer, repository) -> None:
    """A node exercising string, text, int, float, bool, timestamp, enum, list and JSON."""
    created = await writer.write_nodes(
        [
            NodeWrite(
                type="Workbook",
                properties={
                    "luid": "8f3e-daily-var",
                    "name": "Daily VaR",
                    "revision": "14",
                    "size": 4_194_304,
                    "extract_flag": True,
                    "last_published": "2027-01-09T11:02:00Z",
                    "views_90d": 412,
                },
            ),
            NodeWrite(
                type="Worksheet",
                properties={
                    "name": "VaR by Desk",
                    "mark_type": "bar",
                    "rows_shelf": ["Desk", "Book"],
                    "cols_shelf": ["Date"],
                    "marks_shelf": [],
                    "sort": [{"field": "VaR", "direction": "desc"}],
                },
            ),
            NodeWrite(
                type="CalculatedField",
                properties={
                    "name": "Margin %",
                    "formula": "SUM([Margin]) / SUM([Revenue])",
                    "formula_ast": {"op": "DIV", "args": [{"fn": "SUM"}, {"fn": "SUM"}]},
                    "class": "C2",
                    "table_calc_flag": False,
                },
            ),
        ],
        principal=PRINCIPAL,
    )
    assert len(created) == 3

    workbook = await repository.get_node(created[0]["properties"]["id"])
    assert workbook["label"] == "Workbook"
    assert workbook["properties"]["size"] == 4_194_304
    assert workbook["properties"]["extract_flag"] is True
    assert workbook["properties"]["side"] == "source"
    assert workbook["properties"]["created_by"] == PRINCIPAL.value
    assert workbook["properties"]["created_in_run"] == "run-integration"

    worksheet = await repository.get_node(created[1]["properties"]["id"])
    assert worksheet["properties"]["rows_shelf"] == ["Desk", "Book"]
    assert worksheet["properties"]["sort"][0]["direction"] == "desc"

    calc = await repository.get_node(created[2]["properties"]["id"])
    assert calc["properties"]["formula_ast"]["op"] == "DIV"
    assert calc["properties"]["formula"] == "SUM([Margin]) / SUM([Revenue])"


async def test_edge_round_trip(writer, repository) -> None:
    nodes = await writer.write_nodes(
        [
            NodeWrite(type="Site", properties={"luid": f"site-{new_ulid()}", "name": "RQA"}),
            NodeWrite(
                type="Project", properties={"luid": f"proj-{new_ulid()}", "name": "Risk Core"}
            ),
        ],
        principal=PRINCIPAL,
    )
    site_id, project_id = (n["properties"]["id"] for n in nodes)

    edge = await writer.write_edge(
        EdgeWrite(type="CONTAINS", from_id=site_id, to_id=project_id, properties={}),
        principal=PRINCIPAL,
    )
    assert edge["label"] == "CONTAINS"
    assert edge["properties"]["written_by"] == PRINCIPAL.value

    read_back = await repository.get_edge(edge["properties"]["id"])
    assert read_back["label"] == "CONTAINS"


async def test_duplicate_id_is_rejected_by_the_store(repository) -> None:
    element_id = new_ulid()
    properties = {
        "id": element_id,
        "side": "source",
        "created_by": "agent:test",
        "created_at": "2027-01-14T09:12:07.000Z",
        "luid": f"site-{element_id}",
        "name": "RQA",
    }
    await repository.create_nodes([("Site", properties)])
    with pytest.raises(DuplicateElementError):
        await repository.create_nodes([("Site", dict(properties))])


async def test_a_failing_batch_rolls_back_entirely(repository) -> None:
    """The second element collides; neither may survive (spec §8.4 writes per workbook
    transactionally)."""
    shared_id = new_ulid()
    first_id = new_ulid()

    def props(element_id: str) -> dict[str, object]:
        return {
            "id": element_id,
            "side": "source",
            "created_by": "agent:test",
            "created_at": "2027-01-14T09:12:07.000Z",
            "luid": f"site-{element_id}",
            "name": "RQA",
        }

    await repository.create_nodes([("Site", props(shared_id))])
    with pytest.raises(DuplicateElementError):
        await repository.create_nodes(
            [("Site", props(first_id)), ("Site", props(shared_id))]
        )
    assert await repository.get_node(first_id) is None


async def test_migrations_are_idempotent(migrated: Settings) -> None:
    conn = await asyncpg.connect(dsn=migrated.dsn)
    try:
        assert await run_migrations(conn) == []
    finally:
        await conn.close()
