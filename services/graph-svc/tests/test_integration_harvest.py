"""The Harvester against a real PostgreSQL + Apache AGE.

Two things only the real store can answer: that a harvest actually lands in the graph and
is queryable through the API the rest of the platform uses, and that it is fast enough.

S1.2.1 asks for a 1,000-workbook site in under four hours, and spec §8.4 targets 500
workbooks per hour per site worker. The benchmark here measures **what this service
controls** — parse, validate and write — against a fixture adapter with no network in it.
Source I/O is the other term in that budget and belongs to the Tableau adapter's own
measurement (E2); this establishes that the platform side has room to spare.
"""

from __future__ import annotations

import asyncio
import os
import threading
import time
from collections.abc import Callable, Coroutine
from typing import Any

import pytest

pytestmark = pytest.mark.integration

asyncpg = pytest.importorskip("asyncpg")

from astra_graph.adapters.contract import Scope  # noqa: E402
from astra_graph.adapters.fixture import FixtureSourceAdapter, build_site  # noqa: E402
from astra_graph.config import Settings  # noqa: E402
from astra_graph.credentials import StaticCredentialProvider  # noqa: E402
from astra_graph.events import EventType, source_for  # noqa: E402
from astra_graph.graph import AgeGraphRepository, create_pool  # noqa: E402
from astra_graph.graph.queries import accessor  # noqa: E402
from astra_graph.harvest import (  # noqa: E402
    Harvester,
    HarvestRequest,
    HarvestState,
    PostgresHarvestStore,
)
from astra_graph.ids import new_ulid  # noqa: E402
from astra_graph.migrations import run as run_migrations  # noqa: E402
from astra_graph.ontology import EDGE_LABELS, NODE_LABELS  # noqa: E402
from astra_graph.principal import Principal  # noqa: E402
from astra_graph.writes import GraphWriter  # noqa: E402

PRINCIPAL = Principal("agent:harvester", run_id="run-integration")
CREDENTIALS = StaticCredentialProvider({"tableau/rqa": "a-personal-access-token"})

#: S1.2.1: "A site of 1,000 workbooks parses in under 4 hours on the reference
#: deployment", i.e. 14.4 seconds per workbook end to end. Spec §8.4 is tighter at 500
#: workbooks per hour per site worker — 7.2 seconds each. The platform-side budget below
#: is deliberately a fraction of that, because the rest belongs to source I/O.
PLATFORM_SECONDS_PER_WORKBOOK_BUDGET = 1.0
BENCHMARK_WORKBOOKS = 60


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
        pool_min_size=2,
        pool_max_size=12,
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
        await conn.execute(f'CREATE INDEX ON {graph}."{label}" USING BTREE ({accessor("id")})')


async def _drop_graph(conn: asyncpg.Connection, graph: str) -> None:
    await conn.execute("LOAD 'age'")
    if await conn.fetchval(
        "SELECT EXISTS (SELECT 1 FROM ag_catalog.ag_graph WHERE name = $1)", graph
    ):
        await conn.execute("DELETE FROM public.estate_edge_index WHERE graph = $1", graph)
        await conn.execute("DELETE FROM public.estate_element_index WHERE graph = $1", graph)
        await conn.execute("DELETE FROM public.harvest_run WHERE graph = $1", graph)
        await conn.execute("DELETE FROM public.harvest_workbook WHERE graph = $1", graph)
        await conn.execute("SELECT ag_catalog.drop_graph($1, true)", graph)


@pytest.fixture(scope="module")
def settings() -> Settings:
    """A graph of this module's own. Synchronous, driving its own loop off-thread: a
    module-scoped async fixture is torn down in a closed loop."""
    config = _settings(f"astra_harvest_{new_ulid()[-12:].lower()}")

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
            await _drop_graph(conn, config.graph_name)
        finally:
            await conn.close()

    if not _run_off_loop(setup):
        pytest.skip("PostgreSQL with Apache AGE not reachable")

    yield config
    _run_off_loop(teardown)


@pytest.fixture
async def harvest(settings: Settings):
    """A harvester, its repository and its store, all against the module's graph."""
    pool = await create_pool(settings)
    try:
        repository = AgeGraphRepository(pool, graph_name=settings.graph_name)
        writer = GraphWriter(repository, event_source=source_for(settings.graph_name))
        store = PostgresHarvestStore(pool, graph_name=settings.graph_name)

        def build(adapter) -> Harvester:
            return Harvester(
                adapter=adapter,
                writer=writer,
                store=store,
                credentials=CREDENTIALS,
                graph_name=settings.graph_name,
            )

        yield build, repository, store
    finally:
        await pool.close()


def _request(site: str = "rqa", **kwargs) -> HarvestRequest:
    kwargs.setdefault("scope", Scope(site=site))
    kwargs.setdefault("credential_reference", "tableau/rqa")
    return HarvestRequest(**kwargs)


# ------------------------------------------------------- a site lands in the graph


async def test_a_harvest_lands_in_the_graph_and_is_traversable(harvest) -> None:
    build, repository, _ = harvest
    site_name = f"s{new_ulid()[-8:].lower()}"
    adapter = FixtureSourceAdapter([build_site(site_name, 3)])

    progress = await build(adapter).run(_request(site_name), principal=PRINCIPAL)
    assert progress.state is HarvestState.COMPLETED
    assert progress.parsed == 3

    workbook = await repository.get_node_by_luid("Workbook", f"{site_name}-wb-00000")
    assert workbook is not None
    assert workbook.properties["name"] == "Workbook 0"
    assert workbook.properties["created_by"] == "agent:harvester"

    # Everything the workbook holds is reachable from it.
    neighbourhood = await repository.neighbourhood(workbook.id, depth=3)
    labels = {n.node.label for n in neighbourhood.neighbours}
    assert {"Worksheet", "Dashboard", "Datasource", "Field", "CalculatedField"} <= labels


