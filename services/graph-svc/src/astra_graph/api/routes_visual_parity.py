"""§10.5 Visual parity (advisory), over HTTP -- story S7.6.1, opening F7.6.

Scoring is the Parity Engineer's own action (§2.4), the identical persona F7.3/F7.4/F7.5
have already driven for the data-parity side. Reading the scores is folded into
`GET .../parity-dashboard` (`parity_dashboard.py`'s own extension) rather than a second
read route -- the AC's own "shown on the Parity Dashboard" already names where a report
owner sees this, and the dashboard already reads `Visual`-adjacent facts nowhere else
does. `GET .../visual-captures/{visual_id}` is the AC's own "side-by-side images" --
both stored captures, base64-encoded for direct inline display, so the console needs no
separate binary-serving route. `ParityDashboardReaderDep` gates both reads identically:
any Artizent role, or the report owner specifically.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Path, Request

from ..errors import ElementNotFoundError, InvalidRequestError
from ..visual_parity import VisualParityError, VisualParityService
from .deps import ParityDashboardReaderDep, ParityEngineerDep, PrincipalDep

router = APIRouter()

_WORKBOOK_ID = Path(min_length=5, max_length=64, description="ULID of the Workbook (MU identity).")
_VISUAL_ID = Path(min_length=5, max_length=64, description="ULID of the Visual.")


def _service(request: Request) -> VisualParityService:
    service: VisualParityService | None = getattr(request.app.state, "visual_parity", None)
    if service is None:
        raise InvalidRequestError("visual parity scoring is not available on this deployment")
    return service


@router.post(
    "/v1/workbooks/{workbook_id}:run-visual-parity",
    tags=["parity"],
    summary="Score every composed visual's own structural and image similarity against its source sheet (§10.5)",
)
async def run_visual_parity(
    request: Request, principal: PrincipalDep, roles: ParityEngineerDep, workbook_id: str = _WORKBOOK_ID,
) -> dict[str, Any]:
    try:
        return await _service(request).run(workbook_id, workspace="dev", principal=principal)
    except VisualParityError as exc:
        raise InvalidRequestError(str(exc)) from exc


@router.get(
    "/v1/workbooks/{workbook_id}/visual-captures/{visual_id}",
    tags=["parity"],
    summary="Base64 source screenshot and target render for one scored visual, side by side (§10.5)",
)
async def get_visual_captures_route(
    request: Request,
    principal: PrincipalDep,
    roles: ParityDashboardReaderDep,
    workbook_id: str = _WORKBOOK_ID,
    visual_id: str = _VISUAL_ID,
) -> dict[str, Any]:
    del workbook_id  # part of the URL's own nesting; the visual id alone is the real key
    captures = await _service(request).captures(visual_id)
    if captures is None:
        raise ElementNotFoundError(f"no captured images exist yet for visual '{visual_id}'")
    return captures


__all__ = ["router"]
