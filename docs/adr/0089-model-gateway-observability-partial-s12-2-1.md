# ADR 0089: Model gateway observability — a partial S12.2.1

**Status:** Accepted, **partially delivered**
**Date:** 2026-09-19
**Related:** story S12.2.1; builds on S5.3.2 (gateway), S11.4.1–S11.4.3 (request log, enforcement, injection defence)

## Context

S12.2.1 asks for "a single model gateway with provider routing by task class and tenant
policy". Reading `gateway.py` first showed most of it already exists from S5.3.2:
`ModelGateway` routes a task class to providers, a provider becomes routable only after
its eval pass rate clears `ROUTABLE_THRESHOLD`, `ModelCaller` is the one provider
interface, and `AnthropicModelCaller` makes real calls with schema-constrained output.
So this story's real work is the call-accountability criterion, and it was scoped to
Anthropic only (Azure OpenAI deliberately not built).

## What was added

Three nullable columns on `public.gateway_request_log` (migration v0046) and matching
fields on `RawModelResponse`:

* `context_hash` — hash of the request payload.
* `latency_ms` — wall-clock time of the provider call.
* `prompt_template_version` — which prompt template produced the call.

`_dispatch` records them for every call. When the provider call raises there is no
response to read them from, so the log row falls back to the gateway's own hashes and the
elapsed time it measured itself — preserving S11.4.1's guarantee that a request is logged
even if the call fails (a regression test covers this; the first draft of this change
broke it, caught by `mypy`).

## Acceptance criteria — honest status

| Criterion | Status |
|---|---|
| Anthropic + Azure OpenAI behind one interface | Anthropic only (by scope); interface pre-existing |
| Routing table with fallbacks; provider must pass eval | Pre-existing (S5.3.2); unchanged |
| Records task class, provider, model, prompt hash, context hash, tokens in/out, latency, cost | **Mostly met.** Recorded per call: task class, provider, model, both hashes, tokens in/out, latency, and (v0048, S12.2.2) the MU it was for. **Cost is not stored per call**; it is derived exactly at read time from the stored model and token counts (`token_budget.cost_usd_for`), and only for models with a price entry. A call that raised records NULL tokens. |
| Returns a gateway request id used in provenance | Pre-existing |
| Prompt templates versioned in Git; version is part of the prompt hash | **Not met.** `PROMPT_TEMPLATE_VERSION` is the literal `"dev"`, not derived from Git, and it is recorded *beside* the hash rather than folded into it. |

## Behaviour change to be aware of

`prompt_hash` previously hashed the assembled request text; it now hashes the static
system prompt, and the request text's hash moved to `context_hash`. Rows written before
v0046 keep the old meaning in `prompt_hash` and have a null `context_hash`.

## Follow-ons

Store cost per call if a persisted figure is wanted; derive the template version from the
Git SHA at build time and include it in the hash.
