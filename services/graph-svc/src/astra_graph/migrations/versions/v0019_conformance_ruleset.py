"""The conformance ruleset — story S4.3.2.

    "Rules are data, editable by the architect in Admin, versioned, and recorded on the
    ModelFamily at build."

**A platform table, the same footing as `g2_question`/`g2_reminder`/`build_run`** — an
architect's edit is a new version, never an overwrite. `rules` is one `jsonb` column (a
short, fixed-size list of six rule configs, read and written whole every time — no
per-rule query this story performs, the same "compound thing, no sub-query need" reasoning
those three tables already established) rather than a normalised per-rule table.

No ontology change here beyond `ModelFamily.conformance_ruleset_version` (additive, no
migration entry needed for it) — a ruleset version is bookkeeping about *rules*, not a fact
about the source or target estate.
"""

from __future__ import annotations

import asyncpg

VERSION = 19
DESCRIPTION = "The conformance ruleset (public.conformance_ruleset)"

ONTOLOGY_CHANGES: list[dict[str, str]] = []


_DDL = """
CREATE TABLE IF NOT EXISTS public.conformance_ruleset (
    id           text        PRIMARY KEY,
    graph        text        NOT NULL,
    version      int         NOT NULL,
    rules        jsonb       NOT NULL,
    updated_by   text        NOT NULL,
    updated_at   timestamptz NOT NULL DEFAULT now(),
    UNIQUE (graph, version)
)
"""

_INDEXES = (
    "CREATE INDEX IF NOT EXISTS conformance_ruleset_latest_idx "
    "ON public.conformance_ruleset (graph, version DESC)",
)


async def up(conn: asyncpg.Connection) -> None:
    await conn.execute(_DDL)
    for statement in _INDEXES:
        await conn.execute(statement)
