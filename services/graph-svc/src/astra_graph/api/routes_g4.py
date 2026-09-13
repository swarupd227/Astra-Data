"""G4 decommission: the per-site readiness checklist, the gate card, and the owner
confirmation -- story S9.3.1, opening F9.3.

Reading the card (and its own readiness checklist) is open to the identical set the
Decommission Tracker already reads under (`DecommissionTrackerReaderDep`, S9.2.2): any
Artizent role, the report owner, or the client licence administrator. Approving or
deferring G4 is the licence administrator's own action alone (`G4ApproverDep`), the
identical narrower-than-reader shape `G3ApproverDep` already set for G3. Confirming a
single MU's own decommission readiness is the report owner's (`DecommissionConfirmerDep`),
the same persona G3 approval already uses, for its own §14.4 "owner confirmation" fact.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Request
from pydantic import BaseModel, ConfigDict, Field

from ..errors import InvalidRequestError
from ..g4_card import G4CardService
from .deps import (
    DecommissionConfirmerDep,
    DecommissionTrackerReaderDep,
    G4ApproverDep,
    PrincipalDep,
)

router = APIRouter()


def _g4(request: Request) -> G4CardService:
    service: G4CardService | None = getattr(request.app.state, "g4_card", None)
    if service is None:  # pragma: no cover - set in every wiring path
        raise InvalidRequestError("G4 decommission is not available on this deployment")
    return service


@router.get(
    "/v1/sites/{site_id}:g4-card",
    tags=["g4"],
    summary="The G4 card: readiness checklist, MUs, licence tier, and confirmation text (§15.3.4, S9.3.1)",
)
async def get_g4_card(
    site_id: str, request: Request, principal: PrincipalDep, roles: DecommissionTrackerReaderDep,
) -> dict[str, Any]:
    return await _g4(request).card(site_id)


class ApproveG4Request(BaseModel):
    model_config = ConfigDict(extra="forbid")

    rationale: str = Field(min_length=1)
    countersigned_by: str = Field(min_length=1)


@router.post(
    "/v1/sites/{site_id}:approve-g4",
    tags=["g4"],
    summary="Authorise decommission: archive source workbooks, release the licence (§13.1's own G4 row, S9.3.1)",
)
async def approve_g4(
    site_id: str, body: ApproveG4Request, request: Request,
    principal: PrincipalDep, roles: G4ApproverDep,
) -> dict[str, Any]:
    result = await _g4(request).approve(
        site_id, rationale=body.rationale, countersigned_by=body.countersigned_by, principal=principal,
    )
    return result.as_dict()


class DeferG4Request(BaseModel):
    model_config = ConfigDict(extra="forbid")

    reason: str = Field(min_length=1)
    target_date: str = Field(min_length=1)


@router.post(
    "/v1/sites/{site_id}:defer-g4",
    tags=["g4"],
    summary="Defer G4 with a real reason and a new target date (§15.3.4's own 'defer with reason', S9.3.1)",
)
async def defer_g4(
    site_id: str, body: DeferG4Request, request: Request,
    principal: PrincipalDep, roles: G4ApproverDep,
) -> dict[str, Any]:
    result = await _g4(request).defer(site_id, reason=body.reason, target_date=body.target_date, principal=principal)
    return result.as_dict()


@router.post(
    "/v1/workbooks/{workbook_id}:confirm-decommission",
    tags=["g4"],
    summary="The report owner confirms this MU is ready to have its source decommissioned (§14.4, S9.3.1)",
)
async def confirm_decommission(
    workbook_id: str, request: Request, principal: PrincipalDep, roles: DecommissionConfirmerDep,
) -> dict[str, Any]:
    confirmation = await _g4(request).confirm(workbook_id, confirmed_by=principal.value)
    return confirmation.as_dict()


__all__ = ["router"]
