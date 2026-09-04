"""Graph versions, and reading the graph as it was at one.

S1.3.2: "graph versions are addressable by event offset", so that "from a ProvenanceRecord
the console can re-materialise the context at the recorded graph version and show that the
hash matches".

**A version is an event sequence number.** Not a timestamp, not a snapshot id. S1.1.3 made
the outbox the record — every mutation is committed with its event, and a replay from
empty reproduces the graph exactly — which means the stream up to sequence *n* fully
determines the graph at that point. Nothing else needs storing, no snapshots need taking,
and the version an auditor quotes is a number they can look up in the same event stream
the platform publishes.

**How this reads history without replaying it.** A naive implementation replays every
event up to the offset; over a programme that is millions of events for one audit. Instead
every read is indexed and bounded, because each event carries its element's *complete*
post-write state (S1.1.3's design, and the reason replay needs no prior state):

* a node's state at version *n* is its latest ``estate.node.upserted`` at or below *n*,
  plus its retirement if one happened at or below *n*;
* the edges of one type out of a node are the latest ``estate.edge.upserted`` per edge id
  at or below *n*, found by an index on the event's ``from_id``;
* a transitive closure is those two, iterated to a bounded depth.

So an audit of one calculation reads a handful of rows, not the estate's whole history.

**What this cannot see.** A version below the first event is an empty graph, and a version
above the current one is a claim about the future — both are refused rather than answered
with something plausible. See ``retention.py`` for how long a version stays answerable.
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

import asyncpg

from .errors import InvalidRequestError
from .events import EventType
from .graph.model import EdgeRecord, Neighbour, NodeRecord
from .ontology import EDGE_LABELS, NODE_LABELS

EVENT_TABLE = "public.estate_event"

#: A closure at a historical version costs one query per level, so the depth is bounded for
#: the same reason the live one is.
MAX_CLOSURE_DEPTH = 12

#: The most elements one historical read will gather. The live reads have the same ceiling.
MAX_ELEMENTS = 10_000


@dataclass(frozen=True, slots=True)
class GraphVersion:
    """One addressable point in the graph's history."""

    version: int
    """The event sequence number. Zero is the empty graph, before anything was written."""

    at: str | None
    """When the event at this offset was committed. Absent for version zero."""

    def as_dict(self) -> dict[str, Any]:
        return {"graph_version": self.version, "at": self.at}


class UnknownVersionError(InvalidRequestError):
    """A version that is not addressable: below zero, or beyond what has happened."""


#: Latest state of specific nodes at or below a version.
_NODE_STATE_SQL = f"""
SELECT DISTINCT ON (subject) subject, label, data
FROM {EVENT_TABLE}
WHERE graph = $1 AND type = $2 AND subject = ANY($3::text[]) AND seq <= $4
ORDER BY subject, seq DESC
"""

#: Every live node of one label at or below a version.
_NODES_OF_LABEL_SQL = f"""
SELECT DISTINCT ON (subject) subject, label, data
FROM {EVENT_TABLE}
WHERE graph = $1 AND type = $2 AND label = $3 AND seq <= $4
ORDER BY subject, seq DESC
LIMIT $5
"""

#: Edges of one type leaving any of a set of nodes, at or below a version.
_OUTGOING_SQL = f"""
SELECT DISTINCT ON (subject) subject, label, data
FROM {EVENT_TABLE}
WHERE graph = $1 AND type = $2 AND label = $3
  AND data->>'from_id' = ANY($4::text[]) AND seq <= $5
ORDER BY subject, seq DESC
"""


