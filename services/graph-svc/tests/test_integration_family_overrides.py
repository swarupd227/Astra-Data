"""Split, merge and move — against real PostgreSQL + Apache AGE. Story S3.1.2.

    "I want to split, merge and move workbooks between families with a reason, so that the
    proposal is a starting point I control."

What only the real store can answer: that an edge can actually be retired and a new one
takes its place without the workbook ever appearing in two families at once, that grain and
evidence recompute correctly for a hand-picked member set (not just whatever the clustering
algorithm would have chosen), and that a re-cluster genuinely leaves an overridden family
alone until it is named in ``confirm_family_ids``.
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

from astra_graph.cartographer import Cartographer  # noqa: E402
from astra_graph.config import Settings  # noqa: E402
from astra_graph.errors import ElementNotFoundError, InvalidRequestError  # noqa: E402
from astra_graph.events import source_for  # noqa: E402
from astra_graph.family_overrides import merge_families, move_member, split_family  # noqa: E402
from astra_graph.graph import AgeGraphRepository, create_pool  # noqa: E402
from astra_graph.graph.queries import accessor  # noqa: E402
from astra_graph.ids import new_ulid  # noqa: E402
from astra_graph.migrations import run as run_migrations  # noqa: E402
from astra_graph.ontology import EDGE_LABELS, NODE_LABELS  # noqa: E402
from astra_graph.principal import Principal  # noqa: E402
from astra_graph.writes import EdgeWrite, GraphWriter, NodeWrite  # noqa: E402

PRINCIPAL = Principal("agent:model-engineer", run_id="run-overrides")
REASON = "grouped by the model engineer during family review"


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
    config = _settings(f"astra_ovr_{new_ulid()[10:22].lower()}")

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


async def _edge(writer: GraphWriter, type_: str, source: str, target: str, **props: Any) -> str:
    edge = await writer.write_edge(
        EdgeWrite(type=type_, from_id=source, to_id=target, properties=props), principal=PRINCIPAL
    )
    return str(edge["properties"]["id"])


@pytest.fixture
async def estate(settings: Settings):
    """Two pre-clustered families over real lineage: F1 = {alpha, bravo, charlie} sharing
    one table/field/calc shape, F2 = {delta, echo} sharing a different one. Written
    directly rather than through a Cartographer run, so a test starts from a known,
    un-overridden shape.
    """
    pool = await create_pool(settings)
    try:
        repository = AgeGraphRepository(pool, graph_name=settings.graph_name)
        writer = GraphWriter(repository, event_source=source_for(settings.graph_name))
        suffix = new_ulid()[10:18].lower()

        site = await _write(writer, "Site", luid=f"s-{suffix}", name=f"RQA {suffix}")
        project = await _write(writer, "Project", luid=f"p-{suffix}", name="Risk Core")
        await _edge(writer, "CONTAINS", site, project)

        table_one = await _write(writer, "Table", name="positions", schema="risk")
        table_two = await _write(writer, "Table", name="trades", schema="risk")
        ast_one = {"op": "SUM", "args": [{"field": "Notional"}]}
        ast_two = {"op": "SUM", "args": [{"field": "Quantity"}]}

        async def workbook(name: str, table: str, ast: Any) -> str:
            book = await _write(
                writer, "Workbook", luid=f"{name}-{suffix}", name=name, revision="1"
            )
            await _edge(writer, "CONTAINS", project, book)
            sheet = await _write(
                writer, "Worksheet", name=f"{name} sheet",
                rows_shelf=["Desk"], cols_shelf=["Trade Date"], marks_shelf=[],
            )
            await _edge(writer, "CONTAINS", book, sheet)
            datasource = await _write(
                writer, "Datasource", name=f"{name} ds", type="published",
                luid=f"ds-{name}-{suffix}",
            )
            await _edge(writer, "USES_DATASOURCE", sheet, datasource)
            connection = await _write(
                writer, "Connection", **{"class": "postgres"}, server="warehouse", db="risk"
            )
            await _edge(writer, "CONNECTS_TO", datasource, connection)
            await _edge(writer, "CONNECTS_TO", connection, table)
            calc = await _write(
                writer, "CalculatedField", name=f"{name} calc",
                formula="SUM([X])", formula_ast=ast,
            )
            await _edge(writer, "HAS_FIELD", datasource, calc)
            return book

        alpha = await workbook("Alpha", table_one, ast_one)
        bravo = await workbook("Bravo", table_one, ast_one)
        charlie = await workbook("Charlie", table_one, ast_one)
        delta = await workbook("Delta", table_two, ast_two)
        echo = await workbook("Echo", table_two, ast_two)

        family_one = await _write(
            writer, "ModelFamily", name="F1", state="PROPOSED", grain="Desk", conformed_dims=[],
        )
        for member in (alpha, bravo, charlie):
            await _edge(writer, "IN_FAMILY", member, family_one, confidence=1.0)

        family_two = await _write(
            writer, "ModelFamily", name="F2", state="PROPOSED", grain="Desk", conformed_dims=[],
        )
        for member in (delta, echo):
            await _edge(writer, "IN_FAMILY", member, family_two, confidence=1.0)

        yield {
            "pool": pool,
            "settings": settings,
            "writer": writer,
            "repository": repository,
            "cartographer": Cartographer(pool, graph_name=settings.graph_name, writer=writer),
            "alpha": alpha, "bravo": bravo, "charlie": charlie, "delta": delta, "echo": echo,
            "family_one": family_one, "family_two": family_two,
        }
    finally:
        await pool.close()


async def _current_family(estate: dict, workbook_id: str) -> str | None:
    async with estate["pool"].acquire() as conn:
        from astra_graph.graph.queries import EDGE_INDEX_TABLE

        return await conn.fetchval(
            f"""
            SELECT to_id FROM {EDGE_INDEX_TABLE}
            WHERE graph = $1 AND label = 'IN_FAMILY' AND from_id = $2 AND retired_at IS NULL
            """,
            estate["settings"].graph_name,
            workbook_id,
        )


# ---------------------------------------------------------------------------- split


async def test_split_keeps_the_original_id_for_the_remainder(estate) -> None:
    remainder, new_family = await split_family(
        estate["pool"], estate["settings"].graph_name, estate["writer"],
        family_id=estate["family_one"], member_ids=[estate["alpha"]],
        reason=REASON, principal=PRINCIPAL,
    )

    assert remainder.id == estate["family_one"]
    assert set(remainder.members) == {estate["bravo"], estate["charlie"]}
    assert set(new_family.members) == {estate["alpha"]}
    assert new_family.id != estate["family_one"]


async def test_split_relinks_the_moved_member(estate) -> None:
    _remainder, new_family = await split_family(
        estate["pool"], estate["settings"].graph_name, estate["writer"],
        family_id=estate["family_one"], member_ids=[estate["alpha"]],
        reason=REASON, principal=PRINCIPAL,
    )

    assert await _current_family(estate, estate["alpha"]) == new_family.id


async def test_split_marks_both_families_overridden(estate) -> None:
    from astra_graph.lineage import hydrate

    remainder, new_family = await split_family(
        estate["pool"], estate["settings"].graph_name, estate["writer"],
        family_id=estate["family_one"], member_ids=[estate["alpha"]],
        reason=REASON, principal=PRINCIPAL,
    )

    async with estate["pool"].acquire() as conn:
        props = await hydrate(
            conn, estate["settings"].graph_name, "ModelFamily", [remainder.id, new_family.id]
        )
    for family_id in (remainder.id, new_family.id):
        assert props[family_id]["overridden"] is True
        assert props[family_id]["override_action"] == "SPLIT"
        assert props[family_id]["override_reason"] == REASON


async def test_split_refuses_an_unknown_member(estate) -> None:
    with pytest.raises(InvalidRequestError):
        await split_family(
            estate["pool"], estate["settings"].graph_name, estate["writer"],
            family_id=estate["family_one"], member_ids=["not-a-member"],
            reason=REASON, principal=PRINCIPAL,
        )


async def test_split_refuses_taking_every_member(estate) -> None:
    with pytest.raises(InvalidRequestError):
        await split_family(
            estate["pool"], estate["settings"].graph_name, estate["writer"],
            family_id=estate["family_one"],
            member_ids=[estate["alpha"], estate["bravo"], estate["charlie"]],
            reason=REASON, principal=PRINCIPAL,
        )


async def test_split_refuses_an_unknown_family(estate) -> None:
    with pytest.raises(ElementNotFoundError):
        await split_family(
            estate["pool"], estate["settings"].graph_name, estate["writer"],
            family_id="not-a-family", member_ids=[estate["alpha"]],
            reason=REASON, principal=PRINCIPAL,
        )


# ---------------------------------------------------------------------------- merge


async def test_merge_combines_members_into_a_fresh_family(estate) -> None:
    merged = await merge_families(
        estate["pool"], estate["settings"].graph_name, estate["writer"],
        family_ids=(estate["family_one"], estate["family_two"]),
        reason=REASON, principal=PRINCIPAL,
    )

    assert set(merged.members) == {
        estate["alpha"], estate["bravo"], estate["charlie"], estate["delta"], estate["echo"]
    }
    assert merged.id not in (estate["family_one"], estate["family_two"])


async def test_merge_retires_both_originals(estate) -> None:
    await merge_families(
        estate["pool"], estate["settings"].graph_name, estate["writer"],
        family_ids=(estate["family_one"], estate["family_two"]),
        reason=REASON, principal=PRINCIPAL,
    )

    for family_id in (estate["family_one"], estate["family_two"]):
        record = await estate["repository"].get_node_record(family_id)
        assert record is not None
        assert record.properties.get("retired_at") is not None


async def test_merge_relinks_every_member(estate) -> None:
    merged = await merge_families(
        estate["pool"], estate["settings"].graph_name, estate["writer"],
        family_ids=(estate["family_one"], estate["family_two"]),
        reason=REASON, principal=PRINCIPAL,
    )

    for member in (estate["alpha"], estate["delta"]):
        assert await _current_family(estate, member) == merged.id


async def test_merge_refuses_a_family_with_itself(estate) -> None:
    with pytest.raises(InvalidRequestError):
        await merge_families(
            estate["pool"], estate["settings"].graph_name, estate["writer"],
            family_ids=(estate["family_one"], estate["family_one"]),
            reason=REASON, principal=PRINCIPAL,
        )


# ----------------------------------------------------------------------------- move


async def test_move_relinks_the_workbook(estate) -> None:
    result = await move_member(
        estate["pool"], estate["settings"].graph_name, estate["writer"],
        workbook_id=estate["alpha"], to_family_id=estate["family_two"],
        reason=REASON, principal=PRINCIPAL,
    )

    assert result.target.id == estate["family_two"]
    assert estate["alpha"] in result.target.members
    assert await _current_family(estate, estate["alpha"]) == estate["family_two"]


async def test_move_updates_the_source_family(estate) -> None:
    result = await move_member(
        estate["pool"], estate["settings"].graph_name, estate["writer"],
        workbook_id=estate["alpha"], to_family_id=estate["family_two"],
        reason=REASON, principal=PRINCIPAL,
    )

    assert result.source is not None
    assert result.source.id == estate["family_one"]
    assert set(result.source.members) == {estate["bravo"], estate["charlie"]}


async def test_moving_the_last_member_retires_the_emptied_source(estate) -> None:
    await move_member(
        estate["pool"], estate["settings"].graph_name, estate["writer"],
        workbook_id=estate["delta"], to_family_id=estate["family_one"],
        reason=REASON, principal=PRINCIPAL,
    )
    result = await move_member(
        estate["pool"], estate["settings"].graph_name, estate["writer"],
        workbook_id=estate["echo"], to_family_id=estate["family_one"],
        reason=REASON, principal=PRINCIPAL,
    )

    assert result.source is None
    record = await estate["repository"].get_node_record(estate["family_two"])
    assert record is not None
    assert record.properties.get("retired_at") is not None


async def test_move_refuses_a_workbook_already_in_the_target(estate) -> None:
    with pytest.raises(InvalidRequestError):
        await move_member(
            estate["pool"], estate["settings"].graph_name, estate["writer"],
            workbook_id=estate["alpha"], to_family_id=estate["family_one"],
            reason=REASON, principal=PRINCIPAL,
        )


async def test_move_refuses_an_unknown_target_family(estate) -> None:
    with pytest.raises(ElementNotFoundError):
        await move_member(
            estate["pool"], estate["settings"].graph_name, estate["writer"],
            workbook_id=estate["alpha"], to_family_id="not-a-family",
            reason=REASON, principal=PRINCIPAL,
        )


# ---------------------------------------------------------- override protection & confirm


async def test_an_overridden_family_survives_a_re_cluster(estate) -> None:
    remainder, new_family = await split_family(
        estate["pool"], estate["settings"].graph_name, estate["writer"],
        family_id=estate["family_one"], member_ids=[estate["alpha"]],
        reason=REASON, principal=PRINCIPAL,
    )

    result = await estate["cartographer"].run(principal=PRINCIPAL)

    applied_ids = {f.id for f in result.families}
    assert remainder.id not in applied_ids
    assert new_family.id not in applied_ids
    assert await _current_family(estate, estate["alpha"]) == new_family.id
    assert await _current_family(estate, estate["bravo"]) == remainder.id


async def test_a_re_cluster_reports_what_it_would_change(estate) -> None:
    remainder, _new_family = await split_family(
        estate["pool"], estate["settings"].graph_name, estate["writer"],
        family_id=estate["family_one"], member_ids=[estate["alpha"]],
        reason=REASON, principal=PRINCIPAL,
    )

    result = await estate["cartographer"].run(principal=PRINCIPAL)

    changed = {c.family_id: c for c in result.would_change}
    assert remainder.id in changed
    # Unconstrained, Bravo and Charlie still share identical lineage with Alpha, so the
    # free clustering would put all three back together — a real, reportable difference
    # from the human's split.
    assert not changed[remainder.id].unchanged


async def test_confirming_a_family_lets_the_re_cluster_replace_it(estate) -> None:
    """Confirming is per family, not per workbook: Alpha is pinned by ``new_family``, not
    by ``remainder`` — so reuniting all three needs both ids confirmed. Confirming only one
    would free Bravo and Charlie to re-cluster but leave Alpha exactly where the split put
    it, which is the correct, narrower reading of "does not change overridden families
    without confirmation": confirmation names *the family*, and there were two."""
    remainder, new_family = await split_family(
        estate["pool"], estate["settings"].graph_name, estate["writer"],
        family_id=estate["family_one"], member_ids=[estate["alpha"]],
        reason=REASON, principal=PRINCIPAL,
    )

    result = await estate["cartographer"].run(
        principal=PRINCIPAL,
        confirm_family_ids=frozenset({remainder.id, new_family.id}),
    )

    for family_id in (remainder.id, new_family.id):
        record = await estate["repository"].get_node_record(family_id)
        assert record is not None
        assert record.properties.get("retired_at") is not None

    # Alpha, Bravo and Charlie are identical on lineage, so a fully confirmed re-cluster
    # puts them back into one family.
    reunited = next(f for f in result.families if estate["bravo"] in f.members)
    assert estate["alpha"] in reunited.members
    assert estate["charlie"] in reunited.members


