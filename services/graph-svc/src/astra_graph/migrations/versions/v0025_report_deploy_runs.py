"""Report deploy attempts -- story S6.1.2.

    "I want the report definition committed to Git and deployed to the dev workspace bound
    to the family model, so that I can open the generated report in Fabric within minutes
    of generation."

**A platform table, the identical footing `build_run` (v0018, S4.3.1) already set** -- a
deploy attempt's own log (which step ran, whether it passed, what it said) is not a fact
about the source or target estate; §4.1's ontology has nothing that models it, and this
codebase's own established answer for "a thing that happened during an action" is a
platform table, not a graph node.

**One row per attempt, keyed by workbook id, not report id.** A workbook can be recomposed
(S6.1.1's own "a re-compose replaces the workbook's whole report"), which retires the old
`ReportDefinition` and writes a new one under a fresh id -- keying deploy history by
`report_id` alone would silently orphan every earlier deploy attempt's own history the
moment a recompose ran. `workbook_id` is what a Migration Unit actually is (`Workbook`'s own
§4.1.1 note: "One Migration Unit per Workbook"), so it is what "the most recent deploy for
this MU" should mean regardless of which report id happened to produce it.

**`steps`/`attempts` mirror `build_run`'s own shape exactly** -- one JSON column for the
step log (read and written whole, the Build tab's own precedent), a plain `attempts`
integer for "three retries with backoff" to be visible without parsing the step log.
"""

from __future__ import annotations

import asyncpg

VERSION = 25
DESCRIPTION = "Report deploy attempts (public.report_deploy_run)"

ONTOLOGY_CHANGES: list[dict[str, str]] = []


_DDL = """
CREATE TABLE IF NOT EXISTS public.report_deploy_run (
    id              text        PRIMARY KEY,
    graph           text        NOT NULL,
    report_id       text        NOT NULL,
    workbook_id     text        NOT NULL,
    state           text        NOT NULL CHECK (state IN ('SUCCEEDED', 'FAILED')),
    steps           jsonb       NOT NULL DEFAULT '[]'::jsonb,
    git_commit_sha  text,
    git_ref         text,
    workspace       text,
    attempts        int         NOT NULL DEFAULT 0,
    triggered_by    text        NOT NULL,
    started_at      timestamptz NOT NULL,
    finished_at     timestamptz NOT NULL
)
"""

_INDEXES = (
    "CREATE INDEX IF NOT EXISTS report_deploy_run_workbook_idx "
    "ON public.report_deploy_run (graph, workbook_id, started_at DESC)",
)


async def up(conn: asyncpg.Connection) -> None:
    await conn.execute(_DDL)
    for statement in _INDEXES:
        await conn.execute(statement)
