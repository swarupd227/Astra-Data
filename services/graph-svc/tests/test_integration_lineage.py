"""The Lineage View's reads, against real PostgreSQL + Apache AGE.

The scoring is pure and tested in the unit suite. What only the real store can answer is
whether the *reach* is right: a workbook's tables are four hops away
(Workbook → Worksheet → Datasource → Connection → Table), and a mistake anywhere along
that chain gives a model engineer a similarity of zero for two workbooks built on the same
warehouse — a confident number that means nothing, which is the failure S1.4.2 exists to
prevent.
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
from astra_graph.events import source_for  # noqa: E402
from astra_graph.graph import AgeGraphRepository, create_pool  # noqa: E402
from astra_graph.graph.queries import accessor  # noqa: E402
from astra_graph.ids import new_ulid  # noqa: E402
from astra_graph.lineage import LineageReader  # noqa: E402
from astra_graph.migrations import run as run_migrations  # noqa: E402
from astra_graph.ontology import EDGE_LABELS, NODE_LABELS  # noqa: E402
from astra_graph.principal import Principal  # noqa: E402
from astra_graph.writes import EdgeWrite, GraphWriter, NodeWrite  # noqa: E402

PRINCIPAL = Principal("agent:harvester", run_id="run-lineage")


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
    config = _settings(f"astra_lin_{new_ulid()[10:22].lower()}")

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
        EdgeWrite(type=type_, from_id=source, to_id=target, properties=props),
        principal=PRINCIPAL,
    )


@pytest.fixture
async def estate(settings: Settings):
    """Three workbooks over two warehouse tables, with the whole chain in between.

        Site → Project → Workbook → Worksheet → Datasource → Connection → Table
                                              → Field / CalculatedField

    Workbooks A and B share a table, a field and a calculation shape. C shares nothing —
    it is the control, and without it a reader that returned every pair would still pass.
    """
    pool = await create_pool(settings)
    try:
        repository = AgeGraphRepository(pool, graph_name=settings.graph_name)
        writer = GraphWriter(repository, event_source=source_for(settings.graph_name))
        suffix = new_ulid()[10:18].lower()

        site = await _write(writer, "Site", luid=f"s-{suffix}", name=f"RQA {suffix}")
        project = await _write(writer, "Project", luid=f"p-{suffix}", name="Risk Core")
        await _edge(writer, "CONTAINS", site, project)

        shared_table = await _write(writer, "Table", name="positions", schema="risk")
        other_table = await _write(writer, "Table", name="prices", schema="mkt")
        shared_field = await _write(
            writer, "Field", name="Notional", datatype="real", role="measure"
        )
        other_field = await _write(
            writer, "Field", name="Spread", datatype="real", role="measure"
        )

        ratio_ast = {
            "op": "DIV",
            "args": [
                {"fn": "SUM", "arg": {"field": "Margin"}},
                {"fn": "SUM", "arg": {"field": "Revenue"}},
            ],
        }

        async def workbook(name: str, table: str, field: str, ast: Any) -> str:
            book = await _write(
                writer, "Workbook", luid=f"{name}-{suffix}", name=name, revision="1"
            )
            await _edge(writer, "CONTAINS", project, book)
            sheet = await _write(
                writer,
                "Worksheet",
                name=f"{name} sheet",
                rows_shelf=["Desk"],
                cols_shelf=["Date"],
                marks_shelf=[],
            )
            await _edge(writer, "CONTAINS", book, sheet)
            datasource = await _write(
                writer, "Datasource", name=f"{name} ds", type="published", luid=f"ds-{name}-{suffix}"
            )
            await _edge(writer, "USES_DATASOURCE", sheet, datasource)
            connection = await _write(
                writer, "Connection", **{"class": "postgres"}, server="warehouse", db="risk"
            )
            await _edge(writer, "CONNECTS_TO", datasource, connection)
            await _edge(writer, "CONNECTS_TO", connection, table)
            await _edge(writer, "ENCODES", sheet, field, shelf="rows")
            calc = await _write(
                writer,
                "CalculatedField",
                name=f"{name} margin",
                formula="SUM([M]) / SUM([R])",
                formula_ast=ast,
            )
            await _edge(writer, "ENCODES", sheet, calc, shelf="rows")
            return book

        a = await workbook("Alpha", shared_table, shared_field, ratio_ast)
        b = await workbook("Bravo", shared_table, shared_field, ratio_ast)
        c = await workbook("Charlie", other_table, other_field, {"fn": "SUM", "arg": {"field": "X"}})

        yield {
            "pool": pool,
            "settings": settings,
            "writer": writer,
            "reader": LineageReader(pool, graph_name=settings.graph_name),
            "site": f"RQA {suffix}",
            "a": a,
            "b": b,
            "c": c,
            "shared_table": shared_table,
        }
    finally:
        await pool.close()


async def test_a_workbook_reaches_its_tables_through_the_whole_chain(estate) -> None:
    """Four hops. A mistake at any one of them scores two workbooks on the same warehouse
    as sharing nothing."""
    graph = await estate["reader"].read(site=estate["site"], min_strength=0.0)

    tables = {node.id for node in graph.nodes if node.type == "Table"}
    assert estate["shared_table"] in tables

    reaches = {
        (edge.source, edge.target)
        for edge in graph.edges
        if edge.target == estate["shared_table"]
    }
    assert reaches == {(estate["a"], estate["shared_table"]), (estate["b"], estate["shared_table"])}


async def test_two_workbooks_on_the_same_lineage_score_and_a_third_does_not(estate) -> None:
    """The control matters: a reader that linked every pair would pass without Charlie."""
    graph = await estate["reader"].read(site=estate["site"], min_strength=0.0)
    links = {(link.source, link.target): link for link in graph.shared}

    pair = tuple(sorted((estate["a"], estate["b"])))
    assert pair in links
    assert links[pair].strength == pytest.approx(1.0), "identical lineage"
    assert links[pair].jaccard_tables == pytest.approx(1.0)
    assert links[pair].shared_shapes == 1, "the same AST shape, different calc nodes"

    assert not any(estate["c"] in key for key in links), "Charlie shares nothing"


async def test_the_shared_calculation_shape_is_matched_not_the_node(estate) -> None:
    """Alpha and Bravo define *different* CalculatedField nodes with the same shape. §12.1
    counts shapes, so this is the difference between "these two do the same thing" and
    "these two are the same row"."""
    graph = await estate["reader"].read(site=estate["site"], min_strength=0.0)

    calcs = {node.id for node in graph.nodes if node.type == "CalculatedField"}
    assert len(calcs) == 3, "three distinct calculated fields"

    pair = tuple(sorted((estate["a"], estate["b"])))
    link = next(link for link in graph.shared if (link.source, link.target) == pair)
    assert link.shared_shapes == 1


async def test_the_scope_can_be_narrowed_to_a_selection(estate) -> None:
    graph = await estate["reader"].read(workbook_ids=[estate["a"]], min_strength=0.0)

    workbooks = [node.id for node in graph.nodes if node.type == "Workbook"]
    assert workbooks == [estate["a"]]
    assert graph.shared == [], "one workbook shares lineage with nobody"


async def test_the_threshold_drops_weak_links(estate) -> None:
    strong = await estate["reader"].read(site=estate["site"], min_strength=0.99)
    assert len(strong.shared) == 1, "only the identical pair survives"


async def test_a_family_scopes_the_view_and_is_reported(estate) -> None:
    """S1.4.2: "selecting a family highlights its members"."""
    family = await _write(
        estate["writer"], "ModelFamily", name="mf_risk_positions", state="PROPOSED"
    )
    for workbook in (estate["a"], estate["b"]):
        await _edge(estate["writer"], "IN_FAMILY", workbook, family, confidence=0.91)

    graph = await estate["reader"].read(family=family, min_strength=0.0)

    workbooks = {node.id for node in graph.nodes if node.type == "Workbook"}
    assert workbooks == {estate["a"], estate["b"]}
    assert [f.name for f in graph.families] == ["mf_risk_positions"]
    assert set(graph.families[0].members) == {estate["a"], estate["b"]}
    assert graph.families[0].state == "PROPOSED"


async def test_a_stored_shares_lineage_edge_is_what_the_view_shows(estate) -> None:
    """The Cartographer's numbers win over a recomputation.

    Written here with figures this read would never produce, so a reader that quietly
    recomputed would be caught rather than agreed with.
    """
    await _edge(
        estate["writer"],
        "SHARES_LINEAGE",
        estate["a"],
        estate["b"],
        jaccard_tables=0.42,
        jaccard_fields=0.11,
        shared_calc_count=0,
    )

    graph = await estate["reader"].read(site=estate["site"], min_strength=0.0)
    pair = tuple(sorted((estate["a"], estate["b"])))
    link = next(link for link in graph.shared if (link.source, link.target) == pair)

    assert graph.origin == "graph"
    assert link.origin == "graph"
    assert link.jaccard_tables == pytest.approx(0.42), "not the 1.0 a recomputation gives"
    assert link.strength == pytest.approx(0.5 * 0.42 + 0.3 * 0.11)


async def test_a_retired_table_leaves_the_lineage(estate) -> None:
    """Retirement is exclusion, and a similarity computed over retired lineage would group
    workbooks on something the estate no longer has."""
    before = await estate["reader"].read(site=estate["site"], min_strength=0.0)
    assert any(node.id == estate["shared_table"] for node in before.nodes)

    await estate["writer"].retire_node(
        estate["shared_table"],
        reason="Table dropped from the warehouse in the March release",
        principal=Principal("user:p.eng@artizent.example"),
    )

    after = await estate["reader"].read(site=estate["site"], min_strength=0.0)
    assert not any(node.id == estate["shared_table"] for node in after.nodes)

    pair = tuple(sorted((estate["a"], estate["b"])))
    link = next((link for link in after.shared if (link.source, link.target) == pair), None)
    assert link is not None, "they still share a field and a calculation shape"
    assert link.jaccard_tables == 0.0
