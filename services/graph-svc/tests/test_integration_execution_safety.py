"""`PostgresExecutionSafetyPolicyStore`, against real PostgreSQL -- story S11.2.1.

What only the real stack can answer: that a policy really persists across a fresh store
instance, that a save really produces a new, higher version rather than overwriting the
row a previous one wrote, and that two tenants' (graphs') own policies are really kept
apart -- the identical shape test_integration_mender.py already proves for
`PostgresMenderConfigStore`.
"""

from __future__ import annotations

import os

import pytest

pytestmark = pytest.mark.integration

asyncpg = pytest.importorskip("asyncpg")

from astra_graph.config import Settings  # noqa: E402
from astra_graph.execution_safety import (  # noqa: E402
    ExecutionSafetyPolicy,
    PostgresExecutionSafetyPolicyStore,
)
from astra_graph.ids import new_ulid  # noqa: E402


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
        scheduler_enabled=False,
    )


@pytest.fixture
async def pool():
    from astra_graph.graph import create_pool

    settings = _settings("astra_estate_test")
    pool = await create_pool(settings)
    try:
        yield pool
    finally:
        await pool.close()


async def test_a_deployment_with_no_saved_policy_gets_the_honest_default(pool) -> None:
    store = PostgresExecutionSafetyPolicyStore(pool, graph_name=f"execsafe_{new_ulid()}")
    assert await store.latest() == ExecutionSafetyPolicy()


async def test_a_saved_policy_really_persists_and_versions(pool) -> None:
    graph = f"execsafe_{new_ulid()}"
    store = PostgresExecutionSafetyPolicyStore(pool, graph_name=graph)

    first = await store.save(
        ExecutionSafetyPolicy(production_workspaces=frozenset({"prod"})),
        updated_by="user:pe@artizent.example",
    )
    assert first.version == 1
    assert first.production_workspaces == frozenset({"prod"})

    reread = await PostgresExecutionSafetyPolicyStore(pool, graph_name=graph).latest()
    assert reread == first

    second = await store.save(
        ExecutionSafetyPolicy(production_workspaces=frozenset({"prod", "prod-eu"})),
        updated_by="user:pe@artizent.example",
    )
    assert second.version == 2
    assert await store.latest() == second


async def test_two_graphs_own_policies_are_kept_apart(pool) -> None:
    graph_a = f"execsafe_a_{new_ulid()}"
    graph_b = f"execsafe_b_{new_ulid()}"
    store_a = PostgresExecutionSafetyPolicyStore(pool, graph_name=graph_a)
    store_b = PostgresExecutionSafetyPolicyStore(pool, graph_name=graph_b)

    await store_a.save(
        ExecutionSafetyPolicy(production_workspaces=frozenset({"prod-a"})),
        updated_by="user:pe@artizent.example",
    )

    assert (await store_a.latest()).production_workspaces == frozenset({"prod-a"})
    assert await store_b.latest() == ExecutionSafetyPolicy()
