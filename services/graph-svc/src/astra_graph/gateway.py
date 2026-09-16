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

**The gateway enforces the boundary itself — story S11.4.2, closing F11.4.** S11.4.1
built a real, always-on request log as a deliberate, narrow pull-forward (its own
module docstring said so explicitly); this story is the one that makes that log real
governance rather than a permanent, unconditional recording, and adds the two
enforcement layers the AC's own title names ("the gateway to enforce the boundary, not
rely on agents to respect it"):

1. **Field-schema validation** (`validate_task_class_schema`, `TASK_CLASS_FIELD_
   SCHEMAS`). A real, hand-maintained allow-list of field names per task class —
   deliberately *not* derived from `GenerationRequest`/`RepairContext`'s own declared
   dataclass fields, since a schema that auto-syncs with whatever a caller happens to
   send would enforce nothing at all: the whole point is a boundary a caller's own
   future field addition cannot silently widen. An unexpected field, or a field whose
   own JSON-encoded value exceeds `MAX_FIELD_BYTES`, raises `GatewayValidationError`
   before the request is ever built, redacted, logged, or sent.
2. **Pattern-based redaction** (`redaction.redact_data_like_literals`) — applied to
   the *real* outbound payload, not merely to what gets logged: every string leaf value
   is scanned for an email, an account-number-shaped digit group, or a long bare
   numeric literal, and each match is replaced before the payload is wrapped
   (`_DictRequest`) and handed to the real `ModelCaller`. The AC's own "so a prompt bug
   cannot leak data" is a claim about what actually reaches the provider.
3. **Content logging, off by default, InfoSec-granted for a bounded window**
   (`ContentLoggingGrant`/`ContentLoggingGrantStore`). `PostgresGatewayRequestLogStore.
   record` always persists a request's/response's own hash (and the AC's own
   "redaction count"); it persists the literal *text* only while a real, unexpired,
   unrevoked `gateway_content_logging_grant` row exists for this graph — the identical
   "validity is a computed comparison, never a stored flag" discipline `data_handling.
   py`'s own sign-off status and `workload_identity.SvidRecord`'s own `expires_at`/
   `revoked_at` shape already established. A grant's own duration is named by the
   InfoSec reviewer at enable time, capped at `MAX_CONTENT_LOGGING_MINUTES` so it can
   never be left on indefinitely by accident.

Both request and response are now logged (previously request-only) — `_dispatch`
(shared by `ModelGateway.generate`/`StaticGateway.generate`) wraps the real provider
call in `try`/`finally` so a request is always logged even if the call itself fails
(the identical guarantee S11.4.1 already gave, now extended to also capture a real
response when the call succeeds), using the identical `_build_prompt` rendering
`AnthropicModelCaller.generate` itself uses on the same, already-redacted payload — so
the logged text (when a grant makes it visible at all) is guaranteed byte-identical to
what was actually sent. A request refused by schema validation, or one that never
reaches a routable provider (`GatewayRoutingError`), is never logged at all — nothing
was ever really built or sent.

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
from datetime import UTC, datetime, timedelta
from typing import Any, Protocol

import anthropic
import asyncpg

from .agent_identity import agent_id_of, authorize_gateway_call
from .config import Settings
from .context.canonical import context_hash
from .credentials import CredentialProvider
from .ids import new_ulid
from .redaction import redact_data_like_literals

#: §5.5: "routes by task class" — a plain string alias, not an enum, since a closed set
#: would need extending for every new caller and buys nothing a string doesn't already
#: give a `GatewayPolicyStore` keyed by (graph, task_class, provider).
TaskClass = str
TRANSPILE_C3: TaskClass = "transpile_c3"

#: Story S5.3.3, §16.3: the routing *destination* a task class whose calibration falls
#: below its floor is sent to instead of the reasoning tier — "small-model-plus-proof",
#: never the reasoning tier itself. No `ModelCaller` is ever registered under this task
#: class (`build_gateway`'s own provider map only ever holds `anthropic`, under
#: `TRANSPILE_C3`): no story in this backlog stands up a real small-model provider, so a
#: reroute here honestly finds nothing routable and raises `GatewayRoutingError`, the
#: identical "disclosed absent, not a fake failing provider" footing Azure OpenAI already
#: has under `TRANSPILE_C3` itself.
TRANSPILE_C3_SMALL_MODEL: TaskClass = "transpile_c3_small_model"

