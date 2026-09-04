# ADR 0029 — The state machine is declared whole, driven in part

Status: accepted · 4 September 2026 · Story S4.1.2 (E4 / F4.1)

## Context

S4.1.2 asks for editing and submission: *"Tabs: Design (tables, relationships diagram,
grain, conformed dims), Measures (source calc → candidate measure with class and pattern),
RLS, Open Questions, Build. State machine PROPOSED → DRAFT → IN_REVIEW → APPROVED → BUILT →
PUBLISHED enforced; transitions and their actors recorded. Submitting to IN_REVIEW freezes
a version hash; the G2 request references that hash."*

Four questions decided the shape of the work: how much of a six-state machine one story
should actually *drive* versus merely *declare*; what "transitions and their actors
recorded" requires given this codebase already has a general-purpose event log; what a
"version hash" is a hash *of*, and how it stays stable under an irrelevant re-read; and how
far "edit the proposal" reaches before it becomes a second, competing proposal-generation
engine sitting next to S4.1.1's.

## Decisions

### 1. `FAMILY_TRANSITIONS` declares all six states; this story drives exactly two edges

§12.2's table names six transitions. Only two have an owning action in this story's own
acceptance criteria — "engineer accepts" (PROPOSED/SINGLETON → DRAFT) and "engineer
submits" (DRAFT → IN_REVIEW). Approve/request-changes is the data owner's G2 action
(backlog S4.2.1, not built); deploy is the Steward's (S4.3.1, not built); promote has no
story yet. `model_lifecycle.FAMILY_TRANSITIONS` declares the whole legal graph anyway — the
same "declare the shape now, whichever story needs it drives it" precedent this codebase
has followed since `ReleaseTrain`/`Wave` (S1.1.1) and, one epic later, `ModelTable`/
`SemanticModel` themselves (unused until S4.1.1). A future story's `approve`/`deploy`/
`promote` action calls `require_transition` against the same table and gets the same
enforcement — never a second, drifting copy of §12.2's rules. `PUBLISHED` and `DEPRECATED`
are declared as terminal (an empty legal-next-state set) rather than omitted: every value
`ModelFamily.state`'s own closed enum can hold has an entry here, so a family can never
reach a state this module has nothing to say about — checked directly by a test comparing
the transition table's keys against the ontology's own declared enum.

### 2. Transition history reuses the event log; no new audit table

