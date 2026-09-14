"""SPIFFE workload identity — story S11.1.2, opens F11.1.

`LocalWorkloadIdentityProvider` is disclosed as real but never run against a live SPIRE
server (see its own module docstring) -- these tests exercise the real signing/
verification logic directly, the same "a locally generated keypair standing in for a
live issuer's own signing key" posture `test_entra.py` already established for Entra ID.
"""

from __future__ import annotations

import time

import pytest

from astra_graph.workload_identity import (
    DEFAULT_TRUST_DOMAIN,
    MIN_REVOCATION_REASON_LENGTH,
    InMemorySvidStore,
    LocalWorkloadIdentityProvider,
    WorkloadIdentityError,
    WorkloadIdentityRequestError,
    record_from_svid,
    spiffe_id_for,
)


def test_spiffe_id_shape() -> None:
    assert spiffe_id_for("transpiler", "run-1", trust_domain="astra.example") == (
        "spiffe://astra.example/agent/transpiler/run/run-1"
    )


# ------------------------------------------------------------------- issuance


async def test_issuing_an_svid() -> None:
    provider = LocalWorkloadIdentityProvider(trust_domain="astra.example")
    svid = await provider.issue(agent_id="transpiler", run_id="run-1")
    assert svid.spiffe_id == "spiffe://astra.example/agent/transpiler/run/run-1"
    assert svid.agent_id == "transpiler"
    assert svid.run_id == "run-1"
    assert svid.serial == 1
    assert svid.expires_at > svid.issued_at


async def test_two_issuances_get_distinct_jtis() -> None:
    provider = LocalWorkloadIdentityProvider()
    first = await provider.issue(agent_id="steward", run_id="run-1")
    second = await provider.issue(agent_id="steward", run_id="run-2")
    assert first.jti != second.jti


# -------------------------------------------------------------------- rotation


async def test_rotating_an_svid_keeps_the_same_run_but_bumps_the_serial() -> None:
    provider = LocalWorkloadIdentityProvider()
    original = await provider.issue(agent_id="harvest-scheduler", run_id="run-1")
    rotated = await provider.rotate(original)
    assert rotated.agent_id == original.agent_id
    assert rotated.run_id == original.run_id
    assert rotated.serial == original.serial + 1
    assert rotated.jti != original.jti


async def test_a_rotated_svid_verifies_on_its_own() -> None:
    provider = LocalWorkloadIdentityProvider()
    original = await provider.issue(agent_id="harvest-scheduler", run_id="run-1")
    rotated = await provider.rotate(original)
    claims = provider.verify(rotated.token)
    assert claims.jti == rotated.jti


# ------------------------------------------------------------------ verification


async def test_a_real_svid_verifies_against_its_own_issuer() -> None:
    provider = LocalWorkloadIdentityProvider(trust_domain="astra.example")
    svid = await provider.issue(agent_id="transpiler", run_id="run-1")
    claims = provider.verify(svid.token)
    assert claims.spiffe_id == svid.spiffe_id
    assert claims.agent_id == "transpiler"
    assert claims.run_id == "run-1"
    assert claims.jti == svid.jti


async def test_a_token_signed_by_a_different_issuer_is_rejected() -> None:
    issuer = LocalWorkloadIdentityProvider(trust_domain="astra.example")
    other = LocalWorkloadIdentityProvider(trust_domain="astra.example")
    svid = await issuer.issue(agent_id="transpiler", run_id="run-1")
    with pytest.raises(WorkloadIdentityError, match="verification"):
        other.verify(svid.token)


async def test_an_expired_svid_is_rejected() -> None:
    provider = LocalWorkloadIdentityProvider(ttl_seconds=0)
    svid = await provider.issue(agent_id="transpiler", run_id="run-1")
    time.sleep(1.1)
    with pytest.raises(WorkloadIdentityError, match="verification"):
        provider.verify(svid.token)


async def test_a_malformed_token_is_rejected() -> None:
    provider = LocalWorkloadIdentityProvider()
    with pytest.raises(WorkloadIdentityError, match="verification"):
        provider.verify("not-a-real-jwt-at-all")


def test_public_key_pem_is_a_real_exportable_key() -> None:
    provider = LocalWorkloadIdentityProvider()
    pem = provider.public_key_pem()
    assert pem.startswith(b"-----BEGIN PUBLIC KEY-----")


# ---------------------------------------------------------------------- record


async def test_record_from_svid_never_carries_the_token() -> None:
    provider = LocalWorkloadIdentityProvider()
    svid = await provider.issue(agent_id="transpiler", run_id="run-1")
    record = record_from_svid(svid)
    assert not hasattr(record, "token")
    assert record.jti == svid.jti
    assert record.status == "active"


# ----------------------------------------------------------------- InMemorySvidStore


async def test_recording_and_listing() -> None:
    provider = LocalWorkloadIdentityProvider()
    store = InMemorySvidStore()
    svid = await provider.issue(agent_id="transpiler", run_id="run-1")
    await store.record(record_from_svid(svid))

    records = await store.list_records()
    assert len(records) == 1
    assert records[0].jti == svid.jti


async def test_listing_filters_by_agent_id() -> None:
    provider = LocalWorkloadIdentityProvider()
    store = InMemorySvidStore()
    a = await provider.issue(agent_id="transpiler", run_id="run-1")
    b = await provider.issue(agent_id="steward", run_id="run-2")
    await store.record(record_from_svid(a))
    await store.record(record_from_svid(b))

    records = await store.list_records(agent_id="steward")
    assert [r.jti for r in records] == [b.jti]


async def test_revoking_a_real_record() -> None:
    provider = LocalWorkloadIdentityProvider()
    store = InMemorySvidStore()
    svid = await provider.issue(agent_id="transpiler", run_id="run-1")
    await store.record(record_from_svid(svid))

    revoked = await store.revoke(svid.jti, reason="rotated off a compromised host", revoked_by="user:pe@client.example")
    assert revoked.status == "revoked"
    assert revoked.revoked_by == "user:pe@client.example"
    assert await store.is_revoked(svid.jti) is True


async def test_revoking_requires_a_real_reason() -> None:
    provider = LocalWorkloadIdentityProvider()
    store = InMemorySvidStore()
    svid = await provider.issue(agent_id="transpiler", run_id="run-1")
    await store.record(record_from_svid(svid))

    with pytest.raises(WorkloadIdentityRequestError, match=str(MIN_REVOCATION_REASON_LENGTH)):
        await store.revoke(svid.jti, reason="short", revoked_by="user:pe@client.example")


async def test_revoking_an_unknown_jti_is_refused() -> None:
    store = InMemorySvidStore()
    with pytest.raises(WorkloadIdentityRequestError, match="no SVID record"):
        await store.revoke("no-such-jti", reason="a real reason, long enough", revoked_by="user:pe@client.example")


async def test_is_revoked_is_false_for_an_unknown_jti() -> None:
    store = InMemorySvidStore()
    assert await store.is_revoked("no-such-jti") is False


def test_default_trust_domain_is_disclosed_local() -> None:
    assert "local" in DEFAULT_TRUST_DOMAIN or "internal" in DEFAULT_TRUST_DOMAIN
