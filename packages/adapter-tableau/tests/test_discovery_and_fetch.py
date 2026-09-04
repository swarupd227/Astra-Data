"""S2.2.1 — discovery and fetch, against a fake Tableau Server and Cloud.

"Discovery uses the Metadata API (GraphQL) for the object graph and the REST API for
downloads, with personal-access-token and connected-app authentication.
Fetch retrieves .twb and .twbx (unpacking the XML from the archive) and records the
revision id."
"""

from __future__ import annotations

import zipfile

import pytest
from astra_adapter import AdapterError, Scope

from astra_adapter_tableau import Credential, extract_workbook_xml
from astra_adapter_tableau.rest import Deployment

from .conftest import adapter_for
from .fake_tableau import (
    CLOUD_VERSION,
    SERVER_VERSION,
    FakeTableau,
    FakeWorkbook,
    credential_json,
    twbx_bytes,
    workbook_xml,
)

SCOPE = Scope(site="golden")


# --------------------------------------------------------------------- discovery


async def test_discovery_uses_both_apis(adapter, server: FakeTableau) -> None:
    """S2.2.1's first criterion. Neither API is sufficient alone.

    The Metadata API describes the estate's shape in a handful of queries and cannot hand you
    a file; the REST listing carries the ids a download needs and knows nothing about shape.
    Asserting that *both* were called is asserting the design, not the implementation.
    """
    refs = [ref async for ref in adapter.enumerate(SCOPE)]

    assert len(refs) == 5
    assert "workbooks" in server.calls, "the REST listing was not used"
    assert "graphql" in server.calls, "the Metadata API was not used"
    assert {ref.project for ref in refs} == {"Risk", "Trading"}


async def test_both_apis_are_paged(adapter, server: FakeTableau) -> None:
    """Tableau's GraphQL endpoint times out rather than capping a large query, and the REST
    listing simply truncates. A client that read page one of either would report a smaller
    estate than exists and never say so."""
    server.page_size_cap = 2

    refs = [ref async for ref in adapter.enumerate(SCOPE)]

    assert len(refs) == 5, "every workbook was found across pages"
    assert server.calls.count("workbooks") >= 3
    assert server.calls.count("graphql") >= 3


async def test_discovery_signs_in_once(adapter, server: FakeTableau) -> None:
    """A sign-in per call would be slow, and — for a personal access token — would invalidate
    the previous session each time, which is how two workers sharing a PAT sign each other out
    in a loop."""
    [ref async for ref in adapter.enumerate(SCOPE)]

    assert server.sign_ins == 1


async def test_the_estate_is_ordered_so_a_harvest_is_reproducible(adapter) -> None:
    """The Harvester reports progress per project (S1.2.1). Enumeration in whatever order the
    source returned would make two harvests of an unchanged estate report differently."""
    first = [ref.luid async for ref in adapter.enumerate(SCOPE)]
    second = [ref.luid async for ref in adapter.enumerate(SCOPE)]

    assert first == second
    assert first, "and non-empty"


async def test_a_project_scope_narrows_discovery(adapter) -> None:
    refs = [ref async for ref in adapter.enumerate(Scope(site="rqa", project="Risk"))]

    assert refs
    assert {ref.project for ref in refs} == {"Risk"}


async def test_discovery_works_without_the_metadata_api(server: FakeTableau) -> None:
    """A Tableau Server administrator can turn the Metadata API off, and many have.

    §6.1 makes that a fact about the deployment rather than a defect, so discovery falls back
    to the REST listing — and the fallback is *reported*, because a shallower estate labelled
    shallow is a different thing from an estate quietly missing its lineage.
    """
    server.metadata_enabled = False
    adapter = adapter_for(server)
    try:
        refs = [ref async for ref in adapter.enumerate(SCOPE)]
        sites = await adapter.sites(SCOPE)
    finally:
        await adapter.aclose()

    assert len(refs) == 5, "the estate is still discovered"
    assert sites[0].detail["metadata_api"] is False
    assert "disabled by a Tableau Server administrator" in sites[0].detail["detail"]


# ----------------------------------------------------------------------- fetching


async def test_a_packaged_workbook_is_unpacked(adapter, server: FakeTableau) -> None:
    """S2.2.1's second criterion: "unpacking the XML from the archive"."""
    refs = [ref async for ref in adapter.enumerate(SCOPE)]
    packaged = next(ref for ref in refs if ref.luid != "wb-00001")

    raw = await adapter.fetch(packaged)

    assert raw.media_type == "application/xml"
    assert raw.payload.lstrip().startswith(b"<?xml")
    assert b"<workbook" in raw.payload
    assert raw.payload[:2] != b"PK", "the archive, not the XML, would start with PK"


