"""The Programme Board's KPI strip, train swimlanes, milestone rail, the Calibration
Report and the Status Pack -- story S10.2.1, opening F10.2, against real PostgreSQL +
Apache AGE.

What only the real store can answer: that a train's own real `IN_TRAIN` state, a real
`GateDecision`'s own timestamp, a real signed `calibration_baseline` row and a real
generated `status_pack` row all round-trip through Postgres/AGE exactly as written, and
that the PDF/PPTX bytes this story's new dependencies produce are real, well-formed
documents, not placeholders.
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

from astra_graph.adoption import PostgresAdoptionConfigStore, PostgresAdoptionStore  # noqa: E402
from astra_graph.calibration_wave import (  # noqa: E402
    calibration_report,
    render_calibration_report_pdf,
    sign_report,
)
from astra_graph.config import Settings  # noqa: E402
from astra_graph.events import source_for  # noqa: E402
from astra_graph.g2 import PostgresQuestionStore  # noqa: E402
from astra_graph.graph import AgeGraphRepository, create_pool  # noqa: E402
from astra_graph.graph.queries import accessor  # noqa: E402
from astra_graph.ids import new_ulid  # noqa: E402
from astra_graph.invoicing import PostgresUnitPriceStore  # noqa: E402
from astra_graph.migrations import run as run_migrations  # noqa: E402
from astra_graph.ontology import EDGE_LABELS, NODE_LABELS  # noqa: E402
from astra_graph.principal import PRINCIPAL_HEADER, Principal  # noqa: E402
from astra_graph.programme_surface import kpi_strip, milestone_rail, train_swimlanes  # noqa: E402
from astra_graph.roles import ROLES_HEADER  # noqa: E402
from astra_graph.scope import PostgresScopeStore  # noqa: E402
from astra_graph.status_pack import (  # noqa: E402
    edit_pack,
    generate_pack,
    latest_pack,
    publish_pack,
    render_pdf,
    render_pptx,
)
from astra_graph.writes import EdgeWrite, GraphWriter, NodeWrite  # noqa: E402

from .conftest import seed_estate  # noqa: E402

PRINCIPAL = Principal("agent:harvester", run_id="run-programme-surface")
PM_PRINCIPAL = Principal("user:pm@artizent.example")


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
    config = _settings(f"astra_progsurf_{new_ulid()[10:22].lower()}")

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
def scope_store(pool, settings: Settings) -> PostgresScopeStore:
    return PostgresScopeStore(pool, graph_name=settings.graph_name)


@pytest.fixture
def unit_price_store(pool, settings: Settings) -> PostgresUnitPriceStore:
    return PostgresUnitPriceStore(pool, graph_name=settings.graph_name)


@pytest.fixture
def question_store(pool, settings: Settings) -> PostgresQuestionStore:
    return PostgresQuestionStore(pool, graph_name=settings.graph_name)


@pytest.fixture
def adoption_store(pool, settings: Settings) -> PostgresAdoptionStore:
    return PostgresAdoptionStore(pool, graph_name=settings.graph_name)


@pytest.fixture
def adoption_config_store(pool, settings: Settings) -> PostgresAdoptionConfigStore:
    return PostgresAdoptionConfigStore(pool, graph_name=settings.graph_name)


async def _write_train(writer: GraphWriter, *, workbook_id: str, state: str = "CLUSTERED") -> str:
    train_id = new_ulid()
    await writer.write_nodes(
        [
            NodeWrite(
                type="ReleaseTrain", id=train_id,
                properties={
                    "name": f"Train {train_id[-4:]}",
                    "planned_start": "2027-01-01",
                    "planned_end": "2027-01-31",
                    "gate_schedule": {"G2": {"planned_date": "2027-01-01"}, "G3": {"planned_date": "2027-01-31"}},
                },
            )
        ],
        principal=PRINCIPAL,
    )
    await writer.write_edge(
        EdgeWrite(
            type="IN_TRAIN", id=new_ulid(), from_id=workbook_id, to_id=train_id,
            properties={"sequence": 1, "state": state},
        ),
        principal=PRINCIPAL,
    )
    return train_id


async def _write_gate_decision(writer: GraphWriter, *, subject_ref: str, gate: str = "G3") -> str:
    created = await writer.write_nodes(
        [
            NodeWrite(
                type="GateDecision",
                properties={
                    "gate": gate, "subject_ref": subject_ref, "decision": "APPROVED",
                    "approver": "user:owner@client.example",
                    "timestamp": datetime.now(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z"),
                },
            )
        ],
        principal=PRINCIPAL,
    )
    return str(created[0]["properties"]["id"])


# ------------------------------------------------------------------------- KPI strip


async def test_kpi_strip_is_honest_about_an_empty_estate(
    pool, settings, question_store, unit_price_store, adoption_store, adoption_config_store,
) -> None:
    result = await kpi_strip(
        pool, settings.graph_name, question_store=question_store, unit_price_store=unit_price_store,
        adoption_store=adoption_store, adoption_config_store=adoption_config_store,
    )
    assert result["mus_by_state"] == {}
    assert result["first_pass_parity"] == {"cases": 0, "first_pass": 0, "first_pass_rate": None}
    assert result["gates_due_this_week"]["scope"].startswith("G2 only")
    assert result["spend_vs_budget"]["budget"] > 0  # the real planned-value figure, always computable


async def test_kpi_strip_counts_a_real_mu_by_state(
    writer, pool, settings, question_store, unit_price_store, adoption_store, adoption_config_store,
) -> None:
    seeded = await seed_estate(writer, suffix=f"-{new_ulid()}")
    await _write_train(writer, workbook_id=seeded["workbook"], state="CLUSTERED")

    result = await kpi_strip(
        pool, settings.graph_name, question_store=question_store, unit_price_store=unit_price_store,
        adoption_store=adoption_store, adoption_config_store=adoption_config_store,
    )
    assert result["mus_by_state"]["CLUSTERED"] >= 1


async def test_spend_vs_budget_is_real_arithmetic_over_disclosed_planning_constants(
    pool, settings, unit_price_store,
) -> None:
    from astra_graph.invoicing import PLANNED_BY_TIER
    from astra_graph.scope import TIERS

    prices = await unit_price_store.all()
    expected_budget = sum(PLANNED_BY_TIER[tier] * prices[tier] for tier in TIERS)

    result = await kpi_strip(
        pool, settings.graph_name,
        question_store=PostgresQuestionStore(pool, graph_name=settings.graph_name),
        unit_price_store=unit_price_store,
        adoption_store=PostgresAdoptionStore(pool, graph_name=settings.graph_name),
        adoption_config_store=PostgresAdoptionConfigStore(pool, graph_name=settings.graph_name),
    )
    assert result["spend_vs_budget"]["budget"] == expected_budget


# --------------------------------------------------------------------- train swimlanes


async def test_train_swimlanes_reports_real_state_counts_and_blocked_reasons(
    writer, pool, settings,
) -> None:
    seeded = await seed_estate(writer, suffix=f"-{new_ulid()}")
    train_id = await _write_train(writer, workbook_id=seeded["workbook"], state="CLUSTERED")

    await writer.write_nodes(
        [
            NodeWrite(
                type="ExceptionCase",
                properties={
                    "mu_ref": seeded["workbook"], "class": "AGGREGATION", "state": "BLOCKED",
                    "decision": "MODEL_DEFECT_FOUNDRY", "family_ref": "fam_fixture",
                },
            )
        ],
        principal=PRINCIPAL,
    )

    result = await train_swimlanes(pool, settings.graph_name)
    lane = next(t for t in result["trains"] if t["id"] == train_id)
    assert lane["state_counts"] == {"CLUSTERED": 1}
    assert lane["blocked_count"] == 1
    assert lane["blocked"][0]["reason"] == "routed to the Foundry as a model-defect change request"


# ----------------------------------------------------------------------- milestone rail


async def test_milestone_rail_includes_real_train_dates_and_gate_decisions(
    writer, pool, settings,
) -> None:
    seeded = await seed_estate(writer, suffix=f"-{new_ulid()}")
    train_id = await _write_train(writer, workbook_id=seeded["workbook"])
    await _write_gate_decision(writer, subject_ref=seeded["workbook"], gate="G3")

    result = await milestone_rail(pool, settings.graph_name)
    kinds = {(m["kind"], m["ref"]) for m in result["rail"]}
    assert ("train", train_id) in kinds
    assert any(kind == "gate" for kind, _ref in kinds)
    assert "G3" in result["gate_calendar"]


# ------------------------------------------------------------------- Calibration Report


async def test_calibration_report_reflects_real_class_mix(
    writer, pool, settings, scope_store, unit_price_store,
) -> None:
    seeded = await seed_estate(writer, suffix=f"-{new_ulid()}")
    await writer.upsert_nodes(
        [
            NodeWrite(
                type="CalculatedField",
                id=seeded["calc"],
                properties={
                    "name": "Margin %", "formula": "SUM([M]) / SUM([R])",
                    "formula_ast": {"op": "DIV"}, "class": "C4", "pattern_ref": "b1:regexp",
                },
            )
        ],
        principal=PRINCIPAL,
    )

    result = await calibration_report(pool, settings.graph_name, scope_store=scope_store, unit_price_store=unit_price_store)
    report = result["report"]
    assert report["class_mix"]["counts"]["C4"] >= 1
    assert report["c4"]["c4_count"] >= 1
    assert report["c4"]["by_reason"]
    assert report["elapsed_time_per_stage"]["available"] is False
    assert report["executor_strategy_mix"]["available"] is False
    assert result["baseline"] is None
    assert result["comparison"] is None


async def test_signing_writes_a_real_versioned_baseline_and_a_later_report_compares_against_it(
    writer, pool, settings, scope_store, unit_price_store,
) -> None:
    await seed_estate(writer, suffix=f"-{new_ulid()}")

    first = await sign_report(
        pool, settings.graph_name, scope_store=scope_store, unit_price_store=unit_price_store,
        countersigned_by="A. Mehta", signed_by=PM_PRINCIPAL.value,
    )
    assert first.version == 1
    assert first.signed_by == PM_PRINCIPAL.value
    assert first.countersigned_by == "A. Mehta"

    second = await sign_report(
        pool, settings.graph_name, scope_store=scope_store, unit_price_store=unit_price_store,
        countersigned_by="A. Mehta", signed_by=PM_PRINCIPAL.value,
    )
    assert second.version == 2  # append-only -- a second signing is a new row, not an overwrite

    result = await calibration_report(pool, settings.graph_name, scope_store=scope_store, unit_price_store=unit_price_store)
    assert result["baseline"]["version"] == 2
    assert result["comparison"] is not None


async def test_signing_refuses_an_empty_countersigner(pool, settings, scope_store, unit_price_store) -> None:
    from astra_graph.errors import InvalidRequestError

    with pytest.raises(InvalidRequestError, match="client analytics lead"):
        await sign_report(
            pool, settings.graph_name, scope_store=scope_store, unit_price_store=unit_price_store,
            countersigned_by="   ", signed_by=PM_PRINCIPAL.value,
        )


async def test_calibration_report_pdf_is_a_real_pdf(pool, settings, scope_store, unit_price_store) -> None:
    result = await calibration_report(pool, settings.graph_name, scope_store=scope_store, unit_price_store=unit_price_store)
    pdf_bytes = render_calibration_report_pdf(result)
    assert pdf_bytes.startswith(b"%PDF-")
    assert len(pdf_bytes) > 500


# ------------------------------------------------------------------------- Status Pack


async def test_generate_edit_publish_status_pack(
    writer, pool, settings, question_store, unit_price_store, adoption_store, adoption_config_store,
) -> None:
    await seed_estate(writer, suffix=f"-{new_ulid()}")

    generated = await generate_pack(
        pool, settings.graph_name, question_store=question_store, unit_price_store=unit_price_store,
        adoption_store=adoption_store, adoption_config_store=adoption_config_store,
        generated_by=PM_PRINCIPAL.value,
    )
    assert generated.version == 1
    assert "Migration Units by state" in generated.narrative
    assert generated.published_at is None

    edited = await edit_pack(pool, settings.graph_name, narrative="A hand-edited narrative.", edited_by=PM_PRINCIPAL.value)
    assert edited.version == 2
    assert edited.narrative == "A hand-edited narrative."
    assert edited.report == generated.report  # an edit changes the words, not the frozen numbers

    published = await publish_pack(pool, settings.graph_name)
    assert published.published_at is not None
    assert published.version == 2

    latest = await latest_pack(pool, settings.graph_name)
    assert latest is not None
    assert latest.id == published.id


async def test_status_pack_pdf_and_pptx_are_real_documents(pool, settings) -> None:
    pack = await latest_pack(pool, settings.graph_name)
    assert pack is not None

    pdf_bytes = render_pdf(pack)
    assert pdf_bytes.startswith(b"%PDF-")

    pptx_bytes = render_pptx(pack)
    assert pptx_bytes[:2] == b"PK"  # PPTX is a real zip archive (OOXML)


# ----------------------------------------------------------------------------- the API


@pytest.fixture
async def http_client(settings, pool, repository, scope_store, unit_price_store, question_store, adoption_store, adoption_config_store):
    from astra_graph.main import create_app

    app = create_app()
    app.state.pool = pool
    app.state.repository = repository
    app.state.scope_store = scope_store
    app.state.unit_price_store = unit_price_store
    app.state.question_store = question_store
    app.state.adoption_store = adoption_store
    app.state.adoption_config_store = adoption_config_store

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://graph-svc") as async_client:
        yield async_client


def _headers(*, roles: str = "programme_manager") -> dict[str, str]:
    return {PRINCIPAL_HEADER: PM_PRINCIPAL.value, ROLES_HEADER: roles}


async def test_kpis_over_http(http_client) -> None:
    response = await http_client.get("/v1/programme:kpis", headers=_headers(roles="parity_engineer"))
    assert response.status_code == 200
    assert "mus_by_state" in response.json()


async def test_sign_calibration_report_refuses_a_non_programme_manager_over_http(http_client) -> None:
    response = await http_client.post(
        "/v1/calibration:sign", json={"countersigned_by": "A. Mehta"}, headers=_headers(roles="migration_engineer"),
    )
    assert response.status_code == 403


async def test_calibration_report_pdf_over_http(http_client) -> None:
    response = await http_client.get("/v1/calibration:report.pdf", headers=_headers(roles="client_analytics_lead"))
    assert response.status_code == 200
    assert response.headers["content-type"] == "application/pdf"
    assert response.content.startswith(b"%PDF-")


async def test_status_pack_generate_over_http_then_read_by_any_artizent_role(http_client) -> None:
    started = await http_client.post("/v1/status-pack:generate", headers=_headers())
    assert started.status_code == 200

    read = await http_client.get("/v1/status-pack", headers=_headers(roles="parity_engineer"))
    assert read.status_code == 200
    assert read.json()["narrative"]


async def test_status_pack_generate_refuses_a_non_programme_manager_over_http(http_client) -> None:
    response = await http_client.post("/v1/status-pack:generate", headers=_headers(roles="parity_engineer"))
    assert response.status_code == 403
