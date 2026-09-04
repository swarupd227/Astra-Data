"""The Model Gateway — specification §5.5, story S5.3.2.

    "generation to be model-agnostic through the gateway, so that the client's
    data-handling decision (Anthropic or Azure OpenAI in-tenant) does not change the
    Transpiler."

**What this module is.** A real task-class router (`ModelGateway.generate(task_class=...)`)
in front of one or more real, named `ModelCaller` providers; a real, Postgres-backed tenant
policy (`PostgresGatewayPolicyStore`, scoped by `graph_name` — the same "the graph is the
tenant" footing every other per-tenant store in this codebase already uses: `RulesEngine`,
`PostgresConformanceRulesetStore`, `PostgresProvenanceStore`) that records, per
`(task_class, provider)`, the last eval run's pass rate and derives "routable" from it at
`>= ROUTABLE_THRESHOLD`; and a real eval harness (`run_eval_set`) that calls a real provider,
for real, against a fixed golden corpus and grades every response — the same shape
`rules.py`'s own `GoldenCase`/`golden_cases` (S5.2.1) already established for "a real,
checked-in, CI-run corpus", generalised here to run against a live model instead of a
deterministic renderer.

**The one real, disclosed proxy this module accepts.** §16.1's own rung 4 ("Proof") needs a
real parity verdict against a deployed model — no Arbiter exists to produce one (E7, the same
gap `generation.py`'s own rungs 3/4 already disclosed). An eval case here can therefore only
grade what rungs 1-2 can really check: does the response conform to the declared output
schema, and does its candidate pass a structural sanity check. "First-pass proof" in this
module's gate is exactly that — a case's first, unretried attempt passing schema + parse —
not a real parity verdict against Fabric. It is a real, computed number from real model
calls, honestly named for what it actually checks, not a stand-in for §16.6's "Class 3 proof
rate" (the *artefact*-level, post-proof metric that number names — a different, later
measurement this module does not compute).

**Anthropic is real; Azure OpenAI is not wired.** Per an explicit scope decision on this
story (the platform engineer chose live Anthropic integration over disclosed fixtures, and
Azure OpenAI specifically out of scope for now — no credentials, no SDK, no client request to
build it yet): `AnthropicModelCaller` makes real calls against the Anthropic Messages API.
No `ModelCaller` for `azure_openai` is registered anywhere in this codebase; it is not a
failing or fake provider, it is simply absent, so the policy store never gets an eval row for
it and it is correctly never "routable" — the same "disclosed empty, not fabricated" footing
`generation.py`'s own `model_ctx`/`charter_excerpt` already established for a gap this
platform has not filled. Nothing about the architecture is Anthropic-specific: a second real
`ModelCaller` implementation plus one `run_eval_set` call is all a real Azure OpenAI provider
would need to become routable — the "S5.3.2 only has to supply a second `ModelCaller`" shape
ADR 0038 already promised, proven true a second time, forward, for whichever provider is
added next.

**Temperature.** §5.4/§9.4: "Temperature is 0 ... for all generation paths." The Messages
API this SDK's `messages.create` targets exposes no `temperature` parameter at all —
confirmed directly against the installed SDK's own
`anthropic.types.message_create_params.MessageCreateParamsBase`, which simply does not
declare one. `AnthropicModelCaller` still records `temperature=0.0` on every
`RawModelResponse`: the same "provenance states the declared policy, not an echoed request
field" reasoning `provenance.py`'s own `temperature` docstring already gives, now applied to
an API surface that has retired the literal sampling knob in favour of `output_config.effort`
(set to `"high"`, the closest real control this API exposes for a reasoning-tier task, §9.4's
own `model_policy.tier: reasoning`).
"""

from __future__ import annotations

import json
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Protocol

import anthropic
import asyncpg

from .config import Settings
from .context.canonical import context_hash
from .credentials import CredentialProvider
from .ids import new_ulid

#: §5.5: "routes by task class". Only one task class exists in this codebase today (the
#: Transpiler's own C3 path, story S5.3.1) — a plain string alias, not an enum with one
#: member, per the same "don't design for a second case that doesn't exist yet" discipline
#: this session already follows elsewhere.
TaskClass = str
TRANSPILE_C3: TaskClass = "transpile_c3"

