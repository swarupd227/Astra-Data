"""The Programme Board's KPI strip, train swimlanes and milestone rail — story S10.2.1,
opening F10.2. See `programme_surface.py`'s own module docstring for what each figure
reads and which readings are disclosed.

Read-only, and gated the identical `ArtizentDep` every other Programme Board pane's own
route already uses (family count, train projections, G2 reviews, class mix, rule
coverage, exception ageing, accepted units by tier) — this story adds three more panes
to the same screen, not a new access posture.

``graph_name`` comes from the injected `RepositoryDep`, not a freshly re-read global
`settings()` — story S10.1.2 found and fixed exactly this bug once already (`routes_
rebuild.py`'s own docstring): a route that re-derives "the configured graph" from
global config instead of asking the object it was actually given drifts the moment a
caller wires a different one (any test fixture, or a future multi-tenant deployment).
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Request

from ..errors import InvalidRequestError
from ..programme_surface import kpi_strip, milestone_rail, train_swimlanes
from .deps import ArtizentDep, PrincipalDep, RepositoryDep

router = APIRouter()


def _services(request: Request) -> tuple[Any, ...]:
    state = request.app.state
    pool = getattr(state, "pool", None)
    question_store = getattr(state, "question_store", None)
    unit_price_store = getattr(state, "unit_price_store", None)
    adoption_store = getattr(state, "adoption_store", None)
    adoption_config_store = getattr(state, "adoption_config_store", None)
    if any(dep is None for dep in (pool, question_store, unit_price_store, adoption_store, adoption_config_store)):
        raise InvalidRequestError("the Programme Board surface is not available on this deployment")
    return pool, question_store, unit_price_store, adoption_store, adoption_config_store


@router.get(
    "/v1/programme:kpis",
    tags=["programme"],
    summary="The Programme Board's KPI strip (§15.3.1)",
)
async def get_kpi_strip(
    request: Request, principal: PrincipalDep, roles: ArtizentDep, repository: RepositoryDep
) -> dict[str, Any]:
    pool, question_store, unit_price_store, adoption_store, adoption_config_store = _services(request)
    return await kpi_strip(
        pool, repository.graph_name,
        question_store=question_store, unit_price_store=unit_price_store,
        adoption_store=adoption_store, adoption_config_store=adoption_config_store,
    )


@router.get(
    "/v1/programme:swimlanes",
    tags=["programme"],
    summary="Train swimlanes: planned vs projected, MU counts by state, blocked reasons (§15.3.1)",
)
async def get_train_swimlanes(
    request: Request, principal: PrincipalDep, roles: ArtizentDep, repository: RepositoryDep
) -> dict[str, Any]:
    pool, *_rest = _services(request)
    return await train_swimlanes(pool, repository.graph_name)


@router.get(
    "/v1/programme:milestones",
    tags=["programme"],
    summary="The milestone rail and gate calendar (§15.3.1)",
)
async def get_milestone_rail(
    request: Request, principal: PrincipalDep, roles: ArtizentDep, repository: RepositoryDep
) -> dict[str, Any]:
    pool, *_rest = _services(request)
    return await milestone_rail(pool, repository.graph_name)
