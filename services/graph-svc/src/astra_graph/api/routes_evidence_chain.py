"""The Evidence Chain's own API -- story S11.3.1, opens F11.3.

    "Verification tool recomputes the chain and reports the first break; runs nightly
    and on demand."

The nightly cadence is `.github/workflows/nightly.yml`'s own cron, the identical
"external cron, not an in-process scheduler" shape `tools/verify_replay.py` already has.
These two routes are the AC's own "on demand" -- advancing/verifying a chain by hand,
from the console or a script, without waiting for the next nightly run.

Read (status, daily roots) is the same "Artizent, or the InfoSec reviewer" shape
`TenantAccessReaderDep` already sets -- an auditor is not a role this codebase has (see
`decision_register.py`'s own identical finding); triggering advance/verify is
`PlatformEngineerDep`, a real platform action with no other named approver, matching
S11.1.2's SVID-revoke and S11.2.1's execution-safety-policy-edit precedent.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Annotated, Any

from fastapi import APIRouter, Query, Request

from ..errors import InvalidRequestError
from ..evidence_chain import (
    advance_chain,
    chain_status,
    compute_daily_root,
    list_daily_roots,
    verify_chain,
)
from .deps import PlatformEngineerDep, PrincipalDep, TenantAccessReaderDep

router = APIRouter()


def _pool_and_graph(request: Request) -> tuple[Any, str]:
    engine = getattr(request.app.state, "cartographer", None)
    if engine is None:
        raise InvalidRequestError("the evidence chain is not available on this deployment")
    return engine.pool, engine.graph_name


@router.get(
    "/v1/evidence-chain/status",
    tags=["evidence-chain"],
    summary="The chain's own tip and entry counts by category",
)
async def get_chain_status(
    request: Request, principal: PrincipalDep, roles: TenantAccessReaderDep
) -> dict[str, Any]:
    pool, graph_name = _pool_and_graph(request)
    return await chain_status(pool, graph_name)


@router.post(
    "/v1/evidence-chain:advance",
    tags=["evidence-chain"],
    summary="Hash-link every not-yet-chained state transition, gate decision, agent run, model call and verdict",
)
async def advance(
    request: Request, principal: PrincipalDep, roles: PlatformEngineerDep
) -> dict[str, Any]:
    pool, graph_name = _pool_and_graph(request)
    result = await advance_chain(pool, graph_name)
    return result.as_dict()


@router.post(
    "/v1/evidence-chain:verify",
    tags=["evidence-chain"],
    summary="Recompute the chain and report the first break, if any",
)
async def verify(
    request: Request, principal: PrincipalDep, roles: PlatformEngineerDep
) -> dict[str, Any]:
    pool, graph_name = _pool_and_graph(request)
    result = await verify_chain(pool, graph_name)
    return result.as_dict()


@router.get(
    "/v1/evidence-chain/daily-roots",
    tags=["evidence-chain"],
    summary="Daily roots, most recent first",
)
async def get_daily_roots(
    request: Request,
    principal: PrincipalDep,
    roles: TenantAccessReaderDep,
    limit: Annotated[int, Query(ge=1, le=500)] = 100,
) -> dict[str, Any]:
    pool, graph_name = _pool_and_graph(request)
    roots = await list_daily_roots(pool, graph_name, limit=limit)
    return {"daily_roots": [root.as_dict() for root in roots]}


@router.post(
    "/v1/evidence-chain:compute-daily-root",
    tags=["evidence-chain"],
    summary="Compute yesterday's (UTC) daily root, if not already computed",
)
async def compute_yesterdays_root(
    request: Request, principal: PrincipalDep, roles: PlatformEngineerDep
) -> dict[str, Any]:
    pool, graph_name = _pool_and_graph(request)
    yesterday = (datetime.now(UTC) - timedelta(days=1)).date()
    root = await compute_daily_root(pool, graph_name, yesterday)
    if root is None:
        return {"computed": False, "day": yesterday.isoformat()}
    return {"computed": True, **root.as_dict()}


__all__ = ["router"]
