"""The artefact store — S2.4.2, and every later story that produces a binary artefact.

    "Images are stored in the artefact store and linked to the MU; they are never sent to a
    model endpoint." — S2.4.2

**Why a table rather than the artefact store.** §5.2 gives object storage and content
addressing to ``artefact-svc``, which does not exist — the same position provenance records
were in at S1.3.2 and conformance reports at S2.1.2, and the same answer: the record lives
here behind a port, content-addressed, so relocating it to a real object store later changes
one adapter and not the callers. See ADR 0009's and ADR 0014's precedent.

**Why one store and not one for screenshots.** ``kind`` names what an artefact is
("visual_capture" today; an evidence bundle at E7, a PBIR page thumbnail at E6). The shape a
binary artefact needs — content, a hash, a size, who produced it, what it is linked to — does
not change with what is inside it, and a second table per kind would be the same table typed
out twice.

**The link to the Migration Unit is a name, not a foreign key.** E3 has not created an MU yet
— there is no table to reference. ``mu_ref`` is accepted as the caller states it, the same way
`migration_units.py` treats an MU id as an opaque string the control plane will define. Until
then, the workbook LUID is the reasonable stand-in: §3.1 makes an MU "one source workbook and
everything the platform produces for it", so a workbook and its (future) MU share an identity
in every way that matters to this store.

**"Never sent to a model endpoint" is enforced by what a record can say, not by a runtime
check.** Nothing in this codebase today assembles model context from anything but the estate
graph (`context/`), and this module is not imported there. The stronger guarantee is
structural: `ArtefactRecord` — the shape returned by `get()`, the shape a future context
contract could plausibly reference — never carries the bytes. Only `content()` does, and it
exists for exactly one purpose: handing a human viewer (the console's own `<img>` tag) what
they asked to see. A contract that included an `ArtefactRecord` could not leak an image
through it even by accident.
"""

from __future__ import annotations

import hashlib
import logging
from dataclasses import dataclass
from typing import Any, Protocol

import asyncpg

from .ids import new_ulid

logger = logging.getLogger(__name__)

ARTEFACT_TABLE = "public.artefacts"


class ArtefactError(Exception):
    """The artefact could not be stored or read."""


@dataclass(frozen=True, slots=True)
class ArtefactRecord:
    """Everything about a stored artefact except its bytes.

    Deliberately without them — see this module's docstring. `content_hash` is what stands in
    for the bytes wherever they need to be referenced, matching how `ProvenanceRecord` already
    identifies an artefact by content hash rather than by holding a copy.
    """

    id: str
    kind: str
    mu_ref: str
    case_id: str
    content_hash: str
    media_type: str
    size_bytes: int
    width: int | None = None
    height: int | None = None
    adapter_name: str | None = None
    adapter_version: str | None = None
    interface_version: str | None = None
    recorded_by: str = "unknown"
    recorded_at: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "kind": self.kind,
            "mu_ref": self.mu_ref,
            "case_id": self.case_id,
            "content_hash": self.content_hash,
            "media_type": self.media_type,
            "size_bytes": self.size_bytes,
            "width": self.width,
            "height": self.height,
            "produced_by": {
                "adapter": self.adapter_name,
                "adapter_version": self.adapter_version,
                "interface_version": self.interface_version,
            },
            "recorded_by": self.recorded_by,
            "recorded_at": self.recorded_at,
        }


class ArtefactStore(Protocol):
    async def store(
        self,
        *,
        kind: str,
        mu_ref: str,
        case_id: str,
        content: bytes,
        media_type: str,
        width: int | None = None,
        height: int | None = None,
        adapter_name: str | None = None,
        adapter_version: str | None = None,
        interface_version: str | None = None,
        created_by: str,
    ) -> ArtefactRecord: ...

    async def get(self, artefact_id: str) -> ArtefactRecord | None: ...

    async def content(self, artefact_id: str) -> bytes | None:
        """The bytes. Called from exactly one place: the console-facing route that serves
        them to a human viewer. Never call this from anything that assembles model context."""
        ...

    async def for_mu(
        self, mu_ref: str, *, kind: str | None = None, limit: int = 50
    ) -> list[ArtefactRecord]: ...


