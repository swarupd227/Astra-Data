"""Gateway usage attribution -- the blocker under story S12.2.2 (and S12.2.1's AC).

    "Every call records task class, provider, model, prompt hash, context hash, tokens
    in/out, latency, cost."  /  "budgets per programme, per train and per MU."

Four new nullable columns on `public.gateway_request_log`: `model`, `tokens_in`,
`tokens_out` (the usage the provider reported, which existed only on the in-memory
`RawModelResponse` until now) and `workbook_id` (which MU the call was made for -- the
log had no way to say, `agent_id` being the calling agent's role). Nullable, not
defaulted: every earlier row genuinely has no attribution, and a `0` would be summed as
"free". A call that raised also records NULL tokens for the same reason.

An index on `(graph, workbook_id, created_at)` serves the per-MU consumption sum.

No ontology change here.
"""

from __future__ import annotations

import asyncpg

VERSION = 48
DESCRIPTION = (
    "Gateway usage attribution: model, tokens_in, tokens_out, workbook_id on the request "
    "log (S12.2.2)"
)

ONTOLOGY_CHANGES: list[dict[str, str]] = []


_DDL = """
ALTER TABLE public.gateway_request_log
    ADD COLUMN IF NOT EXISTS model text,
    ADD COLUMN IF NOT EXISTS tokens_in integer,
    ADD COLUMN IF NOT EXISTS tokens_out integer,
    ADD COLUMN IF NOT EXISTS workbook_id text
"""

_INDEX = """
CREATE INDEX IF NOT EXISTS gateway_request_log_workbook_idx
    ON public.gateway_request_log (graph, workbook_id, created_at DESC)
    WHERE workbook_id IS NOT NULL
"""


async def up(conn: asyncpg.Connection) -> None:
    await conn.execute(_DDL)
    await conn.execute(_INDEX)