#: §16.6's own "Class 3 proof rate >= 0.80" target and the AC's own literal "0.80" —
#: confirmed, by research, to be the same number this gate uses.
ROUTABLE_THRESHOLD = 0.80

GATEWAY_POLICY_TABLE = "public.model_gateway_policy"


class SupportsAsDict(Protocol):
    """What a `ModelCaller`/`Gateway` needs from a request — not `generation.py`'s own
    `GenerationRequest` type, deliberately: the gateway routes calls, it does not parse the
    Transpiler's own context contract (§5.5 is a different section from §9.4, and a second
    task class's request will not share `GenerationRequest`'s shape)."""

    def as_dict(self) -> dict[str, Any]: ...


@dataclass(frozen=True, slots=True)
class RawModelResponse:
    """What a model call returns: the candidate itself (checked against the request's own
    `output_schema` by the caller of the gateway) plus the out-of-band call metadata §4.2's
    own `model_call` block needs."""

    raw: Mapping[str, Any]
    gateway_request_id: str
    provider: str
    model: str
    prompt_hash: str
    temperature: float
    tokens_in: int
    tokens_out: int


class ModelCaller(Protocol):
    """One provider's own seam — what `AnthropicModelCaller`, a future
    `AzureOpenAIModelCaller`, and test doubles each implement."""

    provider: str
    model: str

    async def generate(
        self, request: SupportsAsDict, *, previous_error: str | None
    ) -> RawModelResponse: ...


class Gateway(Protocol):
    """What the Transpiler actually holds — `gateway.generate(task_class=..., ...)`, the
    AC's own literal call shape. Never a provider name."""

    async def generate(
        self, *, task_class: TaskClass, request: SupportsAsDict, previous_error: str | None
    ) -> RawModelResponse: ...


class GatewayRoutingError(Exception):
    """No configured provider is routable for `task_class` — every provider either has no
    eval row at all (never configured) or its latest pass rate is below
    `ROUTABLE_THRESHOLD`. Raised instead of silently substituting a provider that has not
    earned the accuracy bar; the caller (`generation.py`'s own ladder) treats this as an
    immediate, non-retryable escalation, the same footing a schema failure already has."""

    def __init__(self, task_class: TaskClass, *, considered: tuple[str, ...]) -> None:
        self.task_class = task_class
        self.considered = considered
        detail = (
            f"no routable provider for task_class {task_class!r}"
            + (f" (considered: {', '.join(considered)}, none met the eval bar)" if considered else " (no provider has ever been eval-scored for this task class)")
        )
        super().__init__(detail)


# --------------------------------------------------------------------------- tenant policy


@dataclass(frozen=True, slots=True)
class PolicyEntry:
    provider: str
    model: str
    pass_rate: float
    total_cases: int
    passed_cases: int
    updated_at: str
    updated_by: str
    routable: bool

    def as_dict(self) -> dict[str, Any]:
        return {
            "provider": self.provider,
            "model": self.model,
            "pass_rate": self.pass_rate,
            "total_cases": self.total_cases,
            "passed_cases": self.passed_cases,
            "updated_at": self.updated_at,
            "updated_by": self.updated_by,
            "routable": self.routable,
        }


class GatewayPolicyStore(Protocol):
    async def record_eval(
        self, *, task_class: TaskClass, report: EvalReport, updated_by: str
    ) -> None: ...

    async def routable_providers(self, task_class: TaskClass) -> tuple[str, ...]: ...

    async def policy_for(self, task_class: TaskClass) -> tuple[PolicyEntry, ...]: ...


