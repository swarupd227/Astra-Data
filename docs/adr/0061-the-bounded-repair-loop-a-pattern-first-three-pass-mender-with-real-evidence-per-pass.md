# ADR 0061 — The bounded repair loop: a pattern-first, three-pass Mender with real evidence per pass

Status: accepted · 8 September 2026 · Story S8.2.1, continuing F8.2/E8

## Context

S8.2.1 opens F8.2 (Bounded repair loop), continuing E8 — the backlog's own AC: *"As a
parity engineer, I want the Mender to repair failures in at most three passes with a
pattern-first strategy, so that the loop cannot spin, cannot silently accept and every
pass is in evidence."*

- Pass 1: if an ACTIVE pattern matches the failure class and AST shape, apply it
  deterministically; pass 2: model repair with the failing evidence and the
  class-specific instructions; pass 3: same with a widened context contract
- Every repair is re-validated up the ladder and re-proved on the affected cases only
- Bound (default 3) is configurable per tenant; exhaustion routes to the Exception Desk
  with all passes attached; the MU never transitions to PASSED from within the loop
  without a proof PASS
- Passes consumed per MU is stored and reported (mean passes to pass)

§11.2 itself, verbatim: *"Classify every failing case; group by artefact... Pattern
first... Model repair otherwise... Bound. Each MU has a pass budget (default 3)... A pass
that produces no change in the failing set ends the loop early. Escalate on exhaustion,
on an UNKNOWN class after one model diagnosis, on any KEY_MISSING (which is a model
defect, not a report defect), or on a repair that makes a previously passing case fail
(the artefact is reverted first). Patternise."* §11.1 classification (S8.1.1) already
opens the `ExceptionCase` this story repairs.

## Decisions

### 1. `MenderPass` is a new, real node type — not a JSON blob on `ExceptionCase`

Confirmed by direct research: no `MenderPass`/repair-record concept existed anywhere
before this story. "Every pass is in evidence" is read literally: one `MenderPass` node
per attempt (`exception_case_ref`, `pass_number`, `strategy`, `result`, `pattern_ref`,
`measure_ref`, `cases_reproved`, `cases_still_failing`, `evidence_ref`,
`started_at`/`finished_at`), so a future Exception Desk case page (F8.3) can read a full
pass history directly rather than parsing a growing blob. `MenderPass` is genuinely absent
from spec §4.1.1's own node table (§8.10/§11.2/the glossary describe it only in prose) —
the first whole-node-type `SpecDeviation` this codebase has ever needed, since every prior
addition (`Pattern`, `Visual`, ...) was already spec-listed.

### 2. The backlog's own three-pass breakdown is a real elaboration of §11.2's "pattern first, else model repair," not a contradiction

§11.2 never says passes 2 and 3 must behave identically. `strategy_for_pass` is pure and
literal: pass 1 is always `PATTERN`; pass 2 is `MODEL`; pass 3 is `MODEL_WIDENED`; a
tenant-configured budget above three repeats `MODEL_WIDENED` for any further pass rather
than inventing a fourth, undefined strategy — disclosed, not silently capped.

### 3. A repair never reuses `patterns.apply_active_pattern` directly — it reuses only the safe, pure pieces

`apply_active_pattern` is wired for the Transpiler's own first-generation moment: it also
reclassifies the source `CalculatedField.class` to C2, a Transpiler concern this repair has
no business touching. `apply_pattern_repair` reuses `find_matching_pattern`/
`render_target`/`dax_sanity_check` verbatim (the real, already-proven AST-shape match and
render) but writes the corrected `Measure` under the Mender's own attribution.

### 4. Every repair writes a brand-new `Measure`; a "revert" is a new write of the prior DAX, never an undo

The identical convention `generation._write_measure`/`patterns.apply_active_pattern`
already both established — confirmed neither ever mutates an existing `Measure.dax`.
`_write_repaired_measure` retires the calculation's own prior live `MAPS_TO` edge and
writes a fresh one, so every correction (and every revert) keeps the same full, real
history every other artefact in this codebase already keeps.

### 5. Re-proving re-executes the target side only, and writes real `Verdict`s that are never attached to a `ParityRun`

