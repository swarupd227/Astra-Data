"""Usage and ownership captured with the workbook.

S1.2.3's three acceptance criteria: usage per workbook and per view, owners resolved
against the directory with the unresolved ones listed, and licence tiers where the source
exposes them.
"""

from __future__ import annotations

import pytest

from astra_graph.adapters.contract import Capabilities, Scope
from astra_graph.adapters.fixture import FixtureSite, FixtureSourceAdapter, FixtureWorkbook
from astra_graph.credentials import StaticCredentialProvider
from astra_graph.directory import (
    DirectoryError,
    DirectoryUser,
    NullDirectoryResolver,
    StaticDirectoryResolver,
    validate_directory_id,
)
from astra_graph.harvest import (
    Harvester,
    HarvestRequest,
    InMemoryHarvestStore,
    derive_id,
)
from astra_graph.principal import Principal
from astra_graph.writes import GraphWriter

from .conftest import ARTIZENT_HEADERS, CLIENT_HEADERS
from .fakes import InMemoryGraphRepository

PRINCIPAL = Principal("agent:harvester", run_id="run-usage")
CREDENTIALS = StaticCredentialProvider({"tableau/rqa": "a-token"})
GRAPH = "astra_estate_test"

MEHTA = "a.mehta@client.example"
IYER = "s.iyer@client.example"
GONE = "left.the.company@client.example"

DIRECTORY = StaticDirectoryResolver(
    {
        MEHTA: DirectoryUser("11111111-1111-4111-8111-111111111111", MEHTA, "A Mehta"),
        IYER: DirectoryUser("22222222-2222-4222-8222-222222222222", IYER, "S Iyer"),
    }
)


def site_with(**workbook_kwargs) -> FixtureSite:
    """One workbook with usage, viewers and an owner, plus the site's own facts."""
    defaults = {
        "name": "Daily VaR",
        "luid": "wb-1",
        "project": "Risk Core",
        "sheets": 2,
        "dashboards": 1,
        "datasources": 1,
        "fields": 2,
        "calculations": 1,
        "views_90d": 400,
        "distinct_viewers_90d": 31,
        "owner_upn": MEHTA,
        "viewers": ((IYER, 260), (MEHTA, 140)),
    }
    defaults.update(workbook_kwargs)
    return FixtureSite(
        name="rqa",
        workbooks=[FixtureWorkbook(**defaults)],
        licence_tier="User-based",
        user_count=94,
    )


def build(site, *, directory=DIRECTORY, capabilities=None, repository=None):
    repository = repository or InMemoryGraphRepository()
    harvester = Harvester(
        adapter=FixtureSourceAdapter([site], capabilities=capabilities),
        writer=GraphWriter(repository),
        store=InMemoryHarvestStore(),
        credentials=CREDENTIALS,
        graph_name=GRAPH,
        directory=directory,
    )
    return harvester, repository


def request(**kwargs) -> HarvestRequest:
    kwargs.setdefault("scope", Scope(site="rqa"))
    kwargs.setdefault("credential_reference", "tableau/rqa")
    return HarvestRequest(**kwargs)


def nodes_of(repository, label: str) -> list[dict]:
    return [r["properties"] for r in repository.nodes.values() if r["label"] == label]


def edges_of(repository, label: str) -> list[dict]:
    return [r for r in repository.edges.values() if r["label"] == label]


# ------------------------------------------------- usage per workbook and per view


async def test_usage_is_stored_per_workbook() -> None:
    """S1.2.3 criterion 1, the workbook half."""
    harvester, repository = build(site_with())
    await harvester.run(request(), principal=PRINCIPAL)

    workbook = nodes_of(repository, "Workbook")[0]
    assert workbook["views_90d"] == 400
    assert workbook["distinct_viewers_90d"] == 31


