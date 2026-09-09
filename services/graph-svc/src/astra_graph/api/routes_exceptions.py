"""Redesign flags as work items -- story S6.2.1 -- the Exception Desk itself -- story
S8.3.1, opening F8.3 -- and its own Programme Board tile -- story S8.3.2, continuing
F8.3.

    "Redesign flags create ExceptionCases of class VISUAL_REDESIGN routed to the Exception
    Desk with the source screenshot, the mapping reason and the placeholder location."
    "An MU with open redesign flags cannot enter PROVING for the affected sheets; other
    sheets proceed."
    "Closing the flag records the engineer, the Desktop commit hash and the date."

Reading the queue (what `visual_redesign.py`'s own module docstring calls "the Exception
Desk" -- a real node, not yet a real screen) is open to any Artizent role, the same posture
every other "read what a prior action produced" route in this API has; closing a case is
the migration engineer's (`MigrationEngineerDep`, the persona this story's own acceptance
criteria names). The proving-readiness check has no consumer yet (E7's Arbiter does not
exist) but is real and callable today, the same "build the real check even with nothing to
call it yet" posture this codebase has taken since S5.3.3's own calibration report.

**S8.3.1's own routes reuse `_case_view` but not `_compositor`** -- the Exception Desk's
own decisions need more than a `Compositor`'s own `pool`/`graph_name`/`writer` (artefact
store, provenance store, target adapter, tolerance charter store too), so they read a
real, dedicated `app.state.exception_desk` (`ExceptionDeskService`, the identical
"pre-bound object on app.state" shape `MenderService` already set) instead -- see
`_exception_desk`'s own docstring. `list_exceptions`/`close_exception`/`get_proving_
readiness` are untouched, still reading `_compositor` exactly as S6.2.1 left them.

**S8.3.2's own `exceptions:ageing` route reads `_compositor`, not `_exception_desk`** --
`exception_ageing.exception_ageing` needs only `pool`/`graph_name`, so it is called
directly rather than adding a needless method to `ExceptionDeskService`. Gated
`ArtizentDep`, the identical broad-read posture every other read-only Programme Board
pane already has (`GET /v1/programmes`, `GET /v1/families:awaiting-g2`,
`GET /v1/calculations:class-mix`) -- this tile has no action of its own to gate more
narrowly.
"""

from __future__ import annotations

from typing import Any, Literal

from fastapi import APIRouter, Path, Query, Request
from pydantic import BaseModel, ConfigDict, Field

from ..compositor import Compositor
from ..errors import ElementNotFoundError, InvalidRequestError
from ..exception_ageing import exception_ageing
from ..exception_desk import ExceptionDeskError, ExceptionDeskService
from ..graph.queries import NODE_INDEX_TABLE
from ..lineage import hydrate
from ..visual_redesign import (
    RedesignExceptionError,
    can_enter_proving,
    close_redesign_exception,
)
from .deps import ArtizentDep, ExceptionDeskReaderDep, MigrationEngineerDep, PrincipalDep

router = APIRouter()

_CASE_ID = Path(min_length=5, max_length=64, description="ULID of the ExceptionCase.")
_WORKBOOK_ID = Path(min_length=5, max_length=64, description="ULID of the Workbook.")


def _compositor(request: Request) -> Compositor:
    compositor: Compositor | None = getattr(request.app.state, "compositor", None)
    if compositor is None:
        raise InvalidRequestError("exceptions are not available on this deployment")
    return compositor


def _exception_desk(request: Request) -> ExceptionDeskService:
    service: ExceptionDeskService | None = getattr(request.app.state, "exception_desk", None)
    if service is None:
        raise InvalidRequestError("the Exception Desk is not available on this deployment")
    return service


def _case_view(case_id: str, properties: dict[str, Any]) -> dict[str, Any]:
    return {"id": case_id, **properties}