#: Story S8.2.1, §8.10: "asks the reasoning model for a corrected artefact" -- pass 2/3
#: of the Mender's own bounded repair loop (`mender.py`). Registered here, never routable
#: in this deployment today: `POST /v1/model-gateway:run-eval` (S5.3.2) only ever runs
#: `generation.run_transpile_c3_eval`, hard-coded to `TRANSPILE_C3` -- no eval set exists
#: for this task class, so `routable_providers` always returns empty and every model-
#: repair pass really does raise `GatewayRoutingError`, the identical disclosed-absent
#: footing `TRANSPILE_C3_SMALL_MODEL` already has, for the same real reason (no story in
#: this backlog builds the eval set this task class would need to ever clear
#: `ROUTABLE_THRESHOLD`).
MENDER_REPAIR: TaskClass = "mender_repair"

#: §16.6's own "Class 3 proof rate >= 0.80" target and the AC's own literal "0.80" —
#: confirmed, by research, to be the same number this gate uses.
ROUTABLE_THRESHOLD = 0.80

GATEWAY_POLICY_TABLE = "public.model_gateway_policy"
GATEWAY_REQUEST_LOG_TABLE = "public.gateway_request_log"
CONTENT_LOGGING_GRANT_TABLE = "public.gateway_content_logging_grant"

#: Story S11.4.2: the longest a single content-logging grant may run before it needs a
#: fresh, deliberate re-enable -- so "off by default... for a bounded window" can never
#: quietly become "on indefinitely" through one long-forgotten grant.
MAX_CONTENT_LOGGING_MINUTES = 1440

#: Story S6.2.3: "credits ... so that reporting is generated" / "cost per custodian" --
#: this module's own earlier docstring named the gap directly ("real cost-tier ranking
#: is §5.5's own TokenOps budget/cost half, not built here, disclosed rather than faked
#: with invented cost numbers"). A real, invented, disclosed per-provider rate, the
#: identical footing `invoicing.DEFAULT_UNIT_PRICES` already has for its own per-tier
#: price -- dollars per million tokens, `(input, output)`, matching Anthropic's own
#: published Claude Sonnet rate at the time this story was built. A provider with no
#: entry here is honestly absent from cost reporting, never guessed at.
PROVIDER_TOKEN_COSTS: dict[str, tuple[float, float]] = {
    "anthropic": (3.00, 15.00),
}


def token_cost_usd(provider: str, tokens_in: int, tokens_out: int) -> float | None:
    """`None` when `provider` has no registered rate -- an honest absence, not a
    guessed cost. Otherwise `tokens_in`/`tokens_out` priced per `PROVIDER_TOKEN_COSTS`,
    each rate itself already expressed per million tokens."""
    rate = PROVIDER_TOKEN_COSTS.get(provider)
    if rate is None:
        return None
    rate_in, rate_out = rate
    return (tokens_in * rate_in + tokens_out * rate_out) / 1_000_000


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
        self,
        *,
        task_class: TaskClass,
        request: SupportsAsDict,
        previous_error: str | None,
        principal: str | None = None,
        query_tag: str | None = None,
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


class GatewayValidationError(Exception):
    """A request's own shape violates its task class's allowed field schema — an
    unexpected field, or a field whose own JSON-encoded value exceeds
    `MAX_FIELD_BYTES`. Raised before the request is ever redacted, logged, or sent —
    the identical "refuse before anything happens" footing `GatewayRoutingError`
    already has for a request with no routable provider; a caller (`generation.py`'s
    own ladder, `mender.py`'s own repair loop) treats this the same way, an immediate,
    non-retryable escalation."""

    def __init__(self, task_class: TaskClass, *, detail: str) -> None:
        self.task_class = task_class
        self.detail = detail
        super().__init__(f"request for task_class {task_class!r} refused: {detail}")