class PostgresGatewayPolicyStore:
    """Append-only, the same "an edit is a new version" footing `conformance_ruleset`
    (S4.3.2) already established: every eval run inserts a new row rather than overwriting
    the provider's last one, so a full history survives and "routable" is always derived
    from the *latest* row per `(graph, task_class, provider)`."""

    def __init__(self, pool: asyncpg.Pool, *, graph_name: str) -> None:
        self._pool = pool
        self._graph = graph_name

    async def record_eval(
        self, *, task_class: TaskClass, report: EvalReport, updated_by: str
    ) -> None:
        async with self._pool.acquire() as conn:
            await conn.execute(
                f"""INSERT INTO {GATEWAY_POLICY_TABLE}
                 (id, graph, task_class, provider, model, total_cases, passed_cases,
                  pass_rate, case_results, updated_by, updated_at)
                 VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9::jsonb, $10, now())""",
                f"policy_{new_ulid()}",
                self._graph,
                task_class,
                report.provider,
                report.model,
                report.total,
                report.passed,
                report.pass_rate,
                json.dumps([r.as_dict() for r in report.results]),
                updated_by,
            )

    async def _latest_per_provider(self, task_class: TaskClass) -> list[asyncpg.Record]:
        async with self._pool.acquire() as conn:
            return list(
                await conn.fetch(
                    f"""SELECT DISTINCT ON (provider) provider, model, pass_rate,
                     total_cases, passed_cases, updated_at, updated_by
                     FROM {GATEWAY_POLICY_TABLE}
                     WHERE graph = $1 AND task_class = $2
                     ORDER BY provider, updated_at DESC""",
                    self._graph, task_class,
                )
            )

    async def routable_providers(self, task_class: TaskClass) -> tuple[str, ...]:
        rows = await self._latest_per_provider(task_class)
        return tuple(sorted(r["provider"] for r in rows if r["pass_rate"] >= ROUTABLE_THRESHOLD))

    async def policy_for(self, task_class: TaskClass) -> tuple[PolicyEntry, ...]:
        rows = await self._latest_per_provider(task_class)
        return tuple(
            PolicyEntry(
                provider=r["provider"], model=r["model"], pass_rate=r["pass_rate"],
                total_cases=r["total_cases"], passed_cases=r["passed_cases"],
                updated_at=r["updated_at"].isoformat(), updated_by=r["updated_by"],
                routable=r["pass_rate"] >= ROUTABLE_THRESHOLD,
            )
            for r in sorted(rows, key=lambda r: r["provider"])
        )


class NullGatewayPolicyStore:
    """No provider has ever been eval-scored — the honest starting state before any
    deployment has run `run_eval_set` even once. Used as `GenerationEngine`'s own default
    so a deployment with no gateway wired yet fails the way "no routable provider" already
    fails (`GatewayRoutingError`), rather than needing a live Postgres connection just to
    report that nothing is configured."""

    async def record_eval(self, *, task_class: TaskClass, report: EvalReport, updated_by: str) -> None:
        raise NotImplementedError(
            "NullGatewayPolicyStore cannot record an eval run -- wire a "
            "PostgresGatewayPolicyStore (via build_gateway) first"
        )

    async def routable_providers(self, task_class: TaskClass) -> tuple[str, ...]:
        return ()

    async def policy_for(self, task_class: TaskClass) -> tuple[PolicyEntry, ...]:
        return ()


# ------------------------------------------------------------------------------- the router


class ModelGateway:
    """The real router (§5.5): "routes by task class and tenant policy" — reads which
    providers are routable for `task_class` from `policy_store`, picks the first (in a
    fixed, deterministic — alphabetical — order among routable candidates; real cost-tier
    ranking is §5.5's own TokenOps budget/cost half, not built here, disclosed rather than
    faked with invented cost numbers), and calls it for real. Never fabricates a response of
    its own: with no routable, registered provider it raises `GatewayRoutingError`."""

    def __init__(self, *, providers: Mapping[str, ModelCaller], policy_store: GatewayPolicyStore) -> None:
        self._providers = dict(providers)
        self._policy = policy_store

    @property
    def providers(self) -> Mapping[str, ModelCaller]:
        return self._providers

    @property
    def policy_store(self) -> GatewayPolicyStore:
        return self._policy

    async def generate(
        self, *, task_class: TaskClass, request: SupportsAsDict, previous_error: str | None
    ) -> RawModelResponse:
        routable = await self._policy.routable_providers(task_class)
        candidates = [name for name in routable if name in self._providers]
        if not candidates:
            raise GatewayRoutingError(task_class, considered=routable)
        caller = self._providers[candidates[0]]
        return await caller.generate(request, previous_error=previous_error)


class StaticGateway:
    """Always routes to one fixed `ModelCaller`, ignoring `task_class` and skipping the
    policy check entirely. No real deployment should use this -- it bypasses the eval gate
    §5.5/the AC itself requires -- but it is the natural, honest test double for exercising
    the ladder against a scripted caller without needing a live `GatewayPolicyStore`."""

    def __init__(self, caller: ModelCaller) -> None:
        self._caller = caller

    async def generate(
        self, *, task_class: TaskClass, request: SupportsAsDict, previous_error: str | None
    ) -> RawModelResponse:
        return await self._caller.generate(request, previous_error=previous_error)


