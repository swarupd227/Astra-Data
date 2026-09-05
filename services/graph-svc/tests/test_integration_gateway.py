"""The Model Gateway, against real PostgreSQL — story S5.3.2.

    "Transpiler calls gateway.generate(task_class='transpile_c3', ...) and never names a
    provider. Routing is by task class and tenant policy; both configured providers pass
    the Transpiler eval set at >= 0.80 first-pass proof before being routable for
    transpile_c3. Provider and model are recorded per call in provenance."

What only the real store can answer: that `PostgresGatewayPolicyStore` genuinely persists
and reads back eval history, that "routable" is derived from the *latest* row per
`(graph, task_class, provider)` rather than an average or a first-ever result, and that
`ModelGateway` really refuses to route to a provider below `ROUTABLE_THRESHOLD` -- none of
which the pure-function tests in `test_gateway.py` (an in-memory policy store double) can
see.

A second, narrower set of tests makes a real, live call to the Anthropic API through
`AnthropicModelCaller` -- this story's own explicit scope decision (real integration, not a
disclosed fixture, Anthropic only). These are skipped, the same way every integration test
here already skips when Postgres is unreachable, whenever
`ASTRA_CREDENTIAL_ANTHROPIC_API_KEY` is not set: a real deployment key is not something CI
should require, or spend, on every run.
"""

from __future__ import annotations

import asyncio
import os
import threading
from collections.abc import Callable, Coroutine
from typing import Any

import pytest

pytestmark = pytest.mark.integration

asyncpg = pytest.importorskip("asyncpg")

from astra_graph.config import Settings  # noqa: E402
from astra_graph.credentials import EnvironmentCredentialProvider  # noqa: E402
from astra_graph.gateway import (  # noqa: E402
    ROUTABLE_THRESHOLD,
    TRANSPILE_C3,
    AnthropicModelCaller,
    EvalCase,
    GatewayRoutingError,
    ModelGateway,
    PostgresGatewayPolicyStore,
    RawModelResponse,
    run_eval_set,
)
from astra_graph.generation import TRANSPILE_C3_EVAL_CASES, run_transpile_c3_eval  # noqa: E402
from astra_graph.graph import create_pool  # noqa: E402
from astra_graph.ids import new_ulid  # noqa: E402
from astra_graph.migrations import run as run_migrations  # noqa: E402
from astra_graph.principal import Principal  # noqa: E402

PRINCIPAL = Principal("user:platform@artizent.example")

_HAS_ANTHROPIC_KEY = bool(os.environ.get("ASTRA_CREDENTIAL_ANTHROPIC_API_KEY"))
_requires_anthropic = pytest.mark.skipif(
    not _HAS_ANTHROPIC_KEY,
    reason="ASTRA_CREDENTIAL_ANTHROPIC_API_KEY not set -- a live Anthropic key is a "
    "deployment secret, not something CI should require or spend on every run",
)


def _settings(graph_name: str) -> Settings:
    return Settings(
        postgres_host=os.environ.get("ASTRA_POSTGRES_HOST", "localhost"),
        postgres_port=int(os.environ.get("ASTRA_POSTGRES_PORT", "5432")),
        postgres_db=os.environ.get("ASTRA_POSTGRES_DB", "astra"),
        postgres_user=os.environ.get("ASTRA_POSTGRES_USER", "astra"),
        postgres_password=os.environ.get("ASTRA_POSTGRES_PASSWORD", "astra_local_dev_only"),
        graph_name=graph_name,
        env="test",
        log_level="WARNING",
        pool_min_size=1,
        pool_max_size=6,
        scheduler_enabled=False,
    )


def _run_off_loop(factory: Callable[[], Coroutine[Any, Any, Any]]) -> Any:
    box: dict[str, Any] = {}

    def runner() -> None:
        try:
            box["value"] = asyncio.run(factory())
        except BaseException as exc:
            box["error"] = exc

    thread = threading.Thread(target=runner)
    thread.start()
    thread.join()
    if "error" in box:
        raise box["error"]
    return box.get("value")


