"""Platform events: the outbox every graph mutation writes into, and the notices beside them.

S1.1.3: "every graph mutation ... recorded with who made it and from which run", emitted
as CloudEvents, and sufficient to reconstruct the graph.

**Events are written in the same transaction as the mutation.** That is the whole design.
An event stream published on a best-effort basis after a commit cannot satisfy "a replay
of the event stream from empty produces a graph identical to the live graph" — a crash
between the two leaves a fact in the graph that the record does not contain. So the
mutation and its event go into PostgreSQL together, and a publisher moves them onto the
bus afterwards. That publisher is E12's; ``published_at`` on the outbox row is where it
hooks in.

**Event types.** S1.1.3 names them ``estate.node.upserted``, ``estate.edge.upserted`` and
``estate.node.retired``. Specification Appendix C uses an ``astra.data.*`` prefix for its
own catalogue and does not list these three. The story's names are used verbatim because
they are the acceptance bar; the inconsistency is on the record in ADR 0003.

**Not every event mutates the graph.** ``estate.source.drift`` (S1.2.4) states that a
source workbook changed under a Migration Unit that is already being worked. Nothing in
the graph changes because of it — the re-parse that follows does that, and emits its own
mutation events. So ``EventType.mutates_graph`` divides the two, and replay applies only
the mutating ones. A notice shares the outbox rather than getting a table of its own
because the bus is one ordered stream: a consumer that sees a drift notice at sequence
412 needs to know it comes after the upserts at 400-411, and two tables cannot say that.

The subject columns still describe an element — a drift notice's subject is the Workbook
node it is about — because "what is this event about" is a useful question of every event.
It is the *type* that says what happened to it.

**No redaction.** Event data carries whatever the property carried, including custom SQL
and field names. That is correct: the bus is inside the tenant (spec §5.3, §19). The
inference boundary in §18.3 governs what reaches a model endpoint, which is a different
boundary and is not this one.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import Enum
from typing import Any

from .ids import new_ulid
from .principal import Principal

CLOUDEVENTS_SPEC_VERSION = "1.0"
DATA_CONTENT_TYPE = "application/json"


class EventType(str, Enum):
    """The three mutation events named in S1.1.3, and the notices that share their outbox."""

    NODE_UPSERTED = "estate.node.upserted"
    EDGE_UPSERTED = "estate.edge.upserted"
    NODE_RETIRED = "estate.node.retired"
    EDGE_RETIRED = "estate.edge.retired"
    """S3.1.2: an edge's endpoints cannot change once created, so 'move' retires the old
    relationship rather than mutating it — the same shape S1.1.3 gives nodes."""

    SOURCE_DRIFT = "estate.source.drift"
    """S1.2.4: a source workbook changed while a Migration Unit over it was in progress."""

    PATTERN_RETIRED = "estate.pattern.retired"
    """S5.5.2: an ACTIVE Pattern crossed its own failure threshold and was moved to
    RETIRED automatically (§13.2's own MA-12, ceiling L4 — "automatic on failure
    threshold"). A notice, the same footing SOURCE_DRIFT already has: the real mutation is
    the Pattern's own `promotion_state` write (a normal upsert through `GraphWriter.
    set_node_properties`), which already gets its own NODE_UPSERTED event; this is the AC's
    own "an event is raised" — the one a Parity Dashboard-style consumer would actually
    watch for, without needing to diff every Pattern upsert to notice a retirement among
    them."""

    @property
    def element_kind(self) -> str:
        return "edge" if self in (EventType.EDGE_UPSERTED, EventType.EDGE_RETIRED) else "node"

    @property
    def mutates_graph(self) -> bool:
        """Whether replaying this event changes the graph.

        A notice does not. Replay must skip it rather than fail on it, and rather than
        silently ignore anything it does not recognise — an unknown *mutation* type is
        still a defect in the record.
        """
        return self not in (EventType.SOURCE_DRIFT, EventType.PATTERN_RETIRED)


def source_for(graph_name: str) -> str:
    """CloudEvents ``source``: which graph in which service produced the event."""
    return f"/astra/graph-svc/{graph_name}"


@dataclass(frozen=True, slots=True)
class PlatformEvent:
    """One CloudEvent, before it is written to the outbox."""

    type: EventType
    source: str
    subject: str
    """The element's platform id."""

    label: str
    principal: str
    run_id: str | None
    data: dict[str, Any]
    id: str = field(default_factory=new_ulid)
    time: str = field(
        default_factory=lambda: datetime.now(UTC)
        .isoformat(timespec="milliseconds")
        .replace("+00:00", "Z")
    )

    @property
    def element_kind(self) -> str:
        return self.type.element_kind

    def to_cloudevent(self, *, sequence: int | None = None) -> dict[str, Any]:
        """The CloudEvents 1.0 structured form.

        ``runid``, ``principal`` and ``sequence`` are CloudEvents extension attributes:
        lowercase alphanumeric names, as the specification requires of extensions.
        """
        envelope: dict[str, Any] = {
            "specversion": CLOUDEVENTS_SPEC_VERSION,
            "id": self.id,
            "source": self.source,
            "type": self.type.value,
            "subject": self.subject,
            "time": self.time,
            "datacontenttype": DATA_CONTENT_TYPE,
            "principal": self.principal,
            "data": self.data,
        }
        if self.run_id:
            envelope["runid"] = self.run_id
        if sequence is not None:
            envelope["sequence"] = str(sequence)
        return envelope


def node_upserted(
    *,
    source: str,
    label: str,
    properties: dict[str, Any],
    principal: Principal,
) -> PlatformEvent:
    """A node was created or replaced.

    The event carries the node's complete post-write property set, not a patch. Replay
    then needs no prior state, and applying the same event twice is a no-op — which is
    what makes the stream replayable from any point.
    """
    return PlatformEvent(
        type=EventType.NODE_UPSERTED,
        source=source,
        subject=str(properties["id"]),
        label=label,
        principal=principal.value,
        run_id=principal.run_id,
        data={"type": label, "properties": properties},
    )


def edge_upserted(
    *,
    source: str,
    label: str,
    properties: dict[str, Any],
    from_id: str,
    to_id: str,
    principal: Principal,
) -> PlatformEvent:
    return PlatformEvent(
        type=EventType.EDGE_UPSERTED,
        source=source,
        subject=str(properties["id"]),
        label=label,
        principal=principal.value,
        run_id=principal.run_id,
        data={
            "type": label,
            "properties": properties,
            "from_id": from_id,
            "to_id": to_id,
        },
    )


def node_retired(
    *,
    source: str,
    label: str,
    node_id: str,
    retired_at: str,
    reason: str,
    principal: Principal,
) -> PlatformEvent:
    return PlatformEvent(
        type=EventType.NODE_RETIRED,
        source=source,
        subject=node_id,
        label=label,
        principal=principal.value,
        run_id=principal.run_id,
        data={
            "type": label,
            "retired_at": retired_at,
            "retired_by": principal.value,
            "retirement_reason": reason,
        },
    )


def edge_retired(
    *,
    source: str,
    label: str,
    edge_id: str,
    retired_at: str,
    reason: str,
    principal: Principal,
) -> PlatformEvent:
    return PlatformEvent(
        type=EventType.EDGE_RETIRED,
        source=source,
        subject=edge_id,
        label=label,
        principal=principal.value,
        run_id=principal.run_id,
        data={
            "type": label,
            "retired_at": retired_at,
            "retired_by": principal.value,
            "retirement_reason": reason,
        },
    )


def source_drift(
    *,
    source: str,
    workbook_node_id: str,
    detail: dict[str, Any],
    principal: Principal,
) -> PlatformEvent:
    """A source workbook changed under work already in progress (S1.2.4).

    Not a graph mutation: it is a statement about the source, and about the Migration Unit
    that was being built from an earlier version of it. The Arbiter re-proves on this
    (E7); the Exception Desk classes a parity failure caused by it as SOURCE_DRIFT
    (spec §10.6). Both need the *whole* claim in one place, so the detail carries what
    changed and which MU was affected rather than pointing at a row somewhere else.
    """
    return PlatformEvent(
        type=EventType.SOURCE_DRIFT,
        source=source,
        subject=workbook_node_id,
        label="Workbook",
        principal=principal.value,
        run_id=principal.run_id,
        data=dict(detail),
    )


def pattern_retired(
    *,
    source: str,
    pattern_id: str,
    reason: str,
    requeued_measure_ids: tuple[str, ...],
    principal: Principal,
) -> PlatformEvent:
    """An ACTIVE Pattern crossed its own failure threshold (S5.5.2). Not a graph mutation:
    it is a statement about a decision this platform just made, and the same "carry the
    whole claim in one place" reasoning `source_drift` already gives — a consumer watching
    for retirements should not need to separately look up which artefacts were re-queued.
    """
    return PlatformEvent(
        type=EventType.PATTERN_RETIRED,
        source=source,
        subject=pattern_id,
        label="Pattern",
        principal=principal.value,
        run_id=principal.run_id,
        data={
            "pattern_id": pattern_id,
            "reason": reason,
            "requeued_measure_ids": list(requeued_measure_ids),
        },
    )


@dataclass(frozen=True, slots=True)
class StoredEvent:
    """An event as it came back out of the outbox."""

    sequence: int
    type: EventType
    source: str
    subject: str
    label: str
    time: str
    principal: str
    run_id: str | None
    data: dict[str, Any]
    id: str
    published_at: str | None = None

    def to_cloudevent(self) -> dict[str, Any]:
        envelope: dict[str, Any] = {
            "specversion": CLOUDEVENTS_SPEC_VERSION,
            "id": self.id,
            "source": self.source,
            "type": self.type.value,
            "subject": self.subject,
            "time": self.time,
            "datacontenttype": DATA_CONTENT_TYPE,
            "principal": self.principal,
            "sequence": str(self.sequence),
            "data": self.data,
        }
        if self.run_id:
            envelope["runid"] = self.run_id
        return envelope
