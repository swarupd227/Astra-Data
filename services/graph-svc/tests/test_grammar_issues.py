"""Grammar issues raised from the Parse Quality Queue.

S1.4.3's second action: "open grammar issue (creates a ticket with the construct text and
locations)". The queue's other two actions — mark ignorable, request re-harvest — were built
in S1.2.2 and S1.2.1 and are covered by their own suites; this is what is new.
"""

from __future__ import annotations

import pytest

from astra_graph.grammar import (
    MAX_LOCATIONS,
    MIN_DETAIL,
    GrammarIssueError,
    InMemoryIssueStore,
    IssueState,
    LocalIssueTracker,
    new_issue,
)

from .conftest import ARTIZENT_HEADERS, CLIENT_HEADERS

CONSTRUCT = "RAWSQL_INT(<expr>)"
DETAIL = "Needs a DAX equivalent; see Appendix B for the redesign route."


def issue(**kwargs):
    defaults = {
        "construct": CONSTRUCT,
        "summary": "",
        "detail": DETAIL,
        "opened_by": "user:p.eng@artizent.example",
    }
    defaults.update(kwargs)
    return new_issue(**defaults)


# ------------------------------------------------------------------ the record


def test_an_issue_needs_enough_detail_to_act_on() -> None:
    """Whoever picks it up will not have been in the conversation."""
    with pytest.raises(GrammarIssueError, match="at least"):
        issue(detail="fix it")


def test_an_issue_is_about_a_construct() -> None:
    with pytest.raises(GrammarIssueError, match="about a construct"):
        issue(construct="   ")


def test_the_summary_defaults_to_the_construct() -> None:
    assert issue().summary == f"Grammar cannot read {CONSTRUCT}"
    assert issue(summary="LOD in a table calc").summary == "LOD in a table calc"


def test_an_issue_carries_the_evidence_it_was_raised_on() -> None:
    """A snapshot, deliberately. The estate moves: a re-harvest re-parses the workbooks and
    a later grammar stops producing the construct at all, so an issue that resolved its
    locations live would describe wherever the construct is *now* rather than the case
    somebody made for fixing it."""
    raised = issue(
        locations=[{"site": "rqa", "workbook": "Daily VaR", "sheet": "VaR by Desk"}],
        occurrences=12,
        workbooks_held=8,
    )

    body = raised.as_dict()
    assert body["locations"][0]["workbook"] == "Daily VaR"
    assert body["occurrences_when_raised"] == 12
    assert body["workbooks_held_when_raised"] == 8


def test_the_locations_are_bounded() -> None:
    """A list of four hundred places is one nobody reads, and the queue holds the live
    figure anyway."""
    raised = issue(locations=[{"workbook": f"WB {i}"} for i in range(100)])
    assert len(raised.locations) == MAX_LOCATIONS


async def test_one_open_issue_per_construct() -> None:
    """A second issue is not a second problem; it is two people raising the same one, and
    the queue would then show the gap as blocked twice."""
    store = InMemoryIssueStore()
    await store.open(issue())

    with pytest.raises(GrammarIssueError, match="already open"):
        await store.open(issue())


async def test_a_resolved_construct_can_be_raised_again() -> None:
    """A gap closed as WONT_FIX and reopened later is an ordinary thing."""
    store = InMemoryIssueStore()
    first = await store.open(issue())
    await store.resolve(
        first.id,
        state=IssueState.WONT_FIX,
        resolution="Redesign agreed at the March review",
        resolved_by="user:p.eng@artizent.example",
    )

    again = await store.open(issue())
    assert again.id != first.id


async def test_the_queue_can_ask_which_constructs_are_already_raised_in_one_read() -> None:
    store = InMemoryIssueStore()
    await store.open(issue())
    await store.open(issue(construct="WINDOW_SUM(<expr>)"))

    raised = await store.by_construct()

    assert set(raised) == {CONSTRUCT, "WINDOW_SUM(<expr>)"}


async def test_a_closed_issue_leaves_the_queue() -> None:
    store = InMemoryIssueStore()
    opened = await store.open(issue())

    await store.resolve(
        opened.id,
        state=IssueState.RESOLVED,
        resolution="Grammar 1.5 reads it",
        resolved_by="user:p.eng@artizent.example",
    )

    assert await store.by_construct() == {}


async def test_a_local_tracker_files_nothing_and_says_so() -> None:
    """§21 makes work tracking optional. A platform that silently dropped issues because no
    tracker was configured would be worse than one holding them itself."""
    tracker = LocalIssueTracker()

    ref, url = await tracker.mirror(issue())

    assert (ref, url) == (None, None)
    assert tracker.kind == "local"


# ------------------------------------------------------------- the HTTP surface


