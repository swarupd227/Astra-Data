"""Adoption tracking during parallel run, against real PostgreSQL + Apache AGE, a real
local Git repository and the real fixture source/target adapters -- story S9.2.2,
continuing F9.2.

What only the real stack can answer: that a real weekly sweep really reads both a real
source-side views count (via the real, capability-gated `SourceAdapter.usage()`
contract) and a real target-side views count (via the new `TargetAdapter.usage()`
method) for every currently released (prod-promoted) workbook and none other; that the
ratio and threshold-met flag are computed honestly, including the honest `None` when
the source adapter's own usage capability is absent; that the configured threshold at
capture time is frozen on the row, not recomputed later; that `is_capture_due` reads a
real, graph-wide watermark; and that the Decommission Tracker really groups real sites
with real per-MU snapshots from nothing but `promotion_run`/`adoption_snapshot` rows.
"""

from __future__ import annotations

import asyncio
import os
import threading
from collections.abc import Callable, Coroutine
from pathlib import Path
from typing import Any

import pytest

pytestmark = pytest.mark.integration

asyncpg = pytest.importorskip("asyncpg")

from astra_adapter import Capabilities  # noqa: E402
from astra_adapter.fake.source import (  # noqa: E402
    FixtureSite,
    FixtureSourceAdapter,
    FixtureWorkbook,
)
from astra_adapter.target_contract import TmdlBundle  # noqa: E402
from astra_adapter.target_fake import FixtureTargetAdapter  # noqa: E402

from astra_graph.adoption import (  # noqa: E402
    AdoptionConfig,
    PostgresAdoptionConfigStore,
    PostgresAdoptionStore,
    capture_adoption_sweep,
    decommission_tracker,
    is_capture_due,
)
from astra_graph.config import Settings  # noqa: E402
from astra_graph.events import source_for  # noqa: E402
from astra_graph.graph import AgeGraphRepository, create_pool  # noqa: E402
from astra_graph.graph.queries import accessor  # noqa: E402
from astra_graph.ids import new_ulid  # noqa: E402
from astra_graph.migrations import run as run_migrations  # noqa: E402
from astra_graph.ontology import EDGE_LABELS, NODE_LABELS  # noqa: E402
from astra_graph.principal import Principal  # noqa: E402
from astra_graph.tmdl import safe_name  # noqa: E402
from astra_graph.writes import EdgeWrite, GraphWriter, NodeWrite  # noqa: E402

PRINCIPAL = Principal("agent:steward", run_id="run-steward")
PM = Principal("user:pm@artizent.example")

_PROD_WORKSPACE = "prod"


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


@pytest.fixture
def settings() -> Settings:
    config = _settings(f"astra_adoption_{new_ulid()[10:22].lower()}")

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
                "public.estate_edge_index", "public.estate_element_index", "public.estate_event",
                "public.promotion_run", "public.adoption_config", "public.adoption_snapshot",
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
    created = await writer.write_nodes([NodeWrite(type=type_, properties=properties)], principal=PRINCIPAL)
    return str(created[0]["properties"]["id"])


async def _edge(writer: GraphWriter, type_: str, source: str, target: str, **props: Any) -> None:
    await writer.write_edge(EdgeWrite(type=type_, from_id=source, to_id=target, properties=props), principal=PRINCIPAL)


@pytest.fixture
async def estate(settings: Settings, tmp_path: Path):
    pool = await create_pool(settings)
    try:
        repository = AgeGraphRepository(pool, graph_name=settings.graph_name)
        writer = GraphWriter(repository, event_source=source_for(settings.graph_name))
        adoption_store = PostgresAdoptionStore(pool, graph_name=settings.graph_name)
        config_store = PostgresAdoptionConfigStore(pool, graph_name=settings.graph_name)
        target_adapter = FixtureTargetAdapter(repo_path=tmp_path / "repo")

        suffix = new_ulid()[10:18].lower()
        site = await _write(writer, "Site", luid=f"s-{suffix}", name=f"RQA {suffix}")
        project = await _write(writer, "Project", luid=f"p-{suffix}", name="Risk Core")
        await _edge(writer, "CONTAINS", site, project)

        yield {
            "pool": pool, "settings": settings, "writer": writer,
            "adoption_store": adoption_store, "config_store": config_store,
            "target_adapter": target_adapter, "site": site, "project": project,
        }
    finally:
        await pool.close()


