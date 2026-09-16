"""Throughput and cost metrics -- story S6.2.3.

    "As a project manager, I want custodians live per week, agent acceptance and
    credits per custodian per day, so that reporting is generated.

    Acceptance criteria:
    - Weekly report exported for the client cadence
    - Cost per custodian visible from query tags"

**"Custodian" reads as this platform's own `Site` node (§4.1.1) -- confirmed by direct
research, this story's own vocabulary ("custodian," "credits," "query tags," a "WBS
2.6.6" numbering scheme) appears nowhere else in this codebase, spec, or backlog, and
`S6.2.3` collides with an already-shipped id (`E6`'s own `F6.2` already has exactly
`S6.2.1`/`S6.2.2`). A real, disclosed translation, confirmed by the user before any code
was written: custodian = Site, credits = real LLM token cost, agent acceptance = the
existing `commercial_ledger`/`invoicing.accepted_by_tier` MU-acceptance fact (no second
acceptance concept invented), and "weekly report" reuses the Status Pack's own
on-demand, `POST`-triggered shape rather than a real scheduler this platform does not
have.

**Four new columns on `public.gateway_request_log` (v0043/v0044): `query_tag`,
`tokens_in`, `tokens_out`, `cost_usd`.** "Query tags" did not exist anywhere before this
story -- `query_tag` is a real, new, optional site-id attribution a caller may attach to
a real gateway dispatch (`gateway._dispatch`'s own new `query_tag` parameter);
`tokens_in`/`tokens_out` were already computed on every real `RawModelResponse`
(`gateway.py`'s own module docstring already named the gap: "real cost-tier ranking...
not built here") but never persisted until now; `cost_usd` is `tokens_in`/`tokens_out`
converted through a real, disclosed, invented per-provider rate
(`gateway.PROVIDER_TOKEN_COSTS`), the identical "a real, invented, disclosed planning
assumption" footing `invoicing.DEFAULT_UNIT_PRICES` already has. All four are metadata
about the call, not its content -- persisted unconditionally, the same footing
`redaction_count`/`provider`/`task_class` already have, never gated by the
content-logging grant that only ever controls literal request/response *text*.

**A new table, `public.throughput_report`.** Append-only, one row per real generate
call -- the identical "an edit/regenerate is a new version, never an in-place overwrite"
shape `status_pack` (v0037) already established for its own weekly report. Stores the
full computed report as `jsonb` so a past week's own real numbers stay inspectable even
after the underlying `gateway_request_log`/`commercial_ledger` rows they were computed
from have aged past whatever retention window a later story gives them.

No ontology change: none of this is a fact about the source or target estate -- the
identical reasoning `gateway_request_log`'s own v0043 docstring, and `status_pack`'s own
v0037 docstring, already give for their own tables.
"""

from __future__ import annotations

import asyncpg

VERSION = 45
DESCRIPTION = (
    "Throughput and cost metrics: query tags, token counts and cost on the gateway "
    "request log, plus public.throughput_report (S6.2.3)"
)

ONTOLOGY_CHANGES: list[dict[str, str]] = []


_DDL = """
ALTER TABLE public.gateway_request_log
    ADD COLUMN IF NOT EXISTS query_tag text,
    ADD COLUMN IF NOT EXISTS tokens_in int,
    ADD COLUMN IF NOT EXISTS tokens_out int,
    ADD COLUMN IF NOT EXISTS cost_usd numeric(12,6);

CREATE TABLE IF NOT EXISTS public.throughput_report (
    id            text        PRIMARY KEY,
    graph         text        NOT NULL,
    generated_by  text        NOT NULL,
    generated_at  timestamptz NOT NULL DEFAULT now(),
    weeks         integer     NOT NULL,
    days          integer     NOT NULL,
    report        jsonb       NOT NULL
)
"""

_INDEXES = (
    "CREATE INDEX IF NOT EXISTS gateway_request_log_query_tag_idx "
    "ON public.gateway_request_log (graph, query_tag, created_at DESC)",
    "CREATE INDEX IF NOT EXISTS throughput_report_graph_idx "
    "ON public.throughput_report (graph, generated_at DESC)",
)


async def up(conn: asyncpg.Connection) -> None:
    await conn.execute(_DDL)
    for statement in _INDEXES:
        await conn.execute(statement)