@router.get(
    "/v1/exceptions",
    tags=["exceptions"],
    summary="Every live ExceptionCase -- the Exception Desk's own queue, until it has a screen (§11.3)",
)
async def list_exceptions(
    request: Request,
    principal: PrincipalDep,
    roles: ArtizentDep,
    state: str | None = Query(default=None, description="Filter to this state, e.g. 'OPEN'."),
    mu_ref: str | None = Query(default=None, description="Filter to this Migration Unit (workbook id)."),
) -> dict[str, Any]:
    engine = _compositor(request)
    async with engine.pool.acquire() as conn:
        rows = await conn.fetch(
            f"""SELECT id FROM {NODE_INDEX_TABLE}
             WHERE graph = $1 AND kind = 'node' AND label = 'ExceptionCase' AND retired_at IS NULL""",
            engine.graph_name,
        )
        cases = await hydrate(conn, engine.graph_name, "ExceptionCase", [row["id"] for row in rows])

    views = [
        _case_view(case_id, properties)
        for case_id, properties in cases.items()
        if (state is None or properties.get("state") == state)
        and (mu_ref is None or properties.get("mu_ref") == mu_ref)
    ]
    return {"exceptions": views, "count": len(views)}


class CloseExceptionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    desktop_commit_hash: str = Field(min_length=1, max_length=200)


@router.post(
    "/v1/exceptions/{case_id}:close",
    tags=["exceptions"],
    summary="Close a redesign exception -- records the engineer, the Desktop commit and the date (§11.3)",
)
async def close_exception(
    body: CloseExceptionRequest,
    request: Request,
    principal: PrincipalDep,
    roles: MigrationEngineerDep,
    case_id: str = _CASE_ID,
) -> dict[str, Any]:
    engine = _compositor(request)
    try:
        properties = await close_redesign_exception(
            engine.pool, engine.graph_name, engine.writer,
            case_id=case_id, desktop_commit_hash=body.desktop_commit_hash, principal=principal,
        )
    except (ElementNotFoundError, RedesignExceptionError) as exc:
        raise InvalidRequestError(str(exc)) from exc
    return _case_view(case_id, properties)


@router.get(
    "/v1/workbooks/{workbook_id}:proving-readiness",
    tags=["exceptions"],
    summary="Which of a workbook's own worksheets may enter PROVING today (§3.2)",
)
async def get_proving_readiness(
    request: Request, principal: PrincipalDep, roles: ArtizentDep, workbook_id: str = _WORKBOOK_ID
) -> dict[str, Any]:
    engine = _compositor(request)
    readiness = await can_enter_proving(engine.pool, engine.graph_name, workbook_id)
    return readiness.as_dict()


# --------------------------------------------------------------- S8.3.1: the Exception Desk


@router.get(
    "/v1/exception-desk",
    tags=["exceptions"],
    summary="The queue: every live ExceptionCase ordered by train sequence then age (§11.3, §15.3.4)",
)
async def exception_desk_queue(
    request: Request,
    principal: PrincipalDep,
    roles: ExceptionDeskReaderDep,
    train: str | None = Query(default=None, description="Filter to this ReleaseTrain id."),
    failure_class: str | None = Query(default=None, alias="class", description="Filter to this §11.1 class."),
    site: str | None = Query(default=None, description="Filter to this site name."),
    assignee: str | None = Query(default=None, description="Filter to this assignee."),
) -> dict[str, Any]:
    service = _exception_desk(request)
    entries = await service.queue(train=train, failure_class=failure_class, site=site, assignee=assignee)
    return {"entries": entries, "count": len(entries)}


@router.get(
    "/v1/exceptions/{case_id}",
    tags=["exceptions"],
    summary="The case page: evidence, artefact, Mender pass history (§11.3)",
)
async def get_exception_case(
    request: Request, principal: PrincipalDep, roles: ExceptionDeskReaderDep, case_id: str = _CASE_ID,
) -> dict[str, Any]:
    service = _exception_desk(request)
    try:
        return await service.case_detail(case_id)
    except ElementNotFoundError as exc:
        raise InvalidRequestError(str(exc)) from exc


class BulkAssignRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    exception_case_ids: list[str] = Field(min_length=1)
    assignee: str = Field(min_length=1, max_length=200)


@router.post(
    "/v1/exceptions:bulk-assign",
    tags=["exceptions"],
    summary="Assign several ExceptionCases to one engineer at once (§15.3.4's own 'bulk-assign')",
)
async def bulk_assign_exceptions(
    body: BulkAssignRequest, request: Request, principal: PrincipalDep, roles: MigrationEngineerDep,
) -> dict[str, Any]:
    service = _exception_desk(request)
    updated = await service.bulk_assign(
        exception_case_ids=tuple(body.exception_case_ids), assignee=body.assignee, principal=principal,
    )
    return {"assignee": body.assignee, "updated": list(updated), "count": len(updated)}


class PatchExceptionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    dax: str = Field(min_length=1)
    rationale: str = Field(min_length=1)
    workspace: str = Field(default="dev", min_length=1, max_length=100)


@router.post(
    "/v1/exceptions/{case_id}:patch",
    tags=["exceptions"],
    summary="Patch: a new Measure from the engineer's own DAX, validated and re-proved (§11.3)",
)
async def patch_exception(
    body: PatchExceptionRequest, request: Request, principal: PrincipalDep, roles: MigrationEngineerDep,
    case_id: str = _CASE_ID,
) -> dict[str, Any]:
    service = _exception_desk(request)
    try:
        result = await service.patch(
            case_id, dax=body.dax, rationale=body.rationale, workspace=body.workspace, principal=principal,
        )
    except (ElementNotFoundError, ExceptionDeskError) as exc:
        raise InvalidRequestError(str(exc)) from exc
    return result.as_dict()


class RedesignExceptionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    route: Literal["desktop", "foundry"]
    rationale: str = Field(min_length=1)
    desktop_commit_hash: str | None = Field(default=None, max_length=200)


@router.post(
    "/v1/exceptions/{case_id}:redesign",
    tags=["exceptions"],
    summary="Redesign: route to the Foundry, or record where the Desktop work landed (§11.3)",
)
async def redesign_exception(
    body: RedesignExceptionRequest, request: Request, principal: PrincipalDep, roles: MigrationEngineerDep,
    case_id: str = _CASE_ID,
) -> dict[str, Any]:
    service = _exception_desk(request)
    try:
        if body.route == "desktop":
            if not body.desktop_commit_hash:
                raise InvalidRequestError("route='desktop' needs a desktop_commit_hash")
            result = await service.redesign_desktop(
                case_id, rationale=body.rationale, desktop_commit_hash=body.desktop_commit_hash,
                principal=principal,
            )
        else:
            result = await service.redesign_foundry(case_id, rationale=body.rationale, principal=principal)
    except (ElementNotFoundError, ExceptionDeskError) as exc:
        raise InvalidRequestError(str(exc)) from exc
    return result.as_dict()


class ModelDefectDecisionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    rationale: str = Field(min_length=1)


@router.post(
    "/v1/exceptions/{case_id}:decide-model-defect",
    tags=["exceptions"],
    summary="Model defect: a real Foundry change request against the family (§11.3)",
)
async def decide_model_defect_exception(
    body: ModelDefectDecisionRequest, request: Request, principal: PrincipalDep, roles: MigrationEngineerDep,
    case_id: str = _CASE_ID,
) -> dict[str, Any]:
    service = _exception_desk(request)
    try:
        return await service.model_defect(case_id, rationale=body.rationale, principal=principal)
    except (ElementNotFoundError, ExceptionDeskError) as exc:
        raise InvalidRequestError(str(exc)) from exc


class SourceDefectDecisionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    rationale: str = Field(min_length=1)
    resolution: Literal["REPRODUCE", "FIX_WITH_SIGN_OFF"]
    owner_sign_off: str | None = Field(default=None, max_length=2000)


@router.post(
    "/v1/exceptions/{case_id}:decide-source-defect",
    tags=["exceptions"],
    summary="Source defect: record, notify the owner, reproduce or fix with sign-off (§11.3)",
)
async def decide_source_defect_exception(
    body: SourceDefectDecisionRequest, request: Request, principal: PrincipalDep, roles: MigrationEngineerDep,
    case_id: str = _CASE_ID,
) -> dict[str, Any]:
    service = _exception_desk(request)
    try:
        return await service.source_defect(
            case_id, rationale=body.rationale, resolution=body.resolution,
            owner_sign_off=body.owner_sign_off, principal=principal,
        )
    except (ElementNotFoundError, ExceptionDeskError) as exc:
        raise InvalidRequestError(str(exc)) from exc


# ------------------------------------------------- S8.3.2: ageing and the Mender close rate


@router.get(
    "/v1/exceptions:ageing",
    tags=["exceptions"],
    summary="Programme Board tile: open exceptions by class and age band, and the Mender close rate (§16.6, §25)",
)
async def exceptions_ageing(request: Request, principal: PrincipalDep, roles: ArtizentDep) -> dict[str, Any]:
    engine = _compositor(request)
    return await exception_ageing(engine.pool, engine.graph_name)


__all__ = ["router"]
