"""The Gate Inbox -- story S10.4.1, opening F10.4, against real PostgreSQL + Apache AGE.

What only the real store can answer: that a real family `IN_REVIEW` really appears in
the G2 list and is really excluded once its own real `domain` falls outside the
caller's asserted `X-Astra-Domain-Scope`; that a real composed report with a real
passing `ParityRun` really appears as an open G3 card, and really disappears once a
real `GateDecision(gate="G3", decision="APPROVED")` exists but stays open after a real
`CHANGES_REQUESTED`; that a real, fully-ready site (every real G4 precondition met)
really appears as an open G4 card, disappears once approved, and stays open after a
real deferral; that the inbox is really role-dispatched (a data owner sees only G2, a
report owner only G3, a licence admin only G4, an Artizent role all three); and that a
real "new request" notification is really recorded once and never sent twice for the
same item.
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

from astra_graph.adoption import AdoptionSnapshot, PostgresAdoptionStore  # noqa: E402
from astra_graph.config import Settings  # noqa: E402
from astra_graph.events import source_for  # noqa: E402
from astra_graph.g2 import PostgresQuestionStore  # noqa: E402
from astra_graph.g4_card import PostgresDecommissionConfirmationStore  # noqa: E402
from astra_graph.gate_inbox import (  # noqa: E402
    gate_inbox,
    pending_g2_items,
    pending_g3_cards,
    pending_g4_sites,
)
from astra_graph.gate_notifications import (  # noqa: E402
    LocalGateNotificationChannel,
    PostgresGateNotificationStore,
    notify_new_requests,
)
from astra_graph.graph import AgeGraphRepository, create_pool  # noqa: E402
from astra_graph.graph.queries import accessor  # noqa: E402
from astra_graph.ids import new_ulid  # noqa: E402
from astra_graph.migrations import run as run_migrations  # noqa: E402
from astra_graph.ontology import EDGE_LABELS, NODE_LABELS  # noqa: E402
from astra_graph.principal import PRINCIPAL_HEADER, Principal  # noqa: E402
from astra_graph.regression import PostgresRegressionScheduleStore  # noqa: E402
from astra_graph.release import PostgresPromotionStore  # noqa: E402
from astra_graph.roles import ROLES_HEADER  # noqa: E402
from astra_graph.roles import parse as parse_roles  # noqa: E402
from astra_graph.scope import PostgresScopeStore  # noqa: E402
from astra_graph.writes import EdgeWrite, GraphWriter, NodeWrite  # noqa: E402

from .conftest import seed_estate  # noqa: E402

PRINCIPAL = Principal("agent:harvester", run_id="run-gate-inbox")
ENGINEER = Principal("user:sme@artizent.example")
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
                  "public.gate_notification", "public.promotion_run"):
        await conn.execute(f"DELETE FROM {table} WHERE graph = $1", graph)
    await conn.execute("SELECT ag_catalog.drop_graph($1, true)", graph)


@pytest.fixture(scope="module")
def settings() -> Settings:
    config = _settings(f"astra_gateinbox_{new_ulid()[10:22].lower()}")

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
def question_store(pool, settings: Settings) -> PostgresQuestionStore:
    return PostgresQuestionStore(pool, graph_name=settings.graph_name)


@pytest.fixture
def promotion_store(pool, settings: Settings) -> PostgresPromotionStore:
    return PostgresPromotionStore(pool, graph_name=settings.graph_name)


@pytest.fixture
def adoption_store(pool, settings: Settings) -> PostgresAdoptionStore:
    return PostgresAdoptionStore(pool, graph_name=settings.graph_name)


@pytest.fixture
def confirmation_store(pool, settings: Settings) -> PostgresDecommissionConfirmationStore:
    return PostgresDecommissionConfirmationStore(pool, graph_name=settings.graph_name)


@pytest.fixture
def regression_store(pool, settings: Settings) -> PostgresRegressionScheduleStore:
    return PostgresRegressionScheduleStore(pool, graph_name=settings.graph_name)


@pytest.fixture
def scope_store(pool, settings: Settings) -> PostgresScopeStore:
    return PostgresScopeStore(pool, graph_name=settings.graph_name)


@pytest.fixture
def gate_notification_store(pool, settings: Settings) -> PostgresGateNotificationStore:
    return PostgresGateNotificationStore(pool, graph_name=settings.graph_name)


async def _write_family(
    writer: GraphWriter, *, name: str, domain: str | None, state: str = "IN_REVIEW",
) -> str:
    family_id = new_ulid()
    await writer.write_nodes(
        [NodeWrite(type="ModelFamily", id=family_id, properties={
            "name": name, "state": state, "domain": domain, "owner": OWNER_VALUE,
            "grain": "", "conformed_dims": [],
        })],
        principal=PRINCIPAL,
    )
    return family_id


async def _write_report(writer: GraphWriter, *, workbook_id: str) -> str:
    return str((await writer.write_nodes(
        [NodeWrite(type="ReportDefinition", properties={"mu_ref": workbook_id, "model_ref": "model-placeholder", "pages": ["Overview"]})],
        principal=PRINCIPAL,
    ))[0]["properties"]["id"])


async def _write_passing_parity_run(writer: GraphWriter, *, workbook_id: str, sheet_id: str) -> None:
    case_id = str((await writer.write_nodes(
        [NodeWrite(type="ParityCase", properties={
            "mu_ref": workbook_id, "sheet_ref": sheet_id, "case_key": f"case_{new_ulid()}",
            "grain": ["Desk"], "measures": ["Margin"], "filter_ctx": {}, "param_values": {},
        })],
        principal=PRINCIPAL,
    ))[0]["properties"]["id"])
    verdict_id = str((await writer.write_nodes(
        [NodeWrite(type="Verdict", properties={
            "case_ref": case_id, "result": "PASS", "failing_cells": [], "evidence_ref": None, "sampled": False,
        })],
        principal=PRINCIPAL,
    ))[0]["properties"]["id"])
    await writer.write_nodes(
        [NodeWrite(type="ParityRun", properties={
            "suite_ref": workbook_id, "charter_version": "1",
            "started": "2027-06-01T09:00:00.000Z", "finished": "2027-06-01T09:00:02.000Z",
            "verdicts": [verdict_id],
        })],
        principal=PRINCIPAL,
    )


async def _write_gate_decision(writer: GraphWriter, *, gate: str, subject_ref: str, decision: str) -> None:
    await writer.write_nodes(
        [NodeWrite(type="GateDecision", properties={
            "gate": gate, "subject_ref": subject_ref, "decision": decision,
            "approver": OWNER_VALUE,
            "timestamp": datetime.now(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z"),
        })],
        principal=PRINCIPAL,
    )


async def _ready_site(writer: GraphWriter, pool: asyncpg.Pool, settings: Settings, adoption_store, confirmation_store) -> tuple[str, str]:
    """A site with one workbook meeting every real G4 readiness precondition -- released,
    parallel window elapsed, no regression schedule (honest green), adoption threshold
    met, owner confirmed. Mirrors `test_integration_g4_card.py`'s own `_ready_workbook`."""
    suffix = new_ulid()[10:18].lower()
    site_id = str((await writer.write_nodes(
        [NodeWrite(type="Site", properties={"luid": f"s-{suffix}", "name": f"RQA {suffix}"})], principal=PRINCIPAL,
    ))[0]["properties"]["id"])
    project_id = str((await writer.write_nodes(
        [NodeWrite(type="Project", properties={"luid": f"p-{suffix}", "name": "Risk Core"})], principal=PRINCIPAL,
    ))[0]["properties"]["id"])
    await writer.write_edge(EdgeWrite(type="CONTAINS", from_id=site_id, to_id=project_id, properties={}), principal=PRINCIPAL)
    workbook_id = str((await writer.write_nodes(
        [NodeWrite(type="Workbook", properties={"luid": f"wb-{suffix}", "name": "Ready WB", "revision": "1"})],
        principal=PRINCIPAL,
    ))[0]["properties"]["id"])
    await writer.write_edge(EdgeWrite(type="CONTAINS", from_id=project_id, to_id=workbook_id, properties={}), principal=PRINCIPAL)

    from datetime import timedelta

    finished_at = datetime.now(UTC) - timedelta(weeks=5)
    async with pool.acquire() as conn:
        await conn.execute(
            """INSERT INTO public.promotion_run
                (id, graph, workbook_id, to_stage, workspace, state, steps,
                 model_git_ref, report_deploy_id, approved_by, approver_role,
                 rationale, triggered_by, started_at, finished_at)
               VALUES ($1, $2, $3, 'prod', 'prod', 'SUCCEEDED', '[]'::jsonb,
                       'refs/heads/master', NULL, $4, 'programme_manager', NULL, $4, $5, $5)""",
            f"promotion_{new_ulid()}", settings.graph_name, workbook_id, OWNER_VALUE, finished_at,
        )
    await adoption_store.record(
        AdoptionSnapshot(
            id=f"adoption_{new_ulid()}", workbook_id=workbook_id, captured_at=datetime.now(UTC).isoformat(),
            source_views=100, target_views=90, ratio=0.9, threshold=0.8, meets_threshold=True,
            triggered_by=OWNER_VALUE,
        )
    )
    await confirmation_store.confirm(workbook_id, confirmed_by=OWNER_VALUE)
    return site_id, workbook_id


