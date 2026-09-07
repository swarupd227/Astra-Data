"""Dual execution's own API -- story S7.3.1, opening F7.3.

    "Execution is parallel per MU with a configurable concurrency per Fabric workspace
    (default 8) and per Tableau site (default 4)."

Executing is the Parity Engineer's (§2.4: "Owns the Tolerance Charter and the parity
suite"), the same persona this epic has driven throughout. There is no read route here
beyond what S7.2.1's own `GET /v1/workbooks/{id}/parity-cases` already exposes --
`expected_ref`/`candidate_ref` land on the same `ParityCase` nodes that route already
lists, so execution results are already real and queryable without a second endpoint.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Path, Query, Request

from ..case_execution import CaseExecutionError, CaseExecutionService
from ..errors import InvalidRequestError
from .deps import ParityEngineerDep, PrincipalDep

router = APIRouter()

_WORKBOOK_ID = Path(min_length=5, max_length=64, description="ULID of the Workbook (MU identity).")


def _service(request: Request) -> CaseExecutionService:
    service: CaseExecutionService | None = getattr(request.app.state, "case_execution", None)
    if service is None:
        raise InvalidRequestError("dual execution is not available on this deployment")
    return service


@router.post(
    "/v1/workbooks/{workbook_id}:execute-parity-cases",
    tags=["parity"],
    summary="Execute every live parity case on the source and target sides, and store both ResultSets (§10.2)",
)
async def execute_parity_cases(
    request: Request,
    principal: PrincipalDep,
    roles: ParityEngineerDep,
    workbook_id: str = _WORKBOOK_ID,
    workspace: str = Query(default="dev", min_length=1, max_length=100),
) -> dict[str, Any]:
    try:
        return await _service(request).execute(workbook_id, workspace=workspace, principal=principal)
    except CaseExecutionError as exc:
        raise InvalidRequestError(str(exc)) from exc


__all__ = ["router"]
