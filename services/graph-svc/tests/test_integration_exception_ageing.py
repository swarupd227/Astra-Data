"""Exception ageing and the Mender close rate, against real PostgreSQL + Apache AGE --
story S8.3.2, continuing F8.3/E8.

What only the real stack can answer: that `exception_ageing.exception_ageing` really
reads every live `ExceptionCase` via `hydrate` and hands real
class/state/closed_by/created_at values into `aggregate_ageing` -- the real close-rate
distinction (`closed_by IS NULL` for a Mender-only close versus a real human-recorded
`closed_by`) and the real `VISUAL_REDESIGN` exclusion, both proven against real written
nodes, not fixtures built in memory; and that the new `GET /v1/exceptions:ageing` route
is really live, gated `ArtizentDep` the same broad way every other read-only Programme
Board pane already is.

**Age-band bucketing itself is not re-proven here.** `created_at` is a server-owned
property (`writes._SERVER_OWNED`) no caller, including a test, can backdate through the
normal write path, and no precedent in this codebase backdates a node's own property
directly (`test_integration_g2_reminders.py`'s own backdating helper moves an *event's*
own timestamp, a different mechanism this story's own bucketing never reads). Every case
this file writes is real and freshly created, so every one lands in the real "under 1
day" band -- the pure band-boundary arithmetic itself is already exhaustively covered in
`test_exception_ageing.py`.
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
from astra_graph.exception_ageing import exception_ageing  # noqa: E402
from astra_graph.graph import AgeGraphRepository, create_pool  # noqa: E402
from astra_graph.graph.queries import accessor  # noqa: E402
from astra_graph.ids import new_ulid  # noqa: E402
from astra_graph.migrations import run as run_migrations  # noqa: E402
from astra_graph.ontology import EDGE_LABELS, NODE_LABELS  # noqa: E402
from astra_graph.principal import Principal  # noqa: E402
from astra_graph.writes import GraphWriter, NodeWrite  # noqa: E402

PRINCIPAL = Principal("agent:harvester", run_id="run-harvester")
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


@pytest.fixture
def settings() -> Settings:
    """A single raw `asyncpg.connect()`, not a `Pool` -- the identical convention
    `test_integration_exception_desk.py`'s own `settings` fixture already established.
    A `Pool` opened and closed inside `_run_off_loop`'s own throwaway thread/loop proved
    genuinely unstable here (an intermittent `RuntimeError: Event loop is closed` from a
    `Pool.close()`-scheduled callback still pending when that loop tears down, and a
    correspondingly intermittent hang) -- a plain connection has no such background
    callback to outlive its own loop."""
    config = _settings(f"astra_exageing_{new_ulid()[10:22].lower()}")

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
            ):
                await conn.execute(f"DELETE FROM {table} WHERE graph = $1", config.graph_name)
            await conn.execute("SELECT ag_catalog.drop_graph($1, true)", config.graph_name)
        finally:
            await conn.close()

    if not _run_off_loop(setup):
        pytest.skip("PostgreSQL with Apache AGE not reachable")

    yield config
    _run_off_loop(teardown)


async def _write(writer: GraphWriter, label: str, **properties: Any) -> str:
    created = await writer.write_nodes([NodeWrite(type=label, properties=properties)], principal=PRINCIPAL)
    return str(created[0]["properties"]["id"])


async def _case(writer: GraphWriter, *, failure_class: str, state: str, **extra: Any) -> str:
    return await _write(
        writer, "ExceptionCase", mu_ref="01ARZ3NDEKTSV4RRFFQ69G5FAV",
        **{"class": failure_class, "state": state, **extra},
    )


@pytest.fixture
async def estate(settings: Settings):
    """A plain async fixture, not `_run_off_loop` -- `_run_off_loop` exists only to give
    the sync `settings` fixture a loop to create/drop the schema on before pytest-asyncio
    ever starts one of its own; a pool created on a *second*, throwaway loop and then
    awaited from the test's own real loop fails with "Event loop is closed" the moment
    that throwaway loop's `asyncio.run()` call returns and tears it down -- confirmed by
    exactly that failure on a first draft of this fixture."""
    pool = await create_pool(settings)
    try:
        repository = AgeGraphRepository(pool, graph_name=settings.graph_name)
        writer = GraphWriter(repository)
        yield {"pool": pool, "settings": settings, "writer": writer}
    finally:
        await pool.close()


async def _read(estate: dict[str, Any]) -> dict[str, Any]:
    return await exception_ageing(estate["pool"], estate["settings"].graph_name)


# ------------------------------------------------------------------ open by class/age band


async def test_open_cases_are_really_grouped_by_class_and_age_band(estate) -> None:
    await _case(estate["writer"], failure_class="AGGREGATION", state="OPEN")
    await _case(estate["writer"], failure_class="AGGREGATION", state="OPEN")
    await _case(estate["writer"], failure_class="KEY_MISSING", state="BLOCKED")
    await _case(estate["writer"], failure_class="TYPE_COERCION", state="CLOSED", closed_by=ENGINEER.value)

    result = await _read(estate)
    entries = {(e["class"], e["age_band"]): e["count"] for e in result["open_by_class_and_age_band"]}
    assert entries == {("AGGREGATION", "under_1d"): 2, ("KEY_MISSING", "under_1d"): 1}
    assert result["total_open"] == 3


# --------------------------------------------------------------------- Mender close rate


async def test_a_real_mender_close_leaves_closed_by_null(estate) -> None:
    await _write(
        estate["writer"], "ExceptionCase", mu_ref="01ARZ3NDEKTSV4RRFFQ69G5FAV",
        **{"class": "AGGREGATION", "state": "CLOSED", "passes_consumed": 1},
    )
    result = (await _read(estate))["mender_close_rate"]
    assert result["mender_closed"] == 1
    assert result["total_failures"] == 1
    assert result["rate"] == 1.0
    assert result["meets_target"] is True


async def test_a_real_exception_desk_decision_sets_closed_by_and_is_not_counted(estate) -> None:
    await _write(
        estate["writer"], "ExceptionCase", mu_ref="01ARZ3NDEKTSV4RRFFQ69G5FAV",
        **{"class": "AGGREGATION", "state": "CLOSED", "decision": "PATCHED",
           "closed_by": ENGINEER.value, "closed_at": "2027-06-01T00:00:00.000Z"},
    )
    result = (await _read(estate))["mender_close_rate"]
    assert result["mender_closed"] == 0
    assert result["total_failures"] == 1
    assert result["rate"] == 0.0


async def test_a_real_visual_redesign_case_is_excluded_from_both_sides_of_the_ratio(estate) -> None:
    await _write(
        estate["writer"], "ExceptionCase", mu_ref="01ARZ3NDEKTSV4RRFFQ69G5FAV",
        **{"class": "VISUAL_REDESIGN", "state": "CLOSED",
           "closed_by": ENGINEER.value, "closed_at": "2027-06-01T00:00:00.000Z", "desktop_commit_hash": "a1b2c3d"},
    )
    await _write(
        estate["writer"], "ExceptionCase", mu_ref="01ARZ3NDEKTSV4RRFFQ69G5FAV",
        **{"class": "KEY_MISSING", "state": "CLOSED", "passes_consumed": 1},
    )
    result = (await _read(estate))["mender_close_rate"]
    assert result["total_failures"] == 1
    assert result["mender_closed"] == 1
    assert result["rate"] == 1.0


async def test_rate_is_honestly_none_with_no_real_failures_at_all(estate) -> None:
    result = (await _read(estate))["mender_close_rate"]
    assert result["total_failures"] == 0
    assert result["rate"] is None
    assert result["meets_target"] is None


# ---------------------------------------------------------------------------- the API


@pytest.fixture
async def http_client(estate):
    from httpx import ASGITransport, AsyncClient

    from astra_graph.compositor import Compositor
    from astra_graph.main import create_app

    app = create_app()
    app.state.compositor = Compositor(estate["pool"], graph_name=estate["settings"].graph_name, writer=estate["writer"])
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        yield client


def _headers(principal: Principal, *roles: str) -> dict[str, str]:
    return {"X-Astra-Principal": principal.value, "X-Astra-Roles": ",".join(roles)}


async def test_ageing_route_is_open_to_any_artizent_role(http_client, estate) -> None:
    await _write(
        estate["writer"], "ExceptionCase", mu_ref="01ARZ3NDEKTSV4RRFFQ69G5FAV",
        **{"class": "AGGREGATION", "state": "OPEN"},
    )
    response = await http_client.get(
        "/v1/exceptions:ageing", headers=_headers(ENGINEER, "migration_engineer"),
    )
    assert response.status_code == 200
    body = response.json()
    assert body["total_open"] == 1
    assert body["mender_close_rate"]["target"] == 0.70


async def test_ageing_route_refuses_a_client_role(http_client) -> None:
    response = await http_client.get(
        "/v1/exceptions:ageing",
        headers=_headers(Principal("user:owner@client.example"), "client_data_owner"),
    )
    assert response.status_code == 403
