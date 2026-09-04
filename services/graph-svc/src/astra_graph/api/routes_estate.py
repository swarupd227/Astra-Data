"""The Estate Explorer's API.

Specification §15.3.2, story S1.4.1. Three panes and four actions, over a 1,067-workbook
site in under two seconds.

**One request, three panes.** ``GET /v1/estate`` returns the tree, the filtered page and
the facet counts together. The screen needs all three at once and they are all derived from
one read of the estate; splitting them into three endpoints would triple the work to
produce the same screen, and the facet counts would be computed against a set the other two
calls might no longer agree with.

**The actions are gated at the API, not in the console.** S1.4.1 puts re-tier and withdraw
behind the Programme Manager. A console that only hides the button is not a permission
model, so the role is checked here and the reason is required here.
"""

from __future__ import annotations

import logging
import time
from typing import Annotated, Any

from fastapi import APIRouter, Path, Query, Request, status
from pydantic import BaseModel, ConfigDict, Field

from ..errors import ElementNotFoundError, InvalidRequestError
from ..estate import PENDING_COLUMNS, EstateFilter, EstateReader
from ..scope import (
    TIERS,
    DecisionKind,
    ScopeError,
    ScopeStore,
    new_decision,
)
from .deps import ArtizentDep, PrincipalDep, ProgrammeManagerDep

logger = logging.getLogger(__name__)

router = APIRouter()

_NODE_ID = Path(min_length=26, max_length=26, description="ULID of the workbook.")

#: How much of the centre pane one request returns. The table is virtualised in the
#: console, but a page bounds the response rather than the screen.
DEFAULT_PAGE = 100
MAX_PAGE = 500

#: Depth of the right pane's lineage mini-graph. Two hops reaches a workbook's sheets and
#: the datasources behind them, which is the shape §15.3.2 asks for; three pulls in every
#: field of every datasource and stops being a mini-graph.
LINEAGE_DEPTH = 2


class ScopeDecisionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    reason: str = Field(
        min_length=10,
        max_length=2_000,
        description="Why. Required, and kept: this record outlives everyone who remembers "
        "the conversation (spec §15.2).",
    )


class ReTierRequest(ScopeDecisionRequest):
    tier: str = Field(description=f"One of {', '.join(TIERS)}.")


def _reader(request: Request) -> EstateReader:
    reader: EstateReader | None = getattr(request.app.state, "estate_reader", None)
    if reader is None:  # pragma: no cover - set in every wiring path
        raise InvalidRequestError("the estate reader is not available on this deployment")
    return reader


def _scope(request: Request) -> ScopeStore:
    store: ScopeStore | None = getattr(request.app.state, "scope_store", None)
    if store is None:  # pragma: no cover - set in every wiring path
        raise InvalidRequestError("scope decisions are not available on this deployment")
    return store


@router.get(
    "/v1/estate",
    tags=["estate"],
    summary="The Estate Explorer: tree, filtered workbooks and facet counts",
)
async def estate(
    request: Request,
    principal: PrincipalDep,
    roles: ArtizentDep,
    site: str | None = None,
    project: str | None = None,
    owner: str | None = None,
    tier: str | None = None,
    parse_quality_band: str | None = None,
    usage_band: str | None = None,
    held_only: bool = False,
    unowned_only: bool = False,
    include_withdrawn: bool = False,
    search: str | None = None,
    sort: str = "name",
    offset: int = 0,
    limit: int = DEFAULT_PAGE,
) -> dict[str, Any]:
    """Everything the Explorer renders, from one read.

    ``timing`` is on the response because S1.4.1 sets a two-second budget for the screen
    and a budget nobody can see is a budget nobody keeps.
    """
    if not 1 <= limit <= MAX_PAGE:
        raise InvalidRequestError(f"limit must be between 1 and {MAX_PAGE}, got {limit}")
    if offset < 0:
        raise InvalidRequestError(f"offset cannot be negative, got {offset}")

    started = time.perf_counter()
    scope_states = await _scope(request).states()
    estate_data = await _reader(request).read(scope=scope_states)

    where = EstateFilter(
        site=site,
        project=project,
        owner=owner,
        tier=tier,
        parse_quality_band=parse_quality_band,
        usage_band=usage_band,
        held_only=held_only,
        unowned_only=unowned_only,
        include_withdrawn=include_withdrawn,
        search=search,
    )
    page = estate_data.page(where, offset=offset, limit=limit, sort=sort)
    facets = estate_data.facets(where)
    elapsed = (time.perf_counter() - started) * 1000

    return {
        "tree": [node.as_dict() for node in estate_data.tree(where)],
        **page,
        "facets": facets,
        "tiers": list(TIERS),
        "pending_columns": [
            {"column": name, "reason": reason}
            for name, reason in sorted(PENDING_COLUMNS.items())
        ],
        "timing": {
            "total_ms": round(elapsed, 2),
            "estate_read_ms": estate_data.read_ms,
        },
    }


