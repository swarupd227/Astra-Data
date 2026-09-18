"""Token and cost budgets per MU with consumption tracking and alerts (S12.2.2).

Budgets are configured per MU, consumption tracked daily from gateway_request_log.
Soft alert at 80%, hard stop at 100% (MU escalates with BUDGET reason).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import asyncpg

from .ids import new_ulid


# Model pricing: $ per 1M tokens (Anthropic public pricing, R1 reference)
MODEL_PRICING = {
    "claude-sonnet-5": {"input": 3.0, "output": 15.0},
    "claude-opus-5": {"input": 15.0, "output": 75.0},
}


@dataclass
class TokenBudgetStatus:
    """Current budget status for an MU."""
    tokens_limit: int
    tokens_consumed: int
    cost_usd: float
    percent_used: float
    is_exhausted: bool  # True if >= 100%
    is_warning: bool    # True if >= 80% and < 100%


class TokenBudgetStore:
    """Track and enforce per-MU token budgets."""

    def __init__(self, pool: asyncpg.Pool, *, graph_name: str) -> None:
        self._pool = pool
        self._graph = graph_name

    async def set_budget(
        self, mu_ref: str, tokens_limit: int, reason: str = ""
    ) -> None:
        """Set or update token budget for an MU."""
        async with self._pool.acquire() as conn:
            await conn.execute(
                """INSERT INTO public.token_budget
                   (id, graph, mu_ref, tokens_limit, reason, set_at)
                   VALUES ($1, $2, $3, $4, $5, now())
                   ON CONFLICT (graph, mu_ref) DO UPDATE
                   SET tokens_limit = $4, reason = $5, set_at = now()""",
                f"tbudget_{new_ulid()}", self._graph, mu_ref, tokens_limit, reason,
            )

    async def get_status(
        self, mu_ref: str, model: str = "claude-sonnet-5"
    ) -> TokenBudgetStatus:
        """Get current budget status for an MU (daily consumption from gateway_request_log)."""
        async with self._pool.acquire() as conn:
            # Get configured budget
            row = await conn.fetchrow(
                "SELECT tokens_limit FROM public.token_budget "
                "WHERE graph = $1 AND mu_ref = $2",
                self._graph, mu_ref,
            )
            tokens_limit = row["tokens_limit"] if row else 1_000_000  # Default 1M

            # Query today's token consumption from gateway_request_log
            # Assumption: gateway_request_log.prompt_hash has been populated with model calls
            # We need to sum tokens from calls where the MU's workbook_id matches
            # For now, this is a stub—real implementation would join workbook → MU
            tokens_consumed = await conn.fetchval(
                """SELECT COALESCE(SUM(gr.tokens_in + gr.tokens_out), 0)
                   FROM public.gateway_request_log gr
                   WHERE gr.graph = $1 AND DATE(gr.created_at) = CURRENT_DATE
                   AND gr.agent_id = $2""",
                self._graph, mu_ref,
            ) or 0

            # Calculate cost
            pricing = MODEL_PRICING.get(model, MODEL_PRICING["claude-sonnet-5"])
            # Rough estimate: assume 60/40 split input/output for now
            cost_usd = (
                tokens_consumed * 0.6 * pricing["input"] +
                tokens_consumed * 0.4 * pricing["output"]
            ) / 1_000_000

            percent_used = (tokens_consumed / tokens_limit * 100) if tokens_limit > 0 else 0

            return TokenBudgetStatus(
                tokens_limit=tokens_limit,
                tokens_consumed=tokens_consumed,
                cost_usd=cost_usd,
                percent_used=percent_used,
                is_exhausted=tokens_consumed >= tokens_limit,
                is_warning=80 <= percent_used < 100,
            )
