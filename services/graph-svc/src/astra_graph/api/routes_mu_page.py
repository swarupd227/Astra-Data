"""The Migration Unit page's API — story S10.3.1, opening F10.3. See `mu_page.py`'s own
module docstring for how each section is assembled and why the client response is a
slice of one shared read rather than a second, separately-computed document.

Both routes read `repository.graph_name` via the injected `RepositoryDep`, never
freshly re-read global `settings()` — the ADR 0072 bug-fix precedent, applied here
proactively from the first draft, the same way S10.2.1's own new routes already did.
"""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Path, Query, Request

from ..errors import ForbiddenError, InvalidRequestError
from ..mu_page import mu_page, mu_page_client_view, mu_provenance
from .deps import MuPageReaderDep, PrincipalDep, RepositoryDep

router = APIRouter()

_WORKBOOK_ID = Path(min_length=26, max_length=26, description="ULID of the workbook.")


def _stores(request: Request) -> tuple[Any, Any, Any, Any]:
    state = request.app.state
    pool = getattr(state, "pool", None)
    artefact_store = getattr(state, "artefact_store", None)
    report_deploy_store = getattr(state, "report_deploy_store", None)
    scope_store = getattr(state, "scope_store", None)
    if any(dep is None for dep in (pool, artefact_store, report_deploy_store, scope_store)):
        raise InvalidRequestError("the Migration Unit page is not available on this deployment")
    return pool, artefact_store, report_deploy_store, scope_store


@router.get(
    "/v1/mu/{workbook_id}",
    tags=["mu-page"],
    summary="The Migration Unit page — everything about one report, at one URL",
)
async def get_mu_page(
    request: Request,
    principal: PrincipalDep,
    roles: MuPageReaderDep,
    repository: RepositoryDep,
    workbook_id: Annotated[str, _WORKBOOK_ID],
) -> dict[str, Any]:
    """The full Artizent page, or the client-narrowed slice (§15.4's own literal
    "Source, Artefacts (thumbnails and documentation only), Parity (summary and verdict
    grid), Gates and Timeline") — decided by role, not by a query parameter a caller
    could get wrong."""
    pool, artefact_store, report_deploy_store, scope_store = _stores(request)
    full = await mu_page(
        pool, repository.graph_name,
        artefact_store=artefact_store, report_deploy_store=report_deploy_store, scope_store=scope_store,
        workbook_id=workbook_id,
    )
    if roles.is_artizent():
        return full
    return mu_page_client_view(full)


@router.get(
    "/v1/mu/{workbook_id}/provenance",
    tags=["mu-page"],
    summary="Every artefact's provenance record this MU touches, filterable by mode",
)
async def get_mu_provenance(
    request: Request,
    principal: PrincipalDep,
    roles: MuPageReaderDep,
    repository: RepositoryDep,
    workbook_id: Annotated[str, _WORKBOOK_ID],
    mode: Annotated[str | None, Query(max_length=32)] = None,
) -> dict[str, Any]:
    """Artizent-only in effect: a client reader's own `is_artizent()` check below refuses
    the call outright, since §15.4 excludes Provenance from "client roles see..." — a
    real gate, not a narrower response, given how fan-out-heavy this read is (see
    `mu_page.py`'s own module docstring). Deliberately does not call `get_mu_page`'s own
    `mu_page()` assembly first — `mu_provenance` collects only the subject ids it needs
    on its own, so this call never pays for Parity/Exceptions/Gates/Timeline's own real
    queries just to reach a handful of provenance ids."""
    if not roles.is_artizent():
        raise ForbiddenError(
            "Provenance is an Artizent-only section of the Migration Unit page"
        )
    pool, artefact_store, _report_deploy_store, _scope_store = _stores(request)
    provenance_store = request.app.state.provenance_store
    records = await mu_provenance(
        pool, repository.graph_name, artefact_store, provenance_store,
        workbook_id=workbook_id, mode=mode,
    )
    return {"workbook_id": workbook_id, "records": records}


__all__ = ["router"]
