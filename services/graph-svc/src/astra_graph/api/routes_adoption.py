"""Adoption tracking during parallel run, and the Decommission Tracker's own read --
story S9.2.2, continuing F9.2.

Reading the tracker is open to any Artizent role, the report owner, or the client
licence administrator (`DecommissionTrackerReaderDep`) -- the screen's own real §15.3.4
persona. Setting the configurable threshold is the Migration Architect's own action,
the identical "owns configurable platform policy" posture `require_migration_architect`
already established for the conformance ruleset (S4.3.2) and the visual mapping
ruleset (S6.1.1). Triggering a capture on demand is the Programme Manager's, the
identical persona `RegressionMonitor.tsx`'s own "Schedule" action already uses for a
comparable manual-trigger shape.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Request
from pydantic import BaseModel, ConfigDict, Field

from ..adoption import AdoptionError, AdoptionService
from ..errors import InvalidRequestError
from .deps import (
    DecommissionTrackerReaderDep,
    MigrationArchitectDep,
    PrincipalDep,
    ProgrammeManagerDep,
)

router = APIRouter()


def _adoption(request: Request) -> AdoptionService:
    service: AdoptionService | None = getattr(request.app.state, "adoption", None)
    if service is None:  # pragma: no cover - set in every wiring path
        raise InvalidRequestError("adoption tracking is not available on this deployment")
    return service


@router.get(
    "/v1/decommission:tracker",
    tags=["adoption"],
    summary="Per site: every released MU's own latest real adoption ratio (§15.3.4, S9.2.2)",
)
async def get_decommission_tracker(
    request: Request, principal: PrincipalDep, roles: DecommissionTrackerReaderDep,
) -> dict[str, Any]:
    return await _adoption(request).tracker()


class AdoptionConfigRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    threshold: float = Field(ge=0.0, le=1.0)


@router.get(
    "/v1/adoption:config",
    tags=["adoption"],
    summary="The current configurable adoption threshold (S9.2.2)",
)
async def get_adoption_config(
    request: Request, principal: PrincipalDep, roles: DecommissionTrackerReaderDep,
) -> dict[str, Any]:
    return (await _adoption(request).config()).as_dict()


@router.post(
    "/v1/adoption:config",
    tags=["adoption"],
    summary="Set the configurable adoption threshold that contributes to G4 readiness (S9.2.2)",
)
async def set_adoption_config(
    body: AdoptionConfigRequest, request: Request, principal: PrincipalDep, roles: MigrationArchitectDep,
) -> dict[str, Any]:
    try:
        saved = await _adoption(request).set_config(body.threshold, updated_by=principal.value)
    except AdoptionError as exc:
        raise InvalidRequestError(str(exc)) from exc
    return saved.as_dict()


@router.post(
    "/v1/adoption:capture",
    tags=["adoption"],
    summary="Capture this week's real adoption snapshot now, for every released MU (S9.2.2)",
)
async def capture_adoption(
    request: Request, principal: PrincipalDep, roles: ProgrammeManagerDep,
) -> dict[str, Any]:
    snapshots = await _adoption(request).capture(principal=principal)
    return {"captured": [snapshot.as_dict() for snapshot in snapshots], "count": len(snapshots)}


__all__ = ["router"]
