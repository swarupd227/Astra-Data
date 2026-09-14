"""SPIFFE workload identity for agents — spec §18.1, story S11.1.2, opens F11.1.

    "Agents and adapters are non-human identities (SPIFFE/SVID workload identity) with
    short-lived credentials issued per run by a credential broker" (§18.1).
    "Agents receive SPIFFE identities (SVIDs) at start" (this story's own AC).

**Disclosed, not yet connected — the identical posture `entra.py` and `credentials.
KeyVaultCredentialProvider` already carry for their own E11 pieces (ADR 0079).** There is
no SPIRE server anywhere in `deploy/`, and this module does not add one: `LocalWorkload
IdentityProvider` mints and verifies real, short-lived JWT-SVIDs (RFC-shaped SPIFFE IDs,
signed with a real Ed25519 key, expiry-bound, revocable) using this service's own local
signing key — not a live SPIRE trust bundle. That is a real, tested, working seam
(`WorkloadIdentityProvider`), the same shape `directory.py`'s own `DirectoryResolver` and
`credentials.py`'s own `CredentialProvider` already take: a null/local implementation
today, a live SPIRE Workload API client dropped in behind the identical interface once a
real trust domain exists, with nothing above this module changing shape.

**What this module does NOT do.** It does not make authorization depend on a caller
*presenting* a verified SVID back to this service — nothing in this codebase's own real
running code has a channel to do that yet (no adapter worker or console client attaches
one to a request today). `agent_identity.py`'s own authorization checks still key off the
existing `X-Astra-Principal` header exactly as before this story. What is real here is the
other half of the AC: an SVID is actually minted, with a real signature and a real short
TTL, at the moment a real automated agent run starts (`harvest/scheduler.py`, `regression.
py`, the G2-triggered Steward build in `api/routes_g2.py`); its own record — never the
signed token itself, the same "do not persist a bearer credential" discipline `credentials.
py` already states for a source secret — is durably written so issuance, rotation and
revocation are each a real, queryable fact (`SvidStore`, the "Tenant & Access" screen).
"""

from __future__ import annotations

import time
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from typing import Protocol

import asyncpg
import jwt
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from .ids import new_ulid

SVID_TABLE = "public.svid_record"

#: A real deployment's own SPIRE server assigns this; unset here is the honest "not yet
#: connected" default -- see this module's own docstring.
DEFAULT_TRUST_DOMAIN = "astra-data.internal"

#: SVIDs are deliberately short-lived (spec §18.1's own "short-lived credentials") -- a
#: long-running agent process rotates rather than holding one for its whole lifetime.
DEFAULT_TTL_SECONDS = 300


class WorkloadIdentityError(Exception):
    """An SVID could not be issued, rotated or verified."""


@dataclass(frozen=True, slots=True)
class Svid:
    """A minted identity. Held in memory by the run that owns it; never persisted whole
    (`SvidRecord` below is the durable half, and it never carries `token`)."""

    spiffe_id: str
    agent_id: str
    run_id: str
    jti: str
    serial: int
    issued_at: int
    """Unix seconds."""
    expires_at: int
    token: str
    """The signed JWT-SVID. A bearer credential -- handed to the run that owns it and
    nowhere else; `SvidStore` never stores this column."""


@dataclass(frozen=True, slots=True)
class SvidClaims:
    """What `verify()` proves about a presented token, once something in this codebase
    actually presents one back (see this module's own docstring)."""

    spiffe_id: str
    agent_id: str
    run_id: str
    jti: str


def spiffe_id_for(agent_id: str, run_id: str, *, trust_domain: str = DEFAULT_TRUST_DOMAIN) -> str:
    return f"spiffe://{trust_domain}/agent/{agent_id}/run/{run_id}"


class WorkloadIdentityProvider(Protocol):
    async def issue(self, *, agent_id: str, run_id: str) -> Svid: ...
    async def rotate(self, svid: Svid) -> Svid: ...
    def verify(self, token: str) -> SvidClaims: ...


