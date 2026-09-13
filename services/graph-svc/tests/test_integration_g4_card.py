"""G4 decommission, against real PostgreSQL + Apache AGE and the real fixture source
adapter -- story S9.3.1, opening F9.3.

What only the real stack can answer: that the readiness checklist really reads a real
released MU (a SUCCEEDED `promotion_run` row) and really excludes a real withdrawn one;
that the parallel-run window really elapses against real `promotion_run.finished_at`
timestamps; that a real `RegressionSchedule` FAIL really turns regression red while its
own honest absence stays green; that a real frozen `AdoptionSnapshot.meets_threshold`
really drives the adoption item; that a real owner confirmation really counts; that
`approve` really refuses an unready site or one with no archive capability before
archiving anything; that a real approval really archives every real source workbook
through the adapter's own `archive()`, really writes a real `GateDecision(gate="G4")`,
really records `Site.decommissioned_at`/`.licence_release_value`, and that `defer`
really writes its own real reason and target date.
"""

from __future__ import annotations

import asyncio
import os
import threading
from collections.abc import Callable, Coroutine
from datetime import UTC, datetime, timedelta
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

from astra_graph.adoption import AdoptionSnapshot, PostgresAdoptionStore  # noqa: E402
from astra_graph.artefacts import PostgresArtefactStore  # noqa: E402
from astra_graph.config import Settings  # noqa: E402
from astra_graph.errors import ElementNotFoundError, InvalidRequestError  # noqa: E402
from astra_graph.events import source_for  # noqa: E402
from astra_graph.g3_card import DEFAULT_PARALLEL_WINDOW_WEEKS  # noqa: E402
from astra_graph.g4_card import (  # noqa: E402
    G4CardService,
    PostgresDecommissionConfirmationStore,
    approve,
    defer,
    g4_card,
    readiness_checklist,
)
from astra_graph.graph import AgeGraphRepository, create_pool  # noqa: E402
from astra_graph.graph.queries import accessor  # noqa: E402
from astra_graph.harvest.schedule import Cadence  # noqa: E402
from astra_graph.ids import new_ulid  # noqa: E402
from astra_graph.lineage import hydrate  # noqa: E402
from astra_graph.migrations import run as run_migrations  # noqa: E402
from astra_graph.ontology import EDGE_LABELS, NODE_LABELS  # noqa: E402
from astra_graph.principal import Principal  # noqa: E402
from astra_graph.regression import PostgresRegressionScheduleStore, RegressionSchedule  # noqa: E402
from astra_graph.release import PostgresPromotionStore  # noqa: E402
from astra_graph.scope import DecisionKind, PostgresScopeStore, new_decision  # noqa: E402
from astra_graph.writes import EdgeWrite, GraphWriter, NodeWrite  # noqa: E402

PRINCIPAL = Principal("agent:steward", run_id="run-steward")
LICENCE_ADMIN = Principal("user:licence.admin@client.example")
PM_VALUE = "user:pm@artizent.example"
REPORT_OWNER_VALUE = "user:owner@client.example"

_ELAPSED_FINISHED_AT = datetime.now(UTC) - timedelta(weeks=DEFAULT_PARALLEL_WINDOW_WEEKS + 1)
_NOT_ELAPSED_FINISHED_AT = datetime.now(UTC)


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
    config = _settings(f"astra_g4card_{new_ulid()[10:22].lower()}")

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
                "public.artefacts", "public.scope_decision", "public.promotion_run",
                "public.adoption_snapshot", "public.decommission_confirmation", "public.regression_schedule",
            ):
                await conn.execute(f"DELETE FROM {table} WHERE graph = $1", config.graph_name)
            await conn.execute("SELECT ag_catalog.drop_graph($1, true)", config.graph_name)
        finally:
            await conn.close()

    if not _run_off_loop(setup):
        pytest.skip("PostgreSQL with Apache AGE not reachable")

    yield config
    _run_off_loop(teardown)


def _parsed(value: str) -> datetime:
    """AGE round-trips a TIMESTAMP property to microsecond precision regardless of how
    many digits it was written with -- compare parsed instants, never raw strings."""
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


async def _write(writer: GraphWriter, label: str, **properties: Any) -> str:
    created = await writer.write_nodes([NodeWrite(type=label, properties=properties)], principal=PRINCIPAL)
    return str(created[0]["properties"]["id"])


