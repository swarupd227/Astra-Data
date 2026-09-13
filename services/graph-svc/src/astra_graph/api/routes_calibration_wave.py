"""The Calibration Report — story S10.2.1, opening F10.2. See `calibration_wave.py`'s
own module docstring for why "per F13.2" in the backlog AC is read as F13.1/S13.1.2's
report, and which two named fields are honestly not built.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Request, Response
from pydantic import BaseModel, ConfigDict, Field

from ..calibration_wave import calibration_report, render_calibration_report_pdf, sign_report
from ..errors import InvalidRequestError
from .deps import CalibrationReportReaderDep, PrincipalDep, ProgrammeManagerDep, RepositoryDep

router = APIRouter()


class SignCalibrationReportRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    countersigned_by: str = Field(min_length=1, max_length=200)


def _stores(request: Request) -> tuple[Any, Any]:
    state = request.app.state
    scope_store = getattr(state, "scope_store", None)
    unit_price_store = getattr(state, "unit_price_store", None)
    if scope_store is None or unit_price_store is None:
        raise InvalidRequestError("the Calibration Report is not available on this deployment")
    return scope_store, unit_price_store


@router.get(
    "/v1/calibration:report",
    tags=["calibration"],
    summary="The Calibration Report, live, plus the comparison to the last signed baseline",
)
async def get_calibration_report(
    request: Request, principal: PrincipalDep, roles: CalibrationReportReaderDep, repository: RepositoryDep
) -> dict[str, Any]:
    scope_store, unit_price_store = _stores(request)
    pool = request.app.state.pool
    return await calibration_report(
        pool, repository.graph_name, scope_store=scope_store, unit_price_store=unit_price_store,
    )


@router.post(
    "/v1/calibration:sign",
    tags=["calibration"],
    summary="Sign the Calibration Report, writing the calibrated baseline (S13.1.2)",
)
async def post_sign_calibration_report(
    body: SignCalibrationReportRequest,
    request: Request,
    principal: PrincipalDep,
    roles: ProgrammeManagerDep,
    repository: RepositoryDep,
) -> dict[str, Any]:
    scope_store, unit_price_store = _stores(request)
    pool = request.app.state.pool
    baseline = await sign_report(
        pool, repository.graph_name, scope_store=scope_store, unit_price_store=unit_price_store,
        countersigned_by=body.countersigned_by, signed_by=principal.value,
    )
    return baseline.as_dict()


@router.get(
    "/v1/calibration:report.pdf",
    tags=["calibration"],
    summary="The Calibration Report as a real PDF (§15.3.1's own 'export')",
)
async def get_calibration_report_pdf(
    request: Request, principal: PrincipalDep, roles: CalibrationReportReaderDep, repository: RepositoryDep
) -> Response:
    scope_store, unit_price_store = _stores(request)
    pool = request.app.state.pool
    result = await calibration_report(
        pool, repository.graph_name, scope_store=scope_store, unit_price_store=unit_price_store,
    )
    pdf_bytes = render_calibration_report_pdf(result)
    return Response(
        content=pdf_bytes,
        media_type="application/pdf",
        headers={"Content-Disposition": "inline; filename=calibration-report.pdf"},
    )
