"""The 80% alert and 100% hard stop against a real PostgreSQL -- story S12.2.2.

A real `ModelGateway` writes real `gateway_request_log` rows, the real `BudgetMonitor`
sums them and raises real alerts into the real event outbox, and the hard stop refuses
the next call. Nothing in the budget path is faked; only the provider is a stub, so token
counts are exact.
"""

from __future__ import annotations

import os

import pytest

pytestmark = pytest.mark.integration

asyncpg = pytest.importorskip("asyncpg")

from astra_graph.config import Settings  # noqa: E402
from astra_graph.events import EventType, source_for  # noqa: E402
from astra_graph.gateway import (  # noqa: E402
    TRANSPILE_C3,
    GatewayBudgetError,
    ModelGateway,
    PostgresGatewayRequestLogStore,
    RawModelResponse,
)
from astra_graph.graph import AgeGraphRepository, create_pool  # noqa: E402
from astra_graph.ids import new_ulid  # noqa: E402
from astra_graph.migrations import run as run_migrations  # noqa: E402
from astra_graph.token_budget import BudgetMonitor, TokenBudgetStore  # noqa: E402
from astra_graph.writes import GraphWriter  # noqa: E402

from .test_gateway import _InMemoryPolicyStore  # noqa: E402

GRAPH = "astra_estate_test"


def _settings() -> Settings:
    return Settings(
        postgres_host=os.environ.get("ASTRA_POSTGRES_HOST", "localhost"),
        postgres_port=int(os.environ.get("ASTRA_POSTGRES_PORT", "5432")),
        postgres_db=os.environ.get("ASTRA_POSTGRES_DB", "astra"),
        postgres_user=os.environ.get("ASTRA_POSTGRES_USER", "astra"),
        postgres_password=os.environ.get("ASTRA_POSTGRES_PASSWORD", "astra_local_dev_only"),
        graph_name=GRAPH,
        env="test",
        log_level="WARNING",
        pool_min_size=1,
        pool_max_size=4,
    )


@pytest.fixture
async def pool():
    config = _settings()
    try:
        conn = await asyncpg.connect(dsn=config.dsn, timeout=3)
    except Exception as exc:
        pytest.skip(f"PostgreSQL not reachable: {exc}")
    try:
        await run_migrations(conn)
    finally:
        await conn.close()
    pool = await create_pool(config)
    try:
        yield pool
    finally:
        await pool.close()


class _UsageCaller:
    """A provider that reports a scripted token count per call, and counts its calls."""

    provider = "anthropic"
    model = "claude-sonnet-5"

    def __init__(self, *usages: int) -> None:
        self._usages = list(usages)
        self.calls = 0

    async def generate(self, request, *, previous_error):
        used = self._usages[self.calls]
        self.calls += 1
        return RawModelResponse(
            raw={"dax": "1"}, gateway_request_id=f"gwreq_{new_ulid()}", provider=self.provider,
            model=self.model, prompt_hash="sha256:sys", context_hash="sha256:ctx",
            temperature=0.0, tokens_in=used, tokens_out=0, latency_ms=1.0,
            prompt_template_version="test",
        )


class _Request:
    def as_dict(self):
        return {"output_schema": {}}


def _gateway(pool, caller) -> ModelGateway:
    writer = GraphWriter(AgeGraphRepository(pool, graph_name=GRAPH), event_source=source_for(GRAPH))
    monitor = BudgetMonitor(
        TokenBudgetStore(pool, graph_name=GRAPH), pool=pool, graph_name=GRAPH, writer=writer,
    )
    return ModelGateway(
        providers={"anthropic": caller},
        policy_store=_InMemoryPolicyStore(scores={(TRANSPILE_C3, "anthropic"): 0.9}),
        log_store=PostgresGatewayRequestLogStore(pool, graph_name=GRAPH),
        budget_guard=monitor,
    )


async def _call(gateway, mu: str):
    return await gateway.generate(
        task_class=TRANSPILE_C3, request=_Request(), previous_error=None, workbook_id=mu,
        principal="agent:transpiler",
    )


