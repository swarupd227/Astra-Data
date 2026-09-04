"""The query API against a real PostgreSQL + Apache AGE.

Three things only the real store can answer:

* the SQL traversal agrees with the in-memory one the unit suite asserts against;
* the read-only Cypher endpoint is actually read-only, and actually times out;
* a depth-3 neighbourhood on a 1,000-workbook estate meets the 300 ms p95 budget
  (S1.1.2 criterion 3, and NFR N3).

The benchmark is marked ``slow`` as well as ``integration``: it seeds 56,000 nodes.
"""

from __future__ import annotations

import os
import statistics
import time
from typing import Any

import pytest

pytestmark = pytest.mark.integration

asyncpg = pytest.importorskip("asyncpg")

from astra_graph.config import Settings  # noqa: E402
from astra_graph.cypher import accept  # noqa: E402
from astra_graph.errors import CypherExecutionError, CypherTimeoutError  # noqa: E402
from astra_graph.graph import AgeGraphRepository, create_pool  # noqa: E402
from astra_graph.ids import new_ulid  # noqa: E402
from astra_graph.migrations import run as run_migrations  # noqa: E402
from astra_graph.writes import GraphWriter  # noqa: E402

from .conftest import seed_estate  # noqa: E402

#: S1.1.2 criterion 3.
NEIGHBOURHOOD_P95_BUDGET_MS = 300


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
        pool_max_size=8,
    )


@pytest.fixture(scope="module")
async def settings() -> Settings:
    config = _settings(os.environ.get("ASTRA_GRAPH_NAME", "astra_estate_test"))
    try:
        conn = await asyncpg.connect(dsn=config.dsn, timeout=3)
    except Exception as exc:
        pytest.skip(f"PostgreSQL with Apache AGE not reachable: {exc}")
    try:
        await run_migrations(conn)
    finally:
        await conn.close()
    return config


@pytest.fixture
async def repository(settings: Settings):
    pool = await create_pool(settings)
    try:
        yield AgeGraphRepository(pool, graph_name=settings.graph_name)
    finally:
        await pool.close()


@pytest.fixture
async def estate(repository) -> dict[str, str]:
    """The §3.4-shaped estate, written through the real write path.

    Source identifiers are made unique per run: the database persists between runs, so a
    fixed LUID would have a lookup find an earlier run's workbook.
    """
    return await seed_estate(GraphWriter(repository), suffix=f"-{new_ulid()}")


# --------------------------------------------------------------- lookups


async def test_node_by_id_round_trips_through_age(repository, estate) -> None:
    """Written through cypher(), read back through the direct indexed path.

    This is the canary for the storage-layout coupling in graph/queries.py: if AGE
    changes how a label table is shaped, this fails rather than a query silently
    returning nothing.
    """
    record = await repository.get_node_record(estate["workbook"])
    assert record is not None
    assert record.label == "Workbook"
    assert record.properties["name"] == "Daily VaR"
    assert record.properties["side"] == "source"
    assert record.properties["created_by"] == "agent:harvester"


async def test_node_by_luid(repository, estate) -> None:
    record = await repository.get_node_by_luid("Workbook", estate["workbook_luid"])
    assert record is not None and record.id == estate["workbook"]
    assert await repository.get_node_by_luid("Workbook", "no-such-luid") is None


async def test_get_nodes_batches(repository, estate) -> None:
    wanted = [estate["field"], estate["workbook"], estate["calc"]]
    records = await repository.get_nodes(wanted)
    assert [r.id for r in records] == wanted


# ---------------------------------------------------------- neighbourhood


async def test_neighbourhood_matches_the_in_memory_traversal(repository, estate) -> None:
    """The same assertions the unit suite makes against the in-memory store."""
    result = await repository.neighbourhood(estate["workbook"], depth=1)
    reached = {n.node.id for n in result.neighbours}
    assert reached == {estate["worksheet"], estate["dashboard"], estate["project"]}
    assert all(n.depth == 1 for n in result.neighbours)
    assert len(result.edges) == 3
    assert result.truncated is False


