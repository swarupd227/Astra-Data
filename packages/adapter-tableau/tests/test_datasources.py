"""S2.2.2 — published datasources, embedded extracts, and no stored credentials.

    "Published datasources are captured as Datasource nodes with published: true and their
    connection graph. Embedded Hyper extracts are detected; the adapter records the extract
    schema and refresh schedule; extract data is not copied. Connection credentials are never
    stored; the adapter references a Key Vault secret by name."

The third criterion is the one with teeth, and it gets the most tests: a `.twb` is XML a
person edited, and real workbooks carry `username=` and sometimes `password=` on their
connections. An adapter that let those through would put them in the Estate Graph, in the
event stream S1.1.3 makes permanent and replayable, and in every context an agent is given.
"""

from __future__ import annotations

import pytest
from astra_adapter import AdapterError, Scope

from astra_adapter_tableau.datasource import read_structure
from astra_adapter_tableau.golden import workbook_xml
from astra_adapter_tableau.secrets import SECRET_ATTRIBUTES, secret_reference, strip_secrets

from .conftest import adapter_for
from .fake_tableau import FakeTableau, FakeWorkbook

SCOPE = Scope(site="golden")


def nodes_of(result, kind: str) -> list:
    return [node for node in result.nodes if node.type == kind]


def edges_of(result, kind: str) -> list:
    return [edge for edge in result.edges if edge.type == kind]


async def parsed(adapter, luid: str | None = None):
    refs = [ref async for ref in adapter.enumerate(SCOPE)]
    ref = refs[0] if luid is None else next(r for r in refs if r.luid == luid)
    return await adapter.parse(await adapter.fetch(ref))


# --------------------------------------------------- published datasources


async def test_a_published_datasource_is_captured_as_published(adapter) -> None:
    """S2.2.2's first criterion.

    §4.1.1 spells the story's "published: true" as ``type: embedded | published``, and the
    ontology is machine-checked against that table — so the specification's name is the one
    on the node. ADR 0016.
    """
    result = await parsed(adapter)
    datasources = nodes_of(result, "Datasource")

    published = [node for node in datasources if node.properties["type"] == "published"]
    embedded = [node for node in datasources if node.properties["type"] == "embedded"]

    assert len(published) == 1
    assert published[0].properties["name"] == "Reference Rates"
    assert embedded, "and the embedded ones are still captured"


async def test_only_a_published_datasource_carries_a_luid(adapter) -> None:
    """§4.1.1's own note. It is how the file distinguishes the two, and it is what lets a
    published datasource shared by forty workbooks be recognised as one thing."""
    result = await parsed(adapter)

    for node in nodes_of(result, "Datasource"):
        if node.properties["type"] == "published":
            assert node.properties["luid"], node.properties["name"]
        else:
            assert node.properties["luid"] is None


async def test_the_connection_graph_is_captured(adapter) -> None:
    """ "…and their connection graph": Datasource → Connection → Table, with the fields."""
    result = await parsed(adapter)

    connections = nodes_of(result, "Connection")
    tables = nodes_of(result, "Table")

    assert {node.properties["class"] for node in connections} == {"sqlserver", "postgres"}
    assert any(node.properties["server"] == "warehouse.internal" for node in connections)
    assert tables

    # The chain is connected, not three disconnected sets of nodes.
    datasource_keys = {node.key for node in nodes_of(result, "Datasource")}
    connection_keys = {node.key for node in connections}
    connects = edges_of(result, "CONNECTS_TO")
    assert any(e.from_key in datasource_keys and e.to_key in connection_keys for e in connects)
    assert any(e.from_key in connection_keys for e in connects)


async def test_a_datasource_hangs_off_the_sheets_that_use_it(adapter) -> None:
    """§4.1.2 runs USES_DATASOURCE from Worksheet. A datasource attached to nothing is an
    orphan the platform cannot place, which is why sheet *names* are read here even though
    their contents are S2.3.2's."""
    result = await parsed(adapter)

    uses = edges_of(result, "USES_DATASOURCE")
    sheet_keys = {node.key for node in nodes_of(result, "Worksheet")}
    datasource_keys = {node.key for node in nodes_of(result, "Datasource")}

    assert uses
    assert all(edge.from_key in sheet_keys for edge in uses)
    assert all(edge.to_key in datasource_keys for edge in uses)