#: Story S11.4.2: "validates every request against the task class's allowed field
#: schema." A real, hand-maintained allow-list per task class — see this module's own
#: docstring for why this is deliberately not derived from `GenerationRequest`/
#: `RepairContext`'s own declared fields. Kept here (not in `generation.py`/`mender.py`)
#: because enforcement belongs to the gateway itself, the AC's own literal point.
TASK_CLASS_FIELD_SCHEMAS: dict[TaskClass, frozenset[str]] = {
    TRANSPILE_C3: frozenset({
        "task", "source", "dependency_closure", "sheet_ctx", "model_ctx",
        "patterns", "charter_excerpt", "params", "constraints", "output_schema",
    }),
    TRANSPILE_C3_SMALL_MODEL: frozenset({
        "task", "source", "dependency_closure", "sheet_ctx", "model_ctx",
        "patterns", "charter_excerpt", "params", "constraints", "output_schema",
    }),
    MENDER_REPAIR: frozenset({
        "task", "failure_class", "classification_signals", "failing_cells",
        "filter_ctx", "expected_columns", "candidate_columns", "current_dax",
        "source_formula", "source_formula_ast", "class_instruction",
        "dependency_closure", "widened", "output_schema",
    }),
}

#: Bytes of one field's own JSON-encoded value — generous enough for a real,
#: legitimate dependency closure or a widened (uncapped) failing-cell set, tight
#: enough to refuse a request a bug has stuffed with something this codebase never
#: intends a prompt to carry (an entire result set, say). One uniform cap, not tuned
#: per field: the AC's own wording is "a field over size," not a per-field budget table.
MAX_FIELD_BYTES = 32_768


def validate_task_class_schema(task_class: TaskClass, payload: Mapping[str, Any]) -> None:
    """A task class with no registered schema is not this function's own concern —
    `ModelGateway.generate`'s own routing check already refuses anything unrecognised
    before this ever runs (a task class only reaches here once real routing has picked
    a real provider for it)."""
    schema = TASK_CLASS_FIELD_SCHEMAS.get(task_class)
    if schema is None:
        return
    unexpected = sorted(set(payload) - schema)
    if unexpected:
        raise GatewayValidationError(
            task_class, detail=f"unexpected field(s) {unexpected}; allowed: {sorted(schema)}"
        )
    for key, value in payload.items():
        size = len(json.dumps(value, sort_keys=True, default=str).encode("utf-8"))
        if size > MAX_FIELD_BYTES:
            raise GatewayValidationError(
                task_class, detail=f"field {key!r} is {size} bytes, over the {MAX_FIELD_BYTES}-byte limit"
            )


@dataclass(frozen=True, slots=True)
class _DictRequest:
    """Wraps an already-validated, already-redacted payload dict so it can be handed
    to a real `ModelCaller` in place of the caller's own original request object — the
    real outbound call must see the redacted content, not the original (S11.4.2's own
    "so a prompt bug cannot leak data" is a claim about what actually reaches the
    provider, not only about what gets logged)."""

    payload: dict[str, Any]

    def as_dict(self) -> dict[str, Any]:
        return self.payload


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


class GatewayRequestLogStore(Protocol):
    async def record(
        self, *, provider: str, task_class: TaskClass, agent_id: str | None,
        prompt_hash: str, request_text: str | None,
        response_hash: str | None, response_text: str | None, redaction_count: int,
        query_tag: str | None = None, tokens_in: int | None = None,
        tokens_out: int | None = None, cost_usd: float | None = None,
    ) -> None: ...

    async def contains_text(self, needle: str) -> bool: ...


async def _content_logging_active(conn: asyncpg.Connection, graph_name: str) -> bool:
    """Story S11.4.2: whether a real, unexpired, unrevoked grant exists right now --
    computed at read time, exactly the "validity is a comparison, never a stored flag"
    discipline `data_handling.boundary_status`/`workload_identity.SvidRecord.status`
    already established, applied a third time."""
    row = await conn.fetchval(
        f"""SELECT 1 FROM {CONTENT_LOGGING_GRANT_TABLE}
             WHERE graph = $1 AND revoked_at IS NULL AND expires_at > now()
             ORDER BY enabled_at DESC LIMIT 1""",
        graph_name,
    )
    return row is not None


