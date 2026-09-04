"""Provenance records, and re-materialising the context one describes.

Specification §4.2 and §16.4: "an auditor can ask 'what did the model see?' and be shown
the context hash and the prompt hash, **with the context reproducible from the graph at
that version**." S1.3.2 is that last clause.

**The addition to §4.2.** The record as printed in §4.2 carries ``context_hash`` in its
``inputs`` block but no graph version. A hash without one is not reproducible: the graph
moves, and re-materialising the same contract for the same subject a week later gives a
different document and a different hash, with no way to tell a genuine mismatch from an
ordinary re-harvest. So ``inputs`` gains ``graph_version`` — an event offset — and that is
what makes the record verifiable rather than descriptive. Recorded as a declared extension
in ADR 0009.

**Verification is a re-materialisation, not a lookup.** Nothing stores the document. The
verifier re-runs the same assembler over the graph as it stood at the recorded offset and
compares the hash it computes with the one the record claims. That is what makes the answer
evidence: a stored copy would only prove that a copy was stored.

**Where this lives.** §5.2 gives provenance linkage to artefact-svc, which does not exist.
The verification does belong here — it needs the assembler and the event stream — so the
record is stored here for now and the verifier takes a *claim* rather than a record id
underneath, so that moving the store later changes one adapter and not the audit path.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from enum import Enum
from typing import Any, Protocol

import asyncpg

from .context import ContextAssembler, ContractName
from .ids import new_ulid

logger = logging.getLogger(__name__)

PROVENANCE_TABLE = "public.provenance"


class AgentMode(str, Enum):
    """Specification §8.1: the four modes every unit of agent output is produced in."""

    DETERMINISTIC = "DETERMINISTIC"
    ASSISTED = "ASSISTED"
    GENERATED_PROVED = "GENERATED_PROVED"
    HUMAN = "HUMAN"


class VerificationOutcome(str, Enum):
    MATCH = "MATCH"
    """The context re-materialised at the recorded version hashes to the recorded value."""

    MISMATCH = "MISMATCH"
    """It re-materialised, and hashes to something else. The record is wrong about what
    the agent saw, or the stream has been tampered with. Either way it is a finding."""

    UNVERIFIABLE = "UNVERIFIABLE"
    """It could not be re-materialised at all — the subject did not exist at that version,
    the contract is gone, or the version is outside what the platform still holds. Not the
    same as a mismatch, and reported separately so the two are never conflated."""


@dataclass(frozen=True, slots=True)
class ProvenanceRecord:
    """§4.2, plus the graph version that makes it reproducible."""

    id: str
    artefact_kind: str
    artefact_ref: str
    artefact_content_hash: str
    agent: str
    agent_version: str
    mode: AgentMode
    contract: ContractName
    subject_id: str
    context_hash: str
    graph_version: int
    prompt_hash: str | None = None
    model: str | None = None
    tokens_in: int | None = None
    tokens_out: int | None = None
    confidence: float | None = None
    pattern_ref: str | None = None
    supersedes_id: str | None = None
    provider: str | None = None
    """The model call's own provider — 'anthropic', 'azure_openai', ... Story S5.3.1:
    §4.2's own worked ``model_call`` block names it alongside ``model``; nothing before
    that story ever recorded a model call at all, so this stayed absent until then."""
    gateway_request_id: str | None = None
    """§5.5's own Model Gateway request id (``model_call.gateway_request`` in §4.2's
    example) — the correlation id an operator would use to find this call in the
    gateway's own logs. Story S5.3.1; null wherever ``model`` is null, the same footing
    every other ``model_call``-only field already has."""
    temperature: float | None = None
    """§5.4/§9.4: "Temperature is 0... for all generation paths" — recorded per call
    rather than assumed, so a provenance record states the policy it actually ran under
    instead of a reader having to trust it never changed. Story S5.3.1."""
    created_by: str = "unknown"
    created_at: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "artefact": {
                "kind": self.artefact_kind,
                "ref": self.artefact_ref,
                "content_hash": self.artefact_content_hash,
            },
            "produced_by": {"agent": self.agent, "agent_version": self.agent_version},
            "mode": self.mode.value,
            "inputs": {
                "contract": self.contract.value,
                "subject_ref": self.subject_id,
                "context_hash": self.context_hash,
                "graph_version": self.graph_version,
                "pattern_ref": self.pattern_ref,
            },
            "model_call": None
            if self.model is None
            else {
                "gateway_request": self.gateway_request_id,
                "provider": self.provider,
                "model": self.model,
                "prompt_hash": self.prompt_hash,
                "temperature": self.temperature,
                "tokens_in": self.tokens_in,
                "tokens_out": self.tokens_out,
            },
            "confidence": self.confidence,
            "supersedes": self.supersedes_id,
            "created_by": self.created_by,
            "created_at": self.created_at,
        }


@dataclass(frozen=True, slots=True)
class Verification:
    """The answer to "was the context this record describes really what the agent saw?"."""

    outcome: VerificationOutcome
    record_id: str | None
    contract: str
    subject_id: str
    graph_version: int
    claimed_hash: str
    recomputed_hash: str | None = None
    size_bytes: int | None = None
    node_count: int | None = None
    detail: str = ""
    document: dict[str, Any] | None = None
    differences: list[str] = field(default_factory=list)

    @property
    def matched(self) -> bool:
        return self.outcome is VerificationOutcome.MATCH

    def as_dict(self, *, include_document: bool = False) -> dict[str, Any]:
        out: dict[str, Any] = {
            "outcome": self.outcome.value,
            "matched": self.matched,
            "record_id": self.record_id,
            "contract": self.contract,
            "subject_id": self.subject_id,
            "graph_version": self.graph_version,
            "claimed_context_hash": self.claimed_hash,
            "recomputed_context_hash": self.recomputed_hash,
            "size_bytes": self.size_bytes,
            "node_count": self.node_count,
            "detail": self.detail,
        }
        if self.differences:
            out["differences"] = self.differences
        if include_document:
            out["document"] = self.document
        return out


class ProvenanceStore(Protocol):
    async def record(self, provenance: ProvenanceRecord) -> ProvenanceRecord: ...

    async def get(self, provenance_id: str) -> ProvenanceRecord | None: ...

    async def for_subject(
        self, subject_id: str, *, limit: int = 50
    ) -> list[ProvenanceRecord]: ...


class ContextVerifier:
    """Re-materialises the context a claim describes and compares the hash.

    Takes an assembler factory rather than an assembler: each verification needs one bound
    to a *different* historical reader, and the factory is the seam where "the graph at
    version n" is supplied.
    """

    def __init__(
        self,
        assembler_at: Any,
        *,
        current_version: Any,
    ) -> None:
        self._assembler_at = assembler_at
        """``async (version: int) -> ContextAssembler``. Async because building the view of
        a past version is a read in some implementations, not just a construction."""

        self._current_version = current_version
        """``() -> Awaitable[int]``, so a version beyond the record can be refused."""

    async def verify(
        self,
        *,
        contract: ContractName,
        subject_id: str,
        graph_version: int,
        claimed_hash: str,
        record_id: str | None = None,
        include_document: bool = False,
    ) -> Verification:
        """Compare a claimed context hash against the graph as it was.

        Never raises for a failed verification. A mismatch and an unverifiable record are
        *findings*, and an auditor's tool that threw an exception on the interesting case
        would be one an auditor stops using.
        """
        latest = await self._current_version()
        if graph_version > latest:
            return Verification(
                outcome=VerificationOutcome.UNVERIFIABLE,
                record_id=record_id,
                contract=contract.value,
                subject_id=subject_id,
                graph_version=graph_version,
                claimed_hash=claimed_hash,
                detail=(
                    f"the record cites graph version {graph_version}, but this graph has "
                    f"only reached {latest}. A record cannot describe a future state; "
                    f"either it came from another graph or it has been altered."
                ),
            )

        assembler: ContextAssembler = await self._assembler_at(graph_version)
        try:
            assembled = await assembler.assemble(contract, subject_id)
        except Exception as exc:
            return Verification(
                outcome=VerificationOutcome.UNVERIFIABLE,
                record_id=record_id,
                contract=contract.value,
                subject_id=subject_id,
                graph_version=graph_version,
                claimed_hash=claimed_hash,
                detail=(
                    f"the context could not be re-materialised at version {graph_version}: "
                    f"{type(exc).__name__}: {exc}"
                ),
            )

        matched = assembled.context_hash == claimed_hash
        return Verification(
            outcome=VerificationOutcome.MATCH if matched else VerificationOutcome.MISMATCH,
            record_id=record_id,
            contract=contract.value,
            subject_id=subject_id,
            graph_version=graph_version,
            claimed_hash=claimed_hash,
            recomputed_hash=assembled.context_hash,
            size_bytes=assembled.size_bytes,
            node_count=assembled.node_count,
            document=assembled.document if include_document else None,
            detail=(
                "the context re-materialised at the recorded version hashes to the "
                "recorded value."
                if matched
                else (
                    "the context re-materialised at the recorded version hashes to a "
                    "different value. The record does not describe what this graph would "
                    "have given the agent."
                )
            ),
        )

    async def verify_record(
        self, record: ProvenanceRecord, *, include_document: bool = False
    ) -> Verification:
        return await self.verify(
            contract=record.contract,
            subject_id=record.subject_id,
            graph_version=record.graph_version,
            claimed_hash=record.context_hash,
            record_id=record.id,
            include_document=include_document,
        )


# --------------------------------------------------------------------------- postgres


class PostgresProvenanceStore:
    """Provenance in PostgreSQL. The §21 ``provenance`` table, plus ``graph_version``."""

    def __init__(self, pool: asyncpg.Pool, *, graph_name: str) -> None:
        self._pool = pool
        self._graph = graph_name

    async def record(self, provenance: ProvenanceRecord) -> ProvenanceRecord:
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                f"""
                INSERT INTO {PROVENANCE_TABLE}
                    (id, graph, artefact_kind, artefact_ref, artefact_content_hash,
                     agent, agent_version, mode, contract, subject_id, context_hash,
                     graph_version, prompt_hash, model, tokens_in, tokens_out, confidence,
                     pattern_ref, supersedes_id, provider, gateway_request_id, temperature,
                     created_by)
                VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13, $14, $15,
                        $16, $17, $18, $19, $20, $21, $22, $23)
             RETURNING *
                """,
                provenance.id,
                self._graph,
                provenance.artefact_kind,
                provenance.artefact_ref,
                provenance.artefact_content_hash,
                provenance.agent,
                provenance.agent_version,
                provenance.mode.value,
                provenance.contract.value,
                provenance.subject_id,
                provenance.context_hash,
                provenance.graph_version,
                provenance.prompt_hash,
                provenance.model,
                provenance.tokens_in,
                provenance.tokens_out,
                provenance.confidence,
                provenance.pattern_ref,
                provenance.supersedes_id,
                provenance.provider,
                provenance.gateway_request_id,
                provenance.temperature,
                provenance.created_by,
            )
        return _from_row(row)

    async def get(self, provenance_id: str) -> ProvenanceRecord | None:
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                f"SELECT * FROM {PROVENANCE_TABLE} WHERE graph = $1 AND id = $2",
                self._graph,
                provenance_id,
            )
        return _from_row(row) if row else None

    async def for_subject(
        self, subject_id: str, *, limit: int = 50
    ) -> list[ProvenanceRecord]:
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(
                f"SELECT * FROM {PROVENANCE_TABLE} WHERE graph = $1 AND subject_id = $2 "
                f"ORDER BY created_at DESC LIMIT $3",
                self._graph,
                subject_id,
                limit,
            )
        return [_from_row(row) for row in rows]


class InMemoryProvenanceStore:
    """The same contract without a database."""

    def __init__(self) -> None:
        self.records: dict[str, ProvenanceRecord] = {}

    async def record(self, provenance: ProvenanceRecord) -> ProvenanceRecord:
        stored = (
            provenance
            if provenance.created_at
            else _with_created_at(provenance, _now())
        )
        self.records[stored.id] = stored
        return stored

    async def get(self, provenance_id: str) -> ProvenanceRecord | None:
        return self.records.get(provenance_id)

    async def for_subject(
        self, subject_id: str, *, limit: int = 50
    ) -> list[ProvenanceRecord]:
        matching = [r for r in self.records.values() if r.subject_id == subject_id]
        return matching[:limit]


def new_record(**kwargs: Any) -> ProvenanceRecord:
    """A record with an id and a creation time, from the caller's facts."""
    kwargs.setdefault("id", f"prov_{new_ulid()}")
    kwargs.setdefault("created_at", _now())
    return ProvenanceRecord(**kwargs)


