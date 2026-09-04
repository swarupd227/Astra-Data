from __future__ import annotations

import os
from collections.abc import AsyncIterator

import pytest
from httpx import ASGITransport, AsyncClient

os.environ.setdefault("ASTRA_POSTGRES_PASSWORD", "test")
os.environ.setdefault("ASTRA_GRAPH_NAME", "astra_estate_test")

from astra_graph.artefacts import InMemoryArtefactStore
from astra_graph.context import ContextAssembler
from astra_graph.grammar import InMemoryIssueStore, LocalIssueTracker
from astra_graph.principal import PRINCIPAL_HEADER, Principal
from astra_graph.provenance import ContextVerifier, InMemoryProvenanceStore
from astra_graph.retention import InMemoryProgrammeStore
from astra_graph.roles import ROLES_HEADER
from astra_graph.scope import InMemoryScopeStore
from astra_graph.writes import EdgeWrite, GraphWriter, NodeWrite

from .fakes import InMemoryGraphRepository, historical

PRINCIPAL = "agent:harvester"
HEADERS = {PRINCIPAL_HEADER: PRINCIPAL}
ARTIZENT_HEADERS = {**HEADERS, ROLES_HEADER: "migration_engineer"}
CLIENT_HEADERS = {**HEADERS, ROLES_HEADER: "client_report_owner"}


@pytest.fixture
def repository() -> InMemoryGraphRepository:
    return InMemoryGraphRepository()


@pytest.fixture
def writer(repository: InMemoryGraphRepository) -> GraphWriter:
    return GraphWriter(repository)


@pytest.fixture
async def client(repository: InMemoryGraphRepository) -> AsyncIterator[AsyncClient]:
    """The app wired to the in-memory store, with lifespan bypassed.

    The real lifespan opens a connection pool; these tests are about the write path, not
    the pool, so state is populated directly.
    """
    from astra_graph.main import create_app

    app = create_app()
    app.state.repository = repository
    app.state.writer = GraphWriter(repository)
    app.state.assembler = ContextAssembler(repository)
    app.state.scope_store = InMemoryScopeStore()
    app.state.issue_store = InMemoryIssueStore()
    app.state.issue_tracker = LocalIssueTracker()
    app.state.provenance_store = InMemoryProvenanceStore()
    app.state.programme_store = InMemoryProgrammeStore()
    app.state.artefact_store = InMemoryArtefactStore()

    # Time travel in the fake is a bounded replay; in production it is an indexed read
    # over the same outbox. The integration suite is where the two are shown to agree.
    async def assembler_at(version: int) -> ContextAssembler:
        return ContextAssembler(await historical(repository, version))

    async def current_version() -> int:
        version, _at = await repository.current_version()
        return version

    app.state.verifier = ContextVerifier(assembler_at, current_version=current_version)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://graph-svc") as async_client:
        yield async_client


@pytest.fixture
def harvest_app(client, repository: InMemoryGraphRepository):
    """Attach a fixture-adapter harvester to the app under test.

    The app itself decides whether an adapter is enabled (``harvest_setup``); these tests
    attach one explicitly so both states — with and without — can be exercised.
    """
    from astra_graph.adapters.fixture import FixtureSourceAdapter, build_site
    from astra_graph.credentials import StaticCredentialProvider
    from astra_graph.harvest import Harvester, InMemoryHarvestStore

    store = InMemoryHarvestStore()
    harvester = Harvester(
        adapter=FixtureSourceAdapter([build_site("rqa", 4)]),
        writer=GraphWriter(repository),
        store=store,
        credentials=StaticCredentialProvider({"tableau/rqa": "a-personal-access-token"}),
        graph_name="astra_estate_test",
    )
    app = client._transport.app
    app.state.harvester = harvester
    app.state.harvest_store = store
    app.state.harvest_tasks = set()
    return harvester, store


@pytest.fixture
def valid_site() -> dict[str, object]:
    return {
        "type": "Site",
        "properties": {"luid": "6f1b2c3d-0001", "name": "RQA", "user_count": 412},
    }