async def test_usage_is_stored_per_view() -> None:
    """S1.2.3 criterion 1, the per-view half.

    A view is a Worksheet or a Dashboard. A workbook whose 400 views sit in one dashboard
    is a different proposition for wave ordering than one spread evenly.
    """
    harvester, repository = build(site_with())
    await harvester.run(request(), principal=PRINCIPAL)

    sheets = nodes_of(repository, "Worksheet")
    dashboards = nodes_of(repository, "Dashboard")
    assert len(sheets) == 2
    assert len(dashboards) == 1
    assert all("views_90d" in sheet for sheet in sheets)
    assert all("last_view" in sheet for sheet in sheets)

    # The per-view figures account for the workbook total without loss.
    total = sum(view["views_90d"] for view in sheets + dashboards)
    assert total == 400


async def test_a_view_with_no_usage_carries_none() -> None:
    harvester, repository = build(site_with(views_90d=0, distinct_viewers_90d=0, viewers=()))
    await harvester.run(request(), principal=PRINCIPAL)

    for sheet in nodes_of(repository, "Worksheet"):
        assert "views_90d" not in sheet


async def test_usage_is_absent_when_the_source_cannot_report_it() -> None:
    """Absent is not zero: a site without the Metadata API has unknown usage, and a
    programme manager ordering waves needs to see the difference (backlog §7.1)."""
    harvester, repository = build(
        site_with(),
        capabilities=Capabilities(extract_read=True, usage=False, ownership=True),
    )
    await harvester.run(request(), principal=PRINCIPAL)

    assert "views_90d" not in nodes_of(repository, "Workbook")[0]
    assert all("views_90d" not in sheet for sheet in nodes_of(repository, "Worksheet"))


# -------------------------------------------------------------- viewers and edges


async def test_viewed_by_is_written_per_viewer() -> None:
    """Spec §4.1.2 gives VIEWED_BY a views_90d per (workbook, user) pair."""
    harvester, repository = build(site_with())
    await harvester.run(request(), principal=PRINCIPAL)

    viewed = edges_of(repository, "VIEWED_BY")
    assert len(viewed) == 2
    counts = sorted(edge["properties"]["views_90d"] for edge in viewed)
    assert counts == [140, 260]
    assert all(edge["properties"]["last_view"] for edge in viewed)


async def test_viewed_by_is_not_invented_when_the_source_has_no_viewers() -> None:
    """An aggregate hung off the owner would be a different and untrue statement."""
    harvester, repository = build(site_with(viewers=()))
    await harvester.run(request(), principal=PRINCIPAL)

    assert edges_of(repository, "VIEWED_BY") == []
    # The workbook's aggregate is still recorded; only the per-viewer claim is withheld.
    assert nodes_of(repository, "Workbook")[0]["views_90d"] == 400


async def test_a_viewer_who_is_not_the_owner_becomes_a_user() -> None:
    harvester, repository = build(site_with())
    await harvester.run(request(), principal=PRINCIPAL)

    assert {user["upn"] for user in nodes_of(repository, "User")} == {MEHTA, IYER}
    owned = edges_of(repository, "OWNED_BY")
    assert len(owned) == 1
    assert owned[0]["to_id"] == derive_id("rqa", f"user:{MEHTA}")


# ------------------------------------------------------------ directory resolution


async def test_a_resolved_owner_carries_its_directory_link() -> None:
    """S1.2.3 criterion 2."""
    harvester, repository = build(site_with())
    await harvester.run(request(), principal=PRINCIPAL)

    owner = next(u for u in nodes_of(repository, "User") if u["upn"] == MEHTA)
    assert owner["directory_id"] == "11111111-1111-4111-8111-111111111111"
    assert owner["display"] == "A Mehta"
    assert owner["directory_resolved_at"].endswith("Z")


async def test_an_unresolved_owner_is_recorded_not_dropped() -> None:
    """A Tableau site outlives reorganisations; an owner who has left is ordinary."""
    harvester, repository = build(site_with(owner_upn=GONE, viewers=()))
    await harvester.run(request(), principal=PRINCIPAL)

    owner = next(u for u in nodes_of(repository, "User") if u["upn"] == GONE)
    assert "directory_id" not in owner
    assert edges_of(repository, "OWNED_BY")


