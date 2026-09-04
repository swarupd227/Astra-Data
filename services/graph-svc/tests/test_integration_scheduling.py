"""Schedules and incremental harvest against a real PostgreSQL + Apache AGE.

What only the real store can answer: that ``FOR UPDATE SKIP LOCKED`` actually stops two
schedulers claiming the same schedule, that the source timestamp survives a round trip
through ``timestamptz`` well enough to compare against, and that a drift notice lands in
the outbox alongside the mutations without breaking the replay the whole record rests on.
"""

from __future__ import annotations

import asyncio
import os
import threading
from collections.abc import Callable, Coroutine
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

pytestmark = pytest.mark.integration

asyncpg = pytest.importorskip("asyncpg")

from astra_graph.adapters.contract import Scope  # noqa: E402
from astra_graph.adapters.fixture import (  # noqa: E402
    FixtureSite,
    FixtureSourceAdapter,
    FixtureWorkbook,
)
from astra_graph.config import Settings  # noqa: E402
from astra_graph.credentials import StaticCredentialProvider  # noqa: E402
from astra_graph.events import EventType, source_for  # noqa: E402
from astra_graph.graph import AgeGraphRepository, create_pool  # noqa: E402
from astra_graph.graph.queries import accessor  # noqa: E402
from astra_graph.harvest import (  # noqa: E402
    Cadence,
    Harvester,
    HarvestMode,
    HarvestRequest,
    HarvestScheduler,
    PostgresHarvestStore,
    PostgresScheduleStore,
    ScheduleError,
    new_schedule,
)
from astra_graph.ids import new_ulid  # noqa: E402
from astra_graph.migration_units import (  # noqa: E402
    InMemoryMigrationUnitRegistry,
    MigrationUnitRef,
)
from astra_graph.migrations import run as run_migrations  # noqa: E402
from astra_graph.ontology import EDGE_LABELS, NODE_LABELS  # noqa: E402
from astra_graph.principal import Principal  # noqa: E402
from astra_graph.replay import replay  # noqa: E402
from astra_graph.writes import GraphWriter  # noqa: E402

PRINCIPAL = Principal("agent:harvester", run_id="run-integration")
CREDENTIALS = StaticCredentialProvider({"tableau/rqa": "a-token"})
NOW = datetime(2027, 3, 1, 1, 30, tzinfo=UTC)


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
    config = _settings(f"astra_sched_{new_ulid()[10:22].lower()}")

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
                "public.harvest_workbook",
                "public.harvest_schedule",
                "public.estate_event",
            ):
                await conn.execute(f"DELETE FROM {table} WHERE graph = $1", config.graph_name)
            await conn.execute("SELECT ag_catalog.drop_graph($1, true)", config.graph_name)
        finally:
            await conn.close()

    if not _run_off_loop(setup):
        pytest.skip("PostgreSQL with Apache AGE not reachable")

    yield config
    _run_off_loop(teardown)


@pytest.fixture
async def stack(settings: Settings):
    pool = await create_pool(settings)
    try:
        repository = AgeGraphRepository(pool, graph_name=settings.graph_name)
        writer = GraphWriter(repository, event_source=source_for(settings.graph_name))
        harvest_store = PostgresHarvestStore(pool, graph_name=settings.graph_name)
        schedules = PostgresScheduleStore(pool, graph_name=settings.graph_name)

        def harvester(site: FixtureSite, units=None) -> tuple[Harvester, FixtureSourceAdapter]:
            adapter = FixtureSourceAdapter([site])
            return (
                Harvester(
                    adapter=adapter,
                    writer=writer,
                    store=harvest_store,
                    credentials=CREDENTIALS,
                    graph_name=settings.graph_name,
                    migration_units=units,
                ),
                adapter,
            )

        yield harvester, schedules, repository
    finally:
        await pool.close()


def _site(name: str, *, updated_at: str = "2027-02-01T09:00:00.000Z") -> FixtureSite:
    return FixtureSite(
        name=name,
        workbooks=[
            FixtureWorkbook(
                name="Daily VaR",
                luid=f"{name}-wb-1",
                project="Risk Core",
                sheets=1,
                dashboards=1,
                datasources=1,
                fields=2,
                calculations=1,
                updated_at=updated_at,
            )
        ],
    )