async def test_custom_sql_is_kept_byte_for_byte(adapter) -> None:
    """§4.1.1 requires it, and §6.2's live-replay executor runs it verbatim — a normalised
    copy would execute differently from the report the client actually saw."""
    result = await parsed(adapter)

    sql = [
        node.properties["custom_sql"]
        for node in nodes_of(result, "Table")
        if node.properties.get("custom_sql")
    ]

    assert sql
    assert "select * from risk.positions where as_of_date > current_date - 30" in sql[0]


async def test_a_bracketed_table_name_is_split_correctly(adapter) -> None:
    """``[dbo].[fx_rates]``. Unbracketing the whole string and then splitting gives ``dbo]``
    and ``[fx_rates`` — wrong in a way that looks right in a log line and produces a Table
    node nothing can be matched against."""
    result = await parsed(adapter)

    tables = {
        (node.properties.get("schema"), node.properties["name"])
        for node in nodes_of(result, "Table")
    }

    assert ("dbo", "fx_rates") in tables


async def test_the_extract_engine_is_not_a_connection(adapter) -> None:
    """Tableau writes a ``dataengine`` connection inside a datasource that has an extract,
    pointing at the .hyper in the package. It is the extract, not somewhere data comes from —
    and modelling it as a Connection would derive a Key Vault reference for a credential that
    does not exist."""
    result = await parsed(adapter)

    classes = {node.properties["class"] for node in nodes_of(result, "Connection")}

    assert "hyper" not in classes
    assert classes == {"sqlserver", "postgres"}


# -------------------------------------------------------------- extracts


async def test_an_embedded_extract_is_detected_with_its_schema(adapter) -> None:
    """S2.2.2's second criterion. The *schema* — what Tableau materialised — is metadata the
    Modeller needs to plan a Fabric table. The rows are the client's."""
    result = await parsed(adapter)

    with_extract = [
        node for node in nodes_of(result, "Datasource") if node.properties["extract_flag"]
    ]
    assert len(with_extract) == 1, "the extract belongs to one datasource, not to all of them"
    assert with_extract[0].properties["name"] == "Positions"

    key = with_extract[0].key
    fields = [edge.to_key for edge in edges_of(result, "HAS_FIELD") if edge.from_key == key]
    assert fields, "the extract's columns are the schema"


async def test_the_extracts_data_is_never_read(adapter, server: FakeTableau) -> None:
    """Two lines of defence, both checked: the download asks Tableau not to include the
    extract, and the archive reader never reads a data entry."""
    refs = [ref async for ref in adapter.enumerate(SCOPE)]
    raw = await adapter.fetch(refs[0])
    result = await adapter.parse(raw)

    assert b"THIS IS CLIENT DATA" not in raw.payload
    rendered = repr([node.properties for node in result.nodes])
    assert "THIS IS CLIENT DATA" not in rendered


async def test_the_refresh_schedule_is_recorded(adapter) -> None:
    """ "…and refresh schedule". Tableau's Metadata API reports when an extract *last*
    refreshed; the schedule governing it is a REST concept on the site's tasks. Both are
    recorded because they answer different questions — and "last refreshed nine weeks ago" is
    the one that reveals an abandoned report."""
    sites = await adapter.sites(SCOPE)

    schedules = sites[0].detail["extract_refresh_schedules"]
    assert schedules
    assert "Daily" in next(iter(schedules.values()))
    assert "02:00" in next(iter(schedules.values()))


async def test_a_credential_that_cannot_see_refresh_tasks_is_not_a_failure(
    server: FakeTableau,
) -> None:
    """Extract-refresh tasks need a site administrator on many deployments, and a harvest run
    by a content reader is a legitimate and common configuration. Tableau answers 403; an
    empty answer means "not visible to this credential", which is a fact rather than a gap."""
    server.extract_refresh_tasks = False
    adapter = adapter_for(server)
    try:
        sites = await adapter.sites(SCOPE)
    finally:
        await adapter.aclose()

    assert sites[0].detail["extract_refresh_schedules"] == {}


async def test_a_workbook_with_no_extract_says_so(server: FakeTableau) -> None:
    server.workbooks = [
        FakeWorkbook(luid="wb-1", name="Live", project="Risk", packaged=False, with_extract=False)
    ]
    adapter = adapter_for(server)
    try:
        result = await parsed(adapter)
    finally:
        await adapter.aclose()

    assert not any(node.properties["extract_flag"] for node in nodes_of(result, "Datasource"))


# ------------------------------------------------------------- credentials


