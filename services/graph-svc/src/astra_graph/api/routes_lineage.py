"""The Lineage View's API.

Specification §15.3.2 and story S1.4.2: workbooks, the tables and fields behind them, and
how much any two share — for a family or a selection, so a model engineer can see why the
Cartographer grouped a family and challenge it.

One endpoint, because the screen is one graph. The node-type filter, the strength threshold
and the colouring are applied in the console: they change what is *drawn*, not what is read,
and re-reading the estate to hide a node type would make an instant interaction a round
trip.
"""

from __future__ import annotations

import logging
from typing import Annotated, Any

from fastapi import APIRouter, Query, Request

from ..errors import InvalidRequestError
from ..lineage import DEFAULT_MIN_STRENGTH, MAX_WORKBOOKS, LineageReader
from .deps import ArtizentDep, PrincipalDep

logger = logging.getLogger(__name__)

router = APIRouter()


def _reader(request: Request) -> LineageReader:
    reader: LineageReader | None = getattr(request.app.state, "lineage_reader", None)
    if reader is None:  # pragma: no cover - set in every wiring path
        raise InvalidRequestError("the lineage reader is not available on this deployment")
    return reader


@router.get(
    "/v1/lineage",
    tags=["estate"],
    summary="Workbooks, the tables and fields behind them, and their shared lineage",
)
async def lineage(
    request: Request,
    principal: PrincipalDep,
    roles: ArtizentDep,
    site: str | None = None,
    project: str | None = None,
    family: str | None = None,
    workbooks: Annotated[
        str | None,
        Query(description="Comma-separated workbook ids, for an explicit selection."),
    ] = None,
    min_strength: Annotated[float, Query(ge=0.0, le=1.0)] = DEFAULT_MIN_STRENGTH,
    limit: Annotated[int, Query(ge=1, le=MAX_WORKBOOKS)] = MAX_WORKBOOKS,
) -> dict[str, Any]:
    """The lineage graph for one scope.

    The scope is required in spirit rather than by validation: §15.3.2 says "for a family
    or a selection", and an unscoped call over a large estate returns a hairball. It is
    allowed, bounded and reported as truncated, because refusing it would mean a model
    engineer who has not yet chosen a scope sees an error instead of a starting point.
    """
    selection = (
        [part.strip() for part in workbooks.split(",") if part.strip()] if workbooks else None
    )
    graph = await _reader(request).read(
        site=site,
        project=project,
        family=family,
        workbook_ids=selection,
        min_strength=min_strength,
        limit=limit,
    )
    logger.info(
        "lineage read by %s: %s workbooks, %s links (%s)",
        principal.value,
        graph.workbook_count,
        len(graph.shared),
        graph.origin,
    )
    return {
        "scope": {
            "site": site,
            "project": project,
            "family": family,
            "workbooks": selection,
            "min_strength": min_strength,
            "limit": limit,
        },
        **graph.as_dict(),
    }


__all__ = ["router"]
