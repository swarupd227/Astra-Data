"""§10.3's diff and verdict, over HTTP -- story S7.4.1, closing F7.4.

Diffing is the Parity Engineer's own action (§2.4: "Owns the Tolerance Charter and the
parity suite"), the same persona F7.2/F7.3 have already driven. Reading the result --
`GET .../parity-run` -- is any Artizent role, matching every other read route in this
epic (`GET .../parity-cases`, `GET .../parity-suite`).
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Path, Request

from ..errors import ElementNotFoundError, InvalidRequestError
from ..verdicts import VerdictError, VerdictsService
from .deps import ArtizentDep, ParityEngineerDep, PrincipalDep

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
    request: Request, principal: PrincipalDep, roles: ArtizentDep, workbook_id: str = _WORKBOOK_ID,
) -> dict[str, Any]:
    run = await _service(request).latest(workbook_id)
    if run is None:
        raise ElementNotFoundError(f"no ParityRun exists yet for workbook '{workbook_id}'")
    return run


__all__ = ["router"]