class LocalWorkloadIdentityProvider:
    """Mints and verifies real JWT-SVIDs with this process's own Ed25519 key -- generated
    fresh at construction, the same "ephemeral, local, disclosed" posture as not holding a
    live SPIRE trust bundle at all. A restart mints a new key, which is correct: an SVID
    this process issued before a restart should not outlive the process that vouches for
    it any more than a live SPIRE agent's own attestation would."""

    def __init__(
        self, *, trust_domain: str = DEFAULT_TRUST_DOMAIN, ttl_seconds: int = DEFAULT_TTL_SECONDS
    ) -> None:
        self._trust_domain = trust_domain
        self._ttl_seconds = ttl_seconds
        self._private_key = Ed25519PrivateKey.generate()
        self._public_key = self._private_key.public_key()

    @property
    def trust_domain(self) -> str:
        return self._trust_domain

    def _mint(self, *, agent_id: str, run_id: str, serial: int) -> Svid:
        spiffe_id = spiffe_id_for(agent_id, run_id, trust_domain=self._trust_domain)
        now = int(time.time())
        expires_at = now + self._ttl_seconds
        jti = new_ulid()
        payload = {
            "sub": spiffe_id,
            "iss": f"spiffe://{self._trust_domain}",
            "aud": self._trust_domain,
            "iat": now,
            "exp": expires_at,
            "jti": jti,
            "agent_id": agent_id,
            "run_id": run_id,
            "serial": serial,
        }
        token = jwt.encode(payload, self._private_key, algorithm="EdDSA")
        return Svid(
            spiffe_id=spiffe_id, agent_id=agent_id, run_id=run_id, jti=jti, serial=serial,
            issued_at=now, expires_at=expires_at, token=token,
        )

    async def issue(self, *, agent_id: str, run_id: str) -> Svid:
        return self._mint(agent_id=agent_id, run_id=run_id, serial=1)

    async def rotate(self, svid: Svid) -> Svid:
        """A fresh SVID for the same `(agent_id, run_id)`, one serial higher -- for a run
        that outlives its own current SVID's TTL. The old `jti` is not itself revoked (it
        expires on its own short clock); `SvidStore.record_rotation` links the two so
        "Tenant & Access" shows one real lineage, not two unrelated issuances."""
        return self._mint(agent_id=svid.agent_id, run_id=svid.run_id, serial=svid.serial + 1)

    def verify(self, token: str) -> SvidClaims:
        try:
            payload = jwt.decode(
                token, self._public_key, algorithms=["EdDSA"], audience=self._trust_domain,
                options={"require": ["exp", "iss", "aud", "sub", "jti"]},
            )
        except jwt.PyJWTError as exc:
            raise WorkloadIdentityError(f"SVID failed verification: {exc}") from exc
        return SvidClaims(
            spiffe_id=payload["sub"], agent_id=payload["agent_id"], run_id=payload["run_id"],
            jti=payload["jti"],
        )

    def public_key_pem(self) -> bytes:
        """For a caller that wants to verify independently of this process -- not used by
        anything in this codebase today, kept for the same reason `bom.py`'s own public
        key is a real, exportable artefact rather than kept only inside the signer."""
        return self._public_key.public_bytes(
            serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo,
        )


# --------------------------------------------------------------------------- the record

#: A revocation reason must say why -- the same `MIN_RETIREMENT_REASON_LENGTH` bar
#: `writes.py`'s own `retire_node` already sets for "a decision anyone can audit later".
MIN_REVOCATION_REASON_LENGTH = 8


class WorkloadIdentityRequestError(Exception):
    """A revocation (or another store-level request) was malformed."""


def _iso(value: datetime) -> str:
    return value.isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _parse_iso(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


@dataclass(frozen=True, slots=True)
class SvidRecord:
    """One durable row: an issuance, a rotation, or (in place) a revocation. Never the
    signed token itself -- see this module's own docstring."""

    id: str
    jti: str
    agent_id: str
    run_id: str
    spiffe_id: str
    serial: int
    predecessor_jti: str | None
    issued_at: str
    expires_at: str
    revoked_at: str | None = None
    revoked_by: str | None = None
    revocation_reason: str | None = None

    @property
    def status(self) -> str:
        if self.revoked_at is not None:
            return "revoked"
        if datetime.fromisoformat(self.expires_at.replace("Z", "+00:00")) < datetime.now(UTC):
            return "expired"
        return "active"

    def as_dict(self) -> dict[str, object]:
        return {
            "id": self.id,
            "jti": self.jti,
            "agent_id": self.agent_id,
            "run_id": self.run_id,
            "spiffe_id": self.spiffe_id,
            "serial": self.serial,
            "predecessor_jti": self.predecessor_jti,
            "issued_at": self.issued_at,
            "expires_at": self.expires_at,
            "revoked_at": self.revoked_at,
            "revoked_by": self.revoked_by,
            "revocation_reason": self.revocation_reason,
            "status": self.status,
        }


def record_from_svid(svid: Svid, *, predecessor_jti: str | None = None) -> SvidRecord:
    return SvidRecord(
        id=new_ulid(),
        jti=svid.jti,
        agent_id=svid.agent_id,
        run_id=svid.run_id,
        spiffe_id=svid.spiffe_id,
        serial=svid.serial,
        predecessor_jti=predecessor_jti,
        issued_at=_iso(datetime.fromtimestamp(svid.issued_at, tz=UTC)),
        expires_at=_iso(datetime.fromtimestamp(svid.expires_at, tz=UTC)),
    )


class SvidStore(Protocol):
    async def record(self, record: SvidRecord) -> SvidRecord: ...
    async def revoke(self, jti: str, *, reason: str, revoked_by: str) -> SvidRecord: ...
    async def is_revoked(self, jti: str) -> bool: ...
    async def list_records(self, *, agent_id: str | None = None, limit: int = 100) -> list[SvidRecord]: ...


class PostgresSvidStore:
    def __init__(self, pool: asyncpg.Pool, *, graph_name: str) -> None:
        self._pool = pool
        self._graph = graph_name

    async def record(self, record: SvidRecord) -> SvidRecord:
        async with self._pool.acquire() as conn:
            await conn.execute(
                f"""
                INSERT INTO {SVID_TABLE}
                    (id, graph, jti, agent_id, run_id, spiffe_id, serial, predecessor_jti,
                     issued_at, expires_at)
                VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10)
                """,
                record.id, self._graph, record.jti, record.agent_id, record.run_id,
                record.spiffe_id, record.serial, record.predecessor_jti,
                _parse_iso(record.issued_at), _parse_iso(record.expires_at),
            )
        return record

    async def revoke(self, jti: str, *, reason: str, revoked_by: str) -> SvidRecord:
        cleaned = reason.strip()
        if len(cleaned) < MIN_REVOCATION_REASON_LENGTH:
            raise WorkloadIdentityRequestError(
                f"a revocation needs a reason of at least {MIN_REVOCATION_REASON_LENGTH} "
                f"characters; it is the record of why an identity was cut off"
            )
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                f"""
                UPDATE {SVID_TABLE}
                   SET revoked_at = now(), revoked_by = $3, revocation_reason = $4
                 WHERE graph = $1 AND jti = $2
             RETURNING id, jti, agent_id, run_id, spiffe_id, serial, predecessor_jti,
                       issued_at, expires_at, revoked_at, revoked_by, revocation_reason
                """,
                self._graph, jti, revoked_by, cleaned,
            )
        if row is None:
            raise WorkloadIdentityRequestError(f"no SVID record with jti '{jti}'")
        return _record(row)

    async def is_revoked(self, jti: str) -> bool:
        async with self._pool.acquire() as conn:
            value = await conn.fetchval(
                f"SELECT revoked_at IS NOT NULL FROM {SVID_TABLE} WHERE graph = $1 AND jti = $2",
                self._graph, jti,
            )
        return bool(value)

    async def list_records(self, *, agent_id: str | None = None, limit: int = 100) -> list[SvidRecord]:
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(
                f"""
                SELECT id, jti, agent_id, run_id, spiffe_id, serial, predecessor_jti,
                       issued_at, expires_at, revoked_at, revoked_by, revocation_reason
                  FROM {SVID_TABLE}
                 WHERE graph = $1 AND ($2::text IS NULL OR agent_id = $2)
              ORDER BY issued_at DESC
                 LIMIT $3
                """,
                self._graph, agent_id, limit,
            )
        return [_record(row) for row in rows]


