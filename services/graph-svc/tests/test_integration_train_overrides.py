"""The Wave Board — story S3.2.2, against real PostgreSQL + Apache AGE.

    "As a programme manager, I want a Wave Board where I can drag MUs between trains
    within scheduler constraints, so that re-planning is a board action, not a spreadsheet
    exercise."

What only the real store can answer: that a move genuinely retires the old IN_TRAIN edge
and writes a new one (never two live at once), that a family-splitting move is refused
against real IN_FAMILY data, that a WIP limit read back from ReleaseTrain.wip_limits
actually gates a move, and that TrainPlanner.run() genuinely leaves an overridden train's
membership untouched on a re-propose.
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
from astra_graph.graph.queries import EDGE_INDEX_TABLE, accessor  # noqa: E402
from astra_graph.ids import new_ulid  # noqa: E402
from astra_graph.migrations import run as run_migrations  # noqa: E402
from astra_graph.ontology import EDGE_LABELS, NODE_LABELS  # noqa: E402
from astra_graph.principal import Principal  # noqa: E402
from astra_graph.scope import PostgresScopeStore  # noqa: E402
from astra_graph.train_overrides import move_mu, resequence_mu, set_wip_limits  # noqa: E402
from astra_graph.trains import DEFAULT_MU_STATE, TrainPlanner, get_train  # noqa: E402
from astra_graph.writes import EdgeWrite, GraphWriter, NodeWrite  # noqa: E402

PRINCIPAL = Principal("agent:pm", run_id="run-wave-board")
REASON = "client asked to prioritise treasury reports ahead of risk"


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
    config = _settings(f"astra_wav_{new_ulid()[10:22].lower()}")

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
    """Two trains, hand-built for full control over what a move can and cannot do:

    * ``train_one`` = {alpha (own family), gamma1 + gamma2 (one shared family)}
    * ``train_two`` = {beta (own family)}

    ``alpha`` is safe to move alone (singleton family). ``gamma1``/``gamma2`` share a
    family, so moving either one alone must be refused — moving *both* one at a time is
    impossible by construction (see the module docstring: this board moves one MU at a
    time, and a sibling left behind is exactly the split S3.2.1's packing exists to
    prevent), which is the story's own scope, not a gap this suite works around.
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

        async def workbook(name: str) -> str:
            book = await _write(writer, "Workbook", luid=f"{name}-{suffix}", name=name, revision="1")
            await _edge(writer, "CONTAINS", project, book)
            return book

        alpha = await workbook(f"Alpha-{suffix}")
        beta = await workbook(f"Beta-{suffix}")
        gamma1 = await workbook(f"Gamma1-{suffix}")
        gamma2 = await workbook(f"Gamma2-{suffix}")

        async def family(name: str, members: list[str]) -> str:
            family_id = await _write(
                writer, "ModelFamily", name=name, state="PROPOSED", grain="Desk", conformed_dims=[],
            )
            for member in members:
                await _edge(writer, "IN_FAMILY", member, family_id, confidence=1.0)
            return family_id

        await family(f"FamilyAlpha-{suffix}", [alpha])
        await family(f"FamilyBeta-{suffix}", [beta])
        family_gamma = await family(f"FamilyGamma-{suffix}", [gamma1, gamma2])

        async def train(name: str) -> str:
            return await _write(
                writer, "ReleaseTrain", name=name,
                planned_start="2027-01-01", planned_end="2027-01-31",
                gate_schedule={"G2": {"planned_date": "2027-01-01"}, "G3": {"planned_date": "2027-01-31"}},
            )

        train_one = await train(f"Train One {suffix}")
        train_two = await train(f"Train Two {suffix}")

        for sequence, member in enumerate((alpha, gamma1, gamma2), start=1):
            await _edge(
                writer, "IN_TRAIN", member, train_one, sequence=sequence, state=DEFAULT_MU_STATE
            )
        await _edge(writer, "IN_TRAIN", beta, train_two, sequence=1, state=DEFAULT_MU_STATE)

        yield {
            "pool": pool,
            "settings": settings,
            "writer": writer,
            "repository": repository,
            "planner": TrainPlanner(
                pool, graph_name=settings.graph_name, writer=writer, scope_store=scope_store
            ),
            "alpha": alpha, "beta": beta, "gamma1": gamma1, "gamma2": gamma2,
            "family_gamma": family_gamma,
            "train_one": train_one, "train_two": train_two,
        }
    finally:
        await pool.close()


