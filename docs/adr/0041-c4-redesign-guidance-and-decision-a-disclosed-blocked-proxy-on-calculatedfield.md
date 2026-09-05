# ADR 0041 — C4 redesign guidance and decision: a disclosed-BLOCKED proxy on CalculatedField

Status: accepted · 5 September 2026 · Story S5.4.1 (F5.4, opening it)

## Context

S5.4.1 opens F5.4, following F5.3's own close (S5.3.3, ADR 0040): *"As a migration engineer,
I want C4 constructs flagged with the closest Power BI approach and routed to a redesign
decision, so that no one wastes a proof cycle on something that has no equivalent. For each
C4 the Transpiler writes the reason, the Appendix B guidance, and an ASSISTED-mode redesign
suggestion (marked as such). The MU is BLOCKED until a Migration Engineer records the
redesign decision (implement as suggested / alternative / drop with report-owner agreement).
Decisions are visible to the report owner and referenced at G3."*

Research before any code was written settled the scoping question the AC's own "the MU is
BLOCKED" clause raises: **no Migration Unit exists anywhere in this codebase.** §4.1.1's own
node table declares no `MigrationUnit` row; §3.1 defines an MU as a control-plane concept
spanning several already-existing nodes (a workbook, its model family, its artefacts, its
parity verdicts, its gates), not itself a graph node, and no story before this one has ever
created a real MU record. §3.2's own 15-state machine names BLOCKED as entered for, among
other reasons, "an open Class 4 redesign decision" — exactly this story's own concern, with
nowhere real to record it. A second gap sits under "referenced at G3": `GateDecision` exists
generically (§4.1.1) but only G2 has ever been driven (S4.2.1); no story has ever written
`GateDecision(gate="G3")`, and building the real gate card is **S9.1.1/S9.1.2's own explicit
later scope** (F9.1, milestone I5 "Govern" — two increments after F5.4's own milestone I3
"Generate"). A third: `Role.CLIENT_REPORT_OWNER` has been declared since S1.1.1 but gated
nowhere until now. Given this, the scoping question that mattered was put to the product
owner directly: represent the decision as new properties directly on the real, existing
`CalculatedField` node, or stand up a new lightweight platform-table record for it? The
explicit choice was the former — attach to what already exists rather than half-build a
structure (a generic decision record, visible to a report owner "by construction") that
S8.3.1's own later Exception Desk scope already owns building for real.

## Decisions

### 1. Appendix B becomes data a second time, the identical move ADR 0035 already made

`redesign.APPENDIX_B_GUIDANCE` turns Appendix B.1's own literal target/notes cell for every
C4-producing rule into real data, keyed by `classify.py`'s own `rule_id` exactly as it emits
it today (`b1:no_ast`, `b1:unrecognised_construct`, `b1:table_calc_complex_unresolved`,
`b1:unrecognised_function`, `b1:regexp`, `b1:unmapped_family`, `b1:rawsql`, `b1:unknown`) —
the same "turn the spec's own table into data" move that made B.1's function-family table
`classify.py`'s own `_FAMILY_CLASS` in the first place. A rule_id classify.py can reach but
this table has no entry for is a real drift bug, not a silently tolerated gap: `c4_properties`
raises `RedesignDecisionError` rather than guessing, and `test_redesign.py` proves the two
modules agree by actually invoking `classify()` against a real AST for every entry, in both
directions (every reachable C4 rule_id has guidance; every guidance entry is reachable).

### 2. The redesign suggestion is `AgentMode.ASSISTED` — a second "name only" contract, not a model call

`build_redesign_suggestion` composes a real, deterministic, actionable next step from that
same guidance text — never a model call, so there is no inference boundary to police.
`AgentMode.ASSISTED` is not a green-field enum member invented for this story: `modeller.py`
already drives it for the grain-statement draft (S3.1.1/S4.1.2-era), a real, reproducible,
template-composed output on the identical footing. `ContractName.TRANSPILER_C4_REDESIGN` is
a name only — no `ContextContract` is registered for it in `CONTRACTS` — matching
`MODELLER_FAMILY`'s own precedent exactly, for the identical reason: no model gateway call,
no boundary to validate a fragment against.

### 3. No Migration Unit exists, so `CalculatedField.redesign_decision` (absent) is the disclosed proxy for BLOCKED

The product owner's own explicit choice (see Context): seven new properties land directly on
`CalculatedField` (`appendix_b_guidance`, `redesign_suggestion`,
`redesign_suggestion_provenance_ref`, `redesign_decision`, `redesign_decision_reason`,
`redesign_decision_by`, `redesign_decision_at`), present only when `class` is C4. A C4 field
with no `redesign_decision` yet is exactly what this platform would otherwise call BLOCKED,
told honestly rather than invented against a node type that has never existed here. This
mirrors the session's now-repeated "disclosed gap, not fabricated" discipline (ADR
0038/0039/0040's own model-gateway and calibration gaps) applied to a missing *node*, not a
missing *model call*.

### 4. Reclassification drops stale C4 properties wholesale; a recorded decision always survives

