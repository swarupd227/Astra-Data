"""Programme scope decisions — re-tier, withdraw, reinstate.

S1.4.1's Programme Manager actions, and §15.2's rule that every action is a record with a
required reason.

The tiers are §3.1's: an assessment produces one, and a joint review can change it. Nothing
assesses yet (E3/F3.1), so a decision recorded now carries ``from_value: null`` — the
programme manager is declaring a tier rather than re-tiering one, and the record says which.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import Enum
from typing import Any, Protocol

import asyncpg

from .ids import new_ulid

logger = logging.getLogger(__name__)

SCOPE_TABLE = "public.scope_decision"

#: Spec §3.1. Held as a closed set because a tier drives the generation route (§8.2) and a
#: free-text tier would be a routing decision nobody could act on.
TIERS: tuple[str, ...] = ("SIMPLE", "MODERATE", "COMPLEX", "REDESIGN")

#: §15.2 requires a reason, and a reason of "n/a" is not one. Ten characters is the
#: shortest thing that can be a sentence fragment somebody will still understand in a year.
MIN_REASON = 10


class DecisionKind(str, Enum):
    RE_TIER = "RE_TIER"
    WITHDRAW = "WITHDRAW"
    REINSTATE = "REINSTATE"


class ScopeError(ValueError):
    """The decision as described cannot be recorded."""


@dataclass(frozen=True, slots=True)
class ScopeDecision:
    """One recorded judgement about one workbook."""

    id: str
    workbook_id: str
    kind: DecisionKind
    reason: str
    decided_by: str
    from_value: str | None = None
    to_value: str | None = None
    decided_at: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "workbook_id": self.workbook_id,
            "kind": self.kind.value,
            "from": self.from_value,
            "to": self.to_value,
            "reason": self.reason,
            "decided_by": self.decided_by,
            "decided_at": self.decided_at,
        }


@dataclass(frozen=True, slots=True)
class ScopeState:
    """What the decisions add up to for one workbook."""

    tier: str | None = None
    withdrawn: bool = False
    withdrawn_reason: str | None = None
    tier_reason: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "tier": self.tier,
            "tier_reason": self.tier_reason,
            "withdrawn": self.withdrawn,
            "withdrawn_reason": self.withdrawn_reason,
        }


class ScopeStore(Protocol):
    async def decide(self, decision: ScopeDecision) -> ScopeDecision: ...

    async def history(self, workbook_id: str, *, limit: int = 50) -> list[ScopeDecision]: ...

    async def states(self) -> dict[str, ScopeState]: ...


def new_decision(
    *,
    workbook_id: str,
    kind: DecisionKind,
    reason: str,
    decided_by: str,
    from_value: str | None = None,
    to_value: str | None = None,
) -> ScopeDecision:
    reason = reason.strip()
    if len(reason) < MIN_REASON:
        raise ScopeError(
            f"a scope decision needs a reason of at least {MIN_REASON} characters; this "
            f"record outlives everyone who remembers the conversation"
        )
    if kind is DecisionKind.RE_TIER and to_value not in TIERS:
        raise ScopeError(f"tier must be one of {', '.join(TIERS)}, got {to_value!r}")
    return ScopeDecision(
        id=f"scope_{new_ulid()}",
        workbook_id=workbook_id,
        kind=kind,
        reason=reason,
        decided_by=decided_by,
        from_value=from_value,
        to_value=to_value,
        decided_at=_now(),
    )


def fold(decisions: list[ScopeDecision]) -> ScopeState:
    """The current state from the decisions, oldest first.

    A fold rather than a stored status: the decisions are the record, and a status column
    beside them could disagree with them. Re-tiering a withdrawn workbook leaves it
    withdrawn — the two are separate judgements and neither implies the other.
    """
    tier: str | None = None
    tier_reason: str | None = None
    withdrawn = False
    withdrawn_reason: str | None = None
    for decision in decisions:
        match decision.kind:
            case DecisionKind.RE_TIER:
                tier, tier_reason = decision.to_value, decision.reason
            case DecisionKind.WITHDRAW:
                withdrawn, withdrawn_reason = True, decision.reason
            case DecisionKind.REINSTATE:
                withdrawn, withdrawn_reason = False, None
    return ScopeState(
        tier=tier,
        tier_reason=tier_reason,
        withdrawn=withdrawn,
        withdrawn_reason=withdrawn_reason,
    )


class PostgresScopeStore:
    def __init__(self, pool: asyncpg.Pool, *, graph_name: str) -> None:
        self._pool = pool
        self._graph = graph_name

    async def decide(self, decision: ScopeDecision) -> ScopeDecision:
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                f"""
                INSERT INTO {SCOPE_TABLE}
                    (id, graph, workbook_id, kind, from_value, to_value, reason, decided_by)
                VALUES ($1, $2, $3, $4, $5, $6, $7, $8)
             RETURNING *
                """,
                decision.id,
                self._graph,
                decision.workbook_id,
                decision.kind.value,
                decision.from_value,
                decision.to_value,
                decision.reason,
                decision.decided_by,
            )
        return _from_row(row)

    async def history(self, workbook_id: str, *, limit: int = 50) -> list[ScopeDecision]:
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(
                f"SELECT * FROM {SCOPE_TABLE} WHERE graph = $1 AND workbook_id = $2 "
                f"ORDER BY decided_at LIMIT $3",
                self._graph,
                workbook_id,
                limit,
            )
        return [_from_row(row) for row in rows]

    async def states(self) -> dict[str, ScopeState]:
        """Every workbook's current scope state, in one query.

        The Explorer renders a thousand rows and each needs its tier and whether it is
        withdrawn; asking per row would be a thousand queries for a table that holds one
        row per decision anybody has ever made.
        """
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(
                f"SELECT * FROM {SCOPE_TABLE} WHERE graph = $1 ORDER BY workbook_id, decided_at",
                self._graph,
            )
        by_workbook: dict[str, list[ScopeDecision]] = {}
        for row in rows:
            decision = _from_row(row)
            by_workbook.setdefault(decision.workbook_id, []).append(decision)
        return {
            workbook_id: fold(decisions) for workbook_id, decisions in by_workbook.items()
        }


class InMemoryScopeStore:
    def __init__(self) -> None:
        self.decisions: list[ScopeDecision] = []

    async def decide(self, decision: ScopeDecision) -> ScopeDecision:
        self.decisions.append(decision)
        return decision

    async def history(self, workbook_id: str, *, limit: int = 50) -> list[ScopeDecision]:
        return [d for d in self.decisions if d.workbook_id == workbook_id][:limit]

    async def states(self) -> dict[str, ScopeState]:
        by_workbook: dict[str, list[ScopeDecision]] = {}
        for decision in self.decisions:
            by_workbook.setdefault(decision.workbook_id, []).append(decision)
        return {key: fold(value) for key, value in by_workbook.items()}


def _from_row(row: asyncpg.Record) -> ScopeDecision:
    return ScopeDecision(
        id=row["id"],
        workbook_id=row["workbook_id"],
        kind=DecisionKind(row["kind"]),
        reason=row["reason"],
        decided_by=row["decided_by"],
        from_value=row["from_value"],
        to_value=row["to_value"],
        decided_at=row["decided_at"]
        .astimezone(UTC)
        .isoformat(timespec="milliseconds")
        .replace("+00:00", "Z"),
    )


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")


__all__ = [
    "MIN_REASON",
    "SCOPE_TABLE",
    "TIERS",
    "DecisionKind",
    "InMemoryScopeStore",
    "PostgresScopeStore",
    "ScopeDecision",
    "ScopeError",
    "ScopeState",
    "ScopeStore",
    "fold",
    "new_decision",
]
