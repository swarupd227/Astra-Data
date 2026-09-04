"""Projected versus planned dates per train — story S3.2.3, against real PostgreSQL.

    "Projection uses measured throughput per state over the trailing 14 days and the MU
    counts remaining; shown as a date with a confidence band. A train projected to miss
    its planned date by more than 5 working days is flagged on the Programme Board."

What only the real store can answer: that ``estate_throughput`` correctly mines genuine
state *transitions* out of the raw event log (not just any edge upsert — a Wave Board
resequence upserts the same edge without changing its state, and must not count), and that
a train with no measured history for the state its own MUs currently occupy honestly
reports "insufficient data" rather than a fabricated date.

Historical throughput is seeded by inserting rows into ``public.estate_event`` directly —
the only way to have "14 days of history" in a suite that runs in seconds; no public write
path can backdate an event's ``time``, deliberately (S1.1.1: server-managed).
"""

from __future__ import annotations

import asyncio
import json
import os
import threading
from collections.abc import Callable, Coroutine
from datetime import UTC, date, datetime, timedelta
from typing import Any

import pytest

pytestmark = pytest.mark.integration

asyncpg = pytest.importorskip("asyncpg")

from astra_graph.config import Settings  # noqa: E402
from astra_graph.events import EventType, source_for  # noqa: E402
from astra_graph.graph import AgeGraphRepository, create_pool  # noqa: E402
from astra_graph.graph.queries import accessor  # noqa: E402
from astra_graph.ids import new_ulid  # noqa: E402
from astra_graph.migrations import run as run_migrations  # noqa: E402
from astra_graph.ontology import EDGE_LABELS, NODE_LABELS  # noqa: E402
from astra_graph.principal import Principal  # noqa: E402
from astra_graph.train_projection import estate_throughput, project_trains  # noqa: E402
from astra_graph.trains import get_train  # noqa: E402
from astra_graph.writes import EdgeWrite, GraphWriter, NodeWrite  # noqa: E402

PRINCIPAL = Principal("agent:projection-test", run_id="run-projection")


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
    config = _settings(f"astra_prj_{new_ulid()[10:22].lower()}")

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


async def _edge(writer: GraphWriter, type_: str, source: str, target: str, **props: Any) -> str:
    edge = await writer.write_edge(
        EdgeWrite(type=type_, from_id=source, to_id=target, properties=props), principal=PRINCIPAL
    )
    return str(edge["properties"]["id"])


async def _seed_transition(
    conn: asyncpg.Connection,
    graph_name: str,
    *,
    edge_id: str,
    from_state: str,
    to_state: str,
    exit_time: datetime,
) -> None:
    """Two raw rows in the outbox: ``edge_id`` upserted into ``from_state`` shortly before
    ``exit_time``, then into ``to_state`` at ``exit_time`` — a genuine transition
    ``estate_throughput``'s LAG window should count as one exit from ``from_state`` on
    that day. Inserted in this order so ``seq`` (bigserial) reflects it; ``time`` values
    are what the query actually filters and buckets by.
    """
    source = source_for(graph_name)

    async def _row(state: str, moment: datetime) -> None:
        await conn.execute(
            """
            INSERT INTO public.estate_event
                (event_id, graph, type, source, subject, element_kind, label, time,
                 principal, data)
            VALUES ($1, $2, $3, $4, $5, 'edge', 'IN_TRAIN', $6, $7, $8::jsonb)
            """,
            new_ulid(),
            graph_name,
            EventType.EDGE_UPSERTED.value,
            source,
            edge_id,
            moment,
            PRINCIPAL.value,
            json.dumps(
                {
                    "type": "IN_TRAIN",
                    "properties": {"id": edge_id, "sequence": 1, "state": state},
                    "from_id": "wb-synthetic",
                    "to_id": "trn-synthetic",
                }
            ),
        )

    await _row(from_state, exit_time - timedelta(hours=6))
    await _row(to_state, exit_time)


