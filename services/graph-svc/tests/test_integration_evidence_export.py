"""Evidence Export, against real PostgreSQL + Apache AGE -- story S11.3.2, closes
F11.3.

What only the real stack can answer: that site/train/MU scope really resolves to the
real workbooks under them; that decisions/verdicts/provenance/artefacts really scope
by their own real subject/mu_ref/suite_ref fields; that a G2/G4 decision is really
excluded from a site-scoped export and really included in a programme-scoped one; that
the whole HTTP lifecycle (start, poll, download) really produces a real, signed,
downloadable zip; and that the role gate really matches this story's own persona.
"""

from __future__ import annotations

import asyncio
import json
import os
import threading
import zipfile
from datetime import UTC, datetime
from io import BytesIO
from typing import Any

import pytest

pytestmark = pytest.mark.integration

asyncpg = pytest.importorskip("asyncpg")

from astra_graph.config import Settings  # noqa: E402
from astra_graph.context.canonical import canonical_json  # noqa: E402
from astra_graph.events import source_for  # noqa: E402
from astra_graph.evidence_export import (  # noqa: E402
    ExportScope,
    LocalEvidenceSigner,
    assemble_manifest,
    resolve_workbook_ids,
    verify_bytes,
)
from astra_graph.graph import AgeGraphRepository, create_pool  # noqa: E402
from astra_graph.graph.queries import accessor  # noqa: E402
from astra_graph.ids import new_ulid  # noqa: E402
from astra_graph.migrations import run as run_migrations  # noqa: E402
from astra_graph.ontology import EDGE_LABELS, NODE_LABELS  # noqa: E402
from astra_graph.principal import Principal  # noqa: E402
from astra_graph.provenance import (  # noqa: E402
    AgentMode,
    ContractName,
    PostgresProvenanceStore,
    new_record,
)
from astra_graph.tolerance_charter import (  # noqa: E402
    PostgresToleranceCharterStore,
    ToleranceCharter,
)
from astra_graph.writes import EdgeWrite, GraphWriter, NodeWrite  # noqa: E402

PRINCIPAL = Principal("agent:harvester", run_id="run-evidence-export")
INFOSEC = Principal("user:infosec@client.example")


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


async def _create_graph(conn: asyncpg.Connection, graph: str) -> None:
    await conn.execute("LOAD 'age'")
    await conn.execute('SET search_path = ag_catalog, "$user", public')
    await conn.execute("SELECT ag_catalog.create_graph($1)", graph)
    for label in sorted(NODE_LABELS):
        await conn.execute("SELECT ag_catalog.create_vlabel($1, $2)", graph, label)
        await conn.execute(f'CREATE INDEX ON {graph}."{label}" USING BTREE ({accessor("id")})')
    for label in sorted(EDGE_LABELS):
        await conn.execute("SELECT ag_catalog.create_elabel($1, $2)", graph, label)


def _run_off_loop(factory: Any) -> Any:
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


@pytest.fixture
def settings() -> Settings:
    config = _settings(f"astra_evidence_export_{new_ulid()[10:22].lower()}")

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

    if not _run_off_loop(setup):
        pytest.skip("PostgreSQL with Apache AGE not reachable")

    yield config

    async def teardown() -> None:
        conn = await asyncpg.connect(dsn=config.dsn)
        try:
            await conn.execute("LOAD 'age'")
            for table in (
                "public.estate_edge_index", "public.estate_element_index", "public.estate_event",
                "public.provenance", "public.artefacts", "public.tolerance_charter_version",
                "public.evidence_chain_entry", "public.evidence_daily_root",
            ):
                await conn.execute(f"DELETE FROM {table} WHERE graph = $1", config.graph_name)
            await conn.execute("SELECT ag_catalog.drop_graph($1, true)", config.graph_name)
        finally:
            await conn.close()

    _run_off_loop(teardown)


async def _write(writer: GraphWriter, type_: str, **properties: Any) -> str:
    created = await writer.write_nodes([NodeWrite(type=type_, properties=properties)], principal=PRINCIPAL)
    return str(created[0]["properties"]["id"])


