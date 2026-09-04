"""The artefact store's HTTP surface — S2.4.2.

Two routes for reading, deliberately different shapes: `GET /v1/artefacts/{id}` returns
metadata — never the bytes, per `artefacts.py`'s own reasoning — and
`GET /v1/artefacts/{id}/content` returns exactly the bytes and nothing else, for a viewer that
already knows it wants an image. Nothing else in this service imports the second route; a
context contract (`context/`) can only ever see the first shape, which cannot carry pixels.
"""

from __future__ import annotations

import base64
import binascii
import logging
from typing import Annotated, Any

from fastapi import APIRouter, Path, Query, Request, Response, status
from pydantic import BaseModel, ConfigDict, Field

from ..artefacts import ArtefactError, ArtefactStore
from ..errors import ElementNotFoundError, InvalidRequestError
from .deps import ArtizentDep, PrincipalDep

logger = logging.getLogger(__name__)

router = APIRouter()

_ARTEFACT_ID = Path(min_length=5, max_length=64, description="Artefact record id.")

#: 24 MB of base64 is ~18 MB of image — comfortably past any PNG this adapter produces, and
#: still a bound: an artefact store is not the place for an unbounded upload.
_MAX_BASE64_LENGTH = 24_000_000


class StoreArtefactRequest(BaseModel):
    """A binary artefact, as its producer submits it. S2.4.2's shape is a screenshot; the
    fields are named for any binary artefact this store will hold later."""

    model_config = ConfigDict(extra="forbid")

    kind: str = Field(min_length=1, max_length=64, examples=["visual_capture"])
    mu_ref: str = Field(
        min_length=1,
        max_length=128,
        description="The Migration Unit this artefact belongs to. E3 has not minted MU ids "
        "yet, so the workbook LUID is the accepted stand-in until it does.",
    )
    case_id: str = Field(default="", max_length=128)
    media_type: str = Field(min_length=1, max_length=64, examples=["image/png"])
    content_base64: str = Field(min_length=1, max_length=_MAX_BASE64_LENGTH)
    width: int | None = Field(default=None, ge=0)
    height: int | None = Field(default=None, ge=0)
    adapter_name: str | None = Field(default=None, max_length=64)
    adapter_version: str | None = Field(default=None, max_length=32)
    interface_version: str | None = Field(default=None, max_length=16)


def _store(request: Request) -> ArtefactStore:
    store: ArtefactStore | None = getattr(request.app.state, "artefact_store", None)
    if store is None:  # pragma: no cover - set in every wiring path
        raise InvalidRequestError("the artefact store is not available on this deployment")
    return store


@router.post(
    "/v1/artefacts",
    status_code=status.HTTP_201_CREATED,
    tags=["artefacts"],
    summary="Store a binary artefact, content-addressed and linked to an MU",
)
async def store_artefact(
    body: StoreArtefactRequest,
    request: Request,
    principal: PrincipalDep,
    roles: ArtizentDep,
) -> dict[str, Any]:
    """§16's boundary applies here as much as anywhere: the response is metadata, and the
    bytes this call accepted are never echoed back in it."""
    try:
        content = base64.b64decode(body.content_base64, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise InvalidRequestError(f"content_base64 is not valid base64: {exc}") from None

    try:
        record = await _store(request).store(
            kind=body.kind,
            mu_ref=body.mu_ref,
            case_id=body.case_id,
            content=content,
            media_type=body.media_type,
            width=body.width,
            height=body.height,
            adapter_name=body.adapter_name,
            adapter_version=body.adapter_version,
            interface_version=body.interface_version,
            created_by=principal.value,
        )
    except ArtefactError as exc:
        raise InvalidRequestError(str(exc)) from None

    logger.info(
        "artefact %s recorded by %s: %s for mu_ref=%s (%d bytes)",
        record.id,
        principal.value,
        record.kind,
        record.mu_ref,
        record.size_bytes,
    )
    return record.as_dict()


@router.get(
    "/v1/artefacts/{artefact_id}",
    tags=["artefacts"],
    summary="An artefact's metadata — never its bytes",
)
async def get_artefact(
    request: Request,
    principal: PrincipalDep,
    roles: ArtizentDep,
    artefact_id: Annotated[str, _ARTEFACT_ID],
) -> dict[str, Any]:
    record = await _store(request).get(artefact_id)
    if record is None:
        raise ElementNotFoundError(f"no artefact '{artefact_id}'")
    return record.as_dict()


@router.get(
    "/v1/artefacts/{artefact_id}/content",
    tags=["artefacts"],
    summary="An artefact's bytes, for a human viewer",
)
async def get_artefact_content(
    request: Request,
    principal: PrincipalDep,
    roles: ArtizentDep,
    artefact_id: Annotated[str, _ARTEFACT_ID],
) -> Response:
    """The one route in this service that returns an artefact's bytes.

    For the console's own image tag — nothing that assembles model context calls this, and
    nothing should: see `artefacts.py`'s "never sent to a model endpoint" reasoning.
    """
    store = _store(request)
    record = await store.get(artefact_id)
    if record is None:
        raise ElementNotFoundError(f"no artefact '{artefact_id}'")
    content = await store.content(artefact_id)
    if content is None:  # pragma: no cover - a record with no bytes is a store defect
        raise ElementNotFoundError(f"artefact '{artefact_id}' has no stored content")
    return Response(content=content, media_type=record.media_type)


@router.get(
    "/v1/artefacts",
    tags=["artefacts"],
    summary="Artefacts linked to a Migration Unit",
)
async def list_artefacts(
    request: Request,
    principal: PrincipalDep,
    roles: ArtizentDep,
    mu_ref: Annotated[str, Query(min_length=1, max_length=128)],
    kind: Annotated[str | None, Query(max_length=64)] = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
) -> dict[str, Any]:
    records = await _store(request).for_mu(mu_ref, kind=kind, limit=limit)
    return {"mu_ref": mu_ref, "artefacts": [record.as_dict() for record in records]}
