"""Model gateway observability -- story S12.2.1.

    "Every call records task class, provider, model, prompt hash, context hash, tokens
    in/out, latency, cost; returns a gateway request id used in provenance."

Three new nullable columns on `public.gateway_request_log` (v0043-v0045): `context_hash`
(the request payload's own hash, separate from `prompt_hash`, which now hashes the
system prompt), `latency_ms` (wall-clock time of the real provider call), and
`prompt_template_version` (which version of the prompt template produced the call).
Nullable rather than `NOT NULL`: every row logged before this migration has none of
the three, and inventing a value for them would be a false record.

No ontology change here.
"""

from __future__ import annotations

import asyncpg

VERSION = 46
DESCRIPTION = (
    "Model gateway observability: context_hash, latency_ms, prompt_template_version on "
    "the gateway request log (S12.2.1)"
)

ONTOLOGY_CHANGES: list[dict[str, str]] = []


_DDL = """
ALTER TABLE public.gateway_request_log
    ADD COLUMN IF NOT EXISTS context_hash text,
    ADD COLUMN IF NOT EXISTS latency_ms double precision,
    ADD COLUMN IF NOT EXISTS prompt_template_version text
"""


async def up(conn: asyncpg.Connection) -> None:
    await conn.execute(_DDL)
