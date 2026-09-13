"""The Migration Unit page -- story S10.3.1, opening F10.3, against real PostgreSQL +
Apache AGE.

What only the real store can answer: that a real workbook's own real train/family/
owner/scope facts, a real G3 card, real G2/G4 gate decisions, a real Exception Case and
a real outbox all assemble into one page exactly as `mu_page.py`'s own module docstring
disclosed, that a client-role response really is a strict slice (not the full document
with a role check bolted on the wire), and that Provenance stays off the main response
and lazily reachable only for an Artizent reader.
"""

from __future__ import annotations

import asyncio
import os
import threading
from collections.abc import Callable, Coroutine
from datetime import UTC, datetime
from typing import Any

import pytest

pytestmark = pytest.mark.integration

asyncpg = pytest.importorskip("asyncpg")
httpx = pytest.importorskip("httpx")

from astra_graph.artefacts import PostgresArtefactStore  # noqa: E402
from astra_graph.config import Settings  # noqa: E402
from astra_graph.context.contract import ContractName  # noqa: E402
from astra_graph.errors import ElementNotFoundError  # noqa: E402
from astra_graph.events import source_for  # noqa: E402
from astra_graph.graph import AgeGraphRepository, create_pool  # noqa: E402
from astra_graph.graph.queries import accessor  # noqa: E402
from astra_graph.ids import new_ulid  # noqa: E402
from astra_graph.migrations import run as run_migrations  # noqa: E402
from astra_graph.mu_page import mu_page, mu_page_client_view, mu_provenance  # noqa: E402
from astra_graph.ontology import EDGE_LABELS, NODE_LABELS  # noqa: E402
from astra_graph.principal import PRINCIPAL_HEADER, Principal  # noqa: E402
from astra_graph.provenance import (  # noqa: E402
    AgentMode,
    PostgresProvenanceStore,
    ProvenanceRecord,
)
from astra_graph.report_deploy import PostgresReportDeployStore  # noqa: E402
from astra_graph.roles import ROLES_HEADER  # noqa: E402
from astra_graph.scope import DecisionKind, PostgresScopeStore, new_decision  # noqa: E402
from astra_graph.writes import EdgeWrite, GraphWriter, NodeWrite  # noqa: E402

from .conftest import seed_estate  # noqa: E402

PRINCIPAL = Principal("agent:harvester", run_id="run-mu-page")


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


async def _drop_graph(conn: asyncpg.Connection, graph: str) -> None:
    await conn.execute("LOAD 'age'")
    exists = await conn.fetchval(
        "SELECT EXISTS (SELECT 1 FROM ag_catalog.ag_graph WHERE name = $1)", graph
    )
    if not exists:
        return
    await conn.execute("DELETE FROM public.estate_edge_index WHERE graph = $1", graph)
    await conn.execute("DELETE FROM public.estate_element_index WHERE graph = $1", graph)
    await conn.execute("SELECT ag_catalog.drop_graph($1, true)", graph)


@pytest.fixture(scope="module")
def settings() -> Settings:
    config = _settings(f"astra_mupage_{new_ulid()[10:22].lower()}")

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
            await _drop_graph(conn, config.graph_name)
        finally:
            await conn.close()

    if not _run_off_loop(setup):
        pytest.skip("PostgreSQL with Apache AGE not reachable")

    yield config
    _run_off_loop(teardown)


@pytest.fixture
async def pool(settings: Settings):
    p = await create_pool(settings)
    try:
        yield p
    finally:
        await p.close()


@pytest.fixture
def repository(pool, settings: Settings) -> AgeGraphRepository:
    return AgeGraphRepository(pool, graph_name=settings.graph_name)


@pytest.fixture
def writer(repository: AgeGraphRepository, settings: Settings) -> GraphWriter:
    return GraphWriter(repository, event_source=source_for(settings.graph_name))


@pytest.fixture
def artefact_store(pool, settings: Settings) -> PostgresArtefactStore:
    return PostgresArtefactStore(pool, graph_name=settings.graph_name)


