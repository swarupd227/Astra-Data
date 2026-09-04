"""C3 generation's own ladder and request logic — stories S5.3.1 and S5.3.2.

    "Ladder: schema (typed JSON), parse (DAX parser), compile (deploy to dev model or
    EVALUATE syntax check), proof (parity cases), human (only on escalation)... Up to two
    regeneration attempts on parse or compile failure with the error fed back; then the
    field is routed to the Exception Desk with all attempts attached."

Pure logic only, against a scripted `ModelCaller` test double wrapped in `StaticGateway`
(S5.3.2's own "route to one fixed caller, skip the policy check" test seam) --
`FixtureModelCaller`'s own always-`NOT_EXPRESSIBLE` behaviour would never exercise a parse
failure, a successful regeneration, or an exhausted attempt budget. Real graph writes
(`Measure`/`ExceptionCase`/`ProvenanceRecord`) and the real gateway routing/eval-gating
mechanics are integration-only (`test_integration_generation.py`, `test_gateway.py`,
`test_integration_gateway.py`).
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

import pytest
from pydantic import ValidationError

from astra_graph.gateway import GatewayRoutingError, RawModelResponse, StaticGateway, SupportsAsDict
from astra_graph.generation import (
    CONSTRAINTS,
    MAX_ATTEMPTS,
    NOT_EXPRESSIBLE,
    OUTPUT_SCHEMA,
    FixtureModelCaller,
    GenerationRequest,
    ModelResponseSchema,
    _run_ladder,
)

_REQUEST = GenerationRequest(
    task="TRANSLATE_CALC",
    source={"language": "tableau_calc", "formula": "SUM([Sales])", "ast": {}, "class": "C3", "reason": "test"},
    dependency_closure={"fields": [], "calculations": [], "parameters": []},
    sheet_ctx={"rows": [], "cols": [], "marks": [], "filters": [], "sort": [], "partitioning": "unresolved", "addressing": "unresolved"},
    model_ctx={"tables": [], "columns": [], "relationships": [], "existing_measures": [], "note": "n/a"},
    patterns=(),
    charter_excerpt={"note": "n/a"},
    params=(),
    constraints=CONSTRAINTS,
    output_schema=OUTPUT_SCHEMA,
)


@dataclass
class ScriptedModelCaller:
    """Returns one scripted `raw` response per call, in order; records `previous_error`
    seen on each call so a test can assert the ladder actually fed the error back."""

    provider = "test"
    model = "test-model"

    responses: Sequence[dict[str, Any]]
    seen_previous_errors: list[str | None] | None = None

    def __post_init__(self) -> None:
        if self.seen_previous_errors is None:
            self.seen_previous_errors = []
        self._calls = 0

    async def generate(self, request: SupportsAsDict, *, previous_error: str | None) -> RawModelResponse:
        assert self.seen_previous_errors is not None
        self.seen_previous_errors.append(previous_error)
        raw = self.responses[self._calls]
        self._calls += 1
        return RawModelResponse(
            raw=raw, gateway_request_id=f"req_{self._calls}", provider="test", model="test-model",
            prompt_hash="hash", temperature=0.0, tokens_in=10, tokens_out=5,
        )


def _ok(dax: str, confidence: float = 0.9) -> dict[str, Any]:
    return {"dax": dax, "m": None, "assumptions": [], "confidence": confidence, "notes": "ok"}


# ------------------------------------------------------------------------------ GenerationRequest


def test_generation_request_as_dict_round_trips_every_field() -> None:
    d = _REQUEST.as_dict()
    assert d["task"] == "TRANSLATE_CALC"
    assert set(d) == {
        "task", "source", "dependency_closure", "sheet_ctx", "model_ctx",
        "patterns", "charter_excerpt", "params", "constraints", "output_schema",
    }


def test_generation_request_context_hash_is_deterministic() -> None:
    assert _REQUEST.context_hash() == _REQUEST.context_hash()


def test_generation_request_context_hash_changes_with_content() -> None:
    other = GenerationRequest(
        task=_REQUEST.task, source={**_REQUEST.source, "formula": "SUM([Profit])"},
        dependency_closure=_REQUEST.dependency_closure, sheet_ctx=_REQUEST.sheet_ctx,
        model_ctx=_REQUEST.model_ctx, patterns=_REQUEST.patterns,
        charter_excerpt=_REQUEST.charter_excerpt, params=_REQUEST.params,
        constraints=_REQUEST.constraints, output_schema=_REQUEST.output_schema,
    )
    assert other.context_hash() != _REQUEST.context_hash()


# ---------------------------------------------------------------------------- ModelResponseSchema


def test_model_response_schema_rejects_unknown_fields() -> None:
    with pytest.raises(ValidationError):
        ModelResponseSchema.model_validate({**_ok("[Measure] = 1"), "extra": "nope"})


def test_model_response_schema_requires_confidence() -> None:
    with pytest.raises(ValidationError):
        ModelResponseSchema.model_validate({"dax": "[Measure] = 1", "notes": "ok"})


# --------------------------------------------------------------------------------------- fixture caller


@pytest.mark.asyncio
async def test_fixture_model_caller_always_declines_not_expressible() -> None:
    response = await FixtureModelCaller().generate(_REQUEST, previous_error=None)
    assert response.raw["dax"] == NOT_EXPRESSIBLE
    assert response.provider == "none"


# ------------------------------------------------------------------------------------------ the ladder


@pytest.mark.asyncio
async def test_ladder_succeeds_on_first_valid_response() -> None:
    caller = ScriptedModelCaller(responses=[_ok("[Measure] = SUM([Sales])")])
    attempts, success = await _run_ladder(_REQUEST, gateway=StaticGateway(caller))
    assert success is not None
    assert success.dax == "[Measure] = SUM([Sales])"
    assert success.parse_ok and success.compile_ok and success.proof_ok
    assert len(attempts) == 1


@pytest.mark.asyncio
async def test_ladder_schema_failure_is_never_retried() -> None:
    caller = ScriptedModelCaller(responses=[{"dax": "x"}])  # missing required "confidence"
    attempts, success = await _run_ladder(_REQUEST, gateway=StaticGateway(caller))
    assert success is None
    assert len(attempts) == 1
    assert attempts[0].schema_ok is False
    assert caller._calls == 1


@pytest.mark.asyncio
async def test_ladder_not_expressible_is_never_retried() -> None:
    caller = ScriptedModelCaller(responses=[_ok(NOT_EXPRESSIBLE)])
    attempts, success = await _run_ladder(_REQUEST, gateway=StaticGateway(caller))
    assert success is None
    assert len(attempts) == 1
    assert attempts[0].not_expressible is True
    assert caller._calls == 1


@pytest.mark.asyncio
async def test_ladder_regenerates_on_parse_failure_and_feeds_error_back() -> None:
    caller = ScriptedModelCaller(
        responses=[_ok("not balanced ["), _ok("[Measure] = SUM([Sales])")]
    )
    attempts, success = await _run_ladder(_REQUEST, gateway=StaticGateway(caller))
    assert success is not None
    assert len(attempts) == 2
    assert attempts[0].parse_ok is False
    assert success.dax == "[Measure] = SUM([Sales])"
    assert caller.seen_previous_errors == [None, attempts[0].parse_error and f"parse error: {attempts[0].parse_error}"]


@pytest.mark.asyncio
async def test_ladder_exhausts_max_attempts_on_repeated_parse_failure() -> None:
    caller = ScriptedModelCaller(responses=[_ok("not balanced [") for _ in range(MAX_ATTEMPTS)])
    attempts, success = await _run_ladder(_REQUEST, gateway=StaticGateway(caller))
    assert success is None
    assert len(attempts) == MAX_ATTEMPTS
    assert caller._calls == MAX_ATTEMPTS
    assert all(not a.parse_ok for a in attempts)


@pytest.mark.asyncio
async def test_ladder_attempt_as_dict_shape() -> None:
    caller = ScriptedModelCaller(responses=[_ok("[Measure] = SUM([Sales])")])
    attempts, success = await _run_ladder(_REQUEST, gateway=StaticGateway(caller))
    assert success is not None
    d = attempts[0].as_dict()
    assert set(d) == {
        "attempt", "raw_response", "gateway_error", "schema", "not_expressible",
        "not_expressible_reason", "parse", "compile", "proof", "dax", "gateway_request_id",
        "provider", "model", "prompt_hash", "temperature", "tokens_in", "tokens_out", "confidence",
    }


class _NoRouteGateway:
    """A `Gateway` that always reports no routable provider — the S5.3.2 failure mode a
    real `ModelGateway` produces when nothing has cleared the eval bar yet."""

    async def generate(self, *, task_class: str, request: Any, previous_error: str | None) -> RawModelResponse:
        raise GatewayRoutingError(task_class, considered=("anthropic",))


@pytest.mark.asyncio
async def test_ladder_no_routable_provider_is_never_retried() -> None:
    attempts, success = await _run_ladder(_REQUEST, gateway=_NoRouteGateway())
    assert success is None
    assert len(attempts) == 1
    assert attempts[0].gateway_error is not None
    assert "anthropic" in attempts[0].gateway_error