async def _edge(writer: GraphWriter, type_: str, source: str, target: str, **props: Any) -> None:
    await writer.write_edge(
        EdgeWrite(type=type_, from_id=source, to_id=target, properties=props), principal=PRINCIPAL
    )


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")


@pytest.fixture
async def estate(settings: Settings):
    """Two sites, each with one project and workbooks; a train covering one site's own
    workbooks; a real G1, G2, G3 (workbook-subject), G3 (ExceptionCase-subject) and G4
    decision; a real ParityRun+Verdict for one workbook; a real provenance record and
    artefact for the same workbook; two real Tolerance Charter versions."""
    pool = await create_pool(settings)
    try:
        repository = AgeGraphRepository(pool, graph_name=settings.graph_name)
        writer = GraphWriter(repository, event_source=source_for(settings.graph_name))
        artefact_store = __import__(
            "astra_graph.artefacts", fromlist=["PostgresArtefactStore"]
        ).PostgresArtefactStore(pool, graph_name=settings.graph_name)

        site_a = await _write(writer, "Site", luid=f"site-a-{new_ulid()}", name="RQA")
        project_a = await _write(writer, "Project", luid=f"proj-a-{new_ulid()}", name="Risk Core")
        await _edge(writer, "CONTAINS", site_a, project_a)
        book_a1 = await _write(writer, "Workbook", luid=f"wb-a1-{new_ulid()}", name="Daily VaR", revision="1")
        book_a2 = await _write(writer, "Workbook", luid=f"wb-a2-{new_ulid()}", name="Weekly VaR", revision="1")
        await _edge(writer, "CONTAINS", project_a, book_a1)
        await _edge(writer, "CONTAINS", project_a, book_a2)

        site_b = await _write(writer, "Site", luid=f"site-b-{new_ulid()}", name="GTAA")
        project_b = await _write(writer, "Project", luid=f"proj-b-{new_ulid()}", name="Allocation")
        await _edge(writer, "CONTAINS", site_b, project_b)
        book_b1 = await _write(writer, "Workbook", luid=f"wb-b1-{new_ulid()}", name="Allocation Report", revision="1")
        await _edge(writer, "CONTAINS", project_b, book_b1)

        train = await _write(writer, "ReleaseTrain", name="Train 1")
        await _edge(writer, "IN_TRAIN", book_a1, train, sequence=1, state="QUEUED")

        family = await _write(writer, "ModelFamily", name="VaR family", state="PROPOSED")

        # G1: platform-wide, no workbook subject at all.
        g1 = await _write(
            writer, "GateDecision", gate="G1", subject_ref="tolerance_charter", decision="APPROVED",
            approver="user:pm@artizent.example", timestamp=_now(),
        )
        # G2: ModelFamily subject -- not workbook-shaped.
        g2 = await _write(
            writer, "GateDecision", gate="G2", subject_ref=family, decision="APPROVED",
            approver="user:owner@artizent.example", timestamp=_now(),
        )
        # G3, workbook-subject, in site A.
        g3_workbook = await _write(
            writer, "GateDecision", gate="G3", subject_ref=book_a1, decision="APPROVED",
            approver="user:owner@artizent.example", timestamp=_now(),
        )
        # G3, ExceptionCase-subject, whose own mu_ref is book_a2 (also site A).
        exception_case = await _write(
            writer, "ExceptionCase", **{"mu_ref": book_a2, "class": "KEY_MISSING", "state": "OPEN"},
        )
        g3_exception = await _write(
            writer, "GateDecision", gate="G3", subject_ref=exception_case, decision="REDESIGN",
            approver="user:owner@artizent.example", timestamp=_now(),
        )
        # G4: Site subject -- not workbook-shaped.
        g4 = await _write(
            writer, "GateDecision", gate="G4", subject_ref=site_a, decision="APPROVED",
            approver="user:licence@artizent.example", timestamp=_now(),
        )

        verdict = await _write(writer, "Verdict", case_ref="case-1", result="PASS")
        run_id = await _write(
            writer, "ParityRun", suite_ref=book_a1, charter_version="1",
            started=_now(), finished=_now(), verdicts=[verdict],
        )

        provenance_store = PostgresProvenanceStore(pool, graph_name=settings.graph_name)
        provenance_record = await provenance_store.record(new_record(
            artefact_kind="MEASURE", artefact_ref=f"msr_{new_ulid()[10:18]}",
            artefact_content_hash="sha256:deadbeef", agent="transpiler", agent_version="1.4.2",
            mode=AgentMode.GENERATED_PROVED, contract=ContractName.TRANSPILER_CALC,
            subject_id=book_a1, context_hash="sha256:cafef00d", graph_version=1,
            created_by="agent:transpiler",
        ))

        artefact = await artefact_store.store(
            kind="visual_capture", mu_ref=book_a1, case_id="case-1",
            content=b"fake-png-bytes", media_type="image/png", created_by="agent:compositor",
        )

        charter_store = PostgresToleranceCharterStore(pool, graph_name=settings.graph_name)
        v1 = await charter_store.save(ToleranceCharter(), updated_by="user:pe@artizent.example")
        v2 = await charter_store.save(ToleranceCharter(), updated_by="user:pe@artizent.example")

        yield {
            "pool": pool, "settings": settings, "writer": writer,
            "site_a": site_a, "site_b": site_b, "project_a": project_a,
            "book_a1": book_a1, "book_a2": book_a2, "book_b1": book_b1, "train": train,
            "g1": g1, "g2": g2, "g3_workbook": g3_workbook, "g3_exception": g3_exception, "g4": g4,
            "exception_case": exception_case, "verdict": verdict, "run_id": run_id,
            "provenance_id": provenance_record.id, "artefact_id": artefact.id,
            "charter_v1": v1.version, "charter_v2": v2.version,
        }
    finally:
        await pool.close()


