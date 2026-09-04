"""The deterministic rules engine, against real PostgreSQL + Apache AGE — stories S5.2.1
and S5.2.2.

What only the real store can answer: that applying rules writes a real `Measure` node, a
real `MAPS_TO` edge carrying the rule id, and a real DETERMINISTIC `ProvenanceRecord`
citing the rule id and version; that a re-run does not duplicate a `Measure` already
produced for the same `CalculatedField`; that the coverage report reads back what was
written, broken down by rule family; that the HTTP routes enforce the platform engineer
role on the write and not the read; and (S5.2.2) that `check_regression` correctly tells a
real, already-produced artefact that still renders the same way (unchanged), one that
renders differently under the current rule set (changed, disclosed, not blocking), and one
a rule no longer covers at all (regressed, blocking) apart from real graph data — none of
which the pure-function tests in `test_rules.py` can see.
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
from astra_graph.graph import AgeGraphRepository, create_pool  # noqa: E402
from astra_graph.graph.queries import accessor  # noqa: E402
from astra_graph.ids import new_ulid  # noqa: E402
from astra_graph.lineage import children, hydrate  # noqa: E402
from astra_graph.migrations import run as run_migrations  # noqa: E402
from astra_graph.ontology import EDGE_LABELS, NODE_LABELS  # noqa: E402
from astra_graph.principal import PRINCIPAL_HEADER, Principal  # noqa: E402
from astra_graph.provenance import PostgresProvenanceStore  # noqa: E402
from astra_graph.roles import ROLES_HEADER  # noqa: E402
from astra_graph.rules import (  # noqa: E402
    RULES_VERSION,
    RulesEngine,
    apply_rules_estate,
    check_regression,
    rule_coverage,
)
from astra_graph.writes import EdgeWrite, GraphWriter, NodeWrite  # noqa: E402

PRINCIPAL = Principal("agent:harvester", run_id="run-rules")
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
    config = _settings(f"astra_rules_{new_ulid()[10:22].lower()}")

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


async def _write_measure(writer: GraphWriter, properties: dict[str, Any]) -> str:
    # A plain dict rather than `_write`'s own **kwargs helper: `class` is a Python keyword
    # and cannot be passed as one, but it is the ontology's own literal property name here.
    created = await writer.write_nodes(
        [NodeWrite(type="Measure", properties=properties)], principal=PRINCIPAL
    )
    return str(created[0]["properties"]["id"])


def _ref(name: str) -> dict[str, object]:
    return {"kind": "REFERENCE", "name": name, "value": None, "children": [], "detail": []}


def _aggregate(name: str, *children: dict[str, object]) -> dict[str, object]:
    return {"kind": "AGGREGATE", "name": name, "value": None, "children": list(children), "detail": [["family", "aggregate"]]}


def _op(name: str, *children: dict[str, object]) -> dict[str, object]:
    return {"kind": "OPERATOR", "name": name, "value": None, "children": list(children), "detail": []}


def _fn(name: str, family: str, *children: dict[str, object]) -> dict[str, object]:
    return {"kind": "FUNCTION", "name": name, "value": None, "children": list(children), "detail": [["family", family]]}


@pytest.fixture
async def estate(settings: Settings):
    """A handful of calculated fields covering a rule-coverable C1, a rule-coverable C2, and
    one nothing here can render (RAWSQL) -- the coverage report's own "not everything
    matches" case."""
    pool = await create_pool(settings)
    repository = AgeGraphRepository(pool, graph_name=settings.graph_name)
    from astra_graph.events import source_for

    writer = GraphWriter(repository, event_source=source_for(settings.graph_name))
    provenance = PostgresProvenanceStore(pool, graph_name=settings.graph_name)
    suffix = new_ulid()[10:18].lower()

    sum_calc = await _write(
        writer, "CalculatedField",
        name=f"Total Notional {suffix}",
        formula="SUM([Notional])",
        formula_ast=_aggregate("SUM", _ref("Notional")),
    )
    null_idiom_calc = await _write(
        writer, "CalculatedField",
        name=f"Notional Or Zero {suffix}",
        formula="ZN([Notional])",
        formula_ast=_fn("ZN", "logical", _ref("Notional")),
    )
    uncovered_calc = await _write(
        writer, "CalculatedField",
        name=f"Legacy SQL {suffix}",
        formula="RAWSQL_INT('select 1')",
        formula_ast=_fn("RAWSQL_INT", "rawsql", {"kind": "LITERAL", "name": "string", "value": "select 1", "children": [], "detail": []}),
    )
    composed_calc = await _write(
        writer, "CalculatedField",
        name=f"Margin Ratio {suffix}",
        formula="SUM([Margin]) / SUM([Revenue])",
        formula_ast=_op("/", _aggregate("SUM", _ref("Margin")), _aggregate("SUM", _ref("Revenue"))),
    )

    try:
        yield {
            "pool": pool,
            "graph_name": settings.graph_name,
            "writer": writer,
            "provenance": provenance,
            "sum_calc": sum_calc,
            "null_idiom_calc": null_idiom_calc,
            "uncovered_calc": uncovered_calc,
            "composed_calc": composed_calc,
        }
    finally:
        await pool.close()


