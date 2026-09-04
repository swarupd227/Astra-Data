"""Parse quality against a real PostgreSQL + Apache AGE.

What only the real store can answer: that the score lands on the Workbook node in the
graph, that the queue's grouping SQL agrees with the in-memory implementation the unit
suite asserts against, and that accepting a construct re-scores without touching the
source.
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

from astra_graph.adapters.conformance import (  # noqa: E402
    AdapterBuild,
    PostgresConformanceStore,
    PromotionError,
)
from astra_graph.adapters.contract import Scope  # noqa: E402
from astra_graph.adapters.fixture import FixtureSourceAdapter, build_site  # noqa: E402
from astra_graph.config import Settings  # noqa: E402
from astra_graph.credentials import StaticCredentialProvider  # noqa: E402
from astra_graph.events import source_for  # noqa: E402
from astra_graph.grammar import (  # noqa: E402
    GrammarIssueError,
    IssueState,
    LocalIssueTracker,
    PostgresIssueStore,
    new_issue,
)
from astra_graph.graph import AgeGraphRepository, create_pool  # noqa: E402
from astra_graph.graph.queries import accessor  # noqa: E402
from astra_graph.harvest import (  # noqa: E402
    Harvester,
    HarvestRequest,
    PostgresHarvestStore,
    PostgresParseQualityStore,
    Rescorer,
)
from astra_graph.ids import new_ulid  # noqa: E402
from astra_graph.migrations import run as run_migrations  # noqa: E402
from astra_graph.ontology import EDGE_LABELS, NODE_LABELS  # noqa: E402
from astra_graph.principal import Principal  # noqa: E402
from astra_graph.writes import GraphWriter  # noqa: E402

PRINCIPAL = Principal("agent:harvester", run_id="run-integration")
ENGINEER = Principal("user:p.eng@artizent.example", run_id="run-review")
CREDENTIALS = StaticCredentialProvider({"tableau/rqa": "a-token"})
THRESHOLD = 0.98


def constructs() -> tuple[str, str]:
    """A construct pair unique to one test.

    The module shares a graph, and a decision to accept a construct is deliberately
    carried forward by construct *text* across re-parses and workbooks, so two tests
    sharing a text would see each other's decisions.

    The suffix is the ULID's *tail*, not its head: a ULID begins with 48 bits of
    millisecond timestamp, so ``new_ulid()[:8]`` is the same string for every call within
    roughly a quarter-second — which, in a suite this fast, is most of it. The tail is the
    random half.
    """
    suffix = new_ulid()[-8:]
    return f"RAWSQL_INT({suffix})", f"SCRIPT_REAL({suffix})"


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
    config = _settings(f"astra_quality_{new_ulid()[-12:].lower()}")

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
                "public.parse_construct",
                "public.harvest_workbook",
                "public.estate_edge_index",
                "public.estate_element_index",
            ):
                await conn.execute(f"DELETE FROM {table} WHERE graph = $1", config.graph_name)
            await conn.execute(
                "DELETE FROM public.harvest_run WHERE graph = $1", config.graph_name
            )
            await conn.execute(
                "DELETE FROM public.grammar_issue WHERE graph = $1", config.graph_name
            )
            await conn.execute("SELECT ag_catalog.drop_graph($1, true)", config.graph_name)
        finally:
            await conn.close()

    if not _run_off_loop(setup):
        pytest.skip("PostgreSQL with Apache AGE not reachable")

    yield config
    _run_off_loop(teardown)


@pytest.fixture
async def stack(settings: Settings):
    """Harvester, repository, quality store and rescorer over the module's graph."""
    pool = await create_pool(settings)
    try:
        repository = AgeGraphRepository(pool, graph_name=settings.graph_name)
        writer = GraphWriter(repository, event_source=source_for(settings.graph_name))
        harvest_store = PostgresHarvestStore(pool, graph_name=settings.graph_name)
        quality = PostgresParseQualityStore(pool)

        def build(site) -> Harvester:
            return Harvester(
                adapter=FixtureSourceAdapter([site]),
                writer=writer,
                store=harvest_store,
                credentials=CREDENTIALS,
                graph_name=settings.graph_name,
                quality=quality,
            )

        rescorer = Rescorer(
            quality=quality,
            counts=harvest_store,
            writer=writer,
            graph_name=settings.graph_name,
            threshold=THRESHOLD,
        )
        yield build, repository, quality, rescorer, settings.graph_name
    finally:
        await pool.close()


