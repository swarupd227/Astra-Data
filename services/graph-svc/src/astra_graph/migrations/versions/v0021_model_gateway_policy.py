"""The Model Gateway's own tenant policy — story S5.3.2.

    "Routing is by task class and tenant policy; both configured providers pass the
    Transpiler eval set at >= 0.80 first-pass proof before being routable for
    transpile_c3."

A platform table, the same footing as `conformance_ruleset`/`g2_question`/`build_run`:
append-only, one row per eval run, `routable` derived from the *latest* row per
`(graph, task_class, provider)` rather than stored as a mutable flag — the same "an edit is
a new version, never an overwrite" discipline `conformance_ruleset` (S4.3.2) already set,
here giving a full, queryable eval history rather than only a last-known verdict.

No ontology change: gateway policy is bookkeeping about which providers a tenant may route
to, not a fact about the source or target estate.
"""

from __future__ import annotations

import asyncpg

VERSION = 21
DESCRIPTION = "The Model Gateway's tenant policy (public.model_gateway_policy)"

ONTOLOGY_CHANGES: list[dict[str, str]] = []


_DDL = """
CREATE TABLE IF NOT EXISTS public.model_gateway_policy (
    id            text             PRIMARY KEY,
    graph         text             NOT NULL,
    task_class    text             NOT NULL,
    provider      text             NOT NULL,
    model         text             NOT NULL,
    total_cases   int              NOT NULL,
    passed_cases  int              NOT NULL,
    pass_rate     double precision NOT NULL,
    case_results  jsonb            NOT NULL,
    updated_by    text             NOT NULL,
    updated_at    timestamptz      NOT NULL DEFAULT now()
)
"""

_INDEXES = (
    "CREATE INDEX IF NOT EXISTS model_gateway_policy_latest_idx "
    "ON public.model_gateway_policy (graph, task_class, provider, updated_at DESC)",
)


async def up(conn: asyncpg.Connection) -> None:
    await conn.execute(_DDL)
    for statement in _INDEXES:
        await conn.execute(statement)
