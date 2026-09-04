"""Create the Estate Graph and its element index.

Establishes the Apache AGE graph, one vertex label per node type and one edge label per
edge type from the ontology registry, and the relational element index the write path
uses for id-to-label lookup and duplicate detection.

Labels are created up front rather than left to AGE's create-on-first-write so that each
label's storage exists before any traffic, and so a label that the ontology no longer
declares is visible as a table with no declaration behind it.
"""

from __future__ import annotations

import asyncpg

from ...config import settings
from ...ontology import EDGE_LABELS, NODE_LABELS, SCHEMA_VERSION

VERSION = 1
DESCRIPTION = f"Estate Graph, ontology schema version {SCHEMA_VERSION}"

#: The first migration establishes the schema; there is nothing to backfill from.
ONTOLOGY_CHANGES: list[dict[str, str]] = []


_ELEMENT_INDEX_DDL = """
CREATE TABLE IF NOT EXISTS public.estate_element_index (
    id          text        PRIMARY KEY,
    kind        text        NOT NULL CHECK (kind IN ('node', 'edge')),
    label       text        NOT NULL,
    created_at  timestamptz NOT NULL DEFAULT now()
)
"""

_ELEMENT_INDEX_LABEL_IDX = """
CREATE INDEX IF NOT EXISTS estate_element_index_label_idx
    ON public.estate_element_index (label, id)
"""

_ONTOLOGY_VERSION_DDL = """
CREATE TABLE IF NOT EXISTS public.ontology_schema_version (
    schema_version integer     PRIMARY KEY,
    applied_at     timestamptz NOT NULL DEFAULT now()
)
"""


async def up(conn: asyncpg.Connection) -> None:
    graph_name = settings().graph_name

    await conn.execute("CREATE EXTENSION IF NOT EXISTS age")
    await conn.execute("LOAD 'age'")
    # Deliberately no `SET search_path`: with ag_catalog first, an unqualified CREATE
    # TABLE lands in AGE's catalog schema. Every name below is qualified instead.

    graph_exists = await conn.fetchval(
        "SELECT EXISTS (SELECT 1 FROM ag_catalog.ag_graph WHERE name = $1)", graph_name
    )
    if not graph_exists:
        await conn.execute("SELECT ag_catalog.create_graph($1)", graph_name)

    existing = {
        row["name"]
        for row in await conn.fetch(
            """
            SELECT l.name
            FROM ag_catalog.ag_label l
            JOIN ag_catalog.ag_graph g ON g.graphid = l.graph
            WHERE g.name = $1
            """,
            graph_name,
        )
    }

    for label in sorted(NODE_LABELS):
        if label not in existing:
            await conn.execute("SELECT ag_catalog.create_vlabel($1, $2)", graph_name, label)
    for label in sorted(EDGE_LABELS):
        if label not in existing:
            await conn.execute("SELECT ag_catalog.create_elabel($1, $2)", graph_name, label)

    await conn.execute(_ELEMENT_INDEX_DDL)
    await conn.execute(_ELEMENT_INDEX_LABEL_IDX)
    await conn.execute(_ONTOLOGY_VERSION_DDL)
    await conn.execute(
        """
        INSERT INTO public.ontology_schema_version (schema_version) VALUES ($1)
        ON CONFLICT (schema_version) DO NOTHING
        """,
        SCHEMA_VERSION,
    )
