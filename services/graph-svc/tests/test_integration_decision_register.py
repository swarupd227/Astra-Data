"""The Decision Register -- story S10.4.2, against real PostgreSQL + Apache AGE.

What only the real store can answer: that a real `GateDecision` row -- for every one of
G1's platform singleton, a real G2 ModelFamily, a real G3 Workbook review, a real G3
Exception Desk adjudication (a different subject shape under the identical gate), and a
real G4 Site -- resolves to its own real, human-readable subject name; that search and
the gate/decision/approver filters really narrow the real set; that a decision whose
`evidence_ref` really is a stored artefact really opens it, and one whose `evidence_ref`
is absent or names something else honestly reports no artefact rather than fabricating
one; and that CSV/PDF export and the role gate all work over a real HTTP request.
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
from astra_graph.decision_register import (  # noqa: E402
    decision_row,
    decisions_to_csv,
    evidence_bundle,
    list_decisions,
    render_decision_register_pdf,
)
from astra_graph.events import source_for  # noqa: E402
from astra_graph.graph import AgeGraphRepository, create_pool  # noqa: E402
from astra_graph.graph.queries import accessor  # noqa: E402
from astra_graph.ids import new_ulid  # noqa: E402
from astra_graph.migrations import run as run_migrations  # noqa: E402
from astra_graph.ontology import EDGE_LABELS, NODE_LABELS  # noqa: E402
from astra_graph.principal import PRINCIPAL_HEADER, Principal  # noqa: E402
from astra_graph.roles import ROLES_HEADER  # noqa: E402
from astra_graph.writes import GraphWriter, NodeWrite  # noqa: E402

PRINCIPAL = Principal("agent:harvester", run_id="run-decision-register")
OWNER_VALUE = "user:owner@client.example"


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
    for table in ("public.estate_edge_index", "public.estate_element_index", "public.estate_event",
                  "public.artefacts"):
        await conn.execute(f"DELETE FROM {table} WHERE graph = $1", graph)
    await conn.execute("SELECT ag_catalog.drop_graph($1, true)", graph)


@pytest.fixture(scope="module")
def settings() -> Settings:
    config = _settings(f"astra_decisionreg_{new_ulid()[10:22].lower()}")

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
def writer(pool, settings: Settings) -> GraphWriter:
    repository = AgeGraphRepository(pool, graph_name=settings.graph_name)
    return GraphWriter(repository, event_source=source_for(settings.graph_name))


@pytest.fixture
def artefact_store(pool, settings: Settings) -> PostgresArtefactStore:
    return PostgresArtefactStore(pool, graph_name=settings.graph_name)


async def _write_family(writer: GraphWriter, *, name: str) -> str:
    return str((await writer.write_nodes(
        [NodeWrite(type="ModelFamily", properties={
            "name": name, "state": "IN_REVIEW", "domain": None, "owner": OWNER_VALUE,
            "grain": "", "conformed_dims": [],
        })],
        principal=PRINCIPAL,
    ))[0]["properties"]["id"])


async def _write_site(writer: GraphWriter, *, name: str) -> str:
    suffix = new_ulid()[10:18].lower()
    return str((await writer.write_nodes(
        [NodeWrite(type="Site", properties={"luid": f"s-{suffix}", "name": name})], principal=PRINCIPAL,
    ))[0]["properties"]["id"])


async def _write_workbook(writer: GraphWriter, *, name: str) -> str:
    suffix = new_ulid()[10:18].lower()
    return str((await writer.write_nodes(
        [NodeWrite(type="Workbook", properties={"luid": f"wb-{suffix}", "name": name, "revision": "1"})],
        principal=PRINCIPAL,
    ))[0]["properties"]["id"])


async def _write_exception_case(writer: GraphWriter, *, workbook_id: str, failure_class: str) -> str:
    return str((await writer.write_nodes(
        [NodeWrite(type="ExceptionCase", properties={"mu_ref": workbook_id, "class": failure_class})],
        principal=PRINCIPAL,
    ))[0]["properties"]["id"])


async def _write_gate_decision(
    writer: GraphWriter,
    *,
    gate: str,
    subject_ref: str,
    decision: str,
    approver: str = OWNER_VALUE,
    approver_role: str | None = None,
    countersigner: str | None = None,
    countersigner_role: str | None = None,
    rationale: str | None = None,
    evidence_ref: str | None = None,
    version_hash: str | None = None,
) -> str:
    return str((await writer.write_nodes(
        [NodeWrite(type="GateDecision", properties={
            "gate": gate, "subject_ref": subject_ref, "decision": decision,
            "approver": approver, "approver_role": approver_role,
            "countersigner": countersigner, "countersigner_role": countersigner_role,
            "rationale": rationale, "evidence_ref": evidence_ref, "version_hash": version_hash,
            "timestamp": datetime.now(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z"),
        })],
        principal=PRINCIPAL,
    ))[0]["properties"]["id"])


# --------------------------------------------------------------------------- subjects


async def test_g2_decision_resolves_the_real_family_name(writer, pool, settings) -> None:
    family_id = await _write_family(writer, name="Risk Positions")
    await _write_gate_decision(
        writer, gate="G2", subject_ref=family_id, decision="APPROVED",
        approver_role="client_data_owner", countersigner="S. Engineer",
        countersigner_role="semantic_model_engineer", rationale="Looks right.",
        evidence_ref=family_id, version_hash="v1",
    )

    rows = await list_decisions(pool, settings.graph_name)

    matching = [r for r in rows if r["subject_ref"] == family_id]
    assert len(matching) == 1
    assert matching[0]["subject_name"] == "Risk Positions"
    assert matching[0]["approver_role"] == "client_data_owner"
    assert matching[0]["countersigner_role"] == "semantic_model_engineer"
    assert matching[0]["rationale"] == "Looks right."


async def test_g3_review_and_adjudication_resolve_to_different_subject_shapes(writer, pool, settings) -> None:
    """The central disclosed design point: two G3 decisions, one naming a Workbook (the
    report review) and one naming an ExceptionCase (the Exception Desk's own
    adjudication) -- both `gate="G3"`, resolved to two different real node labels."""
    workbook_id = await _write_workbook(writer, name="Daily VaR")
    await _write_gate_decision(writer, gate="G3", subject_ref=workbook_id, decision="APPROVED")

    case_id = await _write_exception_case(writer, workbook_id=workbook_id, failure_class="FILTER_CONTEXT")
    await _write_gate_decision(writer, gate="G3", subject_ref=case_id, decision="PATCHED")

    rows = await list_decisions(pool, settings.graph_name)
    by_subject = {r["subject_ref"]: r for r in rows}

    assert by_subject[workbook_id]["subject_name"] == "Daily VaR"
    assert by_subject[case_id]["subject_name"] == "FILTER_CONTEXT on Daily VaR"


async def test_g4_decision_resolves_the_real_site_name(writer, pool, settings) -> None:
    site_id = await _write_site(writer, name="GTAA Site")
    await _write_gate_decision(writer, gate="G4", subject_ref=site_id, decision="DEFERRED")

    rows = await list_decisions(pool, settings.graph_name)

    matching = [r for r in rows if r["subject_ref"] == site_id]
    assert matching[0]["subject_name"] == "GTAA Site"


async def test_g1_decision_names_the_platform_singleton(writer, pool, settings) -> None:
    await _write_gate_decision(writer, gate="G1", subject_ref="tolerance_charter", decision="APPROVED")

    rows = await list_decisions(pool, settings.graph_name)

    matching = [r for r in rows if r["gate"] == "G1"]
    assert matching
    assert matching[0]["subject_name"] == "Tolerance Charter"


# ---------------------------------------------------------------------------- filters


async def test_search_and_filter(writer, pool, settings) -> None:
    family_id = await _write_family(writer, name="Treasury Book")
    await _write_gate_decision(
        writer, gate="G2", subject_ref=family_id, decision="APPROVED", approver="user:alice@client.example",
    )
    site_id = await _write_site(writer, name="Unrelated Site")
    await _write_gate_decision(
        writer, gate="G4", subject_ref=site_id, decision="DEFERRED", approver="user:bob@client.example",
    )

    by_gate = await list_decisions(pool, settings.graph_name, gate="G2")
    assert all(r["gate"] == "G2" for r in by_gate)
    assert family_id in {r["subject_ref"] for r in by_gate}
    assert site_id not in {r["subject_ref"] for r in by_gate}

    by_decision = await list_decisions(pool, settings.graph_name, decision="DEFERRED")
    assert site_id in {r["subject_ref"] for r in by_decision}
    assert family_id not in {r["subject_ref"] for r in by_decision}

    by_approver = await list_decisions(pool, settings.graph_name, approver="alice")
    assert family_id in {r["subject_ref"] for r in by_approver}
    assert site_id not in {r["subject_ref"] for r in by_approver}

    by_search = await list_decisions(pool, settings.graph_name, q="treasury")
    assert family_id in {r["subject_ref"] for r in by_search}
    assert site_id not in {r["subject_ref"] for r in by_search}


# --------------------------------------------------------------------------- evidence


async def test_evidence_bundle_opens_a_real_stored_artefact(writer, pool, settings, artefact_store) -> None:
    record = await artefact_store.store(
        kind="g3_card_snapshot", mu_ref="wb_fixture", case_id="",
        content=b'{"waivers": []}', media_type="application/json", created_by=OWNER_VALUE,
    )
    workbook_id = await _write_workbook(writer, name="Evidenced Report")
    decision_id = await _write_gate_decision(
        writer, gate="G3", subject_ref=workbook_id, decision="APPROVED", evidence_ref=record.id,
    )

    bundle = await evidence_bundle(pool, settings.graph_name, artefact_store, decision_id)

    assert bundle is not None
    assert bundle["decision"]["id"] == decision_id
    assert bundle["artefact"] is not None
    assert bundle["artefact"]["id"] == record.id


async def test_evidence_bundle_discloses_no_artefact_when_the_ref_does_not_resolve(
    writer, pool, settings, artefact_store,
) -> None:
    family_id = await _write_family(writer, name="No Artefact Family")
    decision_id = await _write_gate_decision(
        writer, gate="G2", subject_ref=family_id, decision="APPROVED", evidence_ref=family_id,
    )

    bundle = await evidence_bundle(pool, settings.graph_name, artefact_store, decision_id)

    assert bundle is not None
    assert bundle["artefact"] is None
    assert bundle["decision"]["evidence_ref"] == family_id  # disclosed, not hidden


async def test_evidence_bundle_is_none_for_an_unknown_decision(pool, settings, artefact_store) -> None:
    bundle = await evidence_bundle(pool, settings.graph_name, artefact_store, "not-a-real-id")
    assert bundle is None


async def test_decision_row_matches_the_registers_own_row(writer, pool, settings) -> None:
    family_id = await _write_family(writer, name="Row Match Family")
    decision_id = await _write_gate_decision(writer, gate="G2", subject_ref=family_id, decision="APPROVED")

    row = await decision_row(pool, settings.graph_name, decision_id)
    listed = [r for r in await list_decisions(pool, settings.graph_name) if r["id"] == decision_id]

    assert row == listed[0]


# --------------------------------------------------------------------------- export


async def test_decisions_to_csv_carries_every_field(writer, pool, settings) -> None:
    family_id = await _write_family(writer, name="CSV Family")
    await _write_gate_decision(
        writer, gate="G2", subject_ref=family_id, decision="APPROVED",
        approver="user:carol@client.example", rationale="Fine.",
    )

    rows = await list_decisions(pool, settings.graph_name, gate="G2")
    csv_text = decisions_to_csv([r for r in rows if r["subject_ref"] == family_id])

    assert "CSV Family" in csv_text
    assert "user:carol@client.example" in csv_text
    assert "Fine." in csv_text


async def test_render_decision_register_pdf_is_a_real_pdf(writer, pool, settings) -> None:
    family_id = await _write_family(writer, name="PDF Family")
    await _write_gate_decision(writer, gate="G2", subject_ref=family_id, decision="APPROVED")

    rows = [r for r in await list_decisions(pool, settings.graph_name) if r["subject_ref"] == family_id]
    pdf_bytes = render_decision_register_pdf(rows, signed_by="user:auditor@artizent.example")

    assert pdf_bytes.startswith(b"%PDF")


# ---------------------------------------------------------------------------- the API


@pytest.fixture
async def http_client(pool, settings, artefact_store):
    from httpx import ASGITransport, AsyncClient

    from astra_graph.main import create_app

    app = create_app()
    app.state.pool = pool
    app.state.artefact_store = artefact_store

    class _Repository:
        graph_name = settings.graph_name

    app.state.repository = _Repository()

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://graph-svc") as async_client:
        yield async_client


def _headers(role: str) -> dict[str, str]:
    return {PRINCIPAL_HEADER: "user:someone@example.com", ROLES_HEADER: role}


async def test_decisions_over_http_returns_real_items(writer, http_client) -> None:
    family_id = await _write_family(writer, name="HTTP Family")
    await _write_gate_decision(writer, gate="G2", subject_ref=family_id, decision="APPROVED")

    response = await http_client.get("/v1/decisions", headers=_headers("client_infosec_reviewer"))

    assert response.status_code == 200
    body = response.json()
    assert body["count"] >= 1
    assert family_id in {item["subject_ref"] for item in body["items"]}


async def test_decisions_over_http_refuses_an_unrelated_role(http_client) -> None:
    response = await http_client.get("/v1/decisions", headers=_headers("client_data_owner"))
    assert response.status_code == 403


async def test_decision_evidence_over_http(writer, http_client) -> None:
    family_id = await _write_family(writer, name="HTTP Evidence Family")
    decision_id = await _write_gate_decision(writer, gate="G2", subject_ref=family_id, decision="APPROVED")

    response = await http_client.get(
        f"/v1/decisions/{decision_id}/evidence", headers=_headers("programme_manager"),
    )
    assert response.status_code == 200
    body = response.json()
    assert body["decision"]["id"] == decision_id
    assert body["artefact"] is None

    missing = await http_client.get(
        "/v1/decisions/not-a-real-id/evidence", headers=_headers("programme_manager"),
    )
    assert missing.status_code == 404


async def test_decisions_csv_over_http(writer, http_client) -> None:
    family_id = await _write_family(writer, name="CSV HTTP Family")
    await _write_gate_decision(writer, gate="G2", subject_ref=family_id, decision="APPROVED")

    response = await http_client.get("/v1/decisions.csv", headers=_headers("migration_engineer"))

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/csv")
    assert "CSV HTTP Family" in response.text


async def test_decisions_pdf_over_http(writer, http_client) -> None:
    family_id = await _write_family(writer, name="PDF HTTP Family")
    await _write_gate_decision(writer, gate="G2", subject_ref=family_id, decision="APPROVED")

    response = await http_client.get("/v1/decisions.pdf", headers=_headers("migration_engineer"))

    assert response.status_code == 200
    assert response.headers["content-type"] == "application/pdf"
    assert response.content.startswith(b"%PDF")
