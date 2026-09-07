"""§10.3's diff and verdict, over HTTP -- stories S7.4.1/S7.4.2, closing F7.4.

Diffing is the Parity Engineer's own action (§2.4: "Owns the Tolerance Charter and the
parity suite"), the same persona F7.2/F7.3 have already driven. Reading a run --
`GET .../parity-run` -- and the dashboard -- `GET .../parity-dashboard` (story S7.4.2)
-- are both open to any Artizent role *or the report owner specifically*
(`ParityDashboardReaderDep`), the identical "any Artizent role, or the client role this
screen is actually for" shape `require_c4_redesign_reader`/`require_tolerance_charter_
reader` already set. `GET .../parity-run` was `ArtizentDep`-only under S7.4.1, before
this story's own report-owner persona needed the per-run view too -- widened here, not a
second endpoint, since it already returns exactly the shape a per-run view needs.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Path, Request

from ..errors import ElementNotFoundError, InvalidRequestError
from ..verdicts import VerdictError, VerdictsService
from .deps import ParityDashboardReaderDep, ParityEngineerDep, PrincipalDep

router = APIRouter()

_WORKBOOK_ID = Path(min_length=5, max_length=64, description="ULID of the Workbook (MU identity).")


def _service(request: Request) -> VerdictsService:
    service: VerdictsService | None = getattr(request.app.state, "verdicts", None)
    if service is None:
        raise InvalidRequestError("the diff and verdict engine is not available on this deployment")
    return service


@router.post(
    "/v1/workbooks/{workbook_id}:run-parity",
    tags=["parity"],
    summary="Diff every already-executed live parity case under the current Tolerance Charter (§10.3)",
)
async def run_parity(
    request: Request, principal: PrincipalDep, roles: ParityEngineerDep, workbook_id: str = _WORKBOOK_ID,
) -> dict[str, Any]:
    charter_store = getattr(request.app.state, "tolerance_charter_store", None)
    if charter_store is None:
        raise InvalidRequestError("the tolerance charter is not available on this deployment")
    version = await charter_store.latest()
    try:
        return await _service(request).run(
            workbook_id, charter=version.charter, charter_version=str(version.version), principal=principal,
        )
    except VerdictError as exc:
        raise InvalidRequestError(str(exc)) from exc


@router.get(
    "/v1/workbooks/{workbook_id}/parity-run",
    tags=["parity"],
    summary="The workbook's own most recent ParityRun and its Verdicts",
)
async def get_latest_parity_run(
    request: Request, principal: PrincipalDep, roles: ParityDashboardReaderDep, workbook_id: str = _WORKBOOK_ID,
) -> dict[str, Any]:
    run = await _service(request).latest(workbook_id)
    if run is None:
        raise ElementNotFoundError(f"no ParityRun exists yet for workbook '{workbook_id}'")
    return run


@router.get(
    "/v1/workbooks/{workbook_id}/parity-dashboard",
    tags=["parity"],
    summary="Per-sheet pass/fail/inconclusive counts, first-pass rate, waived count, failing "
    "cells, and the pass-rate trend across runs (§15.3.5, story S7.4.2)",
)
async def get_parity_dashboard(
    request: Request, principal: PrincipalDep, roles: ParityDashboardReaderDep, workbook_id: str = _WORKBOOK_ID,
) -> dict[str, Any]:
    dashboard = await _service(request).dashboard(workbook_id)
    if dashboard is None:
        raise ElementNotFoundError(f"no ParityRun exists yet for workbook '{workbook_id}'")
    return dashboard


__all__ = ["router"]
