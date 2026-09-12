"""Promotion through the Fabric deployment pipeline, and the Release Board, against real
PostgreSQL + Apache AGE and a real local Git repository -- story S9.2.1, opening F9.2.

What only the real stack can answer: that MA-08 really refuses a workbook that has not
been G3-approved, or has no composed report, or no successful model build; that a real
promotion really commits and deploys the report to the test workspace and redeploys the
family's own latest successful build's real git ref to that same workspace; that MA-09
really refuses a workbook that has not first reached test; that a real prod promotion
really opens a real, computed parallel-run window; that re-promoting an already-promoted
stage overwrites its own single current row rather than growing a second one; and that
the Release Board really groups real trains, real pipeline stages, real blockers and a
real per-site window from nothing but `promotion_run`/`GateDecision`/`IN_TRAIN` rows.
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

from astra_adapter.target_fake import FixtureTargetAdapter  # noqa: E402

from astra_graph.build import BuildRecord, BuildStep, PostgresBuildStore  # noqa: E402
from astra_graph.compositor import Compositor, compose_report  # noqa: E402
from astra_graph.config import Settings  # noqa: E402
from astra_graph.errors import InvalidRequestError  # noqa: E402
from astra_graph.events import source_for  # noqa: E402
from astra_graph.graph import AgeGraphRepository, create_pool  # noqa: E402
from astra_graph.graph.queries import accessor  # noqa: E402
from astra_graph.ids import new_ulid  # noqa: E402
from astra_graph.migrations import run as run_migrations  # noqa: E402
from astra_graph.modeller import Modeller  # noqa: E402
from astra_graph.ontology import EDGE_LABELS, NODE_LABELS  # noqa: E402
from astra_graph.principal import Principal  # noqa: E402
from astra_graph.provenance import PostgresProvenanceStore  # noqa: E402
from astra_graph.release import (  # noqa: E402
    PostgresPromotionStore,
    promote_workbook,
    promotion_blockers,
    release_board,
)
from astra_graph.report_deploy import PostgresReportDeployStore  # noqa: E402
from astra_graph.visual_mapping import DEFAULT_MAPPINGS, VisualMappingRuleset  # noqa: E402
from astra_graph.writes import EdgeWrite, GraphWriter, NodeWrite  # noqa: E402

PRINCIPAL = Principal("agent:modeller", run_id="run-modeller")
ENGINEER = Principal("user:platform.engineer@artizent.example")
PM = Principal("user:pm@artizent.example")

_TEST_WORKSPACE = "test"
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
    """A single raw `asyncpg.connect()`, not a `Pool` -- see `test_integration_exception_
    ageing.py`'s own fixture docstring for why (S8.3.2's own real, fixed hang)."""
    config = _settings(f"astra_release_{new_ulid()[10:22].lower()}")

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
                "public.provenance", "public.report_deploy_run", "public.build_run", "public.promotion_run",
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


def _ruleset() -> VisualMappingRuleset:
    return VisualMappingRuleset(version=0, rules=DEFAULT_MAPPINGS, updated_by="system", updated_at=None)


@pytest.fixture
async def estate(settings: Settings, tmp_path: Path):
    """The full, real collaborator set `release.py` needs, plus one bare `Site ->
    Project` pair every test's own workbook(s) are created under."""
    pool = await create_pool(settings)
    try:
        repository = AgeGraphRepository(pool, graph_name=settings.graph_name)
        writer = GraphWriter(repository, event_source=source_for(settings.graph_name))
        provenance_store = PostgresProvenanceStore(pool, graph_name=settings.graph_name)
        modeller = Modeller(pool, graph_name=settings.graph_name, writer=writer, provenance_store=provenance_store)
        compositor = Compositor(pool, graph_name=settings.graph_name, writer=writer)
        report_deploy_store = PostgresReportDeployStore(pool, graph_name=settings.graph_name)
        build_store = PostgresBuildStore(pool, graph_name=settings.graph_name)
        promotion_store = PostgresPromotionStore(pool, graph_name=settings.graph_name)
        target_adapter = FixtureTargetAdapter(repo_path=tmp_path / "repo")

        suffix = new_ulid()[10:18].lower()
        site = await _write(writer, "Site", luid=f"s-{suffix}", name=f"RQA {suffix}")
        project = await _write(writer, "Project", luid=f"p-{suffix}", name="Risk Core")
        await _edge(writer, "CONTAINS", site, project)

        yield {
            "pool": pool, "settings": settings, "writer": writer, "modeller": modeller,
            "compositor": compositor, "report_deploy_store": report_deploy_store,
            "build_store": build_store, "promotion_store": promotion_store,
            "target_adapter": target_adapter, "site": site, "project": project,
        }
    finally:
        await pool.close()


