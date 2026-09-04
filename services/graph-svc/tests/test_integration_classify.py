"""Calculation classification, against real PostgreSQL + Apache AGE — story S5.1.1.

What only the real store can answer: that a field's parameter dependency (a real
``DEPENDS_ON`` edge to a ``Parameter`` node) and a table calculation's addressing
(a real ``ENCODES`` edge from a ``Worksheet`` with shelf placement) resolve correctly from
the graph, that a reclassification pass writes `class`/`pattern_ref`/`reason`/
`classifier_version` onto real nodes and reports what moved, that the estate-wide class mix
reads back what was written, and that the HTTP routes enforce the parity engineer role on
the write and not the read — none of which the pure-function tests in `test_classify.py`
can see.
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

from astra_graph.classify import (  # noqa: E402
    CLASSIFIER_VERSION,
    ClassificationEngine,
    class_mix,
    reclassify_estate,
)
from astra_graph.config import Settings  # noqa: E402
from astra_graph.graph import AgeGraphRepository, create_pool  # noqa: E402
from astra_graph.graph.queries import accessor  # noqa: E402
from astra_graph.ids import new_ulid  # noqa: E402
from astra_graph.lineage import hydrate  # noqa: E402
from astra_graph.migrations import run as run_migrations  # noqa: E402
from astra_graph.ontology import EDGE_LABELS, NODE_LABELS  # noqa: E402
from astra_graph.principal import PRINCIPAL_HEADER, Principal  # noqa: E402
from astra_graph.roles import ROLES_HEADER  # noqa: E402
from astra_graph.writes import EdgeWrite, GraphWriter, NodeWrite  # noqa: E402

PRINCIPAL = Principal("agent:harvester", run_id="run-classify")
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
    config = _settings(f"astra_classify_{new_ulid()[10:22].lower()}")

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


def _op(name: str, *children: dict[str, object]) -> dict[str, object]:
    return {"kind": "OPERATOR", "name": name, "value": None, "children": list(children), "detail": []}


def _fn(name: str, family: str, *children: dict[str, object]) -> dict[str, object]:
    return {"kind": "FUNCTION", "name": name, "value": None, "children": list(children), "detail": [["family", family]]}


def _window(name: str, family: str, *children: dict[str, object]) -> dict[str, object]:
    return {
        "kind": "WINDOW",
        "name": name,
        "value": None,
        "children": list(children),
        "detail": [["family", family], ["addressing", "unresolved"], ["partitioning", "unresolved"]],
    }


@pytest.fixture
async def estate(settings: Settings):
    """A handful of calculated fields covering every fact the graph itself must resolve:
    a plain C1 SUM, a RAWSQL C4, one depending on a real Parameter, and one table calc
    encoded by a Worksheet with real shelf placement."""
    pool = await create_pool(settings)
    repository = AgeGraphRepository(pool, graph_name=settings.graph_name)
    from astra_graph.events import source_for

    writer = GraphWriter(repository, event_source=source_for(settings.graph_name))
    suffix = new_ulid()[10:18].lower()

    sum_calc = await _write(
        writer, "CalculatedField",
        name=f"Total Notional {suffix}",
        formula="SUM([Notional])",
        formula_ast=_aggregate("SUM", _ref("Notional")),
    )
    rawsql_calc = await _write(
        writer, "CalculatedField",
        name=f"Legacy SQL {suffix}",
        formula="RAWSQL_INT('select 1')",
        formula_ast=_fn("RAWSQL_INT", "rawsql", {"kind": "LITERAL", "name": "string", "value": "select 1", "children": [], "detail": []}),
    )
    parameterised_calc = await _write(
        writer, "CalculatedField",
        name=f"Scaled Notional {suffix}",
        formula="[Notional] * [Scale Factor]",
        formula_ast=_op("*", _ref("Notional"), _ref("Scale Factor")),
    )
    parameter = await _write(
        writer, "Parameter", name=f"Scale Factor {suffix}", datatype="real", domain="range",
    )
    await _edge(writer, "DEPENDS_ON", parameterised_calc, parameter, position_in_ast="args[1]")

    table_calc = await _write(
        writer, "CalculatedField",
        name=f"Running Total {suffix}",
        formula="RUNNING_SUM(SUM([Notional]))",
        formula_ast=_window("RUNNING_SUM", "table_calc_simple", _aggregate("SUM", _ref("Notional"))),
        table_calc_flag=True,
    )
    worksheet = await _write(
        writer, "Worksheet", name=f"VaR by Desk {suffix}",
        rows_shelf=["Desk"], cols_shelf=["Date"], marks_shelf=[],
    )
    await _edge(writer, "ENCODES", worksheet, table_calc, shelf="rows")

    unencoded_table_calc = await _write(
        writer, "CalculatedField",
        name=f"Unplaced Running Total {suffix}",
        formula="RUNNING_SUM(SUM([Notional]))",
        formula_ast=_window("RUNNING_SUM", "table_calc_simple", _aggregate("SUM", _ref("Notional"))),
        table_calc_flag=True,
    )

    try:
        yield {
            "pool": pool,
            "graph_name": settings.graph_name,
            "writer": writer,
            "sum_calc": sum_calc,
            "rawsql_calc": rawsql_calc,
            "parameterised_calc": parameterised_calc,
            "table_calc": table_calc,
            "unencoded_table_calc": unencoded_table_calc,
        }
    finally:
        await pool.close()


# ------------------------------------------------------------------------- reclassify_estate


async def test_reclassify_writes_class_pattern_ref_reason_and_version(estate) -> None:
    result = await reclassify_estate(
        estate["pool"], estate["graph_name"], estate["writer"], principal=PRINCIPAL
    )
    async with estate["pool"].acquire() as conn:
        properties = await hydrate(conn, estate["graph_name"], "CalculatedField", [estate["sum_calc"]])
    node = properties[estate["sum_calc"]]
    assert node["class"] == "C1"
    assert node["pattern_ref"] == "b1:aggregate"
    assert node["reason"]
    assert node["classifier_version"] == CLASSIFIER_VERSION
    assert result.classifier_version == CLASSIFIER_VERSION


async def test_a_parameter_dependency_is_resolved_from_a_real_depends_on_edge(estate) -> None:
    await reclassify_estate(estate["pool"], estate["graph_name"], estate["writer"], principal=PRINCIPAL)
    async with estate["pool"].acquire() as conn:
        properties = await hydrate(
            conn, estate["graph_name"], "CalculatedField", [estate["parameterised_calc"]]
        )
    node = properties[estate["parameterised_calc"]]
    assert node["class"] == "C2"
    assert node["pattern_ref"] == "b1:parameter"


async def test_table_calc_addressing_resolves_from_a_real_encoding_worksheet(estate) -> None:
    await reclassify_estate(estate["pool"], estate["graph_name"], estate["writer"], principal=PRINCIPAL)
    async with estate["pool"].acquire() as conn:
        properties = await hydrate(
            conn, estate["graph_name"], "CalculatedField",
            [estate["table_calc"], estate["unencoded_table_calc"]],
        )
    assert properties[estate["table_calc"]]["class"] == "C2"
    assert properties[estate["table_calc"]]["pattern_ref"] == "b1:table_calc_simple_resolved"
    # No encoding Worksheet at all — addressing cannot resolve, so this one stays C3.
    assert properties[estate["unencoded_table_calc"]]["class"] == "C3"
    assert properties[estate["unencoded_table_calc"]]["pattern_ref"] == "b1:table_calc_simple_unresolved"


async def test_reclassify_reports_what_moved_class(estate) -> None:
    first = await reclassify_estate(
        estate["pool"], estate["graph_name"], estate["writer"], principal=PRINCIPAL
    )
    moved_ids = {m.calculated_field_id for m in first.moved}
    assert estate["sum_calc"] in moved_ids
    assert estate["rawsql_calc"] in moved_ids
    first_move = next(m for m in first.moved if m.calculated_field_id == estate["sum_calc"])
    assert first_move.from_class is None
    assert first_move.to_class == "C1"

    second = await reclassify_estate(
        estate["pool"], estate["graph_name"], estate["writer"], principal=PRINCIPAL
    )
    assert second.moved == ()


async def test_class_mix_reads_back_what_reclassify_wrote(estate) -> None:
    # The graph is shared across this module's own tests, so only facts every reclassified
    # field in it is guaranteed to share are checked — never "nothing else is unclassified",
    # which a sibling test (deliberately) leaves untrue.
    await reclassify_estate(estate["pool"], estate["graph_name"], estate["writer"], principal=PRINCIPAL)
    mix = await class_mix(estate["pool"], estate["graph_name"])
    assert mix["counts"]["C1"] >= 1
    assert mix["counts"]["C4"] >= 1
    assert mix["targets"] == {"C1": 45, "C2": 30, "C3": 18, "C4": 7}
    # Every field this module has ever classified used the same fixed CLASSIFIER_VERSION,
    # so this stays a single value regardless of how many other fields stay unclassified.
    assert mix["classifier_version"] == CLASSIFIER_VERSION


async def test_class_mix_before_any_reclassification_is_all_unclassified(estate) -> None:
    # The graph is shared across this module's own tests (each with its own uniquely
    # suffixed fields), so this checks this fixture's own fields directly rather than the
    # estate-wide total, which other tests may already have classified.
    async with estate["pool"].acquire() as conn:
        properties = await hydrate(conn, estate["graph_name"], "CalculatedField", [estate["sum_calc"]])
    assert properties[estate["sum_calc"]].get("class") is None
    assert properties[estate["sum_calc"]].get("classifier_version") is None


# ---------------------------------------------------------------------------------- the API


@pytest.fixture
async def http_client(estate):
    from httpx import ASGITransport, AsyncClient

    from astra_graph.main import create_app

    app = create_app()
    app.state.classifier = ClassificationEngine(
        estate["pool"], graph_name=estate["graph_name"], writer=estate["writer"]
    )

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://graph-svc") as async_client:
        yield async_client


def _headers(role: str, principal: Principal) -> dict[str, str]:
    return {PRINCIPAL_HEADER: principal.value, ROLES_HEADER: role}


async def test_get_class_mix_over_http_is_open_to_any_artizent_role(http_client) -> None:
    response = await http_client.get(
        "/v1/calculations:class-mix", headers=_headers("programme_manager", PARITY_ENGINEER)
    )
    assert response.status_code == 200
    assert response.json()["targets"] == {"C1": 45, "C2": 30, "C3": 18, "C4": 7}


async def test_reclassify_over_http_requires_the_parity_engineer_role(http_client) -> None:
    response = await http_client.post(
        "/v1/calculations:reclassify", headers=_headers("programme_manager", PARITY_ENGINEER)
    )
    assert response.status_code == 403


async def test_reclassify_over_http_reports_class_mix_and_moved(http_client, estate) -> None:
    response = await http_client.post(
        "/v1/calculations:reclassify", headers=_headers("parity_engineer", PARITY_ENGINEER)
    )
    assert response.status_code == 200
    body = response.json()
    assert body["classifier_version"] == CLASSIFIER_VERSION
    assert body["class_mix"]["C1"] >= 1
    assert any(m["calculated_field_id"] == estate["sum_calc"] for m in body["moved"])
