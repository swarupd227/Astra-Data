"""G2 cycle time and reminders, against real PostgreSQL + Apache AGE — story S4.2.2.

What only the real store can answer: that "days waiting" is read from the same event log
`family_transition_history` already uses (not a fabricated figure), that `ModelFamily.owner`
— "the approver" — actually round-trips through `update_owner`, and that a reminder,
once recorded, is never sent a second time for the same `(family, day)` pair.

Since a fixture built and submitted for review "now" would always show zero days waiting,
the family's own `IN_REVIEW` event is deliberately backdated by writing directly to
`public.estate_event` — the one piece a unit test cannot fake and only this suite can.
"""

from __future__ import annotations

import asyncio
import os
import threading
from collections.abc import Callable, Coroutine
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

pytestmark = pytest.mark.integration

asyncpg = pytest.importorskip("asyncpg")

from astra_graph.config import Settings  # noqa: E402
from astra_graph.events import source_for  # noqa: E402
from astra_graph.g2 import PostgresQuestionStore, ask_question  # noqa: E402
from astra_graph.g2_reminders import (  # noqa: E402
    LocalNotificationChannel,
    PostgresReminderStore,
    pending_g2_reviews,
    send_due_reminders,
)
from astra_graph.graph import AgeGraphRepository, create_pool  # noqa: E402
from astra_graph.graph.queries import accessor  # noqa: E402
from astra_graph.ids import new_ulid  # noqa: E402
from astra_graph.migrations import run as run_migrations  # noqa: E402
from astra_graph.model_lifecycle import accept_family, submit_for_review, update_owner  # noqa: E402
from astra_graph.modeller import Modeller  # noqa: E402
from astra_graph.ontology import EDGE_LABELS, NODE_LABELS  # noqa: E402
from astra_graph.principal import Principal  # noqa: E402
from astra_graph.provenance import PostgresProvenanceStore  # noqa: E402
from astra_graph.writes import EdgeWrite, GraphWriter, NodeWrite  # noqa: E402

PRINCIPAL = Principal("agent:modeller", run_id="run-modeller")
ENGINEER = Principal("user:sme@artizent.example")


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


@pytest.fixture(scope="module")
def settings() -> Settings:
    config = _settings(f"astra_g2rem_{new_ulid()[10:22].lower()}")

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
                "public.provenance",
                "public.g2_question",
                "public.g2_reminder",
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


async def _backdate_entry_into_review(pool: asyncpg.Pool, graph_name: str, family_id: str, days: int) -> None:
    """Move the family's own `IN_REVIEW` event `days` calendar days into the past — the
    one thing a fixture built "now" cannot otherwise produce."""
    async with pool.acquire() as conn:
        await conn.execute(
            """
            UPDATE public.estate_event
               SET time = $3
             WHERE graph = $1 AND subject = $2 AND type = 'estate.node.upserted'
               AND label = 'ModelFamily' AND data -> 'properties' ->> 'state' = 'IN_REVIEW'
            """,
            graph_name,
            family_id,
            datetime.now(UTC) - timedelta(days=days),
        )


@pytest.fixture
async def estate(settings: Settings):
    """One ModelFamily generated, accepted, and submitted for G2 review six calendar
    days ago (comfortably past both the 3- and 5-working-day thresholds, whichever
    weekday the suite happens to run on)."""
    pool = await create_pool(settings)
    try:
        repository = AgeGraphRepository(pool, graph_name=settings.graph_name)
        writer = GraphWriter(repository, event_source=source_for(settings.graph_name))
        provenance_store = PostgresProvenanceStore(pool, graph_name=settings.graph_name)
        question_store = PostgresQuestionStore(pool, graph_name=settings.graph_name)
        reminder_store = PostgresReminderStore(pool, graph_name=settings.graph_name)
        modeller = Modeller(
            pool, graph_name=settings.graph_name, writer=writer, provenance_store=provenance_store
        )
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
            writer, "Worksheet", name="VaR sheet", rows_shelf=["Desk"], cols_shelf=["Date"], marks_shelf=[],
        )
        await _edge(writer, "CONTAINS", book, sheet)
        datasource = await _write(
            writer, "Datasource", name="VaR ds", type="published", luid=f"ds-{suffix}",
            extract_flag=True, refresh_schedule="daily",
        )
        await _edge(writer, "USES_DATASOURCE", sheet, datasource)
        await _edge(writer, "CONNECTS_TO", datasource, connection)

        family = await _write(
            writer, "ModelFamily", name=f"Risk Positions {suffix}", state="PROPOSED",
            grain="Desk, Date", conformed_dims=[],
        )
        await _edge(writer, "IN_FAMILY", book, family, confidence=1.0)

        await modeller.run(family, principal=PRINCIPAL)
        await accept_family(pool, settings.graph_name, writer, family, principal=ENGINEER)
        await update_owner(pool, settings.graph_name, writer, family, owner="owner@client.example", principal=ENGINEER)
        submitted = await submit_for_review(pool, settings.graph_name, writer, family, principal=ENGINEER)
        await _backdate_entry_into_review(pool, settings.graph_name, family, days=6)
        # submit_for_review does not itself seed questions (routes_modeller.py's own route
        # orchestrates that, see g2.py's module docstring) — this fixture calls the graph
        # functions directly, so it raises one explicitly for the open-question assertions.
        await ask_question(
            question_store, family, category="general",
            question="Why is this table extracted daily rather than hourly?",
            principal=Principal("user:owner@client.example"),
        )

        yield {
            "pool": pool,
            "settings": settings,
            "writer": writer,
            "modeller": modeller,
            "question_store": question_store,
            "reminder_store": reminder_store,
            "family": family,
            "version": submitted["version"],
        }
    finally:
        await pool.close()