async def _make_workbook(estate: dict[str, Any], *, name: str = "Daily VaR") -> str:
    writer = estate["writer"]
    suffix = new_ulid()[10:18].lower()
    table = await _write(writer, "Table", name="positions", schema="risk", row_estimate=1000)
    connection = await _write(writer, "Connection", **{"class": "snowflake"}, server="warehouse", db="risk")
    await _edge(writer, "CONNECTS_TO", connection, table)
    book = await _write(writer, "Workbook", luid=f"wb-{suffix}", name=name, revision="1")
    await _edge(writer, "CONTAINS", estate["project"], book)
    datasource = await _write(
        writer, "Datasource", name="VaR ds", type="published", luid=f"ds-{suffix}",
        extract_flag=True, refresh_schedule="daily",
    )
    await _edge(writer, "CONNECTS_TO", datasource, connection)
    desk = await _write(writer, "Field", name="Desk", datatype="string", role="dimension")
    await _edge(writer, "HAS_FIELD", datasource, desk)
    sheet = await _write(
        writer, "Worksheet", name="Bar sheet", mark_type="bar", rows_shelf=["Desk"], cols_shelf=[], marks_shelf=[],
    )
    await _edge(writer, "CONTAINS", book, sheet)
    await _edge(writer, "USES_DATASOURCE", sheet, datasource)
    return book


async def _cluster_and_build_family(estate: dict[str, Any], workbook_id: str) -> tuple[str, str]:
    """A real `ModelFamily`, a real `SemanticModel` marked BUILT, and a real, SUCCEEDED
    `BuildRecord` for it -- everything `promotion_blockers`/`promote_workbook` check on
    the model side, seeded directly rather than running the whole build pipeline (S4.3.1/
    S4.3.2's own suites already prove that pipeline for real)."""
    writer = estate["writer"]
    suffix = new_ulid()[10:18].lower()
    family = await _write(writer, "ModelFamily", name=f"Risk Positions {suffix}", state="PROPOSED", grain="Desk", conformed_dims=[])
    await _edge(writer, "IN_FAMILY", workbook_id, family, confidence=1.0)
    proposal = await estate["modeller"].run(family, principal=PRINCIPAL)
    await writer.set_node_properties(proposal.semantic_model_id, {"state": "BUILT"}, principal=PRINCIPAL)

    await estate["build_store"].record(BuildRecord(
        id=f"build_{new_ulid()}", family_id=family, version="v1", gate_decision_id=None, state="SUCCEEDED",
        steps=(BuildStep("commit", True, "abc123 on refs/heads/main"),), git_commit_sha="abc123",
        git_ref="refs/heads/main", workspace="dev", triggered_by=PRINCIPAL.value,
        started_at="2027-06-01T09:00:00.000Z", finished_at="2027-06-01T09:00:01.000Z",
    ))
    return family, proposal.semantic_model_id


async def _compose(estate: dict[str, Any], workbook_id: str) -> None:
    await compose_report(
        estate["pool"], estate["settings"].graph_name, estate["writer"],
        workbook_id=workbook_id, ruleset=_ruleset(), principal=ENGINEER,
    )


async def _approve_g3(estate: dict[str, Any], workbook_id: str) -> None:
    await _write(
        estate["writer"], "GateDecision", gate="G3", subject_ref=workbook_id, decision="APPROVED",
        approver="user:owner@client.example", timestamp="2027-06-01T09:00:00.000Z",
    )


async def _ready_workbook(estate: dict[str, Any], *, name: str = "Daily VaR") -> str:
    """A workbook that has cleared every MA-08 precondition: G3 approved, report
    composed, model BUILT, and a real successful build to redeploy."""
    workbook_id = await _make_workbook(estate, name=name)
    await _cluster_and_build_family(estate, workbook_id)
    await _compose(estate, workbook_id)
    await _approve_g3(estate, workbook_id)
    return workbook_id


def _promote_to_test(estate: dict[str, Any], workbook_id: str):
    return promote_workbook(
        estate["pool"], estate["settings"].graph_name, estate["writer"], estate["target_adapter"],
        estate["report_deploy_store"], estate["build_store"], estate["promotion_store"],
        workbook_id=workbook_id, to_stage="test", workspace=_TEST_WORKSPACE,
        principal=ENGINEER, approver_role="platform_engineer",
    )


