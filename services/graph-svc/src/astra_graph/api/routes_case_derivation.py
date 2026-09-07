"""Parity case derivation's own API -- stories S7.2.1/S7.2.2, continuing E7/F7.2.

    "Case count and coverage are shown on the MU page."
    "Manual cases are authored on the Parity Run screen, tagged MANUAL with the
    author, and persist across re-runs."

Neither the MU page nor the Parity Run screen exists (F10.3/F7.4, both unbuilt) -- the
same disclosed proxy every E6/E7 ADR has already used: real, queryable facts instead of
an invented screen. Deriving and adding a manual case are both the Parity Engineer's
(§2.4: "Owns the Tolerance Charter and the parity suite"), the same persona this epic's
own S7.1.1 already drove. Reading is open to any Artizent role -- the Parity Dashboard
(§2.4) is Artizent's own surface, and no client persona has a named reason to see parity
coverage the way the report owner does for C4 decisions.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Path, Request
from pydantic import BaseModel, ConfigDict, Field

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


@router.get(
    "/v1/workbooks/{workbook_id}/parity-cases",
    tags=["parity"],
    summary="Every live parity case for this workbook, derived and manual alike",
)
async def list_parity_cases(
    request: Request, principal: PrincipalDep, roles: ArtizentDep, workbook_id: str = _WORKBOOK_ID,
) -> dict[str, Any]:
    cases = await _service(request).list_cases(workbook_id)
    return {"workbook_id": workbook_id, "cases": cases, "count": len(cases)}


class AddManualCaseRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    sheet_ref: str = Field(min_length=1, max_length=64)
    filter_ctx: dict[str, Any] = Field(default_factory=dict)
    param_values: dict[str, Any] = Field(default_factory=dict)


@router.post(
    "/v1/workbooks/{workbook_id}:add-manual-parity-case",
    tags=["parity"],
    summary="Add a manual case -- 'check this one' -- with specific filters and parameters (story S7.2.2)",
)
async def add_manual_parity_case(
    body: AddManualCaseRequest,
    request: Request,
    principal: PrincipalDep,
    roles: ParityEngineerDep,
    workbook_id: str = _WORKBOOK_ID,
) -> dict[str, Any]:
    try:
        return await _service(request).add_manual_case(
            workbook_id, sheet_ref=body.sheet_ref, filter_ctx=body.filter_ctx,
            param_values=body.param_values, principal=principal,
        )
    except CaseDerivationError as exc:
        raise InvalidRequestError(str(exc)) from exc


__all__ = ["router"]
