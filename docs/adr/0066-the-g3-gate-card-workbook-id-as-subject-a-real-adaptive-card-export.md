# ADR 0066 — The G3 gate card: workbook id as subject, a real Adaptive Card export

Status: accepted · 9 September 2026 · Story S9.1.1, opening F9.1, opening E9

## Context

S9.1.1 opens F9.1 and E9 (Release and Decommission) — the backlog's own AC: *"As a
report owner, I want a gate card that tells me in 30 seconds what I am approving, so
that acceptance is informed and quick."*

- Card anatomy per §15.5: what (report, pages, visuals), proof (cases, charter version,
  sampled flag, waivers), visual (structural score, human review status), changes (C4
  decisions, redesigns), next (promotion, parallel window), buttons Approve / Request
  changes / Ask a question / Open report
- Approve requires the report owner role for that report; countersigned by the
  Migration Engineer; both recorded
- A PASSED (waiver) MU shows the waivers and their justification on the card; approving
  records that the owner saw them
- Card renders identically on desktop, mobile and as a Teams adaptive card

§13.1's own G3 row, verbatim: *"Migration Unit | Client report owner; countersigned by
Migration Engineer | Passing ParityRun (or waived cases with justification); visual
review record | Invoice trigger; release permitted."* §15.5's own worked example (a
literal ASCII card mock-up) closes: *"Identical anatomy on desktop and mobile, and
mirrored into Teams as an adaptive card. Approve and Request Changes require a reason of
at least one sentence."*

Every prior story since S6.2.1 has explicitly disclosed that no real G3 gate *workflow*
exists yet, naming S9.1.1/S9.1.2 as the eventual builder — this is that story, for the
card half of it.

## Decisions

### 1. The workbook id is the real G3 subject — no real Migration Unit exists to be one

Confirmed, again, directly against `migration_units.py`'s own docstring: *"this is a
port, not an implementation"* — the whole §3.2 state machine belongs to the control
plane, never written as a real graph node. Every G3-adjacent story since S8.1.1 has used
the workbook id as the real MU proxy (`ExceptionCase.mu_ref`, ADR 0060's own finding);
this module writes `GateDecision(gate="G3", subject_ref=<workbook_id>)`, the identical
subject a live `ExceptionCase` already names.

### 2. Approve/Request changes reuse `g2.approve`/`.request_changes`'s own proven shape verbatim

Approver + countersigner, both recorded on one `GateDecision`, no new enum values
needed — `GateDecision.decision` already legally allows `APPROVED`/`CHANGES_REQUESTED`
(declared since S4.2.1); this story writes them for a G3 subject for the first time.
Countersigner is a plain, unverified name string, the identical "the approver types who
countersigned, not a second authenticated action" convention `g2.approve`'s own
`countersigned_by` parameter already established — confirmed by direct read, not
invented for this story.

### 3. "Approving records that the owner saw them" is a real, frozen JSON snapshot of the card, stored as an artefact

The card's own rendered anatomy at approval time — including the waivers (or their
honest absence) the owner saw — is serialised and stored via `ArtefactStore`, named by
the `GateDecision`'s own `evidence_ref`. A real fact about what was shown, not a
separate boolean that could drift from what the card actually rendered — the identical
"`evidence_ref` points at a real stored JSON artefact" shape `Verdict.evidence_ref`
already has (S7.4.1).

### 4. "Waivers and their justification" reads the real, live `GateDecision(decision="WAIVED")` query — honestly empty today

Confirmed, again, directly: no story has ever written one — `parity_dashboard.py`'s own
identical finding (S7.4.2), still true after S8.3.1 (which writes
`PATCHED`/`REDESIGN`/`MODEL_DEFECT`/`SOURCE_DEFECT`, never `WAIVED`). This module reads
the same real, disclosed-absent-until-driven fact rather than fabricating one.

### 5. "Human review status" is a new, disclosed-absent property, `Visual.reviewed_by`/`.reviewed_at`