async def _edge(writer: GraphWriter, type_: str, source: str, target: str, **props: Any) -> None:
    await writer.write_edge(EdgeWrite(type=type_, from_id=source, to_id=target, properties=props), principal=PRINCIPAL)


@pytest.fixture
async def estate(settings: Settings):
    pool = await create_pool(settings)
    try:
        repository = AgeGraphRepository(pool, graph_name=settings.graph_name)
        writer = GraphWriter(repository, event_source=source_for(settings.graph_name))
        artefact_store = PostgresArtefactStore(pool, graph_name=settings.graph_name)
        scope_store = PostgresScopeStore(pool, graph_name=settings.graph_name)
        promotion_store = PostgresPromotionStore(pool, graph_name=settings.graph_name)
        adoption_store = PostgresAdoptionStore(pool, graph_name=settings.graph_name)
        confirmation_store = PostgresDecommissionConfirmationStore(pool, graph_name=settings.graph_name)
        regression_store = PostgresRegressionScheduleStore(pool, graph_name=settings.graph_name)

        suffix = new_ulid()[10:18].lower()
        site_name = f"RQA {suffix}"
        project_name = "Risk Core"
        site = await _write(
            writer, "Site", luid=f"s-{suffix}", name=site_name,
            licence_cost_annual=50000.0, licence_tier="User-based",
        )
        project = await _write(writer, "Project", luid=f"p-{suffix}", name=project_name)
        await _edge(writer, "CONTAINS", site, project)

        fixture_site = FixtureSite(name=site_name, workbooks=[], projects=[project_name])
        source_adapter = FixtureSourceAdapter([fixture_site])

        yield {
            "pool": pool, "settings": settings, "writer": writer, "artefact_store": artefact_store,
            "scope_store": scope_store, "promotion_store": promotion_store,
            "adoption_store": adoption_store, "confirmation_store": confirmation_store,
            "regression_store": regression_store, "source_adapter": source_adapter,
            "fixture_site": fixture_site, "site": site, "project": project,
            "site_name": site_name, "project_name": project_name,
        }
    finally:
        await pool.close()


def _service(estate: dict[str, Any], *, source_adapter: Any = None) -> G4CardService:
    return G4CardService(
        estate["pool"], graph_name=estate["settings"].graph_name, writer=estate["writer"],
        artefact_store=estate["artefact_store"], source_adapter=source_adapter or estate["source_adapter"],
        promotion_store=estate["promotion_store"], adoption_store=estate["adoption_store"],
        confirmation_store=estate["confirmation_store"], regression_store=estate["regression_store"],
        scope_store=estate["scope_store"],
    )


async def _workbook(estate: dict[str, Any], *, name: str, luid: str) -> str:
    writer = estate["writer"]
    book = await _write(writer, "Workbook", luid=luid, name=name, revision="1")
    await _edge(writer, "CONTAINS", estate["project"], book)
    estate["fixture_site"].workbooks.append(
        FixtureWorkbook(name=name, luid=luid, project=estate["project_name"])
    )
    return book


async def _release(estate: dict[str, Any], workbook_id: str, *, finished_at: datetime = _ELAPSED_FINISHED_AT) -> None:
    async with estate["pool"].acquire() as conn:
        await conn.execute(
            """INSERT INTO public.promotion_run
                (id, graph, workbook_id, to_stage, workspace, state, steps,
                 model_git_ref, report_deploy_id, approved_by, approver_role,
                 rationale, triggered_by, started_at, finished_at)
               VALUES ($1, $2, $3, 'prod', 'prod', 'SUCCEEDED', '[]'::jsonb,
                       'refs/heads/master', NULL, $4, 'programme_manager', NULL, $4, $5, $5)""",
            f"promotion_{new_ulid()}", estate["settings"].graph_name, workbook_id, PM_VALUE, finished_at,
        )


async def _meet_adoption_threshold(estate: dict[str, Any], workbook_id: str, *, meets: bool = True) -> None:
    await estate["adoption_store"].record(
        AdoptionSnapshot(
            id=f"adoption_{new_ulid()}", workbook_id=workbook_id, captured_at=datetime.now(UTC).isoformat(),
            source_views=100, target_views=90 if meets else 10, ratio=0.9 if meets else 0.1,
            threshold=0.8, meets_threshold=meets, triggered_by=PM_VALUE,
        )
    )


async def _confirm(estate: dict[str, Any], workbook_id: str) -> None:
    await estate["confirmation_store"].confirm(workbook_id, confirmed_by=REPORT_OWNER_VALUE)


