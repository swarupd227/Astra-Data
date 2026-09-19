"""`TokenBudgetStore` and MU usage attribution against a real PostgreSQL -- story S12.2.2.

Two things. The budget configuration itself: one row per MU, the latest limit wins. And
the real path consumption takes -- a call through the real gateway `_dispatch` writes a
real `gateway_request_log` row carrying the MU, model and token counts, and
`TokenBudgetStore.get_status` sums exactly those rows back.
"""

from __future__ import annotations

import os

import pytest

pytestmark = pytest.mark.integration

asyncpg = pytest.importorskip("asyncpg")

from astra_graph.config import Settings  # noqa: E402
from astra_graph.gateway import (  # noqa: E402
    TRANSPILE_C3,
    PostgresGatewayRequestLogStore,
    RawModelResponse,
    StaticGateway,
)
from astra_graph.graph import create_pool  # noqa: E402
from astra_graph.ids import new_ulid  # noqa: E402
from astra_graph.migrations import run as run_migrations  # noqa: E402
from astra_graph.token_budget import TokenBudgetStore  # noqa: E402

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


async def test_an_mu_with_no_budget_gets_the_documented_default(pool) -> None:
    status = await TokenBudgetStore(pool, graph_name=GRAPH).get_status(f"wb-{new_ulid()}")
    assert status.tokens_limit == 1_000_000
    assert status.tokens_consumed == 0
    assert not status.is_warning and not status.is_exhausted


async def test_setting_a_budget_twice_keeps_one_row_and_the_latest_limit(pool) -> None:
    store = TokenBudgetStore(pool, graph_name=GRAPH)
    mu = f"wb-{new_ulid()}"

    await store.set_budget(mu, 50_000, "first")
    await store.set_budget(mu, 75_000, "raised")

    assert (await store.get_status(mu)).tokens_limit == 75_000
    rows = await pool.fetchval(
        "SELECT count(*) FROM public.token_budget WHERE graph = $1 AND mu_ref = $2", GRAPH, mu
    )
    assert rows == 1


async def test_a_budget_is_scoped_to_its_own_graph(pool) -> None:
    mu = f"wb-{new_ulid()}"
    await TokenBudgetStore(pool, graph_name=GRAPH).set_budget(mu, 10_000)

    other = await TokenBudgetStore(pool, graph_name="some_other_graph").get_status(mu)
    assert other.tokens_limit == 1_000_000


# ------------------------------------------------ real calls -> real log -> real status


class _UsageCaller:
    provider = "anthropic"

    def __init__(self, model: str, tokens_in: int, tokens_out: int, *, fail: bool = False) -> None:
        self.model = model
        self._usage = (tokens_in, tokens_out)
        self._fail = fail

    async def generate(self, request, *, previous_error):
        if self._fail:
            raise RuntimeError("provider is down")
        return RawModelResponse(
            raw={"dax": "1"}, gateway_request_id=f"gwreq_{new_ulid()}", provider=self.provider,
            model=self.model, prompt_hash="sha256:sys", context_hash="sha256:ctx",
            temperature=0.0, tokens_in=self._usage[0], tokens_out=self._usage[1],
            latency_ms=1.0, prompt_template_version="test",
        )


class _Request:
    def as_dict(self):
        return {"output_schema": {}}


async def _call(pool, caller, *, workbook_id):
    gateway = StaticGateway(caller, log_store=PostgresGatewayRequestLogStore(pool, graph_name=GRAPH))
    return await gateway.generate(
        task_class=TRANSPILE_C3, request=_Request(), previous_error=None, workbook_id=workbook_id
    )


async def test_consumption_is_the_sum_of_that_mus_own_attributed_calls(pool) -> None:
    mu, other = f"wb-{new_ulid()}", f"wb-{new_ulid()}"
    store = TokenBudgetStore(pool, graph_name=GRAPH)

    await _call(pool, _UsageCaller("claude-sonnet-5", 100, 50), workbook_id=mu)
    await _call(pool, _UsageCaller("claude-sonnet-5", 30, 20), workbook_id=mu)
    await _call(pool, _UsageCaller("claude-sonnet-5", 9_999, 9_999), workbook_id=other)
    await _call(pool, _UsageCaller("claude-sonnet-5", 7_777, 7_777), workbook_id=None)
    with pytest.raises(RuntimeError):
        await _call(pool, _UsageCaller("claude-sonnet-5", 5_555, 5_555, fail=True), workbook_id=mu)

    status = await store.get_status(mu)
    # Only the two successful calls for this MU: not the other MU's, not the unattributed
    # call's, and not the failed call (which reported no usage at all).
    assert status.tokens_consumed == 200
    assert status.unpriced_tokens == 0
    assert status.cost_usd == pytest.approx((130 * 3.0 + 70 * 15.0) / 1_000_000)
    assert (await store.get_status(other)).tokens_consumed == 19_998


async def test_the_attribution_columns_really_land_in_the_row(pool) -> None:
    mu = f"wb-{new_ulid()}"

    await _call(pool, _UsageCaller("claude-sonnet-5", 11, 22), workbook_id=mu)

    row = await pool.fetchrow(
        "SELECT model, tokens_in, tokens_out, workbook_id FROM public.gateway_request_log "
        "WHERE graph = $1 AND workbook_id = $2", GRAPH, mu,
    )
    assert (row["model"], row["tokens_in"], row["tokens_out"], row["workbook_id"]) == (
        "claude-sonnet-5", 11, 22, mu,
    )


async def test_a_failed_call_is_logged_with_null_tokens(pool) -> None:
    mu = f"wb-{new_ulid()}"
    with pytest.raises(RuntimeError):
        await _call(pool, _UsageCaller("claude-sonnet-5", 1, 1, fail=True), workbook_id=mu)

    row = await pool.fetchrow(
        "SELECT tokens_in, tokens_out FROM public.gateway_request_log "
        "WHERE graph = $1 AND workbook_id = $2", GRAPH, mu,
    )
    assert row["tokens_in"] is None and row["tokens_out"] is None


async def test_a_model_with_no_price_counts_tokens_but_is_flagged_not_costed(pool) -> None:
    mu = f"wb-{new_ulid()}"
    store = TokenBudgetStore(pool, graph_name=GRAPH)

    await _call(pool, _UsageCaller("claude-sonnet-5", 1_000_000, 0), workbook_id=mu)
    await _call(pool, _UsageCaller("mystery-model", 10, 10), workbook_id=mu)

    status = await store.get_status(mu)
    assert status.tokens_consumed == 1_000_020
    assert status.unpriced_tokens == 20
    assert status.cost_usd == pytest.approx(3.0)  # only the priced model's own cost


async def test_the_budget_flags_follow_real_consumption(pool) -> None:
    mu = f"wb-{new_ulid()}"
    store = TokenBudgetStore(pool, graph_name=GRAPH)
    await _call(pool, _UsageCaller("claude-sonnet-5", 150, 50), workbook_id=mu)  # 200 used

    await store.set_budget(mu, 1_000)
    assert (await store.get_status(mu)).percent_used == 20.0
    await store.set_budget(mu, 250)  # 200/250 = 80%
    warning = await store.get_status(mu)
    assert warning.is_warning and not warning.is_exhausted
    await store.set_budget(mu, 200)  # 200/200 = 100%
    exhausted = await store.get_status(mu)
    assert exhausted.is_exhausted and not exhausted.is_warning