No action anywhere in this codebase writes it yet — the identical "real, queryable,
honestly empty" posture decision 4's own waiver query has. `structural_score`/
`image_score` (S7.6.1) remain the only automated facts `Visual` carries.

### 6. "Changes (C4 decisions, redesigns)" reads two real, distinct facts, both scoped to the workbook's own live ExceptionCases

"Redesigns" is `GateDecision(gate="G3", decision="REDESIGN")` rows whose `subject_ref`
names one of the workbook's own cases — S8.3.1's own Exception Desk decision. "C4
decisions" is the real `CalculatedField.redesign_decision` (+ reason/by/at) flag on
whichever calculated field each such case's own `artefact_ref` resolves to
(`mender._resolve_calculated_field`, reused verbatim) — S5.4.1's own disclosed
MU-BLOCKED proxy. Scoped to calc fields with a live case pointing at them, not every
calculated field the workbook has ever had — a real, disclosed narrowing.

### 7. "Next (promotion, parallel window)" is informational text, never an executed pipeline

E9's own goal names it plainly: *"accepted reports are promoted through the **client's**
pipeline."* Promotion is the client's own deployment mechanism, external to this
codebase, the same way `migration_units.py` already disclosed the whole §3.2 state
machine belongs to the control plane. Approving this card never triggers a real
promotion; it records a real `GateDecision` and states, as prose, §14.4's own literal
default (`DEFAULT_PARALLEL_WINDOW_WEEKS = 4`).

### 8. "Ask a question" is a new, minimal platform table, deliberately without a thread/answer mechanism

`public.g3_question` (`v0032_g3_questions.py`) is the identical "not an estate-graph
node" footing `g2_question` (S4.2.1) already has, but without `g2_question`'s own
thread/answer columns — this story's own AC names one button, asking, not a resolution
workflow. A future story can widen it the identical additive way nothing has ever needed
to widen `g2_question` since it was built.

### 9. "Renders identically on desktop, mobile and as a Teams adaptive card" is one component plus a real Adaptive Card 1.5 JSON export

One React component, one single-column CSS layout at every width
(`.g3-card-workspace`), guarantees identical anatomy structurally rather than by
maintaining two renderings in sync. `to_adaptive_card` is a real, schema-correct
Adaptive Card 1.5 document carrying the identical five sections — this platform has no
live Teams bot/webhook (confirmed, no such integration exists anywhere), so this is a
real, disclosed output a future integration can post as-is, the same "build the real
check even with nothing to call it yet" posture S5.3.3's own calibration report already
took.

### 10. Reading the card is broader than deciding it

`G3CardReaderDep` (Artizent or the report owner) mirrors `require_exception_desk_reader`/
`require_c4_redesign_reader` exactly — an Artizent role preparing the card needs to see
it too. `G3ApproverDep` (the report owner alone) gates Approve/Request changes/Ask a
question. "The report owner role *for that report*" still checks the bare role, not a
per-report binding — confirmed, still true, no property anywhere links a
`client_report_owner` principal to a specific workbook (ADR 0059's own finding,
unchanged).

## Consequences

- New `services/graph-svc/src/astra_graph/g3_card.py`: `g3_card`, `approve`,
  `request_changes`, `ask_question`, `list_questions`, `to_adaptive_card`,
  `G3CardService`.
- New `services/graph-svc/src/astra_graph/api/routes_g3.py`: `GET
  /v1/workbooks/{id}:g3-card` (`?format=adaptive_card`), `POST
  /v1/workbooks/{id}:approve-g3`, `POST /v1/workbooks/{id}:request-changes-g3`, `POST
  /v1/workbooks/{id}:ask-g3-question`, `GET /v1/workbooks/{id}/g3-questions`.