async def test_an_unpackaged_workbook_is_fetched_too(adapter, server: FakeTableau) -> None:
    """Both forms, which is why the fake estate contains one of each."""
    refs = [ref async for ref in adapter.enumerate(SCOPE)]
    bare = next(ref for ref in refs if ref.luid == "wb-00001")

    raw = await adapter.fetch(bare)

    assert raw.payload.lstrip().startswith(b"<?xml")
    assert b"<workbook" in raw.payload


async def test_the_revision_id_is_recorded(adapter) -> None:
    """S2.2.1: "records the revision id". The Harvester compares it to decide whether to
    re-parse (S1.2.4), so it has to be the source's identity for the content."""
    refs = [ref async for ref in adapter.enumerate(SCOPE)]
    raw = await adapter.fetch(refs[0])

    assert raw.ref.revision.startswith("rev:")
    assert raw.ref.revision != refs[0].revision, "resolved on fetch, not guessed at discovery"


async def test_the_newest_revision_wins(server: FakeTableau) -> None:
    """The fake returns revisions out of order on purpose: a client that took the first
    element would record whichever Tableau happened to list first."""
    server.workbooks = [FakeWorkbook(luid="wb-1", name="One", project="Risk", revisions=7)]
    adapter = adapter_for(server)
    try:
        ref = await anext(adapter.enumerate(SCOPE))
        raw = await adapter.fetch(ref)
    finally:
        await adapter.aclose()

    assert raw.ref.revision == "rev:7"


async def test_a_site_without_revision_history_falls_back_visibly(server: FakeTableau) -> None:
    """Revision history is a per-site setting that is often off. The fallback keeps the
    incremental harvest working and is *visible in the value*, so nobody later reads a
    timestamp as a revision number."""
    server.revision_history = False
    adapter = adapter_for(server)
    try:
        ref = await anext(adapter.enumerate(SCOPE))
        raw = await adapter.fetch(ref)
    finally:
        await adapter.aclose()

    assert raw.ref.revision.startswith("updated:")
    assert "rev:" not in raw.ref.revision


async def test_the_content_hash_is_over_the_xml_not_the_download(adapter) -> None:
    """A .twbx zip is not byte-stable — it records timestamps and orders entries as Tableau
    pleases. Hashing the download would make every re-harvest look like a change, and S1.2.4's
    incremental harvest would download the whole estate every night."""
    refs = [ref async for ref in adapter.enumerate(SCOPE)]

    first = await adapter.fetch(refs[0])
    second = await adapter.fetch(refs[0])

    assert first.content_hash == second.content_hash
    assert first.payload == second.payload


async def test_the_extract_is_not_downloaded_or_read(adapter, server: FakeTableau) -> None:
    """§16 and S2.2.2: extract data is not copied.

    Two lines of defence, and both are checked: the download asks Tableau not to include the
    extract, and the archive reader never reads a data entry even if one arrives.
    """
    refs = [ref async for ref in adapter.enumerate(SCOPE)]
    raw = await adapter.fetch(refs[0])

    assert b"THIS IS CLIENT DATA" not in raw.payload
    assert raw.size_bytes == len(raw.payload)


async def test_an_archive_that_still_carries_an_extract_is_still_not_read() -> None:
    """The second line of defence, on its own: a Tableau version that ignores the flag, or a
    proxy serving a cached copy, must not put client data into the platform's memory."""
    payload = twbx_bytes("Daily VaR", with_extract=True)

    archive = extract_workbook_xml(payload, name="Daily VaR")

    assert b"THIS IS CLIENT DATA" not in archive.xml
    assert archive.extracts == ("Data/Extracts/federated.hyper",)
    assert archive.packaged is True


async def test_fetching_something_never_discovered_is_refused(adapter) -> None:
    """Either a stale queue or a bug. Downloading it anyway would hide which."""
    [ref async for ref in adapter.enumerate(SCOPE)]
    ghost = type(await anext(adapter.enumerate(SCOPE)))(
        luid="wb-missing", name="Ghost", site="rqa", project="Risk", revision="1"
    )

    with pytest.raises(AdapterError, match="not in this adapter's discovery results"):
        await adapter.fetch(ghost)


# -------------------------------------------------------------- Server and Cloud


async def test_a_server_deployment_records_its_version(adapter) -> None:
    """S2.2.1's fourth criterion."""
    sites = await adapter.sites(SCOPE)

    assert len(sites) == 1
    assert sites[0].detail["product_version"] == SERVER_VERSION
    assert sites[0].detail["deployment"] == Deployment.SERVER.value
    assert sites[0].detail["supported"] is True