# --------------------------------------------------------------------- apply_rules_estate


async def test_apply_rules_writes_a_real_measure_maps_to_and_provenance(estate) -> None:
    result = await apply_rules_estate(
        estate["pool"], estate["graph_name"], estate["writer"], estate["provenance"], principal=PLATFORM_ENGINEER
    )
    assert result.rules_version == RULES_VERSION

    async with estate["pool"].acquire() as conn:

        maps_to = await children(conn, estate["graph_name"], [estate["sum_calc"]], "MAPS_TO", "Measure")
    assert maps_to.get(estate["sum_calc"])
    measure_id = next(iter(maps_to[estate["sum_calc"]]))

    async with estate["pool"].acquire() as conn:
        measures = await hydrate(conn, estate["graph_name"], "Measure", [measure_id])
    measure = measures[measure_id]
    assert measure["dax"] == "SUM([Notional])"
    assert measure["class"] == "C1"
    assert measure["pattern_ref"] == "c1_aggregate:v1"
    assert measure["source_calc_ref"] == estate["sum_calc"]
    assert measure["provenance_ref"]

    record = await estate["provenance"].get(measure["provenance_ref"])
    assert record is not None
    assert record.mode.value == "DETERMINISTIC"
    assert record.model is None
    assert record.pattern_ref == "c1_aggregate:v1"
    assert record.artefact_kind == "MEASURE"
    assert record.artefact_ref == measure_id
    assert record.subject_id == estate["sum_calc"]


async def test_a_composed_expression_renders_through_a_real_maps_to_edge(estate) -> None:
    await apply_rules_estate(
        estate["pool"], estate["graph_name"], estate["writer"], estate["provenance"], principal=PLATFORM_ENGINEER
    )
    async with estate["pool"].acquire() as conn:

        maps_to = await children(conn, estate["graph_name"], [estate["composed_calc"]], "MAPS_TO", "Measure")
    measure_id = next(iter(maps_to[estate["composed_calc"]]))
    async with estate["pool"].acquire() as conn:
        measures = await hydrate(conn, estate["graph_name"], "Measure", [measure_id])
    assert measures[measure_id]["dax"] == "(SUM([Margin]) / SUM([Revenue]))"
    assert measures[measure_id]["pattern_ref"] == "c1_operator:v1"


async def test_a_shape_matched_c2_field_writes_its_own_rule(estate) -> None:
    await apply_rules_estate(
        estate["pool"], estate["graph_name"], estate["writer"], estate["provenance"], principal=PLATFORM_ENGINEER
    )
    async with estate["pool"].acquire() as conn:

        maps_to = await children(conn, estate["graph_name"], [estate["null_idiom_calc"]], "MAPS_TO", "Measure")
    measure_id = next(iter(maps_to[estate["null_idiom_calc"]]))
    async with estate["pool"].acquire() as conn:
        measures = await hydrate(conn, estate["graph_name"], "Measure", [measure_id])
    assert measures[measure_id]["dax"] == "COALESCE([Notional], 0)"
    assert measures[measure_id]["class"] == "C2"
    assert measures[measure_id]["pattern_ref"] == "c2_null_idiom:v1"