async def test_an_embedded_password_is_never_stored(adapter) -> None:
    """S2.2.2's third criterion, and the one that matters most.

    The golden workbook carries ``password="hunter2"`` on a connection, because real ones do.
    It must appear nowhere in the fragment — not on a Connection, not on a Datasource, not in
    a property nobody looked at.
    """
    result = await parsed(adapter)

    rendered = repr([node.properties for node in result.nodes])
    assert "hunter2" not in rendered
    assert "svc_reporting" not in rendered, "a username is half a credential and client PII"


async def test_the_stripping_happens_before_anything_is_built() -> None:
    """Filtering on the way *out* would leave a window in which a credential is in a live
    object, and a later code path that read the element directly would miss the filter."""
    kept, removed = strip_secrets(
        {
            "class": "postgres",
            "server": "warehouse.internal",
            "username": "svc_reporting",
            "password": "hunter2",
            "authentication": "username-password",
        }
    )

    assert kept == {
        "class": "postgres",
        "server": "warehouse.internal",
        "authentication": "username-password",
    }
    assert removed == ("password", "username")


async def test_a_connection_references_a_key_vault_secret_by_name(adapter) -> None:
    """ "…the adapter references a Key Vault secret by name"."""
    result = await parsed(adapter)

    references = [
        node.properties["connection_ref"]
        for node in nodes_of(result, "Datasource")
        if node.properties.get("connection_ref")
    ]

    assert references
    assert all(reference.startswith("tableau/golden/") for reference in references)
    assert not any("hunter2" in reference for reference in references)


async def test_the_reference_is_stable_and_shared_across_workbooks() -> None:
    """A client with 400 workbooks has perhaps a dozen distinct connections. Deriving the
    reference from the connection's identity means they provision one secret, not four
    hundred."""
    first = secret_reference(
        site="rqa", connection_class="postgres", server="warehouse.internal", database="risk"
    )
    same = secret_reference(
        site="rqa", connection_class="postgres", server="warehouse.internal", database="risk"
    )
    other_database = secret_reference(
        site="rqa", connection_class="postgres", server="warehouse.internal", database="pnl"
    )

    assert first == same
    assert first != other_database


async def test_the_reference_does_not_spell_out_the_hostname() -> None:
    """A Key Vault secret name is not especially secret, but an internal hostname is exactly
    the kind of thing that ends up in a screenshot in a status deck. The reference only has to
    be stable and unique."""
    reference = secret_reference(
        site="rqa",
        connection_class="snowflake",
        server="acme-prod.eu-west-1.snowflakecomputing.com",
    )

    assert "acme-prod" not in reference
    assert "snowflake" in reference, (
        "the class stays legible so an operator can tell which is which"
    )


async def test_an_embedded_credential_is_reported_as_a_finding(adapter) -> None:
    """The client will have to rotate it, and the target model must not be built assuming the
    connection authenticates the way the old one did."""
    result = await parsed(adapter)

    modes = {node.properties.get("auth_mode") for node in nodes_of(result, "Connection")}

    assert "username-password" in modes or "embedded_credential" in modes


async def test_the_secrets_a_site_needs_are_reported(adapter) -> None:
    """A list an operator can provision against, rather than a missing secret discovered when
    the executor first tries to run a parity case months later."""
    refs = [ref async for ref in adapter.enumerate(SCOPE)]
    for ref in refs[:2]:
        await adapter.parse(await adapter.fetch(ref))

    sites = await adapter.sites(SCOPE)
    secrets = sites[0].detail["connection_secrets"]

    assert secrets
    first = next(iter(secrets.values()))
    assert "class" in first and "server" in first
    # `auth_mode` legitimately contains the word — "username-password" is Tableau's name for
    # a mode, not a credential. What must not be here is the secret itself.
    assert "hunter2" not in repr(secrets)
    assert "svc_reporting" not in repr(secrets)


def test_the_denylist_covers_the_obvious_names() -> None:
    """A denylist of *names* rather than a heuristic on values: a value-based rule both misses
    and over-matches, and a reviewer cannot tell what it will do."""
    for name in ("password", "pwd", "token", "secret", "api-key", "username"):
        assert name in SECRET_ATTRIBUTES


# ---------------------------------------------------------- parse quality


