"""Building an approved design as TMDL and deploying it, against real PostgreSQL + Apache
AGE and a real local Git repository — story S4.3.1.

What only the real stack can answer: that `build_family` actually reads the frozen design
back, that the emitted TMDL bundle actually lands as artefacts and as real Git commits
under the family's own item path, that a family only reaches `BUILT` once every step's own
success is true, that a failed build leaves `APPROVED` alone rather than half-transitioning,
and that the automatic G2-approval trigger and the manual retry route both drive the exact
same pipeline over HTTP.
"""

from __future__ import annotations

import asyncio
import os
import threading
from collections.abc import Callable, Coroutine
from pathlib import Path
from typing import Any

import pytest

pytestmark = pytest.mark.integration

asyncpg = pytest.importorskip("asyncpg")

from astra_adapter.target_fake import FixtureTargetAdapter  # noqa: E402

from astra_graph.artefacts import PostgresArtefactStore  # noqa: E402
from astra_graph.build import BuildRecord, PostgresBuildStore, build_family  # noqa: E402
from astra_graph.config import Settings  # noqa: E402
from astra_graph.conformance_rules import PostgresConformanceRulesetStore  # noqa: E402
from astra_graph.errors import InvalidRequestError  # noqa: E402
from astra_graph.events import source_for  # noqa: E402
from astra_graph.g2 import PostgresQuestionStore, approve  # noqa: E402
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
OWNER = Principal("user:owner@client.example")
STEWARD = Principal("agent:steward", run_id="run-build")


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
    config = _settings(f"astra_build_{new_ulid()[10:22].lower()}")

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
                "public.artefacts",
                "public.build_run",
                "public.conformance_ruleset",
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
async def estate(settings: Settings, tmp_path: Path):
    """One ModelFamily generated, accepted, given an owner, submitted for review and
    approved at G2 — everything `build_family` needs, stopping one step short of it."""
    pool = await create_pool(settings)
    try:
        repository = AgeGraphRepository(pool, graph_name=settings.graph_name)
        writer = GraphWriter(repository, event_source=source_for(settings.graph_name))
        provenance_store = PostgresProvenanceStore(pool, graph_name=settings.graph_name)
        question_store = PostgresQuestionStore(pool, graph_name=settings.graph_name)
        artefact_store = PostgresArtefactStore(pool, graph_name=settings.graph_name)
        build_store = PostgresBuildStore(pool, graph_name=settings.graph_name)
        conformance_store = PostgresConformanceRulesetStore(pool, graph_name=settings.graph_name)
        target_adapter = FixtureTargetAdapter(repo_path=tmp_path / "repo")
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
        approval = await approve(
            pool, settings.graph_name, writer, question_store, family,
            principal=OWNER, domain_scope=frozenset(), countersigned_by=ENGINEER.value,
            rationale="Reviewed and approved.",
        )

        yield {
            "pool": pool,
            "settings": settings,
            "writer": writer,
            "modeller": modeller,
            "artefact_store": artefact_store,
            "build_store": build_store,
            "conformance_store": conformance_store,
            "target_adapter": target_adapter,
            "family": family,
            "family_name": f"Risk Positions {suffix}",
            "version": submitted["version"],
            "gate_decision_id": approval["gate_decision_id"],
        }
    finally:
        await pool.close()


# ---------------------------------------------------------------------------- build_family


async def test_a_successful_build_reaches_built(estate) -> None:
    record = await build_family(
        estate["pool"], estate["settings"].graph_name, estate["writer"], estate["artefact_store"],
        estate["target_adapter"], estate["build_store"], estate["conformance_store"], estate["family"],
        gate_decision_id=estate["gate_decision_id"], workspace="dev", principal=STEWARD,
    )
    assert record.state == "SUCCEEDED"
    assert all(step.ok for step in record.steps)
    assert record.git_commit_sha

    from astra_graph.cartographer import get_family
    family = await get_family(estate["pool"], estate["settings"].graph_name, estate["family"])
    assert family is not None
    assert family["state"] == "BUILT"


async def test_the_commit_message_references_the_family_and_gate_decision(estate) -> None:
    from dulwich.repo import Repo

    await build_family(
        estate["pool"], estate["settings"].graph_name, estate["writer"], estate["artefact_store"],
        estate["target_adapter"], estate["build_store"], estate["conformance_store"], estate["family"],
        gate_decision_id=estate["gate_decision_id"], workspace="dev", principal=STEWARD,
    )
    repo = Repo(str(estate["target_adapter"]._repo_path))
    try:
        message = repo[repo.head()].message.decode("utf-8")
    finally:
        repo.close()
    assert estate["family"] in message
    assert estate["gate_decision_id"] in message


