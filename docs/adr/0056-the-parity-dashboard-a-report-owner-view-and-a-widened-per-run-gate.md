# ADR 0056 — The Parity Dashboard: a report-owner view, and a widened per-run gate

Status: accepted · 7 September 2026 · Story S7.4.2, closing F7.4

## Context

S7.4.2 closes F7.4 — the backlog's own final F7.4 story: *"As a report owner, I want a
Parity Dashboard and per-run view in plain language, so that I can see whether my report
is right without reading a diff."*

- Per sheet: cases run, pass, fail, inconclusive, first-pass rate, waived count; failing
  cells shown as a table with expected / candidate / delta and the filter context
- Per MU: pass rate trend across runs and Mender passes
- A single "this report passes the charter" statement with the charter version when all
  cases pass

S7.4.1 (ADR 0055) already writes every `ParityRun`/`Verdict`/`ParityCase` this story
reads from. This story writes nothing new — it is a read-only aggregation and a console
screen over what S7.4.1 already produces.

## Decisions

### 1. This screen builds the backlog's own report-owner ask, not §15.3.5's own fuller "Parity Dashboard (parity engineer default)" row

Direct research against §15.3.5's own table found a *different*, denser row already
named "Parity Dashboard": a KPI strip (first-pass rate, mean passes to pass, inconclusive
rate, waived count, regression status), a heat grid of MUs × sheets coloured by verdict,
a failure-class histogram over time, and a pattern-retirements feed — aimed at the Parity
Engineer as its default persona. This story's own AC asks for something narrower and
different in shape: per-sheet counts, a failing-cells table, a per-MU trend, and the pass
statement, addressed to the report owner in plain language. Built exactly what the
backlog's own AC asks for, and disclosed the divergence in both `parity_dashboard.py`'s
and `ParityDashboard.tsx`'s own docstrings rather than silently narrowing the spec's own
fuller screen or silently building the wrong one. A future story building §15.3.5's own
KPI-strip-and-heat-grid screen would be additive, not a replacement of this one.

### 2. "First-pass rate" is elaborated from its MU-grain spec definition to per-sheet, via each case's own first-ever verdict

§16.6 defines first-pass parity rate only at MU grain: *"MUs whose first ParityRun is
all-PASS ÷ MUs proved."* The AC's own "per sheet" framing has no literal spec definition
at that grain. Applied the identical "first run" concept to what is actually being asked
about here — a sheet's own cases, not the whole MU: each case's own first-ever `Verdict`
(by the `ParityRun` it belongs to, ordered by `started`) is checked for PASS; a sheet's
own first-pass rate is (cases whose first verdict was PASS) ÷ (cases with any verdict at
all). Proven with a genuine two-real-run integration test (a case that fails under a
strict charter, then is re-run under a looser one): the *current* per-sheet count moves
with the latest run, but first-pass rate does not — it keeps remembering the first
outcome, which is the whole point of naming it separately from the plain pass count.

### 3. Waived count is a real, live `GateDecision` query — honestly zero today, not hardcoded

No `ParityCase.waived` property exists anywhere (confirmed by direct inspection of
`ontology/nodes.py`). The real mechanism §4.1.1 already declares is
`GateDecision(decision="WAIVED", subject_ref=<case id>)`; no story has ever written one,
since recording a case waiver is F8.3's own later Exception Desk scope (§11.3). Built the
real query (`_waived_case_ids`) rather than a stub, the same "build the real mechanism,
disclose it is honestly unpopulated today" posture this codebase has already applied to
`Field → ModelTable` `MAPS_TO` six times over. Proven live: the integration suite writes
a real `GateDecision(decision="WAIVED", ...)` directly and confirms it is really counted,
and a real `GateDecision(decision="APPROVED", ...)` on the same case is confirmed *not*
to count.

### 4. "Mender passes" is disclosed absent, not estimated or omitted

