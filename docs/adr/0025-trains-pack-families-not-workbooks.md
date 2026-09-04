# ADR 0025 — Trains pack families whole; ordering follows the backlog's three factors, not §8.5's

Status: accepted · 4 September 2026 · Story S3.2.1 (E3 / F3.2)

## Context

S3.2.1 asks for release trains proposed from families and usage: *"Trains are proposed by
ordering families by (shared model readiness, usage, tier mix) and packing MUs to a
configurable train size; the proposal explains each train in one paragraph generated from
the graph... Train membership, planned start and end, and gate schedule are stored as
ReleaseTrain nodes; an MU is IN_TRAIN exactly one train at a time."*

Four design questions decided the shape of the work: what a "Migration Unit" actually is in
this graph today, which three factors really order the families (the backlog's wording and
§8.5's differ), whether a family may ever be split across trains, and what "gate schedule"
means when no gate has a train as its subject.

## Decisions

### 1. There is no Migration Unit node — packing MUs means packing Workbooks

§4.1.1's own note on the `Workbook` node type has said "One Migration Unit per Workbook"
since S1.1.1; `migration_units.py`'s control-plane stub (`MigrationUnitRegistry`,
`NullMigrationUnitRegistry`) is explicit that a full MU record is a later story's job, not
this one's. `IN_TRAIN` (`Workbook → ReleaseTrain`, §4.1.2) has been declared, unused, since
the ontology's founding — this story is its first write. Building a real MU record now
would be answering a question S3.2.1 was never asked.

### 2. Ordering uses the backlog's three named factors, not §8.5's reworded three

§8.5 describes the Cartographer's train cost function as preferring "high reuse, high usage
and early-renewal sites." The backlog's own rule (repeated at every prior correction in this
project — see ADR 0022) is that the specification wins on disagreement. It does not win
here, for a concrete reason: "early-renewal sites" needs a site licence-renewal date, and
nothing this platform harvests — §4.1.1's `Site` node, the Metadata API, the licence export
— carries one. The spec names a factor this graph cannot answer with real data. The
backlog's three factors — shared model readiness, usage, tier mix — are what this story's
own acceptance criterion states as the algorithm to build, and every one of them is already
answerable from data this graph holds (`ModelFamily.state`, `Workbook.views_90d`,
`scope_decision.tier`). Implementing them is not a deviation from the acceptance criterion
handed to this story; it is the criterion.

Concretely:

- **Shared model readiness** — a family's position in its own §12.2 lifecycle. SINGLETON
  and PROPOSED rank equally (neither has passed G2); DRAFT through PUBLISHED rank
  increasingly ready.
- **Usage** — the sum of `Workbook.views_90d` (S1.2.3) across a family's members.
- **Tier mix** — the mean tier complexity (SIMPLE < MODERATE < COMPLEX < REDESIGN, S1.4.1)
  across whichever members have been tiered. A family with no tiered member contributes no
  signal at all rather than being scored as maximally complex — most of an estate is
  untiered until a Programme Manager acts on it, and penalising every family nobody has
  looked at yet would be scoring absence of information as risk.

Families sort by `(-readiness, -usage, tier_score)` — most ready, most used, simplest
first — and that sort order is exactly what `pack_trains` walks.

### 3. A family is never split across trains

§3.3's entire reason for a train to exist is that "each model family is designed and
approved once" within it. Splitting a family's members over two trains would mean designing
it twice — the opposite of the story's own "So that." Packing is therefore family-atomic: a
family's whole membership goes into one train or the next, never divided. A family larger
than a train's remaining room still lands there entire — a train is never left artificially
empty for want of a family that fits — and any families left over once every configured
train has taken its share land in the *last* one. Every Workbook ends up `IN_TRAIN`
somewhere; the acceptance criterion's "exactly one train at a time" would be broken by a
workbook this module silently dropped as much as by one it double-booked.

### 4. Gate schedule is a planned window, not a projection

§13.1 gates a family at G2 and a Migration Unit at G3; no gate has a *train* as its subject.
"Gate schedule," stored on `ReleaseTrain` as a new `gate_schedule: JSON` property (additive,
schema version 11, no migration needed — see `nodes.py`'s own note on why: this is an AGE
node property, not a relational column), is the simplest honest thing this story can put
there: G2 clustered near the train's planned start (families are meant to be confirmed
before generation begins), G3 near its planned end (units are accepted as they finish). This
is a first-cut plan a Programme Manager edits, explicitly not the throughput-based forecast
§14.2's wave scheduler produces — that projection is backlog story S3.2.3's job, and nothing
here pretends otherwise.

