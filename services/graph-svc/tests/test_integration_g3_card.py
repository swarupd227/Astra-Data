"""The G3 gate card, against real PostgreSQL + Apache AGE -- story S9.1.1, opening
F9.1/E9.

What only the real stack can answer: that the card really reads a real workbook's own
`ReportDefinition`/`Visual`s for "what"; a real `ParityRun`/`Verdict`s (via the already-
proven `parity_dashboard`/`latest_parity_run`) for "proof", including a real sampled
flag and a real waiver; real `Visual.reviewed_by` for "human review status"; real
`ExceptionCase`/`GateDecision(REDESIGN)` rows and a real `CalculatedField.
redesign_decision` for "changes"; a real `ModelFamily`/G2 approval for the model fact;
that Approve really writes a real `GateDecision(gate="G3", decision="APPROVED")` with a
real countersigner and a real snapshot artefact, and that a still-open ExceptionCase or
a missing rationale/countersigner is really refused; that Request changes and Ask a
question really write their own real records; and that the new routes' own real role
gates work -- the report owner deciding, an Artizent role only reading.
"""

from __future__ import annotations

import asyncio
import json
import os
import threading
from collections.abc import Callable, Coroutine
from typing import Any

import pytest

pytestmark = pytest.mark.integration

asyncpg = pytest.importorskip("asyncpg")

from astra_graph.artefacts import PostgresArtefactStore  # noqa: E402
from astra_graph.config import Settings  # noqa: E402
from astra_graph.errors import ElementNotFoundError, InvalidRequestError  # noqa: E402
from astra_graph.g3_card import G3CardService  # noqa: E402
from astra_graph.graph import AgeGraphRepository, create_pool  # noqa: E402
from astra_graph.graph.queries import accessor  # noqa: E402
from astra_graph.ids import new_ulid  # noqa: E402
from astra_graph.lineage import hydrate  # noqa: E402
from astra_graph.migrations import run as run_migrations  # noqa: E402
from astra_graph.ontology import EDGE_LABELS, NODE_LABELS  # noqa: E402
from astra_graph.principal import Principal  # noqa: E402
from astra_graph.writes import EdgeWrite, GraphWriter, NodeWrite  # noqa: E402

PRINCIPAL = Principal("agent:harvester", run_id="run-harvester")
REPORT_OWNER = Principal("user:owner@client.example")
ENGINEER = Principal("user:engineer@artizent.example")

_MARGIN_CALC_AST: dict[str, Any] = {
    "kind": "AGGREGATE", "name": "SUM",
    "children": [{"kind": "REFERENCE", "name": "Margin", "children": []}],
}


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


@pytest.fixture
def settings() -> Settings:
    """A single raw `asyncpg.connect()`, not a `Pool` -- see `test_integration_exception_
    ageing.py`'s own fixture docstring for why (S8.3.2's own real, fixed hang)."""
    config = _settings(f"astra_g3card_{new_ulid()[10:22].lower()}")

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
            await conn.execute("LOAD 'age'")
            for table in (
                "public.estate_edge_index", "public.estate_element_index", "public.estate_event",
                "public.artefacts", "public.provenance", "public.g3_question",
            ):
                await conn.execute(f"DELETE FROM {table} WHERE graph = $1", config.graph_name)
            await conn.execute("SELECT ag_catalog.drop_graph($1, true)", config.graph_name)
        finally:
            await conn.close()

    if not _run_off_loop(setup):
        pytest.skip("PostgreSQL with Apache AGE not reachable")

    yield config
    _run_off_loop(teardown)


async def _write(writer: GraphWriter, label: str, **properties: Any) -> str:
    created = await writer.write_nodes([NodeWrite(type=label, properties=properties)], principal=PRINCIPAL)
    return str(created[0]["properties"]["id"])


async def _edge(writer: GraphWriter, type_: str, source: str, target: str, **props: Any) -> None:
    await writer.write_edge(EdgeWrite(type=type_, from_id=source, to_id=target, properties=props), principal=PRINCIPAL)


