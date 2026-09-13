# ADR 0069 — Adoption tracking: a views ratio, not "adoption sessions held"

Status: accepted · 13 September 2026 · Story S9.2.2, continuing F9.2

## Context

S9.2.2 continues F9.2 (Promotion and parallel run) — the backlog's own AC, verbatim:
*"As a report owner, I want adoption of the released report tracked against the source
during parallel run, so that we know users have moved before the source is switched
off."*

- Views on the Power BI report (Fabric activity) and the Tableau view (Metadata API)
  are both captured weekly; the ratio is shown on the Decommission Tracker
- Configurable adoption threshold contributes to G4 readiness

§14.4 itself, verbatim: *"...tracks decommission readiness: all MUs released,
regression green, adoption sessions held, owner confirmation received."* §15.3.4's own
Decommission Tracker row (confirmed a distinct screen from the Release Board by direct
read of both rows in the same table): *"Per site: readiness checklist, MUs released,
regression green, owner confirmations, licence value; G4 card when ready."*

Confirmed by direct research before writing anything: `TargetAdapter`'s whole contract
(`manifest`/`commit`/`deploy`/`smoke_query`/`evaluate`/`render_visual`) had no
activity/usage method; `SourceAdapter.usage()` already exists (S1.2.3) but the real
`TableauAdapter` still declares `usage=False` — its own Metadata API integration was
never built; no adoption-threshold config or snapshot table exists anywhere; and
`Role.CLIENT_LICENCE_ADMIN` has been declared in `roles.py` since S1.1.1 but gated
nowhere until this story.

## Decisions

### 1. "Adoption sessions held" and this story's own views-ratio metric are different notions of "adoption" — this story satisfies only the AC's own

§14.4's prose names a session-attendance fact this codebase has no mechanism to record
today (the Release Board's own "schedule adoption session" action records nothing
either — confirmed by direct read). The backlog's own AC introduces a more concrete,
measurable definition: source views vs. target views, captured weekly. This story
builds exactly that, and nothing here claims to also satisfy the session-attendance
reading — that stays real, disclosed, separate future work.

### 2. Source-side views reuse `SourceAdapter.usage()` verbatim, capability-gated; target-side views are a genuinely new `TargetAdapter.usage()` method

No new source contract was needed — `usage()` already exists, gated by `Capabilities.
usage` exactly the way `harvest/runner.py`'s own `_context` already gates it, so a real
deployment against the real `TableauAdapter` (which still declares `usage=False`)
honestly records `source_views=None` rather than fabricating a number. The target side
had no equivalent at all: `ActivityResult`/`TargetAdapter.usage()` is new
(`target_contract.py`, `TARGET_INTERFACE_VERSION` bumped 1.2 → 1.3), following the exact
S7.3.1 (`evaluate`, → 1.1) / S7.6.1 (`render_visual`, → 1.2) precedent for widening this
contract. `FixtureTargetAdapter.usage()` checks something real first — whether
`item_path` was ever actually deployed to `workspace` — then returns deterministic,
disclosed synthetic data, the identical posture `smoke_query`/`evaluate` already set.

### 3. "Released" reuses the identical real signal `release.py` already established — a small, duplicated query, not a new cross-module export

A workbook counts as released for this story the same way `release.py` already treats
prod: a `SUCCEEDED` `promotion_run` row for `to_stage="prod"`. Queried directly in
`adoption.py` via a small `SELECT DISTINCT workbook_id`, matching this codebase's own
tolerance for a short, self-contained read over adding a new export from `release.py`
for one query. `_sites_for_workbooks`/`PROMOTION_TABLE` are imported for cross-epic
private reuse instead, the identical shape `g3_card.py` and `release.py` already use for
`foundry_routing._family_for_workbook`.

### 4. No per-workbook schedule, unlike `harvest`'s or `regression.py`'s own — deliberately

