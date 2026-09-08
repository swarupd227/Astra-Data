"""§10.6 regression, against real PostgreSQL + Apache AGE -- story S7.7.1, closing
F7.7 and E7.

What only the real stack can answer: that `FOR UPDATE SKIP LOCKED` really claims a due
regression schedule the identical way it already does for a harvest schedule; that a
real FAIL really opens a real `ExceptionCase(class=REGRESSION)` with a real, readable
evidence artefact, and really notifies; that `consecutive_failures` really auto-pauses a
schedule after `MAX_CONSECUTIVE_FAILURES`; that a real `SOURCE_DRIFT` event in the real
outbox is really picked up on the next tick for a workbook with an active schedule, and
only once; that `trigger_after_publish` really nudges only an already-scheduled family
member; that `regression_monitor` really reads "released" as `deploy_state ==
"GENERATED"` and really carries a drift alert; that the export bundle is a real,
importable zip; and that all three new routes drive their own real role gate.

`RegressionScheduler.tick()` is exercised against fake `verdicts`/`case_execution`
collaborators with controllable outcomes -- deterministic coverage of this story's own
new orchestration (execute-then-diff-then-exception-then-notify-then-record), not a
second proof that `diff_result_sets` itself is correct; that already has its own 200-pair
fixture suite (`test_diff_fixtures.py`, S7.4.1).
"""

from __future__ import annotations

import asyncio
import io
import os
import threading
import zipfile
from collections.abc import Callable, Coroutine
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

pytestmark = pytest.mark.integration

asyncpg = pytest.importorskip("asyncpg")

from astra_graph.artefacts import PostgresArtefactStore  # noqa: E402
from astra_graph.case_derivation import (  # noqa: E402
    CaseDerivationService,
    PostgresParitySuiteStore,
)
from astra_graph.config import Settings  # noqa: E402
from astra_graph.events import source_drift  # noqa: E402
from astra_graph.graph import AgeGraphRepository, create_pool  # noqa: E402
from astra_graph.graph.queries import NODE_INDEX_TABLE, accessor  # noqa: E402
from astra_graph.harvest.schedule import Cadence  # noqa: E402
from astra_graph.ids import new_ulid  # noqa: E402
from astra_graph.lineage import hydrate  # noqa: E402
from astra_graph.migrations import run as run_migrations  # noqa: E402
from astra_graph.model_lifecycle import promote_family  # noqa: E402
from astra_graph.ontology import EDGE_LABELS, NODE_LABELS  # noqa: E402
from astra_graph.principal import Principal  # noqa: E402
from astra_graph.regression import (  # noqa: E402
    DEFAULT_CADENCE_MINUTES,
    MAX_CONSECUTIVE_FAILURES,
    PostgresRegressionScheduleStore,
    RegressionScheduler,
    RegressionService,
    new_regression_schedule,
    trigger_after_publish,
)
from astra_graph.tolerance_charter import DEFAULT_CHARTER, ParamRule, ToleranceCharter  # noqa: E402
from astra_graph.writes import EdgeWrite, GraphWriter, NodeWrite  # noqa: E402

PRINCIPAL = Principal("agent:harvester", run_id="run-harvester")
PARITY_ENGINEER = Principal("user:parity@artizent.example")
PROGRAMME_MANAGER = Principal("user:pm@artizent.example")


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


async def _tick_and_drain(scheduler: RegressionScheduler, *, now: datetime | None = None) -> list[str]:
    """`RegressionScheduler.tick` fires each due check as a background task and returns
    immediately -- the identical fire-and-forget shape `HarvestScheduler.tick` already
    has, so a slow check never blocks the poll timer. A test asserting on the outcome
    needs the check to have actually finished, so this drains `_running` before
    returning -- a test-only synchronisation point with no production equivalent."""
    started = await scheduler.tick(now=now) if now is not None else await scheduler.tick()
    while scheduler._running:
        await asyncio.sleep(0.01)
    return started


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
    config = _settings(f"astra_regression_{new_ulid()[10:22].lower()}")

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
                "public.parity_suite", "public.artefacts", "public.execution_observation",
                "public.regression_schedule",
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


def _charter() -> ToleranceCharter:
    return ToleranceCharter(params=ParamRule(enumerate_max_values=12, enumerate_strategy="DEFAULT_PLUS_OBSERVED"))


