"""The GraphQL query root.

Reads only. Writes stay on the REST surface, where the ontology rejection body from
S1.1.1 can name the offending property; folding them in here would trade that for
GraphQL's generic error shape.
"""

from __future__ import annotations

from typing import Annotated, cast

import strawberry
from fastapi import Request
from strawberry.scalars import JSON
from strawberry.schema.config import StrawberryConfig
from strawberry.types import Info

from ... import context
from ...errors import ElementNotFoundError, InvalidRequestError
from ...graph.model import NodeRecord
from ...ontology import NODE_LABELS, SCHEMA_VERSION, node_type
from ..deps import get_assembler
from .context import GraphQLContext
from .types import ALL_TYPES, EstateEdge, EstateNode, edge_from_record, node_from_record

#: S1.1.2: "neighbourhood traversal to depth 5".
MAX_DEPTH = 5

#: A neighbourhood is a screen's worth of graph, not an export. The same figure as the
#: Cypher row cap, so the two surfaces bound a result the same way.
MAX_ELEMENTS = 10_000


@strawberry.type(description="A node reached by a traversal, with its distance from the anchor.")
class Neighbour:
    node: EstateNode
    depth: int


@strawberry.type(description="A node, everything within N hops of it, and the edges between.")
class Neighbourhood:
    anchor: EstateNode
    depth: int
    nodes: list[Neighbour]
    edges: list[EstateEdge]
    truncated: bool = strawberry.field(
        description="True when the element limit was reached. The result is a prefix of "
        "the neighbourhood, not the whole of it."
    )


@strawberry.type(description="What one materialised context cost against its budget.")
class ContextUsage:
    size_bytes: int
    node_count: int
    budget_bytes: int
    budget_nodes: int


@strawberry.type(
    description="An agent's context contract, materialised for one subject (spec §4.1.3)."
)
class MaterialisedContext:
    name: str
    version: str
    subject_id: strawberry.ID
    context_hash: str = strawberry.field(
        description="sha256 over the canonical document. The figure §4.2 records in "
        "provenance and §5.4 caches on."
    )
    usage: ContextUsage
    document: JSON = strawberry.field(
        description="The canonical document, whole. Not selectable: the hash describes "
        "all of it, and a partial response would carry a hash of something else."
    )


#: The contract registry's enum, exposed as-is so the GraphQL values and the ones the
#: assembler knows cannot drift.
ContractName = strawberry.enum(
    context.ContractName, description="Named context contracts (spec §4.1.3)."
)


def _context(info: Info) -> GraphQLContext:
    return info.context  # type: ignore[no-any-return]


def _node(record: NodeRecord) -> EstateNode:
    return node_from_record(record)