@pytest.fixture
def report_deploy_store(pool, settings: Settings) -> PostgresReportDeployStore:
    return PostgresReportDeployStore(pool, graph_name=settings.graph_name)


@pytest.fixture
def scope_store(pool, settings: Settings) -> PostgresScopeStore:
    return PostgresScopeStore(pool, graph_name=settings.graph_name)


@pytest.fixture
def provenance_store(pool, settings: Settings) -> PostgresProvenanceStore:
    return PostgresProvenanceStore(pool, graph_name=settings.graph_name)


async def _read_page(pool, settings, artefact_store, report_deploy_store, scope_store, workbook_id: str):
    return await mu_page(
        pool, settings.graph_name,
        artefact_store=artefact_store, report_deploy_store=report_deploy_store, scope_store=scope_store,
        workbook_id=workbook_id,
    )


async def _write_family(writer: GraphWriter, *, workbook_id: str, name: str = "Risk Positions") -> str:
    family_id = new_ulid()
    await writer.write_nodes(
        [NodeWrite(type="ModelFamily", id=family_id, properties={"name": name, "state": "PROPOSED", "grain": "", "conformed_dims": []})],
        principal=PRINCIPAL,
    )
    await writer.write_edge(
        EdgeWrite(type="IN_FAMILY", id=new_ulid(), from_id=workbook_id, to_id=family_id, properties={"confidence": 1.0}),
        principal=PRINCIPAL,
    )
    return family_id


async def _write_train(writer: GraphWriter, *, workbook_id: str, state: str = "CLUSTERED") -> str:
    train_id = new_ulid()
    await writer.write_nodes(
        [NodeWrite(type="ReleaseTrain", id=train_id, properties={"name": "Train 1", "planned_start": "2027-01-01", "planned_end": "2027-01-31"})],
        principal=PRINCIPAL,
    )
    await writer.write_edge(
        EdgeWrite(type="IN_TRAIN", id=new_ulid(), from_id=workbook_id, to_id=train_id, properties={"sequence": 1, "state": state}),
        principal=PRINCIPAL,
    )
    return train_id


async def _write_owner(writer: GraphWriter, *, workbook_id: str, upn: str = "a.mehta@rqa.example", display: str = "A. Mehta") -> str:
    user_id = new_ulid()
    await writer.write_nodes(
        [NodeWrite(type="User", id=user_id, properties={"upn": upn, "display": display, "side": "source"})],
        principal=PRINCIPAL,
    )
    await writer.write_edge(
        EdgeWrite(type="OWNED_BY", id=new_ulid(), from_id=workbook_id, to_id=user_id, properties={}),
        principal=PRINCIPAL,
    )
    return user_id


