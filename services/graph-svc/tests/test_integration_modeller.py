"""The Modeller, against real PostgreSQL + Apache AGE — story S4.1.1.

What only the real store can answer: that the whole chain (Workbook -> Worksheet ->
Datasource -> Connection -> Table, and Datasource -> HAS_FIELD -> CalculatedField) resolves
correctly for a real family's members, that a proposal actually lands in the graph as a
SemanticModel and its ModelTable nodes, that a real ProvenanceRecord is written for the
grain-statement draft, and that a re-run retires what came before it — none of which the
pure-function unit tests in ``test_modeller.py`` can see.
"""

from __future__ import annotations

import asyncio
import os
import threading
import time
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
from astra_graph.migrations import run as run_migrations  # noqa: E402
from astra_graph.modeller import Modeller, read_design_document  # noqa: E402
from astra_graph.ontology import EDGE_LABELS, NODE_LABELS  # noqa: E402
from astra_graph.principal import Principal  # noqa: E402
from astra_graph.provenance import AgentMode, PostgresProvenanceStore  # noqa: E402
from astra_graph.writes import EdgeWrite, GraphWriter, NodeWrite  # noqa: E402

PRINCIPAL = Principal("agent:modeller", run_id="run-modeller")


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
    config = _settings(f"astra_mdl_{new_ulid()[10:22].lower()}")

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


@pytest.fixture
async def estate(settings: Settings):
    """One ModelFamily, two member workbooks sharing a base table and each joining their
    own dimension table, one shared calculation shape and one workbook-specific one, and
    row-level security on one member — enough to exercise every heuristic at once."""
    pool = await create_pool(settings)
    try:
        repository = AgeGraphRepository(pool, graph_name=settings.graph_name)
        writer = GraphWriter(repository, event_source=source_for(settings.graph_name))
        provenance_store = PostgresProvenanceStore(pool, graph_name=settings.graph_name)
        suffix = new_ulid()[10:18].lower()

        site = await _write(writer, "Site", luid=f"s-{suffix}", name=f"RQA {suffix}")
        project = await _write(writer, "Project", luid=f"p-{suffix}", name="Risk Core")
        await _edge(writer, "CONTAINS", site, project)

        base_table = await _write(
            writer, "Table", name="positions", schema="risk", row_estimate=5_000_000
        )
        dim_table = await _write(
            writer, "Table", name="desk", schema="risk", row_estimate=40
        )
        connection = await _write(
            writer, "Connection", **{"class": "snowflake"}, server="warehouse", db="risk"
        )
        await _edge(writer, "CONNECTS_TO", connection, base_table, join_clause=None)
        await _edge(
            writer,
            "CONNECTS_TO",
            connection,
            dim_table,
            join_clause="positions.desk_id = desk.id",
        )

        shared_ast = {
            "op": "DIV",
            "args": [
                {"fn": "SUM", "arg": {"field": "Margin"}},
                {"fn": "SUM", "arg": {"field": "Revenue"}},
            ],
        }

        async def workbook(
            name: str, *, extract_flag: bool, rls: bool = False, own_calc: bool = False
        ) -> tuple[str, str]:
            book = await _write(
                writer, "Workbook", luid=f"{name}-{suffix}", name=name, revision="1",
                **({"rls": True, "rls_expression": "[Desk] = USERNAME()"} if rls else {}),
            )
            await _edge(writer, "CONTAINS", project, book)
            sheet = await _write(
                writer, "Worksheet", name=f"{name} sheet", rows_shelf=["Desk"],
                cols_shelf=["Trade Date"], marks_shelf=[],
            )
            await _edge(writer, "CONTAINS", book, sheet)
            datasource = await _write(
                writer, "Datasource", name=f"{name} ds", type="published",
                luid=f"ds-{name}-{suffix}", extract_flag=extract_flag,
                refresh_schedule="daily" if extract_flag else None,
            )
            await _edge(writer, "USES_DATASOURCE", sheet, datasource)
            await _edge(writer, "CONNECTS_TO", datasource, connection)
            calc_ast = (
                {"fn": "SUM", "arg": {"field": f"{name} Only"}} if own_calc else shared_ast
            )
            calc = await _write(
                writer, "CalculatedField", name="Margin %", formula="SUM([M]) / SUM([R])",
                formula_ast=calc_ast,
            )
            await _edge(writer, "HAS_FIELD", datasource, calc)
            return book, datasource

        alpha, alpha_ds = await workbook("Alpha", extract_flag=True, rls=True)
        bravo, bravo_ds = await workbook("Bravo", extract_flag=True)

        family = await _write(
            writer, "ModelFamily", name=f"Risk Positions {suffix}", state="PROPOSED",
            grain="Desk, Trade Date", conformed_dims=[],
        )
        await _edge(writer, "IN_FAMILY", alpha, family, confidence=1.0)
        await _edge(writer, "IN_FAMILY", bravo, family, confidence=1.0)

        other_family = await _write(
            writer, "ModelFamily", name=f"Other {suffix}", state="PROPOSED",
            grain="Desk, Currency", conformed_dims=[],
        )

        yield {
            "pool": pool,
            "settings": settings,
            "writer": writer,
            "provenance_store": provenance_store,
            "modeller": Modeller(
                pool, graph_name=settings.graph_name, writer=writer,
                provenance_store=provenance_store,
            ),
            "family": family,
            "other_family": other_family,
            "alpha": alpha,
            "bravo": bravo,
            "base_table": base_table,
            "dim_table": dim_table,
        }
    finally:
        await pool.close()


