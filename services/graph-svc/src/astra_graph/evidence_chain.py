"""The Evidence Chain -- spec §4.5/§18.4, story S11.3.1, opens F11.3.

    "As an auditor, I want an append-only, hash-linked record of every state transition,
    gate decision, agent run, model call and verdict, so that the migration can be
    examined years later.

    Acceptance criteria:
    - Every CloudEvent is appended with prev_hash and hash; daily roots are computed and
      can be anchored externally (client-chosen: their own ledger, a timestamping
      service) as an option
    - Verification tool recomputes the chain and reports the first break; runs nightly
      and on demand
    - Retention configurable per tenant (default: programme lifetime + 7 years) with
      export before deletion"

**Scope, per three explicit decisions taken before any code was written.** Spec §5.2
names a whole separate microservice for this ("evidence-svc: append-only chain,
hashing, anchoring, export | per tenant") that does not exist anywhere in this codebase
-- built here, inside graph-svc, the same "the existing service, not a new deployable"
precedent ADR 0081 already set for a story that could equally have argued for one.
External anchoring is a real, disclosed-not-connected interface (`ChainAnchor`) with a
`NullChainAnchor` default -- daily roots are always computed regardless of whether any
anchor is configured; a live RFC 3161 client or a client's own ledger integration is
real future work, not built here, since neither has a client-approved target to reach
in this environment.

**The chain is computed by a separate advancer, never inline on a graph write.** The
existing outbox (`estate_event`) is written inside the same transaction as *every* graph
mutation this service makes -- the hottest code path this codebase has. Adding
`prev_hash`/`hash` there would need a per-graph lock to keep concurrent writes correctly
ordered, adding real contention to a mature, heavily-tested path for a benefit ("the
chain updates the instant an event is written") the AC does not actually ask for --
its own second bullet already anticipates a distinct verification process ("runs nightly
and on demand"), the same shape this codebase's own nightly replay-verification job
(`tools/verify_replay.py`, `.github/workflows/nightly.yml`) already has. `advance_chain`
below is that same shape for the evidence chain: an idempotent, `seq`-ordered walk that
can run as a CLI tool, a nightly cron step, or an on-demand HTTP call, touching no
existing write path at all.

**Three existing tables, not a fourth event type threaded through five call sites.**
Research confirmed "state transition"/"gate decision"/"verdict" already produce real
`estate_event` rows today (every `GateDecision`/`Verdict`/`ParityRun` node write goes
through the identical `GraphWriter.write_nodes` chokepoint every other node type does);
"agent run" and "model call" do not -- an SVID issuance (`public.svid_record`, S11.1.2)
and a model call (`public.provenance`, S5.3.1) are both already real, durable,
per-tenant facts, just not routed through the outbox. Rather than adding a new
`EventType` and wiring `append_event` calls into `workload_identity.py`'s three SVID-
issuance call sites and `provenance.py`'s own record path -- each already a small,
delicate, best-effort block inside otherwise-unrelated production code -- this module
reads all three tables directly, read-only, and chains them together. This keeps every
existing write path (`writes.py`, `workload_identity.py`, `provenance.py`,
`harvest/scheduler.py`, `regression.py`, `api/routes_g2.py`) completely untouched: the
entire evidence chain is new, isolated code with nothing upstream of it to break.

**Ordering is per-source batches, not strict wall-clock interleaving across sources.**
Each `advance_chain` call processes all newly-available `estate_event` rows (in `seq`
order), then all newly-available `svid_record` rows (in `id` order), then all newly-
available `provenance` rows (in `id` order) -- every id in this codebase is a ULID
(`new_ulid()`, or a short fixed prefix plus one), which is lexicographically sortable by
creation time, so within one source the append order is real chronological order. Across
sources, one advance's own three batches are not merged into strict wall-clock order --
a disclosed simplification, not a claim that the chain proves cross-source ordering
finer than "which batch of one nightly/on-demand run an entry belongs to." What the
chain does prove, exactly as the AC asks: that nothing chained has been altered or
removed since it was chained.

**`svid_record`'s own revocation (an in-place `UPDATE`, ADR 0080's one deliberate
exception to append-only) is not re-chained.** Only the row's first-seen state (the
issuance) is ever chained -- a later revocation is a separate governance fact `Tenant &
Access`/`svid_record` already show directly; re-chaining an updated row would either
silently change what an already-chained entry's hash covers (breaking the chain's own
promise) or require detecting row mutation this module does not build. Disclosed, not
silently dropped.

**Daily roots bucket by `chained_at`, not by each entry's own `occurred_at`.** An entry's
`occurred_at` can be `time`/`issued_at`/`created_at` recorded by a different process at a
different moment than this advancer discovers and chains it -- bucketing a daily root by
`occurred_at` risks a "closed" day's root needing to be recomputed (changing an already-
anchored hash) if a late-arriving row surfaces with an old timestamp. `chained_at`
(`now()` at the moment this module inserts the row) is monotonically increasing by
construction, so a day is provably closed forever once that calendar day has passed --
the safe, disclosed choice over a subtly incorrect one.
"""