async def test_tmdl_files_are_stored_as_artefacts(estate) -> None:
    await build_family(
        estate["pool"], estate["settings"].graph_name, estate["writer"], estate["artefact_store"],
        estate["target_adapter"], estate["build_store"], estate["conformance_store"], estate["family"],
        gate_decision_id=estate["gate_decision_id"], workspace="dev", principal=STEWARD,
    )
    artefacts = await estate["artefact_store"].for_mu(estate["family"], kind="tmdl_file")
    assert any(a.case_id == "model.tmdl" for a in artefacts)
    assert any(a.case_id == "tables/positions.tmdl" for a in artefacts)


async def test_rebuilding_an_already_built_family_is_idempotent_in_git(estate) -> None:
    """A rebuild — the Build tab's own retry, or a redeploy with nothing design-side
    changed — is not a state-machine transition (BUILT has no edge back to itself); it
    must still be a legitimate, idempotent action rather than refused outright."""
    first = await build_family(
        estate["pool"], estate["settings"].graph_name, estate["writer"], estate["artefact_store"],
        estate["target_adapter"], estate["build_store"], estate["conformance_store"], estate["family"],
        gate_decision_id=estate["gate_decision_id"], workspace="dev", principal=STEWARD,
    )
    assert first.state == "SUCCEEDED"
    second = await build_family(
        estate["pool"], estate["settings"].graph_name, estate["writer"], estate["artefact_store"],
        estate["target_adapter"], estate["build_store"], estate["conformance_store"], estate["family"],
        gate_decision_id=estate["gate_decision_id"], workspace="dev", principal=STEWARD,
    )
    assert second.state == "SUCCEEDED"
    assert first.git_commit_sha == second.git_commit_sha


async def test_building_an_unknown_family_is_a_clean_404(estate) -> None:
    from astra_graph.errors import ElementNotFoundError

    with pytest.raises(ElementNotFoundError):
        await build_family(
            estate["pool"], estate["settings"].graph_name, estate["writer"], estate["artefact_store"],
            estate["target_adapter"], estate["build_store"], estate["conformance_store"], "not-a-real-family",
            gate_decision_id=None, workspace="dev", principal=STEWARD,
        )


async def test_a_draft_family_cannot_be_built(estate) -> None:
    """`require_transition` refuses before this module ever looks at a frozen version —
    the state-machine guard, not a build-specific one, is what a DRAFT family hits. A bare
    `ModelFamily` node is enough: the guard runs before any design is ever read."""
    draft_family = await _write(
        estate["writer"], "ModelFamily", name="Never submitted", state="DRAFT",
        grain="", conformed_dims=[],
    )
    with pytest.raises(InvalidRequestError, match="DRAFT"):
        await build_family(
            estate["pool"], estate["settings"].graph_name, estate["writer"], estate["artefact_store"],
            estate["target_adapter"], estate["build_store"], estate["conformance_store"], draft_family,
            gate_decision_id=None, workspace="dev", principal=STEWARD,
        )


async def test_conformance_ruleset_version_is_recorded_on_a_successful_build(estate) -> None:
    from astra_graph.cartographer import get_family

    await build_family(
        estate["pool"], estate["settings"].graph_name, estate["writer"], estate["artefact_store"],
        estate["target_adapter"], estate["build_store"], estate["conformance_store"], estate["family"],
        gate_decision_id=estate["gate_decision_id"], workspace="dev", principal=STEWARD,
    )
    family = await get_family(estate["pool"], estate["settings"].graph_name, estate["family"])
    assert family is not None
    # The in-memory default (version 0) — nothing has been saved to public.conformance_ruleset yet.
    assert family["conformance_ruleset_version"] == 0


async def test_latest_returns_the_most_recent_build(estate) -> None:
    await build_family(
        estate["pool"], estate["settings"].graph_name, estate["writer"], estate["artefact_store"],
        estate["target_adapter"], estate["build_store"], estate["conformance_store"], estate["family"],
        gate_decision_id=estate["gate_decision_id"], workspace="dev", principal=STEWARD,
    )
    latest = await estate["build_store"].latest(estate["family"])
    assert isinstance(latest, BuildRecord)
    assert latest.family_id == estate["family"]
    assert latest.state == "SUCCEEDED"


