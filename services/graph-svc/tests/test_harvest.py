"""The Harvester.

S1.2.1's four acceptance criteria, against the fixture adapter and the in-memory store.
The same loop is exercised against Apache AGE in ``test_integration_harvest.py``.
"""

from __future__ import annotations

import pytest

from astra_graph.adapters.contract import (
    INTERFACE_VERSION,
    AdapterError,
    AdapterManifest,
    Capabilities,
    Scope,
)
from astra_graph.adapters.fixture import (
    FixtureSite,
    FixtureSourceAdapter,
    FixtureWorkbook,
    build_site,
)
from astra_graph.credentials import (
    CredentialError,
    EnvironmentCredentialProvider,
    StaticCredentialProvider,
    validate_reference,
)
from astra_graph.harvest import (
    Harvester,
    HarvestRequest,
    HarvestState,
    InMemoryHarvestStore,
    WorkbookOutcome,
    derive_id,
)
from astra_graph.principal import Principal
from astra_graph.writes import GraphWriter

from .conftest import ARTIZENT_HEADERS, HEADERS
from .fakes import InMemoryGraphRepository

PRINCIPAL = Principal("agent:harvester", run_id="run-harvest")
CREDENTIALS = StaticCredentialProvider({"tableau/rqa": "a-personal-access-token"})


def make_harvester(adapter, *, repository=None, store=None):
    repository = repository or InMemoryGraphRepository()
    store = store or InMemoryHarvestStore()
    harvester = Harvester(
        adapter=adapter,
        writer=GraphWriter(repository),
        store=store,
        credentials=CREDENTIALS,
        graph_name="astra_estate_test",
    )
    return harvester, repository, store


def request(**kwargs) -> HarvestRequest:
    kwargs.setdefault("scope", Scope(site="rqa"))
    kwargs.setdefault("credential_reference", "tableau/rqa")
    return HarvestRequest(**kwargs)


# ---------------------------------------------------- a site parses into the graph


async def test_a_site_is_parsed_into_the_graph() -> None:
    adapter = FixtureSourceAdapter([build_site("rqa", 5)])
    harvester, repository, _ = make_harvester(adapter)

    progress = await harvester.run(request(), principal=PRINCIPAL)

    assert progress.state is HarvestState.COMPLETED
    assert progress.queued == 5
    assert progress.parsed == 5
    assert progress.failed == 0
    assert repository.nodes


async def test_each_workbook_records_everything_the_story_lists() -> None:
    """S1.2.1: sheets, dashboards, datasources, connections, fields, calculated fields
    (with formula and AST), parameters, filters, actions, owners, views, viewers."""
    site = FixtureSite(
        name="rqa",
        workbooks=[
            FixtureWorkbook(
                name="Daily VaR",
                luid="wb-1",
                project="Risk Core",
                sheets=2,
                dashboards=1,
                datasources=1,
                fields=3,
                calculations=2,
                parameters=1,
                filters=1,
                actions=1,
                views_90d=412,
                distinct_viewers_90d=31,
                owner_upn="a.mehta@client.example",
                viewers=(("s.iyer@client.example", 260), ("a.mehta@client.example", 152)),
            )
        ],
    )
    harvester, repository, _ = make_harvester(FixtureSourceAdapter([site]))
    await harvester.run(request(), principal=PRINCIPAL)

    by_type: dict[str, list[dict]] = {}
    for record in repository.nodes.values():
        by_type.setdefault(record["label"], []).append(record["properties"])

    assert len(by_type["Worksheet"]) == 2
    assert len(by_type["Dashboard"]) == 1
    assert len(by_type["Datasource"]) == 1
    assert len(by_type["Connection"]) == 1
    assert len(by_type["Field"]) == 3
    assert len(by_type["Parameter"]) == 1
    assert len(by_type["Filter"]) == 1
    assert len(by_type["Action"]) == 1
    # The owner, plus everyone the source reported as having viewed it.
    assert len(by_type["User"]) == 2

    calculations = by_type["CalculatedField"]
    assert len(calculations) == 2
    assert calculations[0]["formula"] == "SUM([Margin]) / SUM([Revenue])"
    assert calculations[0]["formula_ast"]["op"] == "DIV"

    workbook = by_type["Workbook"][0]
    assert workbook["views_90d"] == 412
    assert workbook["distinct_viewers_90d"] == 31

    assert {user["upn"] for user in by_type["User"]} == {
        "a.mehta@client.example",
        "s.iyer@client.example",
    }
    edge_labels = {record["label"] for record in repository.edges.values()}
    assert {"CONTAINS", "USES_DATASOURCE", "HAS_FIELD", "ENCODES", "DEPENDS_ON",
            "FILTERED_BY", "CONNECTS_TO", "OWNED_BY", "VIEWED_BY"} <= edge_labels