@pytest.fixture
async def estate(settings: Settings):
    """A fresh train every test, with fresh (never-reused) *state names* too — not just
    fresh workbook/train ids. The graph is shared across this whole module (the
    established convention), and this story's whole subject is measured history: two
    tests using the literal string "PROVING" would contaminate each other's throughput
    the moment either seeds an exit. Suffixing every state with this test's own unique
    id makes that structurally impossible, the same way unique LUIDs already avoid it for
    node identity.
    """
    pool = await create_pool(settings)
    try:
        repository = AgeGraphRepository(pool, graph_name=settings.graph_name)
        writer = GraphWriter(repository, event_source=source_for(settings.graph_name))
        suffix = new_ulid()[10:18].lower()
        busy_state = f"PROVING_{suffix}"
        idle_state = f"CLUSTERED_{suffix}"

        site = await _write(writer, "Site", luid=f"s-{suffix}", name=f"RQA {suffix}")
        project = await _write(writer, "Project", luid=f"p-{suffix}", name="Risk Core")
        await _edge(writer, "CONTAINS", site, project)

        async def workbook(name: str) -> str:
            book = await _write(writer, "Workbook", luid=f"{name}-{suffix}", name=name, revision="1")
            await _edge(writer, "CONTAINS", project, book)
            return book

        busy_a = await workbook(f"Busy-A-{suffix}")
        busy_b = await workbook(f"Busy-B-{suffix}")
        idle_only = await workbook(f"Idle-{suffix}")

        train = await _write(
            writer,
            "ReleaseTrain",
            name=f"Train {suffix}",
            planned_start="2027-01-01",
            planned_end="2027-01-31",
            gate_schedule={"G2": {"planned_date": "2027-01-01"}, "G3": {"planned_date": "2027-01-31"}},
        )
        await _edge(writer, "IN_TRAIN", busy_a, train, sequence=1, state=busy_state)
        await _edge(writer, "IN_TRAIN", busy_b, train, sequence=2, state=busy_state)
        await _edge(writer, "IN_TRAIN", idle_only, train, sequence=3, state=idle_state)

        yield {
            "pool": pool,
            "settings": settings,
            "writer": writer,
            "train": train,
            "busy_state": busy_state,
            "idle_state": idle_state,
        }
    finally:
        await pool.close()


REFERENCE = date(2027, 2, 10)


async def _seed_busy_history(estate: dict, *, exits_per_day: int, days: int) -> None:
    async with estate["pool"].acquire() as conn:
        for day_offset in range(days):
            exit_time = datetime.combine(
                REFERENCE - timedelta(days=day_offset), datetime.min.time(), tzinfo=UTC
            )
            for i in range(exits_per_day):
                await _seed_transition(
                    conn,
                    estate["settings"].graph_name,
                    edge_id=f"synthetic-{day_offset}-{i}-{new_ulid()[:8]}",
                    from_state=estate["busy_state"],
                    to_state="PASSED",
                    exit_time=exit_time,
                )


# ------------------------------------------------------------------------ estate_throughput


async def test_a_resequence_style_upsert_with_no_state_change_is_not_counted(estate) -> None:
    """Two upserts of the same edge, same state both times — no transition, no exit."""
    async with estate["pool"].acquire() as conn:
        await _seed_transition(
            conn,
            estate["settings"].graph_name,
            edge_id=f"no-op-edge-{new_ulid()[:8]}",
            from_state=estate["busy_state"],
            to_state=estate["busy_state"],
            exit_time=datetime.combine(REFERENCE, datetime.min.time(), tzinfo=UTC),
        )
    throughput = await estate_throughput(estate["pool"], estate["settings"].graph_name, now=REFERENCE)
    busy = estate["busy_state"]
    assert busy not in throughput or throughput[busy].exits == 0


async def test_throughput_is_measured_from_genuine_transitions(estate) -> None:
    await _seed_busy_history(estate, exits_per_day=2, days=14)
    throughput = await estate_throughput(estate["pool"], estate["settings"].graph_name, now=REFERENCE)
    busy = estate["busy_state"]
    assert throughput[busy].daily_mean == pytest.approx(2.0)
    assert throughput[busy].exits == 28