async def test_a_harvest_leaves_a_mutation_event_for_every_write(harvest) -> None:
    """The harvest goes through the ordinary write path, so S1.1.3 still holds."""
    build, repository, _ = harvest
    site_name = f"s{new_ulid()[-8:].lower()}"
    before = len(await repository.read_events(limit=100_000))

    await build(FixtureSourceAdapter([build_site(site_name, 2)])).run(
        _request(site_name), principal=PRINCIPAL
    )

    events = await repository.read_events(after=before, limit=100_000)
    assert events
    assert {e.type for e in events} <= {EventType.NODE_UPSERTED, EventType.EDGE_UPSERTED}
    assert all(e.run_id == "run-integration" for e in events)


async def test_progress_survives_in_the_store(harvest) -> None:
    build, _, store = harvest
    site_name = f"s{new_ulid()[-8:].lower()}"
    progress = await build(FixtureSourceAdapter([build_site(site_name, 4)])).run(
        _request(site_name), principal=PRINCIPAL
    )

    reloaded = await store.get(progress.id)
    assert reloaded is not None
    assert reloaded.state is HarvestState.COMPLETED
    assert reloaded.queued == 4
    assert sum(p.parsed for p in reloaded.projects) == 4


async def test_failures_are_persisted_with_the_error(harvest) -> None:
    build, _, store = harvest
    site_name = f"s{new_ulid()[-8:].lower()}"
    site = build_site(site_name, 3)
    site.workbooks[1].fails_on = "fetch"

    progress = await build(FixtureSourceAdapter([site])).run(
        _request(site_name), principal=PRINCIPAL
    )
    assert progress.failed == 1

    failures = await store.failures(progress.id)
    assert len(failures) == 1
    assert failures[0].stage == "fetch"
    assert failures[0].workbook_luid == site.workbooks[1].luid


async def test_re_harvest_is_a_no_op_against_the_real_store(harvest) -> None:
    build, repository, _ = harvest
    site_name = f"s{new_ulid()[-8:].lower()}"
    adapter = FixtureSourceAdapter([build_site(site_name, 3)])
    harvester = build(adapter)

    await harvester.run(_request(site_name), principal=PRINCIPAL)
    parses = adapter.parses
    events_after_first = len(await repository.read_events(limit=100_000))

    second = await harvester.run(_request(site_name), principal=PRINCIPAL)

    assert second.skipped_unchanged == 3
    assert second.parsed == 0
    assert adapter.parses == parses
    # A no-op writes nothing, so it leaves no events either.
    assert len(await repository.read_events(limit=100_000)) == events_after_first


async def test_a_changed_workbook_updates_in_place(harvest) -> None:
    build, repository, _ = harvest
    site_name = f"s{new_ulid()[-8:].lower()}"
    site = build_site(site_name, 2)
    harvester = build(FixtureSourceAdapter([site]))

    await harvester.run(_request(site_name), principal=PRINCIPAL)
    workbook = await repository.get_node_by_luid("Workbook", f"{site_name}-wb-00001")
    original_id = workbook.id

    site.workbooks[1].name = "Renamed"
    site.workbooks[1].revision = "2"
    second = await harvester.run(_request(site_name), principal=PRINCIPAL)

    assert second.parsed == 1
    updated = await repository.get_node_by_luid("Workbook", f"{site_name}-wb-00001")
    assert updated.id == original_id, "a re-harvest must not duplicate the workbook"
    assert updated.properties["name"] == "Renamed"
    assert updated.properties["revision"] == "2"


# --------------------------------------------------------------------- throughput


@pytest.mark.slow
async def test_platform_side_throughput_has_room_for_the_four_hour_budget(harvest) -> None:
    """S1.2.1: 1,000 workbooks in under four hours.

    Measures parse, validate and write with no source I/O, and extrapolates. The figure
    printed is what the platform contributes to the budget; the Tableau adapter's own
    fetch time is measured with that adapter (E2).
    """
    build, _, _ = harvest
    site_name = f"s{new_ulid()[-8:].lower()}"
    adapter = FixtureSourceAdapter([build_site(site_name, BENCHMARK_WORKBOOKS)])

    started = time.perf_counter()
    progress = await build(adapter).run(_request(site_name), principal=PRINCIPAL)
    elapsed = time.perf_counter() - started

    assert progress.parsed == BENCHMARK_WORKBOOKS
    per_workbook = elapsed / BENCHMARK_WORKBOOKS
    projected_hours = per_workbook * 1000 / 3600
    print(
        f"\nharvest platform cost: {per_workbook:.3f}s/workbook over "
        f"{BENCHMARK_WORKBOOKS} workbooks "
        f"({elapsed:.1f}s) -> {projected_hours:.2f}h for 1,000 workbooks, "
        f"budget 4h"
    )
    assert per_workbook < PLATFORM_SECONDS_PER_WORKBOOK_BUDGET, (
        f"{per_workbook:.3f}s per workbook leaves too little of the four-hour budget "
        f"for source I/O"
    )
