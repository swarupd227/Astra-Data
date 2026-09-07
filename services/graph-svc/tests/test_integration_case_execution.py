"""Dual execution, against real PostgreSQL + Apache AGE -- stories S7.3.1/S7.3.2,
closing F7.3, spec §10.2.

What only the real stack can answer: that a real, derived `ParityCase` really gets run
on both a real `SourceAdapter` and a real `TargetAdapter`, concurrently, and that both
sides' `ResultSet`s land as real Parquet artefacts with a real content hash; that
`expected_ref`/`candidate_ref` are really written back onto the case while `state` is
really left alone; that a real `Field -> ModelTable` `MAPS_TO` binding really qualifies
the DAX query text when one exists; that a source-side adapter failure (an unmatched
workbook luid) is really recorded as `INCONCLUSIVE` with the right reason class, not a
crash; that a workbook with no live cases, no resolvable site, or no source adapter
configured is really refused; that the new route drives its own real role gate; that a
real slow call is really retried once with a real longer budget and really recovers;
that every side-execution really lands as an observation row; and that the real
inconclusive rate, its alert threshold, and its trailing window are computed correctly
against real rows.
"""

from __future__ import annotations

import asyncio
import io
import os
import threading
from collections.abc import Callable, Coroutine
from pathlib import Path
from typing import Any

import pytest

pytestmark = pytest.mark.integration

asyncpg = pytest.importorskip("asyncpg")
pq = pytest.importorskip("pyarrow.parquet")

from astra_adapter import ExecutionCharter  # noqa: E402
from astra_adapter.fake.source import (  # noqa: E402
    FixtureSite,
    FixtureSourceAdapter,
    FixtureWorkbook,
)
from astra_adapter.target_fake import FixtureTargetAdapter  # noqa: E402

from astra_graph.artefacts import PostgresArtefactStore  # noqa: E402
from astra_graph.case_derivation import (  # noqa: E402
    CaseDerivationService,
    PostgresParitySuiteStore,
)
from astra_graph.case_execution import (  # noqa: E402
    EXECUTION_OBSERVATION_TABLE,
    CaseExecutionError,
    CaseExecutionService,
    inconclusive_rate,
)
from astra_graph.config import Settings  # noqa: E402
from astra_graph.events import source_for  # noqa: E402
from astra_graph.graph import AgeGraphRepository, create_pool  # noqa: E402
from astra_graph.graph.queries import EDGE_INDEX_TABLE, NODE_INDEX_TABLE, accessor  # noqa: E402
from astra_graph.ids import new_ulid  # noqa: E402
from astra_graph.lineage import hydrate  # noqa: E402
from astra_graph.migrations import run as run_migrations  # noqa: E402
from astra_graph.ontology import EDGE_LABELS, NODE_LABELS  # noqa: E402
from astra_graph.principal import Principal  # noqa: E402
from astra_graph.tolerance_charter import ParamRule, ToleranceCharter  # noqa: E402
from astra_graph.writes import EdgeWrite, GraphWriter, NodeWrite  # noqa: E402

PRINCIPAL = Principal("agent:harvester", run_id="run-harvester")
PARITY_ENGINEER = Principal("user:parity@artizent.example")


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
    """Function-scoped -- see test_integration_case_derivation.py's own identical
    fixture for why a shared graph would let one test's cases pollute another's."""
    config = _settings(f"astra_case_execution_{new_ulid()[10:22].lower()}")

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
                "public.parity_suite",
                "public.artefacts",
                "public.execution_observation",
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
    await writer.write_edge(
        EdgeWrite(type=type_, from_id=source, to_id=target, properties=props), principal=PRINCIPAL
    )


def _charter(max_values: int = 12) -> ToleranceCharter:
    return ToleranceCharter(params=ParamRule(enumerate_max_values=max_values, enumerate_strategy="DEFAULT_PLUS_OBSERVED"))


