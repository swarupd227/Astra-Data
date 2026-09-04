# ADR 0028 — The Modeller proposes with disclosed heuristics, never a silent guess

Status: accepted · 4 September 2026 · Story S4.1.1 (E4 / F4.1)

## Context

S4.1.1 asks for a model design proposal generated from the graph: *"Proposal contains:
tables with source mapping and storage mode (Import | DirectQuery | Direct Lake),
relationships with cardinality, grain statement, conformed dimensions shared with other
families, candidate measures with source calc refs and dedup decisions, RLS roles derived
from Tableau user filters, refresh policy. Proposal is produced in ASSISTED mode: structure
is deterministic from the graph; naming and the grain statement may be drafted by the model
with provenance recorded. Proposal lists open questions for the data owner (each with the
graph evidence that raised it). Generation of a proposal for a 40-workbook family takes
under 5 minutes."*

Five questions decided the shape of the work: what "ASSISTED... may be drafted by the
model" means when this platform has never called an external model; how a table's storage
mode and a relationship's cardinality can be recommended from an estate that carries no key
metadata; how "deduplicated by normalised AST" and "duplicate measures with different
definitions" are actually two sides of the same computation; where a proposal this rich
lives in an ontology that declares no relationship type between target tables and no
first-class "candidate measure"; and what "provenance recorded" requires given §4.2's own
machinery is built for policing a real inference boundary this story does not cross.

## Decisions

### 1. Naming and the grain statement are drafted deterministically today, and the provenance record says so honestly

No component in this codebase has ever called an external model — §5.5's Model Gateway does
not exist. S3.1.1's own "ASSISTED" family naming (§8.5) is already a deterministic template
under that label, unremarked. This story goes one step further than that precedent because
its own criterion explicitly asks for "provenance recorded": `draft_grain_statement`
renders the family's own candidate grain (`ModelFamily.grain`, the Cartographer's output)
as a sentence, and a real `ProvenanceRecord` (§4.2's own machinery, built at S1.3.2, reused
verbatim) is written for it — `mode: ASSISTED`, `model: null`. This is not a fabricated
claim: §8.2 defines ASSISTED as "a model proposes; a rule or a person decides", which
describes the *kind* of decision (advisory, retained as evidence, edited by the Semantic
Model Engineer at S4.1.2) rather than asserting a specific mechanism produced the text.
`model` stays null exactly when no gateway call happened, so a reader can always tell a
templated draft from a genuine one without reading code — the same discipline S2.1.2's own
ADR states as "never claim a signature you do not have."

**No formal `ContextContract` is registered for this record's `context_hash`.** §4.1.3's
fragment-validated contract machinery exists to police what crosses the §18.3 inference
boundary to a real external model call; today's deterministic drafter never reaches that
boundary, so the machinery would be validating a boundary that is not there yet. The record
is still honest and reproducible: `context_hash` is computed with the same
`context.canonical.canonical_json`/`context_hash` utility every contract uses, over the
gathered family evidence directly. `ContractName.MODELLER_FAMILY` is declared — its own
docstring states this deviation — for exactly the day a real model call needs the
fragment-validated version.

### 2. Storage mode and relationship cardinality are named heuristics, not facts

This estate carries no primary-key metadata for any source table — `Table.row_estimate` is
the only signal a Modeller has. `recommend_storage_mode` reads it alongside
`Datasource.extract_flag`: an extract already means the estate chose to copy this table
rather than query it live, so Import mirrors that choice, unless the table is large enough
(`row_estimate >= 50,000,000`, `DIRECT_LAKE_ROW_FLOOR`) that copying it whole is itself the
wrong choice, in which case Direct Lake is recommended instead — a real Fabric trade-off
(avoid both a live-query cost DirectQuery would carry and the double storage/slow-refresh
cost Import would carry at this scale), not an arbitrary number. No extract recommends
DirectQuery, because there is nothing to import. Fifty million rows is high enough that a
false positive (recommending Direct Lake for a table Import would have handled fine) is
rare, and low enough that it catches the estates where a full extract-then-copy is
genuinely the wrong architecture.