async def _current_train(estate: dict, workbook_id: str) -> str | None:
    async with estate["pool"].acquire() as conn:
        return await conn.fetchval(
            f"""
            SELECT to_id FROM {EDGE_INDEX_TABLE}
            WHERE graph = $1 AND label = 'IN_TRAIN' AND from_id = $2 AND retired_at IS NULL
            """,
            estate["settings"].graph_name,
            workbook_id,
        )


async def _live_in_train_edges(estate: dict, workbook_id: str) -> list[str]:
    async with estate["pool"].acquire() as conn:
        rows = await conn.fetch(
            f"""
            SELECT id FROM {EDGE_INDEX_TABLE}
            WHERE graph = $1 AND label = 'IN_TRAIN' AND from_id = $2 AND retired_at IS NULL
            """,
            estate["settings"].graph_name,
            workbook_id,
        )
    return [row["id"] for row in rows]


# ------------------------------------------------------------------------------- move


async def test_moving_a_singleton_family_succeeds(estate) -> None:
    result = await move_mu(
        estate["pool"], estate["settings"].graph_name, estate["writer"],
        workbook_id=estate["alpha"], to_train_id=estate["train_two"],
        reason=None, principal=PRINCIPAL,
    )
    assert result.from_train_id == estate["train_one"]
    assert result.to_train_id == estate["train_two"]
    assert result.state == DEFAULT_MU_STATE
    assert await _current_train(estate, estate["alpha"]) == estate["train_two"]


async def test_a_move_leaves_exactly_one_live_in_train_edge(estate) -> None:
    await move_mu(
        estate["pool"], estate["settings"].graph_name, estate["writer"],
        workbook_id=estate["alpha"], to_train_id=estate["train_two"],
        reason=None, principal=PRINCIPAL,
    )
    assert len(await _live_in_train_edges(estate, estate["alpha"])) == 1


async def test_moving_one_of_two_family_members_is_refused(estate) -> None:
    with pytest.raises(InvalidRequestError, match="FamilyGamma|split"):
        await move_mu(
            estate["pool"], estate["settings"].graph_name, estate["writer"],
            workbook_id=estate["gamma1"], to_train_id=estate["train_two"],
            reason=REASON, principal=PRINCIPAL,
        )
    # Refused even with a reason offered — a family split is not a WIP-style warning.
    assert await _current_train(estate, estate["gamma1"]) == estate["train_one"]


async def test_moving_to_an_unknown_train_reports_404(estate) -> None:
    with pytest.raises(ElementNotFoundError):
        await move_mu(
            estate["pool"], estate["settings"].graph_name, estate["writer"],
            workbook_id=estate["alpha"], to_train_id="not-a-train",
            reason=None, principal=PRINCIPAL,
        )


async def test_moving_to_the_same_train_is_refused(estate) -> None:
    with pytest.raises(InvalidRequestError):
        await move_mu(
            estate["pool"], estate["settings"].graph_name, estate["writer"],
            workbook_id=estate["alpha"], to_train_id=estate["train_one"],
            reason=None, principal=PRINCIPAL,
        )


async def test_a_move_marks_both_trains_overridden(estate) -> None:
    from astra_graph.lineage import hydrate

    await move_mu(
        estate["pool"], estate["settings"].graph_name, estate["writer"],
        workbook_id=estate["alpha"], to_train_id=estate["train_two"],
        reason=None, principal=PRINCIPAL,
    )
    async with estate["pool"].acquire() as conn:
        props = await hydrate(
            conn, estate["settings"].graph_name, "ReleaseTrain",
            [estate["train_one"], estate["train_two"]],
        )
    for train_id in (estate["train_one"], estate["train_two"]):
        assert props[train_id]["overridden"] is True
        assert props[train_id]["override_action"] == "MOVE"


async def test_a_move_preserves_the_trains_own_planned_dates(estate) -> None:
    """An upsert replaces the whole property set — a naive one would wipe out
    planned_start/planned_end/gate_schedule while only meaning to flip `overridden`."""
    from astra_graph.lineage import hydrate

    await move_mu(
        estate["pool"], estate["settings"].graph_name, estate["writer"],
        workbook_id=estate["alpha"], to_train_id=estate["train_two"],
        reason=None, principal=PRINCIPAL,
    )
    async with estate["pool"].acquire() as conn:
        props = await hydrate(conn, estate["settings"].graph_name, "ReleaseTrain", [estate["train_two"]])
    assert props[estate["train_two"]]["planned_start"] == "2027-01-01"
    assert props[estate["train_two"]]["gate_schedule"]["G3"]["planned_date"] == "2027-01-31"


