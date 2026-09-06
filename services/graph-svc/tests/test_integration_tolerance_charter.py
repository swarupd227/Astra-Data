"""The Tolerance Charter, against real PostgreSQL + Apache AGE -- story S7.1.1, opening
E7/F7.1, spec §4.4/§13.1.

What only the real stack can answer: that a saved charter version is really immutable and
really versioned by insert, that a real `GateDecision(gate="G1")` records the client
analytics lead's approval countersigned by the Parity Engineer, that changing an
already-approved charter really refuses without the client analytics lead's own sign-off
and really writes a fresh G1 decision when given one, that a real workbook proved under
the superseded version is really found and really marked for re-proof, that `simulate`
really re-diffs a real stored Verdict's own sampled cells without writing anything, and
that every new route drives its own real role gate.
"""

from __future__ import annotations

import asyncio
import os
import threading
from collections.abc import Callable, Coroutine
from typing import Any

import pytest

pytestmark = pytest.mark.integration

asyncpg = pytest.importorskip("asyncpg")

from astra_graph.config import Settings  # noqa: E402
from astra_graph.events import source_for  # noqa: E402
from astra_graph.graph import AgeGraphRepository, create_pool  # noqa: E402
from astra_graph.graph.queries import accessor  # noqa: E402
from astra_graph.ids import new_ulid  # noqa: E402
from astra_graph.migration_units import (  # noqa: E402
    InMemoryMigrationUnitRegistry,
    MigrationUnitRef,
)
from astra_graph.migrations import run as run_migrations  # noqa: E402
from astra_graph.ontology import EDGE_LABELS, NODE_LABELS  # noqa: E402
from astra_graph.principal import Principal  # noqa: E402
from astra_graph.tolerance_charter import (  # noqa: E402
    DEFAULT_CHARTER,
    NumericRule,
    PostgresToleranceCharterStore,
    ToleranceCharter,
    ToleranceCharterError,
    ToleranceCharterService,
    affected_workbook_ids,
    has_g1_decision,
)
from astra_graph.writes import EdgeWrite, GraphWriter, NodeWrite  # noqa: E402

PRINCIPAL = Principal("agent:modeller", run_id="run-modeller")
PARITY_ENGINEER = Principal("user:parity@artizent.example")
ANALYTICS_LEAD = Principal("user:lead@client.example")


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
    """Function-scoped, deliberately -- unlike a workbook-scoped fixture elsewhere in this
    suite, the Tolerance Charter and its G1 decision are graph-wide singletons (there is
    exactly one charter per graph), so sharing one graph across tests in this file would
    have each test's own G1 approval leak into every test that runs after it."""
    config = _settings(f"astra_charter_{new_ulid()[10:22].lower()}")

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
                "public.estate_edge_index",
                "public.estate_element_index",
                "public.estate_event",
                "public.tolerance_charter_version",
            ):
                await conn.execute(f"DELETE FROM {table} WHERE graph = $1", config.graph_name)
            await conn.execute("SELECT ag_catalog.drop_graph($1, true)", config.graph_name)
        finally:
            await conn.close()

    if not _run_off_loop(setup):
        pytest.skip("PostgreSQL with Apache AGE not reachable")

    yield config
    _run_off_loop(teardown)


async def _write(writer: GraphWriter, type_: str, **properties: Any) -> str:
    created = await writer.write_nodes(
        [NodeWrite(type=type_, properties=properties)], principal=PRINCIPAL
    )
    return str(created[0]["properties"]["id"])


async def _edge(writer: GraphWriter, type_: str, source: str, target: str, **props: Any) -> None:
    await writer.write_edge(
        EdgeWrite(type=type_, from_id=source, to_id=target, properties=props), principal=PRINCIPAL
    )


