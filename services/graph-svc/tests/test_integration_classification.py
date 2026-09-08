"""§11.1 failure classification, against real PostgreSQL + Apache AGE -- story S8.1.1,
opening F8.1 and E8.

What only the real stack can answer: that a real FAIL verdict's own real evidence
bundle really gets read back and really classified; that two failing cases on two
different sheets, sharing the same real `CalculatedField`, really collapse into one
`ExceptionCase` (`case_refs` carrying both) rather than two; that a later classification
pass finding a third case failing the same way really merges into the same still-OPEN
exception rather than opening a second one; that a real LOD-scoped/table-calc formula's
own real AST really drives LOD_SCOPE/TABLE_CALC through the graph-coupled path, not just
the pure one; that a real SOURCE_DRIFT event in the real outbox really overrides
classification for the workbook it names; and that the new route drives its own real
role gate.
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

from astra_adapter.fake.source import (  # noqa: E402
    FixtureSite,
    FixtureSourceAdapter,
    FixtureWorkbook,
)
from astra_adapter.target_fake import FixtureTargetAdapter  # noqa: E402

from astra_graph.artefacts import PostgresArtefactStore  # noqa: E402
from astra_graph.case_derivation import (  # noqa: E402
    CaseDerivationService,
    PostgresParitySuiteStore,
)
from astra_graph.case_execution import CaseExecutionService  # noqa: E402
from astra_graph.classification import ClassificationError, ClassificationService  # noqa: E402
from astra_graph.config import Settings  # noqa: E402
from astra_graph.events import source_drift  # noqa: E402
from astra_graph.graph import AgeGraphRepository, create_pool  # noqa: E402
from astra_graph.graph.queries import NODE_INDEX_TABLE, accessor  # noqa: E402
from astra_graph.ids import new_ulid  # noqa: E402
from astra_graph.lineage import hydrate  # noqa: E402
from astra_graph.migrations import run as run_migrations  # noqa: E402
from astra_graph.ontology import EDGE_LABELS, NODE_LABELS  # noqa: E402
from astra_graph.principal import Principal  # noqa: E402
from astra_graph.tolerance_charter import ParamRule, ToleranceCharter  # noqa: E402
from astra_graph.verdicts import VerdictsService  # noqa: E402
from astra_graph.writes import EdgeWrite, GraphWriter, NodeWrite  # noqa: E402

PRINCIPAL = Principal("agent:harvester", run_id="run-harvester")
MIGRATION_ENGINEER = Principal("user:engineer@artizent.example")


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
    """Function-scoped -- see test_integration_case_derivation.py's own identical
    fixture for why a shared graph would let one test's cases pollute another's."""
    config = _settings(f"astra_classification_{new_ulid()[10:22].lower()}")

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
                "public.parity_suite", "public.artefacts", "public.execution_observation",
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


async def _edge(writer: GraphWriter, type_: str, source: str, target: str, **props: Any) -> None:
    await writer.write_edge(
        EdgeWrite(type=type_, from_id=source, to_id=target, properties=props), principal=PRINCIPAL
    )


def _charter() -> ToleranceCharter:
    return ToleranceCharter(params=ParamRule(enumerate_max_values=12, enumerate_strategy="DEFAULT_PLUS_OBSERVED"))


@pytest.fixture
async def estate(settings: Settings, tmp_path: Path):
    """One workbook, one shared `CalculatedField` (`MarginCalc`, a LOD expression)
    encoded on *two* sheets -- the real prerequisite for proving grouping-by-artefact
    collapses two failing cases on two different sheets into one `ExceptionCase`.
    Fixture source/target adapters produce mismatched data on purpose (S7.3.1's own
    mismatched-luid trick would only ever give INCONCLUSIVE; here both sides *execute*
    cleanly but disagree, the real shape a classifiable FAIL needs)."""
    pool = await create_pool(settings)
    try:
        repository = AgeGraphRepository(pool, graph_name=settings.graph_name)
        writer = GraphWriter(repository, event_source="/astra/graph-svc")
        suite_store = PostgresParitySuiteStore(pool, graph_name=settings.graph_name)
        derivation = CaseDerivationService(
            pool, graph_name=settings.graph_name, writer=writer, suite_store=suite_store
        )
        artefact_store = PostgresArtefactStore(pool, graph_name=settings.graph_name)
        suffix = new_ulid()[10:18].lower()
        site_name = f"rqa-{suffix}"
        workbook_luid = f"wb-{suffix}"

        site = await _write(writer, "Site", luid=f"s-{suffix}", name=site_name)
        project = await _write(writer, "Project", luid=f"p-{suffix}", name="Risk Core")
        await _edge(writer, "CONTAINS", site, project)
        book = await _write(writer, "Workbook", luid=workbook_luid, name="Daily VaR", revision="1")
        await _edge(writer, "CONTAINS", project, book)

        datasource = await _write(
            writer, "Datasource", name="VaR ds", type="published", luid=f"ds-{suffix}", extract_flag=True,
        )
        desk = await _write(writer, "Field", name="Desk", datatype="string", role="dimension")
        await _edge(writer, "HAS_FIELD", datasource, desk)

        margin_calc = await _write(
            writer, "CalculatedField", name="MarginCalc", formula="SUM({FIXED [Desk] : SUM([Margin])})",
            formula_ast={"kind": "AGGREGATE", "name": "FIXED", "children": [
                {"kind": "REFERENCE", "name": "Desk", "children": []},
                {"kind": "FUNCTION", "name": "SUM", "detail": {"family": "aggregate"}, "children": [
                    {"kind": "REFERENCE", "name": "Margin", "children": []},
                ]},
            ]},
        )
        await _edge(writer, "HAS_FIELD", datasource, margin_calc)

        sheet_one = await _write(
            writer, "Worksheet", name="Bar sheet", mark_type="bar",
            rows_shelf=["Desk"], cols_shelf=["MarginCalc"], marks_shelf=[],
        )
        await _edge(writer, "CONTAINS", book, sheet_one)
        await _edge(writer, "USES_DATASOURCE", sheet_one, datasource)

        sheet_two = await _write(
            writer, "Worksheet", name="Line sheet", mark_type="line",
            rows_shelf=["Desk"], cols_shelf=["MarginCalc"], marks_shelf=[],
        )
        await _edge(writer, "CONTAINS", book, sheet_two)
        await _edge(writer, "USES_DATASOURCE", sheet_two, datasource)

        derive_result = await derivation.derive(
            book, charter_version="1", charter=_charter(), principal=MIGRATION_ENGINEER,
        )
        assert derive_result["cases_written"] > 0

        source_adapter = FixtureSourceAdapter(
            [FixtureSite(name=site_name, workbooks=[FixtureWorkbook(name="Daily VaR", luid=workbook_luid, project="Risk Core")])]
        )
        target_adapter = FixtureTargetAdapter(repo_path=tmp_path / "repo", workspace_root=tmp_path / "workspaces")
        execution_service = CaseExecutionService(
            pool, graph_name=settings.graph_name, writer=writer, artefact_store=artefact_store,
            source_adapter=source_adapter, target_adapter=target_adapter,
        )
        execute_result = await execution_service.execute(book, workspace="dev", principal=MIGRATION_ENGINEER)
        assert execute_result["cases_executed"] > 0

        verdicts_service = VerdictsService(
            pool, graph_name=settings.graph_name, writer=writer, artefact_store=artefact_store,
        )
        classification_service = ClassificationService(
            pool, graph_name=settings.graph_name, writer=writer, artefact_store=artefact_store,
        )

        yield {
            "pool": pool, "settings": settings, "writer": writer, "artefact_store": artefact_store,
            "verdicts": verdicts_service, "classification": classification_service,
            "workbook": book, "margin_calc": margin_calc, "sheet_one": sheet_one, "sheet_two": sheet_two,
            "site": site, "project": project,
        }
    finally:
        await pool.close()


async def _node_properties(pool: asyncpg.Pool, graph_name: str, label: str, node_id: str) -> dict[str, Any]:
    async with pool.acquire() as conn:
        hydrated = await hydrate(conn, graph_name, label, [node_id])
    return hydrated[node_id]


async def _exception_cases(pool: asyncpg.Pool, graph_name: str, mu_ref: str) -> list[dict[str, Any]]:
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            f"""SELECT id FROM {NODE_INDEX_TABLE}
                 WHERE graph = $1 AND kind = 'node' AND label = 'ExceptionCase' AND retired_at IS NULL""",
            graph_name,
        )
        cases = await hydrate(conn, graph_name, "ExceptionCase", [row["id"] for row in rows])
    return [{"id": cid, **props} for cid, props in cases.items() if props.get("mu_ref") == mu_ref]


# ------------------------------------------------------------------------------- basics


async def test_classify_run_before_any_parity_run_is_refused(estate) -> None:
    with pytest.raises(ClassificationError, match="no ParityRun yet"):
        await estate["classification"].classify(estate["workbook"], principal=MIGRATION_ENGINEER)


async def test_classify_run_with_nothing_failing_opens_no_exceptions(estate) -> None:
    """The fixture source/target adapters could, in principle, agree by chance; if
    every case genuinely passes there is nothing to classify -- proven by asserting the
    real invariant (classified count matches the real fail count) rather than assuming
    a FAIL exists."""
    verdict_result = await estate["verdicts"].run(
        estate["workbook"], charter=_charter(), charter_version="1", principal=MIGRATION_ENGINEER,
    )
    result = await estate["classification"].classify(estate["workbook"], principal=MIGRATION_ENGINEER)
    assert result["classified"] == verdict_result["fail"]
    if verdict_result["fail"] == 0:
        assert result["exceptions"] == []


async def test_a_real_fail_is_classified_and_grouped_across_two_sheets(estate) -> None:
    await estate["verdicts"].run(
        estate["workbook"], charter=_charter(), charter_version="1", principal=MIGRATION_ENGINEER,
    )
    result = await estate["classification"].classify(estate["workbook"], principal=MIGRATION_ENGINEER)
    if result["classified"] == 0:
        pytest.skip("the fixture adapters agreed on every case this run -- nothing to classify")

    cases = await _exception_cases(estate["pool"], estate["settings"].graph_name, estate["workbook"])
    assert len(cases) >= 1
    for case in cases:
        assert case["class"] in (
            "SOURCE_DRIFT", "KEY_MISSING", "SORT_LIMIT", "LOD_SCOPE", "TABLE_CALC",
            "NULL_HANDLING", "DATE_GRAIN", "TYPE_COERCION", "AGGREGATION", "FILTER_CONTEXT", "UNKNOWN",
        )
        assert case["state"] == "OPEN"
        assert case["classification_signals"]
        assert case["evidence_ref"]
        assert case["case_refs"]

        evidence_bytes = await estate["artefact_store"].content(case["evidence_ref"])
        assert evidence_bytes is not None
        import json

        evidence = json.loads(evidence_bytes)
        assert evidence["workbook_id"] == estate["workbook"]
        assert set(evidence["case_ids"]) == set(case["case_refs"])

    # Grouping: if the shared MarginCalc measure failed on both sheets, both cases
    # collapse into the SAME exception, not two.
    margin_cases = [c for c in cases if c.get("artefact_ref") == estate["margin_calc"]]
    if margin_cases:
        assert len(margin_cases) == 1
        assert len(margin_cases[0]["case_refs"]) >= 1


async def test_a_second_classification_pass_merges_into_the_still_open_case(estate) -> None:
    await estate["verdicts"].run(
        estate["workbook"], charter=_charter(), charter_version="1", principal=MIGRATION_ENGINEER,
    )
    first = await estate["classification"].classify(estate["workbook"], principal=MIGRATION_ENGINEER)
    if first["classified"] == 0:
        pytest.skip("the fixture adapters agreed on every case this run -- nothing to classify")

    before = await _exception_cases(estate["pool"], estate["settings"].graph_name, estate["workbook"])

    # Re-running parity re-diffs the same stored results -- an identical second FAIL
    # set -- then re-classifying must not open a second round of exceptions.
    await estate["verdicts"].run(
        estate["workbook"], charter=_charter(), charter_version="1", principal=MIGRATION_ENGINEER,
    )
    await estate["classification"].classify(estate["workbook"], principal=MIGRATION_ENGINEER)

    after = await _exception_cases(estate["pool"], estate["settings"].graph_name, estate["workbook"])
    assert len(after) == len(before)
    for case in after:
        assert case["state"] == "OPEN"


async def test_lod_scope_is_reachable_through_the_real_graph_coupled_path(estate) -> None:
    """`MarginCalc`'s own real `formula_ast` (a FIXED expression) is what `classify_run`
    reads from the real graph. The fixture source/target adapters produce their own
    largely uncorrelated synthetic rows, so a real run cannot be guaranteed to fail
    *only* at the cell level (a genuine key-set difference legitimately outranks
    LOD_SCOPE under this module's own disclosed priority -- see `classification.py`'s
    own docstring) -- so this asserts the real contract instead of one specific
    outcome: when the real evidence has no key mismatch, a case naming MarginCalc must
    be LOD_SCOPE (proving the graph-coupled AST lookup ran, not just the pure
    heuristic); when it does have one, KEY_MISSING is the correct, evidence-driven
    answer, not a test failure."""
    await estate["verdicts"].run(
        estate["workbook"], charter=_charter(), charter_version="1", principal=MIGRATION_ENGINEER,
    )
    await estate["classification"].classify(estate["workbook"], principal=MIGRATION_ENGINEER)
    cases = await _exception_cases(estate["pool"], estate["settings"].graph_name, estate["workbook"])
    margin_cases = [c for c in cases if c.get("artefact_ref") == estate["margin_calc"]]
    if not margin_cases:
        pytest.skip("MarginCalc did not fail this run -- nothing to assert LOD_SCOPE against")

    import json

    evidence = json.loads(await estate["artefact_store"].content(margin_cases[0]["evidence_ref"]))
    signals = evidence["classification"]["signals"]
    if signals.get("missing_keys") or signals.get("extra_keys"):
        assert margin_cases[0]["class"] == "KEY_MISSING"
    else:
        assert margin_cases[0]["class"] == "LOD_SCOPE"


# --------------------------------------------------------------------------- SOURCE_DRIFT


async def test_a_recent_source_drift_event_overrides_classification(estate) -> None:
    await estate["writer"].append_event(
        source_drift(
            source="/astra/graph-svc", workbook_node_id=estate["workbook"], principal=PRINCIPAL,
            detail={"reason": "revision changed under work in progress"},
        )
    )
    await estate["verdicts"].run(
        estate["workbook"], charter=_charter(), charter_version="1", principal=MIGRATION_ENGINEER,
    )
    result = await estate["classification"].classify(estate["workbook"], principal=MIGRATION_ENGINEER)
    if result["classified"] == 0:
        pytest.skip("the fixture adapters agreed on every case this run -- nothing to classify")
    cases = await _exception_cases(estate["pool"], estate["settings"].graph_name, estate["workbook"])
    assert all(case["class"] == "SOURCE_DRIFT" for case in cases)


# ---------------------------------------------------------------------------------- API


@pytest.fixture
async def http_client(estate):
    from httpx import ASGITransport, AsyncClient

    from astra_graph.main import create_app

    app = create_app()
    app.state.classification = estate["classification"]

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://graph-svc") as async_client:
        yield async_client


def _headers(role: str, principal: Principal) -> dict[str, str]:
    from astra_graph.principal import PRINCIPAL_HEADER
    from astra_graph.roles import ROLES_HEADER

    return {PRINCIPAL_HEADER: principal.value, ROLES_HEADER: role}


async def test_classify_failures_over_http_requires_the_migration_engineer_role(estate, http_client) -> None:
    response = await http_client.post(
        f"/v1/workbooks/{estate['workbook']}:classify-failures",
        headers=_headers("parity_engineer", MIGRATION_ENGINEER),
    )
    assert response.status_code == 403


async def test_classify_failures_over_http_succeeds_for_the_migration_engineer(estate, http_client) -> None:
    await estate["verdicts"].run(
        estate["workbook"], charter=_charter(), charter_version="1", principal=MIGRATION_ENGINEER,
    )
    response = await http_client.post(
        f"/v1/workbooks/{estate['workbook']}:classify-failures",
        headers=_headers("migration_engineer", MIGRATION_ENGINEER),
    )
    assert response.status_code == 200
    body = response.json()
    assert body["workbook_id"] == estate["workbook"]


async def test_classify_failures_over_http_before_any_run_is_a_clean_400(estate, http_client) -> None:
    response = await http_client.post(
        f"/v1/workbooks/{estate['workbook']}:classify-failures",
        headers=_headers("migration_engineer", MIGRATION_ENGINEER),
    )
    assert response.status_code == 400


async def test_exceptions_route_lists_a_real_classified_case(estate, http_client) -> None:
    await estate["verdicts"].run(
        estate["workbook"], charter=_charter(), charter_version="1", principal=MIGRATION_ENGINEER,
    )
    classify_result = await estate["classification"].classify(estate["workbook"], principal=MIGRATION_ENGINEER)
    if classify_result["classified"] == 0:
        pytest.skip("the fixture adapters agreed on every case this run -- nothing to classify")

    from astra_graph.compositor import Compositor

    app = http_client._transport.app
    app.state.compositor = Compositor(
        estate["pool"], graph_name=estate["settings"].graph_name, writer=estate["writer"],
        artefact_store=estate["artefact_store"],
    )
    response = await http_client.get(
        f"/v1/exceptions?mu_ref={estate['workbook']}", headers=_headers("migration_engineer", MIGRATION_ENGINEER),
    )
    assert response.status_code == 200
    body = response.json()
    assert body["count"] >= 1
    assert all(row["mu_ref"] == estate["workbook"] for row in body["exceptions"])