# ------------------------------------------------------------------------------- WIP


async def test_a_move_within_a_configured_limit_needs_no_reason(estate) -> None:
    await set_wip_limits(
        estate["pool"], estate["settings"].graph_name, estate["writer"],
        train_id=estate["train_two"], train_limit=5, state_limits=None,
        reason=REASON, principal=PRINCIPAL,
    )
    result = await move_mu(
        estate["pool"], estate["settings"].graph_name, estate["writer"],
        workbook_id=estate["alpha"], to_train_id=estate["train_two"],
        reason=None, principal=PRINCIPAL,
    )
    assert result.wip is not None
    assert not result.wip.exceeded


async def test_exceeding_the_train_limit_without_a_reason_is_refused(estate) -> None:
    await set_wip_limits(
        estate["pool"], estate["settings"].graph_name, estate["writer"],
        train_id=estate["train_two"], train_limit=1, state_limits=None,
        reason=REASON, principal=PRINCIPAL,
    )
    with pytest.raises(InvalidRequestError, match="WIP"):
        await move_mu(
            estate["pool"], estate["settings"].graph_name, estate["writer"],
            workbook_id=estate["alpha"], to_train_id=estate["train_two"],
            reason=None, principal=PRINCIPAL,
        )
    assert await _current_train(estate, estate["alpha"]) == estate["train_one"]


async def test_exceeding_the_train_limit_with_a_reason_proceeds(estate) -> None:
    from astra_graph.lineage import hydrate

    await set_wip_limits(
        estate["pool"], estate["settings"].graph_name, estate["writer"],
        train_id=estate["train_two"], train_limit=1, state_limits=None,
        reason=REASON, principal=PRINCIPAL,
    )
    result = await move_mu(
        estate["pool"], estate["settings"].graph_name, estate["writer"],
        workbook_id=estate["alpha"], to_train_id=estate["train_two"],
        reason=REASON, principal=PRINCIPAL,
    )
    assert result.wip is not None
    assert result.wip.exceeded
    edge_id = (await _live_in_train_edges(estate, estate["alpha"]))[0]
    async with estate["pool"].acquire() as conn:
        props = await hydrate(conn, estate["settings"].graph_name, "IN_TRAIN", [edge_id])
    assert props[edge_id]["wip_override_reason"] == REASON


async def test_set_wip_limits_rejects_an_unrecognised_state(estate) -> None:
    with pytest.raises(InvalidRequestError):
        await set_wip_limits(
            estate["pool"], estate["settings"].graph_name, estate["writer"],
            train_id=estate["train_one"], train_limit=None, state_limits={"NOT_A_STATE": 3},
            reason=REASON, principal=PRINCIPAL,
        )


async def test_set_wip_limits_needs_a_reason(estate) -> None:
    with pytest.raises(InvalidRequestError):
        await set_wip_limits(
            estate["pool"], estate["settings"].graph_name, estate["writer"],
            train_id=estate["train_one"], train_limit=5, state_limits=None,
            reason="short", principal=PRINCIPAL,
        )


# --------------------------------------------------------------------------- resequence


async def test_resequencing_reorders_within_the_train(estate) -> None:
    train_id, position = await resequence_mu(
        estate["pool"], estate["settings"].graph_name, estate["writer"],
        workbook_id=estate["gamma2"], position=1, principal=PRINCIPAL,
    )
    assert train_id == estate["train_one"]
    assert position == 1

    train = await get_train(estate["pool"], estate["settings"].graph_name, estate["train_one"])
    ordered_ids = [m["id"] for m in train["members"]]
    assert ordered_ids[0] == estate["gamma2"]


async def test_resequencing_never_moves_a_workbook_between_trains(estate) -> None:
    await resequence_mu(
        estate["pool"], estate["settings"].graph_name, estate["writer"],
        workbook_id=estate["gamma1"], position=99, principal=PRINCIPAL,
    )
    assert await _current_train(estate, estate["gamma1"]) == estate["train_one"]


async def test_a_position_beyond_the_trains_size_is_clamped_to_last(estate) -> None:
    _train_id, position = await resequence_mu(
        estate["pool"], estate["settings"].graph_name, estate["writer"],
        workbook_id=estate["alpha"], position=999, principal=PRINCIPAL,
    )
    assert position == 3  # train_one has 3 members total


