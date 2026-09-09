# ADR 0064 — The Exception Desk: a real queue and four real G3 decisions

Status: accepted · 9 September 2026 · Story S8.3.1, opening F8.3, continuing E8

## Context

S8.3.1 opens F8.3 (Exception Desk), continuing E8 — the backlog's own AC: *"As a
migration engineer, I want a queue of ExceptionCases ordered by train sequence, with the
evidence bundle in the case, so that I never open Tableau to work out what an exception
is."*

- Queue columns: MU, failure class, passes consumed, train, age, assignee; filters by
  train, class, site, assignee; bulk assign
- Case page: evidence (failing cells, key diffs, filter context, parameter values),
  artefact (current DAX / M with source calc alongside, Mender pass history with
  diffs), decision
- Decisions: patch (edit in place, validate, re-prove), redesign (route to Foundry or
  open in Desktop with the MU link), model defect (Foundry change request), source
  defect (record, notify owner, choose reproduce or fix with owner sign-off — G3 matter)
- Every decision is a GateDecision-class record with rationale of at least one sentence
  and is visible to the report owner

§11.3 itself, verbatim: *"Escalated cases become ExceptionCases and appear in the
Exception Desk, which is the Migration Engineer's work queue. There is no separate
defect tracker. Each ExceptionCase carries the full evidence bundle, the Mender's pass
history, the current artefact and its source calc, and the model's diagnosis where one
was made. The engineer's decision is one of: patch (edit the artefact; re-prove),
redesign (Class 4; agree with the report owner; finish in Desktop; re-prove the rest of
the report; waiver the redesigned visual's case with justification), model defect
(route to the Foundry as a change to the family; the MU returns to BLOCKED), or source
defect (the Tableau report was wrong; record, inform the owner, and either reproduce the
defect faithfully or fix it with the owner's written agreement — the choice is a G3
matter). Every decision is a record and every patch is a Pattern candidate."*

## Decisions

### 1. "The queue" is a real, enriching read over `ExceptionCase` — no new store, matching §11.3's own "there is no separate defect tracker"

Every OPEN/BLOCKED `ExceptionCase` already exists (S8.1.1 opens them; S8.2.1/S8.2.2
escalate or route some); `exception_desk.queue` reads and enriches them with real train
position, site and age — it writes nothing new to represent "what is in the queue."

### 2. "Train sequence" resolves through the real `IN_TRAIN` edge, reversed — the first such reverse lookup this codebase has needed

`ExceptionCase.mu_ref` names a workbook, not a real Migration Unit (ADR 0060's own
finding); every existing train read (`trains._train_members`) goes train -> members,
never workbook -> its own train. `_train_position` walks `workbook --IN_TRAIN--> train`
backwards, reading the edge's own real `sequence` property the identical way
`trains._train_members` already does — the same reverse-lookup shape
`foundry_routing._family_for_workbook` (S8.2.2) already set for `IN_FAMILY`. `None`,
honestly, for a workbook the Train Planner has never sequenced; such a case sorts last.

### 3. Queue order is train sequence, then age descending (oldest first) — a real, disclosed choice §11.3 does not itself state

"Age" is real wall-clock time since the already-present `ExceptionCase.created_at`; no
new property was needed. Oldest-first within a train-sequence bucket is the natural
"handle what has waited longest" reading of a work queue, stated here rather than left
an implementation accident.

### 4. Evidence assembly reads the real §10.3 bundle a second, wider way than `mender._gather_parity_evidence` already does

That function narrows to what a repair request needs; the case page additionally needs
the real key-set diff (`missing_keys`/`extra_keys`) and each case's own real
`ParityCase.param_values` — assembled directly in `exception_desk._gather_case_evidence`
rather than widening `_gather_parity_evidence` for a caller (the Mender's own repair
request) that has no use for either. "The artefact... current DAX/M with source calc
alongside" and "Mender pass history with diffs" reuse the Mender's own resolution
helpers and read the already-declared `MenderPass` nodes directly — S8.2.1's own
docstring already named this exact future reader.

### 5. Every decision writes a real `GateDecision(gate="G3")` — the first real G3 write this codebase has ever made, deliberately, not by accident

No real G3 *gate workflow* exists anywhere (confirmed independently by `redesign.py`,
`nodes.py`'s own prior `SpecDeviation`s, `diff.py` and `patterns.py`, all naming
S9.1.1/S9.1.2 as the story that eventually builds it) — but `GateDecision.gate` already
legally allows `"G3"`, and a real, evidenced *record* of a Desk decision is not the same
claim as "a G3 gate now exists to approve or reject against." §11.3's own "the choice is
a G3 matter" (for source defect specifically) is read as "this is what a future G3 gate
will read," not "this story must build G3." `nodes.py`'s own prior `SpecDeviation` for
`ExceptionCase.artefact_ref`/`case_refs` (S8.1.1) already named S8.3.1 as the story that
builds "a `GateDecision`-shaped record, visible to the report owner by construction" —
this module is that promise kept, literally.

### 6. `GateDecision.decision` gains four new, additive values rather than reusing the existing G1/G2 vocabulary

`APPROVED`/`REJECTED`/`CHANGES_REQUESTED`/`WAIVED` were built for the G1/G2 model-design
approval workflow (S4.2.1/S4.2.2); forcing "patch"/"redesign"/"model defect"/"source
defect" into that set would have needed a dishonest semantic stretch (which of those
four does "patch" even mean?). `PATCHED`, `REDESIGN`, `MODEL_DEFECT`, `SOURCE_DEFECT` are
added instead — additive, no migration, each honestly named for what it is.

