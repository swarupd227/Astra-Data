# ADR 0040 — Confidence calibration: a real floor onto a disclosed-absent tier

Status: accepted · 5 September 2026 · Story S5.3.3 (F5.3, closing it)

## Context

S5.3.3 closes F5.3, following S5.3.1 (ADR 0038, the ladder) and S5.3.2 (ADR 0039, the
Model Gateway): *"As a parity engineer, I want candidate confidence declared and
calibrated, so that confidence means something. Model declares a confidence in the output
schema; the platform records it and, per §16.3, reports calibration (declared vs observed
proof rate) in ten buckets. Below a configurable calibration floor a task class is routed
to the small-model-plus-proof path rather than trusted."*

Two facts, found by research before any code was written, shaped this story's scope
sharply. First, no calibration-observation record, bucket concept, or report of any kind
exists anywhere in this codebase — this is built from scratch. Second, and more decisive: a
near-duplicate, *fuller* version of this exact mechanism already exists later in the
backlog, as **S13.2.2** ("Calibration curve ... in ten buckets per agent per class;
calibration error reported; a class whose error exceeds 0.2 is routed to the
small-model-plus-proof path automatically and flagged on the Pattern Library"), in **F13.2**
under **milestone I6 "Release and calibrate"** — a later wave than F5.3's own milestone I3
"Generate", scheduled after the Calibration Wave (E13/F13.1) exists to generate real
evaluation data at scale. No story anywhere in the backlog stands up a real small-model
provider integration either — "small model tier" is only ever a routing *destination name*
(§5.4, §16.3), never an integration task. Given this, the scoping question that mattered was
put to the product owner directly: when a task class's calibration crosses the floor, should
the "small-model-plus-proof path" it reroutes to call a real second model, or stay a
disclosed gap the way every other never-built-yet destination in this epic already does? The
explicit choice was the disclosed-absent path — real routing logic, no fabricated tier.

## Decisions

### 1. A real, append-only observation store — not only successes

`PostgresCalibrationStore` (migration v0022, `public.calibration_observation`) records one
row per `LadderAttempt` that declared a confidence, for *every* generation call, successful
or not — a curve built only from `GENERATED_PROVED` survivors would be trivially 100% at
every bucket, since a failed attempt's confidence is otherwise hashed away into an opaque
`ExceptionCase.evidence_ref` and never durably queryable at all (confirmed by research: only
a successful generation's `ProvenanceRecord.confidence` was ever populated before this
story). The table is append-only, the identical "an edit is a new version" footing
`conformance_ruleset`/`model_gateway_policy` already set — a full history survives, not just
a last-known verdict.

### 2. "Observed pass" is the epic's own real, disclosed proxy for proof — a third time, not a new one

§16.3's own worked example ("declared 0.9 passing at 0.7") means passing *real* proof; no
Arbiter exists to grant one (E7, the identical gap ADR 0038/0039 already disclosed for the
ladder and the eval gate). "Observed pass" here is exactly what those two already meant: a
candidate that cleared rung 1 (schema) and rung 2 (parse) on its own attempt
(`LadderAttempt.parse_ok`). Reusing the identical definition a third time, rather than
inventing a fourth, slightly different one, keeps every "proof rate" number in this codebase
meaning the same real thing wherever it appears.

### 3. The floor is on observed pass rate, matching "floor" as a minimum, not on calibration error

§16.6 separately names "calibration error" (mean |declared − observed|, target ≤ 0.08) as its
own accuracy metric — a *ceiling* concept, phrased with "≤". The AC's own word is "floor", a
*minimum* — read most naturally as a floor on the observed pass rate itself
(`CalibrationReport.overall_pass_rate`), not a ceiling on the error term, and `build_report`
computes and exposes both, but only the pass-rate floor drives routing. `DEFAULT_CALIBRATION_FLOOR
= 0.80` reuses §16.6's own "Class 3 proof rate ≥ 0.80" target and `gateway.ROUTABLE_THRESHOLD`'s
own number, rather than inventing a third, arbitrary threshold for the same underlying bar. A
minimum sample size (`MIN_OBSERVATIONS_FOR_FLOOR_CHECK = 10`, matching this story's own "ten
buckets") guards against judging a task class's calibration on no data at all — §16.3's own
framing is about *drift* from a measured calibration, not a verdict reached on nothing.

### 4. The small-model-plus-proof path is a real routing decision onto a disclosed-absent destination

This story's own explicit scope decision, made directly by the product owner via a
structured question: no real second model is built. `gateway.py` gains a second, real
`TaskClass` (`TRANSPILE_C3_SMALL_MODEL`); `generate_c3_field` computes a real decision —
`TRANSPILE_C3_SMALL_MODEL` if `TRANSPILE_C3`'s own calibration has crossed the floor, else
`TRANSPILE_C3` — and passes it straight into the existing `gateway.generate(task_class=...)`
call, unchanged from S5.3.2's own shape. No `ModelCaller` is ever registered under the small
tier (`build_gateway`'s own provider map holds only `anthropic`, under `TRANSPILE_C3`), so a
reroute correctly raises `GatewayRoutingError` and escalates honestly to the Exception Desk —
the identical "disclosed absent, not a fake failing provider" footing Azure OpenAI already
has under `TRANSPILE_C3` itself (ADR 0039). Nothing about the routing decision itself is
fabricated; only the destination it sometimes points to is honestly empty.

### 5. Once triggered, a reroute stays triggered — by design, matching §16.3's own "until reviewed"

With no real small-model provider, every subsequent rerouted call raises immediately, before
any model is ever reached, so no new observations are recorded for `TRANSPILE_C3` while it
stays rerouted — the calibration that triggered the reroute can never move again on its own.
This is not a gap this story failed to close: §16.3's own wording is "pins routing to the
reasoning tier until *reviewed*" — a sticky, human-reviewed state by the spec's own design.
Building the review/un-pin workflow (and the automatic, 0.2-calibration-error-triggered
version of this same mechanism, and its own Pattern Library flag) is S13.2.2's own later,
explicit, separate scope — not silently assumed solved here.

### 6. No console screen

A calibration-curve *screen* is S13.2.2's own explicit differentiator ("flagged on the
Pattern Library", F13.2, milestone I6, after the Calibration Wave exists to generate real
evaluation data at scale). This story's own acceptance criteria asks for a report, not a
screen — `GET /v1/model-gateway:calibration` (any Artizent role) is the real, queryable
report; building a Programme Board pane now would duplicate scope a sibling story already,
explicitly, owns, and there would be little real signal to show one against yet (a fresh
deployment's own `TRANSPILE_C3` history starts at zero observations).

## Consequences

- New module `calibration.py`: `DEFAULT_CALIBRATION_FLOOR`, `MIN_OBSERVATIONS_FOR_FLOOR_CHECK`,
  `CalibrationBucket`/`CalibrationReport`/`build_report` (pure aggregation, unit-tested
  without a database), `CalibrationStore`/`PostgresCalibrationStore`/`NullCalibrationStore`.
- New migration v0022: `public.calibration_observation` (append-only, platform table, no
  ontology change).
- `gateway.py` gains `TRANSPILE_C3_SMALL_MODEL`.
- `generation.py`: `GenerationEngine`/`generate_c3_field` gain a `calibration` parameter
  (defaulting to `NullCalibrationStore`, which always answers "not below floor" so a
  deployment with no calibration wired yet behaves exactly as S5.3.2 already did);
  `generate_c3_field` checks the floor before running the ladder and records a real
  observation for every attempt with a declared confidence afterward; `GenerationOutcome`
  gains `task_class`, disclosing which tier actually served a given call.
- New route: `GET /v1/model-gateway:calibration` (any Artizent role; `task_class` and
  `floor` as optional, bounded query parameters — the established convention this codebase
  already uses for a configurable business threshold, e.g. `routes_quality.DEFAULT_THRESHOLD`,
  rather than a `Settings`/env-var field).
- `main.py` wires a real `PostgresCalibrationStore` (`app.state.calibration`) into
  `GenerationEngine`.
- Real Postgres-backed tests for the store's own accumulation and floor check
  (`test_integration_calibration.py`), for the routing decision inside `generate_c3_field`
  against a real `ModelGateway`/`PostgresGatewayPolicyStore` (`test_integration_generation.py`),
  and for the new HTTP route (`test_integration_gateway.py`); pure aggregation math
  (bucketing, calibration error, floor boundary conditions) is tested without a database
  (`test_calibration.py`).

## Alternatives considered

**Wire a real second Anthropic model (e.g. Haiku 4.5) as the small-model tier.** Rejected —
the product owner's own explicit choice was the disclosed-absent path. No backlog story
scopes a real small-model integration; building one here would be scope beyond what any
story currently asks for, unlike S5.3.2 where real Anthropic integration was the story's own
explicit scope.

**Compute the floor check against calibration error (≤ some ceiling) instead of observed
pass rate.** Rejected — see decision 3. The AC's own word is "floor", read most naturally as
a minimum on the pass rate itself; calibration error is still computed and reported (§16.6
alignment) but does not drive routing in this story.

**Build the full S13.2.2 mechanism now (per-agent granularity, automatic 0.2-drift routing,
a Pattern Library flag) rather than the narrower S5.3.3 slice.** Rejected — the milestone
split (F5.3 in I3 "Generate" vs. F13.2 in I6 "Release and calibrate", after the Calibration
Wave exists) is a deliberate sequencing decision in the backlog itself, not an oversight this
story should silently close early. Building it now would also mean fabricating the very
evaluation-at-scale data (E13/F13.1) that later milestone exists to generate for real.

**Try to make a rerouted task class self-heal (e.g. periodically re-test the reasoning tier
even while pinned).** Rejected — see decision 5. §16.3's own wording makes this a
human-reviewed state by design; inventing an automatic recovery path not asked for by any
story would quietly change what "pinned until reviewed" means.

**Add a Programme Board pane for the calibration report now, following the S5.1.1/S5.2.1
precedent.** Rejected — see decision 6. Unlike those two stories, a sibling story in this
exact epic family (S13.2.2) explicitly names the screen as *its own* later differentiator;
building it now duplicates scope a specific, identified story already owns.