async def test_confirming_only_one_of_two_overridden_families_leaves_the_other_pinned(
    estate,
) -> None:
    remainder, new_family = await split_family(
        estate["pool"], estate["settings"].graph_name, estate["writer"],
        family_id=estate["family_one"], member_ids=[estate["alpha"]],
        reason=REASON, principal=PRINCIPAL,
    )

    await estate["cartographer"].run(
        principal=PRINCIPAL, confirm_family_ids=frozenset({remainder.id})
    )

    # new_family (Alpha's) was never confirmed, so Alpha is still exactly where the split
    # put it, whatever the free clustering of Bravo and Charlie decided.
    assert await _current_family(estate, estate["alpha"]) == new_family.id


# ---------------------------------------------------------------------------- the API


@pytest.fixture
async def http_client(estate):
    from httpx import ASGITransport, AsyncClient

    from astra_graph.api.routes_families import ClusteringStatus
    from astra_graph.main import create_app

    app = create_app()
    app.state.cartographer = estate["cartographer"]
    app.state.cartographer_status = ClusteringStatus()

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://graph-svc") as async_client:
        yield async_client


def _headers() -> dict[str, str]:
    from astra_graph.principal import PRINCIPAL_HEADER
    from astra_graph.roles import ROLES_HEADER

    return {PRINCIPAL_HEADER: PRINCIPAL.value, ROLES_HEADER: "semantic_model_engineer"}


