"""Parse quality, unrecognised constructs, and re-scoring.

S1.2.2's four acceptance criteria.
"""

from __future__ import annotations

import pytest

from astra_graph.adapters.contract import Scope
from astra_graph.adapters.fixture import FixtureSourceAdapter, build_site
from astra_graph.credentials import StaticCredentialProvider
from astra_graph.harvest import (
    Harvester,
    HarvestRequest,
    InMemoryHarvestStore,
    InMemoryParseQualityStore,
    Rescorer,
    WorkbookOutcome,
    derive_id,
    score,
)
from astra_graph.principal import Principal
from astra_graph.writes import GraphWriter

from .conftest import ARTIZENT_HEADERS
from .fakes import InMemoryGraphRepository

PRINCIPAL = Principal("agent:harvester", run_id="run-quality")
ENGINEER = Principal("user:p.eng@artizent.example", run_id="run-review")
CREDENTIALS = StaticCredentialProvider({"tableau/rqa": "a-token"})
GRAPH = "astra_estate_test"
THRESHOLD = 0.98

RAWSQL = "RAWSQL_INT(...)"
SCRIPT = "SCRIPT_REAL(...)"


def build(site, *, repository=None):
    repository = repository or InMemoryGraphRepository()
    harvest_store = InMemoryHarvestStore()
    quality = InMemoryParseQualityStore(harvest_store)
    writer = GraphWriter(repository)
    harvester = Harvester(
        adapter=FixtureSourceAdapter([site]),
        writer=writer,
        store=harvest_store,
        credentials=CREDENTIALS,
        graph_name=GRAPH,
        quality=quality,
    )
    rescorer = Rescorer(
        quality=quality,
        counts=harvest_store,
        writer=writer,
        graph_name=GRAPH,
        threshold=THRESHOLD,
    )
    return harvester, repository, harvest_store, quality, rescorer


def request(**kwargs) -> HarvestRequest:
    kwargs.setdefault("scope", Scope(site="rqa"))
    kwargs.setdefault("credential_reference", "tableau/rqa")
    kwargs.setdefault("parse_quality_threshold", THRESHOLD)
    return HarvestRequest(**kwargs)


def workbook_node(repository, luid: str) -> dict:
    for record in repository.nodes.values():
        if record["label"] == "Workbook" and record["properties"].get("luid") == luid:
            return record["properties"]
    raise AssertionError(f"no Workbook node with luid {luid}")


# ------------------------------------------------------------------- the score


def test_the_score_is_recognised_over_total() -> None:
    """Spec §4.1.4, with accepted constructs counted as read (S1.2.2)."""
    assert score(98, 0, 100) == 0.98
    assert score(90, 0, 100) == 0.90
    # Accepting the remaining ten lifts the workbook to a full score.
    assert score(90, 10, 100) == 1.0
    # Nothing to read is not a failure to read.
    assert score(0, 0, 0) == 1.0


# ---------------------------------------- parse_quality is on the Workbook node


async def test_parse_quality_is_stored_on_the_workbook_node() -> None:
    """S1.2.2 criterion 1."""
    site = build_site("rqa", 2)
    site.workbooks[0].unrecognised = (RAWSQL,)
    harvester, repository, _, _, _ = build(site)

    await harvester.run(request(), principal=PRINCIPAL)

    held = workbook_node(repository, site.workbooks[0].luid)
    clean = workbook_node(repository, site.workbooks[1].luid)
    assert 0.0 < held["parse_quality"] < 1.0
    assert clean["parse_quality"] == 1.0


async def test_parse_quality_is_queryable_over_graphql(client, repository) -> None:
    site = build_site("rqa", 1)
    harvester, _, harvest_store, quality, _ = build(site, repository=repository)
    await harvester.run(request(), principal=PRINCIPAL)

    node_id = derive_id("rqa", f"workbook:{site.workbooks[0].luid}")
    payload = await client.post(
        "/graphql",
        json={
            "query": "query($id: ID!) { node(id: $id) "
            "{ ... on Workbook { name parse_quality } } }",
            "variables": {"id": node_id},
        },
        headers=ARTIZENT_HEADERS,
    )
    assert payload.json()["data"]["node"]["parse_quality"] == 1.0


# -------------------------------------- constructs are stored verbatim, located


async def test_unrecognised_constructs_are_stored_verbatim_and_located() -> None:
    """S1.2.2 criterion 2."""
    site = build_site("rqa", 1)
    site.workbooks[0].unrecognised = (RAWSQL, SCRIPT)
    harvester, _, _, quality, _ = build(site)

    await harvester.run(request(), principal=PRINCIPAL)

    constructs = await quality.constructs_for(GRAPH, "rqa", site.workbooks[0].luid)
    assert {c.construct for c in constructs} == {RAWSQL, SCRIPT}
    for construct in constructs:
        assert construct.unrecognised is True
        assert construct.sheet is not None
        assert construct.field is not None
        assert construct.grammar_version == "fixture-1"


