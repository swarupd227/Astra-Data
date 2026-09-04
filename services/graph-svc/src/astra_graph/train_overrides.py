"""Move, resequence and WIP-limit a train — a Programme Manager's edits to a train
proposal. Story S3.2.2.

    "As a programme manager, I want a Wave Board where I can drag MUs between trains
    within scheduler constraints, so that re-planning is a board action, not a spreadsheet
    exercise."

Every operation here marks the train(s) it touches ``overridden = True`` — S3.1.2's
``ModelFamily`` pinning mechanism, reused verbatim for ``ReleaseTrain``: this is what
``TrainPlanner.run`` (S3.2.1) reads to leave a train alone on the next re-propose, the same
way ``Cartographer.run`` already does for families (see that module's docstring and
ADR 0023). Without it, the very next train proposal would silently retire and replace every
edit a Programme Manager made on the Wave Board.

**A workbook is IN_TRAIN at most one train at a time.** Moving one means retiring its
current ``IN_TRAIN`` edge and writing a new one — an edge's endpoints are fixed at creation,
so "move" is retire-and-recreate, never a property update. Re-sequencing within the *same*
train needs no retirement at all: ``sequence`` is simply upserted on the edge that already
exists.

**A move that would split a family across trains is refused outright — never a warning,
never overridable.** §3.3's whole reason for a train to exist is that a family is designed
once inside it; S3.2.1's own packing already guarantees this on a fresh proposal, and this
module holds the same line on every edit after it. A move succeeds only when every other
member of the moved workbook's family is already in, or is also moving to, the destination
train.

**A move that would exceed a configured WIP limit is a different kind of stop: a warning,
not a block.** The story's own words — "exceeding it warns and requires a reason" — this
module implements literally: called without a reason, an over-limit move is refused,
naming the limit and the count, so a caller can resubmit with one attached rather than
needing to know upfront whether a reason will be required. Re-sequencing can never trip a
WIP limit; it does not change how many MUs are in any train or state, only their order.

**"State" is read, not written, by anything else in this module.** ``IN_TRAIN.state``
(S3.2.2's own addition) is a card's kanban column — set once, to ``DEFAULT_MU_STATE``, when
``TrainPlanner.run`` first assigns a workbook to a train. This module never changes it
(state transitions belong to whatever eventually builds §3.2's state machine, not to a
re-planning board); a move or resequence simply carries the existing state along.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

import asyncpg

from .errors import ElementNotFoundError, InvalidRequestError
from .graph.queries import EDGE_INDEX_TABLE, NODE_INDEX_TABLE
from .ids import new_ulid
from .lineage import hydrate
from .migration_units import MU_STATES
from .ontology.types import BASE_EDGE_PROPERTIES, BASE_NODE_PROPERTIES
from .principal import Principal
from .trains import DEFAULT_MU_STATE
from .writes import EdgeWrite, GraphWriter, NodeWrite

#: Base properties the writer injects itself (created_by/created_at/... , written_by for
#: edges) — a hydrated read includes them, and resubmitting them as caller-supplied
#: properties is refused as an ontology violation (they are ``server_managed``). Every
#: place below that re-writes a node/edge starting from its own hydrated properties must
#: strip these first, or "preserve everything else" quietly tries to spoof audit fields.
_NODE_SERVER_MANAGED = frozenset(p.name for p in BASE_NODE_PROPERTIES if p.server_managed) | {
    "id",
    "side",
}
_EDGE_SERVER_MANAGED = frozenset(p.name for p in BASE_EDGE_PROPERTIES if p.server_managed) | {"id"}


def _writable_node_properties(properties: Mapping[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in properties.items() if k not in _NODE_SERVER_MANAGED}


def _writable_edge_properties(properties: Mapping[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in properties.items() if k not in _EDGE_SERVER_MANAGED}

#: Same floor writes.MIN_RETIREMENT_REASON_LENGTH / family_overrides.MIN_OVERRIDE_REASON_LENGTH
#: already hold writes to — named again because this reason lands on different properties
#: (ReleaseTrain.override_reason, IN_TRAIN.wip_override_reason), not a retirement one.
MIN_OVERRIDE_REASON_LENGTH = 8

_MOVE_RETIREMENT_REASON = "moved to another train on the Wave Board (story S3.2.2)"


@dataclass(frozen=True, slots=True)
class WipStatus:
    """What a configured WIP limit says about one more MU joining a train/state."""

    train_limit: int | None
    train_count: int
    state_limit: int | None
    state_count: int

    @property
    def train_exceeded(self) -> bool:
        return self.train_limit is not None and self.train_count > self.train_limit

    @property
    def state_exceeded(self) -> bool:
        return self.state_limit is not None and self.state_count > self.state_limit

    @property
    def exceeded(self) -> bool:
        return self.train_exceeded or self.state_exceeded

    def as_dict(self) -> dict[str, Any]:
        return {
            "train_limit": self.train_limit,
            "train_count": self.train_count,
            "state_limit": self.state_limit,
            "state_count": self.state_count,
            "exceeded": self.exceeded,
        }


@dataclass(frozen=True, slots=True)
class MoveResult:
    workbook_id: str
    from_train_id: str
    to_train_id: str
    state: str
    sequence: int
    wip: WipStatus | None
    """``None`` when the destination train has no WIP limit configured at all."""

    def as_dict(self) -> dict[str, Any]:
        return {
            "workbook_id": self.workbook_id,
            "from_train_id": self.from_train_id,
            "to_train_id": self.to_train_id,
            "state": self.state,
            "sequence": self.sequence,
            "wip": self.wip.as_dict() if self.wip is not None else None,
        }


def _validate_reason(reason: str) -> str:
    cleaned = reason.strip()
    if len(cleaned) < MIN_OVERRIDE_REASON_LENGTH:
        raise InvalidRequestError(
            f"an override needs a reason of at least {MIN_OVERRIDE_REASON_LENGTH} "
            f"characters; it is the record of why a Programme Manager changed the plan"
        )
    return cleaned


def _mark_overridden(properties: Mapping[str, Any], *, action: str, reason: str) -> dict[str, Any]:
    """The full property set to upsert: everything the train already had, plus the
    override stamp. An upsert replaces the whole property set (S1.1.1's own hard-won
    lesson), so anything not carried forward here — name, planned dates, gate schedule,
    wip_limits — would otherwise be silently wiped. ``properties`` comes from a hydrated
    read, so the server-managed base fields it also carries (created_by, created_at, ...)
    are stripped first — resubmitting them is refused as an ontology violation, not merged."""
    return {
        **_writable_node_properties(properties),
        "overridden": True,
        "override_action": action,
        "override_reason": reason,
    }


async def _train_properties_by_id(
    pool: asyncpg.Pool, graph_name: str, train_ids: Sequence[str]
) -> dict[str, dict[str, Any]]:
    async with pool.acquire() as conn:
        return await hydrate(conn, graph_name, "ReleaseTrain", train_ids)


async def _current_train_edge(
    pool: asyncpg.Pool, graph_name: str, workbook_id: str
) -> tuple[str, str, dict[str, Any]] | None:
    """(edge_id, train_id, edge_properties) for a workbook's current live IN_TRAIN edge."""
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            f"""
            SELECT e.id, e.to_id AS train
            FROM {EDGE_INDEX_TABLE} e
            WHERE e.graph = $1 AND e.label = 'IN_TRAIN' AND e.from_id = $2
              AND e.retired_at IS NULL
            """,
            graph_name,
            workbook_id,
        )
        if row is None:
            return None
        properties = await hydrate(conn, graph_name, "IN_TRAIN", [row["id"]])
    return row["id"], row["train"], properties.get(row["id"], {})


