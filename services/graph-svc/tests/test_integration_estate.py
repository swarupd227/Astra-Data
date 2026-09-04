"""The Estate Explorer's reads, against real PostgreSQL + Apache AGE.

Two things only the real store can answer. First, that the four queries actually place,
own and count the workbooks they claim to — every one of them joins the relational
adjacency index to an AGE label table, and neither the in-memory fake nor the pure
filtering logic exercises that.

Second, and the reason this file has a benchmark: S1.4.1 says "screen loads a
1,067-workbook site in under 2 seconds". That is a claim about a database, not about a
React component, so it is measured here against an estate of that size.
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
from astra_graph.estate import EstateFilter, EstateReader  # noqa: E402
from astra_graph.events import source_for  # noqa: E402
from astra_graph.graph import AgeGraphRepository, create_pool  # noqa: E402
from astra_graph.graph.queries import accessor  # noqa: E402
from astra_graph.ids import new_ulid  # noqa: E402
from astra_graph.migrations import run as run_migrations  # noqa: E402
from astra_graph.ontology import EDGE_LABELS, NODE_LABELS  # noqa: E402
from astra_graph.principal import Principal  # noqa: E402
from astra_graph.scope import (  # noqa: E402
    DecisionKind,
    PostgresScopeStore,
    new_decision,
)
from astra_graph.writes import EdgeWrite, GraphWriter, NodeWrite  # noqa: E402

PRINCIPAL = Principal("agent:harvester", run_id="run-estate")

#: S1.4.1's figure, and the size of the estate the benchmark builds.
TARGET_WORKBOOKS = 1_067

#: S1.4.1: "screen loads a 1,067-workbook site in under 2 seconds". The API's share of
#: that budget — the console then renders, which is measured in the browser, not here.
LOAD_BUDGET_MS = 2_000


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
    config = _settings(f"astra_est_{new_ulid()[10:22].lower()}")

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
                "public.scope_decision",
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


async def seed(writer: GraphWriter, *, site: str, workbooks: int, projects: int = 8) -> dict:
    """A site of ``workbooks`` workbooks, placed, owned, scored and calculated.

    Written through ``GraphWriter`` so it goes through the ontology and the outbox exactly
    as a harvest would — a benchmark against data inserted by a shortcut would be
    measuring a shape the platform never produces.
    """
    site_node = (
        await writer.write_nodes(
            [NodeWrite(type="Site", properties={"luid": f"{site}-luid", "name": site})],
            principal=PRINCIPAL,
        )
    )[0]
    site_id = str(site_node["properties"]["id"])

    project_records = await writer.write_nodes(
        [
            NodeWrite(
                type="Project",
                properties={"luid": f"{site}-prj-{i}", "name": f"Project {i}"},
            )
            for i in range(projects)
        ],
        principal=PRINCIPAL,
    )
    project_ids = [str(record["properties"]["id"]) for record in project_records]
    for project_id in project_ids:
        await writer.write_edge(
            EdgeWrite(type="CONTAINS", from_id=site_id, to_id=project_id, properties={}),
            principal=PRINCIPAL,
        )

    owners = await writer.write_nodes(
        [
            NodeWrite(
                type="User",
                properties={"upn": f"owner{i}@client.example", "display": f"Owner {i}",
                            "side": "source"},
            )
            for i in range(12)
        ],
        principal=PRINCIPAL,
    )
    owner_ids = [str(record["properties"]["id"]) for record in owners]

    # Written in batches: one round trip per workbook would make the seed, not the read,
    # the slow part of this test.
    workbook_ids: list[str] = []
    batch = 100
    for start in range(0, workbooks, batch):
        chunk = range(start, min(start + batch, workbooks))
        records = await writer.write_nodes(
            [
                NodeWrite(
                    type="Workbook",
                    properties={
                        "luid": f"{site}-wb-{i:05d}",
                        "name": f"Workbook {i:04d}",
                        "revision": "1",
                        "views_90d": (i * 7) % 900,
                        "distinct_viewers_90d": i % 40,
                        "parse_quality": 1.0 if i % 5 else 0.86,
                    },
                )
                for i in chunk
            ],
            principal=PRINCIPAL,
        )
        workbook_ids.extend(str(record["properties"]["id"]) for record in records)

    # A worksheet and a calculation per workbook. Not decoration: a workbook reaches its
    # calculated fields as Workbook → Worksheet → CalculatedField (§4.1.2 gives CONTAINS
    # no Workbook→CalculatedField pair), and a seed without the middle hop cannot tell a
    # correct count from one that returns zero for everything.
    sheets = await writer.write_nodes(
        [
            NodeWrite(
                type="Worksheet",
                properties={
                    "name": f"Sheet {i}",
                    "rows_shelf": ["Desk"],
                    "cols_shelf": ["Date"],
                    "marks_shelf": [],
                },
            )
            for i in range(workbooks)
        ],
        principal=PRINCIPAL,
    )
    sheet_ids = [str(record["properties"]["id"]) for record in sheets]

    calcs = await writer.write_nodes(
        [
            NodeWrite(
                type="CalculatedField",
                properties={
                    "name": f"Margin {i}",
                    "formula": "SUM([M]) / SUM([R])",
                    "formula_ast": {"op": "DIV"},
                },
            )
            for i in range(workbooks)
        ],
        principal=PRINCIPAL,
    )
    calc_ids = [str(record["properties"]["id"]) for record in calcs]

    for index, workbook_id in enumerate(workbook_ids):
        await writer.write_edge(
            EdgeWrite(
                type="CONTAINS",
                from_id=project_ids[index % projects],
                to_id=workbook_id,
                properties={},
            ),
            principal=PRINCIPAL,
        )
        await writer.write_edge(
            EdgeWrite(
                type="CONTAINS", from_id=workbook_id, to_id=sheet_ids[index], properties={}
            ),
            principal=PRINCIPAL,
        )
        await writer.write_edge(
            EdgeWrite(
                type="ENCODES",
                from_id=sheet_ids[index],
                to_id=calc_ids[index],
                properties={"shelf": "rows"},
            ),
            principal=PRINCIPAL,
        )
        # Every fourth workbook is unowned, so the "unowned only" facet has something to
        # find and the flag §15.3.2 asks for is exercised.
        if index % 4:
            await writer.write_edge(
                EdgeWrite(
                    type="OWNED_BY",
                    from_id=workbook_id,
                    to_id=owner_ids[index % len(owner_ids)],
                    properties={},
                ),
                principal=PRINCIPAL,
            )

    return {"site_id": site_id, "workbooks": workbook_ids, "projects": project_ids}


@pytest.fixture
async def small(settings: Settings):
    pool = await create_pool(settings)
    try:
        repository = AgeGraphRepository(pool, graph_name=settings.graph_name)
        writer = GraphWriter(repository, event_source=source_for(settings.graph_name))
        site = f"S{new_ulid()[10:16]}"
        seeded = await seed(writer, site=site, workbooks=24, projects=3)
        yield {
            "pool": pool,
            "settings": settings,
            "writer": writer,
            "reader": EstateReader(pool, graph_name=settings.graph_name),
            "scope": PostgresScopeStore(pool, graph_name=settings.graph_name),
            "site": site,
            **seeded,
        }
    finally:
        await pool.close()


async def test_the_reader_places_owns_and_counts_every_workbook(small) -> None:
    """The four queries, against the real index tables and AGE label tables."""
    estate = await small["reader"].read()
    mine = [row for row in estate.rows if row.site == small["site"]]

    assert len(mine) == 24
    assert all(row.project is not None for row in mine), "every workbook is placed"
    assert {row.project for row in mine} == {"Project 0", "Project 1", "Project 2"}
    assert sum(1 for row in mine if row.owner is None) == 6, "every fourth is unowned"
    assert all(row.parse_quality is not None for row in mine)
    assert sum(1 for row in mine if row.held) == 5, "one in five is below the threshold"
    # Two hops: a workbook has no direct edge to a calculated field, and a one-hop count
    # silently reports nought for every estate. Found by looking at the screen.
    assert all(row.calculated_fields == 1 for row in mine)


async def test_the_tree_rolls_up_from_the_real_estate(small) -> None:
    estate = await small["reader"].read()
    site = next(
        node for node in estate.tree(EstateFilter()) if node.name == small["site"]
    )

    assert site.workbooks == 24
    assert sum(child.workbooks for child in site.children) == 24
    assert site.held == 5
    assert site.views_90d == sum(
        row.views_90d or 0 for row in estate.rows if row.site == small["site"]
    )


async def test_scope_decisions_reach_the_rows(small) -> None:
    """A tier declared by a programme manager shows on the workbook it was declared for."""
    target = small["workbooks"][0]
    await small["scope"].decide(
        new_decision(
            workbook_id=target,
            kind=DecisionKind.RE_TIER,
            reason="Joint review found three nested LOD expressions",
            decided_by="user:pm@artizent.example",
            to_value="COMPLEX",
        )
    )
    await small["scope"].decide(
        new_decision(
            workbook_id=small["workbooks"][1],
            kind=DecisionKind.WITHDRAW,
            reason="Superseded by the Treasury liquidity pack",
            decided_by="user:pm@artizent.example",
        )
    )

    estate = await small["reader"].read(scope=await small["scope"].states())
    rows = {row.id: row for row in estate.rows}

    assert rows[target].tier == "COMPLEX"
    assert rows[small["workbooks"][1]].withdrawn is True
    assert "Treasury" in (rows[small["workbooks"][1]].withdrawn_reason or "")

    # And the withdrawn one is out of the default page but reachable through the facet.
    default = estate.page(EstateFilter(site=small["site"]))
    assert small["workbooks"][1] not in {w["id"] for w in default["workbooks"]}
    including = estate.page(EstateFilter(site=small["site"], include_withdrawn=True))
    assert small["workbooks"][1] in {w["id"] for w in including["workbooks"]}


async def test_a_retired_owner_leaves_the_workbook_unowned_rather_than_broken(small) -> None:
    """Retirement is exclusion, not deletion — the read has to honour it."""
    estate = await small["reader"].read()
    owned = next(row for row in estate.rows if row.site == small["site"] and row.owner_id)

    await small["writer"].retire_node(
        owned.owner_id,
        reason="Left the organisation; ownership to be reassigned",
        principal=Principal("user:pm@artizent.example"),
    )

    after = await small["reader"].read()
    row = next(r for r in after.rows if r.id == owned.id)
    assert row.owner is None


@pytest.mark.slow
async def test_a_1067_workbook_site_reads_inside_the_budget(settings) -> None:
    """S1.4.1's fourth criterion, measured.

    The console makes one request for all three panes, so this measures the whole of the
    server's share of the budget: four queries, the filtering, the banding, the facet
    counts and the tree. Reported whether it passes or fails, because a benchmark that
    only prints on failure is one nobody knows the value of.
    """
    pool = await create_pool(settings)
    try:
        repository = AgeGraphRepository(pool, graph_name=settings.graph_name)
        writer = GraphWriter(repository, event_source=source_for(settings.graph_name))
        site = f"BIG{new_ulid()[10:16]}"

        seeded_at = time.perf_counter()
        await seed(writer, site=site, workbooks=TARGET_WORKBOOKS, projects=24)
        seed_seconds = time.perf_counter() - seeded_at

        reader = EstateReader(pool, graph_name=settings.graph_name)
        scope = PostgresScopeStore(pool, graph_name=settings.graph_name)

        # Warm once: the first read of a fresh estate pays for connection setup and
        # PostgreSQL's own first-touch costs, which a user's second click never does.
        await reader.read(scope=await scope.states())

        samples: list[float] = []
        for _ in range(5):
            started = time.perf_counter()
            states = await scope.states()
            estate = await reader.read(scope=states)
            estate.page(EstateFilter(site=site), limit=100)
            estate.facets(EstateFilter(site=site))
            samples.append((time.perf_counter() - started) * 1000)

        samples.sort()
        median = samples[len(samples) // 2]
        worst = samples[-1]
        rows = [row for row in estate.rows if row.site == site]

        print(
            f"\nEstate Explorer over {len(rows)} workbooks (S1.4.1 budget {LOAD_BUDGET_MS} ms):"
            f"\n  median {median:.0f} ms, worst {worst:.0f} ms of {len(samples)} reads"
            f"\n  {len(estate.rows)} workbooks in the graph, seeded in {seed_seconds:.0f} s"
        )

        assert len(rows) == TARGET_WORKBOOKS
        assert worst < LOAD_BUDGET_MS, (
            f"the estate read took {worst:.0f} ms against a {LOAD_BUDGET_MS} ms budget"
        )
    finally:
        await pool.close()