@pytest.fixture
async def estate(settings: Settings):
    pool = await create_pool(settings)
    try:
        repository = AgeGraphRepository(pool, graph_name=settings.graph_name)
        writer = GraphWriter(repository, event_source=source_for(settings.graph_name))
        store = PostgresToleranceCharterStore(pool, graph_name=settings.graph_name)
        migration_units = InMemoryMigrationUnitRegistry()
        service = ToleranceCharterService(
            pool, graph_name=settings.graph_name, writer=writer,
            store=store, migration_units=migration_units,
        )
        yield {
            "pool": pool, "settings": settings, "writer": writer,
            "store": store, "migration_units": migration_units, "service": service,
        }
    finally:
        await pool.close()


def _edited(**numeric_overrides: Any) -> ToleranceCharter:
    return ToleranceCharter(numeric=NumericRule(**{**DEFAULT_CHARTER.numeric.as_dict(), **numeric_overrides}))


# ------------------------------------------------------------------------------- versioning


async def test_the_default_charter_is_version_zero_before_any_save(estate) -> None:
    latest = await estate["store"].latest()
    assert latest.version == 0
    assert latest.charter == DEFAULT_CHARTER


async def test_saving_creates_an_immutable_version_one(estate) -> None:
    saved = await estate["service"].save(_edited(abs_epsilon=0.01), principal=PARITY_ENGINEER)
    assert saved["charter"]["version"] == 1
    assert saved["charter"]["charter"]["numeric"]["abs_epsilon"] == 0.01


async def test_versions_are_immutable_a_second_save_never_overwrites_the_first(estate) -> None:
    first = await estate["service"].save(_edited(abs_epsilon=0.01), principal=PARITY_ENGINEER)
    await estate["service"].save(_edited(abs_epsilon=0.02), principal=PARITY_ENGINEER)

    still_there = await estate["store"].get(first["charter"]["version"])
    assert still_there is not None
    assert still_there.charter.numeric.abs_epsilon == 0.01

    latest = await estate["store"].latest()
    assert latest.version == first["charter"]["version"] + 1
    assert latest.charter.numeric.abs_epsilon == 0.02


# ---------------------------------------------------------------------------------- G1


async def test_no_g1_decision_exists_before_one_is_recorded(estate) -> None:
    assert await has_g1_decision(estate["pool"], estate["settings"].graph_name) is False


async def test_approving_at_g1_records_the_client_lead_and_the_countersigning_engineer(estate) -> None:
    saved = await estate["service"].save(_edited(abs_epsilon=0.01), principal=PARITY_ENGINEER)
    version = saved["charter"]["version"]

    result = await estate["service"].approve_g1(
        version=version, principal=ANALYTICS_LEAD,
        countersigned_by=PARITY_ENGINEER.value, rationale="Agreed at kickoff with the client.",
    )
    assert result["decision"] == "APPROVED"
    assert await has_g1_decision(estate["pool"], estate["settings"].graph_name) is True


async def test_approving_at_g1_refuses_a_blank_countersigner(estate) -> None:
    saved = await estate["service"].save(_edited(abs_epsilon=0.01), principal=PARITY_ENGINEER)
    with pytest.raises(ToleranceCharterError, match="countersign"):
        await estate["service"].approve_g1(
            version=saved["charter"]["version"], principal=ANALYTICS_LEAD,
            countersigned_by="   ", rationale="Agreed at kickoff with the client.",
        )


async def test_approving_at_g1_refuses_a_short_rationale(estate) -> None:
    saved = await estate["service"].save(_edited(abs_epsilon=0.01), principal=PARITY_ENGINEER)
    with pytest.raises(ToleranceCharterError, match="rationale"):
        await estate["service"].approve_g1(
            version=saved["charter"]["version"], principal=ANALYTICS_LEAD,
            countersigned_by=PARITY_ENGINEER.value, rationale="ok",
        )


# --------------------------------------------------------------------- changing after G1


async def test_saving_before_g1_needs_no_client_sign_off(estate) -> None:
    result = await estate["service"].save(_edited(abs_epsilon=0.01), principal=PARITY_ENGINEER)
    assert result["is_revision"] is False


