# ADR 0002 — The query API reads AGE's storage directly, and traverses a relational adjacency index

Status: accepted · 2 September 2026 · Story S1.1.2 (E1 / F1.1)

## Context

S1.1.2 requires a typed query API, a read-only Cypher endpoint, and a **p95 under 300 ms
for a depth-3 workbook neighbourhood on a 1,000-workbook estate**. NFR N3 repeats the
figure and NFR N1 sets the scale it has to hold at: 1,500 workbooks and two million graph
edges per tenant.

The obvious implementation — express the traversal as Cypher and let Apache AGE run it —
was measured before it was chosen.

## Measurements

Synthetic estate with the fan-out of the §3.4 worked example: 1,000 workbooks, 56,000
nodes, 78,000 edges, on the same PostgreSQL 16 + AGE 1.6.0 image CI uses.

**Traversal**, depth 3 from a workbook:

| Approach | p50 | p95 |
|---|---|---|
| AGE `MATCH (n:Workbook)-[*1..3]-(m)` | 2918 ms | 3252 ms |
| Recursive CTE over a relational adjacency index | 1.1 ms | 2.0 ms |

At 400 workbooks the AGE path was already 980 ms / 1485 ms, so it does not degrade
gracefully: a variable-length path unions every edge label at every hop, which is 15
tables per hop in this ontology.

**Hydration**, fetching the properties of the ~56 nodes a depth-3 traversal reaches:

| Approach | p95 |
|---|---|
| `cypher() … WHERE n.id IN $ids`, one call per label | 235 ms |
| `cypher() … UNWIND $ids … WHERE n.id = wanted` | 53 ms |
| Direct indexed read of the label table, per label | 8.6 ms |
| Direct indexed read, all labels in one `UNION ALL` | 4.0 ms |

`EXPLAIN` confirms the direct read is an `Index Scan` on the property index and that the
`IN` inside `cypher()` is not.

**End to end**, the shipped implementation, measured by
`test_depth_three_neighbourhood_meets_the_latency_budget`: **p50 39 ms, p95 50 ms**,
against the 300 ms budget.

## Decisions

### 1. Writes go through `cypher()`; reads do not

Writes keep going through AGE's own Cypher so AGE owns identity, edge linkage and its
internal catalogue. Reads take two shortcuts:

* **Traversal** walks `estate_edge_index`, a relational table of
  `(id, label, from_id, to_id)` written in the same transaction as the edge itself. The
  neighbourhood is a recursive CTE over a btree.
* **Hydration** reads the label tables directly through AGE's own accessor,
  `agtype_access_operator(VARIADIC ARRAY[properties, '"id"'::agtype])`, which is the
  expression the property indexes are built on.

The cost is a coupling to AGE's storage layout. It is confined to
`graph/queries.py`, it is a layout the platform already depends on — migration 0001
creates those tables through `create_vlabel` — and the integration suite writes through
`cypher()` and reads back through the direct path, so a change in AGE's layout fails
`test_node_by_id_round_trips_through_age` rather than silently returning nothing.

### 2. The adjacency index is derived data, written transactionally

`estate_edge_index` is to edges what `estate_element_index` (ADR 0001) is to nodes. The
graph remains the source of truth; the index exists to make a traversal a btree walk.
Both are written inside the edge's own transaction, so they cannot disagree with it.

Migration 0002 backfills the index from edges already in the graph, so an estate
harvested under schema version 1 is traversable without a re-harvest.

### 3. Traversal is undirected

A neighbourhood follows edges both ways. Someone asking what a workbook touches wants the
datasource it reads and the project that contains it alike; direction is a property of the
edge type, not of the question. `depth` is the shortest path, so a node reachable two ways
reports the shorter. Direction is available where it matters: `closure` and `step` follow
one named edge type forwards, and the context contracts use those.

### 4. GraphQL object types are generated from the ontology