@pytest.fixture
async def estate(settings: Settings, tmp_path: Path):
    """One workbook, one sheet -- the identical shape
    test_integration_case_derivation.py's own `estate` fixture builds, since dual
    execution needs real, already-derived `ParityCase` nodes to run against. The
    workbook's own `luid` matches a `FixtureSourceAdapter` workbook built here too, so
    the happy path really executes rather than falling straight to INCONCLUSIVE."""
    pool = await create_pool(settings)
    try:
        repository = AgeGraphRepository(pool, graph_name=settings.graph_name)
        writer = GraphWriter(repository, event_source=source_for(settings.graph_name))
        suite_store = PostgresParitySuiteStore(pool, graph_name=settings.graph_name)
        derivation = CaseDerivationService(
            pool, graph_name=settings.graph_name, writer=writer, suite_store=suite_store
        )
        artefact_store = PostgresArtefactStore(pool, graph_name=settings.graph_name)
        suffix = new_ulid()[10:18].lower()
        site_name = f"rqa-{suffix}"
        workbook_luid = f"wb-{suffix}"

        site = await _write(writer, "Site", luid=f"s-{suffix}", name=site_name)
        project = await _write(writer, "Project", luid=f"p-{suffix}", name="Risk Core")
        await _edge(writer, "CONTAINS", site, project)
        book = await _write(writer, "Workbook", luid=workbook_luid, name="Daily VaR", revision="1")
        await _edge(writer, "CONTAINS", project, book)

        datasource = await _write(
            writer, "Datasource", name="VaR ds", type="published", luid=f"ds-{suffix}", extract_flag=True,
        )
        desk = await _write(writer, "Field", name="Desk", datatype="string", role="dimension")
        await _edge(writer, "HAS_FIELD", datasource, desk)

        margin_calc = await _write(
            writer, "CalculatedField", name="MarginCalc", formula="SUM([Margin])",
            formula_ast={"kind": "FUNCTION", "name": "SUM", "children": [], "detail": {}},
        )
        await _edge(writer, "HAS_FIELD", datasource, margin_calc)

        sheet = await _write(
            writer, "Worksheet", name="Bar sheet", mark_type="bar",
            rows_shelf=["Desk"], cols_shelf=["MarginCalc"], marks_shelf=[],
        )
        await _edge(writer, "CONTAINS", book, sheet)
        await _edge(writer, "USES_DATASOURCE", sheet, datasource)

        derive_result = await derivation.derive(
            book, charter_version="1", charter=_charter(), principal=PARITY_ENGINEER,
        )
        assert derive_result["cases_written"] > 0

        source_adapter = FixtureSourceAdapter(
            [FixtureSite(name=site_name, workbooks=[FixtureWorkbook(name="Daily VaR", luid=workbook_luid, project="Risk Core")])]
        )
        target_adapter = FixtureTargetAdapter(repo_path=tmp_path / "repo", workspace_root=tmp_path / "workspaces")
        service = CaseExecutionService(
            pool, graph_name=settings.graph_name, writer=writer, artefact_store=artefact_store,
            source_adapter=source_adapter, target_adapter=target_adapter,
        )

        yield {
            "pool": pool, "settings": settings, "writer": writer, "artefact_store": artefact_store,
            "service": service, "source_adapter": source_adapter, "target_adapter": target_adapter,
            "workbook": book, "workbook_luid": workbook_luid, "sheet": sheet, "desk": desk,
        }
    finally:
        await pool.close()


async def _case_properties(pool: asyncpg.Pool, graph_name: str, mu_ref: str) -> dict[str, dict[str, Any]]:
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            f"""SELECT id FROM {NODE_INDEX_TABLE}
             WHERE graph = $1 AND kind = 'node' AND label = 'ParityCase' AND retired_at IS NULL""",
            graph_name,
        )
        ids = [row["id"] for row in rows]
        cases = await hydrate(conn, graph_name, "ParityCase", ids)
    return {cid: props for cid, props in cases.items() if props.get("mu_ref") == mu_ref}


# --------------------------------------------------------------------------- execution


async def test_executing_runs_both_sides_and_stores_real_parquet_artefacts(estate) -> None:
    result = await estate["service"].execute(estate["workbook"], workspace="dev", principal=PARITY_ENGINEER)
    assert result["cases_executed"] > 0

    for one in result["results"]:
        assert one["expected_outcome"] == "OK"
        assert one["candidate_outcome"] == "OK"
        assert one["source_strategy"] == "EXTRACT_READ"
        assert "EVALUATE" in one["query_text"]

        expected = await estate["artefact_store"].get(one["expected_ref"])
        candidate = await estate["artefact_store"].get(one["candidate_ref"])
        assert expected is not None and candidate is not None
        assert expected.kind == "result_set_expected"
        assert candidate.kind == "result_set_candidate"
        assert expected.content_hash and candidate.content_hash
        assert expected.media_type == "application/vnd.apache.parquet"

        expected_bytes = await estate["artefact_store"].content(one["expected_ref"])
        candidate_bytes = await estate["artefact_store"].content(one["candidate_ref"])
        assert expected_bytes is not None
        assert candidate_bytes is not None
        pq.read_table(io.BytesIO(expected_bytes))
        pq.read_table(io.BytesIO(candidate_bytes))