async def test_a_field_no_shipped_rule_covers_gets_no_measure(estate) -> None:
    await apply_rules_estate(
        estate["pool"], estate["graph_name"], estate["writer"], estate["provenance"], principal=PLATFORM_ENGINEER
    )
    async with estate["pool"].acquire() as conn:

        maps_to = await children(conn, estate["graph_name"], [estate["uncovered_calc"]], "MAPS_TO", "Measure")
    assert not maps_to.get(estate["uncovered_calc"])


async def test_a_second_pass_does_not_duplicate_an_already_converted_measure(estate) -> None:
    first = await apply_rules_estate(
        estate["pool"], estate["graph_name"], estate["writer"], estate["provenance"], principal=PLATFORM_ENGINEER
    )
    second = await apply_rules_estate(
        estate["pool"], estate["graph_name"], estate["writer"], estate["provenance"], principal=PLATFORM_ENGINEER
    )
    assert estate["sum_calc"] in {a.calculated_field_id for a in first.applied}
    assert estate["sum_calc"] not in {a.calculated_field_id for a in second.applied}


# ------------------------------------------------------------------------------ coverage


async def test_rule_coverage_reads_back_what_was_written(estate) -> None:
    await apply_rules_estate(
        estate["pool"], estate["graph_name"], estate["writer"], estate["provenance"], principal=PLATFORM_ENGINEER
    )
    coverage = await rule_coverage(estate["pool"], estate["graph_name"])
    assert coverage["rules_version"] == RULES_VERSION
    assert coverage["matched"] >= 3
    assert coverage["by_family"].get("aggregate", 0) >= 1
    assert coverage["by_family"].get("logical", 0) >= 1
    assert coverage["by_family"].get("operator", 0) >= 1


# --------------------------------------------------------------------- check_regression (S5.2.2)


async def test_regression_check_reports_unchanged_for_a_field_still_reproduced(estate) -> None:
    # The graph is shared across this module's own tests (a sibling test deliberately
    # seeds a real regression elsewhere in it), so only this test's own artefact is
    # asserted on here rather than the whole report's own `ok`.
    await apply_rules_estate(
        estate["pool"], estate["graph_name"], estate["writer"], estate["provenance"], principal=PLATFORM_ENGINEER
    )
    report = await check_regression(estate["pool"], estate["graph_name"])
    regressed_calcs = {r.calculated_field_id for r in report.regressed}
    changed_calcs = {c.calculated_field_id for c in report.changed}
    assert estate["sum_calc"] not in regressed_calcs
    assert estate["sum_calc"] not in changed_calcs
    assert report.unchanged >= 1


async def test_regression_check_flags_a_measure_no_rule_covers_any_longer(estate) -> None:
    # Simulates a rule that has since been retired or tightened: a Measure whose own
    # pattern_ref cites a real rule id, but whose source calculation the *current* rule
    # set genuinely cannot render (a RAWSQL construct, always C4).
    uncovered_source = await _write(
        estate["writer"], "CalculatedField",
        name="Retired Rule Source",
        formula="RAWSQL_INT('select 2')",
        formula_ast=_fn("RAWSQL_INT", "rawsql", {"kind": "LITERAL", "name": "string", "value": "select 2", "children": [], "detail": []}),
    )
    provenance_id = f"prov_{new_ulid()}"
    measure_id = await _write_measure(estate["writer"], {
        "name": "Stale Measure",
        "dax": "SUM([Notional])",
        "source_calc_ref": uncovered_source,
        "class": "C1",
        "pattern_ref": "c1_aggregate:v1",
        "provenance_ref": provenance_id,
    })
    await _edge(estate["writer"], "MAPS_TO", uncovered_source, measure_id, **{"class": "C1", "pattern_ref": "c1_aggregate:v1"})

    report = await check_regression(estate["pool"], estate["graph_name"])
    assert not report.ok
    regressed = {r.calculated_field_id: r for r in report.regressed}
    assert uncovered_source in regressed
    assert regressed[uncovered_source].rule_id == "c1_aggregate"
    assert regressed[uncovered_source].measure_id == measure_id