from __future__ import annotations

import hashlib
import json
import logging
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from typing import Any, Protocol

import asyncpg

from .context.canonical import canonical_json
from .ids import new_ulid

logger = logging.getLogger(__name__)

CHAIN_ENTRY_TABLE = "public.evidence_chain_entry"
DAILY_ROOT_TABLE = "public.evidence_daily_root"

#: A fixed, documented starting value -- the first real entry's own ``prev_hash``. Never
#: itself the output of a hash function, so it can never collide with a real one.
GENESIS_HASH = "0" * 64

#: How many rows one source contributes per ``advance_chain`` call, so one call over a
#: large backlog returns promptly rather than holding a connection indefinitely; the
#: caller (the CLI tool, or an HTTP route) calls again until nothing is left to chain.
BATCH_SIZE = 5_000


class EvidenceChainError(Exception):
    """The chain could not be advanced, verified or rooted for a real, stated reason."""


class ChainAnchorUnavailable(EvidenceChainError):
    """No external anchor is configured -- the honest default (spec §18.4's own "as an
    option"). Daily roots are still real and stored; only the external attestation is
    missing."""


def _entry_hash(prev_hash: str, payload: bytes) -> str:
    return hashlib.sha256(prev_hash.encode("utf-8") + payload).hexdigest()


def _category_for_estate_event(event_type: str, data: dict[str, Any]) -> str:
    node_type = data.get("type") if isinstance(data, dict) else None
    if node_type == "GateDecision":
        return "gate_decision"
    if node_type in ("Verdict", "ParityRun"):
        return "verdict"
    return "state_transition"


@dataclass(frozen=True, slots=True)
class ChainAdvanceResult:
    graph: str
    entries_added: int
    tip_seq: int
    tip_hash: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "graph": self.graph,
            "entries_added": self.entries_added,
            "tip_seq": self.tip_seq,
            "tip_hash": self.tip_hash,
        }


@dataclass(frozen=True, slots=True)
class ChainBreak:
    """The first place a recomputation disagrees with what is stored -- spec's own
    "reports the first break"."""

    chain_seq: int
    source_table: str
    source_id: str
    expected_hash: str
    stored_hash: str
    detail: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "chain_seq": self.chain_seq,
            "source_table": self.source_table,
            "source_id": self.source_id,
            "expected_hash": self.expected_hash,
            "stored_hash": self.stored_hash,
            "detail": self.detail,
        }


@dataclass(frozen=True, slots=True)
class ChainVerificationResult:
    graph: str
    entries_checked: int
    intact: bool
    first_break: ChainBreak | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "graph": self.graph,
            "entries_checked": self.entries_checked,
            "intact": self.intact,
            "first_break": self.first_break.as_dict() if self.first_break else None,
        }


