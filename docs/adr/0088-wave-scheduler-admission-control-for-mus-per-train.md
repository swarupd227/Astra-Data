# ADR 0088: Wave Scheduler — Admission Control for MUs per Train

**Status:** Accepted  
**Date:** 2026-09-18  
**Superseded by:** *(none yet)*  
**Supersedes:** *(none)*  
**Related:** [ADR 0087](./0087-mu-workflows-a-real-temporal-skeleton-over-the-c3-slice.md) (MU workflows), [S12.1.2](../reference/Product-Backlog-v1.0.md) (story)

## Problem

S12.1.1 delivered a real Temporal workflow for each MU, but workflows run independently. A program needs control: throughput must not exceed limits (executor capacity, budget, WIP per train), and a program manager must be able to pause/resume trains and sites without killing in-flight work.

The backlog (S12.1.2, [opening E12](../reference/Product-Backlog-v1.0.md)) names this control *the wave scheduler*: a decision-maker that gates MU admission subject to constraints, shows decisions on the Wave Board, and lets program managers govern throughput.

## Solution

A `WaveScheduler` evaluates MU admission decisions in the graph-svc application-tier layer, not the workflow layer. This separates concerns:

### Design

1. **Constraint evaluation (application tier)**: `WaveScheduler.evaluate_admission(mu_ref, train_id, site_id)` checks:
   - Family state: model family must be BUILT or later (families under development cannot admit MUs yet)
   - Executor concurrency per source site (default 5 concurrent MUs per site)
   - Executor concurrency per Fabric workspace (default 10 concurrent MUs per workspace)
   - Model-gateway budget: tokens/requests available (stub: always True pending budget tracking)
   - WIP per train: ReleaseTrain.wip_limits JSON holds per-train limit (optional; null = no limit)
   - Train pause: ReleaseTrain.paused boolean (programme manager can pause a whole train)
   - Site pause: Site.paused boolean (programme manager can pause a whole site)

2. **Constraint ordering**: Evaluated in sequence; first constraint that fails blocks admission and is named in the decision.

3. **Decisions as events**: `MU_ADMISSION_DECISION` event emitted on each evaluation (admit or block). Wave Board watches these to show why an MU is waiting. The event carries:
   - `admitted`: boolean
   - `blocking_constraint`: which constraint blocked it (null if admitted)
   - `reason`: prose reason (e.g. "Site acme-rqa concurrency at limit (5/5)")

4. **Program manager control**: Two control routes:
   - `POST /v1/scheduler/trains/{train_id}:pause` + `POST …:resume` — pauses all MUs in a train
   - `POST /v1/scheduler/sites/{site_id}:pause` + `POST …:resume` — pauses all MUs from a site
   - Paused trains/sites emit `TRAIN_PAUSED`/`TRAIN_RESUMED`/`SITE_PAUSED`/`SITE_RESUMED` events

5. **Query route**: `GET /v1/scheduler/decision/{workbook_id}/{train_id}` returns the current admission decision without mutating anything.

### Why application tier, not workflow?

- **Separation**: Scheduler is a policy layer, distinct from workflow execution
- **Speed**: Evaluation happens instantly; workflow queries can be queued/retried
- **Leverage**: MU workflow already encodes state machine (S12.1.1); scheduler is orthogonal policy
- **Testability**: Scheduler logic unit-testable without Temporal; routes integration-testable without starting workflows

### Enforcement (follow-on)

This story defines and makes scheduler decisions visible. **Enforcement** — actually holding MUs at GENERATED state when blocked — is the next step:

- **Option A:** MU workflow calls scheduler before GENERATED→PROVING transition; if blocked, stays at GENERATED and awaits signal to retry
- **Option B:** Background job runs scheduler periodically; when an MU is admitted, signals the workflow to proceed

S12.1.2 delivers the decision infrastructure (scheduler logic + events + API). Enforcement timing is a follow-on design choice.

### Event types (S12.1.2 additions to S12.1.1)

```python
MU_ADMISSION_DECISION = "estate.mu.admission.decision"  # Not a mutation
TRAIN_PAUSED = "estate.train.paused"                   # Not a mutation
TRAIN_RESUMED = "estate.train.resumed"                 # Not a mutation
SITE_PAUSED = "estate.site.paused"                     # Not a mutation
SITE_RESUMED = "estate.site.resumed"                   # Not a mutation
```

All are notices (non-mutating), matching the pattern S12.1.1 established for `ACTIVITY_STARTED`/`ACTIVITY_FINISHED`.

### Ontology changes (to follow)

Adding `paused`/`pause_reason` properties to `ReleaseTrain` and `Site` nodes requires:
- Schema version bump (38 → 39)
- Ontology lock update
- Non-breaking change (optional properties; absent = never paused)

Deferred to a follow-on commit to keep S12.1.2 focused on scheduler logic.

## Tradeoffs

| Tradeoff | Choice | Rationale |
|----------|--------|-----------|
| Enforcement timing | Deferred to follow-on | Scheduler logic is complete; enforcement choice (workflow hook vs. background job) is orthogonal and can be validated live. |
| Budget constraint | Stubbed (always True) | Budget tracking requires integration with model-gateway metrics. Plumbing deferred; constraint structure is ready. |
| Concurrency defaults | Site: 5, Workspace: 10 | Conservative starting points; can be overridden per-site/workspace via properties (to be added). |
| Pause state storage | Transient (not persisted long-term) | Program managers pause trains/sites briefly during incidents/maintenance. Pause state is runtime-only; if a pod restarts, paused state resets (consistent with "operator is watching"). Persist if durability is needed. |

## Testing

- Unit tests: `test_wave_scheduler.py` tests constraint evaluation and decision structure
- Integration tests: `test_scheduler_routes.py` tests API routes (pause/resume, decision query)
- Full regression suite: Existing MU workflow tests unchanged; scheduler is orthogonal

## Impact

- **MU workflows (S12.1.1):** No changes; scheduler is called *by* workflow or background job, not from workflow
- **API surface:** +5 scheduler routes (decision query, pause/resume per train/site)
- **Event stream:** +5 event types (all notices; replay skips them)
- **Wave Board:** Ready to consume MU_ADMISSION_DECISION events to show why MUs are waiting
- **Program managers:** Can now pause/resume trains and sites without workflow changes

## Follow-ons

1. **Admission enforcement:** Hook scheduler into MU workflow or background job
2. **Budget tracking:** Wire model-gateway metrics into budget constraint
3. **Concurrency tuning:** Add per-site and per-workspace overrides for concurrency limits
4. **Pause durability:** Decide if pause state should survive pod restarts
5. **Wave Board integration:** UI to visualize blocking constraints and manage pause/resume