async def test_usage_is_absent_when_the_adapter_cannot_supply_it() -> None:
    """A site without the Metadata API reports no usage, not zero views (backlog §7.1)."""
    adapter = FixtureSourceAdapter(
        [build_site("rqa", 2)],
        capabilities=Capabilities(extract_read=True, usage=False, ownership=False),
    )
    harvester, repository, _ = make_harvester(adapter)
    await harvester.run(request(), principal=PRINCIPAL)

    workbooks = [r for r in repository.nodes.values() if r["label"] == "Workbook"]
    assert workbooks
    for workbook in workbooks:
        assert "views_90d" not in workbook["properties"]
    assert not [r for r in repository.nodes.values() if r["label"] == "User"]


# ------------------------------------------------------------- progress per project


async def test_progress_is_reported_per_project() -> None:
    adapter = FixtureSourceAdapter([build_site("rqa", 9, project_count=3)])
    harvester, _, store = make_harvester(adapter)

    progress = await harvester.run(request(), principal=PRINCIPAL)

    assert [p.project for p in progress.projects] == ["Project 0", "Project 1", "Project 2"]
    assert all(p.queued == 3 for p in progress.projects)
    assert all(p.parsed == 3 for p in progress.projects)
    assert all(p.remaining == 0 for p in progress.projects)
    assert (await store.get(progress.id)).queued == 9


async def test_progress_is_persisted_before_the_run_finishes() -> None:
    """The queued figure has to be readable while the run is still going."""
    adapter = FixtureSourceAdapter([build_site("rqa", 4)])
    harvester, _, store = make_harvester(adapter)
    seen: list[int] = []

    original = store.update

    async def watching(progress):
        seen.append(progress.queued)
        await original(progress)

    store.update = watching  # type: ignore[method-assign]
    await harvester.run(request(), principal=PRINCIPAL)
    assert seen[0] == 4, "queued should be known before any workbook is fetched"


async def test_a_scope_can_be_one_project() -> None:
    adapter = FixtureSourceAdapter([build_site("rqa", 9, project_count=3)])
    harvester, _, _ = make_harvester(adapter)

    progress = await harvester.run(
        request(scope=Scope(site="rqa", project="Project 1")), principal=PRINCIPAL
    )
    assert progress.queued == 3
    assert [p.project for p in progress.projects] == ["Project 1"]


# --------------------------------------------------------- failures do not stop it


async def test_a_failing_workbook_does_not_stop_the_run() -> None:
    site = build_site("rqa", 5)
    site.workbooks[2].fails_on = "fetch"
    harvester, repository, store = make_harvester(FixtureSourceAdapter([site]))

    progress = await harvester.run(request(), principal=PRINCIPAL)

    assert progress.state is HarvestState.COMPLETED
    assert progress.parsed == 4
    assert progress.failed == 1

    failures = await store.failures(progress.id)
    assert len(failures) == 1
    assert failures[0].workbook_luid == site.workbooks[2].luid
    assert failures[0].stage == "fetch"
    assert "could not download" in failures[0].error


async def test_a_parse_failure_is_attributed_to_the_parse_stage() -> None:
    site = build_site("rqa", 3)
    site.workbooks[0].fails_on = "parse"
    harvester, _, store = make_harvester(FixtureSourceAdapter([site]))

    progress = await harvester.run(request(), principal=PRINCIPAL)
    failures = await store.failures(progress.id)
    assert failures[0].stage == "parse"
    assert progress.parsed == 2


async def test_failures_are_counted_against_their_project() -> None:
    site = build_site("rqa", 6, project_count=2)
    site.workbooks[0].fails_on = "fetch"
    harvester, _, _ = make_harvester(FixtureSourceAdapter([site]))

    progress = await harvester.run(request(), principal=PRINCIPAL)
    failed = [p for p in progress.projects if p.failed]
    assert len(failed) == 1
    assert failed[0].project == site.workbooks[0].project