async def test_tables_are_deduplicated_by_shared_source_table(estate) -> None:
    proposal = await estate["modeller"].run(estate["family"], principal=PRINCIPAL)
    # Each ModelTable gets its own id, distinct from the source Table it was proposed from
    # (every node in this graph has a globally unique id) — source_table_refs is where the
    # source table's own id is carried, and it is what dedup is actually asked of.
    source_refs = {t.source_table_refs[0] for t in proposal.tables}
    assert source_refs == {estate["base_table"], estate["dim_table"]}
    assert len({t.id for t in proposal.tables}) == 2


async def test_storage_mode_follows_the_extract_flag(estate) -> None:
    proposal = await estate["modeller"].run(estate["family"], principal=PRINCIPAL)
    base = next(t for t in proposal.tables if t.source_table_refs == (estate["base_table"],))
    assert base.mode == "import"


async def test_relationship_cardinality_from_row_estimates(estate) -> None:
    proposal = await estate["modeller"].run(estate["family"], principal=PRINCIPAL)
    by_source = {t.source_table_refs[0]: t.id for t in proposal.tables}
    [relationship] = proposal.relationships
    assert relationship.to_table == by_source[estate["base_table"]]
    assert relationship.from_table == by_source[estate["dim_table"]]
    assert relationship.cardinality == "one_to_many"


async def test_shared_calculation_shapes_merge_into_one_measure(estate) -> None:
    proposal = await estate["modeller"].run(estate["family"], principal=PRINCIPAL)
    assert len(proposal.measures) == 1
    assert len(proposal.measures[0].source_calc_refs) == 2


async def test_rls_is_scaffolded_from_the_flagged_workbook(estate) -> None:
    proposal = await estate["modeller"].run(estate["family"], principal=PRINCIPAL)
    assert len(proposal.rls_roles) == 1
    assert proposal.rls_roles[0].expression == "[Desk] = USERNAME()"
    assert proposal.rls_roles[0].source_workbook_ids == (estate["alpha"],)


async def test_grain_statement_is_drafted_from_the_familys_candidate_grain(estate) -> None:
    proposal = await estate["modeller"].run(estate["family"], principal=PRINCIPAL)
    assert proposal.grain_statement == "One row per Desk and Trade Date."


async def test_conformed_dimension_sharing_names_the_other_family(estate) -> None:
    proposal = await estate["modeller"].run(estate["family"], principal=PRINCIPAL)
    desk = next(c for c in proposal.conformed_dimensions if c.dimension == "Desk")
    assert estate["other_family"] in desk.shared_with_family_ids