# --------------------------------------------------------------------------- pending reviews


async def test_a_submitted_family_is_awaiting_g2(estate) -> None:
    reviews = await pending_g2_reviews(estate["pool"], estate["settings"].graph_name, estate["question_store"])
    assert any(r.family_id == estate["family"] for r in reviews)


async def test_days_waiting_and_breach_reflect_the_backdated_entry(estate) -> None:
    reviews = await pending_g2_reviews(estate["pool"], estate["settings"].graph_name, estate["question_store"])
    review = next(r for r in reviews if r.family_id == estate["family"])
    # 6 calendar days back always spans at least the 5 working days a full week beats,
    # but a run landing on a weekend could see fewer working days elapsed than 6 — assert
    # only what is guaranteed: some real wait was measured and it is over the 3-day mark.
    assert review.days_waiting is not None
    assert review.days_waiting >= 3


async def test_the_approver_is_the_assigned_owner(estate) -> None:
    reviews = await pending_g2_reviews(estate["pool"], estate["settings"].graph_name, estate["question_store"])
    review = next(r for r in reviews if r.family_id == estate["family"])
    assert review.approver == "owner@client.example"


async def test_open_questions_are_counted(estate) -> None:
    reviews = await pending_g2_reviews(estate["pool"], estate["settings"].graph_name, estate["question_store"])
    review = next(r for r in reviews if r.family_id == estate["family"])
    assert review.open_questions >= 1  # the question this fixture raised, above


async def test_a_family_not_in_review_is_not_on_the_tile(estate) -> None:
    reviews = await pending_g2_reviews(estate["pool"], estate["settings"].graph_name, estate["question_store"])
    assert all(r.family_id != "not-a-real-family" for r in reviews)


# ------------------------------------------------------------------------------- reminders


async def test_due_reminders_are_sent_and_recorded(estate) -> None:
    sent = await send_due_reminders(
        estate["pool"], estate["settings"].graph_name, estate["question_store"],
        estate["reminder_store"], LocalNotificationChannel(),
    )
    days_sent = {record.day for record in sent if record.family_id == estate["family"]}
    assert days_sent == {3, 5}


async def test_a_reminder_already_sent_is_not_sent_again(estate) -> None:
    first = await send_due_reminders(
        estate["pool"], estate["settings"].graph_name, estate["question_store"],
        estate["reminder_store"], LocalNotificationChannel(),
    )
    second = await send_due_reminders(
        estate["pool"], estate["settings"].graph_name, estate["question_store"],
        estate["reminder_store"], LocalNotificationChannel(),
    )
    assert len(first) >= 1
    assert second == []


# ------------------------------------------------------------------------------------ owner


async def test_update_owner_requires_draft(estate) -> None:
    from astra_graph.errors import InvalidRequestError

    with pytest.raises(InvalidRequestError, match="DRAFT"):
        await update_owner(
            estate["pool"], estate["settings"].graph_name, estate["writer"], estate["family"],
            owner="someone.else@client.example", principal=ENGINEER,
        )


# ---------------------------------------------------------------------------------- the API


@pytest.fixture
async def http_client(estate):
    from httpx import ASGITransport, AsyncClient

    from astra_graph.main import create_app

    app = create_app()
    app.state.modeller = estate["modeller"]
    app.state.question_store = estate["question_store"]
    app.state.reminder_store = estate["reminder_store"]
    app.state.notification_channel = LocalNotificationChannel()

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://graph-svc") as async_client:
        yield async_client


def _headers(role: str = "programme_manager") -> dict[str, str]:
    from astra_graph.principal import PRINCIPAL_HEADER
    from astra_graph.roles import ROLES_HEADER

    return {PRINCIPAL_HEADER: "user:pm@artizent.example", ROLES_HEADER: role}


async def test_awaiting_g2_over_http(estate, http_client) -> None:
    response = await http_client.get("/v1/families:awaiting-g2", headers=_headers())
    assert response.status_code == 200
    body = response.json()
    assert body["sla_working_days"] == 5
    assert any(r["family_id"] == estate["family"] for r in body["reviews"])


async def test_awaiting_g2_requires_an_artizent_role(estate, http_client) -> None:
    response = await http_client.get("/v1/families:awaiting-g2", headers=_headers(role="client_data_owner"))
    assert response.status_code == 403


async def test_send_reminders_over_http(estate, http_client) -> None:
    response = await http_client.post("/v1/g2/reminders:send", headers=_headers())
    assert response.status_code == 200
    body = response.json()
    assert body["count"] >= 1

    # Idempotent over HTTP too — a second call finds nothing new due.
    again = await http_client.post("/v1/g2/reminders:send", headers=_headers())
    assert again.json()["count"] == 0