def _request(site: str) -> HarvestRequest:
    return HarvestRequest(
        scope=Scope(site=site),
        credential_reference="tableau/rqa",
        parse_quality_threshold=THRESHOLD,
    )


async def test_parse_quality_lands_on_the_workbook_node(stack) -> None:
    """S1.2.2 criterion 1, in the graph rather than only in the harvest record."""
    build, repository, _, _, _ = stack
    rawsql, script = constructs()
    name = f"s{new_ulid()[-8:].lower()}"
    site = build_site(name, 2)
    site.workbooks[0].unrecognised = (rawsql,)

    await build(site).run(_request(name), principal=PRINCIPAL)

    held = await repository.get_node_by_luid("Workbook", site.workbooks[0].luid)
    clean = await repository.get_node_by_luid("Workbook", site.workbooks[1].luid)
    assert held.properties["parse_quality"] < THRESHOLD
    assert clean.properties["parse_quality"] == 1.0


async def test_constructs_are_stored_with_their_location(stack) -> None:
    """S1.2.2 criterion 2."""
    build, _, quality, _, graph = stack
    rawsql, script = constructs()
    name = f"s{new_ulid()[-8:].lower()}"
    site = build_site(name, 1)
    site.workbooks[0].unrecognised = (rawsql, script)

    await build(site).run(_request(name), principal=PRINCIPAL)

    stored = await quality.constructs_for(graph, name, site.workbooks[0].luid)
    assert {c.construct for c in stored} == {rawsql, script}
    assert all(c.unrecognised for c in stored)
    assert all(c.sheet and c.field for c in stored)


async def test_the_queue_and_the_grouping_agree_with_the_unit_suite(stack) -> None:
    """S1.2.2 criterion 3, and the SQL grouping the console orders work by."""
    build, _, quality, _, graph = stack
    rawsql, script = constructs()
    name = f"s{new_ulid()[-8:].lower()}"
    site = build_site(name, 5)
    for workbook in site.workbooks[:3]:
        workbook.unrecognised = (rawsql,)
    site.workbooks[3].unrecognised = (script,)

    progress = await build(site).run(_request(name), principal=PRINCIPAL)
    assert progress.held == 4
    assert progress.parsed == 1

    # Scoped to this test's own site: the graph is shared across the module.
    held = [
        item
        for item in await quality.held(graph, threshold=THRESHOLD, limit=10_000)
        if item.site == name
    ]
    assert len(held) == 4
    assert all(item.parse_quality < THRESHOLD for item in held)

    groups = {
        g.construct: g
        for g in await quality.construct_groups(graph, threshold=THRESHOLD, limit=10_000)
    }
    assert groups[rawsql].workbooks == 3
    assert groups[rawsql].workbooks_held == 3, "fixing this releases three workbooks"
    assert groups[script].workbooks_held == 1
    assert name in groups[rawsql].sites


async def test_accepting_a_construct_rescores_the_graph(stack) -> None:
    """S1.2.2 criterion 4, without touching the source."""
    build, repository, quality, rescorer, graph = stack
    rawsql, script = constructs()
    name = f"s{new_ulid()[-8:].lower()}"
    site = build_site(name, 3)
    for workbook in site.workbooks[:2]:
        workbook.unrecognised = (rawsql,)

    harvester = build(site)
    await harvester.run(_request(name), principal=PRINCIPAL)
    fetches = harvester._adapter.fetches

    affected = await quality.mark_ignorable(
        graph, rawsql, reason="Redesigned per Appendix B", principal=ENGINEER.value, site=name
    )
    assert len(affected) == 2

    result = await rescorer.rescore(affected, principal=ENGINEER)
    assert len(result.released) == 2
    assert harvester._adapter.fetches == fetches, "re-scoring must not re-fetch"

    node = await repository.get_node_by_luid("Workbook", site.workbooks[0].luid)
    assert node.properties["parse_quality"] == 1.0
    assert not [
        item
        for item in await quality.held(graph, threshold=THRESHOLD, limit=10_000)
        if item.site == name
    ]

    stored = await quality.constructs_for(graph, name, site.workbooks[0].luid)
    assert stored[0].unrecognised is False
    assert stored[0].decided_by == ENGINEER.value
    assert stored[0].ignorable_reason == "Redesigned per Appendix B"


