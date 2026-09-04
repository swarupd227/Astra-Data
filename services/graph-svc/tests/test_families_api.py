"""The families API surface — story S3.1.1.

Clustering itself needs a real PostgreSQL/AGE pool (``Cartographer`` reads the index
tables directly, the same way every other reader in this service does) and is exercised
end to end in ``test_integration_cartographer.py``. What is tested here, against the
lightweight in-memory ``client`` fixture, is the HTTP contract: request validation, the
"not configured" refusal, and the 404 shape — the parts that do not need a database to get
wrong.
"""

from __future__ import annotations

from .conftest import ARTIZENT_HEADERS


async def test_clustering_is_refused_when_not_configured(client) -> None:
    """The `client` fixture bypasses the real lifespan (see conftest.py), so
    `app.state.cartographer` is never set — exactly the state a deployment with no
    Postgres pool wired up would be in."""
    response = await client.post("/v1/families:cluster", json={}, headers=ARTIZENT_HEADERS)
    assert response.status_code == 400
    assert "not available" in response.json()["message"]


async def test_listing_families_is_refused_when_not_configured(client) -> None:
    response = await client.get("/v1/families", headers=ARTIZENT_HEADERS)
    assert response.status_code == 400


async def test_the_status_endpoint_reports_nothing_running_by_default(client) -> None:
    """Unlike the other routes, status has nothing to refuse — an idle deployment that
    has never clustered is a fact, not an error."""
    response = await client.get("/v1/families:cluster/status", headers=ARTIZENT_HEADERS)
    assert response.status_code == 200
    body = response.json()
    assert body["running"] is False
    assert body["last_result"] is None


async def test_an_out_of_range_threshold_is_rejected(client) -> None:
    response = await client.post(
        "/v1/families:cluster", json={"threshold": 1.5}, headers=ARTIZENT_HEADERS
    )
    assert response.status_code == 422


async def test_an_unknown_field_is_rejected(client) -> None:
    """`extra=\"forbid\"` — a caller misspelling `min_family_size` should see a rejection,
    not a silently-ignored typo that runs at the default."""
    response = await client.post(
        "/v1/families:cluster", json={"min_family_sze": 5}, headers=ARTIZENT_HEADERS
    )
    assert response.status_code == 422
