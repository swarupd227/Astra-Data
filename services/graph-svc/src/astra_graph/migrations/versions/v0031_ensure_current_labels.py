"""Reconcile AGE vertex/edge labels against the current ontology -- discovered by
story S8.2.1's own new `MenderPass` node type, closing a real, latent gap this migration
system has had since `v0001`.

**The gap.** `v0001_estate_graph.py` is the only migration that has ever called
``ag_catalog.create_vlabel``/``create_elabel`` -- every purely additive ontology change
since (a new node type, a new edge type) has landed with no accompanying migration at all,
on the stated, correct reasoning that an additive change needs no backfill (`v0029`'s own
docstring says this explicitly for its own `ExceptionCase.class` enum addition). That
reasoning holds for a *property* or *enum value* -- AGE has no schema to alter for either.
It does not hold for a whole new node/edge type: AGE only recognises a label that has been
explicitly created with ``create_vlabel``/``create_elabel``, and the migration runner
(`migrations/__init__.py`) applies each numbered migration exactly once, so a graph that
was already past `v0001` before a new node type existed never runs the block that would
have created its label -- confirmed directly: `test_integration_age.py::test_migration_
creates_the_graph_and_every_label` failed for real against this repository's own long-lived
local `astra_estate_test` graph the moment `MenderPass` (S8.2.1) existed, with `MenderPass`
the *only* missing label, every other node/edge type already present from whichever earlier
point this same graph was last created fresh.

**The fix, and why it is a new migration rather than a fix to `v0001`.** Editing `v0001`
itself would not help: it is already marked applied on every graph created before today, so
it would never run again there either. A fresh, idempotent migration -- the identical
"create only what is missing" check `v0001` already performs, just re-run once more here --
reconciles any graph already past `v0001`, and is a genuine no-op (nothing to create) on a
graph that was created fresh after this migration existed. This is also the honest,
permanent answer for a real production deployment: an in-place schema upgrade that adds a
node type needs its label created against the *existing* graph exactly the way this
migration now does it, not only against a freshly-created one.
"""

from __future__ import annotations

import asyncpg

from ...ontology import EDGE_LABELS, NODE_LABELS

VERSION = 31
DESCRIPTION = "Reconcile AGE labels against the current ontology (closes a v0001-only gap)"

#: Purely mechanical -- creates a label that already exists in the ontology but not yet in
#: this specific graph. No data changes, so nothing to backfill.
ONTOLOGY_CHANGES: list[dict[str, str]] = []


async def up(conn: asyncpg.Connection) -> None:
    await conn.execute("LOAD 'age'")
    # Every graph this Postgres instance has ever created, from `ag_catalog.ag_graph`
    # itself (the same source `v0001` reads from) -- not only the currently-configured
    # `graph_name`, since a shared Postgres instance can hold more than one.
    graph_names = {
        row["name"] for row in await conn.fetch("SELECT name FROM ag_catalog.ag_graph")
    }
    for graph in sorted(graph_names):
        existing = {
            row["name"]
            for row in await conn.fetch(
                """
                SELECT l.name
                FROM ag_catalog.ag_label l
                JOIN ag_catalog.ag_graph g ON g.graphid = l.graph
                WHERE g.name = $1
                """,
                graph,
            )
        }
        for label in sorted(NODE_LABELS):
            if label not in existing:
                await conn.execute("SELECT ag_catalog.create_vlabel($1, $2)", graph, label)
        for label in sorted(EDGE_LABELS):
            if label not in existing:
                await conn.execute("SELECT ag_catalog.create_elabel($1, $2)", graph, label)
