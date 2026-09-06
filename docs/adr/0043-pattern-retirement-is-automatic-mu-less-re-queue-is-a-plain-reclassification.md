# ADR 0043 — Pattern retirement is automatic; the MU-less re-queue is a plain reclassification

Status: accepted · 6 September 2026 · Story S5.5.2 (F5.5, continuing it)

## Context

S5.5.2 continues F5.5, following S5.5.1's own opening of the Pattern Library (ADR 0042):
*"As a platform engineer, I want patterns retired automatically when they fail, so that a
bad pattern cannot keep applying itself. A proof failure attributed to an ACTIVE pattern
increments its failure count; above the threshold (default 2 in 100 applications) it is
RETIRED automatically (MA-12, L4) and an event is raised. Retiring a pattern re-queues the
artefacts it produced that have not yet been ACCEPTED for regeneration."*

Direct comparison against §9.3's own text (not the backlog's paraphrase — the check this
codebase has already run once before, at S5.1.1's own 0.55-vs-0.35 threshold) found a real,
material discrepancy: §9.3 reads *"above a threshold (default 3 failures or a pass rate
below 0.97 over 30 applications), the pattern is automatically moved to RETIRED and every
MU that used it is flagged on the Parity Dashboard for re-proof."* This is not a rounding
difference — it is a different *shape* of condition (an absolute trip-wire **or** a ratio,
against a 30-application minimum) from the backlog's own single ratio ("2 in 100"). §13.2's
own autonomy table gives the third fact the AC's own "MA-12, L4" cites directly: *"MA-12
Retire pattern | L4 | Safety action; automatic on failure threshold"* — L4 (autonomous, no
approval), a deliberate asymmetry with MA-11's own L2 ("Platform Engineer approves") this
same section already gave promotion.

The AC's own second bullet inherits the identical MU-shaped gap S5.4.1 and S5.5.1 already
found and disclosed against this same specification: §9.3's own "every MU that used it is
flagged ... for re-proof" has no real Migration Unit to flag (§4.1.1 declares none; no
story has ever created one), and no real "ACCEPTED" artefact state exists either (no real
G3 gate has ever been built — S9.1.1/S9.1.2's own later, explicit scope, confirmed absent a
second time this session). The AC's own backlog wording — "re-queues the artefacts ...
for regeneration" — is what a codebase with neither of those things can actually build, and
is exactly what this story implements.

## Decisions

### 1. The threshold is the spec's own dual condition — spec wins on disagreement

`evaluate_retirement` implements §9.3's own literal text: `failures >= 3` **or**
(`applications >= 30 and pass_rate < 0.97`), either sufficient alone. An absolute trip-wire
catches a pattern that fails outright before 30 applications ever accumulate; the ratio
catches one that fails often without yet reaching three outright failures. Both numbers are
overridable function parameters (`failure_count_threshold`, `pass_rate_threshold`,
`min_applications`), the identical shape `calibration.build_report`'s own `floor` parameter
already has — an operator who wants the backlog's own "2 in 100" reading can still configure
toward it; the shipped default is the spec's.

### 2. Retirement is automatic — no route, no approval, matching MA-12's own L4 ceiling exactly

Unlike promotion (`promote_pattern`, an explicit `POST` a Platform Engineer calls),
`record_failure_and_maybe_retire` performs the retirement itself the instant a failure
attributed to a currently-ACTIVE pattern crosses the threshold. This is a deliberate,
spec-directed asymmetry, not an oversight: §13.2 gives MA-11 ("Promote pattern to ACTIVE")
ceiling L2 and MA-12 ("Retire pattern") ceiling L4 in the same table, in consecutive rows,
explicitly contrasting "Platform Engineer approves" against "automatic on failure
threshold." Building a human-approval step for retirement would contradict the one piece of
the autonomy ladder this story's own two action classes make unambiguous.

### 3. `failure_count` is a real, disclosed snapshot — never the authority a retirement decision reads