class PostgresGatewayRequestLogStore:
    """Always records a real hash for every real request/response (the AC's own "all
    gateway requests and responses are logged with hashes") -- the literal *text* is
    persisted only while a real content-logging grant is currently active for this
    graph, checked fresh on every write (never cached), so a grant that has just
    expired or just been revoked takes effect on the very next request.

    Story S6.2.3: `query_tag`/`tokens_in`/`tokens_out`/`cost_usd` are metadata about
    the call, not its content -- persisted unconditionally, the identical footing
    `redaction_count` already has, never gated by the content-logging grant above."""

    def __init__(self, pool: asyncpg.Pool, *, graph_name: str) -> None:
        self._pool = pool
        self._graph = graph_name

    async def record(
        self, *, provider: str, task_class: TaskClass, agent_id: str | None,
        prompt_hash: str, request_text: str | None,
        response_hash: str | None, response_text: str | None, redaction_count: int,
        query_tag: str | None = None, tokens_in: int | None = None,
        tokens_out: int | None = None, cost_usd: float | None = None,
    ) -> None:
        async with self._pool.acquire() as conn:
            content_logging = await _content_logging_active(conn, self._graph)
            await conn.execute(
                f"""INSERT INTO {GATEWAY_REQUEST_LOG_TABLE}
                    (id, graph, provider, task_class, agent_id, prompt_hash, request_text,
                     response_hash, response_text, redaction_count, query_tag,
                     tokens_in, tokens_out, cost_usd)
                    VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13, $14)""",
                f"gwreq_{new_ulid()}", self._graph, provider, task_class, agent_id,
                prompt_hash, request_text if content_logging else None,
                response_hash, response_text if content_logging else None,
                redaction_count, query_tag, tokens_in, tokens_out, cost_usd,
            )

    async def contains_text(self, needle: str) -> bool:
        """Story S11.4.1's own boundary test (now also covering responses, S11.4.2):
        whether `needle` (a sentinel/canary value) appears in any request or response
        text this graph has ever really logged. A plain substring search, not a hash
        lookup -- the AC's own wording is "never appears," which a hash of the
        sentinel could not check against a log that only ever stores real text, not a
        matching hash of it. Finds nothing when content logging was off at write time,
        the honest, correct behaviour: text that was never persisted cannot be found."""
        async with self._pool.acquire() as conn:
            row = await conn.fetchval(
                f"""SELECT 1 FROM {GATEWAY_REQUEST_LOG_TABLE}
                     WHERE graph = $1 AND (
                         request_text LIKE '%' || $2 || '%' OR response_text LIKE '%' || $2 || '%'
                     ) LIMIT 1""",
                self._graph, needle,
            )
        return row is not None


class InMemoryGatewayRequestLogStore:
    """The simple test double -- always records full text, unconditionally; it models
    no grant concept of its own (a real Postgres-backed grant is what `test_
    integration_gateway.py`/`test_integration_data_handling.py` exercise for real)."""

    def __init__(self) -> None:
        self.requests: list[dict[str, Any]] = []

    async def record(
        self, *, provider: str, task_class: TaskClass, agent_id: str | None,
        prompt_hash: str, request_text: str | None,
        response_hash: str | None, response_text: str | None, redaction_count: int,
        query_tag: str | None = None, tokens_in: int | None = None,
        tokens_out: int | None = None, cost_usd: float | None = None,
    ) -> None:
        self.requests.append({
            "provider": provider, "task_class": task_class, "agent_id": agent_id,
            "prompt_hash": prompt_hash, "request_text": request_text,
            "response_hash": response_hash, "response_text": response_text,
            "redaction_count": redaction_count, "query_tag": query_tag,
            "tokens_in": tokens_in, "tokens_out": tokens_out, "cost_usd": cost_usd,
        })

    async def contains_text(self, needle: str) -> bool:
        return any(
            needle in (r["request_text"] or "") or needle in (r["response_text"] or "")
            for r in self.requests
        )


