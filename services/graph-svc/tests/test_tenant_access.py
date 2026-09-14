"""Tenant & Access's own HTTP surface — story S11.1.2, opens F11.1.

Against the in-memory `SvidStore` the `client` fixture already wires up (`conftest.py`),
the same pattern every other store-backed route in this suite already uses.
"""

from __future__ import annotations

from astra_graph.roles import ROLES_HEADER
from astra_graph.workload_identity import record_from_svid

from .conftest import ARTIZENT_HEADERS, CLIENT_HEADERS, HEADERS

INFOSEC_HEADERS = {**HEADERS, ROLES_HEADER: "client_infosec_reviewer"}
PLATFORM_ENGINEER_HEADERS = {**HEADERS, ROLES_HEADER: "platform_engineer"}


async def test_reading_agent_records_requires_a_role(client) -> None:
    response = await client.get("/v1/tenant-access/agent-records", headers=HEADERS)
    assert response.status_code == 403


async def test_an_artizent_role_can_read_the_agent_catalog(client) -> None:
    response = await client.get("/v1/tenant-access/agent-records", headers=ARTIZENT_HEADERS)
    assert response.status_code == 200
    agents = {a["id"] for a in response.json()["agents"]}
    assert agents == {
        "harvester", "cartographer", "modeller", "transpiler",
        "compositor", "arbiter", "mender", "steward",
    }


async def test_the_infosec_reviewer_can_read_the_agent_catalog(client) -> None:
    response = await client.get("/v1/tenant-access/agent-records", headers=INFOSEC_HEADERS)
    assert response.status_code == 200


async def test_a_report_owner_cannot_read_tenant_access(client) -> None:
    response = await client.get("/v1/tenant-access/agent-records", headers=CLIENT_HEADERS)
    assert response.status_code == 403


async def test_the_transpiler_record_shows_its_own_real_narrowed_scope(client) -> None:
    response = await client.get("/v1/tenant-access/agent-records", headers=ARTIZENT_HEADERS)
    transpiler = next(a for a in response.json()["agents"] if a["id"] == "transpiler")
    assert transpiler["scope"]["unrestricted"] is False
    assert "Pattern.promotion_state" in transpiler["scope"]["forbidden_properties"]
    assert transpiler["charter"]["prohibited"] == [
        "write outside MU scope", "call executor", "modify Pattern.promotion_state",
    ]


async def test_listing_svids_when_none_issued_yet(client) -> None:
    response = await client.get("/v1/tenant-access/svids", headers=ARTIZENT_HEADERS)
    assert response.status_code == 200
    assert response.json() == {"svids": []}


async def test_listing_and_revoking_a_real_issued_svid(client) -> None:
    app = client._transport.app
    provider = app.state.svid_provider
    store = app.state.svid_store

    svid = await provider.issue(agent_id="transpiler", run_id="run-1")
    await store.record(record_from_svid(svid))

    listed = await client.get("/v1/tenant-access/svids", headers=ARTIZENT_HEADERS)
    assert listed.status_code == 200
    records = listed.json()["svids"]
    assert len(records) == 1
    assert records[0]["jti"] == svid.jti
    assert records[0]["status"] == "active"
    assert "token" not in records[0]

    filtered = await client.get(
        "/v1/tenant-access/svids", params={"agent_id": "steward"}, headers=ARTIZENT_HEADERS
    )
    assert filtered.json()["svids"] == []

    revoke = await client.post(
        f"/v1/tenant-access/svids/{svid.jti}:revoke",
        json={"reason": "rotated off a decommissioned host"},
        headers=PLATFORM_ENGINEER_HEADERS,
    )
    assert revoke.status_code == 200
    assert revoke.json()["status"] == "revoked"

    relisted = await client.get("/v1/tenant-access/svids", headers=ARTIZENT_HEADERS)
    assert relisted.json()["svids"][0]["status"] == "revoked"


async def test_revoking_requires_the_platform_engineer_role(client) -> None:
    app = client._transport.app
    provider = app.state.svid_provider
    store = app.state.svid_store

    svid = await provider.issue(agent_id="transpiler", run_id="run-1")
    await store.record(record_from_svid(svid))

    response = await client.post(
        f"/v1/tenant-access/svids/{svid.jti}:revoke",
        json={"reason": "a real reason, long enough"},
        headers=ARTIZENT_HEADERS,
    )
    assert response.status_code == 403


async def test_revoking_requires_a_real_reason(client) -> None:
    app = client._transport.app
    provider = app.state.svid_provider
    store = app.state.svid_store

    svid = await provider.issue(agent_id="transpiler", run_id="run-1")
    await store.record(record_from_svid(svid))

    response = await client.post(
        f"/v1/tenant-access/svids/{svid.jti}:revoke",
        json={"reason": ""},
        headers=PLATFORM_ENGINEER_HEADERS,
    )
    assert response.status_code == 422


async def test_revoking_an_unknown_jti_is_a_400(client) -> None:
    response = await client.post(
        "/v1/tenant-access/svids/no-such-jti:revoke",
        json={"reason": "a real reason, long enough"},
        headers=PLATFORM_ENGINEER_HEADERS,
    )
    assert response.status_code == 400