@dataclass(frozen=True, slots=True)
class AnchorReceipt:
    """What an external anchor gave back for one daily root."""

    kind: str
    ref: str
    anchored_at: str


@dataclass(frozen=True, slots=True)
class DailyRoot:
    id: str
    graph: str
    day: str
    first_chain_seq: int
    last_chain_seq: int
    entry_count: int
    prev_root_hash: str
    root_hash: str
    computed_at: str
    anchor_kind: str | None = None
    anchor_ref: str | None = None
    anchored_at: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "day": self.day,
            "first_chain_seq": self.first_chain_seq,
            "last_chain_seq": self.last_chain_seq,
            "entry_count": self.entry_count,
            "root_hash": self.root_hash,
            "computed_at": self.computed_at,
            "anchor_kind": self.anchor_kind,
            "anchor_ref": self.anchor_ref,
            "anchored_at": self.anchored_at,
        }


class ChainAnchor(Protocol):
    """A client's own choice of external attestation for a daily root (spec §4.5/§18.4's
    own "anchored externally at the client's option": their own ledger, a timestamping
    service). Disclosed, not yet connected -- see this module's own docstring."""

    async def anchor(self, *, graph: str, day: str, root_hash: str) -> AnchorReceipt: ...


class NullChainAnchor:
    """The honest default: no external anchor is configured. Daily roots are real and
    stored regardless of whether this is ever replaced -- only the external attestation
    is missing until a client names a real target."""

    async def anchor(self, *, graph: str, day: str, root_hash: str) -> AnchorReceipt:
        raise ChainAnchorUnavailable(
            "no external anchor is configured for this tenant; the daily root is real "
            "and stored, but nothing has attested to it outside this deployment"
        )


# --------------------------------------------------------------------------- advancing


async def _tip(conn: asyncpg.Connection, graph: str) -> tuple[int, str]:
    row = await conn.fetchrow(
        f"SELECT chain_seq, hash FROM {CHAIN_ENTRY_TABLE} WHERE graph = $1 "
        f"ORDER BY chain_seq DESC LIMIT 1",
        graph,
    )
    return (row["chain_seq"], row["hash"]) if row else (0, GENESIS_HASH)


async def _watermark(conn: asyncpg.Connection, graph: str, source_table: str) -> str | None:
    """The highest ``source_id`` already chained for this ``(graph, source_table)`` --
    ``None`` means nothing from this source has been chained yet. Safe as a watermark
    only because every id chained is inserted in increasing order within its own source
    (see this module's own docstring on ULID ordering)."""
    value = await conn.fetchval(
        f"SELECT MAX(source_id) FROM {CHAIN_ENTRY_TABLE} WHERE graph = $1 AND source_table = $2",
        graph, source_table,
    )
    return str(value) if value is not None else None


async def _estate_event_candidates(
    conn: asyncpg.Connection, graph: str, after_seq: int
) -> list[dict[str, Any]]:
    rows = await conn.fetch(
        """SELECT seq, event_id, type, source, subject, element_kind, label, time,
                  principal, run_id, data
             FROM public.estate_event
            WHERE graph = $1 AND seq > $2
         ORDER BY seq
            LIMIT $3""",
        graph, after_seq, BATCH_SIZE,
    )
    out = []
    for row in rows:
        data = json.loads(row["data"]) if isinstance(row["data"], str) else dict(row["data"])
        out.append({
            "seq": row["seq"], "source_id": row["event_id"],
            "occurred_at": row["time"].isoformat() if hasattr(row["time"], "isoformat") else row["time"],
            "category": _category_for_estate_event(row["type"], data),
            "fields": {
                "type": row["type"], "source": row["source"], "subject": row["subject"],
                "element_kind": row["element_kind"], "label": row["label"],
                "principal": row["principal"], "run_id": row["run_id"], "data": data,
            },
        })
    return out