Both existing schedulers are opt-in, per-workbook, and materially expensive (a
regression re-run re-executes real parity DAX; a harvest re-parses a whole workbook),
so each earns its own enable/pause/retry state machine. Adoption capture is the
opposite: a uniform, cheap sweep across *every* currently released workbook in one
pass, nothing to opt into or pause per-workbook. `is_capture_due` checks one graph-wide
fact — how long since `adoption_snapshot`'s own most recent row (`CAPTURE_WINDOW_DAYS =
7`, the AC's own literal "captured weekly") — rather than maintaining a second schedule
table whose only content would be "capture everything, weekly" duplicated per workbook.

### 5. The configured threshold is a real, versioned store, mirroring `mender.MenderConfig` — not a bare constant

The AC's own literal word "configurable" is what earns a real, persisted store here
(`AdoptionConfig`/`PostgresAdoptionConfigStore`, append-only, `version = MAX(version)+1`)
— unlike `g3_card.DEFAULT_PARALLEL_WINDOW_WEEKS`, which stays a bare constant because
nothing in its own AC ever asked to change it. `DEFAULT_ADOPTION_THRESHOLD = 0.8` is an
invented, disclosed default, the identical footing `mender.DEFAULT_PASS_BUDGET`/
`invoicing.DEFAULT_UNIT_PRICES` already have.

### 6. A snapshot freezes its own threshold and verdict at capture time — never recomputed later

`AdoptionSnapshot.threshold`/`meets_threshold` are stored on the row itself. Changing
the configured threshold afterward must never retroactively change whether a past
week's own capture "met" it — verified directly (an integration test changes the
config after a capture and confirms the stored row is untouched).

### 7. `_adoption_ratio` is a pure function, extracted specifically for unit testability

`capture_adoption_sweep` is graph-and-adapter-coupled throughout and is covered end to
end only by the integration suite (the same "pure core, graph-coupled shell" split this
epic's prior stories already established) — but the ratio/threshold arithmetic itself
(honestly `(None, None)` with no real source-side denominator, never a fabricated
ratio) is pulled out as `_adoption_ratio(*, source_views, target_views, threshold)` so
it has its own direct, database-free unit tests.

### 8. The Decommission Tracker is its own top-level screen, gated to a real, previously-undriven persona

Confirmed by direct read of both §15.3.4 rows: the Release Board (platform
engineer/PM-facing, pipeline stage and the parallel-run window) and the Decommission
Tracker (client licence administrator-facing, per-MU real adoption against the
threshold) are two distinct screens, not one screen's own tab. `require_
decommission_tracker_reader` mirrors `require_g3_card_reader`'s own "reader is broader
than the persona who acts on it" shape: any Artizent role, the report owner, or
`Role.CLIENT_LICENCE_ADMIN` — declared in `roles.py` since S1.1.1, gated nowhere until
now. Setting the threshold is the Migration Architect's (the identical "owns
configurable platform policy" posture the conformance and visual-mapping rulesets
already use); triggering a capture on demand is the Programme Manager's, matching
`RegressionMonitor.tsx`'s own "Schedule" action.

### 9. G4 readiness itself is out of scope — only the real, per-MU `meets_threshold` fact is computed

The AC's own words are "contributes to," not "computes," G4 readiness. `adoption.py`
exposes `meets_threshold` per MU and a per-site `meeting_threshold_count` rollup; it
does not attempt a full readiness computation (regression status, owner confirmation,
an actual `GateDecision(gate="G4")`), since neither regression-execution-as-a-real-fact
nor an owner-confirmation mechanism exists anywhere in this codebase yet — the identical
gap `release.py`'s own docstring already disclosed for F9.2's later scope.

## Consequences

- `packages/adapter-sdk`: `ActivityResult` (new dataclass), `TargetAdapter.usage()`
  (new Protocol method), `TARGET_INTERFACE_VERSION` bumped to `"1.3"`, `FixtureTarget
  Adapter.usage()`/`_usage_sync` implementing the "check something real, then disclosed
  synthetic data" posture.
- `services/graph-svc/src/astra_graph/events.py`: `EventType.ADOPTION_CAPTURED`
  (`estate.adoption.captured`), a notice on the identical footing `MU_ACCEPTED`/
  `MU_PROMOTED`/`SOURCE_DRIFT`/`PATTERN_RETIRED` already have (now five members in the
  `mutates_graph` exclusion tuple).
- New migration `v0035_adoption.py`: `public.adoption_config` (versioned, mirrors
  `mender_config`) and `public.adoption_snapshot` (history; `source_views` nullable for
  honest capability-absence; `threshold`/`meets_threshold` frozen per row) — both plain
  Postgres platform tables, not ontology nodes (no ontology change, schema version
  bumped to 36 for the tables alone).
- New `services/graph-svc/src/astra_graph/adoption.py`: `AdoptionConfig`/`AdoptionConfig
  Store`/`PostgresAdoptionConfigStore`/`InMemoryAdoptionConfigStore`, `AdoptionSnapshot`/
  `AdoptionStore`/`PostgresAdoptionStore`, `is_capture_due`, `_adoption_ratio`,
  `capture_adoption_sweep`, `decommission_tracker`, `AdoptionService` (the "pre-bound
  object on app.state" shape `ReleaseService` already takes).
- `api/deps.py`: `require_decommission_tracker_reader`/`DecommissionTrackerReaderDep`.
- New `services/graph-svc/src/astra_graph/api/routes_adoption.py`: `GET /v1/
  decommission:tracker` (any reader role), `GET`/`POST /v1/adoption:config` (read: any
  reader role; write: Migration Architect), `POST /v1/adoption:capture` (Programme
  Manager).
- `main.py`: `app.state.adoption_store`, `app.state.adoption_config_store`,
  `app.state.adoption = AdoptionService(...)`, wired after `app.state.source_adapter`/
  `app.state.target_adapter` are already set.
- New console-web top-level surface, `DecommissionTracker.tsx` (§15.3.4's own other
  named Delivery-surface row) — per-site MU tables (source/target views, ratio, capture
  date, a meets/below-threshold pill honestly showing "not yet captured" for a released
  MU with no snapshot yet), a threshold-setting panel gated to the Migration Architect,
  and a "Capture now" action gated to the Programme Manager; a new "Client Licence
  Administrator" role option added to the console's own role selector (the screen's own
  real persona, never previously selectable). `styles.css`: `.decommission-tracker`
  added to the existing single-column `.workspace` override list — found and fixed at
  the source before it ever reached the demo estate, the identical bug ADR 0066/0068
  already found and fixed for five earlier screens.
- Verified: 16 new pure unit tests (`_adoption_ratio`, `is_capture_due`'s own date math
  against a minimal fake store, both result dataclasses' round-trips, the in-memory
  config store's own threshold validation); 4 new adapter-sdk unit tests for `Fixture
  TargetAdapter.usage()` (honest zero for an undeployed item, real views for a deployed
  one, deterministic for the same inputs, differing by window); 8 new integration tests
  against real PostgreSQL + Apache AGE and the real fixture source/target adapters (a
  real ratio for a released workbook with a known source-views count and a real
  target-side view count; a workbook that was never promoted to prod is never touched;
  honest `None`s with the source usage capability absent while target views stay real;
  the sweep uses whatever threshold is currently configured, frozen on the row even
  after the config later changes; `is_capture_due` honestly true with nothing ever
  captured and false right after a real capture; the Decommission Tracker grouping a
  released MU by its real site with a real snapshot, and honestly empty with nothing
  released yet); 11 new console tests (real per-MU views/ratio, the honest
  "unavailable"/"not yet captured" states, an honest empty state, a read failure,
  setting the threshold gated to the Migration Architect with real client-side range
  validation, capturing on demand gated to the Programme Manager, both hidden for
  neither role, a real API refusal surfaced for each). The full existing graph-svc
  suite (1,484 passed non-integration; 696 passed, 2 skipped, one already-known,
  unrelated `test_integration_cartographer.py` failure in the full integration run --
  confirmed a background-task/pool-teardown race under sustained load by a clean pass
  in isolation, and confirmed unrelated to this story by a clean `git diff` on that
  test's own files; adapter-sdk's own 124 tests and `test_events_and_retirement.py`'s
  5-notice update both confirmed passing) and console-web suite (295 passed) both green
  alongside them; `ruff`/`mypy`/`tsc --noEmit`/`eslint` clean.
- Live-smoke-tested against the real Docker stack: `GET /v1/decommission:tracker`,
  `GET`/`POST /v1/adoption:config`, and `POST /v1/adoption:capture` all live and
  role-gated correctly (a wrong-role attempt refused with a real 403, an unauthorised
  read refused and surfaced honestly in the console's own error banner); triggered a
  real capture against an already prod-promoted demo workbook, confirmed a real
  `adoption_snapshot` row and two real `estate.adoption.captured` events on the outbox
  (the first honestly `target_views=0` before a fresh report deploy under the recreated
  container's own ephemeral fixture-adapter storage, the second real and non-zero once
  redeployed); confirmed the console's Decommission Tracker screen renders the real
  ratio and threshold, and that setting a new threshold through the UI round-trips to a
  real, persisted `adoption_config` row and back.
- **A Docker/infrastructure note, not a product bug**: `FixtureTargetAdapter`'s local
  git repo and per-workspace deployed-file directories live under the container's own
  `/tmp`, which does not survive a container recreate, while `promotion_run`/`report_
  deploy_run` rows in Postgres (a named volume) do — after an unrelated Docker restart
  recreated every `astra-data-*` container mid-session, the demo estate's one
  previously-promoted workbook still showed as `SUCCEEDED` in Postgres but had nothing
  real left deployed. Re-promoting through the same live routes deploys fresh files
  under the current container's own `/tmp` immediately, exactly the same reset any real
  Fabric workspace credential rotation would force — not something this story's own
  code needs to handle differently.

## Alternatives considered

**Build a real "adoption session" scheduling/attendance mechanism to satisfy §14.4's
own prose directly.** Rejected — see decision 1. The backlog's own AC for this story
asks for a views ratio, a different, more concrete metric; inventing session tracking
nobody asked for in this story would be scope well beyond the AC.

**Extend `harvest.py`'s or `regression.py`'s own per-workbook `Cadence`/`Schedule`/
`Scheduler` machinery for the weekly capture.** Rejected — see decision 4. Both
existing schedulers exist because their own work is opt-in, per-workbook, and
expensive; a uniform, cheap, blanket sweep across every released workbook has nothing
per-workbook to schedule.

**Keep the adoption threshold a bare module constant, matching `g3_card.DEFAULT_
PARALLEL_WINDOW_WEEKS`.** Rejected — see decision 5. The AC's own literal word
"configurable" is a real requirement a constant cannot satisfy.

**Recompute `meets_threshold` from the current config whenever a snapshot is read.**
Rejected — see decision 6. A later change to the threshold must never rewrite what a
past week's own capture actually showed.

**Fold the Decommission Tracker into the Release Board as a second tab.** Rejected —
see decision 8. §15.3.4 lists them as two distinct rows with two distinct personas and
two distinct axes (pipeline stage vs. per-MU adoption); conflating them would blur a
gate this story's own role research shows is real and previously ungated.

## Open question for the product owner

- Should a future story's own G4 readiness computation treat `meets_threshold: null`
  (no capture has run yet for a released MU) the same as `false` (captured, but below
  threshold) when deciding whether a site is ready to raise a G4 request — or must
  every released MU have at least one real capture before G4 readiness can be assessed
  at all?
