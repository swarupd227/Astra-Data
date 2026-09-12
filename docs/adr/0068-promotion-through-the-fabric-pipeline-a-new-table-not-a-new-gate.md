# ADR 0068 — Promotion through the Fabric pipeline: a new table, not a new gate

Status: accepted · 12 September 2026 · Story S9.2.1, opening F9.2

## Context

S9.2.1 opens F9.2 (Promotion and parallel run) — the backlog's own AC, verbatim: *"As a
platform engineer, I want the Steward to promote ACCEPTED MUs per train through the
Fabric deployment pipeline, so that release is a pipeline stage with evidence, not a
manual copy."*

- Promotion dev → test → prod via Fabric deployment pipelines with the client's
  approval rules; MA-08 (L3) to test, MA-09 (L2, explicit PM approval) to production
- Release Board shows per train: MUs by pipeline stage, blockers, and the release
  evidence bundle
- Parallel-run window (default 4 weeks) starts at production deployment and is
  visible per MU and per site

§14.4 itself, verbatim: *"The Steward promotes ACCEPTED MUs per train through the
deployment pipeline, opens a parallel-running window per site (default 4 weeks,
configurable)..."* §7.2: *"Deployment uses Fabric deployment pipelines; the Steward
promotes a report and its model together..."* §13.2's own MA-08/MA-09 rows, verbatim:
*"MA-08 Promote to test workspace | L3 | Post-G3 only"* / *"MA-09 Promote to
production | L2 | Explicit release approval by PM."*

Confirmed by direct research before writing anything: this codebase had never
configured a third ("test") workspace (only `target_workspace`/`target_workspace_
published`, `config.py`'s own docstring already flagging real dev/test/prod
configuration as "E9/E12's" to build); `report_deploy.deploy_report` and
`routes_modeller.promote`'s own model-deploy pattern were both already workspace-generic
and directly reusable; no `steward.py` module exists anywhere (`"agent:steward"` is a
bare `Principal` string `regression.py` already declared and drives for unattended
writes only); no `GateDecision.gate` value beyond the spec's own four gates (`G1`-`G4`,
none of them "release" or "promotion") exists; and no Release Board screen exists in
console-web at all.

## Decisions

### 1. "The Steward" here is an attributed human action, not the automated `agent:steward` principal