`upsert_nodes` replaces a node's whole property set (`writes.py`'s own documented contract),
so `reclassify_estate` must explicitly omit the seven C4 properties for a field no longer C4
— otherwise a stale guidance/suggestion/decision would quietly persist as clutter describing
a redesign that is no longer relevant. `c4_properties` always carries through any already-set
`redesign_decision`/`redesign_decision_reason`/`redesign_decision_by`/`redesign_decision_at`
from the field's own prior properties, so a Migration Engineer's real recorded work is never
silently lost to a routine re-classification pass.

### 5. Provenance is idempotent, not spammed, across repeated reclassification

`c4_properties` only writes a fresh `ProvenanceRecord` when the rule_id or the suggestion
text has actually changed since the last computation (checked against the field's existing
properties); otherwise it reuses the existing `redesign_suggestion_provenance_ref`. A
`reclassify_estate` run over an unchanged estate does not grow the provenance table without
bound.

### 6. "Report-owner agreement" for DROP is recorded in the same reason field, not a new co-sign workflow

Building a second-actor countersigning mechanism for DROP specifically would duplicate G2's
own dedicated countersigning design (S4.2.1's scope, `ApproveRequest.countersigned_by`/
`rationale`). Instead, `redesign_decision_reason` is where a DROP decision's report-owner
agreement is documented — enforced only as "every decision needs a real, non-empty reason"
(`validate_decision`), disclosed in both the property's own ontology docstring and the
validator's own error message.

### 7. Visibility: the migration engineer decides, any Artizent role or the report owner reads — no console screen, no real G3

`POST /v1/calculations/{calc_id}:redesign-decision` is gated to `MigrationEngineerDep` — the
first route to ever drive `Role.MIGRATION_ENGINEER`. `GET /v1/calculations:c4-redesigns` is
gated to `C4RedesignReaderDep` (any Artizent role, or `Role.CLIENT_REPORT_OWNER`
specifically) — the first route to ever drive that client role, deliberately narrower than
every client role (a different client persona, e.g. the licence admin, has no reason to see a
redesign decision). This satisfies "decisions are visible to the report owner" directly; it
does not build a `GateDecision`-shaped generic record (S8.3.1's own later Exception Desk
scope) or a real G3 gate card (S9.1.1/S9.1.2's own later scope, two increments out) — "and
referenced at G3" is left as exactly that future reference, not fabricated now.

## Consequences

- `ontology/nodes.py`: seven new `CalculatedField` properties (schema version 18, up from
  17) with a new `SpecDeviation` entry citing the AC and §4.1.1's own lack of both these
  properties and an MU node.
- New module `redesign.py`: `APPENDIX_B_GUIDANCE`, `C4_PROPERTIES`, `REDESIGN_DECISIONS`,
  `RedesignDecisionError`, `c4_properties`, `validate_decision`.
- `context/contract.py`: `ContractName.TRANSPILER_C4_REDESIGN` (name only, unregistered).
- `classify.py`: `ClassificationEngine` and `reclassify_estate` gain a `provenance_store`
  parameter; the per-field write loop drops stale C4 properties and adds real ones for a C4
  verdict via `redesign.c4_properties`.
- `api/deps.py`: `MigrationEngineerDep`, `C4RedesignReaderDep`.
- New route file `routes_redesign.py`: the decision POST and the report-owner-readable GET.
- `main.py`: `ClassificationEngine` construction gains `provenance_store`; the new router is
  wired in.
- Unit tests (`test_redesign.py`): the two-way drift guard between `classify.py` and
  `APPENDIX_B_GUIDANCE`, `validate_decision`'s acceptance/rejection cases, `c4_properties`'s
  idempotency and decision-preservation logic against an in-memory provenance store.
- Integration tests (`test_integration_classify.py`): a real C4 field gets real
  guidance/suggestion/provenance written and readable back; reclassification is idempotent
  (no duplicate provenance); a recorded decision survives reclassification; a field's C4
  properties clear when it is written as a non-C4 verdict; the two new HTTP routes are
  exercised for role-gating (migration engineer only for the write; Artizent-or-report-owner
  for the read, refused to another client role), validation (bad decision, empty reason,
  non-C4 field), and end-to-end record-then-read visibility.

## Alternatives considered

**A new lightweight platform-table record for the redesign decision (mirroring
`calibration_observation`'s own shape).** Rejected — the product owner's own explicit choice
(see Context) was to attach to the real, existing `CalculatedField` node instead. A separate
table would also duplicate the shape S8.3.1's own later Exception Desk scope already owns
building generically for every kind of decision, not just this one.

**Invent a real `MigrationUnit` node now to hold a genuine BLOCKED state.** Rejected — no
story in the backlog scopes building the MU as a real, spanning control-plane concept (§3.1's
own definition), and doing so here, as a side effect of one story's own narrower AC, would
pre-empt whichever later story is actually meant to build it for real, the same reasoning
this session has already applied to the Model Gateway (ADR 0038/0039) and calibration (ADR
0040).

**Build a real G3 gate card now, since the AC says "referenced at G3."** Rejected —
`GateDecision(gate="G3")` has never been written by any story; S9.1.1/S9.1.2 explicitly own
building it, two increments after this one's own milestone. This story satisfies "referenced
at G3" honestly by making the decision visible and readable now, without fabricating the gate
itself.

**Route DROP's "report-owner agreement" through a new second-actor countersign endpoint.**
Rejected — see decision 6. G2 (S4.2.1) already owns a real countersigning mechanism; building
a second one for this one decision type would duplicate it rather than reuse it.
