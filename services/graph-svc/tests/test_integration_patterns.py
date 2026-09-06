"""The Pattern Library, against real PostgreSQL + Apache AGE — stories S5.5.1, S5.5.2 and
S5.5.3.

What only the real store can answer: that a real `GENERATED_PROVED` success generalises
into a real CANDIDATE `Pattern` node, keyed by a real AST shape; that a second, independent
proof of the same shape (a different `CalculatedField`) accumulates a *distinct* pass
against the same pattern rather than creating a second one; that a real ladder failure
records a real proof failure against an existing pattern; that promotion re-checks the AC's
own two objective conditions against the real `pattern_observation` history rather than
trusting a caller; and — S5.5.1's own payoff — that an ACTIVE pattern is applied to a
brand-new C3 field entirely without ever reaching the model gateway, re-evaluating that
field's own `class`/`pattern_ref` in place.

S5.5.2 adds: that a real failure increments a real, disclosed `failure_count` on the
Pattern node; that an ACTIVE pattern crossing the real §9.3 retirement threshold (either
condition — an absolute failure count or a pass rate over a minimum sample) is moved to
RETIRED automatically, with no approval step; that retiring one re-queues every Measure it
produced (retired, its source field reverted to a real, pattern-unaware classification) and
raises a real, readable `estate.pattern.retired` notice event. None of this is visible to
the pure-function tests in `test_patterns.py`.
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
from astra_graph.events import EventType  # noqa: E402
from astra_graph.gateway import RawModelResponse, StaticGateway  # noqa: E402
from astra_graph.generation import (  # noqa: E402
    FixtureModelCaller,
    GenerationEngine,
    generate_c3_field,
)
from astra_graph.graph import AgeGraphRepository, create_pool  # noqa: E402
from astra_graph.graph.queries import accessor  # noqa: E402
from astra_graph.ids import new_ulid  # noqa: E402
from astra_graph.lineage import hydrate  # noqa: E402
from astra_graph.migrations import run as run_migrations  # noqa: E402
from astra_graph.ontology import EDGE_LABELS, NODE_LABELS  # noqa: E402
from astra_graph.patterns import (  # noqa: E402
    PatternMatch,
    PatternPromotionError,
    PatternRetirementError,
    apply_active_pattern,
    edit_guards,
    find_matching_pattern,
    promote_pattern,
    promotion_status,
    record_failure_and_maybe_retire,
    retire_pattern,
)
from astra_graph.principal import PRINCIPAL_HEADER, Principal  # noqa: E402
from astra_graph.provenance import PostgresProvenanceStore  # noqa: E402
from astra_graph.roles import ROLES_HEADER  # noqa: E402
from astra_graph.writes import EdgeWrite, GraphWriter, NodeWrite  # noqa: E402

PRINCIPAL = Principal("agent:harvester", run_id="run-patterns")
PLATFORM_ENGINEER = Principal("user:platform@artizent.example")


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
    config = _settings(f"astra_patterns_{new_ulid()[10:22].lower()}")

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
                "public.pattern_observation",
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
        "kind": "WINDOW", "name": name, "value": None, "children": list(children),
        "detail": [["family", family], ["addressing", "unresolved"], ["partitioning", "unresolved"]],
    }


class _ScriptedCaller:
    """A `ModelCaller` returning one scripted candidate — real enough to drive
    `generate_c3_field`'s own success branch, matching `test_integration_generation.py`'s
    own test double."""

    provider = "test_provider"
    model = "test-model-1"

    def __init__(self, dax: str, confidence: float = 0.9) -> None:
        self._dax = dax
        self._confidence = confidence

    async def generate(self, request: Any, *, previous_error: str | None) -> RawModelResponse:
        return RawModelResponse(
            raw={"dax": self._dax, "m": None, "assumptions": [], "confidence": self._confidence, "notes": "test"},
            gateway_request_id="gw_req_test", provider=self.provider, model=self.model,
            prompt_hash="prompt_hash_test", temperature=0.0, tokens_in=10, tokens_out=5,
        )


class _PoisonGateway:
    """Proves an ACTIVE pattern's own deterministic application never reaches the model —
    the AC's own "ahead of any model call", checked rather than assumed: calling this
    raises, so a test that passes despite this gateway being wired is proof the model path
    was never taken, not merely that it was not observed to be."""

    async def generate(self, *, task_class: str, request: Any, previous_error: str | None) -> RawModelResponse:
        raise AssertionError("the model must never be called when an ACTIVE pattern already covers this shape")


@pytest.fixture
async def estate(settings: Settings):
    """A real Field a table-calc shape depends on (`datatype='real'`, so guard inference
    has something real to find), and a helper to mint a fresh C3-classifiable
    `CalculatedField` sharing that exact shape under a fresh id — the same shape, applied
    to as many distinct source calculations as a test needs, is exactly what "N distinct
    proof passes" means to check.
    """
    pool = await create_pool(settings)
    repository = AgeGraphRepository(pool, graph_name=settings.graph_name)
    from astra_graph.events import source_for

    writer = GraphWriter(repository, event_source=source_for(settings.graph_name))
    provenance = PostgresProvenanceStore(pool, graph_name=settings.graph_name)
    suffix = new_ulid()[10:18].lower()
    # The window function's own name is part of the AST shape (unlike a captured field's
    # name, which `ast_shape` abstracts away) -- this graph is module-scoped, shared by
    # every test in this file, so each test needs its own shape or they would all collide
    # on one Pattern. A per-test-unique window function name gives each test real
    # isolation while still letting *one* test's own repeated calls share a shape on
    # purpose (exactly what "N distinct proof passes" needs to accumulate against).
    window_fn = f"RUNNING_SUM_{suffix.upper()}"

    field = await _write(writer, "Field", name=f"Notional {suffix}", datatype="real", role="measure")

    async def new_c3_calc(*, field_name: str | None = None) -> str:
        name = field_name or f"Notional {suffix}"
        calc_id = await _write(
            writer, "CalculatedField",
            name=f"Running Total {new_ulid()[10:16].lower()}",
            formula=f"{window_fn}(SUM([{name}]))",
            formula_ast=_window(window_fn, "table_calc_simple", _aggregate("SUM", _ref(name))),
            table_calc_flag=True,
        )
        if name == f"Notional {suffix}":
            await _edge(writer, "DEPENDS_ON", calc_id, field, position_in_ast="args[0]")
        return calc_id

    try:
        yield {
            "pool": pool, "graph_name": settings.graph_name, "writer": writer,
            "repository": repository,
            "provenance": provenance, "field": field, "field_name": f"Notional {suffix}",
            "window_fn": window_fn, "new_c3_calc": new_c3_calc,
        }
    finally:
        await pool.close()


# ------------------------------------------------------------------ bullet 1: generalisation


async def test_a_generated_proved_success_creates_a_candidate_pattern(estate) -> None:
    calc_id = await estate["new_c3_calc"]()
    caller = _ScriptedCaller(dax=f"CALCULATE(SUM([{estate['field_name']}]))")
    outcome = await generate_c3_field(
        estate["pool"], estate["graph_name"], estate["writer"], estate["provenance"], calc_id,
        gateway=StaticGateway(caller), principal=PRINCIPAL,
    )
    assert outcome.ok is True
    assert outcome.pattern_id is not None

    async with estate["pool"].acquire() as conn:
        patterns = await hydrate(conn, estate["graph_name"], "Pattern", [outcome.pattern_id])
    pattern = patterns[outcome.pattern_id]
    assert pattern["promotion_state"] == "CANDIDATE"
    assert pattern["class"] == "C3"
    assert pattern["source_signature"]["ast_shape"] == f"{estate['window_fn']}(SUM(a))"
    # The real field name is abstracted away -- a template naming it verbatim could never
    # apply to a differently-named field sharing the same shape.
    assert estate["field_name"] not in pattern["target_template"]
    assert "{a}" in pattern["target_template"]
    assert pattern["guards"] == ["a is real"]


async def test_a_second_proof_of_the_same_shape_reuses_the_pattern_as_a_distinct_pass(estate) -> None:
    first_calc = await estate["new_c3_calc"]()
    second_field = await _write(
        estate["writer"], "Field", name="Margin", datatype="real", role="measure"
    )
    second_calc = await estate["new_c3_calc"](field_name="Margin")
    await _edge(estate["writer"], "DEPENDS_ON", second_calc, second_field, position_in_ast="args[0]")

    caller = _ScriptedCaller(dax=f"CALCULATE(SUM([{estate['field_name']}]))")
    first = await generate_c3_field(
        estate["pool"], estate["graph_name"], estate["writer"], estate["provenance"], first_calc,
        gateway=StaticGateway(caller), principal=PRINCIPAL,
    )
    second_caller = _ScriptedCaller(dax="CALCULATE(SUM([Margin]))")
    second = await generate_c3_field(
        estate["pool"], estate["graph_name"], estate["writer"], estate["provenance"], second_calc,
        gateway=StaticGateway(second_caller), principal=PRINCIPAL,
    )
    assert first.pattern_id == second.pattern_id  # same shape -- reused, not duplicated

    status = await promotion_status(estate["pool"], estate["graph_name"], first.pattern_id, threshold=5)
    assert status.distinct_passing_calcs == 2
    assert status.has_failure is False


async def test_a_ladder_failure_records_a_real_failure_against_an_existing_pattern(estate) -> None:
    first_calc = await estate["new_c3_calc"]()
    caller = _ScriptedCaller(dax=f"CALCULATE(SUM([{estate['field_name']}]))")
    first = await generate_c3_field(
        estate["pool"], estate["graph_name"], estate["writer"], estate["provenance"], first_calc,
        gateway=StaticGateway(caller), principal=PRINCIPAL,
    )
    assert first.ok is True

    failing_calc = await estate["new_c3_calc"]()
    failed = await generate_c3_field(
        estate["pool"], estate["graph_name"], estate["writer"], estate["provenance"], failing_calc,
        gateway=StaticGateway(FixtureModelCaller()), principal=PRINCIPAL,
    )
    assert failed.ok is False

    status = await promotion_status(estate["pool"], estate["graph_name"], first.pattern_id, threshold=5)
    assert status.has_failure is True
    assert status.eligible is False


# --------------------------------------------------------------------------- bullet 2: promotion


async def test_promote_pattern_refuses_below_the_threshold(estate) -> None:
    calc_id = await estate["new_c3_calc"]()
    caller = _ScriptedCaller(dax=f"CALCULATE(SUM([{estate['field_name']}]))")
    outcome = await generate_c3_field(
        estate["pool"], estate["graph_name"], estate["writer"], estate["provenance"], calc_id,
        gateway=StaticGateway(caller), principal=PRINCIPAL,
    )
    with pytest.raises(PatternPromotionError, match="only 1 of 5"):
        await promote_pattern(
            estate["pool"], estate["graph_name"], estate["writer"],
            pattern_id=outcome.pattern_id, principal=PLATFORM_ENGINEER,
        )


async def test_promote_pattern_refuses_with_any_recorded_failure_even_at_threshold(estate) -> None:
    calc_id = await estate["new_c3_calc"]()
    caller = _ScriptedCaller(dax=f"CALCULATE(SUM([{estate['field_name']}]))")
    outcome = await generate_c3_field(
        estate["pool"], estate["graph_name"], estate["writer"], estate["provenance"], calc_id,
        gateway=StaticGateway(caller), principal=PRINCIPAL,
    )
    failing_calc = await estate["new_c3_calc"]()
    await generate_c3_field(
        estate["pool"], estate["graph_name"], estate["writer"], estate["provenance"], failing_calc,
        gateway=StaticGateway(FixtureModelCaller()), principal=PRINCIPAL,
    )
    with pytest.raises(PatternPromotionError, match="failure"):
        await promote_pattern(
            estate["pool"], estate["graph_name"], estate["writer"],
            pattern_id=outcome.pattern_id, principal=PLATFORM_ENGINEER, threshold=1,
        )


async def test_promote_pattern_succeeds_once_the_threshold_is_met_with_zero_failures(estate) -> None:
    calc_id = await estate["new_c3_calc"]()
    caller = _ScriptedCaller(dax=f"CALCULATE(SUM([{estate['field_name']}]))")
    outcome = await generate_c3_field(
        estate["pool"], estate["graph_name"], estate["writer"], estate["provenance"], calc_id,
        gateway=StaticGateway(caller), principal=PRINCIPAL,
    )
    updated = await promote_pattern(
        estate["pool"], estate["graph_name"], estate["writer"],
        pattern_id=outcome.pattern_id, principal=PLATFORM_ENGINEER, threshold=1,
    )
    assert updated["promotion_state"] == "ACTIVE"
    assert updated["provenance"]["approved_by"] == PLATFORM_ENGINEER.value
    assert updated["provenance"]["promoted_at"]
    assert updated["distinct_passing_calcs"] == 1


async def test_promote_pattern_raises_for_an_already_active_pattern(estate) -> None:
    calc_id = await estate["new_c3_calc"]()
    caller = _ScriptedCaller(dax=f"CALCULATE(SUM([{estate['field_name']}]))")
    outcome = await generate_c3_field(
        estate["pool"], estate["graph_name"], estate["writer"], estate["provenance"], calc_id,
        gateway=StaticGateway(caller), principal=PRINCIPAL,
    )
    await promote_pattern(
        estate["pool"], estate["graph_name"], estate["writer"],
        pattern_id=outcome.pattern_id, principal=PLATFORM_ENGINEER, threshold=1,
    )
    with pytest.raises(PatternPromotionError, match="already ACTIVE"):
        await promote_pattern(
            estate["pool"], estate["graph_name"], estate["writer"],
            pattern_id=outcome.pattern_id, principal=PLATFORM_ENGINEER, threshold=1,
        )


# ---------------------------------------------------------- bullet 3: deterministic application


async def test_apply_active_pattern_fails_the_sanity_check_records_a_failure_and_returns_none(estate) -> None:
    calc_id = await estate["new_c3_calc"]()
    async with estate["pool"].acquire() as conn:
        calc = (await hydrate(conn, estate["graph_name"], "CalculatedField", [calc_id]))[calc_id]
    bad_pattern = PatternMatch(
        pattern_id="does-not-need-to-exist-for-this-check",
        class_="C3", target_template="NOT_A_REAL_DAX_FUNCTION({a})", promotion_state="ACTIVE",
    )
    # A real Pattern row is not required by apply_active_pattern itself for this failure
    # path, but record_observation's own FK-less table accepts any id -- what matters here
    # is that a template failing dax_sanity_check is refused, not silently written.
    measure_id = await apply_active_pattern(
        estate["pool"], estate["graph_name"], estate["writer"], estate["provenance"],
        calc_id=calc_id, calc_properties=calc, pattern=bad_pattern, graph_version=1, principal=PRINCIPAL,
    )
    assert measure_id is None

    async with estate["pool"].acquire() as conn:
        rows = await conn.fetch(
            "SELECT observed_pass FROM public.pattern_observation WHERE graph = $1 AND pattern_id = $2",
            estate["graph_name"], bad_pattern.pattern_id,
        )
    assert [r["observed_pass"] for r in rows] == [False]


async def test_generate_c3_field_applies_an_active_pattern_deterministically_never_calling_the_model(
    estate,
) -> None:
    seed_calc = await estate["new_c3_calc"]()
    caller = _ScriptedCaller(dax=f"CALCULATE(SUM([{estate['field_name']}]))")
    seeded = await generate_c3_field(
        estate["pool"], estate["graph_name"], estate["writer"], estate["provenance"], seed_calc,
        gateway=StaticGateway(caller), principal=PRINCIPAL,
    )
    await promote_pattern(
        estate["pool"], estate["graph_name"], estate["writer"],
        pattern_id=seeded.pattern_id, principal=PLATFORM_ENGINEER, threshold=1,
    )

    new_field = await _write(estate["writer"], "Field", name="Revenue", datatype="real", role="measure")
    new_calc = await estate["new_c3_calc"](field_name="Revenue")
    await _edge(estate["writer"], "DEPENDS_ON", new_calc, new_field, position_in_ast="args[0]")

    outcome = await generate_c3_field(
        estate["pool"], estate["graph_name"], estate["writer"], estate["provenance"], new_calc,
        gateway=_PoisonGateway(), principal=PRINCIPAL,
    )
    assert outcome.ok is True
    assert outcome.pattern_id == seeded.pattern_id
    assert outcome.attempts == ()  # the ladder never ran

    async with estate["pool"].acquire() as conn:
        measures = await hydrate(conn, estate["graph_name"], "Measure", [outcome.measure_id])
        calcs = await hydrate(conn, estate["graph_name"], "CalculatedField", [new_calc])

    measure = measures[outcome.measure_id]
    assert measure["dax"] == "CALCULATE(SUM([Revenue]))"  # this field's own real name, not the seed's
    assert measure["class"] == "C2"
    assert measure["pattern_ref"] == seeded.pattern_id

    calc = calcs[new_calc]
    assert calc["class"] == "C2"  # the AC's own "class of the field is re-evaluated to C2"
    assert calc["pattern_ref"] == seeded.pattern_id


async def test_find_matching_pattern_returns_none_for_an_unrelated_shape(estate) -> None:
    unrelated_ast = _aggregate("SUM", _ref("Something Else Entirely"))
    match = await find_matching_pattern(estate["pool"], estate["graph_name"], unrelated_ast)
    assert match is None


# ------------------------------------------------------------------- S5.5.2: retirement


async def _seed_active_pattern(estate) -> str:
    """One proof, promoted at threshold=1 -- the same minimal setup the bullet-2 tests
    already use, factored out since every retirement test needs an ACTIVE pattern first."""
    calc_id = await estate["new_c3_calc"]()
    caller = _ScriptedCaller(dax=f"CALCULATE(SUM([{estate['field_name']}]))")
    outcome = await generate_c3_field(
        estate["pool"], estate["graph_name"], estate["writer"], estate["provenance"], calc_id,
        gateway=StaticGateway(caller), principal=PRINCIPAL,
    )
    await promote_pattern(
        estate["pool"], estate["graph_name"], estate["writer"],
        pattern_id=outcome.pattern_id, principal=PRINCIPAL, threshold=1,
    )
    return outcome.pattern_id


async def test_a_failure_against_a_candidate_increments_failure_count_but_never_retires(estate) -> None:
    calc_id = await estate["new_c3_calc"]()
    caller = _ScriptedCaller(dax=f"CALCULATE(SUM([{estate['field_name']}]))")
    seeded = await generate_c3_field(
        estate["pool"], estate["graph_name"], estate["writer"], estate["provenance"], calc_id,
        gateway=StaticGateway(caller), principal=PRINCIPAL,
    )
    # Never promoted -- stays CANDIDATE. Five failures, well past the absolute threshold
    # of 3, must still never retire a pattern that was never ACTIVE (S5.5.2's own bullet 1
    # names "a proof failure attributed to an ACTIVE pattern", not any pattern).
    for _ in range(5):
        await record_failure_and_maybe_retire(
            estate["pool"], estate["graph_name"], estate["writer"],
            pattern_id=seeded.pattern_id, calc_id=calc_id, source="GENERATED_PROVED", principal=PRINCIPAL,
        )

    async with estate["pool"].acquire() as conn:
        patterns = await hydrate(conn, estate["graph_name"], "Pattern", [seeded.pattern_id])
    pattern = patterns[seeded.pattern_id]
    assert pattern["promotion_state"] == "CANDIDATE"
    assert pattern["failure_count"] == 5


async def test_an_active_pattern_is_retired_at_the_absolute_failure_count_threshold(estate) -> None:
    pattern_id = await _seed_active_pattern(estate)
    # Corrupt the template directly -- a real, checkable way for an ACTIVE pattern to
    # start producing bad DAX (independent of *why* -- the point under test is what
    # happens once it does), avoiding the need to actually break the proof pipeline.
    await estate["writer"].set_node_properties(
        pattern_id, {"target_template": "NOT_A_REAL_DAX_FUNCTION({a})"}, principal=PRINCIPAL,
    )

    async def apply_and_fail() -> None:
        calc_id = await estate["new_c3_calc"]()
        async with estate["pool"].acquire() as conn:
            calc = (await hydrate(conn, estate["graph_name"], "CalculatedField", [calc_id]))[calc_id]
        bad_pattern = PatternMatch(
            pattern_id=pattern_id, class_="C3",
            target_template="NOT_A_REAL_DAX_FUNCTION({a})", promotion_state="ACTIVE",
        )
        result = await apply_active_pattern(
            estate["pool"], estate["graph_name"], estate["writer"], estate["provenance"],
            calc_id=calc_id, calc_properties=calc, pattern=bad_pattern, graph_version=1, principal=PRINCIPAL,
        )
        assert result is None

    await apply_and_fail()
    await apply_and_fail()
    async with estate["pool"].acquire() as conn:
        still_active = await hydrate(conn, estate["graph_name"], "Pattern", [pattern_id])
    assert still_active[pattern_id]["promotion_state"] == "ACTIVE"
    assert still_active[pattern_id]["failure_count"] == 2

    await apply_and_fail()  # the third failure crosses DEFAULT_FAILURE_COUNT_THRESHOLD (3)

    async with estate["pool"].acquire() as conn:
        retired = await hydrate(conn, estate["graph_name"], "Pattern", [pattern_id])
    pattern = retired[pattern_id]
    assert pattern["promotion_state"] == "RETIRED"
    assert pattern["failure_count"] == 3
    assert "3 recorded failures" in pattern["provenance"]["retirement_reason"]
    assert pattern["provenance"]["retired_at"]
    # A prior promotion's own provenance is preserved, not overwritten by the retirement.
    assert pattern["provenance"]["approved_by"] == PRINCIPAL.value


async def test_retiring_a_pattern_via_the_pass_rate_condition_uses_overridable_thresholds(estate) -> None:
    pattern_id = await _seed_active_pattern(estate)
    calc_id = await estate["new_c3_calc"]()
    # A scaled-down window (min_applications=5) rather than the real default of 30 -- the
    # arithmetic itself is `test_patterns.py`'s own pure-function coverage; this proves the
    # DB-backed wrapper reads real observation counts and honours the same override.
    check = await record_failure_and_maybe_retire(
        estate["pool"], estate["graph_name"], estate["writer"],
        pattern_id=pattern_id, calc_id=calc_id, source="GENERATED_PROVED", principal=PRINCIPAL,
        failure_count_threshold=100, pass_rate_threshold=0.97, min_applications=1,
    )
    assert check is not None
    assert check.should_retire is True
    assert "pass rate" in check.reason

    async with estate["pool"].acquire() as conn:
        patterns = await hydrate(conn, estate["graph_name"], "Pattern", [pattern_id])
    assert patterns[pattern_id]["promotion_state"] == "RETIRED"


async def test_retiring_a_pattern_requeues_its_measures_for_regeneration(estate) -> None:
    pattern_id = await _seed_active_pattern(estate)
    new_calc = await estate["new_c3_calc"]()
    outcome = await generate_c3_field(
        estate["pool"], estate["graph_name"], estate["writer"], estate["provenance"], new_calc,
        gateway=_PoisonGateway(), principal=PRINCIPAL,
    )
    assert outcome.ok is True and outcome.pattern_id == pattern_id
    measure_id = outcome.measure_id

    await record_failure_and_maybe_retire(
        estate["pool"], estate["graph_name"], estate["writer"],
        pattern_id=pattern_id, calc_id=new_calc, source="GENERATED_PROVED", principal=PRINCIPAL,
        failure_count_threshold=1, pass_rate_threshold=0.97, min_applications=1000,
    )

    # `hydrate()` fetches by id regardless of retirement (callers are expected to have
    # already filtered live ids from NODE_INDEX_TABLE) -- so retirement itself is checked
    # the same authoritative way `_requeue_measures_for_retired_pattern` itself did it.
    from astra_graph.graph.queries import NODE_INDEX_TABLE

    async with estate["pool"].acquire() as conn:
        row = await conn.fetchrow(
            f"SELECT retired_at FROM {NODE_INDEX_TABLE} WHERE graph = $1 AND id = $2",
            estate["graph_name"], measure_id,
        )
        calcs = await hydrate(conn, estate["graph_name"], "CalculatedField", [new_calc])
    assert row is not None and row["retired_at"] is not None

    calc = calcs[new_calc]
    assert calc["class"] != "C2"  # reverted -- the pattern that made it C2 no longer applies
    assert calc["pattern_ref"] != pattern_id


async def test_retiring_a_pattern_raises_a_real_readable_event(estate) -> None:
    pattern_id = await _seed_active_pattern(estate)
    calc_id = await estate["new_c3_calc"]()
    await record_failure_and_maybe_retire(
        estate["pool"], estate["graph_name"], estate["writer"],
        pattern_id=pattern_id, calc_id=calc_id, source="GENERATED_PROVED", principal=PRINCIPAL,
        failure_count_threshold=1,
    )

    events = await estate["repository"].read_events(subject=pattern_id)
    retirements = [e for e in events if e.type == EventType.PATTERN_RETIRED]
    assert len(retirements) == 1
    event = retirements[0]
    assert event.label == "Pattern"
    assert event.data["pattern_id"] == pattern_id
    assert "recorded failures" in event.data["reason"]
    assert isinstance(event.data["requeued_measure_ids"], list)


# ---------------------------------------------------------------- S5.5.3: manual retirement


async def test_retire_pattern_manually_retires_a_never_promoted_candidate(estate) -> None:
    calc_id = await estate["new_c3_calc"]()
    caller = _ScriptedCaller(dax=f"CALCULATE(SUM([{estate['field_name']}]))")
    seeded = await generate_c3_field(
        estate["pool"], estate["graph_name"], estate["writer"], estate["provenance"], calc_id,
        gateway=StaticGateway(caller), principal=PRINCIPAL,
    )
    updated = await retire_pattern(
        estate["pool"], estate["graph_name"], estate["writer"],
        pattern_id=seeded.pattern_id, reason="this candidate's own shape is clearly a mistake",
        principal=PLATFORM_ENGINEER,
    )
    assert updated["promotion_state"] == "RETIRED"
    assert updated["provenance"]["retirement_reason"] == "this candidate's own shape is clearly a mistake"
    assert updated["provenance"]["retired_by"] == PLATFORM_ENGINEER.value

    events = await estate["repository"].read_events(subject=seeded.pattern_id)
    assert any(e.type == EventType.PATTERN_RETIRED for e in events)


async def test_retire_pattern_requires_a_real_reason(estate) -> None:
    calc_id = await estate["new_c3_calc"]()
    caller = _ScriptedCaller(dax=f"CALCULATE(SUM([{estate['field_name']}]))")
    seeded = await generate_c3_field(
        estate["pool"], estate["graph_name"], estate["writer"], estate["provenance"], calc_id,
        gateway=StaticGateway(caller), principal=PRINCIPAL,
    )
    with pytest.raises(PatternRetirementError, match="reason"):
        await retire_pattern(
            estate["pool"], estate["graph_name"], estate["writer"],
            pattern_id=seeded.pattern_id, reason="short", principal=PLATFORM_ENGINEER,
        )


async def test_retire_pattern_refuses_an_already_retired_pattern(estate) -> None:
    calc_id = await estate["new_c3_calc"]()
    caller = _ScriptedCaller(dax=f"CALCULATE(SUM([{estate['field_name']}]))")
    seeded = await generate_c3_field(
        estate["pool"], estate["graph_name"], estate["writer"], estate["provenance"], calc_id,
        gateway=StaticGateway(caller), principal=PRINCIPAL,
    )
    await retire_pattern(
        estate["pool"], estate["graph_name"], estate["writer"],
        pattern_id=seeded.pattern_id, reason="a real, specific rationale here",
        principal=PLATFORM_ENGINEER,
    )
    with pytest.raises(PatternRetirementError, match="already RETIRED"):
        await retire_pattern(
            estate["pool"], estate["graph_name"], estate["writer"],
            pattern_id=seeded.pattern_id, reason="a second, also real rationale",
            principal=PLATFORM_ENGINEER,
        )


async def test_retire_pattern_refuses_an_unknown_pattern(estate) -> None:
    with pytest.raises(PatternRetirementError, match="no Pattern"):
        await retire_pattern(
            estate["pool"], estate["graph_name"], estate["writer"],
            pattern_id="not-a-real-id", reason="a real, specific rationale here",
            principal=PLATFORM_ENGINEER,
        )


async def test_retire_pattern_manually_also_requeues_measures(estate) -> None:
    pattern_id = await _seed_active_pattern(estate)
    new_calc = await estate["new_c3_calc"]()
    outcome = await generate_c3_field(
        estate["pool"], estate["graph_name"], estate["writer"], estate["provenance"], new_calc,
        gateway=_PoisonGateway(), principal=PRINCIPAL,
    )
    assert outcome.ok is True and outcome.pattern_id == pattern_id

    from astra_graph.graph.queries import NODE_INDEX_TABLE

    await retire_pattern(
        estate["pool"], estate["graph_name"], estate["writer"],
        pattern_id=pattern_id, reason="governance decision -- retiring this one manually",
        principal=PLATFORM_ENGINEER,
    )
    async with estate["pool"].acquire() as conn:
        row = await conn.fetchrow(
            f"SELECT retired_at FROM {NODE_INDEX_TABLE} WHERE graph = $1 AND id = $2",
            estate["graph_name"], outcome.measure_id,
        )
        calcs = await hydrate(conn, estate["graph_name"], "CalculatedField", [new_calc])
    assert row is not None and row["retired_at"] is not None
    assert calcs[new_calc]["class"] != "C2"


# ---------------------------------------------------------------------- S5.5.3: edit guards


async def test_edit_guards_creates_a_new_version_and_retires_the_old_node(estate) -> None:
    pattern_id = await _seed_active_pattern(estate)
    async with estate["pool"].acquire() as conn:
        old = (await hydrate(conn, estate["graph_name"], "Pattern", [pattern_id]))[pattern_id]
    assert int(old.get("version") or 1) == 1

    new = await edit_guards(
        estate["pool"], estate["graph_name"], estate["writer"],
        pattern_id=pattern_id, guards=["a is a real, positive amount"],
        reason="the inferred guard was too vague for reviewers", principal=PLATFORM_ENGINEER,
    )
    assert new["id"] != pattern_id
    assert new["version"] == 2
    assert new["supersedes_id"] == pattern_id
    assert new["guards"] == ["a is a real, positive amount"]
    assert new["promotion_state"] == "ACTIVE"  # inherited -- guards don't change behaviour
    assert new["target_template"] == old["target_template"]
    assert new["provenance"]["edited_from"] == pattern_id
    assert new["provenance"]["edit_reason"] == "the inferred guard was too vague for reviewers"
    assert new["provenance"]["approved_by"] == PRINCIPAL.value  # carried forward from promotion

    from astra_graph.graph.queries import NODE_INDEX_TABLE

    async with estate["pool"].acquire() as conn:
        row = await conn.fetchrow(
            f"SELECT retired_at FROM {NODE_INDEX_TABLE} WHERE graph = $1 AND id = $2",
            estate["graph_name"], pattern_id,
        )
        new_hydrated = (await hydrate(conn, estate["graph_name"], "Pattern", [new["id"]]))[new["id"]]
    assert row is not None and row["retired_at"] is not None
    # `source_signature` is a real ontology property, checked against the raw hydrated
    # node -- `pattern_row`'s own return shape (what the console list renders) does not
    # carry it, since matching is an internal detail the screen has no use for.
    assert new_hydrated["source_signature"] == old["source_signature"]


async def test_edit_guards_new_version_is_the_one_matched_going_forward(estate) -> None:
    pattern_id = await _seed_active_pattern(estate)
    new = await edit_guards(
        estate["pool"], estate["graph_name"], estate["writer"],
        pattern_id=pattern_id, guards=["a stricter guard"],
        reason="tightening this pattern's own documented precondition", principal=PLATFORM_ENGINEER,
    )
    shape_ast = _window(
        estate["window_fn"], "table_calc_simple", _aggregate("SUM", _ref(estate["field_name"])),
    )
    match = await find_matching_pattern(estate["pool"], estate["graph_name"], shape_ast)
    assert match is not None
    assert match.pattern_id == new["id"]  # the retired old version is never matched again
    assert match.target_template == new["target_template"]


async def test_edit_guards_starts_a_fresh_observation_ledger(estate) -> None:
    pattern_id = await _seed_active_pattern(estate)  # already has 1 real proof pass
    new = await edit_guards(
        estate["pool"], estate["graph_name"], estate["writer"],
        pattern_id=pattern_id, guards=[], reason="removing a guard that no longer applies",
        principal=PLATFORM_ENGINEER,
    )
    status = await promotion_status(estate["pool"], estate["graph_name"], new["id"], threshold=5)
    # A fresh version starts its own append-only ledger -- the old version's own evidence
    # stays with the old (now-retired) id, honestly, rather than being silently inherited.
    assert status.distinct_passing_calcs == 0


async def test_edit_guards_requires_a_real_reason(estate) -> None:
    pattern_id = await _seed_active_pattern(estate)
    with pytest.raises(PatternRetirementError, match="reason"):
        await edit_guards(
            estate["pool"], estate["graph_name"], estate["writer"],
            pattern_id=pattern_id, guards=["x"], reason="short", principal=PLATFORM_ENGINEER,
        )


async def test_edit_guards_refuses_an_already_retired_pattern(estate) -> None:
    pattern_id = await _seed_active_pattern(estate)
    await retire_pattern(
        estate["pool"], estate["graph_name"], estate["writer"],
        pattern_id=pattern_id, reason="retiring before anyone tries to edit it",
        principal=PLATFORM_ENGINEER,
    )
    with pytest.raises(PatternRetirementError, match="RETIRED"):
        await edit_guards(
            estate["pool"], estate["graph_name"], estate["writer"],
            pattern_id=pattern_id, guards=["x"], reason="a real, specific rationale here",
            principal=PLATFORM_ENGINEER,
        )


# --------------------------------------------------------------- S5.5.3: list_patterns fields


async def test_list_patterns_reports_applications_pass_total_and_version(estate) -> None:
    calc_id = await estate["new_c3_calc"]()
    caller = _ScriptedCaller(dax=f"CALCULATE(SUM([{estate['field_name']}]))")
    outcome = await generate_c3_field(
        estate["pool"], estate["graph_name"], estate["writer"], estate["provenance"], calc_id,
        gateway=StaticGateway(caller), principal=PRINCIPAL,
    )
    from astra_graph.patterns import list_patterns

    patterns = await list_patterns(estate["pool"], estate["graph_name"])
    row = next(p for p in patterns if p["id"] == outcome.pattern_id)
    assert row["applications"] == 1
    assert row["pass_total"] == 1
    assert row["version"] == 1
    assert row["supersedes_id"] is None


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


async def test_list_patterns_over_http_is_open_to_any_artizent_role(http_client, estate) -> None:
    calc_id = await estate["new_c3_calc"]()
    caller = _ScriptedCaller(dax=f"CALCULATE(SUM([{estate['field_name']}]))")
    await generate_c3_field(
        estate["pool"], estate["graph_name"], estate["writer"], estate["provenance"], calc_id,
        gateway=StaticGateway(caller), principal=PRINCIPAL,
    )
    response = await http_client.get(
        "/v1/patterns", headers=_headers("programme_manager", PLATFORM_ENGINEER)
    )
    assert response.status_code == 200
    body = response.json()
    assert body["count"] >= 1
    assert body["patterns"][0]["distinct_passing_calcs"] >= 1


async def test_promote_over_http_requires_the_platform_engineer_role(http_client, estate) -> None:
    calc_id = await estate["new_c3_calc"]()
    caller = _ScriptedCaller(dax=f"CALCULATE(SUM([{estate['field_name']}]))")
    outcome = await generate_c3_field(
        estate["pool"], estate["graph_name"], estate["writer"], estate["provenance"], calc_id,
        gateway=StaticGateway(caller), principal=PRINCIPAL,
    )
    response = await http_client.post(
        f"/v1/patterns/{outcome.pattern_id}:promote",
        headers=_headers("parity_engineer", PLATFORM_ENGINEER), params={"threshold": 1},
    )
    assert response.status_code == 403


async def test_promote_over_http_with_the_platform_engineer_role_succeeds_at_threshold(
    http_client, estate
) -> None:
    calc_id = await estate["new_c3_calc"]()
    caller = _ScriptedCaller(dax=f"CALCULATE(SUM([{estate['field_name']}]))")
    outcome = await generate_c3_field(
        estate["pool"], estate["graph_name"], estate["writer"], estate["provenance"], calc_id,
        gateway=StaticGateway(caller), principal=PRINCIPAL,
    )
    response = await http_client.post(
        f"/v1/patterns/{outcome.pattern_id}:promote",
        headers=_headers("platform_engineer", PLATFORM_ENGINEER), params={"threshold": 1},
    )
    assert response.status_code == 200
    assert response.json()["promotion_state"] == "ACTIVE"


async def test_promote_over_http_refuses_below_threshold_as_a_400(http_client, estate) -> None:
    calc_id = await estate["new_c3_calc"]()
    caller = _ScriptedCaller(dax=f"CALCULATE(SUM([{estate['field_name']}]))")
    outcome = await generate_c3_field(
        estate["pool"], estate["graph_name"], estate["writer"], estate["provenance"], calc_id,
        gateway=StaticGateway(caller), principal=PRINCIPAL,
    )
    response = await http_client.post(
        f"/v1/patterns/{outcome.pattern_id}:promote",
        headers=_headers("platform_engineer", PLATFORM_ENGINEER),
    )
    assert response.status_code == 400


async def test_promotion_status_over_http_is_open_to_any_artizent_role(http_client, estate) -> None:
    calc_id = await estate["new_c3_calc"]()
    caller = _ScriptedCaller(dax=f"CALCULATE(SUM([{estate['field_name']}]))")
    outcome = await generate_c3_field(
        estate["pool"], estate["graph_name"], estate["writer"], estate["provenance"], calc_id,
        gateway=StaticGateway(caller), principal=PRINCIPAL,
    )
    response = await http_client.get(
        f"/v1/patterns/{outcome.pattern_id}:promotion-status",
        headers=_headers("programme_manager", PLATFORM_ENGINEER), params={"threshold": 5},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["distinct_passing_calcs"] == 1
    assert body["eligible"] is False


# --------------------------------------------------------- S5.5.3: retire/edit-guards over HTTP


async def test_retire_over_http_requires_the_platform_engineer_role(http_client, estate) -> None:
    calc_id = await estate["new_c3_calc"]()
    caller = _ScriptedCaller(dax=f"CALCULATE(SUM([{estate['field_name']}]))")
    outcome = await generate_c3_field(
        estate["pool"], estate["graph_name"], estate["writer"], estate["provenance"], calc_id,
        gateway=StaticGateway(caller), principal=PRINCIPAL,
    )
    response = await http_client.post(
        f"/v1/patterns/{outcome.pattern_id}:retire",
        headers=_headers("parity_engineer", PLATFORM_ENGINEER),
        json={"reason": "a real, specific rationale here"},
    )
    assert response.status_code == 403


async def test_retire_over_http_with_the_platform_engineer_role_succeeds(http_client, estate) -> None:
    calc_id = await estate["new_c3_calc"]()
    caller = _ScriptedCaller(dax=f"CALCULATE(SUM([{estate['field_name']}]))")
    outcome = await generate_c3_field(
        estate["pool"], estate["graph_name"], estate["writer"], estate["provenance"], calc_id,
        gateway=StaticGateway(caller), principal=PRINCIPAL,
    )
    response = await http_client.post(
        f"/v1/patterns/{outcome.pattern_id}:retire",
        headers=_headers("platform_engineer", PLATFORM_ENGINEER),
        json={"reason": "a real, specific governance rationale"},
    )
    assert response.status_code == 200
    assert response.json()["promotion_state"] == "RETIRED"


async def test_retire_over_http_rejects_an_empty_reason_as_a_422(http_client, estate) -> None:
    calc_id = await estate["new_c3_calc"]()
    caller = _ScriptedCaller(dax=f"CALCULATE(SUM([{estate['field_name']}]))")
    outcome = await generate_c3_field(
        estate["pool"], estate["graph_name"], estate["writer"], estate["provenance"], calc_id,
        gateway=StaticGateway(caller), principal=PRINCIPAL,
    )
    response = await http_client.post(
        f"/v1/patterns/{outcome.pattern_id}:retire",
        headers=_headers("platform_engineer", PLATFORM_ENGINEER), json={"reason": ""},
    )
    assert response.status_code == 422  # pydantic's own min_length=1, before the domain check


async def test_retire_over_http_rejects_a_too_short_reason_as_a_400(http_client, estate) -> None:
    calc_id = await estate["new_c3_calc"]()
    caller = _ScriptedCaller(dax=f"CALCULATE(SUM([{estate['field_name']}]))")
    outcome = await generate_c3_field(
        estate["pool"], estate["graph_name"], estate["writer"], estate["provenance"], calc_id,
        gateway=StaticGateway(caller), principal=PRINCIPAL,
    )
    response = await http_client.post(
        f"/v1/patterns/{outcome.pattern_id}:retire",
        headers=_headers("platform_engineer", PLATFORM_ENGINEER), json={"reason": "short"},
    )
    assert response.status_code == 400


async def test_edit_guards_over_http_requires_the_platform_engineer_role(http_client, estate) -> None:
    calc_id = await estate["new_c3_calc"]()
    caller = _ScriptedCaller(dax=f"CALCULATE(SUM([{estate['field_name']}]))")
    outcome = await generate_c3_field(
        estate["pool"], estate["graph_name"], estate["writer"], estate["provenance"], calc_id,
        gateway=StaticGateway(caller), principal=PRINCIPAL,
    )
    response = await http_client.post(
        f"/v1/patterns/{outcome.pattern_id}:edit-guards",
        headers=_headers("parity_engineer", PLATFORM_ENGINEER),
        json={"guards": ["a new guard"], "reason": "a real, specific rationale here"},
    )
    assert response.status_code == 403


async def test_edit_guards_over_http_with_the_platform_engineer_role_creates_a_new_version(
    http_client, estate
) -> None:
    calc_id = await estate["new_c3_calc"]()
    caller = _ScriptedCaller(dax=f"CALCULATE(SUM([{estate['field_name']}]))")
    outcome = await generate_c3_field(
        estate["pool"], estate["graph_name"], estate["writer"], estate["provenance"], calc_id,
        gateway=StaticGateway(caller), principal=PRINCIPAL,
    )
    response = await http_client.post(
        f"/v1/patterns/{outcome.pattern_id}:edit-guards",
        headers=_headers("platform_engineer", PLATFORM_ENGINEER),
        json={"guards": ["a new, clearer guard"], "reason": "clarifying this guard's own wording"},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["id"] != outcome.pattern_id
    assert body["version"] == 2
    assert body["guards"] == ["a new, clearer guard"]

    listing = await http_client.get(
        "/v1/patterns", headers=_headers("programme_manager", PLATFORM_ENGINEER)
    )
    ids = {p["id"] for p in listing.json()["patterns"]}
    assert body["id"] in ids
    assert outcome.pattern_id not in ids  # the superseded version no longer lists as live


async def test_edit_guards_over_http_rejects_a_too_short_reason_as_a_400(http_client, estate) -> None:
    calc_id = await estate["new_c3_calc"]()
    caller = _ScriptedCaller(dax=f"CALCULATE(SUM([{estate['field_name']}]))")
    outcome = await generate_c3_field(
        estate["pool"], estate["graph_name"], estate["writer"], estate["provenance"], calc_id,
        gateway=StaticGateway(caller), principal=PRINCIPAL,
    )
    response = await http_client.post(
        f"/v1/patterns/{outcome.pattern_id}:edit-guards",
        headers=_headers("platform_engineer", PLATFORM_ENGINEER),
        json={"guards": [], "reason": "short"},
    )
    assert response.status_code == 400
