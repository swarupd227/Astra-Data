"""mu.accepted invoicing and the commercial ledger, against real PostgreSQL + Apache
AGE -- story S9.1.2, closing F9.1/E9.

What only the real stack can answer: that `record_acceptance` really resolves a
workbook's own real current tier (via `ScopeStore.states()`, the only real read path),
really writes a real row to `public.commercial_ledger`, really emits a real `mu.
accepted` event, is honestly a no-op with no tier set, and never double-invoices a
workbook accepted twice; that `PostgresUnitPriceStore` really falls back to the real
disclosed defaults and really persists a real update; that `accepted_by_tier`/
`programme_acceptance_summary` really aggregate real ledger rows against the real
`PLANNED_BY_TIER` planning assumption; and that the new `GET /v1/programmes:acceptance`
route is really live, gated the same broad way every other read-only Programme Board
route already is.
"""

from __future__ import annotations

import asyncio
import json
import os
import threading
from collections.abc import Callable, Coroutine
from typing import Any

import pytest

pytestmark = pytest.mark.integration

asyncpg = pytest.importorskip("asyncpg")

from astra_graph.config import Settings  # noqa: E402
from astra_graph.events import EventType  # noqa: E402
from astra_graph.graph import AgeGraphRepository, create_pool  # noqa: E402
from astra_graph.graph.queries import accessor  # noqa: E402
from astra_graph.ids import new_ulid  # noqa: E402
from astra_graph.invoicing import (  # noqa: E402
    DEFAULT_UNIT_PRICES,
    PostgresUnitPriceStore,
    accepted_by_tier,
    programme_acceptance_summary,
    record_acceptance,
)
from astra_graph.migrations import run as run_migrations  # noqa: E402
from astra_graph.ontology import EDGE_LABELS, NODE_LABELS  # noqa: E402
from astra_graph.principal import Principal  # noqa: E402
from astra_graph.scope import DecisionKind, PostgresScopeStore, new_decision  # noqa: E402
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
    """A single raw `asyncpg.connect()`, not a `Pool` -- see `test_integration_exception_
    ageing.py`'s own fixture docstring for why (S8.3.2's own real, fixed hang)."""
    config = _settings(f"astra_invoicing_{new_ulid()[10:22].lower()}")

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
                "public.scope_decision", "public.commercial_ledger", "public.unit_price_schedule",
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
async def estate(settings: Settings):
    pool = await create_pool(settings)
    try:
        repository = AgeGraphRepository(pool, graph_name=settings.graph_name)
        writer = GraphWriter(repository)
        scope_store = PostgresScopeStore(pool, graph_name=settings.graph_name)
        unit_price_store = PostgresUnitPriceStore(pool, graph_name=settings.graph_name)

        created = await writer.write_nodes(
            [NodeWrite(type="Workbook", properties={"luid": "wb-invoicing", "name": "Daily VaR", "revision": "1"})],
            principal=PRINCIPAL,
        )
        workbook_id = str(created[0]["properties"]["id"])

        yield {
            "pool": pool, "settings": settings, "writer": writer, "scope_store": scope_store,
            "unit_price_store": unit_price_store, "workbook": workbook_id,
        }
    finally:
        await pool.close()


async def _set_tier(estate: dict[str, Any], tier: str) -> None:
    decision = new_decision(
        workbook_id=estate["workbook"], kind=DecisionKind.RE_TIER,
        reason="Confirmed against the source workbook's own real complexity.",
        decided_by=ENGINEER.value, to_value=tier,
    )
    await estate["scope_store"].decide(decision)


async def _mu_accepted_events(pool: asyncpg.Pool, graph_name: str, workbook_id: str) -> list[dict[str, Any]]:
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT data FROM public.estate_event WHERE graph = $1 AND type = $2 AND subject = $3",
            graph_name, EventType.MU_ACCEPTED.value, workbook_id,
        )
    return [json.loads(row["data"]) if isinstance(row["data"], str) else row["data"] for row in rows]


# ------------------------------------------------------------------------ record_acceptance


async def test_record_acceptance_writes_a_real_ledger_row_and_emits_a_real_event(estate) -> None:
    await _set_tier(estate, "COMPLEX")

    entry = await record_acceptance(
        estate["pool"], estate["settings"].graph_name, estate["writer"], estate["scope_store"],
        estate["unit_price_store"], workbook_id=estate["workbook"], gate_decision_id="gd_1", principal=ENGINEER,
    )

    assert entry is not None
    assert entry.tier == "COMPLEX"
    assert entry.unit_price == DEFAULT_UNIT_PRICES["COMPLEX"]

    events = await _mu_accepted_events(estate["pool"], estate["settings"].graph_name, estate["workbook"])
    assert len(events) == 1
    assert events[0]["tier"] == "COMPLEX"
    assert events[0]["unit_price"] == DEFAULT_UNIT_PRICES["COMPLEX"]
    assert events[0]["gate_decision_id"] == "gd_1"


async def test_record_acceptance_is_honestly_none_with_no_real_tier(estate) -> None:
    entry = await record_acceptance(
        estate["pool"], estate["settings"].graph_name, estate["writer"], estate["scope_store"],
        estate["unit_price_store"], workbook_id=estate["workbook"], gate_decision_id="gd_1", principal=ENGINEER,
    )
    assert entry is None

    events = await _mu_accepted_events(estate["pool"], estate["settings"].graph_name, estate["workbook"])
    assert events == []


