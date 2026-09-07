# ADR 0054 — INCONCLUSIVE as a first-class outcome: a broadened retry, and a live rate

Status: accepted · 7 September 2026 · Story S7.3.2, closing F7.3

## Context

S7.3.2 closes F7.3 — spec §10.2: *"As a platform engineer, I want INCONCLUSIVE to be a
first-class outcome distinct from FAIL, so that infrastructure problems do not look like
migration defects."*

- Timeout, adapter error, executor error and sampling shortfall produce INCONCLUSIVE
  with the reason class; the orchestrator retries once with a longer budget
- Inconclusive rate is a Platform Health metric with an alert threshold (default 2%)

S7.3.1 (immediately prior, ADR 0053) built dual execution itself — both sides run,
stored as Parquet — but had no timeout, no retry, and classified every failure with a
single free-text `reason` string. This story is the first to give a failed execution a
real, typed cause, a real second chance, and a real, live health signal.

## Decisions

### 1. `InconclusiveReason` lives in the SDK, next to `ExecutionOutcome`

`ExecutionOutcome` already existed in `astra_adapter.proof`, its own docstring already
quoting §10.2's retry sentence verbatim (written at S2.4.1, before E7 existed). A new
sibling enum — `TIMEOUT`, `ADAPTER_ERROR`, `EXECUTOR_ERROR`, `SAMPLING_SHORTFALL`, the
AC's own four words — and a new `ResultSet.reason_class: InconclusiveReason | None`
field follow the identical "the shapes an adapter must accept and produce are defined
here, now" reasoning that module's own docstring already gives for why `ResultSet`
itself lives in the SDK rather than in E7's own later code.

### 2. The retry rule is broadened from §10.2's own timeout-only wording to every reason class — a disclosed backlog elaboration

§10.2's own prose ties "retried once with a longer budget" to a timeout specifically.
The backlog's own AC reads as one retry rule for its whole sentence ("Timeout, adapter
error, executor error and sampling shortfall produce INCONCLUSIVE... the orchestrator
retries once with a longer budget") — covering all four causes, not only the first.
Implemented the broader way, the identical "broaden the spec's one worked scenario to
the general case" reading S7.2.1's own filter-context elaboration already used (ADR
0051).

**The one exception, invented here and disclosed:** an adapter's own honest capability
decline (e.g. `FixtureSourceAdapter.execute_case` returning `INCONCLUSIVE` because
neither `extract_read` nor `live_query` is claimed — no exception raised at all) is
never retried. A longer budget fixes a slow warehouse, not a capability the deployment
does not have; retrying would only repeat the identical decline. Distinguished cleanly:
every INCONCLUSIVE this orchestrator itself classifies carries a `reason_class`; an
adapter's own decline does not (`reason_class` stays `None`), and `_run_with_retry`
checks exactly that field to decide whether to retry.

### 3. "Sampling shortfall" is declared, never produced

§10.4 (sampling) is F7.4's own later, unbuilt scope — confirmed directly against the
backlog's own F7.3/F7.4 boundary a second time (ADR 0053 already confirmed this for the
diff/verdict itself). The fourth `InconclusiveReason` member exists for the AC's own
completeness, for F7.4 to raise the moment sampling exists, not because anything in
this story's own code path can produce it.

### 4. Timeout is real `asyncio.wait_for`, not a stub

