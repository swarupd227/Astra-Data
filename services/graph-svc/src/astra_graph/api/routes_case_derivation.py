"""Parity case derivation's own API -- story S7.2.1, continuing E7/F7.2.

    "Case count and coverage are shown on the MU page."

No MU page exists (F10.3, unbuilt) -- this is the same disclosed proxy every E6/E7 ADR
has already used: a real, queryable fact instead of an invented screen. Deriving is the
Parity Engineer's (§2.4: "Owns the Tolerance Charter and the parity suite"), the same
persona this epic's own S7.1.1 already drove. Reading is open to any Artizent role -- the
Parity Dashboard (§2.4) is Artizent's own surface, and no client persona has a named
reason to see parity coverage the way the report owner does for C4 decisions.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Path, Request

from ..case_derivation import CaseDerivationError, CaseDerivationService
from ..errors import ElementNotFoundError, InvalidRequestError
from ..tolerance_charter import ToleranceCharterService
from .deps import ArtizentDep, ParityEngineerDep, PrincipalDep

router = APIRouter()

_WORKBOOK_ID = Path(min_length=5, max_length=64, description="ULID of the Workbook (MU identity).")


def _service(request: Request) -> CaseDerivationService:
    service: CaseDerivationService | None = getattr(request.app.state, "case_derivation", None)
    if service is None:
        raise InvalidRequestError("parity case derivation is not available on this deployment")
    return service


def _charter_service(request: Request) -> ToleranceCharterService:
    service: ToleranceCharterService | None = getattr(request.app.state, "tolerance_charter", None)
    if service is None:
        raise InvalidRequestError("the Tolerance Charter is not available on this deployment")
    return service


@router.post(
    "/v1/workbooks/{workbook_id}:derive-parity-cases",
    tags=["parity"],
    summary="Derive this workbook's parity cases deterministically from each sheet (§10.1)",
)
async def derive_parity_cases(
    request: Request,
    principal: PrincipalDep,
    roles: ParityEngineerDep,
    workbook_id: str = _WORKBOOK_ID,
) -> dict[str, Any]:
    charter_version = await _charter_service(request).latest()
    try:
        return await _service(request).derive(
            workbook_id, charter_version=str(charter_version.version),
            charter=charter_version.charter, principal=principal,
        )
    except CaseDerivationError as exc:
        raise InvalidRequestError(str(exc)) from exc


@router.get(
    "/v1/workbooks/{workbook_id}/parity-suite",
    tags=["parity"],
    summary="This workbook's own case count and coverage, as last derived",
)
async def get_parity_suite(
    request: Request, principal: PrincipalDep, roles: ArtizentDep, workbook_id: str = _WORKBOOK_ID,
) -> dict[str, Any]:
    suite = await _service(request).suite(workbook_id)
    if suite is None:
        raise ElementNotFoundError(f"workbook '{workbook_id}' has no derived parity cases yet")
    return {"suite": suite.as_dict()}


__all__ = ["router"]