A repair changes only what the target computes; the source data a case's own
`expected_ref` already holds has not changed, so re-executing it would be wasted work and
risk manufacturing spurious `SOURCE_DRIFT` noise from nothing — the identical "only what
changed needs re-running" reasoning "re-run only the affected cases" already gives one
level up. `reprove_cases` writes fresh `Verdict` nodes for exactly the cases touched, but
never a new `ParityRun`: `ParityRun` is a whole-workbook concept, and
`parity_dashboard.py`'s own docstring already discloses that its per-sheet counts assume
the *latest* `ParityRun` covers the *whole* live case set — a real assumption this story
deliberately does not revisit. The Mender's own re-proof stays a parallel, real fact; the
Parity Dashboard's own picture is refreshed for real the next time `:run-parity` runs for
the whole workbook.

### 6. "Configurable per tenant" is a new, real, Postgres-backed, versioned store — not a module constant

No per-tenant configuration concept exists anywhere in this codebase beyond "the graph is
the tenant" (every per-tenant store scopes rows by `graph_name` alone). A bare module
constant (`regression.MAX_CONSECUTIVE_FAILURES`'s own footing) would not satisfy
"configurable" literally. `mender_config` (migration v0030) is scoped and versioned the
identical way `tolerance_charter_version` already is — an edit is a new version, never an
overwrite.

### 7. Pass 2/3's own model call is real; `MENDER_REPAIR` is a real, registered, but permanently unroutable task class in this deployment today

`gateway.MENDER_REPAIR` is a real task class, called through the real `Gateway.generate`.
`POST /v1/model-gateway:run-eval` (S5.3.2) is hard-coded to
`generation.run_transpile_c3_eval`, so no eval set exists for this task class and
`GatewayPolicyStore.routable_providers` always returns empty for it — the identical
disclosed-absent footing `TRANSPILE_C3_SMALL_MODEL` already has. `GatewayRoutingError` is
therefore the honest, expected outcome of every model-repair pass in this deployment
today; the loop treats it as a real, recorded, non-retryable attempt and breaks rather
than spending a further pass repeating the identical failure — the same "won't become
routable within a single run" reasoning `generation._run_ladder` already gives its own
identical error.

### 8. No `ContextContract`/`ContextAssembler` registration for `MENDER_REPAIR` — `RepairContext` is a bespoke dataclass, the identical "name only" choice `generation.GenerationRequest` already made for `TRANSPILER_CALC`

Building the full fragment-validated `ContextContract`/resolution-plan machinery is real,
separate scope this story does not attempt. `ContractName.MENDER_REPAIR` exists as a label
for provenance (`ProvenanceRecord.contract`) only; `RepairContext` carries its own
`context_hash()`, assembled directly from the graph.

### 9. "Class-specific instructions" and a "widened context contract" are backlog phrases with no spec or codebase precedent — both are new, disclosed designs

`_CLASS_INSTRUCTIONS` is a real, per-§11.1-class sentence appended to the repair request's
own evidence (invented and disclosed, the identical footing `generation.CONSTRAINTS`
already has for its own constant instruction list). "Widened" (pass 3 only) means the
failing-cell sample is no longer bounded to 20 (§8.10's own "a bounded sample" becomes the
full set) and the calculation's own real dependency closure (fields, nested calculations)
is included — strictly more evidence, never different evidence.

### 10. Revert-on-regression is scoped to cases sharing the repaired artefact within the same workbook, not the whole workbook regardless of artefact

§11.2's own "a repair that makes a previously passing case fail" is checked against every
other live `ParityCase` in the *same workbook* that resolves (by name, the identical
resolution `classify_run`'s own grouping already performs) to the *same* artefact and
already has a real prior `Verdict(result="PASS")` — not every case in the workbook, which
would need re-diffing everything after every pass for a check the spec's own wording ("a
measure used by several sheets") already scopes to the artefact itself. A detected
regression reverts the artefact immediately (§11.2's own "the artefact is reverted
first"), which leaves the pass's own originally-failing case set literally unchanged — the
loop's own "no change" rule (decision 12) ends the loop that same pass, so a regression is
never retried pointlessly against the same evidence.

### 11. `KEY_MISSING` never enters the loop at all — §11.2's own literal words, not a class this story's pattern/model passes ever see

"Any `KEY_MISSING` (which is a model defect, not a report defect)" escalates immediately,
before any pass is attempted, with one `MenderPass(strategy="ESCALATE_IMMEDIATE",
result="KEY_MISSING_MODEL_DEFECT")` written for a complete evidence trail — "every pass is
in evidence" is read to cover the *decision not to attempt* a repair too, not only the
attempts themselves. Routing the model defect to the Foundry for real is S8.2.2's own
later, explicit scope; this story only makes the escalation itself real.

### 12. A pass that produces no change in the failing set ends the loop early — but only once a real repair was actually re-proved

§11.2's own literal words. Distinguished from a pass that never produced a measure at all
(no pattern match, a schema/parse failure, an unroutable gateway) — those always fall
through to the next strategy rather than ending the loop, since nothing was actually
re-proved to compare against. `MODEL_UNAVAILABLE` is its own separate, immediate break
(decision 7): an unroutable gateway will not become routable within the same run, so
falling through to `MODEL_WIDENED` would only repeat the identical failure.

### 13. `UNKNOWN` escalates after exactly one model diagnosis, never spending a widened third pass repeating it

§11.2's own literal words: "escalate ... on an UNKNOWN class after one model diagnosis."
Pass 2 (`MODEL`) is that one diagnosis; when it does not produce a `PROVED` result, the
loop breaks there rather than attempting `MODEL_WIDENED`, even with budget remaining.

### 14. Pattern matching stays AST-shape-only today, not failure-class-aware — a real, narrower reading of the AC's own words than they literally promise

`Pattern.class` (§4.3) is the Transpiler's own C1–C4 taxonomy; confirmed directly, no
property on `Pattern` carries a §11.1 failure class at all. The AC's own "matches the
failure class and AST shape" is followed only for the AST-shape half; backlog story
S8.2.3 (not this one) is what generalises a successful repair into a pattern keyed by
(failure class, AST shape) — until it exists, pass 1 matches by AST shape alone
(`patterns.find_matching_pattern`, reused verbatim). Patternising a successful repair
(§11.2's own last bullet, "every successful repair is written as a Pattern candidate") is
that same S8.2.3's own scope, not built here.

### 15. A real, previously-latent migration gap, discovered (not caused) by this story's new node type: only `v0001` has ever created AGE vlabels

`v0001_estate_graph.py` is the only migration that has ever called
`create_vlabel`/`create_elabel`; every purely additive ontology change since has correctly
needed no migration of its own (an additive property or enum value has nothing for AGE to
alter). That reasoning does not extend to a whole new node type on an *existing* graph:
AGE only recognises an explicitly created label, and the runner applies `v0001` exactly
once. Confirmed directly against this repository's own long-lived local
`astra_estate_test` graph: `MenderPass` was the only missing label, every other node/edge
type already present from whichever earlier point that graph was last created fresh.
`v0031_ensure_current_labels.py` closes this — the identical idempotent "create only what
is missing" check `v0001` already performs, re-run once more as its own migration so it
actually executes against a graph already past `v0001`. This is also the correct answer
for a real production in-place schema upgrade, not only this local database.

## Consequences

- New `services/graph-svc/src/astra_graph/mender.py`: `MenderConfig`/
  `MenderConfigStore`/`PostgresMenderConfigStore`/`InMemoryMenderConfigStore`,
  `strategy_for_pass`, `RepairContext`, `RepairResponseSchema`, `MenderPassOutcome`,
  `assemble_repair_context`, `apply_pattern_repair`, `call_model_repair`,
  `reprove_cases`, `check_and_revert_regressions`, `mend_exception`/`MenderService`.
- New `services/graph-svc/src/astra_graph/api/routes_mender.py`: `POST
  /v1/exceptions/{case_id}:mend` (`ParityEngineerDep`, the AC's own persona). Reading
  stays on the existing `GET /v1/exceptions` route (S6.2.1) — `passes_consumed` is
  already carried by its generic `{"id": case_id, **properties}` view.
- Ontology: `ExceptionCase` gains `passes_consumed` (INT); new `MenderPass` node type
  (§8.10/§11.2) with two new declared `SpecDeviation`s (a property and, for the first
  time, a whole node type); `SCHEMA_VERSION` 31 → 32.
- New migrations: `v0030_mender_config.py` (the per-graph pass-budget store);
  `v0031_ensure_current_labels.py` (closes decision 15's own gap).
- `gateway.py`: `MENDER_REPAIR: TaskClass` registered, disclosed permanently unroutable.
  `context/contract.py`: `ContractName.MENDER_REPAIR` added as a "name only" entry.
- `main.py`: `app.state.mender = MenderService(...)`, wired alongside the existing
  `ClassificationService`/`RegressionService`; `mender_router` included.
- Verified: 22 new pure unit tests (`strategy_for_pass`, the config store contract,
  `RepairContext`/`MenderPassOutcome` round-trips, `RepairResponseSchema`, every §11.1
  class resolving to a real instruction, `call_model_repair` against a real
  `StaticGateway`-wrapped scripted caller for its `OK`/`SCHEMA_ERROR`/`PARSE_ERROR`/
  `MODEL_UNAVAILABLE` outcomes); 14 new integration tests against real PostgreSQL +
  Apache AGE (a real ACTIVE pattern closing a case in one pass; a real model pass closing
  one when no pattern matches; a real, permanently-unroutable `null_gateway()` escalating
  as `MODEL_UNAVAILABLE`; `KEY_MISSING` escalating immediately with zero repair attempts;
  full-budget exhaustion writing one real `MenderPass` per attempt and staying OPEN; a
  configurable budget genuinely shortening the loop; a no-change pass ending the loop
  early; `UNKNOWN` escalating after exactly one model diagnosis; a real regression on a
  sibling sheet detected and reverted; refusing a non-OPEN or nonexistent
  `ExceptionCase`; the new route's own real role gate); the full existing graph-svc suite
  green alongside them (600 integration tests, 1386 non-integration tests); `ruff`/`mypy`
  clean; `ontology_check.py --spec`/`--generated` and `migration_check.py` all pass.

## Alternatives considered

**Call `patterns.apply_active_pattern` directly for pass 1, rather than a parallel
`apply_pattern_repair`.** Rejected — see decision 3. `apply_active_pattern` also
reclassifies the source `CalculatedField.class` to C2, a Transpiler-specific side effect
this repair has no business producing.

**Re-execute the source side too on every re-proof, matching a full `:run-parity`.**
Rejected — see decision 5. The source has not changed; re-executing it would be wasted
work and risk a spurious `SOURCE_DRIFT` finding from nothing a repair actually touched.

**Fabricate a fourth, "escalated" strategy name for a budget configured above three,
rather than repeating `MODEL_WIDENED`.** Rejected — see decision 2. §11.2 names exactly
three strategies; inventing a fourth would be a real design decision this story has no
basis to make, disclosed and deferred rather than guessed at.

**Scope revert-on-regression to the whole workbook, not just cases sharing the repaired
artefact.** Rejected — see decision 10. §11.2's own wording ("a measure used by several
sheets") already scopes the concern to the artefact; re-diffing every other case in the
workbook after every pass would be real, unbounded, unnecessary cost.

**Route `KEY_MISSING` to a stub "Foundry" endpoint now, rather than only escalating.**
Rejected — see decision 11. The Foundry (source-model-defect routing) is S8.2.2's own
later, explicit backlog scope; building a stub for it here would be scope this story was
not asked to take on, and a stub nobody can call for real is worse than a disclosed gap.

**Leave the `v0001`-only vlabel-creation gap unfixed, treating it as a one-off local
database staleness issue to work around by hand.** Rejected — see decision 15. The same
gap would recur for every future story that adds a node type against any graph that has
already passed `v0001`, including a real production deployment doing an in-place schema
upgrade; a small, idempotent, permanent migration is the honest fix, not a one-time manual
workaround.

## Open questions for the product owner

- Pattern matching is AST-shape-only today (decision 14), a real, narrower reading than
  the AC's own "matches the failure class and AST shape." Should S8.2.3 (pattern
  candidates from a successful repair) also add a failure-class dimension to
  `Pattern.source_signature` itself, or is AST-shape-only sufficient given a pattern's
  own render is already deterministic regardless of which failure class led to it?
- `MODEL_UNAVAILABLE` (decision 7) breaks the loop immediately rather than falling
  through to `MODEL_WIDENED`. Once a real, routable provider exists for `MENDER_REPAIR`,
  should a *routing* failure specifically (as opposed to a schema/parse failure from a
  real response) still count toward `passes_consumed`, or should an escalation on
  routing alone be excluded from "mean passes to pass" reporting as a platform gap
  rather than a genuine repair attempt?
- `check_and_revert_regressions` (decision 10) re-proves every other live case sharing
  the repaired artefact in the workbook, not only ones with a recent `PASS`. For a
  workbook with many sheets sharing one heavily-used measure, is re-proving all of them
  on every pass an acceptable cost, or should a future story bound this the way
  `assemble_repair_context`'s own failing-cell sample is bounded?