async def _alerts(pool, mu: str) -> dict[str, list[dict]]:
    rows = await pool.fetch(
        "SELECT type, data, principal FROM public.estate_event "
        "WHERE graph = $1 AND subject = $2 AND type = ANY($3::text[]) ORDER BY seq",
        GRAPH, mu, [EventType.BUDGET_WARNING.value, EventType.BUDGET_EXHAUSTED.value],
    )
    import json

    out: dict[str, list[dict]] = {"warning": [], "exhausted": []}
    for row in rows:
        data = json.loads(row["data"]) if isinstance(row["data"], str) else row["data"]
        key = "warning" if row["type"] == EventType.BUDGET_WARNING.value else "exhausted"
        out[key].append({**data, "_principal": row["principal"]})
    return out


async def test_the_warning_at_80_percent_and_the_stop_at_100_percent(pool) -> None:
    mu = f"wb-{new_ulid()}"
    await TokenBudgetStore(pool, graph_name=GRAPH).set_budget(mu, 100)
    caller = _UsageCaller(50, 30, 30, 999)
    gateway = _gateway(pool, caller)

    await _call(gateway, mu)  # 50 of 100
    assert await _alerts(pool, mu) == {"warning": [], "exhausted": []}

    await _call(gateway, mu)  # 80 of 100 -- exactly 80% raises the soft alert, not the stop
    alerts = await _alerts(pool, mu)
    assert len(alerts["warning"]) == 1 and alerts["exhausted"] == []
    assert alerts["warning"][0]["tokens_consumed"] == 80
    assert alerts["warning"][0]["percent_used"] == 80.0
    assert alerts["warning"][0]["_principal"] == "agent:transpiler"

    await _call(gateway, mu)  # 110 of 100 -- the call that crosses the line is still served
    alerts = await _alerts(pool, mu)
    assert len(alerts["warning"]) == 1, "the 80% alert is raised once, not on every later call"
    assert len(alerts["exhausted"]) == 1
    assert alerts["exhausted"][0]["tokens_consumed"] == 110

    with pytest.raises(GatewayBudgetError):
        await _call(gateway, mu)  # the next one is refused
    assert caller.calls == 3, "the provider must not be called once the MU is exhausted"

    with pytest.raises(GatewayBudgetError):
        await _call(gateway, mu)
    alerts = await _alerts(pool, mu)
    assert (len(alerts["warning"]), len(alerts["exhausted"])) == (1, 1)


async def test_a_refused_call_spends_and_logs_nothing(pool) -> None:
    mu = f"wb-{new_ulid()}"
    store = TokenBudgetStore(pool, graph_name=GRAPH)
    await store.set_budget(mu, 10)
    gateway = _gateway(pool, _UsageCaller(10, 999))

    await _call(gateway, mu)
    with pytest.raises(GatewayBudgetError):
        await _call(gateway, mu)

    rows = await pool.fetchval(
        "SELECT count(*) FROM public.gateway_request_log WHERE graph = $1 AND workbook_id = $2",
        GRAPH, mu,
    )
    assert rows == 1
    assert (await store.get_status(mu)).tokens_consumed == 10


async def test_one_mu_being_stopped_does_not_stop_another(pool) -> None:
    stopped, other = f"wb-{new_ulid()}", f"wb-{new_ulid()}"
    await TokenBudgetStore(pool, graph_name=GRAPH).set_budget(stopped, 10)
    caller = _UsageCaller(10, 5)
    gateway = _gateway(pool, caller)

    await _call(gateway, stopped)
    with pytest.raises(GatewayBudgetError):
        await _call(gateway, stopped)

    await _call(gateway, other)  # default budget, unaffected
    assert caller.calls == 2


async def test_raising_the_budget_lifts_the_stop_and_re_arms_the_alert(pool) -> None:
    mu = f"wb-{new_ulid()}"
    store = TokenBudgetStore(pool, graph_name=GRAPH)
    await store.set_budget(mu, 100)
    caller = _UsageCaller(100, 60)
    gateway = _gateway(pool, caller)

    await _call(gateway, mu)  # 100 of 100: warning and exhausted, both for limit 100
    with pytest.raises(GatewayBudgetError):
        await _call(gateway, mu)

    await store.set_budget(mu, 200)  # the operator raises it: served again
    await _call(gateway, mu)  # 160 of 200 = 80%: a new budget alerts again

    alerts = await _alerts(pool, mu)
    assert [a["tokens_limit"] for a in alerts["warning"]] == [100, 200]
    assert [a["tokens_limit"] for a in alerts["exhausted"]] == [100]