# ---------------------------------------------------------------------------------- the API


@pytest.fixture
async def http_client(estate):
    from httpx import ASGITransport, AsyncClient

    from astra_graph.g2 import PostgresQuestionStore
    from astra_graph.main import create_app

    app = create_app()
    app.state.modeller = estate["modeller"]
    app.state.question_store = PostgresQuestionStore(estate["pool"], graph_name=estate["settings"].graph_name)
    app.state.artefact_store = estate["artefact_store"]
    app.state.build_store = estate["build_store"]
    app.state.conformance_store = estate["conformance_store"]
    app.state.target_adapter = estate["target_adapter"]
    app.state.target_workspace = "dev"

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://graph-svc") as async_client:
        yield async_client


def _headers(role: str, principal: Principal) -> dict[str, str]:
    from astra_graph.principal import PRINCIPAL_HEADER
    from astra_graph.roles import ROLES_HEADER

    return {PRINCIPAL_HEADER: principal.value, ROLES_HEADER: role}


async def test_manual_build_over_http(estate, http_client) -> None:
    response = await http_client.post(
        f"/v1/families/{estate['family']}:build",
        headers=_headers("semantic_model_engineer", ENGINEER),
    )
    assert response.status_code == 200
    body = response.json()
    assert body["state"] == "SUCCEEDED"


async def test_build_requires_the_semantic_model_engineer_role(estate, http_client) -> None:
    response = await http_client.post(
        f"/v1/families/{estate['family']}:build",
        headers=_headers("programme_manager", ENGINEER),
    )
    assert response.status_code == 403


async def test_get_build_over_http(estate, http_client) -> None:
    await http_client.post(
        f"/v1/families/{estate['family']}:build", headers=_headers("semantic_model_engineer", ENGINEER),
    )
    response = await http_client.get(
        f"/v1/families/{estate['family']}/build", headers=_headers("programme_manager", ENGINEER),
    )
    assert response.status_code == 200
    body = response.json()
    assert body["build"]["state"] == "SUCCEEDED"


async def test_get_build_before_any_build_reports_none(http_client) -> None:
    # A different, never-built family id — `build.py`'s own reasoning: a family that has
    # never been built has no build row, not a fabricated empty one.
    response = await http_client.get(
        "/v1/families/not-a-real-family/build", headers=_headers("programme_manager", ENGINEER),
    )
    assert response.status_code == 200
    assert response.json() == {"family_id": "not-a-real-family", "build": None}