async def test_a_rescore_preserves_creation_attribution(stack) -> None:
    """An upsert changes a node; it does not create it again."""
    build, repository, quality, rescorer, graph = stack
    rawsql, script = constructs()
    name = f"s{new_ulid()[-8:].lower()}"
    site = build_site(name, 1)
    site.workbooks[0].unrecognised = (rawsql,)

    await build(site).run(_request(name), principal=PRINCIPAL)
    created = await repository.get_node_by_luid("Workbook", site.workbooks[0].luid)
    assert created.properties["created_by"] == PRINCIPAL.value
    assert "updated_by" not in created.properties

    affected = await quality.mark_ignorable(
        graph, rawsql, reason="Redesigned per Appendix B", principal=ENGINEER.value, site=name
    )
    await rescorer.rescore(affected, principal=ENGINEER)

    updated = await repository.get_node_by_luid("Workbook", site.workbooks[0].luid)
    assert updated.properties["created_by"] == PRINCIPAL.value, "creation must not move"
    assert updated.properties["created_at"] == created.properties["created_at"]
    assert updated.properties["updated_by"] == ENGINEER.value
    assert updated.properties["parse_quality"] == 1.0


async def test_a_decision_survives_a_re_parse(stack) -> None:
    build, _, quality, rescorer, graph = stack
    rawsql, script = constructs()
    name = f"s{new_ulid()[-8:].lower()}"
    site = build_site(name, 1)
    site.workbooks[0].unrecognised = (rawsql,)
    harvester = build(site)

    await harvester.run(_request(name), principal=PRINCIPAL)
    affected = await quality.mark_ignorable(
        graph, rawsql, reason="Redesigned per Appendix B", principal=ENGINEER.value, site=name
    )
    await rescorer.rescore(affected, principal=ENGINEER)

    site.workbooks[0].name = "Renamed"
    site.workbooks[0].revision = "2"
    progress = await harvester.run(_request(name), principal=PRINCIPAL)

    assert progress.parsed == 1
    assert progress.held == 0, "an accepted construct must not hold the workbook again"
    stored = await quality.constructs_for(graph, name, site.workbooks[0].luid)
    assert stored[0].unrecognised is False
    assert stored[0].decided_by == ENGINEER.value


# ---------------------------------------------------------------- grammar issues


@pytest.fixture
async def issues(settings: Settings):
    """The issue store over the module's graph, on its own pool.

    Its own pool rather than the ``stack`` tuple's so the existing tests keep unpacking
    five values.
    """
    pool = await create_pool(settings)
    try:
        yield PostgresIssueStore(pool, graph_name=settings.graph_name)
    finally:
        await pool.close()


async def test_an_issue_carries_the_construct_and_where_it_was_found(stack, issues) -> None:
    """S1.4.3's second action: a ticket with the construct text and its locations.

    The locations come from the quality store's real grouping SQL, not a hand-built list —
    that join is the part only PostgreSQL can be asked about.
    """
    build, _, quality, _, graph = stack
    rawsql, _script = constructs()
    name = f"s{new_ulid()[-8:].lower()}"
    site = build_site(name, 4)
    for workbook in site.workbooks[:3]:
        workbook.unrecognised = (rawsql,)

    await build(site).run(_request(name), principal=PRINCIPAL)

    where = await quality.occurrences_of(graph, rawsql)
    assert len(where) == 3
    assert all(item.sheet and item.field for item in where)

    opened = await issues.open(
        new_issue(
            construct=rawsql,
            summary="RAWSQL in a calculated field",
            detail="Parse it as an opaque expression and classify it C4.",
            opened_by=ENGINEER.value,
            locations=[item.as_dict() for item in where],
            occurrences=len(where),
            workbooks_held=3,
        )
    )

    stored = await issues.get(opened.id)
    assert stored is not None
    assert stored.construct == rawsql
    assert stored.state is IssueState.OPEN
    assert stored.opened_by == ENGINEER.value
    assert len(stored.locations) == 3
    assert {location["workbook_luid"] for location in stored.locations} == {
        workbook.luid for workbook in site.workbooks[:3]
    }