async def _fail_regression(estate: dict[str, Any], workbook_id: str) -> None:
    schedule = RegressionSchedule(
        id=f"regsched_{new_ulid()}", workbook_id=workbook_id,
        cadence=Cadence(every_minutes=60), next_run_at=datetime.now(UTC).isoformat(),
    )
    await estate["regression_store"].create(schedule)
    await estate["regression_store"].record_run(
        schedule.id, run_id=f"run_{new_ulid()}", result="FAIL", error=None, finished_at=datetime.now(UTC),
    )


async def _ready_workbook(estate: dict[str, Any], *, name: str, luid: str) -> str:
    """Every real precondition met -- released, window elapsed, no regression schedule
    (honest green), adoption threshold met, owner confirmed."""
    workbook_id = await _workbook(estate, name=name, luid=luid)
    await _release(estate, workbook_id)
    await _meet_adoption_threshold(estate, workbook_id)
    await _confirm(estate, workbook_id)
    return workbook_id


# --------------------------------------------------------------------- readiness


async def test_readiness_is_fully_met_when_every_real_precondition_is_real(estate) -> None:
    await _ready_workbook(estate, name="Daily VaR", luid="wb-1")

    items = await readiness_checklist(
        estate["pool"], estate["settings"].graph_name, estate["promotion_store"], estate["adoption_store"],
        estate["confirmation_store"], estate["regression_store"], estate["scope_store"],
        site_id=estate["site"],
    )

    assert all(item.met for item in items)
    by_key = {item.key: item for item in items}
    assert by_key["all_released"].evidence == {"released": 1, "total": 1}
    assert by_key["regression_green"].evidence == {"checked": 0, "failing": 0}
    assert by_key["adoption_threshold_met"].evidence == {"meeting": 1, "released": 1}
    assert by_key["owner_confirmations_received"].evidence == {"confirmed": 1, "total": 1}


async def test_all_released_is_false_with_an_unreleased_workbook(estate) -> None:
    await _workbook(estate, name="Never Promoted", luid="wb-1")

    card = await g4_card(
        estate["pool"], estate["settings"].graph_name, estate["promotion_store"], estate["adoption_store"],
        estate["confirmation_store"], estate["regression_store"], estate["scope_store"],
        site_id=estate["site"],
    )

    checklist = {item["key"]: item for item in card["checklist"]}
    assert checklist["all_released"]["met"] is False
    assert checklist["all_released"]["evidence"] == {"released": 0, "total": 1}
    assert card["ready"] is False


async def test_all_released_ignores_a_withdrawn_workbook(estate) -> None:
    await _ready_workbook(estate, name="Daily VaR", luid="wb-1")
    withdrawn_id = await _workbook(estate, name="Withdrawn", luid="wb-2")
    await estate["scope_store"].decide(
        new_decision(
            workbook_id=withdrawn_id, kind=DecisionKind.WITHDRAW,
            reason="Duplicate workbook, withdrawn from scope.", decided_by=PM_VALUE,
        )
    )

    card = await g4_card(
        estate["pool"], estate["settings"].graph_name, estate["promotion_store"], estate["adoption_store"],
        estate["confirmation_store"], estate["regression_store"], estate["scope_store"],
        site_id=estate["site"],
    )

    assert card["ready"] is True
    assert len(card["mus"]) == 1


async def test_parallel_window_not_yet_elapsed(estate) -> None:
    workbook_id = await _workbook(estate, name="Daily VaR", luid="wb-1")
    await _release(estate, workbook_id, finished_at=_NOT_ELAPSED_FINISHED_AT)
    await _meet_adoption_threshold(estate, workbook_id)
    await _confirm(estate, workbook_id)

    items = await readiness_checklist(
        estate["pool"], estate["settings"].graph_name, estate["promotion_store"], estate["adoption_store"],
        estate["confirmation_store"], estate["regression_store"], estate["scope_store"],
        site_id=estate["site"],
    )

    by_key = {item.key: item for item in items}
    assert by_key["parallel_window_elapsed"].met is False
    assert by_key["parallel_window_elapsed"].evidence["window_start"] is not None


