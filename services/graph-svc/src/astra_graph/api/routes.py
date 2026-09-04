"""HTTP routes.

Writes are the substance of S1.1.1. The reads here are the minimum needed to confirm what
a write stored; the query API — GraphQL, neighbourhood traversal, the read-only Cypher
endpoint — is S1.1.2 and is deliberately absent.
"""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, Path, Response, status

from ..errors import ElementNotFoundError, InvalidRequestError
from ..ontology import SCHEMA_VERSION, sorted_edge_types, sorted_node_types
from ..ontology.render import render_markdown
from ..writes import EdgeWrite, NodeWrite
from .deps import PrincipalDep, RepositoryDep, WriterDep, get_repository
from .schemas import (
    EdgeUpsertRequest,
    EdgeWriteRequest,
    ElementResponse,
    EventPageResponse,
    EventResponse,
    HealthResponse,
    NodeBatchResponse,
    NodeBatchWriteRequest,
    NodeUpsertRequest,
    NodeWriteRequest,
    RetireRequest,
)

router = APIRouter()

_ID_PATH = Path(min_length=26, max_length=26, description="ULID of the element.")


@router.get("/health", response_model=HealthResponse, tags=["operations"])
async def health(repository: Annotated[Any, Depends(get_repository)]) -> HealthResponse:
    """Readiness: the pool answers and the graph exists."""
    await repository.health()
    from ..config import settings

    return HealthResponse(status="ok", graph=settings().graph_name, schema_version=SCHEMA_VERSION)


# --------------------------------------------------------------------------- ontology


@router.get("/v1/ontology", tags=["ontology"])
async def get_ontology() -> dict[str, Any]:
    """The ontology as data, so a client can validate before it writes."""
    return {
        "schema_version": SCHEMA_VERSION,
        "nodes": [
            {
                "label": node.label,
                "side": node.side.value if node.side else None,
                "spec_ref": node.spec_ref,
                "properties": [
                    {
                        "name": p.name,
                        "type": p.render_type(),
                        "required": p.required,
                        "server_managed": p.server_managed,
                        "note": p.note,
                    }
                    for p in node.all_properties
                ],
            }
            for node in sorted_node_types()
        ],
        "edges": [
            {
                "label": edge.label,
                "pairs": [list(pair) for pair in edge.pairs],
                "written_by": edge.written_by,
                "spec_ref": edge.spec_ref,
                "properties": [
                    {
                        "name": p.name,
                        "type": p.render_type(),
                        "required": p.required,
                        "server_managed": p.server_managed,
                        "note": p.note,
                    }
                    for p in edge.all_properties
                ],
            }
            for edge in sorted_edge_types()
        ],
    }


@router.get("/v1/ontology.md", response_class=Response, tags=["ontology"])
async def get_ontology_markdown() -> Response:
    """The same ontology as the generated reference document."""
    return Response(content=render_markdown(), media_type="text/markdown; charset=utf-8")


# ------------------------------------------------------------------------------ nodes


@router.post(
    "/v1/nodes",
    response_model=ElementResponse,
    status_code=status.HTTP_201_CREATED,
    tags=["nodes"],
)
async def create_node(
    body: NodeWriteRequest, writer: WriterDep, principal: PrincipalDep
) -> ElementResponse:
    created = await writer.write_nodes(
        [NodeWrite(type=body.type, properties=body.properties, id=body.id)],
        principal=principal,
    )
    return ElementResponse(type=created[0]["label"], properties=created[0]["properties"])


@router.post(
    "/v1/nodes:batch",
    response_model=NodeBatchResponse,
    status_code=status.HTTP_201_CREATED,
    tags=["nodes"],
)
async def create_nodes(
    body: NodeBatchWriteRequest, writer: WriterDep, principal: PrincipalDep
) -> NodeBatchResponse:
    """Write a batch atomically.

    The whole batch is validated before any of it is written, and the response to a
    rejected batch lists every violation with the index of the node that caused it.
    """
    created = await writer.write_nodes(
        [NodeWrite(type=node.type, properties=node.properties, id=node.id) for node in body.nodes],
        principal=principal,
    )
    return NodeBatchResponse(
        nodes=[ElementResponse(type=n["label"], properties=n["properties"]) for n in created]
    )


@router.get("/v1/nodes/{node_id}", response_model=ElementResponse, tags=["nodes"])
async def get_node(
    repository: Annotated[Any, Depends(get_repository)],
    node_id: Annotated[str, _ID_PATH],
) -> ElementResponse:
    found = await repository.get_node(node_id)
    if found is None:
        raise ElementNotFoundError(f"no node with id '{node_id}'")
    return ElementResponse(type=found["label"], properties=found["properties"])


