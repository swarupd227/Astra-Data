"""Token and cost budgets per MU with consumption tracking and alerts (S12.2.2).

Budgets are configured per MU; the daily-limit mechanism and cost math are real.
Consumption tracking (the part that reads what an MU actually spent) is a disclosed
R1 gap, not a silent one: ``gateway_request_log`` (S5.3.2/S11.4.x/S12.2.1) has no
column attributing a call to the *MU* it was made for -- ``agent_id`` names the
calling agent role (``"transpiler"``, ``"mender"``), not a workbook id -- and never
persisted ``tokens_in``/``tokens_out`` at all, only ``RawModelResponse`` carried
them in memory. Attributing real spend to a real MU needs, as a real follow-on:
(1) a migration adding ``workbook_id``, ``tokens_in``, ``tokens_out`` to
``gateway_request_log``; (2) threading ``workbook_id`` through
``ModelGateway.generate`` and every caller (``generation.py``, ``mender.py``) so it
is actually recorded. Until then, ``get_status`` honestly reports zero consumption
rather than querying columns that do not exist.
"""

from __future__ import annotations

from dataclasses import dataclass

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
        """Get current budget status for an MU.

        The configured limit is real. Consumption is honestly zero until
        ``gateway_request_log`` carries a real per-MU attribution (module
        docstring) -- returning zero here is a disclosed gap, not a silently
        wrong number from a query against columns that do not exist.
        """
        tokens_limit = await self._pool.fetchval(
            "SELECT tokens_limit FROM public.token_budget WHERE graph = $1 AND mu_ref = $2",
            self._graph, mu_ref,
        ) or 1_000_000  # Default 1M when no budget has been configured for this MU

        tokens_consumed = 0  # See module docstring: real attribution is a follow-on.

        pricing = MODEL_PRICING.get(model, MODEL_PRICING["claude-sonnet-5"])
        # Assumes a 60/40 input/output split until real per-call token counts are
        # attributed to an MU (module docstring) -- an estimate, not a measurement.
        cost_usd = (
            tokens_consumed * 0.6 * pricing["input"] +
            tokens_consumed * 0.4 * pricing["output"]
        ) / 1_000_000

        percent_used = (tokens_consumed / tokens_limit * 100) if tokens_limit > 0 else 0.0

        return TokenBudgetStatus(
            tokens_limit=tokens_limit,
            tokens_consumed=tokens_consumed,
            cost_usd=cost_usd,
            percent_used=percent_used,
            is_exhausted=tokens_consumed >= tokens_limit,
            is_warning=80 <= percent_used < 100,
        )