async def test_regression_green_is_false_with_a_real_failing_schedule(estate) -> None:
    workbook_id = await _ready_workbook(estate, name="Daily VaR", luid="wb-1")
    await _fail_regression(estate, workbook_id)

    items = await readiness_checklist(
        estate["pool"], estate["settings"].graph_name, estate["promotion_store"], estate["adoption_store"],
        estate["confirmation_store"], estate["regression_store"], estate["scope_store"],
        site_id=estate["site"],
    )

    by_key = {item.key: item for item in items}
    assert by_key["regression_green"].met is False
    assert by_key["regression_green"].evidence == {"checked": 1, "failing": 1}


async def test_adoption_threshold_not_met_with_no_snapshot(estate) -> None:
    workbook_id = await _workbook(estate, name="Daily VaR", luid="wb-1")
    await _release(estate, workbook_id)
    await _confirm(estate, workbook_id)

    items = await readiness_checklist(
        estate["pool"], estate["settings"].graph_name, estate["promotion_store"], estate["adoption_store"],
        estate["confirmation_store"], estate["regression_store"], estate["scope_store"],
        site_id=estate["site"],
    )

    by_key = {item.key: item for item in items}
    assert by_key["adoption_threshold_met"].met is False
    assert by_key["adoption_threshold_met"].evidence == {"meeting": 0, "released": 1}


async def test_owner_confirmations_not_received(estate) -> None:
    workbook_id = await _workbook(estate, name="Daily VaR", luid="wb-1")
    await _release(estate, workbook_id)
    await _meet_adoption_threshold(estate, workbook_id)

    items = await readiness_checklist(
        estate["pool"], estate["settings"].graph_name, estate["promotion_store"], estate["adoption_store"],
        estate["confirmation_store"], estate["regression_store"], estate["scope_store"],
        site_id=estate["site"],
    )

    by_key = {item.key: item for item in items}
    assert by_key["owner_confirmations_received"].met is False
    assert by_key["owner_confirmations_received"].evidence == {"confirmed": 0, "total": 1}


# --------------------------------------------------------------------------- the card


async def test_g4_card_lists_mus_licence_tier_and_confirmation_text(estate) -> None:
    await _ready_workbook(estate, name="Daily VaR", luid="wb-1")

    card = await g4_card(
        estate["pool"], estate["settings"].graph_name, estate["promotion_store"], estate["adoption_store"],
        estate["confirmation_store"], estate["regression_store"], estate["scope_store"],
        site_id=estate["site"],
    )

    assert card["name"] == estate["site_name"]
    assert card["licence_tier"] == "User-based"
    assert card["released_mu_count"] == 1
    assert [mu["name"] for mu in card["mus"]] == ["Daily VaR"]
    assert card["source_workbooks_to_archive"] == card["mus"]
    assert "Daily VaR" not in card["confirmation_text"] or estate["site_name"] in card["confirmation_text"]
    assert card["ready"] is True


async def test_g4_card_refuses_an_unknown_site(estate) -> None:
    with pytest.raises(ElementNotFoundError):
        await g4_card(
            estate["pool"], estate["settings"].graph_name, estate["promotion_store"], estate["adoption_store"],
            estate["confirmation_store"], estate["regression_store"], estate["scope_store"],
            site_id="no-such-site",
        )


# --------------------------------------------------------------------------- approve


async def test_approve_refuses_an_unready_site(estate) -> None:
    await _workbook(estate, name="Never Promoted", luid="wb-1")

    with pytest.raises(InvalidRequestError, match="not ready for G4"):
        await approve(
            estate["pool"], estate["settings"].graph_name, estate["writer"], estate["artefact_store"],
            estate["source_adapter"], estate["promotion_store"], estate["adoption_store"],
            estate["confirmation_store"], estate["regression_store"], estate["scope_store"],
            site_id=estate["site"], rationale="Site is fully ready for decommission.",
            countersigned_by=PM_VALUE, principal=LICENCE_ADMIN,
        )


async def test_approve_requires_a_real_rationale(estate) -> None:
    await _ready_workbook(estate, name="Daily VaR", luid="wb-1")

    with pytest.raises(InvalidRequestError, match="at least"):
        await approve(
            estate["pool"], estate["settings"].graph_name, estate["writer"], estate["artefact_store"],
            estate["source_adapter"], estate["promotion_store"], estate["adoption_store"],
            estate["confirmation_store"], estate["regression_store"], estate["scope_store"],
            site_id=estate["site"], rationale="too short", countersigned_by=PM_VALUE, principal=LICENCE_ADMIN,
        )


