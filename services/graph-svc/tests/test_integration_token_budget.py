"""`TokenBudgetStore` against a real PostgreSQL -- story S12.2.2.

The upsert (`ON CONFLICT (graph, mu_ref)`) and the default-limit path have never run
anywhere else. Consumption is honestly zero until `gateway_request_log` carries a
per-MU attribution (see `token_budget.py`'s own module docstring), so what is proven
here is the budget configuration itself: one row per MU, the latest limit wins.
"""

from __future__ import annotations

import os

import pytest

pytestmark = pytest.mark.integration

asyncpg = pytest.importorskip("asyncpg")

from astra_graph.config import Settings  # noqa: E402
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
