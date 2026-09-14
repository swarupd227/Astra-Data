"""The Decision Register's API -- story S10.4.2. See `decision_register.py`'s own module
docstring for how a row's subject is resolved, how the evidence bundle stays honest about
what it can and cannot resolve, and why "signed PDF" is a rendered attestation rather than
a persisted baseline.

Four routes, all reads: the filtered list, one row's evidence bundle, and two export
shapes (CSV, PDF) over the identical filtered set the list route itself would return --
"export what you are looking at," not a separate, unfiltered dump.
"""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Path, Query, Request, Response

from ..decision_register import (
    decisions_to_csv,
    evidence_bundle,
    list_decisions,
    render_decision_register_pdf,
)
from ..errors import ElementNotFoundError, InvalidRequestError
from .deps import DecisionRegisterReaderDep, PrincipalDep, RepositoryDep

router = APIRouter()

_DECISION_ID = Path(min_length=5, max_length=64, description="ULID of the GateDecision.")


def _artefact_store(request: Request) -> Any:
    store = getattr(request.app.state, "artefact_store", None)
    if store is None:  # pragma: no cover - set in every wiring path
        raise InvalidRequestError("the artefact store is not available on this deployment")
    return store


async def _filtered(
    request: Request,
    repository: RepositoryDep,
    gate: str | None,
    decision: str | None,
    approver: str | None,
    q: str | None,
) -> list[dict[str, Any]]:
    pool = request.app.state.pool
    return await list_decisions(
        pool, repository.graph_name, gate=gate, decision=decision, approver=approver, q=q,
    )


@router.get(
    "/v1/decisions",
    tags=["decision-register"],
    summary="Every GateDecision and adjudication, newest first; search and filter (§15.3.6)",
)
async def get_decisions(
    request: Request,
    principal: PrincipalDep,
    roles: DecisionRegisterReaderDep,
    repository: RepositoryDep,
    gate: Annotated[str | None, Query(max_length=8)] = None,
    decision: Annotated[str | None, Query(max_length=32)] = None,
    approver: Annotated[str | None, Query(max_length=200)] = None,
    q: Annotated[str | None, Query(max_length=200, alias="q")] = None,
) -> dict[str, Any]:
    rows = await _filtered(request, repository, gate, decision, approver, q)
    return {"items": rows, "count": len(rows)}


@router.get(
    "/v1/decisions/{gate_decision_id}/evidence",
    tags=["decision-register"],
    summary="A decision's own evidence bundle -- the record plus whatever it resolves to",
)
async def get_decision_evidence(
    request: Request,
    principal: PrincipalDep,
    roles: DecisionRegisterReaderDep,
    repository: RepositoryDep,
    gate_decision_id: Annotated[str, _DECISION_ID],
) -> dict[str, Any]:
    pool = request.app.state.pool
    bundle = await evidence_bundle(pool, repository.graph_name, _artefact_store(request), gate_decision_id)
    if bundle is None:
        raise ElementNotFoundError(f"no GateDecision '{gate_decision_id}'")
    return bundle


@router.get(
    "/v1/decisions.csv",
    tags=["decision-register"],
    summary="The filtered register as CSV",
)
async def get_decisions_csv(
    request: Request,
    principal: PrincipalDep,
    roles: DecisionRegisterReaderDep,
    repository: RepositoryDep,
    gate: Annotated[str | None, Query(max_length=8)] = None,
    decision: Annotated[str | None, Query(max_length=32)] = None,
    approver: Annotated[str | None, Query(max_length=200)] = None,
    q: Annotated[str | None, Query(max_length=200, alias="q")] = None,
) -> Response:
    rows = await _filtered(request, repository, gate, decision, approver, q)
    return Response(
        content=decisions_to_csv(rows),
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=decision-register.csv"},
    )


@router.get(
    "/v1/decisions.pdf",
    tags=["decision-register"],
    summary="The filtered register as a signed PDF",
)
async def get_decisions_pdf(
    request: Request,
    principal: PrincipalDep,
    roles: DecisionRegisterReaderDep,
    repository: RepositoryDep,
    gate: Annotated[str | None, Query(max_length=8)] = None,
    decision: Annotated[str | None, Query(max_length=32)] = None,
    approver: Annotated[str | None, Query(max_length=200)] = None,
    q: Annotated[str | None, Query(max_length=200, alias="q")] = None,
) -> Response:
    rows = await _filtered(request, repository, gate, decision, approver, q)
    pdf_bytes = render_decision_register_pdf(rows, signed_by=principal.value)
    return Response(
        content=pdf_bytes,
        media_type="application/pdf",
        headers={"Content-Disposition": "inline; filename=decision-register.pdf"},
    )


__all__ = ["router"]
