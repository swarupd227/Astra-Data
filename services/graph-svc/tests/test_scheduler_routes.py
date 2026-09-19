"""Wave scheduler API route tests (story S12.1.2).

The admission-decision route calls ``GraphRepository.run_read_only_cypher``, which
only Apache AGE really implements -- the ``client`` fixture's own
``InMemoryGraphRepository`` raises ``NotImplementedError`` by design (see
``fakes.py``: "executing Cypher is the store's job"). The full admission-decision
path is covered end to end in ``test_integration_wave_scheduler.py`` against real
PostgreSQL + Apache AGE; this file covers what the in-memory fixture actually can:
role gating and pause/resume, which write plain node properties and never touch
Cypher.
"""

from __future__ import annotations

import pytest
from httpx import AsyncClient

from astra_graph.principal import PRINCIPAL_HEADER
from astra_graph.roles import ROLES_HEADER

_PRINCIPAL = "agent:harvester"
_HEADERS = {PRINCIPAL_HEADER: _PRINCIPAL}
#: A real Artizent role that is not platform_engineer -- migration_engineer, the
#: same non-platform-engineer role `conftest.py`'s own ARTIZENT_HEADERS uses.
_MIGRATION_ENGINEER_HEADERS = {**_HEADERS, ROLES_HEADER: "migration_engineer"}
_PLATFORM_ENGINEER_HEADERS = {**_HEADERS, ROLES_HEADER: "platform_engineer"}


@pytest.mark.asyncio
class TestSchedulerRoutes:
    """Scheduler admission and control routes."""

    async def test_the_decision_route_needs_a_real_store_behind_it(self, client: AsyncClient):
        """The decision reads the MU's real token budget from Postgres and its graph state
        through Cypher, neither of which the in-memory test app has: it has no connection
        pool, so the route refuses cleanly (400) rather than deciding on partial facts.
        The real decision path is `test_integration_wave_scheduler.py`'s."""
        response = await client.get(
            "/v1/scheduler/decision/nonexistent-wb/train-123",
            headers=_MIGRATION_ENGINEER_HEADERS,
        )
        assert response.status_code == 400
        assert "not ready" in response.text

    async def test_pause_train_refused_for_non_platform_engineer(
        self, client: AsyncClient
    ):
        """A real Artizent role that is not platform_engineer is refused (403),
        never reaching the "train not found" check."""
        response = await client.post(
            "/v1/scheduler/trains/train-123:pause",
            json={"reason": "Testing pause"},
            headers=_MIGRATION_ENGINEER_HEADERS,
        )
        assert response.status_code == 403

    async def test_pause_train_not_found_for_platform_engineer(
        self, client: AsyncClient
    ):
        """A real platform_engineer passes the role gate; the in-memory fixture
        has no seeded train, so the route's own honest "not found" (400)."""
        response = await client.post(
            "/v1/scheduler/trains/train-123:pause",
            json={"reason": "Testing pause"},
            headers=_PLATFORM_ENGINEER_HEADERS,
        )
        assert response.status_code == 400

    async def test_pause_site_refused_for_non_platform_engineer(
        self, client: AsyncClient
    ):
        """A real Artizent role that is not platform_engineer is refused (403),
        never reaching the "site not found" check."""
        response = await client.post(
            "/v1/scheduler/sites/site-123:pause",
            json={"reason": "Testing pause"},
            headers=_MIGRATION_ENGINEER_HEADERS,
        )
        assert response.status_code == 403

    async def test_pause_site_not_found_for_platform_engineer(
        self, client: AsyncClient
    ):
        """A real platform_engineer passes the role gate; the in-memory fixture
        has no seeded site, so the route's own honest "not found" (400)."""
        response = await client.post(
            "/v1/scheduler/sites/site-123:pause",
            json={"reason": "Testing pause"},
            headers=_PLATFORM_ENGINEER_HEADERS,
        )
        assert response.status_code == 400
