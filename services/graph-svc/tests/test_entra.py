"""Entra ID sign-in: JWKS caching, token verification, group-to-role mapping.

Story S11.1.1, spec §18.1. Verified against a locally generated RSA keypair standing in
for a real Entra tenant's own signing key — see `entra.py`'s own module docstring for
what "disclosed, not yet connected" means here."""

from __future__ import annotations

import time

import jwt
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from jwt.algorithms import RSAAlgorithm

from astra_graph.entra import (
    EntraConfig,
    EntraError,
    JwksCache,
    get_entra_config,
    map_groups_to_roles,
    parse_group_role_map,
    validate_token,
)
from astra_graph.roles import Role

TENANT_ID = "11111111-1111-1111-1111-111111111111"
CLIENT_ID = "22222222-2222-2222-2222-222222222222"
KID = "test-signing-key-1"


def _config(**overrides: object) -> EntraConfig:
    kwargs = {"tenant_id": TENANT_ID, "client_id": CLIENT_ID, "group_role_map": {}}
    kwargs.update(overrides)
    return EntraConfig(**kwargs)  # type: ignore[arg-type]


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
        jwk["use"] = "sig"
        return {"keys": [jwk]}

    def issue_token(self, config: EntraConfig, *, claims_overrides: dict[str, object] | None = None) -> str:
        now = int(time.time())
        payload = {
            "iss": config.issuer,
            "aud": config.client_id,
            "iat": now,
            "exp": now + 3600,
            "preferred_username": "a.mehta@client.example",
            "oid": "33333333-3333-3333-3333-333333333333",
            "groups": [],
        }
        payload.update(claims_overrides or {})
        return jwt.encode(payload, self.private_pem, algorithm="RS256", headers={"kid": KID})


@pytest.fixture
def keypair() -> _Keypair:
    return _Keypair()


def _jwks_for(keypair: _Keypair) -> JwksCache:
    return JwksCache("https://example.invalid/keys", fetch=lambda _uri: keypair.jwks_document())


# ---------------------------------------------------------- group/role mapping


def test_parse_group_role_map() -> None:
    mapping = parse_group_role_map("grp-a:client_report_owner,grp-b:programme_manager")
    assert mapping == {"grp-a": Role.CLIENT_REPORT_OWNER, "grp-b": Role.PROGRAMME_MANAGER}


def test_an_empty_group_role_map_is_the_honest_default() -> None:
    assert parse_group_role_map("") == {}
    assert parse_group_role_map("   ") == {}


def test_a_malformed_group_role_map_entry_is_rejected() -> None:
    with pytest.raises(EntraError, match="must be"):
        parse_group_role_map("grp-a-with-no-role")


def test_an_unknown_role_in_the_group_role_map_is_rejected() -> None:
    with pytest.raises(EntraError, match="unknown role"):
        parse_group_role_map("grp-a:chief_migrator")


def test_map_groups_to_roles_ignores_unmapped_groups() -> None:
    mapping = {"grp-a": Role.CLIENT_REPORT_OWNER}
    roles = map_groups_to_roles(["grp-a", "grp-unmapped"], mapping)
    assert roles.roles == {Role.CLIENT_REPORT_OWNER}


def test_map_groups_to_roles_of_no_groups_is_empty() -> None:
    assert map_groups_to_roles([], {"grp-a": Role.CLIENT_REPORT_OWNER}).roles == frozenset()


# --------------------------------------------------------------- entra config


def test_entra_is_unconfigured_by_default() -> None:
    from astra_graph.config import Settings

    settings = Settings(
        postgres_host="localhost", postgres_port=5432, postgres_db="astra",
        postgres_user="astra", postgres_password="test", graph_name="astra_estate_test",
        env="local", log_level="INFO", pool_min_size=2, pool_max_size=10,
    )
    assert get_entra_config(settings) is None


def test_entra_config_requires_both_tenant_and_client_id() -> None:
    from astra_graph.config import Settings

    base = {
        "postgres_host": "localhost", "postgres_port": 5432, "postgres_db": "astra",
        "postgres_user": "astra", "postgres_password": "test", "graph_name": "astra_estate_test",
        "env": "local", "log_level": "INFO", "pool_min_size": 2, "pool_max_size": 10,
    }
    assert get_entra_config(Settings(**base, entra_tenant_id=TENANT_ID)) is None
    assert get_entra_config(Settings(**base, entra_client_id=CLIENT_ID)) is None
    assert get_entra_config(Settings(**base, entra_tenant_id=TENANT_ID, entra_client_id=CLIENT_ID)) is not None


def test_entra_config_issuer_and_jwks_uri() -> None:
    config = _config()
    assert config.issuer == f"https://login.microsoftonline.com/{TENANT_ID}/v2.0"
    assert config.jwks_uri == f"https://login.microsoftonline.com/{TENANT_ID}/discovery/v2.0/keys"


