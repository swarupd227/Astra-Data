"""§11.1 failure classification, over HTTP -- story S8.1.1, opening F8.1 and E8.

Classifying is the AC's own persona ("As a migration engineer, I want every failing
case classified"), gated on `MigrationEngineerDep`. Reading the resulting
`ExceptionCase`s needed no new route -- `GET /v1/exceptions?mu_ref=...` (S6.2.1) already
lists every live one, filterable by workbook and state, and a classified case is simply
a new, disclosed use of that same node (see `classification.py`'s own docstring).
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Path, Request

from ..classification import ClassificationError, ClassificationService
from ..errors import InvalidRequestError
from .deps import MigrationEngineerDep, PrincipalDep

router = APIRouter()

_WORKBOOK_ID = Path(min_length=5, max_length=64, description="ULID of the Workbook (MU identity).")


def _service(request: Request) -> ClassificationService:
    service: ClassificationService | None = getattr(request.app.state, "classification", None)
    if service is None:
        raise InvalidRequestError("failure classification is not available on this deployment")
    return service


@router.post(
    "/v1/workbooks/{workbook_id}:classify-failures",
    tags=["classification"],
    summary="Classify every FAIL verdict on this workbook's own most recent parity run into the §11.1 taxonomy",
)
async def classify_failures(
    request: Request,
    principal: PrincipalDep,
    roles: MigrationEngineerDep,
    workbook_id: str = _WORKBOOK_ID,
) -> dict[str, Any]:
    try:
        return await _service(request).classify(workbook_id, principal=principal)
    except ClassificationError as exc:
        raise InvalidRequestError(str(exc)) from exc


__all__ = ["router"]