def _record(row: asyncpg.Record) -> SvidRecord:
    return SvidRecord(
        id=row["id"], jti=row["jti"], agent_id=row["agent_id"], run_id=row["run_id"],
        spiffe_id=row["spiffe_id"], serial=row["serial"], predecessor_jti=row["predecessor_jti"],
        issued_at=_iso(row["issued_at"]), expires_at=_iso(row["expires_at"]),
        revoked_at=_iso(row["revoked_at"]) if row["revoked_at"] else None,
        revoked_by=row["revoked_by"], revocation_reason=row["revocation_reason"],
    )


class InMemorySvidStore:
    """For tests and the fixture stack. No pool, no graph scoping -- one tenant, in a
    dict, the identical shape `InMemoryArtefactStore` already sets."""

    def __init__(self) -> None:
        self._records: dict[str, SvidRecord] = {}

    async def record(self, record: SvidRecord) -> SvidRecord:
        self._records[record.jti] = record
        return record

    async def revoke(self, jti: str, *, reason: str, revoked_by: str) -> SvidRecord:
        cleaned = reason.strip()
        if len(cleaned) < MIN_REVOCATION_REASON_LENGTH:
            raise WorkloadIdentityRequestError(
                f"a revocation needs a reason of at least {MIN_REVOCATION_REASON_LENGTH} "
                f"characters; it is the record of why an identity was cut off"
            )
        existing = self._records.get(jti)
        if existing is None:
            raise WorkloadIdentityRequestError(f"no SVID record with jti '{jti}'")
        updated = replace(
            existing, revoked_at=_iso(datetime.now(UTC)),
            revoked_by=revoked_by, revocation_reason=cleaned,
        )
        self._records[jti] = updated
        return updated

    async def is_revoked(self, jti: str) -> bool:
        record = self._records.get(jti)
        return record is not None and record.revoked_at is not None

    async def list_records(self, *, agent_id: str | None = None, limit: int = 100) -> list[SvidRecord]:
        records = [
            r for r in self._records.values() if agent_id is None or r.agent_id == agent_id
        ]
        records.sort(key=lambda r: r.issued_at, reverse=True)
        return records[:limit]


__all__ = [
    "DEFAULT_TRUST_DOMAIN",
    "DEFAULT_TTL_SECONDS",
    "MIN_REVOCATION_REASON_LENGTH",
    "InMemorySvidStore",
    "LocalWorkloadIdentityProvider",
    "PostgresSvidStore",
    "Svid",
    "SvidClaims",
    "SvidRecord",
    "SvidStore",
    "WorkloadIdentityError",
    "WorkloadIdentityProvider",
    "WorkloadIdentityRequestError",
    "record_from_svid",
    "spiffe_id_for",
]
