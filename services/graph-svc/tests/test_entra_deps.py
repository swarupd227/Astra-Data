"""Entra ID wired into the request path — story S11.1.1.

`get_bearer_claims`/`get_principal`/`get_role_set` (api/deps.py) must produce the exact
same `Principal`/`RoleSet` shape the existing X-Astra-Principal/X-Astra-Roles header path
already does, and must leave every caller who does not present a bearer token completely
unaffected. `settings()` is a process-wide `lru_cache` singleton the whole suite shares,
so these tests monkeypatch `deps.get_entra_config` directly rather than mutating global
settings — the same seam `get_bearer_claims` itself calls through.
"""

from __future__ import annotations

import time

import jwt
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi import HTTPException
from jwt.algorithms import RSAAlgorithm

from astra_graph.api import deps
from astra_graph.entra import EntraConfig, JwksCache
from astra_graph.roles import Role

from .conftest import ARTIZENT_HEADERS

TENANT_ID = "11111111-1111-1111-1111-111111111111"
CLIENT_ID = "22222222-2222-2222-2222-222222222222"
KID = "test-signing-key-1"


class _Keypair:
    def __init__(self) -> None:
        self.private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        self.private_pem = self.private_key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )

    def jwks_document(self) -> dict[str, object]:
        jwk = RSAAlgorithm.to_jwk(self.private_key.public_key(), as_dict=True)
        jwk["kid"] = KID
        return {"keys": [jwk]}

    def issue_token(self, config: EntraConfig, **claim_overrides: object) -> str:
        now = int(time.time())
        payload = {
            "iss": config.issuer, "aud": config.client_id, "iat": now, "exp": now + 3600,
            "preferred_username": "a.mehta@client.example", "groups": [],
        }
        payload.update(claim_overrides)
        return jwt.encode(payload, self.private_pem, algorithm="RS256", headers={"kid": KID})


@pytest.fixture
def keypair() -> _Keypair:
    return _Keypair()


@pytest.fixture
def entra_configured(monkeypatch: pytest.MonkeyPatch, keypair: _Keypair) -> EntraConfig:
    config = EntraConfig(
        tenant_id=TENANT_ID,
        client_id=CLIENT_ID,
        group_role_map={"grp-report-owner": Role.CLIENT_REPORT_OWNER},
    )
    monkeypatch.setattr(deps, "get_entra_config", lambda: config)
    monkeypatch.setattr(
        deps, "jwks_cache_for", lambda _config: JwksCache("unused", fetch=lambda _u: keypair.jwks_document())
    )
    return config


# --------------------------------------------------- backward compatibility, unchanged


def test_no_authorization_header_falls_through_to_the_principal_header() -> None:
    """Entra unconfigured (the honest default): `get_bearer_claims` returns `None`
    without even inspecting the header, and `get_principal`/`get_role_set` behave exactly
    as they did before this story."""
    assert deps.get_bearer_claims(None) is None
    principal = deps.get_principal(None, "agent:harvester", None)
    assert principal.value == "agent:harvester"


def test_a_non_bearer_authorization_header_is_ignored_when_entra_is_unconfigured() -> None:
    assert deps.get_bearer_claims("Basic dXNlcjpwYXNz") is None


async def test_existing_header_only_requests_are_unaffected(client) -> None:
    response = await client.get(
        "/v1/artefacts", params={"mu_ref": "some-mu"}, headers=ARTIZENT_HEADERS
    )
    assert response.status_code == 200  # never touches the new bearer-token path


# --------------------------------------------------------- Entra configured, no token


def test_entra_configured_but_no_authorization_header_still_falls_through(entra_configured: EntraConfig) -> None:
    assert deps.get_bearer_claims(None) is None
    principal = deps.get_principal(None, "agent:harvester", None)
    assert principal.value == "agent:harvester"


# ------------------------------------------------------- Entra configured, verified token


def test_a_verified_bearer_token_produces_claims(entra_configured: EntraConfig, keypair: _Keypair) -> None:
    token = keypair.issue_token(entra_configured, groups=["grp-report-owner"])
    claims = deps.get_bearer_claims(f"Bearer {token}")
    assert claims is not None
    assert claims.principal.value == "user:a.mehta@client.example"
    assert claims.roles.roles == {Role.CLIENT_REPORT_OWNER}


def test_get_principal_prefers_verified_claims_over_the_header(entra_configured: EntraConfig, keypair: _Keypair) -> None:
    token = keypair.issue_token(entra_configured)
    claims = deps.get_bearer_claims(f"Bearer {token}")
    principal = deps.get_principal(claims, "agent:harvester", None)
    assert principal.value == "user:a.mehta@client.example"


def test_get_role_set_prefers_verified_claims_over_the_header(entra_configured: EntraConfig, keypair: _Keypair) -> None:
    token = keypair.issue_token(entra_configured, groups=["grp-report-owner"])
    claims = deps.get_bearer_claims(f"Bearer {token}")
    roles = deps.get_role_set(claims, "programme_manager")
    assert roles.roles == {Role.CLIENT_REPORT_OWNER}


def test_bearer_scheme_is_case_insensitive(entra_configured: EntraConfig, keypair: _Keypair) -> None:
    token = keypair.issue_token(entra_configured)
    claims = deps.get_bearer_claims(f"bearer {token}")
    assert claims is not None


# ------------------------------------------------------ Entra configured, bad token


def test_an_invalid_bearer_token_is_a_401_not_a_silent_fallthrough(entra_configured: EntraConfig) -> None:
    with pytest.raises(HTTPException) as caught:
        deps.get_bearer_claims("Bearer not-a-real-jwt")
    assert caught.value.status_code == 401
    assert caught.value.detail["error"] == "invalid_bearer_token"


async def test_an_invalid_bearer_token_is_rejected_over_http(
    client, entra_configured: EntraConfig
) -> None:
    response = await client.post(
        "/v1/cypher",
        json={"query": "MATCH (n:Site) RETURN n"},
        headers={"Authorization": "Bearer not-a-real-jwt"},
    )
    assert response.status_code == 401
    assert response.json()["detail"]["error"] == "invalid_bearer_token"


async def test_a_verified_bearer_token_authorises_an_http_request(
    client, entra_configured: EntraConfig, keypair: _Keypair
) -> None:
    """`ArtefactReaderDep` (routes_artefacts.py) is open to Artizent or the report
    owner; the token's own `grp-report-owner` group is mapped to exactly that role
    (`entra_configured`), so a bearer token alone -- no X-Astra-Principal/X-Astra-Roles
    header at all -- must reach and pass it."""
    token = keypair.issue_token(entra_configured, groups=["grp-report-owner"])
    response = await client.get(
        "/v1/artefacts",
        params={"mu_ref": "some-mu"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 200