@pytest.fixture
async def estate(settings: Settings):
    pool = await create_pool(settings)
    try:
        repository = AgeGraphRepository(pool, graph_name=settings.graph_name)
        writer = GraphWriter(repository)
        artefact_store = PostgresArtefactStore(pool, graph_name=settings.graph_name)

        suffix = new_ulid()[10:18].lower()
        site = await _write(writer, "Site", luid=f"s-{suffix}", name=f"RQA {suffix}")
        project = await _write(writer, "Project", luid=f"p-{suffix}", name="Risk Core")
        await _edge(writer, "CONTAINS", site, project)
        table = await _write(writer, "Table", name="positions", schema="risk", row_estimate=1000)
        connection = await _write(writer, "Connection", **{"class": "snowflake"}, server="warehouse", db="risk")
        await _edge(writer, "CONNECTS_TO", connection, table)
        book = await _write(writer, "Workbook", luid=f"wb-{suffix}", name="Daily VaR", revision="1")
        await _edge(writer, "CONTAINS", project, book)
        sheet = await _write(
            writer, "Worksheet", name="VaR sheet", rows_shelf=["Desk"], cols_shelf=["MarginCalc"], marks_shelf=[],
        )
        await _edge(writer, "CONTAINS", book, sheet)
        datasource = await _write(
            writer, "Datasource", name="VaR ds", type="published", luid=f"ds-{suffix}",
            extract_flag=True, refresh_schedule="daily",
        )
        await _edge(writer, "USES_DATASOURCE", sheet, datasource)
        await _edge(writer, "CONNECTS_TO", datasource, connection)
        field = await _write(writer, "Field", name="Margin", datatype="real", role="measure")
        await _edge(writer, "HAS_FIELD", datasource, field)
        margin_calc = await _write(
            writer, "CalculatedField", name="MarginCalc", formula="SUM([Margin])",
            formula_ast=_MARGIN_CALC_AST, **{"class": "C1"},
        )
        await _edge(writer, "HAS_FIELD", datasource, margin_calc)
        measure = await _write(
            writer, "Measure", name="MarginCalc", dax="SUM([Margin])", provenance_ref=f"prov_seed_{suffix}",
        )
        await _edge(writer, "MAPS_TO", margin_calc, measure)

        yield {
            "pool": pool, "settings": settings, "writer": writer, "artefact_store": artefact_store,
            "site": site, "project": project, "workbook": book, "sheet": sheet, "datasource": datasource,
            "margin_calc": margin_calc, "measure": measure,
        }
    finally:
        await pool.close()


def _service(estate: dict[str, Any]) -> G3CardService:
    return G3CardService(
        estate["pool"], graph_name=estate["settings"].graph_name, writer=estate["writer"],
        artefact_store=estate["artefact_store"],
    )


async def _write_report(estate: dict[str, Any], *, pages: list[str]) -> str:
    return await _write(
        estate["writer"], "ReportDefinition", mu_ref=estate["workbook"], model_ref="model-placeholder", pages=pages,
    )


async def _write_visual(estate: dict[str, Any], *, page: str, **props: Any) -> str:
    return await _write(estate["writer"], "Visual", page=page, type="bar", source_sheet_ref=estate["sheet"], **props)


async def _write_parity_run(
    estate: dict[str, Any], *, cases_pass: int, cases_fail: int = 0, sampled: bool = False, charter_version: str = "3",
) -> str:
    verdict_ids: list[str] = []
    for i in range(cases_pass + cases_fail):
        case_id = await _write(
            estate["writer"], "ParityCase", mu_ref=estate["workbook"], sheet_ref=estate["sheet"],
            case_key=f"case_{new_ulid()}", grain=["Desk"], measures=["MarginCalc"],
            filter_ctx={}, param_values={},
        )
        result = "PASS" if i < cases_pass else "FAIL"
        verdict_id = await _write(
            estate["writer"], "Verdict", case_ref=case_id, result=result, failing_cells=[],
            evidence_ref=None, sampled=sampled,
        )
        verdict_ids.append(verdict_id)
    return await _write(
        estate["writer"], "ParityRun", suite_ref=estate["workbook"], charter_version=charter_version,
        started="2027-06-01T09:00:00.000Z", finished="2027-06-01T09:00:02.000Z", verdicts=verdict_ids,
    )