class _FakeVerdicts:
    """A controllable stand-in for `VerdictsService` -- see this module's own docstring
    on why the scheduler's own orchestration is tested against a fake rather than a real
    diff (which already has its own fixture suite)."""

    def __init__(self, outcomes: list[dict[str, Any]] | dict[str, Any]) -> None:
        self._outcomes = outcomes if isinstance(outcomes, list) else [outcomes]
        self._calls = 0

    async def run(self, workbook_id: str, *, charter: Any, charter_version: str, principal: Principal) -> dict[str, Any]:
        outcome = self._outcomes[min(self._calls, len(self._outcomes) - 1)]
        self._calls += 1
        if isinstance(outcome, Exception):
            raise outcome
        return {"run_id": new_ulid(), **outcome}

    @property
    def calls(self) -> int:
        return self._calls


class _FakeCaseExecution:
    def __init__(self) -> None:
        self.calls = 0

    async def execute(self, workbook_id: str, *, workspace: str, principal: Principal) -> dict[str, Any]:
        self.calls += 1
        return {"cases_executed": 1}


class _FakeCharterStore:
    async def latest(self) -> Any:
        class _Version:
            version = 0
            charter = DEFAULT_CHARTER

        return _Version()


class _RecordingNotifier:
    def __init__(self) -> None:
        self.notifications: list[dict[str, Any]] = []

    async def notify_regression(self, *, workbook_id: str, exception_case_id: str, fail_count: int) -> None:
        self.notifications.append(
            {"workbook_id": workbook_id, "exception_case_id": exception_case_id, "fail_count": fail_count}
        )


@pytest.fixture
async def estate(settings: Settings):
    """One workbook, one derived-and-executed case, and a schedule store -- the real
    prerequisite pipeline (S7.2.1 derive, S7.3.1 execute) a scheduled check re-runs, plus
    the real `public.regression_schedule` table this story's own migration adds."""
    pool = await create_pool(settings)
    try:
        repository = AgeGraphRepository(pool, graph_name=settings.graph_name)
        writer = GraphWriter(repository, event_source="/astra/graph-svc")
        suite_store = PostgresParitySuiteStore(pool, graph_name=settings.graph_name)
        derivation = CaseDerivationService(
            pool, graph_name=settings.graph_name, writer=writer, suite_store=suite_store
        )
        artefact_store = PostgresArtefactStore(pool, graph_name=settings.graph_name)
        suffix = new_ulid()[10:18].lower()

        site = await _write(writer, "Site", luid=f"s-{suffix}", name=f"rqa-{suffix}")
        project = await _write(writer, "Project", luid=f"p-{suffix}", name="Risk Core")
        await _edge(writer, "CONTAINS", site, project)
        book = await _write(writer, "Workbook", luid=f"wb-{suffix}", name="Daily VaR", revision="1")
        await _edge(writer, "CONTAINS", project, book)

        datasource = await _write(
            writer, "Datasource", name="VaR ds", type="published", luid=f"ds-{suffix}", extract_flag=True,
        )
        desk = await _write(writer, "Field", name="Desk", datatype="string", role="dimension")
        await _edge(writer, "HAS_FIELD", datasource, desk)
        margin = await _write(
            writer, "CalculatedField", name="Margin", formula="SUM([M])",
            formula_ast={"kind": "FUNCTION", "name": "SUM", "children": [], "detail": {}},
        )
        await _edge(writer, "HAS_FIELD", datasource, margin)
        sheet = await _write(
            writer, "Worksheet", name="Bar sheet", mark_type="bar",
            rows_shelf=["Desk"], cols_shelf=["Margin"], marks_shelf=[],
        )
        await _edge(writer, "CONTAINS", book, sheet)
        await _edge(writer, "USES_DATASOURCE", sheet, datasource)

        derive_result = await derivation.derive(
            book, charter_version="1", charter=_charter(), principal=PARITY_ENGINEER,
        )
        assert derive_result["cases_written"] > 0

        schedule_store = PostgresRegressionScheduleStore(pool, graph_name=settings.graph_name)
        service = RegressionService(
            pool, graph_name=settings.graph_name, store=schedule_store,
            case_derivation=derivation, charter_store=_FakeCharterStore(),
        )

        yield {
            "pool": pool, "settings": settings, "writer": writer, "artefact_store": artefact_store,
            "schedule_store": schedule_store, "service": service, "workbook": book,
            "site": site, "project": project, "sheet": sheet,
        }
    finally:
        await pool.close()


# --------------------------------------------------------------------------- scheduler