async def test_only_one_issue_can_be_open_for_a_construct(issues) -> None:
    """The partial unique index, which only a real database enforces.

    A second issue is not a second problem; it is two people raising the same one, and the
    queue would then show the construct as blocked twice.
    """
    rawsql, _script = constructs()

    await issues.open(
        new_issue(
            construct=rawsql,
            summary="",
            detail="Parse it as an opaque expression.",
            opened_by=ENGINEER.value,
        )
    )

    with pytest.raises(GrammarIssueError, match="already open"):
        await issues.open(
            new_issue(
                construct=rawsql,
                summary="",
                detail="Someone else raising the same gap.",
                opened_by="user:other@artizent.example",
            )
        )


async def test_resolving_frees_the_construct_to_be_raised_again(issues) -> None:
    """A grammar gap can come back: a later version can stop reading it again."""
    rawsql, _script = constructs()
    first = await issues.open(
        new_issue(
            construct=rawsql,
            summary="",
            detail="Parse it as an opaque expression.",
            opened_by=ENGINEER.value,
        )
    )

    resolved = await issues.resolve(
        first.id,
        state=IssueState.RESOLVED,
        resolution="Grammar 1.4 reads it.",
        resolved_by=ENGINEER.value,
    )
    assert resolved is not None
    assert resolved.state is IssueState.RESOLVED
    assert resolved.resolved_by == ENGINEER.value
    assert not resolved.state.active

    again = await issues.open(
        new_issue(
            construct=rawsql,
            summary="",
            detail="Grammar 1.5 stopped reading it again.",
            opened_by=ENGINEER.value,
        )
    )
    assert again.id != first.id

    # And resolving an already-resolved issue is not a second resolution.
    assert (
        await issues.resolve(
            first.id,
            state=IssueState.WONT_FIX,
            resolution="Changed my mind.",
            resolved_by=ENGINEER.value,
        )
        is None
    )


async def test_the_queue_shows_a_construct_as_raised(stack, issues) -> None:
    """What the console reads per row: is this one already someone's work?"""
    build, _, quality, _, graph = stack
    rawsql, script = constructs()
    name = f"s{new_ulid()[-8:].lower()}"
    site = build_site(name, 2)
    site.workbooks[0].unrecognised = (rawsql,)
    site.workbooks[1].unrecognised = (script,)
    await build(site).run(_request(name), principal=PRINCIPAL)

    assert rawsql not in await issues.by_construct()

    await issues.open(
        new_issue(
            construct=rawsql,
            summary="",
            detail="Parse it as an opaque expression.",
            opened_by=ENGINEER.value,
        )
    )

    raised = await issues.by_construct()
    assert raised[rawsql].state is IssueState.OPEN
    assert script not in raised, "an issue on one construct is not an issue on the estate"

    groups = {
        group.construct
        for group in await quality.construct_groups(graph, threshold=THRESHOLD, limit=10_000)
    }
    assert {rawsql, script} <= groups, "raising an issue does not take it off the queue"


async def test_no_tracker_configured_is_reported_rather_than_faked(issues) -> None:
    """§21 makes work tracking optional, so the platform must hold the issue itself.

    ``LocalIssueTracker`` mirrors nothing and says so; E12 is where a real tracker lands.
    An empty ``external_ref`` therefore means "nobody was asked", not "asked and refused".
    """
    rawsql, _script = constructs()
    tracker = LocalIssueTracker()
    assert tracker.kind == "local"

    opened = await issues.open(
        new_issue(
            construct=rawsql,
            summary="",
            detail="Parse it as an opaque expression.",
            opened_by=ENGINEER.value,
        )
    )
    ref, url = await tracker.mirror(opened)

    assert (ref, url) == (None, None) or (ref, url) == (None, None)
    stored = await issues.get(opened.id)
    assert stored is not None
    assert stored.external_ref is None
    assert stored.external_url is None



