# ADR 0090: Token budgets — per-MU limit and real per-MU consumption; enforcement not built

**Status:** Accepted, **partially delivered**
**Date:** 2026-09-19 (attribution added 2026-09-20)
**Related:** story S12.2.2; [ADR 0089](./0089-model-gateway-observability-partial-s12-2-1.md)

## Context

S12.2.2 asks for token and cost budgets at programme, train and MU level, real-time
consumption on Model Gateway & TokenOps, a soft alert at 80% and a hard stop at 100% per
MU (the MU goes `ESCALATED` with reason `BUDGET`), cost per accepted report by tier
against the calibrated figure, and a weekly TokenOps summary in the Status Pack.

## The blocker, and how it was removed

Consumption could not be measured: `gateway_request_log` had no column attributing a call
to an MU (`agent_id` is the calling agent role, e.g. `transpiler`) and never persisted
token counts (they existed only on the in-memory `RawModelResponse`).

Migration v0048 adds nullable `workbook_id`, `model`, `tokens_in`, `tokens_out` (plus a
partial index on `(graph, workbook_id, created_at)`). `workbook_id` is threaded as an
optional keyword through `Gateway.generate` → `ModelGateway`/`StaticGateway` →
`_dispatch`, and passed by the two real callers: the Transpiler
(`generate_c3_field` → `_run_ladder`, given the workflow's workbook by
`MuActivities.run_generate`) and the Mender (`call_model_repair`, from the exception's
`mu_ref`).

**NULL, not zero, means unknown.** A call that raised records NULL tokens; rows from
before v0048 and calls with no MU in scope are unattributed. `get_status` sums only rows
with both a workbook and non-NULL token counts, so unknown usage is never counted as
free and never billed to an MU it cannot be tied to.

## What is delivered

* `public.token_budget` (v0047): one row per `(graph, mu_ref)`, upsert on set; default
  limit 1,000,000 when none is set.
* `TokenBudgetStore.get_status` returns the limit against **real, cumulative** consumption
  for that MU (the AC budgets an MU, not a day — an earlier draft's "daily" wording was
  wrong and is removed).
* **Exact cost** where a price exists: each model's own input and output tokens are priced
  from `MODEL_PRICING`. A model with no price contributes tokens but no cost and is
  reported in `unpriced_tokens`, so a partial cost is never mistaken for a complete one.
* `POST`/`GET /v1/token-budget/{workbook_id}:set|:status`.
* Proven by real-Postgres tests (calls through the real gateway `_dispatch` into real log
  rows, summed back per MU) and by a real Temporal workflow run whose spend
  (42 + 17 tokens) is read back for its own MU.

## Acceptance criteria — honest status

| Criterion | Status |
|---|---|
| Budgets configurable at three levels | **MU level only** |
| Consumption shown in real time | **Readable via the status route; no Model Gateway & TokenOps screen** |
| Soft alert at 80% | **Not met** — `is_warning` is computed but nothing raises an event or a UI signal; `BUDGET_WARNING` is defined and never emitted |
| Hard stop at 100%, MU → `ESCALATED` reason `BUDGET` | **Not met** — no workflow or scheduler check consults the budget; `BUDGET_EXHAUSTED` is never emitted; the wave scheduler's budget constraint is still a stub |
| Cost per accepted report by tier vs calibrated figure | **Not built** |
| Weekly TokenOps summary in the Status Pack | **Not built** |

## Known attribution gaps

* A pre-proof generation failure writes an `ExceptionCase` whose `mu_ref` is the synthetic
  `calc:{calc_id}` (the gap S12.1.1 disclosed). The Mender derives its workbook from that
  `mu_ref`, so **repair spend for such cases is attributed to `calc:…`, not the workbook**.
* Calls with no MU in scope (the data-handling boundary test, eval runs) are unattributed
  by design.
* The token count is what the provider reported; `AnthropicModelCaller` reads it from
  `response.usage`. Nothing cross-checks it.

## Follow-ons

Act on the number: raise `BUDGET_WARNING` at 80%; check before `GENERATED → PROVING` in
the MU workflow and feed the wave scheduler's budget constraint; `ESCALATED`/`BUDGET` at
100%. Then the programme and train levels (sum over member MUs via `IN_TRAIN`), the
TokenOps screen, cost per accepted report, and the Status Pack section. Fix the
`calc:…` `mu_ref` gap so Mender spend lands on the workbook.