async def test_calculations_are_now_parsed_rather_than_held(adapter) -> None:
    """S2.3.1 replaced the behaviour this test used to assert.

    Before the grammar existed, every calculation was an unread construct and every workbook
    was held — correct then. Now the golden workbook's calculations parse, so it reports 1.0
    and the number measures the grammar rather than the absence of one.
    """
    result = await parsed(adapter)

    assert not result.unrecognised
    assert result.parse_quality == 1.0
    assert [node for node in result.nodes if node.type == "CalculatedField"]


async def test_an_unreadable_construct_is_retained_verbatim_and_holds_the_workbook(
    server: FakeTableau,
) -> None:
    """S2.3.1's third criterion: "a construct outside the grammar is captured verbatim and
    flagged, never dropped"."""
    server.workbooks = [
        FakeWorkbook(luid="wb-1", name="Odd", project="Risk", unreadable_calculation=True)
    ]
    adapter = adapter_for(server)
    try:
        result = await parsed(adapter)
    finally:
        await adapter.aclose()

    assert result.unrecognised
    assert "MADE_UP_FUNCTION" in result.unrecognised[0].construct
    assert result.parse_quality < 1.0, "and it holds the workbook"

    # Not dropped: the field is still in the estate, with its formula and an AST that says
    # where the gap is. Omitting it would lose the field entirely, which is worse.
    calculated = [node for node in result.nodes if node.type == "CalculatedField"]
    assert any(node.properties["name"] == "Odd" for node in calculated)


async def test_parse_quality_counts_constructs_not_calculations(adapter, server) -> None:
    """A workbook with one unreadable function in a fifty-node formula must not score the
    same as one whose calculation is entirely unreadable — §4.1.4's threshold is meant to
    tell them apart."""
    server.workbooks = [
        FakeWorkbook(luid="wb-1", name="Odd", project="Risk", unreadable_calculation=True)
    ]
    other = adapter_for(server)
    try:
        result = await parsed(other)
    finally:
        await other.aclose()

    assert result.constructs_total > result.constructs_recognised
    assert result.constructs_total > len(result.unrecognised), "constructs, not calculations"
    assert 0.5 < result.parse_quality < 1.0


async def test_a_workbook_with_no_calculations_parses_cleanly(server: FakeTableau) -> None:
    """The other side of the same rule: quality is low because calculations are unread, not
    because the parse is a stub."""
    server.workbooks = [FakeWorkbook(luid="wb-1", name="Plain", project="Risk", calculations=0)]
    adapter = adapter_for(server)
    try:
        result = await parsed(adapter)
    finally:
        await adapter.aclose()

    assert not result.unrecognised
    assert result.parse_quality == 1.0


# ------------------------------------------------------------ the contract


async def test_parse_is_a_function_of_the_bytes(adapter) -> None:
    """S2.1.2's parse round-trip check. An adapter whose parse depended on a cache populated
    by a previous call would produce a different fragment when a harvest resumed, which the
    platform reads as drift."""
    refs = [ref async for ref in adapter.enumerate(SCOPE)]
    raw = await adapter.fetch(refs[0])

    first = await adapter.parse(raw)
    second = await adapter.parse(raw)

    assert first == second


async def test_a_fragment_survives_the_wire(adapter) -> None:
    """Every parse result crosses the adapter RPC and is then written to the graph."""
    import json

    from astra_adapter.rpc import wire

    result = await parsed(adapter)
    restored = wire.decode_parse_result(json.loads(json.dumps(wire.encode_parse_result(result))))

    assert restored == result


async def test_a_workbook_that_is_not_xml_is_refused(adapter) -> None:
    from astra_adapter import RawAsset

    refs = [ref async for ref in adapter.enumerate(SCOPE)]
    raw = await adapter.fetch(refs[0])
    broken = RawAsset(
        ref=raw.ref,
        content_hash=raw.content_hash,
        payload=b"<workbook><unclosed>",
        size_bytes=20,
        media_type="application/xml",
    )

    with pytest.raises(AdapterError, match="could not be parsed"):
        await adapter.parse(broken)


def test_an_unmapped_connection_class_is_reported_not_guessed() -> None:
    """§4.1.1's enum is closed and the platform rejects a write outside it. A guess would fail
    at the graph, with an error about the graph rather than about the connection."""
    xml = workbook_xml("X").replace(b'class="postgres"', b'class="marklogic"')

    structure = read_structure(xml, site="golden", name="X")
    classes = {
        connection.connection_class
        for source in structure.datasources
        for connection in source.connections
    }

    assert "" in classes, "unmapped, rather than mapped to something plausible"