async def test_the_run_itself_fails_when_the_credential_cannot_be_resolved() -> None:
    harvester, _, _ = make_harvester(FixtureSourceAdapter([build_site("rqa", 2)]))
    progress = await harvester.run(
        request(credential_reference="tableau/absent"), principal=PRINCIPAL
    )
    assert progress.state is HarvestState.FAILED
    assert "no credential" in (progress.error or "")
    assert progress.parsed == 0


async def test_an_unknown_site_fails_the_run_not_a_workbook() -> None:
    harvester, _, _ = make_harvester(FixtureSourceAdapter([build_site("rqa", 2)]))
    progress = await harvester.run(request(scope=Scope(site="nope")), principal=PRINCIPAL)
    assert progress.state is HarvestState.FAILED
    assert "no site named" in (progress.error or "")


# ------------------------------------------------------------------- idempotency


async def test_re_harvest_of_an_unchanged_workbook_is_a_no_op() -> None:
    """S1.2.1: recorded as skipped_unchanged."""
    adapter = FixtureSourceAdapter([build_site("rqa", 4)])
    harvester, repository, store = make_harvester(adapter)

    first = await harvester.run(request(), principal=PRINCIPAL)
    assert first.parsed == 4
    nodes_after_first = dict(repository.nodes)
    parses_after_first = adapter.parses

    second = await harvester.run(request(), principal=PRINCIPAL)

    assert second.skipped_unchanged == 4
    assert second.parsed == 0
    assert adapter.parses == parses_after_first, "an unchanged workbook must not be re-parsed"
    assert repository.nodes.keys() == nodes_after_first.keys()
    state = await store.workbook_state("astra_estate_test", "rqa", "rqa-wb-00000")
    assert state.outcome is WorkbookOutcome.SKIPPED_UNCHANGED


async def test_a_changed_workbook_is_re_parsed_and_updates_the_same_nodes() -> None:
    site = build_site("rqa", 3)
    adapter = FixtureSourceAdapter([site])
    harvester, repository, _ = make_harvester(adapter)

    await harvester.run(request(), principal=PRINCIPAL)
    node_ids = set(repository.nodes)

    site.workbooks[1].name = "Daily VaR renamed"
    site.workbooks[1].revision = "2"

    second = await harvester.run(request(), principal=PRINCIPAL)

    assert second.parsed == 1
    assert second.skipped_unchanged == 2
    # Ids are derived from source identity, so the changed workbook updates in place
    # rather than adding a parallel copy of itself.
    assert set(repository.nodes) == node_ids
    renamed = [
        r for r in repository.nodes.values()
        if r["label"] == "Workbook" and r["properties"]["name"] == "Daily VaR renamed"
    ]
    assert len(renamed) == 1
    assert renamed[0]["properties"]["revision"] == "2"


async def test_ids_are_derived_from_source_identity() -> None:
    assert derive_id("rqa", "workbook:abc") == derive_id("rqa", "workbook:abc")
    assert derive_id("rqa", "workbook:abc") != derive_id("gtaa", "workbook:abc")
    assert derive_id("rqa", "workbook:abc") != derive_id("rqa", "workbook:abd")

    identifier = derive_id("rqa", "workbook:abc")
    assert len(identifier) == 26
    assert identifier[0] in "01234567"


async def test_derived_ids_satisfy_the_ontology() -> None:
    """They are written as ULIDs, so they must pass the ULID validator (S1.1.1)."""
    from astra_graph.ontology.properties import PropertySpec, PropertyType, coerce

    spec = PropertySpec("id", PropertyType.ULID)
    for index in range(500):
        coerce(spec, derive_id("rqa", f"workbook:{index}/sheet:{index}"))


# ------------------------------------------------------------- parse quality hold