async def test_split_over_http(estate, http_client) -> None:
    response = await http_client.post(
        f"/v1/families/{estate['family_one']}:split",
        json={"member_ids": [estate["alpha"]], "reason": REASON},
        headers=_headers(),
    )
    assert response.status_code == 201
    body = response.json()
    assert body["remainder"]["id"] == estate["family_one"]
    assert estate["alpha"] in body["new_family"]["members"]
    assert body["new_family"]["overridden"] is True
    assert body["new_family"]["override_action"] == "SPLIT"


async def test_split_over_http_rejects_a_short_reason(estate, http_client) -> None:
    """Below pydantic's floor (empty) is a 422; below the domain floor (S3.1.2's own
    ``MIN_OVERRIDE_REASON_LENGTH``) but non-empty is a 400 from `family_overrides.py`'s own
    validation — both are "rejected", but for different reasons at different layers."""
    response = await http_client.post(
        f"/v1/families/{estate['family_one']}:split",
        json={"member_ids": [estate["alpha"]], "reason": "why"},
        headers=_headers(),
    )
    assert response.status_code == 400


async def test_merge_over_http(estate, http_client) -> None:
    response = await http_client.post(
        "/v1/families:merge",
        json={"family_ids": [estate["family_one"], estate["family_two"]], "reason": REASON},
        headers=_headers(),
    )
    assert response.status_code == 201
    body = response.json()
    assert set(body["members"]) == {
        estate["alpha"], estate["bravo"], estate["charlie"], estate["delta"], estate["echo"]
    }