# --------------------------------------------------- held below the threshold


async def test_a_workbook_below_the_threshold_is_in_the_queue() -> None:
    """S1.2.2 criterion 3."""
    site = build_site("rqa", 3)
    site.workbooks[1].unrecognised = (RAWSQL,)
    harvester, _, _, quality, _ = build(site)

    progress = await harvester.run(request(), principal=PRINCIPAL)
    assert progress.held == 1
    assert progress.parsed == 2

    held = await quality.held(GRAPH, threshold=THRESHOLD)
    assert [item.workbook_luid for item in held] == [site.workbooks[1].luid]
    assert held[0].unrecognised_constructs == 1
    assert held[0].parse_quality < THRESHOLD


async def test_the_threshold_is_configurable() -> None:
    site = build_site("rqa", 1)
    site.workbooks[0].unrecognised = (RAWSQL,)
    harvester, _, _, quality, _ = build(site)
    await harvester.run(request(parse_quality_threshold=0.0), principal=PRINCIPAL)

    # Nothing is held at a threshold of zero, and the same estate is held at 1.0.
    assert await quality.held(GRAPH, threshold=0.0) == []
    assert len(await quality.held(GRAPH, threshold=1.0)) == 1


async def test_constructs_are_grouped_by_how_much_they_hold_up() -> None:
    """One grammar gap blocks many workbooks; the queue is worked construct-first."""
    site = build_site("rqa", 5)
    for workbook in site.workbooks[:4]:
        workbook.unrecognised = (RAWSQL,)
    site.workbooks[4].unrecognised = (SCRIPT,)
    harvester, _, _, quality, _ = build(site)
    await harvester.run(request(), principal=PRINCIPAL)

    groups = await quality.construct_groups(GRAPH, threshold=THRESHOLD)
    assert [group.construct for group in groups] == [RAWSQL, SCRIPT]
    assert groups[0].workbooks == 4
    assert groups[0].workbooks_held == 4, "fixing this releases four workbooks"
    assert groups[1].workbooks_held == 1


# ------------------------------------------------------------------ re-scoring


async def test_marking_a_construct_ignorable_rescores_and_releases() -> None:
    """S1.2.2 criterion 4, the first action."""
    site = build_site("rqa", 3)
    for workbook in site.workbooks[:2]:
        workbook.unrecognised = (RAWSQL,)
    harvester, repository, _, quality, rescorer = build(site)
    await harvester.run(request(), principal=PRINCIPAL)

    before = workbook_node(repository, site.workbooks[0].luid)["parse_quality"]
    assert before < THRESHOLD

    affected = await quality.mark_ignorable(
        GRAPH, RAWSQL, reason="RAWSQL is redesigned per Appendix B", principal=ENGINEER.value
    )
    assert len(affected) == 2

    result = await rescorer.rescore(affected, principal=ENGINEER)
    assert len(result.released) == 2
    assert all(item.parse_quality == 1.0 for item in result.rescored)

    # The new score is on the node, not just in the harvest record.
    assert workbook_node(repository, site.workbooks[0].luid)["parse_quality"] == 1.0
    assert await quality.held(GRAPH, threshold=THRESHOLD) == []


async def test_the_decision_is_recorded_against_every_occurrence() -> None:
    site = build_site("rqa", 2)
    for workbook in site.workbooks:
        workbook.unrecognised = (RAWSQL,)
    harvester, _, _, quality, _ = build(site)
    await harvester.run(request(), principal=PRINCIPAL)

    await quality.mark_ignorable(
        GRAPH, RAWSQL, reason="Accepted for the calibration wave", principal=ENGINEER.value
    )
    constructs = await quality.constructs_for(GRAPH, "rqa", site.workbooks[0].luid)
    assert constructs[0].unrecognised is False
    assert constructs[0].ignorable_reason == "Accepted for the calibration wave"
    assert constructs[0].decided_by == ENGINEER.value
    assert constructs[0].decided_at is not None