async def test_executing_writes_expected_and_candidate_refs_onto_the_case(estate) -> None:
    result = await estate["service"].execute(estate["workbook"], workspace="dev", principal=PARITY_ENGINEER)
    cases = await _case_properties(estate["pool"], estate["settings"].graph_name, estate["workbook"])

    executed_ids = {one["case_id"] for one in result["results"]}
    assert executed_ids == set(cases)
    for case_id in executed_ids:
        properties = cases[case_id]
        assert properties["expected_ref"]
        assert properties["candidate_ref"]


async def test_executing_leaves_case_state_untouched(estate) -> None:
    before = await _case_properties(estate["pool"], estate["settings"].graph_name, estate["workbook"])
    states_before = {cid: props["state"] for cid, props in before.items()}

    await estate["service"].execute(estate["workbook"], workspace="dev", principal=PARITY_ENGINEER)

    after = await _case_properties(estate["pool"], estate["settings"].graph_name, estate["workbook"])
    for case_id, state in states_before.items():
        assert after[case_id]["state"] == state


async def test_a_real_maps_to_binding_qualifies_the_dax_table_reference(estate) -> None:
    table = await _write(
        estate["writer"], "ModelTable", name="Geography", mode="import", family_ref="fam-1",
    )
    await _edge(estate["writer"], "MAPS_TO", estate["desk"], table, target_column="Desk")

    result = await estate["service"].execute(estate["workbook"], workspace="dev", principal=PARITY_ENGINEER)
    assert any("'Geography'[Desk]" in one["query_text"] for one in result["results"])


async def test_a_source_side_luid_mismatch_is_recorded_inconclusive_not_a_crash(estate) -> None:
    mismatched_adapter = FixtureSourceAdapter(
        [FixtureSite(name="other-site", workbooks=[FixtureWorkbook(name="Other", luid="different-luid", project="Risk Core")])]
    )
    service = CaseExecutionService(
        estate["pool"], graph_name=estate["settings"].graph_name, writer=estate["writer"],
        artefact_store=estate["artefact_store"], source_adapter=mismatched_adapter,
        target_adapter=estate["target_adapter"],
    )
    result = await service.execute(estate["workbook"], workspace="dev", principal=PARITY_ENGINEER)
    assert result["cases_executed"] > 0
    for one in result["results"]:
        assert one["expected_outcome"] == "INCONCLUSIVE"
        assert one["expected_reason_class"] == "ADAPTER_ERROR"
        # A raised AdapterError is retried once too (S7.3.2's own broadened rule, not
        # only a timeout) -- the mismatch is real on both attempts, so it surfaces still
        # INCONCLUSIVE after using its one retry.
        assert one["expected_attempts"] == 2
        assert one["candidate_outcome"] == "OK"
        assert one["candidate_reason_class"] is None


class _SlowSourceAdapter:
    """Wraps a real `FixtureSourceAdapter`, delaying `execute_case` by a configurable
    duration -- real enough to exercise `CaseExecutionService`'s own retry-with-a-
    longer-budget path end to end, story S7.3.2's own AC."""

    def __init__(self, inner: FixtureSourceAdapter, *, delay_seconds: float) -> None:
        self._inner = inner
        self._delay_seconds = delay_seconds
        self.calls = 0

    async def execute_case(self, case: Any) -> Any:
        self.calls += 1
        await asyncio.sleep(self._delay_seconds)
        return await self._inner.execute_case(case)


async def test_a_slow_source_call_is_retried_once_with_a_longer_budget_and_recovers(estate) -> None:
    # First budget 0.1s < the real 0.15s delay (times out); retry budget
    # 0.1 * DEFAULT_RETRY_TIMEOUT_MULTIPLIER (2.0) = 0.2s > 0.15s (recovers).
    slow_adapter = _SlowSourceAdapter(estate["source_adapter"], delay_seconds=0.15)
    service = CaseExecutionService(
        estate["pool"], graph_name=estate["settings"].graph_name, writer=estate["writer"],
        artefact_store=estate["artefact_store"], source_adapter=slow_adapter,
        target_adapter=estate["target_adapter"],
        execution_charter=ExecutionCharter(timeout_seconds=0.1),
    )
    result = await service.execute(estate["workbook"], workspace="dev", principal=PARITY_ENGINEER)
    assert result["cases_executed"] > 0
    for one in result["results"]:
        assert one["expected_outcome"] == "OK"
        assert one["expected_reason_class"] is None
        assert one["expected_attempts"] == 2  # timed out once, recovered on the retry
    assert slow_adapter.calls == 2 * result["cases_executed"]