# --------------------------------------------------------------------------- scope


async def test_site_scope_resolves_the_real_workbooks_under_it(estate) -> None:
    ids = await resolve_workbook_ids(
        estate["pool"], estate["settings"].graph_name, ExportScope(kind="site", ref=estate["site_a"]),
    )
    assert ids == frozenset({estate["book_a1"], estate["book_a2"]})


async def test_the_other_sites_own_workbook_is_excluded(estate) -> None:
    ids = await resolve_workbook_ids(
        estate["pool"], estate["settings"].graph_name, ExportScope(kind="site", ref=estate["site_a"]),
    )
    assert estate["book_b1"] not in ids


async def test_train_scope_resolves_its_real_member(estate) -> None:
    ids = await resolve_workbook_ids(
        estate["pool"], estate["settings"].graph_name, ExportScope(kind="train", ref=estate["train"]),
    )
    assert ids == frozenset({estate["book_a1"]})


async def test_a_site_with_no_real_workbooks_resolves_to_an_empty_set(estate) -> None:
    ids = await resolve_workbook_ids(
        estate["pool"], estate["settings"].graph_name, ExportScope(kind="site", ref="site-nonexistent"),
    )
    assert ids == frozenset()


# ------------------------------------------------------------------ manifest assembly


async def test_a_programme_scoped_manifest_includes_every_gate(estate) -> None:
    manifest = await assemble_manifest(
        estate["pool"], estate["settings"].graph_name, ExportScope(kind="programme"),
        export_id="evexp_1", generated_by=INFOSEC.value,
    )
    gates = {row["gate"] for row in manifest["decisions"]}
    assert gates == {"G1", "G2", "G3", "G4"}
    assert manifest["counts"]["decisions"] == 5


