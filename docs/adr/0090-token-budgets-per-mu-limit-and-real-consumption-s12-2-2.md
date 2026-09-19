# ADR 0090: Token budgets — per-MU limit and real per-MU consumption; enforcement not built

**Status:** Accepted, **partially delivered** (per-MU only)
**Date:** 2026-09-19; attribution 2026-09-20; alert and hard stop 2026-09-21
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

## The 80% alert and the 100% hard stop

**Where it is enforced: the model gateway, per call.** `ModelGateway` asks a
`BudgetGuard` (`token_budget.BudgetMonitor`) before each call attributed to an MU and
refuses it if the MU's consumption has reached its limit (`GatewayBudgetError`); it asks
again after the call so the alert is raised by the call that crossed the line. Enforcing
per call, rather than between workflow activities, matters because one activity makes
several model calls (a Transpiler ladder, a Mender run) and would otherwise overshoot by a
whole activity. A guard failure *after* a call is logged and swallowed: it must not cost
the caller a response the provider has already been paid for. "No routable provider" is
still reported ahead of a budget refusal.

**How the MU reaches `ESCALATED`.** §3.2 allows `ESCALATED` only from `FAILED` or
`MENDING`, so the workflow cannot simply "escalate on exhaustion" from `GENERATED`
without inventing an edge in the spec's state machine, which was deliberately not done.
`GatewayBudgetError` is a `GatewayRoutingError`, which both real callers already treat as
"this call cannot happen and will not become possible within this run": the attempt
fails immediately (no retry), the MU takes the legal `FAILED → (MENDING →) ESCALATED`
route, and `MuActivities.write_mu_state` stamps the reason.

**The reason.** `Workbook.mu_state_reason` (new optional enum, `SCHEMA_VERSION` 40,
additive; the single value `BUDGET`) is written with *every* state write, so it is absent
for any state without a reason and can never linger from an earlier escalation.
`BUDGET` means the MU was at or over its budget at the moment it escalated. It is decided
in the activity (the workflow must stay deterministic), so the workflow code itself, and
therefore any in-flight workflow's history, is unchanged.

**Alerts.** `BUDGET_WARNING` (≥ 80%) and `BUDGET_EXHAUSTED` (≥ 100%) are notice events in
the outbox. Each is raised once per `(MU, limit)`, tested against the outbox itself so it
holds across restarts and workers; raising an MU's limit is a new budget and can alert
again. Two calls racing over the line could each raise one, giving a duplicate notice,
never a missed one.

**A bug the end-to-end test found.** The first workflow run of a budget-stopped MU ended
`PASSED`: generation was refused, but the Mender then made a model call and closed the
case. The Mender derives its MU from the exception's `mu_ref`, which for a pre-proof
generation failure is the synthetic `calc:{id}` (the gap S12.1.1 disclosed), so its spend
was checked against a different, unspent budget: the hard stop had a hole. `mend_exception`
now takes `charge_to_mu`, which the workflow's `run_mend` sets to the real workbook; it
changes nothing else (evidence and sibling-case lookups still key on `mu_ref`).

## Acceptance criteria — honest status

| Criterion | Status |
|---|---|
| Budgets configurable at three levels | **MU level only** |
| Consumption shown in real time | **Readable via the status route; no Model Gateway & TokenOps screen** |
| Soft alert at 80% | **Met as an event** (`BUDGET_WARNING`, once per MU and limit). No console toast or notification routing consumes it yet. |
| Hard stop at 100% per MU, MU → `ESCALATED` reason `BUDGET` | **Met for model calls attributed to an MU through `ModelGateway`**, proven end to end (see below). Limits below. |
| Cost per accepted report by tier vs calibrated figure | **Not built** |
| Weekly TokenOps summary in the Status Pack | **Not built** |

### Limits of the hard stop (read these)

* **Overshoot is bounded by one call, not zero.** The call that crosses the line is served;
  only later calls are refused. A single call's own tokens can exceed what was left.
* **It stops model spend, not the MU.** An MU whose generation already succeeded, or whose
  repair is deterministic (an ACTIVE Pattern, no model call), carries on: no spend is
  needed. It escalates only when a refused call makes an attempt fail.
* **Only calls that name an MU are budgeted.** The data-handling boundary test, eval runs
  and any route-driven call made without a `workbook_id` are unbudgeted by design.
* **Only `ModelGateway` (what `build_gateway` builds, with a writer) enforces it.**
  `StaticGateway` and scripted test gateways make no budget decision.
* `write_mu_state` labels an escalation `BUDGET` whenever the MU is exhausted at that
  moment, even if the proximate cause was something else that coincided.
* Each guarded call costs two extra queries (limit, sum) before and after.

## Known attribution gaps

* A **route-driven** Mender run (not via the workflow) still charges by the exception's
  `mu_ref`; for a pre-proof failure that is the synthetic `calc:{id}`. The workflow path is
  fixed via `charge_to_mu`.
* Calls with no MU in scope are unattributed by design.
* The token count is what the provider reported; nothing cross-checks it.
* The MU workflow itself cannot yet run more than one calc (`PASSED → PROVING` is not a
  legal transition); pre-existing, not addressed here.

## Follow-ons

Surface the alerts (console, the notifications route); the programme and train levels (sum
over member MUs via `IN_TRAIN`); feed the wave scheduler's still-stubbed budget
constraint from the same status; the TokenOps screen; cost per accepted report; the
Status Pack section; charge route-driven Mender runs to the real MU.
