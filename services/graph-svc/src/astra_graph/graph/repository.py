"""The Estate Graph store.

``GraphRepository`` is the interface the API writes through; ``AgeGraphRepository`` is the
PostgreSQL 16 + Apache AGE implementation (spec §5.4).

Two stores are written in one transaction: the AGE graph, which is the source of truth,
and ``estate_element_index``, a relational table of ``(id, kind, label)``. The index makes
an id-to-label lookup a primary-key probe, which the edge endpoint check needs on every
edge write, and it makes a duplicate id a unique-violation instead of a race. It is
derived data: it is written in the same transaction as the graph and never read as
authority for anything but routing a lookup. ``estate_edge_index`` is the same idea for
edges, and is what the neighbourhood traversal walks; see ``queries.py`` for why reads do
not go through ``cypher()``.

A third table joins them from S1.1.3: ``estate_event``, the mutation outbox. Every write
here commits its event with the mutation, in one transaction. Nothing in this module
publishes to a bus — that is E12's — but nothing can mutate the graph without leaving a
record either, and that is what makes the stream replayable.
"""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import Iterable, Sequence
from datetime import UTC, datetime
from typing import Any, Protocol

import asyncpg

from ..config import Settings
from ..errors import (
    CypherExecutionError,
    CypherTimeoutError,
    DuplicateElementError,
    ElementNotFoundError,
    GraphUnavailableError,
    InvalidRequestError,
)
from ..events import EventType, PlatformEvent, StoredEvent
from ..ontology import EDGE_LABELS, NODE_LABELS, node_type
from . import agtype, queries
from .model import EdgeRecord, Neighbour, NeighbourhoodResult, NodeRecord

logger = logging.getLogger(__name__)

# Fully qualified: connections run with ag_catalog first on the search path so
# that AGE's operators resolve, which would otherwise make ag_catalog the
# default schema for unqualified names. Platform tables live in public.
_INDEX_TABLE = queries.NODE_INDEX_TABLE
_EDGE_INDEX_TABLE = queries.EDGE_INDEX_TABLE
_EVENT_TABLE = "public.estate_event"

#: A neighbourhood or an edge listing never returns more than this in one call.
DEFAULT_MAX_ELEMENTS = 10_000


#: Apache AGE reports a vertex or edge that changed under an update as an internal error
#: with this text, rather than blocking on the row the way an ordinary UPDATE would.
_AGE_CONCURRENT_UPDATE = "failed to be updated"
#: Set only when an upsert changes a node that already existed.
_UPDATE_ONLY = frozenset({"updated_by", "updated_at"})
_UPSERT_ATTEMPTS = 4
_UPSERT_BACKOFF_SECONDS = 0.05


class ConcurrentUpdateError(Exception):
    """Another writer changed this element mid-update. Retryable."""


def _is_concurrent_update(exc: BaseException) -> bool:
    return _AGE_CONCURRENT_UPDATE in str(exc)