async def test_a_site_scoped_manifest_includes_only_that_sites_own_g3_decisions(estate) -> None:
    manifest = await assemble_manifest(
        estate["pool"], estate["settings"].graph_name, ExportScope(kind="site", ref=estate["site_a"]),
        export_id="evexp_2", generated_by=INFOSEC.value,
    )
    decision_ids = {row["id"] for row in manifest["decisions"]}
    assert decision_ids == {estate["g3_workbook"], estate["g3_exception"]}
    # G1 (platform-wide), G2 (ModelFamily) and G4 (a different site) are all real,
    # deliberate exclusions -- none of them is a workbook-shaped subject.
    assert estate["g1"] not in decision_ids
    assert estate["g2"] not in decision_ids
    assert estate["g4"] not in decision_ids


async def test_a_site_scoped_manifest_includes_that_sites_own_verdicts_and_provenance(estate) -> None:
    manifest = await assemble_manifest(
        estate["pool"], estate["settings"].graph_name, ExportScope(kind="site", ref=estate["site_a"]),
        export_id="evexp_3", generated_by=INFOSEC.value,
    )
    assert manifest["verdicts"][0]["run_id"] == estate["run_id"]
    assert manifest["verdicts"][0]["verdicts"][0]["id"] == estate["verdict"]
    assert manifest["provenance_records"][0]["id"] == estate["provenance_id"]
    assert manifest["artefact_hashes"][0]["id"] == estate["artefact_id"]


async def test_a_site_b_scoped_manifest_excludes_site_as_own_facts(estate) -> None:
    manifest = await assemble_manifest(
        estate["pool"], estate["settings"].graph_name, ExportScope(kind="site", ref=estate["site_b"]),
        export_id="evexp_4", generated_by=INFOSEC.value,
    )
    assert manifest["verdicts"] == []
    assert manifest["provenance_records"] == []
    assert manifest["decisions"] == []


async def test_every_charter_version_is_included_regardless_of_scope(estate) -> None:
    manifest = await assemble_manifest(
        estate["pool"], estate["settings"].graph_name, ExportScope(kind="mu", ref=estate["book_b1"]),
        export_id="evexp_5", generated_by=INFOSEC.value,
    )
    versions = {row["version"] for row in manifest["charter_versions"]}
    assert versions == {estate["charter_v1"], estate["charter_v2"]}


async def test_events_are_scoped_to_the_workbooks_own_real_subjects(estate) -> None:
    manifest = await assemble_manifest(
        estate["pool"], estate["settings"].graph_name, ExportScope(kind="mu", ref=estate["book_a1"]),
        export_id="evexp_6", generated_by=INFOSEC.value,
    )
    subjects = {row["subject"] for row in manifest["events"]}
    assert estate["book_a1"] in subjects
    assert estate["book_a2"] not in subjects
    # The G3 decision on book_a1 is already gathered as in-scope, so its own event
    # is correctly pulled in too, even though its own subject is not the workbook id.
    assert estate["g3_workbook"] in subjects


async def test_on_progress_fires_once_per_category(estate) -> None:
    seen: list[str] = []
    await assemble_manifest(
        estate["pool"], estate["settings"].graph_name, ExportScope(kind="programme"),
        export_id="evexp_7", generated_by=INFOSEC.value, on_progress=lambda category, _n: seen.append(category),
    )
    assert set(seen) == {
        "scope", "decisions", "verdicts", "provenance_records", "artefact_hashes",
        "charter_versions", "chain_roots", "events",
    }


# --------------------------------------------------------------------------------- API


def _headers(role: str, principal: Principal) -> dict[str, str]:
    from astra_graph.principal import PRINCIPAL_HEADER
    from astra_graph.roles import ROLES_HEADER

    return {PRINCIPAL_HEADER: principal.value, ROLES_HEADER: role}


class _FakeCartographer:
    """The identical stand-in `test_integration_invoicing.py`'s own `http_client`
    fixture already uses -- every route here only ever reads `.pool`/`.graph_name`."""

    def __init__(self, pool: Any, graph_name: str) -> None:
        self.pool = pool
        self.graph_name = graph_name