async def test_a_slow_source_call_that_never_recovers_surfaces_a_real_timeout(estate) -> None:
    # Even the retry's own longer budget (0.02 * 2 = 0.04s) is far shorter than the
    # 0.2s delay -- both attempts genuinely time out.
    slow_adapter = _SlowSourceAdapter(estate["source_adapter"], delay_seconds=0.2)
    service = CaseExecutionService(
        estate["pool"], graph_name=estate["settings"].graph_name, writer=estate["writer"],
        artefact_store=estate["artefact_store"], source_adapter=slow_adapter,
        target_adapter=estate["target_adapter"],
        execution_charter=ExecutionCharter(timeout_seconds=0.02),
    )
    result = await service.execute(estate["workbook"], workspace="dev", principal=PARITY_ENGINEER)
    for one in result["results"]:
        assert one["expected_outcome"] == "INCONCLUSIVE"
        assert one["expected_reason_class"] == "TIMEOUT"
        assert one["expected_attempts"] == 2
    assert slow_adapter.calls == 2 * result["cases_executed"]


# ------------------------------------------------------------------------ observability


async def test_executing_records_one_observation_row_per_side_per_case(estate) -> None:
    result = await estate["service"].execute(estate["workbook"], workspace="dev", principal=PARITY_ENGINEER)
    async with estate["pool"].acquire() as conn:
        rows = await conn.fetch(
            f"SELECT side, strategy, outcome, reason_class, attempts FROM {EXECUTION_OBSERVATION_TABLE} "
            "WHERE graph = $1 ORDER BY side",
            estate["settings"].graph_name,
        )
    assert len(rows) == 2 * result["cases_executed"]
    sides = {row["side"] for row in rows}
    assert sides == {"source", "target"}
    assert all(row["outcome"] == "OK" for row in rows)
    assert all(row["reason_class"] is None for row in rows)
    assert all(row["attempts"] == 1 for row in rows)


async def test_inconclusive_rate_is_zero_after_an_all_ok_execution(estate) -> None:
    await estate["service"].execute(estate["workbook"], workspace="dev", principal=PARITY_ENGINEER)
    report = await estate["service"].inconclusive_rate()
    assert report["total"] > 0
    assert report["inconclusive"] == 0
    assert report["rate"] == 0.0
    assert report["alert"] is False
    assert report["by_reason"] == {}


async def test_inconclusive_rate_reflects_a_real_failure_and_trips_the_alert(estate) -> None:
    mismatched_adapter = FixtureSourceAdapter(
        [FixtureSite(name="other-site", workbooks=[FixtureWorkbook(name="Other", luid="different-luid", project="Risk Core")])]
    )
    service = CaseExecutionService(
        estate["pool"], graph_name=estate["settings"].graph_name, writer=estate["writer"],
        artefact_store=estate["artefact_store"], source_adapter=mismatched_adapter,
        target_adapter=estate["target_adapter"],
    )
    result = await service.execute(estate["workbook"], workspace="dev", principal=PARITY_ENGINEER)

    report = await service.inconclusive_rate()
    expected_total = 2 * result["cases_executed"]
    expected_inconclusive = result["cases_executed"]  # every source side, none of the target sides
    assert report["total"] == expected_total
    assert report["inconclusive"] == expected_inconclusive
    assert report["rate"] == pytest.approx(expected_inconclusive / expected_total)
    assert report["alert"] is True  # far above the default 2% threshold
    assert report["by_reason"] == {"ADAPTER_ERROR": expected_inconclusive}


async def test_inconclusive_rate_respects_a_lower_threshold_argument(estate) -> None:
    await estate["service"].execute(estate["workbook"], workspace="dev", principal=PARITY_ENGINEER)
    report = await estate["service"].inconclusive_rate(threshold=-1.0)
    assert report["alert"] is True  # even a real 0% rate exceeds an impossible -1.0 threshold


async def test_inconclusive_rate_excludes_observations_outside_the_window(estate) -> None:
    await estate["service"].execute(estate["workbook"], workspace="dev", principal=PARITY_ENGINEER)
    async with estate["pool"].acquire() as conn:
        await conn.execute(
            f"UPDATE {EXECUTION_OBSERVATION_TABLE} SET recorded_at = now() - interval '48 hours' "
            "WHERE graph = $1",
            estate["settings"].graph_name,
        )
    report = await inconclusive_rate(estate["pool"], estate["settings"].graph_name, window_hours=24.0)
    assert report["total"] == 0
    assert report["rate"] == 0.0
    assert report["alert"] is False