### 7. "Patch (edit in place)" still never mutates a Measure in place — the AC's own literal wording loses to this codebase's own far stronger, universal convention

Every existing `Measure` writer (`generation.py`, `mender.py`, `patterns.py`, `rules.py`)
writes a brand-new node; none has ever edited `Measure.dax` on an existing one. A
literal in-place edit would be this codebase's first ever and would discard the "an edit
is a new version, the old row is never touched" discipline `Pattern.version`/
`SemanticModel`'s own per-version lifecycle already established. `decide_patch` writes a
new `Measure` the identical way a Mender model repair already does
(`mender._write_repaired_measure`, reused verbatim, widened with one new optional
`retire_reason` keyword so the retired `MAPS_TO` edge's own audit trail correctly says a
human patched it, not "superseded by a Mender repair"). Attributed `AgentMode.HUMAN` —
confirmed, by direct grep, the first real write this declared-since-§8.1 mode has ever
had.

### 8. A patch closes the case only when every one of its own cases re-proves PASS; a still-failing patch still records its own real decision

`decide_patch` reuses `mender.reprove_cases` verbatim. When re-proof does not fully
clear, the case stays OPEN (a real, honest "not fixed yet," never a false close) but the
`GateDecision` is still written — the act of deciding to patch and the mechanical
outcome of whether it worked are two different real facts, and only the first is what
"every decision is a record" actually requires.

### 9. Redesign implements both of the backlog's own named alternatives — Desktop and Foundry — as two real, distinct functions sharing one decision label

"Open in Desktop with the MU link" cannot carry a real MU *link* — the same "no MU page
exists" gap ADR 0048 already found for the identical words in S6.2.1's own AC — so it is
made real the only way this codebase already has: a real Desktop commit hash, recorded
via the identical `closed_by`/`closed_at`/`desktop_commit_hash` shape
`visual_redesign.close_redesign_exception` established, generalised here to any class
(that function itself is untouched, still class-gated for its own S6.2.1 caller).
"Route to Foundry" reuses `foundry_routing.route_to_foundry` directly — the identical
mechanism S8.2.2 already built — called with a human-asserted `ModelDefectEvidence`
rather than a re-run of the automated detection: a human's own judgement that a fix
belongs in the model is itself real evidence, not something this module second-guesses.

### 10. "Model defect" is the identical Foundry call redesign's own foundry sub-path uses — the difference is only *why*, recorded on the `GateDecision`

`decide_model_defect` and `decide_redesign_foundry` are two thin, near-identical
wrappers around `route_to_foundry`; the only real difference between them is the
`GateDecision.decision` value each writes (`MODEL_DEFECT` vs `REDESIGN`), naming the
engineer's own stated reason for routing there — not a second mechanism.

### 11. "Notify owner" reuses the identical `NotificationChannel` shape `regression.py` already established, applied to a source-defect decision instead

A real, honest local log — no outward notification channel (email, chat) is configured
anywhere this platform has ever been deployed, the identical disclosed-absent posture
`g2_reminders.LocalNotificationChannel`/`regression.LocalNotificationChannel` already
carry. "Choose reproduce or fix with owner sign-off" is a real, required choice; the fix
path additionally requires a real, non-blank sign-off text — "the owner's written
agreement" taken literally, refused without one rather than accepted as a checkbox
nobody actually read.

### 12. "Rationale of at least one sentence" is a character-length proxy, set higher than any existing precedent and disclosed as such

`g2.MIN_RATIONALE_LENGTH = 8`, `model_lifecycle.MIN_CHANGE_REQUEST_REASON = 10`,
`writes.MIN_RETIREMENT_REASON_LENGTH = 8` are each real, defensible, disclosed numbers
built for a bare "reason." `exception_desk.MIN_RATIONALE_LENGTH = 20` is deliberately
higher, since "a sentence" is a qualitatively fuller bar than "a reason" alone — a real,
disclosed choice, not a reused smaller number that happened to be lying around.

### 13. Visibility "to the report owner" reuses the established Artizent-plus-report-owner shape, under its own name

`require_exception_desk_reader`/`ExceptionDeskReaderDep` is the identical condition
`require_c4_redesign_reader`/`require_parity_dashboard_reader` already enforce (`is_
artizent() or CLIENT_REPORT_OWNER`), given its own name and error message rather than
reusing `ParityDashboardReaderDep` directly — the same established convention (two
prior dependencies already duplicate this exact condition) so a refused request names
the real screen, not a different one that happens to share its logic.

## Consequences