def _iso(value: datetime | str | None) -> str:
    """Render a timestamp the way the write path canonicalises them."""
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    return value.astimezone(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _stored_event(row: Any) -> StoredEvent:
    """One outbox row as an event. Shared by the ordered read and the by-type read."""
    return StoredEvent(
        sequence=row["seq"],
        id=row["event_id"],
        type=EventType(row["type"]),
        source=row["source"],
        subject=row["subject"],
        label=row["label"],
        time=_iso(row["time"]),
        principal=row["principal"],
        run_id=row["run_id"],
        data=json.loads(row["data"]),
        published_at=_iso(row["published_at"]) if row["published_at"] else None,
    )


def _parse_timestamp(value: Any) -> datetime | None:
    """Parse a canonical timestamp property back into a datetime for the index column."""
    if not value:
        return None
    text = str(value)
    if text.endswith(("z", "Z")):
        text = text[:-1] + "+00:00"
    return datetime.fromisoformat(text)


class GraphRepository(Protocol):
    """What the write path needs from a graph store."""

    async def create_node(
        self, label: str, properties: dict[str, Any], event: PlatformEvent | None = None
    ) -> dict[str, Any]: ...

    async def create_nodes(
        self,
        elements: Sequence[tuple[str, dict[str, Any]]],
        events: Sequence[PlatformEvent] = (),
    ) -> list[dict[str, Any]]: ...

    async def create_edge(
        self,
        label: str,
        *,
        from_id: str,
        to_id: str,
        from_label: str,
        to_label: str,
        properties: dict[str, Any],
        event: PlatformEvent | None = None,
    ) -> dict[str, Any]: ...

    async def get_node(self, node_id: str) -> dict[str, Any] | None: ...

    async def get_edge(self, edge_id: str) -> dict[str, Any] | None: ...

    async def labels_for(self, ids: Iterable[str]) -> dict[str, str]: ...

    async def health(self) -> None: ...

    async def get_node_record(self, node_id: str) -> NodeRecord | None: ...

    async def get_edge_record(self, edge_id: str) -> EdgeRecord | None: ...

    async def get_node_by_luid(self, label: str, luid: str) -> NodeRecord | None: ...

    async def get_nodes(self, ids: Sequence[str]) -> list[NodeRecord]: ...

    async def neighbourhood(
        self,
        anchor_id: str,
        *,
        depth: int,
        edge_types: Sequence[str] | None = None,
        node_types: Sequence[str] | None = None,
        limit: int = DEFAULT_MAX_ELEMENTS,
        include_retired: bool = False,
    ) -> NeighbourhoodResult: ...

    async def closure(
        self, anchor_id: str, *, edge_type: str, depth: int, limit: int = DEFAULT_MAX_ELEMENTS
    ) -> list[Neighbour]: ...

    async def step(
        self, from_ids: Sequence[str], *, edge_type: str, to_types: Sequence[str] | None = None
    ) -> list[Neighbour]: ...

    async def outgoing_edges(
        self, from_ids: Sequence[str], *, edge_type: str
    ) -> list[EdgeRecord]: ...

    async def run_read_only_cypher(
        self, query: str, columns: Sequence[str], params: dict[str, Any], *,
        timeout_seconds: int, row_limit: int,
    ) -> tuple[list[dict[str, Any]], bool]: ...

    async def upsert_nodes(
        self,
        elements: Sequence[tuple[str, dict[str, Any]]],
        events: Sequence[PlatformEvent] = (),
    ) -> list[dict[str, Any]]: ...

    async def upsert_edge(
        self,
        label: str,
        *,
        from_id: str,
        to_id: str,
        from_label: str,
        to_label: str,
        properties: dict[str, Any],
        event: PlatformEvent | None = None,
    ) -> dict[str, Any]: ...

    async def retire_node(
        self,
        node_id: str,
        *,
        retired_at: str,
        retired_by: str,
        reason: str,
        event: PlatformEvent | None = None,
    ) -> dict[str, Any]: ...

    async def retire_edge(
        self,
        edge_id: str,
        *,
        retired_at: str,
        retired_by: str,
        reason: str,
        event: PlatformEvent | None = None,
    ) -> dict[str, Any]: ...

    async def append_event(self, event: PlatformEvent) -> None: ...

    async def events_of_type(
        self, event_type: EventType, *, limit: int = 100
    ) -> list[StoredEvent]: ...

    async def current_version(self) -> tuple[int, str | None]: ...

    async def read_events(
        self, *, after: int = 0, limit: int = 1000, subject: str | None = None
    ) -> list[StoredEvent]: ...

    async def nodes_of_type(self, label: str, *, limit: int = 1000) -> list[NodeRecord]: ...

    async def incoming_counts(
        self, node_ids: Sequence[str], *, edge_type: str
    ) -> dict[str, int]: ...

    async def dump(self) -> dict[str, Any]: ...


class AgeGraphRepository:
    """Apache AGE implementation of :class:`GraphRepository`."""

    def __init__(self, pool: asyncpg.Pool, *, graph_name: str) -> None:
        self._pool = pool
        # Validated in config.load_settings; asserted here because it is interpolated
        # into SQL — AGE takes the graph name as a literal, not a bind parameter.
        if not graph_name.replace("_", "").isalnum():
            raise ValueError(f"unsafe graph name {graph_name!r}")
        self._graph = graph_name

    # ------------------------------------------------------------------ internals

    def _cypher(self, query: str, *, returns: str = "v ag_catalog.agtype") -> str:
        return f"SELECT * FROM ag_catalog.cypher('{self._graph}', $${query}$$, $1) AS ({returns})"

    async def _run(
        self,
        conn: asyncpg.Connection,
        query: str,
        params: dict[str, Any],
        *,
        returns: str = "v ag_catalog.agtype",
    ) -> list[asyncpg.Record]:
        sql = self._cypher(query, returns=returns)
        rows: list[asyncpg.Record] = await conn.fetch(sql, agtype.encode_params(params))
        return rows

    # ---------------------------------------------------------------------- reads

    async def health(self) -> None:
        try:
            async with self._pool.acquire() as conn:
                await conn.fetchval("SELECT 1")
                exists = await conn.fetchval(
                    "SELECT EXISTS (SELECT 1 FROM ag_catalog.ag_graph WHERE name = $1)",
                    self._graph,
                )
                if not exists:
                    raise GraphUnavailableError(
                        f"graph '{self._graph}' does not exist; run the migrations"
                    )
        except asyncpg.PostgresError as exc:
            raise GraphUnavailableError(str(exc)) from exc

    async def labels_for(self, ids: Iterable[str]) -> dict[str, str]:
        wanted = list(dict.fromkeys(ids))
        if not wanted:
            return {}
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(
                f"SELECT id, label FROM {_INDEX_TABLE} "
                f"WHERE graph = $2 AND id = ANY($1::text[])",
                wanted,
                self._graph,
            )
        return {row["id"]: row["label"] for row in rows}

    async def get_node(self, node_id: str) -> dict[str, Any] | None:
        async with self._pool.acquire() as conn:
            label = await conn.fetchval(
                f"SELECT label FROM {_INDEX_TABLE} "
                f"WHERE graph = $2 AND id = $1 AND kind = 'node'",
                node_id,
                self._graph,
            )
            if label is None or label not in NODE_LABELS:
                return None
            rows = await self._run(
                conn,
                f"MATCH (n:{label}) WHERE n.id = $node_id RETURN n",
                {"node_id": node_id},
            )
        if not rows:
            return None
        return agtype.decode_vertex(rows[0]["v"])

    async def get_edge(self, edge_id: str) -> dict[str, Any] | None:
        async with self._pool.acquire() as conn:
            label = await conn.fetchval(
                f"SELECT label FROM {_INDEX_TABLE} "
                f"WHERE graph = $2 AND id = $1 AND kind = 'edge'",
                edge_id,
                self._graph,
            )
            if label is None or label not in EDGE_LABELS:
                return None
            rows = await self._run(
                conn,
                f"MATCH ()-[e:{label}]->() WHERE e.id = $edge_id RETURN e",
                {"edge_id": edge_id},
            )
        if not rows:
            return None
        return agtype.decode_edge(rows[0]["v"])

    # ------------------------------------------------------- reads: the query API
    #
    # These bypass cypher() and read AGE's label tables through the indexed property
    # accessor. queries.py records the measurements behind that decision.

    async def _hydrate(
        self, conn: asyncpg.Connection, by_label: dict[str, list[str]]
    ) -> dict[str, NodeRecord]:
        """Fetch node rows for ids grouped by label, in one round trip."""
        if not by_label:
            return {}
        labels = sorted(by_label)
        sql = queries.hydrate_nodes(self._graph, labels)
        args = [[queries.agtype_literal(i) for i in by_label[label]] for label in labels]
        rows = await conn.fetch(sql, *args)
        found: dict[str, NodeRecord] = {}
        for row in rows:
            properties = queries.decode_properties(row["properties"])
            found[str(properties["id"])] = NodeRecord(label=row["label"], properties=properties)
        return found

    async def _hydrate_edges(
        self, conn: asyncpg.Connection, rows: Sequence[asyncpg.Record]
    ) -> list[EdgeRecord]:
        """Fetch edge rows for adjacency-index rows, carrying their endpoints through."""
        by_label: dict[str, list[str]] = {}
        endpoints: dict[str, tuple[str, str]] = {}
        for row in rows:
            by_label.setdefault(row["label"], []).append(row["id"])
            endpoints[row["id"]] = (row["from_id"], row["to_id"])
        if not by_label:
            return []
        labels = sorted(by_label)
        sql = queries.hydrate_nodes(self._graph, labels)
        args = [[queries.agtype_literal(i) for i in by_label[label]] for label in labels]
        hydrated = await conn.fetch(sql, *args)
        edges: list[EdgeRecord] = []
        for row in hydrated:
            properties = queries.decode_properties(row["properties"])
            edge_id = str(properties["id"])
            from_id, to_id = endpoints[edge_id]
            edges.append(
                EdgeRecord(label=row["label"], properties=properties,
                           from_id=from_id, to_id=to_id)
            )
        edges.sort(key=lambda e: e.id)
        return edges

    async def get_node_record(self, node_id: str) -> NodeRecord | None:
        async with self._pool.acquire() as conn:
            label = await conn.fetchval(
                f"SELECT label FROM {_INDEX_TABLE} "
                f"WHERE graph = $2 AND id = $1 AND kind = 'node'",
                node_id,
                self._graph,
            )
            if label is None or label not in NODE_LABELS:
                return None
            found = await self._hydrate(conn, {label: [node_id]})
        return found.get(node_id)

    async def get_edge_record(self, edge_id: str) -> EdgeRecord | None:
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                f"SELECT i.label, e.from_id, e.to_id FROM {_INDEX_TABLE} i "
                f"JOIN {_EDGE_INDEX_TABLE} e ON e.graph = i.graph AND e.id = i.id "
                f"WHERE i.graph = $2 AND i.id = $1 AND i.kind = 'edge'",
                edge_id,
                self._graph,
            )
            if row is None or row["label"] not in EDGE_LABELS:
                return None
            rows = await conn.fetch(
                queries.hydrate_nodes(self._graph, [row["label"]]),
                [queries.agtype_literal(edge_id)],
            )
        if not rows:
            return None
        properties = queries.decode_properties(rows[0]["properties"])
        return EdgeRecord(
            label=row["label"], properties=properties, from_id=row["from_id"], to_id=row["to_id"]
        )

    async def get_nodes(self, ids: Sequence[str]) -> list[NodeRecord]:
        wanted = list(dict.fromkeys(ids))
        if not wanted:
            return []
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(
                f"SELECT id, label FROM {_INDEX_TABLE} "
                f"WHERE graph = $2 AND kind = 'node' AND id = ANY($1::text[])",
                wanted,
                self._graph,
            )
            by_label: dict[str, list[str]] = {}
            for row in rows:
                by_label.setdefault(row["label"], []).append(row["id"])
            found = await self._hydrate(conn, by_label)
        return [found[i] for i in wanted if i in found]

    async def get_node_by_luid(self, label: str, luid: str) -> NodeRecord | None:
        """Look a node up by its source-system identifier.

        Scoped to one label because a LUID is only unique within its own kind of object;
        the caller knows which kind it is asking about.
        """
        if label not in NODE_LABELS:
            raise ValueError(f"unknown node label {label!r} reached the repository")
        declared = node_type(label)
        if declared is None or "luid" not in declared.declared_property_names:
            raise ValueError(f"node type {label!r} does not carry a luid")
        sql = queries.read_by_property(self._graph, label, "luid")
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(sql, [queries.agtype_literal(luid)])
        if not rows:
            return None
        # A LUID is unique per label in the source system. If the estate says otherwise the
        # graph is wrong; return the first rather than inventing a merge policy here.
        properties = queries.decode_properties(rows[0]["properties"])
        return NodeRecord(label=label, properties=properties)

    async def neighbourhood(
        self,
        anchor_id: str,
        *,
        depth: int,
        edge_types: Sequence[str] | None = None,
        node_types: Sequence[str] | None = None,
        limit: int = DEFAULT_MAX_ELEMENTS,
        include_retired: bool = False,
    ) -> NeighbourhoodResult:
        """Every node within ``depth`` hops of the anchor, and the edges between them.

        Traversal is undirected: someone asking what a workbook touches wants the
        datasource it reads and the user who owns it alike, and direction is a property of
        the edge type rather than of the question.

        Retired nodes are left out unless asked for. A node retired out of the estate is
        still in the record, but it is no longer part of what a reader is looking at.
        """
        anchor = await self.get_node_record(anchor_id)
        if anchor is None:
            raise ElementNotFoundError(f"no node with id '{anchor_id}'")

        async with self._pool.acquire() as conn:
            if edge_types:
                rows = await conn.fetch(
                    queries.NEIGHBOURHOOD_FILTERED_SQL,
                    anchor_id, depth, limit + 1, self._graph, include_retired,
                    list(edge_types),
                )
            else:
                rows = await conn.fetch(
                    queries.NEIGHBOURHOOD_SQL,
                    anchor_id, depth, limit + 1, self._graph, include_retired,
                )

            truncated = len(rows) > limit
            rows = rows[:limit]

            wanted = [r for r in rows if r["id"] != anchor_id]
            if node_types:
                allowed = set(node_types)
                wanted = [r for r in wanted if r["label"] in allowed]

            by_label: dict[str, list[str]] = {}
            for row in wanted:
                by_label.setdefault(row["label"], []).append(row["id"])
            hydrated = await self._hydrate(conn, by_label)

            neighbours = [
                Neighbour(node=hydrated[row["id"]], depth=row["depth"])
                for row in wanted
                if row["id"] in hydrated
            ]

            member_ids = [anchor_id, *(n.node.id for n in neighbours)]
            edge_rows = await conn.fetch(
                queries.EDGES_WITHIN_SQL, member_ids, limit, self._graph
            )
            edges = await self._hydrate_edges(conn, edge_rows)

        return NeighbourhoodResult(
            anchor=anchor, depth=depth, neighbours=neighbours,
            edges=edges, truncated=truncated,
        )

    async def closure(
        self, anchor_id: str, *, edge_type: str, depth: int, limit: int = DEFAULT_MAX_ELEMENTS
    ) -> list[Neighbour]:
        """Transitive closure across one edge type, followed forwards."""
        if edge_type not in EDGE_LABELS:
            raise ValueError(f"unknown edge label {edge_type!r} reached the repository")
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(
                queries.CLOSURE_SQL, anchor_id, edge_type, depth, limit, self._graph
            )
            by_label: dict[str, list[str]] = {}
            for row in rows:
                by_label.setdefault(row["label"], []).append(row["id"])
            hydrated = await self._hydrate(conn, by_label)
        return [
            Neighbour(node=hydrated[row["id"]], depth=row["depth"])
            for row in rows
            if row["id"] in hydrated
        ]

    async def outgoing_edges(
        self, from_ids: Sequence[str], *, edge_type: str
    ) -> list[EdgeRecord]:
        """Outgoing edges of one type, with their properties.

        ``step`` answers "what does this point at"; this answers "what does the pointing
        itself say". The Transpiler's contract needs the second: a source field's target
        column is carried on the MAPS_TO edge, because §4.1.1 declares no column node.
        """
        if edge_type not in EDGE_LABELS:
            raise ValueError(f"unknown edge label {edge_type!r} reached the repository")
        wanted = list(dict.fromkeys(from_ids))
        if not wanted:
            return []
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(
                queries.OUTGOING_EDGES_SQL, wanted, edge_type, self._graph
            )
            return await self._hydrate_edges(conn, rows)

    async def step(
        self, from_ids: Sequence[str], *, edge_type: str, to_types: Sequence[str] | None = None
    ) -> list[Neighbour]:
        """One hop forwards across a named edge type from any of ``from_ids``."""
        if edge_type not in EDGE_LABELS:
            raise ValueError(f"unknown edge label {edge_type!r} reached the repository")
        wanted = list(dict.fromkeys(from_ids))
        if not wanted:
            return []
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(
                queries.DIRECTED_STEP_SQL, wanted, edge_type, self._graph
            )
            if to_types:
                allowed = set(to_types)
                rows = [r for r in rows if r["label"] in allowed]
            by_label: dict[str, list[str]] = {}
            for row in rows:
                by_label.setdefault(row["label"], []).append(row["id"])
            hydrated = await self._hydrate(conn, by_label)
        seen: set[str] = set()
        out: list[Neighbour] = []
        for row in rows:
            if row["id"] in hydrated and row["id"] not in seen:
                seen.add(row["id"])
                out.append(Neighbour(node=hydrated[row["id"]], depth=1))
        return out

    async def run_read_only_cypher(
        self,
        query: str,
        columns: Sequence[str],
        params: dict[str, Any],
        *,
        timeout_seconds: int,
        row_limit: int,
    ) -> tuple[list[dict[str, Any]], bool]:
        """Execute a caller's Cypher under a read-only transaction.

        The lexical guard in ``cypher.py`` produces the error messages; this is what makes
        the promise true. READ ONLY is enforced by PostgreSQL, so a write that slipped past
        the guard fails here rather than landing in the graph.
        """
        definition = ", ".join(f'"{name}" ag_catalog.agtype' for name in columns)
        sql = (
            f"SELECT * FROM ag_catalog.cypher('{self._graph}', $${query}$$, $1) "
            f"AS ({definition})"
        )
        collected: list[dict[str, Any]] = []
        truncated = False
        try:
            async with self._pool.acquire() as conn, conn.transaction(readonly=True):
                await conn.execute(f"SET LOCAL statement_timeout = {int(timeout_seconds) * 1000}")
                cursor = await conn.cursor(sql, agtype.encode_params(params))
                fetched = await cursor.fetch(row_limit + 1)
                truncated = len(fetched) > row_limit
                for record in fetched[:row_limit]:
                    collected.append({name: agtype.decode(record[name]) for name in columns})
        except asyncpg.PostgresSyntaxError as exc:
            raise CypherExecutionError(f"the query is not valid Cypher: {exc}") from exc
        except asyncpg.QueryCanceledError as exc:
            raise CypherTimeoutError(
                f"the query exceeded the {timeout_seconds}-second limit"
            ) from exc
        except asyncpg.ReadOnlySQLTransactionError as exc:
            raise CypherExecutionError(
                "the query attempted a write; this endpoint is read-only"
            ) from exc
        except asyncpg.PostgresError as exc:
            raise CypherExecutionError(str(exc)) from exc
        return collected, truncated

    # --------------------------------------------------------------------- writes

    async def create_node(
        self, label: str, properties: dict[str, Any], event: PlatformEvent | None = None
    ) -> dict[str, Any]:
        result = await self.create_nodes(
            [(label, properties)], [event] if event is not None else ()
        )
        return result[0]

    async def create_nodes(
        self,
        elements: Sequence[tuple[str, dict[str, Any]]],
        events: Sequence[PlatformEvent] = (),
    ) -> list[dict[str, Any]]:
        """Create nodes in one transaction. All succeed or none do.

        A batch is atomic because a harvest writes a workbook's nodes and edges as one
        unit (spec §8.4: "writes graph fragments transactionally per workbook"); a
        half-written workbook is worse than none. The mutation events go into the same
        transaction, so the record cannot be missing a fact the graph has.
        """
        if not elements:
            return []
        for label, _ in elements:
            if label not in NODE_LABELS:
                raise ValueError(f"unknown node label {label!r} reached the repository")

        created: list[dict[str, Any]] = []
        try:
            async with self._pool.acquire() as conn, conn.transaction():
                await conn.executemany(
                    f"INSERT INTO {_INDEX_TABLE} (graph, id, kind, label) "
                    f"VALUES ($1, $2, 'node', $3)",
                    [(self._graph, properties["id"], label) for label, properties in elements],
                )
                for label, properties in elements:
                    literal, params = agtype.property_map(properties)
                    rows = await self._run(
                        conn, f"CREATE (n:{label} {literal}) RETURN n", params
                    )
                    vertex = agtype.decode_vertex(rows[0]["v"]) if rows else None
                    if vertex is None:
                        raise GraphUnavailableError(
                            f"AGE did not return the created {label} node"
                        )
                    created.append(vertex)
                await self._append_events(conn, events)
        except asyncpg.UniqueViolationError as exc:
            raise DuplicateElementError(
                "an element with this id already exists in the graph"
            ) from exc
        except asyncpg.PostgresError as exc:
            logger.exception("node write failed")
            raise GraphUnavailableError(str(exc)) from exc
        return created

    async def create_edge(
        self,
        label: str,
        *,
        from_id: str,
        to_id: str,
        from_label: str,
        to_label: str,
        properties: dict[str, Any],
        event: PlatformEvent | None = None,
    ) -> dict[str, Any]:
        if label not in EDGE_LABELS:
            raise ValueError(f"unknown edge label {label!r} reached the repository")
        if from_label not in NODE_LABELS or to_label not in NODE_LABELS:
            raise ValueError("endpoint labels must be known node types")

        literal, params = agtype.property_map(properties)
        params |= {"from_id": from_id, "to_id": to_id}
        query = (
            f"MATCH (a:{from_label}), (b:{to_label}) "
            f"WHERE a.id = $from_id AND b.id = $to_id "
            f"CREATE (a)-[e:{label} {literal}]->(b) RETURN e"
        )
        try:
            async with self._pool.acquire() as conn, conn.transaction():
                await conn.execute(
                    f"INSERT INTO {_INDEX_TABLE} (graph, id, kind, label) "
                    f"VALUES ($1, $2, 'edge', $3)",
                    self._graph,
                    properties["id"],
                    label,
                )
                # The adjacency index is what the neighbourhood traversal walks. Written
                # in the same transaction as the edge, so the two cannot disagree.
                await conn.execute(
                    f"INSERT INTO {_EDGE_INDEX_TABLE} (graph, id, label, from_id, to_id) "
                    f"VALUES ($1, $2, $3, $4, $5)",
                    self._graph,
                    properties["id"],
                    label,
                    from_id,
                    to_id,
                )
                rows = await self._run(conn, query, params)
                edge = agtype.decode_edge(rows[0]["v"]) if rows else None
                if edge is None:
                    # MATCH found no endpoints. The API resolves labels before calling,
                    # so this means the graph changed underneath us.
                    raise GraphUnavailableError(
                        "edge endpoints were not found when the edge was written"
                    )
                await self._append_events(conn, [event] if event is not None else [])
        except asyncpg.UniqueViolationError as exc:
            raise DuplicateElementError(
                "an element with this id already exists in the graph"
            ) from exc
        except asyncpg.PostgresError as exc:
            logger.exception("edge write failed")
            raise GraphUnavailableError(str(exc)) from exc
        return edge


    # --------------------------------------------------- the mutation outbox (S1.1.3)

    async def _append_events(
        self, conn: asyncpg.Connection, events: Sequence[PlatformEvent]
    ) -> None:
        """Write events inside the caller's transaction.

        Never called outside one. An event committed apart from its mutation would break
        the property the whole story rests on.
        """
        if not events:
            return
        await conn.executemany(
            f"""
            INSERT INTO {_EVENT_TABLE}
                (event_id, graph, type, source, subject, element_kind, label,
                 time, principal, run_id, data)
            VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11::jsonb)
            """,
            [
                (
                    event.id,
                    self._graph,
                    event.type.value,
                    event.source,
                    event.subject,
                    event.element_kind,
                    event.label,
                    # asyncpg binds timestamptz from a datetime, not from the canonical
                    # string the event carries on the wire.
                    _parse_timestamp(event.time),
                    event.principal,
                    event.run_id,
                    json.dumps(event.data),
                )
                for event in events
            ],
        )

    async def append_event(self, event: PlatformEvent) -> None:
        """Write one event that has no mutation to be committed with.

        The rule everywhere else — an event goes into the outbox in the same transaction
        as the change it records — exists so the record cannot disagree with the graph.
        A notice records no graph change, so there is nothing for it to disagree with, and
        it commits on its own. Refusing anything that *does* mutate keeps that argument
        true rather than assumed.
        """
        if event.type.mutates_graph:
            raise ValueError(
                f"{event.type.value} changes the graph, so it must be committed with the "
                f"mutation it records, not appended on its own"
            )
        async with self._pool.acquire() as conn, conn.transaction():
            await self._append_events(conn, [event])

    async def current_version(self) -> tuple[int, str | None]:
        """The graph's current version: the highest event sequence, and when it landed.

        Zero for a graph nothing has been written to — an addressable version meaning the
        empty graph, not an error (S1.3.2).
        """
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                f"SELECT seq, time FROM {_EVENT_TABLE} WHERE graph = $1 "
                f"ORDER BY seq DESC LIMIT 1",
                self._graph,
            )
        if row is None:
            return 0, None
        return int(row["seq"]), _iso(row["time"])

    async def events_of_type(
        self, event_type: EventType, *, limit: int = 100
    ) -> list[StoredEvent]:
        """The most recent events of one type, newest first.

        The one read that does not want the stream in order: Platform Health wants the
        last twenty drift notices, and finding them by paging the whole outbox would cost
        a scan of every mutation ever written.
        """
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(
                f"""
                SELECT seq, event_id, type, source, subject, label, time, principal,
                       run_id, data, published_at
                  FROM {_EVENT_TABLE}
                 WHERE graph = $1 AND type = $2
                 ORDER BY seq DESC
                 LIMIT $3
                """,
                self._graph,
                event_type.value,
                limit,
            )
        return [_stored_event(row) for row in rows]

    async def read_events(
        self, *, after: int = 0, limit: int = 1000, subject: str | None = None
    ) -> list[StoredEvent]:
        """The event stream in commit order, from an offset.

        Ordering is by ``seq``, which PostgreSQL assigns at insert. A transaction cannot
        insert an edge event before the events for its endpoints, because the write path
        refuses an edge whose endpoints are not already committed — so replaying in seq
        order never applies an edge before its nodes.
        """
        clauses = ["graph = $1", "seq > $2"]
        args: list[Any] = [self._graph, after]
        if subject is not None:
            clauses.append(f"subject = ${len(args) + 1}")
            args.append(subject)
        args.append(limit)

        sql = (
            f"SELECT seq, event_id, type, source, subject, label, time, principal, "
            f"run_id, data, published_at FROM {_EVENT_TABLE} "
            f"WHERE {' AND '.join(clauses)} ORDER BY seq LIMIT ${len(args)}"
        )
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(sql, *args)
        return [_stored_event(row) for row in rows]

    # ------------------------------------------------------------- upsert and retire

    async def upsert_nodes(
        self, elements: Sequence[tuple[str, dict[str, Any]]], events: Sequence[PlatformEvent] = ()
    ) -> list[dict[str, Any]]:
        """Create or wholly replace nodes, with their events, in one transaction.

        Replace rather than merge: an upsert event carries the node's complete property
        set, so applying it must leave exactly that set. A merge would let a property
        removed by the writer survive in the graph, and replay would then disagree with
        the live estate.
        """
        if not elements:
            return []
        for label, _ in elements:
            if label not in NODE_LABELS:
                raise ValueError(f"unknown node label {label!r} reached the repository")

        for attempt in range(_UPSERT_ATTEMPTS):
            try:
                return await self._upsert_nodes_once(elements, events)
            except ConcurrentUpdateError:
                if attempt == _UPSERT_ATTEMPTS - 1:
                    raise
                # Apache AGE fails an update whose vertex changed under it rather than
                # blocking on the row. Two harvests touching one shared node is a real
                # case, so the transaction is retried rather than the workbook failed.
                await asyncio.sleep(_UPSERT_BACKOFF_SECONDS * (attempt + 1))
        raise AssertionError("unreachable")  # pragma: no cover

    async def _upsert_nodes_once(
        self,
        elements: Sequence[tuple[str, dict[str, Any]]],
        events: Sequence[PlatformEvent] = (),
    ) -> list[dict[str, Any]]:
        written: list[dict[str, Any]] = []
        try:
            async with self._pool.acquire() as conn, conn.transaction():
                for label, properties in elements:
                    node_id = str(properties["id"])
                    existing = await conn.fetchrow(
                        f"SELECT label FROM {_INDEX_TABLE} "
                        f"WHERE graph = $1 AND id = $2 AND kind = 'node'",
                        self._graph,
                        node_id,
                    )
                    if existing is not None and existing["label"] != label:
                        raise InvalidRequestError(
                            f"node '{node_id}' is a {existing['label']}; an upsert cannot "
                            f"change a node's type to {label}"
                        )

                    if existing is None:
                        # A node that has just been created has not been updated.
                        created = {
                            name: value
                            for name, value in properties.items()
                            if name not in _UPDATE_ONLY
                        }
                        literal, params = agtype.property_map(created)
                        await conn.execute(
                            f"INSERT INTO {_INDEX_TABLE} (graph, id, kind, label) "
                            f"VALUES ($1, $2, 'node', $3)",
                            self._graph,
                            node_id,
                            label,
                        )
                        rows = await self._run(
                            conn, f"CREATE (n:{label} {literal}) RETURN n", params
                        )
                    else:
                        literal, params = agtype.property_map(properties)
                        # Carry the original creation attribution across the property
                        # replacement, in one statement rather than a read then a write:
                        # an upsert changes a node, it does not create it again.
                        rows = await self._run(
                            conn,
                            f"MATCH (n:{label}) WHERE n.id = $p_id "
                            f"WITH n, n.created_by AS cb, n.created_at AS ca, "
                            f"n.created_in_run AS cr "
                            f"SET n = {literal} "
                            f"SET n.created_by = cb, n.created_at = ca, "
                            f"n.created_in_run = cr "
                            f"RETURN n",
                            params,
                        )
                        await conn.execute(
                            f"UPDATE {_INDEX_TABLE} SET retired_at = $3 "
                            f"WHERE graph = $1 AND id = $2",
                            self._graph,
                            node_id,
                            _parse_timestamp(properties.get("retired_at")),
                        )
                    vertex = agtype.decode_vertex(rows[0]["v"]) if rows else None
                    if vertex is None:
                        raise GraphUnavailableError(
                            f"AGE did not return the upserted {label} node"
                        )
                    written.append(vertex)
                await self._append_events(conn, events)
        except asyncpg.PostgresError as exc:
            if _is_concurrent_update(exc):
                raise ConcurrentUpdateError(str(exc)) from exc
            logger.exception("node upsert failed")
            raise GraphUnavailableError(str(exc)) from exc
        return written

    async def upsert_edge(
        self,
        label: str,
        *,
        from_id: str,
        to_id: str,
        from_label: str,
        to_label: str,
        properties: dict[str, Any],
        event: PlatformEvent | None = None,
    ) -> dict[str, Any]:
        """Create or wholly replace one edge, with its event, in one transaction."""
        if label not in EDGE_LABELS:
            raise ValueError(f"unknown edge label {label!r} reached the repository")
        edge_id = str(properties["id"])
        literal, params = agtype.property_map(properties)
        params |= {"from_id": from_id, "to_id": to_id}

        try:
            async with self._pool.acquire() as conn, conn.transaction():
                existing = await conn.fetchrow(
                    f"SELECT label FROM {_INDEX_TABLE} "
                    f"WHERE graph = $1 AND id = $2 AND kind = 'edge'",
                    self._graph,
                    edge_id,
                )
                if existing is not None and existing["label"] != label:
                    raise InvalidRequestError(
                        f"edge '{edge_id}' is a {existing['label']}; an upsert cannot "
                        f"change an edge's type to {label}"
                    )
                if existing is None:
                    await conn.execute(
                        f"INSERT INTO {_INDEX_TABLE} (graph, id, kind, label) "
                        f"VALUES ($1, $2, 'edge', $3)",
                        self._graph,
                        edge_id,
                        label,
                    )
                    await conn.execute(
                        f"INSERT INTO {_EDGE_INDEX_TABLE} (graph, id, label, from_id, to_id) "
                        f"VALUES ($1, $2, $3, $4, $5)",
                        self._graph,
                        edge_id,
                        label,
                        from_id,
                        to_id,
                    )
                    rows = await self._run(
                        conn,
                        f"MATCH (a:{from_label}), (b:{to_label}) "
                        f"WHERE a.id = $from_id AND b.id = $to_id "
                        f"CREATE (a)-[e:{label} {literal}]->(b) RETURN e",
                        params,
                    )
                else:
                    rows = await self._run(
                        conn,
                        f"MATCH ()-[e:{label}]->() WHERE e.id = $p_id "
                        f"SET e = {literal} RETURN e",
                        params,
                    )
                edge = agtype.decode_edge(rows[0]["v"]) if rows else None
                if edge is None:
                    raise GraphUnavailableError("the edge was not written")
                await self._append_events(conn, [event] if event is not None else [])
        except asyncpg.PostgresError as exc:
            if _is_concurrent_update(exc):
                raise ConcurrentUpdateError(str(exc)) from exc
            logger.exception("edge upsert failed")
            raise GraphUnavailableError(str(exc)) from exc
        return edge

    async def retire_node(
        self,
        node_id: str,
        *,
        retired_at: str,
        retired_by: str,
        reason: str,
        event: PlatformEvent | None = None,
    ) -> dict[str, Any]:
        """Retire a node. It stays in the graph; nothing is deleted (S1.1.3)."""
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                f"SELECT label, retired_at FROM {_INDEX_TABLE} "
                f"WHERE graph = $1 AND id = $2 AND kind = 'node'",
                self._graph,
                node_id,
            )
            if row is None:
                raise ElementNotFoundError(f"no node with id '{node_id}'")
            if row["retired_at"] is not None:
                raise InvalidRequestError(
                    f"node '{node_id}' was already retired at {_iso(row['retired_at'])}"
                )
            label = row["label"]

            try:
                async with conn.transaction():
                    rows = await self._run(
                        conn,
                        f"MATCH (n:{label}) WHERE n.id = $node_id "
                        f"SET n.retired_at = $retired_at, n.retired_by = $retired_by, "
                        f"n.retirement_reason = $reason RETURN n",
                        {
                            "node_id": node_id,
                            "retired_at": retired_at,
                            "retired_by": retired_by,
                            "reason": reason,
                        },
                    )
                    vertex = agtype.decode_vertex(rows[0]["v"]) if rows else None
                    if vertex is None:
                        raise GraphUnavailableError("the node was not retired")
                    await conn.execute(
                        f"UPDATE {_INDEX_TABLE} SET retired_at = $3 "
                        f"WHERE graph = $1 AND id = $2",
                        self._graph,
                        node_id,
                        _parse_timestamp(retired_at),
                    )
                    await self._append_events(conn, [event] if event is not None else [])
            except asyncpg.PostgresError as exc:
                logger.exception("node retirement failed")
                raise GraphUnavailableError(str(exc)) from exc
        return vertex

    async def retire_edge(
        self,
        edge_id: str,
        *,
        retired_at: str,
        retired_by: str,
        reason: str,
        event: PlatformEvent | None = None,
    ) -> dict[str, Any]:
        """Retire an edge. Its endpoints cannot change once created, so 'move' is retiring
        the old relationship and creating a new one (story S3.1.2) — the same shape
        ``retire_node`` gives nodes, and the reason ``estate_edge_index`` gets its own
        ``retired_at`` alongside the element index's (v0013): a traversal filters on it on
        every hop, and a join for a fact this common was worth avoiding when the adjacency
        table was built (v0002), so it stays worth avoiding now.
        """
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                f"SELECT label, retired_at FROM {_INDEX_TABLE} "
                f"WHERE graph = $1 AND id = $2 AND kind = 'edge'",
                self._graph,
                edge_id,
            )
            if row is None:
                raise ElementNotFoundError(f"no edge with id '{edge_id}'")
            if row["retired_at"] is not None:
                raise InvalidRequestError(
                    f"edge '{edge_id}' was already retired at {_iso(row['retired_at'])}"
                )
            label = row["label"]

            try:
                async with conn.transaction():
                    rows = await self._run(
                        conn,
                        f"MATCH ()-[e:{label}]->() WHERE e.id = $edge_id "
                        f"SET e.retired_at = $retired_at, e.retired_by = $retired_by, "
                        f"e.retirement_reason = $reason RETURN e",
                        {
                            "edge_id": edge_id,
                            "retired_at": retired_at,
                            "retired_by": retired_by,
                            "reason": reason,
                        },
                    )
                    edge = agtype.decode_edge(rows[0]["v"]) if rows else None
                    if edge is None:
                        raise GraphUnavailableError("the edge was not retired")
                    parsed = _parse_timestamp(retired_at)
                    await conn.execute(
                        f"UPDATE {_INDEX_TABLE} SET retired_at = $3 "
                        f"WHERE graph = $1 AND id = $2",
                        self._graph,
                        edge_id,
                        parsed,
                    )
                    await conn.execute(
                        f"UPDATE {_EDGE_INDEX_TABLE} SET retired_at = $3 "
                        f"WHERE graph = $1 AND id = $2",
                        self._graph,
                        edge_id,
                        parsed,
                    )
                    await self._append_events(conn, [event] if event is not None else [])
            except asyncpg.PostgresError as exc:
                logger.exception("edge retirement failed")
                raise GraphUnavailableError(str(exc)) from exc
        return edge

    async def nodes_of_type(self, label: str, *, limit: int = 1000) -> list[NodeRecord]:
        """Every live node of one label.

        Reasonable for User, which numbers in the hundreds on a site; it would not be for
        Field, and no caller asks it to be.
        """
        if label not in NODE_LABELS:
            raise ValueError(f"unknown node label {label!r} reached the repository")
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(
                queries.ELEMENTS_OF_LABEL_SQL, self._graph, label, limit
            )
            found = await self._hydrate(conn, {label: [row["id"] for row in rows]})
        return [found[row["id"]] for row in rows if row["id"] in found]

    async def incoming_counts(
        self, node_ids: Sequence[str], *, edge_type: str
    ) -> dict[str, int]:
        """How many edges of one type point at each node."""
        if edge_type not in EDGE_LABELS:
            raise ValueError(f"unknown edge label {edge_type!r} reached the repository")
        wanted = list(dict.fromkeys(node_ids))
        if not wanted:
            return {}
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(
                queries.INCOMING_COUNTS_SQL, self._graph, edge_type, wanted
            )
        return {row["to_id"]: row["n"] for row in rows}

    # ----------------------------------------------------------- replay comparison

    async def dump(self) -> dict[str, Any]:
        """Every node and edge in this graph, for comparison against a replay.

        Returns plain dictionaries keyed by element id so two graphs compare with ``==``
        and a difference points at the element that differs.
        """
        async with self._pool.acquire() as conn:
            elements = await conn.fetch(queries.DUMP_ELEMENTS_SQL, self._graph)
            endpoints = {
                row["id"]: (row["from_id"], row["to_id"])
                for row in await conn.fetch(queries.DUMP_EDGE_ENDPOINTS_SQL, self._graph)
            }

            by_label: dict[str, list[str]] = {}
            kinds: dict[str, str] = {}
            for row in elements:
                by_label.setdefault(row["label"], []).append(row["id"])
                kinds[row["id"]] = row["kind"]

            nodes: dict[str, Any] = {}
            edges: dict[str, Any] = {}
            for label, ids in by_label.items():
                for start in range(0, len(ids), 5_000):
                    batch = ids[start : start + 5_000]
                    rows = await conn.fetch(
                        queries.hydrate_nodes(self._graph, [label]),
                        [queries.agtype_literal(i) for i in batch],
                    )
                    for row in rows:
                        properties = queries.decode_properties(row["properties"])
                        element_id = str(properties["id"])
                        if kinds.get(element_id) == "edge":
                            from_id, to_id = endpoints.get(element_id, (None, None))
                            edges[element_id] = {
                                "type": label,
                                "properties": properties,
                                "from_id": from_id,
                                "to_id": to_id,
                            }
                        else:
                            nodes[element_id] = {"type": label, "properties": properties}
        return {"nodes": nodes, "edges": edges}


