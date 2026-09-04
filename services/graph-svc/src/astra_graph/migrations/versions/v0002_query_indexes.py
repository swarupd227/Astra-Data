"""Indexes and the adjacency index that make the query API meet its latency budget.

Three things:

* a btree index on every label's ``id``, and on ``luid`` for the labels that declare one,
  so a node lookup is an index scan rather than a scan of the label table;
* ``estate_edge_index``, the relational adjacency the neighbourhood traversal walks
  (rationale and measurements in ``graph/queries.py`` and ADR 0002);
* a backfill of that index from any edges already in the graph, so an estate harvested
  under schema version 1 is traversable without a re-harvest.

The ontology is unchanged, so this migration claims no ontology changes.
"""

from __future__ import annotations

import asyncpg

from ...config import settings
from ...graph.queries import accessor, index_ddl
from ...ontology import EDGE_LABELS, NODE_LABELS, node_type

VERSION = 2
DESCRIPTION = "Property indexes and the edge adjacency index for the query API"

#: No node or edge type changed, and no property was removed or made required.
ONTOLOGY_CHANGES: list[dict[str, str]] = []


_EDGE_INDEX_DDL = """
CREATE TABLE IF NOT EXISTS public.estate_edge_index (
    id          text        PRIMARY KEY,
    label       text        NOT NULL,
    from_id     text        NOT NULL,
    to_id       text        NOT NULL,
    created_at  timestamptz NOT NULL DEFAULT now()
)
"""

_EDGE_INDEX_INDEXES = (
    "CREATE INDEX IF NOT EXISTS estate_edge_index_from_idx ON public.estate_edge_index (from_id)",
    "CREATE INDEX IF NOT EXISTS estate_edge_index_to_idx ON public.estate_edge_index (to_id)",
    "CREATE INDEX IF NOT EXISTS estate_edge_index_label_idx ON public.estate_edge_index (label)",
)


async def up(conn: asyncpg.Connection) -> None:
    graph = settings().graph_name

    await conn.execute("LOAD 'age'")

    # Property indexes. `id` on every label; `luid` only where the ontology declares it,
    # since that is the identifier a caller has when coming from the source system.
    for label in sorted(NODE_LABELS):
        await conn.execute(index_ddl(graph, label, "id"))
        declared = node_type(label)
        if declared is not None and "luid" in declared.declared_property_names:
            await conn.execute(index_ddl(graph, label, "luid"))
    for label in sorted(EDGE_LABELS):
        await conn.execute(index_ddl(graph, label, "id"))

    await conn.execute(_EDGE_INDEX_DDL)
    for statement in _EDGE_INDEX_INDEXES:
        await conn.execute(statement)

    await _backfill_edge_index(conn, graph)


async def _backfill_edge_index(conn: asyncpg.Connection, graph: str) -> None:
    """Populate the adjacency index from edges already in the graph.

    Reads each edge label's table, resolving the AGE-internal endpoint identifiers
    (``start_id`` / ``end_id``) to the platform's own ``id`` property, which is what the
    traversal joins on. Idempotent: a re-run inserts nothing new.
    """
    # Comparing two graphid values needs AGE's operators visible, and an operator cannot
    # be schema-qualified inside an expression. SET LOCAL keeps this to the migration's
    # own transaction; every table and type below stays qualified regardless.
    await conn.execute("SET LOCAL search_path = ag_catalog, public")
    id_of = accessor("id")
    for label in sorted(EDGE_LABELS):
        await conn.execute(
            f"""
            INSERT INTO public.estate_edge_index (id, label, from_id, to_id)
            SELECT
                trim(both '"' from ({id_of.replace('properties', 'e.properties')})::text),
                '{label}',
                trim(both '"' from (s.id_value)::text),
                trim(both '"' from (t.id_value)::text)
            FROM {graph}."{label}" e
            JOIN (
                {_all_vertices(graph)}
            ) s ON s.graph_id = e.start_id
            JOIN (
                {_all_vertices(graph)}
            ) t ON t.graph_id = e.end_id
            WHERE ({id_of.replace('properties', 'e.properties')}) IS NOT NULL
            ON CONFLICT (id) DO NOTHING
            """
        )


def _all_vertices(graph: str) -> str:
    """Every vertex as (graph_id, id_value), for resolving AGE endpoint identifiers."""
    id_of = accessor("id")
    return " UNION ALL ".join(
        f'SELECT id AS graph_id, {id_of} AS id_value FROM {graph}."{label}"'
        for label in sorted(NODE_LABELS)
    )
