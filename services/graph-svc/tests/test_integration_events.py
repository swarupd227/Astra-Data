"""Events, retirement and replay against a real PostgreSQL + Apache AGE.

The replay here is the real one: events are applied through the same repository the
service writes with, into a second AGE graph in the same database, and the two graphs are
compared element by element. That is the exercise the nightly CI job runs
(``tools/verify_replay.py``), and it is what S1.1.3 criterion 2 asks for.
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

from astra_graph.config import Settings  # noqa: E402
from astra_graph.errors import InvalidRequestError  # noqa: E402
from astra_graph.events import EventType, source_for  # noqa: E402
from astra_graph.graph import AgeGraphRepository, create_pool  # noqa: E402
from astra_graph.graph.queries import accessor  # noqa: E402
from astra_graph.ids import new_ulid  # noqa: E402
from astra_graph.migrations import run as run_migrations  # noqa: E402
from astra_graph.ontology import EDGE_LABELS, NODE_LABELS  # noqa: E402
from astra_graph.principal import Principal  # noqa: E402
from astra_graph.replay import compare, replay  # noqa: E402
from astra_graph.writes import GraphWriter, NodeWrite  # noqa: E402

from .conftest import seed_estate  # noqa: E402

PRINCIPAL = Principal("agent:harvester", run_id="run-integration")


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
        pool_max_size=4,
    )



def _run_off_loop(factory: Callable[[], Coroutine[Any, Any, Any]]) -> Any:
    """Run a coroutine on a thread of its own.

    A synchronous fixture is finalised inside pytest-asyncio's running loop, where
    ``asyncio.run`` refuses to start a second one. A dedicated thread gets a clean loop
    for the connection this setup and teardown need.
    """
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


@pytest.fixture(scope="module")
def settings() -> Settings:
    """A graph of this module's own, so the replay comparison sees only its own writes.

    Deliberately a *synchronous* fixture that drives its own event loop. A module-scoped
    async fixture is set up in the first test's loop and torn down after the last test,
    by which time that loop is closed and its connections are unusable.
    """
    config = _settings(f"astra_events_{new_ulid()[-12:].lower()}")

    async def setup() -> bool:
        try:
            conn = await asyncpg.connect(dsn=config.dsn, timeout=3)
        except Exception:
            return False
        try:
            await run_migrations(conn)
            await _create_graph(conn, config.graph_name)
            await _create_graph(conn, f"{config.graph_name}_replay")
        finally:
            await conn.close()
        return True

    async def teardown() -> None:
        # One connection per drop. Apache AGE caches label relations per session, and
        # dropping a graph leaves that cache stale: a second drop on the same connection
        # fails with "label (relation) cache corrupted" and the backend closes the
        # connection. See ADR 0003.
        for graph in (config.graph_name, f"{config.graph_name}_replay"):
            conn = await asyncpg.connect(dsn=config.dsn)
            try:
                await _drop_graph(conn, graph)
            finally:
                await conn.close()

    if not _run_off_loop(setup):
        pytest.skip("PostgreSQL with Apache AGE not reachable")

    yield config
    _run_off_loop(teardown)


async def _create_graph(conn: asyncpg.Connection, graph: str) -> None:
    await conn.execute("LOAD 'age'")
    await conn.execute('SET search_path = ag_catalog, "$user", public')
    await conn.execute("SELECT ag_catalog.create_graph($1)", graph)
    for label in sorted(NODE_LABELS):
        await conn.execute("SELECT ag_catalog.create_vlabel($1, $2)", graph, label)
        await conn.execute(f'CREATE INDEX ON {graph}."{label}" USING BTREE ({accessor("id")})')
    for label in sorted(EDGE_LABELS):
        await conn.execute("SELECT ag_catalog.create_elabel($1, $2)", graph, label)
        await conn.execute(f'CREATE INDEX ON {graph}."{label}" USING BTREE ({accessor("id")})')


async def _drop_graph(conn: asyncpg.Connection, graph: str, *, missing_ok: bool = False) -> None:
    await conn.execute("LOAD 'age'")
    exists = await conn.fetchval(
        "SELECT EXISTS (SELECT 1 FROM ag_catalog.ag_graph WHERE name = $1)", graph
    )
    if not exists:
        if missing_ok:
            return
        return
    await conn.execute("DELETE FROM public.estate_edge_index WHERE graph = $1", graph)
    await conn.execute("DELETE FROM public.estate_element_index WHERE graph = $1", graph)
    await conn.execute("SELECT ag_catalog.drop_graph($1, true)", graph)


@pytest.fixture
async def repository(settings: Settings):
    pool = await create_pool(settings)
    try:
        yield AgeGraphRepository(pool, graph_name=settings.graph_name)
    finally:
        await pool.close()


@pytest.fixture
def writer(repository, settings: Settings) -> GraphWriter:
    return GraphWriter(repository, event_source=source_for(settings.graph_name))


# ---------------------------------------------------------------------- events


async def test_the_event_is_committed_with_the_mutation(repository, writer) -> None:
    created = await writer.write_nodes(
        [NodeWrite(type="Site", properties={"luid": f"rqa-{new_ulid()}", "name": "RQA"})],
        principal=PRINCIPAL,
    )
    events = await repository.read_events()
    assert len(events) == 1
    event = events[0]
    assert event.type is EventType.NODE_UPSERTED
    assert event.subject == created[0]["properties"]["id"]
    assert event.principal == "agent:harvester"
    assert event.run_id == "run-integration"
    assert event.data["properties"] == created[0]["properties"]


async def test_a_rejected_write_writes_no_event(repository, writer) -> None:
    from astra_graph.errors import OntologyViolationError

    before = len(await repository.read_events(limit=10_000))
    with pytest.raises(OntologyViolationError):
        await writer.write_nodes(
            [NodeWrite(type="Workbook", properties={"luid": "only"})], principal=PRINCIPAL
        )
    assert len(await repository.read_events(limit=10_000)) == before


async def test_events_are_ordered_and_pageable(repository, writer) -> None:
    await seed_estate(writer, suffix=f"-{new_ulid()}")
    first = await repository.read_events(limit=5)
    assert [e.sequence for e in first] == sorted(e.sequence for e in first)
    second = await repository.read_events(after=first[-1].sequence, limit=5)
    assert second[0].sequence > first[-1].sequence


# ------------------------------------------------------------------ retirement


async def test_retirement_keeps_the_node_and_hides_it_from_traversal(repository, writer) -> None:
    seeded = await seed_estate(writer, suffix=f"-{new_ulid()}")

    await writer.retire_node(
        seeded["dashboard"], reason="Superseded by the risk overview", principal=PRINCIPAL
    )

    record = await repository.get_node_record(seeded["dashboard"])
    assert record is not None, "retirement must not delete the node"
    assert record.properties["retirement_reason"] == "Superseded by the risk overview"
    assert record.properties["retired_by"] == "agent:harvester"

    live = await repository.neighbourhood(seeded["workbook"], depth=1)
    assert seeded["dashboard"] not in {n.node.id for n in live.neighbours}

    including = await repository.neighbourhood(
        seeded["workbook"], depth=1, include_retired=True
    )
    assert seeded["dashboard"] in {n.node.id for n in including.neighbours}


async def test_retiring_twice_is_refused(repository, writer) -> None:
    seeded = await seed_estate(writer, suffix=f"-{new_ulid()}")
    await writer.retire_node(seeded["dashboard"], reason="No longer used", principal=PRINCIPAL)
    with pytest.raises(InvalidRequestError, match="already retired"):
        await writer.retire_node(
            seeded["dashboard"], reason="No longer used", principal=PRINCIPAL
        )


# ---------------------------------------------------------------------- upsert


async def test_upsert_replaces_the_property_set_in_age(repository, writer) -> None:
    node_id = new_ulid()
    luid = f"wb-{new_ulid()}"
    await writer.upsert_nodes(
        [
            NodeWrite(
                type="Workbook",
                id=node_id,
                properties={"luid": luid, "name": "Daily VaR", "revision": "14", "size": 99},
            )
        ],
        principal=PRINCIPAL,
    )
    await writer.upsert_nodes(
        [
            NodeWrite(
                type="Workbook",
                id=node_id,
                properties={"luid": luid, "name": "Daily VaR v2", "revision": "15"},
            )
        ],
        principal=PRINCIPAL,
    )
    record = await repository.get_node_record(node_id)
    assert record.properties["revision"] == "15"
    assert "size" not in record.properties


# ---------------------------------------------------------------------- replay


@pytest.fixture
async def replay_target(settings: Settings):
    """An AGE graph alongside the live one, for the rebuild to write into.

    Created once by the module fixture, not per test: replay is idempotent, so a target
    carrying an earlier test's rebuild converges on the same content, and per-test DDL
    raced the previous test's pool shutdown.
    """
    pool = await create_pool(_settings(f"{settings.graph_name}_replay"))
    try:
        yield AgeGraphRepository(pool, graph_name=f"{settings.graph_name}_replay")
    finally:
        await pool.close()


async def test_replay_from_empty_reproduces_the_live_graph(
    repository, writer, replay_target
) -> None:
    """S1.1.3 criterion 2, rebuilt into a real graph and compared element by element."""
    await seed_estate(writer, suffix=f"-{new_ulid()}")
    await writer.retire_node(
        (await repository.read_events(limit=5))[4].subject,
        reason="Retired as part of the replay exercise",
        principal=PRINCIPAL,
    )

    result = await replay(repository, replay_target)
    assert result.events_applied > 0
    # The module shares one graph, so earlier tests may have retired nodes too; what
    # matters is that retirements are replayed at all, and that the comparison holds.
    assert result.retirements >= 1

    comparison = compare(await repository.dump(), await replay_target.dump())
    assert comparison.identical, [
        f"{d.kind} {d.element_id}: {d.detail}" for d in comparison.differences[:10]
    ]
    assert comparison.live_nodes == comparison.replayed_nodes
    assert comparison.live_edges == comparison.replayed_edges


async def test_the_replayed_graph_is_a_separate_graph(
    repository, writer, replay_target, settings
) -> None:
    """The rebuild stands alongside the live estate rather than overwriting it.

    This is what the ``graph`` column on the index tables is for; without it the two
    would collide on element ids.
    """
    await seed_estate(writer, suffix=f"-{new_ulid()}")
    await replay(repository, replay_target)

    conn = await asyncpg.connect(dsn=settings.dsn)
    try:
        counts = {
            row["graph"]: row["n"]
            for row in await conn.fetch(
                "SELECT graph, count(*) AS n FROM public.estate_element_index "
                "WHERE graph = ANY($1::text[]) GROUP BY graph",
                [settings.graph_name, f"{settings.graph_name}_replay"],
            )
        }
    finally:
        await conn.close()
    assert counts[settings.graph_name] == counts[f"{settings.graph_name}_replay"]


async def test_replay_is_idempotent(repository, writer, replay_target) -> None:
    await seed_estate(writer, suffix=f"-{new_ulid()}")
    await replay(repository, replay_target)
    once = await replay_target.dump()
    await replay(repository, replay_target)
    assert compare(once, await replay_target.dump()).identical
