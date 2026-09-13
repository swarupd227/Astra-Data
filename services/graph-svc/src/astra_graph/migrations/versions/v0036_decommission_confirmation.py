"""Owner decommission confirmation -- story S9.3.1, opening F9.3.

    "As a licence admin, I want a Decommission Tracker per site with a readiness
    checklist and a G4 card when ready, so that I switch off a site once, safely, with
    a record.

    Acceptance criteria:
    - Readiness = all in-scope MUs RELEASED + parallel window elapsed + regression
      green + adoption threshold met + owner confirmations received; each item shows
      its state and evidence"

**A plain Postgres platform table, the identical footing `promotion_run`/`adoption_
snapshot` already have -- not an estate-graph node.** No `astra.data.decommission.*`
event or §21 data-model table names this fact anywhere in the spec (confirmed by direct
search); §14.4's own "owner confirmation received" is a real, invented, disclosed shape
this story builds for the first time.

**One current confirmation per workbook -- overwrite, not append.** `UNIQUE (graph,
workbook_id)` with `ON CONFLICT ... DO UPDATE`, the identical shape `retention.
Programme.family_count_confirmed_by`/`_at` already set: a later confirmation supersedes
the earlier one, since this is a "has the owner confirmed" current-state fact, not a
growing history of every time someone confirmed the same thing.

**No ontology changes accompany this migration.** `Site.decommissioned_at`/`.licence_
release_value` and `GateDecision.target_date` are new but purely additive node
properties (confirmed by `tools/migration_check.py`: "Additive ontology changes (no
migration required)") -- `ontology.lock.json`/`SCHEMA_VERSION` alone carry them; this
migration's own `ONTOLOGY_CHANGES` stays empty, the identical footing `v0034`/`v0035`
already have for their own plain Postgres tables.
"""

from __future__ import annotations

import asyncpg

VERSION = 36
DESCRIPTION = "Owner decommission confirmation (public.decommission_confirmation)"

ONTOLOGY_CHANGES: list[dict[str, str]] = []


_CONFIRMATION_DDL = """
CREATE TABLE IF NOT EXISTS public.decommission_confirmation (
    id             text        PRIMARY KEY,
    graph          text        NOT NULL,
    workbook_id    text        NOT NULL,
    confirmed_by   text        NOT NULL,
    confirmed_at   timestamptz NOT NULL DEFAULT now(),
    UNIQUE (graph, workbook_id)
)
"""

_INDEXES = (
    "CREATE INDEX IF NOT EXISTS decommission_confirmation_workbook_idx "
    "ON public.decommission_confirmation (graph, workbook_id)",
)


async def up(conn: asyncpg.Connection) -> None:
    await conn.execute(_CONFIRMATION_DDL)
    for statement in _INDEXES:
        await conn.execute(statement)
