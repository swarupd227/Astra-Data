"""A scratch Apache AGE graph to replay the event stream into.

Extracted from ``tools/verify_replay.py`` (the nightly CI job S1.1.3 already promises)
so that job and story S10.1.2's own console-triggered rebuild call the identical code to
prepare and tear down the target graph — one implementation, not two that could quietly
diverge on what "an empty graph with the same shape as a migrated one" means.
"""

from __future__ import annotations

import asyncpg

from ..ontology import EDGE_LABELS, NODE_LABELS
from .queries import accessor


async def prepare_scratch_graph(conn: asyncpg.Connection, graph: str) -> None:
    """An empty graph with the same labels and indexes as a migrated one."""
    await conn.execute("LOAD 'age'")
    await conn.execute('SET search_path = ag_catalog, "$user", public')
    exists = await conn.fetchval(
        "SELECT EXISTS (SELECT 1 FROM ag_catalog.ag_graph WHERE name = $1)", graph
    )
    if exists:
        await conn.execute("SELECT ag_catalog.drop_graph($1, true)", graph)
    await conn.execute("SELECT ag_catalog.create_graph($1)", graph)

    for label in sorted(NODE_LABELS):
        await conn.execute("SELECT ag_catalog.create_vlabel($1, $2)", graph, label)
        await conn.execute(f'CREATE INDEX ON {graph}."{label}" USING BTREE ({accessor("id")})')
    for label in sorted(EDGE_LABELS):
        await conn.execute("SELECT ag_catalog.create_elabel($1, $2)", graph, label)
        await conn.execute(f'CREATE INDEX ON {graph}."{label}" USING BTREE ({accessor("id")})')

    await drop_scratch_index_rows(conn, graph)


async def drop_scratch_index_rows(conn: asyncpg.Connection, graph: str) -> None:
    await conn.execute("DELETE FROM public.estate_edge_index WHERE graph = $1", graph)
    await conn.execute("DELETE FROM public.estate_element_index WHERE graph = $1", graph)


async def teardown_scratch_graph(conn: asyncpg.Connection, graph: str) -> None:
    await conn.execute("LOAD 'age'")
    await drop_scratch_index_rows(conn, graph)
    await conn.execute("SELECT ag_catalog.drop_graph($1, true)", graph)
