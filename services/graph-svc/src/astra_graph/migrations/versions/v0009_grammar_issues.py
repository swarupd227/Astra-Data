"""Grammar issues: a construct the adapter cannot read, raised as work.

S1.4.3's second action — "open grammar issue (creates a ticket with the construct text and
locations)".

**Why a table rather than a call to a tracker.** §21's integration table lists work tracking
as *optional*, one-way, and Azure DevOps or Jira "for clients who require it", and the
mirror lands in R1.1. So the issue is a platform record first: the platform must be able to
say what grammar gaps are open, what each is holding up and who raised it, on a deployment
with no tracker at all. Where a client has one, E12 mirrors these rows into it and fills
``external_ref``.

**Why the locations are a snapshot.** The estate moves — a re-harvest can re-parse the
workbooks a construct was found in, and a later grammar version can stop producing it at
all. An issue that resolved its locations live would, months later, describe wherever the
construct happens to be *now* rather than the evidence it was raised on. So the construct
text, its locations and the workbook counts are copied in at the moment the issue is
opened, and the live figures stay on the queue where they belong.
"""

from __future__ import annotations

import asyncpg

VERSION = 9
DESCRIPTION = "Grammar issues raised from the Parse Quality Queue"

ONTOLOGY_CHANGES: list[dict[str, str]] = []


_DDL = """
CREATE TABLE IF NOT EXISTS public.grammar_issue (
    id               text        PRIMARY KEY,
    graph            text        NOT NULL,
    construct        text        NOT NULL,
    adapter          text,
    grammar_version  text,
    state            text        NOT NULL
                     CHECK (state IN ('OPEN', 'IN_PROGRESS', 'RESOLVED', 'WONT_FIX')),
    summary          text        NOT NULL,
    detail           text        NOT NULL CHECK (length(btrim(detail)) >= 10),
    locations        jsonb       NOT NULL DEFAULT '[]'::jsonb,
    occurrences      integer     NOT NULL DEFAULT 0,
    workbooks_held   integer     NOT NULL DEFAULT 0,
    external_ref     text,
    external_url     text,
    opened_by        text        NOT NULL,
    opened_at        timestamptz NOT NULL DEFAULT now(),
    resolved_by      text,
    resolved_at      timestamptz,
    resolution       text
)
"""

_INDEXES = (
    # The queue renders one row per construct and needs to know, for each, whether an issue
    # is already open — so this is read once per screen, keyed by construct.
    "CREATE INDEX IF NOT EXISTS grammar_issue_construct_idx "
    "ON public.grammar_issue (graph, construct, opened_at DESC)",
    # One open issue per construct. A second is not a second problem, it is two people
    # raising the same one, and the queue would then show a construct as blocked twice.
    "CREATE UNIQUE INDEX IF NOT EXISTS grammar_issue_one_open_idx "
    "ON public.grammar_issue (graph, construct) WHERE state IN ('OPEN', 'IN_PROGRESS')",
    "CREATE INDEX IF NOT EXISTS grammar_issue_state_idx "
    "ON public.grammar_issue (graph, state, opened_at DESC)",
)


async def up(conn: asyncpg.Connection) -> None:
    await conn.execute(_DDL)
    for statement in _INDEXES:
        await conn.execute(statement)
