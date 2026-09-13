"""The Status Pack — story S10.2.1, opening F10.2. See `status_pack.py`'s own module
docstring for why "generated weekly" is a `POST`-triggered snapshot and why "publish to
client" records a state transition rather than a real delivery.

Generate/edit/publish are the Programme Manager's own action (this story's own literal
"As a programme manager") — hidden, not disabled, for anyone else, the identical
hide-not-disable convention every other role-gated action in this console already uses.
Reading the pack (including its own PDF/PPTX export) is open to any Artizent role; no
specific client persona is named as a Status Pack reader anywhere in the spec or backlog
AC (unlike the Calibration Report's own explicit client analytics lead), so none is
added here — a disclosed, deliberately narrower reader gate than "publish to client"
alone might suggest, until a real story names who on the client side actually receives
one.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Request, Response
from pydantic import BaseModel, ConfigDict, Field

from ..errors import InvalidRequestError
from ..status_pack import (
    edit_pack,
    generate_pack,
    latest_pack,
    publish_pack,
    render_pdf,
    render_pptx,
)
from .deps import ArtizentDep, PrincipalDep, ProgrammeManagerDep, RepositoryDep

router = APIRouter()


class EditStatusPackRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    narrative: str = Field(min_length=1, max_length=20_000)


def _stores(request: Request) -> tuple[Any, Any, Any, Any, Any]:
    state = request.app.state
    pool = getattr(state, "pool", None)
    question_store = getattr(state, "question_store", None)
    unit_price_store = getattr(state, "unit_price_store", None)
    adoption_store = getattr(state, "adoption_store", None)
    adoption_config_store = getattr(state, "adoption_config_store", None)
    if any(dep is None for dep in (pool, question_store, unit_price_store, adoption_store, adoption_config_store)):
        raise InvalidRequestError("the Status Pack is not available on this deployment")
    return pool, question_store, unit_price_store, adoption_store, adoption_config_store


@router.post(
    "/v1/status-pack:generate",
    tags=["status-pack"],
    summary="Generate this week's Status Pack from the Programme Board's own real numbers",
)
async def post_generate_status_pack(
    request: Request, principal: PrincipalDep, roles: ProgrammeManagerDep, repository: RepositoryDep
) -> dict[str, Any]:
    pool, question_store, unit_price_store, adoption_store, adoption_config_store = _stores(request)
    pack = await generate_pack(
        pool, repository.graph_name,
        question_store=question_store, unit_price_store=unit_price_store,
        adoption_store=adoption_store, adoption_config_store=adoption_config_store,
        generated_by=principal.value,
    )
    return pack.as_dict()


@router.get(
    "/v1/status-pack",
    tags=["status-pack"],
    summary="The latest Status Pack",
)
async def get_status_pack(
    request: Request, principal: PrincipalDep, roles: ArtizentDep, repository: RepositoryDep
) -> dict[str, Any]:
    pool = request.app.state.pool
    pack = await latest_pack(pool, repository.graph_name)
    if pack is None:
        raise InvalidRequestError("no Status Pack has been generated yet")
    return pack.as_dict()


@router.post(
    "/v1/status-pack:edit",
    tags=["status-pack"],
    summary="Edit the latest Status Pack's own narrative — stored as a new version",
)
async def post_edit_status_pack(
    body: EditStatusPackRequest,
    request: Request,
    principal: PrincipalDep,
    roles: ProgrammeManagerDep,
    repository: RepositoryDep,
) -> dict[str, Any]:
    pool = request.app.state.pool
    pack = await edit_pack(pool, repository.graph_name, narrative=body.narrative, edited_by=principal.value)
    return pack.as_dict()


@router.post(
    "/v1/status-pack:publish",
    tags=["status-pack"],
    summary="Publish the latest Status Pack to the client (a real state transition, no live delivery)",
)
async def post_publish_status_pack(
    request: Request, principal: PrincipalDep, roles: ProgrammeManagerDep, repository: RepositoryDep
) -> dict[str, Any]:
    pool = request.app.state.pool
    pack = await publish_pack(pool, repository.graph_name)
    return pack.as_dict()


@router.get(
    "/v1/status-pack.pdf",
    tags=["status-pack"],
    summary="The latest Status Pack as a real PDF",
)
async def get_status_pack_pdf(
    request: Request, principal: PrincipalDep, roles: ArtizentDep, repository: RepositoryDep
) -> Response:
    pool = request.app.state.pool
    pack = await latest_pack(pool, repository.graph_name)
    if pack is None:
        raise InvalidRequestError("no Status Pack has been generated yet")
    return Response(
        content=render_pdf(pack),
        media_type="application/pdf",
        headers={"Content-Disposition": "inline; filename=status-pack.pdf"},
    )


@router.get(
    "/v1/status-pack.pptx",
    tags=["status-pack"],
    summary="The latest Status Pack as a real PPTX",
)
async def get_status_pack_pptx(
    request: Request, principal: PrincipalDep, roles: ArtizentDep, repository: RepositoryDep
) -> Response:
    pool = request.app.state.pool
    pack = await latest_pack(pool, repository.graph_name)
    if pack is None:
        raise InvalidRequestError("no Status Pack has been generated yet")
    return Response(
        content=render_pptx(pack),
        media_type="application/vnd.openxmlformats-officedocument.presentationml.presentation",
        headers={"Content-Disposition": "inline; filename=status-pack.pptx"},
    )
