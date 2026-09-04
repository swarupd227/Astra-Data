"""Edges can be retired — story S3.1.2.

    "Move (member → other family)... re-computes the family's grain and dimensions."

A move needs the workbook's *old* `IN_FAMILY` edge gone from the working graph and a new
one in its place. Cypher edges are immutable once created — a property update cannot change
an edge's endpoints — so "move" is unavoidably "retire the old edge, create a new one", the
same shape S1.1.3 already gave nodes: nothing is deleted, a retired element stays in the
record and is excluded from ordinary reads.

**Not a new idea.** `BASE_EDGE_PROPERTIES.id`'s own note, written at S1.1.1, already says an
edge id exists "so an edge can be addressed and superseded" — retirement is that seam,
finally used.

`estate_edge_index` gets its own `retired_at` column (denormalised from
`estate_element_index`, updated in the same transaction) rather than requiring every
adjacency query to join against the element index to find out — the same reasoning
`estate_edge_index` itself was built on (v0002): the adjacency table exists so a traversal
does not pay a join for a fact used on every hop.

No ontology change: `retired_at`/`retired_by`/`retirement_reason` join `BASE_EDGE_PROPERTIES`
in code (a platform-managed base property, the same as the equivalent trio on
`BASE_NODE_PROPERTIES` — never a per-type declaration, so `ontology_check.py --spec` does not
see it and there is nothing to declare a deviation against).
"""

from __future__ import annotations

import asyncpg

VERSION = 13
DESCRIPTION = "Edges can be retired, the same as nodes"

ONTOLOGY_CHANGES: list[dict[str, str]] = []


async def up(conn: asyncpg.Connection) -> None:
    await conn.execute(
        "ALTER TABLE public.estate_edge_index ADD COLUMN IF NOT EXISTS retired_at timestamptz"
    )
    await conn.execute(
        "CREATE INDEX IF NOT EXISTS estate_edge_index_live_idx "
        "ON public.estate_edge_index (graph, id) WHERE retired_at IS NULL"
    )