async def test_throughput_outside_the_trailing_window_is_excluded(estate) -> None:
    async with estate["pool"].acquire() as conn:
        await _seed_transition(
            conn,
            estate["settings"].graph_name,
            edge_id=f"too-old-{new_ulid()[:8]}",
            from_state=estate["busy_state"],
            to_state="PASSED",
            exit_time=datetime.combine(
                REFERENCE - timedelta(days=20), datetime.min.time(), tzinfo=UTC
            ),
        )
    throughput = await estate_throughput(estate["pool"], estate["settings"].graph_name, now=REFERENCE)
    busy = estate["busy_state"]
    assert throughput.get(busy) is None or throughput[busy].exits == 0


# --------------------------------------------------------------------------- project_trains


async def test_a_train_with_no_measured_throughput_reports_insufficient_data(estate) -> None:
    train = await get_train(estate["pool"], estate["settings"].graph_name, estate["train"])
    [projection] = await project_trains(
        estate["pool"], estate["settings"].graph_name, [train], now=REFERENCE
    )
    assert projection.projected_end is None
    assert not projection.flagged
    assert "insufficient" in projection.reason.lower() or "no measured throughput" in projection.reason


async def test_a_train_projects_from_its_bottleneck_state(estate) -> None:
    await _seed_busy_history(estate, exits_per_day=2, days=14)
    train = await get_train(estate["pool"], estate["settings"].graph_name, estate["train"])
    [projection] = await project_trains(
        estate["pool"], estate["settings"].graph_name, [train], now=REFERENCE
    )
    # 2 MUs remaining in the busy state, 2/day measured -> 1 day.
    assert projection.bottleneck_state == estate["busy_state"]
    assert projection.remaining_in_bottleneck == 2
    assert projection.projected_end == (REFERENCE + timedelta(days=1)).isoformat()
    assert estate["idle_state"] in projection.reason  # named as excluded, not silently dropped


async def test_a_train_projected_well_past_its_planned_date_is_flagged(estate) -> None:
    # Planned end is 2027-01-31; a projection landing in February is many working days late.
    await _seed_busy_history(estate, exits_per_day=1, days=14)
    train = await get_train(estate["pool"], estate["settings"].graph_name, estate["train"])
    [projection] = await project_trains(
        estate["pool"], estate["settings"].graph_name, [train], now=REFERENCE
    )
    assert projection.flagged
    assert projection.days_late is not None and projection.days_late > 5


async def test_a_confidence_band_is_reported_alongside_the_point_estimate(estate) -> None:
    await _seed_busy_history(estate, exits_per_day=2, days=7)
    await _seed_busy_history(estate, exits_per_day=4, days=7)  # varied rate -> real stddev
    train = await get_train(estate["pool"], estate["settings"].graph_name, estate["train"])
    [projection] = await project_trains(
        estate["pool"], estate["settings"].graph_name, [train], now=REFERENCE
    )
    assert projection.projected_end is not None
    assert projection.projected_end_early is not None
    assert projection.projected_end_early <= projection.projected_end


# ---------------------------------------------------------------------------- the API


@pytest.fixture
async def http_client(estate):
    from httpx import ASGITransport, AsyncClient

    from astra_graph.main import create_app

    app = create_app()
    app.state.train_planner = type(
        "_FakePlanner", (), {"pool": estate["pool"], "graph_name": estate["settings"].graph_name}
    )()

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://graph-svc") as async_client:
        yield async_client


def _headers() -> dict[str, str]:
    from astra_graph.principal import PRINCIPAL_HEADER
    from astra_graph.roles import ROLES_HEADER

    return {PRINCIPAL_HEADER: PRINCIPAL.value, ROLES_HEADER: "semantic_model_engineer"}


async def test_projections_over_http(estate, http_client) -> None:
    await _seed_busy_history(estate, exits_per_day=2, days=14)
    response = await http_client.get(
        "/v1/trains:projections", params={"now": REFERENCE.isoformat()}, headers=_headers()
    )
    assert response.status_code == 200
    body = response.json()
    found = next(p for p in body["projections"] if p["train_id"] == estate["train"])
    assert found["bottleneck_state"] == estate["busy_state"]
    assert body["trailing_days"] == 14
    assert body["late_threshold_working_days"] == 5