async def _train_member_edges(
    pool: asyncpg.Pool, graph_name: str, train_id: str
) -> list[tuple[str, str, dict[str, Any]]]:
    """Every live IN_TRAIN edge for one train — (edge_id, workbook_id, edge_properties),
    ordered by the edge's own ``sequence``."""
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            f"""
            SELECT e.id, e.from_id AS workbook
            FROM {EDGE_INDEX_TABLE} e
            JOIN {NODE_INDEX_TABLE} n ON n.id = e.from_id AND n.kind = 'node'
             AND n.graph = $1 AND n.label = 'Workbook' AND n.retired_at IS NULL
            WHERE e.graph = $1 AND e.label = 'IN_TRAIN' AND e.to_id = $2
              AND e.retired_at IS NULL
            """,
            graph_name,
            train_id,
        )
        properties = await hydrate(conn, graph_name, "IN_TRAIN", [row["id"] for row in rows])
    members = [(row["id"], row["workbook"], properties.get(row["id"], {})) for row in rows]
    return sorted(members, key=lambda item: int(item[2].get("sequence") or 0))


async def _family_of(
    pool: asyncpg.Pool, graph_name: str, workbook_id: str
) -> tuple[str, list[str]] | None:
    """(family_id, every member incl. ``workbook_id``) for a workbook's live family, or
    ``None`` if it has none — nothing this story needs to solve."""
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            f"""
            SELECT e.to_id AS family FROM {EDGE_INDEX_TABLE} e
            WHERE e.graph = $1 AND e.label = 'IN_FAMILY' AND e.from_id = $2
              AND e.retired_at IS NULL
            """,
            graph_name,
            workbook_id,
        )
        if row is None:
            return None
        members = await conn.fetch(
            f"""
            SELECT e.from_id AS workbook FROM {EDGE_INDEX_TABLE} e
            WHERE e.graph = $1 AND e.label = 'IN_FAMILY' AND e.to_id = $2
              AND e.retired_at IS NULL
            """,
            graph_name,
            row["family"],
        )
    return row["family"], [m["workbook"] for m in members]


