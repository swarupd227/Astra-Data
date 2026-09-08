# ADR 0059 — §10.6 regression: a second scheduler that re-executes before it re-diffs

Status: accepted · 8 September 2026 · Story S7.7.1, closing F7.7 and E7

## Context

S7.7.1 closes F7.7 (Regression), and with it E7 — the backlog's own AC: *"As a
programme manager, I want parity suites re-run on a schedule after acceptance and on
demand, so that a source change during parallel run is caught before the report owner
notices."*

- Steward schedules re-runs (default: after model publish, weekly during parallel run,
  on SOURCE_DRIFT); a regression FAIL creates an ExceptionCase tagged REGRESSION and
  notifies the report owner
- Regression Monitor screen lists released MUs with last result, schedule and drift
  alerts
- At handover suites and a runner are exported so the client can keep running them

§10.6 itself, verbatim: *"Every parity suite is retained after acceptance. The Steward
re-runs a site's suites on a schedule (default: after every model publish and weekly
during parallel running) and on demand from the Parity Dashboard. A regression FAIL on a
released report raises an ExceptionCase tagged REGRESSION and notifies the report owner;
it does not change the MU's state. At handover the suites are exported with a runner so
the client can keep running them without the platform."*

## Decisions

### 1. "On demand" needed no new code — a Parity Engineer already has both buttons

`POST .../:execute-parity-cases` (S7.3.1) and `POST .../:run-parity` (S7.4.1) already
exist on the Parity Dashboard. This story's own scope is the *scheduled* half only: a
workbook-level `RegressionSchedule`, a `RegressionScheduler` loop, and what happens on a
FAIL.

### 2. A scheduled check re-executes, then re-diffs — it does not call `:run-parity` alone

