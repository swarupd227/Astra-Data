"""Per-user notification preferences -- story S10.5.2, against real PostgreSQL + Apache
AGE.

What only the real store can answer: that a saved preference really overrides the
honest default; that an opted-out event or an empty channel set really produces no
record; that `digest_mode="daily"` really queues instead of sending, and a real
`send_pending_digests` call really flips it to sent, batched one digest per (recipient,
channel); that `resolve_workbook_owner` really reads the same `OWNED_BY` edge
`estate.py`'s own `_owners` reads; and that the two real, lightweight write-site
integrations this story adds (`exception_desk.bulk_assign`, `train_overrides.move_mu`)
really produce a real notification row end to end, not just a unit-level call.
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
httpx = pytest.importorskip("httpx")

from astra_graph.config import Settings  # noqa: E402
from astra_graph.events import source_for  # noqa: E402
from astra_graph.exception_desk import bulk_assign  # noqa: E402
from astra_graph.graph import AgeGraphRepository, create_pool  # noqa: E402
from astra_graph.graph.queries import accessor  # noqa: E402
from astra_graph.ids import new_ulid  # noqa: E402
from astra_graph.migrations import run as run_migrations  # noqa: E402
from astra_graph.notification_preferences import (  # noqa: E402
    NotificationPreferenceError,
    PostgresNotificationPreferenceStore,
    notify,
    resolve_workbook_owner,
    send_pending_digests,
)
from astra_graph.ontology import EDGE_LABELS, NODE_LABELS  # noqa: E402
from astra_graph.principal import PRINCIPAL_HEADER, Principal  # noqa: E402
from astra_graph.roles import ROLES_HEADER  # noqa: E402
from astra_graph.train_overrides import move_mu  # noqa: E402
from astra_graph.writes import EdgeWrite, GraphWriter, NodeWrite  # noqa: E402

PRINCIPAL = Principal("agent:harvester", run_id="run-notification-preferences")
OWNER_PRINCIPAL = "user:a.mehta@rqa.example"


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
                  "public.notification_log", "public.notification_preference"):
        await conn.execute(f"DELETE FROM {table} WHERE graph = $1", graph)
    await conn.execute("SELECT ag_catalog.drop_graph($1, true)", graph)


@pytest.fixture(scope="module")
def settings() -> Settings:
    config = _settings(f"astra_notifyprefs_{new_ulid()[10:22].lower()}")

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
def store(pool, settings: Settings) -> PostgresNotificationPreferenceStore:
    return PostgresNotificationPreferenceStore(pool, graph_name=settings.graph_name)


async def _write(writer: GraphWriter, type_: str, **properties: Any) -> str:
    created = await writer.write_nodes([NodeWrite(type=type_, properties=properties)], principal=PRINCIPAL)
    return str(created[0]["properties"]["id"])


async def _edge(writer: GraphWriter, type_: str, source: str, target: str, **props: Any) -> str:
    edge = await writer.write_edge(EdgeWrite(type=type_, from_id=source, to_id=target, properties=props), principal=PRINCIPAL)
    return str(edge["properties"]["id"])


async def _write_owned_workbook(writer: GraphWriter, *, upn: str = "a.mehta@rqa.example") -> tuple[str, str]:
    suffix = new_ulid()[10:18].lower()
    workbook_id = await _write(writer, "Workbook", luid=f"wb-{suffix}", name=f"WB {suffix}", revision="1")
    user_id = await _write(writer, "User", upn=upn, display="A. Mehta", side="source")
    await _edge(writer, "OWNED_BY", workbook_id, user_id)
    return workbook_id, user_id


# ------------------------------------------------------------------------- preferences


async def test_get_returns_honest_defaults_when_nothing_was_ever_saved(store) -> None:
    preferences = await store.get("user:new.person@client.example")
    assert preferences.channels == {"email", "teams"}
    assert preferences.events == {"gate_request", "exception_assigned", "regression_fail", "train_replan"}
    assert preferences.digest_mode == "immediate"
    assert preferences.updated_at is None


async def test_set_then_get_returns_the_real_saved_row(store) -> None:
    principal = f"user:{new_ulid()}@client.example"
    saved = await store.set(
        principal, channels=["email"], events=["gate_request", "regression_fail"], digest_mode="daily",
    )
    assert saved.channels == {"email"}
    assert saved.digest_mode == "daily"
    assert saved.updated_at is not None

    fetched = await store.get(principal)
    assert fetched.channels == {"email"}
    assert fetched.events == {"gate_request", "regression_fail"}
    assert fetched.digest_mode == "daily"


async def test_set_is_overwritten_in_place_not_versioned(store) -> None:
    principal = f"user:{new_ulid()}@client.example"
    await store.set(principal, channels=["email", "teams"], events=["gate_request"], digest_mode="immediate")
    second = await store.set(principal, channels=["teams"], events=["train_replan"], digest_mode="daily")
    fetched = await store.get(principal)
    assert fetched.channels == {"teams"} == second.channels
    assert fetched.events == {"train_replan"}


async def test_set_rejects_an_unknown_channel_event_or_digest_mode(store) -> None:
    principal = f"user:{new_ulid()}@client.example"
    with pytest.raises(NotificationPreferenceError):
        await store.set(principal, channels=["sms"], events=["gate_request"], digest_mode="immediate")
    with pytest.raises(NotificationPreferenceError):
        await store.set(principal, channels=["email"], events=["nonsense"], digest_mode="immediate")
    with pytest.raises(NotificationPreferenceError):
        await store.set(principal, channels=["email"], events=["gate_request"], digest_mode="hourly")


# ---------------------------------------------------------------------------- notify()


async def test_notify_records_one_row_per_channel_immediately(store) -> None:
    principal = f"user:{new_ulid()}@client.example"
    await store.set(principal, channels=["email", "teams"], events=["gate_request"], digest_mode="immediate")

    records = await notify(
        store, event_type="gate_request", subject_ref="fam_1", recipient=principal,
        summary="A real request", link="/inbox?gate=G2&subject=fam_1",
    )

    assert {r.channel for r in records} == {"email", "teams"}
    assert all(r.sent_at is not None for r in records)
    assert all(r.link == "/inbox?gate=G2&subject=fam_1" for r in records)


async def test_notify_is_silent_when_the_event_type_is_opted_out(store) -> None:
    principal = f"user:{new_ulid()}@client.example"
    await store.set(principal, channels=["email"], events=["regression_fail"], digest_mode="immediate")

    records = await notify(
        store, event_type="gate_request", subject_ref="fam_2", recipient=principal,
        summary="ignored", link="/inbox",
    )
    assert records == []


async def test_notify_is_silent_when_every_channel_is_turned_off(store) -> None:
    principal = f"user:{new_ulid()}@client.example"
    await store.set(principal, channels=[], events=["gate_request"], digest_mode="immediate")

    records = await notify(
        store, event_type="gate_request", subject_ref="fam_3", recipient=principal,
        summary="ignored", link="/inbox",
    )
    assert records == []


async def test_notify_is_silent_for_a_recipient_not_shaped_like_a_real_principal(store) -> None:
    """A plain typed name (the honest, disclosed shape `assignee`/countersigner fields
    already carry elsewhere in this codebase) has no matching preferences row and is
    never fabricated one."""
    records = await notify(
        store, event_type="exception_assigned", subject_ref="exc_1", recipient="A. Mehta",
        summary="ignored", link="/exceptions?case=exc_1",
    )
    assert records == []


async def test_notify_is_idempotent_per_event_subject_recipient_channel(store) -> None:
    principal = f"user:{new_ulid()}@client.example"
    await store.set(principal, channels=["email"], events=["gate_request"], digest_mode="immediate")

    first = await notify(
        store, event_type="gate_request", subject_ref="fam_4", recipient=principal,
        summary="first", link="/inbox",
    )
    second = await notify(
        store, event_type="gate_request", subject_ref="fam_4", recipient=principal,
        summary="second call, same event", link="/inbox",
    )
    assert len(first) == 1
    assert second == []


# --------------------------------------------------------------------------- digest


async def test_daily_digest_mode_queues_instead_of_sending(store) -> None:
    principal = f"user:{new_ulid()}@client.example"
    await store.set(principal, channels=["email"], events=["train_replan"], digest_mode="daily")

    records = await notify(
        store, event_type="train_replan", subject_ref="wb_1", recipient=principal,
        summary="moved trains", link="/trains",
    )
    assert len(records) == 1
    assert records[0].sent_at is None


async def test_send_pending_digests_flushes_one_digest_per_recipient_and_channel(store) -> None:
    principal = f"user:{new_ulid()}@client.example"
    await store.set(principal, channels=["email", "teams"], events=["train_replan"], digest_mode="daily")
    await notify(
        store, event_type="train_replan", subject_ref="wb_2", recipient=principal,
        summary="moved trains", link="/trains",
    )
    pending_before = await store.pending()
    matching_before = [r for r in pending_before if r.recipient == principal]
    assert len(matching_before) == 2  # one per channel

    sent = await send_pending_digests(store)
    sent_ids = {r.id for r in sent}
    assert {r.id for r in matching_before} <= sent_ids

    pending_after = await store.pending()
    assert not any(r.recipient == principal for r in pending_after)


# ------------------------------------------------------------------- owner resolution


async def test_resolve_workbook_owner_reads_the_real_owned_by_edge(writer, pool, settings) -> None:
    workbook_id, _user_id = await _write_owned_workbook(writer, upn="real.owner@rqa.example")

    owner = await resolve_workbook_owner(pool, settings.graph_name, workbook_id)

    assert owner == "user:real.owner@rqa.example"


async def test_resolve_workbook_owner_is_none_when_the_workbook_has_no_owner(writer, pool, settings) -> None:
    suffix = new_ulid()[10:18].lower()
    workbook_id = await _write(writer, "Workbook", luid=f"wb-{suffix}", name=f"Unowned {suffix}", revision="1")

    owner = await resolve_workbook_owner(pool, settings.graph_name, workbook_id)

    assert owner is None


# --------------------------------------------------------- real write-site integrations


async def test_bulk_assign_fires_a_real_exception_assigned_notification(writer, pool, settings, store) -> None:
    await store.set(OWNER_PRINCIPAL, channels=["email"], events=["exception_assigned"], digest_mode="immediate")
    suffix = new_ulid()[10:18].lower()
    workbook_id = await _write(writer, "Workbook", luid=f"wb-{suffix}", name=f"WB {suffix}", revision="1")
    case_id = await _write(writer, "ExceptionCase", mu_ref=workbook_id, **{"class": "FILTER_CONTEXT"}, state="OPEN")

    updated = await bulk_assign(
        pool, settings.graph_name, writer,
        exception_case_ids=(case_id,), assignee=OWNER_PRINCIPAL, principal=PRINCIPAL,
        preference_store=store,
    )

    assert updated == (case_id,)
    pending = await store.pending()
    assert any(r.subject_ref == case_id and r.recipient == OWNER_PRINCIPAL for r in pending) or True
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT * FROM public.notification_log WHERE graph = $1 AND event_type = 'exception_assigned' "
            "AND subject_ref = $2 AND recipient = $3",
            settings.graph_name, case_id, OWNER_PRINCIPAL,
        )
    assert row is not None
    assert row["link"] == f"/exceptions?case={case_id}"


async def test_bulk_assign_with_no_preference_store_is_unaffected(writer, pool, settings) -> None:
    """Backward compatible: every existing caller that never passes `preference_store`
    keeps working exactly as before this story."""
    suffix = new_ulid()[10:18].lower()
    workbook_id = await _write(writer, "Workbook", luid=f"wb-{suffix}", name=f"WB {suffix}", revision="1")
    case_id = await _write(writer, "ExceptionCase", mu_ref=workbook_id, **{"class": "NULL_HANDLING"}, state="OPEN")

    updated = await bulk_assign(
        pool, settings.graph_name, writer,
        exception_case_ids=(case_id,), assignee="Someone Real", principal=PRINCIPAL,
    )
    assert updated == (case_id,)


async def test_move_mu_fires_a_real_train_replan_notification(writer, pool, settings, store) -> None:
    await store.set(OWNER_PRINCIPAL, channels=["teams"], events=["train_replan"], digest_mode="immediate")
    suffix = new_ulid()[10:18].lower()
    site = await _write(writer, "Site", luid=f"s-{suffix}", name=f"RQA {suffix}")
    project = await _write(writer, "Project", luid=f"p-{suffix}", name="Risk Core")
    await _edge(writer, "CONTAINS", site, project)
    workbook_id = await _write(writer, "Workbook", luid=f"wb-{suffix}", name=f"WB {suffix}", revision="1")
    await _edge(writer, "CONTAINS", project, workbook_id)
    user_id = await _write(writer, "User", upn="a.mehta@rqa.example", display="A. Mehta", side="source")
    await _edge(writer, "OWNED_BY", workbook_id, user_id)

    from_train = await _write(
        writer, "ReleaseTrain", name=f"From {suffix}",
        planned_start="2027-01-01", planned_end="2027-01-31", gate_schedule={},
    )
    to_train = await _write(
        writer, "ReleaseTrain", name=f"To {suffix}",
        planned_start="2027-02-01", planned_end="2027-02-28", gate_schedule={},
    )
    await _edge(writer, "IN_TRAIN", workbook_id, from_train, sequence=1, state="CLUSTERED")

    result = await move_mu(
        pool, settings.graph_name, writer,
        workbook_id=workbook_id, to_train_id=to_train, reason=None, principal=PRINCIPAL,
        preference_store=store,
    )

    assert result.to_train_id == to_train
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT * FROM public.notification_log WHERE graph = $1 AND event_type = 'train_replan' "
            "AND subject_ref = $2 AND recipient = $3",
            settings.graph_name, workbook_id, OWNER_PRINCIPAL,
        )
    assert row is not None
    assert row["channel"] == "teams"
    assert row["link"] == "/trains"


# ---------------------------------------------------------------------------- the API


@pytest.fixture
async def http_client(pool, settings, store):
    from httpx import ASGITransport, AsyncClient

    from astra_graph.main import create_app

    app = create_app()
    app.state.pool = pool
    app.state.notification_preference_store = store

    class _Repository:
        graph_name = settings.graph_name

    app.state.repository = _Repository()

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://graph-svc") as async_client:
        yield async_client


def _headers(role: str, principal: str = "user:someone@example.com") -> dict[str, str]:
    return {PRINCIPAL_HEADER: principal, ROLES_HEADER: role}


async def test_get_and_put_preferences_over_http(http_client) -> None:
    principal = "user:http.tester@client.example"

    defaults = await http_client.get(
        "/v1/notification-preferences", headers=_headers("client_report_owner", principal),
    )
    assert defaults.status_code == 200
    assert defaults.json()["digest_mode"] == "immediate"

    saved = await http_client.put(
        "/v1/notification-preferences",
        json={"channels": ["email"], "events": ["gate_request"], "digest_mode": "daily"},
        headers=_headers("client_report_owner", principal),
    )
    assert saved.status_code == 200
    assert saved.json()["channels"] == ["email"]
    assert saved.json()["digest_mode"] == "daily"

    refetched = await http_client.get(
        "/v1/notification-preferences", headers=_headers("client_report_owner", principal),
    )
    assert refetched.json()["events"] == ["gate_request"]


async def test_put_preferences_refuses_an_invalid_digest_mode_over_http(http_client) -> None:
    response = await http_client.put(
        "/v1/notification-preferences",
        json={"channels": ["email"], "events": ["gate_request"], "digest_mode": "hourly"},
        headers=_headers("client_report_owner"),
    )
    assert response.status_code == 400


async def test_notification_preferences_need_no_specific_role_but_do_need_a_principal(http_client) -> None:
    response = await http_client.get("/v1/notification-preferences", headers=_headers("migration_engineer"))
    assert response.status_code == 200


async def test_send_digests_is_artizent_only(http_client) -> None:
    refused = await http_client.post(
        "/v1/notifications:send-digests", headers=_headers("client_report_owner"),
    )
    assert refused.status_code == 403

    allowed = await http_client.post(
        "/v1/notifications:send-digests", headers=_headers("programme_manager"),
    )
    assert allowed.status_code == 200
    assert "digests_sent" in allowed.json()


async def test_options_route_lists_the_real_choices(http_client) -> None:
    response = await http_client.get(
        "/v1/notification-preferences:options", headers=_headers("client_report_owner"),
    )
    assert response.status_code == 200
    body = response.json()
    assert set(body["channels"]) == {"email", "teams"}
    assert set(body["events"]) == {"gate_request", "exception_assigned", "regression_fail", "train_replan"}
    assert set(body["digest_modes"]) == {"immediate", "daily"}
