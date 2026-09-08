"""Routing a real model defect to the Foundry, against real PostgreSQL + Apache AGE --
story S8.2.2, continuing F8.2/E8.

What only the real stack can answer: that a real KEY_MISSING case whose own grain field
carries no real `Field -> ModelTable` binding really opens a real Foundry change request
(`model_lifecycle.request_new_version`, walked all the way from a real `PROPOSED`
family through `PUBLISHED` the same way `test_integration_versioning.py` already proves
that mechanism); that the identical case, when its own dimension IS really mapped, does
not route at all and falls back to S8.2.1's own already-shipped escalation; that a real
AGGREGATION case whose own grain names a field the family's own real candidate grain does
not falls through the identical real path; that AGGREGATION without a real mismatch is
untouched and still reaches the ordinary pattern/model repair loop; that a family already
past `PUBLISHED` (a change already in flight) is honestly `ALREADY_IN_FOUNDRY`, never a
second, colliding change request; and that a workbook with no real family at all falls
through cleanly rather than crashing.
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
from astra_graph.events import source_for  # noqa: E402
from astra_graph.g2 import PostgresQuestionStore, approve  # noqa: E402
from astra_graph.gateway import null_gateway  # noqa: E402
from astra_graph.graph import AgeGraphRepository, create_pool  # noqa: E402
from astra_graph.graph.queries import NODE_INDEX_TABLE, accessor  # noqa: E402
from astra_graph.ids import new_ulid  # noqa: E402
from astra_graph.lineage import hydrate  # noqa: E402
from astra_graph.mender import InMemoryMenderConfigStore, MenderService  # noqa: E402
from astra_graph.migrations import run as run_migrations  # noqa: E402
from astra_graph.model_lifecycle import (  # noqa: E402
    accept_family,
    request_new_version,
    submit_for_review,
    update_owner,
)
from astra_graph.modeller import Modeller  # noqa: E402
from astra_graph.ontology import EDGE_LABELS, NODE_LABELS  # noqa: E402
from astra_graph.principal import Principal  # noqa: E402
from astra_graph.provenance import PostgresProvenanceStore  # noqa: E402
from astra_graph.tolerance_charter import PostgresToleranceCharterStore  # noqa: E402
from astra_graph.writes import EdgeWrite, GraphWriter, NodeWrite  # noqa: E402

PRINCIPAL = Principal("agent:harvester", run_id="run-harvester")
ENGINEER = Principal("user:sme@artizent.example")
OWNER = Principal("user:owner@client.example")
STEWARD = Principal("agent:steward", run_id="run-build")
PARITY_ENGINEER = Principal("user:parity@artizent.example")


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
    """Function-scoped -- see `test_integration_mender.py`'s own identical fixture for
    why a shared graph would let one test's exceptions pollute another's."""
    config = _settings(f"astra_foundry_{new_ulid()[10:22].lower()}")

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
                "public.artefacts", "public.provenance", "public.build_run", "public.conformance_ruleset",
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
    created = await writer.write_nodes([NodeWrite(type=type_, properties=properties)], principal=PRINCIPAL)
    return str(created[0]["properties"]["id"])


async def _write_props(writer: GraphWriter, type_: str, properties: dict[str, Any]) -> str:
    """The identical write `_write` performs, for a property set carrying a reserved
    Python keyword (`class`) that cannot be spelled as a `**kwargs` name at a call site."""
    created = await writer.write_nodes([NodeWrite(type=type_, properties=properties)], principal=PRINCIPAL)
    return str(created[0]["properties"]["id"])


async def _edge(writer: GraphWriter, type_: str, source: str, target: str, **props: Any) -> None:
    await writer.write_edge(
        EdgeWrite(type=type_, from_id=source, to_id=target, properties=props), principal=PRINCIPAL
    )


async def _promote(estate: dict[str, Any], family_id: str) -> dict[str, Any]:
    """`test_integration_versioning.py`'s own real orchestration, reused verbatim: deploy
    the already-committed build to "prod" before calling `promote_family`."""
    from astra_graph.model_lifecycle import promote_family

    latest_build = await estate["build_store"].latest(family_id)
    assert latest_build is not None and latest_build.state == "SUCCEEDED"
    deployment = await estate["target_adapter"].deploy(workspace="prod", git_ref=latest_build.git_ref)
    assert deployment.ok
    return await promote_family(
        estate["pool"], estate["settings"].graph_name, estate["writer"], family_id, principal=ENGINEER,
    )


@pytest.fixture
async def estate(settings: Settings, tmp_path: Path):
    """One workbook, one sheet (`rows_shelf=["Desk"]`), one real `ModelFamily` walked all
    the way from `PROPOSED` to `PUBLISHED` v1 -- the identical real state-machine walk
    `test_integration_versioning.py`'s own estate fixture already proves, reused here as
    the starting point every test in this file needs. `Desk` is deliberately left with no
    real `Field -> ModelTable` binding: the KEY_MISSING-with-real-evidence scenario every
    test in this file's own first section needs; the "field really is mapped" negative
    control adds its own binding on top, per test.
    """
    pool = await create_pool(settings)
    try:
        repository = AgeGraphRepository(pool, graph_name=settings.graph_name)
        writer = GraphWriter(repository, event_source=source_for(settings.graph_name))
        provenance_store = PostgresProvenanceStore(pool, graph_name=settings.graph_name)
        question_store = PostgresQuestionStore(pool, graph_name=settings.graph_name)
        artefact_store = PostgresArtefactStore(pool, graph_name=settings.graph_name)
        build_store = PostgresBuildStore(pool, graph_name=settings.graph_name)
        conformance_store = PostgresConformanceRulesetStore(pool, graph_name=settings.graph_name)
        charter_store = PostgresToleranceCharterStore(pool, graph_name=settings.graph_name)
        target_adapter = FixtureTargetAdapter(repo_path=tmp_path / "repo", workspace_root=tmp_path / "workspaces")
        modeller = Modeller(pool, graph_name=settings.graph_name, writer=writer, provenance_store=provenance_store)
        suffix = new_ulid()[10:18].lower()

        site = await _write(writer, "Site", luid=f"s-{suffix}", name=f"rqa-{suffix}")
        project = await _write(writer, "Project", luid=f"p-{suffix}", name="Risk Core")
        await _edge(writer, "CONTAINS", site, project)

        base_table = await _write(writer, "Table", name=f"positions_{suffix}", schema="risk", row_estimate=5_000_000)
        dim_table = await _write(writer, "Table", name=f"desk_{suffix}", schema="risk", row_estimate=40)
        connection = await _write_props(writer, "Connection", {"class": "snowflake", "server": "warehouse", "db": "risk"})
        await _edge(writer, "CONNECTS_TO", connection, base_table, join_clause=None)
        await _edge(
            writer, "CONNECTS_TO", connection, dim_table,
            join_clause=f"positions_{suffix}.desk_id = desk_{suffix}.id",
        )

        book = await _write(writer, "Workbook", luid=f"wb-{suffix}", name="Daily VaR", revision="1")
        await _edge(writer, "CONTAINS", project, book)

        datasource = await _write(
            writer, "Datasource", name="VaR ds", type="published", luid=f"ds-{suffix}",
            extract_flag=True, refresh_schedule="daily",
        )
        await _edge(writer, "CONNECTS_TO", datasource, connection)
        desk = await _write(writer, "Field", name="Desk", datatype="string", role="dimension")
        await _edge(writer, "HAS_FIELD", datasource, desk)
        margin_calc = await _write(
            writer, "CalculatedField", name="MarginCalc", formula="SUM([Margin])",
            formula_ast={"kind": "AGGREGATE", "name": "SUM", "children": [
                {"kind": "REFERENCE", "name": "Margin", "children": []},
            ]},
        )
        await _edge(writer, "HAS_FIELD", datasource, margin_calc)

        sheet = await _write(
            writer, "Worksheet", name="Bar sheet", mark_type="bar",
            rows_shelf=["Desk"], cols_shelf=["MarginCalc"], marks_shelf=[],
        )
        await _edge(writer, "CONTAINS", book, sheet)
        await _edge(writer, "USES_DATASOURCE", sheet, datasource)

        family = await _write_props(writer, "ModelFamily", {
            "name": f"Risk Positions {suffix}", "state": "PROPOSED", "grain": "Desk", "conformed_dims": [],
        })
        await _edge(writer, "IN_FAMILY", book, family, confidence=1.0)

        await modeller.run(family, principal=PRINCIPAL)
        await accept_family(pool, settings.graph_name, writer, family, principal=ENGINEER)
        await update_owner(pool, settings.graph_name, writer, family, owner="owner@client.example", principal=ENGINEER)
        await submit_for_review(pool, settings.graph_name, writer, family, principal=ENGINEER)
        await approve(
            pool, settings.graph_name, writer, question_store, family,
            principal=OWNER, domain_scope=frozenset(), countersigned_by=ENGINEER.value,
            rationale="Reviewed and approved.",
        )

        estate_dict: dict[str, Any] = {
            "pool": pool, "settings": settings, "writer": writer, "artefact_store": artefact_store,
            "provenance_store": provenance_store, "charter_store": charter_store, "target_adapter": target_adapter,
            "build_store": build_store, "workbook": book, "sheet": sheet, "desk": desk,
            "margin_calc": margin_calc, "family": family,
        }

        built = await build_family(
            pool, settings.graph_name, writer, artefact_store, target_adapter, build_store,
            conformance_store, family, gate_decision_id=None, workspace="dev", principal=STEWARD,
        )
        assert built.state == "SUCCEEDED"
        promoted = await _promote(estate_dict, family)
        assert promoted["version_number"] == 1

        yield estate_dict
    finally:
        await pool.close()


async def _write_case(estate: dict[str, Any], *, grain: tuple[str, ...], sheet_ref: str | None = None) -> str:
    """A real `ParityCase` naming `grain` -- no real execution/diff is needed anywhere in
    this file, since `detect_model_defect` reads only `ParityCase.grain`/`.sheet_ref`,
    never a `Verdict`'s own evidence (see `foundry_routing.py`'s own docstring)."""
    return await _write(
        estate["writer"], "ParityCase", mu_ref=estate["workbook"], sheet_ref=sheet_ref or estate["sheet"],
        grain=list(grain), measures=["MarginCalc"], filter_ctx={}, param_values={},
        case_key=f"case_{new_ulid()}", state="EXECUTED",
    )


async def _open_exception(estate: dict[str, Any], *, failure_class: str, case_ids: list[str]) -> str:
    return await _write_props(estate["writer"], "ExceptionCase", {
        "mu_ref": estate["workbook"], "class": failure_class, "state": "OPEN",
        "artefact_ref": estate["margin_calc"], "case_refs": case_ids,
        "classification_signals": {"note": "test-seeded"},
    })


def _service(estate: dict[str, Any]) -> MenderService:
    return MenderService(
        estate["pool"], graph_name=estate["settings"].graph_name, writer=estate["writer"],
        artefact_store=estate["artefact_store"], provenance_store=estate["provenance_store"],
        gateway=null_gateway(), target_adapter=estate["target_adapter"],
        config_store=InMemoryMenderConfigStore(), charter_store=estate["charter_store"],
    )


async def _exception_case_properties(pool: asyncpg.Pool, graph_name: str, case_id: str) -> dict[str, Any]:
    async with pool.acquire() as conn:
        hydrated = await hydrate(conn, graph_name, "ExceptionCase", [case_id])
    return hydrated[case_id]


async def _mender_passes(pool: asyncpg.Pool, graph_name: str, exception_case_id: str) -> list[dict[str, Any]]:
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            f"""SELECT id FROM {NODE_INDEX_TABLE}
                 WHERE graph = $1 AND kind = 'node' AND label = 'MenderPass' AND retired_at IS NULL""",
            graph_name,
        )
        passes = await hydrate(conn, graph_name, "MenderPass", [row["id"] for row in rows])
    return [
        {"id": pid, **props} for pid, props in passes.items() if props.get("exception_case_ref") == exception_case_id
    ]


# ---------------------------------------------------------------- KEY_MISSING, real evidence


async def test_key_missing_with_an_unmapped_dimension_routes_to_the_foundry(estate) -> None:
    """`Desk` carries no real `Field -> ModelTable` binding -- the estate fixture's own
    default -- so a KEY_MISSING case naming it as its own grain is real, structural
    evidence of a missing dimension member."""
    case_id = await _write_case(estate, grain=("Desk",))
    exception_id = await _open_exception(estate, failure_class="KEY_MISSING", case_ids=[case_id])

    result = await _service(estate).mend(exception_id, workspace="dev", principal=PARITY_ENGINEER)
    assert result["outcome"] == "routed_to_foundry"
    assert result["route_result"]["outcome"] == "ROUTED_TO_FOUNDRY"
    assert result["route_result"]["family_id"] == estate["family"]
    assert result["route_result"]["foundry_request_ref"]
    assert result["passes_consumed"] == 1

    properties = await _exception_case_properties(estate["pool"], estate["settings"].graph_name, exception_id)
    assert properties["state"] == "BLOCKED"
    assert properties["family_ref"] == estate["family"]
    assert properties["foundry_request_ref"] == result["route_result"]["foundry_request_ref"]
    assert properties["decision"] == "MODEL_DEFECT_FOUNDRY"

    passes = await _mender_passes(estate["pool"], estate["settings"].graph_name, exception_id)
    assert len(passes) == 1
    assert passes[0]["strategy"] == "ROUTE_TO_FOUNDRY"
    assert passes[0]["result"] == "ROUTED_TO_FOUNDRY"
    assert passes[0].get("measure_ref") is None

    # The real change request itself: a fresh DRAFT v2, the family moved off PUBLISHED.
    async with estate["pool"].acquire() as conn:
        family_props = (await hydrate(conn, estate["settings"].graph_name, "ModelFamily", [estate["family"]]))[estate["family"]]
    assert family_props["state"] == "DRAFT"


async def test_the_mender_never_writes_a_measure_or_touches_tmdl_when_routing(estate) -> None:
    """The AC's own second bullet, proven directly: a ROUTE_TO_FOUNDRY pass produces no
    Measure and the workbook's own MarginCalc keeps whatever MAPS_TO it already had (none,
    in this estate) -- routing is a graph-only fact, never a TMDL/Git write."""
    case_id = await _write_case(estate, grain=("Desk",))
    exception_id = await _open_exception(estate, failure_class="KEY_MISSING", case_ids=[case_id])
    await _service(estate).mend(exception_id, workspace="dev", principal=PARITY_ENGINEER)

    async with estate["pool"].acquire() as conn:
        rows = await conn.fetch(
            f"""SELECT id FROM {NODE_INDEX_TABLE}
                 WHERE graph = $1 AND kind = 'node' AND label = 'Measure' AND retired_at IS NULL""",
            estate["settings"].graph_name,
        )
    assert rows == []


# -------------------------------------------------------------- KEY_MISSING, no evidence


async def test_key_missing_with_a_mapped_dimension_does_not_route(estate) -> None:
    """`Desk` really is mapped this time -- real graph evidence rules out a model
    defect, so the case falls back to S8.2.1's own already-shipped unconditional
    escalation instead of routing anywhere."""
    model_table = await _write(estate["writer"], "ModelTable", name="dim_desk", mode="import", family_ref=estate["family"])
    await _edge(estate["writer"], "MAPS_TO", estate["desk"], model_table)

    case_id = await _write_case(estate, grain=("Desk",))
    exception_id = await _open_exception(estate, failure_class="KEY_MISSING", case_ids=[case_id])

    result = await _service(estate).mend(exception_id, workspace="dev", principal=PARITY_ENGINEER)
    assert result["outcome"] == "escalated"
    assert result["reason"] == "key_missing_model_defect"

    properties = await _exception_case_properties(estate["pool"], estate["settings"].graph_name, exception_id)
    assert properties["state"] == "OPEN"
    assert properties.get("family_ref") is None

    passes = await _mender_passes(estate["pool"], estate["settings"].graph_name, exception_id)
    assert len(passes) == 1
    assert passes[0]["strategy"] == "ESCALATE_IMMEDIATE"
    assert passes[0]["result"] == "KEY_MISSING_MODEL_DEFECT"


# ------------------------------------------------------------------------- AGGREGATION


async def test_aggregation_with_a_grain_the_family_does_not_name_routes_to_the_foundry(estate) -> None:
    """The family's own real candidate grain is just `Desk` (the estate fixture's own
    default) -- a case naming `Region` too is real evidence the model's own grain is
    coarser than this report needs."""
    case_id = await _write_case(estate, grain=("Desk", "Region"))
    exception_id = await _open_exception(estate, failure_class="AGGREGATION", case_ids=[case_id])

    result = await _service(estate).mend(exception_id, workspace="dev", principal=PARITY_ENGINEER)
    assert result["outcome"] == "routed_to_foundry"
    assert result["route_result"]["outcome"] == "ROUTED_TO_FOUNDRY"

    properties = await _exception_case_properties(estate["pool"], estate["settings"].graph_name, exception_id)
    assert properties["state"] == "BLOCKED"
    assert properties["family_ref"] == estate["family"]


async def test_aggregation_within_the_family_grain_is_untouched(estate) -> None:
    """A case naming only `Desk` -- already part of the family's own real grain -- is
    not a model defect; S8.2.1's own ordinary pattern/model loop runs exactly as it did
    before this story, never routed anywhere."""
    case_id = await _write_case(estate, grain=("Desk",))
    exception_id = await _open_exception(estate, failure_class="AGGREGATION", case_ids=[case_id])

    result = await _service(estate).mend(exception_id, workspace="dev", principal=PARITY_ENGINEER)
    assert result["outcome"] == "escalated"  # S8.2.1's own real path: NO_PATTERN_MATCH, then MODEL_UNAVAILABLE
    assert [p["strategy"] for p in result["passes"]] == ["PATTERN", "MODEL"]

    properties = await _exception_case_properties(estate["pool"], estate["settings"].graph_name, exception_id)
    assert properties["state"] == "OPEN"


# ------------------------------------------------------------------- family not PUBLISHED


async def test_a_second_model_defect_on_a_family_already_off_published_joins_it(estate) -> None:
    """The first routing already moved the family to `DRAFT` v2 -- a second, independent
    model defect on the same family must not open a colliding third version; it is
    honestly `ALREADY_IN_FOUNDRY`, still marked BLOCKED on the real, same family."""
    first_case = await _write_case(estate, grain=("Desk",))
    first_exception = await _open_exception(estate, failure_class="KEY_MISSING", case_ids=[first_case])
    first_result = await _service(estate).mend(first_exception, workspace="dev", principal=PARITY_ENGINEER)
    assert first_result["route_result"]["outcome"] == "ROUTED_TO_FOUNDRY"

    second_case = await _write_case(estate, grain=("Desk", "Region"))
    second_exception = await _open_exception(estate, failure_class="AGGREGATION", case_ids=[second_case])
    second_result = await _service(estate).mend(second_exception, workspace="dev", principal=PARITY_ENGINEER)

    assert second_result["outcome"] == "routed_to_foundry"
    assert second_result["route_result"]["outcome"] == "ALREADY_IN_FOUNDRY"
    assert second_result["route_result"]["foundry_request_ref"] is None
    assert second_result["route_result"]["family_state"] == "DRAFT"

    properties = await _exception_case_properties(estate["pool"], estate["settings"].graph_name, second_exception)
    assert properties["state"] == "BLOCKED"
    assert properties["family_ref"] == estate["family"]
    assert properties.get("foundry_request_ref") is None


async def test_manually_requesting_a_change_first_is_also_honoured(estate) -> None:
    """The identical `ALREADY_IN_FOUNDRY` outcome when a *human* Semantic Model Engineer
    (not the Mender) already opened the change request through the real, existing
    `request_new_version` route -- proving this story's own check is a real read of the
    family's own live state, not a Mender-only bookkeeping flag."""
    await request_new_version(
        estate["pool"], estate["settings"].graph_name, estate["writer"], estate["family"],
        reason="A Semantic Model Engineer already opened this independently.", principal=ENGINEER,
    )
    case_id = await _write_case(estate, grain=("Desk",))
    exception_id = await _open_exception(estate, failure_class="KEY_MISSING", case_ids=[case_id])

    result = await _service(estate).mend(exception_id, workspace="dev", principal=PARITY_ENGINEER)
    assert result["route_result"]["outcome"] == "ALREADY_IN_FOUNDRY"


