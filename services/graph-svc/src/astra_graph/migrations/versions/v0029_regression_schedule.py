"""Regression schedules.

S7.7.1, closing F7.7 and E7. One change:

* ``regression_schedule`` -- one row per workbook enrolled in scheduled regression
  (spec §10.6: "the Steward re-runs a site's suites on a schedule"). A schedule is data
  for the identical reason `v0006`'s own ``harvest_schedule`` already gives: the
  Regression Monitor has to show when a workbook last ran and a programme manager has to
  be able to pause one, neither of which a cron entry baked into an image can answer.

Unlike ``harvest_schedule`` (scoped to a site, at most one project deep), a regression
schedule is scoped to one workbook -- §10.6's own re-run unit is "a site's suites," but
the suite that gets re-run is always the one already retained for a specific, already-
released report (spec §10.3's own per-workbook `ParitySuite`), so the schedule is keyed
the same way.

The ontology is unchanged: like a harvest schedule, a regression schedule is a platform
record, not an estate fact. `ExceptionCase.class` gains the ``REGRESSION`` value this
migration's own SCHEMA_VERSION bump (29→30) records -- an additive enum value, not a new
column, so no ``ALTER TABLE`` accompanies it (the identical shape `v0024`'s own
VISUAL_REDESIGN addition already established for a co-occurring ontology change).
"""

from __future__ import annotations

import asyncpg

VERSION = 29
DESCRIPTION = "Regression schedules (§10.6, S7.7.1)"

ONTOLOGY_CHANGES: list[dict[str, str]] = []


_SCHEDULE_DDL = """
CREATE TABLE IF NOT EXISTS public.regression_schedule (
    id                      text        PRIMARY KEY,
    graph                   text        NOT NULL,
    workbook_id             text        NOT NULL,
    workspace               text        NOT NULL DEFAULT 'dev',
    cadence                 jsonb       NOT NULL,
    enabled                 boolean     NOT NULL DEFAULT true,
    paused_reason           text,
    next_run_at             timestamptz NOT NULL,
    last_run_at             timestamptz,
    last_run_id             text,
    last_result             text,
    last_error              text,
    consecutive_failures    integer     NOT NULL DEFAULT 0,
    last_seen_drift_seq     bigint      NOT NULL DEFAULT 0,
    created_by              text        NOT NULL,
    created_at              timestamptz NOT NULL DEFAULT now(),
    updated_at              timestamptz NOT NULL DEFAULT now()
)
"""

_INDEXES = (
    # One schedule per workbook -- a second would race the first for the same suite and
    # double-open ExceptionCases on the same FAIL, the identical "one schedule per scope"
    # reasoning `v0006`'s own harvest_schedule_scope indexes already give.
    "CREATE UNIQUE INDEX IF NOT EXISTS regression_schedule_workbook_idx "
    "ON public.regression_schedule (graph, workbook_id)",
    # What the scheduler's poll reads, every poll interval, forever.
    "CREATE INDEX IF NOT EXISTS regression_schedule_due_idx "
    "ON public.regression_schedule (graph, next_run_at) WHERE enabled",
    # What the drift check reads every tick -- schedules whose own watermark is behind
    # the outbox's latest SOURCE_DRIFT sequence number.
    "CREATE INDEX IF NOT EXISTS regression_schedule_drift_idx "
    "ON public.regression_schedule (graph, last_seen_drift_seq) WHERE enabled",
)


async def up(conn: asyncpg.Connection) -> None:
    await conn.execute(_SCHEDULE_DDL)
    for statement in _INDEXES:
        await conn.execute(statement)