async def _open_exception(estate: dict[str, Any], *, case_refs: list[str], state: str = "OPEN") -> str:
    return await _write(
        estate["writer"], "ExceptionCase", mu_ref=estate["workbook"], **{"class": "AGGREGATION"},
        state=state, artefact_ref=estate["margin_calc"], case_refs=case_refs,
    )


async def _write_gate_decision(estate: dict[str, Any], *, subject_ref: str, gate: str, decision: str, **extra: Any) -> str:
    return await _write(
        estate["writer"], "GateDecision", gate=gate, subject_ref=subject_ref, decision=decision,
        approver=ENGINEER.value, timestamp="2027-06-01T09:00:00.000Z", **extra,
    )


async def _case_ref_from_run(estate: dict[str, Any], *, run_id: str) -> str:
    """The real `ParityCase` id a run's own first `Verdict` names -- the same lookup
    `_proof`'s own waiver query performs, used here to open a real `ExceptionCase`
    against a real case this run actually diffed."""
    async with estate["pool"].acquire() as conn:
        run_props = (await hydrate(conn, estate["settings"].graph_name, "ParityRun", [run_id]))[run_id]
        verdict_id = run_props["verdicts"][0]
        verdict_props = (await hydrate(conn, estate["settings"].graph_name, "Verdict", [verdict_id]))[verdict_id]
    return str(verdict_props["case_ref"])


# ---------------------------------------------------------------------------------- what


async def test_what_counts_real_pages_and_real_visuals(estate) -> None:
    await _write_report(estate, pages=["Page1", "Page2"])
    await _write_visual(estate, page="Page1")
    await _write_visual(estate, page="Page2")
    await _write_visual(estate, page="Page3")  # a page this report never claims

    card = await _service(estate).card(estate["workbook"])
    assert card["what"]["name"] == "Daily VaR"
    assert card["what"]["pages"] == 2
    assert card["what"]["visuals"] == 2


async def test_what_is_honest_zero_before_any_composition(estate) -> None:
    card = await _service(estate).card(estate["workbook"])
    assert card["what"]["pages"] == 0
    assert card["what"]["visuals"] == 0


# --------------------------------------------------------------------------------- proof


async def test_proof_reads_a_real_parity_run(estate) -> None:
    await _write_parity_run(estate, cases_pass=3, cases_fail=1, charter_version="3")

    card = await _service(estate).card(estate["workbook"])
    assert card["proof"]["cases_run"] == 4
    assert card["proof"]["cases_pass"] == 3
    assert card["proof"]["charter_version"] == "3"
    assert card["proof"]["sampled"] is False
    assert card["proof"]["passes_the_charter"] is False


async def test_proof_shows_a_real_sampled_flag(estate) -> None:
    await _write_parity_run(estate, cases_pass=2, sampled=True)
    card = await _service(estate).card(estate["workbook"])
    assert card["proof"]["sampled"] is True


async def test_proof_is_honest_with_no_run_yet(estate) -> None:
    card = await _service(estate).card(estate["workbook"])
    assert card["proof"]["cases_run"] == 0
    assert card["proof"]["charter_version"] is None
    assert card["proof"]["waivers"] == []


async def test_proof_shows_a_real_waiver_and_its_justification(estate) -> None:
    run_id = await _write_parity_run(estate, cases_pass=1)
    case_ref = await _case_ref_from_run(estate, run_id=run_id)
    await _open_exception(estate, case_refs=[case_ref])
    await _write_gate_decision(
        estate, subject_ref=case_ref, gate="G3", decision="WAIVED",
        rationale="C4 waiver signed by the report owner and the migration engineer.",
    )

    card = await _service(estate).card(estate["workbook"])
    assert len(card["proof"]["waivers"]) == 1
    assert card["proof"]["waivers"][0]["rationale"] == (
        "C4 waiver signed by the report owner and the migration engineer."
    )


# -------------------------------------------------------------------------------- visual