# ------------------------------------------------------------------------------ G2


async def test_pending_g2_items_lists_a_real_family_in_review(writer, pool, settings, question_store) -> None:
    family_id = await _write_family(writer, name="Risk Positions", domain="risk")

    items = await pending_g2_items(pool, settings.graph_name, question_store, domain_scope=None)

    matching = [i for i in items if i["subject_ref"] == family_id]
    assert len(matching) == 1
    assert matching[0]["gate"] == "G2"
    assert matching[0]["domain"] == "risk"
    assert matching[0]["countersigner_role"] == "semantic_model_engineer"


async def test_pending_g2_items_is_filtered_by_domain_scope(writer, pool, settings, question_store) -> None:
    scoped = await _write_family(writer, name="Risk Positions", domain="risk")
    unscoped = await _write_family(writer, name="Undomained Family", domain=None)
    other = await _write_family(writer, name="Treasury Book", domain="treasury")

    items = await pending_g2_items(pool, settings.graph_name, question_store, domain_scope=frozenset({"risk"}))
    subjects = {i["subject_ref"] for i in items}

    assert scoped in subjects
    assert unscoped in subjects  # an unset domain is open to any data owner (ADR 0030)
    assert other not in subjects


# ------------------------------------------------------------------------------ G3


async def test_pending_g3_cards_lists_a_real_composed_report_that_passes_the_charter(writer, pool, settings) -> None:
    seeded = await seed_estate(writer, suffix=f"-{new_ulid()}")
    await _write_report(writer, workbook_id=seeded["workbook"])
    await _write_passing_parity_run(writer, workbook_id=seeded["workbook"], sheet_id=seeded["worksheet"])

    items = await pending_g3_cards(pool, settings.graph_name)

    matching = [i for i in items if i["subject_ref"] == seeded["workbook"]]
    assert len(matching) == 1
    assert matching[0]["gate"] == "G3"
    assert matching[0]["site"] == "RQA"
    assert matching[0]["countersigner_role"] == "migration_engineer"