async def _wip_status(
    pool: asyncpg.Pool,
    graph_name: str,
    train_id: str,
    state: str,
    *,
    train_properties: Mapping[str, Any],
) -> WipStatus | None:
    """What admitting one more MU (in ``state``) to ``train_id`` would do to its configured
    limits — ``None`` if nothing is configured at all, so a caller can skip the reason
    prompt entirely on a train nobody has capped."""
    limits = train_properties.get("wip_limits") or {}
    train_limit = limits.get("train")
    state_limit = (limits.get("states") or {}).get(state)
    if train_limit is None and state_limit is None:
        return None
    members = await _train_member_edges(pool, graph_name, train_id)
    state_count = sum(1 for _e, _w, props in members if props.get("state") == state)
    return WipStatus(
        train_limit=train_limit,
        train_count=len(members) + 1,
        state_limit=state_limit,
        state_count=state_count + 1,
    )


async def move_mu(
    pool: asyncpg.Pool,
    graph_name: str,
    writer: GraphWriter,
    *,
    workbook_id: str,
    to_train_id: str,
    reason: str | None,
    principal: Principal,
) -> MoveResult:
    """Move one MU into ``to_train_id``, out of whichever train it is in now."""
    validated_reason = _validate_reason(reason) if reason else None

    target_properties = (await _train_properties_by_id(pool, graph_name, [to_train_id])).get(
        to_train_id
    )
    if target_properties is None:
        raise ElementNotFoundError(f"no release train '{to_train_id}'")

    current = await _current_train_edge(pool, graph_name, workbook_id)
    if current is None:
        raise InvalidRequestError(f"workbook '{workbook_id}' is not currently IN_TRAIN any train")
    old_edge_id, from_train_id, edge_properties = current
    if from_train_id == to_train_id:
        raise InvalidRequestError(f"workbook '{workbook_id}' is already in '{to_train_id}'")
    state = str(edge_properties.get("state") or DEFAULT_MU_STATE)

    family = await _family_of(pool, graph_name, workbook_id)
    if family is not None:
        family_id, siblings = family
        stray_trains: set[str] = set()
        for sibling in siblings:
            if sibling == workbook_id:
                continue
            sibling_edge = await _current_train_edge(pool, graph_name, sibling)
            if sibling_edge is not None and sibling_edge[1] != to_train_id:
                stray_trains.add(sibling_edge[1])
        if stray_trains:
            raise InvalidRequestError(
                f"moving '{workbook_id}' alone would split family '{family_id}' across "
                f"trains ('{to_train_id}' and {sorted(stray_trains)}) — every member of a "
                f"family moves together, or not at all"
            )

    wip = await _wip_status(pool, graph_name, to_train_id, state, train_properties=target_properties)
    if wip is not None and wip.exceeded and validated_reason is None:
        raise InvalidRequestError(
            f"moving '{workbook_id}' into '{to_train_id}' would exceed its configured WIP "
            f"limit ({wip.as_dict()}) — resubmit with a reason to proceed anyway"
        )

    existing_members = await _train_member_edges(pool, graph_name, to_train_id)
    next_sequence = (
        max((int(props.get("sequence") or 0) for _e, _w, props in existing_members), default=0) + 1
    )

    await writer.retire_edge(old_edge_id, reason=_MOVE_RETIREMENT_REASON, principal=principal)
    new_edge_properties: dict[str, Any] = {"sequence": next_sequence, "state": state}
    if wip is not None and wip.exceeded and validated_reason is not None:
        new_edge_properties["wip_override_reason"] = validated_reason
    await writer.write_edge(
        EdgeWrite(
            type="IN_TRAIN",
            id=new_ulid(),
            from_id=workbook_id,
            to_id=to_train_id,
            properties=new_edge_properties,
        ),
        principal=principal,
    )

    override_reason = validated_reason or f"moved '{workbook_id}' on the Wave Board"
    from_properties = (await _train_properties_by_id(pool, graph_name, [from_train_id])).get(
        from_train_id, {}
    )
    for train_id, properties in ((from_train_id, from_properties), (to_train_id, target_properties)):
        await writer.upsert_nodes(
            [
                NodeWrite(
                    type="ReleaseTrain",
                    id=train_id,
                    properties=_mark_overridden(properties, action="MOVE", reason=override_reason),
                )
            ],
            principal=principal,
        )

    return MoveResult(
        workbook_id=workbook_id,
        from_train_id=from_train_id,
        to_train_id=to_train_id,
        state=state,
        sequence=next_sequence,
        wip=wip,
    )