async def test_regression_check_reports_a_different_rendering_as_changed_not_regressed(estate) -> None:
    # A Measure whose own stored text no longer matches what the *current* rule produces
    # for the very same source AST -- simulating a rule's rendering format changing between
    # versions. Still renders, so this is a disclosed change, not a blocking regression.
    stale_source = await _write(
        estate["writer"], "CalculatedField",
        name="Reformatted Measure Source",
        formula="SUM([Notional])",
        formula_ast=_aggregate("SUM", _ref("Notional")),
    )
    provenance_id = f"prov_{new_ulid()}"
    measure_id = await _write_measure(estate["writer"], {
        "name": "Stale Formatting Measure",
        "dax": "SUM ( [Notional] )",  # not what c1_aggregate renders today
        "source_calc_ref": stale_source,
        "class": "C1",
        "pattern_ref": "c1_aggregate:v1",
        "provenance_ref": provenance_id,
    })
    await _edge(estate["writer"], "MAPS_TO", stale_source, measure_id, **{"class": "C1", "pattern_ref": "c1_aggregate:v1"})

    # The graph is shared across this module's own tests (a sibling test deliberately
    # seeds a real regression), so only this test's own artefact is asserted on here --
    # "a change alone never blocks" is proven directly at the unit level, on
    # `RegressionReport.ok`, in `test_rules.py`.
    report = await check_regression(estate["pool"], estate["graph_name"])
    changed = {c.calculated_field_id: c for c in report.changed}
    assert stale_source in changed
    assert changed[stale_source].previous_dax == "SUM ( [Notional] )"
    assert changed[stale_source].current_dax == "SUM([Notional])"
    assert stale_source not in {r.calculated_field_id for r in report.regressed}


# ---------------------------------------------------------------------------------- the API


@pytest.fixture
async def http_client(estate):
    from httpx import ASGITransport, AsyncClient

    from astra_graph.main import create_app

    app = create_app()
    app.state.rules_engine = RulesEngine(
        estate["pool"], graph_name=estate["graph_name"], writer=estate["writer"], provenance_store=estate["provenance"],
    )

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://graph-svc") as async_client:
        yield async_client


def _headers(role: str, principal: Principal) -> dict[str, str]:
    return {PRINCIPAL_HEADER: principal.value, ROLES_HEADER: role}


async def test_get_rule_catalog_over_http_is_open_to_any_artizent_role(http_client) -> None:
    response = await http_client.get(
        "/v1/calculations:rule-catalog", headers=_headers("programme_manager", PLATFORM_ENGINEER)
    )
    assert response.status_code == 200
    ids = {rule["id"] for rule in response.json()["rules"]}
    assert "c1_aggregate" in ids
    assert "c2_lod_fixed" in ids


async def test_get_rule_coverage_over_http_is_open_to_any_artizent_role(http_client) -> None:
    response = await http_client.get(
        "/v1/calculations:rule-coverage", headers=_headers("programme_manager", PLATFORM_ENGINEER)
    )
    assert response.status_code == 200
    assert "by_family" in response.json()


async def test_get_rule_regression_over_http_is_open_to_any_artizent_role(http_client) -> None:
    response = await http_client.get(
        "/v1/calculations:rule-regression", headers=_headers("programme_manager", PLATFORM_ENGINEER)
    )
    assert response.status_code == 200
    body = response.json()
    assert "ok" in body
    assert "regressed" in body


async def test_apply_rules_over_http_requires_the_platform_engineer_role(http_client) -> None:
    response = await http_client.post(
        "/v1/calculations:apply-rules", headers=_headers("programme_manager", PLATFORM_ENGINEER)
    )
    assert response.status_code == 403


async def test_apply_rules_over_http_reports_matched_and_by_family(http_client, estate) -> None:
    response = await http_client.post(
        "/v1/calculations:apply-rules", headers=_headers("platform_engineer", PLATFORM_ENGINEER)
    )
    assert response.status_code == 200
    body = response.json()
    assert body["rules_version"] == RULES_VERSION
    assert body["matched"] >= 3
    assert any(a["calculated_field_id"] == estate["sum_calc"] for a in body["applied"])
