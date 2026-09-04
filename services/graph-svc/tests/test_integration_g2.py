"""The G2 workflow, against real PostgreSQL + Apache AGE — story S4.2.1.

What only the real store can answer: that questions seeded from a frozen design actually
land as tracked rows, that approval is genuinely blocked while one is open and genuinely
unblocked once answered, that a `GateDecision` node lands in the graph with both the
approver and the countersigner recorded, that domain scope is enforced against a real
`ModelFamily.domain`, and that the G2 cycle count increments across a real request-changes
round trip — none of which the pure `check_domain_scope`/`plain_language_summary` unit
tests in ``test_g2.py`` can see.
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
from astra_graph.errors import (  # noqa: E402
    ElementNotFoundError,
    ForbiddenError,
    InvalidRequestError,
)
from astra_graph.events import source_for  # noqa: E402
from astra_graph.g2 import (  # noqa: E402
    PostgresQuestionStore,
    QuestionState,
    answer_question,
    approve,
    ask_question,
    client_proposal_view,
    list_questions,
    reply_to_question,
    request_changes,
    seed_questions,
)
from astra_graph.graph import AgeGraphRepository, create_pool  # noqa: E402
from astra_graph.graph.queries import accessor  # noqa: E402
from astra_graph.ids import new_ulid  # noqa: E402
from astra_graph.migrations import run as run_migrations  # noqa: E402
from astra_graph.model_lifecycle import accept_family, submit_for_review  # noqa: E402
from astra_graph.modeller import Modeller, read_design_document  # noqa: E402
from astra_graph.ontology import EDGE_LABELS, NODE_LABELS  # noqa: E402
from astra_graph.principal import Principal  # noqa: E402
from astra_graph.provenance import PostgresProvenanceStore  # noqa: E402
from astra_graph.writes import EdgeWrite, GraphWriter, NodeWrite  # noqa: E402

PRINCIPAL = Principal("agent:modeller", run_id="run-modeller")
ENGINEER = Principal("user:sme@artizent.example")
OWNER = Principal("user:owner@client.example")


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
    config = _settings(f"astra_g2_{new_ulid()[10:22].lower()}")

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
    """One ModelFamily generated, accepted into DRAFT and submitted for review — an
    open question already seeded from the design's own custom-SQL table (ADR 0028's
    "ambiguous key" open question, the simplest one to seed deterministically)."""
    pool = await create_pool(settings)
    try:
        repository = AgeGraphRepository(pool, graph_name=settings.graph_name)
        writer = GraphWriter(repository, event_source=source_for(settings.graph_name))
        provenance_store = PostgresProvenanceStore(pool, graph_name=settings.graph_name)
        question_store = PostgresQuestionStore(pool, graph_name=settings.graph_name)
        modeller = Modeller(
            pool, graph_name=settings.graph_name, writer=writer, provenance_store=provenance_store
        )
        suffix = new_ulid()[10:18].lower()

        site = await _write(writer, "Site", luid=f"s-{suffix}", name=f"RQA {suffix}")
        project = await _write(writer, "Project", luid=f"p-{suffix}", name="Risk Core")
        await _edge(writer, "CONTAINS", site, project)

        # custom_sql=... makes the Modeller raise an "ambiguous_key" open question — a
        # real one, seeded from the graph, not fabricated for this test.
        table = await _write(
            writer, "Table", name="positions", schema="risk", row_estimate=1000,
            custom_sql="SELECT * FROM raw.positions",
        )
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
        submitted = await submit_for_review(pool, settings.graph_name, writer, family, principal=ENGINEER)
        document = await read_design_document(pool, settings.graph_name, family)
        seeded = await seed_questions(question_store, family, document, principal=PRINCIPAL)

        yield {
            "pool": pool,
            "settings": settings,
            "writer": writer,
            "modeller": modeller,
            "question_store": question_store,
            "family": family,
            "version": submitted["version"],
            "seeded_question_id": seeded[0].id if seeded else None,
        }
    finally:
        await pool.close()


# ---------------------------------------------------------------------------- questions


async def test_the_designs_own_open_question_was_seeded(estate) -> None:
    assert estate["seeded_question_id"] is not None
    questions = await list_questions(estate["question_store"], estate["family"])
    assert any(q.category == "ambiguous_key" for q in questions)
    assert all(q.state is QuestionState.OPEN for q in questions)


async def test_a_data_owner_can_ask_a_new_question(estate) -> None:
    question = await ask_question(
        estate["question_store"], estate["family"], category="general",
        question="Why is this table extracted daily rather than hourly?", principal=OWNER,
    )
    assert question.state is QuestionState.OPEN
    assert question.asked_by == OWNER.value


async def test_a_reply_is_visible_in_the_thread(estate) -> None:
    question = await ask_question(
        estate["question_store"], estate["family"], category="general", question="What refresh cadence is this?",
        principal=OWNER,
    )
    updated = await reply_to_question(
        estate["question_store"], question.id, message="Daily, matching the source extract.", principal=ENGINEER,
    )
    assert len(updated.thread) == 1
    assert updated.thread[0]["from"] == ENGINEER.value
    assert updated.thread[0]["text"] == "Daily, matching the source extract."


async def test_answering_a_question_closes_it(estate) -> None:
    question = await ask_question(
        estate["question_store"], estate["family"], category="general", question="Confirm the grain please.",
        principal=OWNER,
    )
    answered = await answer_question(estate["question_store"], question.id, principal=OWNER)
    assert answered.state is QuestionState.ANSWERED
    assert answered.answered_by == OWNER.value


async def test_answering_an_already_answered_question_is_refused(estate) -> None:
    question = await ask_question(
        estate["question_store"], estate["family"], category="general", question="One more thing to confirm.",
        principal=OWNER,
    )
    await answer_question(estate["question_store"], question.id, principal=OWNER)
    with pytest.raises(InvalidRequestError, match="already"):
        await answer_question(estate["question_store"], question.id, principal=OWNER)


async def test_replying_to_an_unknown_question_is_a_clean_404(estate) -> None:
    with pytest.raises(ElementNotFoundError):
        await reply_to_question(estate["question_store"], "not-a-real-question", message="hello", principal=OWNER)


# ------------------------------------------------------------------------------ approval


async def test_approval_is_refused_while_a_question_is_open(estate) -> None:
    with pytest.raises(InvalidRequestError, match="open question"):
        await approve(
            estate["pool"], estate["settings"].graph_name, estate["writer"], estate["question_store"],
            estate["family"], principal=OWNER, domain_scope=frozenset(), countersigned_by=ENGINEER.value,
            rationale="Looks good, approving.",
        )


async def test_approval_succeeds_once_every_question_is_answered(estate) -> None:
    for question in await list_questions(estate["question_store"], estate["family"]):
        if question.state is QuestionState.OPEN:
            await answer_question(estate["question_store"], question.id, principal=OWNER)

    result = await approve(
        estate["pool"], estate["settings"].graph_name, estate["writer"], estate["question_store"],
        estate["family"], principal=OWNER, domain_scope=frozenset(), countersigned_by=ENGINEER.value,
        rationale="Reviewed and approved for the Risk domain.",
    )
    assert result["state"] == "APPROVED"
    assert result["version"] == estate["version"]

    repository = AgeGraphRepository(estate["pool"], graph_name=estate["settings"].graph_name)
    decision = await repository.get_node_record(result["gate_decision_id"])
    assert decision is not None
    assert decision.properties["gate"] == "G2"
    assert decision.properties["decision"] == "APPROVED"
    assert decision.properties["approver"] == OWNER.value
    assert decision.properties["countersigner"] == ENGINEER.value
    assert decision.properties["countersigner_role"] == "semantic_model_engineer"
    assert decision.properties["version_hash"] == estate["version"]


async def test_a_family_with_an_assigned_domain_requires_it_in_scope(estate) -> None:
    from astra_graph.model_lifecycle import update_domain

    # Cycle back to DRAFT to assign a domain (only legal while DRAFT), then resubmit.
    for question in await list_questions(estate["question_store"], estate["family"]):
        if question.state is QuestionState.OPEN:
            await answer_question(estate["question_store"], question.id, principal=OWNER)
    await request_changes(
        estate["pool"], estate["settings"].graph_name, estate["writer"], estate["family"],
        principal=OWNER, domain_scope=frozenset(), comment="Please assign a domain first.",
    )
    await update_domain(
        estate["pool"], estate["settings"].graph_name, estate["writer"], estate["family"],
        domain="Treasury", principal=ENGINEER,
    )
    resubmitted = await submit_for_review(
        estate["pool"], estate["settings"].graph_name, estate["writer"], estate["family"], principal=ENGINEER,
    )
    assert resubmitted["state"] == "IN_REVIEW"

    with pytest.raises(ForbiddenError, match="domain"):
        await approve(
            estate["pool"], estate["settings"].graph_name, estate["writer"], estate["question_store"],
            estate["family"], principal=OWNER, domain_scope=frozenset({"risk"}),
            countersigned_by=ENGINEER.value, rationale="Should be refused before this matters.",
        )


# -------------------------------------------------------------------------- request-changes


async def test_request_changes_returns_the_family_to_draft_and_stores_the_cycle_count(estate) -> None:
    result = await request_changes(
        estate["pool"], estate["settings"].graph_name, estate["writer"], estate["family"],
        principal=OWNER, domain_scope=frozenset(), comment="Please add a description to each measure.",
    )
    assert result["state"] == "DRAFT"
    assert result["g2_cycle_count"] == 1


async def test_a_second_cycle_increments_the_count_again(estate) -> None:
    await request_changes(
        estate["pool"], estate["settings"].graph_name, estate["writer"], estate["family"],
        principal=OWNER, domain_scope=frozenset(), comment="First round of changes requested.",
    )
    await submit_for_review(estate["pool"], estate["settings"].graph_name, estate["writer"], estate["family"], principal=ENGINEER)
    second = await request_changes(
        estate["pool"], estate["settings"].graph_name, estate["writer"], estate["family"],
        principal=OWNER, domain_scope=frozenset(), comment="Second round of changes requested.",
    )
    assert second["g2_cycle_count"] == 2


# --------------------------------------------------------------------------- client view


async def test_client_proposal_view_renders_the_plain_language_summary(estate) -> None:
    view = await client_proposal_view(estate["pool"], estate["settings"].graph_name, estate["question_store"], estate["family"])
    assert view["version"] == estate["version"]
    assert "Daily VaR" in view["reports"]
    assert "table" in view["plain_summary"]
    assert view["unanswered_count"] == len(
        [q for q in await list_questions(estate["question_store"], estate["family"]) if q.state is QuestionState.OPEN]
    )


# ---------------------------------------------------------------------------------- the API


@pytest.fixture
async def http_client(estate):
    from httpx import ASGITransport, AsyncClient

    from astra_graph.main import create_app

    app = create_app()
    app.state.modeller = estate["modeller"]
    app.state.question_store = estate["question_store"]

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://graph-svc") as async_client:
        yield async_client


def _headers(role: str = "client_data_owner", principal: Principal = OWNER) -> dict[str, str]:
    from astra_graph.principal import PRINCIPAL_HEADER
    from astra_graph.roles import ROLES_HEADER

    return {PRINCIPAL_HEADER: principal.value, ROLES_HEADER: role}


async def test_get_proposal_over_http(estate, http_client) -> None:
    response = await http_client.get(f"/v1/families/{estate['family']}/proposal", headers=_headers())
    assert response.status_code == 200
    assert response.json()["version"] == estate["version"]


async def test_ask_over_http_requires_the_data_owner_role(estate, http_client) -> None:
    response = await http_client.post(
        f"/v1/families/{estate['family']}/questions:ask",
        json={"question": "A question that needs the right role to ask."},
        headers=_headers(role="semantic_model_engineer", principal=ENGINEER),
    )
    assert response.status_code == 403


async def test_the_full_review_cycle_over_http(estate, http_client) -> None:
    family_id = estate["family"]

    for question in await list_questions(estate["question_store"], family_id):
        if question.state is QuestionState.OPEN:
            answer = await http_client.post(f"/v1/questions/{question.id}:answer", headers=_headers())
            assert answer.status_code == 200

    approved = await http_client.post(
        f"/v1/families/{family_id}:approve-g2",
        json={"countersigned_by": ENGINEER.value, "rationale": "Approved over HTTP, everything checks out."},
        headers=_headers(),
    )
    assert approved.status_code == 200
    assert approved.json()["state"] == "APPROVED"
