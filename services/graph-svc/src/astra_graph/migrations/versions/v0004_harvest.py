"""Harvest runs, per-workbook state and failures.

Platform records rather than estate facts, so they are relational — the same split the
specification makes in §21. Three tables:

* ``harvest_run`` — one row per run, with its progress document. Progress is persisted as
  it changes because S1.2.1 wants it visible *while* a run is in flight, and a run over a
  1,000-workbook site lasts hours.
* ``harvest_workbook`` — the content hash last seen for each workbook. This is what makes
  a re-harvest of an unchanged workbook a no-op (spec §8.4).
* ``harvest_failure`` — one row per workbook that could not be harvested, so the failures
  of a run can be listed with their errors without reading a log.

The ontology is unchanged; this migration claims no ontology changes.
"""

from __future__ import annotations

import asyncpg

VERSION = 4
DESCRIPTION = "Harvest runs, per-workbook state and failures"

ONTOLOGY_CHANGES: list[dict[str, str]] = []


_RUN_DDL = """
CREATE TABLE IF NOT EXISTS public.harvest_run (
    id           text        PRIMARY KEY,
    graph        text        NOT NULL,
    state        text        NOT NULL,
    scope        jsonb       NOT NULL,
    adapter      jsonb       NOT NULL,
    principal    text        NOT NULL,
    started_at   timestamptz,
    finished_at  timestamptz,
    progress     jsonb       NOT NULL,
    error        text,
    created_at   timestamptz NOT NULL DEFAULT now()
)
"""

_WORKBOOK_DDL = """
CREATE TABLE IF NOT EXISTS public.harvest_workbook (
    graph          text        NOT NULL,
    site           text        NOT NULL,
    workbook_luid  text        NOT NULL,
    workbook_name  text        NOT NULL,
    project        text        NOT NULL,
    revision       text        NOT NULL,
    content_hash   text        NOT NULL,
    outcome        text        NOT NULL,
    parse_quality  double precision,
    unrecognised   jsonb       NOT NULL DEFAULT '[]'::jsonb,
    harvested_at   timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (graph, site, workbook_luid)
)
"""

_FAILURE_DDL = """
CREATE TABLE IF NOT EXISTS public.harvest_failure (
    id             bigserial   PRIMARY KEY,
    harvest_id     text        NOT NULL REFERENCES public.harvest_run(id) ON DELETE CASCADE,
    workbook_luid  text        NOT NULL,
    workbook_name  text        NOT NULL,
    project        text        NOT NULL,
    stage          text        NOT NULL,
    error          text        NOT NULL,
    retryable      boolean     NOT NULL DEFAULT false,
    created_at     timestamptz NOT NULL DEFAULT now()
)
"""

_INDEXES = (
    "CREATE INDEX IF NOT EXISTS harvest_run_graph_idx "
    "ON public.harvest_run (graph, started_at DESC)",
    "CREATE INDEX IF NOT EXISTS harvest_run_active_idx "
    "ON public.harvest_run (graph) WHERE state IN ('QUEUED', 'RUNNING')",
    # The Parse Quality Queue (S1.4.3) reads this: workbooks under the threshold.
    "CREATE INDEX IF NOT EXISTS harvest_workbook_quality_idx "
    "ON public.harvest_workbook (graph, parse_quality) WHERE parse_quality IS NOT NULL",
    "CREATE INDEX IF NOT EXISTS harvest_failure_run_idx "
    "ON public.harvest_failure (harvest_id, id)",
)


async def up(conn: asyncpg.Connection) -> None:
    await conn.execute(_RUN_DDL)
    await conn.execute(_WORKBOOK_DDL)
    await conn.execute(_FAILURE_DDL)
    for statement in _INDEXES:
        await conn.execute(statement)