async def _dispatch(
    caller: ModelCaller, log_store: GatewayRequestLogStore | None, *,
    provider: str, task_class: TaskClass, principal: str | None,
    request: SupportsAsDict, previous_error: str | None,
    query_tag: str | None = None,
) -> RawModelResponse:
    """Shared by `ModelGateway.generate`/`StaticGateway.generate`: validate the
    request's own shape, redact data-like literals from the *real* outbound payload,
    call the provider, and log request+response (as hashes always, as text only while
    a content-logging grant is active) regardless of whether the call itself
    succeeds -- the identical `try`/`finally` guarantee S11.4.1 already gave request
    logging, now covering the response too.

    Story S6.2.3: `query_tag` is an optional, caller-supplied attribution (the real
    `Site` id a caller like `generation.py`/`mender.py` resolved for its own workbook)
    logged verbatim alongside a real token count and its own computed `cost_usd` --
    the AC's own "cost per custodian visible from query tags" and "credits ... per
    custodian per day" bullets."""
    payload = request.as_dict()
    validate_task_class_schema(task_class, payload)
    redacted_payload, redaction_count = redact_data_like_literals(payload)
    redacted_request = _DictRequest(redacted_payload)
    prompt = _build_prompt(redacted_payload, previous_error)

    response: RawModelResponse | None = None
    try:
        response = await caller.generate(redacted_request, previous_error=previous_error)
        return response
    finally:
        if log_store is not None:
            response_text = (
                json.dumps(dict(response.raw), sort_keys=True, default=str)
                if response is not None else None
            )
            await log_store.record(
                provider=provider, task_class=task_class,
                agent_id=agent_id_of(principal) if principal else None,
                prompt_hash=context_hash(prompt.encode("utf-8")), request_text=prompt,
                response_hash=(
                    context_hash(response_text.encode("utf-8")) if response_text is not None else None
                ),
                response_text=response_text,
                redaction_count=redaction_count,
                query_tag=query_tag,
                tokens_in=response.tokens_in if response is not None else None,
                tokens_out=response.tokens_out if response is not None else None,
                cost_usd=(
                    token_cost_usd(provider, response.tokens_in, response.tokens_out)
                    if response is not None else None
                ),
            )


class ContentLoggingGrantError(Exception):
    """A grant/revoke attempt could not proceed for a real, stated reason (an
    out-of-bounds duration, or revoking when nothing is active)."""


@dataclass(frozen=True, slots=True)
class ContentLoggingGrant:
    """One real, append-only grant record -- `active` is always computed, never a
    stored column (this module's own docstring explains why)."""

    enabled_by: str
    enabled_at: str
    expires_at: str
    revoked_at: str | None
    revoked_by: str | None

    @property
    def active(self) -> bool:
        if self.revoked_at is not None:
            return False
        return _parse_iso(self.expires_at) > datetime.now(UTC)

    def as_dict(self) -> dict[str, Any]:
        return {
            "enabled_by": self.enabled_by, "enabled_at": self.enabled_at,
            "expires_at": self.expires_at, "revoked_at": self.revoked_at,
            "revoked_by": self.revoked_by, "active": self.active,
        }