async def _svid_record_candidates(
    conn: asyncpg.Connection, graph: str, after_id: str | None
) -> list[dict[str, Any]]:
    rows = await conn.fetch(
        """SELECT id, jti, agent_id, run_id, spiffe_id, serial, predecessor_jti, issued_at
             FROM public.svid_record
            WHERE graph = $1 AND ($2::text IS NULL OR id > $2)
         ORDER BY id
            LIMIT $3""",
        graph, after_id, BATCH_SIZE,
    )
    return [
        {
            "source_id": row["id"], "occurred_at": row["issued_at"].isoformat(),
            "category": "agent_run",
            "fields": {
                "jti": row["jti"], "agent_id": row["agent_id"], "run_id": row["run_id"],
                "spiffe_id": row["spiffe_id"], "serial": row["serial"],
                "predecessor_jti": row["predecessor_jti"],
            },
        }
        for row in rows
    ]


async def _provenance_candidates(
    conn: asyncpg.Connection, graph: str, after_id: str | None
) -> list[dict[str, Any]]:
    rows = await conn.fetch(
        """SELECT id, agent, agent_version, contract, subject_id, context_hash,
                   prompt_hash, model, provider, tokens_in, tokens_out, created_by, created_at
             FROM public.provenance
            WHERE graph = $1 AND ($2::text IS NULL OR id > $2)
         ORDER BY id
            LIMIT $3""",
        graph, after_id, BATCH_SIZE,
    )
    return [
        {
            "source_id": row["id"],
            "occurred_at": row["created_at"].isoformat() if hasattr(row["created_at"], "isoformat") else str(row["created_at"]),
            "category": "model_call",
            "fields": {
                "agent": row["agent"], "agent_version": row["agent_version"],
                "contract": row["contract"], "subject_id": row["subject_id"],
                "context_hash": row["context_hash"], "prompt_hash": row["prompt_hash"],
                "model": row["model"], "provider": row["provider"],
                "tokens_in": row["tokens_in"], "tokens_out": row["tokens_out"],
                "created_by": row["created_by"],
            },
        }
        for row in rows
    ]


async def advance_chain(pool: asyncpg.Pool, graph_name: str) -> ChainAdvanceResult:
    """Hash-link every not-yet-chained row from ``estate_event``, ``svid_record`` and
    ``provenance`` onto this graph's own chain, in that order, up to ``BATCH_SIZE`` rows
    per source. Idempotent: calling this with nothing new to chain adds nothing and
    returns the current tip. Call it again if ``entries_added`` comes back at
    ``3 * BATCH_SIZE`` -- there may be more still queued."""
    async with pool.acquire() as conn, conn.transaction():
        tip_seq, tip_hash = await _tip(conn, graph_name)

        estate_seq_watermark = await conn.fetchval(
            f"SELECT COALESCE(MAX(seq), 0) FROM public.estate_event e "
            f"JOIN {CHAIN_ENTRY_TABLE} c ON c.graph = e.graph AND c.source_table = 'estate_event' "
            f"AND c.source_id = e.event_id WHERE e.graph = $1",
            graph_name,
        ) or 0
        svid_watermark = await _watermark(conn, graph_name, "svid_record")
        provenance_watermark = await _watermark(conn, graph_name, "provenance")

        batches: list[tuple[str, list[dict[str, Any]]]] = [
            ("estate_event", await _estate_event_candidates(conn, graph_name, estate_seq_watermark)),
            ("svid_record", await _svid_record_candidates(conn, graph_name, svid_watermark)),
            ("provenance", await _provenance_candidates(conn, graph_name, provenance_watermark)),
        ]

        rows_to_insert: list[tuple[Any, ...]] = []
        for source_table, candidates in batches:
            for candidate in candidates:
                tip_seq += 1
                prev_hash = tip_hash
                payload = canonical_json({
                    "source_table": source_table,
                    "source_id": candidate["source_id"],
                    "occurred_at": candidate["occurred_at"],
                    "category": candidate["category"],
                    "fields": candidate["fields"],
                })
                tip_hash = _entry_hash(prev_hash, payload)
                rows_to_insert.append((
                    new_ulid(), graph_name, tip_seq, source_table, candidate["source_id"],
                    candidate["category"], _parse_occurred_at(candidate["occurred_at"]),
                    prev_hash, tip_hash,
                ))

        if rows_to_insert:
            await conn.executemany(
                f"""INSERT INTO {CHAIN_ENTRY_TABLE}
                    (id, graph, chain_seq, source_table, source_id, category, occurred_at,
                     prev_hash, hash)
                    VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9)""",
                rows_to_insert,
            )

    return ChainAdvanceResult(
        graph=graph_name, entries_added=len(rows_to_insert), tip_seq=tip_seq, tip_hash=tip_hash,
    )