async def test_approve_requires_a_countersigner(estate) -> None:
    await _ready_workbook(estate, name="Daily VaR", luid="wb-1")

    with pytest.raises(InvalidRequestError, match="countersigning"):
        await approve(
            estate["pool"], estate["settings"].graph_name, estate["writer"], estate["artefact_store"],
            estate["source_adapter"], estate["promotion_store"], estate["adoption_store"],
            estate["confirmation_store"], estate["regression_store"], estate["scope_store"],
            site_id=estate["site"], rationale="Site is fully ready for decommission.",
            countersigned_by="   ", principal=LICENCE_ADMIN,
        )


async def test_approve_is_refused_without_archive_capability(estate) -> None:
    await _ready_workbook(estate, name="Daily VaR", luid="wb-1")
    no_archive_adapter = FixtureSourceAdapter(
        [estate["fixture_site"]], capabilities=Capabilities(usage=True, ownership=True, archive=False),
    )

    with pytest.raises(InvalidRequestError, match="cannot archive workbooks"):
        await approve(
            estate["pool"], estate["settings"].graph_name, estate["writer"], estate["artefact_store"],
            no_archive_adapter, estate["promotion_store"], estate["adoption_store"],
            estate["confirmation_store"], estate["regression_store"], estate["scope_store"],
            site_id=estate["site"], rationale="Site is fully ready for decommission.",
            countersigned_by=PM_VALUE, principal=LICENCE_ADMIN,
        )


async def test_approve_is_refused_honestly_when_the_source_no_longer_reports_the_site(estate) -> None:
    """A real bug found live: the source adapter's own `Scope(site=...)` enumeration can
    raise `AdapterError` for a site the graph knows about but the source no longer does
    -- `approve` must refuse cleanly (InvalidRequestError), never crash with an
    unhandled 500."""
    await _ready_workbook(estate, name="Daily VaR", luid="wb-1")
    stale_site_adapter = FixtureSourceAdapter([FixtureSite(name="a different site entirely", workbooks=[])])

    with pytest.raises(InvalidRequestError, match="no longer reports site"):
        await approve(
            estate["pool"], estate["settings"].graph_name, estate["writer"], estate["artefact_store"],
            stale_site_adapter, estate["promotion_store"], estate["adoption_store"],
            estate["confirmation_store"], estate["regression_store"], estate["scope_store"],
            site_id=estate["site"], rationale="Site is fully ready for decommission.",
            countersigned_by=PM_VALUE, principal=LICENCE_ADMIN,
        )


async def test_approve_archives_workbooks_and_records_the_site(estate) -> None:
    await _ready_workbook(estate, name="Daily VaR", luid="wb-1")

    result = await approve(
        estate["pool"], estate["settings"].graph_name, estate["writer"], estate["artefact_store"],
        estate["source_adapter"], estate["promotion_store"], estate["adoption_store"],
        estate["confirmation_store"], estate["regression_store"], estate["scope_store"],
        site_id=estate["site"], rationale="Site is fully ready for decommission.",
        countersigned_by=PM_VALUE, principal=LICENCE_ADMIN,
    )

    assert result.decision == "APPROVED"
    assert result.archived_count == 1
    assert result.licence_release_value == 50000.0
    assert result.decommissioned_at is not None

    # The real fixture workbook was really archived.
    assert estate["fixture_site"].workbooks[0].archived is True

    # The real Site node carries the real licence-release facts.
    async with estate["pool"].acquire() as conn:
        site = (await hydrate(conn, estate["settings"].graph_name, "Site", [estate["site"]]))[estate["site"]]
    assert _parsed(site["decommissioned_at"]) == _parsed(result.decommissioned_at)
    assert site["licence_release_value"] == 50000.0

    # A real GateDecision is now this site's own latest.
    card = await g4_card(
        estate["pool"], estate["settings"].graph_name, estate["promotion_store"], estate["adoption_store"],
        estate["confirmation_store"], estate["regression_store"], estate["scope_store"],
        site_id=estate["site"],
    )
    assert card["latest_decision"]["decision"] == "APPROVED"
    assert card["latest_decision"]["countersigner"] == PM_VALUE


