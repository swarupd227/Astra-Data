# ADR 0026 — The Wave Board moves and resequences; it does not model §3.2's state machine

Status: accepted · 4 September 2026 · Story S3.2.2 (E3 / F3.2)

## Context

S3.2.2 asks for a board a Programme Manager re-plans from directly: *"Kanban of trains →
states with MU cards; drag to re-sequence within a train or move between trains; a move
that breaks a family dependency is refused with the reason. WIP limit per train and per
state is configurable and shown; exceeding it warns and requires a reason. Every change is
an event and appears on the Programme timeline."*

Four questions decided the shape of the work: what a card's "state" actually is when no MU
state machine has been built; what "warns and requires a reason" means in a codebase where
every other write either succeeds or refuses outright; what "appears on the Programme
timeline" requires given no such surface exists yet; and what happens when the backlog
assigns "the Wave Board" to two different stories.

## Decisions

### 1. A card's state is stored, shown, and never transitioned by this module

§3.2 defines fifteen states with a real, restrictive transition graph (`CLUSTERED` reaches
only `MODEL_READY` or `BLOCKED`, never `PROVING` directly) driven by components this
codebase has not built — a Transpiler, a Compositor, an Arbiter, a Mender. Building that
machine now, for a re-planning board, would answer a question three-plus future epics own.