@pytest.fixture
async def queue(client, repository):
    """Two held workbooks sharing one unrecognised construct.

    Seeded by running a real harvest rather than by writing store rows: the constructs, the
    scores and the held state all come out of the same path a client estate would, so a
    change to how any of them is recorded breaks this too.
    """
    from astra_graph.adapters.contract import Scope
    from astra_graph.adapters.fixture import FixtureSourceAdapter, build_site
    from astra_graph.credentials import StaticCredentialProvider
    from astra_graph.harvest import (
        Harvester,
        HarvestRequest,
        InMemoryHarvestStore,
        InMemoryParseQualityStore,
    )
    from astra_graph.principal import Principal
    from astra_graph.writes import GraphWriter

    site = build_site("rqa", 3)
    site.workbooks[0].unrecognised = (CONSTRUCT,)
    site.workbooks[1].unrecognised = (CONSTRUCT,)

    harvest = InMemoryHarvestStore()
    quality = InMemoryParseQualityStore(harvest)
    harvester = Harvester(
        adapter=FixtureSourceAdapter([site]),
        writer=GraphWriter(repository),
        store=harvest,
        credentials=StaticCredentialProvider({"tableau/rqa": "a-token"}),
        graph_name="astra_estate_test",
        quality=quality,
    )
    await harvester.run(
        HarvestRequest(scope=Scope(site="rqa"), credential_reference="tableau/rqa"),
        principal=Principal("agent:harvester", run_id="run-queue"),
    )

    app = client._transport.app
    app.state.harvest_store = harvest
    app.state.quality_store = quality
    return client, app


async def test_a_construct_can_be_raised_as_an_issue(queue) -> None:
    """S1.4.3's second action, end to end."""
    client, _app = queue

    response = await client.post(
        "/v1/parse-quality/constructs:issue",
        json={"construct": CONSTRUCT, "detail": DETAIL},
        headers=ARTIZENT_HEADERS,
    )

    assert response.status_code == 201
    body = response.json()
    assert body["construct"] == CONSTRUCT
    assert body["state"] == "OPEN"
    assert body["tracker"] == "local"
    assert body["mirrored"] is False
    # The ticket carries the construct text *and* its locations, which is what makes a
    # grammar gap actionable rather than abstract.
    assert len(body["locations"]) == 2, "both workbooks that hold the construct"
    assert {location["site"] for location in body["locations"]} == {"rqa"}
    assert all(location["workbook"] for location in body["locations"])


async def test_the_issue_records_what_the_gap_was_holding_up(queue) -> None:
    client, _app = queue

    body = (
        await client.post(
            "/v1/parse-quality/constructs:issue",
            json={"construct": CONSTRUCT, "detail": DETAIL},
            headers=ARTIZENT_HEADERS,
        )
    ).json()

    assert body["occurrences_when_raised"] == 2
    assert body["workbooks_held_when_raised"] == 2


async def test_the_queue_shows_which_constructs_are_already_raised(queue) -> None:
    """So a platform engineer does not open a second ticket for the same gap."""
    client, _app = queue

    before = (
        await client.get("/v1/parse-quality/constructs", headers=ARTIZENT_HEADERS)
    ).json()
    assert before["constructs"][0]["issue"] is None

    await client.post(
        "/v1/parse-quality/constructs:issue",
        json={"construct": CONSTRUCT, "detail": DETAIL},
        headers=ARTIZENT_HEADERS,
    )

    after = (
        await client.get("/v1/parse-quality/constructs", headers=ARTIZENT_HEADERS)
    ).json()
    assert after["constructs"][0]["issue"]["state"] == "OPEN"
    assert after["constructs"][0]["issue"]["opened_by"] == "agent:harvester"


async def test_raising_a_construct_the_estate_does_not_have_is_a_404(queue) -> None:
    """The queue reports the text verbatim; a near-miss is a different construct."""
    client, _app = queue

    response = await client.post(
        "/v1/parse-quality/constructs:issue",
        json={"construct": "RAWSQL_INT(<expr>) ", "detail": DETAIL},
        headers=ARTIZENT_HEADERS,
    )

    assert response.status_code == 404


async def test_a_thin_detail_is_refused_before_it_reaches_the_store(queue) -> None:
    client, _app = queue

    response = await client.post(
        "/v1/parse-quality/constructs:issue",
        json={"construct": CONSTRUCT, "detail": "no"},
        headers=ARTIZENT_HEADERS,
    )

    assert response.status_code == 422
    assert MIN_DETAIL == 10


async def test_a_second_issue_for_the_same_construct_is_refused(queue) -> None:
    client, _app = queue
    body = {"construct": CONSTRUCT, "detail": DETAIL}

    first = await client.post(
        "/v1/parse-quality/constructs:issue", json=body, headers=ARTIZENT_HEADERS
    )
    second = await client.post(
        "/v1/parse-quality/constructs:issue", json=body, headers=ARTIZENT_HEADERS
    )

    assert first.status_code == 201
    assert second.status_code == 400
    assert "already open" in second.json()["message"]


async def test_issues_are_listed_with_the_tracker_that_holds_them(queue) -> None:
    client, _app = queue
    await client.post(
        "/v1/parse-quality/constructs:issue",
        json={"construct": CONSTRUCT, "detail": DETAIL},
        headers=ARTIZENT_HEADERS,
    )

    body = (await client.get("/v1/parse-quality/issues", headers=ARTIZENT_HEADERS)).json()

    assert body["count"] == 1
    assert body["tracker"] == "local"


