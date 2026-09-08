# ADR 0062 — Routing a model defect to the Foundry: real graph evidence, two narrow triggers

Status: accepted · 8 September 2026 · Story S8.2.2, continuing F8.2/E8

## Context

S8.2.2 continues F8.2 (Bounded repair loop), continuing E8 — the backlog's own AC: *"As
a model engineer, I want a failure diagnosed as a model defect to be routed to the
Foundry, not patched in the report, so that the fix lands where the cause is."*

- KEY_MISSING with graph evidence of a missing dimension member, or AGGREGATION with a
  grain mismatch at the model, opens a Foundry change request and sets the MU BLOCKED on
  the family
- The Mender never edits TMDL directly in R1

§11.3's own worked decision text, verbatim, is the mechanism this story makes real:
*"model defect (route to the Foundry as a change to the family; the MU returns to
BLOCKED)."* §11.1's own KEY_MISSING row already gives the same instruction as its fix
path: *"Model repair via Foundry (not per report)."* §12's own glossary: *"Foundry: the
workflow that turns a family into an approved, built semantic model."*

## Decisions

### 1. Neither AC trigger is a signal `classification.py`'s own §11.1 classifier already computes — both are new, real checks in a new module, `foundry_routing.py`

Confirmed by direct reading: `KEY_MISSING`'s own signal is `{missing_keys, extra_keys}`
(counts); `AGGREGATION`'s own signal is `{totals_fail, row_count_within_tolerance,
expected_row_count, candidate_row_count}`. Neither carries any model-side fact at all.
Rather than widen `classification.py` itself (F8.1's own closed scope) or hand-wave the
AC's own "graph evidence"/"at the model" language, `foundry_routing.detect_model_defect`
is a second-stage, graph-coupled check the Mender runs before ever attempting a repair
pass — checked for both classes at the very top of `mend_exception`, ahead of S8.2.1's
own unconditional KEY_MISSING escalation.

### 2. "A missing dimension member" is read as: a grain field with no real `Field -> ModelTable` binding at all — the strongest real evidence this platform's own graph can give, honestly narrower than the AC's own words

This platform's graph has never harvested member-level *data* (a specific value like
"EMEA") anywhere — only schema. A literal "which member is missing" is therefore not
answerable from any real fact this codebase holds. What *is* real and checkable: whether
the case's own grain field has a real `MAPS_TO` edge into a `ModelTable` at all
(`case_execution._table_map_for_sheet`, reused verbatim — the identical "honestly empty
in every real deployment today" fact that module's own docstring already discloses six
times over). A dimension the model never wired to a table cannot carry *any* of that
dimension's own members on the target side, for any case that uses it — a real,
structural, model-level absence, not a report-side repair a Pattern or a DAX rewrite
could ever fix.

### 3. "A grain mismatch at the model" is read as: the case's own grain names a dimension the family's own real candidate grain does not

`ModelFamily.grain` (`"Candidate grain inferred from member sheets; confirmed at G2"`) is
written as a comma-joined field-name list (`cartographer._family_properties`:
`", ".join(proposal.grain)`) and already has a real, established reverse parse
(`cartographer._family_summary`'s own `[part.strip() for part in grain.split(",") ...]`,
mirrored here as `_family_grain_fields` — a private helper of a different epic's own
module, duplicated rather than imported, the same precedent
`case_derivation._worksheet_field_index` already set). Both `ModelFamily.grain` and
`ParityCase.grain` are drawn from the identical `rows_shelf`/`cols_shelf` vocabulary
(confirmed: `cartographer._sheet_dimensions` is what `candidate_grain` aggregates from),
so a set comparison between them is apples to apples, not a fuzzy text match. A case
whose own grain needs a dimension the family's own chosen grain never named is real,
structural evidence the model itself is coarser than the report.

### 4. Resolving "the family" is a new reverse lookup — `IN_FAMILY`, walked backwards for the first time in this codebase

`ExceptionCase.mu_ref` names a workbook; every existing family read
(`cartographer.get_family`) already goes family -> members, never the other way.
`_family_for_workbook` reads the real `IN_FAMILY` edge (`workbook --IN_FAMILY-->
family`) directly. `None` — honestly, not an error — when no Cartographer run has ever
clustered this workbook: nothing real exists yet to check either trigger against, so the
case falls through to whatever S8.2.1 already does for its own class.

### 5. "Opens a Foundry change request" reuses `model_lifecycle.request_new_version` verbatim — S4.3.3's own already-shipped mechanism, not a new one

Its own docstring already anticipated this exact caller: *"a Mender repair or a design
change does not regress what is live."* It only succeeds when the family's current
version is `PUBLISHED`; `route_to_foundry` reads the family's own live state first and
calls it only then. When the family is not `PUBLISHED` (already `DRAFT`/`IN_REVIEW`/
`APPROVED`/`BUILT` — a change is already in flight, or nothing has ever published), no
second, colliding change request is opened; the exception is still marked BLOCKED,
honestly disclosed (`ALREADY_IN_FOUNDRY`) as joining whatever is already in progress
rather than a fabricated new one. A real, if rare, race (another caller moves the family
off `PUBLISHED` between the read and the call) is caught and folded into the identical
honest outcome rather than left to crash the caller.

### 6. "Sets the MU BLOCKED on the family" is `ExceptionCase.state = "BLOCKED"` — a real, literal write, not a proxy

No real Migration Unit node or §3.2 state machine exists anywhere in this codebase
(confirmed, the same finding ADR 0041/0048/0055/0060/0061 have each already made
independently); `ExceptionCase` is the one real work-item mechanism this platform has for
an MU's own failing state, the identical choice §11.1 classification (S8.1.1) and the
bounded repair loop (S8.2.1) already made. Unlike ADR 0041's own disclosed proxy (the
*absence* of a property standing in for BLOCKED, since C4 redesign had no positive state
to write), `ExceptionCase.state` is a plain, unconstrained `T.STRING` — confirmed, no
enum — so `"BLOCKED"` is written directly and literally, a real positive fact rather
than an inferred one, reusing exactly the property S8.2.1's own `"OPEN"`/`"CLOSED"`
already established the convention for.

### 7. The Mender never edits TMDL directly in R1 — confirmed by direct grep, not merely asserted, and the backlog's own words show this AC is drawing a real boundary against a named, deferred capability

`mender.py` imports nothing from `target_contract`/`tmdl` anywhere; `route_to_foundry`
calls `request_new_version` alone, which itself never calls `emit_tmdl`/
`TargetAdapter.commit` either — only `build.build_family` (the Semantic Model Engineer's
own separate, later, explicitly-triggered action) ever emits TMDL. The backlog's own
§7.3 open question (*"should the Mender be allowed to edit TMDL under L2 in R1... current
answer: Foundry-only in R1"*) and §6.3's own R1.1 roadmap (*"Mender TMDL edits under
L2"*) confirm this is a real, already-discussed, explicitly-deferred capability the AC
pre-emptively rules out — not a restatement of something no design ever considered.

### 8. No new route, no new role gate — the existing `POST /v1/exceptions/{case_id}:mend` (`ParityEngineerDep`) already covers it

Routing is checked automatically, inside `mend_exception`, the moment a Parity Engineer
runs the Mender on a qualifying case. The AC's own persona ("model engineer") is who acts
*next* — reviewing and completing the DRAFT change request `request_new_version` already
opens, through the existing, already-gated `SemanticModelEngineerDep` surface
(`routes_modeller.py`) — not who triggers the routing itself. `GET /v1/exceptions`
(S6.2.1) already serves `ExceptionCase`'s generic `{"id": case_id, **properties}` view
with a generic `state` filter, so `state="BLOCKED"`, `family_ref`, `foundry_request_ref`
and `decision` are all readable with zero route changes.

### 9. `ExceptionCase.decision = "MODEL_DEFECT_FOUNDRY"` reuses an existing, previously-unpopulated property, and sets the vocabulary backlog story S8.3.1 will need

`ExceptionCase.decision` has been declared since §4.1.1 as a plain `T.STRING` with no
enum, and no story has ever written to it. Backlog story S8.3.1 (Exception Desk, not yet
built) will give a Migration Engineer the identical manual decision — its own literal
words, *"model defect (Foundry change request)"* — as one of four case-page choices; this
story's own machine-written value (`"MODEL_DEFECT_FOUNDRY"`) is chosen to match that
future vocabulary directly, so a later story reading this convention recognises it rather
than inventing a second, colliding one.

### 10. `MenderPass` gains one new strategy (`ROUTE_TO_FOUNDRY`) and two new results (`ROUTED_TO_FOUNDRY`, `ALREADY_IN_FOUNDRY`) — additive enum widening, no migration

Both are checked ahead of every other strategy: a routed pass is always `pass_number=1`
and the loop returns immediately, the identical shape S8.2.1's own `ESCALATE_IMMEDIATE`
already established for a decision made before any repair is attempted. `measure_ref`
stays absent on a `ROUTE_TO_FOUNDRY` pass by construction (decision 7) — the ontology
note states this explicitly rather than leaving it to be inferred.

## Consequences

- New `services/graph-svc/src/astra_graph/foundry_routing.py`: `ModelDefectEvidence`,
  `MODEL_DEFECT_CLASSES`, `detect_model_defect`, `route_to_foundry`, plus the private
  `_family_for_workbook`/`_detect_missing_dimension_member`/`_family_grain_fields`/
  `_detect_model_grain_mismatch` helpers.
- `mender.py`: `mend_exception` checks `detect_model_defect` before its own
  `_ESCALATE_WITHOUT_REPAIR` check and before the pass loop; a real model defect routes
  and returns immediately (`outcome: "routed_to_foundry"`), writing one
  `MenderPass(strategy="ROUTE_TO_FOUNDRY")`. Every other case (no family, or no
  confirmable evidence) is entirely unaffected — S8.2.1's own tests all pass unchanged.
- Ontology: `ExceptionCase` gains `family_ref`/`foundry_request_ref` (STRING, both
  additive); `MenderPass.strategy` gains `ROUTE_TO_FOUNDRY`; `MenderPass.result` gains
  `ROUTED_TO_FOUNDRY`/`ALREADY_IN_FOUNDRY`; `SCHEMA_VERSION` 32 -> 33; one new declared
  `SpecDeviation`. No migration file — purely additive properties/enum values, confirmed
  by `tools/migration_check.py` itself.
- No new route, no new role, no console change — see decision 8.
- Verified: 5 new pure unit tests (`_family_grain_fields`'s own comma-split parse,
  `ModelDefectEvidence`, `MODEL_DEFECT_CLASSES`); 8 new integration tests against real
  PostgreSQL + Apache AGE, including a real family walked all the way from `PROPOSED` to
  `PUBLISHED` (the identical real state-machine walk `test_integration_versioning.py`
  already proves) — a real KEY_MISSING case with an unmapped dimension routing for real
  (a real change request opened, the family really moved to `DRAFT`, the exception really
  `BLOCKED`, no `Measure` ever written); the identical case with the dimension really
  mapped falling back to S8.2.1's own escalation instead; a real AGGREGATION grain
  mismatch routing the same way; AGGREGATION within the family's own grain left entirely
  untouched; a second, independent model defect on a family already off `PUBLISHED`
  honestly joining what is already in progress rather than opening a second version,
  proven both when the Mender caused the first move and when a human Semantic Model
  Engineer did; a workbook with no family at all falling through cleanly; the full
  existing graph-svc suite green alongside them; `ruff`/`mypy` clean;
  `ontology_check.py --spec`/`--generated` and `migration_check.py` all pass.

## Alternatives considered

**Widen `classification.py`'s own §11.1 signals to carry model-side facts directly.**
Rejected — see decision 1. Classification (F8.1) is closed scope from a shipped, tested
story; the model-defect check is a genuinely separate, second-stage concern the Mender
(F8.2) runs, not a new §11.1 signal.

**Invent a real `MigrationUnit`/`ChangeRequest` node type now, rather than reusing
`ExceptionCase.state`/`model_lifecycle.request_new_version`.** Rejected — see decisions 5
and 6. Both real mechanisms already exist and already fit; inventing new ones would
duplicate real, working machinery and pre-empt whichever future story actually owns a
real MU registry (still nobody's, per every prior ADR that has found this same gap).

**Treat every KEY_MISSING (or every AGGREGATION) as Foundry-routable, matching §11.1's
own row-level fix-path wording literally.** Rejected — the AC's own two-clause wording
("with graph evidence of...", "with a grain mismatch...") is read as deliberately
narrower than the whole class; S8.2.1's own unconditional KEY_MISSING escalation already
covers the class as a whole (never repaired as a report), and this story only adds the
*confident, evidenced* subset that can additionally take the stronger, more useful
action (a real change request + BLOCKED) rather than only sitting escalated.

**Let a family-state race raise an uncaught exception from `route_to_foundry`.**
Rejected — see decision 5. Two Parity Engineers routing two different exceptions on the
same family at nearly the same time is a real, plausible concurrent scenario in a
multi-user system; converting the resulting `InvalidRequestError` into the identical
honest `ALREADY_IN_FOUNDRY` outcome the "already in progress" branch already gives is
more correct than a 500 over a lost race.

## Open questions for the product owner

- `_detect_model_grain_mismatch` compares a case's own grain against the *family's* own
  candidate grain, never a specific `ModelTable`'s own grain (no such property exists —
  confirmed, `ModelTable` carries `mode`/`schema`/`source_table_refs` only). Should a
  future story add a real, structured grain property to `ModelTable` itself once the
  Foundry's own build step needs one, and should this check then compare against that
  instead, or does the family-level candidate grain remain the right comparison since
  §11.1's own words say "at the model" (singular, matching the family) rather than "at
  one table"?
- `route_to_foundry` never inspects *which* `ModelTable`/relationship within the family
  is actually responsible for the missing dimension or the coarser grain — the change
  request it opens is a bare `DRAFT` copy for a Semantic Model Engineer to diagnose by
  hand. Should a future story attach the real evidence (the unmapped field name, the
  grain-only-on-case set) directly onto the new `SemanticModel`'s own `design_document`
  (e.g. as a pre-filled `open_questions` entry) so the engineer starts from the Mender's
  own real finding rather than re-deriving it from the `MenderPass`'s own evidence
  artefact?
