"""Incremental scheduled harvest.

S1.2.4's three acceptance criteria: new revisions detected through the source's own
``updatedAt`` with only those workbooks re-parsed, a SOURCE_DRIFT event and a re-proof mark
when the changed workbook already has a Migration Unit in progress, and the schedule and
its last run visible on Platform Health.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from astra_graph.adapters.contract import Scope
from astra_graph.adapters.fixture import FixtureSite, FixtureSourceAdapter, FixtureWorkbook
from astra_graph.credentials import StaticCredentialProvider
from astra_graph.events import EventType
from astra_graph.harvest import (
    Cadence,
    Harvester,
    HarvestMode,
    HarvestRequest,
    HarvestScheduler,
    InMemoryHarvestStore,
    InMemoryScheduleStore,
    ScheduleError,
    new_schedule,
)
from astra_graph.migration_units import (
    IN_PROGRESS_STATES,
    InMemoryMigrationUnitRegistry,
    MigrationUnitRef,
    NullMigrationUnitRegistry,
)
from astra_graph.principal import Principal
from astra_graph.writes import GraphWriter

from .conftest import ARTIZENT_HEADERS
from .fakes import InMemoryGraphRepository

PRINCIPAL = Principal("agent:harvester", run_id="run-incremental")
CREDENTIALS = StaticCredentialProvider({"tableau/rqa": "a-token"})
GRAPH = "astra_estate_test"
NOW = datetime(2027, 3, 1, 1, 30, tzinfo=UTC)


def workbook(**kwargs) -> FixtureWorkbook:
    defaults = {
        "name": "Daily VaR",
        "luid": "wb-1",
        "project": "Risk Core",
        "sheets": 1,
        "dashboards": 1,
        "datasources": 1,
        "fields": 2,
        "calculations": 1,
        "updated_at": "2027-02-01T09:00:00.000Z",
    }
    defaults.update(kwargs)
    return FixtureWorkbook(**defaults)


def site(*workbooks: FixtureWorkbook) -> FixtureSite:
    return FixtureSite(name="rqa", workbooks=list(workbooks or (workbook(),)))


def build(fixture_site: FixtureSite, *, units=None):
    """A Harvester over one fixture site, keeping the adapter so fetches can be counted."""
    adapter = FixtureSourceAdapter([fixture_site])
    repository = InMemoryGraphRepository()
    harvester = Harvester(
        adapter=adapter,
        writer=GraphWriter(repository),
        store=InMemoryHarvestStore(),
        credentials=CREDENTIALS,
        graph_name=GRAPH,
        migration_units=units,
    )
    return harvester, adapter, repository


def request(mode: HarvestMode = HarvestMode.FULL, **kwargs) -> HarvestRequest:
    kwargs.setdefault("scope", Scope(site="rqa"))
    kwargs.setdefault("credential_reference", "tableau/rqa")
    return HarvestRequest(mode=mode, **kwargs)


# ------------------------------------------- criterion 1: detection through updatedAt


async def test_an_unchanged_workbook_is_not_even_fetched() -> None:
    """S1.2.4 criterion 1. The saving is the download that does not happen."""
    estate = site()
    harvester, adapter, _ = build(estate)

    await harvester.run(request(), principal=PRINCIPAL)
    assert adapter.fetches == 1

    second = await harvester.run(request(HarvestMode.INCREMENTAL), principal=PRINCIPAL)

    assert adapter.fetches == 1, "an incremental run fetched a workbook that had not moved"
    assert second.skipped_not_modified == 1
    assert second.parsed == 0


async def test_a_workbook_whose_updated_at_moved_is_re_parsed() -> None:
    estate = site()
    harvester, adapter, _ = build(estate)
    await harvester.run(request(), principal=PRINCIPAL)

    estate.workbooks[0].updated_at = "2027-02-14T11:00:00.000Z"
    estate.workbooks[0].revision = "2"
    estate.workbooks[0].calculations = 2  # so the content genuinely differs

    second = await harvester.run(request(HarvestMode.INCREMENTAL), principal=PRINCIPAL)

    assert adapter.fetches == 2
    assert second.parsed == 1
    assert second.skipped_not_modified == 0


async def test_only_the_changed_workbook_of_many_is_re_parsed() -> None:
    """"re-parses only those workbooks" — the word doing the work is *only*."""
    estate = site(
        workbook(name="A", luid="wb-a"),
        workbook(name="B", luid="wb-b"),
        workbook(name="C", luid="wb-c"),
    )
    harvester, adapter, _ = build(estate)
    await harvester.run(request(), principal=PRINCIPAL)
    assert adapter.fetches == 3

    estate.workbooks[1].updated_at = "2027-02-20T08:00:00.000Z"
    estate.workbooks[1].revision = "2"
    estate.workbooks[1].fields = 3

    second = await harvester.run(request(HarvestMode.INCREMENTAL), principal=PRINCIPAL)

    assert adapter.fetches == 4, "exactly one more download, for the one that changed"
    assert (second.parsed, second.skipped_not_modified) == (1, 2)


async def test_a_full_run_still_fetches_everything() -> None:
    """The default mode is unchanged: an operator asking by hand means "look properly"."""
    estate = site(workbook(luid="wb-a"), workbook(luid="wb-b"))
    harvester, adapter, _ = build(estate)
    await harvester.run(request(), principal=PRINCIPAL)

    second = await harvester.run(request(HarvestMode.FULL), principal=PRINCIPAL)

    assert adapter.fetches == 4
    assert second.skipped_unchanged == 2, "fetched, compared, and found identical"
    assert second.skipped_not_modified == 0


async def test_a_revision_change_defeats_an_unmoved_timestamp() -> None:
    """Belt and braces: a source that bumps the revision without the timestamp."""
    estate = site()
    harvester, adapter, _ = build(estate)
    await harvester.run(request(), principal=PRINCIPAL)

    estate.workbooks[0].revision = "2"
    estate.workbooks[0].calculations = 2

    second = await harvester.run(request(HarvestMode.INCREMENTAL), principal=PRINCIPAL)

    assert adapter.fetches == 2
    assert second.parsed == 1


async def test_a_source_that_reports_no_timestamp_is_always_fetched() -> None:
    """A source that cannot say when a workbook changed must not be guessed at."""
    estate = site(workbook(updated_at=""))
    harvester, adapter, _ = build(estate)
    await harvester.run(request(), principal=PRINCIPAL)

    second = await harvester.run(request(HarvestMode.INCREMENTAL), principal=PRINCIPAL)

    assert adapter.fetches == 2
    assert second.skipped_unchanged == 1, "fetched and compared, the only honest route"
    assert second.skipped_not_modified == 0


async def test_a_workbook_never_harvested_is_fetched_even_when_incremental() -> None:
    estate = site()
    harvester, adapter, _ = build(estate)

    first = await harvester.run(request(HarvestMode.INCREMENTAL), principal=PRINCIPAL)

    assert adapter.fetches == 1
    assert first.parsed == 1


async def test_a_new_grammar_re_parses_a_workbook_that_did_not_change() -> None:
    """Otherwise extending the grammar (S1.2.2) could never reach an unchanged workbook.

    A held workbook would stay held forever, and the Parse Quality Queue would never clear.
    """
    estate = site()
    harvester, adapter, _ = build(estate)
    await harvester.run(request(), principal=PRINCIPAL)

    adapter.grammar_version = "fixture-2"

    second = await harvester.run(request(HarvestMode.INCREMENTAL), principal=PRINCIPAL)

    assert adapter.fetches == 2
    assert second.parsed == 1


async def test_a_new_grammar_re_parses_on_a_full_run_too() -> None:
    """The content-hash skip has to respect the grammar as well as the bytes.

    A full run never consults ``updatedAt``, so without this an operator asking for a
    full re-harvest after extending the grammar would get a run that fetched every
    workbook and re-parsed none of them.
    """
    estate = site()
    harvester, adapter, _ = build(estate)
    await harvester.run(request(), principal=PRINCIPAL)

    adapter.grammar_version = "fixture-2"

    second = await harvester.run(request(HarvestMode.FULL), principal=PRINCIPAL)

    assert second.parsed == 1
    assert second.skipped_unchanged == 0


async def test_timestamps_are_compared_as_instants_not_as_text() -> None:
    """``...Z`` and ``...+00:00`` are the same moment; a string comparison disagrees."""
    estate = site(workbook(updated_at="2027-02-01T09:00:00.000Z"))
    harvester, adapter, _ = build(estate)
    await harvester.run(request(), principal=PRINCIPAL)

    estate.workbooks[0].updated_at = "2027-02-01T09:00:00+00:00"

    second = await harvester.run(request(HarvestMode.INCREMENTAL), principal=PRINCIPAL)

    assert adapter.fetches == 1
    assert second.skipped_not_modified == 1


# --------------------------------------------- criterion 2: SOURCE_DRIFT and re-proof


def unit(state: str = "PROVING", luid: str = "wb-1") -> InMemoryMigrationUnitRegistry:
    return InMemoryMigrationUnitRegistry(
        {("rqa", luid): MigrationUnitRef("mu_01HX7", state, "rqa", luid)}
    )


async def drifted(units, *, state_change=True):
    """Harvest once, change the workbook, harvest again. Returns (progress, repository)."""
    estate = site()
    harvester, _adapter, repository = build(estate, units=units)
    await harvester.run(request(), principal=PRINCIPAL)

    if state_change:
        estate.workbooks[0].revision = "2"
        estate.workbooks[0].calculations = 2

    progress = await harvester.run(request(HarvestMode.INCREMENTAL), principal=PRINCIPAL)
    return progress, repository


async def test_a_change_under_a_migration_unit_in_progress_raises_source_drift() -> None:
    """S1.2.4 criterion 2."""
    registry = unit("PROVING")
    progress, repository = await drifted(registry)

    notices = [e for e in repository.events if e.type is EventType.SOURCE_DRIFT]
    assert len(notices) == 1
    assert progress.drifted == 1

    detail = notices[0].data
    assert detail["migration_unit"] == {"id": "mu_01HX7", "state": "PROVING"}
    assert detail["previous"]["revision"] == "1"
    assert detail["current"]["revision"] == "2"
    assert detail["previous"]["content_hash"] != detail["current"]["content_hash"]
    assert detail["workbook_luid"] == "wb-1"


async def test_the_migration_unit_is_marked_for_re_proof() -> None:
    """The other half of criterion 2: E7 is asked to prove it again."""
    registry = unit("PROVING")
    await drifted(registry)

    assert len(registry.marked) == 1
    unit_id, reason, principal = registry.marked[0]
    assert unit_id == "mu_01HX7"
    assert "revision 1 -> 2" in reason
    assert principal == PRINCIPAL.value


async def test_the_notice_records_whether_the_mark_was_accepted() -> None:
    """A notice that says re-proof was requested when nothing accepted it would be a lie."""
    registry = unit("PROVING")
    _progress, repository = await drifted(registry)
    accepted = next(e for e in repository.events if e.type is EventType.SOURCE_DRIFT)
    assert accepted.data["reproof_requested"] is True


async def test_no_drift_without_a_migration_unit() -> None:
    """A changed workbook with nothing built from it is an ordinary re-parse."""
    progress, repository = await drifted(NullMigrationUnitRegistry())

    assert not [e for e in repository.events if e.type is EventType.SOURCE_DRIFT]
    assert progress.drifted == 0
    assert progress.parsed == 1, "and it is still re-parsed"


async def test_no_drift_when_the_unit_is_only_harvested() -> None:
    """At HARVESTED nothing has been built from the old version. The re-parse *is* the
    update, so interrupting somebody would be noise."""
    registry = unit("HARVESTED")
    progress, repository = await drifted(registry)

    assert not [e for e in repository.events if e.type is EventType.SOURCE_DRIFT]
    assert progress.drifted == 0
    assert registry.marked == []


async def test_no_drift_when_the_unit_is_withdrawn() -> None:
    registry = unit("WITHDRAWN")
    _progress, repository = await drifted(registry)
    assert not [e for e in repository.events if e.type is EventType.SOURCE_DRIFT]


async def test_a_released_unit_does_drift() -> None:
    """A source changing under a report already in production is the expensive case.

    The backlog has the Steward re-running parity "weekly during parallel run, on
    SOURCE_DRIFT", so RELEASED is deliberately in scope.
    """
    registry = unit("RELEASED")
    progress, _repository = await drifted(registry)
    assert progress.drifted == 1


async def test_no_drift_on_a_first_harvest() -> None:
    """Nothing has changed if the platform has never seen it."""
    registry = unit("PROVING")
    estate = site()
    harvester, _adapter, repository = build(estate, units=registry)

    await harvester.run(request(), principal=PRINCIPAL)

    assert not [e for e in repository.events if e.type is EventType.SOURCE_DRIFT]
    assert registry.marked == []


async def test_no_drift_when_the_content_is_identical() -> None:
    """A re-publish of the same file moves updatedAt without changing anything."""
    registry = unit("PROVING")
    estate = site()
    harvester, _adapter, repository = build(estate, units=registry)
    await harvester.run(request(), principal=PRINCIPAL)

    estate.workbooks[0].updated_at = "2027-02-28T09:00:00.000Z"

    progress = await harvester.run(request(HarvestMode.INCREMENTAL), principal=PRINCIPAL)

    assert progress.skipped_unchanged == 1, "fetched, because the timestamp moved"
    assert not [e for e in repository.events if e.type is EventType.SOURCE_DRIFT]


async def test_the_in_progress_states_are_the_state_machine_minus_three() -> None:
    """Spec §3.2. Stated as a set so a change to it is a decision, not a drift."""
    assert "HARVESTED" not in IN_PROGRESS_STATES
    assert "WITHDRAWN" not in IN_PROGRESS_STATES
    assert "DECOMMISSIONED" not in IN_PROGRESS_STATES
    assert {"CLUSTERED", "GENERATED", "PROVING", "ACCEPTED", "RELEASED"} <= IN_PROGRESS_STATES


async def test_a_notice_is_skipped_by_replay_rather_than_failing_it() -> None:
    """The stream stays replayable with notices in it (ADR 0003's property)."""
    from astra_graph.replay import replay

    registry = unit("PROVING")
    _progress, repository = await drifted(registry)

    target = InMemoryGraphRepository()
    result = await replay(repository, target)

    assert result.notices == 1
    assert result.nodes > 0


# ------------------------------------------------------------------- the scheduler


def scheduled(**kwargs):
    store = InMemoryScheduleStore()
    schedule = new_schedule(
        site="rqa",
        project=kwargs.pop("project", None),
        credential_reference="tableau/rqa",
        cadence=kwargs.pop("cadence", Cadence(daily_at="02:00")),
        created_by="user:p.eng@artizent.example",
        now=NOW,
    )
    return store, schedule


async def test_a_new_schedule_does_not_fire_immediately() -> None:
    """Setting up four sites should not start four full harvests by typing."""
    _store, schedule = scheduled()
    assert schedule.next_run_at == "2027-03-01T02:00:00.000Z"


async def test_a_daily_cadence_rolls_to_tomorrow_once_the_time_has_passed() -> None:
    cadence = Cadence(daily_at="02:00")
    assert cadence.next_after(datetime(2027, 3, 1, 3, 0, tzinfo=UTC)) == datetime(
        2027, 3, 2, 2, 0, tzinfo=UTC
    )


async def test_a_cadence_is_one_form_or_the_other() -> None:
    with pytest.raises(ScheduleError, match="exactly one"):
        Cadence(every_minutes=60, daily_at="02:00")
    with pytest.raises(ScheduleError, match="exactly one"):
        Cadence()


async def test_a_cadence_below_the_floor_is_refused() -> None:
    """A one-minute "schedule" is a busy loop against the client's Tableau server."""
    with pytest.raises(ScheduleError, match="every_minutes must be between"):
        Cadence(every_minutes=1)


async def test_a_daily_time_must_be_a_time() -> None:
    with pytest.raises(ScheduleError, match="HH:MM"):
        Cadence(daily_at="2am")


async def test_the_scheduler_starts_an_incremental_run_when_a_schedule_is_due() -> None:
    """S1.2.4's headline: it runs, and it runs incrementally."""
    store, schedule = scheduled(cadence=Cadence(every_minutes=60))
    await store.create(schedule)
    estate = site()
    harvester, adapter, _ = build(estate)
    await harvester.run(request(), principal=PRINCIPAL)
    assert adapter.fetches == 1

    scheduler = HarvestScheduler(store=store, harvester=harvester)
    started = await scheduler.tick(now=NOW + timedelta(hours=2))
    assert len(started) == 1
    await _settle(scheduler)

    assert adapter.fetches == 1, "the scheduled run was incremental"
    recorded = await store.get(schedule.id)
    assert recorded.last_run_id == started[0]
    assert recorded.last_run_state == "COMPLETED"

    # The link back: a run knows which schedule started it, so Platform Health does not
    # have to infer it from the principal.
    run = await harvester._store.get(started[0])
    assert run.schedule_id == schedule.id
    assert run.mode is HarvestMode.INCREMENTAL


async def test_a_schedule_that_is_not_due_does_not_fire() -> None:
    store, schedule = scheduled(cadence=Cadence(every_minutes=60))
    await store.create(schedule)
    harvester, _adapter, _ = build(site())

    scheduler = HarvestScheduler(store=store, harvester=harvester)

    assert await scheduler.tick(now=NOW) == []


async def test_claiming_advances_the_next_firing_so_a_second_poll_finds_nothing() -> None:
    """What stops two replicas — or two fast polls — starting the same run twice."""
    store, schedule = scheduled(cadence=Cadence(every_minutes=60))
    await store.create(schedule)
    later = NOW + timedelta(hours=2)

    first = await store.due(now=later)
    second = await store.due(now=later)

    assert [s.id for s in first] == [schedule.id]
    assert second == []


async def test_a_paused_schedule_does_not_fire() -> None:
    store, schedule = scheduled(cadence=Cadence(every_minutes=60))
    await store.create(schedule)
    await store.set_enabled(schedule.id, enabled=False, reason="change freeze until April")
    harvester, _adapter, _ = build(site())

    scheduler = HarvestScheduler(store=store, harvester=harvester)

    assert await scheduler.tick(now=NOW + timedelta(hours=2)) == []


async def test_a_failing_schedule_is_paused_rather_than_left_to_hammer_the_source() -> None:
    """Spec §12.3.1 alerts on scheduler starvation; this is the state it reads."""
    store, schedule = scheduled(cadence=Cadence(every_minutes=60))
    await store.create(schedule)
    harvester, _adapter, _ = build(site())
    scheduler = HarvestScheduler(store=store, harvester=harvester, max_consecutive_failures=2)

    for attempt in range(2):
        await store.record_run(
            schedule.id,
            run_id=f"run-{attempt}",
            state="FAILED",
            error="CredentialError: token expired",
            finished_at=NOW,
        )
    await scheduler._pause_if_persistently_failing(schedule.id)

    paused = await store.get(schedule.id)
    assert paused.enabled is False
    assert "token expired" in paused.paused_reason


async def test_a_completed_run_clears_the_failure_count() -> None:
    store, schedule = scheduled()
    await store.create(schedule)
    await store.record_run(
        schedule.id, run_id="a", state="FAILED", error="boom", finished_at=NOW
    )
    await store.record_run(
        schedule.id, run_id="b", state="COMPLETED", error=None, finished_at=NOW
    )

    assert (await store.get(schedule.id)).consecutive_failures == 0


async def test_one_scope_has_one_schedule() -> None:
    """Two schedules over a site would race each other for the same workbooks."""
    store, schedule = scheduled()
    await store.create(schedule)
    _other_store, duplicate = scheduled()

    with pytest.raises(ScheduleError, match="already exists"):
        await store.create(duplicate)


async def test_amending_a_cadence_re_bases_the_next_firing() -> None:
    """Otherwise a schedule changed from quarter-hourly to daily fires in eleven minutes."""
    store, schedule = scheduled(cadence=Cadence(every_minutes=15))
    await store.create(schedule)

    updated = await store.update(
        schedule.id,
        cadence=Cadence(daily_at="03:00"),
        credential_reference=None,
        now=NOW,
    )

    assert updated.next_run_at == "2027-03-01T03:00:00.000Z"


async def _settle(scheduler: HarvestScheduler) -> None:
    """Wait for the runs a tick started. Tests assert on outcomes, not on timing."""
    import asyncio

    for task in list(scheduler._running.values()):
        await asyncio.gather(task, return_exceptions=True)


# ------------------------------------------ criterion 3: visible on Platform Health


@pytest.fixture
def platform(client, repository):
    """The app with a schedule store and an adapter, as Platform Health expects."""
    from astra_graph.adapters.fixture import build_site
    from astra_graph.harvest import Harvester as _Harvester

    app = client._transport.app
    store = InMemoryScheduleStore()
    harvest_store = InMemoryHarvestStore()
    app.state.schedule_store = store
    app.state.harvest_store = harvest_store
    app.state.harvester = _Harvester(
        adapter=FixtureSourceAdapter([build_site("rqa", 2)]),
        writer=GraphWriter(repository),
        store=harvest_store,
        credentials=CREDENTIALS,
        graph_name=GRAPH,
    )
    app.state.migration_units = NullMigrationUnitRegistry()
    return client, store, app


async def test_platform_health_shows_the_schedule_and_its_last_run(platform) -> None:
    """S1.2.4 criterion 3."""
    client, store, _app = platform
    schedule = new_schedule(
        site="rqa",
        project=None,
        credential_reference="tableau/rqa",
        cadence=Cadence(daily_at="02:00"),
        created_by="user:p.eng@artizent.example",
        now=NOW,
    )
    await store.create(schedule)
    await store.record_run(
        schedule.id,
        run_id="01M1HAWSYBZRR3KB10KX3THJ0W",
        state="COMPLETED",
        error=None,
        finished_at=NOW,
    )

    response = await client.get("/v1/platform/health", headers=ARTIZENT_HEADERS)

    assert response.status_code == 200
    body = response.json()
    assert body["schedules"]["count"] == 1
    entry = body["schedules"]["entries"][0]
    assert entry["site"] == "rqa"
    assert entry["cadence_description"] == "daily at 02:00 UTC"
    assert entry["next_run_at"] == "2027-03-01T02:00:00.000Z"
    assert entry["last_run"]["id"] == "01M1HAWSYBZRR3KB10KX3THJ0W"
    assert entry["last_run"]["state"] == "COMPLETED"


async def test_platform_health_names_the_adapter_and_the_seams(platform) -> None:
    """A deployment that resolves nothing should say so, not look healthy and empty."""
    client, _store, _app = platform

    body = (await client.get("/v1/platform/health", headers=ARTIZENT_HEADERS)).json()

    assert body["adapter"]["enabled"] is True
    assert body["adapter"]["name"] == "fixture"
    assert body["migration_units"] == "none"
    assert body["scheduler"]["running"] is False


async def test_a_running_scheduler_reports_itself_as_running(platform) -> None:
    """The flag and the list of in-flight runs must not share a name.

    An empty ``running`` list under the key that means "is the scheduler alive" reads as a
    dead scheduler to anyone scanning Platform Health.
    """
    client, store, app = platform
    app.state.scheduler = HarvestScheduler(store=store, harvester=app.state.harvester)

    body = (await client.get("/v1/platform/health", headers=ARTIZENT_HEADERS)).json()

    assert body["scheduler"]["running"] is True
    assert body["scheduler"]["running_schedules"] == []


async def test_platform_health_reports_an_absent_adapter_rather_than_failing(client) -> None:
    client._transport.app.state.schedule_store = InMemoryScheduleStore()
    client._transport.app.state.harvest_store = InMemoryHarvestStore()

    response = await client.get("/v1/platform/health", headers=ARTIZENT_HEADERS)

    assert response.status_code == 200
    assert response.json()["adapter"]["enabled"] is False


async def test_platform_health_lists_recent_source_drift(platform) -> None:
    client, _store, app = platform
    registry = unit("PROVING")
    estate = site()
    harvester = Harvester(
        adapter=FixtureSourceAdapter([estate]),
        writer=GraphWriter(app.state.repository),
        store=app.state.harvest_store,
        credentials=CREDENTIALS,
        graph_name=GRAPH,
        migration_units=registry,
    )
    await harvester.run(request(), principal=PRINCIPAL)
    estate.workbooks[0].revision = "2"
    estate.workbooks[0].calculations = 2
    await harvester.run(request(HarvestMode.INCREMENTAL), principal=PRINCIPAL)

    body = (await client.get("/v1/platform/health", headers=ARTIZENT_HEADERS)).json()

    assert body["source_drift"]["count"] == 1
    assert body["source_drift"]["recent"][0]["migration_unit"]["id"] == "mu_01HX7"
    assert body["source_drift"]["recent"][0]["reproof_requested"] is True


async def test_a_run_reports_the_mode_it_ran_in(platform) -> None:
    """Otherwise a nightly run and a full re-harvest are indistinguishable afterwards."""
    client, _store, app = platform
    harvester = app.state.harvester
    await harvester.run(request(HarvestMode.INCREMENTAL), principal=PRINCIPAL)

    body = (await client.get("/v1/platform/health", headers=ARTIZENT_HEADERS)).json()

    assert body["harvests"]["recent"][0]["mode"] == "INCREMENTAL"


# ------------------------------------------------------------------ the API surface


async def test_a_schedule_can_be_created_paused_and_resumed_over_http(platform) -> None:
    client, _store, _app = platform

    created = await client.post(
        "/v1/harvest-schedules",
        json={
            "site": "rqa",
            "credential": "tableau/rqa",
            "cadence": {"daily_at": "02:00"},
        },
        headers=ARTIZENT_HEADERS,
    )
    assert created.status_code == 201
    schedule_id = created.json()["id"]
    assert created.json()["enabled"] is True

    paused = await client.post(
        f"/v1/harvest-schedules/{schedule_id}:pause",
        json={"reason": "RQA is in a change freeze until 14 April"},
        headers=ARTIZENT_HEADERS,
    )
    assert paused.status_code == 200
    assert paused.json()["enabled"] is False
    assert "change freeze" in paused.json()["paused_reason"]
    assert paused.json()["next_run_at"] is None, "a paused schedule has no next run"

    resumed = await client.post(
        f"/v1/harvest-schedules/{schedule_id}:resume", headers=ARTIZENT_HEADERS
    )
    assert resumed.json()["enabled"] is True
    assert resumed.json()["paused_reason"] is None


async def test_a_pause_needs_a_reason(platform) -> None:
    client, _store, _app = platform
    created = await client.post(
        "/v1/harvest-schedules",
        json={"site": "rqa", "credential": "tableau/rqa", "cadence": {"every_minutes": 60}},
        headers=ARTIZENT_HEADERS,
    )
    schedule_id = created.json()["id"]

    response = await client.post(
        f"/v1/harvest-schedules/{schedule_id}:pause", json={}, headers=ARTIZENT_HEADERS
    )

    assert response.status_code == 422


async def test_a_schedule_never_carries_a_secret(platform) -> None:
    """The rule the whole service keeps: a reference over the API, never a credential."""
    client, _store, _app = platform

    response = await client.post(
        "/v1/harvest-schedules",
        json={
            "site": "rqa",
            "credential": "a-personal-access-token-value",
            "cadence": {"daily_at": "02:00"},
        },
        headers=ARTIZENT_HEADERS,
    )

    assert response.status_code == 400
    assert response.json()["error"] == "invalid_request"
    assert "<system>/<name>" in response.json()["message"]


async def test_a_cadence_of_both_forms_is_refused_over_http(platform) -> None:
    client, _store, _app = platform

    response = await client.post(
        "/v1/harvest-schedules",
        json={
            "site": "rqa",
            "credential": "tableau/rqa",
            "cadence": {"daily_at": "02:00", "every_minutes": 60},
        },
        headers=ARTIZENT_HEADERS,
    )

    assert response.status_code == 422


async def test_a_missing_schedule_is_a_404_not_a_500(platform) -> None:
    client, _store, _app = platform

    response = await client.post(
        "/v1/harvest-schedules/01ARZ3NDEKTSV4RRFFQ69G5FAV:resume", headers=ARTIZENT_HEADERS
    )

    assert response.status_code == 404