async def test_cloud_is_recognised_and_supported(server: FakeTableau) -> None:
    """Cloud is continuously updated and always beyond the Server floor. Comparing its
    version string to a Server release would be comparing two numbering schemes and getting a
    confident wrong answer."""
    server.cloud = True
    adapter = adapter_for(server, base_url="https://10ax.online.tableau.com")
    try:
        sites = await adapter.sites(SCOPE)
    finally:
        await adapter.aclose()

    assert sites[0].detail["deployment"] == Deployment.CLOUD.value
    assert sites[0].detail["product_version"] == CLOUD_VERSION
    assert sites[0].detail["supported"] is True


async def test_a_server_below_the_floor_is_refused(server: FakeTableau) -> None:
    """S2.2.1 supports 2021.4 and later. Harvesting an older one would produce an estate
    whose gaps nobody could account for — and the refusal happens before a credential is
    presented to it."""
    server.product_version = "2020.4.1"
    adapter = adapter_for(server)
    try:
        with pytest.raises(AdapterError) as caught:
            await adapter.sites(SCOPE)
    finally:
        await adapter.aclose()

    assert "below the 2021.4 floor" in str(caught.value)
    assert not caught.value.retryable
    assert server.sign_ins == 0, "refused before signing in"


async def test_a_rate_limited_server_is_not_mistaken_for_an_old_one(
    server: FakeTableau,
) -> None:
    """The first call is the one least likely to be throttled, which is why it had no backoff
    and why a throttled deployment was reported as "below the 2021.4 floor". A wrong diagnosis
    on the first call sends whoever reads it to check a version that was never the problem."""
    server.throttle_next = 2
    adapter = adapter_for(server)
    try:
        sites = await adapter.sites(SCOPE)
    finally:
        await adapter.aclose()

    assert sites[0].detail["product_version"] == SERVER_VERSION


# ----------------------------------------------------------------- authentication


async def test_a_personal_access_token_signs_in(server: FakeTableau) -> None:
    adapter = adapter_for(server, kind="personal_access_token")
    try:
        refs = [ref async for ref in adapter.enumerate(SCOPE)]
    finally:
        await adapter.aclose()

    assert refs
    assert server.sign_ins == 1


async def test_a_connected_app_signs_in_with_a_minted_jwt(server: FakeTableau) -> None:
    """The fake validates the JWT properly — signature, `kid`, issuer and subject — so this
    checks the token the adapter mints rather than that it sent something."""
    adapter = adapter_for(server, kind="connected_app")
    try:
        refs = [ref async for ref in adapter.enumerate(SCOPE)]
    finally:
        await adapter.aclose()

    assert refs
    assert server.sign_ins == 1


async def test_a_connected_app_token_with_the_wrong_secret_id_is_rejected(
    server: FakeTableau,
) -> None:
    """Tableau uses `kid` to select which of an app's secrets to validate against. Putting the
    client id there instead produces an authentication failure that names neither."""
    adapter = adapter_for(
        server,
        credential=Credential.from_json(credential_json("connected_app", secret_id="wrong")),
    )
    try:
        with pytest.raises(AdapterError) as caught:
            await adapter.sites(SCOPE)
    finally:
        await adapter.aclose()

    assert "unknown secret id" in str(caught.value)
    assert not caught.value.retryable


async def test_a_rejected_credential_is_not_retryable(server: FakeTableau) -> None:
    """S2.1.2's taxonomy, where it matters most: retrying with the same rejected credential
    fails identically, and a retryable classification would mark every workbook in a
    1,067-workbook estate as individually failed rather than stopping the run once."""
    adapter = adapter_for(
        server,
        credential=Credential.from_json(credential_json(secret="the-wrong-secret")),
    )
    try:
        with pytest.raises(AdapterError) as caught:
            await adapter.sites(SCOPE)
    finally:
        await adapter.aclose()

    assert not caught.value.retryable
    assert "Retrying with the same credential will fail the same way" in str(caught.value)


async def test_an_expired_session_is_signed_in_again_mid_harvest(
    adapter, server: FakeTableau
) -> None:
    """Tableau's default session is 240 minutes and a large harvest outlives it. A 401
    mid-harvest is a session that ended, not a failure — treating it as one loses the rest of
    the run."""
    refs = [ref async for ref in adapter.enumerate(SCOPE)]
    first = await adapter.fetch(refs[0])

    server.expire_sessions()
    second = await adapter.fetch(refs[1])

    assert first.payload and second.payload
    assert server.sign_ins == 2, "signed in again rather than failing"


async def test_an_incomplete_connected_app_credential_is_refused_before_use() -> None:
    """A missing username produces a JWT Tableau rejects with a generic error that names none
    of the four fields."""
    with pytest.raises(AdapterError) as caught:
        Credential.from_json(credential_json("connected_app", username=""))

    assert "connected app credential needs username" in str(caught.value)


