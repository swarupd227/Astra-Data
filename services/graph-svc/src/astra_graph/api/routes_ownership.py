"""Owners, and the ones the directory could not place.

S1.2.3: "Owner is linked to a User node resolved against Entra ID where possible;
unresolved owners are listed for assignment".

The listing exists because resolution failing is ordinary rather than exceptional. A
Tableau site that has outlived a reorganisation is full of owners who no longer exist in
the directory, and each one is a workbook that cannot be sent a G3 gate request
(spec §13.1) until somebody says who owns it now. So it is a queue to work through, in the
same shape as the Parse Quality Queue.
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Request
from pydantic import BaseModel, ConfigDict, Field

from ..directory import DirectoryError, validate_directory_id
from ..errors import ElementNotFoundError, InvalidRequestError
from ..graph import GraphRepository
from ..harvest.identity import derive_id
from ..principal import Principal
from ..writes import GraphWriter
from .deps import ArtizentDep, PrincipalDep

logger = logging.getLogger(__name__)

router = APIRouter()

#: Users are far fewer than estate objects, but the listing is still bounded.
MAX_USERS = 5_000


class AssignOwnerRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    site: str = Field(min_length=1, max_length=256, description="Site the user came from.")
    upn: str = Field(
        min_length=1,
        max_length=256,
        description="The identity as the source knows it, exactly as the listing reports "
        "it. This is what the User node is keyed on; assigning adds the directory link "
        "rather than changing who the user is.",
    )
    directory_id: str = Field(
        min_length=1,
        max_length=128,
        description="Object id of the directory user, as a GUID.",
    )
    display: str | None = Field(default=None, max_length=256)


class UnresolvedResponse(BaseModel):
    unresolved: list[dict[str, Any]]
    count: int
    resolver: str


def _repository(request: Request) -> GraphRepository:
    repository: GraphRepository | None = getattr(request.app.state, "repository", None)
    if repository is None:  # pragma: no cover
        raise InvalidRequestError("graph store is not ready")
    return repository


def _writer(request: Request) -> GraphWriter:
    writer: GraphWriter | None = getattr(request.app.state, "writer", None)
    if writer is None:  # pragma: no cover
        raise InvalidRequestError("graph store is not ready")
    return writer


def _resolver_kind(request: Request) -> str:
    resolver = getattr(request.app.state, "directory", None)
    return str(getattr(resolver, "kind", "none"))


@router.get(
    "/v1/ownership/unresolved",
    response_model=UnresolvedResponse,
    tags=["ownership"],
    summary="Owners the directory could not place",
)
async def unresolved_owners(
    request: Request,
    principal: PrincipalDep,
    roles: ArtizentDep,
    limit: int = 200,
) -> UnresolvedResponse:
    """Users with no directory link, worst-connected first.

    ``owns`` counts the workbooks each holds, because that is what makes one unresolved
    owner more urgent than another: it is the number of gate requests that currently have
    nobody to go to.
    """
    if not 1 <= limit <= MAX_USERS:
        raise InvalidRequestError(f"limit must be between 1 and {MAX_USERS}, got {limit}")

    repository = _repository(request)
    users = await repository.nodes_of_type("User", limit=MAX_USERS)
    unresolved = [user for user in users if not user.properties.get("directory_id")]

    owned = await repository.incoming_counts(
        [user.id for user in unresolved], edge_type="OWNED_BY"
    )
    listing = sorted(
        (
            {
                "id": user.id,
                "upn": user.properties.get("upn"),
                "display": user.properties.get("display"),
                "licence_tier": user.properties.get("licence_tier"),
                "owns": owned.get(user.id, 0),
            }
            for user in unresolved
        ),
        key=lambda item: (-int(item["owns"] or 0), str(item["upn"])),
    )
    return UnresolvedResponse(
        unresolved=listing[:limit],
        count=len(listing),
        resolver=_resolver_kind(request),
    )


@router.post(
    "/v1/ownership/assign",
    tags=["ownership"],
    summary="Link a source identity to a directory user by hand",
)
async def assign_owner(
    body: AssignOwnerRequest,
    request: Request,
    principal: PrincipalDep,
    roles: ArtizentDep,
) -> dict[str, Any]:
    """Assign the directory link the resolver could not find.

    A person's judgement recorded as a fact, so it goes through the ordinary write path:
    the change is an event like any other, and who made it is on the record
    (``directory_resolved_at``, and the event's principal).
    """
    try:
        directory_id = validate_directory_id(body.directory_id)
    except DirectoryError as exc:
        raise InvalidRequestError(str(exc)) from exc

    node_id = derive_id(body.site, f"user:{body.upn}")
    repository = _repository(request)
    existing = await repository.get_node_record(node_id)
    if existing is None or existing.label != "User":
        raise ElementNotFoundError(
            f"no user '{body.upn}' harvested from site '{body.site}'"
        )
    if existing.properties.get("directory_id"):
        raise InvalidRequestError(
            f"user '{body.upn}' is already linked to directory id "
            f"{existing.properties['directory_id']}"
        )

    properties: dict[str, Any] = {
        "directory_id": directory_id,
        "directory_resolved_at": _now(),
    }
    if body.display:
        properties["display"] = body.display

    updated = await _writer(request).set_node_properties(
        node_id, properties, principal=principal
    )
    logger.info(
        "ownership: %s linked %s to directory id %s", principal.value, body.upn, directory_id
    )
    return {
        "id": node_id,
        "upn": body.upn,
        "directory_id": directory_id,
        "display": updated["properties"].get("display"),
        "assigned_by": principal.value,
    }


def _now() -> str:
    from datetime import UTC, datetime

    return datetime.now(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")


__all__ = ["Principal", "router"]
