"""Throughput and cost metrics, against real PostgreSQL + Apache AGE -- story S6.2.3.

What only the real stack can answer: that `custodians_live_per_week`/`agent_acceptance_
per_custodian_per_day`/`credits_per_custodian_per_day` really resolve a workbook's own
real Site via the real `CONTAINS` chain, really group real `gateway_request_log`/
`commercial_ledger` rows by that site and by real calendar day/week; that a real gateway
dispatch really persists its own real `query_tag`/`tokens_in`/`tokens_out`/`cost_usd`;
that `throughput_report.generate_report`/`latest_report` really round-trip a real,
versioned row and `render_csv` really renders it; and that the three new HTTP routes are
really live, gated the way the AC's own "as a project manager" and the established
Status Pack precedent both require (Programme Manager generates, any Artizent role
reads, a client role is refused).
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
from astra_graph.gateway import (  # noqa: E402
    PostgresGatewayRequestLogStore,
    RawModelResponse,
    StaticGateway,
    token_cost_usd,
)
from astra_graph.graph import AgeGraphRepository, create_pool  # noqa: E402
from astra_graph.graph.queries import accessor  # noqa: E402
from astra_graph.ids import new_ulid  # noqa: E402
from astra_graph.invoicing import PostgresUnitPriceStore, record_acceptance  # noqa: E402
from astra_graph.migrations import run as run_migrations  # noqa: E402
from astra_graph.ontology import EDGE_LABELS, NODE_LABELS  # noqa: E402
from astra_graph.principal import Principal  # noqa: E402
from astra_graph.scope import DecisionKind, PostgresScopeStore, new_decision  # noqa: E402
from astra_graph.throughput_metrics import (  # noqa: E402
    agent_acceptance_per_custodian_per_day,
    credits_per_custodian_per_day,
    custodians_live_per_week,
)
from astra_graph.throughput_report import generate_report, latest_report, render_csv  # noqa: E402
from astra_graph.writes import EdgeWrite, GraphWriter, NodeWrite  # noqa: E402

PRINCIPAL = Principal("agent:harvester", run_id="run-harvester")
ENGINEER = Principal("user:engineer@artizent.example")
PM = Principal("user:pm@artizent.example")


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
    config = _settings(f"astra_throughput_{new_ulid()[10:22].lower()}")

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
                "public.gateway_request_log", "public.throughput_report",
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
    created = await writer.write_nodes([NodeWrite(type=type_, properties=properties)], principal=PRINCIPAL)
    return str(created[0]["properties"]["id"])


async def _edge(writer: GraphWriter, type_: str, source: str, target: str, **props: Any) -> None:
    await writer.write_edge(EdgeWrite(type=type_, from_id=source, to_id=target, properties=props), principal=PRINCIPAL)


@pytest.fixture
async def estate(settings: Settings):
    """A real `Site -> Project -> Workbook` chain -- the identical shape `release.py`'s
    own fixture already builds -- plus the real stores `throughput_metrics.py`/
    `throughput_report.py` and `invoicing.py` need."""
    pool = await create_pool(settings)
    try:
        repository = AgeGraphRepository(pool, graph_name=settings.graph_name)
        writer = GraphWriter(repository)
        scope_store = PostgresScopeStore(pool, graph_name=settings.graph_name)
        unit_price_store = PostgresUnitPriceStore(pool, graph_name=settings.graph_name)
        log_store = PostgresGatewayRequestLogStore(pool, graph_name=settings.graph_name)

        suffix = new_ulid()[10:18].lower()
        site = await _write(writer, "Site", luid=f"s-{suffix}", name=f"RQA {suffix}")
        project = await _write(writer, "Project", luid=f"p-{suffix}", name="Risk Core")
        await _edge(writer, "CONTAINS", site, project)
        workbook = await _write(writer, "Workbook", luid=f"wb-{suffix}", name="Daily VaR", revision="1")
        await _edge(writer, "CONTAINS", project, workbook)

        yield {
            "pool": pool, "settings": settings, "writer": writer, "scope_store": scope_store,
            "unit_price_store": unit_price_store, "log_store": log_store,
            "site": site, "project": project, "workbook": workbook,
        }
    finally:
        await pool.close()


class _FixedCaller:
    provider = "anthropic"
    model = "claude-sonnet-5"

    def __init__(self, tokens_in: int, tokens_out: int) -> None:
        self._tokens_in = tokens_in
        self._tokens_out = tokens_out

    async def generate(self, request: Any, *, previous_error: str | None) -> RawModelResponse:
        return RawModelResponse(
            raw={"dax": "ok"}, gateway_request_id="gwreq_test", provider=self.provider, model=self.model,
            prompt_hash="hash", temperature=0.0, tokens_in=self._tokens_in, tokens_out=self._tokens_out,
        )


class _DictRequestDouble:
    def as_dict(self) -> dict[str, Any]:
        return {"task": "TRANSLATE_CALC", "output_schema": {}}


async def _real_dispatch(estate: dict[str, Any], *, query_tag: str | None, tokens_in: int, tokens_out: int) -> None:
    gateway = StaticGateway(_FixedCaller(tokens_in, tokens_out), log_store=estate["log_store"])
    await gateway.generate(
        task_class="transpile_c3", request=_DictRequestDouble(), previous_error=None, query_tag=query_tag,
    )


async def _backdate_latest_request(estate: dict[str, Any], *, days_ago: int) -> None:
    async with estate["pool"].acquire() as conn:
        await conn.execute(
            f"""UPDATE public.gateway_request_log SET created_at = created_at - interval '{days_ago} days'
                 WHERE id = (SELECT id FROM public.gateway_request_log WHERE graph = $1 ORDER BY created_at DESC LIMIT 1)""",
            estate["settings"].graph_name,
        )


async def _set_tier(estate: dict[str, Any], tier: str) -> None:
    decision = new_decision(
        workbook_id=estate["workbook"], kind=DecisionKind.RE_TIER,
        reason="Confirmed against the source workbook's own real complexity.",
        decided_by=ENGINEER.value, to_value=tier,
    )
    await estate["scope_store"].decide(decision)


# --------------------------------------------------------------- credits_per_custodian_per_day


async def test_a_real_dispatch_is_grouped_by_its_own_real_site_and_day(estate) -> None:
    await _real_dispatch(estate, query_tag=estate["site"], tokens_in=1000, tokens_out=200)

    rows = await credits_per_custodian_per_day(estate["pool"], estate["settings"].graph_name)
    assert len(rows) == 1
    assert rows[0]["site_id"] == estate["site"]
    assert rows[0]["custodian"].startswith("RQA")
    assert rows[0]["calls"] == 1
    assert rows[0]["tokens_in"] == 1000
    assert rows[0]["tokens_out"] == 200
    assert rows[0]["credits_usd"] == pytest.approx(token_cost_usd("anthropic", 1000, 200))


async def test_an_untagged_dispatch_is_grouped_under_none_honestly(estate) -> None:
    await _real_dispatch(estate, query_tag=None, tokens_in=500, tokens_out=100)

    rows = await credits_per_custodian_per_day(estate["pool"], estate["settings"].graph_name)
    assert len(rows) == 1
    assert rows[0]["site_id"] is None
    assert rows[0]["custodian"] == "(unattributed)"


async def test_two_days_of_dispatches_are_two_real_rows(estate) -> None:
    await _real_dispatch(estate, query_tag=estate["site"], tokens_in=1000, tokens_out=200)
    await _backdate_latest_request(estate, days_ago=2)
    await _real_dispatch(estate, query_tag=estate["site"], tokens_in=500, tokens_out=50)

    rows = await credits_per_custodian_per_day(estate["pool"], estate["settings"].graph_name, days=10)
    assert len(rows) == 2
    assert {row["tokens_in"] for row in rows} == {1000, 500}


# --------------------------------------------------------- agent_acceptance_per_custodian_per_day


async def test_a_real_acceptance_is_attributed_to_its_own_real_site(estate) -> None:
    await _set_tier(estate, "SIMPLE")
    await record_acceptance(
        estate["pool"], estate["settings"].graph_name, estate["writer"], estate["scope_store"],
        estate["unit_price_store"], workbook_id=estate["workbook"], gate_decision_id="gd_1", principal=ENGINEER,
    )

    rows = await agent_acceptance_per_custodian_per_day(estate["pool"], estate["settings"].graph_name)
    assert len(rows) == 1
    assert rows[0]["site_id"] == estate["site"]
    assert rows[0]["accepted"] == 1


async def test_no_acceptances_yet_is_honestly_empty(estate) -> None:
    rows = await agent_acceptance_per_custodian_per_day(estate["pool"], estate["settings"].graph_name)
    assert rows == []


# ------------------------------------------------------------------ custodians_live_per_week


async def test_a_query_tagged_dispatch_makes_its_site_live_that_week(estate) -> None:
    await _real_dispatch(estate, query_tag=estate["site"], tokens_in=10, tokens_out=10)

    rows = await custodians_live_per_week(estate["pool"], estate["settings"].graph_name)
    assert len(rows) == 1
    assert rows[0]["custodians_live"] == 1


async def test_an_acceptance_alone_also_makes_its_site_live_that_week(estate) -> None:
    await _set_tier(estate, "SIMPLE")
    await record_acceptance(
        estate["pool"], estate["settings"].graph_name, estate["writer"], estate["scope_store"],
        estate["unit_price_store"], workbook_id=estate["workbook"], gate_decision_id="gd_1", principal=ENGINEER,
    )

    rows = await custodians_live_per_week(estate["pool"], estate["settings"].graph_name)
    assert len(rows) == 1
    assert rows[0]["custodians_live"] == 1


async def test_no_real_activity_is_honestly_empty(estate) -> None:
    rows = await custodians_live_per_week(estate["pool"], estate["settings"].graph_name)
    assert rows == []


# ---------------------------------------------------------------------------- the report


async def test_generate_report_computes_and_persists_all_three_real_metrics(estate) -> None:
    await _real_dispatch(estate, query_tag=estate["site"], tokens_in=1000, tokens_out=200)
    await _set_tier(estate, "SIMPLE")
    await record_acceptance(
        estate["pool"], estate["settings"].graph_name, estate["writer"], estate["scope_store"],
        estate["unit_price_store"], workbook_id=estate["workbook"], gate_decision_id="gd_1", principal=ENGINEER,
    )

    report = await generate_report(estate["pool"], estate["settings"].graph_name, generated_by=PM.value)

    assert report.generated_by == PM.value
    assert len(report.credits_per_custodian_per_day) == 1
    assert len(report.agent_acceptance_per_custodian_per_day) == 1
    assert len(report.custodians_live_per_week) == 1

    fetched = await latest_report(estate["pool"], estate["settings"].graph_name)
    assert fetched is not None
    assert fetched.id == report.id


async def test_latest_report_is_honestly_none_before_any_generate(estate) -> None:
    assert await latest_report(estate["pool"], estate["settings"].graph_name) is None


async def test_a_regenerate_is_a_new_row_not_an_overwrite(estate) -> None:
    first = await generate_report(estate["pool"], estate["settings"].graph_name, generated_by=PM.value)
    second = await generate_report(estate["pool"], estate["settings"].graph_name, generated_by=PM.value)
    assert first.id != second.id
    assert (await latest_report(estate["pool"], estate["settings"].graph_name)).id == second.id


async def test_render_csv_carries_the_real_numbers(estate) -> None:
    await _real_dispatch(estate, query_tag=estate["site"], tokens_in=1000, tokens_out=200)
    report = await generate_report(estate["pool"], estate["settings"].graph_name, generated_by=PM.value)

    csv_text = render_csv(report)
    assert "Custodians live per week" in csv_text
    assert "Credits (real LLM cost) per custodian per day" in csv_text
    assert "RQA" in csv_text
    assert "1000" in csv_text


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
    # `routes_throughput_report.py` reads `request.app.state.pool` directly (the
    # identical `routes_status_pack.py` convention it copies) and `RepositoryDep` needs
    # a real `app.state.repository` (`deps.get_repository`) -- neither is the
    # Cartographer-reached pool `_estate_graph`-style routes use, so both are set here
    # explicitly, matching what `main.py`'s own real lifespan sets them to.
    app.state.pool = estate["pool"]
    app.state.repository = AgeGraphRepository(estate["pool"], graph_name=estate["settings"].graph_name)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        yield client


def _headers(principal: Principal, *roles: str) -> dict[str, str]:
    return {"X-Astra-Principal": principal.value, "X-Astra-Roles": ",".join(roles)}


async def test_the_programme_manager_can_generate_the_report_over_http(http_client, estate) -> None:
    await _real_dispatch(estate, query_tag=estate["site"], tokens_in=1000, tokens_out=200)

    response = await http_client.post(
        "/v1/throughput-report:generate", headers=_headers(PM, "programme_manager"),
    )
    assert response.status_code == 200
    body = response.json()
    assert len(body["credits_per_custodian_per_day"]) == 1


async def test_a_non_programme_manager_artizent_role_cannot_generate(http_client) -> None:
    response = await http_client.post(
        "/v1/throughput-report:generate", headers=_headers(ENGINEER, "migration_engineer"),
    )
    assert response.status_code == 403


async def test_a_client_role_cannot_read_the_report(http_client) -> None:
    response = await http_client.get(
        "/v1/throughput-report", headers=_headers(Principal("user:x@client.example"), "client_data_owner"),
    )
    assert response.status_code == 403


async def test_reading_before_any_generate_is_a_real_400(http_client) -> None:
    response = await http_client.get("/v1/throughput-report", headers=_headers(ENGINEER, "migration_engineer"))
    assert response.status_code == 400


async def test_the_csv_export_is_a_real_download(http_client, estate) -> None:
    await _real_dispatch(estate, query_tag=estate["site"], tokens_in=1000, tokens_out=200)
    await http_client.post("/v1/throughput-report:generate", headers=_headers(PM, "programme_manager"))

    response = await http_client.get("/v1/throughput-report.csv", headers=_headers(ENGINEER, "migration_engineer"))
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/csv")
    assert "Custodians live per week" in response.text