def _parse_occurred_at(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


# -------------------------------------------------------------------------- verifying


async def verify_chain(pool: asyncpg.Pool, graph_name: str) -> ChainVerificationResult:
    """Recompute every stored hash from its own source row and ``prev_hash``, and report
    the first place a recomputation disagrees -- the AC's own literal "recomputes the
    chain and reports the first break"."""
    async with pool.acquire() as conn:
        entries = await conn.fetch(
            f"""SELECT chain_seq, source_table, source_id, category, occurred_at,
                       prev_hash, hash
                  FROM {CHAIN_ENTRY_TABLE}
                 WHERE graph = $1
              ORDER BY chain_seq""",
            graph_name,
        )
        checked = 0
        expected_prev = GENESIS_HASH
        for entry in entries:
            checked += 1
            if entry["prev_hash"] != expected_prev:
                return ChainVerificationResult(
                    graph=graph_name, entries_checked=checked, intact=False,
                    first_break=ChainBreak(
                        chain_seq=entry["chain_seq"], source_table=entry["source_table"],
                        source_id=entry["source_id"], expected_hash=expected_prev,
                        stored_hash=entry["prev_hash"],
                        detail="this entry's own prev_hash does not match the previous "
                               "entry's real hash -- the chain has been altered or a "
                               "row was removed",
                    ),
                )
            fields = await _real_fields(conn, graph_name, entry["source_table"], entry["source_id"])
            if fields is None:
                return ChainVerificationResult(
                    graph=graph_name, entries_checked=checked, intact=False,
                    first_break=ChainBreak(
                        chain_seq=entry["chain_seq"], source_table=entry["source_table"],
                        source_id=entry["source_id"], expected_hash="(unavailable)",
                        stored_hash=entry["hash"],
                        detail=f"the source row {entry['source_table']}/{entry['source_id']} "
                               f"this entry was chained from no longer exists",
                    ),
                )
            payload = canonical_json({
                "source_table": entry["source_table"], "source_id": entry["source_id"],
                "occurred_at": entry["occurred_at"].isoformat(), "category": entry["category"],
                "fields": fields,
            })
            recomputed = _entry_hash(entry["prev_hash"], payload)
            if recomputed != entry["hash"]:
                return ChainVerificationResult(
                    graph=graph_name, entries_checked=checked, intact=False,
                    first_break=ChainBreak(
                        chain_seq=entry["chain_seq"], source_table=entry["source_table"],
                        source_id=entry["source_id"], expected_hash=recomputed,
                        stored_hash=entry["hash"],
                        detail="the recomputed hash does not match what is stored -- "
                               "either the source row or the chain entry itself changed "
                               "after it was chained",
                    ),
                )
            expected_prev = entry["hash"]

    return ChainVerificationResult(graph=graph_name, entries_checked=checked, intact=True)


async def _real_fields(
    conn: asyncpg.Connection, graph: str, source_table: str, source_id: str
) -> dict[str, Any] | None:
    if source_table == "estate_event":
        row = await conn.fetchrow(
            """SELECT type, source, subject, element_kind, label, principal, run_id, data
                 FROM public.estate_event WHERE graph = $1 AND event_id = $2""",
            graph, source_id,
        )
        if row is None:
            return None
        data = json.loads(row["data"]) if isinstance(row["data"], str) else dict(row["data"])
        return {
            "type": row["type"], "source": row["source"], "subject": row["subject"],
            "element_kind": row["element_kind"], "label": row["label"],
            "principal": row["principal"], "run_id": row["run_id"], "data": data,
        }
    if source_table == "svid_record":
        row = await conn.fetchrow(
            """SELECT jti, agent_id, run_id, spiffe_id, serial, predecessor_jti
                 FROM public.svid_record WHERE graph = $1 AND id = $2""",
            graph, source_id,
        )
        if row is None:
            return None
        return {
            "jti": row["jti"], "agent_id": row["agent_id"], "run_id": row["run_id"],
            "spiffe_id": row["spiffe_id"], "serial": row["serial"],
            "predecessor_jti": row["predecessor_jti"],
        }
    if source_table == "provenance":
        row = await conn.fetchrow(
            """SELECT agent, agent_version, contract, subject_id, context_hash, prompt_hash,
                      model, provider, tokens_in, tokens_out, created_by
                 FROM public.provenance WHERE graph = $1 AND id = $2""",
            graph, source_id,
        )
        if row is None:
            return None
        return {
            "agent": row["agent"], "agent_version": row["agent_version"],
            "contract": row["contract"], "subject_id": row["subject_id"],
            "context_hash": row["context_hash"], "prompt_hash": row["prompt_hash"],
            "model": row["model"], "provider": row["provider"],
            "tokens_in": row["tokens_in"], "tokens_out": row["tokens_out"],
            "created_by": row["created_by"],
        }
    raise EvidenceChainError(f"unknown chain source table {source_table!r}")


# ----------------------------------------------------------------------- daily roots


async def compute_daily_root(
    pool: asyncpg.Pool, graph_name: str, day: date, *, anchor: ChainAnchor | None = None
) -> DailyRoot | None:
    """The AC's own "daily roots are computed". Buckets by ``chained_at`` (see this
    module's own docstring for why), and only for a day strictly before today (UTC) --
    a day still in progress could still receive more entries, and a root once computed
    is never recomputed. Returns ``None`` if this day already has a root, or if nothing
    was chained on it at all."""
    today = datetime.now(UTC).date()
    if day >= today:
        raise EvidenceChainError(
            f"{day.isoformat()} is not yet a closed day (today is {today.isoformat()}); "
            f"a daily root is only ever computed for a day that has fully elapsed"
        )

    async with pool.acquire() as conn, conn.transaction():
        existing = await conn.fetchval(
            f"SELECT 1 FROM {DAILY_ROOT_TABLE} WHERE graph = $1 AND day = $2", graph_name, day,
        )
        if existing:
            return None

        day_start = datetime.combine(day, datetime.min.time(), tzinfo=UTC)
        day_end = day_start + timedelta(days=1)
        rows = await conn.fetch(
            f"""SELECT chain_seq, hash FROM {CHAIN_ENTRY_TABLE}
                 WHERE graph = $1 AND chained_at >= $2 AND chained_at < $3
              ORDER BY chain_seq""",
            graph_name, day_start, day_end,
        )
        if not rows:
            return None

        prev_root = await conn.fetchval(
            f"SELECT root_hash FROM {DAILY_ROOT_TABLE} WHERE graph = $1 "
            f"ORDER BY day DESC LIMIT 1",
            graph_name,
        ) or GENESIS_HASH

        digest = hashlib.sha256(prev_root.encode("utf-8"))
        for row in rows:
            digest.update(row["hash"].encode("utf-8"))
        root_hash = digest.hexdigest()

        anchor_kind: str | None = None
        anchor_ref: str | None = None
        anchored_at: str | None = None
        if anchor is not None:
            try:
                receipt = await anchor.anchor(graph=graph_name, day=day.isoformat(), root_hash=root_hash)
                anchor_kind, anchor_ref, anchored_at = receipt.kind, receipt.ref, receipt.anchored_at
            except ChainAnchorUnavailable:
                pass

        root_id = f"evroot_{new_ulid()}"
        row = await conn.fetchrow(
            f"""INSERT INTO {DAILY_ROOT_TABLE}
                (id, graph, day, first_chain_seq, last_chain_seq, entry_count,
                 prev_root_hash, root_hash, anchor_kind, anchor_ref, anchored_at)
                VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11)
             RETURNING computed_at""",
            root_id, graph_name, day, rows[0]["chain_seq"], rows[-1]["chain_seq"], len(rows),
            prev_root, root_hash, anchor_kind, anchor_ref,
            _parse_occurred_at(anchored_at) if anchored_at else None,
        )

    return DailyRoot(
        id=root_id, graph=graph_name, day=day.isoformat(),
        first_chain_seq=rows[0]["chain_seq"], last_chain_seq=rows[-1]["chain_seq"],
        entry_count=len(rows), prev_root_hash=prev_root, root_hash=root_hash,
        computed_at=row["computed_at"].isoformat(), anchor_kind=anchor_kind,
        anchor_ref=anchor_ref, anchored_at=anchored_at,
    )


async def list_daily_roots(pool: asyncpg.Pool, graph_name: str, *, limit: int = 100) -> list[DailyRoot]:
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            f"""SELECT id, day, first_chain_seq, last_chain_seq, entry_count,
                       prev_root_hash, root_hash, computed_at, anchor_kind, anchor_ref, anchored_at
                  FROM {DAILY_ROOT_TABLE}
                 WHERE graph = $1
              ORDER BY day DESC
                 LIMIT $2""",
            graph_name, limit,
        )
    return [
        DailyRoot(
            id=row["id"], graph=graph_name, day=row["day"].isoformat(),
            first_chain_seq=row["first_chain_seq"], last_chain_seq=row["last_chain_seq"],
            entry_count=row["entry_count"], prev_root_hash=row["prev_root_hash"],
            root_hash=row["root_hash"], computed_at=row["computed_at"].isoformat(),
            anchor_kind=row["anchor_kind"], anchor_ref=row["anchor_ref"],
            anchored_at=row["anchored_at"].isoformat() if row["anchored_at"] else None,
        )
        for row in rows
    ]