async def test_changing_the_charter_after_g1_is_refused_without_the_analytics_leads_ack(estate) -> None:
    saved = await estate["service"].save(_edited(abs_epsilon=0.01), principal=PARITY_ENGINEER)
    await estate["service"].approve_g1(
        version=saved["charter"]["version"], principal=ANALYTICS_LEAD,
        countersigned_by=PARITY_ENGINEER.value, rationale="Agreed at kickoff with the client.",
    )
    with pytest.raises(ToleranceCharterError, match="client analytics lead"):
        await estate["service"].save(_edited(abs_epsilon=0.02), principal=PARITY_ENGINEER)


async def test_changing_the_charter_after_g1_is_refused_with_a_short_reason(estate) -> None:
    saved = await estate["service"].save(_edited(abs_epsilon=0.01), principal=PARITY_ENGINEER)
    await estate["service"].approve_g1(
        version=saved["charter"]["version"], principal=ANALYTICS_LEAD,
        countersigned_by=PARITY_ENGINEER.value, rationale="Agreed at kickoff with the client.",
    )
    with pytest.raises(ToleranceCharterError, match="reason"):
        await estate["service"].save(
            _edited(abs_epsilon=0.02), principal=PARITY_ENGINEER,
            client_analytics_lead_ack=ANALYTICS_LEAD.value, reason="short",
        )


async def test_changing_the_charter_after_g1_succeeds_with_both_parties_and_records_a_fresh_g1_decision(
    estate,
) -> None:
    saved = await estate["service"].save(_edited(abs_epsilon=0.01), principal=PARITY_ENGINEER)
    await estate["service"].approve_g1(
        version=saved["charter"]["version"], principal=ANALYTICS_LEAD,
        countersigned_by=PARITY_ENGINEER.value, rationale="Agreed at kickoff with the client.",
    )

    revised = await estate["service"].save(
        _edited(abs_epsilon=0.02), principal=PARITY_ENGINEER,
        client_analytics_lead_ack=ANALYTICS_LEAD.value,
        reason="Client requested a looser tolerance after the calibration wave.",
    )
    assert revised["is_revision"] is True
    assert revised["charter"]["charter"]["numeric"]["abs_epsilon"] == 0.02

    from astra_graph.graph.queries import NODE_INDEX_TABLE

    async with estate["pool"].acquire() as conn:
        rows = await conn.fetch(
            f"SELECT id FROM {NODE_INDEX_TABLE} WHERE graph = $1 AND label = 'GateDecision' AND retired_at IS NULL",
            estate["settings"].graph_name,
        )
    assert len(rows) == 2  # the first approval, plus this revision's own re-approval


async def test_changing_the_charter_after_g1_re_proves_every_workbook_proved_under_the_superseded_version(
    estate,
) -> None:
    saved = await estate["service"].save(_edited(abs_epsilon=0.01), principal=PARITY_ENGINEER)
    superseded_version = saved["charter"]["version"]
    await estate["service"].approve_g1(
        version=superseded_version, principal=ANALYTICS_LEAD,
        countersigned_by=PARITY_ENGINEER.value, rationale="Agreed at kickoff with the client.",
    )

    workbook = await _write(estate["writer"], "Workbook", luid="wb-affected", name="Affected Workbook", revision="1")
    report = await _write(
        estate["writer"], "ReportDefinition", mu_ref=workbook, pages=["Overview"], model_ref="sm1", version="1",
    )
    run = await _write(
        estate["writer"], "ParityRun", suite_ref="suite1", charter_version=str(superseded_version),
        started="2026-09-01T00:00:00.000Z", finished="2026-09-01T00:05:00.000Z", verdicts=[],
    )
    await _edge(estate["writer"], "PROVED_BY", report, run, charter_version=str(superseded_version))

    estate["migration_units"].add(
        MigrationUnitRef(id=workbook, state="PASSED", site="rqa", workbook_luid="wb-affected")
    )

    found = await affected_workbook_ids(
        estate["pool"], estate["settings"].graph_name, superseded_version=str(superseded_version)
    )
    assert found == [workbook]

    revised = await estate["service"].save(
        _edited(abs_epsilon=0.02), principal=PARITY_ENGINEER,
        client_analytics_lead_ack=ANALYTICS_LEAD.value,
        reason="Client requested a looser tolerance after the calibration wave.",
    )
    assert revised["reproved_workbook_ids"] == [workbook]
    assert (workbook, "tolerance charter revised", PARITY_ENGINEER.value) in estate["migration_units"].marked


