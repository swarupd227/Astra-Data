"""S12.2.2: Token and cost budgets per MU with consumption tracking.

Story S12.2.2: Configurable budgets per MU, daily consumption tracked,
soft alert at 80%, hard stop at 100% (MU escalates).

Tables created:
- public.token_budget: configure per-MU limits
"""

import asyncpg


_DDL = """
CREATE TABLE IF NOT EXISTS public.token_budget (
    id            text        PRIMARY KEY,
    graph         text        NOT NULL,
    mu_ref        text        NOT NULL,
    tokens_limit  int         NOT NULL,
    reason        text,
    set_at        timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT token_budget_unique UNIQUE (graph, mu_ref)
);

CREATE INDEX IF NOT EXISTS token_budget_graph_idx
    ON public.token_budget (graph, mu_ref);
"""


async def up(conn: asyncpg.Connection) -> None:
    await conn.execute(_DDL)