async def resequence_mu(
    pool: asyncpg.Pool,
    graph_name: str,
    writer: GraphWriter,
    *,
    workbook_id: str,
    position: int,
    principal: Principal,
) -> tuple[str, int]:
    """Move ``workbook_id`` to 1-based ``position`` within its current train, renumbering
    whatever else shifts as a result. Returns ``(train_id, actual_position)`` — position is
    clamped to the train's size, so asking for 999 in a 6-MU train lands it last."""
    if position < 1:
        raise InvalidRequestError("position must be 1 or greater")

    current = await _current_train_edge(pool, graph_name, workbook_id)
    if current is None:
        raise InvalidRequestError(f"workbook '{workbook_id}' is not currently IN_TRAIN any train")
    _edge_id, train_id, _properties = current

    members = await _train_member_edges(pool, graph_name, train_id)
    by_workbook = {workbook: (edge_id, props) for edge_id, workbook, props in members}
    ordered = [workbook for _e, workbook, _p in members]
    ordered.remove(workbook_id)
    target_index = min(position - 1, len(ordered))
    ordered.insert(target_index, workbook_id)

    for new_sequence, workbook in enumerate(ordered, start=1):
        edge_id, props = by_workbook[workbook]
        if int(props.get("sequence") or 0) == new_sequence:
            continue
        await writer.upsert_edge(
            EdgeWrite(
                type="IN_TRAIN",
                id=edge_id,
                from_id=workbook,
                to_id=train_id,
                properties={**_writable_edge_properties(props), "sequence": new_sequence},
            ),
            principal=principal,
        )

    properties = (await _train_properties_by_id(pool, graph_name, [train_id])).get(train_id, {})
    await writer.upsert_nodes(
        [
            NodeWrite(
                type="ReleaseTrain",
                id=train_id,
                properties=_mark_overridden(
                    properties,
                    action="RESEQUENCE",
                    reason=f"resequenced '{workbook_id}' on the Wave Board",
                ),
            )
        ],
        principal=principal,
    )
    return train_id, target_index + 1


async def set_wip_limits(
    pool: asyncpg.Pool,
    graph_name: str,
    writer: GraphWriter,
    *,
    train_id: str,
    train_limit: int | None,
    state_limits: Mapping[str, int] | None,
    reason: str,
    principal: Principal,
) -> dict[str, Any]:
    """Configure ``train_id``'s WIP caps. Always needs a reason — unlike a move, which only
    needs one when a limit is actually exceeded, changing the limit itself is always a
    deliberate act worth recording why."""
    validated_reason = _validate_reason(reason)
    properties = (await _train_properties_by_id(pool, graph_name, [train_id])).get(train_id)
    if properties is None:
        raise ElementNotFoundError(f"no release train '{train_id}'")
    if train_limit is not None and train_limit < 1:
        raise InvalidRequestError("train_limit must be a positive number of MUs")
    limits = dict(state_limits or {})
    unknown = set(limits) - set(MU_STATES)
    if unknown:
        raise InvalidRequestError(
            f"unrecognised state(s) {sorted(unknown)}; must be one of {MU_STATES}"
        )
    for state, limit in limits.items():
        if limit < 1:
            raise InvalidRequestError(f"the WIP limit for '{state}' must be a positive number of MUs")

    wip_limits = {"train": train_limit, "states": limits}
    await writer.upsert_nodes(
        [
            NodeWrite(
                type="ReleaseTrain",
                id=train_id,
                properties=_mark_overridden(
                    {**properties, "wip_limits": wip_limits},
                    action="WIP_LIMITS",
                    reason=validated_reason,
                ),
            )
        ],
        principal=principal,
    )
    return wip_limits


__all__ = [
    "DEFAULT_MU_STATE",
    "MIN_OVERRIDE_REASON_LENGTH",
    "MoveResult",
    "WipStatus",
    "move_mu",
    "resequence_mu",
    "set_wip_limits",
]
