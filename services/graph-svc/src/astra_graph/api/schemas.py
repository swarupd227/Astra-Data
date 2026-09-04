"""Request and response bodies.

Pydantic validates the *envelope* — that a request has a type and a properties object.
The ontology validates the *content*. Keeping them apart matters: FastAPI turns a Pydantic
failure into its own 422 shape, and S1.1.1 requires ontology rejections to arrive in one
predictable shape with the offending property named. So the models here are deliberately
permissive about properties and let the ontology do the rejecting.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class NodeWriteRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: str = Field(min_length=1, max_length=128, description="Node label from the ontology.")
    id: str | None = Field(
        default=None,
        max_length=26,
        description="Optional caller-supplied ULID. Adapters supply deterministic ids so a "
        "re-harvest is idempotent; omit to have the service issue one.",
    )
    properties: dict[str, Any] = Field(default_factory=dict)


class NodeBatchWriteRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    nodes: list[NodeWriteRequest] = Field(min_length=1, max_length=1000)


class EdgeWriteRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: str = Field(min_length=1, max_length=128, description="Edge label from the ontology.")
    id: str | None = Field(default=None, max_length=26)
    from_id: str = Field(min_length=1, max_length=26, description="Id of the source node.")
    to_id: str = Field(min_length=1, max_length=26, description="Id of the target node.")
    properties: dict[str, Any] = Field(default_factory=dict)


class ElementResponse(BaseModel):
    type: str
    properties: dict[str, Any]


class NodeBatchResponse(BaseModel):
    nodes: list[ElementResponse]


class HealthResponse(BaseModel):
    status: str
    graph: str
    schema_version: int


class NodeUpsertRequest(BaseModel):
    """An upsert replaces the whole property set, so the body carries the node as it
    should be, not the part of it that changed."""

    model_config = ConfigDict(extra="forbid")

    type: str = Field(min_length=1, max_length=128)
    properties: dict[str, Any] = Field(default_factory=dict)


class EdgeUpsertRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: str = Field(min_length=1, max_length=128)
    from_id: str = Field(min_length=1, max_length=26)
    to_id: str = Field(min_length=1, max_length=26)
    properties: dict[str, Any] = Field(default_factory=dict)


class RetireRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    reason: str = Field(
        min_length=1,
        max_length=4096,
        description="Why the node is leaving the estate. Recorded on the node and in the "
        "retirement event.",
    )


class EventResponse(BaseModel):
    """A CloudEvent as stored in the outbox."""

    sequence: int
    event: dict[str, Any]


class EventPageResponse(BaseModel):
    events: list[EventResponse]
    next_after: int = Field(
        description="Pass as `after` to continue. Equal to the last sequence returned."
    )
    has_more: bool