Every `ModelFamily` upsert already carries `updated_by`/`updated_at` and is a real
CloudEvent (S1.1.3) — a bespoke "who changed this and when" table would duplicate a fact
already recorded. `family_transition_history` finds genuine `state` changes with the
identical `LAG() OVER (ORDER BY seq)` technique `train_projection.py` (S3.2.3) uses to find
genuine `IN_TRAIN.state` transitions in the same event stream: a write that carries the
same state forward (some other property changing) is correctly excluded, because `LAG`
compares against the *previous row for this subject*, not a fixed baseline. The very first
write — a family's creation into `PROPOSED` — is itself reported as a transition, `NULL ->
PROPOSED`, deliberately: `NULL IS DISTINCT FROM 'PROPOSED'` is true, and a family's own
creation (who, when) is exactly the kind of fact "transitions and their actors recorded"
asks to keep, not a special case to filter out.

**No `GateDecision` record is written.** §13.3 gives that shape — approver, countersign,
evidence, rationale — to the G2/G3/G4 workflow itself (E11/E13, not built). This module
answers "who moved this family through the design lifecycle and when," which is a fact
about the `ModelFamily` node; a `GateDecision` is a fact about a *gate*, and inventing one
now would be guessing at a shape a real future epic already owns.

### 3. The version hash is `modeller.read_design_document`'s own canonical read, minus timestamps, hashed with the same utility every other content hash in this codebase uses

"Submitting... freezes a version hash" needs something to hash. Rather than a second,
purpose-built serialisation, `submit_for_review` hashes the exact document
`GET /v1/families/{id}/design` already returns — `read_design_document` re-reads the live
`SemanticModel`/`ModelTable` nodes, so verifying "is this still what was frozen" is
re-running the same read and re-hashing it, the identical reproducibility discipline S1.3.2
built for provenance context hashes. `context.canonical.canonical_json`/`context_hash` is
reused verbatim — sorted keys, no insignificant whitespace, `sha256:` prefix — rather than
a new hashing convention existing only here.

**`design_generated_at` and any prior `version` are excluded before hashing**
(`hashable_document`). Re-reading the same design twice must hash the same regardless of
when either read happened, or the very act of reading would look like a change — the same
reasoning S1.3.1's own context hash excludes `created_at`/`updated_at` for. Without this
exclusion, a caller comparing "is this the version I approved" against a fresh read would
see a false mismatch on every call.

### 4. Editing is three targeted actions, not a general PATCH

DRAFT's own meaning (§12.2) is "engineer editing tables, keys, grain, measures, RLS" — a
wide brief, and building a general-purpose editor for every field of `design_document`
would mean re-deriving CRUD semantics for a JSON blob that mostly isn't first-class graph
data yet (ADR 0028). This story wires the three fields a Semantic Model Engineer most
plausibly needs to correct before a client sees the proposal: the grain statement (the
drafted prose, freely rewritable), a table's storage mode (one of §12.2's three modes,
correcting ADR 0028's own disclosed heuristic), and one relationship's cardinality
(likewise a disclosed heuristic, not a verified fact). Each is a single `POST
.../:verb`-suffixed route, matching every other write in this API (`:move-member`,
`:set-wip-limits`) rather than introducing PATCH for the first time. Renaming a candidate
measure, adding or removing a table, and RLS role edits are not built — real, disclosed
future scope; `design_document`'s shape already has room for all of it whenever a story
asks for it, the same way this story's own three edits landed in it without an ontology
change.

Every edit — and both driven transitions — requires the caller to be a Semantic Model
Engineer and the family to be `DRAFT`; §12.2 gives editing to DRAFT alone.

### 5. `Modeller.run` now refuses once a family has been accepted

A latent risk `modeller.py`'s own S4.1.1 docstring flagged and deferred: nothing stopped
`propose-design` from silently retiring and replacing a Semantic Model Engineer's edits the
moment this story made those edits possible. `Modeller.run` now refuses outright — never a
warning, never overridable — once a family's state has left `{PROPOSED, SINGLETON}`
(`PRE_ACCEPT_STATES`), the same "never a warning" posture S3.2.2 takes for a family-
splitting move. A genuine "regenerate and discard my edits" action is real future scope
this story does not claim; the safe default is refusal, not a silent merge this module has
no way to get right.

## Consequences

- A genuine, pre-existing bug in S4.1.1's own write path was found while wiring this
  story's table/relationship edits: `Modeller._write` generated a *second*, independent
  `new_ulid()` for each `ModelTable` node at write time, different from the id
  `_table_candidates` had already put on the `TableCandidate` it returned — so
  `POST :propose-design`'s immediate response reported ids that did not match what
  `GET /design` (a fresh read of the same nodes) reported afterward, and `Relationship
  Candidate.from_table`/`.to_table` referenced *source* `Table` ids rather than the
  `ModelTable` ids a reader following them into `design_document["tables"]` needs. Fixed by
  generating each `ModelTable`'s id once, in `_table_candidates`, and threading it through
  both the write and `_relationship_candidates`' own translation (`model_table_id_of`).
  Two S4.1.1 integration tests that had asserted the old, wrong id relationship were
  updated to assert the correct one (matched by `source_table_refs`, not raw id equality).
  Neither test suite had caught this because neither cross-checked a `.run()` response
  against a subsequent `GET /design` read — worth remembering: **a proposal-generation
  story's own tests should assert the immediate response and a fresh re-read agree**, not
  just that each independently looks plausible.
- `Modeller` gained a `.writer` property (mirroring `TrainPlanner`'s own), needed by
  `model_lifecycle.py`'s routes to reach the same writer the modeller itself uses.
- No ontology change — every property this story touches (`ModelFamily.state`,
  `SemanticModel.version`/`.grain_statement`/`.design_document`, `ModelTable.mode`) was
  already declared, most of it by S4.1.1 or earlier.

## Alternatives considered

**Build every transition (including `approve`/`deploy`/`promote`) now, ahead of the
stories that own their real side effects.** Rejected — see decision 1. An `approve` action
with nothing behind it but a state flip would answer S4.2.1's own question (who may
approve, what evidence is required) without the gate machinery that question actually
needs.

**A dedicated `state_transition` platform table, one row per move.** Rejected — see
decision 2. The event log already records exactly this fact for every node in the graph;
a second table would either duplicate it or drift from it the first time a write bypassed
one path but not the other.

**Hash the whole `SemanticModel`/`ModelTable` node set directly, bypassing
`read_design_document`.** Rejected — see decision 3. It would mean two document shapes
existed for "what this design is" — one for reading, one for hashing — and a change to one
without the other would silently decouple "what a caller sees" from "what was approved."

**A single generic `PATCH /v1/families/{id}/design` accepting a partial `design_document`.**
Rejected — see decision 4. It would let a caller rewrite fields this story never validates
(an arbitrary open question, an unrecognised RLS role shape) with no server-side check that
the result is still a coherent design — the opposite of the validation every other narrow
write in this API already performs.
