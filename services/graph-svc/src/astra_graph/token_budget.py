"""Token and cost budgets per MU, and what each MU has actually spent (story S12.2.2).

A budget is a per-MU token limit (`public.token_budget`). Consumption is read from
`public.gateway_request_log`, which since migration v0048 records, for every model call,
the `workbook_id` it was made for and the `tokens_in`/`tokens_out` the provider reported.
Consumption is **cumulative for the MU** -- the AC budgets an MU, not a day.

Only calls that were attributed to an MU count. Rows written before v0048, calls made
with no MU in scope (the boundary test, eval runs), and calls that raised (NULL tokens)
are never summed: "unknown" is not "free" and is not billed to an MU it cannot be tied to.

Cost is exact where it can be: each model's own input and output tokens are priced from
`MODEL_PRICING`. A model with no price entry contributes tokens but no cost, and its
tokens are reported separately as `unpriced_tokens` so a partial cost is never mistaken
for a complete one.

Not built here (see ADR 0090): the 80% alert and 100% hard stop that act on this number,
the programme and train levels, and the cost-per-accepted-report and Status Pack views.
"""

from __future__ import annotations

from dataclasses import dataclass

import asyncpg

from .ids import new_ulid

DEFAULT_TOKENS_LIMIT = 1_000_000

#: $ per 1M tokens, keyed by the exact model id the gateway logs.
MODEL_PRICING: dict[str, dict[str, float]] = {
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
    unpriced_tokens: int = 0  # tokens from models with no MODEL_PRICING entry


def cost_usd_for(model: str, tokens_in: int, tokens_out: int) -> float | None:
    """Exact cost of one model's usage, or None if that model has no price."""
    pricing = MODEL_PRICING.get(model)
    if pricing is None:
        return None
    return (tokens_in * pricing["input"] + tokens_out * pricing["output"]) / 1_000_000


class TokenBudgetStore:
    """Configure per-MU token budgets and read what each MU has consumed."""

    def __init__(self, pool: asyncpg.Pool, *, graph_name: str) -> None:
        self._pool = pool
        self._graph = graph_name

    async def set_budget(self, mu_ref: str, tokens_limit: int, reason: str = "") -> None:
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

    async def get_status(self, mu_ref: str) -> TokenBudgetStatus:
        """The MU's configured limit (default 1,000,000 if none was set) against the
        tokens and cost its attributed gateway calls have really used."""
        tokens_limit = await self._pool.fetchval(
            "SELECT tokens_limit FROM public.token_budget WHERE graph = $1 AND mu_ref = $2",
            self._graph, mu_ref,
        ) or DEFAULT_TOKENS_LIMIT

        rows = await self._pool.fetch(
            """SELECT model,
                      COALESCE(SUM(tokens_in), 0)::bigint  AS tokens_in,
                      COALESCE(SUM(tokens_out), 0)::bigint AS tokens_out
                 FROM public.gateway_request_log
                WHERE graph = $1 AND workbook_id = $2
                  AND tokens_in IS NOT NULL AND tokens_out IS NOT NULL
                GROUP BY model""",
            self._graph, mu_ref,
        )

        tokens_consumed = 0
        unpriced_tokens = 0
        cost_usd = 0.0
        for row in rows:
            used = int(row["tokens_in"]) + int(row["tokens_out"])
            tokens_consumed += used
            cost = cost_usd_for(row["model"] or "", int(row["tokens_in"]), int(row["tokens_out"]))
            if cost is None:
                unpriced_tokens += used
            else:
                cost_usd += cost

        percent_used = (tokens_consumed / tokens_limit * 100) if tokens_limit > 0 else 0.0
        return TokenBudgetStatus(
            tokens_limit=tokens_limit,
            tokens_consumed=tokens_consumed,
            cost_usd=cost_usd,
            percent_used=percent_used,
            is_exhausted=tokens_consumed >= tokens_limit,
            is_warning=80 <= percent_used < 100,
            unpriced_tokens=unpriced_tokens,
        )