async def test_the_user_node_is_keyed_on_the_source_identity_not_the_directory() -> None:
    """Resolving adds a fact to a user; it must not change which user they are."""
    harvester, repository = build(site_with())
    await harvester.run(request(), principal=PRINCIPAL)
    resolved_id = next(
        node_id
        for node_id, record in repository.nodes.items()
        if record["label"] == "User" and record["properties"]["upn"] == MEHTA
    )
    assert resolved_id == derive_id("rqa", f"user:{MEHTA}")


async def test_a_null_resolver_leaves_everyone_unresolved() -> None:
    harvester, repository = build(site_with(), directory=NullDirectoryResolver())
    await harvester.run(request(), principal=PRINCIPAL)

    assert all("directory_id" not in user for user in nodes_of(repository, "User"))


def test_a_directory_id_must_be_a_guid() -> None:
    validate_directory_id("11111111-1111-4111-8111-111111111111")
    with pytest.raises(DirectoryError, match="not a GUID"):
        validate_directory_id("A Mehta")


# --------------------------------------------------------------- licence tiers


async def test_the_site_licence_tier_is_stored() -> None:
    """S1.2.3 criterion 3, the site half."""
    harvester, repository = build(site_with())
    await harvester.run(request(), principal=PRINCIPAL)

    site = nodes_of(repository, "Site")[0]
    assert site["licence_tier"] == "User-based"
    assert site["user_count"] == 94


async def test_the_per_user_licence_tier_is_stored() -> None:
    """S1.2.3 criterion 3, the user half."""
    harvester, repository = build(site_with())
    await harvester.run(request(), principal=PRINCIPAL)

    owner = next(u for u in nodes_of(repository, "User") if u["upn"] == MEHTA)
    assert owner["licence_tier"] == "Creator"
    assert owner["site_roles"] == ["Explorer"]


async def test_licence_tiers_are_absent_without_the_ownership_capability(
) -> None:
    harvester, repository = build(
        site_with(),
        capabilities=Capabilities(extract_read=True, usage=True, ownership=False),
    )
    await harvester.run(request(), principal=PRINCIPAL)

    site = nodes_of(repository, "Site")[0]
    assert "licence_tier" not in site


# -------------------------------------------------------------------- the API


@pytest.fixture
async def harvested(client, repository):
    """An estate with one resolved owner and one who has left the company."""
    site = FixtureSite(
        name="rqa",
        licence_tier="User-based",
        workbooks=[
            FixtureWorkbook(
                name="Daily VaR", luid="wb-1", project="Risk Core",
                views_90d=400, distinct_viewers_90d=31, owner_upn=MEHTA, viewers=(),
            ),
            FixtureWorkbook(
                name="Legacy VaR", luid="wb-2", project="Risk Core",
                views_90d=12, distinct_viewers_90d=2, owner_upn=GONE, viewers=(),
            ),
            FixtureWorkbook(
                name="Older VaR", luid="wb-3", project="Risk Core",
                views_90d=3, distinct_viewers_90d=1, owner_upn=GONE, viewers=(),
            ),
        ],
    )
    harvester = Harvester(
        adapter=FixtureSourceAdapter([site]),
        writer=GraphWriter(repository),
        store=InMemoryHarvestStore(),
        credentials=CREDENTIALS,
        graph_name=GRAPH,
        directory=DIRECTORY,
    )
    app = client._transport.app
    app.state.directory = DIRECTORY
    await harvester.run(request(), principal=PRINCIPAL)
    return harvester


async def test_unresolved_owners_are_listed(client, harvested) -> None:
    """S1.2.3 criterion 2: 'unresolved owners are listed for assignment'."""
    response = await client.get("/v1/ownership/unresolved", headers=ARTIZENT_HEADERS)
    assert response.status_code == 200
    body = response.json()
    assert body["count"] == 1
    assert body["unresolved"][0]["upn"] == GONE
    # Ordered by how many gate requests currently have nobody to go to.
    assert body["unresolved"][0]["owns"] == 2
    assert body["resolver"] == "static"


