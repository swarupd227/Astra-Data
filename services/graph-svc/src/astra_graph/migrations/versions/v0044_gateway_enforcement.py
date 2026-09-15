"""The gateway enforces the boundary -- story S11.4.2, closes F11.4.

    "The gateway to enforce the boundary, not rely on agents to respect it... All
    gateway requests and responses are logged with hashes; content logging is off by
    default and requires the InfoSec role to enable for a bounded window."

Two real changes to `public.gateway_request_log` (v0043): `request_text` becomes
nullable (content logging is now off by default -- a row with no active grant records
only its own hash, never bare text), and two new columns, `response_hash`/
`response_text` (the AC's own "requests **and responses**" -- nothing before this story
ever logged a response at all) and `redaction_count` (the pattern-redaction pass's own
"logging the redaction count").

A new table, `gateway_content_logging_grant` -- the identical "the graph is the tenant,
append-only, validity computed at read time, never a stored flag" footing every other
versioned/time-bounded record in this codebase already has (`data_handling_position`/
`data_handling_signoff`, S11.4.1; `SvidRecord`'s own `expires_at`/`revoked_at` shape,
S11.1.2). A grant is "active" iff `revoked_at IS NULL AND expires_at > now()` -- computed
by `gateway.ContentLoggingGrantStore.active`, never a column of its own.

No ontology change here: none of this is a fact about the source or target estate --
the identical reasoning `gateway_request_log`'s own v0043 docstring already gives.
"""

from __future__ import annotations

import asyncpg

VERSION = 44
DESCRIPTION = (
    "The gateway enforces the boundary: response logging, redaction counts, and a "
    "bounded content-logging grant (public.gateway_content_logging_grant)"
)

ONTOLOGY_CHANGES: list[dict[str, str]] = []


_DDL = """
ALTER TABLE public.gateway_request_log
    ALTER COLUMN request_text DROP NOT NULL,
    ADD COLUMN IF NOT EXISTS response_hash text,
    ADD COLUMN IF NOT EXISTS response_text text,
    ADD COLUMN IF NOT EXISTS redaction_count int NOT NULL DEFAULT 0;

CREATE TABLE IF NOT EXISTS public.gateway_content_logging_grant (
    id             text        PRIMARY KEY,
    graph          text        NOT NULL,
    enabled_by     text        NOT NULL,
    enabled_at     timestamptz NOT NULL DEFAULT now(),
    expires_at     timestamptz NOT NULL,
    revoked_at     timestamptz,
    revoked_by     text,
    revoked_reason text
)
"""

_INDEXES = (
    "CREATE INDEX IF NOT EXISTS gateway_content_logging_grant_active_idx "
    "ON public.gateway_content_logging_grant (graph, expires_at DESC)",
)


async def up(conn: asyncpg.Connection) -> None:
    await conn.execute(_DDL)
    for statement in _INDEXES:
        await conn.execute(statement)