- New `services/graph-svc/src/astra_graph/exception_desk.py`: `queue`, `case_detail`,
  `bulk_assign`, `decide_patch`, `decide_redesign_desktop`, `decide_redesign_foundry`,
  `decide_model_defect`, `decide_source_defect`, `NotificationChannel`/
  `LocalNotificationChannel`, `ExceptionDeskService`.
- `mender._write_repaired_measure` gains one new, optional `retire_reason` keyword
  (default unchanged) — its only other change this story makes.
- New routes in `services/graph-svc/src/astra_graph/api/routes_exceptions.py`: `GET
  /v1/exception-desk` (queue), `GET /v1/exceptions/{case_id}` (case page), `POST
  /v1/exceptions:bulk-assign`, `POST /v1/exceptions/{case_id}:patch`, `POST
  /v1/exceptions/{case_id}:redesign`, `POST /v1/exceptions/{case_id}:decide-model-
  defect`, `POST /v1/exceptions/{case_id}:decide-source-defect` — reading gated on the
  new `ExceptionDeskReaderDep`, deciding on the existing `MigrationEngineerDep`.
  `list_exceptions`/`close_exception`/`get_proving_readiness` (S6.2.1) are untouched.
- `api/deps.py`: `require_exception_desk_reader`/`ExceptionDeskReaderDep`.
- Ontology: `GateDecision.decision` gains `PATCHED`/`REDESIGN`/`MODEL_DEFECT`/
  `SOURCE_DEFECT`; `SCHEMA_VERSION` 34 -> 35, one new declared `SpecDeviation`. No
  migration file — additive enum values only, confirmed by `tools/migration_check.py`.
- `main.py`: `app.state.exception_desk = ExceptionDeskService(...)`, wired alongside
  the existing `MenderService`.
- Verified: 11 new pure unit tests (`_age_seconds`, `_clean_rationale`,
  `PatchResult`/`RedesignResult` round-trips, `LocalNotificationChannel`'s own real
  log); 24 new integration tests against real PostgreSQL + Apache AGE (the queue really
  enriching and filtering and ordering real cases by real train/site/age; the case page
  really assembling real evidence, artefact and pass history; bulk assign really
  driving `assignee` for the first time; a real patch closing on a real re-proved PASS
  and staying open on a real re-proved FAIL, both recording a real decision; redesign
  really closing with a real commit hash and really routing to the Foundry; model
  defect routing the same way; source defect really notifying and really requiring a
  real sign-off for the fix path; every decision's own real `GateDecision(gate="G3")`;
  the new routes' own real role gates, including the report owner's own real read
  access and a clean 400 for an unknown case); the full existing graph-svc suite green
  alongside them; `ruff`/`mypy` clean; `ontology_check.py --spec`/`--generated` and
  `migration_check.py` all pass.

## Alternatives considered

**Widen `mender._gather_parity_evidence` to also carry `missing_keys`/`extra_keys`
rather than a second read.** Rejected — see decision 4. That function's own caller (a
Mender repair request) has no use for the key-set diff; widening it for a reader that
does not need it would have coupled two genuinely different consumers of the same real
bundle for no benefit.

**Reuse `ParityDashboardReaderDep` directly for the Exception Desk's own reader
routes.** Rejected — see decision 13. Identical condition, but its own embedded error
message ("the Parity Dashboard is open to...") would mislead a refused Exception Desk
request; this codebase's own established convention is already one dependency per
screen even where the logic is identical.

**Literally mutate `Measure.dax` in place for "patch," matching the AC's own words
exactly.** Rejected — see decision 7. Every existing `Measure` writer in this codebase
creates a new node; a literal first-ever in-place edit would discard a universal,
repeatedly-reinforced convention for one story's own narrower wording.

**Force the four Exception Desk decisions into `GateDecision`'s existing
`APPROVED`/`REJECTED`/`CHANGES_REQUESTED`/`WAIVED` vocabulary rather than adding new
values.** Rejected — see decision 6. That vocabulary was built for a different
workflow; reusing it here would answer a real, later query ("was this patched or
redesigned?") with an enum value that does not actually say so.

## Open questions for the product owner

- `decide_redesign_desktop`/`decide_redesign_foundry` are two separate functions/routes
  rather than one `route` parameter deciding between them at the API layer (which is
  how the HTTP route itself is shaped, for a simpler client contract). Should a future
  story merge the console's own "Redesign" action into a single guided flow that picks
  the right sub-path automatically from the case's own class (`VISUAL_REDESIGN` ->
  Desktop by default; anything else -> Foundry by default), or should the engineer
  always choose explicitly, the way this story built it?
- `decide_model_defect`/`decide_redesign_foundry`'s own human-asserted
  `ModelDefectEvidence` is never re-verified against `foundry_routing.detect_model_
  defect`'s own automated checks (an engineer's own judgement is trusted directly).
  Should a future story surface the automated check's own result alongside the manual
  decision (e.g. "the automated check also agrees" / "the automated check found no
  evidence — proceeding on your own judgement alone"), so an engineer routing to the
  Foundry manually can see whether the evidence would have triggered S8.2.2's own
  automatic routing too?