# ------------------------------------------------------------------------- no family


async def test_a_workbook_with_no_family_falls_through_cleanly(estate) -> None:
    """A second workbook, never clustered into any family -- `detect_model_defect` must
    return a real, honest `None` (nothing to check evidence against), not raise; the
    case falls back to S8.2.1's own unconditional KEY_MISSING escalation."""
    unclustered_book = await _write(estate["writer"], "Workbook", luid=f"wb-unclustered-{new_ulid()[10:18]}", name="Other Report", revision="1")
    sheet = await _write(
        estate["writer"], "Worksheet", name="Other sheet", mark_type="bar",
        rows_shelf=["Desk"], cols_shelf=["MarginCalc"], marks_shelf=[],
    )
    await _edge(estate["writer"], "CONTAINS", unclustered_book, sheet)

    case_id = await _write(
        estate["writer"], "ParityCase", mu_ref=unclustered_book, sheet_ref=sheet, grain=["Desk"],
        measures=["MarginCalc"], filter_ctx={}, param_values={}, case_key=f"case_{new_ulid()}", state="EXECUTED",
    )
    exception_id = await _write_props(estate["writer"], "ExceptionCase", {
        "mu_ref": unclustered_book, "class": "KEY_MISSING", "state": "OPEN",
        "artefact_ref": estate["margin_calc"], "case_refs": [case_id],
        "classification_signals": {"note": "test-seeded"},
    })

    result = await _service(estate).mend(exception_id, workspace="dev", principal=PARITY_ENGINEER)
    assert result["outcome"] == "escalated"
    assert result["reason"] == "key_missing_model_defect"