async def test_a_workbook_below_the_threshold_is_held_not_failed() -> None:
    """Spec §4.1.4: written, and held out of CLUSTERED until reviewed."""
    site = build_site("rqa", 2)
    site.workbooks[0].unrecognised = ("RAWSQL_INT(...)", "SCRIPT_REAL(...)")
    harvester, repository, store = make_harvester(FixtureSourceAdapter([site]))

    progress = await harvester.run(request(), principal=PRINCIPAL)

    assert progress.held == 1
    assert progress.parsed == 1
    assert progress.failed == 0
    state = await store.workbook_state("astra_estate_test", "rqa", site.workbooks[0].luid)
    assert state.parse_quality < 1.0
    assert store.unrecognised[("astra_estate_test", "rqa", site.workbooks[0].luid)]
    # Held, not discarded: the workbook is in the graph.
    assert any(
        r["properties"].get("luid") == site.workbooks[0].luid
        for r in repository.nodes.values()
        if r["label"] == "Workbook"
    )


async def test_parse_quality_p50_is_reported() -> None:
    harvester, _, _ = make_harvester(FixtureSourceAdapter([build_site("rqa", 3)]))
    progress = await harvester.run(request(), principal=PRINCIPAL)
    assert progress.parse_quality_p50 == 1.0


# ------------------------------------------------------------------ credentials


def test_a_credential_reference_is_not_a_secret() -> None:
    validate_reference("tableau/rqa")
    with pytest.raises(CredentialError, match="must look like"):
        validate_reference("a-personal-access-token")
    with pytest.raises(CredentialError):
        validate_reference("../../etc/passwd")


async def test_a_resolved_credential_does_not_leak_in_logs() -> None:
    credential = await CREDENTIALS.resolve("tableau/rqa")
    assert "a-personal-access-token" not in repr(credential)
    assert "a-personal-access-token" not in str(credential)
    assert credential.secret() == "a-personal-access-token"


async def test_the_environment_provider_says_what_to_set(monkeypatch) -> None:
    monkeypatch.delenv("ASTRA_CREDENTIAL_TABLEAU_RQA", raising=False)
    with pytest.raises(CredentialError, match="ASTRA_CREDENTIAL_TABLEAU_RQA"):
        await EnvironmentCredentialProvider().resolve("tableau/rqa")


# -------------------------------------------------------------------- the API


async def test_starting_a_harvest_returns_immediately(client, harvest_app) -> None:
    response = await client.post(
        "/v1/harvests",
        json={"site": "rqa", "credential": "tableau/rqa"},
        headers=ARTIZENT_HEADERS,
    )
    assert response.status_code == 202
    body = response.json()
    assert len(body["id"]) == 26
    assert body["scope"]["site"] == "rqa"


async def test_a_secret_in_the_credential_field_is_rejected(client, harvest_app) -> None:
    response = await client.post(
        "/v1/harvests",
        json={"site": "rqa", "credential": "pat-abcdef0123456789"},
        headers=ARTIZENT_HEADERS,
    )
    assert response.status_code == 400
    assert "must look like" in response.json()["message"]


async def test_harvest_requires_an_artizent_role(client, harvest_app) -> None:
    response = await client.post(
        "/v1/harvests", json={"site": "rqa", "credential": "tableau/rqa"}, headers=HEADERS
    )
    assert response.status_code == 403


async def test_a_project_without_a_site_is_rejected(client, harvest_app) -> None:
    response = await client.post(
        "/v1/harvests",
        json={"project": "Risk Core", "credential": "tableau/rqa"},
        headers=ARTIZENT_HEADERS,
    )
    assert response.status_code == 400
    assert "within a named site" in response.json()["message"]


async def test_progress_is_readable_over_http(client, harvest_app) -> None:
    harvester, store = harvest_app
    progress = await harvester.run(request(), principal=PRINCIPAL)

    response = await client.get(f"/v1/harvests/{progress.id}", headers=ARTIZENT_HEADERS)
    assert response.status_code == 200
    body = response.json()
    assert body["state"] == "COMPLETED"
    assert body["totals"]["parsed"] == body["totals"]["queued"]
    assert body["projects"]
    assert body["adapter"]["name"] == "fixture"


async def test_failures_are_listed_over_http(client, harvest_app) -> None:
    harvester, store = harvest_app
    site = build_site("rqa", 3)
    site.workbooks[1].fails_on = "fetch"
    harvester._adapter = FixtureSourceAdapter([site])

    progress = await harvester.run(request(), principal=PRINCIPAL)
    response = await client.get(
        f"/v1/harvests/{progress.id}/failures", headers=ARTIZENT_HEADERS
    )
    assert response.status_code == 200
    body = response.json()
    assert body["count"] == 1
    assert body["failures"][0]["stage"] == "fetch"
    assert body["failures"][0]["workbook_name"] == site.workbooks[1].name