@router.get(
    "/v1/estate/workbooks/{workbook_id}",
    tags=["estate"],
    summary="One workbook: summary, scope history and lineage mini-graph",
)
async def workbook(
    request: Request,
    principal: PrincipalDep,
    roles: ArtizentDep,
    workbook_id: Annotated[str, _NODE_ID],
    depth: Annotated[int, Query(ge=1, le=3)] = LINEAGE_DEPTH,
) -> dict[str, Any]:
    """The right pane.

    The lineage is a neighbourhood, not a separate projection: §15.3.2 wants "a lineage
    mini-graph", and the graph already answers that question in one traversal.
    """
    repository = request.app.state.repository
    record = await repository.get_node_record(workbook_id)
    if record is None or record.label != "Workbook":
        raise ElementNotFoundError(f"no workbook with id '{workbook_id}'")

    neighbourhood = await repository.neighbourhood(workbook_id, depth=depth)
    history = await _scope(request).history(workbook_id)

    return {
        "workbook": {"id": record.id, "type": record.label, "properties": record.properties},
        "scope": {
            "decisions": [decision.as_dict() for decision in history],
            "current": _fold(history).as_dict(),
        },
        "lineage": {
            "depth": depth,
            "nodes": [
                {
                    "id": neighbour.node.id,
                    "type": neighbour.node.label,
                    "name": neighbour.node.properties.get("name")
                    or neighbour.node.properties.get("luid"),
                    "depth": neighbour.depth,
                }
                for neighbour in neighbourhood.neighbours
            ],
            "edges": [
                {"type": edge.label, "from": edge.from_id, "to": edge.to_id}
                for edge in neighbourhood.edges
            ],
            "truncated": neighbourhood.truncated,
        },
        # The Migration Unit page (§15.4) is where "open MU" goes. Reported as absent with
        # the reason rather than omitted, so the console can render the action disabled and
        # say why instead of leaving a dead button or no button at all.
        "migration_unit": None,
        "migration_unit_reason": (
            "No Migration Unit exists for this workbook. The Cartographer creates MUs when "
            "it clusters the estate (E3/F3.2); until then a harvested workbook has a parse "
            "and nothing downstream of it."
        ),
    }


@router.post(
    "/v1/estate/workbooks/{workbook_id}:re-tier",
    status_code=status.HTTP_201_CREATED,
    tags=["estate"],
    summary="Set a workbook's complexity tier, with a reason (Programme Manager)",
)
async def re_tier(
    body: ReTierRequest,
    request: Request,
    principal: PrincipalDep,
    roles: ProgrammeManagerDep,
    workbook_id: Annotated[str, _NODE_ID],
) -> dict[str, Any]:
    """Record a tier decision.

    Called "re-tier" by §15.3.2 because assessment normally proposes one first. Nothing
    assesses yet (E3/F3.1), so the first decision on a workbook records ``from: null`` — a
    declaration rather than a revision, and the record says which it was.
    """
    await _require_workbook(request, workbook_id)
    history = await _scope(request).history(workbook_id)
    try:
        decision = new_decision(
            workbook_id=workbook_id,
            kind=DecisionKind.RE_TIER,
            reason=body.reason,
            decided_by=principal.value,
            from_value=_fold(history).tier,
            to_value=body.tier,
        )
    except ScopeError as exc:
        raise InvalidRequestError(str(exc)) from exc

    stored = await _scope(request).decide(decision)
    logger.info(
        "scope: %s tiered workbook %s as %s", principal.value, workbook_id, body.tier
    )
    return stored.as_dict()


@router.post(
    "/v1/estate/workbooks/{workbook_id}:withdraw",
    status_code=status.HTTP_201_CREATED,
    tags=["estate"],
    summary="Withdraw a workbook from scope, with a reason (Programme Manager)",
)
async def withdraw(
    body: ScopeDecisionRequest,
    request: Request,
    principal: PrincipalDep,
    roles: ProgrammeManagerDep,
    workbook_id: Annotated[str, _NODE_ID],
) -> dict[str, Any]:
    """Take a workbook out of scope.

    It is not retired and not deleted: the harvest found it, and the estate should keep
    saying so. Withdrawal is a decision about the *programme*, which is why it is a scope
    record rather than a change to the graph.
    """
    await _require_workbook(request, workbook_id)
    try:
        decision = new_decision(
            workbook_id=workbook_id,
            kind=DecisionKind.WITHDRAW,
            reason=body.reason,
            decided_by=principal.value,
        )
    except ScopeError as exc:
        raise InvalidRequestError(str(exc)) from exc

    stored = await _scope(request).decide(decision)
    logger.info("scope: %s withdrew workbook %s", principal.value, workbook_id)
    return stored.as_dict()


@router.post(
    "/v1/estate/workbooks/{workbook_id}:reinstate",
    status_code=status.HTTP_201_CREATED,
    tags=["estate"],
    summary="Return a withdrawn workbook to scope, with a reason (Programme Manager)",
)
async def reinstate(
    body: ScopeDecisionRequest,
    request: Request,
    principal: PrincipalDep,
    roles: ProgrammeManagerDep,
    workbook_id: Annotated[str, _NODE_ID],
) -> dict[str, Any]:
    """The way back. A withdrawal that cannot be undone is a deletion with extra steps."""
    await _require_workbook(request, workbook_id)
    try:
        decision = new_decision(
            workbook_id=workbook_id,
            kind=DecisionKind.REINSTATE,
            reason=body.reason,
            decided_by=principal.value,
        )
    except ScopeError as exc:
        raise InvalidRequestError(str(exc)) from exc

    stored = await _scope(request).decide(decision)
    logger.info("scope: %s reinstated workbook %s", principal.value, workbook_id)
    return stored.as_dict()


async def _require_workbook(request: Request, workbook_id: str) -> None:
    record = await request.app.state.repository.get_node_record(workbook_id)
    if record is None or record.label != "Workbook":
        raise ElementNotFoundError(f"no workbook with id '{workbook_id}'")


def _fold(history: Any) -> Any:
    from ..scope import fold

    return fold(list(history))


__all__ = ["router"]
