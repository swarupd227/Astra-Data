"""Making sure the configured graph actually exists.

Migration state is recorded once per database, but the Apache AGE graph, its labels and
its property indexes are per *graph*. Those two facts do not compose: point a deployment
at a graph name it has not used before and the migrations are all "already applied", so
nothing creates the graph. The service then starts, health answers 503, and every write
fails with "graph does not exist".

That is exactly what happened the first time a second graph name was configured, and it
is why provisioning is reconciled rather than migrated. This runs on every ``migrate``
invocation, is idempotent, and creates only what is missing — so a new graph name works,
and an existing one is untouched.

Labels and indexes are reconciled too, not just the graph: an ontology change adds node
types, and a graph provisioned before that change would otherwise be missing their
storage.
"""

from __future__ import annotations

import logging

import asyncpg

from .graph.queries import accessor, index_ddl
from .ontology import EDGE_LABELS, NODE_LABELS, node_type

logger = logging.getLogger(__name__)


async def graph_exists(conn: asyncpg.Connection, graph: str) -> bool:
    await conn.execute("LOAD 'age'")
    return bool(
        await conn.fetchval(
            "SELECT EXISTS (SELECT 1 FROM ag_catalog.ag_graph WHERE name = $1)", graph
        )
    )


async def existing_labels(conn: asyncpg.Connection, graph: str) -> set[str]:
    rows = await conn.fetch(
        """
        SELECT l.name
        FROM ag_catalog.ag_label l
        JOIN ag_catalog.ag_graph g ON g.graphid = l.graph
        WHERE g.name = $1
        """,
        graph,
    )
    return {row["name"] for row in rows}


async def ensure_graph(conn: asyncpg.Connection, graph: str) -> dict[str, int]:
    """Create the graph, its labels and its property indexes if they are missing.

    Returns what it had to create, so ``migrate`` can say whether it did anything.
    """
    await conn.execute("CREATE EXTENSION IF NOT EXISTS age")
    await conn.execute("LOAD 'age'")

    created_graph = 0
    if not await graph_exists(conn, graph):
        await conn.execute("SELECT ag_catalog.create_graph($1)", graph)
        created_graph = 1
        logger.info("created graph %s", graph)

    present = await existing_labels(conn, graph)
    created_labels = 0

    for label in sorted(NODE_LABELS):
        if label not in present:
            await conn.execute("SELECT ag_catalog.create_vlabel($1, $2)", graph, label)
            created_labels += 1
    for label in sorted(EDGE_LABELS):
        if label not in present:
            await conn.execute("SELECT ag_catalog.create_elabel($1, $2)", graph, label)
            created_labels += 1

    # Index DDL is IF NOT EXISTS, so this is cheap to re-run and repairs a graph whose
    # indexes were never built.
    created_indexes = 0
    for label in sorted(NODE_LABELS):
        await conn.execute(index_ddl(graph, label, "id"))
        created_indexes += 1
        declared = node_type(label)
        if declared is not None and "luid" in declared.declared_property_names:
            await conn.execute(index_ddl(graph, label, "luid"))
            created_indexes += 1
    for label in sorted(EDGE_LABELS):
        await conn.execute(index_ddl(graph, label, "id"))
        created_indexes += 1

    return {
        "graph_created": created_graph,
        "labels_created": created_labels,
        "indexes_ensured": created_indexes,
    }


__all__ = ["accessor", "ensure_graph", "existing_labels", "graph_exists"]