`_attempt_once` wraps each adapter/executor call in `asyncio.wait_for(call(), timeout=
budget_seconds)`. A `TimeoutError` becomes `INCONCLUSIVE(TIMEOUT)`; any other raised
exception becomes `INCONCLUSIVE` with whichever reason class the caller names —
`ADAPTER_ERROR` for the source side, `EXECUTOR_ERROR` for the target side — §10.2's own
two named causes for a failed execution, made concrete. `_run_with_retry` composes two
attempts: the charter's own `timeout_seconds` first, then `timeout_seconds *
DEFAULT_RETRY_TIMEOUT_MULTIPLIER` (an invented, disclosed `2.0` — neither the spec nor
the backlog says how much longer, the identical "invented, disclosed bound" footing
`MAX_FILTER_VALUES_PER_FILTER` (S7.2.1) and `DEPLOY_RETRY_DEFAULT`'s own backoff
schedule (S6.1.2) already set for their own unspecified knobs).

### 5. `CaseExecutionService` now holds an `ExecutionCharter` — a real, disclosed gap on its own

`astra_adapter.proof.ExecutionCharter` (`timeout_seconds`, `strategy_order`,
`per_case`) has existed since S2.4.1 but was never once constructed or read by
`graph-svc` before this story — confirmed directly, `case_execution.py` imported
`ExecutionOutcome`/`ExecutionStrategy`/`ResultSet` from the SDK but never `ExecutionCharter`
itself. `CaseExecutionService.__init__` now accepts one, defaulting to
`ExecutionCharter()` (`DEFAULT_TIMEOUT_SECONDS = 120.0`) when none is given. No route or
store persists a charter anywhere yet — a real, disclosed gap this story does not close,
left for whichever future story wants a platform engineer to tune it live, the same
footing `MAX_FILTER_VALUES_PER_FILTER`/the retry multiplier already have. This is
distinct from — and never confused with — §4.4's own Tolerance Charter (diff
tolerances), which this module still never reads.

### 6. Every side-execution is recorded, win or lose — the identical `pattern_observation`/`calibration_observation` discipline

`record_execution_observation` appends one row per side per case per execution to a new
`public.execution_observation` table (migration v0028) *regardless of outcome* — not
only failures. Append-only, never an update: `inconclusive_rate` is always computed
live from the complete history this platform has actually seen, the identical "never a
maintained counter" discipline `patterns.record_observation`/`calibration.
PostgresCalibrationStore.record` already established for their own metrics (S5.5.1/
S5.3.3). Recording every outcome, not only failures, is what gives the rate a real
denominator.

### 7. The inconclusive rate is windowed, not all-time — an invented, disclosed default

`inconclusive_rate` computes `inconclusive / total` over a trailing
`DEFAULT_INCONCLUSIVE_WINDOW_HOURS = 24.0` window, against `DEFAULT_INCONCLUSIVE_
RATE_THRESHOLD = 0.02` (the AC's own literal default). Neither number is specified by
spec or backlog beyond that one default threshold; the window is this story's own
addition, reasoned directly: an operational alert should reflect what the platform is
doing *now*, not an inconclusive spike from months ago that has long since stopped
recurring. `calibration.build_report` computes over *all* observations per task class
(a calibration curve is a slow-moving, historical fact); `patterns.evaluate_retirement`
windows by *count* ("30 applications"); this metric windows by *time*, since it is
explicitly an operational health signal, not either of those other two shapes.

### 8. The metric is surfaced from `GET /v1/platform/health`'s own existing computed-on-read shape — not a new exposition mechanism

Confirmed by direct research: no `prometheus`/OTel dependency, no `/metrics` route, no
metrics-store module exists anywhere in this codebase today — every "metric" so far
(calibration, pattern promotion stats, this one) is a computed-on-read HTTP GET.
`routes_platform.py`'s own docstring already states the model: *"Specification §15.3.3
wants more on that screen than one service holds... which arrive with the epics that own
them (E12/F12.3). This is the graph service's contribution."* A new `_execution(state)`
section, matching `_drift`/`_schedules`/`_harvests`'s own exact shape, is added to that
same route rather than building a new mechanism. The real Platform Health *screen*
(S12.3.2) and its real OpenTelemetry-backed metrics pipeline (S12.3.1) stay E12/F12.3's
own later, unbuilt, explicitly-scoped-elsewhere work.

### 9. Found live: a pre-existing `app.state.conformance_store` naming collision, silently shadowing the adapter conformance store since S4.3.2 — fixed at the root

Live-smoke-testing this story's own new `"execution"` section on `GET /v1/platform/
health` hit a real 500, but *before* that section ever ran: `_conformance` (an
unrelated, pre-existing section, S1.2.4/S2.1.2) raised `AttributeError:
'PostgresConformanceRulesetStore' object has no attribute 'promotion'`. Root cause,
confirmed by direct research: `main.py` wires *two* completely different stores onto
the identical `app.state.conformance_store` name — S2.1.2's own adapter-promotion
`PostgresConformanceStore` (line 158) and S4.3.2's own model-build-rules
`PostgresConformanceRulesetStore` (line 228, added two milestones later) — with the
second silently clobbering the first at every real startup. `routes_adapters.py` and
`routes_platform.py`'s own `_conformance` section (both want the adapter store) had
been reading the wrong type since S4.3.2 shipped; `routes_conformance.py`/
`routes_g2.py`/`routes_modeller.py` (all three actually want the ruleset store)
happened to keep working, since the second, later wiring is what "won." No test caught
this because every existing test — unit and integration alike — constructs its own
isolated app and wires only the one store it needs by hand; none exercises the real,
full `main.py` startup path where both assignments run in sequence against the same
app instance. Fixed at the root, the identical "found live, fixed at the root, not
worked around" posture ADR 0044 (the nginx resolver bug) and ADR 0045 (the dashboard
zone-shape bug) already set for this codebase: the ruleset store's own attribute
renamed to `app.state.conformance_ruleset_store`, its three real readers updated, and
the four test fixtures that wire it directly (`test_integration_build.py`,
`test_integration_versioning.py`, `test_integration_conformance_rules.py`) updated to
match. `test_adapter_promotion.py` (the adapter store's own consumer) needed no
change. A dedicated automated regression test exercising the real, full `lifespan()`
startup was considered and declined: `config.settings()` is a process-global
`lru_cache(maxsize=1)`, and a test that clears/repopulates it mid-suite risks
destabilising every *other* integration test's own settings in the same pytest
process — a disproportionate new risk for one wiring bug. Verified instead by the full
graph-svc suite (1,611 passed + 2 skipped, unchanged) and a live Docker smoke test
showing both `GET /v1/platform/health`'s own `"conformance"` section and `GET
/v1/conformance/rules` now read their own correct, distinct store simultaneously off
the one real running app.

## Consequences

- `astra_adapter.proof`: `InconclusiveReason` enum added; `ResultSet.reason_class:
  InconclusiveReason | None = None` added.
- `services/graph-svc/src/astra_graph/case_execution.py`: `_inconclusive` now requires
  `reason_class`; new `_attempt_once`/`_run_with_retry` helpers; `_execute_one_case`/
  `execute_cases_for_workbook` gain a `charter: ExecutionCharter` parameter;
  `CaseExecutionService` gains an `execution_charter` constructor parameter and an
  `inconclusive_rate()` method; new `record_execution_observation`/`inconclusive_rate`
  module functions; new constants `DEFAULT_RETRY_TIMEOUT_MULTIPLIER` (2.0),
  `DEFAULT_INCONCLUSIVE_RATE_THRESHOLD` (0.02), `DEFAULT_INCONCLUSIVE_WINDOW_HOURS`
  (24.0).
- New migration `v0028_execution_observation.py`: `public.execution_observation`,
  append-only. No ontology change — schema version stays 27, confirmed unchanged by
  `ontology_check.py --spec`.
- `routes_platform.py`: new `"execution"` section on `GET /v1/platform/health`, the
  AC's own Platform Health metric, computed live.
- **Bugfix found live** (see decision 9): `main.py`'s `app.state.conformance_store` /
  `app.state.conformance_ruleset_store` split into two distinct attributes;
  `routes_conformance.py`/`routes_g2.py`/`routes_modeller.py` and four test fixtures
  updated to the new ruleset name.
- Verified: 27 unit tests (8 new — `_attempt_once`/`_run_with_retry` behaviour, no
  database needed) and 19 integration tests (8 new — a real slow call really retried
  and really recovering, a real slow call that never recovers really surfacing
  `TIMEOUT`, a real `ADAPTER_ERROR` really retried once and still surfacing
  INCONCLUSIVE, real observation rows for every side-execution, a real zero rate, a
  real tripped alert with a real `by_reason` breakdown, a real window exclusion, and
  the real HTTP route) all pass against real PostgreSQL + Apache AGE.

## Alternatives considered

**Tie the retry only to a timeout, matching §10.2's own literal prose.** Rejected — see
decision 2. The backlog's own AC sentence structure reads as one rule for all four
causes; narrowing it back to timeout-only would silently drop half of what the AC asks
for.

**Retry an adapter's own honest capability decline too, for uniformity.** Rejected —
also decision 2. A longer budget cannot change whether a deployment has a capability;
retrying would waste a call and delay the result for a guaranteed-identical outcome.

**Store `reason_class` as a free-text string rather than a typed enum.** Rejected — the
AC's own wording ("the reason class") and this codebase's own precedent
(`ExecutionOutcome`/`ExecutionStrategy`, both typed enums already) both point the same
way; a typed value is what lets `inconclusive_rate`'s own `by_reason` breakdown group
correctly without string-matching.

**Compute the inconclusive rate all-time, matching `calibration.build_report`'s own
precedent.** Rejected — see decision 7. A calibration curve is a slow-moving fact about
model quality; an inconclusive rate is an operational health signal an alert acts on,
which needs to reflect recent behaviour, not the platform's entire history.

**Build a real Prometheus/OpenTelemetry exporter for this one metric now, ahead of
E12.** Rejected — see decision 8. No metrics-exposition mechanism exists anywhere in
this codebase yet; building one for a single metric, ahead of S12.3.1's own explicit,
later scope, would pre-empt a decision that story is meant to make for every metric at
once, not just this one.

## Open questions for the product owner

- Should the retry-timeout multiplier (invented here as `2.0`) become a real, tunable
  field on `ExecutionCharter` once a platform engineer actually wants to change it, the
  same way `MAX_FILTER_VALUES_PER_FILTER` remains an open question from ADR 0051?
- Should the inconclusive-rate window (invented here as 24 hours) stay fixed, or become
  a query parameter on `GET /v1/platform/health` once S12.3.1/S12.3.2 build the real
  Platform Health screen and a platform engineer wants to compare windows live?
- Once F7.4's own §10.4 sampling exists, does `SAMPLING_SHORTFALL` retry the same way
  the other three reason classes do, or does a stratification failure need a different
  retry shape (e.g. a smaller sample rather than a longer time budget)?
