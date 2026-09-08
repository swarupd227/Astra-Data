# ADR 0063 — A repair made once becomes a rule: failure-class-keyed pattern generalisation

Status: accepted · 8 September 2026 · Story S8.2.3, continuing F8.2/E8

## Context

S8.2.3 continues F8.2 (Bounded repair loop), continuing E8 — the backlog's own AC: *"As
a platform engineer, I want successful repairs to feed the pattern pipeline, so that a
repair made once becomes a rule."*

- A model repair that passes proof is generalised into a CANDIDATE pattern keyed by
  (failure class, AST shape) and follows F5.5 promotion

This closes a gap S8.2.1's own docstring explicitly deferred here, verbatim: *"Pattern
matching is AST-shape-only today, not failure-class-aware... Backlog story S8.2.3 (not
this one) is what generalises a repair into a CANDIDATE pattern keyed by (failure class,
AST shape)... Patternising a successful repair (§11.2's own last bullet) is that same
S8.2.3's own scope, not built here."* §11.2's own last bullet, verbatim: *"Patternise.
Every successful repair is written as a Pattern candidate with the failure class as part
of its signature."*

## Decisions

### 1. `Pattern.class` and the new `Pattern.failure_class` are two real, orthogonal axes — never merged into one

`Pattern.class` (§4.3) is the Transpiler's own C1-C4 taxonomy, confirmed unrelated to
§11.1's failure taxonomy since S8.2.1's own research. Rather than overload `class` or
fold a failure class into `source_signature`'s own JSON (which `generation.py`'s own C3
pathway also writes and reads, with no failure-class concept at all), `Pattern` gains a
new, optional, additive `failure_class` property (ENUM, the identical `_FAILURE_CLASSES`
set `ExceptionCase.class` already uses). A Mender-generalised pattern from a proved
repair still gets `class="C3"` (the identical value `generation.generate_c3_field`
already writes for its own GENERATED_PROVED artefacts — structurally the same kind of
fact: model-produced DAX for an AST shape, passed proof, worth generalising) and, new
this story, `failure_class` naming which §11.1 class it repairs.

### 2. `find_matching_pattern`/`generalise_from_proof` are widened with one optional keyword each — the same real mechanism, not a second one

Both gain `failure_class: str | None = None`. Every existing caller
(`generation.generate_c3_field`, both call sites) passes no value, so the pattern
library behaves identically for the Transpiler's own first-generation pathway — proven
directly by the full existing `test_integration_patterns.py` suite passing unchanged.
`mender.py` is the one new caller that ever passes a real value.

### 3. `find_matching_pattern`'s own failure-class preference is real but not exclusive — a shape match with no (or a different) failure_class still matches as a fallback

Promotion state (ACTIVE over CANDIDATE) stays the primary sort key, unchanged from
S8.2.1; an exact `failure_class` match becomes a secondary preference within each state
tier. This is a deliberate choice over an exclusive filter: an AST-shape match from a
*different* (or absent) failure class can still be the correct fix for the current
failure — the same real capability S8.2.1 already shipped and tested — so narrowing to
"exact failure-class match or nothing" would have been a real regression, not a
refinement. `apply_pattern_repair` (Mender pass 1) now passes this exception's own real
failure class through; `generation.generate_c3_field`'s own call passes none, unchanged.

### 4. `generalise_from_proof` still reuses one Pattern per AST shape — a `failure_class` match is preferred for reuse, but a shape match without one still absorbs the new proof pass rather than fragmenting into a near-duplicate

Calling the widened `find_matching_pattern` internally for its own "does this shape
already have a pattern" check means a first, plain C3 generation (no failure_class) and
a later Mender-sourced generalisation of the identical shape reuse the *same* node,
accumulating real, distinct proof passes toward the *one* pattern's own promotion
threshold — "N distinct proof passes" (§9.3) stays meaningful and undiluted. Only when
*no* shape match exists at all does a new Pattern get written, and only then does it
carry `failure_class`. `Pattern.failure_class` itself is never edited after creation —
the same "write once, reuse the node, never mutate a fact after the fact" discipline
every prior Pattern property already has.

### 5. Generalisation happens only for a proved MODEL/MODEL_WIDENED pass — never for a PATTERN-strategy success

A PATTERN-strategy pass that proves already reused an *existing* Pattern; generalising
it again would derive the identical (shape, template) tuple from itself — a real
no-op dressed up as new evidence, not "a repair made once becomes a rule" (which is
about a *model-derived* fix earning a rule for the first time). `mend_exception` checks
`strategy != "PATTERN"` before ever calling `generalise_from_proof`, alongside `result ==
"PROVED"` (never for `STILL_FAILING`/`REGRESSED`/an error result) and `calc is not None`
(a real formula_ast to key off).

### 6. F5.5 promotion is completely untouched — "follows F5.5 promotion" is read literally

`promote_pattern`/`record_failure_and_maybe_retire`/`evaluate_retirement` (S5.5.2/S5.5.3)
receive zero changes. A Mender-generalised CANDIDATE is promoted by a Platform Engineer's
own approval once it clears the identical objective threshold (default 5 distinct proof
passes, zero failures) every other CANDIDATE already needs, and is retired automatically
on the identical dual failure condition. Nothing about *how* a Pattern was generalised
changes what a proof pass or a failure against it means to the pipeline that already
exists — the story's own scope is entirely upstream of promotion, not a parallel gate.