def _with_created_at(record: ProvenanceRecord, moment: str) -> ProvenanceRecord:
    return replace(record, created_at=moment)


def _from_row(row: asyncpg.Record) -> ProvenanceRecord:
    return ProvenanceRecord(
        id=row["id"],
        artefact_kind=row["artefact_kind"],
        artefact_ref=row["artefact_ref"],
        artefact_content_hash=row["artefact_content_hash"],
        agent=row["agent"],
        agent_version=row["agent_version"],
        mode=AgentMode(row["mode"]),
        contract=ContractName(row["contract"]),
        subject_id=row["subject_id"],
        context_hash=row["context_hash"],
        graph_version=row["graph_version"],
        prompt_hash=row["prompt_hash"],
        model=row["model"],
        tokens_in=row["tokens_in"],
        tokens_out=row["tokens_out"],
        confidence=row["confidence"],
        pattern_ref=row["pattern_ref"],
        supersedes_id=row["supersedes_id"],
        provider=row["provider"],
        gateway_request_id=row["gateway_request_id"],
        temperature=row["temperature"],
        created_by=row["created_by"],
        created_at=_iso(row["created_at"]),
    )


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _iso(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, str):
        return value
    rendered: str = value.astimezone(UTC).isoformat(timespec="milliseconds")
    return rendered.replace("+00:00", "Z")


__all__ = [
    "PROVENANCE_TABLE",
    "AgentMode",
    "ContextVerifier",
    "InMemoryProvenanceStore",
    "PostgresProvenanceStore",
    "ProvenanceRecord",
    "ProvenanceStore",
    "Verification",
    "VerificationOutcome",
    "new_record",
]
