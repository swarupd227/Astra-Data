"""The write path: validate against the ontology, then persist with its event.

Everything that writes to the Estate Graph goes through here. Validation happens before
the store is touched, and a batch is validated in full before any of it is written, so a
rejected batch leaves nothing behind.

Since S1.1.3, every mutation also produces a CloudEvent that is committed in the same
transaction as the mutation itself. There is no path through this module that changes the
graph without recording who changed it and from which run, and no path that deletes
anything — a node leaves the working estate by being retired, and stays in the record.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from . import events as event_factory
from .errors import ElementNotFoundError, InvalidRequestError, OntologyViolationError
from .events import PlatformEvent
from .graph import GraphRepository
from .ids import new_ulid
from .ontology import Violation, ViolationCode, node_type, validate_edge, validate_node
from .principal import Principal

#: Set by the service on every write, so a merge must not carry them back in as if the
#: caller had supplied them (the validator rejects that, correctly).
_SERVER_OWNED = frozenset(
    {"id", "side", "created_by", "created_at", "created_in_run",
     "updated_by", "updated_at",
     "retired_at", "retired_by", "retirement_reason"}
)

#: A retirement must say why. Spec P4 makes human decisions first-class records with a
#: rationale; a retirement with no reason is not a decision anyone can audit later.
MIN_RETIREMENT_REASON_LENGTH = 8


@dataclass(frozen=True, slots=True)
class NodeWrite:
    """One node a caller wants written."""

    type: str
    properties: Mapping[str, Any]
    id: str | None = None


@dataclass(frozen=True, slots=True)
class EdgeWrite:
    """One edge a caller wants written."""

    type: str
    from_id: str
    to_id: str
    properties: Mapping[str, Any]
    id: str | None = None


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _base_properties(principal: Principal, *, element_id: str, actor_property: str) -> dict[str, Any]:
    base: dict[str, Any] = {
        "id": element_id,
        actor_property: principal.value,
        "created_at": _now(),
    }
    if principal.run_id:
        base["created_in_run"] = principal.run_id
    return base


class GraphWriter:
    """Validates writes against the ontology and persists the ones that conform."""

    def __init__(self, repository: GraphRepository, *, event_source: str = "/astra/graph-svc") -> None:
        self._repository = repository
        self._event_source = event_source

    @property
    def event_source(self) -> str:
        """The CloudEvents ``source`` this writer stamps. Callers building a notice need it."""
        return self._event_source

    async def append_event(self, event: PlatformEvent) -> None:
        """Append an event that records no graph change (S1.2.4).

        On the writer rather than straight on the repository because everything that
        writes to the outbox goes through one object, and because the repository refuses
        a mutating type here — a caller that reaches for this by mistake gets told.
        """
        await self._repository.append_event(event)

    # ---------------------------------------------------------------------- nodes

    def _prepare_nodes(
        self,
        writes: Sequence[NodeWrite],
        principal: Principal,
        *,
        upsert: bool = False,
    ) -> tuple[list[tuple[str, dict[str, Any]]], list[PlatformEvent]]:
        violations: list[Violation] = []
        prepared: list[tuple[str, dict[str, Any]]] = []

        for index, write in enumerate(writes):
            element_id = write.id or new_ulid()
            server = _base_properties(principal, element_id=element_id, actor_property="created_by")
            if upsert:
                # The repository decides which of these survive: on an update it keeps the
                # node's original created_* and takes these as the change; on a create it
                # drops them, because a node just created has not been updated.
                server["updated_by"] = principal.value
                server["updated_at"] = server["created_at"]
            result = validate_node(write.type, write.properties, server_supplied=server)
            if result.ok:
                prepared.append((write.type, result.properties))
            else:
                violations.extend(v.with_index(index) for v in result.violations)

        if violations:
            raise OntologyViolationError(violations)

        emitted = [
            event_factory.node_upserted(
                source=self._event_source,
                label=label,
                properties=properties,
                principal=principal,
            )
            for label, properties in prepared
        ]
        return prepared, emitted

    async def write_nodes(
        self, writes: Sequence[NodeWrite], *, principal: Principal
    ) -> list[dict[str, Any]]:
        """Validate and create nodes. Raises OntologyViolationError listing every problem."""
        prepared, emitted = self._prepare_nodes(writes, principal)
        return await self._repository.create_nodes(prepared, emitted)

    async def upsert_nodes(
        self, writes: Sequence[NodeWrite], *, principal: Principal
    ) -> list[dict[str, Any]]:
        """Validate and create-or-replace nodes.

        An upsert replaces the whole property set, so the caller must send the node as it
        should be, not the part of it that changed. The event carries the same complete
        set, which is what lets a replay apply it without prior state.
        """
        for write in writes:
            if not write.id:
                raise InvalidRequestError("an upsert needs the id of the node to write")
        prepared, emitted = self._prepare_nodes(writes, principal, upsert=True)
        return await self._repository.upsert_nodes(prepared, emitted)

    # ---------------------------------------------------------------------- edges

    async def _prepare_edge(
        self, write: EdgeWrite, principal: Principal
    ) -> tuple[dict[str, Any], str, str, PlatformEvent]:
        labels = await self._repository.labels_for([write.from_id, write.to_id])
        from_label = labels.get(write.from_id)
        to_label = labels.get(write.to_id)

        element_id = write.id or new_ulid()
        server = _base_properties(principal, element_id=element_id, actor_property="written_by")
        result = validate_edge(
            write.type,
            write.properties,
            from_label=from_label,
            to_label=to_label,
            server_supplied=server,
        )

        missing = [
            Violation(
                code=ViolationCode.UNKNOWN_ENDPOINT_NODE,
                message=f"no node with id '{node_id}' exists in the graph",
                property=field_name,
                element_type=write.type,
            )
            for field_name, node_id, label in (
                ("from_id", write.from_id, from_label),
                ("to_id", write.to_id, to_label),
            )
            if label is None
        ]

        if result.violations or missing:
            raise OntologyViolationError(result.violations + missing)

        assert from_label is not None and to_label is not None
        emitted = event_factory.edge_upserted(
            source=self._event_source,
            label=write.type,
            properties=result.properties,
            from_id=write.from_id,
            to_id=write.to_id,
            principal=principal,
        )
        return result.properties, from_label, to_label, emitted

    async def write_edge(self, write: EdgeWrite, *, principal: Principal) -> dict[str, Any]:
        """Validate and create one edge.

        Endpoint labels are read from the graph first: whether an edge is permitted
        depends on what its endpoints actually are, not on what the caller claims.
        """
        properties, from_label, to_label, emitted = await self._prepare_edge(write, principal)
        return await self._repository.create_edge(
            write.type,
            from_id=write.from_id,
            to_id=write.to_id,
            from_label=from_label,
            to_label=to_label,
            properties=properties,
            event=emitted,
        )

    async def upsert_edge(self, write: EdgeWrite, *, principal: Principal) -> dict[str, Any]:
        if not write.id:
            raise InvalidRequestError("an upsert needs the id of the edge to write")
        properties, from_label, to_label, emitted = await self._prepare_edge(write, principal)
        return await self._repository.upsert_edge(
            write.type,
            from_id=write.from_id,
            to_id=write.to_id,
            from_label=from_label,
            to_label=to_label,
            properties=properties,
            event=emitted,
        )

    async def set_node_properties(
        self,
        node_id: str,
        properties: Mapping[str, Any],
        *,
        principal: Principal,
    ) -> dict[str, Any]:
        """Change some of a node's properties, leaving the rest as they are.

        Reads the node, merges, validates the whole result, and writes it back through the
        upsert path — so the mutation event still carries the node's complete property set
        and a replay reproduces it (S1.1.3). A partial event would need replay to hold
        prior state, which is precisely what the event design avoids.
        """
        record = await self._repository.get_node_record(node_id)
        if record is None:
            raise ElementNotFoundError(f"no node with id '{node_id}'")

        # `side` is server-owned for every type whose side the ontology fixes, but User
        # spans both sides (spec §4.1.1) and the writer declares it there — so on that one
        # type it has to be carried forward or the merged node fails validation.
        declared = node_type(record.label)
        owned = _SERVER_OWNED if declared is None or declared.side is not None else (
            _SERVER_OWNED - {"side"}
        )
        merged = {
            name: value for name, value in record.properties.items() if name not in owned
        }
        merged.update(properties)

        written = await self.upsert_nodes(
            [NodeWrite(type=record.label, id=node_id, properties=merged)],
            principal=principal,
        )
        return written[0]

    # ----------------------------------------------------------------- retirement

    async def retire_node(
        self, node_id: str, *, reason: str, principal: Principal
    ) -> dict[str, Any]:
        """Retire a node.

        The only way a node leaves the working estate. It is not deleted: the graph keeps
        it, reads skip it unless asked otherwise, and the retirement is a record with a
        principal, a time and a stated reason.
        """
        cleaned = reason.strip()
        if len(cleaned) < MIN_RETIREMENT_REASON_LENGTH:
            raise InvalidRequestError(
                f"a retirement needs a reason of at least "
                f"{MIN_RETIREMENT_REASON_LENGTH} characters; it is the record of why a "
                f"node left the estate"
            )

        record = await self._repository.get_node_record(node_id)
        if record is None:
            raise ElementNotFoundError(f"no node with id '{node_id}'")

        retired_at = _now()
        emitted = event_factory.node_retired(
            source=self._event_source,
            label=record.label,
            node_id=node_id,
            retired_at=retired_at,
            reason=cleaned,
            principal=principal,
        )
        return await self._repository.retire_node(
            node_id,
            retired_at=retired_at,
            retired_by=principal.value,
            reason=cleaned,
            event=emitted,
        )

    async def retire_edge(
        self, edge_id: str, *, reason: str, principal: Principal
    ) -> dict[str, Any]:
        """Retire an edge — the only way one leaves the working graph, and the only way a
        relationship 'changes': an edge's endpoints are fixed at creation, so replacing one
        (S3.1.2's 'move') is retiring this edge and writing a new one, never mutating this
        one in place.
        """
        cleaned = reason.strip()
        if len(cleaned) < MIN_RETIREMENT_REASON_LENGTH:
            raise InvalidRequestError(
                f"a retirement needs a reason of at least "
                f"{MIN_RETIREMENT_REASON_LENGTH} characters; it is the record of why an "
                f"edge left the graph"
            )

        record = await self._repository.get_edge_record(edge_id)
        if record is None:
            raise ElementNotFoundError(f"no edge with id '{edge_id}'")

        retired_at = _now()
        emitted = event_factory.edge_retired(
            source=self._event_source,
            label=record.label,
            edge_id=edge_id,
            retired_at=retired_at,
            reason=cleaned,
            principal=principal,
        )
        return await self._repository.retire_edge(
            edge_id,
            retired_at=retired_at,
            retired_by=principal.value,
            reason=cleaned,
            event=emitted,
        )