async def test_pending_g3_cards_excludes_an_already_approved_report(writer, pool, settings) -> None:
    seeded = await seed_estate(writer, suffix=f"-{new_ulid()}")
    await _write_report(writer, workbook_id=seeded["workbook"])
    await _write_passing_parity_run(writer, workbook_id=seeded["workbook"], sheet_id=seeded["worksheet"])
    await _write_gate_decision(writer, gate="G3", subject_ref=seeded["workbook"], decision="APPROVED")

    items = await pending_g3_cards(pool, settings.graph_name)

    assert seeded["workbook"] not in {i["subject_ref"] for i in items}


async def test_pending_g3_cards_still_includes_a_report_sent_back_for_changes(writer, pool, settings) -> None:
    seeded = await seed_estate(writer, suffix=f"-{new_ulid()}")
    await _write_report(writer, workbook_id=seeded["workbook"])
    await _write_passing_parity_run(writer, workbook_id=seeded["workbook"], sheet_id=seeded["worksheet"])
    await _write_gate_decision(writer, gate="G3", subject_ref=seeded["workbook"], decision="CHANGES_REQUESTED")

    items = await pending_g3_cards(pool, settings.graph_name)

    matching = [i for i in items if i["subject_ref"] == seeded["workbook"]]
    assert len(matching) == 1
    assert matching[0]["detail"]["resubmitted"] is True


