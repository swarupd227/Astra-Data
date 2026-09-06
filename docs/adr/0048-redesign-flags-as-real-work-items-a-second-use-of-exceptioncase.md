# ADR 0048 — Redesign flags as real work items: a second, disclosed use of ExceptionCase

Status: accepted · 6 September 2026 · Story S6.2.1 (opens F6.2)

## Context

S6.2.1 opens F6.2 — spec §11.1/§11.3, §3.2: *"As a migration engineer, I want every
redesign flag to be a work item with its evidence, so that flagged visuals get finished in
Desktop and nothing is forgotten."*

- Redesign flags create ExceptionCases of class VISUAL_REDESIGN routed to the Exception
  Desk with the source screenshot, the mapping reason and the placeholder location
- An MU with open redesign flags cannot enter PROVING for the affected sheets; other
  sheets proceed
- Closing the flag records the engineer, the Desktop commit hash and the date

Three previous ADRs (0045/0046/0047) each found the identical placeholder-visual/redesign-
flag mechanism (S6.1.1) and each deliberately stopped short of building what this story
now builds — ADR 0047 named it explicitly: *"minting VISUAL_REDESIGN now would reach into
F6.2/S6.2.1's own explicit, not-yet-built scope."* `ExceptionCase` was already declared
(§4.1.1) and already driven once, by `generation.py`'s own pre-proof `UNKNOWN`-class case
(S5.3.1) — this story is the second real use of that one mechanism, not new ground.

One correction to carry forward: earlier ADRs in this epic described this repository as
having no console at all. That is not accurate — `services/console-web` is a real, working,
tested React console with nine live surfaces (Estate Explorer, Lineage View, Parse Quality
Queue, Programme Board, Wave Board, Model Detail, Model Proposal, Admin, Pattern Library).
What *is* accurate, and confirmed again for this story specifically, is that none of those
nine surfaces is an Exception Desk, an MU page, or anything else this epic's own stories
have needed — the narrower, correct claim every E6 ADR should have made from the start.

## Decisions

### 1. `ExceptionCase.class = VISUAL_REDESIGN` is a second, disclosed use of one real mechanism — not a new node type

§11.1's own failure taxonomy (`FILTER_CONTEXT`, `NULL_HANDLING`, ...) is for a parity
verdict the Mender diagnoses *after* a real proof attempt; a redesign flag is a pre-proof,
structural fact about a mark type with no Power BI mapping at all — the identical "different
moment" gap `generation.py`'s own `UNKNOWN`-class case already discloses for its own
pre-proof generation failures. Adding `VISUAL_REDESIGN` to `_FAILURE_CLASSES` reuses the
one real work-item mechanism this platform has, rather than inventing a second node type
for "a thing a migration engineer needs to act on."

### 2. "Routed to the Exception Desk" means a real, queryable `ExceptionCase` — not a real queue

§11.3 itself: *"the Exception Desk, which is the Migration Engineer's work queue... there
is no separate defect tracker."* It is a console view over this exact node type (F8.3/
S8.3.1, milestone I4, unbuilt), not a component with its own contract. Writing a real
`ExceptionCase` with `state="OPEN"` is the whole of "routing" it — the identical "the
mechanism is real, the screen is later" posture every E6 story has already taken, now
applied to F6.2's own first story instead of invented ahead of it.

### 3. Evidence is a snapshot at open time, not a live read

`mapping_reason`/`placeholder_location` are copied from the `Visual` the moment its case
opens, not read live from it — S1.4.3's own "evidence copied onto a record is a snapshot,
and its field names say so" precedent (`grammar_issue.occurrences_when_raised`). A later
visual-mapping ruleset edit, or a recompose that changes nothing about this specific sheet,
must not quietly rewrite what an already-open work item says it is about.

### 4. No screenshot is ever automatically captured — `screenshot_ref` is honestly, usually absent

`ArtefactStore`/`ArtefactRecord` (S2.4.2) are real and work end to end for whatever bytes a
caller supplies, but nothing in this codebase's own local/demo environment — no live
Tableau connection exists here — ever calls `.store(kind="visual_capture", ...)` on its
own. `find_screenshot_ref` looks for an existing artefact matched by `case_id` naming the
source worksheet (the one real linking convention `ArtefactRecord` already supports without
inventing a new one) and returns `None`, honestly, when nothing has ever been uploaded —
the same "disclosed absent, not fabricated" posture every other real gap in this codebase
already takes, proven by both directions in the integration suite (absent by default;
found for real once a matching artefact is stored).

### 5. A recompose retires a dependent case, it does not close it

`compositor._retire_previous_report` already retires every previous `Visual` on a
recompose (S6.1.1); an `ExceptionCase` referencing one of them is retired alongside it —
`patterns.py`'s own "retiring the parent, react to the dependent" cascade (S5.5.x). This is
a retirement, never a close: closing implies an engineer's own recorded decision (decision
6 below), and a recompose is not one. If the fresh compose still flags the same sheet, a
fresh case opens against the fresh `Visual` — no work is silently lost, the same "an edit
is a new version" footing every other recompose-shaped write in this codebase already has.

### 6. Closing mirrors `redesign.py`'s own `*_by`/`*_at` shape, plus the one new fact the AC names

S5.4.1's own C4 calculated-field redesign closing (`redesign_decision_by`/
`redesign_decision_at`) is the closest real precedent for "recording who closed something
and when" — reused directly (`closed_by`, `closed_at`), plus `desktop_commit_hash`, the one
fact that story never needed. The hash is recorded as the engineer states it, never
verified against a real Power BI Desktop or Git history this platform cannot reach — the
same "a real fact, not a verified one" footing `ArtefactRecord.content_hash` already has
for a caller-supplied artefact.

