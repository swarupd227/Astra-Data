"""Promotion through the Fabric deployment pipeline, and the Release Board -- story
S9.2.1, opening F9.2.

Reading the board is open to any Artizent role (`ArtizentDep`), the identical "any real
delivery role can see programme-wide status" posture `routes_provenance.py`'s own
`/v1/programmes*` reads already have. Promoting is split across the AC's own two
ceilings: `PlatformEngineerDep` for MA-08 (test), `ProgrammeManagerDep` for MA-09 (prod,
explicit approval) -- both reach `app.state.release` (`ReleaseService`), the identical
"pre-bound object on app.state" shape `app.state.g3_card` already takes.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Path, Query, Request
from pydantic import BaseModel, ConfigDict, Field

from ..errors import InvalidRequestError
from ..release import ReleaseService
from .deps import ArtizentDep, PlatformEngineerDep, PrincipalDep, ProgrammeManagerDep

router = APIRouter()

_WORKBOOK_ID = Path(min_length=5, max_length=64, description="ULID of the Workbook.")


def _release(request: Request) -> ReleaseService:
    service: ReleaseService | None = getattr(request.app.state, "release", None)
    if service is None:  # pragma: no cover - set in every wiring path
        raise InvalidRequestError("promotion is not available on this deployment")
    return service


@router.get(
    "/v1/release:board",
    tags=["release"],
    summary="Per train: MUs by pipeline stage, blockers and evidence; per site: the parallel-run window (§15.3.4, S9.2.1)",
)
async def get_release_board(request: Request, principal: PrincipalDep, roles: ArtizentDep) -> dict[str, Any]:
    return await _release(request).board()


@router.get(
    "/v1/workbooks/{workbook_id}:promotion-blockers",
    tags=["release"],
    summary="Real, computed reasons this workbook cannot promote to the given stage right now",
)
async def get_promotion_blockers(
    request: Request, principal: PrincipalDep, roles: ArtizentDep,
    workbook_id: str = _WORKBOOK_ID,
    to_stage: str = Query(default="test", description="'test' (MA-08) or 'prod' (MA-09)."),
) -> dict[str, Any]:
    blockers = await _release(request).blockers(workbook_id, to_stage=to_stage)
    return {"workbook_id": workbook_id, "to_stage": to_stage, "blockers": blockers}


@router.post(
    "/v1/workbooks/{workbook_id}:promote-to-test",
    tags=["release"],
    summary="MA-08 (L3): promote report and model to the test workspace, post-G3 only",
)
async def promote_to_test(
    request: Request, principal: PrincipalDep, roles: PlatformEngineerDep, workbook_id: str = _WORKBOOK_ID,
) -> dict[str, Any]:
    record = await _release(request).promote_to_test(workbook_id, principal=principal)
    return record.as_dict()


class PromoteToProdRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    rationale: str = Field(min_length=1)


@router.post(
    "/v1/workbooks/{workbook_id}:promote-to-prod",
    tags=["release"],
    summary="MA-09 (L2): promote report and model to production, with explicit PM approval",
)
async def promote_to_prod(
    body: PromoteToProdRequest, request: Request, principal: PrincipalDep, roles: ProgrammeManagerDep,
    workbook_id: str = _WORKBOOK_ID,
) -> dict[str, Any]:
    record = await _release(request).promote_to_prod(workbook_id, rationale=body.rationale, principal=principal)
    return record.as_dict()


__all__ = ["router"]
