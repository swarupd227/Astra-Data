"""Throughput and cost metrics -- story S6.2.3. See `throughput_report.py`'s own module
docstring for why "generated weekly" is a `POST`-triggered snapshot, the same reading
`status_pack.py` already established, and `throughput_metrics.py`'s own module
docstring for the three vocabulary translations (custodian = Site, credits = real LLM
token cost, agent acceptance = the existing MU-acceptance fact) confirmed by the user
before any code was written.

Generate is the Programme Manager's own action (this story's own literal "As a project
manager") -- hidden, not disabled, for anyone else, the identical hide-not-disable
convention `routes_status_pack.py` already uses. Reading the report (including its own
CSV export) is open to any Artizent role, matching `status_pack`'s own reader gate.
"""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Query, Request, Response

from ..errors import InvalidRequestError
from ..throughput_report import (
    DEFAULT_DAYS,
    DEFAULT_WEEKS,
    generate_report,
    latest_report,
    render_csv,
)
from .deps import ArtizentDep, PrincipalDep, ProgrammeManagerDep, RepositoryDep

router = APIRouter()


@router.post(
    "/v1/throughput-report:generate",
    tags=["throughput-report"],
    summary="Generate this report from the real, live gateway request log and commercial ledger",
)
async def post_generate_throughput_report(
    request: Request,
    principal: PrincipalDep,
    roles: ProgrammeManagerDep,
    repository: RepositoryDep,
    weeks: Annotated[int, Query(ge=1, le=52)] = DEFAULT_WEEKS,
    days: Annotated[int, Query(ge=1, le=366)] = DEFAULT_DAYS,
) -> dict[str, Any]:
    pool = request.app.state.pool
    report = await generate_report(
        pool, repository.graph_name, generated_by=principal.value, weeks=weeks, days=days,
    )
    return report.as_dict()


@router.get(
    "/v1/throughput-report",
    tags=["throughput-report"],
    summary="The latest throughput and cost report",
)
async def get_throughput_report(
    request: Request, principal: PrincipalDep, roles: ArtizentDep, repository: RepositoryDep,
) -> dict[str, Any]:
    pool = request.app.state.pool
    report = await latest_report(pool, repository.graph_name)
    if report is None:
        raise InvalidRequestError("no throughput report has been generated yet")
    return report.as_dict()


@router.get(
    "/v1/throughput-report.csv",
    tags=["throughput-report"],
    summary="The latest throughput and cost report as CSV, for the client cadence",
)
async def get_throughput_report_csv(
    request: Request, principal: PrincipalDep, roles: ArtizentDep, repository: RepositoryDep,
) -> Response:
    pool = request.app.state.pool
    report = await latest_report(pool, repository.graph_name)
    if report is None:
        raise InvalidRequestError("no throughput report has been generated yet")
    return Response(
        content=render_csv(report),
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=throughput-report.csv"},
    )