def _content_hash(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


# --------------------------------------------------------------------------- stores


class PostgresArtefactStore:
    def __init__(self, pool: asyncpg.Pool, *, graph_name: str) -> None:
        self._pool = pool
        self._graph = graph_name

    async def store(
        self,
        *,
        kind: str,
        mu_ref: str,
        case_id: str,
        content: bytes,
        media_type: str,
        width: int | None = None,
        height: int | None = None,
        adapter_name: str | None = None,
        adapter_version: str | None = None,
        interface_version: str | None = None,
        created_by: str,
    ) -> ArtefactRecord:
        if not content:
            raise ArtefactError("an artefact with no bytes is not an artefact")
        artefact_id = f"af_{new_ulid()}"
        content_hash = _content_hash(content)
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                f"""
                INSERT INTO {ARTEFACT_TABLE}
                    (id, graph, kind, mu_ref, case_id, content_hash, media_type, size_bytes,
                     width, height, adapter_name, adapter_version, interface_version,
                     content, recorded_by)
                VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13, $14, $15)
             RETURNING id, kind, mu_ref, case_id, content_hash, media_type, size_bytes,
                       width, height, adapter_name, adapter_version, interface_version,
                       recorded_by, recorded_at
                """,
                artefact_id,
                self._graph,
                kind,
                mu_ref,
                case_id,
                content_hash,
                media_type,
                len(content),
                width,
                height,
                adapter_name,
                adapter_version,
                interface_version,
                content,
                created_by,
            )
        assert row is not None
        return _record(row)

    async def get(self, artefact_id: str) -> ArtefactRecord | None:
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                f"""
                SELECT id, kind, mu_ref, case_id, content_hash, media_type, size_bytes,
                       width, height, adapter_name, adapter_version, interface_version,
                       recorded_by, recorded_at
                  FROM {ARTEFACT_TABLE}
                 WHERE graph = $1 AND id = $2
                """,
                self._graph,
                artefact_id,
            )
        return _record(row) if row else None

    async def content(self, artefact_id: str) -> bytes | None:
        async with self._pool.acquire() as conn:
            value = await conn.fetchval(
                f"SELECT content FROM {ARTEFACT_TABLE} WHERE graph = $1 AND id = $2",
                self._graph,
                artefact_id,
            )
        return bytes(value) if value is not None else None

    async def for_mu(
        self, mu_ref: str, *, kind: str | None = None, limit: int = 50
    ) -> list[ArtefactRecord]:
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(
                f"""
                SELECT id, kind, mu_ref, case_id, content_hash, media_type, size_bytes,
                       width, height, adapter_name, adapter_version, interface_version,
                       recorded_by, recorded_at
                  FROM {ARTEFACT_TABLE}
                 WHERE graph = $1 AND mu_ref = $2 AND ($3::text IS NULL OR kind = $3)
              ORDER BY recorded_at DESC
                 LIMIT $4
                """,
                self._graph,
                mu_ref,
                kind,
                limit,
            )
        return [_record(row) for row in rows]


def _record(row: asyncpg.Record) -> ArtefactRecord:
    recorded_at = row["recorded_at"]
    return ArtefactRecord(
        id=row["id"],
        kind=row["kind"],
        mu_ref=row["mu_ref"],
        case_id=row["case_id"],
        content_hash=row["content_hash"],
        media_type=row["media_type"],
        size_bytes=row["size_bytes"],
        width=row["width"],
        height=row["height"],
        adapter_name=row["adapter_name"],
        adapter_version=row["adapter_version"],
        interface_version=row["interface_version"],
        recorded_by=row["recorded_by"],
        recorded_at=recorded_at.isoformat() if recorded_at else None,
    )


class InMemoryArtefactStore:
    """For tests and the fixture stack. No pool, no graph scoping — one tenant, in a dict."""

    def __init__(self) -> None:
        self._records: dict[str, ArtefactRecord] = {}
        self._content: dict[str, bytes] = {}

    async def store(
        self,
        *,
        kind: str,
        mu_ref: str,
        case_id: str,
        content: bytes,
        media_type: str,
        width: int | None = None,
        height: int | None = None,
        adapter_name: str | None = None,
        adapter_version: str | None = None,
        interface_version: str | None = None,
        created_by: str,
    ) -> ArtefactRecord:
        if not content:
            raise ArtefactError("an artefact with no bytes is not an artefact")
        record = ArtefactRecord(
            id=f"af_{new_ulid()}",
            kind=kind,
            mu_ref=mu_ref,
            case_id=case_id,
            content_hash=_content_hash(content),
            media_type=media_type,
            size_bytes=len(content),
            width=width,
            height=height,
            adapter_name=adapter_name,
            adapter_version=adapter_version,
            interface_version=interface_version,
            recorded_by=created_by,
            recorded_at=None,
        )
        self._records[record.id] = record
        self._content[record.id] = content
        return record

    async def get(self, artefact_id: str) -> ArtefactRecord | None:
        return self._records.get(artefact_id)

    async def content(self, artefact_id: str) -> bytes | None:
        return self._content.get(artefact_id)

    async def for_mu(
        self, mu_ref: str, *, kind: str | None = None, limit: int = 50
    ) -> list[ArtefactRecord]:
        matches = [
            record
            for record in self._records.values()
            if record.mu_ref == mu_ref and (kind is None or record.kind == kind)
        ]
        return matches[:limit]