def _promote_to_prod(estate: dict[str, Any], workbook_id: str, *, rationale: str = "Ready for release, client has signed off."):
    return promote_workbook(
        estate["pool"], estate["settings"].graph_name, estate["writer"], estate["target_adapter"],
        estate["report_deploy_store"], estate["build_store"], estate["promotion_store"],
        workbook_id=workbook_id, to_stage="prod", workspace=_PROD_WORKSPACE,
        principal=PM, approver_role="programme_manager", rationale=rationale,
    )


# --------------------------------------------------------------------------- MA-08: test


async def test_ma08_promotes_a_real_accepted_workbook_to_test(estate) -> None:
    workbook_id = await _ready_workbook(estate)
    record = await _promote_to_test(estate, workbook_id)
    assert record.state == "SUCCEEDED"
    assert record.workspace == _TEST_WORKSPACE
    assert record.model_git_ref == "refs/heads/main"
    assert record.report_deploy_id
    assert record.approved_by == ENGINEER.value
    assert record.approver_role == "platform_engineer"
    assert record.rationale is None


async def test_ma08_refuses_a_workbook_that_is_not_g3_approved(estate) -> None:
    workbook_id = await _make_workbook(estate)
    await _cluster_and_build_family(estate, workbook_id)
    await _compose(estate, workbook_id)
    with pytest.raises(InvalidRequestError, match="G3 has not been approved"):
        await _promote_to_test(estate, workbook_id)


async def test_ma08_refuses_a_workbook_with_no_composed_report(estate) -> None:
    workbook_id = await _make_workbook(estate)
    await _cluster_and_build_family(estate, workbook_id)
    await _approve_g3(estate, workbook_id)
    with pytest.raises(InvalidRequestError, match="no report has been composed"):
        await _promote_to_test(estate, workbook_id)


async def test_ma08_refuses_a_workbook_with_no_successful_build(estate) -> None:
    workbook_id = await _make_workbook(estate)
    writer = estate["writer"]
    family = await _write(writer, "ModelFamily", name="Unbuilt Family", state="PROPOSED", grain="Desk", conformed_dims=[])
    await _edge(writer, "IN_FAMILY", workbook_id, family, confidence=1.0)
    await estate["modeller"].run(family, principal=PRINCIPAL)
    await _compose(estate, workbook_id)
    await _approve_g3(estate, workbook_id)
    with pytest.raises(InvalidRequestError, match="no successful build"):
        await _promote_to_test(estate, workbook_id)


async def test_promoting_to_test_twice_overwrites_the_one_real_row(estate) -> None:
    workbook_id = await _ready_workbook(estate)
    first = await _promote_to_test(estate, workbook_id)
    second = await _promote_to_test(estate, workbook_id)
    assert first.id != second.id
    latest = await estate["promotion_store"].latest(workbook_id, "test")
    assert latest is not None
    assert latest.id == second.id


# -------------------------------------------------------------------------- MA-09: prod


async def test_ma09_promotes_a_tested_workbook_to_prod_with_explicit_pm_approval(estate) -> None:
    workbook_id = await _ready_workbook(estate)
    await _promote_to_test(estate, workbook_id)
    record = await _promote_to_prod(estate, workbook_id, rationale="Client sign-off received; releasing now.")
    assert record.state == "SUCCEEDED"
    assert record.workspace == _PROD_WORKSPACE
    assert record.approved_by == PM.value
    assert record.approver_role == "programme_manager"
    assert record.rationale == "Client sign-off received; releasing now."


async def test_ma09_refuses_a_workbook_that_has_not_reached_test(estate) -> None:
    workbook_id = await _ready_workbook(estate)
    with pytest.raises(InvalidRequestError, match="has not been promoted to test"):
        await _promote_to_prod(estate, workbook_id)


async def test_ma09_refuses_a_blank_rationale(estate) -> None:
    workbook_id = await _ready_workbook(estate)
    await _promote_to_test(estate, workbook_id)
    with pytest.raises(InvalidRequestError, match="at least"):
        await _promote_to_prod(estate, workbook_id, rationale="too short")


# ---------------------------------------------------------------------- promotion_blockers


async def test_promotion_blockers_lists_every_real_reason(estate) -> None:
    workbook_id = await _make_workbook(estate)
    blockers = await promotion_blockers(
        estate["pool"], estate["settings"].graph_name, estate["build_store"], estate["promotion_store"],
        workbook_id=workbook_id, to_stage="test",
    )
    assert any("G3 has not been approved" in b for b in blockers)
    assert any("no report has been composed" in b for b in blockers)
    assert any("no model family has been assigned" in b for b in blockers)


