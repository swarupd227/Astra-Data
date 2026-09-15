"""The Data Handling screen's own API -- stories S11.4.1/S11.4.2, F11.4.

    S11.4.1: "A Data Handling screen that states exactly what reaches a model endpoint
    and lets me confirm it... 'Sign boundary'... Boundary test: a CI and on-demand
    check..."

    S11.4.2: "All gateway requests and responses are logged with hashes; content
    logging is off by default and requires the InfoSec role to enable for a bounded
    window."

Reading the screen (position, boundary table, sign-off status, content-logging grant
status) is the same "Artizent, or the InfoSec reviewer" shape every other governance
screen in this epic already has (`DataHandlingReaderDep`); editing the position
(provider/retention/redaction-rule changes) is `PlatformEngineerDep`, the same real-
platform-action-with-no-other-named-approver precedent S11.1.2/S11.2.1 already set;
signing and enabling/disabling content logging are both narrower still --
`InfosecReviewerDep` alone (`deps.require_infosec_reviewer`'s own docstring gives the
full reasoning: this is the InfoSec reviewer's own real confirmation/control, not
Artizent's to grant on the client's behalf). Running the boundary test is open to the
same readers as the screen itself -- §15.3.7 lists "run boundary test" as one of this
screen's own reader actions, the identical "the story's own persona triggers it"
reasoning S11.3.2's Evidence Export already used.
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
from ..gateway import ContentLoggingGrantError, ContentLoggingGrantStore
from .deps import DataHandlingReaderDep, InfosecReviewerDep, PlatformEngineerDep, PrincipalDep

router = APIRouter()


class SetPositionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    providers: list[dict[str, Any]] = Field(default_factory=list, max_length=10)
    retention_terms: str = Field(max_length=4000)
    redaction_rules: list[str] = Field(default_factory=list, max_length=50)


class EnableContentLoggingRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    duration_minutes: int = Field(ge=1, le=1440)


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


def _content_logging_store(request: Request) -> ContentLoggingGrantStore:
    store: ContentLoggingGrantStore | None = getattr(request.app.state, "content_logging_grant_store", None)
    if store is None:
        raise InvalidRequestError("Data Handling is not available on this deployment")
    return store


@router.get(
    "/v1/data-handling",
    tags=["data-handling"],
    summary="Providers, retention, redaction rules, the inference boundary table, sign-off and content-logging status",
)
async def get_data_handling(request: Request, principal: PrincipalDep, roles: DataHandlingReaderDep) -> dict[str, Any]:
    status = await boundary_status(_position_store(request), _signoff_store(request))
    grant = await _content_logging_store(request).latest()
    return {
        **status.as_dict(), "inference_boundary_table": INFERENCE_BOUNDARY_TABLE,
        "content_logging_grant": grant.as_dict() if grant else None,
    }


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


@router.post(
    "/v1/data-handling:enable-content-logging",
    tags=["data-handling"],
    summary="Story S11.4.2: turn on literal request/response text logging for a bounded window -- the InfoSec reviewer's own action",
)
async def enable_content_logging(
    body: EnableContentLoggingRequest, request: Request, principal: PrincipalDep, roles: InfosecReviewerDep,
) -> dict[str, Any]:
    try:
        grant = await _content_logging_store(request).grant(
            enabled_by=principal.value, duration_minutes=body.duration_minutes,
        )
    except ContentLoggingGrantError as exc:
        raise InvalidRequestError(str(exc)) from exc
    return grant.as_dict()


@router.post(
    "/v1/data-handling:disable-content-logging",
    tags=["data-handling"],
    summary="Story S11.4.2: revoke the active content-logging grant early -- the InfoSec reviewer's own action",
)
async def disable_content_logging(
    request: Request, principal: PrincipalDep, roles: InfosecReviewerDep,
) -> dict[str, Any]:
    grant = await _content_logging_store(request).revoke(revoked_by=principal.value)
    return grant.as_dict() if grant else {"active": False}


__all__ = ["router"]
