"""Replaying the event stream, and comparing the result against the live graph.

S1.1.3: "A replay of the event stream from empty produces a graph identical to the live
graph (verified by a nightly CI job on the test estate)."

The replay is a real rebuild, not a simulation: events are applied through the same
repository the service writes with, into a second Apache AGE graph in the same database.
That is why the relational index tables carry a ``graph`` column — a replay has to be able
to stand alongside the estate it is checking without colliding with it.

What "identical" means here is stated explicitly in ``compare``: the same node ids with
the same labels and the same properties, and the same edge ids with the same labels,
properties and endpoints. Identifiers, provenance and timestamps are all included, because
an auditor's question is whether the record accounts for the estate exactly, not
approximately.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any, Protocol

from .events import EventType, StoredEvent

#: Events are read in pages rather than all at once; an estate at N1 scale has millions.
DEFAULT_PAGE_SIZE = 2_000


class ReplayTarget(Protocol):
    """What a replay needs to write. Satisfied by the graph repository."""

    async def upsert_nodes(
        self, elements: Sequence[tuple[str, dict[str, Any]]], events: Sequence[Any] = ()
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
        event: Any | None = None,
    ) -> dict[str, Any]: ...

    async def labels_for(self, ids: Any) -> dict[str, str]: ...

    async def dump(self) -> dict[str, Any]: ...


class ReplaySource(Protocol):
    async def read_events(
        self, *, after: int = 0, limit: int = 1000, subject: str | None = None
    ) -> list[StoredEvent]: ...

    async def dump(self) -> dict[str, Any]: ...


@dataclass(slots=True)
class ReplayResult:
    events_applied: int = 0
    nodes: int = 0
    edges: int = 0
    retirements: int = 0
    notices: int = 0
    """Events that carry no graph change — skipped, and counted so that is visible."""

    last_sequence: int = 0


@dataclass(slots=True)
class Difference:
    kind: str
    element_id: str
    detail: str


@dataclass(slots=True)
class ComparisonResult:
    """Whether a replayed graph accounts for the live one, and where it does not."""

    differences: list[Difference] = field(default_factory=list)
    live_nodes: int = 0
    live_edges: int = 0
    replayed_nodes: int = 0
    replayed_edges: int = 0

    @property
    def identical(self) -> bool:
        return not self.differences

    def summary(self) -> str:
        if self.identical:
            return (
                f"identical: {self.live_nodes} nodes, {self.live_edges} edges "
                f"reproduced exactly"
            )
        return f"{len(self.differences)} difference(s) across {self.live_nodes} nodes"


async def replay(source: ReplaySource, target: ReplayTarget) -> ReplayResult:
    """Apply the whole event stream to ``target``, in sequence order."""
    result = ReplayResult()
    after = 0

    while True:
        page = await source.read_events(after=after, limit=DEFAULT_PAGE_SIZE)
        if not page:
            break
        for event in page:
            await _apply(target, event, result)
            result.events_applied += 1
            result.last_sequence = event.sequence
        after = page[-1].sequence

    return result


async def _apply(target: ReplayTarget, event: StoredEvent, result: ReplayResult) -> None:
    data = event.data

    if not event.type.mutates_graph:
        # A notice states something about the world outside the graph — that a source
        # workbook drifted, say. Replaying it must not change the estate, and must not
        # fail either: the stream is still perfectly consistent. Counted so a replay
        # reports what it passed over rather than quietly dropping it.
        result.notices += 1
        return

    if event.type is EventType.NODE_UPSERTED:
        await target.upsert_nodes([(data["type"], dict(data["properties"]))])
        result.nodes += 1
        return

    if event.type is EventType.EDGE_UPSERTED:
        from_id, to_id = data["from_id"], data["to_id"]
        labels = await target.labels_for([from_id, to_id])
        missing = [i for i in (from_id, to_id) if i not in labels]
        if missing:
            # The write path refuses an edge whose endpoints are not committed, so their
            # events always carry a lower sequence. Reaching here means the stream is not
            # self-consistent, which is exactly what this exercise is meant to detect.
            raise ReplayError(
                f"event {event.sequence} ({event.id}) creates a {data['type']} edge whose "
                f"endpoint(s) {', '.join(missing)} have not been seen in the stream"
            )
        await target.upsert_edge(
            data["type"],
            from_id=from_id,
            to_id=to_id,
            from_label=labels[from_id],
            to_label=labels[to_id],
            properties=dict(data["properties"]),
        )
        result.edges += 1
        return

    if event.type is EventType.NODE_RETIRED:
        # Retirement is replayed as an upsert of the retired property set rather than by
        # calling retire_node: replay must be idempotent, and retire_node deliberately
        # refuses to retire something already retired.
        existing = await target.dump()
        node = existing["nodes"].get(event.subject)
        if node is None:
            raise ReplayError(
                f"event {event.sequence} ({event.id}) retires node {event.subject}, "
                f"which the stream has not created"
            )
        properties = dict(node["properties"])
        properties["retired_at"] = data["retired_at"]
        properties["retired_by"] = data["retired_by"]
        properties["retirement_reason"] = data["retirement_reason"]
        await target.upsert_nodes([(node["type"], properties)])
        result.retirements += 1
        return

    if event.type is EventType.EDGE_RETIRED:
        # Same reasoning as NODE_RETIRED: replayed as an upsert of the retired property
        # set, not by calling retire_edge, so replay stays idempotent over a stream that
        # retired this edge once.
        existing = await target.dump()
        edge = existing["edges"].get(event.subject)
        if edge is None:
            raise ReplayError(
                f"event {event.sequence} ({event.id}) retires edge {event.subject}, "
                f"which the stream has not created"
            )
        from_id, to_id = edge["from_id"], edge["to_id"]
        labels = await target.labels_for([from_id, to_id])
        properties = dict(edge["properties"])
        properties["retired_at"] = data["retired_at"]
        properties["retired_by"] = data["retired_by"]
        properties["retirement_reason"] = data["retirement_reason"]
        await target.upsert_edge(
            edge["type"],
            from_id=from_id,
            to_id=to_id,
            from_label=labels[from_id],
            to_label=labels[to_id],
            properties=properties,
        )
        result.retirements += 1
        return

    raise ReplayError(f"event {event.sequence} has unknown type {event.type!r}")


class ReplayError(Exception):
    """The event stream cannot be replayed. Always a defect in the record."""


def compare(live: dict[str, Any], replayed: dict[str, Any]) -> ComparisonResult:
    """Compare two graph dumps element by element."""
    result = ComparisonResult(
        live_nodes=len(live["nodes"]),
        live_edges=len(live["edges"]),
        replayed_nodes=len(replayed["nodes"]),
        replayed_edges=len(replayed["edges"]),
    )

    for kind in ("nodes", "edges"):
        live_side: dict[str, Any] = live[kind]
        replay_side: dict[str, Any] = replayed[kind]

        for element_id in sorted(set(live_side) - set(replay_side)):
            result.differences.append(
                Difference(kind, element_id, "in the live graph, absent from the replay")
            )
        for element_id in sorted(set(replay_side) - set(live_side)):
            result.differences.append(
                Difference(kind, element_id, "in the replay, absent from the live graph")
            )
        for element_id in sorted(set(live_side) & set(replay_side)):
            result.differences.extend(
                _compare_element(kind, element_id, live_side[element_id], replay_side[element_id])
            )
    return result


def _compare_element(
    kind: str, element_id: str, live: dict[str, Any], replayed: dict[str, Any]
) -> list[Difference]:
    differences: list[Difference] = []
    if live["type"] != replayed["type"]:
        differences.append(
            Difference(kind, element_id, f"type {live['type']} vs {replayed['type']}")
        )
    if kind == "edges":
        for endpoint in ("from_id", "to_id"):
            if live.get(endpoint) != replayed.get(endpoint):
                differences.append(
                    Difference(
                        kind,
                        element_id,
                        f"{endpoint} {live.get(endpoint)} vs {replayed.get(endpoint)}",
                    )
                )

    live_properties: dict[str, Any] = live["properties"]
    replayed_properties: dict[str, Any] = replayed["properties"]
    for name in sorted(set(live_properties) | set(replayed_properties)):
        expected = live_properties.get(name)
        actual = replayed_properties.get(name)
        if expected != actual:
            differences.append(
                Difference(kind, element_id, f"property '{name}': {expected!r} vs {actual!r}")
            )
    return differences
