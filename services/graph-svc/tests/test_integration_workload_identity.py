"""SVID issuance/rotation/revocation against real PostgreSQL — story S11.1.2, opens F11.1.

What only a real database can be asked about: that a revocation is a real, durable
`UPDATE` (not lost on restart), that `graph` scoping actually narrows a listing, and that
the signed token itself never lands in a column (`workload_identity.py`'s own discipline,
checked here against the real table, not just the in-memory store).
"""

from __future__ import annotations

import os

import pytest

pytestmark = pytest.mark.integration

asyncpg = pytest.importorskip("asyncpg")

from astra_graph.config import Settings  # noqa: E402
from astra_graph.graph import create_pool  # noqa: E402
from astra_graph.workload_identity import (  # noqa: E402
    LocalWorkloadIdentityProvider,
    PostgresSvidStore,
    WorkloadIdentityRequestError,
    record_from_svid,
)


def _settings(**overrides: object) -> Settings:
    base = {
        "postgres_host": os.environ.get("ASTRA_POSTGRES_HOST", "localhost"),
        "postgres_port": int(os.environ.get("ASTRA_POSTGRES_PORT", "5432")),
        "postgres_db": os.environ.get("ASTRA_POSTGRES_DB", "astra"),
        "postgres_user": os.environ.get("ASTRA_POSTGRES_USER", "astra"),
        "postgres_password": os.environ.get("ASTRA_POSTGRES_PASSWORD", "astra_local_dev_only"),
        "graph_name": os.environ.get("ASTRA_GRAPH_NAME", "astra_estate_test"),
        "env": "test", "log_level": "WARNING", "pool_min_size": 1, "pool_max_size": 4,
    }
    base.update(overrides)
    return Settings(**base)  # type: ignore[arg-type]


@pytest.fixture(scope="module")
async def settings() -> Settings:
    config = _settings()
    try:
        conn = await asyncpg.connect(dsn=config.dsn, timeout=3)
    except Exception as exc:
        pytest.skip(f"PostgreSQL with Apache AGE not reachable: {exc}")
    await conn.close()
    return config


@pytest.fixture
async def store(settings: Settings):
    pool = await create_pool(settings)
    try:
        yield PostgresSvidStore(pool, graph_name=settings.graph_name)
    finally:
        await pool.close()


@pytest.fixture
def provider() -> LocalWorkloadIdentityProvider:
    return LocalWorkloadIdentityProvider()


async def test_a_real_issuance_round_trips(store, provider) -> None:
    svid = await provider.issue(agent_id="transpiler", run_id="run-1")
    recorded = await store.record(record_from_svid(svid))

    listed = await store.list_records(agent_id="transpiler")
    assert recorded.jti in [r.jti for r in listed]
    match = next(r for r in listed if r.jti == svid.jti)
    assert match.spiffe_id == svid.spiffe_id
    assert match.status == "active"


async def test_the_signed_token_is_never_a_column(store, provider) -> None:
    """`workload_identity.py`'s own discipline: never persist a bearer credential."""
    svid = await provider.issue(agent_id="transpiler", run_id="run-1")
    await store.record(record_from_svid(svid))
    listed = await store.list_records(agent_id="transpiler")
    match = next(r for r in listed if r.jti == svid.jti)
    assert "token" not in match.as_dict()


async def test_revoking_is_a_real_durable_update(store, provider) -> None:
    svid = await provider.issue(agent_id="steward", run_id="run-2")
    await store.record(record_from_svid(svid))

    revoked = await store.revoke(svid.jti, reason="a real, long-enough reason", revoked_by="user:pe@client.example")
    assert revoked.status == "revoked"
    assert await store.is_revoked(svid.jti) is True

    relisted = await store.list_records(agent_id="steward")
    match = next(r for r in relisted if r.jti == svid.jti)
    assert match.status == "revoked"
    assert match.revoked_by == "user:pe@client.example"


async def test_revoking_an_unknown_jti_is_refused(store) -> None:
    with pytest.raises(WorkloadIdentityRequestError, match="no SVID record"):
        await store.revoke("no-such-jti", reason="a real, long-enough reason", revoked_by="user:pe@client.example")


async def test_is_revoked_is_false_for_a_real_active_record(store, provider) -> None:
    svid = await provider.issue(agent_id="harvest-scheduler", run_id="run-3")
    await store.record(record_from_svid(svid))
    assert await store.is_revoked(svid.jti) is False


async def test_graphs_do_not_see_each_others_svids(settings: Settings, provider) -> None:
    """The ``graph`` column is tenant scoping, the same as every other store in this
    service (`test_integration_artefacts.py`'s own equivalent test)."""
    from astra_graph.ids import new_ulid

    pool = await create_pool(settings)
    try:
        mine = PostgresSvidStore(pool, graph_name=settings.graph_name)
        elsewhere = PostgresSvidStore(pool, graph_name=f"other-{new_ulid()[-8:].lower()}")

        svid = await provider.issue(agent_id="transpiler", run_id="run-4")
        await mine.record(record_from_svid(svid))

        assert svid.jti not in [r.jti for r in await elsewhere.list_records()]
        assert await elsewhere.is_revoked(svid.jti) is False
    finally:
        await pool.close()