# ------------------------------------------------------------------------------ G4


async def test_pending_g4_sites_lists_a_real_fully_ready_site(writer, pool, settings, adoption_store, confirmation_store) -> None:
    site_id, _workbook_id = await _ready_site(writer, pool, settings, adoption_store, confirmation_store)
    empty_promotion_store = PostgresPromotionStore(pool, graph_name=settings.graph_name)
    regression_store = PostgresRegressionScheduleStore(pool, graph_name=settings.graph_name)
    scope_store = PostgresScopeStore(pool, graph_name=settings.graph_name)

    items = await pending_g4_sites(
        pool, settings.graph_name, empty_promotion_store, adoption_store, confirmation_store,
        regression_store, scope_store,
    )

    matching = [i for i in items if i["subject_ref"] == site_id]
    assert len(matching) == 1
    assert matching[0]["gate"] == "G4"
    assert matching[0]["can_defer"] is True
    assert matching[0]["can_ask_question"] is False


async def test_pending_g4_sites_excludes_an_approved_site_but_keeps_a_deferred_one(
    writer, pool, settings, adoption_store, confirmation_store,
) -> None:
    promotion_store = PostgresPromotionStore(pool, graph_name=settings.graph_name)
    regression_store = PostgresRegressionScheduleStore(pool, graph_name=settings.graph_name)
    scope_store = PostgresScopeStore(pool, graph_name=settings.graph_name)

    approved_site, _wb1 = await _ready_site(writer, pool, settings, adoption_store, confirmation_store)
    await _write_gate_decision(writer, gate="G4", subject_ref=approved_site, decision="APPROVED")

    deferred_site, _wb2 = await _ready_site(writer, pool, settings, adoption_store, confirmation_store)
    await _write_gate_decision(writer, gate="G4", subject_ref=deferred_site, decision="DEFERRED")

    items = await pending_g4_sites(
        pool, settings.graph_name, promotion_store, adoption_store, confirmation_store,
        regression_store, scope_store,
    )
    subjects = {i["subject_ref"] for i in items}

    assert approved_site not in subjects
    assert deferred_site in subjects


# ------------------------------------------------------------------------- role dispatch


