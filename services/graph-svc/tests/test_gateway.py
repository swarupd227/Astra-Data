"""The Model Gateway's own routing and eval-set logic — story S5.3.2.

    "Transpiler calls gateway.generate(task_class='transpile_c3', ...) and never names a
    provider. Routing is by task class and tenant policy; both configured providers pass
    the Transpiler eval set at >= 0.80 first-pass proof before being routable for
    transpile_c3."

Pure logic against an in-memory `GatewayPolicyStore` test double -- no Postgres, no real
Anthropic call. What only a real store/real API can answer (append-only history read back,
a real Anthropic structured-output round trip) is integration-only
(`test_integration_gateway.py`).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import pytest

from astra_graph.gateway import (
    ROUTABLE_THRESHOLD,
    TRANSPILE_C3,
    EvalCase,
    EvalReport,
    GatewayRoutingError,
    ModelCaller,
    ModelGateway,
    NullGatewayPolicyStore,
    PolicyEntry,
    RawModelResponse,
    StaticGateway,
    _json_schema_from_output_schema,
    null_gateway,
    run_eval_set,
)


class _Request:
    def __init__(self, output_schema: dict[str, str] | None = None) -> None:
        self._output_schema = output_schema or {}

    def as_dict(self) -> dict[str, Any]:
        return {"output_schema": self._output_schema}


@dataclass
class _StubCaller:
    provider: str
    model: str = "stub-model"
    raw: dict[str, Any] = field(default_factory=lambda: {"dax": "ok", "confidence": 0.9, "notes": "n", "m": None, "assumptions": []})
    fail: bool = False

    async def generate(self, request: Any, *, previous_error: str | None) -> RawModelResponse:
        if self.fail:
            raise RuntimeError("stub provider failure")
        return RawModelResponse(
            raw=self.raw, gateway_request_id="gwreq_1", provider=self.provider, model=self.model,
            prompt_hash="hash", temperature=0.0, tokens_in=1, tokens_out=1,
        )


@dataclass
class _InMemoryPolicyStore:
    """A fake `GatewayPolicyStore` -- an explicit `{(task_class, provider): pass_rate}` map,
    no Postgres."""

    scores: dict[tuple[str, str], float] = field(default_factory=dict)
    recorded: list[tuple[str, EvalReport]] = field(default_factory=list)

    async def record_eval(self, *, task_class: str, report: EvalReport, updated_by: str) -> None:
        self.scores[(task_class, report.provider)] = report.pass_rate
        self.recorded.append((task_class, report))

    async def routable_providers(self, task_class: str) -> tuple[str, ...]:
        return tuple(
            sorted(p for (tc, p), rate in self.scores.items() if tc == task_class and rate >= ROUTABLE_THRESHOLD)
        )

    async def policy_for(self, task_class: str) -> tuple[PolicyEntry, ...]:
        return tuple(
            PolicyEntry(
                provider=p, model="stub-model", pass_rate=rate, total_cases=5,
                passed_cases=round(rate * 5), updated_at="2027-01-01T00:00:00+00:00",
                updated_by="test", routable=rate >= ROUTABLE_THRESHOLD,
            )
            for (tc, p), rate in self.scores.items() if tc == task_class
        )


# --------------------------------------------------------------------------------- ModelGateway


@pytest.mark.asyncio
async def test_gateway_routes_to_a_routable_provider() -> None:
    policy = _InMemoryPolicyStore(scores={(TRANSPILE_C3, "anthropic"): 0.90})
    caller = _StubCaller(provider="anthropic")
    gateway = ModelGateway(providers={"anthropic": caller}, policy_store=policy)
    response = await gateway.generate(task_class=TRANSPILE_C3, request=_Request(), previous_error=None)
    assert response.provider == "anthropic"


@pytest.mark.asyncio
async def test_gateway_never_routes_a_provider_below_the_threshold() -> None:
    policy = _InMemoryPolicyStore(scores={(TRANSPILE_C3, "anthropic"): 0.79})
    gateway = ModelGateway(providers={"anthropic": _StubCaller(provider="anthropic")}, policy_store=policy)
    with pytest.raises(GatewayRoutingError):
        await gateway.generate(task_class=TRANSPILE_C3, request=_Request(), previous_error=None)


@pytest.mark.asyncio
async def test_gateway_boundary_exactly_at_threshold_is_routable() -> None:
    policy = _InMemoryPolicyStore(scores={(TRANSPILE_C3, "anthropic"): ROUTABLE_THRESHOLD})
    gateway = ModelGateway(providers={"anthropic": _StubCaller(provider="anthropic")}, policy_store=policy)
    response = await gateway.generate(task_class=TRANSPILE_C3, request=_Request(), previous_error=None)
    assert response.provider == "anthropic"


@pytest.mark.asyncio
async def test_gateway_raises_when_no_provider_has_ever_been_scored() -> None:
    gateway = ModelGateway(providers={"anthropic": _StubCaller(provider="anthropic")}, policy_store=_InMemoryPolicyStore())
    with pytest.raises(GatewayRoutingError) as exc_info:
        await gateway.generate(task_class=TRANSPILE_C3, request=_Request(), previous_error=None)
    assert TRANSPILE_C3 in str(exc_info.value)


@pytest.mark.asyncio
async def test_gateway_ignores_a_routable_score_for_an_unregistered_provider() -> None:
    # The policy store remembers a provider that was later removed from the deployed
    # provider map -- the gateway must not try to call something that no longer exists.
    policy = _InMemoryPolicyStore(scores={(TRANSPILE_C3, "retired_provider"): 0.95})
    gateway = ModelGateway(providers={}, policy_store=policy)
    with pytest.raises(GatewayRoutingError):
        await gateway.generate(task_class=TRANSPILE_C3, request=_Request(), previous_error=None)


@pytest.mark.asyncio
async def test_gateway_picks_the_alphabetically_first_routable_candidate() -> None:
    policy = _InMemoryPolicyStore(scores={(TRANSPILE_C3, "zeta"): 0.90, (TRANSPILE_C3, "alpha"): 0.90})
    gateway = ModelGateway(
        providers={"zeta": _StubCaller(provider="zeta"), "alpha": _StubCaller(provider="alpha")},
        policy_store=policy,
    )
    response = await gateway.generate(task_class=TRANSPILE_C3, request=_Request(), previous_error=None)
    assert response.provider == "alpha"


# -------------------------------------------------------------------------- null / static gateways


@pytest.mark.asyncio
async def test_null_gateway_always_raises_routing_error() -> None:
    with pytest.raises(GatewayRoutingError):
        await null_gateway().generate(task_class=TRANSPILE_C3, request=_Request(), previous_error=None)


@pytest.mark.asyncio
async def test_null_gateway_policy_store_reports_nothing_configured() -> None:
    store = NullGatewayPolicyStore()
    assert await store.routable_providers(TRANSPILE_C3) == ()
    assert await store.policy_for(TRANSPILE_C3) == ()


@pytest.mark.asyncio
async def test_static_gateway_ignores_task_class_and_skips_the_policy_check() -> None:
    caller = _StubCaller(provider="test_provider")
    gateway = StaticGateway(caller)
    response = await gateway.generate(task_class="anything_at_all", request=_Request(), previous_error=None)
    assert response.provider == "test_provider"


# ------------------------------------------------------------------------------------- run_eval_set


@pytest.mark.asyncio
async def test_run_eval_set_computes_a_real_pass_rate() -> None:
    caller: ModelCaller = _StubCaller(provider="anthropic")

    def _grade(response: RawModelResponse) -> tuple[bool, str]:
        return response.raw.get("dax") == "ok", "checked"

    cases = tuple(EvalCase(name=f"case_{i}", request=_Request(), grade=_grade) for i in range(4))
    report = await run_eval_set(caller, task_class=TRANSPILE_C3, cases=cases)
    assert report.total == 4
    assert report.passed == 4
    assert report.pass_rate == 1.0
    assert report.provider == "anthropic"


@pytest.mark.asyncio
async def test_run_eval_set_counts_a_mixed_grade() -> None:
    caller: ModelCaller = _StubCaller(provider="anthropic", raw={"dax": "bad", "confidence": 0.1, "notes": "n", "m": None, "assumptions": []})

    def _grade(response: RawModelResponse) -> tuple[bool, str]:
        return response.raw.get("dax") == "ok", "expected ok"

    cases = (EvalCase(name="a", request=_Request(), grade=_grade), EvalCase(name="b", request=_Request(), grade=_grade))
    report = await run_eval_set(caller, task_class=TRANSPILE_C3, cases=cases)
    assert report.total == 2
    assert report.passed == 0
    assert report.pass_rate == 0.0
    assert not report.as_dict()["routable"]


@pytest.mark.asyncio
async def test_run_eval_set_counts_a_provider_exception_as_a_failed_case() -> None:
    caller: ModelCaller = _StubCaller(provider="anthropic", fail=True)
    cases = (EvalCase(name="a", request=_Request(), grade=lambda r: (True, "n/a")),)
    report = await run_eval_set(caller, task_class=TRANSPILE_C3, cases=cases)
    assert report.total == 1
    assert report.passed == 0
    assert "RuntimeError" in report.results[0].detail


@pytest.mark.asyncio
async def test_run_eval_set_of_zero_cases_has_a_zero_pass_rate_not_a_division_error() -> None:
    caller: ModelCaller = _StubCaller(provider="anthropic")
    report = await run_eval_set(caller, task_class=TRANSPILE_C3, cases=())
    assert report.total == 0
    assert report.pass_rate == 0.0


# --------------------------------------------------------------------- JSON schema derivation


def test_json_schema_from_output_schema_marks_non_nullable_fields_required() -> None:
    schema = _json_schema_from_output_schema({"dax": "string", "m": "string|null", "confidence": "number"})
    assert schema["required"] == ["dax", "confidence"]
    assert schema["properties"]["dax"] == {"type": "string"}
    assert schema["properties"]["confidence"] == {"type": "number"}


def test_json_schema_from_output_schema_handles_nullable_and_array_types() -> None:
    schema = _json_schema_from_output_schema({"m": "string|null", "assumptions": "[string]"})
    assert schema["properties"]["m"] == {"anyOf": [{"type": "string"}, {"type": "null"}]}
    assert schema["properties"]["assumptions"] == {"type": "array", "items": {"type": "string"}}
    assert schema["additionalProperties"] is False
