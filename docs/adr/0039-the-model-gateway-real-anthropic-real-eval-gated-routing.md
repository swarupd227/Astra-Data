# ADR 0039 — The Model Gateway: real Anthropic, real eval-gated routing

Status: accepted · 5 September 2026 · Story S5.3.2 (F5.3, continuing it)

## Context

S5.3.2 follows directly on S5.3.1 (ADR 0038), which built the Transpiler's own validation
ladder behind a disclosed `ModelCaller` seam and named the one gap it deliberately left for
this story: *"As a platform engineer, I want generation to be model-agnostic through the
gateway, so that the client's data-handling decision (Anthropic or Azure OpenAI in-tenant)
does not change the Transpiler. Transpiler calls gateway.generate(task_class='transpile_c3',
...) and never names a provider. Routing is by task class and tenant policy; both configured
providers pass the Transpiler eval set at >= 0.80 first-pass proof before being routable for
transpile_c3. Provider and model are recorded per call in provenance."*

As with S5.3.1, no infrastructure this story needs exists yet: no `gateway.py`, no
`TaskClass` concept, no tenant-policy store, no eval-set harness, and no Anthropic or Azure
OpenAI SDK or API-key configuration anywhere in `config.py`. Unlike S5.3.1, one piece of this
gap is not a structural absence a fixture must stand in for — a real provider integration is
genuinely possible here, given credentials, since Anthropic's API is real and reachable. That
made the scoping question a live one rather than a foregone conclusion, and it was put to the
product owner directly: build the full gateway architecture (task-class routing, a real
Postgres-backed eval-set policy, a real eval harness) with either disclosed fixture providers
behind it (matching S5.3.1's own precedent) or a real, live integration. The explicit choice
was **real Anthropic integration** — live API calls, no fixture — with Azure OpenAI
specifically out of scope for this story (no credentials, no client request to build it yet).
This ADR records the reasoning for what was built around that choice.

## Decisions

### 1. `Gateway` (task-class router) and `ModelCaller` (one provider) are two different Protocols, and `gateway.py` owns both

The AC's own call shape — `gateway.generate(task_class='transpile_c3', ...)`, never naming a
provider — is a different contract from "one provider, given a request and the previous
error, return a candidate" (S5.3.1's own `ModelCaller`, until this story lived in
`generation.py`). Both now live in `gateway.py`: `ModelCaller` (moved from `generation.py`,
unchanged in shape) is what `AnthropicModelCaller` and a future `AzureOpenAIModelCaller`
implement; `Gateway` is what `generate_c3_field`'s own ladder actually holds and calls.
`ModelGateway` is the real implementation of `Gateway` — given a `Mapping[str, ModelCaller]`
and a `GatewayPolicyStore`, it looks up which providers are routable for the task class and
picks the first one (deterministic order — see decision 5), never fabricating a response of
its own. `generation.py` imports both from `.gateway`; nothing about the ladder's own
mechanics (`_run_ladder`, the regeneration loop, `LadderAttempt`) changed — only what it
calls changed, from a bare `ModelCaller` to `gateway.generate(task_class=TRANSPILE_C3, ...)`.

### 2. A real, Postgres-backed tenant policy, append-only

`PostgresGatewayPolicyStore` (migration v0021, `public.model_gateway_policy`) follows the
same "an edit is a new version, never an overwrite" discipline `conformance_ruleset`
(S4.3.2) already established: every eval run inserts a new row rather than overwriting the
provider's last one, and `routable_providers`/`policy_for` derive their answer from the
*latest* row per `(graph, task_class, provider)` via `DISTINCT ON`. This gives a full,
queryable eval history — an operator can see when a provider's own accuracy changed, not
just its current verdict — for the cost of one extra `ORDER BY` clause over an UPDATE-based
design. `ROUTABLE_THRESHOLD = 0.80` is the AC's own literal number, confirmed by research to
be the same one §16.6's own "Class 3 proof rate >= 0.80" target names.

### 3. The eval gate is a real, disclosed proxy for "first-pass proof" — schema + parse, not a real parity verdict