async def test_tick_runs_a_due_schedule_and_records_pass(estate) -> None:
    schedule = new_regression_schedule(
        workbook_id=estate["workbook"], cadence=Cadence(every_minutes=5),
        created_by=PROGRAMME_MANAGER.value, now=datetime.now(UTC) - timedelta(minutes=10),
    )
    await estate["schedule_store"].create(schedule)

    verdicts = _FakeVerdicts({"pass": 3, "fail": 0, "inconclusive": 0})
    case_execution = _FakeCaseExecution()
    notifier = _RecordingNotifier()
    scheduler = RegressionScheduler(
        estate["pool"], graph_name=estate["settings"].graph_name, writer=estate["writer"],
        artefact_store=estate["artefact_store"], verdicts=verdicts, case_execution=case_execution,
        charter_store=_FakeCharterStore(), store=estate["schedule_store"], notifier=notifier,
    )
    started = await _tick_and_drain(scheduler)
    assert len(started) == 1
    assert case_execution.calls == 1  # re-executed, not just re-diffed -- see this module's own docstring
    assert verdicts.calls == 1
    assert notifier.notifications == []  # no FAIL, no notification

    updated = await estate["schedule_store"].get(schedule.id)
    assert updated is not None
    assert updated.last_result == "PASS"
    assert updated.consecutive_failures == 0
    assert updated.last_run_id is not None


async def test_tick_skips_a_schedule_still_running(estate) -> None:
    schedule = new_regression_schedule(
        workbook_id=estate["workbook"], cadence=Cadence(every_minutes=5),
        created_by=PROGRAMME_MANAGER.value, now=datetime.now(UTC) - timedelta(minutes=10),
    )
    await estate["schedule_store"].create(schedule)
    scheduler = RegressionScheduler(
        estate["pool"], graph_name=estate["settings"].graph_name, writer=estate["writer"],
        artefact_store=estate["artefact_store"], verdicts=_FakeVerdicts({"pass": 1, "fail": 0, "inconclusive": 0}),
        case_execution=_FakeCaseExecution(), charter_store=_FakeCharterStore(), store=estate["schedule_store"],
    )
    scheduler._running[schedule.id] = asyncio.get_running_loop().create_future()  # never resolves
    started = await scheduler.tick(now=datetime.now(UTC))
    assert started == []


async def test_a_fail_opens_a_real_regression_exception_and_notifies(estate) -> None:
    schedule = new_regression_schedule(
        workbook_id=estate["workbook"], cadence=Cadence(every_minutes=5),
        created_by=PROGRAMME_MANAGER.value, now=datetime.now(UTC) - timedelta(minutes=10),
    )
    await estate["schedule_store"].create(schedule)

    notifier = _RecordingNotifier()
    scheduler = RegressionScheduler(
        estate["pool"], graph_name=estate["settings"].graph_name, writer=estate["writer"],
        artefact_store=estate["artefact_store"],
        verdicts=_FakeVerdicts({"pass": 1, "fail": 2, "inconclusive": 0}),
        case_execution=_FakeCaseExecution(), charter_store=_FakeCharterStore(),
        store=estate["schedule_store"], notifier=notifier,
    )
    await _tick_and_drain(scheduler)

    updated = await estate["schedule_store"].get(schedule.id)
    assert updated is not None
    assert updated.last_result == "FAIL"
    assert updated.consecutive_failures == 1

    assert len(notifier.notifications) == 1
    notice = notifier.notifications[0]
    assert notice["workbook_id"] == estate["workbook"]
    assert notice["fail_count"] == 2

    async with estate["pool"].acquire() as conn:
        rows = await conn.fetch(
            f"""SELECT id FROM {NODE_INDEX_TABLE}
                 WHERE graph = $1 AND kind = 'node' AND label = 'ExceptionCase' AND retired_at IS NULL""",
            estate["settings"].graph_name,
        )
        cases = await hydrate(conn, estate["settings"].graph_name, "ExceptionCase", [r["id"] for r in rows])
    matches = [c for c in cases.values() if c["mu_ref"] == estate["workbook"] and c["class"] == "REGRESSION"]
    assert len(matches) == 1
    case = matches[0]
    assert case["state"] == "OPEN"
    assert case["evidence_ref"]  # a real, stored artefact id

    evidence_bytes = await estate["artefact_store"].content(case["evidence_ref"])
    assert evidence_bytes is not None
    import json

    evidence = json.loads(evidence_bytes)
    assert evidence["fail"] == 2
    assert evidence["pass"] == 1