@pytest.fixture(scope="module")
def settings() -> Settings:
    # No Apache AGE graph is created here -- unlike every other integration fixture in this
    # suite, PostgresGatewayPolicyStore touches only the platform table
    # `public.model_gateway_policy` (migration v0021); `graph_name` is a scoping column
    # value on that table, not an AGE graph this test ever reads or writes as a graph.
    config = _settings(f"astra_gateway_{new_ulid()[10:22].lower()}")

    async def setup() -> bool:
        try:
            conn = await asyncpg.connect(dsn=config.dsn, timeout=3)
        except Exception:
            return False
        try:
            await run_migrations(conn)
        finally:
            await conn.close()
        return True

    async def teardown() -> None:
        conn = await asyncpg.connect(dsn=config.dsn)
        try:
            await conn.execute(
                "DELETE FROM public.model_gateway_policy WHERE graph = $1", config.graph_name
            )
        finally:
            await conn.close()

    if not _run_off_loop(setup):
        pytest.skip("PostgreSQL with Apache AGE not reachable")

    yield config
    _run_off_loop(teardown)


@pytest.fixture
async def store(settings: Settings):
    pool = await create_pool(settings)
    try:
        yield PostgresGatewayPolicyStore(pool, graph_name=settings.graph_name)
    finally:
        await pool.close()


def _ok_response(dax: str = "[Measure] = SUM([Sales])") -> RawModelResponse:
    return RawModelResponse(
        raw={"dax": dax, "m": None, "assumptions": [], "confidence": 0.9, "notes": "ok"},
        gateway_request_id="gwreq_test", provider="test_provider", model="test-model-1",
        prompt_hash="hash", temperature=0.0, tokens_in=10, tokens_out=5,
    )


class _ScriptedCaller:
    provider = "test_provider"
    model = "test-model-1"

    def __init__(self, responses: list[RawModelResponse]) -> None:
        self._responses = responses
        self.calls = 0

    async def generate(self, request: Any, *, previous_error: str | None) -> RawModelResponse:
        response = self._responses[min(self.calls, len(self._responses) - 1)]
        self.calls += 1
        return response


def _pass_grade(response: RawModelResponse) -> tuple[bool, str]:
    return response.raw.get("dax") not in (None, "NOT_EXPRESSIBLE"), "checked"


def _fail_grade(response: RawModelResponse) -> tuple[bool, str]:
    return False, "always fails"


# ------------------------------------------------------------------------- policy store


async def test_record_eval_and_routable_providers_round_trip(store) -> None:
    caller = _ScriptedCaller([_ok_response()])
    cases = tuple(EvalCase(name=f"c{i}", request=type("R", (), {"as_dict": lambda self: {}})(), grade=_pass_grade) for i in range(5))
    report = await run_eval_set(caller, task_class=TRANSPILE_C3, cases=cases)
    assert report.pass_rate == 1.0

    await store.record_eval(task_class=TRANSPILE_C3, report=report, updated_by=PRINCIPAL.value)

    routable = await store.routable_providers(TRANSPILE_C3)
    assert "test_provider" in routable


async def test_a_provider_below_threshold_is_not_routable(store) -> None:
    caller = _ScriptedCaller([_ok_response()])
    cases = tuple(EvalCase(name=f"c{i}", request=type("R", (), {"as_dict": lambda self: {}})(), grade=_fail_grade) for i in range(5))
    report = await run_eval_set(caller, task_class=TRANSPILE_C3, cases=cases)
    assert report.pass_rate == 0.0

    await store.record_eval(task_class=TRANSPILE_C3, report=report, updated_by=PRINCIPAL.value)

    routable = await store.routable_providers(TRANSPILE_C3)
    assert "test_provider" not in routable


async def test_routable_providers_reflects_only_the_latest_eval_run(store) -> None:
    caller = _ScriptedCaller([_ok_response()])

    failing = tuple(EvalCase(name=f"c{i}", request=type("R", (), {"as_dict": lambda self: {}})(), grade=_fail_grade) for i in range(5))
    await store.record_eval(
        task_class=TRANSPILE_C3,
        report=await run_eval_set(caller, task_class=TRANSPILE_C3, cases=failing),
        updated_by=PRINCIPAL.value,
    )
    assert "test_provider" not in await store.routable_providers(TRANSPILE_C3)

    passing = tuple(EvalCase(name=f"c{i}", request=type("R", (), {"as_dict": lambda self: {}})(), grade=_pass_grade) for i in range(5))
    await store.record_eval(
        task_class=TRANSPILE_C3,
        report=await run_eval_set(caller, task_class=TRANSPILE_C3, cases=passing),
        updated_by=PRINCIPAL.value,
    )
    # An append-only history: the failing run is still there, but the *latest* row wins.
    assert "test_provider" in await store.routable_providers(TRANSPILE_C3)


