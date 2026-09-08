"""§11.2/§8.10 the bounded repair loop's own pure logic -- story S8.2.1, continuing
F8.2/E8. Pure dataclasses, the strategy-per-pass table, the config store's own contract,
and `call_model_repair` against a scripted `ModelCaller` wrapped in `gateway.StaticGateway`
(the identical seam `test_generation.py` already established for `generation._run_ladder`).
The graph-coupled half (`assemble_repair_context`, `apply_pattern_repair`, `reprove_cases`,
`check_and_revert_regressions`, `mend_exception` end to end) is integration-only, the same
split every prior E7/E8 story's own test suite already uses.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

import pytest
from pydantic import ValidationError

from astra_graph.classification import FAILURE_CLASSES
from astra_graph.gateway import GatewayRoutingError, RawModelResponse, StaticGateway, SupportsAsDict
from astra_graph.mender import (
    _CLASS_INSTRUCTIONS,
    _ESCALATE_WITHOUT_REPAIR,
    DEFAULT_PASS_BUDGET,
    MAX_PASS_BUDGET_APPLIED,
    OUTPUT_SCHEMA,
    InMemoryMenderConfigStore,
    MenderConfig,
    MenderPassOutcome,
    RepairContext,
    RepairResponseSchema,
    call_model_repair,
    strategy_for_pass,
)

_CONTEXT = RepairContext(
    failure_class="NULL_HANDLING",
    classification_signals={"null_pairs": 3},
    failing_cells=({"row": {"Desk": "FX"}, "expected": None, "candidate": 0.0, "case_ref": "case_1"},),
    filter_ctx={"Region": ["EMEA"]},
    expected_columns=({"name": "VaR", "type": "real"},),
    candidate_columns=({"name": "VaR", "type": "real"},),
    current_dax="SUM([Sales])",
    source_formula="SUM([Sales])",
    source_formula_ast={"op": "SUM"},
    class_instruction=_CLASS_INSTRUCTIONS["NULL_HANDLING"],
    dependency_closure={},
    widened=False,
    output_schema=OUTPUT_SCHEMA,
)


# ------------------------------------------------------------------------ strategy_for_pass


def test_pass_one_is_pattern() -> None:
    assert strategy_for_pass(1) == "PATTERN"


def test_pass_two_is_model() -> None:
    assert strategy_for_pass(2) == "MODEL"


def test_pass_three_is_model_widened() -> None:
    assert strategy_for_pass(3) == "MODEL_WIDENED"


def test_a_pass_number_below_one_still_starts_at_pattern() -> None:
    assert strategy_for_pass(0) == "PATTERN"


def test_a_pass_number_beyond_three_repeats_model_widened() -> None:
    """This module's own docstring: a tenant configuring a budget above
    `MAX_PASS_BUDGET_APPLIED` repeats the last named strategy rather than inventing a
    fourth, undefined one."""
    assert strategy_for_pass(4) == "MODEL_WIDENED"
    assert strategy_for_pass(100) == "MODEL_WIDENED"


# ------------------------------------------------------------------------------ MenderConfig


def test_default_pass_budget_is_three() -> None:
    assert DEFAULT_PASS_BUDGET == 3
    assert MAX_PASS_BUDGET_APPLIED == 3
    assert MenderConfig().pass_budget == DEFAULT_PASS_BUDGET


def test_mender_config_as_dict() -> None:
    assert MenderConfig(pass_budget=5).as_dict() == {"pass_budget": 5}


async def test_in_memory_config_store_starts_at_the_default() -> None:
    store = InMemoryMenderConfigStore()
    assert (await store.latest()).pass_budget == DEFAULT_PASS_BUDGET


async def test_in_memory_config_store_round_trips_a_save() -> None:
    store = InMemoryMenderConfigStore()
    saved = await store.save(MenderConfig(pass_budget=5), updated_by="architect@example.com")
    assert saved.pass_budget == 5
    assert (await store.latest()).pass_budget == 5


# ---------------------------------------------------------------------------- RepairContext


def test_repair_context_as_dict_round_trips_every_field() -> None:
    d = _CONTEXT.as_dict()
    assert d["task"] == "MENDER_REPAIR"
    assert set(d) == {
        "task", "failure_class", "classification_signals", "failing_cells", "filter_ctx",
        "expected_columns", "candidate_columns", "current_dax", "source_formula",
        "source_formula_ast", "class_instruction", "dependency_closure", "widened", "output_schema",
    }
    assert d["failure_class"] == "NULL_HANDLING"
    assert d["widened"] is False


def test_repair_context_hash_is_deterministic() -> None:
    assert _CONTEXT.context_hash() == _CONTEXT.context_hash()


def test_repair_context_hash_changes_with_content() -> None:
    other = RepairContext(
        failure_class=_CONTEXT.failure_class,
        classification_signals=_CONTEXT.classification_signals,
        failing_cells=_CONTEXT.failing_cells,
        filter_ctx=_CONTEXT.filter_ctx,
        expected_columns=_CONTEXT.expected_columns,
        candidate_columns=_CONTEXT.candidate_columns,
        current_dax="SUM([Sales]) * 2",
        source_formula=_CONTEXT.source_formula,
        source_formula_ast=_CONTEXT.source_formula_ast,
        class_instruction=_CONTEXT.class_instruction,
        dependency_closure=_CONTEXT.dependency_closure,
        widened=_CONTEXT.widened,
        output_schema=_CONTEXT.output_schema,
    )
    assert other.context_hash() != _CONTEXT.context_hash()


# ------------------------------------------------------------------------- MenderPassOutcome


def test_mender_pass_outcome_as_dict_round_trips_every_field() -> None:
    outcome = MenderPassOutcome(
        pass_number=2, strategy="MODEL", result="PROVED", pattern_ref=None, measure_id="measure_1",
        cases_reproved=("case_1", "case_2"), cases_still_failing=(), evidence={"note": "ok"},
        started_at="2026-01-01T00:00:00.000Z", finished_at="2026-01-01T00:00:01.000Z",
    )
    d = outcome.as_dict()
    assert d["pass_number"] == 2
    assert d["cases_reproved"] == ["case_1", "case_2"]
    assert d["cases_still_failing"] == []
    assert d["evidence"] == {"note": "ok"}


# --------------------------------------------------------------------- RepairResponseSchema


def test_repair_response_schema_accepts_the_generation_shape() -> None:
    parsed = RepairResponseSchema.model_validate(
        {"dax": "SUM([Sales])", "m": None, "assumptions": [], "confidence": 0.9, "notes": "ok"}
    )
    assert parsed.dax == "SUM([Sales])"
    assert parsed.confidence == 0.9


def test_repair_response_schema_rejects_an_undeclared_field() -> None:
    with pytest.raises(ValidationError):
        RepairResponseSchema.model_validate(
            {"dax": "SUM([Sales])", "m": None, "assumptions": [], "confidence": 0.9, "notes": "ok", "extra": 1}
        )


def test_repair_response_schema_requires_dax_and_confidence() -> None:
    with pytest.raises(ValidationError):
        RepairResponseSchema.model_validate({"m": None, "assumptions": [], "notes": "ok"})


# -------------------------------------------------------------------------- _CLASS_INSTRUCTIONS


def test_every_failure_class_resolves_to_a_non_empty_instruction() -> None:
    """`assemble_repair_context`'s own `_CLASS_INSTRUCTIONS.get(failure_class,
    _CLASS_INSTRUCTIONS["UNKNOWN"])` -- proven directly here since `SOURCE_DRIFT` has no
    entry of its own and genuinely does fall back to the `UNKNOWN` instruction (`SOURCE_
    DRIFT` is not in `_ESCALATE_WITHOUT_REPAIR`, so it does reach a model-repair pass)."""
    for failure_class in FAILURE_CLASSES:
        if failure_class in _ESCALATE_WITHOUT_REPAIR:
            continue
        instruction = _CLASS_INSTRUCTIONS.get(failure_class, _CLASS_INSTRUCTIONS["UNKNOWN"])
        assert instruction, failure_class


def test_key_missing_is_the_only_escalate_without_repair_class() -> None:
    assert set(_ESCALATE_WITHOUT_REPAIR) == {"KEY_MISSING"}


# ------------------------------------------------------------------------------- call_model_repair


@dataclass
class _ScriptedModelCaller:
    """The identical scripted `ModelCaller` test double `test_generation.py` already
    uses for `_run_ladder` -- one `raw` response per call, wrapped in a real
    `StaticGateway` so `call_model_repair` is exercised against the real `Gateway`
    protocol, not a hand-rolled stand-in for it."""

    provider = "test"
    model = "test-model"
    responses: Sequence[dict[str, Any]]

    def __post_init__(self) -> None:
        self._calls = 0

    async def generate(self, request: SupportsAsDict, *, previous_error: str | None) -> RawModelResponse:
        raw = self.responses[self._calls]
        self._calls += 1
        return RawModelResponse(
            raw=raw, gateway_request_id=f"req_{self._calls}", provider="test", model="test-model",
            prompt_hash="hash", temperature=0.0, tokens_in=10, tokens_out=5,
        )


class _RoutingErrorGateway:
    async def generate(self, *, task_class: Any, request: SupportsAsDict, previous_error: str | None) -> RawModelResponse:
        raise GatewayRoutingError(task_class, considered=())


async def test_call_model_repair_ok_path_returns_the_corrected_dax() -> None:
    gateway = StaticGateway(_ScriptedModelCaller(
        responses=[{"dax": "COALESCE([Sales], 0)", "m": None, "assumptions": [], "confidence": 0.85, "notes": "fixed"}],
    ))
    dax, result, detail = await call_model_repair(gateway, _CONTEXT)
    assert result == "OK"
    assert dax == "COALESCE([Sales], 0)"
    assert detail["confidence"] == 0.85
    assert detail["gateway_request_id"] == "req_1"


async def test_call_model_repair_schema_error_when_a_required_field_is_missing() -> None:
    gateway = StaticGateway(_ScriptedModelCaller(
        responses=[{"m": None, "assumptions": [], "notes": "no dax or confidence at all"}],
    ))
    dax, result, detail = await call_model_repair(gateway, _CONTEXT)
    assert result == "SCHEMA_ERROR"
    assert dax is None
    assert "schema_error" in detail


async def test_call_model_repair_parse_error_on_structurally_broken_dax() -> None:
    gateway = StaticGateway(_ScriptedModelCaller(
        responses=[{"dax": "CALCULATE([Sales]", "m": None, "assumptions": [], "confidence": 0.5, "notes": "n"}],
    ))
    dax, result, detail = await call_model_repair(gateway, _CONTEXT)
    assert result == "PARSE_ERROR"
    assert dax is None
    assert "parse_error" in detail


async def test_call_model_repair_model_unavailable_on_a_routing_error() -> None:
    """The genuinely, permanently unroutable posture `MENDER_REPAIR` has in this
    deployment today -- see mender.py's own docstring."""
    dax, result, detail = await call_model_repair(_RoutingErrorGateway(), _CONTEXT)
    assert result == "MODEL_UNAVAILABLE"
    assert dax is None
    assert "gateway_error" in detail
