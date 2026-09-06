"""The Tolerance Charter — story S7.1.1.

    "The Tolerance Charter as a versioned document the platform enforces... versions are
    immutable."

A platform table, the identical footing `conformance_ruleset` (v0019) and
`visual_mapping_ruleset` (v0024) already established: a parity engineer's edit is a new
version, never an overwrite. `charter` is one `jsonb` column (the whole nine-block
document, read and written whole every time) rather than nine normalised tables — the
same "compound thing, no sub-query need" reasoning those two priors already gave.

No ontology change here: §4.4 itself says the charter is "stored in Git," and neither of
this table's own two precedents ever wrote to Git either — the same, already-accepted gap.
A charter version is bookkeeping about rules, not a fact about the source or target
estate, so §4.1.1's own node table declares no `ToleranceCharter` node, and this migration
adds none.
"""

from __future__ import annotations

import asyncpg

VERSION = 26
DESCRIPTION = "The Tolerance Charter (public.tolerance_charter_version)"

ONTOLOGY_CHANGES: list[dict[str, str]] = []


_DDL = """
CREATE TABLE IF NOT EXISTS public.tolerance_charter_version (
    id           text        PRIMARY KEY,
    graph        text        NOT NULL,
    version      int         NOT NULL,
    charter      jsonb       NOT NULL,
    updated_by   text        NOT NULL,
    updated_at   timestamptz NOT NULL DEFAULT now(),
    UNIQUE (graph, version)
)
"""

_INDEXES = (
    "CREATE INDEX IF NOT EXISTS tolerance_charter_version_latest_idx "
    "ON public.tolerance_charter_version (graph, version DESC)",
)


async def up(conn: asyncpg.Connection) -> None:
    await conn.execute(_DDL)
    for statement in _INDEXES:
        await conn.execute(statement)