async def chain_status(pool: asyncpg.Pool, graph_name: str) -> dict[str, Any]:
    """What the Tenant & Access screen (and an auditor) reads about this graph's own
    chain -- the tip, and how many entries exist, without recomputing anything."""
    async with pool.acquire() as conn:
        tip_seq, tip_hash = await _tip(conn, graph_name)
        total = await conn.fetchval(
            f"SELECT count(*) FROM {CHAIN_ENTRY_TABLE} WHERE graph = $1", graph_name,
        )
        by_category_rows = await conn.fetch(
            f"SELECT category, count(*) AS n FROM {CHAIN_ENTRY_TABLE} WHERE graph = $1 "
            f"GROUP BY category",
            graph_name,
        )
    return {
        "tip_seq": tip_seq,
        "tip_hash": tip_hash if tip_seq else None,
        "total_entries": total or 0,
        "by_category": {row["category"]: row["n"] for row in by_category_rows},
    }


__all__ = [
    "BATCH_SIZE",
    "CHAIN_ENTRY_TABLE",
    "DAILY_ROOT_TABLE",
    "GENESIS_HASH",
    "AnchorReceipt",
    "ChainAdvanceResult",
    "ChainAnchor",
    "ChainAnchorUnavailable",
    "ChainBreak",
    "ChainVerificationResult",
    "DailyRoot",
    "EvidenceChainError",
    "NullChainAnchor",
    "advance_chain",
    "chain_status",
    "compute_daily_root",
    "list_daily_roots",
    "verify_chain",
]