Every prior Steward-shaped write in this codebase (`regression.py`'s scheduled
re-runs, `build.py`'s post-G2-approval auto-build) is unattended. Promoting to test or
to production is the opposite: a real platform engineer or programme manager performs
it, the identical "the real human's own principal, not a borrowed identity" posture
`g3_card.approve` already takes for the report owner. `release.py` records the request's
own real principal throughout; "the Steward" names the mechanism, not an identity this
story invents.

### 2. Neither MA-08 nor MA-09 gets a new `GateDecision.gate` value — `public.promotion_run` instead

Confirmed by direct read of `ontology/nodes.py`: the four legal gates are Charter/
Model/Parity/Decommission (§13.1's own four-row table) — there has never been a
numbered release/promotion gate anywhere in the spec, unlike `decision`'s own
four-value widening S8.3.1 already did for a real Exception Desk need. `promotion_run`
(a new, plain Postgres platform table, the identical footing `build_run`/`report_
deploy_run`/`commercial_ledger` already have) carries `approved_by`/`approver_role`/
`rationale` directly, keyed `UNIQUE (graph, workbook_id, to_stage)` so each workbook's
own current attempt at a given stage is a single, current row a later attempt
overwrites — not a `GateDecision`.

### 3. "ACCEPTED" is checked against `GateDecision(gate="G3", decision="APPROVED")`, not the commercial ledger

S9.1.2's own `commercial_ledger` row is a real but *conditional* side effect of G3
approval — `invoicing.record_acceptance` honestly writes no row for a workbook that has
never been re-tiered. The MU's own §3.2 state name, "ACCEPTED," is set by G3 approval
itself (confirmed against the MU state table: *"ACCEPTED ... Client report owner
approves G3"*) — checking the ledger instead would wrongly block a genuinely
G3-approved but untiered workbook from ever reaching test.

### 4. Promoting the report and the model are two separate, already-proven deploy calls — no new commit/deploy code

The report half is `report_deploy.deploy_report(..., workspace=<stage>)` unchanged: it
already commits and deploys a fresh PBIR bundle to whatever workspace name it is given
(S6.1.2's own workspace-generic contract). The model half copies `routes_modeller.
promote`'s own exact pattern (S4.3.3): find the workbook's family
(`foundry_routing._family_for_workbook`, cross-epic private reuse, the identical helper
`g3_card.py` already imports the same way), read that family's latest *successful*
build (`BuildStore.latest`), and redeploy that build's own already-committed `git_ref`
to the new workspace via `TargetAdapter.deploy` directly — a pipeline promotes the
*same, already-proved* artefact across stages, it does not rebuild at every stage.
Neither `model_lifecycle.promote_family`'s own `BUILT -> PUBLISHED` flip nor
`ModelFamily.state` is touched: that state machine is the *family's* own, and multiple
workbooks can share one family — this story's own promotion is scoped to one
workbook's own MU, a distinct, additive fact.

### 5. `promotion_run` IS the release evidence bundle — no second, duplicated snapshot via `ArtefactStore`

Unlike a rendered G3 card (a *view*, needing its own frozen snapshot via `ArtefactStore`
per ADR 0066), a `promotion_run` row already is the queryable record of what happened
(steps, workspace, git ref, approver) — the identical "the row IS the evidence" footing
`build_run`/`report_deploy_run` already have. The Release Board reads it directly.

### 6. The parallel-run window is computed, never stored, on both axes the AC names

Per MU: `promotion_run`'s own `finished_at` for a SUCCEEDED `to_stage="prod"` row, plus
`g3_card.DEFAULT_PARALLEL_WINDOW_WEEKS` (4, reused verbatim — the identical number, not
a second, possibly-drifting one). Per site: the *earliest* prod promotion among that
site's own workbooks — a real, disclosed reading choice, since §14.4 never states
whether a site's window is keyed to its first or its last released MU. No new `Site`
property is added (spec's own §21 `site_record.parallel_run_start` names a stored
field) — deliberately: storing a second, derived copy on `Site` risks disagreeing with
`promotion_run` the moment a promotion is corrected or backfilled, the identical "never
trust a stored counter over the live rows" posture `Pattern.failure_count` already
established (S5.5.2).

### 7. Blockers are real, computed facts, checked twice for two different reasons

`promotion_blockers` is called once to *refuse* an actual promotion attempt
(`promote_workbook` raises `InvalidRequestError` naming every blocker it finds, never
attempting a partial or guessed deploy), and again, read-only, to *show* the Release
Board why a given MU cannot yet advance — the identical fact, read the identical way,
so the board can never claim readiness the action itself would refuse.

### 8. Scope deliberately stops at promotion and the parallel-run window

§14.4's fuller sentence — *"tracks decommission readiness: all MUs released,
regression green, adoption sessions held, owner confirmation received... the G4
request opens automatically"* — names real, separate future work (F9.2's own later
story). The Release Board built here shows exactly the AC's own three nouns — pipeline
stage, blockers, evidence bundle — plus the parallel-run window, not a full readiness
checklist or G4.

## Consequences

- `config.py`: new `target_workspace_test` (default `"test"`, env `ASTRA_TARGET_
  WORKSPACE_TEST`) — the platform's first real three-stage (dev/test/prod) pipeline
  configuration.
- New `services/graph-svc/src/astra_graph/release.py`: `PromotionRecord`,
  `PromotionStep`, `PromotionStore`/`PostgresPromotionStore`, `promotion_blockers`,
  `promote_workbook`, `release_board`, `ReleaseService` (the "pre-bound object on
  app.state" shape `G3CardService` already takes).
- `events.py`: `EventType.MU_PROMOTED` (`estate.mu.promoted`), a notice on the identical
  footing `MU_ACCEPTED`/`SOURCE_DRIFT`/`PATTERN_RETIRED` already have.
- New migration `v0034_promotion_run.py`: `public.promotion_run` (a plain Postgres
  platform table, not an ontology node — no ontology change, schema version unchanged).
- New `services/graph-svc/src/astra_graph/api/routes_release.py`: `GET
  /v1/release:board`, `GET /v1/workbooks/{id}:promotion-blockers`, `POST
  /v1/workbooks/{id}:promote-to-test` (`PlatformEngineerDep`, MA-08), `POST
  /v1/workbooks/{id}:promote-to-prod` (`ProgrammeManagerDep`, MA-09, required
  rationale).
- `main.py`: `app.state.promotion_store`, `app.state.release = ReleaseService(...)`.
- New console-web top-level surface, `ReleaseBoard.tsx` (§15.3.4's own named Delivery
  screen) — per-train MU tables (stage pill, blocker count, evidence count, a
  role-gated Promote action per row) and a per-site parallel-run window panel;
  promoting to prod reuses the shared `ReasonDialog` for its own required rationale.
- Verified: 10 new pure unit tests (`_current_stage`, `_window_end`, both result
  dataclasses' round-trips); 14 new integration tests against real PostgreSQL +
  Apache AGE and a real local Git repository (MA-08 promoting a real accepted
  workbook, refusing an unapproved/uncomposed/unbuilt one, overwriting its own single
  current row on a second promotion; MA-09 promoting a tested workbook with a real
  rationale, refusing one that skipped test, refusing a blank rationale;
  `promotion_blockers` listing every real reason and being honestly empty once every
  precondition is real; the Release Board grouping real trains with real pipeline
  stages, showing real blockers for a not-yet-accepted MU, computing a real per-site
  window, and honestly showing nothing for an untouched site); 12 new console tests
  (real pipeline stages and blocker counts, the per-site window panel, an honest empty
  state, a read failure, both promotion actions gated to their own role with a real
  API refusal surfaced, a disabled action while a real blocker remains); the full
  existing graph-svc suite (1,468 passed in the non-integration run; the S9.2.1
  integration file's own 14 tests confirmed passing) and console-web suite (283
  passed, one unrelated, confirmed-flaky `quality.test.tsx` test that passes cleanly
  in isolation) both green alongside them; `ruff`/`mypy`/`tsc --noEmit`/`eslint` clean.
- Live-smoke-tested against the real Docker stack: re-tiered and G3-approved a real
  demo workbook via the API, promoted it to test then to production through the new
  routes, confirmed a real `invoiced`/`model_git_ref`/`workspace` outcome at each step,
  and confirmed the Release Board's own real pipeline-stage and parallel-run-window
  panels update accordingly in the console.
- **A real, pre-existing bug found live: two non-retired `IN_FAMILY` edges for the same
  workbook**, from an earlier story's own re-clustering pass never retiring the first
  edge. `foundry_routing._family_for_workbook`'s own `LIMIT 1` query (no `ORDER BY`)
  returns whichever edge Postgres happens to return first — for this workbook, the
  stale one, with no successful build, which surfaced as a real, honestly-computed
  blocker ("no successful build to promote") pointing at the wrong family. Confirmed by
  direct query against `estate_edge_index`, not fixed here — `foundry_routing.py` is
  pre-existing, cross-epic code this story only reads, and the real fix belongs to
  whichever clustering path leaves a stale edge live, not to the reader. Worked around
  for this story's own live verification by using a workbook with a single, clean
  `IN_FAMILY` edge instead; flagged as a separate follow-up.
- **A second real bug found live and fixed in this story's own new code**: `ReleaseBoard.
  tsx` inherited the identical multi-pane CSS bug ADR 0066 already found and flagged for
  five earlier screens — `.workspace`'s three-column grid (built for the Estate
  Explorer) with no override, rendering the board's two panes side by side instead of
  stacked. Fixed at the source, following the exact precedent those five screens'
  own fix already set: `.release-board` added to `styles.css`'s existing single-column
  override selector list, confirmed by a real screenshot before and after.

## Alternatives considered

**Add a fifth `GateDecision.gate` value ("G5") for release/promotion.** Rejected — see
decision 2. The spec's own four-gate table (§13.1) never names a release gate; MA-08/
MA-09 are autonomy-ceiling approvals, not numbered gates, and a dedicated table
matches every other "history, not current state" precedent this codebase already has.

**Rebuild the model from scratch at each pipeline stage.** Rejected — see decision 4.
A promotion pipeline's whole value is moving the *same, already-proved* artefact
forward; redeploying the latest successful build's own `git_ref` is both the honest
reading of "pipeline" and the identical pattern `routes_modeller.promote` already
established for the family's own dev→prod hop.

**Snapshot each promotion via `ArtefactStore`, matching the G3 card's own evidence
convention.** Rejected — see decision 5. A `promotion_run` row is already a complete,
directly queryable record; duplicating it into a second store buys nothing a rendered
*view* (the G3 card) actually needed a snapshot for.

**Store `Site.parallel_run_start` as a real ontology property, matching spec §21's own
`site_record`.** Rejected — see decision 6. `promotion_run` is the one real source of
truth for every promotion this platform has made; a second, derived copy on `Site`
risks disagreeing with it the moment a promotion is corrected or backfilled.

## Open question for the product owner

- §14.4's own site-level parallel-run window is read here as opening at the site's
  *first* real production promotion. Should a later story (the one that builds
  decommission readiness) instead key it to the *last* MU released for that site, since
  "the parallel-run period elapsed" (a G4 precondition) more naturally reads as "every
  MU's own window has closed," which is a different, later date than the first MU's own
  window end?
