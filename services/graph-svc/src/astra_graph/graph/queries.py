"""SQL for reading the Estate Graph.

Writes go through ``cypher()`` so Apache AGE owns identity and edge linkage. Reads take a
different route, and the reason is measured rather than assumed. On a seeded
1,000-workbook estate (56,000 nodes, 78,000 edges) a depth-3 neighbourhood cost:

    AGE `MATCH (n)-[*1..3]-(m)`                    p50 2918 ms   p95 3252 ms
    relational BFS + `cypher() ... WHERE id IN`    p50  196 ms   p95  248 ms
    relational BFS + direct indexed read           p50    6 ms   p95   10 ms

The budget is 300 ms p95 (S1.1.2, and NFR N3). Two things make the difference:

* **Traversal.** A variable-length AGE path unions every edge label at every hop. The
  adjacency index in ``estate_edge_index`` turns the same question into one recursive CTE
  over a btree.
* **Hydration.** ``WHERE n.id IN $ids`` inside ``cypher()`` does not reach the property
  index; the identical predicate written against the label table does, which EXPLAIN
  confirms as an Index Scan.

The relational indexes carry a ``graph`` column so several AGE graphs can share one
database — which is how the nightly replay rebuilds the estate alongside the live one
and compares the two.

The cost is that these reads know AGE's storage layout: a label is a table of
``(id, properties)`` under the graph's schema, and a property is extracted with
``agtype_access_operator``. That layout is already a dependency — migration 0001 creates
those tables through ``create_vlabel`` — and it is confined to this module. The
integration suite writes through ``cypher()`` and reads back through here, so a change in
AGE's layout fails a test rather than corrupting a result.
"""

from __future__ import annotations

import json
from typing import Any

#: AGE's accessor for a top-level property, and the expression the property indexes are
#: built on. Both must stay identical or the index stops being used.
# Every type is schema-qualified: migrations run on a connection that has not set a
# search_path, and the expression must be textually identical to the one the index was
# built on or PostgreSQL will not use the index.
PROPERTY_ACCESSOR = (
    "ag_catalog.agtype_access_operator("
    "VARIADIC ARRAY[properties, {key}::ag_catalog.agtype])"
)

NODE_INDEX_TABLE = "public.estate_element_index"
EDGE_INDEX_TABLE = "public.estate_edge_index"


def accessor(property_name: str) -> str:
    """Index-matching expression that extracts ``property_name`` from an AGE row."""
    # The property name comes from the ontology registry, never from a caller.
    return PROPERTY_ACCESSOR.format(key=f"'{json.dumps(property_name)}'")


def agtype_literal(value: str) -> str:
    """A string rendered as an agtype scalar, for comparison against the accessor."""
    return json.dumps(value)


def index_ddl(graph: str, label: str, property_name: str) -> str:
    """A btree index over one property of one label."""
    safe = property_name.replace("_", "")
    return (
        f'CREATE INDEX IF NOT EXISTS "idx_{label}_{safe}" '
        f'ON {graph}."{label}" USING BTREE ({accessor(property_name)})'
    )


def read_by_property(graph: str, label: str, property_name: str, *, parameter: int = 1) -> str:
    """Fetch whole rows of ``label`` whose ``property_name`` is in a parameter array."""
    return (
        f"SELECT properties::text AS properties "
        f'FROM {graph}."{label}" '
        f"WHERE {accessor(property_name)} = ANY(${parameter}::ag_catalog.agtype[])"
    )


def hydrate_nodes(graph: str, labels: list[str]) -> str:
    """One query fetching rows for several labels, each with its own id array parameter.

    One round trip rather than one per label: at depth 3 a neighbourhood typically spans
    five or six labels, and the round trips dominated the measurement.
    """
    return " UNION ALL ".join(
        f"SELECT '{label}' AS label, properties::text AS properties "
        f'FROM {graph}."{label}" '
        f"WHERE {accessor('id')} = ANY(${position + 1}::ag_catalog.agtype[])"
        for position, label in enumerate(labels)
    )


#: Breadth-first traversal over the adjacency index, undirected, bounded by depth.
#:
#: `UNION` (not `UNION ALL`) deduplicates the working set, which is what stops a cyclic
#: estate — two workbooks sharing a datasource, say — from expanding forever. The outer
#: aggregation keeps the shortest path to each node, so `depth` is the hop count a reader
#: expects rather than whichever route the planner happened to walk first.
#:
#: Retired nodes are excluded unless $5 is true: a node retired out of the estate should
#: not appear in a neighbourhood merely because it is still in the record (S1.1.3).
NEIGHBOURHOOD_SQL = f"""
WITH RECURSIVE reach(id, depth) AS (
        SELECT $1::text, 0
    UNION
        SELECT CASE WHEN e.from_id = r.id THEN e.to_id ELSE e.from_id END, r.depth + 1
        FROM reach r
        JOIN {EDGE_INDEX_TABLE} e
          ON (e.from_id = r.id OR e.to_id = r.id) AND e.graph = $4
        WHERE r.depth < $2
), hops AS (
    SELECT id, min(depth) AS depth FROM reach GROUP BY id
)
SELECT h.id, h.depth, n.label
FROM hops h
JOIN {NODE_INDEX_TABLE} n ON n.id = h.id AND n.kind = 'node' AND n.graph = $4
WHERE ($5 OR n.retired_at IS NULL)
ORDER BY h.depth, h.id
LIMIT $3
"""