async def create_pool(settings: Settings) -> asyncpg.Pool:
    """Open the connection pool, with AGE loaded on every connection.

    ``LOAD 'age'`` and the search path are per-session, so they are set in the pool's
    connection initialiser rather than once at start-up.
    """

    async def init(conn: asyncpg.Connection) -> None:
        """Once per physical connection."""
        await conn.execute("LOAD 'age'")
        # asyncpg has no built-in codec for agtype; treat it as text and convert in
        # graph/agtype.py. Registered by schema, so it does not depend on search_path.
        await conn.set_type_codec(
            "agtype",
            schema="ag_catalog",
            encoder=str,
            decoder=str,
            format="text",
        )

    async def setup(conn: asyncpg.Connection) -> None:
        """Once per acquisition.

        asyncpg runs RESET ALL when a connection returns to the pool, which discards a
        search_path set in `init`. AGE needs ag_catalog on the path for the operators its
        Cypher expansion emits, so it is re-established here on every acquire.
        """
        await conn.execute('SET search_path = ag_catalog, "$user", public')

    pool = await asyncpg.create_pool(
        dsn=settings.dsn,
        min_size=settings.pool_min_size,
        max_size=settings.pool_max_size,
        init=init,
        setup=setup,
        command_timeout=30,
    )
    if pool is None:  # pragma: no cover - asyncpg returns None only on misuse
        raise GraphUnavailableError("could not open a connection pool")
    return pool