# ------------------------------------------------------------------------------ edges


@router.post(
    "/v1/edges",
    response_model=ElementResponse,
    status_code=status.HTTP_201_CREATED,
    tags=["edges"],
)
async def create_edge(
    body: EdgeWriteRequest, writer: WriterDep, principal: PrincipalDep
) -> ElementResponse:
    created = await writer.write_edge(
        EdgeWrite(
            type=body.type,
            from_id=body.from_id,
            to_id=body.to_id,
            properties=body.properties,
            id=body.id,
        ),
        principal=principal,
    )
    return ElementResponse(type=created["label"], properties=created["properties"])


@router.get("/v1/edges/{edge_id}", response_model=ElementResponse, tags=["edges"])
async def get_edge(
    repository: Annotated[Any, Depends(get_repository)],
    edge_id: Annotated[str, _ID_PATH],
) -> ElementResponse:
    found = await repository.get_edge(edge_id)
    if found is None:
        raise ElementNotFoundError(f"no edge with id '{edge_id}'")
    return ElementResponse(type=found["label"], properties=found["properties"])


# ------------------------------------------------------------------------- upsert


@router.put(
    "/v1/nodes/{node_id}",
    response_model=ElementResponse,
    tags=["nodes"],
    summary="Create or replace one node",
)
async def upsert_node(
    body: NodeUpsertRequest,
    writer: WriterDep,
    principal: PrincipalDep,
    node_id: Annotated[str, _ID_PATH],
) -> ElementResponse:
    """Replaces the whole property set. Emits `estate.node.upserted`."""
    written = await writer.upsert_nodes(
        [NodeWrite(type=body.type, properties=body.properties, id=node_id)],
        principal=principal,
    )
    return ElementResponse(type=written[0]["label"], properties=written[0]["properties"])


@router.put(
    "/v1/edges/{edge_id}",
    response_model=ElementResponse,
    tags=["edges"],
    summary="Create or replace one edge",
)
async def upsert_edge(
    body: EdgeUpsertRequest,
    writer: WriterDep,
    principal: PrincipalDep,
    edge_id: Annotated[str, _ID_PATH],
) -> ElementResponse:
    written = await writer.upsert_edge(
        EdgeWrite(
            type=body.type,
            from_id=body.from_id,
            to_id=body.to_id,
            properties=body.properties,
            id=edge_id,
        ),
        principal=principal,
    )
    return ElementResponse(type=written["label"], properties=written["properties"])


# --------------------------------------------------------------------- retirement


@router.post(
    "/v1/nodes/{node_id}:retire",
    response_model=ElementResponse,
    tags=["nodes"],
    summary="Retire a node",
)
async def retire_node(
    body: RetireRequest,
    writer: WriterDep,
    principal: PrincipalDep,
    node_id: Annotated[str, _ID_PATH],
) -> ElementResponse:
    """Take a node out of the working estate without deleting it.

    There is no delete endpoint, here or anywhere: retirement stamps `retired_at`,
    `retired_by` and `retirement_reason` and leaves the node in the graph. Reads skip
    retired nodes unless asked for them. Emits `estate.node.retired`.
    """
    retired = await writer.retire_node(node_id, reason=body.reason, principal=principal)
    return ElementResponse(type=retired["label"], properties=retired["properties"])


# ------------------------------------------------------------------------- events


@router.get(
    "/v1/events",
    response_model=EventPageResponse,
    tags=["events"],
    summary="Read the mutation event stream",
)
async def read_events(
    repository: RepositoryDep,
    after: int = 0,
    limit: int = 500,
    subject: str | None = None,
) -> EventPageResponse:
    """The mutation record, in commit order, as CloudEvents.

    This is the outbox, read directly. Publishing it onto the platform bus is E12's; a
    consumer that needs the stream before then can page it from here, and
    `astra.data.*`-style delivery semantics arrive with the publisher.
    """
    if not 1 <= limit <= 1000:
        raise InvalidRequestError(f"limit must be between 1 and 1000, got {limit}")
    if after < 0:
        raise InvalidRequestError("after must not be negative")

    stored = await repository.read_events(after=after, limit=limit + 1, subject=subject)
    has_more = len(stored) > limit
    page = stored[:limit]
    return EventPageResponse(
        events=[
            EventResponse(sequence=event.sequence, event=event.to_cloudevent())
            for event in page
        ],
        next_after=page[-1].sequence if page else after,
        has_more=has_more,
    )
