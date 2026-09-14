"""Entra ID sign-in: verified identity, groups mapped to roles.

Spec §18.1: "Users sign in with Entra ID; roles are mapped from Entra groups" (story
S11.1.1, F11.1). ``principal.py``'s own docstring already named this the plan: "the
principal is asserted by the caller in a header ... until agent and human identity land
in E11 ... the header is replaced by a verified identity in E11 without the ontology or
the write path changing shape." This module is that replacement, and it keeps that
promise literally: ``validate_token`` (wired in ``api/deps.py``'s own ``get_bearer_claims``)
produces the exact same ``Principal``/``RoleSet`` pair the header path already produces,
so nothing that consumes them needs to know which path supplied them.

**Disclosed, not yet connected — exactly like ``directory.py``'s own ``NullDirectoryResolver``.**
JWKS fetch, RS256 signature verification and claim checks (issuer, audience, expiry) are
all real and covered by tests against a locally generated RSA keypair. None of it has ever
run against a live Entra tenant, because this project has not been given one yet (story
S11.1.1's own scoping answer: "build it disclosed, not yet connected"). Configuring
``ASTRA_ENTRA_TENANT_ID``/``ASTRA_ENTRA_CLIENT_ID`` is what turns this on; leaving them
unset — the honest default today — means every request keeps using the ``X-Astra-
Principal``/``X-Astra-Roles`` headers exactly as it always has.

**A known, disclosed gap: group overage.** A user in more than ~200 groups gets a
``_claim_names``/``hasgroups`` indicator from Entra instead of an inline ``groups`` claim,
requiring a follow-up Microsoft Graph call this module does not make. Real, undisclosed
gap until a live tenant surfaces it; noted here rather than silently mishandled.
"""

from __future__ import annotations

import json
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

import httpx
import jwt
from jwt.algorithms import RSAAlgorithm

from .config import Settings, settings
from .principal import InvalidPrincipalError, Principal
from .principal import parse as parse_principal
from .roles import Role, RoleSet

#: ``Authorization: Bearer <token>``.
AUTHORIZATION_HEADER = "Authorization"


class EntraError(Exception):
    """A bearer token could not be verified, or Entra configuration is malformed."""


@dataclass(frozen=True, slots=True)
class EntraConfig:
    tenant_id: str
    client_id: str
    group_role_map: dict[str, Role]

    @property
    def issuer(self) -> str:
        return f"https://login.microsoftonline.com/{self.tenant_id}/v2.0"

    @property
    def jwks_uri(self) -> str:
        return f"https://login.microsoftonline.com/{self.tenant_id}/discovery/v2.0/keys"


@dataclass(frozen=True, slots=True)
class EntraClaims:
    """What a verified bearer token proves — the same pair the header path asserts."""

    principal: Principal
    roles: RoleSet
    tenant_id: str
    object_id: str | None = None


def parse_group_role_map(raw: str) -> dict[str, Role]:
    """``ASTRA_ENTRA_GROUP_ROLE_MAP``: ``<entra-group-id>:<role>,<entra-group-id>:<role>``.

    The group id is whatever GUID the client's Entra tenant assigns; the role must be one
    of ``roles.py``'s own real, enforced set — this never invents a role, it only decides
    which existing one an Entra group stands for.
    """
    mapping: dict[str, Role] = {}
    for pair in raw.split(","):
        pair = pair.strip()
        if not pair:
            continue
        group_id, sep, role_token = pair.partition(":")
        group_id = group_id.strip()
        role_token = role_token.strip()
        if not sep or not group_id or not role_token:
            raise EntraError(
                f"ASTRA_ENTRA_GROUP_ROLE_MAP entry {pair!r} must be '<group-id>:<role>'"
            )
        try:
            mapping[group_id] = Role(role_token)
        except ValueError as exc:
            known = ", ".join(sorted(role.value for role in Role))
            raise EntraError(
                f"ASTRA_ENTRA_GROUP_ROLE_MAP names an unknown role {role_token!r} for group "
                f"{group_id!r}. Known roles: {known}."
            ) from exc
    return mapping


def map_groups_to_roles(group_ids: list[str], group_role_map: dict[str, Role]) -> RoleSet:
    """A group a tenant has not mapped grants nothing — the honest default for a group
    the client has not yet told this deployment what it means, not an error."""
    return RoleSet(frozenset(group_role_map[g] for g in group_ids if g in group_role_map))