async def test_neighbourhood_depth_is_the_shortest_path(repository, estate) -> None:
    result = await repository.neighbourhood(estate["workbook"], depth=5)
    by_id = {n.node.id: n.depth for n in result.neighbours}
    assert by_id[estate["worksheet"]] == 1
    assert by_id[estate["datasource"]] == 2
    assert by_id[estate["calc"]] == 2
    assert by_id[estate["field"]] == 3


async def test_neighbourhood_edge_type_filter(repository, estate) -> None:
    result = await repository.neighbourhood(
        estate["workbook"], depth=3, edge_types=["CONTAINS"]
    )
    labels = {n.node.label for n in result.neighbours}
    assert labels == {"Worksheet", "Dashboard", "Project", "Site"}


async def test_closure_follows_one_edge_type_forwards(repository, estate) -> None:
    reached = await repository.closure(estate["calc"], edge_type="DEPENDS_ON", depth=12)
    by_id = {n.node.id: n.depth for n in reached}
    assert by_id[estate["nested_calc"]] == 1
    assert by_id[estate["parameter"]] == 1
    assert by_id[estate["field"]] == 2  # transitive
    assert estate["worksheet"] not in by_id  # ENCODES is not followed


async def test_edge_index_is_written_with_the_edge(repository, estate) -> None:
    """The adjacency index and the graph are written in one transaction."""
    result = await repository.neighbourhood(estate["site"], depth=2)
    assert {n.node.id for n in result.neighbours} == {estate["project"], estate["workbook"]}


# ------------------------------------------------------- read-only Cypher


async def _cypher(repository, query: str, params: dict[str, Any] | None = None):
    accepted = accept(query)
    return await repository.run_read_only_cypher(
        accepted.text, accepted.columns, params or {}, timeout_seconds=30, row_limit=10_000
    )


async def test_cypher_returns_rows(repository, estate) -> None:
    rows, truncated = await _cypher(
        repository, "MATCH (w:Workbook) RETURN w.name AS name, w.luid AS luid"
    )
    assert {"name": "Daily VaR", "luid": estate["workbook_luid"]} in rows
    assert truncated is False


async def test_cypher_takes_parameters(repository, estate) -> None:
    rows, _ = await _cypher(
        repository,
        "MATCH (w:Workbook) WHERE w.luid = $luid RETURN w.name AS name",
        {"luid": estate["workbook_luid"]},
    )
    assert rows == [{"name": "Daily VaR"}]


async def test_cypher_answers_a_lineage_question(repository, estate) -> None:
    """The story's point: a question the typed API has no field for."""
    rows, _ = await _cypher(
        repository,
        "MATCH (c:CalculatedField)-[:DEPENDS_ON]->(p:Parameter) "
        "RETURN c.name AS calc, p.name AS parameter",
    )
    assert {"calc": "Margin %", "parameter": "As Of"} in rows


async def test_a_write_is_blocked_by_the_transaction_not_only_the_guard(repository) -> None:
    """The guard is bypassed deliberately: PostgreSQL must refuse the write itself."""
    with pytest.raises(CypherExecutionError, match="read-only"):
        await repository.run_read_only_cypher(
            "CREATE (n:Site {id: 'sneaky', side: 'source', created_by: 'x', "
            "created_at: '2027-01-01T00:00:00Z', luid: 'l', name: 'n'}) RETURN n",
            ["n"],
            {},
            timeout_seconds=30,
            row_limit=10,
        )
    assert await repository.get_node_record("sneaky") is None


async def test_the_row_cap_is_enforced_and_reported(repository, estate) -> None:
    rows, truncated = await repository.run_read_only_cypher(
        "MATCH (n) RETURN n AS node", ["node"], {}, timeout_seconds=30, row_limit=3
    )
    assert len(rows) == 3
    assert truncated is True


async def test_the_timeout_is_enforced(repository) -> None:
    with pytest.raises(CypherTimeoutError):
        await repository.run_read_only_cypher(
            "MATCH (a), (b), (c), (d) RETURN count(a) AS n",
            ["n"],
            {},
            timeout_seconds=1,
            row_limit=10,
        )


# ------------------------------------------------------------ the budget


