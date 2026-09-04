"""In-memory graph store.

Stands in for Apache AGE so the validation, API, GraphQL and replay tests run without a
database. It implements the same ``GraphRepository`` protocol and the same failure modes
the write path depends on: duplicate ids raise, an unknown label reaching the repository
is a programming error, and a retired node is skipped by reads.

The traversal here is a plain breadth-first search over the same adjacency the real
repository keeps in ``estate_edge_index``, so the two agree on what a neighbourhood is.
Whether the SQL implementation actually agrees is checked by the integration suite, which
runs the same assertions against AGE.
"""

from __future__ import annotations

from collections import deque
from collections.abc import Iterable, Sequence
from typing import Any

from astra_graph.errors import (
    DuplicateElementError,
    ElementNotFoundError,
    InvalidRequestError,
)
from astra_graph.events import EventType, PlatformEvent, StoredEvent
from astra_graph.graph.model import EdgeRecord, Neighbour, NeighbourhoodResult, NodeRecord
from astra_graph.ontology import EDGE_LABELS, NODE_LABELS, node_type


class InMemoryGraphRepository:
    def __init__(self) -> None:
        self.nodes: dict[str, dict[str, Any]] = {}
        self.edges: dict[str, dict[str, Any]] = {}
        self.events: list[StoredEvent] = []

    # ---------------------------------------------------------------- basic reads

    async def health(self) -> None:
        return None

    async def labels_for(self, ids: Iterable[str]) -> dict[str, str]:
        return {
            node_id: self.nodes[node_id]["label"] for node_id in ids if node_id in self.nodes
        }

    async def get_node(self, node_id: str) -> dict[str, Any] | None:
        return self.nodes.get(node_id)

    async def get_edge(self, edge_id: str) -> dict[str, Any] | None:
        found = self.edges.get(edge_id)
        if found is None:
            return None
        return {"label": found["label"], "properties": found["properties"]}

    # ------------------------------------------------------------- the query API

    def _retired(self, node_id: str) -> bool:
        return bool(self.nodes[node_id]["properties"].get("retired_at"))

    def _record(self, node_id: str) -> NodeRecord:
        stored = self.nodes[node_id]
        return NodeRecord(label=stored["label"], properties=dict(stored["properties"]))

    async def get_node_record(self, node_id: str) -> NodeRecord | None:
        return self._record(node_id) if node_id in self.nodes else None

    async def get_edge_record(self, edge_id: str) -> EdgeRecord | None:
        stored = self.edges.get(edge_id)
        if stored is None:
            return None
        return EdgeRecord(
            label=stored["label"],
            properties=dict(stored["properties"]),
            from_id=stored["from_id"],
            to_id=stored["to_id"],
        )

    async def get_nodes(self, ids: Sequence[str]) -> list[NodeRecord]:
        return [self._record(i) for i in dict.fromkeys(ids) if i in self.nodes]

    async def get_node_by_luid(self, label: str, luid: str) -> NodeRecord | None:
        if label not in NODE_LABELS:
            raise ValueError(f"unknown node label {label!r} reached the repository")
        declared = node_type(label)
        if declared is None or "luid" not in declared.declared_property_names:
            raise ValueError(f"node type {label!r} does not carry a luid")
        for node_id, stored in self.nodes.items():
            if stored["label"] == label and stored["properties"].get("luid") == luid:
                return self._record(node_id)
        return None

    def _adjacency(self, edge_types: Sequence[str] | None) -> dict[str, list[str]]:
        adjacency: dict[str, list[str]] = {}
        allowed = set(edge_types) if edge_types else None
        for stored in self.edges.values():
            if allowed is not None and stored["label"] not in allowed:
                continue
            adjacency.setdefault(stored["from_id"], []).append(stored["to_id"])
            adjacency.setdefault(stored["to_id"], []).append(stored["from_id"])
        return adjacency

    async def neighbourhood(
        self,
        anchor_id: str,
        *,
        depth: int,
        edge_types: Sequence[str] | None = None,
        node_types: Sequence[str] | None = None,
        limit: int = 10_000,
        include_retired: bool = False,
    ) -> NeighbourhoodResult:
        if anchor_id not in self.nodes:
            raise ElementNotFoundError(f"no node with id '{anchor_id}'")

        adjacency = self._adjacency(edge_types)
        hops: dict[str, int] = {anchor_id: 0}
        queue: deque[str] = deque([anchor_id])
        while queue:
            current = queue.popleft()
            if hops[current] >= depth:
                continue
            for neighbour in adjacency.get(current, []):
                if neighbour not in hops:
                    hops[neighbour] = hops[current] + 1
                    queue.append(neighbour)

        ordered = sorted(hops.items(), key=lambda item: (item[1], item[0]))
        truncated = len(ordered) > limit
        ordered = ordered[:limit]

        allowed_nodes = set(node_types) if node_types else None
        neighbours = [
            Neighbour(node=self._record(node_id), depth=hop)
            for node_id, hop in ordered
            if node_id != anchor_id
            and node_id in self.nodes
            and (allowed_nodes is None or self.nodes[node_id]["label"] in allowed_nodes)
            and (include_retired or not self._retired(node_id))
        ]

        member_ids = {anchor_id, *(n.node.id for n in neighbours)}
        edges = [
            EdgeRecord(
                label=stored["label"],
                properties=dict(stored["properties"]),
                from_id=stored["from_id"],
                to_id=stored["to_id"],
            )
            for stored in self.edges.values()
            if stored["from_id"] in member_ids and stored["to_id"] in member_ids
        ]
        edges.sort(key=lambda e: e.id)

        return NeighbourhoodResult(
            anchor=self._record(anchor_id),
            depth=depth,
            neighbours=neighbours,
            edges=edges,
            truncated=truncated,
        )

    async def closure(
        self, anchor_id: str, *, edge_type: str, depth: int, limit: int = 10_000
    ) -> list[Neighbour]:
        if edge_type not in EDGE_LABELS:
            raise ValueError(f"unknown edge label {edge_type!r} reached the repository")
        forward: dict[str, list[str]] = {}
        for stored in self.edges.values():
            if stored["label"] == edge_type:
                forward.setdefault(stored["from_id"], []).append(stored["to_id"])

        hops: dict[str, int] = {anchor_id: 0}
        queue: deque[str] = deque([anchor_id])
        while queue:
            current = queue.popleft()
            if hops[current] >= depth:
                continue
            for neighbour in forward.get(current, []):
                if neighbour not in hops:
                    hops[neighbour] = hops[current] + 1
                    queue.append(neighbour)

        ordered = sorted(
            (
                (i, h)
                for i, h in hops.items()
                if h > 0 and i in self.nodes and not self._retired(i)
            ),
            key=lambda item: (item[1], item[0]),
        )
        return [Neighbour(node=self._record(i), depth=h) for i, h in ordered[:limit]]

    async def outgoing_edges(
        self, from_ids: Sequence[str], *, edge_type: str
    ) -> list[EdgeRecord]:
        if edge_type not in EDGE_LABELS:
            raise ValueError(f"unknown edge label {edge_type!r} reached the repository")
        sources = set(from_ids)
        out = [
            EdgeRecord(
                label=stored["label"],
                properties=dict(stored["properties"]),
                from_id=stored["from_id"],
                to_id=stored["to_id"],
            )
            for stored in self.edges.values()
            if stored["label"] == edge_type
            and stored["from_id"] in sources
            and stored["to_id"] in self.nodes
            and not self._retired(stored["to_id"])
        ]
        out.sort(key=lambda edge: edge.id)
        return out

    async def step(
        self, from_ids: Sequence[str], *, edge_type: str, to_types: Sequence[str] | None = None
    ) -> list[Neighbour]:
        if edge_type not in EDGE_LABELS:
            raise ValueError(f"unknown edge label {edge_type!r} reached the repository")
        sources = set(from_ids)
        allowed = set(to_types) if to_types else None
        seen: set[str] = set()
        out: list[Neighbour] = []
        for stored in self.edges.values():
            if stored["label"] != edge_type or stored["from_id"] not in sources:
                continue
            target = stored["to_id"]
            if target in seen or target not in self.nodes:
                continue
            if allowed is not None and self.nodes[target]["label"] not in allowed:
                continue
            if self._retired(target):
                continue
            seen.add(target)
            out.append(Neighbour(node=self._record(target), depth=1))
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
        """Not simulated: executing Cypher is the store's job.

        The guard that decides what reaches here is unit-tested directly, and the
        execution path is covered by the integration suite against AGE.
        """
        raise NotImplementedError("read-only Cypher runs against Apache AGE only")

    # -------------------------------------------------------------------- writes

    async def create_node(
        self, label: str, properties: dict[str, Any], event: PlatformEvent | None = None
    ) -> dict[str, Any]:
        created = await self.create_nodes(
            [(label, properties)], [event] if event is not None else ()
        )
        return created[0]

    async def create_nodes(
        self,
        elements: Sequence[tuple[str, dict[str, Any]]],
        events: Sequence[PlatformEvent] = (),
    ) -> list[dict[str, Any]]:
        for label, properties in elements:
            if label not in NODE_LABELS:
                raise ValueError(f"unknown node label {label!r} reached the repository")
            if properties["id"] in self.nodes or properties["id"] in self.edges:
                raise DuplicateElementError(
                    "an element with this id already exists in the graph"
                )
        created: list[dict[str, Any]] = []
        for label, properties in elements:
            record = {"label": label, "properties": dict(properties)}
            self.nodes[properties["id"]] = record
            created.append(record)
        self._append(events)
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
        if properties["id"] in self.edges or properties["id"] in self.nodes:
            raise DuplicateElementError("an element with this id already exists in the graph")
        record = {
            "label": label,
            "properties": dict(properties),
            "from_id": from_id,
            "to_id": to_id,
        }
        self.edges[properties["id"]] = record
        self._append([event] if event is not None else [])
        return {"label": label, "properties": record["properties"]}

    # ----------------------------------------------------- events, upsert, retire

    def _append(self, events: Sequence[PlatformEvent]) -> None:
        """The outbox. In the real store this is one INSERT inside the mutation's own
        transaction; here the list append stands in for that atomicity."""
        for event in events:
            self.events.append(
                StoredEvent(
                    sequence=len(self.events) + 1,
                    id=event.id,
                    type=event.type,
                    source=event.source,
                    subject=event.subject,
                    label=event.label,
                    time=event.time,
                    principal=event.principal,
                    run_id=event.run_id,
                    data=event.data,
                )
            )

    async def append_event(self, event: PlatformEvent) -> None:
        if event.type.mutates_graph:
            raise ValueError(
                f"{event.type.value} changes the graph, so it must be committed with the "
                f"mutation it records, not appended on its own"
            )
        self._append([event])

    async def current_version(self) -> tuple[int, str | None]:
        if not self.events:
            return 0, None
        latest = self.events[-1]
        return latest.sequence, latest.time

    async def events_of_type(
        self, event_type: EventType, *, limit: int = 100
    ) -> list[StoredEvent]:
        matching = [event for event in self.events if event.type is event_type]
        return list(reversed(matching))[:limit]

    async def read_events(
        self, *, after: int = 0, limit: int = 1000, subject: str | None = None
    ) -> list[StoredEvent]:
        matching = [
            event
            for event in self.events
            if event.sequence > after and (subject is None or event.subject == subject)
        ]
        return matching[:limit]

    async def upsert_nodes(
        self,
        elements: Sequence[tuple[str, dict[str, Any]]],
        events: Sequence[PlatformEvent] = (),
    ) -> list[dict[str, Any]]:
        written: list[dict[str, Any]] = []
        for label, properties in elements:
            if label not in NODE_LABELS:
                raise ValueError(f"unknown node label {label!r} reached the repository")
            node_id = properties["id"]
            existing = self.nodes.get(node_id)
            if existing is not None and existing["label"] != label:
                raise InvalidRequestError(
                    f"node '{node_id}' is a {existing['label']}; an upsert cannot change "
                    f"a node's type to {label}"
                )
            merged = dict(properties)
            if existing is None:
                # A node that has just been created has not been updated.
                merged.pop("updated_by", None)
                merged.pop("updated_at", None)
            else:
                # An upsert changes a node; it does not create it again.
                for name in ("created_by", "created_at", "created_in_run"):
                    if name in existing["properties"]:
                        merged[name] = existing["properties"][name]
                    else:
                        merged.pop(name, None)
            record = {"label": label, "properties": merged}
            self.nodes[node_id] = record
            written.append(record)
        self._append(events)
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
        if label not in EDGE_LABELS:
            raise ValueError(f"unknown edge label {label!r} reached the repository")
        edge_id = properties["id"]
        existing = self.edges.get(edge_id)
        if existing is not None and existing["label"] != label:
            raise InvalidRequestError(
                f"edge '{edge_id}' is a {existing['label']}; an upsert cannot change an "
                f"edge's type to {label}"
            )
        record = {
            "label": label,
            "properties": dict(properties),
            "from_id": from_id,
            "to_id": to_id,
        }
        self.edges[edge_id] = record
        self._append([event] if event is not None else [])
        return {"label": label, "properties": record["properties"]}

    async def retire_node(
        self,
        node_id: str,
        *,
        retired_at: str,
        retired_by: str,
        reason: str,
        event: PlatformEvent | None = None,
    ) -> dict[str, Any]:
        record = self.nodes.get(node_id)
        if record is None:
            raise ElementNotFoundError(f"no node with id '{node_id}'")
        if record["properties"].get("retired_at"):
            raise InvalidRequestError(
                f"node '{node_id}' was already retired at "
                f"{record['properties']['retired_at']}"
            )
        record["properties"]["retired_at"] = retired_at
        record["properties"]["retired_by"] = retired_by
        record["properties"]["retirement_reason"] = reason
        self._append([event] if event is not None else [])
        return {"label": record["label"], "properties": dict(record["properties"])}

    async def retire_edge(
        self,
        edge_id: str,
        *,
        retired_at: str,
        retired_by: str,
        reason: str,
        event: PlatformEvent | None = None,
    ) -> dict[str, Any]:
        record = self.edges.get(edge_id)
        if record is None:
            raise ElementNotFoundError(f"no edge with id '{edge_id}'")
        if record["properties"].get("retired_at"):
            raise InvalidRequestError(
                f"edge '{edge_id}' was already retired at "
                f"{record['properties']['retired_at']}"
            )
        record["properties"]["retired_at"] = retired_at
        record["properties"]["retired_by"] = retired_by
        record["properties"]["retirement_reason"] = reason
        self._append([event] if event is not None else [])
        return {"label": record["label"], "properties": dict(record["properties"])}

    async def nodes_of_type(self, label: str, *, limit: int = 1000) -> list[NodeRecord]:
        if label not in NODE_LABELS:
            raise ValueError(f"unknown node label {label!r} reached the repository")
        matching = [
            node_id
            for node_id, record in self.nodes.items()
            if record["label"] == label and not self._retired(node_id)
        ]
        return [self._record(node_id) for node_id in sorted(matching)[:limit]]

    async def incoming_counts(
        self, node_ids: Sequence[str], *, edge_type: str
    ) -> dict[str, int]:
        if edge_type not in EDGE_LABELS:
            raise ValueError(f"unknown edge label {edge_type!r} reached the repository")
        wanted = set(node_ids)
        counts: dict[str, int] = {}
        for stored in self.edges.values():
            if stored["label"] == edge_type and stored["to_id"] in wanted:
                counts[stored["to_id"]] = counts.get(stored["to_id"], 0) + 1
        return counts

    async def dump(self) -> dict[str, Any]:
        return {
            "nodes": {
                node_id: {"type": r["label"], "properties": dict(r["properties"])}
                for node_id, r in self.nodes.items()
            },
            "edges": {
                edge_id: {
                    "type": r["label"],
                    "properties": dict(r["properties"]),
                    "from_id": r["from_id"],
                    "to_id": r["to_id"],
                }
                for edge_id, r in self.edges.items()
            },
        }


