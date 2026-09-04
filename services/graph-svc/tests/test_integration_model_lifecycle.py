"""The G2 state machine and design editing, against real PostgreSQL + Apache AGE.
Story S4.1.2.

What only the real store can answer: that a family actually moves through
PROPOSED -> DRAFT -> IN_REVIEW with each transition recorded in the real event log, that
the frozen version hash is stable across re-reads and changes when the design does, that
editing a table's mode or a relationship's cardinality lands on the right node without
disturbing anything else, and that ``Modeller.run`` genuinely refuses once a family has
been accepted — none of which the pure `require_transition`/`hashable_document` unit tests
in ``test_model_lifecycle.py`` can see.
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
from astra_graph.errors import ElementNotFoundError, InvalidRequestError  # noqa: E402
from astra_graph.events import source_for  # noqa: E402
from astra_graph.graph import AgeGraphRepository, create_pool  # noqa: E402
from astra_graph.graph.queries import accessor  # noqa: E402
from astra_graph.ids import new_ulid  # noqa: E402
from astra_graph.migrations import run as run_migrations  # noqa: E402
from astra_graph.model_lifecycle import (  # noqa: E402
    accept_family,
    family_transition_history,
    submit_for_review,
    update_grain_statement,
    update_relationship_cardinality,
    update_table_mode,
)
from astra_graph.modeller import Modeller, read_design_document  # noqa: E402
from astra_graph.ontology import EDGE_LABELS, NODE_LABELS  # noqa: E402
from astra_graph.principal import Principal  # noqa: E402
from astra_graph.provenance import PostgresProvenanceStore  # noqa: E402
from astra_graph.writes import EdgeWrite, GraphWriter, NodeWrite  # noqa: E402

PRINCIPAL = Principal("agent:modeller", run_id="run-modeller")
ENGINEER = Principal("user:sme@artizent.example")


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
    config = _settings(f"astra_lcy_{new_ulid()[10:22].lower()}")

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
    """One ModelFamily with a generated proposal already sitting on it: two tables joined
    on one connection (so a relationship exists to edit), two members sharing a
    calculation shape (so measures exist), and a fresh PROPOSED state ready to accept."""
    pool = await create_pool(settings)
    try:
        repository = AgeGraphRepository(pool, graph_name=settings.graph_name)
        writer = GraphWriter(repository, event_source=source_for(settings.graph_name))
        provenance_store = PostgresProvenanceStore(pool, graph_name=settings.graph_name)
        modeller = Modeller(
            pool, graph_name=settings.graph_name, writer=writer, provenance_store=provenance_store
        )
        suffix = new_ulid()[10:18].lower()

        site = await _write(writer, "Site", luid=f"s-{suffix}", name=f"RQA {suffix}")
        project = await _write(writer, "Project", luid=f"p-{suffix}", name="Risk Core")
        await _edge(writer, "CONTAINS", site, project)

        base_table = await _write(writer, "Table", name="positions", schema="risk", row_estimate=5_000_000)
        dim_table = await _write(writer, "Table", name="desk", schema="risk", row_estimate=40)
        connection = await _write(writer, "Connection", **{"class": "snowflake"}, server="warehouse", db="risk")
        await _edge(writer, "CONNECTS_TO", connection, base_table)
        await _edge(writer, "CONNECTS_TO", connection, dim_table, join_clause="positions.desk_id = desk.id")

        shared_ast = {"op": "DIV", "args": [{"fn": "SUM", "arg": {"field": "Margin"}}]}

        async def workbook(name: str) -> str:
            book = await _write(writer, "Workbook", luid=f"{name}-{suffix}", name=name, revision="1")
            await _edge(writer, "CONTAINS", project, book)
            sheet = await _write(
                writer, "Worksheet", name=f"{name} sheet", rows_shelf=["Desk"], cols_shelf=["Date"],
                marks_shelf=[],
            )
            await _edge(writer, "CONTAINS", book, sheet)
            datasource = await _write(
                writer, "Datasource", name=f"{name} ds", type="published",
                luid=f"ds-{name}-{suffix}", extract_flag=True, refresh_schedule="daily",
            )
            await _edge(writer, "USES_DATASOURCE", sheet, datasource)
            await _edge(writer, "CONNECTS_TO", datasource, connection)
            calc = await _write(
                writer, "CalculatedField", name="Margin %", formula="SUM([M])", formula_ast=shared_ast,
            )
            await _edge(writer, "HAS_FIELD", datasource, calc)
            return book

        alpha = await workbook("Alpha")
        bravo = await workbook("Bravo")

        family = await _write(
            writer, "ModelFamily", name=f"Risk Positions {suffix}", state="PROPOSED",
            grain="Desk, Date", conformed_dims=[],
        )
        await _edge(writer, "IN_FAMILY", alpha, family, confidence=1.0)
        await _edge(writer, "IN_FAMILY", bravo, family, confidence=1.0)

        await modeller.run(family, principal=PRINCIPAL)

        # Each ModelTable gets its own id, distinct from the source Table it was proposed
        # from (every node in this graph has a globally unique id) — resolve the actual
        # ModelTable ids the tests below need to address, by the source table they came
        # from, exactly the way a real caller reading the generated proposal would.
        document = await read_design_document(pool, settings.graph_name, family)
        model_base_table = next(
            t["id"] for t in document["tables"] if t["source_table_refs"] == [base_table]
        )
        model_dim_table = next(
            t["id"] for t in document["tables"] if t["source_table_refs"] == [dim_table]
        )

        yield {
            "pool": pool,
            "settings": settings,
            "writer": writer,
            "modeller": modeller,
            "family": family,
            "base_table": model_base_table,
            "dim_table": model_dim_table,
        }
    finally:
        await pool.close()


# ------------------------------------------------------------------------- the state machine


async def test_accept_moves_a_proposed_family_to_draft(estate) -> None:
    result = await accept_family(estate["pool"], estate["settings"].graph_name, estate["writer"], estate["family"], principal=ENGINEER)
    assert result["state"] == "DRAFT"


async def test_accepting_twice_is_refused(estate) -> None:
    await accept_family(estate["pool"], estate["settings"].graph_name, estate["writer"], estate["family"], principal=ENGINEER)
    with pytest.raises(InvalidRequestError):
        await accept_family(estate["pool"], estate["settings"].graph_name, estate["writer"], estate["family"], principal=ENGINEER)


async def test_regenerating_a_proposal_is_refused_once_accepted(estate) -> None:
    await accept_family(estate["pool"], estate["settings"].graph_name, estate["writer"], estate["family"], principal=ENGINEER)
    with pytest.raises(InvalidRequestError, match="already been accepted"):
        await estate["modeller"].run(estate["family"], principal=PRINCIPAL)


async def test_submitting_before_draft_is_refused(estate) -> None:
    with pytest.raises(InvalidRequestError):
        await submit_for_review(estate["pool"], estate["settings"].graph_name, estate["writer"], estate["family"], principal=ENGINEER)


async def test_submit_freezes_a_version_hash_and_moves_to_in_review(estate) -> None:
    await accept_family(estate["pool"], estate["settings"].graph_name, estate["writer"], estate["family"], principal=ENGINEER)
    result = await submit_for_review(estate["pool"], estate["settings"].graph_name, estate["writer"], estate["family"], principal=ENGINEER)
    assert result["state"] == "IN_REVIEW"
    assert result["version"].startswith("sha256:")

    document = await read_design_document(estate["pool"], estate["settings"].graph_name, estate["family"])
    assert document["version"] == result["version"]


async def test_re_reading_after_submit_reproduces_the_same_hash(estate) -> None:
    await accept_family(estate["pool"], estate["settings"].graph_name, estate["writer"], estate["family"], principal=ENGINEER)
    submitted = await submit_for_review(estate["pool"], estate["settings"].graph_name, estate["writer"], estate["family"], principal=ENGINEER)

    from astra_graph.context.canonical import canonical_json, context_hash
    from astra_graph.model_lifecycle import hashable_document

    document = await read_design_document(estate["pool"], estate["settings"].graph_name, estate["family"])
    recomputed = context_hash(canonical_json(hashable_document(document)))
    assert recomputed == submitted["version"]


async def test_the_transition_history_records_both_moves_in_order(estate) -> None:
    await accept_family(estate["pool"], estate["settings"].graph_name, estate["writer"], estate["family"], principal=ENGINEER)
    await submit_for_review(estate["pool"], estate["settings"].graph_name, estate["writer"], estate["family"], principal=ENGINEER)

    history = await family_transition_history(estate["pool"], estate["settings"].graph_name, estate["family"])
    moves = [(h["from_state"], h["to_state"]) for h in history]
    assert ("PROPOSED", "DRAFT") in moves
    assert ("DRAFT", "IN_REVIEW") in moves
    assert moves.index(("PROPOSED", "DRAFT")) < moves.index(("DRAFT", "IN_REVIEW"))
    accepted = next(h for h in history if h["to_state"] == "DRAFT")
    assert accepted["by"] == ENGINEER.value


async def test_transition_history_records_creation_for_a_family_never_moved(estate) -> None:
    # The family's very first write is itself a genuine transition — from nothing to
    # PROPOSED — and is who/when it was created, which is worth keeping, not filtering out
    # as a special case: the LAG comparison is `previous_state IS DISTINCT FROM state`, and
    # NULL is honestly distinct from every declared state.
    history = await family_transition_history(estate["pool"], estate["settings"].graph_name, estate["family"])
    assert history == [
        {
            "from_state": None,
            "to_state": "PROPOSED",
            "at": history[0]["at"],
            "by": PRINCIPAL.value,
        }
    ]


# --------------------------------------------------------------------------------- editing


async def test_editing_before_accept_is_refused(estate) -> None:
    with pytest.raises(InvalidRequestError, match="DRAFT"):
        await update_grain_statement(
            estate["pool"], estate["settings"].graph_name, estate["writer"], estate["family"],
            grain_statement="One row per Desk.", principal=ENGINEER,
        )


async def test_grain_statement_can_be_edited_while_draft(estate) -> None:
    await accept_family(estate["pool"], estate["settings"].graph_name, estate["writer"], estate["family"], principal=ENGINEER)
    await update_grain_statement(
        estate["pool"], estate["settings"].graph_name, estate["writer"], estate["family"],
        grain_statement="One row per Desk and settlement date.", principal=ENGINEER,
    )
    document = await read_design_document(estate["pool"], estate["settings"].graph_name, estate["family"])
    assert document["grain_statement"] == "One row per Desk and settlement date."


async def test_table_mode_can_be_overridden_while_draft(estate) -> None:
    await accept_family(estate["pool"], estate["settings"].graph_name, estate["writer"], estate["family"], principal=ENGINEER)
    await update_table_mode(
        estate["pool"], estate["settings"].graph_name, estate["writer"], estate["family"],
        estate["base_table"], mode="directlake", principal=ENGINEER,
    )
    document = await read_design_document(estate["pool"], estate["settings"].graph_name, estate["family"])
    table = next(t for t in document["tables"] if t["id"] == estate["base_table"])
    assert table["mode"] == "directlake"


async def test_an_invalid_table_mode_is_refused(estate) -> None:
    await accept_family(estate["pool"], estate["settings"].graph_name, estate["writer"], estate["family"], principal=ENGINEER)
    with pytest.raises(InvalidRequestError):
        await update_table_mode(
            estate["pool"], estate["settings"].graph_name, estate["writer"], estate["family"],
            estate["base_table"], mode="not_a_real_mode", principal=ENGINEER,
        )


async def test_a_table_from_another_family_cannot_be_edited(estate) -> None:
    await accept_family(estate["pool"], estate["settings"].graph_name, estate["writer"], estate["family"], principal=ENGINEER)
    with pytest.raises(ElementNotFoundError):
        await update_table_mode(
            estate["pool"], estate["settings"].graph_name, estate["writer"], estate["family"],
            "not-a-real-table", mode="import", principal=ENGINEER,
        )


async def test_relationship_cardinality_can_be_overridden_while_draft(estate) -> None:
    await accept_family(estate["pool"], estate["settings"].graph_name, estate["writer"], estate["family"], principal=ENGINEER)
    result = await update_relationship_cardinality(
        estate["pool"], estate["settings"].graph_name, estate["writer"], estate["family"],
        from_table=estate["dim_table"], to_table=estate["base_table"], cardinality="one_to_many",
        principal=ENGINEER,
    )
    assert result["relationship"]["cardinality"] == "one_to_many"
    assert result["relationship"]["confidence"] == "engineer_confirmed"

    document = await read_design_document(estate["pool"], estate["settings"].graph_name, estate["family"])
    [relationship] = document["relationships"]
    assert relationship["cardinality"] == "one_to_many"


async def test_edits_after_submission_are_refused(estate) -> None:
    await accept_family(estate["pool"], estate["settings"].graph_name, estate["writer"], estate["family"], principal=ENGINEER)
    await submit_for_review(estate["pool"], estate["settings"].graph_name, estate["writer"], estate["family"], principal=ENGINEER)
    with pytest.raises(InvalidRequestError, match="DRAFT"):
        await update_grain_statement(
            estate["pool"], estate["settings"].graph_name, estate["writer"], estate["family"],
            grain_statement="One row per Desk.", principal=ENGINEER,
        )


# ---------------------------------------------------------------------------------- the API


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

    return {PRINCIPAL_HEADER: ENGINEER.value, ROLES_HEADER: role}


async def test_the_full_lifecycle_over_http(estate, http_client) -> None:
    family_id = estate["family"]

    accepted = await http_client.post(f"/v1/families/{family_id}:accept", headers=_headers())
    assert accepted.status_code == 200
    assert accepted.json()["state"] == "DRAFT"

    grain = await http_client.post(
        f"/v1/families/{family_id}:edit-grain-statement",
        json={"grain_statement": "One row per Desk."},
        headers=_headers(),
    )
    assert grain.status_code == 200

    mode = await http_client.post(
        f"/v1/families/{family_id}/tables/{estate['base_table']}:set-mode",
        json={"mode": "import"},
        headers=_headers(),
    )
    assert mode.status_code == 200

    cardinality = await http_client.post(
        f"/v1/families/{family_id}/relationships:set-cardinality",
        json={"from_table": estate["dim_table"], "to_table": estate["base_table"], "cardinality": "one_to_many"},
        headers=_headers(),
    )
    assert cardinality.status_code == 200

    submitted = await http_client.post(f"/v1/families/{family_id}:submit-for-review", headers=_headers())
    assert submitted.status_code == 200
    assert submitted.json()["state"] == "IN_REVIEW"
    assert submitted.json()["version"].startswith("sha256:")

    history = await http_client.get(f"/v1/families/{family_id}/transitions", headers=_headers())
    assert history.status_code == 200
    moves = [(t["from_state"], t["to_state"]) for t in history.json()["transitions"]]
    assert ("PROPOSED", "DRAFT") in moves
    assert ("DRAFT", "IN_REVIEW") in moves


async def test_accept_requires_the_semantic_model_engineer_role(estate, http_client) -> None:
    response = await http_client.post(
        f"/v1/families/{estate['family']}:accept", headers=_headers(role="programme_manager")
    )
    assert response.status_code == 403


async def test_accepting_an_unknown_family_is_404(estate, http_client) -> None:
    response = await http_client.post("/v1/families/not-a-real-family:accept", headers=_headers())
    assert response.status_code == 404