### 7. `MenderPass.pattern_ref` gains a second, real meaning, disclosed on the property itself rather than left implicit

Pass 1's own `pattern_ref` already meant "the ACTIVE Pattern applied." A proved MODEL/
MODEL_WIDENED pass now also sets `pattern_ref` — to the Pattern the repair was
generalised into (a new CANDIDATE, or an existing shape match reused). Both are "a real
Pattern id this pass is associated with," the same underlying concept; the ontology note
states both meanings explicitly rather than let a reader assume `pattern_ref` implies
"applied" universally.

## Consequences

- `patterns.py`: `find_matching_pattern`/`generalise_from_proof` gain an optional
  `failure_class` keyword each; module docstring records the widening and its own
  reasoning.
- `mender.py`: `apply_pattern_repair` gains a required `failure_class` parameter, passed
  from `mend_exception`'s own real `failure_class`; a proved MODEL/MODEL_WIDENED pass
  calls `generalise_from_proof` with it and records the returned Pattern id as the
  pass's own `pattern_ref`. Module docstring updated; the "AST-shape-only, not
  failure-class-aware" gap it previously disclosed is now closed and says so.
- Ontology: `Pattern` gains `failure_class` (STRING enum, additive); `MenderPass.
  pattern_ref`'s own note widened to its second meaning; `SCHEMA_VERSION` 33 -> 34, one
  new declared `SpecDeviation`. No migration file — additive only, confirmed by
  `tools/migration_check.py` itself.
- No new route, no new role, no console change — every surface this story touches
  (`patterns.py`, `mender.py`) is already reachable through the existing `:mend` route
  and the existing Pattern Library routes (`promote_pattern`'s own HTTP surface,
  untouched).
- Verified: minimal new *pure* surface (the widening is a signature change plus a
  conditional call, not new arithmetic to unit-test in isolation — disclosed honestly
  rather than padded with contrived pure tests); 5 new integration tests in
  `test_integration_patterns.py` against real PostgreSQL + Apache AGE (a real
  `failure_class` written and read back; the additive keyword's absence leaving it
  genuinely absent; an existing shape match reused regardless of failure_class, recording
  a real further distinct proof pass; `find_matching_pattern`'s own real preference
  ordering, proven directly against two live patterns sharing one shape); one new,
  comprehensive integration test in `test_integration_mender.py` proving the whole story
  arc end to end against real PostgreSQL + Apache AGE (a proved MODEL repair generalises
  a real CANDIDATE; a second, genuinely distinct calculation proves against the same
  pattern, real evidence it is not yet promotable; F5.5's own real, unchanged promotion
  pipeline promotes it; a third case's own pass 1 then applies it deterministically, no
  model call reached); the full existing graph-svc suite green alongside them; `ruff`/
  `mypy` clean; `ontology_check.py --spec`/`--generated` and `migration_check.py` all
  pass.

## Alternatives considered

**Fold `failure_class` into `Pattern.source_signature`'s own JSON blob rather than a new
property.** Rejected — see decision 1. `source_signature`/`signature_of`/`matches` are
shared, unmodified machinery the Transpiler's own C3 pathway also uses; widening their
own shape would have coupled two genuinely unrelated concerns (an AST-shape comparison
utility, and a §11.1-specific fact) for no real benefit over an orthogonal property.

**Make `find_matching_pattern`'s own `failure_class` an exclusive filter (only an exact
match counts) rather than a preference with fallback.** Rejected — see decision 3. This
would have silently regressed S8.2.1's own already-shipped, already-tested capability (an
AST-shape match from any pattern, regardless of class, still being a real, useful
repair candidate) for a purity the AC's own words do not actually demand.

**Generalise from every proved pass, PATTERN-strategy included.** Rejected — see
decision 5. A PATTERN-strategy proof is not new evidence about a *model-derived* fix; it
is confirmation an already-generalised pattern still works, which S8.2.1's own
`record_observation` call inside `apply_pattern_repair`... actually does not record one
either (confirmed: `apply_pattern_repair` never calls `record_observation`) — a real,
disclosed, separate gap this story does not attempt to close, since the AC's own words
name "a model repair," not "any successful repair" (see the open question below).

## Open questions for the product owner

- `apply_pattern_repair` (pass 1, unchanged by this story) still does not record a
  `pattern_observation` row for its own successful deterministic application — confirmed,
  a real, pre-existing gap this story's own narrow scope ("a model repair... is
  generalised") does not touch. Should a future story record a real PASS observation
  there too (strengthening an ACTIVE pattern's own evidence with genuine re-proof
  results, not just `apply_active_pattern`'s own weaker structural-sanity-check
  observation the Transpiler's pathway already records), or does §11.2's own literal
  "successful repairs feed the pattern pipeline" only ever mean the model-repair half?
- `generalise_from_proof`'s own reuse-by-shape-first behaviour (decision 4) means a
  pattern first generalised for one failure class can silently absorb proof passes from
  a *different* failure class repairing the identical shape, without ever recording which
  class each individual pass addressed (only the node's own single `failure_class`,
  fixed at creation). Is a single failure_class per Pattern the right long-term model, or
  should a future story track failure_class per `pattern_observation` row instead, so a
  pattern's own "which classes has this actually fixed" is a real, queryable history
  rather than one fixed label from its first-ever generalisation?