async def test_move_over_http(estate, http_client) -> None:
    response = await http_client.post(
        f"/v1/families/{estate['family_two']}:add-member",
        json={"workbook_id": estate["alpha"], "reason": REASON},
        headers=_headers(),
    )
    assert response.status_code == 200
    body = response.json()
    assert estate["alpha"] in body["target"]["members"]
    assert body["source"] is not None
    assert estate["alpha"] not in body["source"]["members"]


async def test_move_over_http_reports_an_unknown_family_as_404(estate, http_client) -> None:
    response = await http_client.post(
        "/v1/families/not-a-family:add-member",
        json={"workbook_id": estate["alpha"], "reason": REASON},
        headers=_headers(),
    )
    assert response.status_code == 404


async def test_clustering_with_confirm_family_ids_over_http(estate, http_client) -> None:
    import asyncio

    split_response = await http_client.post(
        f"/v1/families/{estate['family_one']}:split",
        json={"member_ids": [estate["alpha"]], "reason": REASON},
        headers=_headers(),
    )
    remainder_id = split_response.json()["remainder"]["id"]
    new_family_id = split_response.json()["new_family"]["id"]

    response = await http_client.post(
        "/v1/families:cluster",
        json={"confirm_family_ids": [remainder_id, new_family_id]},
        headers=_headers(),
    )
    assert response.status_code == 202

    status_body: dict = {}
    for _ in range(150):
        status_response = await http_client.get(
            "/v1/families:cluster/status", headers=_headers()
        )
        status_body = status_response.json()
        if not status_body["running"]:
            break
        await asyncio.sleep(0.2)

    assert not status_body["running"], "the background run never finished"
    assert status_body["last_error"] is None
    reunited = next(
        f for f in status_body["last_result"]["families"] if estate["bravo"] in f["members"]
    )
    assert estate["alpha"] in reunited["members"]
