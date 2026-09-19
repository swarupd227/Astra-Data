# ADR 0090: Token budgets — configuration only, a small part of S12.2.2

**Status:** Accepted, **mostly not delivered**
**Date:** 2026-09-19
**Related:** story S12.2.2; [ADR 0089](./0089-model-gateway-observability-partial-s12-2-1.md)

## Context

S12.2.2 asks for token and cost budgets at programme, train and MU level, real-time
consumption on Model Gateway & TokenOps, a soft alert at 80% and a hard stop at 100% per
MU (the MU goes `ESCALATED` with reason `BUDGET`), cost per accepted report by tier
against the calibrated figure, and a weekly TokenOps summary in the Status Pack.

## The blocking finding

Consumption cannot be measured today. `gateway_request_log` has **no column attributing
a call to an MU** (`agent_id` is the calling agent role, e.g. `transpiler`, not a
workbook id) and **never persisted `tokens_in`/`tokens_out`** (they exist only on the
in-memory `RawModelResponse`). The first draft of this story summed columns that do not
exist and filtered `agent_id` by an MU id, which would have failed at runtime; it now
reports zero consumption and says so, rather than a wrong number.

Fixing it needs (1) a migration adding `workbook_id`, `tokens_in`, `tokens_out` (and
`model`, `cost`, per ADR 0089) to the log, and (2) threading the workbook id through
`ModelGateway.generate` and every caller (`generation.py`, `mender.py`).

## What was delivered

* `public.token_budget` (migration v0047): one row per `(graph, mu_ref)`, upsert on set.
* `TokenBudgetStore.set_budget` / `get_status`; `get_status` returns the configured
  limit (default 1,000,000 when none) and **`tokens_consumed = 0`**.
* `POST /v1/token-budget/{workbook_id}:set` (platform engineer) and
  `GET /v1/token-budget/{workbook_id}:status` (Artizent roles).
* `MODEL_PRICING` (Sonnet 5 / Opus 5 rates) and the cost formula — but the cost uses an
  assumed 60/40 input/output split because real per-call splits are not attributed.
* Two notice event types, `BUDGET_WARNING` / `BUDGET_EXHAUSTED`, defined and replay-safe.
  **Nothing emits them.**

## Acceptance criteria — honest status

| Criterion | Status |
|---|---|
| Budgets configurable at three levels | **MU level only** |
| Consumption shown in real time | **Not met** (consumption is always 0) |
| Soft alert at 80% | **Not met** — `is_warning` is computed but never triggers an event or a UI |
| Hard stop at 100%, MU → `ESCALATED` reason `BUDGET` | **Not met** — no workflow or scheduler check exists |
| Cost per accepted report by tier vs calibrated figure | **Not built** |
| Weekly TokenOps summary in the Status Pack | **Not built** |

The "daily" window the code comments mention is also unimplemented — there is no date
logic because there is no consumption to window.

## Follow-ons

Consumption attribution (above) first; then the 80%/100% checks (in the MU workflow
before `GENERATED → PROVING`, and in the wave scheduler's currently stubbed budget
constraint), the programme/train levels, the TokenOps screen, cost-per-accepted-report,
and the Status Pack section.