async def test_executing_a_workbook_with_no_live_cases_is_refused(estate) -> None:
    # A resolvable site (so `_resolve_site` succeeds) but no derived cases -- otherwise
    # `execute()`'s own site check would fire first and this would test the wrong thing.
    async with estate["pool"].acquire() as conn:
        project_row = await conn.fetchrow(
            f"""SELECT e.from_id AS project_id FROM {EDGE_INDEX_TABLE} e
                 WHERE e.graph = $1 AND e.label = 'CONTAINS' AND e.to_id = $2 AND e.retired_at IS NULL""",
            estate["settings"].graph_name, estate["workbook"],
        )
    project_id = project_row["project_id"]
    empty_book = await _write(estate["writer"], "Workbook", luid="wb-empty", name="Empty", revision="1")
    await _edge(estate["writer"], "CONTAINS", project_id, empty_book)
    with pytest.raises(CaseExecutionError, match="no live parity cases"):
        await estate["service"].execute(empty_book, workspace="dev", principal=PARITY_ENGINEER)


async def test_executing_with_no_source_adapter_configured_is_refused(estate) -> None:
    service = CaseExecutionService(
        estate["pool"], graph_name=estate["settings"].graph_name, writer=estate["writer"],
        artefact_store=estate["artefact_store"], source_adapter=None, target_adapter=estate["target_adapter"],
    )
    with pytest.raises(CaseExecutionError, match="no source adapter"):
        await service.execute(estate["workbook"], workspace="dev", principal=PARITY_ENGINEER)


async def test_executing_a_workbook_with_no_resolvable_site_is_refused(estate) -> None:
    orphan_book = await _write(estate["writer"], "Workbook", luid="wb-orphan", name="Orphan", revision="1")
    with pytest.raises(CaseExecutionError, match="no resolvable Tableau site"):
        await estate["service"].execute(orphan_book, workspace="dev", principal=PARITY_ENGINEER)


# ---------------------------------------------------------------------------------- API


@pytest.fixture
async def http_client(estate):
    from httpx import ASGITransport, AsyncClient

    from astra_graph.main import create_app

    app = create_app()
    app.state.case_execution = estate["service"]

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://graph-svc") as async_client:
        yield async_client


def _headers(role: str, principal: Principal) -> dict[str, str]:
    from astra_graph.principal import PRINCIPAL_HEADER
    from astra_graph.roles import ROLES_HEADER

    return {PRINCIPAL_HEADER: principal.value, ROLES_HEADER: role}


async def test_execute_over_http_requires_the_parity_engineer_role(estate, http_client) -> None:
    response = await http_client.post(
        f"/v1/workbooks/{estate['workbook']}:execute-parity-cases",
        headers=_headers("programme_manager", PARITY_ENGINEER),
    )
    assert response.status_code == 403


async def test_execute_over_http_succeeds_for_the_parity_engineer(estate, http_client) -> None:
    response = await http_client.post(
        f"/v1/workbooks/{estate['workbook']}:execute-parity-cases",
        headers=_headers("parity_engineer", PARITY_ENGINEER),
    )
    assert response.status_code == 200
    body = response.json()
    assert body["cases_executed"] > 0
    assert all(one["expected_ref"] for one in body["results"])


async def test_execute_over_http_with_no_cases_is_a_clean_400(estate, http_client) -> None:
    empty_book = await _write(estate["writer"], "Workbook", luid="wb-empty-http", name="Empty", revision="1")
    response = await http_client.post(
        f"/v1/workbooks/{empty_book}:execute-parity-cases",
        headers=_headers("parity_engineer", PARITY_ENGINEER),
    )
    assert response.status_code == 400


async def test_platform_health_over_http_reports_the_real_inconclusive_rate(estate, http_client) -> None:
    await http_client.post(
        f"/v1/workbooks/{estate['workbook']}:execute-parity-cases",
        headers=_headers("parity_engineer", PARITY_ENGINEER),
    )
    response = await http_client.get(
        "/v1/platform/health", headers=_headers("platform_engineer", PARITY_ENGINEER),
    )
    assert response.status_code == 200
    execution = response.json()["execution"]
    assert execution["available"] is True
    assert execution["total"] > 0
    assert execution["inconclusive"] == 0
    assert execution["threshold"] == pytest.approx(0.02)
    assert execution["alert"] is False