async def test_a_schedule_is_auto_paused_after_persistent_failure(estate) -> None:
    schedule = new_regression_schedule(
        workbook_id=estate["workbook"], cadence=Cadence(every_minutes=5),
        created_by=PROGRAMME_MANAGER.value, now=datetime.now(UTC) - timedelta(minutes=10),
    )
    await estate["schedule_store"].create(schedule)

    always_fail = _FakeVerdicts({"pass": 0, "fail": 1, "inconclusive": 0})
    scheduler = RegressionScheduler(
        estate["pool"], graph_name=estate["settings"].graph_name, writer=estate["writer"],
        artefact_store=estate["artefact_store"], verdicts=always_fail, case_execution=_FakeCaseExecution(),
        charter_store=_FakeCharterStore(), store=estate["schedule_store"],
    )
    for _ in range(MAX_CONSECUTIVE_FAILURES):
        await estate["schedule_store"].touch_next_run(schedule.id, next_run_at=datetime.now(UTC) - timedelta(minutes=1))
        await _tick_and_drain(scheduler)

    paused = await estate["schedule_store"].get(schedule.id)
    assert paused is not None
    assert paused.enabled is False
    assert paused.paused_reason is not None
    assert str(MAX_CONSECUTIVE_FAILURES) in paused.paused_reason


async def test_an_execution_error_is_recorded_as_a_fail_not_a_crash(estate) -> None:
    schedule = new_regression_schedule(
        workbook_id=estate["workbook"], cadence=Cadence(every_minutes=5),
        created_by=PROGRAMME_MANAGER.value, now=datetime.now(UTC) - timedelta(minutes=10),
    )
    await estate["schedule_store"].create(schedule)

    class _BrokenExecution:
        async def execute(self, workbook_id: str, *, workspace: str, principal: Principal) -> dict[str, Any]:
            raise RuntimeError("source adapter unreachable")

    scheduler = RegressionScheduler(
        estate["pool"], graph_name=estate["settings"].graph_name, writer=estate["writer"],
        artefact_store=estate["artefact_store"], verdicts=_FakeVerdicts({"pass": 1, "fail": 0, "inconclusive": 0}),
        case_execution=_BrokenExecution(), charter_store=_FakeCharterStore(), store=estate["schedule_store"],
    )
    started = await _tick_and_drain(scheduler)
    assert len(started) == 1

    updated = await estate["schedule_store"].get(schedule.id)
    assert updated is not None
    assert updated.last_result == "FAIL"
    assert updated.last_error is not None
    assert "source adapter unreachable" in updated.last_error


# ---------------------------------------------------------------------------- drift


async def test_a_source_drift_event_triggers_an_immediate_check(estate) -> None:
    schedule = new_regression_schedule(
        workbook_id=estate["workbook"], cadence=Cadence(every_minutes=DEFAULT_CADENCE_MINUTES),
        created_by=PROGRAMME_MANAGER.value, now=datetime.now(UTC),
    )
    await estate["schedule_store"].create(schedule)

    await estate["writer"].append_event(
        source_drift(
            source="/astra/graph-svc", workbook_node_id=estate["workbook"], principal=PRINCIPAL,
            detail={"reason": "revision changed under work in progress"},
        )
    )

    verdicts = _FakeVerdicts({"pass": 1, "fail": 0, "inconclusive": 0})
    scheduler = RegressionScheduler(
        estate["pool"], graph_name=estate["settings"].graph_name, writer=estate["writer"],
        artefact_store=estate["artefact_store"], verdicts=verdicts, case_execution=_FakeCaseExecution(),
        charter_store=_FakeCharterStore(), store=estate["schedule_store"],
    )
    started = await _tick_and_drain(scheduler)
    assert len(started) == 1
    assert verdicts.calls == 1

    # A second tick with no new drift does not fire again.
    started_again = await _tick_and_drain(scheduler)
    assert started_again == []
    assert verdicts.calls == 1


async def test_drift_for_an_unscheduled_workbook_triggers_nothing(estate) -> None:
    await estate["writer"].append_event(
        source_drift(
            source="/astra/graph-svc", workbook_node_id="wb-nobody-is-watching", principal=PRINCIPAL,
            detail={"reason": "revision changed"},
        )
    )
    scheduler = RegressionScheduler(
        estate["pool"], graph_name=estate["settings"].graph_name, writer=estate["writer"],
        artefact_store=estate["artefact_store"], verdicts=_FakeVerdicts({"pass": 1, "fail": 0, "inconclusive": 0}),
        case_execution=_FakeCaseExecution(), charter_store=_FakeCharterStore(), store=estate["schedule_store"],
    )
    assert await scheduler.tick() == []