# ---------------------------------------------------------------- run() respects overrides


async def test_a_re_propose_leaves_an_overridden_trains_membership_alone(estate) -> None:
    await move_mu(
        estate["pool"], estate["settings"].graph_name, estate["writer"],
        workbook_id=estate["alpha"], to_train_id=estate["train_two"],
        reason=None, principal=PRINCIPAL,
    )

    await estate["planner"].run(principal=PRINCIPAL, train_sizes=[50])

    assert await _current_train(estate, estate["alpha"]) == estate["train_two"]
    assert await _current_train(estate, estate["beta"]) == estate["train_two"]


async def test_confirming_a_trains_id_lets_the_re_propose_replace_it(estate) -> None:
    await move_mu(
        estate["pool"], estate["settings"].graph_name, estate["writer"],
        workbook_id=estate["alpha"], to_train_id=estate["train_two"],
        reason=None, principal=PRINCIPAL,
    )

    result = await estate["planner"].run(
        principal=PRINCIPAL, train_sizes=[50],
        confirm_train_ids=frozenset({estate["train_one"], estate["train_two"]}),
    )

    # A fresh, single train now holds everything the free algorithm packed.
    assert len(result.trains) == 1
    fresh_members = set(result.trains[0].members)
    assert estate["alpha"] in fresh_members
    assert estate["beta"] in fresh_members
    assert estate["gamma1"] in fresh_members
    assert estate["gamma2"] in fresh_members


# ---------------------------------------------------------------------------- the API


@pytest.fixture
async def http_client(estate):
    from httpx import ASGITransport, AsyncClient

    from astra_graph.api.routes_trains import TrainProposalStatus
    from astra_graph.main import create_app

    app = create_app()
    app.state.train_planner = estate["planner"]
    app.state.train_status = TrainProposalStatus()
    app.state.repository = estate["repository"]

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://graph-svc") as async_client:
        yield async_client


def _headers(*, roles: str = "programme_manager") -> dict[str, str]:
    from astra_graph.principal import PRINCIPAL_HEADER
    from astra_graph.roles import ROLES_HEADER

    return {PRINCIPAL_HEADER: PRINCIPAL.value, ROLES_HEADER: roles}


async def test_move_member_over_http(estate, http_client) -> None:
    response = await http_client.post(
        f"/v1/trains/{estate['train_two']}:move-member",
        json={"workbook_id": estate["alpha"]},
        headers=_headers(),
    )
    assert response.status_code == 200
    assert response.json()["to_train_id"] == estate["train_two"]


async def test_move_member_refuses_a_non_programme_manager(estate, http_client) -> None:
    response = await http_client.post(
        f"/v1/trains/{estate['train_two']}:move-member",
        json={"workbook_id": estate["alpha"]},
        headers=_headers(roles="semantic_model_engineer"),
    )
    assert response.status_code == 403


async def test_resequence_member_over_http(estate, http_client) -> None:
    response = await http_client.post(
        f"/v1/trains/{estate['train_one']}:resequence-member",
        json={"workbook_id": estate["gamma2"], "position": 1},
        headers=_headers(),
    )
    assert response.status_code == 200
    assert response.json()["position"] == 1


async def test_set_wip_limits_over_http(estate, http_client) -> None:
    response = await http_client.post(
        f"/v1/trains/{estate['train_one']}:set-wip-limits",
        json={"train_limit": 10, "state_limits": {}, "reason": REASON},
        headers=_headers(),
    )
    assert response.status_code == 200
    assert response.json()["wip_limits"]["train"] == 10


async def test_train_events_over_http_after_a_move(estate, http_client) -> None:
    await http_client.post(
        f"/v1/trains/{estate['train_two']}:move-member",
        json={"workbook_id": estate["alpha"]},
        headers=_headers(),
    )
    response = await http_client.get(f"/v1/trains/{estate['train_two']}/events", headers=_headers())
    assert response.status_code == 200
    body = response.json()
    assert body["events"]
    subjects = {event["event"]["subject"] for event in body["events"]}
    assert estate["train_two"] in subjects


async def test_train_events_reports_an_unknown_train_as_404(estate, http_client) -> None:
    response = await http_client.get("/v1/trains/not-a-train/events", headers=_headers())
    assert response.status_code == 404
