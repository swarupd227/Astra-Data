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

from astra_graph.calibration import PostgresCalibrationStore  # noqa: E402
from astra_graph.config import Settings  # noqa: E402
from astra_graph.credentials import EnvironmentCredentialProvider  # noqa: E402
from astra_graph.gateway import (  # noqa: E402
    MENDER_REPAIR,
    ROUTABLE_THRESHOLD,
    TRANSPILE_C3,
    AnthropicModelCaller,
    ContentLoggingGrantError,
    EvalCase,
    EvalReport,
    GatewayRoutingError,
    GatewayValidationError,
    ModelGateway,
    PostgresContentLoggingGrantStore,
    PostgresGatewayPolicyStore,
    PostgresGatewayRequestLogStore,
    RawModelResponse,
    run_eval_set,
)
from astra_graph.generation import TRANSPILE_C3_EVAL_CASES, run_transpile_c3_eval  # noqa: E402
from astra_graph.graph import create_pool  # noqa: E402
from astra_graph.ids import new_ulid  # noqa: E402
from astra_graph.migrations import run as run_migrations  # noqa: E402
from astra_graph.principal import PRINCIPAL_HEADER, Principal  # noqa: E402
from astra_graph.roles import ROLES_HEADER  # noqa: E402

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
            for table in (
                "public.model_gateway_policy", "public.gateway_request_log",
                "public.gateway_content_logging_grant",
            ):
                await conn.execute(f"DELETE FROM {table} WHERE graph = $1", config.graph_name)
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


@pytest.fixture
async def pool(settings: Settings):
    p = await create_pool(settings)
    try:
        yield p
    finally:
        await p.close()