class BoundedEventSource:
    """The event stream up to one offset, and no further.

    Replay's source protocol, narrowed to a version. Used to build the in-memory
    equivalent of ``versions.HistoricalGraphReader``: the production reader answers from
    indexed lookups over the outbox, this one replays, and both must agree — which is
    exactly what the integration suite checks.
    """

    def __init__(self, repository: InMemoryGraphRepository, version: int) -> None:
        self._repository = repository
        self._version = version

    async def read_events(
        self, *, after: int = 0, limit: int = 1000, subject: str | None = None
    ) -> list[StoredEvent]:
        matching = [
            event
            for event in self._repository.events
            if after < event.sequence <= self._version
        ]
        return matching[:limit]

    async def dump(self) -> dict[str, Any]:
        return await self._repository.dump()


async def historical(repository: InMemoryGraphRepository, version: int) -> InMemoryGraphRepository:
    """The in-memory graph as it stood at an event offset."""
    from astra_graph.replay import replay

    target = InMemoryGraphRepository()
    await replay(BoundedEventSource(repository, version), target)
    return target


class StubEstateReader:
    """An estate already read.

    ``EstateReader`` is four SQL queries; everything the Explorer decides — filtering,
    banding, facet counts, the tree — is pure and lives in ``Estate``. So the routes are
    tested against a set of rows, and the queries are tested against PostgreSQL.
    """

    def __init__(self, rows: list[Any]) -> None:
        self.rows = rows

    async def read(self, *, limit: int = 20_000, scope: Any = None) -> Any:
        from dataclasses import replace as _replace

        from astra_graph.estate import Estate, WorkbookRow

        rows = []
        for row in self.rows:
            state = (scope or {}).get(row.id)
            rows.append(
                _replace(
                    row,
                    tier=getattr(state, "tier", None),
                    withdrawn=bool(getattr(state, "withdrawn", False)),
                    withdrawn_reason=getattr(state, "withdrawn_reason", None),
                )
                if state
                else row
            )
        assert all(isinstance(r, WorkbookRow) for r in rows)
        return Estate(rows=rows[:limit], read_ms=0.0)

