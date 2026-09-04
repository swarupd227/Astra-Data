"""A second version of a published model, without breaking released reports —
story S4.3.3, against real PostgreSQL + Apache AGE and a real local Git repository.

What only the real stack can answer: that v(n) genuinely never changes while v(n+1) is
designed (its own SemanticModel node, its own ModelTable copies, its own relationships,
untouched), that `read_design_document` resolves "the current one" deterministically once
two live versions coexist, that promoting v(n+1) marks v(n) DEPRECATED with a real date
while leaving it otherwise exactly as it was, and that both the direct function calls and
the HTTP routes agree.
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
from astra_graph.build import PostgresBuildStore, build_family  # noqa: E402
from astra_graph.config import Settings  # noqa: E402
from astra_graph.conformance_rules import PostgresConformanceRulesetStore  # noqa: E402
from astra_graph.errors import InvalidRequestError  # noqa: E402
from astra_graph.events import source_for  # noqa: E402
from astra_graph.g2 import PostgresQuestionStore, approve  # noqa: E402
from astra_graph.graph import AgeGraphRepository, create_pool  # noqa: E402
from astra_graph.graph.queries import accessor  # noqa: E402
from astra_graph.ids import new_ulid  # noqa: E402
from astra_graph.migrations import run as run_migrations  # noqa: E402
from astra_graph.model_lifecycle import (  # noqa: E402
    accept_family,
    list_model_versions,
    promote_family,
    request_new_version,
    submit_for_review,
    update_owner,
)
from astra_graph.modeller import Modeller, read_design_document  # noqa: E402
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
    config = _settings(f"astra_versioning_{new_ulid()[10:22].lower()}")

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


async def _promote(estate: dict[str, Any], family_id: str) -> dict[str, Any]:
    """The route's own orchestration (`routes_modeller.promote`), replicated here for a
    direct function-level test: deploy the already-committed build to "prod" *before*
    calling `promote_family`, exactly as the real route refuses to mark anything PUBLISHED
    on the strength of a state flip alone."""
    latest_build = await estate["build_store"].latest(family_id)
    assert latest_build is not None and latest_build.state == "SUCCEEDED"
    deployment = await estate["target_adapter"].deploy(workspace="prod", git_ref=latest_build.git_ref)
    assert deployment.ok
    return await promote_family(
        estate["pool"], estate["settings"].graph_name, estate["writer"], family_id, principal=ENGINEER,
    )


@pytest.fixture
async def estate(settings: Settings, tmp_path: Path):
    """One ModelFamily, two tables joined through a shared connection, built and promoted
    all the way to PUBLISHED v1 — the starting point every test in this file needs."""
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

        # Named uniquely per test (not just "positions"/"desk") — this file's estate
        # fixture is function-scoped but shares one module-scoped graph across every test,
        # and a repeated table name would make the Modeller's own conformed-dimension
        # detection see it as genuinely shared across many families, tripping S4.3.2's own
        # "shared by reference, not copied" conformance rule for reasons that have nothing
        # to do with what this file is testing.
        base_table = await _write(writer, "Table", name=f"positions_{suffix}", schema="risk", row_estimate=5_000_000)
        dim_table = await _write(writer, "Table", name=f"desk_{suffix}", schema="risk", row_estimate=40)
        connection = await _write(writer, "Connection", **{"class": "snowflake"}, server="warehouse", db="risk")
        await _edge(writer, "CONNECTS_TO", connection, base_table, join_clause=None)
        await _edge(
            writer, "CONNECTS_TO", connection, dim_table,
            join_clause=f"positions_{suffix}.desk_id = desk_{suffix}.id",
        )

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
        await approve(
            pool, settings.graph_name, writer, question_store, family,
            principal=OWNER, domain_scope=frozenset(), countersigned_by=ENGINEER.value,
            rationale="Reviewed and approved.",
        )

        estate_dict = {
            "pool": pool,
            "settings": settings,
            "writer": writer,
            "modeller": modeller,
            "question_store": question_store,
            "artefact_store": artefact_store,
            "build_store": build_store,
            "conformance_store": conformance_store,
            "target_adapter": target_adapter,
            "family": family,
            "family_name": f"Risk Positions {suffix}",
            "v1_version_hash": submitted["version"],
        }

        built = await build_family(
            pool, settings.graph_name, writer, artefact_store, target_adapter, build_store,
            conformance_store, family, gate_decision_id=None, workspace="dev", principal=STEWARD,
        )
        assert built.state == "SUCCEEDED"
        promoted = await _promote(estate_dict, family)
        assert promoted["version_number"] == 1
        estate_dict["v1_semantic_model_id"] = promoted["semantic_model_id"]

        yield estate_dict
    finally:
        await pool.close()


# ------------------------------------------------------------------------ request_new_version


async def test_a_change_request_creates_v2_as_draft(estate) -> None:
    result = await request_new_version(
        estate["pool"], estate["settings"].graph_name, estate["writer"], estate["family"],
        reason="Mender repair: a source column was renamed upstream.", principal=ENGINEER,
    )
    assert result["version_number"] == 2
    assert result["previous_version_number"] == 1
    assert result["previous_semantic_model_id"] == estate["v1_semantic_model_id"]

    from astra_graph.cartographer import get_family

    family = await get_family(estate["pool"], estate["settings"].graph_name, estate["family"])
    assert family is not None
    assert family["state"] == "DRAFT"


async def test_v1_is_untouched_by_the_change_request(estate) -> None:
    before = await read_design_document(
        estate["pool"], estate["settings"].graph_name, estate["family"],
        semantic_model_id=estate["v1_semantic_model_id"],
    )
    await request_new_version(
        estate["pool"], estate["settings"].graph_name, estate["writer"], estate["family"],
        reason="A design change that must not disturb what is live.", principal=ENGINEER,
    )
    after = await read_design_document(
        estate["pool"], estate["settings"].graph_name, estate["family"],
        semantic_model_id=estate["v1_semantic_model_id"],
    )
    assert after["version"] == before["version"]
    assert after["grain_statement"] == before["grain_statement"]
    assert {t["id"] for t in after["tables"]} == {t["id"] for t in before["tables"]}
    assert after["version_number"] == 1
    assert after["state"] == "PUBLISHED"


async def test_v2_gets_its_own_table_copies_with_remapped_relationships(estate) -> None:
    v1_tables = {t["name"]: t["id"] for t in (await read_design_document(
        estate["pool"], estate["settings"].graph_name, estate["family"],
        semantic_model_id=estate["v1_semantic_model_id"],
    ))["tables"]}

    await request_new_version(
        estate["pool"], estate["settings"].graph_name, estate["writer"], estate["family"],
        reason="Copy-and-edit should never touch v1's own tables.", principal=ENGINEER,
    )
    v2 = await read_design_document(estate["pool"], estate["settings"].graph_name, estate["family"])
    v2_tables = {t["name"]: t["id"] for t in v2["tables"]}

    assert set(v2_tables) == set(v1_tables)  # same table names
    assert set(v2_tables.values()).isdisjoint(v1_tables.values())  # but new ids

    assert len(v2["relationships"]) == 1
    rel = v2["relationships"][0]
    assert rel["from_table"] in v2_tables.values()
    assert rel["to_table"] in v2_tables.values()
    assert rel["from_table"] not in v1_tables.values()
    assert rel["to_table"] not in v1_tables.values()


async def test_change_request_needs_a_real_reason(estate) -> None:
    with pytest.raises(InvalidRequestError, match="reason"):
        await request_new_version(
            estate["pool"], estate["settings"].graph_name, estate["writer"], estate["family"],
            reason="short", principal=ENGINEER,
        )


async def test_a_second_change_request_is_refused_while_one_is_already_open(estate) -> None:
    await request_new_version(
        estate["pool"], estate["settings"].graph_name, estate["writer"], estate["family"],
        reason="First change request, opening v2.", principal=ENGINEER,
    )
    with pytest.raises(InvalidRequestError):
        await request_new_version(
            estate["pool"], estate["settings"].graph_name, estate["writer"], estate["family"],
            reason="A second one while v2 is still in progress.", principal=ENGINEER,
        )


async def test_read_design_document_default_is_the_latest_version(estate) -> None:
    await request_new_version(
        estate["pool"], estate["settings"].graph_name, estate["writer"], estate["family"],
        reason="Make v2 the current version for every default read.", principal=ENGINEER,
    )
    current = await read_design_document(estate["pool"], estate["settings"].graph_name, estate["family"])
    assert current["version_number"] == 2
    assert current["semantic_model_id"] != estate["v1_semantic_model_id"]


async def test_list_model_versions_shows_both(estate) -> None:
    await request_new_version(
        estate["pool"], estate["settings"].graph_name, estate["writer"], estate["family"],
        reason="Now there should be two versions to list.", principal=ENGINEER,
    )
    versions = await list_model_versions(estate["pool"], estate["settings"].graph_name, estate["family"])
    assert [v["version_number"] for v in versions] == [2, 1]
    assert versions[0]["state"] == "DRAFT"
    assert versions[1]["state"] == "PUBLISHED"
    assert versions[1]["semantic_model_id"] == estate["v1_semantic_model_id"]


# --------------------------------------------------------------------------------- promote


async def test_promoting_v2_deprecates_v1_with_a_date(estate) -> None:
    await request_new_version(
        estate["pool"], estate["settings"].graph_name, estate["writer"], estate["family"],
        reason="Full cycle: v2 through to promotion.", principal=ENGINEER,
    )
    await submit_for_review(estate["pool"], estate["settings"].graph_name, estate["writer"], estate["family"], principal=ENGINEER)
    await approve(
        estate["pool"], estate["settings"].graph_name, estate["writer"], estate["question_store"], estate["family"],
        principal=OWNER, domain_scope=frozenset(), countersigned_by=ENGINEER.value,
        rationale="v2 reviewed and approved.",
    )
    built = await build_family(
        estate["pool"], estate["settings"].graph_name, estate["writer"], estate["artefact_store"],
        estate["target_adapter"], estate["build_store"], estate["conformance_store"], estate["family"],
        gate_decision_id=None, workspace="dev", principal=STEWARD,
    )
    assert built.state == "SUCCEEDED"

    promoted = await _promote(estate, estate["family"])
    assert promoted["version_number"] == 2
    assert promoted["deprecated_semantic_model_id"] == estate["v1_semantic_model_id"]
    assert promoted["deprecated_version_number"] == 1
    assert promoted["published_at"]

    versions = await list_model_versions(estate["pool"], estate["settings"].graph_name, estate["family"])
    by_number = {v["version_number"]: v for v in versions}
    assert by_number[2]["state"] == "PUBLISHED"
    assert by_number[1]["state"] == "DEPRECATED"
    assert by_number[1]["deprecated_at"] is not None

    from astra_graph.cartographer import get_family

    family = await get_family(estate["pool"], estate["settings"].graph_name, estate["family"])
    assert family is not None
    assert family["state"] == "PUBLISHED"


async def test_promote_is_refused_before_built(estate) -> None:
    await request_new_version(
        estate["pool"], estate["settings"].graph_name, estate["writer"], estate["family"],
        reason="v2 opened but not yet built.", principal=ENGINEER,
    )
    with pytest.raises(InvalidRequestError):
        await promote_family(
            estate["pool"], estate["settings"].graph_name, estate["writer"], estate["family"], principal=ENGINEER,
        )


# ---------------------------------------------------------------------------------- the API


@pytest.fixture
async def http_client(estate):
    from httpx import ASGITransport, AsyncClient

    from astra_graph.main import create_app

    app = create_app()
    app.state.modeller = estate["modeller"]
    app.state.question_store = estate["question_store"]
    app.state.artefact_store = estate["artefact_store"]
    app.state.build_store = estate["build_store"]
    app.state.conformance_store = estate["conformance_store"]
    app.state.target_adapter = estate["target_adapter"]
    app.state.target_workspace = "dev"
    app.state.target_workspace_published = "prod"

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://graph-svc") as async_client:
        yield async_client


def _headers(role: str, principal: Principal) -> dict[str, str]:
    from astra_graph.principal import PRINCIPAL_HEADER
    from astra_graph.roles import ROLES_HEADER

    return {PRINCIPAL_HEADER: principal.value, ROLES_HEADER: role}


async def test_request_new_version_over_http(estate, http_client) -> None:
    response = await http_client.post(
        f"/v1/families/{estate['family']}:request-new-version",
        json={"reason": "A design change requested over HTTP."},
        headers=_headers("semantic_model_engineer", ENGINEER),
    )
    assert response.status_code == 200
    assert response.json()["version_number"] == 2


async def test_request_new_version_requires_the_role(estate, http_client) -> None:
    response = await http_client.post(
        f"/v1/families/{estate['family']}:request-new-version",
        json={"reason": "Should be refused before this even matters."},
        headers=_headers("programme_manager", ENGINEER),
    )
    assert response.status_code == 403


async def test_get_versions_over_http_shows_both(estate, http_client) -> None:
    await http_client.post(
        f"/v1/families/{estate['family']}:request-new-version",
        json={"reason": "Opening v2 so the versions list has two rows."},
        headers=_headers("semantic_model_engineer", ENGINEER),
    )
    response = await http_client.get(
        f"/v1/families/{estate['family']}/versions", headers=_headers("programme_manager", ENGINEER),
    )
    assert response.status_code == 200
    versions = response.json()["versions"]
    assert [v["version_number"] for v in versions] == [2, 1]


async def test_get_design_with_semantic_model_id_reads_a_specific_version(estate, http_client) -> None:
    await http_client.post(
        f"/v1/families/{estate['family']}:request-new-version",
        json={"reason": "Opening v2 to read v1 by id afterward."},
        headers=_headers("semantic_model_engineer", ENGINEER),
    )
    response = await http_client.get(
        f"/v1/families/{estate['family']}/design",
        params={"semantic_model_id": estate["v1_semantic_model_id"]},
        headers=_headers("programme_manager", ENGINEER),
    )
    assert response.status_code == 200
    assert response.json()["semantic_model_id"] == estate["v1_semantic_model_id"]
    assert response.json()["version_number"] == 1


async def test_promote_over_http_requires_a_successful_build(estate, http_client) -> None:
    await http_client.post(
        f"/v1/families/{estate['family']}:request-new-version",
        json={"reason": "v2 opened but never built, over HTTP."},
        headers=_headers("semantic_model_engineer", ENGINEER),
    )
    response = await http_client.post(
        f"/v1/families/{estate['family']}:promote", headers=_headers("semantic_model_engineer", ENGINEER),
    )
    assert response.status_code == 400