async def test_a_decision_survives_a_re_parse() -> None:
    """An engineer should not have to accept the same construct twice."""
    site = build_site("rqa", 1)
    site.workbooks[0].unrecognised = (RAWSQL,)
    harvester, repository, _, quality, rescorer = build(site)
    await harvester.run(request(), principal=PRINCIPAL)

    affected = await quality.mark_ignorable(
        GRAPH, RAWSQL, reason="Accepted for the calibration wave", principal=ENGINEER.value
    )
    await rescorer.rescore(affected, principal=ENGINEER)

    # The workbook changes and is re-parsed; the same construct is still there.
    site.workbooks[0].name = "Renamed"
    site.workbooks[0].revision = "2"
    progress = await harvester.run(request(), principal=PRINCIPAL)

    assert progress.parsed == 1
    assert progress.held == 0, "the accepted construct must not hold it again"
    constructs = await quality.constructs_for(GRAPH, "rqa", site.workbooks[0].luid)
    assert constructs[0].unrecognised is False
    assert constructs[0].decided_by == ENGINEER.value


async def test_re_scoring_does_not_re_harvest() -> None:
    """S1.2.2: 'without a full re-harvest'."""
    site = build_site("rqa", 2)
    for workbook in site.workbooks:
        workbook.unrecognised = (RAWSQL,)
    harvester, _, _, quality, rescorer = build(site)
    await harvester.run(request(), principal=PRINCIPAL)
    fetches = harvester._adapter.fetches
    parses = harvester._adapter.parses

    affected = await quality.mark_ignorable(
        GRAPH, RAWSQL, reason="Accepted for the calibration wave", principal=ENGINEER.value
    )
    await rescorer.rescore(affected, principal=ENGINEER)

    assert harvester._adapter.fetches == fetches
    assert harvester._adapter.parses == parses


async def test_re_scoring_leaves_a_mutation_event(repository) -> None:
    """The score change goes through the ordinary write path, so S1.1.3 still holds."""
    site = build_site("rqa", 1)
    site.workbooks[0].unrecognised = (RAWSQL,)
    harvester, repo, _, quality, rescorer = build(site, repository=repository)
    await harvester.run(request(), principal=PRINCIPAL)
    before = len(await repo.read_events(limit=10_000))

    affected = await quality.mark_ignorable(
        GRAPH, RAWSQL, reason="Accepted for the calibration wave", principal=ENGINEER.value
    )
    await rescorer.rescore(affected, principal=ENGINEER)

    events = await repo.read_events(after=before, limit=10_000)
    assert len(events) == 1
    assert events[0].data["properties"]["parse_quality"] == 1.0
    assert events[0].principal == ENGINEER.value


async def test_re_scoring_preserves_the_rest_of_the_node(repository) -> None:
    site = build_site("rqa", 1)
    site.workbooks[0].unrecognised = (RAWSQL,)
    site.workbooks[0].views_90d = 412
    harvester, repo, _, quality, rescorer = build(site, repository=repository)
    await harvester.run(request(), principal=PRINCIPAL)

    affected = await quality.mark_ignorable(
        GRAPH, RAWSQL, reason="Accepted for the calibration wave", principal=ENGINEER.value
    )
    await rescorer.rescore(affected, principal=ENGINEER)

    node = workbook_node(repo, site.workbooks[0].luid)
    assert node["parse_quality"] == 1.0
    assert node["views_90d"] == 412
    assert node["name"] == "Workbook 0"
    assert node["created_by"] == "agent:harvester"


# --------------------------------------------------------------------- the API


@pytest.fixture
def quality_app(client, repository):
    """Attach a harvested estate with a grammar gap to the app under test."""
    from astra_graph.api.routes_quality import DEFAULT_THRESHOLD

    site = build_site("rqa", 4)
    for workbook in site.workbooks[:2]:
        workbook.unrecognised = (RAWSQL,)
    harvester, _, harvest_store, quality, rescorer = build(site, repository=repository)

    app = client._transport.app
    app.state.quality_store = quality
    app.state.harvest_store = harvest_store
    app.state.rescorer = Rescorer(
        quality=quality,
        counts=harvest_store,
        writer=GraphWriter(repository),
        graph_name=GRAPH,
        threshold=DEFAULT_THRESHOLD,
    )
    return harvester, quality, site


async def test_the_queue_is_readable_over_http(client, quality_app) -> None:
    harvester, _, site = quality_app
    await harvester.run(request(), principal=PRINCIPAL)

    response = await client.get("/v1/parse-quality/queue", headers=ARTIZENT_HEADERS)
    assert response.status_code == 200
    body = response.json()
    assert body["threshold"] == 0.98
    assert body["count"] == 2
    assert body["held"][0]["unrecognised_constructs"] == 1


