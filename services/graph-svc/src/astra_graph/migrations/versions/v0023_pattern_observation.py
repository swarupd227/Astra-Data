"""Pattern Library proof observations — story S5.5.1.

    "Promotion CANDIDATE -> ACTIVE requires N distinct proof passes (default 5), zero
    failures, and a Platform Engineer approval."

A platform table, the same footing as `calibration_observation` (S5.3.3): one row per real
proof pass or failure a Pattern was ever a party to, never overwritten, so promotion
eligibility is always checked against the complete history this platform has actually
observed, not a maintained running counter that could drift from it.

No ontology change: a proof observation is bookkeeping about a generation attempt, not a
fact about the source or target estate. `Pattern.guards` (the ontology change this story
also makes) is additive and already covered without a migration (see
`tools/migration_check.py`'s own "additive changes need no migration" rule).
"""

from __future__ import annotations

import asyncpg

VERSION = 23
DESCRIPTION = "Pattern Library proof observations (public.pattern_observation)"

ONTOLOGY_CHANGES: list[dict[str, str]] = []


_DDL = """
CREATE TABLE IF NOT EXISTS public.pattern_observation (
    id            text        PRIMARY KEY,
    graph         text        NOT NULL,
    pattern_id    text        NOT NULL,
    calc_id       text        NOT NULL,
    observed_pass boolean     NOT NULL,
    source        text        NOT NULL,
    created_by    text        NOT NULL,
    recorded_at   timestamptz NOT NULL DEFAULT now()
)
"""

_INDEXES = (
    "CREATE INDEX IF NOT EXISTS pattern_observation_pattern_idx "
    "ON public.pattern_observation (graph, pattern_id)",
)


async def up(conn: asyncpg.Connection) -> None:
    await conn.execute(_DDL)
    for statement in _INDEXES:
        await conn.execute(statement)