The AC's own literal words ("increments its failure count") name a real property, added to
`Pattern` (a `SpecDeviation`, the identical gap `Pattern.guards` already found in the same
§4.1.1 table for S5.5.1's own sibling fact). It increments on every recorded failure,
regardless of promotion_state — even a CANDIDATE's own failures count, since §4.3's own
`stats.proof_fail` example does not condition on promotion state either. But exactly like
`pass_count` before it, the counter is disclosed as a point-in-time snapshot: the actual
retirement decision is always computed live against `pattern_observation`'s own append-only
history, so a missed or double-counted increment could never itself cause a wrong
retirement — the same "never a maintained counter for a real decision" discipline this
module's own `pass_count`/`promotion_status` already established.

### 4. No real Migration Unit or ACCEPTED state exists, so every live Measure citing the pattern is re-queued, unconditionally

Confirmed a third time against this exact spec section (after S5.4.1's and S5.5.1's own
identical findings): §4.1.1 declares no `MigrationUnit` node, and nothing in this codebase
has ever marked an artefact ACCEPTED (no real G3 gate exists to grant that state). The
AC's own "not yet ACCEPTED" therefore excludes nothing real — every live `Measure` whose
`pattern_ref` names the retiring pattern qualifies. "Re-queued for regeneration" is
concrete: the stale Measure is retired (`GraphWriter.retire_node`, the only mechanism this
platform has for "this artefact is no longer trustworthy output"), and its source
`CalculatedField`'s own `class`/`pattern_ref`/`reason`/`classifier_version` are overwritten
with a fresh `classify.classify()` verdict — the plain, pattern-unaware classifier's own
real answer, computed exactly as `classify.py`'s own `reclassify_estate` already computes
it for any other field. Without the now-RETIRED pattern to match, that answer is almost
always C3 again — which is precisely what makes the field eligible for a real
`generate_c3_field` call the next time one is made, satisfying "regeneration" as an actual,
reachable next step rather than a flag nobody consumes.

### 5. "An event is raised" is a real notice, not a mutation a consumer has to infer

`EventType.PATTERN_RETIRED` (`estate.pattern.retired`) shares the outbox exactly the way
`SOURCE_DRIFT` already does (S1.2.4's own precedent): `mutates_graph` is `False`, so replay
skips it, and it carries the retirement reason and every re-queued Measure id in one place.
The Pattern's own `promotion_state` write is a separate, ordinary mutation (a normal
`NODE_UPSERTED`); the notice is what a Parity-Dashboard-style consumer would actually watch
for, without diffing every Pattern upsert to notice a retirement among them.

### 6. `apply_active_pattern`'s own sanity-check failure and the model-served ladder's own failure both feed the same retirement check

Both of S5.5.1's own failure-recording call sites (a deterministic pattern application
failing even `rules.dax_sanity_check`'s structural stand-in; a model-served ladder
exhausting attempts or declining, when an existing pattern already covers that shape) now
call one shared function, `record_failure_and_maybe_retire`, rather than
`record_observation` directly — a real behavioural change from S5.5.1's own shipped state
(which recorded the failure but never checked a threshold), not just new code alongside the
old.

## Consequences

- `ontology/nodes.py`: `Pattern.failure_count` (`T.INT`), schema version 20 (up from 19);
  new `SpecDeviation` entry, the sibling of S5.5.1's own `Pattern.guards` one.
- `events.py`: `EventType.PATTERN_RETIRED`, a new `pattern_retired()` constructor, and
  `mutates_graph` updated to exclude it (a second notice sharing the outbox with
  `SOURCE_DRIFT`).
- `patterns.py`: `DEFAULT_FAILURE_COUNT_THRESHOLD`/`DEFAULT_PASS_RATE_THRESHOLD`/
  `MIN_APPLICATIONS_FOR_RATE_CHECK`, `RetirementCheck`, `evaluate_retirement` (pure),
  `_retirement_check` (DB-backed wrapper), `_requeue_measures_for_retired_pattern`,
  `record_failure_and_maybe_retire` (the new shared entry point both failure sites call);
  `list_patterns` now also surfaces each pattern's own `provenance` (where
  `retired_at`/`retirement_reason` land) without a second lookup.
- `generation.py`: the ladder-failure hook now calls `record_failure_and_maybe_retire`
  instead of `record_observation` directly.
- No new migration: `Pattern.failure_count` is additive, covered by
  `tools/migration_check.py`'s own additive rule without a claiming migration file.
- `tests/test_events_and_retirement.py`: a pre-existing test hard-coded the notice-type set
  to exactly `{estate.source.drift}`; updated to name both notices explicitly now that a
  second one exists, rather than silently start passing on a broadened, unstated condition.
- New unit tests (`test_patterns.py`): `evaluate_retirement`'s own dual-condition
  arithmetic, both branches, both overridable, the zero-application edge case.
- New integration tests (`test_integration_patterns.py`): a CANDIDATE's own failures
  increment `failure_count` but never retire it; an ACTIVE pattern retires exactly at the
  third recorded failure (not before); the pass-rate condition retires with an overridden,
  test-scaled minimum sample; retiring a pattern retires its own Measure and reverts its
  source field's class/pattern_ref to a real, freshly-computed classification; a real,
  readable `estate.pattern.retired` event is written and found via `read_events`.

## Alternatives considered

**Implement the backlog's own "2 in 100" number instead of the spec's dual condition.**
Rejected — see decision 1. This repository's own standing rule ("spec wins on
disagreement") already settled an analogous discrepancy at S5.1.1; nothing about this one
is different in kind, only in which numbers are at stake.

**Require a Platform Engineer's own approval before retiring, mirroring promotion's own L2
gate.** Rejected — see decision 2. §13.2 explicitly contrasts MA-11 (L2) against MA-12 (L4,
"automatic") in the same table; building an approval step here would directly contradict
the one part of this story's own spec citation that is completely unambiguous.

**Skip `Pattern.failure_count` entirely and rely solely on `pattern_observation` for
visibility too.** Rejected — see decision 3. The AC's own literal wording names a counter
that increments; `pass_count`'s own S5.5.1 precedent already established that a disclosed,
non-authoritative snapshot alongside a real observation table is this codebase's own
accepted shape for exactly this kind of dual need (readable at a glance, never trusted for
the real decision).

**Leave re-queued artefacts merely flagged (a property on the Measure) rather than actually
retiring them and reverting the source field's class.** Rejected — see decision 4. A flag
nobody's own generation path reads would satisfy the AC's own words without satisfying its
own "so that" clause ("a bad pattern cannot keep applying itself") — the field must actually
become eligible for real regeneration again, not merely be marked as needing it.