@pytest.mark.slow
async def test_depth_three_neighbourhood_meets_the_latency_budget(settings) -> None:
    """S1.1.2 criterion 3: p95 under 300 ms on a 1,000-workbook estate.

    Seeds directly rather than through the write path: the write path is covered
    elsewhere, and 78,000 individual edge writes would dominate the run.
    """
    graph = settings.graph_name + "_bench"
    config = _settings(graph)

    # Two connections on purpose. Apache AGE caches label relations per session, and
    # dropping a graph leaves that cache stale: creating labels afterwards on the same
    # connection fails with "label (relation) cache corrupted". See ADR 0003.
    conn = await asyncpg.connect(dsn=config.dsn)
    try:
        await _drop_benchmark_graph(conn, graph)
    finally:
        await conn.close()

    conn = await asyncpg.connect(dsn=config.dsn)
    try:
        await _build_benchmark_estate(conn, graph)
    finally:
        await conn.close()

    pool = await create_pool(config)
    try:
        repository = AgeGraphRepository(pool, graph_name=graph)
        timings: list[float] = []
        for index in range(60):
            anchor = f"{_BENCH_PREFIX}wb{(index * 17) % _BENCH_WORKBOOKS}"
            started = time.perf_counter()
            result = await repository.neighbourhood(anchor, depth=3)
            timings.append((time.perf_counter() - started) * 1000)
            assert result.neighbours
    finally:
        await pool.close()

    conn = await asyncpg.connect(dsn=config.dsn)
    try:
        await _drop_benchmark_graph(conn, graph)
    finally:
        await conn.close()

    ordered = sorted(timings)
    p50 = statistics.median(ordered)
    p95 = ordered[int(len(ordered) * 0.95)]
    print(
        f"\ndepth-3 neighbourhood over {_BENCH_WORKBOOKS} workbooks: "
        f"p50={p50:.1f}ms p95={p95:.1f}ms max={max(ordered):.1f}ms"
    )
    assert p95 < NEIGHBOURHOOD_P95_BUDGET_MS, (
        f"p95 {p95:.1f}ms exceeds the {NEIGHBOURHOOD_P95_BUDGET_MS}ms budget"
    )


#: Every benchmark row is namespaced so it can be removed without touching the rest of
#: the suite: the relational index tables are per-deployment, not per-graph.
_BENCH_PREFIX = "bench-"
_BENCH_WORKBOOKS = 1_000
_BENCH_SHEETS, _BENCH_DASHBOARDS, _BENCH_DATASOURCES = 6, 2, 3
_BENCH_FIELDS, _BENCH_CALCS = 12, 8


async def _drop_benchmark_graph(conn: asyncpg.Connection, graph: str) -> None:
    """Remove a benchmark graph left by an earlier run, if any."""
    await conn.execute("LOAD 'age'")
    await conn.execute('SET search_path = ag_catalog, "$user", public')
    await _remove_benchmark_rows(conn)
    if await conn.fetchval(
        "SELECT EXISTS(SELECT 1 FROM ag_catalog.ag_graph WHERE name=$1)", graph
    ):
        await conn.execute("SELECT ag_catalog.drop_graph($1, true)", graph)