async def test_approve_is_honest_about_a_site_with_no_known_licence_cost(estate) -> None:
    suffix = new_ulid()[10:18].lower()
    site_name = f"Unknown Cost {suffix}"
    site = await _write(estate["writer"], "Site", luid=f"s-{suffix}", name=site_name)
    project = await _write(estate["writer"], "Project", luid=f"p-{suffix}", name="Risk Core")
    await _edge(estate["writer"], "CONTAINS", site, project)
    fixture_site = FixtureSite(name=site_name, workbooks=[], projects=["Risk Core"])
    source_adapter = FixtureSourceAdapter([fixture_site])

    book = await _write(estate["writer"], "Workbook", luid="wb-nocost", name="Daily VaR", revision="1")
    await _edge(estate["writer"], "CONTAINS", project, book)
    fixture_site.workbooks.append(FixtureWorkbook(name="Daily VaR", luid="wb-nocost", project="Risk Core"))
    await _release(estate, book)
    await _meet_adoption_threshold(estate, book)
    await _confirm(estate, book)

    result = await approve(
        estate["pool"], estate["settings"].graph_name, estate["writer"], estate["artefact_store"],
        source_adapter, estate["promotion_store"], estate["adoption_store"],
        estate["confirmation_store"], estate["regression_store"], estate["scope_store"],
        site_id=site, rationale="Site is fully ready for decommission.",
        countersigned_by=PM_VALUE, principal=LICENCE_ADMIN,
    )

    assert result.licence_release_value is None


# ----------------------------------------------------------------------------- defer


async def test_defer_records_the_reason_and_target_date(estate) -> None:
    await _ready_workbook(estate, name="Daily VaR", luid="wb-1")
    target = (datetime.now(UTC) + timedelta(weeks=8)).isoformat()

    result = await defer(
        estate["pool"], estate["settings"].graph_name, estate["writer"],
        site_id=estate["site"], reason="Client asked for a later cutover date.",
        target_date=target, principal=LICENCE_ADMIN,
    )

    assert result.decision == "DEFERRED"
    assert result.target_date is not None

    card = await g4_card(
        estate["pool"], estate["settings"].graph_name, estate["promotion_store"], estate["adoption_store"],
        estate["confirmation_store"], estate["regression_store"], estate["scope_store"],
        site_id=estate["site"],
    )
    assert card["latest_decision"]["decision"] == "DEFERRED"
    assert _parsed(card["latest_decision"]["target_date"]) == _parsed(result.target_date)


async def test_defer_refuses_a_target_date_in_the_past(estate) -> None:
    with pytest.raises(InvalidRequestError, match="must be in the future"):
        await defer(
            estate["pool"], estate["settings"].graph_name, estate["writer"],
            site_id=estate["site"], reason="Client asked for a later cutover date.",
            target_date="2020-01-01T00:00:00Z", principal=LICENCE_ADMIN,
        )


# --------------------------------------------------------------------------- G4CardService


async def test_the_service_binds_card_approve_defer_and_confirm(estate) -> None:
    """The pre-bound `G4CardService` object real routes call -- the identical
    "app.state" shape `G3CardService`/`ReleaseService`/`AdoptionService` already take."""
    workbook_id = await _ready_workbook(estate, name="Daily VaR", luid="wb-1")
    service = _service(estate)

    card = await service.card(estate["site"])
    assert card["ready"] is True

    confirmation = await service.confirm(workbook_id, confirmed_by=REPORT_OWNER_VALUE)
    assert confirmation.workbook_id == workbook_id

    result = await service.approve(
        estate["site"], rationale="Site is fully ready for decommission.",
        countersigned_by=PM_VALUE, principal=LICENCE_ADMIN,
    )
    assert result.decision == "APPROVED"


async def test_the_service_defers_with_a_real_reason_and_target_date(estate) -> None:
    await _ready_workbook(estate, name="Daily VaR", luid="wb-1")
    service = _service(estate)
    target = (datetime.now(UTC) + timedelta(weeks=8)).isoformat()

    result = await service.defer(
        estate["site"], reason="Client asked for a later cutover date.",
        target_date=target, principal=LICENCE_ADMIN,
    )
    assert result.decision == "DEFERRED"


# --------------------------------------------------------------------------- confirm


async def test_confirm_decommission_overwrites_the_earlier_confirmation(estate) -> None:
    workbook_id = await _workbook(estate, name="Daily VaR", luid="wb-1")

    await estate["confirmation_store"].confirm(workbook_id, confirmed_by="user:first@client.example")
    await estate["confirmation_store"].confirm(workbook_id, confirmed_by="user:second@client.example")

    confirmations = await estate["confirmation_store"].for_workbooks([workbook_id])
    assert len(confirmations) == 1
    assert confirmations[workbook_id].confirmed_by == "user:second@client.example"