@pytest.fixture
def valid_workbook() -> dict[str, object]:
    return {
        "type": "Workbook",
        "properties": {"luid": "8f3e-daily-var", "name": "Daily VaR", "revision": "14"},
    }


async def seed_estate(writer: GraphWriter, *, suffix: str = "") -> dict[str, str]:
    """A small estate shaped like the §3.4 worked example.

        Site → Project → Workbook → Worksheet → Datasource → Field
                                  → Dashboard
        Worksheet ─ENCODES→ CalculatedField ─DEPENDS_ON→ CalculatedField ─DEPENDS_ON→ Field
                                            ─DEPENDS_ON→ Parameter
        Field ─MAPS_TO→ ModelTable

    Returned as a name-to-id map so a test can say what it means, plus the LUIDs it
    used. Used by both the in-memory suite and the integration suite, so the two assert
    against the same shape.

    ``suffix`` makes the source identifiers unique. The integration database persists
    between runs, so a fixed LUID would have one run's lookup find an earlier run's
    workbook.
    """
    principal = Principal("agent:harvester", run_id="run-seed")

    def node(type_: str, **properties: object) -> NodeWrite:
        return NodeWrite(type=type_, properties=properties)

    created = await writer.write_nodes(
        [
            node("Site", luid=f"rqa{suffix}", name="RQA"),
            node("Project", luid=f"risk-core{suffix}", name="Risk Core"),
            node("Workbook", luid=f"8f3e-daily-var{suffix}", name="Daily VaR", revision="14"),
            node("Worksheet", name="VaR by Desk", rows_shelf=["Desk"],
                 cols_shelf=["Date"], marks_shelf=[]),
            node("Dashboard", name="Risk Overview", layout_json={"zones": []},
                 contained_sheets=["VaR by Desk"]),
            node("Datasource", name="Positions", type="published", luid=f"ds-positions{suffix}"),
            node("Field", name="Notional", datatype="real", role="measure"),
            node("CalculatedField", name="Margin %", formula="SUM([M]) / SUM([R])",
                 formula_ast={"op": "DIV"}),
            node("CalculatedField", name="Base Margin", formula="SUM([M])",
                 formula_ast={"fn": "SUM"}),
            node("Parameter", name="As Of", datatype="date", domain="range"),
            node("ModelTable", name="fact_positions", mode="import", family_ref="fam_fixture"),
        ],
        principal=principal,
    )
    names = [
        "site", "project", "workbook", "worksheet", "dashboard", "datasource",
        "field", "calc", "nested_calc", "parameter", "model_table",
    ]
    seeded = {
        name: str(record["properties"]["id"])
        for name, record in zip(names, created, strict=True)
    }
    seeded["workbook_luid"] = f"8f3e-daily-var{suffix}"
    seeded["site_luid"] = f"rqa{suffix}"

    edges = [
        ("CONTAINS", "site", "project", {}),
        ("CONTAINS", "project", "workbook", {}),
        ("CONTAINS", "workbook", "worksheet", {}),
        ("CONTAINS", "workbook", "dashboard", {}),
        ("USES_DATASOURCE", "worksheet", "datasource", {}),
        ("HAS_FIELD", "datasource", "field", {}),
        ("ENCODES", "worksheet", "calc", {"shelf": "rows"}),
        ("DEPENDS_ON", "calc", "nested_calc", {"position_in_ast": "args[0]"}),
        ("DEPENDS_ON", "nested_calc", "field", {"position_in_ast": "args[0]"}),
        ("DEPENDS_ON", "calc", "parameter", {"position_in_ast": "args[1]"}),
        ("MAPS_TO", "field", "model_table", {"target_column": "notional"}),
    ]
    for edge_type, source, target, properties in edges:
        await writer.write_edge(
            EdgeWrite(
                type=edge_type,
                from_id=seeded[source],
                to_id=seeded[target],
                properties=properties,
            ),
            principal=principal,
        )
    return seeded


@pytest.fixture
async def seeded(repository: InMemoryGraphRepository) -> dict[str, str]:
    return await seed_estate(GraphWriter(repository))
