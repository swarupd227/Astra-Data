# ADR 0035 — Classification reads Appendix B.1 off the AST the grammar already stamps

Status: accepted · 4 September 2026 · Story S5.1.1 (E5 / F5.1, new epic)

## Context

S5.1.1 opens E5, the Transpiler: *"As a parity engineer, I want every calculated field
classified C1–C4 from its AST before any generation, so that the class mix is measured on
day one and drives cost and routing. Classifier is deterministic: C1 when a direct-map rule
matches the whole AST; C2 when a structural rewrite rule matches; C3 when the AST is within
grammar but no rule matches or context (LOD scope, table calc addressing, parameter-driven
logic) is required; C4 when a construct has no Power BI equivalent per Appendix B. Class,
matched rule or pattern id, and reason are written to the CalculatedField node. Estate-wide
class mix is reported on the Programme Board against the calibration targets 45 / 30 / 18 /
7. Re-classification runs when the rule set or pattern library changes and reports what
moved class."*

Four questions decided the shape of the work: what the classifier actually walks (the real
AST the Tableau grammar produces, or `context/signature.py`'s own generic shape-string
walk); where the two facts §9.1 itself says the AST alone cannot answer — a parameter
reference, and whether a table calculation's addressing resolves — come from; where the
line sits against F5.2 (the rules engine that rewrites an AST) and F5.3 (Class 3 generation
via the reasoning model), neither built yet; and who triggers re-classification, since no
Admin-editable rule set exists for this story to gate the way S4.3.2's conformance rules
were.

## Decisions

### 1. The classifier walks the real `CalcNode` shape, not `context/signature.py`'s generic one