async def test_policy_for_reports_every_configured_provider_with_its_routable_bit(store) -> None:
    caller = _ScriptedCaller([_ok_response()])
    cases = tuple(EvalCase(name=f"c{i}", request=type("R", (), {"as_dict": lambda self: {}})(), grade=_pass_grade) for i in range(5))
    report = await run_eval_set(caller, task_class=TRANSPILE_C3, cases=cases)
    await store.record_eval(task_class=TRANSPILE_C3, report=report, updated_by=PRINCIPAL.value)

    policy = await store.policy_for(TRANSPILE_C3)
    entry = next(e for e in policy if e.provider == "test_provider")
    assert entry.routable is True
    assert entry.pass_rate == 1.0
    assert entry.total_cases == 5
    assert entry.passed_cases == 5
    assert entry.updated_by == PRINCIPAL.value


async def test_a_task_class_with_no_eval_history_has_no_routable_providers(store) -> None:
    assert await store.routable_providers("some_other_task_class_no_one_ever_scored") == ()
    assert await store.policy_for("some_other_task_class_no_one_ever_scored") == ()


# ------------------------------------------------------------------------------- ModelGateway


async def test_gateway_refuses_to_route_before_any_eval_is_recorded(store) -> None:
    caller = _ScriptedCaller([_ok_response()])
    gateway = ModelGateway(providers={"test_provider": caller}, policy_store=store)
    request = type("R", (), {"as_dict": lambda self: {}})()
    with pytest.raises(GatewayRoutingError):
        await gateway.generate(task_class="unscored_task_class", request=request, previous_error=None)


async def test_gateway_routes_for_real_once_a_provider_clears_the_bar(store) -> None:
    caller = _ScriptedCaller([_ok_response(dax="[Measure] = SUM([Notional])")])
    cases = tuple(EvalCase(name=f"c{i}", request=type("R", (), {"as_dict": lambda self: {}})(), grade=_pass_grade) for i in range(5))
    await store.record_eval(
        task_class=TRANSPILE_C3,
        report=await run_eval_set(caller, task_class=TRANSPILE_C3, cases=cases),
        updated_by=PRINCIPAL.value,
    )

    gateway = ModelGateway(providers={"test_provider": caller}, policy_store=store)
    request = type("R", (), {"as_dict": lambda self: {}})()
    response = await gateway.generate(task_class=TRANSPILE_C3, request=request, previous_error=None)
    assert response.raw["dax"] == "[Measure] = SUM([Notional])"
    assert response.provider == "test_provider"


# -------------------------------------------------------------- real Anthropic integration


@_requires_anthropic
async def test_anthropic_model_caller_makes_a_real_call() -> None:
    credentials = EnvironmentCredentialProvider()
    caller = AnthropicModelCaller(credentials=credentials)

    request = TRANSPILE_C3_EVAL_CASES[0].request
    response = await caller.generate(request, previous_error=None)

    assert response.provider == "anthropic"
    assert response.model
    assert response.gateway_request_id
    assert response.temperature == 0.0
    assert response.tokens_in > 0
    assert response.tokens_out > 0
    # A real structured-output call: the response is a real JSON object with the fields
    # the request's own output_schema declared, whether or not the model judged the
    # calculation expressible.
    assert "dax" in response.raw


@_requires_anthropic
async def test_running_and_recording_a_real_anthropic_eval_makes_it_routable_or_not(store) -> None:
    credentials = EnvironmentCredentialProvider()
    caller = AnthropicModelCaller(credentials=credentials)

    report = await run_transpile_c3_eval(caller)
    assert report.provider == "anthropic"
    assert report.total == len(TRANSPILE_C3_EVAL_CASES)

    await store.record_eval(task_class=TRANSPILE_C3, report=report, updated_by=PRINCIPAL.value)

    policy = await store.policy_for(TRANSPILE_C3)
    entry = next(e for e in policy if e.provider == "anthropic")
    assert entry.pass_rate == report.pass_rate
    # This is the AC's own gate, exercised against a real model for real: routable if and
    # only if the real pass rate cleared ROUTABLE_THRESHOLD -- not asserted as always true,
    # since a real model's own accuracy is the thing being measured, not assumed.
    assert entry.routable == (report.pass_rate >= ROUTABLE_THRESHOLD)
