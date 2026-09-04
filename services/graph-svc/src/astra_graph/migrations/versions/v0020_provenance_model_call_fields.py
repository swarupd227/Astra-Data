"""Provenance gains the rest of §4.2's own ``model_call`` block — story S5.3.1.

    "Mode recorded as GENERATED_PROVED; provenance carries gateway request id, provider,
    model, prompt hash, context hash, temperature 0, token counts."

``model``/``prompt_hash``/``tokens_in``/``tokens_out``/``context_hash`` have all existed
since S1.3.2 — but nothing before this story ever actually called a model, so ``model_call``
was always null in practice. §4.2's own worked example names three fields this table has
never had: ``gateway_request`` (the correlation id an operator would use to find this call
in the Model Gateway's own logs, §5.5 — not built yet, story S5.3.2's), ``provider``, and
``temperature`` ("Temperature is 0... for all generation paths", §5.4/§9.4 — recorded per
call rather than assumed).

A platform-table column addition, not an ontology change: provenance is §21's own table,
not an estate-graph node.
"""

from __future__ import annotations

import asyncpg

VERSION = 20
DESCRIPTION = "Provenance gains provider/gateway_request_id/temperature"

ONTOLOGY_CHANGES: list[dict[str, str]] = []


async def up(conn: asyncpg.Connection) -> None:
    await conn.execute("ALTER TABLE public.provenance ADD COLUMN IF NOT EXISTS provider text")
    await conn.execute(
        "ALTER TABLE public.provenance ADD COLUMN IF NOT EXISTS gateway_request_id text"
    )
    await conn.execute(
        "ALTER TABLE public.provenance ADD COLUMN IF NOT EXISTS temperature double precision"
    )