async def test_a_provenance_record_is_written_for_the_grain_statement(estate) -> None:
    proposal = await estate["modeller"].run(estate["family"], principal=PRINCIPAL)
    record = await estate["provenance_store"].get(proposal.grain_provenance_id)
    assert record is not None
    assert record.mode is AgentMode.ASSISTED
    assert record.model is None
    assert record.subject_id == estate["family"]


async def test_a_rerun_retires_the_previous_semantic_model_and_tables(estate) -> None:
    first = await estate["modeller"].run(estate["family"], principal=PRINCIPAL)
    second = await estate["modeller"].run(estate["family"], principal=PRINCIPAL)
    assert first.semantic_model_id != second.semantic_model_id

    repository = AgeGraphRepository(estate["pool"], graph_name=estate["settings"].graph_name)
    retired = await repository.get_node_record(first.semantic_model_id)
    assert retired is not None
    assert retired.properties.get("retired_at") is not None


async def test_generation_completes_comfortably_inside_the_five_minute_budget(estate) -> None:
    started = time.perf_counter()
    await estate["modeller"].run(estate["family"], principal=PRINCIPAL)
    elapsed = time.perf_counter() - started
    # Two workbooks reach the same amount of graph structure this heuristic touches
    # regardless of family size (one hop set per label, not one query per workbook), so
    # this elapsed time is representative of a 40-workbook family too — see modeller.py's
    # module docstring and ADR 0028 for why the read does not scale with member count in
    # the way a naive per-workbook loop would.
    assert elapsed < 30.0


async def test_read_design_document_returns_the_generated_proposal(estate) -> None:
    await estate["modeller"].run(estate["family"], principal=PRINCIPAL)
    document = await read_design_document(
        estate["pool"], estate["settings"].graph_name, estate["family"]
    )
    assert document["family_id"] == estate["family"]
    assert len(document["tables"]) == 2
    assert document["grain_statement"] == "One row per Desk and Trade Date."


async def test_read_design_document_before_any_run_is_a_clean_not_found(estate) -> None:
    from astra_graph.errors import ElementNotFoundError

    with pytest.raises(ElementNotFoundError):
        await read_design_document(estate["pool"], estate["settings"].graph_name, estate["other_family"])


# ---------------------------------------------------------------------------- the API


@pytest.fixture
async def http_client(estate):
    from httpx import ASGITransport, AsyncClient

    from astra_graph.main import create_app

    app = create_app()
    app.state.modeller = estate["modeller"]

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://graph-svc") as async_client:
        yield async_client


def _headers(role: str = "semantic_model_engineer") -> dict[str, str]:
    from astra_graph.principal import PRINCIPAL_HEADER
    from astra_graph.roles import ROLES_HEADER

    return {PRINCIPAL_HEADER: PRINCIPAL.value, ROLES_HEADER: role}


async def test_propose_design_over_http(estate, http_client) -> None:
    response = await http_client.post(
        f"/v1/families/{estate['family']}:propose-design", headers=_headers()
    )
    assert response.status_code == 200
    body = response.json()
    assert len(body["tables"]) == 2
    assert body["family_id"] == estate["family"]


async def test_propose_design_requires_the_semantic_model_engineer_role(estate, http_client) -> None:
    response = await http_client.post(
        f"/v1/families/{estate['family']}:propose-design",
        headers=_headers(role="programme_manager"),
    )
    assert response.status_code == 403


async def test_get_design_over_http_after_a_proposal_exists(estate, http_client) -> None:
    await http_client.post(f"/v1/families/{estate['family']}:propose-design", headers=_headers())
    response = await http_client.get(f"/v1/families/{estate['family']}/design", headers=_headers())
    assert response.status_code == 200
    assert response.json()["grain_statement"] == "One row per Desk and Trade Date."


async def test_propose_design_for_an_unknown_family_is_404(estate, http_client) -> None:
    response = await http_client.post("/v1/families/not-a-real-family:propose-design", headers=_headers())
    assert response.status_code == 404
