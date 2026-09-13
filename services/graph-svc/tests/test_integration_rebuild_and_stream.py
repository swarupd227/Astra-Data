"""S10.1.2: rebuild-from-empty with progress, live server-sent events, and the explain
registry — against real PostgreSQL + Apache AGE.

The rebuild test exercises the identical `replay`/`compare` machinery `test_integration_
events.py`'s own replay tests already prove, but through the real HTTP route and its
in-memory progress tracker, since that plumbing (not the replay engine itself) is what
this story adds. The stream test proves the AC's own "≤ 2 s from event" budget with a
real timing assertion, not a description of one.
"""

from __future__ import annotations

import asyncio
import json
import os
import time

import pytest

pytestmark = pytest.mark.integration

asyncpg = pytest.importorskip("asyncpg")
httpx = pytest.importorskip("httpx")

from astra_graph.config import Settings  # noqa: E402
from astra_graph.events import source_for  # noqa: E402
from astra_graph.graph import AgeGraphRepository, create_pool  # noqa: E402
from astra_graph.graph.queries import accessor  # noqa: E402
from astra_graph.ids import new_ulid  # noqa: E402
from astra_graph.migrations import run as run_migrations  # noqa: E402
from astra_graph.ontology import EDGE_LABELS, NODE_LABELS  # noqa: E402
from astra_graph.principal import PRINCIPAL_HEADER, Principal  # noqa: E402
from astra_graph.roles import ROLES_HEADER  # noqa: E402
from astra_graph.writes import GraphWriter  # noqa: E402

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


async def _create_graph(conn, graph: str) -> None:
    await conn.execute("LOAD 'age'")
    await conn.execute('SET search_path = ag_catalog, "$user", public')
    await conn.execute("SELECT ag_catalog.create_graph($1)", graph)
    for label in sorted(NODE_LABELS):
        await conn.execute("SELECT ag_catalog.create_vlabel($1, $2)", graph, label)
        await conn.execute(f'CREATE INDEX ON {graph}."{label}" USING BTREE ({accessor("id")})')
    for label in sorted(EDGE_LABELS):
        await conn.execute("SELECT ag_catalog.create_elabel($1, $2)", graph, label)
        await conn.execute(f'CREATE INDEX ON {graph}."{label}" USING BTREE ({accessor("id")})')


async def _drop_graph(conn, graph: str) -> None:
    await conn.execute("LOAD 'age'")
    exists = await conn.fetchval(
        "SELECT EXISTS (SELECT 1 FROM ag_catalog.ag_graph WHERE name = $1)", graph
    )
    if not exists:
        return
    await conn.execute("DELETE FROM public.estate_edge_index WHERE graph = $1", graph)
    await conn.execute("DELETE FROM public.estate_element_index WHERE graph = $1", graph)
    await conn.execute("SELECT ag_catalog.drop_graph($1, true)", graph)


@pytest.fixture
async def settings():
    config = _settings(f"astra_rebuild_{new_ulid()[-12:].lower()}")
    try:
        conn = await asyncpg.connect(dsn=config.dsn, timeout=3)
    except Exception:
        pytest.skip("PostgreSQL with Apache AGE not reachable")
        return
    try:
        await run_migrations(conn)
        await _create_graph(conn, config.graph_name)
    finally:
        await conn.close()

    yield config

    # One connection per drop, not two drops sharing one -- Apache AGE's own per-session
    # label cache goes stale after the first `drop_graph`, and a second on that same
    # connection fails with "label (relation) cache corrupted" (the identical, disclosed
    # AGE quirk `test_integration_events.py`'s own teardown already works around).
    conn = await asyncpg.connect(dsn=config.dsn)
    try:
        await _drop_graph(conn, config.graph_name)
    finally:
        await conn.close()

    # A rebuild names its own scratch graph deterministically; a test that failed
    # mid-run could leave one behind.
    conn = await asyncpg.connect(dsn=config.dsn)
    try:
        await _drop_graph(conn, f"{config.graph_name}_rebuild")
    finally:
        await conn.close()


@pytest.fixture
async def pool(settings):
    p = await create_pool(settings)
    try:
        yield p
    finally:
        await p.close()


@pytest.fixture
def repository(pool, settings):
    return AgeGraphRepository(pool, graph_name=settings.graph_name)


@pytest.fixture
def writer(repository, settings) -> GraphWriter:
    return GraphWriter(repository, event_source=source_for(settings.graph_name))


@pytest.fixture
async def http_client(settings, pool, repository):
    from astra_graph.api.routes_rebuild import RebuildStatus
    from astra_graph.main import create_app

    app = create_app()
    app.state.repository = repository
    app.state.pool = pool
    app.state.rebuild_status = RebuildStatus()

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://graph-svc") as async_client:
        yield async_client


def _headers(*, roles: str = "platform_engineer") -> dict[str, str]:
    return {PRINCIPAL_HEADER: PRINCIPAL.value, ROLES_HEADER: roles}


# --------------------------------------------------------------------------- rebuild


