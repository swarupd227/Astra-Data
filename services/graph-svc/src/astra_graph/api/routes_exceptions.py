"""Redesign flags as work items -- story S6.2.1.

    "Redesign flags create ExceptionCases of class VISUAL_REDESIGN routed to the Exception
    Desk with the source screenshot, the mapping reason and the placeholder location."
    "An MU with open redesign flags cannot enter PROVING for the affected sheets; other
    sheets proceed."
    "Closing the flag records the engineer, the Desktop commit hash and the date."

Reading the queue (what `visual_redesign.py`'s own module docstring calls "the Exception
Desk" -- a real node, not yet a real screen) is open to any Artizent role, the same posture
every other "read what a prior action produced" route in this API has; closing a case is
the migration engineer's (`MigrationEngineerDep`, the persona this story's own acceptance
criteria names). The proving-readiness check has no consumer yet (E7's Arbiter does not
exist) but is real and callable today, the same "build the real check even with nothing to
call it yet" posture this codebase has taken since S5.3.3's own calibration report.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Path, Query, Request
from pydantic import BaseModel, ConfigDict, Field

from ..compositor import Compositor
from ..errors import ElementNotFoundError, InvalidRequestError
from ..graph.queries import NODE_INDEX_TABLE
from ..lineage import hydrate
from ..visual_redesign import (
    RedesignExceptionError,
    can_enter_proving,
    close_redesign_exception,
)
from .deps import ArtizentDep, MigrationEngineerDep, PrincipalDep

router = APIRouter()

_CASE_ID = Path(min_length=5, max_length=64, description="ULID of the ExceptionCase.")
_WORKBOOK_ID = Path(min_length=5, max_length=64, description="ULID of the Workbook.")


def _compositor(request: Request) -> Compositor:
    compositor: Compositor | None = getattr(request.app.state, "compositor", None)
    if compositor is None:
        raise InvalidRequestError("exceptions are not available on this deployment")
    return compositor


def _case_view(case_id: str, properties: dict[str, Any]) -> dict[str, Any]:
    return {"id": case_id, **properties}


@router.get(
    "/v1/exceptions",
    tags=["exceptions"],
    summary="Every live ExceptionCase -- the Exception Desk's own queue, until it has a screen (§11.3)",
)
async def list_exceptions(
    request: Request,
    principal: PrincipalDep,
    roles: ArtizentDep,
    state: str | None = Query(default=None, description="Filter to this state, e.g. 'OPEN'."),
    mu_ref: str | None = Query(default=None, description="Filter to this Migration Unit (workbook id)."),
) -> dict[str, Any]:
    engine = _compositor(request)
    async with engine.pool.acquire() as conn:
        rows = await conn.fetch(
            f"""SELECT id FROM {NODE_INDEX_TABLE}
             WHERE graph = $1 AND kind = 'node' AND label = 'ExceptionCase' AND retired_at IS NULL""",
            engine.graph_name,
        )
        cases = await hydrate(conn, engine.graph_name, "ExceptionCase", [row["id"] for row in rows])

    views = [
        _case_view(case_id, properties)
        for case_id, properties in cases.items()
        if (state is None or properties.get("state") == state)
        and (mu_ref is None or properties.get("mu_ref") == mu_ref)
    ]
    return {"exceptions": views, "count": len(views)}


class CloseExceptionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    desktop_commit_hash: str = Field(min_length=1, max_length=200)


@router.post(
    "/v1/exceptions/{case_id}:close",
    tags=["exceptions"],
    summary="Close a redesign exception -- records the engineer, the Desktop commit and the date (§11.3)",
)
async def close_exception(
    body: CloseExceptionRequest,
    request: Request,
    principal: PrincipalDep,
    roles: MigrationEngineerDep,
    case_id: str = _CASE_ID,
) -> dict[str, Any]:
    engine = _compositor(request)
    try:
        properties = await close_redesign_exception(
            engine.pool, engine.graph_name, engine.writer,
            case_id=case_id, desktop_commit_hash=body.desktop_commit_hash, principal=principal,
        )
    except (ElementNotFoundError, RedesignExceptionError) as exc:
        raise InvalidRequestError(str(exc)) from exc
    return _case_view(case_id, properties)


@router.get(
    "/v1/workbooks/{workbook_id}:proving-readiness",
    tags=["exceptions"],
    summary="Which of a workbook's own worksheets may enter PROVING today (§3.2)",
)
async def get_proving_readiness(
    request: Request, principal: PrincipalDep, roles: ArtizentDep, workbook_id: str = _WORKBOOK_ID
) -> dict[str, Any]:
    engine = _compositor(request)
    readiness = await can_enter_proving(engine.pool, engine.graph_name, workbook_id)
    return readiness.as_dict()


__all__ = ["router"]