async def test_promotion_blockers_is_honestly_empty_once_every_precondition_is_real(estate) -> None:
    workbook_id = await _ready_workbook(estate)
    blockers = await promotion_blockers(
        estate["pool"], estate["settings"].graph_name, estate["build_store"], estate["promotion_store"],
        workbook_id=workbook_id, to_stage="test",
    )
    assert blockers == []


# -------------------------------------------------------------------------- Release Board


async def _put_in_train(estate: dict[str, Any], workbook_id: str, *, train_id: str, sequence: int) -> None:
    await _edge(estate["writer"], "IN_TRAIN", workbook_id, train_id, sequence=sequence, state="CLUSTERED")


async def test_release_board_groups_real_trains_with_real_pipeline_stages(estate) -> None:
    writer = estate["writer"]
    train = await _write(writer, "ReleaseTrain", name="Train 1", planned_start="2027-01-01", planned_end="2027-01-31")

    accepted_only = await _ready_workbook(estate, name="Accepted Only")
    await _put_in_train(estate, accepted_only, train_id=train, sequence=1)

    at_test = await _ready_workbook(estate, name="At Test")
    await _promote_to_test(estate, at_test)
    await _put_in_train(estate, at_test, train_id=train, sequence=2)

    board = await release_board(estate["pool"], estate["settings"].graph_name, estate["build_store"], estate["promotion_store"])
    assert len(board["trains"]) == 1
    mus = {mu["workbook_id"]: mu for mu in board["trains"][0]["mus"]}
    assert mus[accepted_only]["stage"] == "ACCEPTED"
    assert mus[accepted_only]["next_stage"] == "test"
    assert mus[accepted_only]["blockers"] == []
    assert mus[at_test]["stage"] == "TEST"
    assert mus[at_test]["next_stage"] == "prod"
    assert len(mus[at_test]["evidence"]) == 1


async def test_release_board_shows_real_blockers_for_a_not_yet_accepted_mu(estate) -> None:
    writer = estate["writer"]
    train = await _write(writer, "ReleaseTrain", name="Train 2", planned_start="2027-01-01", planned_end="2027-01-31")
    not_accepted = await _make_workbook(estate, name="Not Accepted")
    await _put_in_train(estate, not_accepted, train_id=train, sequence=1)

    board = await release_board(estate["pool"], estate["settings"].graph_name, estate["build_store"], estate["promotion_store"])
    mu = next(mu for mu in board["trains"][0]["mus"] if mu["workbook_id"] == not_accepted)
    assert mu["stage"] == "NOT_ACCEPTED"
    assert mu["next_stage"] == "test"
    assert any("G3 has not been approved" in b for b in mu["blockers"])


async def test_release_board_computes_a_real_per_site_parallel_run_window(estate) -> None:
    writer = estate["writer"]
    train = await _write(writer, "ReleaseTrain", name="Train 3", planned_start="2027-01-01", planned_end="2027-01-31")
    released = await _ready_workbook(estate, name="Released")
    await _promote_to_test(estate, released)
    await _promote_to_prod(estate, released)
    await _put_in_train(estate, released, train_id=train, sequence=1)

    board = await release_board(estate["pool"], estate["settings"].graph_name, estate["build_store"], estate["promotion_store"])
    site_row = next(s for s in board["sites"] if s["site_id"] == estate["site"])
    assert site_row["released_mu_count"] == 1
    assert site_row["parallel_run_start"] is not None
    assert site_row["parallel_run_end"] is not None
    assert site_row["parallel_run_end"] > site_row["parallel_run_start"]


async def test_release_board_is_honest_about_a_site_with_nothing_released_yet(estate) -> None:
    writer = estate["writer"]
    train = await _write(writer, "ReleaseTrain", name="Train 4", planned_start="2027-01-01", planned_end="2027-01-31")
    workbook_id = await _make_workbook(estate, name="Untouched")
    await _put_in_train(estate, workbook_id, train_id=train, sequence=1)

    board = await release_board(estate["pool"], estate["settings"].graph_name, estate["build_store"], estate["promotion_store"])
    site_row = next(s for s in board["sites"] if s["site_id"] == estate["site"])
    assert site_row["released_mu_count"] == 0
    assert site_row["parallel_run_start"] is None
    assert site_row["parallel_run_end"] is None
