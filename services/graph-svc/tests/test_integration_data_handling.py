"""The Data Handling position/sign-off and the real boundary test, against real
PostgreSQL + Apache AGE -- story S11.4.1, opens F11.4.

What only the real stack can answer: that the position/sign-off stores really persist
and version correctly; that editing a real, saved position really invalidates a real
prior signature; that the real boundary test really drives `mender.assemble_repair_
context` end to end and really finds nothing leaked, both in the assembled context and
in a real `gateway_request_log` row; and that the HTTP routes really enforce this
story's own three distinct role gates (any Artizent role or the InfoSec reviewer to
read/run the boundary test, the platform engineer alone to edit the position, the
InfoSec reviewer alone -- not even Artizent -- to sign).
"""

from __future__ import annotations

import asyncio
import os
import threading
from typing import Any

import pytest

pytestmark = pytest.mark.integration

asyncpg = pytest.importorskip("asyncpg")

from astra_graph.artefacts import PostgresArtefactStore  # noqa: E402
from astra_graph.config import Settings  # noqa: E402
from astra_graph.data_handling import (  # noqa: E402
    PostgresDataHandlingPositionStore,
    PostgresDataHandlingSignoffStore,
    boundary_status,
    run_boundary_test,
    sign_position,
)
from astra_graph.events import source_for  # noqa: E402
from astra_graph.graph import AgeGraphRepository, create_pool  # noqa: E402
from astra_graph.graph.queries import accessor  # noqa: E402
from astra_graph.ids import new_ulid  # noqa: E402
from astra_graph.migrations import run as run_migrations  # noqa: E402
from astra_graph.ontology import EDGE_LABELS, NODE_LABELS  # noqa: E402
from astra_graph.principal import Principal  # noqa: E402
from astra_graph.writes import GraphWriter  # noqa: E402

PRINCIPAL = Principal("agent:harvester", run_id="run-data-handling")


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
        pool_max_size=4,
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
    config = _settings(f"astra_data_handling_{new_ulid()[10:22].lower()}")

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
                "public.artefacts", "public.data_handling_position", "public.data_handling_signoff",
                "public.gateway_request_log", "public.model_gateway_policy",
                "public.gateway_content_logging_grant",
            ):
                await conn.execute(f"DELETE FROM {table} WHERE graph = $1", config.graph_name)
            await conn.execute("SELECT ag_catalog.drop_graph($1, true)", config.graph_name)
        finally:
            await conn.close()

    _run_off_loop(teardown)


@pytest.fixture
async def estate(settings: Settings):
    pool = await create_pool(settings)
    try:
        repository = AgeGraphRepository(pool, graph_name=settings.graph_name)
        writer = GraphWriter(repository, event_source=source_for(settings.graph_name))
        artefact_store = PostgresArtefactStore(pool, graph_name=settings.graph_name)
        yield {"pool": pool, "settings": settings, "writer": writer, "artefact_store": artefact_store}
    finally:
        await pool.close()


# --------------------------------------------------------------------------- the stores


async def test_a_fresh_graph_has_the_honest_default_position(estate) -> None:
    store = PostgresDataHandlingPositionStore(estate["pool"], graph_name=estate["settings"].graph_name, config=estate["settings"])
    position = await store.latest()
    assert position.version == 0
    assert position.providers[0]["name"] == "anthropic"


async def test_saving_a_position_really_versions(estate) -> None:
    store = PostgresDataHandlingPositionStore(estate["pool"], graph_name=estate["settings"].graph_name, config=estate["settings"])
    first = await store.save(
        providers=({"name": "anthropic", "model": "claude-sonnet-5", "region": None},),
        retention_terms="terms v1", redaction_rules=("rule 1",), updated_by="user:pe@artizent.example",
    )
    second = await store.save(
        providers=({"name": "anthropic", "model": "claude-sonnet-5", "region": None},),
        retention_terms="terms v2", redaction_rules=("rule 1", "rule 2"), updated_by="user:pe@artizent.example",
    )
    assert first.version == 1
    assert second.version == 2
    latest = await store.latest()
    assert latest.version == 2
    assert latest.retention_terms == "terms v2"
    assert latest.redaction_rules == ("rule 1", "rule 2")


async def test_signing_and_re_signing_a_real_position(estate) -> None:
    positions = PostgresDataHandlingPositionStore(estate["pool"], graph_name=estate["settings"].graph_name, config=estate["settings"])
    signoffs = PostgresDataHandlingSignoffStore(estate["pool"], graph_name=estate["settings"].graph_name)

    assert await signoffs.latest() is None
    status = await boundary_status(positions, signoffs)
    assert status.signed is False

    await sign_position(positions, signoffs, reviewer="user:infosec@client.example")
    status = await boundary_status(positions, signoffs)
    assert status.signed is True
    assert status.signoff.reviewer == "user:infosec@client.example"  # type: ignore[union-attr]

    await positions.save(
        providers=({"name": "anthropic", "model": "claude-sonnet-5", "region": "us"},),
        retention_terms="a real change", redaction_rules=(), updated_by="user:pe@artizent.example",
    )
    status = await boundary_status(positions, signoffs)
    assert status.signed is False, "editing a real, saved position must invalidate a real prior signature"

    await sign_position(positions, signoffs, reviewer="user:infosec@client.example")
    assert (await boundary_status(positions, signoffs)).signed is True