async def test_a_rebuild_from_empty_reproduces_the_live_graph(
    writer, settings, http_client
) -> None:
    await seed_estate(writer, suffix=f"-{new_ulid()}")

    started = await http_client.post("/v1/graph:rebuild", headers=_headers())
    assert started.status_code == 202
    assert started.json()["events_total"] > 0

    status_body: dict = {}
    for _ in range(100):
        response = await http_client.get("/v1/graph:rebuild/status", headers=_headers(roles="programme_manager"))
        status_body = response.json()
        if not status_body["running"]:
            break
        await asyncio.sleep(0.1)

    assert not status_body["running"], "the rebuild never finished"
    assert status_body["last_error"] is None
    assert status_body["events_applied"] == status_body["events_total"]
    assert status_body["last_result"]["identical"] is True, status_body["last_result"]["summary"]

    # The scratch graph is torn down, not left behind, once the rebuild is done.
    conn = await asyncpg.connect(dsn=settings.dsn)
    try:
        exists = await conn.fetchval(
            "SELECT EXISTS (SELECT 1 FROM ag_catalog.ag_graph WHERE name = $1)",
            f"{settings.graph_name}_rebuild",
        )
    finally:
        await conn.close()
    assert not exists


async def test_a_rebuild_refuses_a_non_platform_engineer(http_client) -> None:
    response = await http_client.post("/v1/graph:rebuild", headers=_headers(roles="programme_manager"))
    assert response.status_code == 403


async def test_a_second_rebuild_is_refused_while_one_is_running(writer, http_client) -> None:
    await seed_estate(writer, suffix=f"-{new_ulid()}")
    first = await http_client.post("/v1/graph:rebuild", headers=_headers())
    assert first.status_code == 202

    second = await http_client.post("/v1/graph:rebuild", headers=_headers())
    assert second.status_code == 400

    # Drain the first run so the fixture's own teardown does not race it.
    for _ in range(100):
        status_body = (await http_client.get(
            "/v1/graph:rebuild/status", headers=_headers(roles="programme_manager")
        )).json()
        if not status_body["running"]:
            break
        await asyncio.sleep(0.1)


# ----------------------------------------------------------------------------- stream
#
# The route's own `StreamingResponse` never terminates on its own (a live feed has no
# natural end) -- consuming it through a full HTTP round trip needs httpx to read an
# unbounded stream through `ASGITransport` and stop early, a combination that hung
# indefinitely in practice (confirmed: response headers never even completed) rather
# than honouring `asyncio.wait_for`'s own timeout. These tests instead call `_stream`,
# the real async generator `events_stream` (`routes_events_stream.py`) hands to
# `StreamingResponse`, directly -- the identical code the HTTP route runs, against the
# same real repository, without the transport layer that would not cooperate with a
# bounded test.


class _NeverDisconnects:
    """`_stream` only ever calls `request.is_disconnected()` -- a real `Request` is not
    needed to exercise it directly, only something answering that one question."""

    async def is_disconnected(self) -> bool:
        return False


async def test_a_new_event_reaches_the_stream_within_the_budget(writer, repository) -> None:
    """The AC's own "p95 screen update within 2 seconds of the event" — a real, timed
    single-sample proof, the same "measured, not just described" posture ADR 0010's own
    latency figures already set for the Estate Explorer's read budget."""
    from astra_graph.api.routes_events_stream import _stream

    after, _at = await repository.current_version()
    gen = _stream(_NeverDisconnects(), repository, after, None)
    try:
        await asyncio.sleep(0.05)
        started = time.monotonic()
        await seed_estate(writer, suffix=f"-{new_ulid()}")

        frame = await asyncio.wait_for(gen.__anext__(), timeout=2.0)
        elapsed = time.monotonic() - started
    finally:
        await gen.aclose()

    assert frame.startswith("event: estate.node.upserted")
    data = frame.split("data: ", 1)[1].strip()
    payload = json.loads(data)
    assert payload["type"] == "estate.node.upserted"
    assert elapsed < 2.0, f"took {elapsed:.3f}s, over the 2s budget"


async def test_the_stream_filters_by_subject(writer, repository) -> None:
    from astra_graph.api.routes_events_stream import _stream

    seeded = await seed_estate(writer, suffix=f"-{new_ulid()}")
    workbook_id = seeded["workbook"]

    gen = _stream(_NeverDisconnects(), repository, 0, workbook_id)
    try:
        frame = await asyncio.wait_for(gen.__anext__(), timeout=2.0)
    finally:
        await gen.aclose()

    data = frame.split("data: ", 1)[1].strip()
    assert json.loads(data)["subject"] == workbook_id


async def test_events_stream_route_builds_a_real_sse_response(repository) -> None:
    """The route function itself (not just `_stream`) -- media type and the header that
    defeats nginx's own default response buffering (see the route's own docstring)."""
    from astra_graph.api.routes_events_stream import events_stream

    response = await events_stream(
        request=_NeverDisconnects(), repository=repository, after=None, subject=None  # type: ignore[arg-type]
    )
    assert response.media_type == "text/event-stream"
    assert response.headers["X-Accel-Buffering"] == "no"


# ---------------------------------------------------------------------------- explain


async def test_explain_returns_a_real_registered_entry(http_client) -> None:
    response = await http_client.get("/v1/explain/estate.total")
    assert response.status_code == 200
    body = response.json()
    assert body["metric_key"] == "estate.total"
    assert "self.rows" in body["text"]
    assert body["source"].startswith("astra_graph/estate.py")


async def test_explain_404s_for_an_unregistered_key(http_client) -> None:
    response = await http_client.get("/v1/explain/not-a-real-metric")
    assert response.status_code == 404
