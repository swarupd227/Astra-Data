"""Unrecognised constructs, and the counts a re-score needs.

S1.2.2. Two things:

* ``parse_construct`` — every construct the adapter grammar could not read, verbatim, with
  where it was found and whether an engineer has since accepted it. Relational rather than
  graph-shaped: an unrecognised construct is a fact about a parse, not about the estate,
  and the question asked of it — "which construct is holding up the most workbooks" — is a
  grouping the graph is the wrong shape for.
* the construct counts on ``harvest_workbook``, without which a score cannot be recomputed
  when a construct is accepted. Storing only the ratio would have made
  "re-scores the workbook without a full re-harvest" impossible.

The ontology also gained ``Workbook.parse_quality``, which is an optional property and so
additive — no backfill. A workbook harvested before this migration has no score until it
is next harvested, which is the correct reading of its absence.
"""

from __future__ import annotations

import asyncpg

VERSION = 5
DESCRIPTION = "Unrecognised constructs and parse-quality counts"

ONTOLOGY_CHANGES: list[dict[str, str]] = []


_CONSTRUCT_DDL = """
CREATE TABLE IF NOT EXISTS public.parse_construct (
    id                bigserial   PRIMARY KEY,
    graph             text        NOT NULL,
    site              text        NOT NULL,
    workbook_luid     text        NOT NULL,
    construct         text        NOT NULL,
    sheet             text,
    field             text,
    detail            text        NOT NULL DEFAULT '',
    unrecognised      boolean     NOT NULL DEFAULT true,
    ignorable_reason  text,
    decided_by        text,
    decided_at        timestamptz,
    grammar_version   text,
    created_at        timestamptz NOT NULL DEFAULT now()
)
"""

_COUNT_COLUMNS = (
    "ALTER TABLE public.harvest_workbook "
    "ADD COLUMN IF NOT EXISTS constructs_recognised integer NOT NULL DEFAULT 0",
    "ALTER TABLE public.harvest_workbook "
    "ADD COLUMN IF NOT EXISTS constructs_ignorable integer NOT NULL DEFAULT 0",
    "ALTER TABLE public.harvest_workbook "
    "ADD COLUMN IF NOT EXISTS constructs_total integer NOT NULL DEFAULT 0",
    "ALTER TABLE public.harvest_workbook ADD COLUMN IF NOT EXISTS grammar_version text",
)

_INDEXES = (
    # The queue is worked construct-first: one grammar gap blocks many workbooks.
    "CREATE INDEX IF NOT EXISTS parse_construct_group_idx "
    "ON public.parse_construct (graph, construct) WHERE unrecognised",
    "CREATE INDEX IF NOT EXISTS parse_construct_workbook_idx "
    "ON public.parse_construct (graph, site, workbook_luid)",
)


async def up(conn: asyncpg.Connection) -> None:
    await conn.execute(_CONSTRUCT_DDL)
    for statement in _COUNT_COLUMNS:
        await conn.execute(statement)
    for statement in _INDEXES:
        await conn.execute(statement)