#: The same traversal, restricted to a set of edge labels.
NEIGHBOURHOOD_FILTERED_SQL = f"""
WITH RECURSIVE reach(id, depth) AS (
        SELECT $1::text, 0
    UNION
        SELECT CASE WHEN e.from_id = r.id THEN e.to_id ELSE e.from_id END, r.depth + 1
        FROM reach r
        JOIN {EDGE_INDEX_TABLE} e
          ON (e.from_id = r.id OR e.to_id = r.id)
         AND e.graph = $4
         AND e.label = ANY($6::text[])
        WHERE r.depth < $2
), hops AS (
    SELECT id, min(depth) AS depth FROM reach GROUP BY id
)
SELECT h.id, h.depth, n.label
FROM hops h
JOIN {NODE_INDEX_TABLE} n ON n.id = h.id AND n.kind = 'node' AND n.graph = $4
WHERE ($5 OR n.retired_at IS NULL)
ORDER BY h.depth, h.id
LIMIT $3
"""

#: Edges wholly inside a node set — the edges of a neighbourhood, not those leaving it.
EDGES_WITHIN_SQL = f"""
SELECT id, label, from_id, to_id
FROM {EDGE_INDEX_TABLE}
WHERE graph = $3 AND from_id = ANY($1::text[]) AND to_id = ANY($1::text[])
ORDER BY id
LIMIT $2
"""

#: Outgoing edges of one type, with their endpoints, so the caller can hydrate the edge
#: itself rather than only what it points at. The Transpiler contract needs this: a source
#: field's target column is a property of the MAPS_TO edge, not of either endpoint
#: (spec §4.1.2, and the declared deviation for MAPS_TO.target_column).
OUTGOING_EDGES_SQL = f"""
SELECT e.id, e.label, e.from_id, e.to_id
FROM {EDGE_INDEX_TABLE} e
JOIN {NODE_INDEX_TABLE} n ON n.id = e.to_id AND n.kind = 'node' AND n.graph = $3
WHERE e.graph = $3 AND e.from_id = ANY($1::text[]) AND e.label = $2
  AND n.retired_at IS NULL
ORDER BY e.id
"""

#: Neighbours across one named edge type, in one direction. Used by the context contracts.
DIRECTED_STEP_SQL = f"""
SELECT e.to_id AS id, n.label
FROM {EDGE_INDEX_TABLE} e
JOIN {NODE_INDEX_TABLE} n ON n.id = e.to_id AND n.kind = 'node' AND n.graph = $3
WHERE e.graph = $3 AND e.from_id = ANY($1::text[]) AND e.label = $2
  AND n.retired_at IS NULL
"""

#: Transitive closure across one edge type, following it forwards only.
CLOSURE_SQL = f"""
WITH RECURSIVE closure(id, depth) AS (
        SELECT $1::text, 0
    UNION
        SELECT e.to_id, c.depth + 1
        FROM closure c
        JOIN {EDGE_INDEX_TABLE} e
          ON e.from_id = c.id AND e.label = $2 AND e.graph = $5
        WHERE c.depth < $3
), hops AS (
    SELECT id, min(depth) AS depth FROM closure GROUP BY id
)
SELECT h.id, h.depth, n.label
FROM hops h
JOIN {NODE_INDEX_TABLE} n ON n.id = h.id AND n.kind = 'node' AND n.graph = $5
WHERE h.depth > 0 AND n.retired_at IS NULL
ORDER BY h.depth, h.id
LIMIT $4
"""

#: Every element of one label in a graph. Users are far fewer than estate objects, which
#: is what makes a whole-label read reasonable for the unresolved-owner listing.
ELEMENTS_OF_LABEL_SQL = f"""
SELECT id
FROM {NODE_INDEX_TABLE}
WHERE graph = $1 AND label = $2 AND kind = 'node' AND retired_at IS NULL
ORDER BY id
LIMIT $3
"""

#: How many edges of one type point at each of a set of nodes.
INCOMING_COUNTS_SQL = f"""
SELECT to_id, count(*) AS n
FROM {EDGE_INDEX_TABLE}
WHERE graph = $1 AND label = $2 AND to_id = ANY($3::text[])
GROUP BY to_id
"""

#: Every node and edge in a graph, for the replay comparison. Ordered so two dumps of the
#: same content compare equal without the caller sorting them.
DUMP_ELEMENTS_SQL = f"""
SELECT id, kind, label
FROM {NODE_INDEX_TABLE}
WHERE graph = $1
ORDER BY kind, id
"""

DUMP_EDGE_ENDPOINTS_SQL = f"""
SELECT id, from_id, to_id
FROM {EDGE_INDEX_TABLE}
WHERE graph = $1
"""


def decode_properties(raw: str) -> dict[str, Any]:
    """Parse the ``properties::text`` column of an AGE row."""
    parsed: dict[str, Any] = json.loads(raw)
    return parsed
