"""Prompt-injection defence -- story S11.4.3, closes F11.4.

    "Gateway runs an injection classifier on typed content; hits are logged and the
    field is replaced with a placeholder plus an ExceptionCase for a human."

One new column on `public.gateway_request_log` (v0043/v0044): `injection_flagged_fields`,
a real `jsonb` array of the typed-content field names `injection_defense.
scan_payload_for_injection` withheld before a real request was ever sent -- metadata
about the call, not its content, persisted unconditionally (defaults to `'[]'::jsonb`,
never null) the identical footing `redaction_count` already has, never gated by the
S11.4.2 content-logging grant that only ever controls literal request/response *text*.

No ontology change here: `ExceptionCase.class` gains a new allowed value,
`INJECTION_SUSPECTED` (see `ontology/nodes.py`'s own `SpecDeviation` entry for the full
reasoning) -- *adding* an enum value is not a breaking ontology change
(`ontology/lock.py`'s own `diff` only flags a *removed* enum value as breaking), so no
migration entry or `SCHEMA_VERSION` bump is needed for it, the identical footing the
prior two disclosed uses of this same enum (`VISUAL_REDESIGN`, `REGRESSION`) already
established when each was added.
"""

from __future__ import annotations

import asyncpg

VERSION = 45
DESCRIPTION = (
    "Prompt-injection defence: injection_flagged_fields on the gateway request log "
    "(S11.4.3)"
)

ONTOLOGY_CHANGES: list[dict[str, str]] = []


_DDL = """
ALTER TABLE public.gateway_request_log
    ADD COLUMN IF NOT EXISTS injection_flagged_fields jsonb NOT NULL DEFAULT '[]'::jsonb
"""


async def up(conn: asyncpg.Connection) -> None:
    await conn.execute(_DDL)