async def _released_workbook(estate: dict[str, Any], *, name: str, luid: str) -> str:
    """A real `Workbook` node, promoted to prod (a real `promotion_run` row, inserted
    directly -- S9.2.1's own suite already proves the promotion mechanism itself), with
    its own report item actually committed and deployed to prod so the target adapter's
    own `usage()` finds something real to answer about."""
    writer = estate["writer"]
    book = await _write(writer, "Workbook", luid=luid, name=name, revision="1")
    await _edge(writer, "CONTAINS", estate["project"], book)

    async with estate["pool"].acquire() as conn:
        await conn.execute(
            """INSERT INTO public.promotion_run
                (id, graph, workbook_id, to_stage, workspace, state, steps,
                 model_git_ref, report_deploy_id, approved_by, approver_role,
                 rationale, triggered_by, started_at, finished_at)
               VALUES ($1, $2, $3, 'prod', $4, 'SUCCEEDED', '[]'::jsonb,
                       'refs/heads/master', NULL, $5, 'programme_manager', NULL, $5, now(), now())""",
            f"promotion_{new_ulid()}", estate["settings"].graph_name, book, _PROD_WORKSPACE, PM.value,
        )

    item_path = f"{safe_name(name)}.Report"
    commit = await estate["target_adapter"].commit(
        TmdlBundle(files={"report.json": b"{}"}), item_path=item_path, message=f"Deploy {name}",
    )
    await estate["target_adapter"].deploy(workspace=_PROD_WORKSPACE, git_ref=commit.ref)
    return book


def _fixture_source_adapter(*workbooks: FixtureWorkbook, usage: bool = True) -> FixtureSourceAdapter:
    return FixtureSourceAdapter(
        [FixtureSite(name="rqa", workbooks=list(workbooks), projects=["Risk Core"])],
        capabilities=Capabilities(live_query=False, extract_read=True, usage=usage, ownership=False, screenshot=False),
    )


# --------------------------------------------------------------------------- the sweep


async def test_capture_records_a_real_ratio_for_a_released_workbook(estate) -> None:
    book = await _released_workbook(estate, name="Daily VaR", luid="wb-daily-var")
    source_adapter = _fixture_source_adapter(
        FixtureWorkbook(name="Daily VaR", luid="wb-daily-var", project="Risk Core", views_90d=100),
    )

    snapshots = await capture_adoption_sweep(
        estate["pool"], estate["settings"].graph_name, estate["writer"],
        source_adapter, estate["target_adapter"], estate["adoption_store"], estate["config_store"],
        target_workspace=_PROD_WORKSPACE, principal=PRINCIPAL,
    )

    assert len(snapshots) == 1
    snapshot = snapshots[0]
    assert snapshot.workbook_id == book
    assert snapshot.source_views == 100
    assert snapshot.target_views >= 0
    assert snapshot.ratio == pytest.approx(snapshot.target_views / 100)
    assert snapshot.threshold == pytest.approx(0.8)


async def test_capture_never_touches_a_workbook_that_was_never_released(estate) -> None:
    await _write(estate["writer"], "Workbook", luid="wb-unreleased", name="Unreleased", revision="1")
    source_adapter = _fixture_source_adapter(
        FixtureWorkbook(name="Unreleased", luid="wb-unreleased", project="Risk Core", views_90d=50),
    )

    snapshots = await capture_adoption_sweep(
        estate["pool"], estate["settings"].graph_name, estate["writer"],
        source_adapter, estate["target_adapter"], estate["adoption_store"], estate["config_store"],
        target_workspace=_PROD_WORKSPACE, principal=PRINCIPAL,
    )

    assert snapshots == []