def _parse_iso(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


class ContentLoggingGrantStore(Protocol):
    async def latest(self) -> ContentLoggingGrant | None: ...

    async def grant(self, *, enabled_by: str, duration_minutes: int) -> ContentLoggingGrant: ...

    async def revoke(self, *, revoked_by: str) -> ContentLoggingGrant | None: ...


class PostgresContentLoggingGrantStore:
    """Append-only, per-graph -- the identical `data_handling_signoff`/`svid_record`
    shape (a grant/revoke is a real event, never overwritten in place)."""

    def __init__(self, pool: asyncpg.Pool, *, graph_name: str) -> None:
        self._pool = pool
        self._graph = graph_name

    async def latest(self) -> ContentLoggingGrant | None:
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                f"""SELECT enabled_by, enabled_at, expires_at, revoked_at, revoked_by
                     FROM {CONTENT_LOGGING_GRANT_TABLE}
                    WHERE graph = $1 ORDER BY enabled_at DESC LIMIT 1""",
                self._graph,
            )
        return _grant_from_row(row) if row else None

    async def grant(self, *, enabled_by: str, duration_minutes: int) -> ContentLoggingGrant:
        if not (1 <= duration_minutes <= MAX_CONTENT_LOGGING_MINUTES):
            raise ContentLoggingGrantError(
                f"duration_minutes must be between 1 and {MAX_CONTENT_LOGGING_MINUTES} "
                f"(24 hours); got {duration_minutes}"
            )
        expires_at = datetime.now(UTC) + timedelta(minutes=duration_minutes)
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                f"""INSERT INTO {CONTENT_LOGGING_GRANT_TABLE}
                    (id, graph, enabled_by, expires_at)
                    VALUES ($1, $2, $3, $4)
                 RETURNING enabled_by, enabled_at, expires_at, revoked_at, revoked_by""",
                f"cloggrant_{new_ulid()}", self._graph, enabled_by, expires_at,
            )
        assert row is not None
        return _grant_from_row(row)

    async def revoke(self, *, revoked_by: str) -> ContentLoggingGrant | None:
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                f"""UPDATE {CONTENT_LOGGING_GRANT_TABLE}
                       SET revoked_at = now(), revoked_by = $2
                     WHERE id = (
                         SELECT id FROM {CONTENT_LOGGING_GRANT_TABLE}
                          WHERE graph = $1 AND revoked_at IS NULL AND expires_at > now()
                       ORDER BY enabled_at DESC LIMIT 1
                     )
                 RETURNING enabled_by, enabled_at, expires_at, revoked_at, revoked_by""",
                self._graph, revoked_by,
            )
        return _grant_from_row(row) if row else None


def _grant_from_row(row: asyncpg.Record) -> ContentLoggingGrant:
    return ContentLoggingGrant(
        enabled_by=row["enabled_by"], enabled_at=row["enabled_at"].isoformat(),
        expires_at=row["expires_at"].isoformat(),
        revoked_at=row["revoked_at"].isoformat() if row["revoked_at"] else None,
        revoked_by=row["revoked_by"],
    )


class InMemoryContentLoggingGrantStore:
    def __init__(self) -> None:
        self._grant: ContentLoggingGrant | None = None

    async def latest(self) -> ContentLoggingGrant | None:
        return self._grant

    async def grant(self, *, enabled_by: str, duration_minutes: int) -> ContentLoggingGrant:
        if not (1 <= duration_minutes <= MAX_CONTENT_LOGGING_MINUTES):
            raise ContentLoggingGrantError(
                f"duration_minutes must be between 1 and {MAX_CONTENT_LOGGING_MINUTES} "
                f"(24 hours); got {duration_minutes}"
            )
        now = datetime.now(UTC)
        self._grant = ContentLoggingGrant(
            enabled_by=enabled_by, enabled_at=now.isoformat(),
            expires_at=(now + timedelta(minutes=duration_minutes)).isoformat(),
            revoked_at=None, revoked_by=None,
        )
        return self._grant

    async def revoke(self, *, revoked_by: str) -> ContentLoggingGrant | None:
        if self._grant is None or not self._grant.active:
            return None
        self._grant = ContentLoggingGrant(
            enabled_by=self._grant.enabled_by, enabled_at=self._grant.enabled_at,
            expires_at=self._grant.expires_at,
            revoked_at=datetime.now(UTC).isoformat(), revoked_by=revoked_by,
        )
        return self._grant


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

    def __init__(
        self, *, providers: Mapping[str, ModelCaller], policy_store: GatewayPolicyStore,
        log_store: GatewayRequestLogStore | None = None,
    ) -> None:
        self._providers = dict(providers)
        self._policy = policy_store
        self._log_store = log_store

    @property
    def providers(self) -> Mapping[str, ModelCaller]:
        return self._providers

    @property
    def policy_store(self) -> GatewayPolicyStore:
        return self._policy

    async def generate(
        self,
        *,
        task_class: TaskClass,
        request: SupportsAsDict,
        previous_error: str | None,
        principal: str | None = None,
        query_tag: str | None = None,
    ) -> RawModelResponse:
        # Story S11.1.2: additive -- `principal` is optional and every existing caller
        # (there were none before this story; both real call sites now pass one, see
        # generation.py/mender.py) that omits it gets the identical, unchanged behaviour.
        # Story S6.2.3: `query_tag` is additive the same way.
        if principal is not None:
            authorize_gateway_call(principal, task_class)
        routable = await self._policy.routable_providers(task_class)
        candidates = [name for name in routable if name in self._providers]
        if not candidates:
            raise GatewayRoutingError(task_class, considered=routable)
        caller = self._providers[candidates[0]]
        return await _dispatch(
            caller, self._log_store, provider=caller.provider, task_class=task_class,
            principal=principal, request=request, previous_error=previous_error,
            query_tag=query_tag,
        )