One object type per node type and per edge type, built at import from the registry, with
each type's declared properties and their declared nullability. The schema therefore
cannot describe a shape the write path would reject, and a new ontology property appears
in the API without anyone editing the GraphQL layer.

Two conventions:

* **`auto_camel_case` is off.** `views_90d` is `views_90d`, not `views90D`. The names come
  from the specification and a reader comparing the two should not have to translate.
* **The ontology type is read with `__typename`,** not a field of our own. Datasource,
  Filter, Action and Visual each declare a property called `type`; an interface field of
  that name is shadowed by the property, so `Datasource.type` answered `"published"` where
  a caller asked what kind of node it was. This was found by smoke-testing the running
  service, and `test_the_interfaces_do_not_declare_a_field_the_ontology_uses` now fails
  if any interface field ever collides with an ontology property name again.

`class` is a property of five types and cannot be a Python attribute name, so the
generated types spell it `class_` internally and `strawberry.field(name="class")` keeps
the GraphQL field on the specification's name.

### 5. The Cypher endpoint is guarded in layers, and the transaction is the guarantee

`cypher.py` rejects, with an explanation:

* dollar-quote delimiters and `;` — the query is interpolated into `$$ … $$`, so those
  could escape into SQL;
* write clauses by name, after string literals and comments are stripped, so a workbook
  called "Create new report" is not mistaken for a write;
* a RETURN clause whose columns cannot be named — AGE requires the result columns declared
  in SQL before the query runs, so `RETURN *` is refused with instructions rather than
  guessed at.

None of that is the guarantee. The guarantee is that execution happens inside a PostgreSQL
`READ ONLY` transaction with `SET LOCAL statement_timeout = 30000`, both verified against
the live database by `test_a_write_is_blocked_by_the_transaction_not_only_the_guard`,
which bypasses the lexical guard on purpose.

### 6. Roles come from §2.4, asserted in a header until E11

The eleven roles and the organisation each belongs to are transcribed from the
specification. The Cypher endpoint is open to the six Artizent roles and closed to the
five client ones, because it bypasses the shaping the console applies to client surfaces
(§15.2). Roles arrive in `X-Astra-Roles` and are replaced by Entra ID group mapping in
E11, the same way `X-Astra-Principal` is.

### 7. One log line per query, and never the data

Every read writes one line with principal, roles, duration, operation and element count.
Query *results* are never logged: a result can carry a field name or a custom SQL literal
the client classifies as restricted (§18.3).

The raw Cypher endpoint is the exception and records the query text, including for a
rejected query. It is the one surface where a caller composes arbitrary traversal, and an
auditor asking what was run against the estate needs to see it. The text is a query over
metadata; row-level data lives in the Proof Engine, not the graph.

## Consequences

- The 300 ms budget is met with roughly 6× headroom, measured on every CI run rather than
  asserted.
- `graph/queries.py` must be read before AGE is upgraded. The round-trip test is the
  canary, and it runs in CI.
- Depth is capped at 5 (the story's figure) and results at 10,000 elements, the same cap
  as the Cypher endpoint. A truncated result says so; it is a prefix, not a sample.

## Open questions for the product owner

1. **The other agents' context contracts.** §4.1.3 specifies the Transpiler's in full and
   that one is implemented. The Modeller's, Compositor's and Mender's input shapes are
   settled by E4, E6 and E8; declaring them now from the one-line summaries in the §8.3
   catalogue would be guessing. Confirm they belong with their agents.
2. **The `patterns` section of the Transpiler contract** returns nothing and says why:
   patterns are matched by AST-shape signature (§9.3), and the normaliser that computes
   the shape key belongs to the Pattern Library (E5/F5.3). The contract reports it as a
   pending section rather than an empty one, so a caller cannot read "no matching
   patterns" into "matching does not exist yet".
3. **An index consistency check.** The relational indexes are written transactionally and
   cannot drift in normal operation, but nothing detects it if they ever do — a node whose
   index row is missing is invisible to lookup and traversal while still being in the
   graph. A reconciliation job is not in any story I have seen; say whether it should be.