# ------------------------------------------------------------ token validation


def test_a_validly_signed_token_verifies(keypair: _Keypair) -> None:
    config = _config()
    token = keypair.issue_token(config)
    claims = validate_token(token, config, _jwks_for(keypair))
    assert claims.principal.value == "user:a.mehta@client.example"
    assert claims.tenant_id == TENANT_ID


def test_groups_map_to_roles(keypair: _Keypair) -> None:
    config = _config(group_role_map={"grp-report-owner": Role.CLIENT_REPORT_OWNER})
    token = keypair.issue_token(config, claims_overrides={"groups": ["grp-report-owner"]})
    claims = validate_token(token, config, _jwks_for(keypair))
    assert claims.roles.roles == {Role.CLIENT_REPORT_OWNER}


def test_a_token_signed_by_a_different_key_is_rejected(keypair: _Keypair) -> None:
    other = _Keypair()
    config = _config()
    token = other.issue_token(config)
    with pytest.raises(EntraError, match="verification"):
        validate_token(token, config, _jwks_for(keypair))


def test_an_expired_token_is_rejected(keypair: _Keypair) -> None:
    config = _config()
    token = keypair.issue_token(config, claims_overrides={"exp": int(time.time()) - 60})
    with pytest.raises(EntraError, match="verification"):
        validate_token(token, config, _jwks_for(keypair))


def test_a_token_for_the_wrong_audience_is_rejected(keypair: _Keypair) -> None:
    config = _config()
    token = keypair.issue_token(config, claims_overrides={"aud": "some-other-app"})
    with pytest.raises(EntraError, match="verification"):
        validate_token(token, config, _jwks_for(keypair))


def test_a_token_from_the_wrong_issuer_is_rejected(keypair: _Keypair) -> None:
    config = _config()
    token = keypair.issue_token(config, claims_overrides={"iss": "https://login.microsoftonline.com/other-tenant/v2.0"})
    with pytest.raises(EntraError, match="verification"):
        validate_token(token, config, _jwks_for(keypair))


def test_a_token_with_an_unknown_kid_is_rejected(keypair: _Keypair) -> None:
    config = _config()
    now = int(time.time())
    payload = {
        "iss": config.issuer, "aud": config.client_id, "iat": now, "exp": now + 3600,
        "preferred_username": "a.mehta@client.example",
    }
    token = jwt.encode(payload, keypair.private_pem, algorithm="RS256", headers={"kid": "no-such-key"})
    with pytest.raises(EntraError, match="no Entra signing key"):
        validate_token(token, config, _jwks_for(keypair))


def test_a_token_with_no_identity_claim_is_rejected(keypair: _Keypair) -> None:
    config = _config()
    now = int(time.time())
    payload = {"iss": config.issuer, "aud": config.client_id, "iat": now, "exp": now + 3600}
    token = jwt.encode(payload, keypair.private_pem, algorithm="RS256", headers={"kid": KID})
    with pytest.raises(EntraError, match="carries no preferred_username"):
        validate_token(token, config, _jwks_for(keypair))


def test_a_malformed_bearer_token_is_rejected(keypair: _Keypair) -> None:
    with pytest.raises(EntraError, match="malformed"):
        validate_token("not-a-jwt-at-all", _config(), _jwks_for(keypair))


def test_an_identity_claim_that_does_not_form_a_valid_principal_is_rejected(keypair: _Keypair) -> None:
    config = _config()
    token = keypair.issue_token(config, claims_overrides={"preferred_username": "has spaces!"})
    with pytest.raises(EntraError, match="does not form a valid principal"):
        validate_token(token, config, _jwks_for(keypair))


# ------------------------------------------------------------------- JwksCache


def test_jwks_cache_reuses_keys_within_the_ttl(keypair: _Keypair) -> None:
    calls = []

    def fetch(uri: str) -> dict[str, object]:
        calls.append(uri)
        return keypair.jwks_document()

    cache = JwksCache("https://example.invalid/keys", ttl_seconds=3600.0, fetch=fetch)
    cache.key_for(KID)
    cache.key_for(KID)
    assert len(calls) == 1


def test_jwks_cache_refetches_after_ttl_expiry(keypair: _Keypair, monkeypatch: pytest.MonkeyPatch) -> None:
    calls = []

    def fetch(uri: str) -> dict[str, object]:
        calls.append(uri)
        return keypair.jwks_document()

    cache = JwksCache("https://example.invalid/keys", ttl_seconds=0.0, fetch=fetch)
    cache.key_for(KID)
    cache.key_for(KID)
    assert len(calls) == 2


def test_jwks_cache_returns_none_for_an_unknown_kid(keypair: _Keypair) -> None:
    cache = _jwks_for(keypair)
    assert cache.key_for("no-such-key") is None