@strawberry.type
class Query:
    @strawberry.field(description="Ontology schema version this service enforces.")
    def schema_version(self) -> int:
        return SCHEMA_VERSION

    @strawberry.field(description="One node by its platform id.")
    async def node(self, info: Info, id: strawberry.ID) -> EstateNode | None:
        context = _context(info)
        record = await context.repository.get_node_record(str(id))
        context.record_read("node", elements=1 if record else 0)
        return _node(record) if record else None

    @strawberry.field(description="Several nodes by platform id, in the order asked for.")
    async def nodes(self, info: Info, ids: list[strawberry.ID]) -> list[EstateNode]:
        context = _context(info)
        if len(ids) > MAX_ELEMENTS:
            raise InvalidRequestError(f"at most {MAX_ELEMENTS} ids per call")
        records = await context.repository.get_nodes([str(i) for i in ids])
        context.record_read("nodes", elements=len(records))
        return [_node(r) for r in records]

    @strawberry.field(
        description="One node by its source-system identifier, within a node type."
    )
    async def node_by_luid(
        self,
        info: Info,
        type: Annotated[str, strawberry.argument(description="Node type from the ontology.")],
        luid: str,
    ) -> EstateNode | None:
        context = _context(info)
        if type not in NODE_LABELS:
            raise InvalidRequestError(f"'{type}' is not a node type in the ontology")
        declared = node_type(type)
        if declared is None or "luid" not in declared.declared_property_names:
            carriers = ", ".join(
                sorted(
                    label
                    for label in NODE_LABELS
                    if "luid" in (node_type(label).declared_property_names)  # type: ignore[union-attr]
                )
            )
            raise InvalidRequestError(
                f"node type '{type}' does not carry a luid. Types that do: {carriers}."
            )
        record = await context.repository.get_node_by_luid(type, luid)
        context.record_read("node_by_luid", elements=1 if record else 0)
        return _node(record) if record else None

    @strawberry.field(description="Everything within `depth` hops of a node.")
    async def neighbourhood(
        self,
        info: Info,
        id: strawberry.ID,
        depth: Annotated[int, strawberry.argument(description="1 to 5 hops.")] = 1,
        edge_types: list[str] | None = None,
        node_types: list[str] | None = None,
        limit: int = MAX_ELEMENTS,
    ) -> Neighbourhood:
        context = _context(info)
        if not 1 <= depth <= MAX_DEPTH:
            raise InvalidRequestError(f"depth must be between 1 and {MAX_DEPTH}, got {depth}")
        if not 1 <= limit <= MAX_ELEMENTS:
            raise InvalidRequestError(f"limit must be between 1 and {MAX_ELEMENTS}, got {limit}")
        _validate_labels(edge_types, node_types)

        result = await context.repository.neighbourhood(
            str(id), depth=depth, edge_types=edge_types, node_types=node_types, limit=limit
        )
        context.record_read(
            "neighbourhood",
            elements=len(result.neighbours) + len(result.edges),
            detail={"depth": depth, "truncated": result.truncated},
        )
        return Neighbourhood(
            anchor=_node(result.anchor),
            depth=result.depth,
            nodes=[Neighbour(node=_node(n.node), depth=n.depth) for n in result.neighbours],
            edges=[edge_from_record(e) for e in result.edges],
            truncated=result.truncated,
        )

    @strawberry.field(
        description="An agent's context contract, materialised for one subject (spec §4.1.3)."
    )
    async def context_contract(
        self, info: Info, name: context.ContractName, subject_id: strawberry.ID
    ) -> MaterialisedContext:
        graphql_context = _context(info)
        # Fetched here rather than built into every request's context: a query for the
        # schema version should not fail because the assembler is missing. Strawberry's
        # context types the connection as Request-or-WebSocket; GraphQL is mounted over
        # HTTP only, and a subscription transport would have to be added deliberately.
        request = graphql_context.request
        if not isinstance(request, Request):  # pragma: no cover - no websocket transport
            raise InvalidRequestError("a context contract can only be assembled over HTTP")
        assembler = get_assembler(request)
        assembled = await assembler.assemble(context.ContractName(name.value), str(subject_id))
        graphql_context.record_read(
            "context_contract",
            elements=assembled.node_count,
            detail={"contract": name.value, "context_hash": assembled.context_hash},
        )
        usage = assembled.usage()
        return MaterialisedContext(
            name=assembled.contract.value,
            version=assembled.version,
            subject_id=strawberry.ID(assembled.subject_id),
            context_hash=assembled.context_hash,
            usage=ContextUsage(
                size_bytes=usage["size_bytes"],
                node_count=usage["node_count"],
                budget_bytes=usage["budget_bytes"],
                budget_nodes=usage["budget_nodes"],
            ),
            # strawberry.scalars.JSON is a NewType over str for typing purposes; the
            # value serialised is the object itself.
            document=cast("JSON", assembled.document),
        )


def _validate_labels(edge_types: list[str] | None, node_types: list[str] | None) -> None:
    from ...ontology import EDGE_LABELS

    for label in edge_types or []:
        if label not in EDGE_LABELS:
            raise InvalidRequestError(f"'{label}' is not an edge type in the ontology")
    for label in node_types or []:
        if label not in NODE_LABELS:
            raise InvalidRequestError(f"'{label}' is not a node type in the ontology")


def build_schema() -> strawberry.Schema:
    """The schema. ``types=`` carries the generated types that no field returns directly,
    so they are reachable through the interfaces via inline fragments."""
    return strawberry.Schema(
        query=Query,
        types=ALL_TYPES,
        config=StrawberryConfig(auto_camel_case=False),
    )


schema = build_schema()

__all__ = ["ElementNotFoundError", "Query", "build_schema", "schema"]
