# ADR 0022 — Family clustering reuses the Lineage View's scoring, and fixes two things it depended on

Status: accepted · 3 September 2026 · Story S3.1.1 (E3 / F3.1)

## Context

S3.1.1 asks for workbooks clustered into candidate model families by shared lineage, *"so
that the ~150-model planning assumption becomes a measured number in Month 1."* §12.1 gives
the algorithm precisely: three inputs per workbook (tables reached, fields encoded,
calculated-field AST shapes defined), a weighted Jaccard similarity, agglomerative clustering
at a threshold, and a rule for what happens to a family too small to stand alone.

Two things shaped the design more than the algorithm did. First, S1.4.2 already built this
exact formula for the Lineage View — read-only, so a model engineer can see the evidence
behind a grouping and challenge it. Second, actually reading "fields it encodes" and
"calculated-field AST shapes it defines" against a *real* harvested Tableau estate — not the
SDK's fixture, which nothing before this story ever pushed through the full write path with
calculated fields — surfaced two gaps in already-delivered work.

## Decisions

### 1. The threshold is 0.55, not the backlog's 0.35

The backlog's own rule (its line 69): where the specification and the backlog disagree, the
specification is corrected. §12.1 states the default plainly as 0.55.

This is not a rounding matter. At 0.55, a pair sharing zero tables can score at most
`0.3·J(fields) + 0.2·shape_ratio = 0.5` — below the threshold, always. So S3.1.1's own
scoping ("computed for every workbook pair sharing at least one table") is not an
optimisation of the spec's formula at this default; it is an exact consequence of it. Had the
backlog's 0.35 been used, that scoping would have been a real behavioural narrowing — silently
dropping pairs that share no table but score up to 0.5 on fields and shapes alone, which is
above 0.35. Getting the threshold right is what makes the "only table-sharing pairs" shortcut
correct rather than merely convenient.

### 2. The formula, its weights, and the AST-shape normaliser are imported, not reimplemented

