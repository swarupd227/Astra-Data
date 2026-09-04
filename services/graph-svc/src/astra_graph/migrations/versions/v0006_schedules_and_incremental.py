"""Harvest schedules, and the two facts an incremental run compares against.

S1.2.4. Two changes:

* ``harvest_schedule`` — one row per recurring harvest. A schedule is data rather than a
  cron entry in an image because Platform Health has to show when it last ran and an
  engineer has to be able to pause it (spec §15.3.3).
* ``harvest_workbook.source_updated_at`` — what the source said about this workbook the
  last time it was recorded. Without it an incremental run has nothing to compare the
  enumeration's ``updatedAt`` against, and would have to fetch to find out.

The backfill is deliberately absent. A workbook harvested before this migration has no
recorded source timestamp, and inventing one — ``harvested_at``, say — would claim the
source had not changed since a moment the source never mentioned. Leaving it NULL makes
the first incremental run fetch that workbook once and record the truth. One extra pass
over an estate, in exchange for never skipping a workbook on a guess.

The ontology is unchanged: a schedule is a platform record, not an estate fact.
"""

from __future__ import annotations

import asyncpg

VERSION = 6
DESCRIPTION = "Harvest schedules and incremental change detection"

ONTOLOGY_CHANGES: list[dict[str, str]] = []


_SCHEDULE_DDL = """
CREATE TABLE IF NOT EXISTS public.harvest_schedule (
    id                      text        PRIMARY KEY,
    graph                   text        NOT NULL,
    site                    text        NOT NULL,
    project                 text,
    credential_reference    text        NOT NULL,
    cadence                 jsonb       NOT NULL,
    enabled                 boolean     NOT NULL DEFAULT true,
    paused_reason           text,
    next_run_at             timestamptz NOT NULL,
    last_run_at             timestamptz,
    last_run_id             text,
    last_run_state          text,
    last_error              text,
    consecutive_failures    integer     NOT NULL DEFAULT 0,
    parse_quality_threshold double precision,
    concurrency             integer,
    created_by              text        NOT NULL,
    created_at              timestamptz NOT NULL DEFAULT now(),
    updated_at              timestamptz NOT NULL DEFAULT now()
)
"""

_INDEXES = (
    # One schedule per scope. Two schedules over the same site would race each other for
    # the same workbooks and double the source I/O to no purpose. NULL project is its own
    # scope — the whole site — so the uniqueness is expressed as two partial indexes,
    # because in SQL one NULL does not equal another.
    "CREATE UNIQUE INDEX IF NOT EXISTS harvest_schedule_scope_site_idx "
    "ON public.harvest_schedule (graph, site) WHERE project IS NULL",
    "CREATE UNIQUE INDEX IF NOT EXISTS harvest_schedule_scope_project_idx "
    "ON public.harvest_schedule (graph, site, project) WHERE project IS NOT NULL",
    # What the scheduler's poll reads, every poll interval, forever.
    "CREATE INDEX IF NOT EXISTS harvest_schedule_due_idx "
    "ON public.harvest_schedule (graph, next_run_at) WHERE enabled",
)

#: Platform Health lists the recent source-drift notices. Without this the only way to find
#: them is a scan of every event ever written, which on a harvested estate is millions of
#: rows to return twenty.
_EVENT_TYPE_INDEX = (
    "CREATE INDEX IF NOT EXISTS estate_event_type_idx "
    "ON public.estate_event (graph, type, seq DESC)"
)


async def up(conn: asyncpg.Connection) -> None:
    await conn.execute(_SCHEDULE_DDL)
    for statement in _INDEXES:
        await conn.execute(statement)
    await conn.execute(_EVENT_TYPE_INDEX)
    await conn.execute(
        "ALTER TABLE public.harvest_workbook "
        "ADD COLUMN IF NOT EXISTS source_updated_at timestamptz"
    )