`formula_ast` on a real `CalculatedField` is exactly `astra_adapter.rpc.wire.
encode_calc_node`'s own output — `kind`/`name`/`children`/`detail`, recursively, the same
shape the Tableau grammar's own `CalcNode` dataclass produces (S2.3.1). `context/
signature.py`'s `ast_shape()` was written before that grammar existed (its own docstring:
*"The calculation grammar belongs to the Tableau adapter... and does not exist yet"*) and
walks a different, generic shape (`op`/`fn`/`args`/`field` keys) that a real AST does not
use — a real classification cannot be built on a walk that does not recognise the AST it is
given. `classify.py` is a second, purpose-built walk over the real shape; `ast_shape()` is
untouched; reconciling the two (or replacing the generic walk with a grammar-aware one) is
F5.2/F5.3's own concern once the Pattern Library actually needs to match on it.

### 2. Appendix B.1's own families are already on the AST; only the family→class table was missing

`functions.py` (S2.3.1, `packages/adapter-tableau`) already stamps `("family", <name>)`
into every FUNCTION/AGGREGATE/WINDOW node's `detail` at parse time — its own docstring says
so: *"Recording the family on the AST node is what lets the Transpiler ask 'is this C1?'
without re-deriving it from a function name."* What did not exist anywhere as data was
Appendix B.1's own "Default class" column. `classify.py`'s `_FAMILY_CLASS` table is exactly
that column, transcribed once; the classifier reads `detail["family"]` off nodes the
grammar already produced rather than importing `packages/adapter-tableau`'s own registry
(graph-svc has never imported an adapter package directly — S2.1.1's own "invert the
dependency" precedent — and Appendix B.1 is spec text both sides are independently anchored
to, not a table that needs to stay in lockstep code-to-code).

### 3. A parameter dependency and table-calc addressing are resolved from the graph, not guessed from the AST

Tableau writes a parameter reference identically to a field reference — `parser.py`'s own
`_reference` docstring: *"the caller ... decides."* So whether a calculation depends on a
*parameter* (Appendix B.1's own C2 "Parameters" row) is answered from a real `DEPENDS_ON`
edge to a `Parameter` node, not guessed from AST shape. Table-calc addressing is the
opposite kind of gap: the grammar always records it `"unresolved"` (§6.2: addressing "comes
from the sheet, not from the expression"), and nothing in this codebase has ever resolved
it since. `reclassify_estate` resolves it itself — for every `Worksheet` with a populated
`rows_shelf`/`cols_shelf` (S2.3.2), every `CalculatedField` it `ENCODES` gets "addressing
resolved" for the whole reclassification pass, one query rather than one per field. A
calculation's `ClassificationContext` (parameter dependency, addressing resolution) is
therefore resolved once per estate-wide pass, not derivable from `classify()`'s own pure
AST argument alone — the acceptance criterion's own wording ("context ... is required")
names exactly this.

### 4. The worst node in the tree decides the whole calculation's class

§9.1's own C1 definition is *"every node in the AST has a one-to-one target equivalent"* —
so one node needing more than that is what the whole expression needs, and `classify()`
returns the single worst (class, rule_id, reason) triple found anywhere in the tree, not a
summary. A `DIV(SUM(a), RAWSQL_INT(...))` is C4, and the reason names the RAWSQL call, not
the division. Unrecognised functions (parsed but not in the platform's registry) and
UNKNOWN nodes (not parsed at all) both classify C4 rather than being left unclassified: a
human has to look at either regardless of which is true, the same outcome C4's own "Redesign
flag; HUMAN" path already means. Two Appendix B.1 rows name real ambiguity rather than one
answer — "REGEXP → M or C4" and "table calc — complex... C3 / C4" — and both take the
conservative half by default, disclosed in the reason, since no M pass-through path or
addressing-resolution mechanism exists yet to justify the more optimistic one.

### 5. Re-classification is an explicit, estate-wide action — not automatic, not scoped to one family

No Admin-editable rule set exists for this story the way S4.3.2's conformance rules did:
§4.3 names the deterministic rules as *"the deterministic rule set shipped with the
adapter"* — code, authored via PR (F5.2's own future scope), not data an architect edits.
"Re-classification runs when the rule set or pattern library changes" is therefore a
release-triggered action, not a harvest-triggered one — checked against this codebase's own
precedent (`Rescorer`, S1.2.2's parse-quality rescoring engine, is itself never invoked
automatically after a harvest; it is an explicit, on-demand action) rather than inventing an
automatic hook nothing else in this codebase has. `POST /v1/calculations:reclassify` walks
every live `CalculatedField` in the graph and reports what moved class; `CLASSIFIER_VERSION`
(a plain module constant, bumped by hand alongside a future rule-set change) is stamped on
every field so a later run can always tell "classified against the rules as they stand
today" from "classified before they changed." Gated to `parity_engineer` — the persona this
story's own acceptance criteria names, and, like `migration_architect` at S4.3.2, the first
route to actually drive a role declared since S1.1.1 and gated nowhere until now.

## Consequences

- New module `classify.py`: `classify()` (pure, one AST + context → class/rule_id/reason),
  `reclassify_estate()` (the estate-wide pass: resolves parameter/addressing context from
  real graph reads, writes `class`/`pattern_ref`/`reason`/`classifier_version` onto every
  live `CalculatedField`, reports what moved), `class_mix()` (a live read of what the last
  pass wrote, against the calibration targets 45/30/18/7).
- New `CalculatedField` properties (schema version 16 → 17, additive):
  `pattern_ref`/`reason`/`classifier_version` — a declared `SpecDeviation`, since §4.1.1
  lists only `class` there (its own `pattern_ref` exists, but on the MAPS_TO edge, for a
  *generated* Measure's pattern — a later, different fact from why the classifier chose
  this class in the first place).
- New routes: `GET /v1/calculations:class-mix` (any Artizent role, the same posture every
  other Programme Board figure has) and `POST /v1/calculations:reclassify`
  (`ParityEngineerDep`, new).
- Console: a fourth Programme Board pane — every class against its calibration target, and
  a "Re-classify" action reporting how many fields moved.

## Alternatives considered

**Rewrite `context/signature.py`'s `ast_shape()` to walk the real `CalcNode` shape, and
build the classifier on top of it.** Rejected — see decision 1. `ast_shape()`'s own job is
producing the compact shape string §4.3's `source_signature` needs for exact-match pattern
lookup (F5.2/F5.3, not built); classification needs to distinguish node *kind*
(FUNCTION/AGGREGATE/WINDOW/...) and read `detail`, which a shape string deliberately
discards. Reconciling the two is real future work, but conflating them now would make one
module serve two different jobs it does not yet need to serve.

**Import `packages/adapter-tableau`'s own `Family` enum and `family_of()` directly into
graph-svc.** Rejected — see decision 2. graph-svc has never imported an adapter package;
doing so here for one data table would be a new, unprecedented coupling for a fact
(Appendix B.1's default-class column) neither side needs the other's code to express.

**Treat every table calculation as C3 (simple) / C4 (complex) unconditionally, since
addressing is never resolved anywhere else in this codebase.** Rejected — see decision 3.
The data needed to resolve it for real (`Worksheet.rows_shelf`/`cols_shelf`, `ENCODES`)
already exists and is real, committed harvest data; classifying conservatively when a real
answer is one query away would understate C2 coverage for the single most common
structural-rewrite case Appendix B.1 names.

**Run reclassification automatically after every harvest.** Rejected — see decision 5. No
precedent in this codebase actually does this (`Rescorer` is wired but never auto-invoked);
inventing the hook here would be new, unproven plumbing for a trigger ("the rule set
changes") that harvest completion does not actually correspond to.