# --------------------------------------------------------------------------- the eval set


@dataclass(frozen=True, slots=True)
class EvalCase:
    name: str
    request: SupportsAsDict
    grade: Callable[[RawModelResponse], tuple[bool, str]]
    """Grades one real response. Returns `(passed, detail)` — `detail` is kept even on a
    pass, so a report is legible without re-running anything."""


@dataclass(frozen=True, slots=True)
class EvalCaseResult:
    name: str
    passed: bool
    detail: str

    def as_dict(self) -> dict[str, Any]:
        return {"name": self.name, "passed": self.passed, "detail": self.detail}


@dataclass(frozen=True, slots=True)
class EvalReport:
    provider: str
    model: str
    task_class: TaskClass
    total: int
    passed: int
    pass_rate: float
    ran_at: str
    results: tuple[EvalCaseResult, ...]

    def as_dict(self) -> dict[str, Any]:
        return {
            "provider": self.provider,
            "model": self.model,
            "task_class": self.task_class,
            "total": self.total,
            "passed": self.passed,
            "pass_rate": self.pass_rate,
            "routable": self.pass_rate >= ROUTABLE_THRESHOLD,
            "ran_at": self.ran_at,
            "results": [r.as_dict() for r in self.results],
        }


async def run_eval_set(
    caller: ModelCaller, *, task_class: TaskClass, cases: Sequence[EvalCase]
) -> EvalReport:
    """Calls `caller` for real, once per case, no retry — "first-pass" is the point: this
    grades what a single, unregenerated attempt produces, the same footing the Transpiler's
    own rung 1/2 checks already give a first attempt before any regeneration loop begins
    (`generation.py`'s own `_run_ladder`). A real network/auth/rate-limit failure counts as
    a failed case rather than aborting the whole run — an eval set has to survive one bad
    call to report honestly on the rest."""
    results: list[EvalCaseResult] = []
    for case in cases:
        try:
            response = await caller.generate(case.request, previous_error=None)
            passed, detail = case.grade(response)
        except Exception as exc:
            passed, detail = False, f"{type(exc).__name__}: {exc}"
        results.append(EvalCaseResult(name=case.name, passed=passed, detail=detail))

    total = len(results)
    passed_count = sum(1 for r in results if r.passed)
    return EvalReport(
        provider=caller.provider, model=caller.model, task_class=task_class,
        total=total, passed=passed_count,
        pass_rate=(passed_count / total) if total else 0.0,
        ran_at=datetime.now(UTC).isoformat(), results=tuple(results),
    )


# ---------------------------------------------------------------------- the Anthropic caller

_SYSTEM_PROMPT = (
    "You are the Transpiler's reasoning tier, translating one Tableau calculation into "
    "the target language the request names. Follow every listed constraint exactly. "
    "Respond with a single JSON object matching the declared output_schema and nothing "
    "else -- no prose outside the object's own fields."
)

DEFAULT_ANTHROPIC_MODEL = "claude-sonnet-5"


def _build_prompt(payload: Mapping[str, Any], previous_error: str | None) -> str:
    lines = [f"{key}: {json.dumps(value, sort_keys=True)}" for key, value in payload.items()]
    if previous_error:
        lines.append(f"previous_attempt_error (fix this): {previous_error}")
    return "\n".join(lines)


def _json_schema_type(kind: str) -> dict[str, Any]:
    nullable = kind.endswith("|null")
    base = kind[: -len("|null")] if nullable else kind
    if base.startswith("[") and base.endswith("]"):
        schema: dict[str, Any] = {"type": "array", "items": _json_schema_type(base[1:-1])}
    elif base == "number":
        schema = {"type": "number"}
    elif base == "boolean":
        schema = {"type": "boolean"}
    else:
        schema = {"type": "string"}  # "string" and any unrecognised primitive name alike
    return {"anyOf": [schema, {"type": "null"}]} if nullable else schema


def _json_schema_from_output_schema(output_schema: Mapping[str, str]) -> dict[str, Any]:
    """Derives a real JSON Schema from whatever `output_schema` the request itself
    declares (§9.4's own field) — this module stays task-class-agnostic by deferring to
    the request's own contract rather than hardcoding the Transpiler's DAX/M shape."""
    properties = {key: _json_schema_type(kind) for key, kind in output_schema.items()}
    required = [key for key, kind in output_schema.items() if not kind.endswith("|null")]
    return {
        "type": "object", "properties": properties, "required": required,
        "additionalProperties": False,
    }


