# ADR 0042 — The Pattern Library: a real shape fix, generalisation, and deterministic promotion

Status: accepted · 5 September 2026 · Story S5.5.1 (F5.5, opening it)

## Context

S5.5.1 opens F5.5 (Pattern Library and promotion), following E5's F5.1–F5.3 close: *"As a
platform engineer, I want a proved C3 transformation to become a candidate pattern
automatically, so that the platform gets faster and more deterministic as the programme
runs. When a GENERATED_PROVED artefact passes proof, its (source AST shape, target
template, guards) tuple is generalised and stored as a Pattern in CANDIDATE state, keyed by
AST shape hash. Promotion CANDIDATE → ACTIVE requires N distinct proof passes (default 5),
zero failures, and a Platform Engineer approval (MA-11, L2). ACTIVE patterns are applied
deterministically ahead of any model call; the class of the field is re-evaluated to C2
with pattern_ref."*

Research before any code was written surfaced a real, pre-existing defect this story's own
correctness depends on fixing, not working around: `context/signature.py`'s `ast_shape()` —
the function every "AST shape" claim in this codebase already routes through — was written
against a hypothetical generic AST format from before the real Tableau calculation grammar
existed (S1.3.1, before F2.3/S2.3.1). Run against the real, production wire shape
(`packages/adapter-sdk`'s `CalcNode`: `kind`/`name`/`value`/`children`/`detail`, the exact
shape `classify.py`/`rules.py` already consume), it could not tell `kind`/`name` apart from
any other string field, so it rendered both as an opaque `<str>` literal — confirmed by
direct execution, not assumed: `SUM([Notional]) / SUM([Margin])` and
`SUM([Notional]) + SUM([Margin])` (different operators, different fields) collapsed onto
the identical shape string. Every existing caller has been silently affected since S1.4.2
(`lineage.calc_shapes`, feeding S3.1.1's own Cartographer clustering evidence) and
S5.3.1/S5.3.2 (`generation._matching_patterns`, `context.assembler._patterns`, both moot
until a Pattern existed to match). A second research pass confirmed the fix's own scope
directly against the wire contract: `astra_adapter.calc.NodeKind` has exactly nine values,
and `classify.py`'s own dispatch already names all nine.

The rest of the design followed directly from what already exists: `Pattern`'s own ontology
declaration (`promotion_state` already `CANDIDATE`/`ACTIVE`/`RETIRED`; `source_signature`/
`target_template` already typed correctly), the Transpiler's own already-wired,
previously-moot pattern-matching read paths, `rules.py`'s own `{table}`-placeholder
convention and `RuleMeta.guards`' own "descriptive, not evaluated" precedent, and
`calibration.py`'s own append-only-observation-table precedent for "N distinct proof passes
... zero failures." One genuine gap needed a product decision by inheritance rather than a
fresh question: no Migration Unit exists anywhere in this codebase (confirmed a second time,
identically to S5.4.1's own finding), so §9.3's "applied to at least five distinct MUs" has
no real MU to count — the same disclosed-proxy reasoning the product owner already chose for
S5.4.1 (attach to the nearest real, existing record) extends here without needing to be
asked again: a `CalculatedField`'s own id is what "distinct" counts.

## Decisions

### 1. `context/signature.py` is fixed for the real wire shape, not worked around

A new, kind-discriminated dispatch (`_render_wire_node`) runs ahead of the existing generic
key-based walk, recognising all nine real `NodeKind` values by name: `REFERENCE` is the sole
leaf-with-an-identifier kind, `LITERAL` the sole leaf-with-a-value kind, `UNKNOWN` an opaque
token (always C4, never reaches a `GENERATED_PROVED` artefact to generalise from), and
`FUNCTION`/`AGGREGATE`/`OPERATOR`/`CONDITIONAL`/`CAST`/`WINDOW` all share one operator shape
(`name` is the operator, `children` its arguments). `detail`/`value` (except on LITERAL) are
deliberately excluded — they carry classifier metadata (§9.1's own family tag, table-calc
addressing) that must not make two calls to the same function look like different shapes.
The existing generic walk is untouched and still runs for anything that is not this shape
(confirmed against `test_integration_cartographer.py`'s own fixture, which predates the real
grammar and still uses the generic key convention — zero regression). A new
`capture_identifiers()` exposes the placeholder → real-identifier mapping the Pattern
Library needs for substitution, alongside the existing `ast_shape()`/`signature_of()`.

### 2. "Keyed by AST shape hash" is shape equality, not a literal hash comparison

`find_matching_pattern` reuses `signature.matches()` — already built for the Transpiler's
own context contract (S1.3.1) — directly, the same real-not-hashed comparison
`generation._matching_patterns`/`context.assembler._patterns` already make. A hash
(`context_hash` over the shape string) is only useful as an index key, not as the
comparison itself, and nothing here needs indexing: the Pattern Library is read whole
(`MAX_PATTERNS = 5_000`, the identical "a library, not an estate" bound
`context.assembler`'s own pattern-matching read already uses).

### 3. No Migration Unit exists, so a proof's own `CalculatedField` id is the disclosed proxy for "distinct MU"

Confirmed a second time, not assumed from S5.4.1's own prior finding: §4.1.1 declares no
`MigrationUnit` node, and no story before this one has ever created a real MU record.
`pattern_observation`'s own `calc_id` column is the nearest real thing this platform has to
"which MU proved this" — the same "attach to what already exists" choice the product owner
made explicitly for S5.4.1's own MU-shaped gap, extended here without a fresh question since
the underlying fact (no MU exists) has not changed.

### 4. Promotion eligibility is checked against a real, append-only observation table — never a maintained counter

`public.pattern_observation` (migration v0023) is `calibration_observation`'s own precedent,
exactly: one row per real proof pass or failure, never overwritten. `Pattern.pass_count`
(already declared) becomes a point-in-time snapshot (written at creation and at promotion),
disclosed as such in its own ontology docstring — the authoritative answer to "is this
pattern eligible" is always computed live from the raw table (`promotion_status`), the
identical "computed from the raw table on read" footing `calibration.report()` already set,
so eligibility can never drift from a counter that forgot to increment.

### 5. `Pattern.guards` is a real ontology gap, fixed as a declared deviation — descriptive, not evaluated

§4.1.1's own node table lists `Pattern` with no `guards` property at all; §4.3's worked
example (a narrative section, not the §4.1.1 table) already shows one. The AC's own tuple —
"(source AST shape, target template, guards)" — needs a real place to write the third
element, so `guards` (`T.STRING_LIST`) is added, declared as a `SpecDeviation` (schema
version 19). Guards are inferred modestly and honestly: for each capture this platform can
resolve to a real `Field`/`Parameter` with a known datatype, a plain descriptive string
(`"a is real"`) — never fabricated for a capture it cannot resolve. Matching stays
exact-shape only; §9.3's own "guarded on types and model context" is not built as a real
evaluation engine, the identical "descriptive, never enforced by the renderer" footing
`rules.RuleMeta.guards` already has.

### 6. Generalisation reuses an existing pattern rather than ever duplicating one

`generalise_from_proof` looks up an existing CANDIDATE/ACTIVE pattern for a shape first; if
found, it records another real pass observation against it (this is how a CANDIDATE ever
accumulates the AC's own "N distinct proof passes"); only an unmatched shape creates a new
CANDIDATE. `target_template` is generalised from the model's own real, generated DAX text by
a text substitution (`_abstract_template`, the reverse of `render_target`) — an honestly
limited generaliser, not a DAX parser: if the model's own output did not use this platform's
`[Name]` bracket convention verbatim for a captured reference, that occurrence stays baked
into the template as literal text rather than becoming a placeholder. Identifiers are
substituted longest-first so one name being a substring of another's cannot corrupt an
unrelated occurrence.

### 7. "Zero failures" is a real, checked fact — both generation paths feed it

A ladder failure (the normal, model-served path exhausting attempts or declining) records a
real failure observation against any existing candidate/active pattern for that shape, and a
deterministic ACTIVE-pattern application whose own rendered DAX fails even
`rules.dax_sanity_check`'s structural stand-in records one too (rare — the template came
from an already-proven artefact — but checked, not assumed away) and falls back to the
normal model-call path rather than blocking the field on one pattern's own hiccup. Without
this, "zero failures" would be vacuously true forever, since nothing before this decision
ever recorded a failure against a pattern at all.

### 8. Deterministic application is the single choke point already in front of every model call

`generate_c3_field` — already the sole per-field entry point to a model call — checks for a
matching ACTIVE pattern immediately after confirming a field classifies C3, before
`build_generation_request`/`_run_ladder` are ever reached. A match renders the pattern's own
`target_template` against this specific calculation's real captures (`render_target`, the
`{table}`-placeholder convention `rules.py`/§4.3's own worked example already established,
left untouched since it names no capture of this AST), writes a real Measure/MAPS_TO/
DETERMINISTIC provenance record, and re-evaluates the source `CalculatedField`'s own
`class`/`pattern_ref` in place via `writer.set_node_properties` — the one real record
`classify.py` ever wrote a field's class to, satisfying the AC's own "the class of the field
is re-evaluated to C2 with pattern_ref" directly rather than requiring a separate
reclassification pass to notice it later. No change was needed to `classify.py` itself:
threading pattern-awareness through its own `ClassificationContext`/`_worse()` machinery
(built only to move *toward* a harder class) would have needed new downgrade logic there for
no benefit `generate_c3_field`'s own single choke point does not already give for free.

### 9. Promotion is the Platform Engineer's, reusing the existing role dependency — MA-11, autonomy ceiling L2

§13.2's own Release 1 autonomy ladder names `MA-11 Promote pattern to ACTIVE` at ceiling
`L2` ("Platform Engineer approves") — `PlatformEngineerDep` (already built for S5.2.1's
apply-rules route and S5.3.2's eval-run route) is reused directly rather than a new
dependency; its own docstring is broadened to name this third use rather than staying
narrowly worded to the first. `promote_pattern` re-checks eligibility server-side against
the real observation history — never trusting a caller's own claimed count — and raises
`PatternPromotionError` naming exactly why when it is not met.

## Consequences

- `context/signature.py`: real wire-shape dispatch (`_render_wire_node`), a new
  `capture_identifiers()` export; the pre-existing generic walk is unchanged.
- `ontology/nodes.py`: `Pattern.guards` (`T.STRING_LIST`), schema version 19 (up from 18);
  `Pattern.pass_count`'s own docstring clarified as a snapshot, not a live counter. New
  `SpecDeviation` entry.
- New migration v0023: `public.pattern_observation` (append-only, platform table, no
  ontology change of its own).
- New module `patterns.py`: `find_matching_pattern`, `render_target`,
  `generalise_from_proof`, `record_observation`, `promotion_status`, `promote_pattern`,
  `apply_active_pattern`, `list_patterns`.
- `generation.py`: `generate_c3_field` gains the ACTIVE-pattern short-circuit and the
  success/failure observation hooks; `GenerationOutcome` gains `pattern_id`; `_matching_
  patterns`'s own now-stale "honestly moot today" docstring corrected.
- `api/deps.py`: `require_platform_engineer`'s docstring broadened to name this third use.
- New route file `routes_patterns.py`: `GET /v1/patterns`, `GET /v1/patterns/{id}
  :promotion-status`, `POST /v1/patterns/{id}:promote`.
- `main.py`/`api/__init__.py`: the new router wired in.
- New unit tests: `test_signature.py` (the fix itself — none existed before this story),
  `test_patterns.py` (template substitution both directions, promotion-status arithmetic).
- New integration tests (`test_integration_patterns.py`): generalisation from a real
  `GENERATED_PROVED` success; a second, independent proof of the same shape reusing the
  pattern as a distinct pass; a real ladder failure recording a real failure against an
  existing pattern; promotion refused below threshold, refused with any recorded failure
  even at threshold, succeeding once both conditions are real; and the story's own payoff —
  an ACTIVE pattern applied to a brand-new field with a poisoned gateway that raises if ever
  called, proving the model path was genuinely never taken, not merely unobserved.

## Alternatives considered

**Leave `context/signature.py` as it was and build Pattern matching on top of it anyway.**
Rejected — every calculation of one operator/family would have matched every pattern of the
same nesting depth regardless of actual content, which is not "matched by AST shape" in any
meaningful sense; shipping this story on top of a confirmed-broken shape function would mean
shipping broken pattern matching.

**Thread pattern-shape awareness through `classify.py`'s own `ClassificationContext`/
`_worse()`, matching the parameter-dependency/table-calc-addressing precedent.** Rejected —
see decision 8. `_worse()` only ever moves a classification toward the harder class; a
pattern match needs the opposite direction (C3 → C2), which would need new logic there for
no benefit `generate_c3_field`'s own single per-field choke point does not already give.

**A literal `context_hash` comparison as the actual pattern-matching mechanism, rather than
shape-string equality.** Rejected — see decision 2. A hash is a good index key, not a
comparison; `signature.matches()` already exists, already correct, already used by two other
call sites.

**Machine-evaluate `guards` against real model-context/type facts (§9.3's own "guarded on
types and model context").** Rejected for this story — see decision 5. A real evaluation
engine is substantial, unbuilt scope; `RuleMeta.guards`' own precedent (descriptive, never
enforced) is the identical, already-accepted posture this codebase gives a guard today.

**Skip failure-observation recording for the model-served ladder path, since only the
deterministic-application path was named "guarded" in the AC's own bullet 3.** Rejected —
see decision 7. Without it, "zero failures" (bullet 2) would never be able to be false in
practice, since a CANDIDATE pattern's own passes come mostly from the model-served path in
this story's real usage — a promotion gate that can never fail is not a gate.
