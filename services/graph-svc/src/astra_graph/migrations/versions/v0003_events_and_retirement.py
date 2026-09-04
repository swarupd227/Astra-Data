"""The mutation outbox, retirement, and graph-scoped index tables.

Three things:

* ``estate_event``, the transactional outbox every mutation writes into. It is not a
  cache of the bus, it is the record: an event committed with its mutation is what makes
  "a replay of the event stream from empty produces a graph identical to the live graph"
  a property of the system rather than a hope.
* ``retired_at`` on the element index, so a retired node can be excluded from reads
  without opening every node's properties.
* a ``graph`` column on both index tables, so a replay can rebuild the estate into a
  second AGE graph in the same database and be compared against the live one.

The ``graph`` column is a breaking change to the index tables — their primary key moves
from ``id`` to ``(graph, id)`` — so it carries a backfill. The ontology itself only gained
optional properties, which the guard classifies as additive.
"""

from __future__ import annotations

import asyncpg

from ...config import settings

VERSION = 3
DESCRIPTION = "Mutation outbox, node retirement, graph-scoped index tables"

#: The ontology gained retired_at, retired_by and retirement_reason as optional
#: server-managed properties. Optional additions need no backfill: a node written before
#: this migration is simply not retired, which is the correct reading of their absence.
ONTOLOGY_CHANGES: list[dict[str, str]] = []


_EVENT_TABLE_DDL = """
CREATE TABLE IF NOT EXISTS public.estate_event (
    seq           bigserial   PRIMARY KEY,
    event_id      text        NOT NULL UNIQUE,
    graph         text        NOT NULL,
    type          text        NOT NULL,
    source        text        NOT NULL,
    subject       text        NOT NULL,
    element_kind  text        NOT NULL CHECK (element_kind IN ('node', 'edge')),
    label         text        NOT NULL,
    time          timestamptz NOT NULL,
    principal     text        NOT NULL,
    run_id        text,
    data          jsonb       NOT NULL,
    published_at  timestamptz
)
"""

_EVENT_INDEXES = (
    "CREATE INDEX IF NOT EXISTS estate_event_graph_seq_idx ON public.estate_event (graph, seq)",
    "CREATE INDEX IF NOT EXISTS estate_event_subject_idx ON public.estate_event (subject, seq)",
    "CREATE INDEX IF NOT EXISTS estate_event_run_idx ON public.estate_event (run_id) "
    "WHERE run_id IS NOT NULL",
    # Where the E12 publisher picks up: everything not yet on the bus, in order.
    "CREATE INDEX IF NOT EXISTS estate_event_unpublished_idx ON public.estate_event (seq) "
    "WHERE published_at IS NULL",
)


async def up(conn: asyncpg.Connection) -> None:
    graph = settings().graph_name

    await conn.execute(_EVENT_TABLE_DDL)
    for statement in _EVENT_INDEXES:
        await conn.execute(statement)

    await _add_graph_column(conn, "estate_element_index", graph)
    await _add_graph_column(conn, "estate_edge_index", graph)

    await conn.execute(
        "ALTER TABLE public.estate_element_index ADD COLUMN IF NOT EXISTS retired_at timestamptz"
    )
    await conn.execute(
        "CREATE INDEX IF NOT EXISTS estate_element_index_live_idx "
        "ON public.estate_element_index (graph, kind, id) WHERE retired_at IS NULL"
    )


async def _add_graph_column(conn: asyncpg.Connection, table: str, graph: str) -> None:
    """Add the graph column, backfill it, and move the primary key onto it.

    Backfill: every row that exists was written by this deployment against its configured
    graph, because the column did not exist to hold anything else.
    """
    already = await conn.fetchval(
        """
        SELECT EXISTS (
            SELECT 1 FROM information_schema.columns
            WHERE table_schema = 'public' AND table_name = $1 AND column_name = 'graph'
        )
        """,
        table,
    )
    if already:
        return

    await conn.execute(f"ALTER TABLE public.{table} ADD COLUMN graph text")
    await conn.execute(f"UPDATE public.{table} SET graph = $1 WHERE graph IS NULL", graph)
    await conn.execute(f"ALTER TABLE public.{table} ALTER COLUMN graph SET NOT NULL")
    await conn.execute(f"ALTER TABLE public.{table} DROP CONSTRAINT IF EXISTS {table}_pkey")
    await conn.execute(f"ALTER TABLE public.{table} ADD PRIMARY KEY (graph, id)")
