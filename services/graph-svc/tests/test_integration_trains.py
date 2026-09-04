"""Release trains — story S3.2.1, against real PostgreSQL + Apache AGE.

    "Train membership, planned start and end, and gate schedule are stored as ReleaseTrain
    nodes; an MU is IN_TRAIN exactly one train at a time."

What only the real store can answer: that ``views_90d``/tier are actually readable off the
graph and ``scope_decision`` the way ``trains.py`` assumes, that packing writes real
``ReleaseTrain`` nodes and ``IN_TRAIN`` edges with a real ``sequence``, and that a re-run
genuinely leaves no workbook holding two live ``IN_TRAIN`` edges at once.
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
from astra_graph.graph.queries import EDGE_INDEX_TABLE, accessor  # noqa: E402
from astra_graph.ids import new_ulid  # noqa: E402
from astra_graph.migrations import run as run_migrations  # noqa: E402
from astra_graph.ontology import EDGE_LABELS, NODE_LABELS  # noqa: E402
from astra_graph.principal import Principal  # noqa: E402
from astra_graph.scope import PostgresScopeStore, new_decision  # noqa: E402
from astra_graph.trains import TrainPlanner  # noqa: E402
from astra_graph.writes import EdgeWrite, GraphWriter, NodeWrite  # noqa: E402

PRINCIPAL = Principal("agent:train-planner", run_id="run-trains")


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
    config = _settings(f"astra_trn_{new_ulid()[10:22].lower()}")

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
                "public.scope_decision",
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
    """Three families, deliberately shaped so ordering has an obvious right answer:

    * ``family_a`` — DRAFT (more ready than the others), 3 members, high usage.
    * ``family_b`` — PROPOSED, 2 members, modest usage.
    * ``family_c`` — SINGLETON, 1 member, no usage.
    * ``loose`` — a workbook in no family at all.

    Packed into train sizes ``[3, 3]``: family_a (most ready) fills train 1 alone;
    family_b then family_c (next by usage, tied on readiness) share train 2 — proving both
    "readiness beats usage" and "a family is never split" against a real pack.
    """
    pool = await create_pool(settings)
    try:
        repository = AgeGraphRepository(pool, graph_name=settings.graph_name)
        writer = GraphWriter(repository, event_source=source_for(settings.graph_name))
        scope_store = PostgresScopeStore(pool, graph_name=settings.graph_name)
        suffix = new_ulid()[10:18].lower()

        site = await _write(writer, "Site", luid=f"s-{suffix}", name=f"RQA {suffix}")
        project = await _write(writer, "Project", luid=f"p-{suffix}", name="Risk Core")
        await _edge(writer, "CONTAINS", site, project)

        async def workbook(name: str, *, views_90d: int) -> str:
            book = await _write(
                writer, "Workbook", luid=f"{name}-{suffix}", name=name, revision="1",
                views_90d=views_90d,
            )
            await _edge(writer, "CONTAINS", project, book)
            return book

        a_members = [await workbook(f"A{i}-{suffix}", views_90d=v) for i, v in enumerate((500, 400, 300))]
        b_members = [await workbook(f"B{i}-{suffix}", views_90d=v) for i, v in enumerate((50, 10))]
        c_members = [await workbook(f"C0-{suffix}", views_90d=1)]
        loose = await workbook(f"Loose-{suffix}", views_90d=0)

        async def family(name: str, state: str, members: list[str]) -> str:
            family_id = await _write(
                writer, "ModelFamily", name=name, state=state, grain="Desk", conformed_dims=[],
            )
            for member in members:
                await _edge(writer, "IN_FAMILY", member, family_id, confidence=1.0)
            return family_id

        family_a = await family(f"FamilyA-{suffix}", "DRAFT", a_members)
        family_b = await family(f"FamilyB-{suffix}", "PROPOSED", b_members)
        family_c = await family(f"FamilyC-{suffix}", "SINGLETON", c_members)

        yield {
            "pool": pool,
            "settings": settings,
            "writer": writer,
            "scope_store": scope_store,
            "planner": TrainPlanner(
                pool, graph_name=settings.graph_name, writer=writer, scope_store=scope_store
            ),
            "family_a": family_a, "family_b": family_b, "family_c": family_c,
            "a_members": a_members, "b_members": b_members, "c_members": c_members,
            "loose": loose,
        }
    finally:
        await pool.close()


async def _live_in_train_edges(estate: dict, workbook_id: str) -> list[str]:
    async with estate["pool"].acquire() as conn:
        rows = await conn.fetch(
            f"""
            SELECT to_id FROM {EDGE_INDEX_TABLE}
            WHERE graph = $1 AND label = 'IN_TRAIN' AND from_id = $2 AND retired_at IS NULL
            """,
            estate["settings"].graph_name,
            workbook_id,
        )
    return [row["to_id"] for row in rows]


def _train_containing(result, workbook_id: str):
    """The train a member ended up in — found by membership, not by index.

    The graph is shared across every test in this module (the established convention —
    see ``test_integration_family_overrides.py``'s own ``estate`` docstring), and
    ``TrainPlanner`` legitimately reads the *whole* graph's families, so earlier tests'
    families are still there when a later test computes a proposal. Every assertion below
    is therefore by membership, never by a train's index or an exact train/family count.
    """
    return next(t for t in result.trains if workbook_id in t.members)


# --------------------------------------------------------------------------- compute()


async def test_compute_orders_the_more_ready_family_no_later(estate) -> None:
    """family_a (DRAFT) outranks family_b/family_c (PROPOSED/SINGLETON) on readiness, the
    first and most significant sort key — so it can never be scheduled into a *later*
    train than either of them, whatever else the shared graph also contains."""
    result = await estate["planner"].compute(train_sizes=[3, 3])

    train_a = _train_containing(result, estate["a_members"][0])
    train_b = _train_containing(result, estate["b_members"][0])
    train_c = _train_containing(result, estate["c_members"][0])

    assert train_a.sequence <= train_b.sequence
    assert train_a.sequence <= train_c.sequence


async def test_compute_never_splits_a_family_across_trains(estate) -> None:
    result = await estate["planner"].compute(train_sizes=[3, 3])

    train_a = _train_containing(result, estate["a_members"][0])
    for member in estate["a_members"]:
        assert member in train_a.members

    train_b = _train_containing(result, estate["b_members"][0])
    for member in estate["b_members"]:
        assert member in train_b.members

    train_c = _train_containing(result, estate["c_members"][0])
    for member in estate["c_members"]:
        assert member in train_c.members


async def test_compute_reports_the_workbook_that_belongs_to_no_family_by_name(estate) -> None:
    result = await estate["planner"].compute(train_sizes=[3, 3])
    assert estate["loose"] in result.unclustered_workbook_ids
    for member in estate["a_members"] + estate["b_members"] + estate["c_members"]:
        assert member not in result.unclustered_workbook_ids


async def test_each_train_carries_a_one_paragraph_explanation(estate) -> None:
    result = await estate["planner"].compute(train_sizes=[3, 3])
    for train in result.trains:
        assert train.explanation.startswith(f"Train {train.sequence} packs")
        assert "shared-model readiness, usage and tier mix" in train.explanation


# --------------------------------------------------------------------------------- run()


async def test_run_writes_release_train_nodes_with_a_gate_schedule(estate) -> None:
    from astra_graph.lineage import hydrate

    result = await estate["planner"].run(principal=PRINCIPAL, train_sizes=[3, 3])

    async with estate["pool"].acquire() as conn:
        props = await hydrate(
            conn, estate["settings"].graph_name, "ReleaseTrain", [t.id for t in result.trains]
        )
    for train in result.trains:
        assert props[train.id]["planned_start"] == train.planned_start
        assert props[train.id]["planned_end"] == train.planned_end
        assert props[train.id]["gate_schedule"]["G2"]["planned_date"] == train.planned_start
        assert props[train.id]["gate_schedule"]["G3"]["planned_date"] == train.planned_end


async def test_run_writes_in_train_edges_in_sequence(estate) -> None:
    result = await estate["planner"].run(principal=PRINCIPAL, train_sizes=[3, 3])

    first_train = result.trains[0]
    for workbook_id in first_train.members:
        assert await _live_in_train_edges(estate, workbook_id) == [first_train.id]


async def test_a_workbook_is_in_train_exactly_one_train_even_after_a_re_run(estate) -> None:
    first = await estate["planner"].run(principal=PRINCIPAL, train_sizes=[3, 3])
    moved_workbook = first.trains[-1].members[0]

    second = await estate["planner"].run(principal=PRINCIPAL, train_sizes=[6])

    assert len(second.trains) == 1
    assert await _live_in_train_edges(estate, moved_workbook) == [second.trains[0].id]


async def test_a_re_run_retires_the_trains_it_replaces(estate) -> None:
    from astra_graph.lineage import hydrate

    first = await estate["planner"].run(principal=PRINCIPAL, train_sizes=[3, 3])
    await estate["planner"].run(principal=PRINCIPAL, train_sizes=[6])

    async with estate["pool"].acquire() as conn:
        props = await hydrate(
            conn, estate["settings"].graph_name, "ReleaseTrain", [t.id for t in first.trains]
        )
    for train in first.trains:
        assert props[train.id]["retired_at"] is not None


# --------------------------------------------------------------------------- tier mix


async def test_a_tiered_member_shows_up_in_the_explanation(estate) -> None:
    from astra_graph.scope import DecisionKind

    await estate["scope_store"].decide(
        new_decision(
            workbook_id=estate["a_members"][0],
            kind=DecisionKind.RE_TIER,
            reason="Redesign candidate — heavy custom SQL, per the architecture review",
            decided_by="user:pm@artizent.example",
            to_value="REDESIGN",
        )
    )

    result = await estate["planner"].compute(train_sizes=[3, 3])
    train = _train_containing(result, estate["a_members"][0])
    family_a_signal = next(f for f in train.families if f.id == estate["family_a"])

    assert "1 redesign" in train.explanation
    assert family_a_signal.tier_score == pytest.approx(3.0)


# ---------------------------------------------------------------------------- the API


@pytest.fixture
async def http_client(estate):
    from httpx import ASGITransport, AsyncClient

    from astra_graph.api.routes_trains import TrainProposalStatus
    from astra_graph.main import create_app

    app = create_app()
    app.state.train_planner = estate["planner"]
    app.state.train_status = TrainProposalStatus()

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://graph-svc") as async_client:
        yield async_client


def _headers(*, roles: str = "semantic_model_engineer") -> dict[str, str]:
    from astra_graph.principal import PRINCIPAL_HEADER
    from astra_graph.roles import ROLES_HEADER

    return {PRINCIPAL_HEADER: PRINCIPAL.value, ROLES_HEADER: roles}


async def test_propose_over_http(estate, http_client) -> None:
    response = await http_client.post(
        "/v1/trains:propose", json={"train_sizes": [3, 3]}, headers=_headers()
    )
    assert response.status_code == 202

    status_body: dict = {}
    for _ in range(150):
        status_response = await http_client.get("/v1/trains:propose/status", headers=_headers())
        status_body = status_response.json()
        if not status_body["running"]:
            break
        await asyncio.sleep(0.2)

    assert not status_body["running"], "the background run never finished"
    assert status_body["last_error"] is None
    # At most the two configured trains — could be fewer only if the (shared, accumulating)
    # graph somehow held under 3 MUs by this point in the suite, which it never does once
    # any earlier test in this module has run.
    assert 1 <= status_body["last_result"]["trains_produced"] <= 2

    listed = await http_client.get("/v1/trains", headers=_headers())
    assert listed.status_code == 200
    assert listed.json()["count"] == status_body["last_result"]["trains_produced"]

    train_id = status_body["last_result"]["trains"][0]["id"]
    one = await http_client.get(f"/v1/trains/{train_id}", headers=_headers())
    assert one.status_code == 200
    assert one.json()["gate_schedule"]["G2"]["planned_date"]


async def test_get_one_train_reports_an_unknown_id_as_404(estate, http_client) -> None:
    response = await http_client.get("/v1/trains/not-a-train", headers=_headers())
    assert response.status_code == 404


async def test_propose_refuses_a_non_artizent_role(estate, http_client) -> None:
    response = await http_client.post(
        "/v1/trains:propose", json={}, headers=_headers(roles="client_report_owner")
    )
    assert response.status_code == 403


async def test_propose_rejects_an_empty_train_sizes_list(estate, http_client) -> None:
    response = await http_client.post(
        "/v1/trains:propose", json={"train_sizes": []}, headers=_headers()
    )
    assert response.status_code == 422