def _extract_json(response: anthropic.types.Message) -> dict[str, Any]:
    for block in response.content:
        if getattr(block, "type", None) == "text":
            text = block.text  # type: ignore[union-attr]
            try:
                parsed: Any = json.loads(text)
            except json.JSONDecodeError:
                return {"_unparseable_response": text}
            return parsed if isinstance(parsed, dict) else {"_unparseable_response": text}
    return {"_unparseable_response": "no text content block in the response"}


class AnthropicModelCaller:
    """A real `ModelCaller`: genuine calls to the Anthropic Messages API, using this SDK's
    own structured-output feature (`output_config.format`, a JSON-schema-constrained
    response) instead of prompt-engineered JSON and a hopeful regex. See the module
    docstring's own "Temperature" section for why `temperature=0.0` on every response is a
    declared policy, not an echoed request parameter."""

    provider = "anthropic"

    def __init__(self, *, credentials: CredentialProvider, model: str = DEFAULT_ANTHROPIC_MODEL) -> None:
        self._credentials = credentials
        self.model = model

    async def generate(
        self, request: SupportsAsDict, *, previous_error: str | None
    ) -> RawModelResponse:
        credential = await self._credentials.resolve("anthropic/api_key")
        client = anthropic.AsyncAnthropic(api_key=credential.secret())

        payload = request.as_dict()
        prompt = _build_prompt(payload, previous_error)
        response_schema = _json_schema_from_output_schema(payload.get("output_schema") or {})

        response = await client.messages.create(
            model=self.model,
            max_tokens=2000,
            system=_SYSTEM_PROMPT,
            messages=[{"role": "user", "content": prompt}],
            output_config={
                "effort": "high",
                "format": {"type": "json_schema", "schema": response_schema},
            },
        )

        return RawModelResponse(
            raw=_extract_json(response),
            gateway_request_id=response.id,
            provider=self.provider,
            model=self.model,
            prompt_hash=context_hash(prompt.encode("utf-8")),
            temperature=0.0,
            tokens_in=response.usage.input_tokens,
            tokens_out=response.usage.output_tokens,
        )


class _NoGateway:
    """The honest default before anything is wired: every call raises
    `GatewayRoutingError`, the same failure a real, empty policy store already produces --
    this just skips needing a `Settings`/`asyncpg.Pool` to construct one."""

    async def generate(
        self, *, task_class: TaskClass, request: SupportsAsDict, previous_error: str | None
    ) -> RawModelResponse:
        raise GatewayRoutingError(task_class, considered=())


def null_gateway() -> Gateway:
    return _NoGateway()


def build_gateway(
    config: Settings, *, pool: asyncpg.Pool, graph_name: str, credentials: CredentialProvider
) -> ModelGateway:
    """The real wiring `main.py` uses: `anthropic` is the only registered provider (this
    story's own explicit scope decision — real Anthropic integration, Azure OpenAI not
    wired). Registering a provider here does not make it routable; a platform engineer
    still has to run `POST /v1/model-gateway:run-eval` and clear `ROUTABLE_THRESHOLD`
    before the gateway will ever pick it."""
    providers: dict[str, ModelCaller] = {
        "anthropic": AnthropicModelCaller(credentials=credentials, model=config.anthropic_model),
    }
    return ModelGateway(
        providers=providers, policy_store=PostgresGatewayPolicyStore(pool, graph_name=graph_name)
    )


__all__ = [
    "DEFAULT_ANTHROPIC_MODEL",
    "GATEWAY_POLICY_TABLE",
    "ROUTABLE_THRESHOLD",
    "TRANSPILE_C3",
    "AnthropicModelCaller",
    "EvalCase",
    "EvalCaseResult",
    "EvalReport",
    "Gateway",
    "GatewayPolicyStore",
    "GatewayRoutingError",
    "ModelCaller",
    "ModelGateway",
    "NullGatewayPolicyStore",
    "PolicyEntry",
    "PostgresGatewayPolicyStore",
    "RawModelResponse",
    "StaticGateway",
    "SupportsAsDict",
    "TaskClass",
    "build_gateway",
    "null_gateway",
    "run_eval_set",
]