def _request(site: str, mode: HarvestMode = HarvestMode.FULL) -> HarvestRequest:
    return HarvestRequest(
        scope=Scope(site=site), credential_reference="tableau/rqa", mode=mode
    )


async def test_the_source_timestamp_survives_the_round_trip(stack) -> None:
    """S1.2.4 criterion 1, through a real ``timestamptz`` column.

    The in-memory store keeps the string it was given. This one parses it, stores it as an
    instant, and renders it back — and the comparison has to hold across all three.
    """
    build, _schedules, _repository = stack
    name = f"s{new_ulid()[10:18].lower()}"
    site = _site(name)
    harvester, adapter = build(site)

    await harvester.run(_request(name), principal=PRINCIPAL)
    assert adapter.fetches == 1

    progress = await harvester.run(
        _request(name, HarvestMode.INCREMENTAL), principal=PRINCIPAL
    )

    assert adapter.fetches == 1, "a stored timestamp did not compare equal to itself"
    assert progress.skipped_not_modified == 1


async def test_a_changed_workbook_is_re_parsed_through_the_real_store(stack) -> None:
    build, _schedules, _repository = stack
    name = f"s{new_ulid()[10:18].lower()}"
    site = _site(name)
    harvester, adapter = build(site)
    await harvester.run(_request(name), principal=PRINCIPAL)

    site.workbooks[0].updated_at = "2027-02-20T09:00:00.000Z"
    site.workbooks[0].revision = "2"
    site.workbooks[0].calculations = 2

    progress = await harvester.run(
        _request(name, HarvestMode.INCREMENTAL), principal=PRINCIPAL
    )

    assert adapter.fetches == 2
    assert progress.parsed == 1


async def test_two_schedulers_do_not_start_the_same_run_twice(stack) -> None:
    """S1.2.4 against a scaled-out deployment. The claim is the whole reason for the lock.

    Two pools, two schedulers, one due schedule, polled at the same moment: exactly one
    run must start. Without ``FOR UPDATE SKIP LOCKED`` both would read the row as due.
    """
    _build, schedules, _repository = stack
    name = f"s{new_ulid()[10:18].lower()}"
    schedule = new_schedule(
        site=name,
        project=None,
        credential_reference="tableau/rqa",
        cadence=Cadence(every_minutes=60),
        created_by="user:p.eng@artizent.example",
        now=NOW,
    )
    await schedules.create(schedule)
    later = NOW + timedelta(hours=2)

    first, second = await asyncio.gather(
        schedules.due(now=later), schedules.due(now=later)
    )

    claimed = [s.id for s in first] + [s.id for s in second]
    assert claimed == [schedule.id], f"claimed {len(claimed)} times, expected once"


async def test_a_claim_advances_the_next_firing_in_the_database(stack) -> None:
    _build, schedules, _repository = stack
    name = f"s{new_ulid()[10:18].lower()}"
    await schedules.create(
        new_schedule(
            site=name,
            project=None,
            credential_reference="tableau/rqa",
            cadence=Cadence(daily_at="02:00"),
            created_by="user:p.eng@artizent.example",
            now=NOW,
        )
    )
    later = datetime(2027, 3, 1, 2, 5, tzinfo=UTC)

    claimed = await schedules.due(now=later)

    assert claimed[0].next_run_at == "2027-03-02T02:00:00.000Z"
    stored = await schedules.get(claimed[0].id)
    assert stored.next_run_at == "2027-03-02T02:00:00.000Z"


async def test_one_scope_has_one_schedule_in_the_database(stack) -> None:
    """The partial unique indexes, which are the only thing enforcing this across replicas."""
    _build, schedules, _repository = stack
    name = f"s{new_ulid()[10:18].lower()}"

    def one():
        return new_schedule(
            site=name,
            project=None,
            credential_reference="tableau/rqa",
            cadence=Cadence(daily_at="02:00"),
            created_by="user:p.eng@artizent.example",
            now=NOW,
        )

    await schedules.create(one())

    with pytest.raises(ScheduleError, match="already exists"):
        await schedules.create(one())