def _ok_response(dax: str = "[Measure] = SUM([Sales])") -> RawModelResponse:
    return RawModelResponse(
        raw={"dax": dax, "m": None, "assumptions": [], "confidence": 0.9, "notes": "ok"},
        gateway_request_id="gwreq_test", provider="test_provider", model="test-model-1",
        prompt_hash="hash", context_hash="hash", temperature=0.0, tokens_in=10, tokens_out=5, latency_ms=0.0, prompt_template_version="test",
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


# --------------------------------------------------------------- S11.4.2: gateway enforcement


class _DictLikeRequest:
    def __init__(self, payload: dict[str, Any]) -> None:
        self._payload = payload

    def as_dict(self) -> dict[str, Any]:
        return dict(self._payload)


def _mender_payload(**overrides: Any) -> dict[str, Any]:
    payload = {
        "task": "MENDER_REPAIR", "failure_class": "AGGREGATION", "classification_signals": {},
        "failing_cells": [], "filter_ctx": {}, "expected_columns": [], "candidate_columns": [],
        "current_dax": "SUM([Margin])", "source_formula": "SUM([Margin])",
        "source_formula_ast": None, "class_instruction": "fix it", "dependency_closure": {},
        "widened": False, "output_schema": {},
    }
    payload.update(overrides)
    return payload


async def _seed_routable_policy(store: PostgresGatewayPolicyStore, *, provider: str) -> None:
    await store.record_eval(
        task_class=MENDER_REPAIR,
        report=EvalReport(
            provider=provider, model="test-model-1", task_class=MENDER_REPAIR,
            total=1, passed=1, pass_rate=1.0, ran_at="2027-01-01T00:00:00+00:00", results=(),
        ),
        updated_by=PRINCIPAL.value,
    )


async def test_a_request_with_an_unexpected_field_is_refused_before_anything_is_sent(store, pool, settings: Settings) -> None:
    await _seed_routable_policy(store, provider="test_provider")
    caller = _ScriptedCaller([_ok_response()])
    log_store = PostgresGatewayRequestLogStore(pool, graph_name=settings.graph_name)
    gateway = ModelGateway(providers={"test_provider": caller}, policy_store=store, log_store=log_store)

    with pytest.raises(GatewayValidationError):
        await gateway.generate(
            task_class=MENDER_REPAIR,
            request=_DictLikeRequest(_mender_payload(a_field_nobody_declared="leaked!")),
            previous_error=None,
        )
    assert caller.calls == 0
    assert not await log_store.contains_text("leaked!")


async def test_a_field_over_the_byte_limit_is_refused(store, pool, settings: Settings) -> None:
    await _seed_routable_policy(store, provider="test_provider")
    caller = _ScriptedCaller([_ok_response()])
    log_store = PostgresGatewayRequestLogStore(pool, graph_name=settings.graph_name)
    gateway = ModelGateway(providers={"test_provider": caller}, policy_store=store, log_store=log_store)

    with pytest.raises(GatewayValidationError):
        await gateway.generate(
            task_class=MENDER_REPAIR,
            request=_DictLikeRequest(_mender_payload(current_dax="x" * 40_000)),
            previous_error=None,
        )
    assert caller.calls == 0


async def test_a_real_data_like_literal_is_redacted_before_the_provider_ever_sees_it(store, pool, settings: Settings) -> None:
    await _seed_routable_policy(store, provider="test_provider")
    caller = _ScriptedCaller([_ok_response()])
    log_store = PostgresGatewayRequestLogStore(pool, graph_name=settings.graph_name)
    gateway = ModelGateway(providers={"test_provider": caller}, policy_store=store, log_store=log_store)

    received: dict[str, Any] = {}
    original_generate = caller.generate

    async def _capture(request: Any, *, previous_error: str | None) -> RawModelResponse:
        received.update(request.as_dict())
        return await original_generate(request, previous_error=previous_error)

    caller.generate = _capture  # type: ignore[method-assign]

    await gateway.generate(
        task_class=MENDER_REPAIR,
        request=_DictLikeRequest(_mender_payload(
            source_formula='IF [Email] = "real.person@example.com" THEN 1 ELSE 0',
        )),
        previous_error=None,
    )
    assert "real.person@example.com" not in received["source_formula"]
    assert "[REDACTED:EMAIL]" in received["source_formula"]


async def test_content_logging_is_off_by_default_only_hashes_are_persisted(store, pool, settings: Settings) -> None:
    await _seed_routable_policy(store, provider="test_provider")
    caller = _ScriptedCaller([_ok_response(dax="SUM([RealMeasure])")])
    log_store = PostgresGatewayRequestLogStore(pool, graph_name=settings.graph_name)
    gateway = ModelGateway(providers={"test_provider": caller}, policy_store=store, log_store=log_store)

    await gateway.generate(
        task_class=MENDER_REPAIR, request=_DictLikeRequest(_mender_payload()), previous_error=None,
    )

    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT request_text, response_text, prompt_hash, response_hash, redaction_count "
            "FROM public.gateway_request_log WHERE graph = $1 ORDER BY created_at DESC LIMIT 1",
            settings.graph_name,
        )
    assert row is not None
    assert row["request_text"] is None
    assert row["response_text"] is None
    assert row["prompt_hash"]
    assert row["response_hash"]
    assert row["redaction_count"] == 0


async def test_an_active_grant_makes_both_request_and_response_text_visible(store, pool, settings: Settings) -> None:
    graph_name = settings.graph_name
    await _seed_routable_policy(store, provider="test_provider")
    caller = _ScriptedCaller([_ok_response(dax="SUM([RealMeasure])")])
    log_store = PostgresGatewayRequestLogStore(pool, graph_name=graph_name)
    gateway = ModelGateway(providers={"test_provider": caller}, policy_store=store, log_store=log_store)
    grants = PostgresContentLoggingGrantStore(pool, graph_name=graph_name)

    granted = await grants.grant(enabled_by="user:infosec@client.example", duration_minutes=5)
    assert granted.active is True

    await gateway.generate(
        task_class=MENDER_REPAIR,
        request=_DictLikeRequest(_mender_payload(current_dax="SUM([RealMeasure])")),
        previous_error=None,
    )

    assert await log_store.contains_text("RealMeasure")

    revoked = await grants.revoke(revoked_by="user:infosec@client.example")
    assert revoked is not None
    assert revoked.active is False

    await gateway.generate(
        task_class=MENDER_REPAIR,
        request=_DictLikeRequest(_mender_payload(current_dax="SUM([AfterRevoke])")),
        previous_error=None,
    )
    assert not await log_store.contains_text("AfterRevoke")


