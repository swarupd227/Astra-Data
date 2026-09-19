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

`BudgetMonitor` acts on that number, at the model gateway (see its own docstring): it
raises the 80% and 100% alerts and lets the gateway refuse further calls once an MU is
at its limit.

Not built here (see ADR 0090): the programme and train levels, and the
cost-per-accepted-report and Status Pack views.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

import asyncpg

from .events import EventType, PlatformEvent, budget_exhausted, budget_warning, source_for
from .ids import new_ulid
from .principal import Principal

if TYPE_CHECKING:
    from .writes import GraphWriter

#: The share of an MU's token budget at which the soft alert is raised (S12.2.2's AC).
WARNING_PERCENT = 80.0

#: Who a budget alert is attributed to when the call that crossed the line names no principal.
DEFAULT_ALERT_PRINCIPAL = "service:model-gateway"

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
            is_warning=WARNING_PERCENT <= percent_used < 100,
            unpriced_tokens=unpriced_tokens,
        )


class BudgetMonitor:
    """The gateway-side budget guard (story S12.2.2): 80% soft alert, 100% hard stop.

    `check` reads an MU's status, raises whichever alerts it has reached, and returns the
    status; `ModelGateway` calls it before each model call (refusing the call if the MU is
    exhausted) and after (so the alert is raised by the very call that crossed the line,
    not one call later).

    **Why the gateway, not the workflow.** The hard stop has to bound spend per *call*: a
    Transpiler ladder or a Mender run makes several model calls inside one workflow
    activity, so a check between activities could overshoot by a whole activity. It also
    cannot be a workflow-level "escalate now": §3.2 allows `ESCALATED` only from `FAILED`
    or `MENDING`, so a refused call instead flows through the legal chain (a refused call
    fails the attempt, the MU goes `FAILED` and then `ESCALATED`, stamped with reason
    `BUDGET` by `MuActivities.write_mu_state`).

    **Each alert is raised once per `(MU, limit)`.** A budget the operator raises is a new
    budget and can alert again when it is reached. The once-only test reads the event
    outbox rather than in-process state, so it survives restarts and holds across
    workers. Two calls racing across the line could in principle each raise it; the
    consequence is one duplicate notice, never a missed one.
    """

    def __init__(
        self, store: TokenBudgetStore, *, pool: asyncpg.Pool, graph_name: str,
        writer: GraphWriter,
    ) -> None:
        self._store = store
        self._pool = pool
        self._graph = graph_name
        self._writer = writer

    async def check(self, workbook_id: str, *, principal: str | None = None) -> TokenBudgetStatus:
        status = await self._store.get_status(workbook_id)
        actor = Principal(principal or DEFAULT_ALERT_PRINCIPAL)
        source = source_for(self._graph)

        if status.percent_used >= WARNING_PERCENT:
            await self._raise_once(
                EventType.BUDGET_WARNING, workbook_id, status.tokens_limit,
                budget_warning(
                    source=source, workbook_id=workbook_id,
                    tokens_consumed=status.tokens_consumed, tokens_limit=status.tokens_limit,
                    percent_used=status.percent_used, principal=actor,
                ),
            )
        if status.is_exhausted:
            await self._raise_once(
                EventType.BUDGET_EXHAUSTED, workbook_id, status.tokens_limit,
                budget_exhausted(
                    source=source, workbook_id=workbook_id,
                    tokens_consumed=status.tokens_consumed, tokens_limit=status.tokens_limit,
                    cost_usd=status.cost_usd, principal=actor,
                ),
            )
        return status

    async def _raise_once(
        self, type_: EventType, workbook_id: str, tokens_limit: int, event: PlatformEvent
    ) -> None:
        already = await self._pool.fetchval(
            """SELECT 1 FROM public.estate_event
                WHERE graph = $1 AND type = $2 AND subject = $3
                  AND (data->>'tokens_limit')::bigint = $4
                LIMIT 1""",
            self._graph, type_.value, workbook_id, tokens_limit,
        )
        if not already:
            await self._writer.append_event(event)