def get_entra_config(config: Settings | None = None) -> EntraConfig | None:
    """``None`` — the honest default — until both tenant and client id are configured."""
    config = config or settings()
    if not config.entra_tenant_id or not config.entra_client_id:
        return None
    return EntraConfig(
        tenant_id=config.entra_tenant_id,
        client_id=config.entra_client_id,
        group_role_map=parse_group_role_map(config.entra_group_role_map),
    )


class JwksCache:
    """Entra's own published signing keys, fetched once per ``ttl_seconds`` rather than
    once per request — real network I/O behind a real cache, the same reasoning
    ``directory.py``'s own docstring gives for caching a resolver's own lookups.

    ``fetch`` is injectable so tests exercise real signature verification against a
    locally generated keypair without a network call standing in for Entra's own JWKS
    endpoint.
    """

    def __init__(
        self,
        jwks_uri: str,
        *,
        ttl_seconds: float = 3600.0,
        fetch: Callable[[str], dict[str, Any]] | None = None,
    ) -> None:
        self._jwks_uri = jwks_uri
        self._ttl_seconds = ttl_seconds
        self._fetch = fetch or _fetch_jwks_document
        self._keys: dict[str, Any] = {}
        self._fetched_at = 0.0

    def key_for(self, kid: str) -> Any | None:
        now = time.monotonic()
        if kid not in self._keys or (now - self._fetched_at) > self._ttl_seconds:
            self._refresh()
        return self._keys.get(kid)

    def _refresh(self) -> None:
        document = self._fetch(self._jwks_uri)
        keys: dict[str, Any] = {}
        for jwk in document.get("keys", []):
            kid = jwk.get("kid")
            if not kid:
                continue
            try:
                keys[kid] = RSAAlgorithm.from_jwk(json.dumps(jwk))
            except (ValueError, TypeError):
                continue
        self._keys = keys
        self._fetched_at = time.monotonic()


def _fetch_jwks_document(jwks_uri: str) -> dict[str, Any]:
    response = httpx.get(jwks_uri, timeout=10.0)
    response.raise_for_status()
    result: dict[str, Any] = response.json()
    return result


_jwks_caches: dict[str, JwksCache] = {}


def jwks_cache_for(config: EntraConfig) -> JwksCache:
    """One cache per tenant JWKS endpoint, reused across requests for the life of the
    process — a fresh ``JwksCache`` per call would fetch (and refuse to cache) on every
    single bearer token, defeating the point of ``ttl_seconds``."""
    cache = _jwks_caches.get(config.jwks_uri)
    if cache is None:
        cache = JwksCache(config.jwks_uri)
        _jwks_caches[config.jwks_uri] = cache
    return cache


def validate_token(token: str, config: EntraConfig, jwks: JwksCache) -> EntraClaims:
    """Verify signature, issuer, audience and expiry, then map the token's own claims to
    this service's real identity shape. Raises ``EntraError`` on any failure — a caller
    presenting a bearer token that does not check out gets a 401, never a silent
    fall-through to the header path (see ``api/deps.py``)."""
    try:
        header = jwt.get_unverified_header(token)
    except jwt.PyJWTError as exc:
        raise EntraError(f"malformed bearer token: {exc}") from exc

    kid = header.get("kid")
    if not kid:
        raise EntraError("bearer token header carries no 'kid'")

    key = jwks.key_for(kid)
    if key is None:
        raise EntraError(f"no Entra signing key found for kid {kid!r}")

    try:
        payload = jwt.decode(
            token,
            key=key,
            algorithms=["RS256"],
            audience=config.client_id,
            issuer=config.issuer,
            options={"require": ["exp", "iss", "aud"]},
        )
    except jwt.PyJWTError as exc:
        raise EntraError(f"bearer token failed verification: {exc}") from exc

    upn = payload.get("preferred_username") or payload.get("upn") or payload.get("oid")
    if not upn:
        raise EntraError(
            "bearer token carries no preferred_username/upn/oid claim to identify the user"
        )
    try:
        principal = parse_principal(f"user:{upn}")
    except InvalidPrincipalError as exc:
        raise EntraError(
            f"bearer token's identity claim does not form a valid principal: {exc}"
        ) from exc

    group_ids = [str(g) for g in payload.get("groups", [])]
    roles = map_groups_to_roles(group_ids, config.group_role_map)

    return EntraClaims(
        principal=principal,
        roles=roles,
        tenant_id=config.tenant_id,
        object_id=payload.get("oid"),
    )


__all__ = [
    "AUTHORIZATION_HEADER",
    "EntraClaims",
    "EntraConfig",
    "EntraError",
    "JwksCache",
    "get_entra_config",
    "jwks_cache_for",
    "map_groups_to_roles",
    "parse_group_role_map",
    "validate_token",
]
