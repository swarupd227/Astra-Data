# ADR 0018 — Filters are part of the question, and row-level security is a finding

Status: accepted · 3 September 2026 · Story S2.3.2 (E2 / F2.3)

## Context

S2.3.2 asks for sheets, filters, parameters, actions and dashboards parsed *with their
context* — and the reason it gives is the design constraint: *"so that the Proof Engine can
derive cases that respect what the user actually sees."*

§10.2 derives parity cases at the grain a sheet shows. A case derived without the sheet's
filters compares a report nobody has: the client's dashboard shows last quarter's top ten
desks, and a case querying all desks over all time would "fail" on rows the user has never
seen — or pass while proving nothing. A filter is not metadata recorded for completeness; it
is part of the question.

## Decisions

### 1. A top-N filter is not a categorical filter, and telling them apart takes work

Tableau writes a top-N as `class="categorical"` carrying a `<groupfilter function="end">`.
Reading the class alone types it as categorical and loses the thing that makes it a top-N —
which is precisely the filter §10.2 most needs to respect, because a case that ignored it
would compare ten rows against four hundred.

The same asymmetry runs through the whole filter mapping: Tableau's `quantitative` is
§4.1.1's `range`, `relative-date` is `relative_date`, and a top-N hides inside a categorical.
The typing is a translation, not a lookup.

An unmapped class becomes `condition` — Tableau's own catch-all — **with its expression and
its original class recorded**, and a log line. §4.1.1's enum is closed, and a filter typed
wrongly is a parity case asking the wrong question.

### 2. A context filter is flagged, because it changes what an LOD means

Tableau applies a context filter *before* the others, so the same `{FIXED …}` expression
computes over a different population inside and outside one. §4.1.1 has `context_flag` for
exactly this, and §10.2's case derivation is where it matters.

### 3. The dashboard's zone tree is kept nested

Flattening it to a list of sheet placements loses the containers, and containers are what
§11.3's Compositor lays a Power BI page out from. A list of rectangles is not a layout.

### 4. Row-level security is recorded on the Workbook — and needs a SpecDeviation

The story names the node and the property: *"recorded on the Workbook node as rls: true with
the expression"*. §4.1.1's Workbook row does not list it, so `Workbook.rls` and
`Workbook.rls_expression` are a **declared deviation** with the story as their warrant. The
backlog adds here rather than contradicting, which is the case the deviation mechanism exists
for. Schema version 6; both properties additive, so no migration.

**The expression is kept verbatim** because "this workbook restricts rows" without saying how
is not something a Modeller can act on — the target has to reproduce the access model.

Three places are searched, not one: a Tableau *user filter* (a calculated field over
`ISMEMBEROF` or `USERNAME`, applied as an ordinary filter), the same functions used directly
in any calculation, and a filter whose condition calls them. The story names `ISMEMBEROF` and
`USERNAME`; `FULLNAME`, `ISUSERNAME`, `ISFULLNAME` and `USERDOMAIN` are the same family, and a
workbook restricting rows with `FULLNAME()` restricts rows just as much.

**`false` and absent are different.** The property is written whenever the adapter looked;
absent means it did not. A workbook harvested before S2.3.2 has neither, and an operator
reading the estate before a Calibration Wave needs to know which of those they are looking at.

### 5. Actions and Parameters get nodes and no containing edge

§4.1.1 models an action's linkage as `source_sheets` / `target_sheets` *properties*, and
§4.1.2 gives Parameter only `DEPENDS_ON(CalculatedField → Parameter)`. So neither has an edge
reaching it from the Workbook.

That is uncomfortable — a node nothing points at is unreachable by traversal — but inventing
`CONTAINS(Workbook → Action)` would be an ontology change for one adapter's convenience, and
the platform's own fixture adapter already emits them the same way. Following the
specification keeps the two producing the same shape. Recorded as an open question below.

### 6. Filters are on the Worksheet *and* are nodes

§4.1.1 lists `filters[]` on Worksheet; §4.1.2 gives Filter its own node with `FILTERED_BY`.
Both, because they serve different readers: the JSON is what a screen renders without a
traversal, and the nodes are what the Proof Engine walks.

## Consequences

- Ontology schema version 6, ten declared deviations, `ontology.lock.json` re-locked. Both new
  properties are additive so the migration guard requires no migration.
- `parse` now emits Worksheet with its full visual specification, Dashboard with its layout,
  Filter, Action and Parameter nodes, and `DEPENDS_ON` edges to parameters — which makes "what
  breaks if this parameter changes" answerable by traversal for the first time.
- Parse quality counts filters, actions and parameters. A workbook full of filters would
  otherwise have scored on its datasources alone.
- The adapter still passes conformance: 8 / 0 / 3.

## What building it found

1. **`Filter.field` shadowed `dataclasses.field`**, which made the very next line —
   `values: dict = field(default_factory=dict)` — a call on a string. Renaming it to
   `field_ref` fixed it and matched §4.1.1's property name, which it should have been from the
   start.
2. **Encodings were read from the wrong attribute.** Tableau writes `column=`, not `field=`,
   so every sheet reported an empty marks shelf — which reads as "this sheet encodes nothing"
   rather than as a parser missing an attribute, and Appendix B.2 binds encodings to the
   target visual.
3. **The golden corpus had one filter and no dashboard zones.** Adding one sheet per filter
   kind was not padding: the top-N branch would never have run.

## Open questions for the product owner

1. **Actions and parameters are unreachable by traversal.** §4.1.1 models their linkage as
   properties, so nothing points at them from the Workbook and the Estate Explorer cannot show
   them under a workbook without a property scan. Whether §4.1.2 should gain
   `CONTAINS(Workbook → Action | Parameter)` is a specification question, and it will be asked
   again the first time somebody wants "which workbooks have set actions" on a screen.
2. **Row-level security is detected, not resolved.** The platform knows a workbook restricts
   rows and knows the expression; it does not know *which groups exist* or who is in them, so
   it cannot say what a given user would see. §10's parity cases run under a service identity
   that sees everything — so for an RLS workbook, every case currently proves the unrestricted
   view and nothing else. That is a gap in what a parity verdict means, not in this adapter.
3. **Shelf expressions are reduced to field names.** `([federated.p].[none:desk:nk])` becomes
   `desk`, which is what a person recognises — but the aggregation prefix (`none:`, `sum:`)
   carries the shelf's aggregation, and the Compositor will need it. Recording both forms is
   cheap; deciding which is authoritative is not, and belongs with E6.
4. **Nothing yet reads a sheet's *grain*.** §10.2 derives cases at the grain a sheet shows,
   and the shelves are the raw material rather than the answer — a sheet with two dimensions on
   rows and one on colour has a grain the Proof Engine has to compute. Left deliberately to
   E7, but worth confirming that is where it belongs.