async def test_visual_averages_real_scores_and_shows_a_real_human_review(estate) -> None:
    await _write_report(estate, pages=["Page1"])
    await _write_visual(estate, page="Page1", structural_score=0.9, image_score=0.8)
    await _write_visual(
        estate, page="Page1", structural_score=0.7, image_score=0.6,
        reviewed_by=REPORT_OWNER.value, reviewed_at="2027-01-14T00:00:00.000Z",
    )

    card = await _service(estate).card(estate["workbook"])
    assert card["visual"]["structural_score"] == pytest.approx(0.8)
    assert card["visual"]["human_review_status"] == "reviewed"
    assert card["visual"]["reviewed"][0]["reviewed_by"] == REPORT_OWNER.value


# ------------------------------------------------------------------------------- changes


async def test_changes_shows_a_real_c4_decision_and_a_real_redesign(estate) -> None:
    run_id = await _write_parity_run(estate, cases_pass=1)
    case_ref = await _case_ref_from_run(estate, run_id=run_id)

    exception_id = await _open_exception(estate, case_refs=[case_ref], state="CLOSED")
    await estate["writer"].set_node_properties(
        estate["margin_calc"],
        {"redesign_decision": "ALTERNATIVE", "redesign_decision_reason": "table-calc addressing"},
        principal=PRINCIPAL,
    )
    await _write_gate_decision(
        estate, subject_ref=exception_id, gate="G3", decision="REDESIGN",
        rationale="Class 4 visual; agreed with the report owner to finish in Desktop.",
    )

    card = await _service(estate).card(estate["workbook"])
    assert len(card["changes"]["c4_decisions"]) == 1
    assert card["changes"]["c4_decisions"][0]["redesign_decision"] == "ALTERNATIVE"
    assert len(card["changes"]["redesigns"]) == 1


async def test_changes_shows_a_real_approved_model_family(estate) -> None:
    family_id = await _write(estate["writer"], "ModelFamily", name="Risk Positions", state="APPROVED", grain="Desk")
    await _edge(estate["writer"], "IN_FAMILY", estate["workbook"], family_id, confidence=1.0)
    await _write_gate_decision(
        estate, subject_ref=family_id, gate="G2", decision="APPROVED",
        rationale="A real, complete G2 review with no open questions.",
    )

    card = await _service(estate).card(estate["workbook"])
    assert card["changes"]["model"]["family_id"] == family_id
    assert card["changes"]["model"]["approved_at"] == "2027-06-01T09:00:00Z"


# ---------------------------------------------------------------------------- decisions


async def test_approve_writes_a_real_gate_decision_with_a_real_countersigner(estate) -> None:
    result = await _service(estate).approve(
        estate["workbook"], rationale="This report is accurate and ready for release.",
        countersigned_by="A. Mehta", principal=REPORT_OWNER,
    )
    assert result.decision == "APPROVED"

    async with estate["pool"].acquire() as conn:
        decision = (await hydrate(conn, estate["settings"].graph_name, "GateDecision", [result.gate_decision_id]))[
            result.gate_decision_id
        ]
    assert decision["gate"] == "G3"
    assert decision["subject_ref"] == estate["workbook"]
    assert decision["approver"] == REPORT_OWNER.value
    assert decision["countersigner"] == "A. Mehta"
    assert decision["evidence_ref"] is not None


async def test_approve_snapshot_records_the_waivers_the_owner_saw(estate) -> None:
    run_id = await _write_parity_run(estate, cases_pass=1)
    case_ref = await _case_ref_from_run(estate, run_id=run_id)
    await _open_exception(estate, case_refs=[case_ref])
    await _write_gate_decision(
        estate, subject_ref=case_ref, gate="G3", decision="WAIVED",
        rationale="C4 waiver signed by the report owner and the migration engineer.",
    )

    result = await _service(estate).approve(
        estate["workbook"], rationale="This report is accurate and ready for release.",
        countersigned_by="A. Mehta", principal=REPORT_OWNER,
    )
    async with estate["pool"].acquire() as conn:
        decision = (await hydrate(conn, estate["settings"].graph_name, "GateDecision", [result.gate_decision_id]))[
            result.gate_decision_id
        ]
    snapshot = await estate["artefact_store"].content(str(decision["evidence_ref"]))
    assert snapshot is not None
    snapshot_card = json.loads(snapshot)
    assert len(snapshot_card["proof"]["waivers"]) == 1