async def test_gate_inbox_is_role_dispatched(
    writer, pool, settings, question_store, promotion_store, adoption_store, confirmation_store,
    regression_store, scope_store,
) -> None:
    family_id = await _write_family(writer, name="Risk Positions", domain=None)
    seeded = await seed_estate(writer, suffix=f"-{new_ulid()}")
    await _write_report(writer, workbook_id=seeded["workbook"])
    await _write_passing_parity_run(writer, workbook_id=seeded["workbook"], sheet_id=seeded["worksheet"])

    async def _inbox(role: str) -> dict[str, Any]:
        return await gate_inbox(
            pool, settings.graph_name, roles=parse_roles(role), domain_scope=frozenset(),
            question_store=question_store, promotion_store=promotion_store, adoption_store=adoption_store,
            confirmation_store=confirmation_store, regression_store=regression_store, scope_store=scope_store,
        )

    data_owner_inbox = await _inbox("client_data_owner")
    assert {i["gate"] for i in data_owner_inbox["items"]} <= {"G2"}
    assert family_id in {i["subject_ref"] for i in data_owner_inbox["items"]}

    report_owner_inbox = await _inbox("client_report_owner")
    assert {i["gate"] for i in report_owner_inbox["items"]} <= {"G3"}
    assert seeded["workbook"] in {i["subject_ref"] for i in report_owner_inbox["items"]}

    artizent_inbox = await _inbox("migration_engineer")
    gates_seen = {i["gate"] for i in artizent_inbox["items"]}
    assert "G2" in gates_seen
    assert "G3" in gates_seen


# --------------------------------------------------------------------------- notifications


async def test_notify_new_requests_is_idempotent(writer, pool, settings, gate_notification_store) -> None:
    family_id = await _write_family(writer, name="Risk Positions", domain=None)
    channel = LocalGateNotificationChannel()
    items = [{
        "gate": "G2", "subject_ref": family_id, "name": "Risk Positions",
        "approver_role": "client_data_owner", "countersigner_role": "semantic_model_engineer",
    }]

    first = await notify_new_requests(gate_notification_store, channel, items)
    second = await notify_new_requests(gate_notification_store, channel, items)

    assert len(first) == 1
    assert first[0].gate == "G2"
    assert first[0].subject_ref == family_id
    assert second == []


# ---------------------------------------------------------------------------- the API


@pytest.fixture
async def http_client(
    pool, settings, question_store, promotion_store, adoption_store, confirmation_store,
    regression_store, scope_store, gate_notification_store,
):
    from httpx import ASGITransport, AsyncClient

    from astra_graph.g2_reminders import LocalNotificationChannel, PostgresReminderStore
    from astra_graph.main import create_app

    app = create_app()
    app.state.pool = pool
    app.state.question_store = question_store
    app.state.promotion_store = promotion_store
    app.state.adoption_store = adoption_store
    app.state.decommission_confirmation_store = confirmation_store
    app.state.regression_schedule_store = regression_store
    app.state.scope_store = scope_store
    app.state.gate_notification_store = gate_notification_store
    app.state.reminder_store = PostgresReminderStore(pool, graph_name=settings.graph_name)
    app.state.notification_channel = LocalNotificationChannel()

    class _Repository:
        graph_name = settings.graph_name

    app.state.repository = _Repository()

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://graph-svc") as async_client:
        yield async_client


def _headers(role: str) -> dict[str, str]:
    return {PRINCIPAL_HEADER: "user:someone@example.com", ROLES_HEADER: role}


async def test_gate_inbox_over_http_returns_real_items(writer, http_client) -> None:
    await _write_family(writer, name="Risk Positions", domain=None)

    response = await http_client.get("/v1/gate-inbox", headers=_headers("client_data_owner"))

    assert response.status_code == 200
    assert response.json()["count"] >= 1


async def test_gate_inbox_over_http_refuses_an_unrelated_role(http_client) -> None:
    response = await http_client.get("/v1/gate-inbox", headers=_headers("client_infosec_reviewer"))

    assert response.status_code == 403


async def test_notify_over_http_sends_new_requests_and_sla_reminders(writer, http_client) -> None:
    await _write_family(writer, name="Notify Family", domain=None)

    response = await http_client.post("/v1/gate-inbox:notify", headers=_headers("migration_engineer"))

    assert response.status_code == 200
    body = response.json()
    assert "new_requests_sent" in body
    assert "sla_reminders_sent" in body