async def test_closing_an_issue_does_not_claim_to_have_re_scored_anything(queue) -> None:
    """Extending the grammar changes what a *re-parse* produces. Marking the issue closed
    and silently re-scoring would claim a result the parser has not produced."""
    client, _app = queue
    opened = (
        await client.post(
            "/v1/parse-quality/constructs:issue",
            json={"construct": CONSTRUCT, "detail": DETAIL},
            headers=ARTIZENT_HEADERS,
        )
    ).json()

    response = await client.post(
        f"/v1/parse-quality/issues/{opened['id']}:resolve",
        json={"state": "RESOLVED", "resolution": "Grammar 1.5 reads it"},
        headers=ARTIZENT_HEADERS,
    )

    assert response.status_code == 200
    assert response.json()["state"] == "RESOLVED"
    assert "Re-harvest" in response.json()["note"]

    # And the queue still holds it, because nothing has been re-parsed yet.
    queue_body = (
        await client.get("/v1/parse-quality/queue", headers=ARTIZENT_HEADERS)
    ).json()
    assert queue_body["count"] == 2, "the workbooks are still held until they are re-parsed"


async def test_an_issue_cannot_be_closed_into_an_open_state(queue) -> None:
    client, _app = queue
    opened = (
        await client.post(
            "/v1/parse-quality/constructs:issue",
            json={"construct": CONSTRUCT, "detail": DETAIL},
            headers=ARTIZENT_HEADERS,
        )
    ).json()

    response = await client.post(
        f"/v1/parse-quality/issues/{opened['id']}:resolve",
        json={"state": "IN_PROGRESS", "resolution": "Somebody is looking at it"},
        headers=ARTIZENT_HEADERS,
    )

    assert response.status_code == 400
    assert "does not close an issue" in response.json()["message"]


async def test_grammar_issues_need_an_artizent_role(queue) -> None:
    client, _app = queue

    response = await client.post(
        "/v1/parse-quality/constructs:issue",
        json={"construct": CONSTRUCT, "detail": DETAIL},
        headers=CLIENT_HEADERS,
    )

    assert response.status_code == 403


# ------------------------------------------------------------- the wire contract


async def test_the_queue_endpoints_keep_their_key_names(queue) -> None:
    """The console reads these by name, and its tests use a fake it wrote itself.

    Three times while building S1.4.3 the fake and the service disagreed about a key —
    ``held`` against ``workbooks``, and one number serialised under a name the console read
    as two. Each looked fine in jsdom and white-screened against the real service. Pinning
    the key sets here does not prove the console is right, but it makes a rename break
    something loud instead of a screen nobody has opened yet.
    """
    listing = (await client_get(queue, "/v1/parse-quality/queue")).json()
    assert set(listing) == {"threshold", "held", "count"}
    assert set(listing["held"][0]) == {
        "site",
        "workbook_luid",
        "workbook_name",
        "project",
        "parse_quality",
        "recognised",
        "ignorable",
        "total",
        "unrecognised_constructs",
        "grammar_version",
        "harvested_at",
    }

    grouped = (await client_get(queue, "/v1/parse-quality/constructs")).json()
    assert set(grouped) == {"threshold", "constructs", "count"}
    assert set(grouped["constructs"][0]) == {
        "construct",
        "occurrences",
        "workbooks",
        # One number: the store calls it workbooks_held, the wire calls it this. A console
        # that read both names would render one of them as undefined.
        "workbooks_released_if_resolved",
        "sites",
        "example_location",
        "unrecognised",
        "issue",
    }


async def test_the_issue_endpoint_keeps_its_key_names(queue) -> None:
    """Same pin, for the shape the console reads an issue back in.

    The two counts are named ``*_when_raised`` on purpose: they are a snapshot taken when
    the issue was opened, not the live figures, and a console showing them beside the
    queue's live ones would otherwise present two numbers as though they disagreed.
    """
    client, _app = queue
    await client.post(
        "/v1/parse-quality/constructs:issue",
        json={
            "construct": CONSTRUCT,
            "summary": "RAWSQL in a calculated field",
            "detail": "Parse it as an opaque expression and classify it C4.",
        },
        headers=ARTIZENT_HEADERS,
    )

    listing = (await client_get(queue, "/v1/parse-quality/issues")).json()
    assert set(listing) == {"issues", "count", "tracker"}
    assert set(listing["issues"][0]) == {
        "id",
        "construct",
        "summary",
        "detail",
        "state",
        "active",
        "adapter",
        "grammar_version",
        "locations",
        "occurrences_when_raised",
        "workbooks_held_when_raised",
        "external",
        "opened_by",
        "opened_at",
        "resolved_by",
        "resolved_at",
        "resolution",
    }
    assert set(listing["issues"][0]["external"]) == {"ref", "url"}


async def client_get(queue, path: str):
    client, _app = queue
    return await client.get(path, headers=ARTIZENT_HEADERS)