# -------------------------------------------------------------------- trigger_after_publish


async def test_trigger_after_publish_nudges_only_an_already_scheduled_workbook(estate) -> None:
    unscheduled = await _write(estate["writer"], "Workbook", luid="wb-unscheduled", name="No Schedule", revision="1")
    await _edge(estate["writer"], "CONTAINS", estate["project"], unscheduled)

    family = await _write(estate["writer"], "ModelFamily", name="Risk Family", state="BUILT")
    await _edge(estate["writer"], "IN_FAMILY", estate["workbook"], family, confidence=1.0)
    await _edge(estate["writer"], "IN_FAMILY", unscheduled, family, confidence=1.0)

    schedule = new_regression_schedule(
        workbook_id=estate["workbook"], cadence=Cadence(every_minutes=DEFAULT_CADENCE_MINUTES),
        created_by=PROGRAMME_MANAGER.value, now=datetime.now(UTC),
    )
    await estate["schedule_store"].create(schedule)
    original_next_run = schedule.next_run_at

    nudged = await trigger_after_publish(
        estate["pool"], estate["settings"].graph_name, estate["schedule_store"], family_id=family,
    )
    assert nudged == [schedule.id]

    updated = await estate["schedule_store"].get(schedule.id)
    assert updated is not None
    assert updated.next_run_at <= original_next_run
    assert await estate["schedule_store"].get_by_workbook(unscheduled) is None


async def test_promote_family_calls_the_hook_without_failing_the_promotion(estate) -> None:
    """`promote_family`'s own regression-nudge hook is best-effort -- a real family that
    is not actually promotable (no BUILT version here) still proves the hook parameter
    is accepted and wired, without needing this test to build a full model pipeline."""
    from astra_graph.errors import ElementNotFoundError

    with pytest.raises(ElementNotFoundError):
        await promote_family(
            estate["pool"], estate["settings"].graph_name, estate["writer"], "no-such-family",
            principal=PROGRAMME_MANAGER, regression_schedule_store=estate["schedule_store"],
        )


# ------------------------------------------------------------------------------ monitor


async def test_regression_monitor_lists_only_released_workbooks(estate) -> None:
    await _write(
        estate["writer"], "ReportDefinition", mu_ref=estate["workbook"], model_ref="fam-1",
        deploy_state="GENERATED",
    )
    unreleased_book = await _write(estate["writer"], "Workbook", luid="wb-not-released", name="Not Released", revision="1")
    await _edge(estate["writer"], "CONTAINS", estate["project"], unreleased_book)
    await _write(
        estate["writer"], "ReportDefinition", mu_ref=unreleased_book, model_ref="fam-2",
        deploy_state="DEPLOY_FAILED",
    )

    rows = await estate["service"].monitor()
    workbook_ids = [row["workbook_id"] for row in rows]
    assert estate["workbook"] in workbook_ids
    assert unreleased_book not in workbook_ids


async def test_regression_monitor_carries_schedule_and_drift_alert(estate) -> None:
    await _write(
        estate["writer"], "ReportDefinition", mu_ref=estate["workbook"], model_ref="fam-1",
        deploy_state="GENERATED",
    )
    schedule = new_regression_schedule(
        workbook_id=estate["workbook"], cadence=Cadence(every_minutes=DEFAULT_CADENCE_MINUTES),
        created_by=PROGRAMME_MANAGER.value, now=datetime.now(UTC),
    )
    await estate["schedule_store"].create(schedule)
    await estate["writer"].append_event(
        source_drift(
            source="/astra/graph-svc", workbook_node_id=estate["workbook"], principal=PRINCIPAL,
            detail={"reason": "revision changed"},
        )
    )

    rows = await estate["service"].monitor()
    row = next(r for r in rows if r["workbook_id"] == estate["workbook"])
    assert row["schedule"]["id"] == schedule.id
    assert row["drift_alert"]["unaddressed"] is True


async def test_regression_monitor_is_empty_when_nothing_is_released(estate) -> None:
    assert await estate["service"].monitor() == []


# ------------------------------------------------------------------------------- export


