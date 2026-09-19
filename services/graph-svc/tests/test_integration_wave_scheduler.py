"""The wave scheduler's constraints against a real PostgreSQL + Apache AGE -- story S12.1.2.

`WaveScheduler` reads graph state through `GraphRepository.run_read_only_cypher`, which
only Apache AGE implements (the in-memory fixture raises `NotImplementedError` by
design), and AGE supports only a subset of Cypher -- so the only honest proof that every
query in `wave_scheduler.py` actually runs, and returns what the schema says it should,
is to run it. Each test seeds a small, uniquely-named estate through the real write path
(the graph persists between runs) and asserts the scheduler's real decision.
"""

from __future__ import annotations

import os

import pytest

pytestmark = pytest.mark.integration

asyncpg = pytest.importorskip("asyncpg")

from astra_graph.config import Settings  # noqa: E402
from astra_graph.gateway import PostgresGatewayRequestLogStore  # noqa: E402
from astra_graph.graph import AgeGraphRepository, create_pool  # noqa: E402
from astra_graph.ids import new_ulid  # noqa: E402
from astra_graph.migrations import run as run_migrations  # noqa: E402
from astra_graph.principal import Principal  # noqa: E402
from astra_graph.token_budget import TokenBudgetStore  # noqa: E402
from astra_graph.wave_scheduler import SchedulerConstraint, WaveScheduler  # noqa: E402
from astra_graph.writes import EdgeWrite, GraphWriter, NodeWrite  # noqa: E402

PRINCIPAL = Principal("agent:cartographer", run_id="run-wave-scheduler-test")


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


class _Estate:
    """A real, uniquely-named site -> project -> workbook, a family, and a train."""

    def __init__(self, writer: GraphWriter) -> None:
        self.writer = writer
        self.suffix = f"-{new_ulid()}"

    async def node(self, type_: str, **properties: object) -> str:
        [record] = await self.writer.write_nodes(
            [NodeWrite(type=type_, properties=properties)], principal=PRINCIPAL
        )
        return str(record["properties"]["id"])

    async def edge(self, type_: str, from_id: str, to_id: str, **properties: object) -> None:
        await self.writer.write_edge(
            EdgeWrite(type=type_, from_id=from_id, to_id=to_id, properties=properties),
            principal=PRINCIPAL,
        )

    async def build(self, *, family_state: str = "BUILT") -> None:
        self.site = await self.node("Site", luid=f"site{self.suffix}", name="Test site")
        self.project = await self.node("Project", luid=f"proj{self.suffix}", name="P")
        self.workbook = await self.workbook_in_project("wb-under-test")
        self.family = await self.node("ModelFamily", name="F", state=family_state)
        self.train = await self.node("ReleaseTrain", name="T")
        await self.edge("CONTAINS", self.site, self.project)
        await self.edge("CONTAINS", self.project, self.workbook)
        await self.edge("IN_FAMILY", self.workbook, self.family, confidence=0.9)
        await self.edge("IN_TRAIN", self.workbook, self.train, sequence=1)

    async def workbook_in_project(self, name: str, **properties: object) -> str:
        return await self.node(
            "Workbook", luid=f"{name}{self.suffix}-{new_ulid()}", name=name, revision="1",
            **properties,
        )


@pytest.fixture
async def estate(repository) -> _Estate:
    estate = _Estate(GraphWriter(repository))
    await estate.build()
    return estate


def _scheduler(repository) -> WaveScheduler:
    # The repository's own pool, so the scheduler reads real per-MU spend (story S12.2.2)
    # from the same database the estate lives in.
    store = TokenBudgetStore(repository._pool, graph_name=repository.graph_name)
    return WaveScheduler(repository, budget_store=store)


async def _decide(repository, estate: _Estate):
    return await _scheduler(repository).evaluate_admission(
        estate.workbook, estate.train, estate.site
    )


async def test_an_unconstrained_mu_is_admitted(repository, estate) -> None:
    decision = await _decide(repository, estate)
    assert decision.admitted, decision.reason
    assert decision.blocking_constraint is None


