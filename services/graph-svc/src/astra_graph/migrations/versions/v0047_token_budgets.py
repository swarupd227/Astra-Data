"""Token and cost budgets per MU -- story S12.2.2.

    "Budgets configurable at three levels; consumption shown in real time; soft alert
    at 80%, hard stop at 100% per MU."

One new table, `public.token_budget`: a per-MU daily token limit, scoped by graph (the
same "the graph is the tenant" footing every other per-tenant table here already
uses), one row per `(graph, mu_ref)`. R1 configures the MU level only; programme and
train levels are a disclosed follow-on.

No ontology change here.
"""

from __future__ import annotations

import asyncpg

VERSION = 47
DESCRIPTION = "Token budgets: a per-MU daily token limit (S12.2.2)"

ONTOLOGY_CHANGES: list[dict[str, str]] = []


_DDL = """
CREATE TABLE IF NOT EXISTS public.token_budget (
    id            text        PRIMARY KEY,
    graph         text        NOT NULL,
    mu_ref        text        NOT NULL,
    tokens_limit  integer     NOT NULL,
    reason        text,
    set_at        timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT token_budget_graph_mu_unique UNIQUE (graph, mu_ref)
)
"""


async def up(conn: asyncpg.Connection) -> None:
    await conn.execute(_DDL)