async def test_constructs_are_listed_over_http(client, quality_app) -> None:
    harvester, _, _ = quality_app
    await harvester.run(request(), principal=PRINCIPAL)

    response = await client.get("/v1/parse-quality/constructs", headers=ARTIZENT_HEADERS)
    body = response.json()
    assert body["constructs"][0]["construct"] == RAWSQL
    assert body["constructs"][0]["workbooks_released_if_resolved"] == 2


async def test_a_workbooks_constructs_are_readable_over_http(client, quality_app) -> None:
    harvester, _, site = quality_app
    await harvester.run(request(), principal=PRINCIPAL)

    response = await client.get(
        f"/v1/parse-quality/workbooks/rqa/{site.workbooks[0].luid}", headers=ARTIZENT_HEADERS
    )
    body = response.json()
    assert body["unrecognised_count"] == 1
    construct = body["constructs"][0]
    assert construct["construct"] == RAWSQL
    assert construct["unrecognised"] is True
    assert set(construct["location"]) == {"sheet", "field"}


async def test_marking_ignorable_over_http_releases_workbooks(client, quality_app) -> None:
    harvester, quality, _ = quality_app
    await harvester.run(request(), principal=PRINCIPAL)

    response = await client.post(
        "/v1/parse-quality/constructs:ignorable",
        json={"construct": RAWSQL, "reason": "Redesigned per Appendix B"},
        headers=ARTIZENT_HEADERS,
    )
    assert response.status_code == 200
    body = response.json()
    assert body["occurrences_accepted"] == 2
    assert body["workbooks_released"] == 2

    after = await client.get("/v1/parse-quality/queue", headers=ARTIZENT_HEADERS)
    assert after.json()["count"] == 0


async def test_marking_ignorable_needs_a_reason(client, quality_app) -> None:
    harvester, _, _ = quality_app
    await harvester.run(request(), principal=PRINCIPAL)

    response = await client.post(
        "/v1/parse-quality/constructs:ignorable",
        json={"construct": RAWSQL, "reason": "ok"},
        headers=ARTIZENT_HEADERS,
    )
    assert response.status_code == 400
    assert "needs a reason" in response.json()["message"]


async def test_marking_an_unknown_construct_is_404(client, quality_app) -> None:
    harvester, _, _ = quality_app
    await harvester.run(request(), principal=PRINCIPAL)

    response = await client.post(
        "/v1/parse-quality/constructs:ignorable",
        json={"construct": "NEVER_SEEN(...)", "reason": "Nothing to accept here"},
        headers=ARTIZENT_HEADERS,
    )
    assert response.status_code == 404


async def test_the_gate_answers_whether_a_workbook_may_advance(client, quality_app) -> None:
    """S1.2.2: held workbooks 'do not advance to CLUSTERED'."""
    harvester, _, site = quality_app
    await harvester.run(request(), principal=PRINCIPAL)

    held = await client.get(
        f"/v1/parse-quality/gate/rqa/{site.workbooks[0].luid}", headers=ARTIZENT_HEADERS
    )
    assert held.json()["may_advance"] is False
    assert "below the" in held.json()["reason"]

    clear = await client.get(
        f"/v1/parse-quality/gate/rqa/{site.workbooks[3].luid}", headers=ARTIZENT_HEADERS
    )
    assert clear.json()["may_advance"] is True
    assert clear.json()["reason"] is None
    # The gate reports the score either way, not only when it is blocking.
    assert clear.json()["parse_quality"] == 1.0


async def test_the_gate_opens_once_the_construct_is_accepted(client, quality_app) -> None:
    harvester, _, site = quality_app
    await harvester.run(request(), principal=PRINCIPAL)

    await client.post(
        "/v1/parse-quality/constructs:ignorable",
        json={"construct": RAWSQL, "reason": "Redesigned per Appendix B"},
        headers=ARTIZENT_HEADERS,
    )
    gate = await client.get(
        f"/v1/parse-quality/gate/rqa/{site.workbooks[0].luid}", headers=ARTIZENT_HEADERS
    )
    assert gate.json()["may_advance"] is True


async def test_parse_quality_endpoints_need_an_artizent_role(client, quality_app) -> None:
    from .conftest import CLIENT_HEADERS

    response = await client.get("/v1/parse-quality/queue", headers=CLIENT_HEADERS)
    assert response.status_code == 403


async def test_the_harvest_records_the_outcome_as_held(client, quality_app) -> None:
    harvester, _, site = quality_app
    progress = await harvester.run(request(), principal=PRINCIPAL)
    assert progress.held == 2
    assert progress.parsed == 2
    assert progress.failed == 0
    state = harvester._store.workbooks[(GRAPH, "rqa", site.workbooks[0].luid)]
    assert state.outcome is WorkbookOutcome.HELD_PARSE_QUALITY