@pytest.fixture
async def http_client(estate):
    from httpx import ASGITransport, AsyncClient

    from astra_graph.artefacts import PostgresArtefactStore
    from astra_graph.evidence_export import ExportProgress
    from astra_graph.main import create_app

    app = create_app()
    app.state.cartographer = _FakeCartographer(estate["pool"], estate["settings"].graph_name)
    app.state.artefact_store = PostgresArtefactStore(estate["pool"], graph_name=estate["settings"].graph_name)
    app.state.evidence_signer = LocalEvidenceSigner()
    app.state.evidence_export_progress = ExportProgress()

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://graph-svc", timeout=60.0) as async_client:
        yield async_client


async def _wait_for_finish(client, headers: dict[str, str], *, timeout: float = 20.0) -> dict[str, Any]:
    import time

    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        response = await client.get("/v1/evidence-export/status", headers=headers)
        body = response.json()
        if not body["running"]:
            return body
        await asyncio.sleep(0.05)
    raise AssertionError("export did not finish within the test's own timeout")


async def test_export_over_http_requires_the_infosec_or_artizent_role(estate, http_client) -> None:
    response = await http_client.post(
        "/v1/evidence-export", json={"kind": "programme"},
        headers=_headers("client_report_owner", INFOSEC),
    )
    assert response.status_code == 403


async def test_a_full_export_over_http_produces_a_real_signed_downloadable_bundle(estate, http_client) -> None:
    started = await http_client.post(
        "/v1/evidence-export", json={"kind": "site", "ref": estate["site_a"]},
        headers=_headers("client_infosec_reviewer", INFOSEC),
    )
    assert started.status_code == 202
    export_id = started.json()["export_id"]

    final = await _wait_for_finish(http_client, _headers("client_infosec_reviewer", INFOSEC))
    assert final["export_id"] == export_id
    assert final["last_error"] is None
    assert final["artefact_id"] is not None
    assert final["signature"] is not None
    assert final["counts"]["decisions"] == 2

    downloaded = await http_client.get(
        f"/v1/evidence-export/{export_id}/download", headers=_headers("client_infosec_reviewer", INFOSEC),
    )
    assert downloaded.status_code == 200
    assert downloaded.headers["content-type"] == "application/zip"

    with zipfile.ZipFile(BytesIO(downloaded.content)) as archive:
        manifest = json.loads(archive.read("manifest.json"))
        envelope = json.loads(archive.read("signature.json"))

    assert manifest["export_id"] == export_id
    assert verify_bytes(
        canonical_json(manifest), envelope["signature"], envelope["public_key_pem"].encode("utf-8"),
    ) is True


async def test_the_public_key_route_matches_the_signature_in_a_real_export(estate, http_client) -> None:
    key_response = await http_client.get(
        "/v1/evidence-export/public-key", headers=_headers("client_infosec_reviewer", INFOSEC),
    )
    assert key_response.status_code == 200

    started = await http_client.post(
        "/v1/evidence-export", json={"kind": "programme"},
        headers=_headers("client_infosec_reviewer", INFOSEC),
    )
    assert started.status_code == 202
    final = await _wait_for_finish(http_client, _headers("client_infosec_reviewer", INFOSEC))
    assert final["public_key_pem"] == key_response.json()["public_key_pem"]


async def test_a_second_export_while_one_runs_is_refused(estate, http_client) -> None:
    started = await http_client.post(
        "/v1/evidence-export", json={"kind": "programme"},
        headers=_headers("client_infosec_reviewer", INFOSEC),
    )
    assert started.status_code == 202
    second = await http_client.post(
        "/v1/evidence-export", json={"kind": "programme"},
        headers=_headers("client_infosec_reviewer", INFOSEC),
    )
    assert second.status_code == 400
    await _wait_for_finish(http_client, _headers("client_infosec_reviewer", INFOSEC))


async def test_downloading_an_unknown_export_id_is_a_clean_404(estate, http_client) -> None:
    response = await http_client.get(
        "/v1/evidence-export/evexp_nonexistent/download", headers=_headers("client_infosec_reviewer", INFOSEC),
    )
    assert response.status_code == 404
