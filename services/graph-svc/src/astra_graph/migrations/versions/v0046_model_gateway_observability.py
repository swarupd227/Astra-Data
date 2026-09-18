"""S12.2.1: Model gateway observability — add latency, context hash, and prompt versioning.

Story S12.2.1: Track latency (milliseconds), context_hash (payload separate from template),
and prompt_template_version (Git SHA of template) in gateway request log for full provenance.

Tables modified:
- public.gateway_request_log: add context_hash, latency_ms, prompt_template_version
"""

import asyncpg


_DDL = f"""
ALTER TABLE public.gateway_request_log
    ADD COLUMN IF NOT EXISTS context_hash text,
    ADD COLUMN IF NOT EXISTS latency_ms double precision,
    ADD COLUMN IF NOT EXISTS prompt_template_version text;
"""


async def up(conn: asyncpg.Connection) -> None:
    await conn.execute(_DDL)
