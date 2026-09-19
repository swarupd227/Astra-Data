# ADR 0088: Wave Scheduler — admission control for MUs per train

**Status:** Accepted
**Date:** 2026-09-18 (corrected 2026-09-19)
**Related:** [ADR 0087](./0087-mu-workflows-a-real-temporal-skeleton-over-the-c3-slice.md) (MU workflows), story S12.1.2

## Problem

S12.1.1 gave every MU its own Temporal workflow, but nothing bounds how many run at
once. S12.1.2 asks for a scheduler that admits MUs by train subject to family state,
per-site and per-Fabric-workspace concurrency, the model-gateway budget and a per-train
WIP limit, lets a programme manager pause and resume a train or a site, and makes the
reason an MU is waiting visible.

## Decision

`wave_scheduler.WaveScheduler.evaluate_admission(mu_ref, train_id, site_id)` is an
application-tier decision function — not a Temporal workflow and not a new deployable —
that returns a `SchedulerDecision(admitted, blocking_constraint, reason)`. Constraints
are checked in this order and the first failure is the reported reason:

1. train paused (`ReleaseTrain.paused`)
2. site paused (`Site.paused`)
3. family state below `BUILT` (via `Workbook -[:IN_FAMILY]-> ModelFamily`)
4. site concurrency — MUs in `PROVING`/`MENDING`/`ESCALATED` under the site
   (fixed default 5)
5. Fabric-workspace concurrency (fixed default 10)
6. the MU's own token budget — held once the MU has used all of it (S12.2.2, ADR 0090)
7. train WIP limit (`ReleaseTrain.wip_limits.train`, optional)

### Findings that shaped the implementation

Reading the ontology before writing the queries (the first draft skipped this and was
wrong — see *Correction*):

* There is **no `Workspace` node and no `IN_WORKSPACE`/`TARGETS`/`IN_ESTATE` edge**. A
  Fabric workspace is the plain string `SemanticModel.workspace`, and a `SemanticModel`
  points at its family by the string `family_ref`, not by an edge. The workspace lookup
  therefore joins on that string.
* A `Site` reaches a `Workbook` only through the chain
  `Site -[:CONTAINS]-> Project -[:CONTAINS]-> Workbook`.
* Graph state lives inside Apache AGE, so all reads go through
  `GraphRepository.run_read_only_cypher` (read-only, timeout-bounded), the same path the
  `/v1/cypher` route uses.

### Pause state is persisted, as ontology properties

`Site.paused`, `Site.pause_reason`, `ReleaseTrain.paused`, `ReleaseTrain.pause_reason`
are new optional properties (`SCHEMA_VERSION` 38 → 39, additive, confirmed non-breaking
by `tools/migration_check.py`; declared as spec deviations). Pause/resume writes them
through `GraphWriter.set_node_properties`, so each change is a normal, replayable
`estate.node.upserted` event and survives a restart.

### API and events

* `POST /v1/scheduler/trains/{id}:pause|:resume`, `POST /v1/scheduler/sites/{id}:pause|:resume`
  — platform-engineer role.
* `GET /v1/scheduler/decision/{workbook_id}/{train_id}` — any Artizent role; answers
  "why is this MU waiting" from live graph state.
* Five non-mutating scheduler notice event types exist in `events.py`
  (`MU_ADMISSION_DECISION`, `TRAIN_PAUSED`/`RESUMED`, `SITE_PAUSED`/`SITE_RESUMED`). **They
  are defined and replay-safe, but nothing emits them** — the pause/resume routes write the
  property (and its own `node.upserted` event) but do not raise `TRAIN_PAUSED` etc. (The
  two budget notices added by S12.2.2 *are* emitted, by `BudgetMonitor`; see ADR 0090.)

## What this story does not do (disclosed)

* **No enforcement.** Nothing calls `evaluate_admission` from the MU workflow or from a
  background job, so an MU is not actually held at `GENERATED` when blocked. The AC's
  "a paused train holds MUs at their current state" is therefore **not yet true
  end-to-end**; this story delivers the decision, the pause state and the query. Wiring
  is a follow-on with two candidate shapes: the workflow asks before `GENERATED → PROVING`,
  or a periodic job signals admitted workflows.
* **No Wave Board surface.** "Visible on the Wave Board" is served only as an API; no
  console screen was built.
* **The budget constraint reads real per-MU spend** (added by S12.2.2): `WaveScheduler`
  takes a `TokenBudgetStore`, and an MU whose consumption has reached its limit is held
  with `MODEL_GATEWAY_BUDGET` and the numbers as the reason. Only the hard limit holds; an
  MU in the 80% alert zone is still admitted. Without a store the constraint admits (a
  caller with no Postgres pool). It is read-only: the alerts stay the gateway's to raise.
* **Concurrency limits are fixed constants** (5 / 10). A per-site or per-workspace
  override is not built.
* **"By train sequence" ordering** (`IN_TRAIN.sequence`) is not implemented — the
  evaluator answers per MU and never selects which MU goes next.

## Correction

The first commit of this story (813ed61) was wrong in ways its own tests could not catch:
`wave_scheduler.py` imported a non-existent `db` module and queried invented relational
tables (`nodes`, `edges`) and invented ontology (`Workspace`, `IN_ESTATE`); the routes
read a non-existent `repository.db`; the pause properties were not in the ontology so the
write would have been rejected; and its README row quoted test counts that were never
measured. All were fixed afterwards, and the constraints are now proven by
`tests/test_integration_wave_scheduler.py`, which runs every constraint (admit and block)
against real PostgreSQL + Apache AGE.

## Testing

* `test_wave_scheduler.py` — decision/constraint structure; drift guards tying the
  scheduler's family ladder to the ontology and its MU-state sets to `MU_STATES`.
* `test_scheduler_routes.py` — role gating and not-found behaviour (in-memory fixture,
  which cannot run Cypher by design).
* `test_integration_wave_scheduler.py` — 9 tests on real AGE: admit; family short of
  `BUILT`; no family; train pause + resume; site pause; WIP limit; site concurrency;
  terminal/waiting MUs not counted; workspace concurrency.