async def test_affected_workbook_ids_is_empty_when_nothing_ran_under_that_version(estate) -> None:
    found = await affected_workbook_ids(
        estate["pool"], estate["settings"].graph_name, superseded_version="not-a-real-version"
    )
    assert found == []


# ---------------------------------------------------------------------------- simulate


async def test_simulate_reports_no_prior_run_for_an_unproved_workbook(estate) -> None:
    result = await estate["service"].simulate(workbook_id="not-a-real-workbook", charter=DEFAULT_CHARTER)
    assert result["has_prior_run"] is False
    assert result["verdicts"] == []


async def test_simulate_re_diffs_a_real_verdicts_own_sampled_cells_without_writing_anything(estate) -> None:
    workbook = await _write(estate["writer"], "Workbook", luid="wb-sim", name="Sim Workbook", revision="1")
    report = await _write(
        estate["writer"], "ReportDefinition", mu_ref=workbook, pages=["Overview"], model_ref="sm1", version="1",
    )
    verdict = await _write(
        estate["writer"], "Verdict", case_ref="case1", result="FAIL",
        failing_cells=[
            {"kind": "numeric", "grain_key": "Desk=EMEA", "measure": "Margin", "expected": 100.0, "candidate": 100.2},
        ],
    )
    run = await _write(
        estate["writer"], "ParityRun", suite_ref="suite1", charter_version="1",
        started="2026-09-01T00:00:00.000Z", finished="2026-09-01T00:05:00.000Z", verdicts=[verdict],
    )
    await _edge(estate["writer"], "PROVED_BY", report, run, charter_version="1")

    tight = ToleranceCharter(numeric=NumericRule(abs_epsilon=0.01, rel_epsilon=0.0))
    result = await estate["service"].simulate(workbook_id=workbook, charter=tight)
    assert result["has_prior_run"] is True
    assert result["run_id"] == run
    assert len(result["verdicts"]) == 1
    assert result["verdicts"][0]["result"] == "FAIL"

    loose = ToleranceCharter(numeric=NumericRule(abs_epsilon=1.0, rel_epsilon=0.0))
    result_loose = await estate["service"].simulate(workbook_id=workbook, charter=loose)
    assert result_loose["verdicts"][0]["result"] == "PASS"

    # nothing was written -- the stored Verdict itself is unchanged
    stored = await estate["store"].latest()
    assert stored.version == 0  # simulate never touches the charter store either


# ---------------------------------------------------------------------------------- API


@pytest.fixture
async def http_client(estate):
    from httpx import ASGITransport, AsyncClient

    from astra_graph.main import create_app

    app = create_app()
    app.state.tolerance_charter = estate["service"]

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://graph-svc") as async_client:
        yield async_client


def _headers(role: str, principal: Principal) -> dict[str, str]:
    from astra_graph.principal import PRINCIPAL_HEADER
    from astra_graph.roles import ROLES_HEADER

    return {PRINCIPAL_HEADER: principal.value, ROLES_HEADER: role}


async def test_get_charter_over_http_is_open_to_any_artizent_role(estate, http_client) -> None:
    response = await http_client.get("/v1/tolerance-charter", headers=_headers("platform_engineer", PARITY_ENGINEER))
    assert response.status_code == 200
    body = response.json()
    assert body["charter"]["version"] == 0
    assert "abs_epsilon" in body["field_metadata"]["numeric"]