async def test_approve_refuses_a_blank_countersigner(estate) -> None:
    with pytest.raises(InvalidRequestError, match="countersign"):
        await _service(estate).approve(
            estate["workbook"], rationale="This report is accurate and ready for release.",
            countersigned_by="   ", principal=REPORT_OWNER,
        )


async def test_approve_refuses_a_short_rationale(estate) -> None:
    with pytest.raises(InvalidRequestError, match="at least 20 characters"):
        await _service(estate).approve(
            estate["workbook"], rationale="looks fine", countersigned_by="A. Mehta", principal=REPORT_OWNER,
        )


async def test_request_changes_writes_a_real_gate_decision(estate) -> None:
    result = await _service(estate).request_changes(
        estate["workbook"], rationale="The visual on page 2 does not match the source at all.",
        principal=REPORT_OWNER,
    )
    assert result.decision == "CHANGES_REQUESTED"

    card = await _service(estate).card(estate["workbook"])
    assert card["latest_decision"]["decision"] == "CHANGES_REQUESTED"


async def test_ask_question_writes_and_lists_a_real_question(estate) -> None:
    question = await _service(estate).ask_question(
        estate["workbook"], question="Why was the table-calc visual redesigned?", principal=REPORT_OWNER,
    )
    assert question.workbook_id == estate["workbook"]

    listed = await _service(estate).questions(estate["workbook"])
    assert len(listed) == 1
    assert listed[0].question == "Why was the table-calc visual redesigned?"


async def test_ask_question_refuses_a_too_short_question(estate) -> None:
    with pytest.raises(InvalidRequestError):
        await _service(estate).ask_question(estate["workbook"], question="hi", principal=REPORT_OWNER)


async def test_card_refuses_an_unknown_workbook(estate) -> None:
    with pytest.raises(ElementNotFoundError, match="no Workbook"):
        await _service(estate).card("not-a-real-workbook")


# ---------------------------------------------------------------------------- the API


@pytest.fixture
async def http_client(estate):
    from httpx import ASGITransport, AsyncClient

    from astra_graph.main import create_app

    app = create_app()
    app.state.g3_card = _service(estate)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        yield client


def _headers(principal: Principal, *roles: str) -> dict[str, str]:
    return {"X-Astra-Principal": principal.value, "X-Astra-Roles": ",".join(roles)}


async def test_card_route_is_open_to_an_artizent_role(http_client, estate) -> None:
    response = await http_client.get(
        f"/v1/workbooks/{estate['workbook']}:g3-card", headers=_headers(ENGINEER, "migration_engineer"),
    )
    assert response.status_code == 200
    assert response.json()["workbook_id"] == estate["workbook"]


async def test_card_route_as_adaptive_card(http_client, estate) -> None:
    response = await http_client.get(
        f"/v1/workbooks/{estate['workbook']}:g3-card?format=adaptive_card",
        headers=_headers(REPORT_OWNER, "client_report_owner"),
    )
    assert response.status_code == 200
    assert response.json()["type"] == "AdaptiveCard"


async def test_card_route_refuses_an_unrelated_client_role(http_client) -> None:
    response = await http_client.get(
        "/v1/workbooks/wb_1:g3-card", headers=_headers(Principal("user:x@client.example"), "client_data_owner"),
    )
    assert response.status_code == 403


async def test_approve_route_requires_the_report_owner_role(http_client, estate) -> None:
    response = await http_client.post(
        f"/v1/workbooks/{estate['workbook']}:approve-g3",
        json={"rationale": "This report is accurate and ready for release.", "countersigned_by": "A. Mehta"},
        headers=_headers(ENGINEER, "migration_engineer"),
    )
    assert response.status_code == 403


async def test_approve_route_succeeds_for_the_report_owner(http_client, estate) -> None:
    response = await http_client.post(
        f"/v1/workbooks/{estate['workbook']}:approve-g3",
        json={"rationale": "This report is accurate and ready for release.", "countersigned_by": "A. Mehta"},
        headers=_headers(REPORT_OWNER, "client_report_owner"),
    )
    assert response.status_code == 200
    assert response.json()["decision"] == "APPROVED"
