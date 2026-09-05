"""C3 generation, against real PostgreSQL + Apache AGE — stories S5.3.1 and S5.3.2.

What only the real store can answer: that `build_generation_request` resolves a real
dependency closure (`DEPENDS_ON` to a real `Field`/`Parameter`) and a real encoding
`Worksheet`'s own shelf/filter/sort data; that a successful generation writes a real
`Measure` (mode `GENERATED_PROVED`, class `C3`), a real `MAPS_TO` edge, and a real
`ProvenanceRecord` carrying the full §4.2 `model_call` block (provider, gateway request id,
model, prompt hash, temperature, token counts) readable back; that an exhausted, declined, or
gateway-routing-failed generation writes a real `ExceptionCase` with every attempt attached;
and that the HTTP routes enforce the parity engineer role on the trigger and not the read —
none of which the pure-function tests in `test_generation.py` can see. Calls are routed
through a `StaticGateway`/`FixtureModelCaller` or a scripted caller here, never a real
Anthropic call — the real gateway's own routing/eval-gating mechanics against a real
Postgres policy store are `test_integration_gateway.py`'s own scope.
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
from astra_graph.gateway import (  # noqa: E402
    TRANSPILE_C3,
    TRANSPILE_C3_SMALL_MODEL,
    EvalCaseResult,
    EvalReport,
    ModelGateway,
    PostgresGatewayPolicyStore,
    RawModelResponse,
    StaticGateway,
)
from astra_graph.generation import (  # noqa: E402
    FixtureModelCaller,
    GenerationEngine,
    build_generation_request,
    generate_c3_field,
)
from astra_graph.graph import AgeGraphRepository, create_pool  # noqa: E402
from astra_graph.graph.queries import accessor  # noqa: E402
from astra_graph.ids import new_ulid  # noqa: E402
from astra_graph.lineage import hydrate  # noqa: E402
from astra_graph.migrations import run as run_migrations  # noqa: E402
from astra_graph.ontology import EDGE_LABELS, NODE_LABELS  # noqa: E402
from astra_graph.principal import PRINCIPAL_HEADER, Principal  # noqa: E402
from astra_graph.provenance import PostgresProvenanceStore  # noqa: E402
from astra_graph.roles import ROLES_HEADER  # noqa: E402
from astra_graph.writes import EdgeWrite, GraphWriter, NodeWrite  # noqa: E402

PRINCIPAL = Principal("agent:harvester", run_id="run-generation")
PARITY_ENGINEER = Principal("user:parity@artizent.example")


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


async def _create_graph(conn: asyncpg.Connection, graph: str) -> None:
    await conn.execute("LOAD 'age'")
    await conn.execute('SET search_path = ag_catalog, "$user", public')
    await conn.execute("SELECT ag_catalog.create_graph($1)", graph)
    for label in sorted(NODE_LABELS):
        await conn.execute("SELECT ag_catalog.create_vlabel($1, $2)", graph, label)
        await conn.execute(f'CREATE INDEX ON {graph}."{label}" USING BTREE ({accessor("id")})')
    for label in sorted(EDGE_LABELS):
        await conn.execute("SELECT ag_catalog.create_elabel($1, $2)", graph, label)


@pytest.fixture(scope="module")
def settings() -> Settings:
    config = _settings(f"astra_generation_{new_ulid()[10:22].lower()}")

    async def setup() -> bool:
        try:
            conn = await asyncpg.connect(dsn=config.dsn, timeout=3)
        except Exception:
            return False
        try:
            await run_migrations(conn)
            await _create_graph(conn, config.graph_name)
        finally:
            await conn.close()
        return True

    async def teardown() -> None:
        conn = await asyncpg.connect(dsn=config.dsn)
        try:
            await conn.execute("LOAD 'age'")
            for table in (
                "public.estate_edge_index",
                "public.estate_element_index",
                "public.estate_event",
                "public.provenance",
            ):
                await conn.execute(f"DELETE FROM {table} WHERE graph = $1", config.graph_name)
            await conn.execute("SELECT ag_catalog.drop_graph($1, true)", config.graph_name)
        finally:
            await conn.close()

    if not _run_off_loop(setup):
        pytest.skip("PostgreSQL with Apache AGE not reachable")

    yield config
    _run_off_loop(teardown)


async def _write(writer: GraphWriter, type_: str, **properties: Any) -> str:
    created = await writer.write_nodes(
        [NodeWrite(type=type_, properties=properties)], principal=PRINCIPAL
    )
    return str(created[0]["properties"]["id"])


async def _edge(writer: GraphWriter, type_: str, source: str, target: str, **props: Any) -> None:
    await writer.write_edge(
        EdgeWrite(type=type_, from_id=source, to_id=target, properties=props), principal=PRINCIPAL
    )


def _ref(name: str) -> dict[str, object]:
    return {"kind": "REFERENCE", "name": name, "value": None, "children": [], "detail": []}


def _aggregate(name: str, *children: dict[str, object]) -> dict[str, object]:
    return {"kind": "AGGREGATE", "name": name, "value": None, "children": list(children), "detail": [["family", "aggregate"]]}


def _window(name: str, family: str, *children: dict[str, object]) -> dict[str, object]:
    return {
        "kind": "WINDOW",
        "name": name,
        "value": None,
        "children": list(children),
        "detail": [["family", family], ["addressing", "unresolved"], ["partitioning", "unresolved"]],
    }


class _ScriptedCaller:
    """A `ModelCaller` test double returning one scripted candidate, real enough to drive a
    genuine write path (`generate_c3_field`'s own success branch) — `FixtureModelCaller`'s
    always-`NOT_EXPRESSIBLE` behaviour would never exercise it."""

    provider = "test_provider"
    model = "test-model-1"

    def __init__(self, dax: str) -> None:
        self._dax = dax

    async def generate(self, request: Any, *, previous_error: str | None) -> RawModelResponse:
        return RawModelResponse(
            raw={"dax": self._dax, "m": None, "assumptions": ["test assumption"], "confidence": 0.87, "notes": "generated by test double"},
            gateway_request_id="gw_req_test_1",
            provider=self.provider,
            model=self.model,
            prompt_hash="prompt_hash_test",
            temperature=0.0,
            tokens_in=42,
            tokens_out=17,
        )


class _FakeCalibrationStore:
    """A `CalibrationStore` test double whose `is_below_floor` answer is set directly,
    rather than needing `MIN_OBSERVATIONS_FOR_FLOOR_CHECK` real rows recorded first --
    `PostgresCalibrationStore`'s own real accumulation is `test_integration_calibration.py`'s
    scope; this is only for deterministically exercising `generate_c3_field`'s own routing
    decision."""

    def __init__(self, *, below_floor: bool) -> None:
        self.below_floor = below_floor
        self.recorded: list[dict[str, Any]] = []

    async def record(self, **kwargs: Any) -> None:
        self.recorded.append(kwargs)

    async def report(self, task_class: str, *, floor: float = 0.80) -> Any:
        raise NotImplementedError("not used by these tests")

    async def is_below_floor(self, task_class: str, *, floor: float = 0.80) -> bool:
        return self.below_floor


@pytest.fixture
async def estate(settings: Settings):
    """One genuine C3 field (an unresolved table calc depending on a real Field and a real
    Parameter, encoded by a real Worksheet whose filters/sort are populated but whose
    rows/cols shelves stay empty — so addressing stays unresolved and classification stays
    C3, per `classify.py`'s own rule), plus a plain C1 field for the "refuses a non-C3
    field" case."""
    pool = await create_pool(settings)
    repository = AgeGraphRepository(pool, graph_name=settings.graph_name)
    from astra_graph.events import source_for

    writer = GraphWriter(repository, event_source=source_for(settings.graph_name))
    provenance = PostgresProvenanceStore(pool, graph_name=settings.graph_name)
    suffix = new_ulid()[10:18].lower()

    field = await _write(writer, "Field", name=f"Notional {suffix}", datatype="real", role="measure")
    parameter = await _write(writer, "Parameter", name=f"Window Size {suffix}", datatype="integer", domain="range")

    c3_calc = await _write(
        writer, "CalculatedField",
        name=f"Running Total {suffix}",
        formula="RUNNING_SUM(SUM([Notional]))",
        formula_ast=_window("RUNNING_SUM", "table_calc_simple", _aggregate("SUM", _ref("Notional"))),
        table_calc_flag=True,
    )
    await _edge(writer, "DEPENDS_ON", c3_calc, field, position_in_ast="args[0]")
    await _edge(writer, "DEPENDS_ON", c3_calc, parameter, position_in_ast="args[1]")

    worksheet = await _write(
        writer, "Worksheet", name=f"Desk View {suffix}",
        rows_shelf=[], cols_shelf=[], marks_shelf=[],
        filters=[{"field": "Region", "op": "in", "values": ["East"]}],
        sort=[{"field": "Date", "dir": "asc"}],
    )
    await _edge(writer, "ENCODES", worksheet, c3_calc, shelf="rows")

    c1_calc = await _write(
        writer, "CalculatedField",
        name=f"Total Notional {suffix}",
        formula="SUM([Notional])",
        formula_ast=_aggregate("SUM", _ref("Notional")),
    )

    try:
        yield {
            "pool": pool,
            "graph_name": settings.graph_name,
            "writer": writer,
            "provenance": provenance,
            "field": field,
            "parameter": parameter,
            "c3_calc": c3_calc,
            "c1_calc": c1_calc,
        }
    finally:
        await pool.close()


# ------------------------------------------------------------------- build_generation_request


async def test_build_generation_request_returns_none_for_a_missing_calc(estate) -> None:
    request = await build_generation_request(estate["pool"], estate["graph_name"], "not-a-real-id")
    assert request is None


async def test_build_generation_request_resolves_a_real_dependency_closure(estate) -> None:
    request = await build_generation_request(estate["pool"], estate["graph_name"], estate["c3_calc"])
    assert request is not None
    assert request.source["class"] == "C3"
    field_ids = {f["id"] for f in request.dependency_closure["fields"]}
    param_ids = {p["id"] for p in request.dependency_closure["parameters"]}
    assert estate["field"] in field_ids
    assert estate["parameter"] in param_ids


async def test_build_generation_request_pulls_real_worksheet_shelf_data(estate) -> None:
    request = await build_generation_request(estate["pool"], estate["graph_name"], estate["c3_calc"])
    assert request is not None
    assert request.sheet_ctx["filters"] == [{"field": "Region", "op": "in", "values": ["East"]}]
    assert request.sheet_ctx["sort"] == [{"field": "Date", "dir": "asc"}]
    assert request.sheet_ctx["rows"] == []


async def test_build_generation_request_discloses_empty_model_ctx_and_charter(estate) -> None:
    request = await build_generation_request(estate["pool"], estate["graph_name"], estate["c3_calc"])
    assert request is not None
    assert request.model_ctx["tables"] == []
    assert "note" in request.model_ctx
    assert "note" in request.charter_excerpt


# ------------------------------------------------------------------------------- generate_c3_field


async def test_generate_c3_field_refuses_a_missing_calc(estate) -> None:
    outcome = await generate_c3_field(
        estate["pool"], estate["graph_name"], estate["writer"], estate["provenance"], "not-a-real-id",
        gateway=StaticGateway(FixtureModelCaller()), principal=PARITY_ENGINEER,
    )
    assert outcome.ok is False
    assert outcome.measure_id is None
    assert outcome.exception_case_id is None
    assert "no such CalculatedField" in outcome.reason


async def test_generate_c3_field_refuses_a_non_c3_field(estate) -> None:
    outcome = await generate_c3_field(
        estate["pool"], estate["graph_name"], estate["writer"], estate["provenance"], estate["c1_calc"],
        gateway=StaticGateway(FixtureModelCaller()), principal=PARITY_ENGINEER,
    )
    assert outcome.ok is False
    assert "not a Class 3 calculation" in outcome.reason


async def test_generate_c3_field_with_the_fixture_caller_writes_a_real_exception_case(estate) -> None:
    outcome = await generate_c3_field(
        estate["pool"], estate["graph_name"], estate["writer"], estate["provenance"], estate["c3_calc"],
        gateway=StaticGateway(FixtureModelCaller()), principal=PARITY_ENGINEER,
    )
    assert outcome.ok is False
    assert outcome.measure_id is None
    assert outcome.exception_case_id is not None
    assert len(outcome.attempts) == 1
    assert outcome.attempts[0].not_expressible is True

    async with estate["pool"].acquire() as conn:
        cases = await hydrate(conn, estate["graph_name"], "ExceptionCase", [outcome.exception_case_id])
    case = cases[outcome.exception_case_id]
    assert case["mu_ref"] == f"calc:{estate['c3_calc']}"
    assert case["class"] == "UNKNOWN"
    assert case["state"] == "OPEN"
    assert case["evidence_ref"]


async def test_generate_c3_field_with_a_valid_candidate_writes_measure_maps_to_and_provenance(estate) -> None:
    caller = _ScriptedCaller(dax="Running Total = CALCULATE(SUM([Notional]))")
    outcome = await generate_c3_field(
        estate["pool"], estate["graph_name"], estate["writer"], estate["provenance"], estate["c3_calc"],
        gateway=StaticGateway(caller), principal=PARITY_ENGINEER,
    )
    assert outcome.ok is True
    assert outcome.exception_case_id is None
    assert outcome.measure_id is not None

    async with estate["pool"].acquire() as conn:
        measures = await hydrate(conn, estate["graph_name"], "Measure", [outcome.measure_id])
    measure = measures[outcome.measure_id]
    assert measure["dax"] == caller._dax
    assert measure["class"] == "C3"
    assert measure["source_calc_ref"] == estate["c3_calc"]
    assert "pattern_ref" not in measure
    assert measure["provenance_ref"]

    record = await estate["provenance"].get(measure["provenance_ref"])
    assert record is not None
    assert record.mode.value == "GENERATED_PROVED"
    assert record.artefact_kind == "MEASURE"
    assert record.artefact_ref == outcome.measure_id
    assert record.subject_id == estate["c3_calc"]
    assert record.provider == "test_provider"
    assert record.gateway_request_id == "gw_req_test_1"
    assert record.model == "test-model-1"
    assert record.prompt_hash == "prompt_hash_test"
    assert record.temperature == 0.0
    assert record.tokens_in == 42
    assert record.tokens_out == 17


# --------------------------------------------------------------------- calibration (S5.3.3)


async def test_generate_c3_field_records_a_real_calibration_observation_on_success(estate) -> None:
    caller = _ScriptedCaller(dax="Running Total = CALCULATE(SUM([Notional]))")
    calibration = PostgresCalibrationStore(estate["pool"], graph_name=estate["graph_name"])
    outcome = await generate_c3_field(
        estate["pool"], estate["graph_name"], estate["writer"], estate["provenance"], estate["c3_calc"],
        gateway=StaticGateway(caller), calibration=calibration, principal=PARITY_ENGINEER,
    )
    assert outcome.ok is True
    assert outcome.task_class == TRANSPILE_C3

    report = await calibration.report(TRANSPILE_C3)
    bucket = report.buckets[8]  # caller's own declared confidence, 0.87, is [0.8, 0.9)
    assert bucket.count >= 1
    assert bucket.observed_pass_rate == 1.0


async def test_generate_c3_field_records_a_real_calibration_observation_on_a_decline(estate) -> None:
    calibration = PostgresCalibrationStore(estate["pool"], graph_name=estate["graph_name"])
    outcome = await generate_c3_field(
        estate["pool"], estate["graph_name"], estate["writer"], estate["provenance"], estate["c3_calc"],
        gateway=StaticGateway(FixtureModelCaller()), calibration=calibration, principal=PARITY_ENGINEER,
    )
    assert outcome.ok is False

    report = await calibration.report(TRANSPILE_C3)
    bucket = report.buckets[0]  # FixtureModelCaller's own declared confidence is 0.0
    assert bucket.count >= 1
    assert bucket.observed_pass_rate == 0.0


async def test_generate_c3_field_uses_the_reasoning_tier_when_calibration_is_not_below_floor(estate) -> None:
    caller = _ScriptedCaller(dax="Running Total = CALCULATE(SUM([Notional]))")
    policy = PostgresGatewayPolicyStore(estate["pool"], graph_name=estate["graph_name"])
    await policy.record_eval(
        task_class=TRANSPILE_C3,
        report=EvalReport(
            provider=caller.provider, model=caller.model, task_class=TRANSPILE_C3,
            total=5, passed=5, pass_rate=1.0, ran_at="2027-01-01T00:00:00+00:00",
            results=tuple(EvalCaseResult(name=f"c{i}", passed=True, detail="ok") for i in range(5)),
        ),
        updated_by=PARITY_ENGINEER.value,
    )
    gateway = ModelGateway(providers={caller.provider: caller}, policy_store=policy)
    calibration = _FakeCalibrationStore(below_floor=False)

    outcome = await generate_c3_field(
        estate["pool"], estate["graph_name"], estate["writer"], estate["provenance"], estate["c3_calc"],
        gateway=gateway, calibration=calibration, principal=PARITY_ENGINEER,
    )
    assert outcome.task_class == TRANSPILE_C3
    assert outcome.ok is True


async def test_generate_c3_field_reroutes_to_the_small_model_path_when_below_floor(estate) -> None:
    caller = _ScriptedCaller(dax="Running Total = CALCULATE(SUM([Notional]))")
    policy = PostgresGatewayPolicyStore(estate["pool"], graph_name=estate["graph_name"])
    # Routable for the reasoning tier only -- nothing is ever recorded, let alone passing,
    # for TRANSPILE_C3_SMALL_MODEL, since no real small-model provider exists to eval-score.
    await policy.record_eval(
        task_class=TRANSPILE_C3,
        report=EvalReport(
            provider=caller.provider, model=caller.model, task_class=TRANSPILE_C3,
            total=5, passed=5, pass_rate=1.0, ran_at="2027-01-01T00:00:00+00:00",
            results=tuple(EvalCaseResult(name=f"c{i}", passed=True, detail="ok") for i in range(5)),
        ),
        updated_by=PARITY_ENGINEER.value,
    )
    gateway = ModelGateway(providers={caller.provider: caller}, policy_store=policy)
    calibration = _FakeCalibrationStore(below_floor=True)

    outcome = await generate_c3_field(
        estate["pool"], estate["graph_name"], estate["writer"], estate["provenance"], estate["c3_calc"],
        gateway=gateway, calibration=calibration, principal=PARITY_ENGINEER,
    )
    assert outcome.task_class == TRANSPILE_C3_SMALL_MODEL
    assert outcome.ok is False
    assert outcome.exception_case_id is not None
    assert len(outcome.attempts) == 1
    assert outcome.attempts[0].gateway_error is not None
    assert "transpile_c3_small_model" in outcome.attempts[0].gateway_error
    # No real model was ever reached under the rerouted task class, so nothing was
    # recorded -- there is no confidence to have declared.
    assert calibration.recorded == []


async def test_a_second_generation_of_an_already_converted_field_writes_a_second_measure(estate) -> None:
    # Unlike the deterministic rules engine (S5.2.1), generation has no "already converted"
    # short-circuit -- a model call is expensive and non-idempotent, so re-running is a
    # deliberate caller decision, not something this module silently dedupes.
    caller = _ScriptedCaller(dax="Running Total = CALCULATE(SUM([Notional]))")
    first = await generate_c3_field(
        estate["pool"], estate["graph_name"], estate["writer"], estate["provenance"], estate["c3_calc"],
        gateway=StaticGateway(caller), principal=PARITY_ENGINEER,
    )
    second = await generate_c3_field(
        estate["pool"], estate["graph_name"], estate["writer"], estate["provenance"], estate["c3_calc"],
        gateway=StaticGateway(caller), principal=PARITY_ENGINEER,
    )
    assert first.ok and second.ok
    assert first.measure_id != second.measure_id


# ---------------------------------------------------------------------------------- the API


@pytest.fixture
async def http_client(estate):
    from httpx import ASGITransport, AsyncClient

    from astra_graph.main import create_app

    app = create_app()
    app.state.generation_engine = GenerationEngine(
        estate["pool"], graph_name=estate["graph_name"], writer=estate["writer"],
        provenance_store=estate["provenance"], gateway=StaticGateway(FixtureModelCaller()),
    )

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://graph-svc") as async_client:
        yield async_client


def _headers(role: str, principal: Principal) -> dict[str, str]:
    return {PRINCIPAL_HEADER: principal.value, ROLES_HEADER: role}


async def test_get_generation_request_over_http_is_open_to_any_artizent_role(http_client, estate) -> None:
    response = await http_client.get(
        f"/v1/calculations/{estate['c3_calc']}:generation-request",
        headers=_headers("programme_manager", PARITY_ENGINEER),
    )
    assert response.status_code == 200
    assert response.json()["source"]["class"] == "C3"


async def test_get_generation_request_over_http_404s_a_missing_calc(http_client) -> None:
    response = await http_client.get(
        "/v1/calculations/not-a-real-id:generation-request",
        headers=_headers("programme_manager", PARITY_ENGINEER),
    )
    assert response.status_code == 404


async def test_generate_over_http_requires_the_parity_engineer_role(http_client, estate) -> None:
    response = await http_client.post(
        f"/v1/calculations/{estate['c3_calc']}:generate",
        headers=_headers("programme_manager", PARITY_ENGINEER),
    )
    assert response.status_code == 403


async def test_generate_over_http_with_the_parity_engineer_role_returns_the_outcome(http_client, estate) -> None:
    response = await http_client.post(
        f"/v1/calculations/{estate['c3_calc']}:generate",
        headers=_headers("parity_engineer", PARITY_ENGINEER),
    )
    assert response.status_code == 200
    body = response.json()
    assert body["calculated_field_id"] == estate["c3_calc"]
    # This fixture wires a StaticGateway(FixtureModelCaller()) (never a real Anthropic
    # call) which always declines -- an honest `ok: false` over HTTP, not a fabricated
    # success. A deployment with no gateway wired at all (the real `GenerationEngine`
    # default, `null_gateway()`) would instead set `attempts[0].gateway_error` --
    # `test_generation.py`'s own `test_ladder_no_routable_provider_is_never_retried`.
    assert body["ok"] is False
    assert body["exception_case_id"]
    assert body["attempts"][0]["not_expressible"] is True