`infer_cardinality` reads the same figure for the two tables either side of a join: a ratio
of 3x or more (`CONFIDENT_CARDINALITY_RATIO`) calls the larger side "many" with confidence;
within that band, the estimate is reported `ambiguous` rather than guessed — an open
question, not a coin flip dressed as a recommendation. Every recommendation this module
makes carries its own `reason` string naming exactly which figure decided it, so a
Semantic Model Engineer reviewing the proposal is shown the evidence, not just the verdict
— the same "evidence one click away" principle §15.2 states for every other screen in this
console.

### 3. A relationship defaults every joined table to the connection's biggest table, because the target has to be star-shaped regardless of what Tableau's join graph looked like

A Tableau datasource's join graph can chain tables in any order, and `join_clause` (§4.1.2,
carried on the `Connection → Table` edge) is raw text this platform does not parse
semantically. Reconstructing the *exact* join topology Tableau used is out of reach without
that parsing. But it does not need to be reconstructed: §12.3's own conformance rule for
this target architecture is "star schema only — no many-to-many without a bridge", so every
join a family's connection carries has to resolve to *some* table being the hub regardless
of how Tableau expressed it. `_relationship_candidates` picks the table with the largest
`row_estimate` per connection as that hub (a fact table has the most rows; ties broken by
id for determinism) and proposes every other table's relationship as directly to it. This
is a deliberate simplification disclosed as one, not a guess dressed up as topology — and
it is the *correct* default independently of the real join graph, because the target model
is going to be forced into this shape at BUILT time (S4.3.2) no matter what.

### 4. Candidate measures are deduplicated by AST shape; the same computation finds "duplicate measures with different definitions" as its mirror image

§8.6 asks for measures "deduplicated by normalised AST" and, separately, for open questions
about "duplicate measures with different definitions" — these are the same underlying fact
looked at from two directions. `dedupe_measures` groups calculations by
`context.signature.ast_shape` — the Pattern Library's own normaliser (§4.3), so "the same
calculation" means the same thing here as everywhere else in this codebase — collapsing
every group of two or more into one candidate measure. Separately, it groups the same
calculations by *normalised name*: a name mapped to more than one distinct shape is a real
disagreement between member workbooks about what that name means, and becomes an open
question rather than a silent pick of either definition. A calculation whose AST cannot be
shaped at all (deeper than the signature normaliser's own bound) is carried through
unmerged rather than dropped — an un-normalisable calculation is still a candidate measure,
just one this pass could not fold into anything else.

### 5. No `Measure` node is written; `ModelTable` and `SemanticModel` are, with one new reference property and one new JSON document

§4.1.1 makes `Measure.dax` and `Measure.provenance_ref` required — "the Transpiler's
product" (E5, not built). A candidate measure here has no DAX; writing a `Measure` node for
it would either violate the ontology or fabricate a DAX string nobody generated. It lives
instead in `SemanticModel.design_document`, alongside every other part of this proposal
that has no first-class graph shape yet: relationships (§4.1.2 declares no edge type
between `ModelTable`s), conformed-dimension sharing, refresh policy, open questions, and
RLS role detail. `SemanticModel.grain_statement`, `.design_generated_at` and
`.design_provenance_ref` stay separate scalar properties — each a plain value an engineer
or a filter would want directly, not something worth digging out of a JSON blob.

`ModelTable` and `SemanticModel` have both been declared in the ontology, unused, since
S1.1.1 — the same position `ReleaseTrain` was in before S3.2.1, and `ModelTable` gets its
first write here. §4.1.1 declares no edge or property linking a `ModelTable` back to its
owning family (`MAPS_TO` only carries a source `Field` to a `ModelTable`'s own column), so
`ModelTable.family_ref` is added — a plain reference property, the same shape
`ReportDefinition.mu_ref` already uses to point at an MU that, like a design before G2, is
not yet a settled first-class record. `family_ref` is required (migration v0015): `ModelTable`
has never been written before this story, so the required addition backfills nothing —
recorded explicitly per the migration guard's own discipline, not silently assumed safe.

### 6. Tables are deduplicated "by connection + table" for free, because node identity already is

Two datasources reaching the same physical table already resolve to the same `Table` node
id — S1.3.1's identity rule derives it from the owning connection plus name and schema at
harvest time. Collecting the *set* of table ids a family's members reach across every
member's datasources is the deduplication; no second pass groups by `(connection, name,
schema)` because the graph has already done that grouping by construction.

### 7. RLS is read from `Workbook.rls`/`.rls_expression`, not re-derived from Filter nodes or calculation text

S2.3.2 already flags a workbook as RLS-restricted and records its user-filter expression
verbatim. The criterion asks for a scaffold "from Tableau user filters" — that is exactly
what those two properties are, so `derive_rls_roles` reads them directly rather than
re-deriving the same fact by scanning calculated-field formulas for `USERNAME()`/
`ISMEMBEROF()` (a second, weaker signal for a fact the harvester already recorded
precisely). Member workbooks sharing an identical expression become one role; a workbook
flagged `rls: true` with no recorded expression is an open question, not a silently dropped
role — the flag says the estate restricts rows and the platform does not yet know how.

### 8. A re-run replaces the whole proposal; there is no pin to respect yet

Unlike `ModelFamily.overridden` (S3.1.2) and `ReleaseTrain.overridden` (S3.2.2), nothing in
this story protects a Semantic Model Engineer's edits from a re-run, because no editing
exists yet — the Model Detail screen that lets one edit a proposal is S4.1.2's own
acceptance criterion. `Modeller.run` retires the family's previous `SemanticModel` and
`ModelTable` nodes and writes fresh ones on every call, the same starting posture S3.1.1
and S3.2.1 each had before their own override story added a pin. S4.1.2 is expected to add
the equivalent pin here, reusing the identical mechanism.

## Consequences

- Two new ontology properties on `ModelTable` (`family_ref`, required) and four on
  `SemanticModel` (`grain_statement`, `design_generated_at`, `design_provenance_ref`,
  `design_document`) — schema version 12 → 13, migration v0015 claiming the one breaking
  change with a no-op backfill (no `ModelTable` row has ever existed before this story).
- `ContractName.MODELLER_FAMILY` is declared with no registered `ContextContract` — the
  first time this deviation shape exists in the codebase. `context.assembler.assemble`'s
  own "declared but unregistered" branch, previously unreachable (`# pragma: no cover`
  before this story, since exactly one contract existed and was always registered), is now
  live; its error message was fixed in the same change to report the contract's `.value`
  rather than its Python repr, and to say *why* — a declared name with nothing registered —
  rather than reusing the "never heard of it" message a genuinely unknown name gets.