Confirmed by direct reading: `VerdictsService.run` (`verdicts.py`'s own docstring) diffs
whatever Parquet `expected_ref`/`candidate_ref` a case's *last execution* already
stored — it is `diff.py`'s graph-coupled other half, not a second execution. Diffing the
same stale snapshot on a schedule would report the identical verdict every time and
could never itself notice a source change, defeating the AC's own "so that a source
change... is caught." Each scheduled check therefore calls `CaseExecutionService.
execute` (S7.3.1's own dual execution, the identical call `:execute-parity-cases`
makes) first, and only then `VerdictsService.run` — the same two-step a Parity Engineer
already performs by hand for "on demand." `RegressionSchedule.workspace` (default
`"dev"`) exists because no property anywhere binds a workbook to its own Fabric
workspace — the identical gap `:execute-parity-cases`'s own required `workspace` query
parameter already discloses — so it is asked for once, at scheduling time, rather than
on every tick.

### 3. The scheduler is a second, parallel copy of `HarvestScheduler`'s own shape — not a generalisation of it

`HarvestScheduler` is built around one `Harvester` and one harvest-shaped `Schedule`;
widening an already-shipped, tested class for a second, differently-shaped caller would
risk both. `Cadence` (`harvest/schedule.py`) is genuinely job-agnostic already and is
imported directly, unchanged. Everything else — `RegressionSchedule`,
`RegressionScheduleStore` (`Postgres`/`InMemory`), `RegressionScheduler` — mirrors the
harvest module's own shape (`due()`'s claim-and-advance `FOR UPDATE SKIP LOCKED`
discipline, one run per schedule at a time, `tick`/`run_forever`/`status`) applied to a
workbook instead of a site.

### 4. No `steward.py` module exists — this story is the first to make `"agent:steward"` do something

Confirmed by direct research: `"agent:steward"` was, before this story, a bare
`Principal` string used at exactly one call site (`routes_g2.py`), never backed by a
real service; `report_documentation.py` already disclosed the identical gap for its own
"the Steward drafts documentation" AC and attributed its own agent to `compositor`
instead. `regression.py` attributes every scheduled write to `STEWARD_PRINCIPAL =
"agent:steward"` — the first story to make that principal actually act, not just name
it. Who *configures* a schedule (`POST .../:schedule-regression`) is gated on
`ProgrammeManagerDep` instead, matching the AC's own literal opening persona ("As a
programme manager, I want... schedules re-runs") — a real, disclosed split between
"who sets the policy" and "who the automated runs are attributed to."

### 5. "After every model publish" is a narrow, best-effort hook into `model_lifecycle.promote_family` — never a schedule created out of nowhere

Every schedule in this codebase (harvest, now regression) is created by an explicit
action; a publish that silently enrolled a workbook nobody asked to monitor would be a
surprise, not a convenience. `trigger_after_publish` only *re-bases* an *already-
existing* schedule's own `next_run_at` to now, for whichever of the family's own
workbooks already have one — a workbook with no schedule is untouched.
`promote_family` gained one new, optional `regression_schedule_store` parameter
(`None` by default, so every caller and test that predates this story is unaffected);
when given, the hook runs inside a `try`/`except` that only logs — a promotion this
AC's own six state-machine edges already gate on real deploy success must never be
undone by a scheduling nudge failing.

### 6. "On SOURCE_DRIFT" reads the real event outbox directly — no new pub/sub layer

`EventType.SOURCE_DRIFT` (S1.2.4) already exists; confirmed by direct research, nothing
consumed it before this story — the Harvester only ever calls `mark_for_reproof` and
appends the notice. Each scheduler tick reads the outbox's own latest `SOURCE_DRIFT`
sequence number and compares it against each enabled schedule's own stored
`last_seen_drift_seq` watermark — claimed and advanced with the identical discipline
`due()` already uses for time-based firing, applied to an event stream instead of a
clock. A drift event naming a workbook with no schedule triggers nothing.

### 7. A regression FAIL never touches an MU's state — because no MU state machine exists to touch

§10.6's own literal words ("it does not change the MU's state") describe a constraint
this codebase could not violate even by accident: no Migration Unit graph node or state
machine exists anywhere in it, confirmed a great many times over this epic.
`ExceptionCase.mu_ref` is the workbook id directly — the same anchor `ParityCase.mu_ref`
already uses.

### 8. `REGRESSION` is a new, disclosed `ExceptionCase.class` value — reusing `evidence_ref`, not a new property

§11.1's own failure taxonomy is for a first-pass parity verdict (FILTER_CONTEXT,
NULL_HANDLING, ...); a regression is a second, later fact about a report the platform
already accepted — the identical "different moment" gap `VISUAL_REDESIGN` (S6.2.1) and
`SOURCE_DRIFT` already have. §10.6 names it explicitly ("tagged REGRESSION"). No new
property is added to `ExceptionCase`: the already-declared `evidence_ref` points at a
real stored artefact (kind `regression_evidence`, a small JSON bundle: run id,
pass/fail/inconclusive counts, checked-at) — the identical "`evidence_ref` names a real
artefact" shape the parity evidence bundle (S7.4.1) already established.

### 9. "Notifies the report owner" is honestly a role, not a named person

Confirmed by direct research (a gap this epic already found and disclosed once, for
S7.4.2's own persona): no property anywhere links a `ReportDefinition` to a specific
report-owner principal or email — access is role-based, not per-report ownership. The
new `NotificationChannel`/`LocalNotificationChannel` mirrors `g2_reminders.py`'s own
honest disclosure verbatim: "notified" means "logged, addressed to the role" until a
real outward channel exists, never a claim of delivery nobody could verify.

### 10. "Released" is proxied by `ReportDefinition.deploy_state == "GENERATED"` — the closest real, already-written fact

Confirmed: no MU node, no "released" flag anywhere, and "parallel running" is prose-only
— §10.3-§10.6 never gave this platform a real state machine for it. `deploy_state`
(S6.1.2) already records `"GENERATED"` on a real successful deploy versus
`"DEPLOY_FAILED"` on a real failed one — the disclosed proxy `regression_monitor`'s own
read uses for "released," the same "closest real fact stands in for a state this
codebase has never built" reasoning this epic has already applied repeatedly
(`SOURCE_DRIFT` for source-side drift, `redesign_flag` for a visual redesign).

### 11. Auto-pause after persistent failure — the identical `HarvestScheduler` behaviour, reused verbatim

`MAX_CONSECUTIVE_FAILURES = 5` (the exact constant and reasoning `HarvestScheduler`
already uses): a schedule that fails five checks in a row is disabled with a real,
attributed `paused_reason`, rather than continuing to burn execution against a source
or target that is plainly broken. `consecutive_failures` resets to 0 on the next real
PASS.

### 12. The handover export vendors the real algorithm's source — it does not re-implement it, and does not ship the DB-coupled module it lives in

`diff.py` and (this story's own new) `tolerance_rules.py`/`case_execution_query.py` are
all, confirmed by direct reading of their own imports, pure Python with zero database or
graph coupling. `tolerance_rules.py` and `case_execution_query.py` did not exist before
this story: `tolerance_charter.py`'s own nine rule dataclasses, `ToleranceCharter`
itself and the `compare_*` comparators were interleaved with genuinely DB-coupled code
(the G1 gate, the Postgres store) in the same file, and `case_execution.py`'s own
`build_dax_query`/`to_sdk_filters`/`to_sdk_parameters` sat beside `asyncpg`/`pyarrow`
imports needed only for the rest of dual execution. Both were split into new, standalone
modules — imported by the original module in place of the code they used to contain, so
there is exactly one definition of each, not a second copy that could drift. The export
bundle copies `diff.py`, `tolerance_rules.py` and `case_execution_query.py` verbatim
(never `tolerance_charter.py` itself, which is not needed standalone), plus a new
`run_suite.py` driver, `suite.json` (this workbook's own case definitions, from
`CaseDerivationService.list_cases`, S7.2.1 — definitions, not a frozen row snapshot, so
a client re-running it compares against *now*, not a moment already stale at export
time), `charter.json` (the live Tolerance Charter, `ToleranceCharter.as_dict()`) and a
README. The one mechanical exception: `diff.py`'s own in-package `from .tolerance_rules
import ...` (a relative import, valid only inside the `astra_graph` package) is rewritten
to a plain `from tolerance_rules import ...` when embedded, since the bundle is a flat
folder of scripts, not a package — a one-line, disclosed string substitution on the
import statement itself, never on any comparison or query-construction logic.

### 13. "Without the platform" means without graph-svc and its database — not without any Astra code

The Source/Target adapters `run_suite.py` needs are `astra-adapter-sdk` — already, by
design, an independently pip-installable package (§6's own "a versioned worker image,"
`adapter-sdk/cli.py`'s own `astra-adapter` command). The runner loads them the identical
way that CLI already does: `astra_adapter.registry.load_adapter(name)`. What the
exported bundle never needs is Postgres, Apache AGE, or graph-svc itself.

## Consequences

- New `services/graph-svc/src/astra_graph/regression.py`: `RegressionSchedule`,
  `RegressionScheduleStore` (`Postgres`/`InMemory`), `new_regression_schedule`,
  `open_regression_exception`, `NotificationChannel`/`LocalNotificationChannel`,
  `RegressionScheduler` (`tick`/`run_forever`/`status`, execute-then-diff, drift
  polling, auto-pause), `trigger_after_publish`, `regression_monitor`,
  `RegressionService`.
- New `services/graph-svc/src/astra_graph/regression_export.py`: `build_regression_
  export` (the zip builder), the vendored `run_suite.py` driver and README templates.
- New, standalone `services/graph-svc/src/astra_graph/tolerance_rules.py`: the nine
  charter rule dataclasses, `ToleranceCharter`, `ToleranceCharterVersion`, the
  `compare_*` comparators — split out of `tolerance_charter.py`, which now imports them
  instead of defining them. `diff.py` now imports `ToleranceCharter`/`compare_cell`
  from `tolerance_rules` directly rather than through `tolerance_charter`.
- New, standalone `services/graph-svc/src/astra_graph/case_execution_query.py`:
  `build_dax_query`, `to_sdk_filters`, `to_sdk_parameters` — split out of
  `case_execution.py`, which now imports them instead of defining them.
- New `services/graph-svc/src/astra_graph/api/routes_regression.py`: `POST
  /v1/workbooks/{id}:schedule-regression` (`ProgrammeManagerDep`), `GET
  /v1/regression-monitor` (`ParityDashboardReaderDep`), `POST /v1/workbooks/{id}
  :export-regression-suite` (`ArtizentDep` — the resulting artefact is fetched via the
  existing `GET /v1/artefacts/{id}/content`, not a second content-serving route).
- `model_lifecycle.promote_family` gains one new, optional, best-effort
  `regression_schedule_store` parameter; `routes_modeller.py`'s `promote` route passes
  it through when wired.
- Ontology: `ExceptionCase.class` gains `"REGRESSION"`; `SCHEMA_VERSION` 29 → 30, two
  new declared `SpecDeviation`s, no new properties (evidence_ref reused).
- New migration `v0029_regression_schedule.py`: `public.regression_schedule` (one row
  per workbook, unique per workbook, indexed on due-time and drift-watermark).
- `main.py`: a second, parallel `RegressionScheduler` wired into the app lifespan
  alongside the existing `HarvestScheduler`, gated identically (no source adapter, no
  scheduler).
- Console: `api.ts` gains `RegressionScheduleRecord`, `RegressionMonitorRow`/
  `Response`, `RegressionExportRecord`, `Api.regressionMonitor`/`.scheduleRegression`/
  `.exportRegressionSuite`. New top-level surface `services/console-web/src/
  regression/RegressionMonitor.tsx` — a listing (not a search-by-workbook screen, the
  opposite shape from the Parity Dashboard), with a "Schedule" action hidden for anyone
  but the Programme Manager and an "Export for handover" action hidden for anyone
  outside Artizent.
- Verified: 17 new pure unit tests (`InMemoryRegressionScheduleStore`'s full contract,
  `new_regression_schedule`'s defaults, the notification channel's own disclosure); 20
  new integration tests against real PostgreSQL + Apache AGE (`FOR UPDATE SKIP LOCKED`
  claiming, a real FAIL opening a real `ExceptionCase` with a real readable evidence
  artefact and a real notification, auto-pause after five consecutive failures, a real
  execution error recorded as a FAIL rather than a crash, a real `SOURCE_DRIFT` event
  triggering exactly one immediate check, `trigger_after_publish` nudging only an
  already-scheduled family member, `regression_monitor`'s "released" filter and drift
  alert, a real importable export zip with the de-relativised import confirmed, all
  three routes' own role gate); 9 new console tests (the listing, schedule/export
  actions and their own role gates, read/write failure surfacing); the full existing
  graph-svc suite (1,370 non-integration + the full integration suite) and console-web
  suite both green alongside them; `ruff`/`mypy`/`tsc`/`eslint` all clean;
  `ontology_check.py --spec`/`--generated` and `migration_check.py` all pass.

## Alternatives considered

**Call `:run-parity` alone on a schedule, without re-executing first.** Rejected — see
decision 2. Re-diffing the same stale Parquet snapshot on every scheduled tick would
report the identical verdict forever and could never itself detect a source change,
directly defeating the AC's own stated purpose.

**Generalise `HarvestScheduler` into a job-agnostic scheduler both harvest and
regression share.** Rejected — see decision 3. `HarvestScheduler` is already shipped and
tested around one job shape; widening it for a second, structurally different caller
risks both without a real shared need beyond `Cadence`, which is already reused as-is.

**Auto-create a regression schedule on every model publish, rather than only nudging an
already-existing one.** Rejected — see decision 5. Every schedule in this codebase is
created by an explicit action; silently enrolling a workbook nobody asked to monitor on
its very first publish would be a surprise a Programme Manager did not choose.

**Give `ExceptionCase` a new `run_id`/`fail_count` property pair for a regression case,
rather than reusing `evidence_ref`.** Rejected — see decision 8. `evidence_ref` already
exists and already means "a stored artefact with this case's own evidence" for every
other failure class; a second, REGRESSION-only fact-carrying property would duplicate
that shape for no real benefit.

**Vendor the whole `tolerance_charter.py`/`case_execution.py` files into the export
bundle, accepting that they would fail to import standalone.** Rejected — see decision
12. Both files import `asyncpg`, graph queries, and (for `tolerance_charter.py`) `g2.py`
at module level; a client running the exported bundle has none of those installed. The
correct fix was extracting the genuinely pure code these files already contained into
its own module, not shipping a bundle that cannot actually run.

**Have the export bundle depend on `astra-graph-svc` itself (accepting a Postgres/AGE
dependency) rather than vendoring pure source files.** Rejected — see decision 13. §10.6
is explicit: "without the platform." `astra-graph-svc` as a whole is not designed to run
without its database; vendoring the two genuinely pure modules keeps that promise
literally true.

## Open questions for the product owner

- Now that a Regression Monitor screen and a scheduling action exist, should scheduling
  gain a cadence editor (currently: create with a default weekly cadence, or a
  caller-chosen one via the API; no in-console amend/pause/resume), the next time a
  Programme Manager needs to change one without calling the API directly?
- `STEWARD_PRINCIPAL = "agent:steward"` is now the first principal in this codebase to
  actually perform writes under that name. Should a later story build a real, disclosed
  `steward.py` module (mirroring `harvester.py`'s own shape) once more Steward-attributed
  work exists, rather than leaving each Steward-attributed action to live in whichever
  module owns it?
- The export bundle's `run_suite.py` re-derives the target-side DAX query with an empty
  `table_map` (no `Field -> ModelTable` binding is available outside the platform's own
  graph) — the identical, already-disclosed placeholder `case_execution.build_dax_query`
  itself falls back to when no binding exists. Should a future export instead snapshot
  whichever bindings existed at export time into `suite.json`, so a post-handover query
  can be qualified correctly even after the platform (and its graph) is gone?