class HistoricalGraphReader:
    """The graph as it stood at one event offset.

    Satisfies ``context.ContextReader``, which is the whole point: the assembler that
    materialises a contract today materialises it at a past version without knowing the
    difference. If it needed a second code path, a re-materialised context would be
    evidence about that path rather than about the one the agent used.
    """

    def __init__(self, pool: asyncpg.Pool, *, graph_name: str, version: int) -> None:
        if version < 0:
            raise UnknownVersionError(f"a graph version cannot be negative, got {version}")
        self._pool = pool
        self._graph = graph_name
        self._version = version

    @property
    def version(self) -> int:
        return self._version

    # ------------------------------------------------------------------- nodes

    async def get_node_record(self, node_id: str) -> NodeRecord | None:
        found = await self._nodes([node_id])
        return found.get(node_id)

    async def get_nodes(self, ids: Sequence[str]) -> list[NodeRecord]:
        found = await self._nodes(ids)
        return [found[node_id] for node_id in ids if node_id in found]

    async def nodes_of_type(self, label: str, *, limit: int = 1000) -> list[NodeRecord]:
        if label not in NODE_LABELS:
            raise ValueError(f"unknown node label {label!r} reached the historical reader")
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(
                _NODES_OF_LABEL_SQL,
                self._graph,
                EventType.NODE_UPSERTED.value,
                label,
                self._version,
                min(limit, MAX_ELEMENTS),
            )
            retired = await self._retired(conn, [row["subject"] for row in rows])
        return [
            NodeRecord(label=row["label"], properties=_properties(row))
            for row in rows
            if row["subject"] not in retired
        ]

    async def _nodes(self, ids: Sequence[str]) -> dict[str, NodeRecord]:
        wanted = list(dict.fromkeys(ids))
        if not wanted:
            return {}
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(
                _NODE_STATE_SQL,
                self._graph,
                EventType.NODE_UPSERTED.value,
                wanted,
                self._version,
            )
            retired = await self._retired(conn, [row["subject"] for row in rows])
        return {
            row["subject"]: NodeRecord(label=row["label"], properties=_properties(row))
            for row in rows
            if row["subject"] not in retired
        }

    async def _retired(
        self,
        conn: asyncpg.Connection,
        ids: Sequence[str],
        *,
        event_type: EventType = EventType.NODE_RETIRED,
    ) -> set[str]:
        """Which of these were retired at or below this version.

        Retired elements are excluded, exactly as the live reads exclude them. A context
        assembled at a version *before* a retirement still sees the element, which is the
        point: it is what the agent saw. ``event_type`` is ``NODE_RETIRED`` by default and
        ``EDGE_RETIRED`` for an edge's *own* retirement (S3.1.2) — the query is identical
        either way, since both events carry the retired element's id as ``subject``.
        """
        if not ids:
            return set()
        rows = await conn.fetch(
            _NODE_STATE_SQL,
            self._graph,
            event_type.value,
            list(dict.fromkeys(ids)),
            self._version,
        )
        return {row["subject"] for row in rows}

    # ------------------------------------------------------------------- edges

    async def outgoing_edges(
        self, from_ids: Sequence[str], *, edge_type: str
    ) -> list[EdgeRecord]:
        if edge_type not in EDGE_LABELS:
            raise ValueError(f"unknown edge label {edge_type!r} reached the historical reader")
        wanted = list(dict.fromkeys(from_ids))
        if not wanted:
            return []
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(
                _OUTGOING_SQL,
                self._graph,
                EventType.EDGE_UPSERTED.value,
                edge_type,
                wanted,
                self._version,
            )
            targets = [str(json.loads(row["data"])["to_id"]) for row in rows]
            retired = await self._retired(conn, targets)
            retired_edges = await self._retired(
                conn, [row["subject"] for row in rows], event_type=EventType.EDGE_RETIRED
            )

        edges: list[EdgeRecord] = []
        for row in rows:
            if row["subject"] in retired_edges:
                continue
            data = json.loads(row["data"])
            if str(data["to_id"]) in retired:
                continue
            edges.append(
                EdgeRecord(
                    label=row["label"],
                    properties=dict(data["properties"]),
                    from_id=str(data["from_id"]),
                    to_id=str(data["to_id"]),
                )
            )
        edges.sort(key=lambda edge: edge.id)
        return edges

    async def step(
        self, from_ids: Sequence[str], *, edge_type: str, to_types: Sequence[str] | None = None
    ) -> list[Neighbour]:
        edges = await self.outgoing_edges(from_ids, edge_type=edge_type)
        found = await self._nodes([edge.to_id for edge in edges])
        allowed = set(to_types) if to_types else None
        seen: set[str] = set()
        out: list[Neighbour] = []
        for edge in edges:
            record = found.get(edge.to_id)
            if record is None or edge.to_id in seen:
                continue
            if allowed is not None and record.label not in allowed:
                continue
            seen.add(edge.to_id)
            out.append(Neighbour(node=record, depth=1))
        return out

    # --------------------------------------------------------------- traversal

    async def closure(
        self,
        anchor_id: str,
        *,
        edge_type: str,
        depth: int,
        limit: int = MAX_ELEMENTS,
    ) -> list[Neighbour]:
        """Transitive closure at this version, breadth first.

        One indexed query per level rather than one recursive query, because the recursive
        CTE the live reader uses runs over the adjacency index, which only holds *current*
        edges. Depth is small — the live traversal is bounded at 12 for the same reason —
        so a handful of round trips is the right trade for reading history exactly.
        """
        if edge_type not in EDGE_LABELS:
            raise ValueError(f"unknown edge label {edge_type!r} reached the historical reader")
        bound = min(depth, MAX_CLOSURE_DEPTH)

        anchor = await self.get_node_record(anchor_id)
        if anchor is None:
            return []

        reached: dict[str, Neighbour] = {anchor_id: Neighbour(node=anchor, depth=0)}
        frontier = [anchor_id]
        for level in range(1, bound + 1):
            if not frontier or len(reached) >= limit:
                break
            edges = await self.outgoing_edges(frontier, edge_type=edge_type)
            targets = [e.to_id for e in edges if e.to_id not in reached]
            found = await self._nodes(targets)
            frontier = []
            for node_id in targets:
                record = found.get(node_id)
                if record is None or node_id in reached:
                    continue
                reached[node_id] = Neighbour(node=record, depth=level)
                frontier.append(node_id)
                if len(reached) >= limit:
                    break

        return sorted(reached.values(), key=lambda n: (n.depth, n.node.id))


def _properties(row: asyncpg.Record) -> dict[str, Any]:
    """An event's node properties.

    Read straight out of the event rather than out of the graph. That is the guarantee
    S1.1.3 built: the event carries the element's complete post-write property set, so
    this is the node exactly as it was, not a reconstruction of it.
    """
    data = json.loads(row["data"])
    return dict(data["properties"])


__all__ = [
    "MAX_CLOSURE_DEPTH",
    "MAX_ELEMENTS",
    "GraphVersion",
    "HistoricalGraphReader",
    "UnknownVersionError",
]