async def _build_benchmark_estate(conn: asyncpg.Connection, graph: str) -> None:
    """A synthetic estate with the fan-out of the §3.4 worked example."""
    from astra_graph.graph.queries import accessor
    from astra_graph.ontology import EDGE_LABELS, NODE_LABELS

    await conn.execute("LOAD 'age'")
    await conn.execute('SET search_path = ag_catalog, "$user", public')
    await conn.execute("SELECT ag_catalog.create_graph($1)", graph)

    for label in sorted(NODE_LABELS):
        await conn.execute("SELECT ag_catalog.create_vlabel($1,$2)", graph, label)
        await conn.execute(f'CREATE INDEX ON {graph}."{label}" USING BTREE ({accessor("id")})')
    for label in sorted(EDGE_LABELS):
        await conn.execute("SELECT ag_catalog.create_elabel($1,$2)", graph, label)

    await _remove_benchmark_rows(conn)

    nodes: list[tuple[str, str, str]] = []
    edges: list[tuple[str, str, str, str]] = []
    edge_seq = 0

    for index in range(_BENCH_WORKBOOKS):
        workbook = f"{_BENCH_PREFIX}wb{index}"
        patterns = [f"(w:Workbook {{id:'{workbook}', luid:'l-{workbook}', name:'{workbook}'}})"]
        nodes.append((workbook, "node", "Workbook"))

        def link(source: str, target: str, label: str) -> None:
            nonlocal edge_seq
            edge_seq += 1
            edges.append((f"{_BENCH_PREFIX}e{edge_seq}", label, source, target))

        for sheet in range(_BENCH_SHEETS):
            sheet_id = f"{workbook}-s{sheet}"
            patterns += [
                f"(s{sheet}:Worksheet {{id:'{sheet_id}', name:'s'}})",
                f"(w)-[:CONTAINS]->(s{sheet})",
            ]
            nodes.append((sheet_id, "node", "Worksheet"))
            link(workbook, sheet_id, "CONTAINS")
        for dashboard in range(_BENCH_DASHBOARDS):
            dashboard_id = f"{workbook}-b{dashboard}"
            patterns += [
                f"(b{dashboard}:Dashboard {{id:'{dashboard_id}', name:'b'}})",
                f"(w)-[:CONTAINS]->(b{dashboard})",
            ]
            nodes.append((dashboard_id, "node", "Dashboard"))
            link(workbook, dashboard_id, "CONTAINS")
        for datasource in range(_BENCH_DATASOURCES):
            datasource_id = f"{workbook}-d{datasource}"
            patterns.append(f"(d{datasource}:Datasource {{id:'{datasource_id}', name:'d'}})")
            nodes.append((datasource_id, "node", "Datasource"))
            for sheet in range(_BENCH_SHEETS):
                patterns.append(f"(s{sheet})-[:USES_DATASOURCE]->(d{datasource})")
                link(f"{workbook}-s{sheet}", datasource_id, "USES_DATASOURCE")
            for field in range(_BENCH_FIELDS):
                field_id = f"{workbook}-f{datasource}-{field}"
                patterns += [
                    f"(f{datasource}_{field}:Field {{id:'{field_id}', name:'f'}})",
                    f"(d{datasource})-[:HAS_FIELD]->(f{datasource}_{field})",
                ]
                nodes.append((field_id, "node", "Field"))
                link(datasource_id, field_id, "HAS_FIELD")
        for calc in range(_BENCH_CALCS):
            calc_id = f"{workbook}-c{calc}"
            patterns += [
                f"(c{calc}:CalculatedField {{id:'{calc_id}', name:'c'}})",
                f"(s0)-[:ENCODES]->(c{calc})",
                f"(c{calc})-[:DEPENDS_ON]->(f0_0)",
            ]
            nodes.append((calc_id, "node", "CalculatedField"))
            link(f"{workbook}-s0", calc_id, "ENCODES")
            link(calc_id, f"{workbook}-f0-0", "DEPENDS_ON")

        await conn.fetch(
            f"SELECT * FROM ag_catalog.cypher('{graph}', "
            f"$$ CREATE {', '.join(patterns)} RETURN 1 $$) AS (v ag_catalog.agtype)"
        )

    for edge_id, label, _, _ in edges:
        nodes.append((edge_id, "edge", label))
    # The index tables are scoped by graph (S1.1.3), so the bulk load carries it.
    await conn.copy_records_to_table(
        "estate_element_index",
        records=[(graph, *row) for row in nodes],
        columns=["graph", "id", "kind", "label"],
        schema_name="public",
    )
    await conn.copy_records_to_table(
        "estate_edge_index",
        records=[(graph, *row) for row in edges],
        columns=["graph", "id", "label", "from_id", "to_id"],
        schema_name="public",
    )
    await conn.execute("ANALYZE")


async def _remove_benchmark_rows(conn: asyncpg.Connection) -> None:
    """Drop this benchmark's rows from the shared index tables.

    Platform-issued ids are ULIDs, which are uppercase Crockford base32, so a lowercase
    prefix cannot collide with a real one.
    """
    await conn.execute(
        "DELETE FROM public.estate_edge_index WHERE id LIKE $1", f"{_BENCH_PREFIX}%"
    )
    await conn.execute(
        "DELETE FROM public.estate_element_index WHERE id LIKE $1", f"{_BENCH_PREFIX}%"
    )