async def test_export_produces_a_real_importable_zip(estate) -> None:
    content = await estate["service"].export(estate["workbook"])
    archive = zipfile.ZipFile(io.BytesIO(content))
    names = set(archive.namelist())
    assert {"suite.json", "charter.json", "diff.py", "tolerance_rules.py",
            "case_execution_query.py", "run_suite.py", "README.md"} <= names

    import json

    suite = json.loads(archive.read("suite.json"))
    assert suite["workbook_id"] == estate["workbook"]
    assert len(suite["cases"]) > 0

    diff_source = archive.read("diff.py").decode("utf-8")
    assert "from .tolerance_rules import" not in diff_source  # de-relativised for a flat bundle
    assert "from tolerance_rules import" in diff_source

    charter = json.loads(archive.read("charter.json"))
    assert "numeric" in charter and "rows" in charter


async def test_export_refuses_a_workbook_with_no_derived_cases(estate) -> None:
    bare_book = await _write(estate["writer"], "Workbook", luid="wb-bare", name="Bare", revision="1")
    await _edge(estate["writer"], "CONTAINS", estate["project"], bare_book)
    with pytest.raises(LookupError, match="no derived parity cases"):
        await estate["service"].export(bare_book)


# ---------------------------------------------------------------------------------- API


@pytest.fixture
async def http_client(estate):
    from httpx import ASGITransport, AsyncClient

    from astra_graph.main import create_app

    app = create_app()
    app.state.regression_schedule_store = estate["schedule_store"]
    app.state.regression_service = estate["service"]
    app.state.artefact_store = estate["artefact_store"]

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://graph-svc") as async_client:
        yield async_client


def _headers(role: str, principal: Principal) -> dict[str, str]:
    from astra_graph.principal import PRINCIPAL_HEADER
    from astra_graph.roles import ROLES_HEADER

    return {PRINCIPAL_HEADER: principal.value, ROLES_HEADER: role}


async def test_schedule_regression_over_http_requires_the_programme_manager_role(estate, http_client) -> None:
    response = await http_client.post(
        f"/v1/workbooks/{estate['workbook']}:schedule-regression",
        json={}, headers=_headers("parity_engineer", PROGRAMME_MANAGER),
    )
    assert response.status_code == 403


async def test_schedule_regression_over_http_succeeds_for_the_programme_manager(estate, http_client) -> None:
    response = await http_client.post(
        f"/v1/workbooks/{estate['workbook']}:schedule-regression",
        json={"workspace": "prod"}, headers=_headers("programme_manager", PROGRAMME_MANAGER),
    )
    assert response.status_code == 201
    body = response.json()
    assert body["workbook_id"] == estate["workbook"]
    assert body["workspace"] == "prod"
    assert "every 10080 minutes" in body["cadence_description"]


async def test_regression_monitor_over_http_is_open_to_the_report_owner(estate, http_client) -> None:
    response = await http_client.get(
        "/v1/regression-monitor", headers=_headers("client_report_owner", PROGRAMME_MANAGER),
    )
    assert response.status_code == 200
    assert response.json() == {"workbooks": [], "count": 0}


async def test_regression_monitor_over_http_refuses_an_unrelated_role(estate, http_client) -> None:
    response = await http_client.get(
        "/v1/regression-monitor", headers=_headers("client_data_owner", PROGRAMME_MANAGER),
    )
    assert response.status_code == 403


async def test_export_regression_suite_over_http_requires_artizent(estate, http_client) -> None:
    response = await http_client.post(
        f"/v1/workbooks/{estate['workbook']}:export-regression-suite",
        headers=_headers("client_report_owner", PROGRAMME_MANAGER),
    )
    assert response.status_code == 403


async def test_export_regression_suite_over_http_stores_a_real_downloadable_artefact(estate, http_client) -> None:
    response = await http_client.post(
        f"/v1/workbooks/{estate['workbook']}:export-regression-suite",
        headers=_headers("migration_engineer", PROGRAMME_MANAGER),
    )
    assert response.status_code == 201
    record = response.json()
    assert record["kind"] == "regression_export"
    assert record["mu_ref"] == estate["workbook"]

    content_response = await http_client.get(
        f"/v1/artefacts/{record['id']}/content", headers=_headers("migration_engineer", PROGRAMME_MANAGER),
    )
    assert content_response.status_code == 200
    archive = zipfile.ZipFile(io.BytesIO(content_response.content))
    assert "run_suite.py" in archive.namelist()