- A pre-existing test (`test_an_unknown_contract_names_the_ones_that_exist`) had chosen
  `modeller_family` as its example of a name that does not exist, coincidentally. Updated
  to a name that genuinely does not exist, and a new test added for the now-real "declared,
  unregistered" case it collided with.
- Generation is dominated by a fixed number of graph hops (one per edge type reached), not
  by member count — the 40-workbook, 5-minute budget is comfortably inside what this
  design costs regardless of family size; see the module docstring and
  `test_generation_completes_comfortably_inside_the_five_minute_budget`.

## Alternatives considered

**Call a real external model for the grain statement and naming.** Rejected for this
story — see decision 1. No Model Gateway exists (§5.5), and building one as a side effect
of a design-proposal story would be answering a question a future epic owns. The seam
(`ContractName.MODELLER_FAMILY`, ready for a registered contract) is left for it.

**Write `Measure` nodes with a placeholder or empty `dax`.** Rejected — see decision 5.
`Measure.dax` is required precisely because a `Measure` is defined as the Transpiler's
product; writing one with fabricated DAX would let a downstream reader mistake a design-time
guess for a generated, validated artefact.

**Attempt to parse `join_clause` to reconstruct the real join topology.** Rejected — see
decision 3. The text is arbitrary SQL-like join syntax with no guaranteed structure across
connection classes; a star-schema default is both simpler and, given §12.3's own
conformance rule, no less correct in the end state.

**A single JSON blob for the whole proposal, including tables.** Rejected — see decision 5.
`ModelTable` is a real, already-declared node type with no `Measure`-style blocking
required field; writing it as a first-class node (rather than folding it into
`design_document` alongside what genuinely has no graph shape yet) keeps the graph the
single source of truth for what this platform already knows how to model.
