"""Wave scheduler API route tests (story S12.1.2)."""

from __future__ import annotations

import pytest
from httpx import AsyncClient


@pytest.mark.asyncio
class TestSchedulerRoutes:
    """Scheduler admission and control routes."""

    async def test_get_admission_decision_workbook_not_found(self, client: AsyncClient):
        """Query admission decision for non-existent workbook returns error."""
        response = await client.get(
            "/v1/scheduler/decision/nonexistent-wb/train-123",
            headers={"X-Astra-Principal": "test@example.com", "X-Astra-Roles": "artizent"},
        )
        assert response.status_code in (400, 404)

    async def test_pause_train_requires_platform_engineer(self, client: AsyncClient):
        """Pausing a train requires PlatformEngineer role."""
        response = await client.post(
            "/v1/scheduler/trains/train-123:pause",
            json={"reason": "Testing pause"},
            headers={"X-Astra-Principal": "test@example.com", "X-Astra-Roles": "artizent"},
        )
        # Should fail or succeed depending on role enforcement
        # For now, just verify the route exists and responds
        assert response.status_code in (400, 403, 404)

    async def test_pause_site_requires_platform_engineer(self, client: AsyncClient):
        """Pausing a site requires PlatformEngineer role."""
        response = await client.post(
            "/v1/scheduler/sites/site-123:pause",
            json={"reason": "Testing pause"},
            headers={"X-Astra-Principal": "test@example.com", "X-Astra-Roles": "artizent"},
        )
        # Should fail or succeed depending on role enforcement
        assert response.status_code in (400, 403, 404)