async def test_get_charter_over_http_is_also_open_to_the_client_analytics_lead(estate, http_client) -> None:
    # A live gap this story found by driving the console for real: the client analytics
    # lead approves a charter at G1 and must be able to read it first.
    response = await http_client.get(
        "/v1/tolerance-charter", headers=_headers("client_analytics_lead", ANALYTICS_LEAD)
    )
    assert response.status_code == 200


async def test_get_charter_over_http_refuses_an_unrelated_client_role(estate, http_client) -> None:
    response = await http_client.get(
        "/v1/tolerance-charter", headers=_headers("client_licence_admin", ANALYTICS_LEAD)
    )
    assert response.status_code == 403


def _charter_payload(abs_epsilon: float = 0.01) -> dict[str, Any]:
    payload = _edited(abs_epsilon=abs_epsilon).as_dict()
    return payload


async def test_save_charter_over_http_requires_the_parity_engineer_role(estate, http_client) -> None:
    response = await http_client.post(
        "/v1/tolerance-charter", json={"charter": _charter_payload()},
        headers=_headers("programme_manager", PARITY_ENGINEER),
    )
    assert response.status_code == 403


async def test_save_charter_over_http_succeeds_for_the_parity_engineer(estate, http_client) -> None:
    response = await http_client.post(
        "/v1/tolerance-charter", json={"charter": _charter_payload()},
        headers=_headers("parity_engineer", PARITY_ENGINEER),
    )
    assert response.status_code == 200
    assert response.json()["charter"]["version"] == 1


async def test_approve_g1_over_http_requires_the_client_analytics_lead_role(estate, http_client) -> None:
    await http_client.post(
        "/v1/tolerance-charter", json={"charter": _charter_payload()},
        headers=_headers("parity_engineer", PARITY_ENGINEER),
    )
    response = await http_client.post(
        "/v1/tolerance-charter/1:approve-g1",
        json={"countersigned_by": PARITY_ENGINEER.value, "rationale": "Agreed at kickoff."},
        headers=_headers("client_data_owner", ANALYTICS_LEAD),
    )
    assert response.status_code == 403


async def test_approve_g1_over_http_succeeds_for_the_client_analytics_lead(estate, http_client) -> None:
    await http_client.post(
        "/v1/tolerance-charter", json={"charter": _charter_payload()},
        headers=_headers("parity_engineer", PARITY_ENGINEER),
    )
    response = await http_client.post(
        "/v1/tolerance-charter/1:approve-g1",
        json={"countersigned_by": PARITY_ENGINEER.value, "rationale": "Agreed at kickoff."},
        headers=_headers("client_analytics_lead", ANALYTICS_LEAD),
    )
    assert response.status_code == 200
    assert response.json()["decision"] == "APPROVED"


async def test_save_after_g1_over_http_is_refused_without_the_client_lead_ack(estate, http_client) -> None:
    await http_client.post(
        "/v1/tolerance-charter", json={"charter": _charter_payload()},
        headers=_headers("parity_engineer", PARITY_ENGINEER),
    )
    await http_client.post(
        "/v1/tolerance-charter/1:approve-g1",
        json={"countersigned_by": PARITY_ENGINEER.value, "rationale": "Agreed at kickoff."},
        headers=_headers("client_analytics_lead", ANALYTICS_LEAD),
    )
    response = await http_client.post(
        "/v1/tolerance-charter", json={"charter": _charter_payload(0.02)},
        headers=_headers("parity_engineer", PARITY_ENGINEER),
    )
    assert response.status_code == 400


async def test_simulate_over_http_is_open_to_any_artizent_role(estate, http_client) -> None:
    response = await http_client.post(
        "/v1/workbooks/not-a-real-workbook/tolerance-charter:simulate",
        json={"charter": DEFAULT_CHARTER.as_dict()},
        headers=_headers("platform_engineer", PARITY_ENGINEER),
    )
    assert response.status_code == 200
    assert response.json()["has_prior_run"] is False
