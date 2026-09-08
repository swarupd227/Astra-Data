"""The Mender's own bounded repair loop, over HTTP -- story S8.2.1, continuing F8.2/E8.

Mending is the AC's own persona ("As a parity engineer, I want the Mender to repair
failures"), gated on `ParityEngineerDep` -- the same role that already owns "the
Tolerance Charter and the parity suite" (§2.4), and the one that already triggers
`:run-parity`/`:execute-parity-cases`. Reading the result needed no new route: `GET
/v1/exceptions?mu_ref=...` (S6.2.1) already lists every live `ExceptionCase`, and its
own generic `{"id": case_id, **properties}` view already carries `passes_consumed`
alongside everything else, the identical "reuse the existing generic read" choice
S8.1.1's own classification route already made.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Path, Query, Request

from ..errors import InvalidRequestError
from ..mender import MenderError, MenderService
from .deps import ParityEngineerDep, PrincipalDep

router = APIRouter()

_CASE_ID = Path(min_length=5, max_length=64, description="ULID of the ExceptionCase.")


def _service(request: Request) -> MenderService:
    service: MenderService | None = getattr(request.app.state, "mender", None)
    if service is None:
        raise InvalidRequestError("the Mender is not available on this deployment")
    return service


@router.post(
    "/v1/exceptions/{case_id}:mend",
    tags=["mender"],
    summary="Run the bounded repair loop over one OPEN ExceptionCase, pattern first, then model repair (§11.2)",
)
async def mend(
    request: Request,
    principal: PrincipalDep,
    roles: ParityEngineerDep,
    case_id: str = _CASE_ID,
    workspace: str = Query(default="dev", min_length=1, max_length=100),
) -> dict[str, Any]:
    try:
        return await _service(request).mend(case_id, workspace=workspace, principal=principal)
    except MenderError as exc:
        raise InvalidRequestError(str(exc)) from exc


__all__ = ["router"]
