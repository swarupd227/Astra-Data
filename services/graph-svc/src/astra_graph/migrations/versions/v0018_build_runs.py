"""Build attempts — story S4.3.1.

    "As a model engineer, I want an approved design built as TMDL and deployed to the dev
    workspace automatically, so that the model exists as code the moment it is approved."

**A platform table, the same footing as `g2_question` (v0016) and `g2_reminder` (v0017) —
not an estate-graph node.** A build attempt's own log (which step ran, whether it passed,
what it said) is not a fact about the source or target estate; §4.1's ontology has nothing
that models it, and — like the two tables before it — inventing a node for "a thing that
happened during a build" would be guessing at a graph shape nothing else needs.

**One row per attempt, not one row per family.** A family can be rebuilt (a retry after a
fix); each attempt is its own record, so the Build tab's own history is a real query
(`ORDER BY started_at DESC LIMIT 1` for "the current state", `WHERE family_id = ...` for
the rest) rather than something a single mutable row would silently overwrite.

**`steps` is one JSON column, not a normalised child table** — the exact `g2_question.
thread` precedent: a build's own step log is read and written as a whole every time (the
Build tab renders it entire), so a per-step table would only add a join for no query this
story performs.
"""

from __future__ import annotations

import asyncpg

VERSION = 18
DESCRIPTION = "Build attempts (public.build_run)"

ONTOLOGY_CHANGES: list[dict[str, str]] = []


_DDL = """
CREATE TABLE IF NOT EXISTS public.build_run (
    id                text        PRIMARY KEY,
    graph             text        NOT NULL,
    family_id         text        NOT NULL,
    version           text        NOT NULL,
    gate_decision_id  text,
    state             text        NOT NULL CHECK (state IN ('SUCCEEDED', 'FAILED')),
    steps             jsonb       NOT NULL DEFAULT '[]'::jsonb,
    git_commit_sha    text,
    git_ref           text,
    workspace         text,
    triggered_by      text        NOT NULL,
    started_at        timestamptz NOT NULL,
    finished_at       timestamptz NOT NULL
)
"""

_INDEXES = (
    "CREATE INDEX IF NOT EXISTS build_run_family_idx "
    "ON public.build_run (graph, family_id, started_at DESC)",
)


async def up(conn: asyncpg.Connection) -> None:
    await conn.execute(_DDL)
    for statement in _INDEXES:
        await conn.execute(statement)