# ---------------------------------------------------------------------- the boundary test


async def test_the_real_boundary_test_passes_against_a_clean_estate(estate) -> None:
    result = await run_boundary_test(
        estate["pool"], estate["settings"].graph_name,
        artefact_store=estate["artefact_store"], writer=estate["writer"],
    )
    assert result.passed is True
    assert "OK" in result.detail


async def test_the_boundary_test_retires_its_own_disposable_verdict(estate) -> None:
    from astra_graph.graph.queries import NODE_INDEX_TABLE

    await run_boundary_test(
        estate["pool"], estate["settings"].graph_name,
        artefact_store=estate["artefact_store"], writer=estate["writer"],
    )
    async with estate["pool"].acquire() as conn:
        live = await conn.fetchval(
            f"""SELECT count(*) FROM {NODE_INDEX_TABLE}
                 WHERE graph = $1 AND kind = 'node' AND label = 'Verdict' AND retired_at IS NULL""",
            estate["settings"].graph_name,
        )
    assert live == 0, "the boundary test's own disposable Verdict must be retired, not left live"


async def test_a_real_gateway_request_row_was_written(estate) -> None:
    await run_boundary_test(
        estate["pool"], estate["settings"].graph_name,
        artefact_store=estate["artefact_store"], writer=estate["writer"],
    )
    async with estate["pool"].acquire() as conn:
        row = await conn.fetchrow(
            "SELECT provider, task_class, request_text FROM public.gateway_request_log WHERE graph = $1",
            estate["settings"].graph_name,
        )
    assert row is not None
    assert row["task_class"] == "mender_repair"
    assert "BOUNDARY_TEST_CLASS" in row["request_text"]


# --------------------------------------------------------------------------------- HTTP


def _headers(role: str, principal: Principal) -> dict[str, str]:
    from astra_graph.principal import PRINCIPAL_HEADER
    from astra_graph.roles import ROLES_HEADER

    return {PRINCIPAL_HEADER: principal.value, ROLES_HEADER: role}


class _FakeCartographer:
    def __init__(self, pool: Any, graph_name: str) -> None:
        self.pool = pool
        self.graph_name = graph_name


@pytest.fixture
async def http_client(estate):
    from httpx import ASGITransport, AsyncClient

    from astra_graph.data_handling import (
        PostgresDataHandlingPositionStore,
        PostgresDataHandlingSignoffStore,
    )
    from astra_graph.gateway import PostgresContentLoggingGrantStore
    from astra_graph.main import create_app

    app = create_app()
    app.state.cartographer = _FakeCartographer(estate["pool"], estate["settings"].graph_name)
    app.state.artefact_store = estate["artefact_store"]
    app.state.writer = estate["writer"]
    app.state.data_handling_position_store = PostgresDataHandlingPositionStore(
        estate["pool"], graph_name=estate["settings"].graph_name, config=estate["settings"]
    )
    app.state.data_handling_signoff_store = PostgresDataHandlingSignoffStore(
        estate["pool"], graph_name=estate["settings"].graph_name
    )
    app.state.content_logging_grant_store = PostgresContentLoggingGrantStore(
        estate["pool"], graph_name=estate["settings"].graph_name
    )

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://graph-svc", timeout=60.0) as async_client:
        yield async_client


INFOSEC = Principal("user:infosec@client.example")
PLATFORM_ENGINEER = Principal("user:pe@artizent.example")
CLIENT_REPORT_OWNER = Principal("user:owner@client.example")


async def test_get_data_handling_includes_the_static_boundary_table_and_live_status(estate, http_client) -> None:
    response = await http_client.get("/v1/data-handling", headers=_headers("client_infosec_reviewer", INFOSEC))
    assert response.status_code == 200
    body = response.json()
    assert body["signed"] is False
    assert "Row-level data" in " ".join(body["inference_boundary_table"]["never_sent"])
    assert body["position"]["providers"][0]["name"] == "anthropic"


async def test_a_client_report_owner_cannot_read_the_screen(estate, http_client) -> None:
    response = await http_client.get(
        "/v1/data-handling", headers=_headers("client_report_owner", CLIENT_REPORT_OWNER),
    )
    assert response.status_code == 403


async def test_the_platform_engineer_can_edit_the_position(estate, http_client) -> None:
    response = await http_client.put(
        "/v1/data-handling/position",
        json={"providers": [{"name": "anthropic", "model": "claude-sonnet-5", "region": None}],
              "retention_terms": "real terms", "redaction_rules": ["rule 1"]},
        headers=_headers("platform_engineer", PLATFORM_ENGINEER),
    )
    assert response.status_code == 200
    assert response.json()["version"] == 1