### 5. `mu_refs` is not populated; `IN_TRAIN` edges are the only source of truth

`ReleaseTrain.mu_refs` (a `STRING_LIST`) has existed in the ontology since S1.1.1 alongside
`planned_start`/`planned_end`. This story does not write it. `ModelFamily` carries no
members-list property either — `IN_FAMILY` edges are its only membership record — and giving
`ReleaseTrain` a second, redundant membership record would just be a second place train
membership could drift from the edges that are actually queried.

### 6. No `Wave` node

§3.3 also defines `Wave` as a calendar window containing one or more trains. Nothing in this
story's acceptance criteria mentions one. `Wave` has been declared, unused, in the ontology
since S1.1.1 — same position `ReleaseTrain` was in until today — waiting for whichever
backlog story actually asks the platform to group trains into calendar windows.

### 7. A re-run replaces every train it wrote, families are never retired

Nothing yet lets a Programme Manager edit a proposed train (that is S3.2.2's Wave Board, not
built), so unlike the Cartographer's family re-clustering there is no "a human already
touched this" case to protect on a train re-run — every existing live `ReleaseTrain`, and
every live `IN_TRAIN` edge, is retired before the fresh proposal is written. This is also
what keeps "an MU is IN_TRAIN exactly one train at a time" true across repeated proposals:
without retiring the stale edges first, a workbook reassigned to a different train on a
re-run would hold two live `IN_TRAIN` edges at once. `ModelFamily` nodes themselves are
never touched by this module — trains are read from families, not the other way round, and
retiring a family is S3.1.1/S3.1.2's business, not this one's.

When S3.2.2 lands, S3.1.2's `overridden` pinning mechanism for families is the direct
precedent to reuse for trains a Programme Manager has since edited on the Wave Board.

## Consequences

- `count_families`-style live reads, not a cached run history, back every figure this story
  reports — the same "measured, not stored" posture S3.1.3 established for the family count
  itself.
- A Programme Manager reading a proposal sees exactly which workbooks a proposal does *not*
  cover (`unclustered_workbook_ids`, named rather than only counted — the Estate Explorer's
  `PENDING_COLUMNS` set the precedent for naming a gap instead of hiding it behind a number).
- The 277/328/184/177/101 BlackRock figures are reproduced as this module's own default
  (`BLACKROCK_DEFAULT_TRAIN_SIZES`) with no derivation to validate against — no worked
  example for them exists anywhere in the spec (confirmed by a full-text search); they are
  the backlog's own stated target, not a number this algorithm is proven to reproduce
  exactly against any real fixture, since no 1,067-workbook fixture exists in this codebase
  today. Sizes are editable precisely because the default is a target, not a law.
- Test isolation: this story's own integration suite hit the now-familiar "integration tests
  share a graph" trap in a new shape — `TrainPlanner` legitimately reads the *whole* graph's
  families (correct production behaviour), so a later test's proposal includes every earlier
  test's leftover families in the same module. Fixed by asserting on train membership
  (`_train_containing(result, workbook_id)`) rather than on train index or exact counts,
  the same fix already applied in `test_integration_family_overrides.py` and
  `test_integration_cartographer.py`.

## Alternatives considered

**Correct the ordering factors to §8.5's wording, adding a `renewal_date` property to
`Site`.** Rejected: nothing in the Metadata API or the licence export gives this platform a
real renewal date to harvest, so the property would sit permanently null — an invented field
answering a question this platform has no way to ask the source estate.

**Allow a family to be split across trains when it doesn't fit.** Rejected — see decision 3.
It directly contradicts §3.3's stated purpose for a train's existence.

**Build the real Migration Unit record now, since this story is E3's first candidate to
need one.** Rejected: nothing in S3.2.1's acceptance criteria asks for MU state, tier
inheritance at creation, or the §3.2 state machine — only that a Workbook is `IN_TRAIN`
exactly one train. Building the control-plane record ahead of the story that actually reads
MU state would be scope this story was not asked for.

**Store the wave-scheduler's throughput projection instead of a placeholder gate
schedule.** Rejected: §14.2 explicitly gives that computation to the wave scheduler
(backlog S3.2.3), which needs measured throughput over a trailing window that does not
exist yet — there is nothing to project from before any MU has moved through a single
train.