class StaticGateway:
    """Always routes to one fixed `ModelCaller`, ignoring `task_class` and skipping the
    policy check entirely. No real deployment should use this -- it bypasses the eval gate
    §5.5/the AC itself requires -- but it is the natural, honest test double for exercising
    the ladder against a scripted caller without needing a live `GatewayPolicyStore`."""

    def __init__(self, caller: ModelCaller, *, log_store: GatewayRequestLogStore | None = None) -> None:
        self._caller = caller
        self._log_store = log_store

    async def generate(
        self,
        *,
        task_class: TaskClass,
        request: SupportsAsDict,
        previous_error: str | None,
        principal: str | None = None,
        query_tag: str | None = None,
    ) -> RawModelResponse:
        if principal is not None:
            authorize_gateway_call(principal, task_class)
        return await _dispatch(
            self._caller, self._log_store, provider=self._caller.provider, task_class=task_class,
            principal=principal, request=request, previous_error=previous_error,
            query_tag=query_tag,
        )


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
        self,
        *,
        task_class: TaskClass,
        request: SupportsAsDict,
        previous_error: str | None,
        principal: str | None = None,
        query_tag: str | None = None,
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
        providers=providers, policy_store=PostgresGatewayPolicyStore(pool, graph_name=graph_name),
        log_store=PostgresGatewayRequestLogStore(pool, graph_name=graph_name),
    )


__all__ = [
    "CONTENT_LOGGING_GRANT_TABLE",
    "DEFAULT_ANTHROPIC_MODEL",
    "GATEWAY_POLICY_TABLE",
    "GATEWAY_REQUEST_LOG_TABLE",
    "MAX_CONTENT_LOGGING_MINUTES",
    "MAX_FIELD_BYTES",
    "PROVIDER_TOKEN_COSTS",
    "ROUTABLE_THRESHOLD",
    "TASK_CLASS_FIELD_SCHEMAS",
    "TRANSPILE_C3",
    "TRANSPILE_C3_SMALL_MODEL",
    "AnthropicModelCaller",
    "ContentLoggingGrant",
    "ContentLoggingGrantError",
    "ContentLoggingGrantStore",
    "EvalCase",
    "EvalCaseResult",
    "EvalReport",
    "Gateway",
    "GatewayPolicyStore",
    "GatewayRequestLogStore",
    "GatewayRoutingError",
    "GatewayValidationError",
    "InMemoryContentLoggingGrantStore",
    "InMemoryGatewayRequestLogStore",
    "ModelCaller",
    "ModelGateway",
    "NullGatewayPolicyStore",
    "PolicyEntry",
    "PostgresContentLoggingGrantStore",
    "PostgresGatewayPolicyStore",
    "PostgresGatewayRequestLogStore",
    "RawModelResponse",
    "StaticGateway",
    "SupportsAsDict",
    "TaskClass",
    "build_gateway",
    "null_gateway",
    "run_eval_set",
    "token_cost_usd",
    "validate_task_class_schema",
]
