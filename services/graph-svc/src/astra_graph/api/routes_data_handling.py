"""The Data Handling screen's own API -- story S11.4.1, opens F11.4.

    "A Data Handling screen that states exactly what reaches a model endpoint and lets
    me confirm it... 'Sign boundary'... Boundary test: a CI and on-demand check..."

Reading the screen (position, boundary table, sign-off status) is the same "Artizent,
or the InfoSec reviewer" shape every other governance screen in this epic already has
(`DataHandlingReaderDep`); editing the position (provider/retention/redaction-rule
changes) is `PlatformEngineerDep`, the same real-platform-action-with-no-other-named-
approver precedent S11.1.2/S11.2.1 already set; signing is narrower still --
`InfosecReviewerDep` alone, see `deps.require_infosec_reviewer`'s own docstring for why.
Running the boundary test is open to the same readers as the screen itself -- §15.3.7
lists "run boundary test" as one of this screen's own reader actions, the identical
"the story's own persona triggers it" reasoning S11.3.2's Evidence Export already used.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Request
from pydantic import BaseModel, ConfigDict, Field

from ..data_handling import (
    INFERENCE_BOUNDARY_TABLE,
    DataHandlingPositionStore,
    DataHandlingSignoffStore,
    boundary_status,
    run_boundary_test,
    sign_position,
)
from ..errors import InvalidRequestError
from .deps import DataHandlingReaderDep, InfosecReviewerDep, PlatformEngineerDep, PrincipalDep

router = APIRouter()


class SetPositionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    providers: list[dict[str, Any]] = Field(default_factory=list, max_length=10)
    retention_terms: str = Field(max_length=4000)
    redaction_rules: list[str] = Field(default_factory=list, max_length=50)


def _position_store(request: Request) -> DataHandlingPositionStore:
    store: DataHandlingPositionStore | None = getattr(request.app.state, "data_handling_position_store", None)
    if store is None:
        raise InvalidRequestError("Data Handling is not available on this deployment")
    return store


def _signoff_store(request: Request) -> DataHandlingSignoffStore:
    store: DataHandlingSignoffStore | None = getattr(request.app.state, "data_handling_signoff_store", None)
    if store is None:
        raise InvalidRequestError("Data Handling is not available on this deployment")
    return store


@router.get(
    "/v1/data-handling",
    tags=["data-handling"],
    summary="Providers, retention, redaction rules, the inference boundary table, and sign-off status",
)
async def get_data_handling(request: Request, principal: PrincipalDep, roles: DataHandlingReaderDep) -> dict[str, Any]:
    status = await boundary_status(_position_store(request), _signoff_store(request))
    return {**status.as_dict(), "inference_boundary_table": INFERENCE_BOUNDARY_TABLE}


@router.put(
    "/v1/data-handling/position",
    tags=["data-handling"],
    summary="Record a new provider/retention/redaction-rule position -- invalidates the current signature",
)
async def set_data_handling_position(
    body: SetPositionRequest, request: Request, principal: PrincipalDep, roles: PlatformEngineerDep,
) -> dict[str, Any]:
    saved = await _position_store(request).save(
        providers=tuple(body.providers), retention_terms=body.retention_terms,
        redaction_rules=tuple(body.redaction_rules), updated_by=principal.value,
    )
    return saved.as_dict()


@router.post(
    "/v1/data-handling:sign",
    tags=["data-handling"],
    summary="Record the reviewer, the position version and the date -- the InfoSec reviewer's own confirmation",
)
async def sign_data_handling(request: Request, principal: PrincipalDep, roles: InfosecReviewerDep) -> dict[str, Any]:
    signoff = await sign_position(_position_store(request), _signoff_store(request), reviewer=principal.value)
    return signoff.as_dict()


@router.post(
    "/v1/data-handling:verify-boundary",
    tags=["data-handling"],
    summary="Send sentinel row data through the one real row-level-data path and check the gateway request log",
)
async def verify_data_handling_boundary(
    request: Request, principal: PrincipalDep, roles: DataHandlingReaderDep,
) -> dict[str, Any]:
    cartographer = getattr(request.app.state, "cartographer", None)
    artefact_store = getattr(request.app.state, "artefact_store", None)
    writer = getattr(request.app.state, "writer", None)
    if cartographer is None or artefact_store is None or writer is None:
        raise InvalidRequestError("the boundary test is not available on this deployment")
    result = await run_boundary_test(
        cartographer.pool, cartographer.graph_name, artefact_store=artefact_store, writer=writer,
    )
    return result.as_dict()


__all__ = ["router"]
