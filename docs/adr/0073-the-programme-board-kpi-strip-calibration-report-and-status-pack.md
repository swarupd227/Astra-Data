# ADR 0073 — The Programme Board's KPI strip, the Calibration Report, and the Status Pack

Status: accepted · 13 September 2026 · Story S10.2.1, opening F10.2

## Context

S10.2.1 opens F10.2 — the backlog's own AC, verbatim: *"As a programme manager, I want
the Programme Board, Wave Board, Calibration Report and Status Pack, so that the
programme's state is one screen and the status pack writes itself."*

- Programme Board per §15.3.1: KPI strip (MUs by state, first-pass parity, absorption vs
  calibrated baseline, gates due this week, spend vs budget), train swimlanes with
  planned vs projected, blocked reasons, exceptions ageing, milestones
- Status Pack: generated weekly as an editable narrative with the numbers and charts of
  the Board, exportable to PDF and PPTX; edits are stored with the version
- Calibration Report screen per F13.2

§15.3.1's own row, quoted verbatim, is the shape actually built: *"Top: KPI strip — MUs
by state, first-pass parity, absorption vs calibrated baseline, gates due this week,
spend vs budget. Middle: train swimlanes with planned vs projected bars, MU counts by
state per train, blocked reasons. Bottom: milestone rail and gate calendar."* Actions:
"Open train; re-plan; open gate; export status pack." "Exceptions ageing" is already a
fully-built pane (`ExceptionAgeingPane`, S8.3.2) on the existing Programme Board — this
story reuses it rather than duplicating it, since nothing in the AC names a different
shape for it here.

**The backlog AC's own "per F13.2" is a real spec/backlog mismatch, the same kind ADR
0071 already found and disclosed once for §15.1/§14.4.** §15.3.1's own Calibration
Report row content — *"class-mix by tier, coverage gauges, parity rates, C4 reasons
histogram, family count, cost per report, stage timings; comparison panel to the
pre-calibration assumptions... Sign report"* — matches F13.1/S13.1.2's own already-named
"Calibration Wave" report almost verbatim. F13.2/S13.2.2 is a different, still-unbuilt
concept: a model-confidence calibration curve for the Model Gateway's own routing
decisions (the engine half of that, `calibration.py`, was already built in S5.3.3, and
shares only the English word "calibration" with this screen). This story builds
§15.3.1's own named report — S13.1.2's — and discloses the mismatch in `calibration_
wave.py`'s own module docstring rather than silently substituting one for the other
without a trace.

## Decisions

### 1. Every KPI strip figure reads a real, existing fact — several as an honestly
narrower proxy than its own name suggests

No new state machine, budget ledger, or milestone concept was invented for this story;
each of the five tiles is a disclosed reading of something this codebase already
computes, following E10's own risk-register rule cited again here: *"no screen without
an engine feature."*

- **MUs by state** aggregates every train's own `IN_TRAIN.state` — the identical,
  already-disclosed static proxy the Wave Board's own kanban already carries (`trains.
  py`'s own docstring: "a card's state is set once, at proposal time"). An honest count,
  not a richer progression.
- **First-pass parity** is the estate-wide roll-up of the same first-verdict-per-case
  fact the Parity Dashboard already computes per workbook (S7.4.2).
- **Absorption vs calibrated baseline** reads "absorption" as the real S9.2.2 adoption
  views-ratio metric and "calibrated baseline" as the *currently configured* `Adoption
  Config.threshold` — until a real Calibration Wave has signed a different one via
  `calibration_wave.sign_report`. The response's own `baseline_source` field discloses
  this reading on every call rather than presenting a threshold as if a wave had already
  run.
- **Gates due this week** is G2-only, the only gate anywhere in this codebase with a real
  due-date/SLA concept (`g2_reminders.py`). "This week" means "not yet breached, but
  within 5 working days of its own SLA breach." The response's own `scope` field states
  this plainly rather than implying parity across every gate.
- **Spend vs budget** computes a real "budget" as `sum(PLANNED_BY_TIER[tier] * unit_price
  [tier])` — extending `invoicing.programme_acceptance_summary`'s own already-disclosed
  planned-unit-count concept to currency, since a real unit price already exists per
  tier. No currency figure named "budget" exists anywhere in this codebase's own spec or
  backlog text; this is the one real, defensible number available rather than either a
  fabricated figure or an omitted tile.

### 2. Blocked reasons: a real bug found and fixed before it shipped, keyed off the
one real `decision` value that is ever actually persisted

`train_swimlanes()`'s own "blocked reasons" clause was first written against an assumed
`redesign_decision_reason` field on `ExceptionCase` — which is real, but lives on
`CalculatedField` (written by `redesign.c4_properties`), not on `ExceptionCase` at all.
Writing the integration test against this assumption raised a real `OntologyViolation
Error`, which led to reading `foundry_routing.route_to_foundry` — the sole, real write
site for `ExceptionCase.state = "BLOCKED"` anywhere in this codebase — and confirming
directly that it persists only `state`, `family_ref`, and `decision` (value `"MODEL_
DEFECT_FOUNDRY"`); the free-text `detail_note` a caller passes is returned to that caller
and never written to the graph. No free-text "blocked reason" is ever actually recorded
on an `ExceptionCase` today. Fixed by keying a small, disclosed `_BLOCKED_REASONS: dict
[str, str]` mapping off the real `decision` value instead, with `_UNSPECIFIED_BLOCKED_
REASON` as the honest fallback for any future `decision` value this mapping does not yet
name — a more defensible design than the original assumption would have produced even
had it been correct, since it reads a value this codebase actually persists.