async def test_a_resolved_owner_is_not_listed(client, harvested) -> None:
    body = (await client.get("/v1/ownership/unresolved", headers=ARTIZENT_HEADERS)).json()
    assert MEHTA not in {item["upn"] for item in body["unresolved"]}


async def test_an_owner_can_be_assigned_by_hand(client, harvested, repository) -> None:
    response = await client.post(
        "/v1/ownership/assign",
        json={
            "site": "rqa",
            "upn": GONE,
            "directory_id": "33333333-3333-4333-8333-333333333333",
            "display": "R Successor",
        },
        headers=ARTIZENT_HEADERS,
    )
    assert response.status_code == 200
    assert response.json()["directory_id"] == "33333333-3333-4333-8333-333333333333"

    after = (await client.get("/v1/ownership/unresolved", headers=ARTIZENT_HEADERS)).json()
    assert after["count"] == 0

    user = next(u for u in nodes_of(repository, "User") if u["upn"] == GONE)
    assert user["display"] == "R Successor"
    assert user["directory_resolved_at"].endswith("Z")


async def test_assignment_goes_through_the_write_path(client, harvested, repository) -> None:
    """A person's judgement recorded as a fact, so it leaves a mutation event."""
    before = len(await repository.read_events(limit=10_000))
    await client.post(
        "/v1/ownership/assign",
        json={
            "site": "rqa",
            "upn": GONE,
            "directory_id": "33333333-3333-4333-8333-333333333333",
        },
        headers=ARTIZENT_HEADERS,
    )
    events = await repository.read_events(after=before, limit=10_000)
    assert len(events) == 1
    assert events[0].principal == "agent:harvester"
    assert (
        events[0].data["properties"]["directory_id"]
        == "33333333-3333-4333-8333-333333333333"
    )


async def test_assigning_a_non_guid_is_rejected(client, harvested) -> None:
    response = await client.post(
        "/v1/ownership/assign",
        json={"site": "rqa", "upn": GONE, "directory_id": "A Mehta not a guid at all!!"},
        headers=ARTIZENT_HEADERS,
    )
    assert response.status_code == 400
    assert "not a GUID" in response.json()["message"]


async def test_assigning_an_already_linked_owner_is_refused(client, harvested) -> None:
    response = await client.post(
        "/v1/ownership/assign",
        json={
            "site": "rqa",
            "upn": MEHTA,
            "directory_id": "33333333-3333-4333-8333-333333333333",
        },
        headers=ARTIZENT_HEADERS,
    )
    assert response.status_code == 400
    assert "already linked" in response.json()["message"]


async def test_assigning_an_unknown_user_is_404(client, harvested) -> None:
    response = await client.post(
        "/v1/ownership/assign",
        json={
            "site": "rqa",
            "upn": "never.harvested@client.example",
            "directory_id": "33333333-3333-4333-8333-333333333333",
        },
        headers=ARTIZENT_HEADERS,
    )
    assert response.status_code == 404


async def test_ownership_endpoints_need_an_artizent_role(client, harvested) -> None:
    response = await client.get("/v1/ownership/unresolved", headers=CLIENT_HEADERS)
    assert response.status_code == 403


async def test_the_user_count_fallback_counts_everyone_the_source_named() -> None:
    """A site's user count is its people, not just the ones who own something.

    The fallback exists for sources that do not report a count; counting owners alone
    would have a demo estate of five people report one.
    """
    site = FixtureSite(
        name="counted",
        workbooks=[
            FixtureWorkbook(
                name="One",
                luid="counted-wb-1",
                project="Counted",
                owner_upn="owner@client.example",
                viewers=(("viewer0@client.example", 3), ("viewer1@client.example", 2)),
            )
        ],
    )
    adapter = FixtureSourceAdapter([site])

    records = await adapter.sites(Scope(site="counted"))

    assert [record.user_count for record in records] == [3]