async def test_record_acceptance_never_double_invoices_the_same_workbook(estate) -> None:
    await _set_tier(estate, "SIMPLE")

    first = await record_acceptance(
        estate["pool"], estate["settings"].graph_name, estate["writer"], estate["scope_store"],
        estate["unit_price_store"], workbook_id=estate["workbook"], gate_decision_id="gd_1", principal=ENGINEER,
    )
    second = await record_acceptance(
        estate["pool"], estate["settings"].graph_name, estate["writer"], estate["scope_store"],
        estate["unit_price_store"], workbook_id=estate["workbook"], gate_decision_id="gd_2", principal=ENGINEER,
    )

    assert first is not None
    assert second is None  # a real no-op, not a second ledger row or a second event

    events = await _mu_accepted_events(estate["pool"], estate["settings"].graph_name, estate["workbook"])
    assert len(events) == 1


async def test_record_acceptance_uses_a_real_updated_price(estate) -> None:
    await _set_tier(estate, "MODERATE")
    await estate["unit_price_store"].set("MODERATE", 19_500.0, updated_by=ENGINEER.value)

    entry = await record_acceptance(
        estate["pool"], estate["settings"].graph_name, estate["writer"], estate["scope_store"],
        estate["unit_price_store"], workbook_id=estate["workbook"], gate_decision_id="gd_1", principal=ENGINEER,
    )
    assert entry is not None
    assert entry.unit_price == 19_500.0


# --------------------------------------------------------------------- PostgresUnitPriceStore


async def test_unit_price_store_falls_back_to_real_defaults(estate) -> None:
    prices = await estate["unit_price_store"].all()
    assert prices == DEFAULT_UNIT_PRICES


async def test_unit_price_store_set_persists_a_real_price(estate) -> None:
    await estate["unit_price_store"].set("REDESIGN", 55_000.0, updated_by=ENGINEER.value)
    assert await estate["unit_price_store"].get("REDESIGN") == 55_000.0
    # Every other tier stays at its own real default.
    assert await estate["unit_price_store"].get("SIMPLE") == DEFAULT_UNIT_PRICES["SIMPLE"]


async def test_unit_price_store_set_refuses_an_unreal_tier(estate) -> None:
    with pytest.raises(ValueError, match="tier must be one of"):
        await estate["unit_price_store"].set("EXTREME", 1.0, updated_by=ENGINEER.value)


# --------------------------------------------------------------------- programme summary


async def test_accepted_by_tier_groups_real_ledger_rows(estate) -> None:
    await _set_tier(estate, "SIMPLE")
    await record_acceptance(
        estate["pool"], estate["settings"].graph_name, estate["writer"], estate["scope_store"],
        estate["unit_price_store"], workbook_id=estate["workbook"], gate_decision_id="gd_1", principal=ENGINEER,
    )

    counts = await accepted_by_tier(estate["pool"], estate["settings"].graph_name)
    assert counts["SIMPLE"] == 1
    assert counts["COMPLEX"] == 0


async def test_programme_acceptance_summary_combines_accepted_planned_and_price(estate) -> None:
    await _set_tier(estate, "COMPLEX")
    await record_acceptance(
        estate["pool"], estate["settings"].graph_name, estate["writer"], estate["scope_store"],
        estate["unit_price_store"], workbook_id=estate["workbook"], gate_decision_id="gd_1", principal=ENGINEER,
    )

    summary = await programme_acceptance_summary(estate["pool"], estate["settings"].graph_name, estate["unit_price_store"])
    complex_row = next(row for row in summary["by_tier"] if row["tier"] == "COMPLEX")
    assert complex_row["accepted"] == 1
    assert complex_row["planned"] == 20
    assert complex_row["delta"] == 1 - 20
    assert complex_row["unit_price"] == DEFAULT_UNIT_PRICES["COMPLEX"]
    assert complex_row["accepted_value"] == DEFAULT_UNIT_PRICES["COMPLEX"]
    assert summary["total_accepted"] == 1
    assert summary["total_planned"] == 150


async def test_programme_acceptance_summary_is_honest_with_nothing_accepted_yet(estate) -> None:
    summary = await programme_acceptance_summary(estate["pool"], estate["settings"].graph_name, estate["unit_price_store"])
    assert summary["total_accepted"] == 0
    assert all(row["accepted"] == 0 for row in summary["by_tier"])


# ---------------------------------------------------------------------------- the API


@pytest.fixture
async def http_client(estate):
    from httpx import ASGITransport, AsyncClient

    from astra_graph.main import create_app

    class _FakeCartographer:
        def __init__(self, pool: Any, graph_name: str) -> None:
            self.pool = pool
            self.graph_name = graph_name

    app = create_app()
    app.state.cartographer = _FakeCartographer(estate["pool"], estate["settings"].graph_name)
    app.state.unit_price_store = estate["unit_price_store"]
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        yield client


def _headers(principal: Principal, *roles: str) -> dict[str, str]:
    return {"X-Astra-Principal": principal.value, "X-Astra-Roles": ",".join(roles)}


async def test_acceptance_route_is_open_to_an_artizent_role(http_client, estate) -> None:
    await _set_tier(estate, "SIMPLE")
    await record_acceptance(
        estate["pool"], estate["settings"].graph_name, estate["writer"], estate["scope_store"],
        estate["unit_price_store"], workbook_id=estate["workbook"], gate_decision_id="gd_1", principal=ENGINEER,
    )

    response = await http_client.get("/v1/programmes:acceptance", headers=_headers(ENGINEER, "migration_engineer"))
    assert response.status_code == 200
    body = response.json()
    assert body["total_accepted"] == 1


async def test_acceptance_route_refuses_a_client_role(http_client) -> None:
    response = await http_client.get(
        "/v1/programmes:acceptance",
        headers=_headers(Principal("user:x@client.example"), "client_data_owner"),
    )
    assert response.status_code == 403