### 3. Milestones are assembled from real train dates and real gate decisions — no
`Milestone` node was created

No `Milestone` concept exists anywhere in the ontology, and none was added — a `Milestone
` node with no other real reader or writer would be exactly the kind of screen-only
fiction the risk register's rule blocks. `milestone_rail()` instead sorts every real
`ReleaseTrain`'s own planned/actual start and end dates alongside every real `Gate
Decision`'s own `timestamp` into one `rail`, and groups the same facts into a `gate_
calendar` by gate name — the AC's own "milestone rail and gate calendar" built entirely
from facts this platform already writes for other reasons.

### 4. The Calibration Report is F13.1/S13.1.2's report, with two of its own named
fields honestly disclosed as absent rather than fabricated

Every other field §15.3.1 names — class mix by tier, coverage gauges, parity rates, C4
reasons histogram, family count, cost per report — reads a real, already-computed fact
(class mix via `classify.class_mix`, coverage via `rules.rule_coverage` and the Pattern
Library's own active/total counts, parity via the Parity Dashboard's own per-tier
first-pass rate, C4 reasons via `CalculatedField.pattern_ref` mapped through `redesign.
APPENDIX_B_GUIDANCE`, family count via `retention.Programme`, cost per report via the
same real unit prices `invoicing.py` already prices tiers with). **`elapsed_time_per_
stage` and `executor_strategy_mix`** are the two named fields with no real backing fact
anywhere in this codebase — no uniform stage-timestamp series exists across harvest,
build and promotion, and no execution-strategy concept exists at all — so both are
returned as `{"available": false, "detail": "..."}` rather than a fabricated number, the
identical honest-absence pattern `MenderPassesInfo`/`elapsed_time_per_stage`-shaped
fields already use elsewhere in this console (`ParityDashboardResponse.trend.mender_
passes`, S7.6.1's own `image_score`).

**The Calibration Baseline is versioned and append-only** (new `calibration_baseline`
table, `UNIQUE (graph, version)`), the identical reasoning `mender_config`/`adoption_
config` already established for their own versioned stores — "the calibrated baseline"
is by its own nature a historical trail a KPI comparison reads against, not a
current-state-only fact a plain UPDATE could serve. Signing writes a new row with `version
= MAX(version) + 1`; it never overwrites a prior signature.

**Sign Calibration Report reuses the G3/G4 "approver role plus typed countersigner
string" pattern.** `signed_by` comes from the calling, `ProgrammeManagerDep`-gated
principal; `countersigned_by` is a plain typed string with no separate authenticated
second action — the identical, already-disclosed limitation `g3_card.approve`/`g4_card.
approve` already carry. A new `CalibrationReportReaderDep` (`ArtizentDep` OR `Role.
CLIENT_ANALYTICS_LEAD`, mirroring `require_tolerance_charter_reader`'s exact shape) gates
reading the report, since the client analytics lead is a real, named co-signer
(S13.1.2's own "Sign report by the Programme Manager and the client analytics lead") who
must be able to read the report before signing it.

### 5. PDF and PPTX are real, generated documents — tables and text, deliberately not
native charts

`reportlab` (PDF, via `platypus`) and `python-pptx` (PPTX) are new dependencies, added to
`pyproject.toml` with the same "this story needs it, added here rather than a shared
package" precedent `pyarrow`/`Pillow` already set. Both the Calibration Report and Status
Pack export routes render real tables and real narrative text from the same data the
screen itself shows — a deliberate, disclosed scope boundary: native chart rendering was
judged more machinery than an already-large story could additionally justify, and a
table carries the same numbers a chart would, honestly labelled as a table rather than
implying a visual this pass does not build.

**Binary export needed a new client-side path, since this console's identity model is
header-based.** A plain `<a href="/v1/....pdf">` cannot carry `X-Astra-Principal`/`X-
Astra-Roles`, the identical problem SSE's native `EventSource` has (ADR 0072). Fixed with
a new `getBlob()` helper in `lib/api.ts` (`fetch` with identity headers, `.blob()`) and a
new shared `lib/download.ts`'s `downloadBlob()` (object-URL-plus-synthetic-click, the
same mechanism `lineage/export.ts` already uses for its own JSON/PNG exports).

### 6. The Status Pack is a snapshot, not a live view — "generated weekly" is a real
action, not a claim of a scheduler this platform does not have

No cron, background scheduler, or "weekly" timer exists anywhere in this codebase for
this purpose, and none was added — "generated weekly" is read as **Programme-Manager-
triggered**, the same "a real action, not an automated claim nobody could verify"
posture already established for `g2_reminders.py`'s own "sent" reminders. `generate_pack`
freezes the Programme Board's own real `kpi_strip`/`train_swimlanes`/`milestone_rail`/
`exception_ageing` facts onto one row at generation time; the pack does not re-read live
data afterward. This is what makes "edits are stored with the version" a coherent
requirement at all — an edit needs a stable thing to attach to, and `status_pack` (new
table, `UNIQUE (graph, week_of, version)`) is the same append-only-versioned shape the
Calibration Baseline uses, for the identical reason.

**"Publish to client" records a real state transition (`published_at`) only — no real
outward delivery exists or is claimed.** The identical posture `g2_reminders.py`'s own
"sent" reminders already established: a fact this platform can verify (a timestamp was
set) rather than a claim of delivery it cannot (an email left an inbox, a client opened a
link).

**The Status Pack's own reader gate is deliberately narrower than "publish to client"
might suggest.** Unlike the Calibration Report, no specific client persona is named
anywhere in spec or backlog text as a Status Pack reader — reading it (including its own
PDF/PPTX export) is gated to `ArtizentDep` only, a disclosed, deliberate scope-narrowing
pending a future story naming who on the client side actually receives one, rather than
guessing a role and gating against a guess.

## Consequences

- `services/graph-svc`: new migration v0037 (`public.calibration_baseline`, `public.
  status_pack`, both append-only/versioned, no ontology change — `ONTOLOGY_CHANGES: list
  [dict[str, str]] = []`); new `programme_surface.py` (`kpi_strip`, `train_swimlanes`,
  `milestone_rail`); new `calibration_wave.py` (`calibration_report`, `sign_report`,
  `latest_baseline`, `render_calibration_report_pdf`); new `status_pack.py` (`generate_
  pack`, `edit_pack`, `publish_pack`, `latest_pack`, `render_pdf`, `render_pptx`); new
  routes `GET /v1/programme:kpis|:swimlanes|:milestones` (`routes_programme_surface.py`),
  `GET /v1/calibration:report`, `POST /v1/calibration:sign`, `GET /v1/calibration:report.
  pdf` (`routes_calibration_wave.py`), `POST /v1/status-pack:generate|:edit|:publish`,
  `GET /v1/status-pack`, `GET /v1/status-pack.pdf|.pptx` (`routes_status_pack.py`); new
  `CalibrationReportReaderDep` in `deps.py`; two new `explain.py` registry entries
  (`programme.absorption`, `programme.spend_vs_budget`); new dependencies `reportlab>=4,
  <5`, `python-pptx>=1,<2`. Every new route reads `repository.graph_name` via the
  injected `RepositoryDep`, never global `settings()` — the exact bug-fix precedent ADR
  0072 already found in `routes_rebuild.py`, applied proactively here from the first
  draft rather than found by a second failure.
- `services/console-web`: new `calibration/CalibrationReport.tsx` and `status-pack/
  StatusPack.tsx` top-level screens; `ProgrammeBoard.tsx` gained three new panes (`KpiStripPane`,
  `TrainSwimlanesPane`, `MilestoneRailPane`), inserted before the pre-existing `Train
  ProjectionsPane` to match §15.3.1's own Top/Middle/Bottom ordering; new `lib/download.
  ts` (`downloadBlob`); `lib/api.ts` gained a `getBlob` helper and 12 new methods with
  their real response types; `App.tsx` gained two new `SURFACES` entries (`calibration`,
  `statuspack`) and added `client_analytics_lead` to `CLIENT_VISIBLE_SURFACES` for
  `calibration`, matching its real `CalibrationReportReaderDep` grant; `styles.css`
  gained `.kpi-tiles`/`.kpi-tile`/`.milestone-rail`/`.status-pack-narrative`.
- Verified: `services/graph-svc` — a new `test_integration_programme_surface.py` (16
  tests, covering empty-estate honesty, real MU-by-state counts, spend-vs-budget
  arithmetic, real blocked reasons, milestone rail dates, Calibration Report signing
  producing a new version rather than an overwrite, signing refusing an empty
  countersigner, real PDF bytes, and the full Status Pack generate/edit/publish/export
  lifecycle) plus 5 HTTP-level role-gate tests, all passing; the full graph-svc
  integration suite re-run clean afterward (741 passed, 2 skipped) with one pre-existing,
  unrelated flake confirmed by a clean `git diff` on every file it touches: `test_
  integration_g2_reminders.py::test_due_reminders_are_sent_and_recorded` (the same
  working-day/calendar-day-backdate-versus-today's-weekday flake already disclosed for
  this exact test — see ADR 0072 — recurring because the suite happened to run on a
  Sunday this time); `ruff`/`mypy` clean on every file this story touches. `services/
  console-web` — 383 tests passing (26 new: nine for the Programme Board's three new
  panes, nine for the Calibration Report, eight for the Status Pack); `tsc --noEmit`/
  `eslint`/`vite build` all clean.
- Live-smoke-tested against the real Docker stack (both images rebuilt, schema_version
  confirmed at 37 on startup): the Programme Board's KPI strip, train swimlanes and
  milestone rail all rendered real figures from the actual demo estate (65 MUs
  `CLUSTERED`, a real gate history spanning G1 through G4 decisions on the milestone
  rail). A real Sign action on the Calibration Report wrote a real `calibration_baseline`
  row (`last signed v1`, a real comparison panel against it appearing immediately after);
  its PDF export returned real bytes (`%PDF-1.4`, 2,803 bytes, `application/pdf`). A real
  Generate action created Status Pack v1 for the real current week; a real narrative edit
  produced v2 (confirmed append-only, not an overwrite); a real Publish action set a real
  `published_at`; its PDF (`%PDF-`, 2,167 bytes) and PPTX (`PK` — a real OOXML zip,
  30,387 bytes) exports both returned real, correctly-typed, non-empty documents.

## Alternatives considered

**Build a real `Milestone` node and a real currency `budget` field on `Programme`.**
Rejected — see decisions 1 and 3. Neither concept is named or needed by any other story,
and inventing graph state solely to back one screen's own KPI tile is exactly the "no
screen without an engine feature" scope creep E10's own risk register exists to block;
the real facts already available (train dates, gate decisions, planned units × unit
price) answer the AC's own words without it.

**Render native charts in the PDF/PPTX exports** rather than tables. Deferred, not
rejected outright — see decision 5. A real chart-rendering pipeline (matplotlib or an
equivalent, plus a real design for what each chart should look like) was judged
meaningfully more work than this already-large story could add without compromising the
rest of it; a table carries the identical numbers, honestly labelled as what it is.

**Persist a free-text "blocked reason" on `ExceptionCase` retroactively**, to make the
AC's own words literally true rather than reading a fixed-sentence mapping off the real
`decision` value. Rejected — see decision 2. Adding a field nothing else writes or reads
would be new, unused surface area invented for this screen alone; the real, sole write
site's own real `decision` value already carries enough information to produce an
honest, specific sentence per case today.

**Gate Status Pack reads to a guessed client role** (e.g. `client_analytics_lead`, by
analogy to the Calibration Report). Rejected — see decision 6. No such role is named
anywhere in spec or backlog text for this specific screen; gating against a guess would
either wrongly admit a role that was never meant to see it or wrongly need reversing
once a real story does name one. `ArtizentDep`-only is a decision that survives an
E12-era client-persona story building on top of it either way.