§16.1's own rung 4 ("Proof") needs a real parity verdict against a deployed model; no Arbiter
exists to produce one (E7, the identical gap ADR 0038 already disclosed for the ladder
itself). An eval case here can therefore only grade what rungs 1-2 can really check: does a
response conform to the declared output schema, and does its candidate pass the structural
DAX sanity check (`rules.dax_sanity_check`, reused unchanged). `run_eval_set` calls a real
provider once per case, with no retry — "first-pass" is the point, the identical footing a
ladder's own first attempt already has before any regeneration begins. `TRANSPILE_C3_EVAL_CASES`
is a real, fixed, checked-in corpus of five table-calc idioms, the same "a real, checked-in,
CI-run corpus" shape `rules.py`'s own `GoldenCase`/`golden_cases` (S5.2.1) already established
for a rule, generalised here to grade a live model's response instead of a deterministic
render. The number this gate produces is real and computed from real model calls; it is not,
and is not claimed to be, §16.6's own "Class 3 proof rate" (the different, later,
artefact-level post-proof metric that number names).

### 4. Anthropic is real; Azure OpenAI is disclosed-absent, not a fake failing provider

`AnthropicModelCaller` makes genuine calls to the Anthropic Messages API via the `anthropic`
SDK (pinned `>=1.0,<2` — confirmed against the actually-installed 1.3.0 to be the current
public API surface, not stale training-era assumptions), using its structured-output feature
(`output_config.format`, a JSON-schema-constrained response derived at call time from
whatever `output_schema` the request itself declares, so this stays task-class-agnostic
rather than hardcoding the Transpiler's own DAX/M shape) instead of prompt-engineered JSON and
a hopeful regex. No `ModelCaller` for `azure_openai` is registered anywhere in this codebase —
not a failing or fake provider that would produce a real, misleading `0.00` eval score, but a
provider genuinely absent from `build_gateway`'s own provider map, so the policy store never
gets an eval row for it at all and it is correctly, honestly never "routable" for want of
being asked. The architecture is not Anthropic-specific: a second real `ModelCaller` plus one
`run_eval_set` call is everything a real Azure OpenAI provider would need to become routable —
the "S5.3.2 only has to supply a second `ModelCaller`" shape ADR 0038 already promised is
proven true a second time, forward, for whichever provider is added next.

### 5. Temperature is a declared policy, not an echoed request parameter — the current API has no such field to set

§5.4/§9.4: "Temperature is 0 ... for all generation paths." The installed SDK's own
`anthropic.types.message_create_params.MessageCreateParamsBase` declares no `temperature`
field at all — confirmed directly against the package actually resolved from PyPI, not
assumed from stale knowledge — the current Messages API has retired the literal sampling
knob in favour of `output_config.effort` (set to `"high"`, the closest real control this API
exposes for a reasoning-tier task, §9.4's own `model_policy.tier: reasoning`).
`RawModelResponse.temperature` still records `0.0` on every Anthropic call: the identical
"provenance states the declared policy, not an echoed request field" reasoning
`provenance.py`'s own `temperature` docstring already gave for a field nothing before S5.3.1
had ever populated, now applied to an API surface where there is, additionally, no literal
parameter to echo even if the design wanted to. This is disclosed directly in
`AnthropicModelCaller`'s own docstring rather than left for a reader to wonder why an
apparently-hardcoded value is not actually read from the response.

### 6. Real cost-tier routing among multiple routable providers is not built

§5.5 describes routing "to the cheapest model tier that meets the calibrated accuracy bar" —
with exactly one real provider registered, there is nothing to rank. `ModelGateway.generate`
picks the first routable candidate in a fixed, deterministic (alphabetical) order rather than
inventing a cost number with nothing real behind it. TokenOps' own budget/cost tracking
(§5.5's second half) is real, separate, unbuilt scope; this decision is recorded so a second
provider's arrival does not silently assume tie-breaking was ever solved.

## Consequences

- New module `gateway.py`: `TaskClass`/`TRANSPILE_C3`, `ROUTABLE_THRESHOLD`, `SupportsAsDict`,
  `RawModelResponse`, `ModelCaller`, `Gateway`, `GatewayRoutingError`, `PolicyEntry`,
  `GatewayPolicyStore`/`PostgresGatewayPolicyStore`/`NullGatewayPolicyStore`, `ModelGateway`,
  `StaticGateway` (a test-only "route to one fixed caller, skip the policy check" double),
  `EvalCase`/`EvalCaseResult`/`EvalReport`/`run_eval_set`, `AnthropicModelCaller`,
  `build_gateway`/`null_gateway`.
- `generation.py` refactored: `RawModelResponse`/`ModelCaller` moved to `gateway.py` (still
  re-exported for backward compatibility); `_run_ladder`/`generate_c3_field`/
  `GenerationEngine` now take a `Gateway` instead of a bare `ModelCaller`, calling
  `gateway.generate(task_class=TRANSPILE_C3, ...)` directly; `LadderAttempt` gains
  `gateway_error` (set, and not retried, when the gateway itself has no routable provider —
  a different, earlier failure than a model responding badly); `FixtureModelCaller` stays as
  a test/no-provider-configured double, deliberately not part of `build_gateway`'s own real
  provider map (it always fails its own eval set). New: `TRANSPILE_C3_EVAL_CASES`,
  `run_transpile_c3_eval`.
- New migration v0021: `public.model_gateway_policy` (append-only, platform table, no
  ontology change).
- New routes: `GET /v1/model-gateway:policy` (any Artizent role — the tenant policy for one
  task class), `POST /v1/model-gateway:run-eval` (platform engineer — runs the eval set
  against one real, configured provider and records the verdict; the one action here that
  makes a real, billed external call, gated to the persona this story's own AC names).
- `main.py` wires a real `ModelGateway` (`app.state.gateway`, registering `AnthropicModelCaller`
  via `build_credential_provider`/`CredentialProvider.resolve("anthropic/api_key")`, the same
  "a secret never crosses request-config plumbing" discipline `credentials.py` already
  established for source credentials) into `GenerationEngine`, replacing S5.3.1's own
  `FixtureModelCaller` default.
- New `Settings.anthropic_model` (default `claude-sonnet-5`, env `ASTRA_ANTHROPIC_MODEL`); new
  dependency `anthropic>=1.0,<2`.
- Routing/policy mechanics are tested against a real Postgres-backed
  `PostgresGatewayPolicyStore` and a scripted `ModelCaller` double
  (`test_integration_gateway.py`); pure logic (`ModelGateway` routing decisions, `run_eval_set`
  grading, JSON-schema derivation) is tested against an in-memory policy double
  (`test_gateway.py`); a narrow, `ASTRA_CREDENTIAL_ANTHROPIC_API_KEY`-gated set of tests makes
  one real Anthropic call and one real eval run, skipped like every other integration test
  here skips when its own required resource (elsewhere, a reachable Postgres) is absent — a
  live deployment key is not something CI should require or spend on every run.

## Alternatives considered

**Build only disclosed fixture providers behind the gateway, matching S5.3.1's own precedent
exactly.** Rejected — the product owner's own explicit choice (via `AskUserQuestion`) was
real Anthropic integration over a fixture, unlike S5.3.1 where no real option existed at all.
The routing/eval-gating architecture is designed to be equally real either way; only
`AnthropicModelCaller` itself is the live piece.

**Build both Anthropic and Azure OpenAI providers, to literally satisfy the AC's own "both
configured providers" wording.** Rejected — see decision 4. The product owner's own explicit
scope for this story named Anthropic only; "both configured providers" describes the general
case where a tenant has configured two, not a requirement that this story configure two
itself. A real Azure OpenAI provider needs its own SDK, credentials, and — per the same
discipline — its own explicit scope decision, not a fixture standing in for it under a claim
of being "configured."

**Extend `FixtureModelCaller` itself to make real calls, rather than a new
`AnthropicModelCaller`.** Rejected — `FixtureModelCaller`'s own documented contract (ADR 0038)
is "never fabricates a plausible-but-fake translation; always honestly declines." Repurposing
it to sometimes call a real API would blur a name that already has a settled, tested meaning;
a new class keeps both honest about what they are.

**Skip the real Anthropic-gated integration tests, since CI has no key to run them.**
Rejected — every other integration test in this suite already tolerates its own required
resource being absent (`pytest.skip` when Postgres is unreachable) rather than being omitted
outright. The same discipline extended to a live API key is a real, disclosed gap in CI
coverage stated as a skip reason, not a false claim that this integration is exercised
automatically.

**Invent a cost-tier number for routing, so "cheapest tier" reads as literally implemented.**
Rejected — see decision 6. A single real provider makes any such number untestable and
unfalsifiable; a disclosed, deterministic tie-break says plainly what this story did and did
not build.
