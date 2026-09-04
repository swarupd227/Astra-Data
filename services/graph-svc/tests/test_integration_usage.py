"""Usage and ownership against a real PostgreSQL + Apache AGE.

What only the real store can answer: that per-view usage lands on the Worksheet and
Dashboard nodes, that VIEWED_BY edges carry per-viewer counts, and that the
unresolved-owner listing's two reads — every User, and how many workbooks each owns —
agree with the in-memory implementation the unit suite asserts against.
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

from astra_graph.adapters.contract import Scope  # noqa: E402
from astra_graph.adapters.fixture import (  # noqa: E402
    FixtureSite,
    FixtureSourceAdapter,
    FixtureWorkbook,
)
from astra_graph.config import Settings  # noqa: E402
from astra_graph.credentials import StaticCredentialProvider  # noqa: E402
from astra_graph.directory import DirectoryUser, StaticDirectoryResolver  # noqa: E402
from astra_graph.events import source_for  # noqa: E402
from astra_graph.graph import AgeGraphRepository, create_pool  # noqa: E402
from astra_graph.graph.queries import accessor  # noqa: E402
from astra_graph.harvest import (  # noqa: E402
    Harvester,
    HarvestRequest,
    InMemoryHarvestStore,
    derive_id,
)
from astra_graph.ids import new_ulid  # noqa: E402
from astra_graph.migrations import run as run_migrations  # noqa: E402
from astra_graph.ontology import EDGE_LABELS, NODE_LABELS  # noqa: E402
from astra_graph.principal import Principal  # noqa: E402
from astra_graph.writes import GraphWriter  # noqa: E402

PRINCIPAL = Principal("agent:harvester", run_id="run-integration")
CREDENTIALS = StaticCredentialProvider({"tableau/rqa": "a-token"})


def people() -> tuple[str, str, str]:
    """Identities unique to one test: the module shares a graph."""
    suffix = new_ulid()[10:18].lower()
    return (
        f"owner.{suffix}@client.example",
        f"viewer.{suffix}@client.example",
        f"gone.{suffix}@client.example",
    )


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
    config = _settings(f"astra_usage_{new_ulid()[10:22].lower()}")

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
                "public.harvest_workbook",
            ):
                await conn.execute(f"DELETE FROM {table} WHERE graph = $1", config.graph_name)
            await conn.execute("SELECT ag_catalog.drop_graph($1, true)", config.graph_name)
        finally:
            await conn.close()

    if not _run_off_loop(setup):
        pytest.skip("PostgreSQL with Apache AGE not reachable")

    yield config
    _run_off_loop(teardown)


@pytest.fixture
async def stack(settings: Settings):
    pool = await create_pool(settings)
    try:
        repository = AgeGraphRepository(pool, graph_name=settings.graph_name)
        writer = GraphWriter(repository, event_source=source_for(settings.graph_name))

        def build(site, directory) -> Harvester:
            return Harvester(
                adapter=FixtureSourceAdapter([site]),
                writer=writer,
                store=InMemoryHarvestStore(),
                credentials=CREDENTIALS,
                graph_name=settings.graph_name,
                directory=directory,
            )

        yield build, repository, writer
    finally:
        await pool.close()


def _site(name: str, owner: str, viewer: str, *, gone: str | None = None) -> FixtureSite:
    workbooks = [
        FixtureWorkbook(
            name="Daily VaR",
            luid=f"{name}-wb-1",
            project="Risk Core",
            sheets=2,
            dashboards=1,
            views_90d=400,
            distinct_viewers_90d=31,
            owner_upn=owner,
            viewers=((viewer, 260), (owner, 140)),
        )
    ]
    if gone:
        workbooks.append(
            FixtureWorkbook(
                name="Legacy VaR",
                luid=f"{name}-wb-2",
                project="Risk Core",
                views_90d=12,
                distinct_viewers_90d=2,
                owner_upn=gone,
                viewers=(),
            )
        )
    return FixtureSite(
        name=name, workbooks=workbooks, licence_tier="User-based", user_count=94
    )


def _request(site: str) -> HarvestRequest:
    return HarvestRequest(scope=Scope(site=site), credential_reference="tableau/rqa")


async def test_usage_lands_per_workbook_and_per_view(stack) -> None:
    """S1.2.3 criterion 1, in the graph."""
    build, repository, _ = stack
    owner, viewer, _gone = people()
    name = f"s{new_ulid()[10:18].lower()}"
    directory = StaticDirectoryResolver(
        {owner: DirectoryUser("11111111-1111-4111-8111-111111111111", owner, "An Owner")}
    )

    await build(_site(name, owner, viewer), directory).run(
        _request(name), principal=PRINCIPAL
    )

    workbook = await repository.get_node_by_luid("Workbook", f"{name}-wb-1")
    assert workbook.properties["views_90d"] == 400
    assert workbook.properties["distinct_viewers_90d"] == 31

    neighbourhood = await repository.neighbourhood(workbook.id, depth=1)
    views = [
        n.node
        for n in neighbourhood.neighbours
        if n.node.label in {"Worksheet", "Dashboard"}
    ]
    assert len(views) == 3
    assert all("views_90d" in view.properties for view in views)
    assert sum(view.properties["views_90d"] for view in views) == 400


async def test_viewed_by_carries_per_viewer_counts(stack) -> None:
    """Spec §4.1.2: views_90d per (workbook, user) pair."""
    build, repository, _ = stack
    owner, viewer, _gone = people()
    name = f"s{new_ulid()[10:18].lower()}"

    await build(_site(name, owner, viewer), StaticDirectoryResolver({})).run(
        _request(name), principal=PRINCIPAL
    )

    workbook = await repository.get_node_by_luid("Workbook", f"{name}-wb-1")
    result = await repository.neighbourhood(workbook.id, depth=1, edge_types=["VIEWED_BY"])
    viewed = [edge for edge in result.edges if edge.label == "VIEWED_BY"]
    assert len(viewed) == 2
    assert sorted(edge.properties["views_90d"] for edge in viewed) == [140, 260]


async def test_a_resolved_owner_carries_its_directory_link(stack) -> None:
    """S1.2.3 criterion 2."""
    build, repository, _ = stack
    owner, viewer, _gone = people()
    name = f"s{new_ulid()[10:18].lower()}"
    directory = StaticDirectoryResolver(
        {owner: DirectoryUser("11111111-1111-4111-8111-111111111111", owner, "An Owner")}
    )

    await build(_site(name, owner, viewer), directory).run(
        _request(name), principal=PRINCIPAL
    )

    record = await repository.get_node_record(derive_id(name, f"user:{owner}"))
    assert record.properties["directory_id"] == "11111111-1111-4111-8111-111111111111"
    assert record.properties["display"] == "An Owner"

    unresolved = await repository.get_node_record(derive_id(name, f"user:{viewer}"))
    assert "directory_id" not in unresolved.properties


async def test_the_unresolved_listing_reads_agree_with_the_graph(stack) -> None:
    """The two reads behind the listing: every User, and how many each owns."""
    build, repository, _ = stack
    owner, viewer, gone = people()
    name = f"s{new_ulid()[10:18].lower()}"
    directory = StaticDirectoryResolver(
        {owner: DirectoryUser("11111111-1111-4111-8111-111111111111", owner, "An Owner")}
    )

    await build(_site(name, owner, viewer, gone=gone), directory).run(
        _request(name), principal=PRINCIPAL
    )

    users = await repository.nodes_of_type("User", limit=10_000)
    mine = {u.properties["upn"]: u for u in users if u.properties["upn"].endswith(
        owner.split("@")[1]
    ) and u.properties["upn"] in {owner, viewer, gone}}
    assert set(mine) == {owner, viewer, gone}
    assert "directory_id" not in mine[gone].properties

    counts = await repository.incoming_counts(
        [user.id for user in mine.values()], edge_type="OWNED_BY"
    )
    assert counts[mine[owner].id] == 1
    assert counts[mine[gone].id] == 1
    assert mine[viewer].id not in counts, "a viewer who owns nothing owns nothing"


async def test_licence_tiers_land_on_the_site_and_the_user(stack) -> None:
    """S1.2.3 criterion 3."""
    build, repository, _ = stack
    owner, viewer, _gone = people()
    name = f"s{new_ulid()[10:18].lower()}"

    await build(_site(name, owner, viewer), StaticDirectoryResolver({})).run(
        _request(name), principal=PRINCIPAL
    )

    site = await repository.get_node_record(derive_id(name, f"site:{name}"))
    assert site.properties["licence_tier"] == "User-based"
    assert site.properties["user_count"] == 94

    user = await repository.get_node_record(derive_id(name, f"user:{owner}"))
    assert user.properties["licence_tier"] == "Creator"


async def test_assigning_a_directory_link_preserves_the_rest_of_the_user(stack) -> None:
    build, repository, writer = stack
    owner, viewer, gone = people()
    name = f"s{new_ulid()[10:18].lower()}"

    await build(_site(name, owner, viewer, gone=gone), StaticDirectoryResolver({})).run(
        _request(name), principal=PRINCIPAL
    )

    node_id = derive_id(name, f"user:{gone}")
    before = await repository.get_node_record(node_id)
    assert before.properties["licence_tier"] == "Creator"

    await writer.set_node_properties(
        node_id,
        {
            "directory_id": "44444444-4444-4444-8444-444444444444",
            "directory_resolved_at": "2027-01-14T09:12:07.000Z",
        },
        principal=Principal("user:pm@artizent.example", run_id="run-assign"),
    )

    after = await repository.get_node_record(node_id)
    assert after.properties["directory_id"] == "44444444-4444-4444-8444-444444444444"
    assert after.properties["upn"] == gone
    assert after.properties["licence_tier"] == "Creator"
    # User is the one type whose side the writer declares (spec §4.1.1).
    assert after.properties["side"] == "source"
    assert after.properties["created_by"] == PRINCIPAL.value
    assert after.properties["updated_by"] == "user:pm@artizent.example"