async def test_an_unknown_harvest_is_404(client, harvest_app) -> None:
    from astra_graph.ids import new_ulid

    response = await client.get(f"/v1/harvests/{new_ulid()}", headers=ARTIZENT_HEADERS)
    assert response.status_code == 404


async def test_harvests_are_listed_most_recent_first(client, harvest_app) -> None:
    harvester, _ = harvest_app
    await harvester.run(request(), principal=PRINCIPAL)
    response = await client.get("/v1/harvests", headers=ARTIZENT_HEADERS)
    assert response.status_code == 200
    assert len(response.json()["harvests"]) >= 1


async def test_a_deployment_without_an_adapter_says_so(client) -> None:
    """No adapter enabled is a fact about the deployment, reported as such."""
    response = await client.post(
        "/v1/harvests",
        json={"site": "rqa", "credential": "tableau/rqa"},
        headers=ARTIZENT_HEADERS,
    )
    assert response.status_code == 400
    assert "no source adapter is enabled" in response.json()["message"]


# ------------------------------------------------- S2.1.1: the interface version


async def test_every_harvest_records_the_interface_version() -> None:
    """S2.1.1 criterion 4, the harvest half.

    The version is on the adapter record so a harvest can be read, months later, against the
    contract that produced it — which matters most when the contract has moved on since.
    """
    harvester, _repository, store = make_harvester(FixtureSourceAdapter([build_site("rqa", 2)]))

    progress = await harvester.run(request(), principal=PRINCIPAL)

    recorded = (await store.get(progress.id)).adapter
    assert recorded["interface_version"] == INTERFACE_VERSION
    assert recorded["name"] and recorded["version"] and recorded["grammar_version"]


async def test_an_adapter_with_no_interface_version_cannot_harvest() -> None:
    """Checked, not assumed. An adapter reporting a blank version would otherwise be
    recorded silently, and the record's whole purpose is to say which contract this was."""

    class Unversioned(FixtureSourceAdapter):
        def manifest(self) -> AdapterManifest:
            base = super().manifest()
            return AdapterManifest(
                name=base.name,
                version=base.version,
                grammar_version=base.grammar_version,
                interface_version="  ",
                capabilities=base.capabilities,
            )

    harvester, _repository, _store = make_harvester(Unversioned([build_site("rqa", 1)]))

    with pytest.raises(AdapterError, match="no interface version"):
        await harvester.run(request(), principal=PRINCIPAL)


async def test_the_platform_can_harvest_through_an_out_of_process_adapter() -> None:
    """S2.1.1 criterion 2, from the platform's side.

    The Harvester is written against §6.1 and cannot tell a `RemoteAdapter` from an
    in-process one — which is the whole point, and is why this test asserts the *result* is
    identical rather than merely that the call worked. When the Tableau adapter lands (F2.2)
    it is configured by URL and nothing here changes.

    Driven through an in-process ASGI transport rather than a socket: the routing, the
    codecs and the error translation are the same code, and the SDK's own suite covers the
    socket and the crash.
    """
    import httpx
    from astra_adapter.rpc import RemoteAdapter, create_app

    site = build_site("rqa", 3)
    local = FixtureSourceAdapter([site])
    remote = RemoteAdapter(
        "http://adapter",
        client=httpx.AsyncClient(
            transport=httpx.ASGITransport(app=create_app(FixtureSourceAdapter([site]))),
            base_url="http://adapter",
        ),
    )
    await remote.connect()

    near, _repo_a, store_a = make_harvester(local)
    far, _repo_b, store_b = make_harvester(remote)
    try:
        in_process = await near.run(request(), principal=PRINCIPAL)
        out_of_process = await far.run(request(), principal=PRINCIPAL)

        assert out_of_process.state is in_process.state
        counts = ("queued", "parsed", "held", "failed", "skipped_unchanged", "drifted")
        assert [getattr(out_of_process, name) for name in counts] == [
            getattr(in_process, name) for name in counts
        ]
        assert out_of_process.parsed == 3
        assert out_of_process.parse_quality_p50 == in_process.parse_quality_p50
        assert (await store_b.get(out_of_process.id)).adapter == (
            await store_a.get(in_process.id)
        ).adapter
    finally:
        await remote.aclose()
