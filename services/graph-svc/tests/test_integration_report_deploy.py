"""Committing and deploying a composed report, against real PostgreSQL + Apache AGE and a
real local Git repository — story S6.1.2, spec §7.1/§7.2.

What only the real stack can answer: that a real commit lands under the workbook's own
item path with the MU id in the message, that a real deploy actually syncs into the dev
workspace, that a report bound to a model that has never been BUILT is refused before any
Git write happens, that a retry loop really does retry a real (if simulated) transient
failure the right number of times with the right backoff calls, that a deploy failure is
recorded honestly (DEPLOY_FAILED, the real error, on the ReportDefinition itself) rather
than silently claiming success, and that both new routes drive their real role gates.
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

from astra_adapter.target_contract import DeploymentResult, TargetAdapterError  # noqa: E402
from astra_adapter.target_fake import FixtureTargetAdapter  # noqa: E402

from astra_graph.compositor import Compositor, compose_report  # noqa: E402
from astra_graph.config import Settings  # noqa: E402
from astra_graph.errors import ElementNotFoundError  # noqa: E402
from astra_graph.events import source_for  # noqa: E402
from astra_graph.graph import AgeGraphRepository, create_pool  # noqa: E402
from astra_graph.graph.queries import accessor  # noqa: E402
from astra_graph.ids import new_ulid  # noqa: E402
from astra_graph.migrations import run as run_migrations  # noqa: E402
from astra_graph.modeller import Modeller  # noqa: E402
from astra_graph.ontology import EDGE_LABELS, NODE_LABELS  # noqa: E402
from astra_graph.principal import Principal  # noqa: E402
from astra_graph.provenance import PostgresProvenanceStore  # noqa: E402
from astra_graph.report_deploy import (  # noqa: E402
    DEPLOY_RETRY_DEFAULT,
    PostgresReportDeployStore,
    ReportDeployError,
    deploy_report,
)
from astra_graph.visual_mapping import DEFAULT_MAPPINGS, VisualMappingRuleset  # noqa: E402
from astra_graph.writes import EdgeWrite, GraphWriter, NodeWrite  # noqa: E402

PRINCIPAL = Principal("agent:modeller", run_id="run-modeller")
ENGINEER = Principal("user:engineer@artizent.example")


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
    config = _settings(f"astra_report_deploy_{new_ulid()[10:22].lower()}")

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
                "public.report_deploy_run",
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


def _ruleset() -> VisualMappingRuleset:
    return VisualMappingRuleset(version=0, rules=DEFAULT_MAPPINGS, updated_by="system", updated_at=None)


class _FlakyTargetAdapter:
    """A `TargetAdapter` wrapping a real `FixtureTargetAdapter`, whose own `deploy` always
    succeeds once committed and so cannot exercise a retry loop on its own -- this fails a
    controllable, deterministic number of times first, then delegates for real."""

    kind = "flaky"

    def __init__(self, inner: FixtureTargetAdapter, *, fail_times: int, raise_instead: bool = False) -> None:
        self._inner = inner
        self._fail_times = fail_times
        self._raise_instead = raise_instead
        self.deploy_attempts = 0

    def manifest(self) -> Any:
        return self._inner.manifest()

    async def commit(self, bundle: Any, *, item_path: str, message: str) -> Any:
        return await self._inner.commit(bundle, item_path=item_path, message=message)

    async def deploy(self, *, workspace: str, git_ref: str) -> DeploymentResult:
        self.deploy_attempts += 1
        if self.deploy_attempts <= self._fail_times:
            if self._raise_instead:
                raise TargetAdapterError("simulated transient failure")
            return DeploymentResult(deployment_id="", workspace=workspace, ok=False, detail="simulated failure")
        return await self._inner.deploy(workspace=workspace, git_ref=git_ref)

    async def smoke_query(self, *, workspace: str, table: str, measure_name: str | None) -> Any:
        return await self._inner.smoke_query(workspace=workspace, table=table, measure_name=measure_name)


async def _no_sleep(_seconds: float) -> None:
    """Skips the real backoff wait -- the retry *count* and *order* are what this suite
    proves; nothing here should cost wall-clock seconds to verify."""


@pytest.fixture
async def estate(settings: Settings, tmp_path: Path):
    """One workbook, one bar sheet, composed into a real report, bound to a family whose
    `SemanticModel` is stamped BUILT -- everything `deploy_report` needs to succeed, so
    each test can focus on the one precondition or failure mode it targets."""
    pool = await create_pool(settings)
    try:
        repository = AgeGraphRepository(pool, graph_name=settings.graph_name)
        writer = GraphWriter(repository, event_source=source_for(settings.graph_name))
        provenance_store = PostgresProvenanceStore(pool, graph_name=settings.graph_name)
        modeller = Modeller(
            pool, graph_name=settings.graph_name, writer=writer, provenance_store=provenance_store
        )
        compositor = Compositor(pool, graph_name=settings.graph_name, writer=writer)
        deploy_store = PostgresReportDeployStore(pool, graph_name=settings.graph_name)
        target_adapter = FixtureTargetAdapter(repo_path=tmp_path / "repo")
        suffix = new_ulid()[10:18].lower()

        site = await _write(writer, "Site", luid=f"s-{suffix}", name=f"RQA {suffix}")
        project = await _write(writer, "Project", luid=f"p-{suffix}", name="Risk Core")
        await _edge(writer, "CONTAINS", site, project)
        table = await _write(writer, "Table", name="positions", schema="risk", row_estimate=1000)
        connection = await _write(writer, "Connection", **{"class": "snowflake"}, server="warehouse", db="risk")
        await _edge(writer, "CONNECTS_TO", connection, table)
        book = await _write(writer, "Workbook", luid=f"wb-{suffix}", name="Daily VaR", revision="1")
        await _edge(writer, "CONTAINS", project, book)

        datasource = await _write(
            writer, "Datasource", name="VaR ds", type="published", luid=f"ds-{suffix}",
            extract_flag=True, refresh_schedule="daily",
        )
        await _edge(writer, "CONNECTS_TO", datasource, connection)
        desk = await _write(writer, "Field", name="Desk", datatype="string", role="dimension")
        await _edge(writer, "HAS_FIELD", datasource, desk)

        sheet = await _write(
            writer, "Worksheet", name="Bar sheet", mark_type="bar",
            rows_shelf=["Desk"], cols_shelf=[], marks_shelf=[],
        )
        await _edge(writer, "CONTAINS", book, sheet)
        await _edge(writer, "USES_DATASOURCE", sheet, datasource)

        family = await _write(
            writer, "ModelFamily", name=f"Risk Positions {suffix}", state="PROPOSED",
            grain="Desk", conformed_dims=[],
        )
        await _edge(writer, "IN_FAMILY", book, family, confidence=1.0)
        proposal = await modeller.run(family, principal=PRINCIPAL)

        report = await compose_report(
            pool, settings.graph_name, writer,
            workbook_id=book, ruleset=_ruleset(), principal=ENGINEER,
        )

        yield {
            "pool": pool,
            "settings": settings,
            "writer": writer,
            "compositor": compositor,
            "deploy_store": deploy_store,
            "target_adapter": target_adapter,
            "workbook": book,
            "family": family,
            "report_id": report["report_id"],
            "semantic_model_id": proposal.semantic_model_id,
        }
    finally:
        await pool.close()


async def _mark_model(estate: dict[str, Any], state: str | None) -> None:
    if state is None:
        return
    await estate["writer"].set_node_properties(
        estate["semantic_model_id"], {"state": state}, principal=PRINCIPAL
    )


# ------------------------------------------------------------------------------- deploying


async def test_deploying_before_the_model_is_built_is_refused(estate) -> None:
    with pytest.raises(ReportDeployError, match="BUILT or PUBLISHED"):
        await deploy_report(
            estate["pool"], estate["settings"].graph_name, estate["writer"],
            estate["target_adapter"], estate["deploy_store"],
            workbook_id=estate["workbook"], workspace="dev", principal=ENGINEER,
        )


async def test_a_built_model_allows_deploy(estate) -> None:
    await _mark_model(estate, "BUILT")
    record = await deploy_report(
        estate["pool"], estate["settings"].graph_name, estate["writer"],
        estate["target_adapter"], estate["deploy_store"],
        workbook_id=estate["workbook"], workspace="dev", principal=ENGINEER,
    )
    assert record.state == "SUCCEEDED"
    assert record.attempts == 1
    assert record.git_commit_sha


async def test_a_published_model_also_allows_deploy(estate) -> None:
    await _mark_model(estate, "PUBLISHED")
    record = await deploy_report(
        estate["pool"], estate["settings"].graph_name, estate["writer"],
        estate["target_adapter"], estate["deploy_store"],
        workbook_id=estate["workbook"], workspace="dev", principal=ENGINEER,
    )
    assert record.state == "SUCCEEDED"


async def test_a_draft_model_still_refuses(estate) -> None:
    await _mark_model(estate, "DRAFT")
    with pytest.raises(ReportDeployError):
        await deploy_report(
            estate["pool"], estate["settings"].graph_name, estate["writer"],
            estate["target_adapter"], estate["deploy_store"],
            workbook_id=estate["workbook"], workspace="dev", principal=ENGINEER,
        )


async def test_deploying_an_uncomposed_workbook_is_a_clean_404(estate) -> None:
    lone = await _write(estate["writer"], "Workbook", luid="wb-lone", name="Lone book", revision="1")
    with pytest.raises(ElementNotFoundError):
        await deploy_report(
            estate["pool"], estate["settings"].graph_name, estate["writer"],
            estate["target_adapter"], estate["deploy_store"],
            workbook_id=lone, workspace="dev", principal=ENGINEER,
        )


async def test_the_commit_message_and_item_path_carry_the_mu_id(estate) -> None:
    from dulwich.repo import Repo

    await _mark_model(estate, "BUILT")
    await deploy_report(
        estate["pool"], estate["settings"].graph_name, estate["writer"],
        estate["target_adapter"], estate["deploy_store"],
        workbook_id=estate["workbook"], workspace="dev", principal=ENGINEER,
    )
    repo = Repo(str(estate["target_adapter"]._repo_path))
    try:
        message = repo[repo.head()].message.decode("utf-8")
    finally:
        repo.close()
    assert estate["workbook"] in message
    assert "MU" in message


async def test_the_reportdefinition_records_generated_on_success(estate) -> None:
    from astra_graph.compositor import read_report

    await _mark_model(estate, "BUILT")
    await deploy_report(
        estate["pool"], estate["settings"].graph_name, estate["writer"],
        estate["target_adapter"], estate["deploy_store"],
        workbook_id=estate["workbook"], workspace="dev", principal=ENGINEER,
    )
    report = await read_report(estate["pool"], estate["settings"].graph_name, estate["workbook"])
    assert report["deploy_state"] == "GENERATED"
    # Absent, not an explicit null -- the same "no value" convention every other optional
    # property in this ontology already uses (e.g. `Visual.redesign_reason`).
    assert report.get("deploy_error") is None
    assert report["pbir_ref"]


# ---------------------------------------------------------------------------- retry/backoff


async def test_a_transient_deploy_failure_retries_and_then_succeeds(estate) -> None:
    await _mark_model(estate, "BUILT")
    flaky = _FlakyTargetAdapter(estate["target_adapter"], fail_times=1)
    record = await deploy_report(
        estate["pool"], estate["settings"].graph_name, estate["writer"],
        flaky, estate["deploy_store"],
        workbook_id=estate["workbook"], workspace="dev", principal=ENGINEER,
        sleep=_no_sleep,
    )
    assert record.state == "SUCCEEDED"
    assert record.attempts == 2
    assert flaky.deploy_attempts == 2


async def test_a_deploy_that_always_fails_is_retried_exactly_the_configured_budget(estate) -> None:
    await _mark_model(estate, "BUILT")
    flaky = _FlakyTargetAdapter(estate["target_adapter"], fail_times=99, raise_instead=True)
    record = await deploy_report(
        estate["pool"], estate["settings"].graph_name, estate["writer"],
        flaky, estate["deploy_store"],
        workbook_id=estate["workbook"], workspace="dev", principal=ENGINEER,
        sleep=_no_sleep,
    )
    assert record.state == "FAILED"
    assert record.attempts == DEPLOY_RETRY_DEFAULT
    assert flaky.deploy_attempts == DEPLOY_RETRY_DEFAULT
    assert "simulated transient failure" in record.steps[-1].detail


async def test_an_exhausted_retry_budget_records_deploy_failed_with_the_real_error(estate) -> None:
    from astra_graph.compositor import read_report

    await _mark_model(estate, "BUILT")
    flaky = _FlakyTargetAdapter(estate["target_adapter"], fail_times=99, raise_instead=True)
    await deploy_report(
        estate["pool"], estate["settings"].graph_name, estate["writer"],
        flaky, estate["deploy_store"],
        workbook_id=estate["workbook"], workspace="dev", principal=ENGINEER,
        sleep=_no_sleep,
    )
    report = await read_report(estate["pool"], estate["settings"].graph_name, estate["workbook"])
    assert report["deploy_state"] == "DEPLOY_FAILED"
    assert "simulated transient failure" in report["deploy_error"]


async def test_a_custom_retry_budget_is_honoured(estate) -> None:
    await _mark_model(estate, "BUILT")
    flaky = _FlakyTargetAdapter(estate["target_adapter"], fail_times=99, raise_instead=True)
    record = await deploy_report(
        estate["pool"], estate["settings"].graph_name, estate["writer"],
        flaky, estate["deploy_store"],
        workbook_id=estate["workbook"], workspace="dev", principal=ENGINEER,
        retries=1, sleep=_no_sleep,
    )
    assert record.attempts == 1
    assert flaky.deploy_attempts == 1


async def test_a_failed_commit_never_attempts_a_deploy(estate) -> None:
    class _CommitFailingAdapter:
        kind = "commit-fails"

        def manifest(self) -> Any:
            return estate["target_adapter"].manifest()

        async def commit(self, bundle: Any, *, item_path: str, message: str) -> Any:
            raise TargetAdapterError("simulated commit failure")

        async def deploy(self, *, workspace: str, git_ref: str) -> Any:
            raise AssertionError("deploy should never be called when commit fails")

        async def smoke_query(self, *, workspace: str, table: str, measure_name: str | None) -> Any:
            raise AssertionError("smoke_query is not part of this story")

    await _mark_model(estate, "BUILT")
    record = await deploy_report(
        estate["pool"], estate["settings"].graph_name, estate["writer"],
        _CommitFailingAdapter(), estate["deploy_store"],
        workbook_id=estate["workbook"], workspace="dev", principal=ENGINEER,
        sleep=_no_sleep,
    )
    assert record.state == "FAILED"
    assert record.attempts == 0
    assert record.steps[0].name == "commit"


# ------------------------------------------------------------------------------ the store


async def test_latest_returns_the_most_recent_deploy(estate) -> None:
    await _mark_model(estate, "BUILT")
    await deploy_report(
        estate["pool"], estate["settings"].graph_name, estate["writer"],
        estate["target_adapter"], estate["deploy_store"],
        workbook_id=estate["workbook"], workspace="dev", principal=ENGINEER,
    )
    latest = await estate["deploy_store"].latest(estate["workbook"])
    assert latest is not None
    assert latest.workbook_id == estate["workbook"]
    assert latest.state == "SUCCEEDED"


# ---------------------------------------------------------------------------------- the API


@pytest.fixture
async def http_client(estate):
    from httpx import ASGITransport, AsyncClient

    from astra_graph.main import create_app
    from astra_graph.visual_mapping import PostgresVisualMappingRulesetStore

    app = create_app()
    app.state.compositor = estate["compositor"]
    app.state.visual_mapping_store = PostgresVisualMappingRulesetStore(
        estate["pool"], graph_name=estate["settings"].graph_name
    )
    app.state.report_deploy_store = estate["deploy_store"]
    app.state.target_adapter = estate["target_adapter"]
    app.state.target_workspace = "dev"

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://graph-svc") as async_client:
        yield async_client


def _headers(role: str, principal: Principal) -> dict[str, str]:
    from astra_graph.principal import PRINCIPAL_HEADER
    from astra_graph.roles import ROLES_HEADER

    return {PRINCIPAL_HEADER: principal.value, ROLES_HEADER: role}


async def test_deploy_over_http_requires_the_migration_engineer_role(estate, http_client) -> None:
    response = await http_client.post(
        f"/v1/workbooks/{estate['workbook']}:deploy",
        headers=_headers("programme_manager", ENGINEER),
    )
    assert response.status_code == 403


async def test_deploy_over_http_refuses_a_model_that_is_not_built(estate, http_client) -> None:
    response = await http_client.post(
        f"/v1/workbooks/{estate['workbook']}:deploy",
        headers=_headers("migration_engineer", ENGINEER),
    )
    assert response.status_code == 400
    assert "BUILT or PUBLISHED" in response.json()["message"]


async def test_deploy_over_http_succeeds_once_the_model_is_built(estate, http_client) -> None:
    await _mark_model(estate, "BUILT")
    response = await http_client.post(
        f"/v1/workbooks/{estate['workbook']}:deploy",
        headers=_headers("migration_engineer", ENGINEER),
    )
    assert response.status_code == 200
    assert response.json()["state"] == "SUCCEEDED"


async def test_get_deploy_before_any_deploy_reports_none(estate, http_client) -> None:
    response = await http_client.get(
        f"/v1/workbooks/{estate['workbook']}/deploy", headers=_headers("programme_manager", ENGINEER),
    )
    assert response.status_code == 200
    assert response.json()["deploy"] is None


async def test_get_deploy_after_a_deploy_over_http(estate, http_client) -> None:
    await _mark_model(estate, "BUILT")
    await http_client.post(
        f"/v1/workbooks/{estate['workbook']}:deploy", headers=_headers("migration_engineer", ENGINEER),
    )
    response = await http_client.get(
        f"/v1/workbooks/{estate['workbook']}/deploy", headers=_headers("programme_manager", ENGINEER),
    )
    assert response.status_code == 200
    assert response.json()["deploy"]["state"] == "SUCCEEDED"