# ------------------------------------------------- adapter promotion (S2.1.2)


@pytest.fixture
async def conformance(settings: Settings):
    """The conformance store over the module's graph, on its own pool."""
    pool = await create_pool(settings)
    try:
        yield PostgresConformanceStore(pool, graph_name=settings.graph_name)
    finally:
        await pool.close()


def _report(*, version: str, passed: bool = True, adapter: str = "tableau") -> dict:
    checks = [{"name": "throttling", "outcome": "PASSED" if passed else "FAILED",
               "summary": "backs off" if passed else "gave up on a 429", "detail": []}]
    return {
        "report": {
            "adapter": adapter,
            "adapter_version": version,
            "interface_version": "1.0",
            "grammar_version": "tableau-3",
            "corpus": "tableau/acme-sample",
            "passed": passed,
            "counts": {"PASSED": 1 if passed else 0, "FAILED": 0 if passed else 1, "SKIPPED": 0},
            "checks": checks,
        },
        "content_hash": "sha256:" + "c" * 64,
        "signed": True,
        "signature": "d" * 64,
        "algorithm": "hmac-sha256",
        "key_id": "env",
    }


async def test_a_report_survives_the_database_whole(conformance) -> None:
    """The jsonb round trip, which only a real database can be asked about — and the reason
    the whole report is stored rather than a verdict."""
    name = f"tableau-{new_ulid()[-8:].lower()}"
    stored = await conformance.record(_report(version="1.4.0", adapter=name), principal=ENGINEER.value)

    read = await conformance.get(stored.id)
    assert read is not None
    assert read.report == stored.report
    assert read.report["checks"][0]["summary"] == "backs off"
    assert read.signed and read.algorithm == "hmac-sha256"
    assert read.recorded_at is not None


async def test_only_one_build_is_promoted_at_a_time(conformance) -> None:
    """The partial unique index, which only a real database enforces. Two promoted builds
    would mean the platform could not say which adapter it is running."""
    name = f"tableau-{new_ulid()[-8:].lower()}"
    await conformance.record(_report(version="1.3.0", adapter=name), principal=ENGINEER.value)
    await conformance.record(_report(version="1.4.0", adapter=name), principal=ENGINEER.value)

    first = await conformance.promote(
        AdapterBuild(name, "1.3.0", "1.0", "tableau-3"),
        reason="the pilot build",
        principal=ENGINEER.value,
    )
    second = await conformance.promote(
        AdapterBuild(name, "1.4.0", "1.0", "tableau-3"),
        reason="upgraded after the grammar fix",
        principal=ENGINEER.value,
    )

    assert first.id != second.id
    active = [p for p in await conformance.promotions() if p.build.name == name]
    assert len(active) == 1
    assert active[0].build.version == "1.4.0"


async def test_a_failing_report_blocks_promotion_against_the_real_store(conformance) -> None:
    """S2.1.2 criterion 3, through the SQL that finds the report."""
    name = f"tableau-{new_ulid()[-8:].lower()}"
    await conformance.record(
        _report(version="1.4.0", passed=False, adapter=name), principal=ENGINEER.value
    )

    with pytest.raises(PromotionError, match="blocks promotion"):
        await conformance.promote(
            AdapterBuild(name, "1.4.0", "1.0", "tableau-3"),
            reason="the client is waiting",
            principal=ENGINEER.value,
        )

    assert await conformance.promotion(name) is None


async def test_the_passing_report_is_matched_on_the_whole_build(conformance) -> None:
    """Matched in SQL, including a NULL grammar version — `= NULL` is never true, and a
    build with no grammar would otherwise be unpromotable for a reason nobody could see."""
    name = f"tableau-{new_ulid()[-8:].lower()}"
    without = dict(_report(version="2.0.0", adapter=name))
    without["report"] = {**without["report"], "grammar_version": None}
    await conformance.record(without, principal=ENGINEER.value)

    found = await conformance.passing_for(AdapterBuild(name, "2.0.0", "1.0", None))
    assert found is not None
    assert found.build.grammar_version is None
    assert await conformance.passing_for(AdapterBuild(name, "2.0.0", "1.0", "tableau-3")) is None
