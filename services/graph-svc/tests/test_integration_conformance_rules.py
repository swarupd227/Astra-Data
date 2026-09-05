"""The conformance ruleset's own API, against real PostgreSQL — story S4.3.2.

    "Rules are data, editable by the architect in Admin, versioned."

What only the real store can answer: that a save genuinely lands as a new version rather
than an overwrite, that the architect role gate is enforced on the write and not the read,
and that an incomplete or unknown rule set is refused before it ever reaches the database.
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
from astra_graph.conformance_rules import (  # noqa: E402
    RULES,
    PostgresConformanceRulesetStore,
    RuleConfig,
)
from astra_graph.ids import new_ulid  # noqa: E402
from astra_graph.migrations import run as run_migrations  # noqa: E402
from astra_graph.principal import PRINCIPAL_HEADER, Principal  # noqa: E402
from astra_graph.roles import ROLES_HEADER  # noqa: E402

ARCHITECT = Principal("user:architect@artizent.example")
ENGINEER = Principal("user:sme@artizent.example")


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


@pytest.fixture
def settings():
    config = _settings(f"astra_conformance_{new_ulid()[10:22].lower()}")

    async def setup() -> bool:
        try:
            conn = await asyncpg.connect(dsn=config.dsn, timeout=3)
        except Exception:
            return False
        try:
            await run_migrations(conn)
        finally:
            await conn.close()
        return True

    async def teardown() -> None:
        conn = await asyncpg.connect(dsn=config.dsn)
        try:
            await conn.execute(
                "DELETE FROM public.conformance_ruleset WHERE graph = $1", config.graph_name
            )
        finally:
            await conn.close()

    if not _run_off_loop(setup):
        pytest.skip("PostgreSQL with Apache AGE not reachable")

    yield config
    _run_off_loop(teardown)


@pytest.fixture
async def store(settings: Settings):
    from astra_graph.graph import create_pool

    pool = await create_pool(settings)
    try:
        yield PostgresConformanceRulesetStore(pool, graph_name=settings.graph_name)
    finally:
        await pool.close()


# ------------------------------------------------------------------------------ the store


async def test_latest_is_the_in_memory_default_when_nothing_is_saved(store) -> None:
    ruleset = await store.latest()
    assert ruleset.version == 0
    assert {r.rule_id for r in ruleset.rules} == set(RULES)


async def test_saving_creates_a_new_version_rather_than_overwriting(store) -> None:
    first = await store.save(
        [RuleConfig(rule_id) for rule_id in RULES], updated_by=ARCHITECT.value,
    )
    assert first.version == 1
    second = await store.save(
        [RuleConfig(rule_id, enabled=False) for rule_id in RULES], updated_by=ARCHITECT.value,
    )
    assert second.version == 2
    assert await store.latest() == second
    # The first version is still real history, not overwritten — the same "versioned"
    # requirement this story's own acceptance criteria names.
    assert first.version != second.version


# ---------------------------------------------------------------------------------- the API


@pytest.fixture
async def http_client(store):
    from httpx import ASGITransport, AsyncClient

    from astra_graph.main import create_app

    app = create_app()
    app.state.conformance_store = store

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://graph-svc") as async_client:
        yield async_client


def _headers(role: str, principal: Principal) -> dict[str, str]:
    return {PRINCIPAL_HEADER: principal.value, ROLES_HEADER: role}


async def test_get_rules_over_http(http_client) -> None:
    response = await http_client.get("/v1/conformance/rules", headers=_headers("programme_manager", ENGINEER))
    assert response.status_code == 200
    body = response.json()
    assert body["ruleset"]["version"] == 0
    assert "naming_convention" in body["rule_metadata"]


async def test_save_rules_requires_the_architect_role(http_client) -> None:
    response = await http_client.post(
        "/v1/conformance/rules",
        json={"rules": [{"rule_id": r, "enabled": True, "params": {}} for r in RULES]},
        headers=_headers("semantic_model_engineer", ENGINEER),
    )
    assert response.status_code == 403


async def test_save_rules_over_http_creates_a_new_version(http_client) -> None:
    response = await http_client.post(
        "/v1/conformance/rules",
        json={"rules": [{"rule_id": r, "enabled": True, "params": {}} for r in RULES]},
        headers=_headers("migration_architect", ARCHITECT),
    )
    assert response.status_code == 200
    body = response.json()
    assert body["ruleset"]["version"] == 1
    assert body["ruleset"]["updated_by"] == ARCHITECT.value


async def test_save_rules_rejects_a_missing_rule(http_client) -> None:
    incomplete = [r for r in RULES if r != "naming_convention"]
    response = await http_client.post(
        "/v1/conformance/rules",
        json={"rules": [{"rule_id": r, "enabled": True, "params": {}} for r in incomplete]},
        headers=_headers("migration_architect", ARCHITECT),
    )
    assert response.status_code == 400
    assert "naming_convention" in response.json()["message"]


async def test_save_rules_rejects_an_unknown_rule(http_client) -> None:
    rules = [{"rule_id": r, "enabled": True, "params": {}} for r in RULES]
    rules.append({"rule_id": "not_a_real_rule", "enabled": True, "params": {}})
    response = await http_client.post(
        "/v1/conformance/rules", json={"rules": rules}, headers=_headers("migration_architect", ARCHITECT),
    )
    assert response.status_code == 400
    assert "not_a_real_rule" in response.json()["message"]