`cartographer.py` imports `similarity()`, `WEIGHT_TABLES/FIELDS/SHAPES`, and `ast_shape()`
from `lineage.py` and `context/signature.py` rather than writing its own. Two independent
implementations of "how similar are these workbooks" is exactly the kind of drift this
codebase has been bitten by before (`lineage.py`'s own docstring names the same numbers "must
not drift"), and the Lineage View's whole purpose — showing a model engineer the evidence a
family was clustered from — depends on it being the *same* evidence, not a second opinion
that happens to agree today.

The generic graph-hop query helpers (`children`, `hydrate`, `calc_shapes`, `reach`) were
promoted from `LineageReader`'s private methods to module-level functions in `lineage.py` for
the same reason, with `LineageReader` reduced to thin wrappers so its own behaviour and tests
are unchanged.

### 3. "Fields it encodes" is read from Worksheet shelf properties, not ENCODES edges — a real gap, worked around rather than fixed here

§4.1.2 declares `ENCODES` (Worksheet→Field/CalculatedField, with a `shelf` property), and
`lineage.py` already reads it. Building this story's own reach-computation surfaced that the
Tableau adapter has never actually written it: `fragments.py`'s `_sheets()` populates
`Worksheet.rows_shelf`/`cols_shelf`/`marks_shelf` as raw string-list properties (S2.3.2) but
never materialises the corresponding graph edges. Invisible until now because nothing before
this story exercised "fields a workbook encodes" against a *real* harvested workbook rather
than the SDK's fixture adapter, which does emit the edge.

Fixing it properly means aggregating `field_keys`/`calc_keys` across all of a workbook's
datasources into a workbook-wide map inside `fragments.py`'s `build()` — a real change, but to
F2.3's surface, not F3.1's. Out of proportion for this story, so it is flagged as a follow-up
task rather than fixed inline, and worked around here: `Worksheet.rows_shelf` /
`cols_shelf` / `marks_shelf` already carry exactly the field *names* a sheet places (S2.3.2's
`_field_names()` resolves them to readable names, not raw Tableau tokens), which is what
"encodes" needs. Reading them directly is a faithful stand-in, not a guess, and nothing about
this story's design changes when the real edges arrive — `_sheet_encoded_fields` becomes a
query over `ENCODES` instead of over node properties, and every caller above it is unaffected.

### 4. "Calculated-field AST shapes it defines" needed no workaround — and exposed a second, more serious gap

§12.1 says *defines*, not *encodes*. A calculation belongs to a workbook because its
datasource `HAS_FIELD`-s it — S2.3.1's own `fragments.py` already writes exactly that edge,
independent of whether any sheet ever places the calculation on a shelf. Reading calc shapes
this way needed no new plumbing.

Writing it, in an integration test that seeded a workbook shaped the way a real harvest
leaves one, did not: the ontology's `HAS_FIELD` edge type permits only `Table→Field` and
`Datasource→Field` (§4.1.2 writes the endpoint as "Table/Datasource→Field", naming only
`Field`). The adapter's fragment builder has written `Datasource→HAS_FIELD→CalculatedField`
since S2.3.1; the ontology never permitted the pair. **A real Tableau workbook with a
calculated field could not be harvested through the real write path at all** — only through
the SDK's fixture adapter, whose own `HAS_FIELD` wiring never touches a `CalculatedField`
either, so nothing before this story's own integration suite ever asked the ontology to
validate this specific edge.

Fixed here, in the ontology: `HAS_FIELD` now permits `Datasource→CalculatedField`, declared
as a spec deviation (`EDGE_SPEC_DEVIATIONS`, `ontology/edges.py`) with the reasoning above.
Additive — no migration, `SCHEMA_VERSION` bumped to 8 alongside the `ModelFamily` property
additions below.

### 5. New ModelFamily properties: `reason`, and the evidence itself

§4.1.1 lists `id, name, domain, grain, state, owner, conformed_dims[]` for `ModelFamily`.
Backlog story S3.1.1 asks for more by name — a family held as SINGLETON must carry "the
reason", and a PROPOSED family must carry "the evidence (shared tables, shared fields, shared
calc shapes)" — so `reason`, `evidence_shared_tables`, `evidence_shared_fields` and
`evidence_shared_calc_shapes` are added, declared as a spec deviation the same way
`Workbook.rls` was for S2.3.2: the backlog adds rather than contradicts, so the property is
carried with the story as its warrant.

Evidence is a **frozen snapshot at proposal time**, not a live query. §12.1's "measured
number" and E3's own goal ("shown with their reasoning") both mean the evidence has to be
inspectable later, including after a human edits membership (S3.1.2). Storing it means a
split or merge does not quietly rewrite the history of why the original proposal was made —
the live picture is what `IN_FAMILY` edges and the Lineage View's own recomputation already
show; this is the record of what the Cartographer actually saw.

"Shared" means reached by **two or more** members, not the union of everything any one member
reaches — a table only one workbook touches is a fact about that workbook, not evidence the
family was grouped on.

`SINGLETON` was added to the `state` enum. It is §12.1's own outcome for the clustering step
(distinct from §12.2's post-proposal lifecycle table, which starts at `PROPOSED`), so it is
not a spec deviation in the same sense as the properties above — enum membership is not
machine-checked against the specification's node table, only property names are — but it is
documented in `nodes.py` precisely because it is easy to mistake for one.

### 6. Grain excludes marks; SINGLETON can mean "isolated" or "ran out of things to merge into"

Grain is computed only from `rows_shelf`/`cols_shelf` — the shelves Tableau itself uses for
axes and grouping — not `marks_shelf` (colour, size, detail...), which are encoding channels
rather than dimensions. The fields-Jaccard term does use all three, matching the broader,
generic sense of "encodes".

The undersized-family pass (§12.1's second sentence) is not a single pass over the initial
clusters: a merge that leaves a family still under the minimum is still "a family under a
minimum size" and is reconsidered, until every family either clears the floor or genuinely has
no positive-average candidate left to merge into. That second condition is what `SINGLETON`
means in practice — usually a workbook sharing no table with the rest of the estate, but it
can also be two or three workbooks whose only lineage was to each other. Either way the label
tells a reviewer the same thing: nothing in this run could responsibly make the family bigger.

### 7. A run is a background task with an in-memory status, not a persisted run history

`POST /v1/families:cluster` returns 202 immediately, the same shape as `POST /v1/harvests` —
a run over a large estate is not a request worth blocking on, and the criterion's own
"under 30 minutes" says it can take a while even when nothing is wrong. Unlike the Harvester,
there is no persisted run ledger: the durable result of the *last* run is the programme
record (`clustering_json`, migration v0012), which is what the acceptance criterion actually
asks for, and a full run-history table is a real thing a later story could want (live progress
during a run, for instance) but nothing here asks for it — building it speculatively would be
exactly the kind of ahead-of-the-story scope this project's stories are taken one at a time to
avoid.

### 8. A re-run retires only what it owns

Every run retires existing `ModelFamily` nodes in `{PROPOSED, SINGLETON}` before writing fresh
ones — states nothing but the Cartographer itself has ever put a family into. `DRAFT` and
beyond are S3.1.2's: a human accepted that family, and a re-run has no business overwriting a
decision it did not make. `SHARES_LINEAGE` edges are upserted at a deterministic id per pair
(derived, not `harvest.identity.derive_id` — that function's docstring is specifically about
source-object identity, and a Cartographer-derived edge has no source object behind it), so a
re-run replaces the evidence for a pair rather than accumulating duplicates.

## Consequences

- Every ModelFamily a tenant sees is either a live Cartographer proposal or a human decision;
  nothing in between, and nothing this module writes is ever half of either.
- The Lineage View and the Cartographer are now provably the same arithmetic: a future change
  to the weights or the shape normaliser changes both or neither.
- A client whose Tableau workbooks make heavy use of calculated fields can, for the first
  time, actually be harvested through the real write path rather than only parsed — the
  `HAS_FIELD` fix's blast radius is larger than this story alone.
- `ENCODES` remains unwritten by the real adapter. Anything else that comes to depend on it
  (a future Lineage View smoke test against a real harvest, for instance) will hit the same
  gap this story worked around; the follow-up task names the fix.

## Alternatives considered

**Use the backlog's 0.35 threshold, since it is what the story literally says.** Rejected on
the backlog's own rule, and because 0.35 would make "only table-sharing pairs" a real,
undocumented narrowing rather than the exact consequence it is at 0.55.

**Recompute the evidence live from `SHARES_LINEAGE` edges whenever a family is read.**
Rejected: it would make "the evidence a family was proposed from" silently drift the moment
membership changes, which is the opposite of what "shown with their reasoning" (E3's own
goal) asks for.

**Fix the `ENCODES` gap inline.** Rejected on proportion, not on merit — see decision 3. It is
a real defect and is flagged as its own task rather than folded into this one.

**Give undersized clusters one merge attempt and accept the result regardless of size.**
Rejected: it would let two orphaned workbooks stay in a "family" of two indefinitely rather
than surfacing them for review, which is exactly what SINGLETON exists to do.