async def test_a_grant_duration_outside_the_bound_is_refused(store, pool, settings: Settings) -> None:
    grants = PostgresContentLoggingGrantStore(pool, graph_name=settings.graph_name)
    with pytest.raises(ContentLoggingGrantError):
        await grants.grant(enabled_by="user:infosec@client.example", duration_minutes=0)
    with pytest.raises(ContentLoggingGrantError):
        await grants.grant(enabled_by="user:infosec@client.example", duration_minutes=1441)


async def test_revoking_with_no_active_grant_returns_none(store, pool, settings: Settings) -> None:
    grants = PostgresContentLoggingGrantStore(pool, graph_name=settings.graph_name)
    assert await grants.revoke(revoked_by="user:infosec@client.example") is None


async def test_a_real_pattern_match_is_counted_and_the_count_is_logged(store, pool, settings: Settings) -> None:
    graph_name = settings.graph_name
    await _seed_routable_policy(store, provider="test_provider")
    caller = _ScriptedCaller([_ok_response()])
    log_store = PostgresGatewayRequestLogStore(pool, graph_name=graph_name)
    gateway = ModelGateway(providers={"test_provider": caller}, policy_store=store, log_store=log_store)

    await gateway.generate(
        task_class=MENDER_REPAIR,
        request=_DictLikeRequest(_mender_payload(
            source_formula='a@b.com and c@d.com both appear here',
        )),
        previous_error=None,
    )
    async with pool.acquire() as conn:
        redaction_count = await conn.fetchval(
            "SELECT redaction_count FROM public.gateway_request_log "
            "WHERE graph = $1 ORDER BY created_at DESC LIMIT 1",
            graph_name,
        )
    assert redaction_count == 2


async def test_a_request_that_never_routes_is_never_logged_and_validation_never_runs(store, pool, settings: Settings) -> None:
    log_store = PostgresGatewayRequestLogStore(pool, graph_name=settings.graph_name)
    gateway = ModelGateway(providers={}, policy_store=store, log_store=log_store)
    # A unique marker, not "MENDER_REPAIR" -- this fixture's own graph is shared
    # (module-scoped) across every test above, some of which really do log real
    # MENDER_REPAIR-tagged text; a generic string would find one of those instead of
    # proving anything about this call, which must never be logged at all.
    unique_marker = f"never-routed-{new_ulid()}"

    with pytest.raises(GatewayRoutingError):
        await gateway.generate(
            task_class=MENDER_REPAIR,
            request=_DictLikeRequest(_mender_payload(class_instruction=unique_marker)),
            previous_error=None,
        )
    assert not await log_store.contains_text(unique_marker)


# ---------------------------------------------------------------------------------- the API


@pytest.fixture
async def http_client(settings: Settings):
    from httpx import ASGITransport, AsyncClient

    from astra_graph.main import create_app

    pool = await create_pool(settings)
    app = create_app()
    app.state.calibration = PostgresCalibrationStore(pool, graph_name=settings.graph_name)

    transport = ASGITransport(app=app)
    try:
        async with AsyncClient(transport=transport, base_url="http://graph-svc") as async_client:
            yield async_client
    finally:
        await pool.close()


def _headers(role: str, principal: Principal) -> dict[str, str]:
    return {PRINCIPAL_HEADER: principal.value, ROLES_HEADER: role}


async def test_get_calibration_over_http_is_open_to_any_artizent_role(http_client) -> None:
    response = await http_client.get(
        "/v1/model-gateway:calibration",
        params={"task_class": TRANSPILE_C3},
        headers=_headers("programme_manager", PRINCIPAL),
    )
    assert response.status_code == 200
    body = response.json()
    assert body["task_class"] == TRANSPILE_C3
    assert len(body["buckets"]) == 10


async def test_get_calibration_over_http_honours_a_custom_floor_query_param(http_client) -> None:
    response = await http_client.get(
        "/v1/model-gateway:calibration",
        params={"task_class": TRANSPILE_C3, "floor": 0.5},
        headers=_headers("programme_manager", PRINCIPAL),
    )
    assert response.status_code == 200
    assert response.json()["floor"] == 0.5