### 7. "Cannot enter PROVING for the affected sheets" is a real, callable, currently-uncalled function — at a finer grain than §3.2's own table

No real Migration Unit node or state machine exists anywhere (`migration_units.py`'s own
registry has no state-transition method at all — confirmed a seventh time, after S5.4.1/
S5.5.1/S5.5.2/S5.5.3/S6.1.1/S6.1.2/S6.1.3), and E7's Arbiter — the only thing that would
ever call a proving gate — does not exist either. `can_enter_proving` is written as a real,
honest function over real, live data (`Visual.redesign_flag` via every open `VISUAL_REDESIGN`
case's own `visual_ref`) so a real answer is waiting the day an Arbiter exists, rather than
a gap to fill from scratch — the identical posture `report_deploy.py`'s own `deploy_state`
proxy and every other MU-shaped gap in this codebase already discloses. §3.2's own state
table expresses blocking only at whole-MU grain (`BLOCKED`); the AC's own "for the affected
sheets... other sheets proceed" is explicitly finer than that table — this function answers
at the sheet grain the AC actually asks for, a disclosed departure from the spec's own
table shape rather than a forced fit into a coarser state name.

## Consequences

- `ontology/nodes.py`: `_FAILURE_CLASSES` gains `VISUAL_REDESIGN`; `ExceptionCase` gains
  seven new, additive properties (`visual_ref`, `mapping_reason`, `placeholder_location`,
  `screenshot_ref`, `closed_by`, `closed_at`, `desktop_commit_hash`); schema version 25 (up
  from 24); two new `SpecDeviation` entries.
- New module `visual_redesign.py`: `open_redesign_exception`, `close_redesign_exception`,
  `can_enter_proving`/`ProvingReadiness`, `retire_exceptions_for_visuals`,
  `find_screenshot_ref`, `RedesignExceptionError`.
- `compositor.py`: `compose_report` gains an optional `artefact_store` parameter (backward
  compatible — every existing caller keeps working unchanged); a redesign-flagged
  placement now also opens a real `ExceptionCase` after the main write succeeds;
  `_retire_previous_report` now also retires dependent exceptions before retiring their
  own `Visual`s. `Compositor` gained the same optional `artefact_store` constructor
  parameter, wired from `main.py`'s own existing `app.state.artefact_store`.
- New routes: `GET /v1/exceptions` (filterable by `state`/`mu_ref`), `POST /v1/exceptions/
  {id}:close`, `GET /v1/workbooks/{id}:proving-readiness` — the first two reusing
  `ArtizentDep`/`MigrationEngineerDep`, no new role.
- No console screen: the Exception Desk is F8.3/S8.3.1's own unbuilt future surface, and
  (corrected from earlier E6 ADRs) `services/console-web`'s own nine real surfaces include
  no MU page, Compositor screen, or Exception Desk among them.
- Verified against real PostgreSQL + Apache AGE: composing a workbook with an unmapped
  sheet opens a real, snapshot-carrying `ExceptionCase`; a recompose retires the old case
  rather than orphaning it; the proving-readiness check correctly separates a workbook's
  own ready and blocked sheets; closing records the engineer/commit/date for real and
  refuses a blank hash, a double close, and an unknown case; a real screenshot upload is
  found by worksheet name and a missing one is honestly absent; every new route drives its
  own real role gate — 15 new integration tests, 9 new unit tests, full suite green (1,019
  unit, up from 1,010; 435 integration passed + 2 skipped, up from 420 + 2, in the same run
  as the one already-flagged, pre-existing, unrelated `test_integration_g2_reminders.py`
  flake).

## Alternatives considered

**A new `RedesignCase` node type, separate from `ExceptionCase`.** Rejected — see decision
1. `ExceptionCase` already exists for exactly "a work item a migration engineer must act
on, with evidence"; a parallel type would duplicate the one real mechanism this platform
has for that idea, for a taxonomy mismatch `generation.py` already disclosed as acceptable
for its own, different `UNKNOWN`-class case.

**Build a real Exception Desk console screen now.** Rejected. F8.3/S8.3.1 is explicitly
later, unbuilt scope (milestone I4) with its own fuller AC (queue columns, bulk assign,
train-sequence ordering, a `GateDecision`-shaped generic decision record) this story's own
AC does not ask for. The mechanism this story builds is exactly what that future screen
would read.

**Guess or synthesize a screenshot when none exists** (e.g., a generated placeholder
image). Rejected — see decision 4. Fabricating evidence for a work item whose whole point
is trustworthy evidence would be worse than honestly disclosing its absence.

**Close a dependent `ExceptionCase` automatically on recompose, rather than retiring it.**
Rejected — see decision 5. Closing implies a real engineer decision (decision 6) that a
recompose never makes; retiring accurately says "this specific case no longer applies to
anything live" without claiming a decision nobody made.

**Skip the proving-readiness check entirely since nothing calls it yet.** Rejected — see
decision 7. This codebase has repeatedly chosen to build the real, honest check even with
no live consumer (S5.3.3's own calibration report, S6.1.2's own `deploy_state`) rather than
leave a gap for whichever future story needs it to rediscover from scratch.

## Open questions for the product owner

- Should `can_enter_proving` gain a real caller once E7's Arbiter exists, or should this
  story's own function signature change at that point (e.g. to also consider parity-failure
  `ExceptionCase`s, not only `VISUAL_REDESIGN` ones)?
- Should a future story add an automated way to capture a Tableau screenshot (closing
  decision 4's own gap for real), or does this remain a manual `POST /v1/artefacts` upload
  indefinitely?