async def _write_gate_decision(writer: GraphWriter, *, gate: str, subject_ref: str, decision: str = "APPROVED") -> str:
    created = await writer.write_nodes(
        [
            NodeWrite(
                type="GateDecision",
                properties={
                    "gate": gate, "subject_ref": subject_ref, "decision": decision,
                    "approver": "user:owner@client.example",
                    "timestamp": datetime.now(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z"),
                },
            )
        ],
        principal=PRINCIPAL,
    )
    return str(created[0]["properties"]["id"])


# ------------------------------------------------------------------------------ header


async def test_a_fresh_workbook_has_an_honest_almost_empty_page(
    writer, pool, settings, artefact_store, report_deploy_store, scope_store,
) -> None:
    seeded = await seed_estate(writer, suffix=f"-{new_ulid()}")
    page = await _read_page(pool, settings, artefact_store, report_deploy_store, scope_store, seeded["workbook"])

    header = page["header"]
    assert header["family"] is None
    assert header["train"] is None
    assert header["state"] is None
    assert header["owner"] is None
    assert header["tier"] is None
    assert page["parity"] is None
    assert page["exceptions"]["cases"] == []
    assert page["gates"]["g2"] is None
    assert page["gates"]["g4"] is None
    assert [g["decision"] for g in header["gate_status_strip"]] == [None, None, None, None]


async def test_unknown_workbook_is_a_real_not_found(
    pool, settings, artefact_store, report_deploy_store, scope_store,
) -> None:
    with pytest.raises(ElementNotFoundError):
        await _read_page(pool, settings, artefact_store, report_deploy_store, scope_store, "not-a-real-workbook")


async def test_header_reports_the_real_train_family_owner_and_tier(
    writer, pool, settings, artefact_store, report_deploy_store, scope_store,
) -> None:
    seeded = await seed_estate(writer, suffix=f"-{new_ulid()}")
    workbook_id = seeded["workbook"]
    train_id = await _write_train(writer, workbook_id=workbook_id, state="PROVING")
    family_id = await _write_family(writer, workbook_id=workbook_id)
    await _write_owner(writer, workbook_id=workbook_id)
    await scope_store.decide(
        new_decision(
            workbook_id=workbook_id, kind=DecisionKind.RE_TIER, reason="a real complexity assessment",
            decided_by="user:pm@artizent.example", from_value=None, to_value="MODERATE",
        )
    )

    page = await _read_page(pool, settings, artefact_store, report_deploy_store, scope_store, workbook_id)
    header = page["header"]
    assert header["train"] == {"id": train_id, "name": "Train 1", "sequence": 1}
    assert header["state"] == "PROVING"
    assert header["family"] == {"id": family_id, "name": "Risk Positions", "state": "PROPOSED"}
    assert header["owner"] == {"id": header["owner"]["id"], "name": "A. Mehta"}
    assert header["tier"] == "MODERATE"
    assert header["site"]["name"] == "RQA"
    assert header["project"]["name"] == "Risk Core"


# ------------------------------------------------------------------------------ source


async def test_source_lists_the_real_worksheets_dashboards_datasources_and_calcs(
    writer, pool, settings, artefact_store, report_deploy_store, scope_store,
) -> None:
    seeded = await seed_estate(writer, suffix=f"-{new_ulid()}")
    page = await _read_page(pool, settings, artefact_store, report_deploy_store, scope_store, seeded["workbook"])

    source = page["source"]
    assert [w["id"] for w in source["worksheets"]] == [seeded["worksheet"]]
    assert [d["id"] for d in source["dashboards"]] == [seeded["dashboard"]]
    assert [d["id"] for d in source["datasources"]] == [seeded["datasource"]]
    calc_ids = {c["id"] for c in source["calculated_fields"]}
    assert seeded["calc"] in calc_ids
    assert source["screenshot"] is None


# ------------------------------------------------------------------------------- gates


async def test_gates_read_g2_from_the_family_and_g4_from_the_site(
    writer, pool, settings, artefact_store, report_deploy_store, scope_store,
) -> None:
    seeded = await seed_estate(writer, suffix=f"-{new_ulid()}")
    workbook_id = seeded["workbook"]
    family_id = await _write_family(writer, workbook_id=workbook_id)
    await _write_gate_decision(writer, gate="G2", subject_ref=family_id)

    page = await _read_page(pool, settings, artefact_store, report_deploy_store, scope_store, workbook_id)
    assert page["gates"]["g2"]["decision"] == "APPROVED"
    assert page["header"]["gate_status_strip"][1] == {"gate": "G2", "decision": "APPROVED"}
    # No site-level G4 decision has been written yet -- honestly absent, not fabricated.
    assert page["gates"]["g4"] is None


async def test_gates_g3_reuses_the_real_g3_card_wholesale(
    writer, pool, settings, artefact_store, report_deploy_store, scope_store,
) -> None:
    seeded = await seed_estate(writer, suffix=f"-{new_ulid()}")
    workbook_id = seeded["workbook"]
    await _write_gate_decision(writer, gate="G3", subject_ref=workbook_id)

    page = await _read_page(pool, settings, artefact_store, report_deploy_store, scope_store, workbook_id)
    g3 = page["gates"]["g3"]
    assert g3["workbook_id"] == workbook_id
    assert g3["latest_decision"]["decision"] == "APPROVED"
    assert page["header"]["gate_status_strip"][2] == {"gate": "G3", "decision": "APPROVED"}


# -------------------------------------------------------------------------- exceptions


async def test_exceptions_include_open_and_closed_cases_with_their_decisions(
    writer, pool, settings, artefact_store, report_deploy_store, scope_store,
) -> None:
    seeded = await seed_estate(writer, suffix=f"-{new_ulid()}")
    workbook_id = seeded["workbook"]
    case_id = new_ulid()
    await writer.write_nodes(
        [
            NodeWrite(
                type="ExceptionCase", id=case_id,
                properties={
                    "mu_ref": workbook_id, "class": "AGGREGATION", "state": "BLOCKED",
                    "decision": "MODEL_DEFECT_FOUNDRY", "family_ref": "fam_fixture",
                },
            )
        ],
        principal=PRINCIPAL,
    )
    await _write_gate_decision(writer, gate="G3", subject_ref=case_id, decision="REDESIGN")

    page = await _read_page(pool, settings, artefact_store, report_deploy_store, scope_store, workbook_id)
    cases = page["exceptions"]["cases"]
    assert len(cases) == 1
    assert cases[0]["id"] == case_id
    assert cases[0]["state"] == "BLOCKED"
    assert [d["decision"] for d in cases[0]["decisions"]] == ["REDESIGN"]


# ---------------------------------------------------------------------------- timeline


async def test_timeline_carries_real_events_about_this_workbook(
    writer, pool, settings, artefact_store, report_deploy_store, scope_store,
) -> None:
    seeded = await seed_estate(writer, suffix=f"-{new_ulid()}")
    page = await _read_page(pool, settings, artefact_store, report_deploy_store, scope_store, seeded["workbook"])

    subjects = {event["event"]["subject"] for event in page["timeline"]["events"]}
    assert seeded["workbook"] in subjects


# ------------------------------------------------------------------------- client view


async def test_client_view_is_a_strict_slice_not_the_full_document(
    writer, pool, settings, artefact_store, report_deploy_store, scope_store,
) -> None:
    seeded = await seed_estate(writer, suffix=f"-{new_ulid()}")
    workbook_id = seeded["workbook"]
    await writer.write_nodes(
        [
            NodeWrite(
                type="ExceptionCase",
                properties={"mu_ref": workbook_id, "class": "AGGREGATION", "state": "OPEN"},
            )
        ],
        principal=PRINCIPAL,
    )

    full = await _read_page(pool, settings, artefact_store, report_deploy_store, scope_store, workbook_id)
    client = mu_page_client_view(full)

    assert "exceptions" not in client
    assert client["artefacts"]["measures"] == []
    assert client["artefacts"]["git"] is None
    assert client["artefacts"]["model_ref"] is None
    # Source, Parity, Gates and Timeline are the same real facts, not reworded.
    assert client["source"] == full["source"]
    assert client["parity"] == full["parity"]
    assert client["gates"] == full["gates"]
    assert client["timeline"] == full["timeline"]
    assert client["header"]["gate_status_strip"] == full["header"]["gate_status_strip"]


# --------------------------------------------------------------------------- provenance


async def test_provenance_is_honestly_empty_until_a_real_record_exists(
    writer, pool, settings, provenance_store,
) -> None:
    seeded = await seed_estate(writer, suffix=f"-{new_ulid()}")
    records = await mu_provenance(
        pool, settings.graph_name,
        PostgresArtefactStore(pool, graph_name=settings.graph_name), provenance_store,
        workbook_id=seeded["workbook"],
    )
    assert records == []


async def test_provenance_surfaces_a_real_record_for_a_source_calc_field_and_filters_by_mode(
    writer, pool, settings, provenance_store,
) -> None:
    seeded = await seed_estate(writer, suffix=f"-{new_ulid()}")
    await provenance_store.record(
        ProvenanceRecord(
            id=new_ulid(), artefact_kind="calculated_field", artefact_ref=seeded["calc"],
            artefact_content_hash="deadbeef", agent="agent:transpiler", agent_version="1.0",
            mode=AgentMode.DETERMINISTIC, contract=ContractName.MODELLER_FAMILY,
            subject_id=seeded["calc"], context_hash="cafebabe", graph_version=1,
            created_by="agent:transpiler",
        )
    )

    all_records = await mu_provenance(
        pool, settings.graph_name,
        PostgresArtefactStore(pool, graph_name=settings.graph_name), provenance_store,
        workbook_id=seeded["workbook"],
    )
    assert any(r["inputs"]["subject_ref"] == seeded["calc"] for r in all_records)

    filtered_out = await mu_provenance(
        pool, settings.graph_name,
        PostgresArtefactStore(pool, graph_name=settings.graph_name), provenance_store,
        workbook_id=seeded["workbook"], mode="ASSISTED",
    )
    assert filtered_out == []


# ---------------------------------------------------------------------------- the API


@pytest.fixture
async def http_client(pool, settings, artefact_store, report_deploy_store, scope_store, provenance_store):
    from httpx import ASGITransport, AsyncClient

    from astra_graph.main import create_app

    app = create_app()
    app.state.pool = pool
    app.state.artefact_store = artefact_store
    app.state.report_deploy_store = report_deploy_store
    app.state.scope_store = scope_store
    app.state.provenance_store = provenance_store

    class _Repository:
        graph_name = settings.graph_name

    app.state.repository = _Repository()

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://graph-svc") as async_client:
        yield async_client


def _headers(role: str) -> dict[str, str]:
    return {PRINCIPAL_HEADER: "user:someone@example.com", ROLES_HEADER: role}


async def test_mu_page_over_http_gives_a_client_reader_the_narrower_view(
    writer, http_client,
) -> None:
    seeded = await seed_estate(writer, suffix=f"-{new_ulid()}")
    response = await http_client.get(f"/v1/mu/{seeded['workbook']}", headers=_headers("client_report_owner"))
    assert response.status_code == 200
    body = response.json()
    assert "exceptions" not in body


async def test_mu_page_over_http_gives_an_artizent_reader_the_full_view(
    writer, http_client,
) -> None:
    seeded = await seed_estate(writer, suffix=f"-{new_ulid()}")
    response = await http_client.get(f"/v1/mu/{seeded['workbook']}", headers=_headers("migration_engineer"))
    assert response.status_code == 200
    assert "exceptions" in response.json()


async def test_mu_page_over_http_refuses_an_unrelated_role(
    writer, http_client,
) -> None:
    seeded = await seed_estate(writer, suffix=f"-{new_ulid()}")
    response = await http_client.get(f"/v1/mu/{seeded['workbook']}", headers=_headers("client_data_owner"))
    assert response.status_code == 403


async def test_mu_provenance_over_http_refuses_a_client_reader(
    writer, http_client,
) -> None:
    seeded = await seed_estate(writer, suffix=f"-{new_ulid()}")
    response = await http_client.get(
        f"/v1/mu/{seeded['workbook']}/provenance", headers=_headers("client_report_owner"),
    )
    assert response.status_code == 403


async def test_mu_provenance_over_http_serves_an_artizent_reader(
    writer, http_client,
) -> None:
    seeded = await seed_estate(writer, suffix=f"-{new_ulid()}")
    response = await http_client.get(
        f"/v1/mu/{seeded['workbook']}/provenance", headers=_headers("migration_engineer"),
    )
    assert response.status_code == 200
    assert response.json()["records"] == []


async def test_artefact_content_is_reachable_by_the_report_owner_not_only_artizent(
    writer, http_client, artefact_store,
) -> None:
    """Story S10.3.1 widened `GET /v1/artefacts/{id}/content` so the Migration Unit
    page's own client view can actually render its own thumbnails -- see `deps.py`'s
    `require_artefact_reader`."""
    record = await artefact_store.store(
        kind="visual_capture", mu_ref="wb_fixture", case_id="sheet_fixture",
        content=b"\x89PNG fixture bytes", media_type="image/png", created_by="agent:harvester",
    )
    response = await http_client.get(
        f"/v1/artefacts/{record.id}/content", headers=_headers("client_report_owner"),
    )
    assert response.status_code == 200
    unrelated = await http_client.get(
        f"/v1/artefacts/{record.id}/content", headers=_headers("client_data_owner"),
    )
    assert unrelated.status_code == 403