async def test_a_credential_never_appears_in_its_own_repr() -> None:
    """The commonest way a secret escapes is a log line nobody wrote on purpose."""
    credential = Credential.from_json(credential_json())

    assert "a-personal-access-token" not in repr(credential)
    assert "a-personal-access-token" not in str(credential)
    assert credential.describe() == "personal access token 'astra'"


# --------------------------------------------------------------- the archive alone


def test_a_login_page_is_reported_as_a_login_page() -> None:
    """The usual cause of a "corrupt workbook" is a proxy in front of Tableau serving HTML
    with a 200. Parsing that as a workbook fails three layers from the cause."""
    with pytest.raises(AdapterError) as caught:
        extract_workbook_xml(b"<!DOCTYPE html><html><body>Sign in</body></html>", name="VaR")

    assert "is HTML, not a Tableau workbook" in str(caught.value)


def test_an_archive_with_no_workbook_is_refused() -> None:
    import io

    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("Data/Extracts/federated.hyper", b"data")

    with pytest.raises(AdapterError, match="contains no .twb"):
        extract_workbook_xml(buffer.getvalue(), name="VaR")


def test_an_archive_with_two_workbooks_is_refused() -> None:
    """Which one is the workbook is not something an adapter should guess: guessing would put
    a workbook in the estate under another workbook's identity."""
    import io

    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("one.twb", workbook_xml("One"))
        archive.writestr("two.twb", workbook_xml("Two"))

    with pytest.raises(AdapterError, match="contains 2 .twb files"):
        extract_workbook_xml(buffer.getvalue(), name="VaR")


def test_an_empty_download_is_retryable() -> None:
    """Nothing came back, which is a transport problem rather than a broken workbook."""
    with pytest.raises(AdapterError) as caught:
        extract_workbook_xml(b"", name="VaR")

    assert caught.value.retryable


# ------------------------------------------------------- what this story does not do


async def test_parsing_reads_the_datasources_and_the_calculations(adapter) -> None:
    """This test has been rewritten twice, which is the shape of the epic.

    S2.2.1 asserted `parse` refused outright. S2.2.2 asserted it read the datasources and held
    on the calculations. S2.3.1 built the grammar, so it now reads both — and the assertion
    that survives all three is that the adapter never claims to have read more than it has.
    """
    refs = [ref async for ref in adapter.enumerate(SCOPE)]
    result = await adapter.parse(await adapter.fetch(refs[0]))

    assert [node for node in result.nodes if node.type == "Datasource"]
    assert [node for node in result.nodes if node.type == "CalculatedField"]
    assert result.parse_quality == 1.0
    assert not result.unrecognised


async def test_only_what_this_deployment_can_execute_is_claimed(adapter) -> None:
    """S2.4.1 made `execute_case` real, so this test moved from "it refuses" to "it claims
    only what it can do". S2.4.2 did the same for `capture_visual` — see
    `test_execution.py`'s and `test_visual_capture.py`'s own tests for the behaviour.

    Extract read needs the Hyper API and live replay needs warehouse connectivity; neither is
    present here, so neither is claimed. Screenshot needs nothing this deployment might lack,
    so it is the one capability claimed unconditionally. §6.1 makes an unclaimed capability a
    fact about the deployment, and S2.1.2 makes a *claim* binding — the suite fails an adapter
    that claims one and cannot deliver it.
    """
    capabilities = adapter.manifest().capabilities

    assert capabilities.extract_read is False
    assert capabilities.live_query is False
    assert capabilities.screenshot is True


async def test_the_manifest_names_the_grammar_that_parsed(adapter) -> None:
    """S2.3.1's fourth criterion: the grammar is versioned and parse results record it.

    It used to read ``tableau-none-f2.3`` — a fact, where a blank would have been the absence
    of one. F2.3 supplied the grammar, and the version moved with it.
    """
    manifest = adapter.manifest()

    assert manifest.name == "tableau"
    assert manifest.grammar_version == "tableau-1"
    assert manifest.interface_version == "1.1", "retyped columns at S2.4.1"

    ast = await adapter.parse_calc("SUM([Sales])")
    assert ast.grammar_version == manifest.grammar_version


async def test_the_adapter_is_registered_under_its_own_name() -> None:
    """§20: "A new source adapter is a repository that passes the harness." The registry finds
    it through the entry point, which is the path S2.1.1 built for exactly this moment."""
    from astra_adapter import load_adapter, registered_names

    assert "tableau" in registered_names()

    import os

    os.environ.setdefault("ASTRA_TABLEAU_URL", "https://tableau.client.example")
    built = load_adapter("tableau")
    assert built.manifest().name == "tableau"


async def test_asking_for_tableau_now_finds_it() -> None:
    """S2.1.1's CLI said "F2.2 builds it". F2.2 has."""
    from astra_adapter.registry import registered_names

    assert {"fake", "tableau"} <= set(registered_names())
