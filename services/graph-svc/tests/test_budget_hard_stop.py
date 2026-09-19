"""The gateway's per-MU token-budget hard stop -- story S12.2.2.

`ModelGateway` asks a `BudgetGuard` before each call attributed to an MU and refuses the
call once the MU is at its limit. These tests use a scripted guard, so every decision the
gateway makes is covered without a database; `test_integration_budget_hard_stop.py`
runs the real `BudgetMonitor` and the real log against PostgreSQL.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import pytest

from astra_graph.gateway import (
    MENDER_REPAIR,
    TRANSPILE_C3,
    GatewayBudgetError,
    GatewayRoutingError,
    InMemoryGatewayRequestLogStore,
    ModelGateway,
    RawModelResponse,
)
from astra_graph.generation import _run_ladder
from astra_graph.mender import call_model_repair
from astra_graph.token_budget import TokenBudgetStatus

from .test_gateway import _InMemoryPolicyStore
from .test_generation import _REQUEST
from .test_mender import _CONTEXT


def _status(consumed: int, limit: int = 100) -> TokenBudgetStatus:
    percent = consumed / limit * 100
    return TokenBudgetStatus(
        tokens_limit=limit, tokens_consumed=consumed, cost_usd=0.0, percent_used=percent,
        is_exhausted=consumed >= limit, is_warning=80 <= percent < 100,
    )


@dataclass
class _ScriptedGuard:
    """Answers each `check` from a script; the last answer repeats."""

    answers: list[TokenBudgetStatus]
    seen: list[tuple[str, str | None]] = field(default_factory=list)
    fail_after_first: bool = False

    async def check(self, workbook_id: str, *, principal: str | None = None) -> TokenBudgetStatus:
        self.seen.append((workbook_id, principal))
        if self.fail_after_first and len(self.seen) > 1:
            raise RuntimeError("budget store unreachable")
        i = min(len(self.seen) - 1, len(self.answers) - 1)
        return self.answers[i]


@dataclass
class _CountingCaller:
    provider: str = "anthropic"
    model: str = "stub-model"
    calls: int = 0

    async def generate(self, request: Any, *, previous_error: str | None) -> RawModelResponse:
        self.calls += 1
        return RawModelResponse(
            raw={"dax": "1"}, gateway_request_id="gwreq_1", provider=self.provider,
            model=self.model, prompt_hash="h", context_hash="c", temperature=0.0,
            tokens_in=1, tokens_out=1, latency_ms=0.0, prompt_template_version="test",
        )


class _Request:
    def as_dict(self) -> dict[str, Any]:
        return {"output_schema": {}}


def _gateway(guard: _ScriptedGuard | None, *, routable: bool = True, log=None):
    scores = {(TRANSPILE_C3, "anthropic"): 0.9, (MENDER_REPAIR, "anthropic"): 0.9} if routable else {}
    caller = _CountingCaller()
    gateway = ModelGateway(
        providers={"anthropic": caller}, policy_store=_InMemoryPolicyStore(scores=scores),
        log_store=log, budget_guard=guard,
    )
    return gateway, caller


async def _generate(gateway, **kwargs):
    return await gateway.generate(
        task_class=TRANSPILE_C3, request=_Request(), previous_error=None, **kwargs
    )


# ------------------------------------------------------------------ the hard stop


async def test_an_exhausted_mu_is_refused_and_the_provider_is_never_called() -> None:
    log = InMemoryGatewayRequestLogStore()
    gateway, caller = _gateway(_ScriptedGuard([_status(100)]), log=log)

    with pytest.raises(GatewayBudgetError) as raised:
        await _generate(gateway, workbook_id="wb-1")

    assert caller.calls == 0
    assert log.requests == []  # nothing was sent, so nothing is logged
    assert raised.value.workbook_id == "wb-1"
    assert (raised.value.tokens_consumed, raised.value.tokens_limit) == (100, 100)
    assert "budget exhausted" in str(raised.value)


async def test_a_call_over_the_limit_is_refused_not_only_one_exactly_at_it() -> None:
    gateway, caller = _gateway(_ScriptedGuard([_status(250)]))

    with pytest.raises(GatewayBudgetError):
        await _generate(gateway, workbook_id="wb-1")
    assert caller.calls == 0


async def test_an_mu_under_its_limit_is_served_and_checked_before_and_after() -> None:
    guard = _ScriptedGuard([_status(50), _status(52)])
    gateway, caller = _gateway(guard)

    response = await _generate(gateway, workbook_id="wb-1", principal="agent:transpiler")

    assert response.raw == {"dax": "1"} and caller.calls == 1
    assert guard.seen == [("wb-1", "agent:transpiler"), ("wb-1", "agent:transpiler")]


async def test_a_call_with_no_mu_in_scope_makes_no_budget_decision() -> None:
    guard = _ScriptedGuard([_status(1_000)])
    gateway, caller = _gateway(guard)

    await _generate(gateway)  # no workbook_id

    assert caller.calls == 1
    assert guard.seen == []


async def test_a_gateway_with_no_guard_makes_no_budget_decision() -> None:
    gateway, caller = _gateway(None)

    await _generate(gateway, workbook_id="wb-1")

    assert caller.calls == 1


async def test_no_routable_provider_is_reported_ahead_of_the_budget() -> None:
    """The more fundamental refusal wins: an exhausted MU with nothing routable is told
    about routing, not budget."""
    guard = _ScriptedGuard([_status(100)])
    gateway, _ = _gateway(guard, routable=False)

    with pytest.raises(GatewayRoutingError) as raised:
        await _generate(gateway, workbook_id="wb-1")

    assert not isinstance(raised.value, GatewayBudgetError)
    assert guard.seen == []


async def test_a_failing_post_call_check_never_costs_the_caller_its_response() -> None:
    """The provider has been paid by then; an alerting fault must not discard the answer."""
    guard = _ScriptedGuard([_status(10), _status(10)], fail_after_first=True)
    gateway, caller = _gateway(guard)

    response = await _generate(gateway, workbook_id="wb-1")

    assert response.raw == {"dax": "1"} and caller.calls == 1


# ------------------------------------- how the two real callers react to the refusal


async def test_the_transpiler_ladder_stops_at_once_instead_of_retrying() -> None:
    gateway, caller = _gateway(_ScriptedGuard([_status(100)]))

    attempts, success = await _run_ladder(_REQUEST, gateway=gateway, workbook_id="wb-1")

    assert success is None
    assert len(attempts) == 1  # a budget is not something a retry can fix
    assert attempts[0].gateway_error is not None
    assert "budget exhausted" in attempts[0].gateway_error
    assert caller.calls == 0


async def test_the_mender_reports_the_model_unavailable_with_the_budget_reason() -> None:
    gateway, caller = _gateway(_ScriptedGuard([_status(100)]))

    dax, result, detail = await call_model_repair(gateway, _CONTEXT, workbook_id="wb-1")

    assert dax is None and result == "MODEL_UNAVAILABLE"
    assert "budget exhausted" in detail["gateway_error"]
    assert caller.calls == 0