Re-reading the acceptance criterion closely settles this cleanly: it names exactly two drag
actions — *"re-sequence within a train"* and *"move between trains"* — never *"drag between
state columns."* States are the kanban's grouping axis, not something this story's board
writes. `IN_TRAIN.state` (new, additive, a plain `T.STRING` — not an enum, deliberately
matching `migration_units.py`'s own choice to hold `MU_STATES` as strings because "the state
machine belongs to the control plane, and this service should not be the place it is
defined") is set once, to `DEFAULT_MU_STATE` (`CLUSTERED` — §3.2's own "entered by the
Cartographer," and being assigned to a train is exactly that), when `TrainPlanner.run`
first creates the edge. A move or resequence carries the existing value forward; neither
ever changes it.

### 2. A family-split refusal is a hard block; a WIP-exceeded move is a soft warning — different mechanisms, on purpose

§3.3's whole reason for a train to exist is that a family is designed once inside it —
S3.2.1's packing already guarantees this on a fresh proposal, and a move that would leave a
family straddling two trains is refused unconditionally, with no reason that makes it
acceptable, because it isn't a judgement call.

A WIP limit is different — the story's own words, *"exceeding it warns and **requires** a
reason,"* imply normal moves need none. `move_mu` is built accordingly: called without a
reason, an over-limit move is refused, naming the limit and the count; called again with
one, it proceeds and the reason lands on the new edge (`IN_TRAIN.wip_override_reason`,
additive). This is the first "soft" outcome in this codebase — every other write until now
either succeeds or refuses outright (`errors.py`'s eight exception types are all-or-nothing)
— implemented inside that same all-or-nothing contract rather than inventing a new
"succeeded, but here's a warning" response envelope: the caller's second attempt, with a
reason attached, is simply a normal request that now passes validation.

**A consequence worth stating plainly: a multi-member family cannot be moved between trains
through this board.** Moving one member while its siblings stay behind always fails the
split check, for every member, in every order — there is no sequence of single-MU moves
that reunites a family elsewhere. This is not a gap the test suite works around; it is what
the acceptance criterion's own scope (one MU dragged at a time) implies once combined with
S3.2.1's family-atomicity rule. A batch "move this whole family" action is future scope this
story does not claim.

### 3. "Appears on the Programme timeline" is answered with what already exists, plus one resolver

`GET /v1/events` (built at S1.1.3, not S3.2.2) already pages the whole mutation outbox and
can filter to one `subject`. Every Wave Board write already goes through `GraphWriter`, so
it was already going to emit a CloudEvent with no new work — the acceptance criterion's
first half ("every change is an event") was true before this story touched anything.

The gap was narrower than it looked: an event's `subject` is the one element a mutation
touched — a train's own node id, or one `IN_TRAIN` edge's id — never "this train" as a set.
Resolving "everything that happened to train X" means knowing every edge that has ever
pointed at it first. `trains.train_event_subjects` does exactly that (live and retired
`IN_TRAIN` edges, plus the train's own id), and `GET /v1/trains/{id}/events` filters a
bounded recent window (2000 events) down to that subject set. This is a recent-activity
view, not a full audit archive — `GET /v1/events?subject=` already serves deep history for
one element, and a train with meaningful age deserves its own paginated read if that is ever
asked for; nothing here claims to be that.

No new `EventType` was needed — `EDGE_RETIRED`/`EDGE_UPSERTED`/`NODE_UPSERTED` (all existing
since S1.1.3/S3.1.2) cover every Wave Board write.

### 4. The backlog gives "the Wave Board" to two stories; this one builds the mechanics, not the frame

S10.2.1 (*"the Programme Board, Wave Board, Calibration Report and Status Pack"*) also names
this screen, as part of a four-surface console epic not yet reached. The split mirrors
S3.1.3's Programme Board precedent exactly: this story ships a real, working screen scoped
to its own three criteria (drag, WIP, per-train activity) at `/trains`; S10.2.1 later
absorbs it into whatever larger layout the Programme surface needs (family-dependency lines
between cards, milestones, the rest of §15.3's KPI strip) rather than replacing it outright.

### 5. Native HTML5 drag-and-drop, no new dependency

`services/console-web/package.json` has never carried a drag-and-drop library, and nothing
elsewhere in the console needed one until this story. `draggable`/`onDragStart`/
`onDragOver`/`onDrop` are sufficient for two lanes of interaction (reorder within a column,
move between columns) without pulling in `react-dnd`/`@dnd-kit` for a console that has kept
its dependency footprint at `react`/`react-dom` plus tooling since S1.4.1.

## Consequences

- `ReleaseTrain` gained `wip_limits`, `overridden`, `override_action`, `override_reason` —
  S3.1.2's `ModelFamily` pinning mechanism, reused verbatim: `TrainPlanner.run` (S3.2.1)
  now reads `overridden` the same way `Cartographer.run` does, so a re-propose leaves a
  Programme Manager's move, resequence or WIP configuration alone unless the train's id is
  named in `confirm_train_ids`. Without this, S3.2.1's existing full-replace-on-every-run
  behaviour would have silently destroyed every Wave Board edit the next time anyone ran
  `:propose` — not a hypothetical, a structural certainty this story had to close.
- A hydrated property dict can never be spread directly into a `NodeWrite`/`EdgeWrite` —
  `created_by`/`created_at`/`written_by`/`created_in_run` (and their `retired_*` cousins)
  are `server_managed` and refused if a caller resupplies them. Every place in
  `train_overrides.py` that "preserves everything else" on an upsert filters these first
  (`_writable_node_properties`/`_writable_edge_properties`) — found by the first real
  integration run, the same class of gap S3.1.2 and S3.2.1 each hit once before it.
- The Wave Board is this codebase's first "warn, don't block" mutation. If a later story
  needs the same shape again, this is the precedent: validate, and on a soft failure refuse
  with a message a client can literally resubmit with a reason attached — not a new response
  envelope.

## Alternatives considered

**Build §3.2's state machine now, since a card needs "a state" to exist at all.** Rejected
— see decision 1. The acceptance criterion needs a display value, not a machine; building
one three epics early would answer a question nobody asked this story.

**Let a move cascade to every member of a family, so a whole family can relocate.**
Rejected: not in the acceptance criteria (one MU per drag), and a cascading multi-write
action changes the blast radius of "drag a card" in a way this story never asked a
Programme Manager to reason about. Flagged as a real gap (decision 2), not silently
patched.

**A distinct "confirm" endpoint for a WIP-exceeded move, rather than resubmitting the same
request with a reason.** Rejected: it would mean the client tracks two different pending
states for what is, from the caller's point of view, one action — retrying the identical
request with one field filled in is simpler for the Wave Board to implement and matches how
every reason-gated action elsewhere in this codebase already works (type a reason, resend).

**A drag-and-drop library.** Rejected — see decision 5. Two lanes of interaction do not
justify a new dependency this console has avoided since its first screen.
