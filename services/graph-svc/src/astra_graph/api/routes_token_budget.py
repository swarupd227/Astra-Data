"""Token budget management and consumption tracking (S12.2.2)."""

from __future__ import annotations

from typing import Any

import asyncpg
from fastapi import APIRouter, Request
from pydantic import BaseModel, ConfigDict, Field

from ..errors import InvalidRequestError
from ..token_budget import TokenBudgetStore
from .deps import ArtizentDep, PlatformEngineerDep, PrincipalDep, RepositoryDep

router = APIRouter()


def _pool(request: Request) -> asyncpg.Pool:
    pool: asyncpg.Pool | None = getattr(request.app.state, "pool", None)
    if pool is None:  # pragma: no cover - set in every wiring path
        raise InvalidRequestError("graph store is not ready")
    return pool


class SetBudgetRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    tokens_limit: int = Field(gt=0)
    reason: str = Field(default="")


class BudgetStatusResponse(BaseModel):
    tokens_limit: int
    tokens_consumed: int
    cost_usd: float
    percent_used: float
    is_exhausted: bool
    is_warning: bool
    unpriced_tokens: int


@router.post(
    "/v1/token-budget/{workbook_id}:set",
    tags=["token-budget"],
    summary="Set or update the token budget for an MU",
)
async def post_set_budget(
    workbook_id: str,
    body: SetBudgetRequest,
    request: Request,
    principal: PrincipalDep,
    roles: PlatformEngineerDep,
    repository: RepositoryDep,
) -> dict[str, Any]:
    """Set token budget limit for a workbook (MU)."""
    store = TokenBudgetStore(_pool(request), graph_name=repository.graph_name)
    await store.set_budget(workbook_id, body.tokens_limit, body.reason)
    return {"workbook_id": workbook_id, "tokens_limit": body.tokens_limit}


@router.get(
    "/v1/token-budget/{workbook_id}:status",
    tags=["token-budget"],
    summary="Get current token budget status for an MU",
    response_model=BudgetStatusResponse,
)
async def get_budget_status(
    workbook_id: str,
    request: Request,
    principal: PrincipalDep,
    roles: ArtizentDep,
    repository: RepositoryDep,
) -> dict[str, Any]:
    """Query current token consumption and budget status for an MU."""
    store = TokenBudgetStore(_pool(request), graph_name=repository.graph_name)
    status = await store.get_status(workbook_id)
    return {
        "tokens_limit": status.tokens_limit,
        "tokens_consumed": status.tokens_consumed,
        "cost_usd": status.cost_usd,
        "percent_used": status.percent_used,
        "is_exhausted": status.is_exhausted,
        "is_warning": status.is_warning,
        "unpriced_tokens": status.unpriced_tokens,
    }