async def test_a_family_short_of_built_holds_the_mu(repository) -> None:
    estate = _Estate(GraphWriter(repository))
    await estate.build(family_state="DRAFT")
    decision = await _decide(repository, estate)
    assert not decision.admitted
    assert decision.blocking_constraint == SchedulerConstraint.FAMILY_STATE


async def test_a_mu_with_no_family_is_held(repository) -> None:
    estate = _Estate(GraphWriter(repository))
    await estate.build()
    orphan = await estate.workbook_in_project("orphan")
    decision = await _scheduler(repository).evaluate_admission(
        orphan, estate.train, estate.site
    )
    assert not decision.admitted
    assert decision.blocking_constraint == SchedulerConstraint.FAMILY_STATE


async def test_a_paused_train_holds_the_mu_and_resuming_releases_it(repository, estate) -> None:
    await estate.writer.set_node_properties(
        estate.train, {"paused": True, "pause_reason": "incident"}, principal=PRINCIPAL
    )
    held = await _decide(repository, estate)
    assert not held.admitted
    assert held.blocking_constraint == SchedulerConstraint.PAUSED_TRAIN

    await estate.writer.set_node_properties(
        estate.train, {"paused": False, "pause_reason": None}, principal=PRINCIPAL
    )
    assert (await _decide(repository, estate)).admitted


async def test_a_paused_site_holds_the_mu(repository, estate) -> None:
    await estate.writer.set_node_properties(
        estate.site, {"paused": True, "pause_reason": "maintenance"}, principal=PRINCIPAL
    )
    decision = await _decide(repository, estate)
    assert not decision.admitted
    assert decision.blocking_constraint == SchedulerConstraint.PAUSED_SITE


async def test_a_train_at_its_wip_limit_holds_the_mu(repository, estate) -> None:
    await estate.writer.set_node_properties(
        estate.train, {"wip_limits": {"train": 1, "states": {}}}, principal=PRINCIPAL
    )
    busy = await estate.workbook_in_project("busy", mu_state="PROVING")
    await estate.edge("IN_TRAIN", busy, estate.train, sequence=2)

    decision = await _decide(repository, estate)
    assert not decision.admitted
    assert decision.blocking_constraint == SchedulerConstraint.WIP_LIMIT


async def test_a_site_at_its_concurrency_limit_holds_the_mu(repository, estate) -> None:
    for i in range(5):
        active = await estate.workbook_in_project(f"active{i}", mu_state="PROVING")
        await estate.edge("CONTAINS", estate.project, active)

    decision = await _decide(repository, estate)
    assert not decision.admitted
    assert decision.blocking_constraint == SchedulerConstraint.EXECUTOR_CONCURRENCY_SITE


async def test_terminal_and_waiting_workbooks_do_not_count_against_a_site(
    repository, estate
) -> None:
    for i, state in enumerate(["GENERATED", "ACCEPTED", "WITHDRAWN"] * 3):
        other = await estate.workbook_in_project(f"idle{i}", mu_state=state)
        await estate.edge("CONTAINS", estate.project, other)

    assert (await _decide(repository, estate)).admitted


async def test_a_workspace_at_its_concurrency_limit_holds_the_mu(repository, estate) -> None:
    workspace = f"ws{estate.suffix}"
    await estate.node("SemanticModel", family_ref=estate.family, workspace=workspace)
    # Workbooks outside the site's own project, so only the workspace limit binds.
    for i in range(10):
        active = await estate.workbook_in_project(f"ws-active{i}", mu_state="PROVING")
        await estate.edge("IN_FAMILY", active, estate.family, confidence=0.9)

    decision = await _decide(repository, estate)
    assert not decision.admitted
    assert decision.blocking_constraint == SchedulerConstraint.EXECUTOR_CONCURRENCY_WORKSPACE


