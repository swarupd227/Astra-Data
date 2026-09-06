"""The visual-mapping ruleset -- story S6.1.1.

    "Mapping table from Appendix B (mark type x encodings -> visual type) is data,
    versioned, and editable by the architect."

A platform table, the identical footing `conformance_ruleset` (S4.3.2, migration v0019)
already set -- an architect's edit is a new version, never an overwrite. `mappings` is one
`jsonb` column (a short, fixed-size list of mark-type mapping rows, read and written whole
every time) rather than a normalised per-row table, for the same "compound thing, no
sub-query need" reason `conformance_ruleset`/`g2_question`/`g2_reminder`/`build_run` already
each made.

No ontology change here: `Visual.layout` (also added by this story) is additive and needs
no migration entry of its own, the identical footing `ModelFamily.conformance_ruleset_version`
had alongside v0019.
"""

from __future__ import annotations

import asyncpg

VERSION = 24
DESCRIPTION = "The visual-mapping ruleset (public.visual_mapping_ruleset)"

ONTOLOGY_CHANGES: list[dict[str, str]] = []


_DDL = """
CREATE TABLE IF NOT EXISTS public.visual_mapping_ruleset (
    id           text        PRIMARY KEY,
    graph        text        NOT NULL,
    version      int         NOT NULL,
    mappings     jsonb       NOT NULL,
    updated_by   text        NOT NULL,
    updated_at   timestamptz NOT NULL DEFAULT now(),
    UNIQUE (graph, version)
)
"""

_INDEXES = (
    "CREATE INDEX IF NOT EXISTS visual_mapping_ruleset_latest_idx "
    "ON public.visual_mapping_ruleset (graph, version DESC)",
)


async def up(conn: asyncpg.Connection) -> None:
    await conn.execute(_DDL)
    for statement in _INDEXES:
        await conn.execute(statement)