- `api/deps.py`: `G3CardReaderDep`, `G3ApproverDep`.
- Ontology: `Visual.reviewed_by`/`.reviewed_at` (additive, one new declared
  `SpecDeviation`); schema version 35 -> 36. New migration `v0032_g3_questions.py`
  (`public.g3_question`, additive Postgres table, not an ontology change).
- `main.py`: `app.state.g3_card = G3CardService(...)`.
- New console-web top-level surface, `G3Card.tsx` — a single-workbook search (the
  identical shape `ParityDashboard.tsx` already set), one stacked-section layout, an
  Approve/Request-changes/Ask-a-question form gated to the report owner, and a
  "Preview as Teams adaptive card" action showing the real exported JSON.
- **A real, pre-existing CSS bug found and flagged, not fixed here**: every multi-pane
  screen since S3.1.3 (Programme Board, Parity Dashboard, Regression Monitor, Exception
  Desk) inherits `.workspace`'s three-column grid (built for the Estate Explorer) with
  no override of its own, so their panes render side-by-side instead of stacked —
  confirmed with a real screenshot, not just text extraction. Out of this story's own
  scope; flagged as a separate follow-up task rather than folded in here.
- Verified: 16 new pure unit tests (`_clean_rationale`, `_visual_summary`'s own
  averaging/honest-absence/real-review-listing, `to_adaptive_card`'s own real Adaptive
  Card shape and fact set, both result dataclasses' round-trips); 22 new integration
  tests against real PostgreSQL + Apache AGE (real pages/visuals counted from a real
  `ReportDefinition`/`Visual`s, honestly zero before composition; a real `ParityRun`'s
  own cases/charter/sampled flag; a real waiver and its justification; a real averaged
  visual score and a real human review; a real C4 decision and a real redesign, both
  correctly scoped; a real approved `ModelFamily`; Approve really writing a real
  countersigned `GateDecision` with a real snapshot artefact recording the waivers the
  owner saw; refusing a blank countersigner and a short rationale; Request changes
  writing its own real record; Ask a question writing and listing a real question; the
  new routes' own real role gates, the report owner deciding, an Artizent role only
  reading, an unrelated client role refused); 13 new console tests (all five sections,
  a real waiver, the honest "No waivers" state, a read failure, decide controls hidden
  for anyone but the report owner, the rationale-and-countersigner gate on Approve, all
  three decisions, a decision refusal, the adaptive card export, the new surface); the
  full existing graph-svc suite and console-web suite both green alongside them;
  `ruff`/`mypy` clean; `ontology_check.py --spec`/`--generated` and `migration_check.py`
  all pass.

## Alternatives considered

**Invent a real Migration Unit node for this story alone, so the card has a "proper"
subject.** Rejected — see decision 1. E3/F3.2 (Migration Unit derivation) remains
entirely unbuilt; a G3-only MU node would be a fabricated state machine this story has
no business inventing, and every sibling story already uses the workbook id.

**Skip the Adaptive Card export until a real Teams integration exists.** Rejected — see
decision 9. The identical "build the real, disclosed output even with nothing to
consume it yet" posture this codebase has taken repeatedly (S5.3.3's calibration
report, S8.2.1's `MENDER_REPAIR` task class); a schema-correct export is real and
testable today, and the AC's own words ("mirrored into Teams") name it directly.

**Build a full `g2_question`-style thread (reply/answer) for G3 questions.** Rejected —
see decision 8. This story's own AC names one button; a reply/answer workflow is real
scope creep beyond what "Ask a question" asks for.

## Open question for the product owner

- "A PASSED (waiver) MU" implies a real waiver-recording mechanism exists somewhere in
  the pipeline before a report reaches G3 — this story reads real waivers honestly, but
  nothing in this codebase writes one yet (§7.1.1's own `WaiverRule` policy config has
  sat entirely unused since it was declared). Should a near-future story build the real
  "waive a case" action the Exception Desk or the Parity Dashboard would naturally host,
  so this card's own waiver section can show something other than an honestly empty list
  in a real deployment?