async def test_the_infosec_reviewer_cannot_edit_the_position(estate, http_client) -> None:
    response = await http_client.put(
        "/v1/data-handling/position",
        json={"providers": [], "retention_terms": "x", "redaction_rules": []},
        headers=_headers("client_infosec_reviewer", INFOSEC),
    )
    assert response.status_code == 403


async def test_the_infosec_reviewer_can_sign(estate, http_client) -> None:
    response = await http_client.post(
        "/v1/data-handling:sign", headers=_headers("client_infosec_reviewer", INFOSEC),
    )
    assert response.status_code == 200
    body = response.json()
    assert body["reviewer"] == INFOSEC.value
    assert body["position_version"] == 0


async def test_artizent_cannot_sign_even_though_it_can_read(estate, http_client) -> None:
    """This story's own deliberate departure: signing is the InfoSec reviewer's own
    confirmation, not open to 'any Artizent role' the way every other read/edit gate
    in this epic is (`deps.require_infosec_reviewer`'s own docstring)."""
    response = await http_client.post(
        "/v1/data-handling:sign", headers=_headers("platform_engineer", PLATFORM_ENGINEER),
    )
    assert response.status_code == 403


async def test_verify_boundary_over_http_runs_the_real_check(estate, http_client) -> None:
    response = await http_client.post(
        "/v1/data-handling:verify-boundary", headers=_headers("client_infosec_reviewer", INFOSEC),
    )
    assert response.status_code == 200
    body = response.json()
    assert body["passed"] is True
    assert body["sentinel"].startswith("CANARY-")


# --------------------------------------------------- S11.4.2: content-logging grant, over HTTP


async def test_content_logging_is_off_by_default_on_a_fresh_deployment(estate, http_client) -> None:
    response = await http_client.get("/v1/data-handling", headers=_headers("client_infosec_reviewer", INFOSEC))
    assert response.status_code == 200
    assert response.json()["content_logging_grant"] is None


async def test_the_infosec_reviewer_can_enable_content_logging_for_a_real_bounded_window(estate, http_client) -> None:
    response = await http_client.post(
        "/v1/data-handling:enable-content-logging", json={"duration_minutes": 30},
        headers=_headers("client_infosec_reviewer", INFOSEC),
    )
    assert response.status_code == 200
    body = response.json()
    assert body["enabled_by"] == INFOSEC.value
    assert body["active"] is True

    status = await http_client.get("/v1/data-handling", headers=_headers("client_infosec_reviewer", INFOSEC))
    assert status.json()["content_logging_grant"]["active"] is True


async def test_the_platform_engineer_cannot_enable_content_logging(estate, http_client) -> None:
    """The identical departure `sign_data_handling` already has -- this is the InfoSec
    reviewer's own real control, not Artizent's to grant on the client's behalf."""
    response = await http_client.post(
        "/v1/data-handling:enable-content-logging", json={"duration_minutes": 30},
        headers=_headers("platform_engineer", PLATFORM_ENGINEER),
    )
    assert response.status_code == 403


async def test_enabling_content_logging_beyond_the_cap_is_refused(estate, http_client) -> None:
    response = await http_client.post(
        "/v1/data-handling:enable-content-logging", json={"duration_minutes": 1441},
        headers=_headers("client_infosec_reviewer", INFOSEC),
    )
    assert response.status_code == 422  # pydantic's own le=1440 field constraint


async def test_the_infosec_reviewer_can_disable_content_logging_early(estate, http_client) -> None:
    await http_client.post(
        "/v1/data-handling:enable-content-logging", json={"duration_minutes": 60},
        headers=_headers("client_infosec_reviewer", INFOSEC),
    )
    response = await http_client.post(
        "/v1/data-handling:disable-content-logging", headers=_headers("client_infosec_reviewer", INFOSEC),
    )
    assert response.status_code == 200
    assert response.json()["active"] is False

    status = await http_client.get("/v1/data-handling", headers=_headers("client_infosec_reviewer", INFOSEC))
    assert status.json()["content_logging_grant"]["active"] is False


async def test_the_boundary_test_itself_grants_and_revokes_its_own_real_window(estate, http_client) -> None:
    """`run_boundary_test` (data_handling.py) grants itself a real, short content-
    logging window so its own check has real text to search -- and revokes it again
    before returning (this module's own docstring on `_BOUNDARY_TEST_GRANT_MINUTES`
    explains why). Confirmed here at the HTTP level: no grant is left active
    afterward, even though the run's own request really was logged with full text."""
    response = await http_client.post(
        "/v1/data-handling:verify-boundary", headers=_headers("client_infosec_reviewer", INFOSEC),
    )
    assert response.status_code == 200
    assert response.json()["passed"] is True

    status = await http_client.get("/v1/data-handling", headers=_headers("client_infosec_reviewer", INFOSEC))
    grant = status.json()["content_logging_grant"]
    assert grant is not None
    assert grant["active"] is False, "the boundary test's own grant must be revoked before it returns"