async def test_a_project_scope_is_distinct_from_its_site(stack) -> None:
    """NULL project is its own scope, which is why it takes two partial indexes."""
    _build, schedules, _repository = stack
    name = f"s{new_ulid()[10:18].lower()}"

    def one(project):
        return new_schedule(
            site=name,
            project=project,
            credential_reference="tableau/rqa",
            cadence=Cadence(daily_at="02:00"),
            created_by="user:p.eng@artizent.example",
            now=NOW,
        )

    await schedules.create(one(None))
    await schedules.create(one("Risk Core"))
    await schedules.create(one("Treasury"))

    stored = [s for s in await schedules.list_schedules() if s.site == name]
    assert sorted((s.project or "") for s in stored) == ["", "Risk Core", "Treasury"]


async def test_the_scheduler_runs_a_schedule_and_records_its_outcome(stack) -> None:
    """End to end: a due schedule, a real incremental harvest, the result written back."""
    build, schedules, _repository = stack
    name = f"s{new_ulid()[10:18].lower()}"
    site = _site(name)
    harvester, adapter = build(site)
    await harvester.run(_request(name), principal=PRINCIPAL)

    # The module shares a graph, and a tick claims every schedule due in it — correctly,
    # since that is what a scheduler is for. Pausing the ones other tests left makes this
    # test about one schedule rather than about the order the module happens to run in.
    for other in await schedules.list_schedules():
        await schedules.set_enabled(other.id, enabled=False, reason="not this test's")

    schedule = await schedules.create(
        new_schedule(
            site=name,
            project=None,
            credential_reference="tableau/rqa",
            cadence=Cadence(every_minutes=60),
            created_by="user:p.eng@artizent.example",
            now=NOW,
        )
    )
    scheduler = HarvestScheduler(store=schedules, harvester=harvester)

    started = await scheduler.tick(now=NOW + timedelta(hours=2))
    for task in list(scheduler._running.values()):
        await asyncio.gather(task, return_exceptions=True)

    assert len(started) == 1
    assert adapter.fetches == 1, "the scheduled run was incremental"
    stored = await schedules.get(schedule.id)
    assert stored.last_run_id == started[0]
    assert stored.last_run_state == "COMPLETED"
    assert stored.consecutive_failures == 0


async def test_a_drift_notice_lands_in_the_outbox_and_replay_still_holds(stack) -> None:
    """S1.2.4 criterion 2, and the property ADR 0003 rests on, together.

    The notice shares the mutation outbox. If replay could not skip it, the nightly
    verification job would start failing the first time a client edited a workbook.
    """
    build, _schedules, repository = stack
    name = f"s{new_ulid()[10:18].lower()}"
    site = _site(name)
    registry = InMemoryMigrationUnitRegistry(
        {(name, f"{name}-wb-1"): MigrationUnitRef("mu_01HX7", "PROVING", name, f"{name}-wb-1")}
    )
    harvester, _adapter = build(site, registry)
    await harvester.run(_request(name), principal=PRINCIPAL)

    site.workbooks[0].updated_at = "2027-02-20T09:00:00.000Z"
    site.workbooks[0].revision = "2"
    site.workbooks[0].calculations = 2
    progress = await harvester.run(
        _request(name, HarvestMode.INCREMENTAL), principal=PRINCIPAL
    )

    assert progress.drifted == 1
    assert registry.marked and registry.marked[0][0] == "mu_01HX7"

    notices = await repository.events_of_type(EventType.SOURCE_DRIFT, limit=50)
    mine = [e for e in notices if e.data.get("site") == name]
    assert len(mine) == 1
    assert mine[0].data["migration_unit"]["state"] == "PROVING"
    assert mine[0].label == "Workbook", "the notice names the element it is about"

    # And the stream is still a stream: replaying it into an empty graph works.
    from .fakes import InMemoryGraphRepository

    result = await replay(repository, InMemoryGraphRepository())
    assert result.notices >= 1
    assert result.nodes > 0