async def test_approving_at_g2_triggers_a_build_automatically(settings: Settings, tmp_path: Path) -> None:
    """A second, independent family, approved over HTTP rather than by calling `approve`
    directly — proving the automatic trigger `routes_g2.approve_route` wires in, not just
    the function this suite otherwise calls by hand."""
    from httpx import ASGITransport, AsyncClient

    from astra_graph.artefacts import PostgresArtefactStore
    from astra_graph.build import PostgresBuildStore
    from astra_graph.conformance_rules import PostgresConformanceRulesetStore
    from astra_graph.events import source_for
    from astra_graph.g2 import PostgresQuestionStore
    from astra_graph.graph import AgeGraphRepository, create_pool
    from astra_graph.main import create_app
    from astra_graph.model_lifecycle import accept_family, submit_for_review, update_owner
    from astra_graph.modeller import Modeller
    from astra_graph.provenance import PostgresProvenanceStore
    from astra_graph.writes import GraphWriter

    pool = await create_pool(settings)
    try:
        repository = AgeGraphRepository(pool, graph_name=settings.graph_name)
        writer = GraphWriter(repository, event_source=source_for(settings.graph_name))
        provenance_store = PostgresProvenanceStore(pool, graph_name=settings.graph_name)
        question_store = PostgresQuestionStore(pool, graph_name=settings.graph_name)
        artefact_store = PostgresArtefactStore(pool, graph_name=settings.graph_name)
        build_store = PostgresBuildStore(pool, graph_name=settings.graph_name)
        conformance_store = PostgresConformanceRulesetStore(pool, graph_name=settings.graph_name)
        target_adapter = FixtureTargetAdapter(repo_path=tmp_path / "repo2")
        modeller = Modeller(pool, graph_name=settings.graph_name, writer=writer, provenance_store=provenance_store)

        suffix = new_ulid()[10:18].lower()
        site = await _write(writer, "Site", luid=f"s2-{suffix}", name=f"RQA2 {suffix}")
        project = await _write(writer, "Project", luid=f"p2-{suffix}", name="Risk Core 2")
        await _edge(writer, "CONTAINS", site, project)
        table = await _write(writer, "Table", name="positions2", schema="risk", row_estimate=500)
        connection = await _write(writer, "Connection", **{"class": "snowflake"}, server="warehouse", db="risk")
        await _edge(writer, "CONNECTS_TO", connection, table)
        book = await _write(writer, "Workbook", luid=f"wb2-{suffix}", name="Weekly VaR", revision="1")
        await _edge(writer, "CONTAINS", project, book)
        sheet = await _write(writer, "Worksheet", name="sheet", rows_shelf=[], cols_shelf=[], marks_shelf=[])
        await _edge(writer, "CONTAINS", book, sheet)
        datasource = await _write(
            writer, "Datasource", name="ds2", type="published", luid=f"ds2-{suffix}",
            extract_flag=True, refresh_schedule="daily",
        )
        await _edge(writer, "USES_DATASOURCE", sheet, datasource)
        await _edge(writer, "CONNECTS_TO", datasource, connection)
        family = await _write(
            writer, "ModelFamily", name=f"Weekly VaR {suffix}", state="PROPOSED", grain="Date", conformed_dims=[],
        )
        await _edge(writer, "IN_FAMILY", book, family, confidence=1.0)

        await modeller.run(family, principal=PRINCIPAL)
        await accept_family(pool, settings.graph_name, writer, family, principal=ENGINEER)
        await update_owner(pool, settings.graph_name, writer, family, owner="owner2@client.example", principal=ENGINEER)
        await submit_for_review(pool, settings.graph_name, writer, family, principal=ENGINEER)

        app = create_app()
        app.state.modeller = modeller
        app.state.question_store = question_store
        app.state.artefact_store = artefact_store
        app.state.build_store = build_store
        app.state.conformance_store = conformance_store
        app.state.target_adapter = target_adapter
        app.state.target_workspace = "dev"

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://graph-svc") as client:
            approved = await client.post(
                f"/v1/families/{family}:approve-g2",
                json={"countersigned_by": ENGINEER.value, "rationale": "Approved; should build itself."},
                headers=_headers("client_data_owner", OWNER),
            )
            assert approved.status_code == 200

            build = await client.get(
                f"/v1/families/{family}/build", headers=_headers("programme_manager", ENGINEER),
            )
        assert build.json()["build"] is not None
        assert build.json()["build"]["state"] == "SUCCEEDED"
        assert build.json()["build"]["triggered_by"] == "agent:steward"
    finally:
        await pool.close()


# --------------------------------------------------------------------------- conformance (S4.3.2)
#
# Saves a new ruleset version against the shared module-scoped graph, so this stays the
# very last test in the file: anything run after it would see the mutated ruleset instead
# of the in-memory default every other test above assumes.


async def test_a_conformance_failure_blocks_built_and_never_reaches_commit(estate) -> None:
    from astra_graph.cartographer import get_family
    from astra_graph.conformance_rules import RULES, RuleConfig

    # A guaranteed failure: every real name "exceeds 0 characters".
    await estate["conformance_store"].save(
        [
            RuleConfig(rule_id, enabled=(rule_id == "naming_convention"), params={"max_length": 0})
            for rule_id in RULES
        ],
        updated_by="user:architect@artizent.example",
    )

    record = await build_family(
        estate["pool"], estate["settings"].graph_name, estate["writer"], estate["artefact_store"],
        estate["target_adapter"], estate["build_store"], estate["conformance_store"], estate["family"],
        gate_decision_id=estate["gate_decision_id"], workspace="dev", principal=STEWARD,
    )

    assert record.state == "FAILED"
    conformance_step = next(s for s in record.steps if s.name == "conformance")
    assert conformance_step.ok is False
    assert "positions" in conformance_step.detail  # the offending object, named
    assert not any(s.name == "commit" for s in record.steps)  # never reached the target adapter

    family = await get_family(estate["pool"], estate["settings"].graph_name, estate["family"])
    assert family is not None
    assert family["state"] == "APPROVED"  # never advanced
    assert family["conformance_ruleset_version"] == 1  # still recorded despite the failure
