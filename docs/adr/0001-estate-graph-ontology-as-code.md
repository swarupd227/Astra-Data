# ADR 0001 — The Estate Graph ontology is code, and drift from the specification fails CI

Status: accepted · 2 September 2026 · Story S1.1.1 (E1 / F1.1)

## Context

Specification §4.1.1 and §4.1.2 define the Estate Graph ontology as two tables: 28 node
types and 15 edge types with their key properties. Every agent in the mesh reads and
writes this graph and holds no private state about the estate (principle P3), so the
ontology is the interface between eight components that are built at different times by
different people.

S1.1.1 requires the ontology to be enforced at write time, requires each element to carry
provenance base properties, requires the documented ontology and the enforced ontology not
to drift, and requires a schema change that removes a property to fail CI unless it also
supplies a backfill.

## Decisions

### 1. The ontology is declared in Python, not in a database schema or a JSON file

`src/astra_graph/ontology/nodes.py` and `edges.py` are the single definition. The
write-time validator, the generated reference document, the migration guard, the
`GET /v1/ontology` endpoint and the label creation in migration 0001 all read from it.

A declaration that only lived in the database could not be validated before a write
reached the database, and one that only lived in a document could not be enforced at all.

### 2. Required vs optional is a documented judgement

The specification's "key properties" column does not say which properties are mandatory.
The rule applied:

- **required** — identity or classification the producing component always has when it
  first writes the node, and that a downstream reader cannot function without. `Workbook.
  luid`, `CalculatedField.formula_ast` (classification and pattern matching key off it),
  `ParityCase.grain` (a case without a grain is not executable), `Measure.provenance_ref`
  (§16.2 makes provenance mandatory on generated artefacts), `ENCODES.shelf` (parity case
  grain is derived from shelf placement).
- **optional** — values a later stage derives (`CalculatedField.class`, set by the
  Transpiler), values the specification marks nullable (`Table.custom_sql`), and values
  that depend on a source capability that may be absent (usage, ownership, licence cost).

Each non-obvious call carries its reason in the property's `note`, which appears in the
generated reference.

### 3. Base properties are server-managed, and `side` comes from the type

Every node carries `id`, `side`, `created_by`, `created_at`; every edge carries `id`,
`written_by`, `created_at`. Both also carry an optional `created_in_run`.

`created_by`, `created_at` and `written_by` are set by the service. A caller that submits
one is rejected rather than silently overridden: a harvest that believes it stamped a
provenance field and did not produces evidence nobody can trust.

`side` is a property of the node type, not of the instance, so it is not accepted from the
caller — except on `User`, the one type §4.1.1 places on both sides, where the writer must
declare it.

### 4. Two drift guards, not one

`tools/ontology_check.py --generated` regenerates `docs/generated/ontology.md` and fails
if the committed file differs. That proves the document matches the code.

`tools/ontology_check.py --spec` parses the §4.1.1 and §4.1.2 tables out of the
specification and compares node labels, edge labels and property names against the schema.
That proves the code matches the product. It is the check that matters, and it found four
real transcription problems while this story was being built.

Where the schema cannot be a literal transcription, the difference is declared in
`SPEC_DEVIATIONS` with a reason, and appears in the generated reference. There are five:

| Deviation | Reason |
|---|---|
| `ReleaseTrain` / `Wave` split | One specification row, two objects per §3.3 |
| `actual_*` expanded to `actual_start`, `actual_end` | The specification abbreviates |
| `OWNED_BY` / `VIEWED_BY` split, usage properties on `VIEWED_BY` only | One row, different properties |
| `MAPS_TO.target_column` added | §4.1.2 writes the endpoint as `Field→ModelTable.column`; columns are not nodes in this ontology |
| `CONTAINS: Project→Project` | §4.1.1 preserves the project hierarchy and gives Project a `parent` |

An undeclared difference fails. A declared one is a decision on the record.

### 5. A lock file plus claimed backfills, rather than inferring intent from a diff

`ontology.lock.json` is the committed snapshot of the schema as last migrated.
`tools/migration_check.py` classifies each difference between the lock and the live schema
as additive or breaking, and refuses any breaking change that no migration claims in its
`ONTOLOGY_CHANGES` with a non-empty backfill.

Breaking means: a type removed, a property removed, a property made required (including a
required property added), a property's type changed, an enum value withdrawn, a node
type's side changed, an edge endpoint pair withdrawn.

### 6. A hand-written migration runner rather than Alembic

Nearly all of this schema is Apache AGE DDL — `create_graph`, `create_vlabel`,
`create_elabel` — not SQLAlchemy metadata, so Alembic's autogenerate has nothing to work
from and would be used only as a version-tracking table. The runner is about 60 lines,
takes a PostgreSQL advisory lock so replicas cannot race, and keeps the service image on
asyncpg alone.

Revisit if the platform's relational tables (spec §21) grow to the point where
autogenerate earns its place.

### 7. A relational element index alongside the graph

`estate_element_index (id, kind, label)` is written in the same transaction as the graph.
It gives an id-to-label lookup as a primary-key probe — needed on every edge write, since
whether an edge is permitted depends on what its endpoints actually are — and turns a
duplicate id into a unique violation rather than a race.

It is derived data. The graph is the source of truth; the index is never read as authority
for anything but routing a lookup.

### 8. The principal is asserted in a header until E11

`X-Astra-Principal` carries `agent:<name>`, `user:<upn>` or `service:<name>`, and the
service records what it is told. Verified identity — Entra ID for people, workload
identity for agents (§18.1) — is E11. The value is required and recorded on every element,
so E11 replaces the source of the value without changing the ontology or the write path.

## Consequences

- Adding a node type, an edge type or a property is a code change with a test and a
  generated-document update, not a database migration alone.
- The specification cannot quietly diverge from the running system in either direction.
- A breaking ontology change cannot reach `main` without someone writing down what happens
  to the data already in the graph.
- Six components (Harvester through Steward) can be built against a schema that is
  machine-readable at `GET /v1/ontology` rather than transcribed by hand from a document.

## Open questions for the product owner

1. **`Connection.class` is a closed set.** §4.1.1 enumerates nine classes
   (`sybase|sqlserver|snowflake|postgres|hive|excel|text|odbc|hyper`). A source estate
   with, say, an Oracle or Teradata connection is rejected at write time and the workbook
   cannot be harvested. Should the set stay closed per the specification, or should an
   unrecognised class be admitted and flagged the way unrecognised calculation constructs
   are under §4.1.4 parse quality?
2. **`validation_state` has no closed set.** §7.2 refers to `PROVED` and `WAIVED`; §16.1
   defines a five-rung ladder. The values are free text until E5 and E7 fix them. Confirm
   the closed set when those epics are specified.
3. **`Workbook.revision` is a string.** The Tableau REST API returns `revisionNumber` as a
   string; §3.1 shows `revision: 14`. String is the faithful transport form and the
   Harvester's idempotency key. Confirm no downstream consumer needs it ordered
   numerically.