async def test_capture_is_honest_with_no_source_usage_capability(estate) -> None:
    await _released_workbook(estate, name="Daily VaR", luid="wb-daily-var")
    source_adapter = _fixture_source_adapter(
        FixtureWorkbook(name="Daily VaR", luid="wb-daily-var", project="Risk Core", views_90d=100),
        usage=False,
    )

    snapshots = await capture_adoption_sweep(
        estate["pool"], estate["settings"].graph_name, estate["writer"],
        source_adapter, estate["target_adapter"], estate["adoption_store"], estate["config_store"],
        target_workspace=_PROD_WORKSPACE, principal=PRINCIPAL,
    )

    assert len(snapshots) == 1
    assert snapshots[0].source_views is None
    assert snapshots[0].ratio is None
    assert snapshots[0].meets_threshold is None
    assert snapshots[0].target_views >= 0


async def test_capture_uses_the_configured_threshold_frozen_on_the_row(estate) -> None:
    await estate["config_store"].save(AdoptionConfig(threshold=0.5), updated_by=PM.value)
    await _released_workbook(estate, name="Daily VaR", luid="wb-daily-var")
    source_adapter = _fixture_source_adapter(
        FixtureWorkbook(name="Daily VaR", luid="wb-daily-var", project="Risk Core", views_90d=1),
    )

    snapshots = await capture_adoption_sweep(
        estate["pool"], estate["settings"].graph_name, estate["writer"],
        source_adapter, estate["target_adapter"], estate["adoption_store"], estate["config_store"],
        target_workspace=_PROD_WORKSPACE, principal=PRINCIPAL,
    )

    assert snapshots[0].threshold == pytest.approx(0.5)

    # Changing the config afterward must never retroactively change this row's own facts.
    await estate["config_store"].save(AdoptionConfig(threshold=0.99), updated_by=PM.value)
    stored = await estate["adoption_store"].latest_for_workbook(snapshots[0].workbook_id)
    assert stored is not None
    assert stored.threshold == pytest.approx(0.5)


async def test_is_capture_due_honestly_true_with_nothing_captured_yet(estate) -> None:
    assert await is_capture_due(estate["adoption_store"]) is True


async def test_is_capture_due_is_false_right_after_a_real_capture(estate) -> None:
    await _released_workbook(estate, name="Daily VaR", luid="wb-daily-var")
    source_adapter = _fixture_source_adapter(
        FixtureWorkbook(name="Daily VaR", luid="wb-daily-var", project="Risk Core", views_90d=10),
    )
    await capture_adoption_sweep(
        estate["pool"], estate["settings"].graph_name, estate["writer"],
        source_adapter, estate["target_adapter"], estate["adoption_store"], estate["config_store"],
        target_workspace=_PROD_WORKSPACE, principal=PRINCIPAL,
    )

    assert await is_capture_due(estate["adoption_store"]) is False


# -------------------------------------------------------------------- Decommission Tracker


async def test_decommission_tracker_groups_by_site_with_a_real_snapshot(estate) -> None:
    await _released_workbook(estate, name="Daily VaR", luid="wb-daily-var")
    source_adapter = _fixture_source_adapter(
        FixtureWorkbook(name="Daily VaR", luid="wb-daily-var", project="Risk Core", views_90d=100),
    )
    await capture_adoption_sweep(
        estate["pool"], estate["settings"].graph_name, estate["writer"],
        source_adapter, estate["target_adapter"], estate["adoption_store"], estate["config_store"],
        target_workspace=_PROD_WORKSPACE, principal=PRINCIPAL,
    )

    tracker = await decommission_tracker(
        estate["pool"], estate["settings"].graph_name, estate["adoption_store"], estate["config_store"],
    )

    assert tracker["threshold"] == pytest.approx(0.8)
    site_row = next(row for row in tracker["sites"] if row["site_id"] == estate["site"])
    assert site_row["released_mu_count"] == 1
    mu_row = site_row["mus"][0]
    assert mu_row["name"] == "Daily VaR"
    assert mu_row["snapshot"]["source_views"] == 100


async def test_decommission_tracker_is_honest_with_nothing_released_yet(estate) -> None:
    tracker = await decommission_tracker(
        estate["pool"], estate["settings"].graph_name, estate["adoption_store"], estate["config_store"],
    )
    assert tracker["sites"] == []