async def _spend(repository, estate: _Estate, *, tokens: int, workbook: str | None = None) -> None:
    """Really log `tokens` of model use against a workbook, as the gateway would."""
    await PostgresGatewayRequestLogStore(
        repository._pool, graph_name=repository.graph_name
    ).record(
        provider="anthropic", task_class="transpile_c3", agent_id=None, prompt_hash="sha256:s",
        context_hash="sha256:c", request_text=None, response_hash=None, response_text=None,
        redaction_count=0, latency_ms=1.0, prompt_template_version="test",
        model="claude-sonnet-5", tokens_in=tokens, tokens_out=0,
        workbook_id=workbook or estate.workbook,
    )


async def test_an_mu_that_has_used_its_token_budget_is_held(repository, estate) -> None:
    store = TokenBudgetStore(repository._pool, graph_name=repository.graph_name)
    await store.set_budget(estate.workbook, 100)
    await _spend(repository, estate, tokens=100)

    decision = await _decide(repository, estate)

    assert not decision.admitted
    assert decision.blocking_constraint == SchedulerConstraint.MODEL_GATEWAY_BUDGET
    assert "100/100" in decision.reason


async def test_raising_the_budget_releases_the_held_mu(repository, estate) -> None:
    store = TokenBudgetStore(repository._pool, graph_name=repository.graph_name)
    await store.set_budget(estate.workbook, 100)
    await _spend(repository, estate, tokens=100)
    assert not (await _decide(repository, estate)).admitted

    await store.set_budget(estate.workbook, 500)

    assert (await _decide(repository, estate)).admitted


async def test_an_mu_in_the_80_percent_zone_is_still_admitted(repository, estate) -> None:
    store = TokenBudgetStore(repository._pool, graph_name=repository.graph_name)
    await store.set_budget(estate.workbook, 100)
    await _spend(repository, estate, tokens=90)

    assert (await _decide(repository, estate)).admitted


async def test_another_mus_spend_does_not_hold_this_one(repository, estate) -> None:
    store = TokenBudgetStore(repository._pool, graph_name=repository.graph_name)
    await store.set_budget(estate.workbook, 100)
    await _spend(repository, estate, tokens=1_000_000, workbook=f"other-{new_ulid()}")

    assert (await _decide(repository, estate)).admitted


async def test_an_earlier_constraint_is_reported_ahead_of_the_budget(repository) -> None:
    estate = _Estate(GraphWriter(repository))
    await estate.build(family_state="DRAFT")
    await TokenBudgetStore(repository._pool, graph_name=repository.graph_name).set_budget(
        estate.workbook, 100
    )
    await _spend(repository, estate, tokens=100)

    decision = await _decide(repository, estate)

    assert decision.blocking_constraint == SchedulerConstraint.FAMILY_STATE


async def _get_decision(repository, estate: _Estate) -> dict:
    """The real route, over the real repository and pool, as a programme manager sees it."""
    from httpx import ASGITransport, AsyncClient

    from astra_graph.main import create_app
    from astra_graph.principal import PRINCIPAL_HEADER
    from astra_graph.roles import ROLES_HEADER

    app = create_app()
    app.state.repository = repository
    app.state.pool = repository._pool
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get(
            f"/v1/scheduler/decision/{estate.workbook}/{estate.train}",
            headers={PRINCIPAL_HEADER: "user:pm@artizent.example", ROLES_HEADER: "programme_manager"},
        )
    assert response.status_code == 200, response.text
    return response.json()


async def test_the_decision_route_explains_a_budget_hold_and_its_release(repository, estate) -> None:
    """The Wave Board's 'why is this MU waiting', end to end over HTTP."""
    store = TokenBudgetStore(repository._pool, graph_name=repository.graph_name)
    await store.set_budget(estate.workbook, 100)
    await _spend(repository, estate, tokens=100)

    held = await _get_decision(repository, estate)
    assert held["admitted"] is False
    assert held["blocking_constraint"] == "MODEL_GATEWAY_BUDGET"
    assert "100/100" in held["reason"]

    await store.set_budget(estate.workbook, 500)
    released = await _get_decision(repository, estate)
    assert released["admitted"] is True and released["blocking_constraint"] is None