Confirmed by direct research (grepped the whole codebase, checked the backlog): the
Mender is a fully specified E8 concept (§8.10; a "Mender pass" is glossary-defined as
"one bounded iteration of classify → fix → re-prove") that no story has built any part
of yet — no `MenderPass`-shaped node, property, or event exists anywhere. Rather than
inventing a placeholder metric or silently dropping the AC's own "Mender passes" bullet,
`aggregate_dashboard`'s own trend always returns `mender_passes: {"available": false,
"detail": "..."}`, and the console states this plainly rather than showing a blank or a
zero that would misleadingly read as "zero passes recorded" instead of "not built yet" —
the identical disclosed-absent posture `InconclusiveReason.SAMPLING_SHORTFALL` (S7.3.2)
already set for a declared-but-never-producible value.

### 5. `parity_dashboard.py` splits into a pure `aggregate_dashboard` and a thin graph-coupled `parity_dashboard`, before any test was written

Applied S7.4.1's own established "pure core, graph-coupled shell" precedent
(`diff.py`/`verdicts.py`, ADR 0055 decision 2) proactively: `aggregate_dashboard(*,
workbook_id, runs, verdicts, cases, sheets, waived_case_ids)` takes every input already
hydrated and awaits nothing; `parity_dashboard()` does the four real reads (`ParityRun`s
by `suite_ref`, `Verdict`s, `ParityCase`s by `mu_ref`, `Worksheet`s, and the waived-case
query) and delegates the actual per-sheet/first-pass-rate/trend logic to the pure
function. This is what let the complex aggregation — first-pass-rate history, per-sheet
grouping, trend ordering — be unit-tested directly against 15 hand-built scenarios with
no Postgres, before the graph-coupled integration suite was written at all.

### 6. Current per-sheet counts come from the *latest* run's own verdicts, not a per-case "most recent verdict across any run" scan

A disclosed assumption, not an oversight: this only produces correct sheet-level counts
because `run_parity_for_workbook` (S7.4.1) always diffs every executed case for the whole
workbook, never a subset — so the latest run's own verdict set already *is* "each case's
current verdict." A future story that lets a Parity Engineer re-run a narrower subset of
cases would need to revisit this assumption (a case a narrower re-run skipped would then
wrongly disappear from "current" counts, rather than correctly keeping its own last real
verdict). Documented in `parity_dashboard.py`'s own module docstring rather than left as
an implicit dependency on S7.4.1's own current, but not permanently guaranteed, behaviour.

### 7. `GET .../parity-run` is widened to the same gate as the new dashboard route, not duplicated behind a second endpoint

S7.4.1 gated the per-run read (`GET .../parity-run`) `ArtizentDep`-only — correct for
that story's own persona (the Parity Engineer reading back a run it just triggered), but
a real, now-outgrown restriction: this story's own report-owner persona needs the per-run
view too (§15.3.5's own "Parity Run" row is the identical shape a report owner's
per-run drill-down needs). Rather than building a second endpoint returning the same
shape under a different path, widened the existing route's own dependency to
`ParityDashboardReaderDep` — the identical "any Artizent role, or the client role this
screen is actually for" template `require_c4_redesign_reader`/
`require_tolerance_charter_reader` already set, applied here as
`require_parity_dashboard_reader` (`is_artizent() or CLIENT_REPORT_OWNER in roles`).
Proven live over HTTP: a `client_data_owner` (a real client role, deliberately *not* the
report owner) is refused 403 on both routes, while `client_report_owner` gets 200 on
both — confirming the widened gate is role-specific, not "any client role."

## Consequences

- New `services/graph-svc/src/astra_graph/parity_dashboard.py`: `aggregate_dashboard`
  (pure), `parity_dashboard` (graph-coupled), `_all_parity_runs_for_workbook`,
  `_waived_case_ids`.
- `verdicts.py`: `VerdictsService.dashboard(workbook_id)` — a thin binding onto
  `parity_dashboard.py`.
- `deps.py`: `require_parity_dashboard_reader`/`ParityDashboardReaderDep`.
- `routes_verdicts.py`: new `GET /v1/workbooks/{id}/parity-dashboard`
  (`ParityDashboardReaderDep`); existing `GET /v1/workbooks/{id}/parity-run` widened
  from `ArtizentDep` to `ParityDashboardReaderDep`.
- New console screen `services/console-web/src/parity/ParityDashboard.tsx` — its own
  top-level surface (`SURFACES`/`App.tsx`, the identical reasoning the Tolerance Charter
  and Pattern Library screens already established for not being an Admin sub-screen),
  reading `parityDashboard`/`parityRun` and offering `runParity` (re-run), hidden-with-
  explanation for anyone but the Parity Engineer.
- `api.ts`: `FailingCellRow`, `SheetParityStats`, `ParityRunTrendEntry`,
  `MenderPassesInfo`, `ParityDashboardResponse`, `VerdictRow`, `ParityRunResponse`,
  `RunParityResult`; `Api.parityDashboard`/`.parityRun`/`.runParity`.
- No ontology or migration change — every property read here (`ParityRun.suite_ref`,
  `Verdict.case_ref`/`.result`/`.failing_cells`, `ParityCase.sheet_ref`/`.filter_ctx`,
  `GateDecision.decision`/`.subject_ref`, `Worksheet.name`) was already declared before
  this story; verified via `ontology_check.py --spec`/`migration_check.py` both passing
  unchanged (28 node types, schema version 27).
- Verified: 15 unit tests against `aggregate_dashboard` (first-pass-rate history,
  per-sheet grouping, trend ordering, waived independence, Mender-absence disclosure);
  10 integration tests against real PostgreSQL + Apache AGE (a real single run, a real
  two-run trend under two different charters, a real waived `GateDecision`, a real
  non-waived one, and both HTTP routes' widened role gate — `client_report_owner` gets
  200, `client_data_owner` gets 403, a clean 404 before any run); 11 console tests
  (reading, drilling into a sheet's own failing cells, the trend, the Mender disclosure,
  a read failure, re-running, and Re-run's own role gate); the full existing graph-svc
  suite (1,851 passed, the one already-known `test_integration_g2_reminders.py` flake
  unaffected) and console-web suite (218 passed, one already-known unrelated
  `EstateExplorer` timing flake under full-suite parallel load, confirmed passing 26/26
  in isolation) both green alongside them; `ruff`/`mypy`/`tsc`/`eslint` all clean.

## Alternatives considered

**Build §15.3.5's own fuller "Parity Dashboard (parity engineer default)" screen — KPI
strip, heat grid, failure-class histogram, pattern-retirements feed — instead of the
backlog's own narrower report-owner ask.** Rejected — see decision 1. The backlog's own
AC is explicit about audience ("As a report owner... in plain language") and shape
(per-sheet counts, a failing-cells table, a trend, the pass statement); building the
denser technical screen instead would satisfy the spec's own table row but not this
story's own stated persona and acceptance criteria.

**Leave "first-pass rate" undefined per sheet, or silently reuse the current-pass count
in its place.** Rejected — see decision 2. The AC names both `pass` and `first-pass
rate` as distinct columns; collapsing them would make the metric structurally unable to
show what it exists to show — that a case which recovered on a later run still failed
the first time.

**Hardcode waived count to 0 rather than querying `GateDecision`.** Rejected — see
decision 3. The real mechanism already exists in the ontology; querying it for real (even
though today it always returns empty) means the column becomes correct the moment F8.3
ever writes a waiver, with no further code change here.

**Omit the "Mender passes" field entirely, or show a fabricated placeholder value.**
Rejected — see decision 4. The AC names it explicitly; omitting it would silently narrow
the AC, and a fabricated number would misrepresent a real, unbuilt capability as if it
had already run.

**Keep the aggregation as one graph-coupled function, tested only at integration
level.** Rejected — see decision 5. Splitting out a pure core is what let the trickiest
logic (first-pass-rate history, trend ordering) be verified directly against small,
exact scenarios, the same value S7.4.1's own `diff.py` split already delivered.

**Build a second `GET .../parity-dashboard`-shaped endpoint for the per-run view instead
of widening the existing `GET .../parity-run`.** Rejected — see decision 7. The existing
route already returns exactly the shape a per-run view needs; the only real gap was its
gate being narrower than this story's own new persona needs, so widening the gate is the
smaller, more honest change than standing up a duplicate route.

## Open questions for the product owner

- Should §15.3.5's own fuller "Parity Dashboard (parity engineer default)" screen — the
  KPI strip, heat grid, failure-class histogram, and pattern-retirements feed — become
  its own later story, additive to this one, once F7.4 work resumes or a Parity Engineer
  names a concrete need for it?
- Now that a real Mender-passes gap is visibly disclosed on every workbook's own
  dashboard, does that change E8's own priority relative to the backlog's other
  remaining epics?
- Should a Parity Engineer be able to waive a specific case directly from this screen
  (writing a real `GateDecision`) once F8.3's Exception Desk exists, rather than waiving
  remaining a write path with no UI anywhere yet?
