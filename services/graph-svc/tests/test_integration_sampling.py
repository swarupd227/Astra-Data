"""§10.4 sampling, against real PostgreSQL + Apache AGE -- story S7.5.1, opening F7.5.

What only the real stack can answer: that a real diff run, under a charter forcing
sampling, really writes `ParityCase.sampled`/`.sample_size`/`.sampling_seed`/
`.sampling_strategy` (§10.4's own literal "sampling is recorded on the ParityCase") via
a real `set_node_properties` merge, and really writes `Verdict.sampled` alongside the
verdict itself; that the evidence bundle really carries the same sampling facts; that a
case's own sampling record really reflects only its *most recent* run (a later,
unsampled run really clears it, not leaves a stale `True` behind); and that
`GET .../parity-run` really surfaces `sampled` on a real verdict row over HTTP, ready for
the Parity Dashboard's own SAMPLED label (S7.4.2).

The pure stratification algorithm itself -- coverage guarantees, top-N-by-measure
inclusion, seed reproducibility -- is `tests/test_sampling.py`'s own job, with richer
result sets than this estate's small fixture data produces. Here, `full_compare_max_rows`
is forced to 0 so even this fixture's own handful of rows sample for real, proving the
write path end to end rather than the algorithm's own internals a second time.
"""

from __future__ import annotations

import asyncio
import json
import os
import threading
from collections.abc import Callable, Coroutine
from dataclasses import replace
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
from astra_graph.config import Settings  # noqa: E402
from astra_graph.events import source_for  # noqa: E402
from astra_graph.graph import AgeGraphRepository, create_pool  # noqa: E402
from astra_graph.graph.queries import accessor  # noqa: E402
from astra_graph.ids import new_ulid  # noqa: E402
from astra_graph.lineage import hydrate  # noqa: E402
from astra_graph.migrations import run as run_migrations  # noqa: E402
from astra_graph.ontology import EDGE_LABELS, NODE_LABELS  # noqa: E402
from astra_graph.principal import Principal  # noqa: E402
from astra_graph.tolerance_charter import (  # noqa: E402
    DEFAULT_CHARTER,
    ParamRule,
    SamplingRule,
    ToleranceCharter,
)
from astra_graph.verdicts import VerdictsService  # noqa: E402
from astra_graph.writes import EdgeWrite, GraphWriter, NodeWrite  # noqa: E402

PRINCIPAL = Principal("agent:harvester", run_id="run-harvester")
PARITY_ENGINEER = Principal("user:parity@artizent.example")

FORCED_SAMPLING = replace(DEFAULT_CHARTER, sampling=SamplingRule(full_compare_max_rows=0, sample_rows=10))


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
    config = _settings(f"astra_sampling_{new_ulid()[10:22].lower()}")

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


def _charter(max_values: int = 12) -> ToleranceCharter:
    return ToleranceCharter(params=ParamRule(enumerate_max_values=max_values, enumerate_strategy="DEFAULT_PLUS_OBSERVED"))


async def _node_properties(pool: asyncpg.Pool, graph_name: str, label: str, node_id: str) -> dict[str, Any]:
    async with pool.acquire() as conn:
        hydrated = await hydrate(conn, graph_name, label, [node_id])
    return hydrated[node_id]


@pytest.fixture
async def estate(settings: Settings, tmp_path: Path):
    """One workbook, one derived-and-executed case -- the real prerequisite pipeline
    (S7.2.1 derive, S7.3.1 execute) this story's own sampling reads from."""
    pool = await create_pool(settings)
    try:
        repository = AgeGraphRepository(pool, graph_name=settings.graph_name)
        writer = GraphWriter(repository, event_source=source_for(settings.graph_name))
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
            writer, "CalculatedField", name="MarginCalc", formula="SUM([Margin])",
            formula_ast={"kind": "FUNCTION", "name": "SUM", "children": [], "detail": {}},
        )
        await _edge(writer, "HAS_FIELD", datasource, margin_calc)

        sheet = await _write(
            writer, "Worksheet", name="Bar sheet", mark_type="bar",
            rows_shelf=["Desk"], cols_shelf=["MarginCalc"], marks_shelf=[],
        )
        await _edge(writer, "CONTAINS", book, sheet)
        await _edge(writer, "USES_DATASOURCE", sheet, datasource)

        derive_result = await derivation.derive(
            book, charter_version="1", charter=_charter(), principal=PARITY_ENGINEER,
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
        execute_result = await execution_service.execute(book, workspace="dev", principal=PARITY_ENGINEER)
        assert execute_result["cases_executed"] > 0

        verdicts_service = VerdictsService(
            pool, graph_name=settings.graph_name, writer=writer, artefact_store=artefact_store,
        )

        yield {
            "pool": pool, "settings": settings, "writer": writer, "artefact_store": artefact_store,
            "verdicts": verdicts_service, "workbook": book, "sheet": sheet,
        }
    finally:
        await pool.close()


async def test_a_forced_sample_writes_real_sampling_facts_on_the_parity_case(estate) -> None:
    result = await estate["verdicts"].run(
        estate["workbook"], charter=FORCED_SAMPLING, charter_version="s1", principal=PARITY_ENGINEER,
    )
    case_id = result["results"][0]["case_id"]

    case = await _node_properties(estate["pool"], estate["settings"].graph_name, "ParityCase", case_id)
    assert case["sampled"] is True
    assert isinstance(case["sample_size"], int) and case["sample_size"] > 0
    assert isinstance(case["sampling_seed"], int)
    assert case["sampling_strategy"] == "stratified_by_grain_plus_top_n_by_measure"


async def test_a_forced_sample_writes_sampled_true_on_the_verdict(estate) -> None:
    result = await estate["verdicts"].run(
        estate["workbook"], charter=FORCED_SAMPLING, charter_version="s1", principal=PARITY_ENGINEER,
    )
    verdict_id = result["results"][0]["verdict_id"]
    verdict = await _node_properties(estate["pool"], estate["settings"].graph_name, "Verdict", verdict_id)
    assert verdict["sampled"] is True


async def test_the_evidence_bundle_carries_the_same_sampling_facts(estate) -> None:
    result = await estate["verdicts"].run(
        estate["workbook"], charter=FORCED_SAMPLING, charter_version="s1", principal=PARITY_ENGINEER,
    )
    evidence_ref = result["results"][0]["evidence_ref"]
    content = await estate["artefact_store"].content(evidence_ref)
    assert content is not None
    bundle = json.loads(content)
    assert bundle["diff"]["sampling"] is not None
    assert bundle["diff"]["sampling"]["sample_size"] > 0
    assert isinstance(bundle["diff"]["sampling"]["seed"], int)


async def test_an_unsampled_run_clears_a_previously_sampled_case(estate) -> None:
    sampled_result = await estate["verdicts"].run(
        estate["workbook"], charter=FORCED_SAMPLING, charter_version="s1", principal=PARITY_ENGINEER,
    )
    case_id = sampled_result["results"][0]["case_id"]
    sampled_case = await _node_properties(estate["pool"], estate["settings"].graph_name, "ParityCase", case_id)
    assert sampled_case["sampled"] is True

    # DEFAULT_CHARTER's own full_compare_max_rows (200,000) is never crossed by this
    # fixture's own handful of rows -- a real, unsampled re-run of the same case.
    await estate["verdicts"].run(
        estate["workbook"], charter=DEFAULT_CHARTER, charter_version="s2", principal=PARITY_ENGINEER,
    )
    unsampled_case = await _node_properties(estate["pool"], estate["settings"].graph_name, "ParityCase", case_id)
    assert unsampled_case["sampled"] is False
    assert unsampled_case.get("sample_size") is None
    assert unsampled_case.get("sampling_seed") is None


async def test_sampled_surfaces_over_http_on_the_parity_run_route(estate) -> None:
    from httpx import ASGITransport, AsyncClient

    from astra_graph.main import create_app
    from astra_graph.principal import PRINCIPAL_HEADER
    from astra_graph.roles import ROLES_HEADER
    from astra_graph.tolerance_charter import ToleranceCharterVersion

    app = create_app()
    app.state.verdicts = estate["verdicts"]

    class _ForcedSamplingCharterStore:
        async def latest(self) -> ToleranceCharterVersion:
            return ToleranceCharterVersion(version=9, charter=FORCED_SAMPLING, updated_by="test", updated_at=None)

    app.state.tolerance_charter_store = _ForcedSamplingCharterStore()

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://graph-svc") as client:
        run_response = await client.post(
            f"/v1/workbooks/{estate['workbook']}:run-parity",
            headers={PRINCIPAL_HEADER: PARITY_ENGINEER.value, ROLES_HEADER: "parity_engineer"},
        )
        assert run_response.status_code == 200

        get_response = await client.get(
            f"/v1/workbooks/{estate['workbook']}/parity-run",
            headers={PRINCIPAL_HEADER: PARITY_ENGINEER.value, ROLES_HEADER: "parity_engineer"},
        )
    assert get_response.status_code == 200
    verdicts = get_response.json()["verdicts"]
    assert len(verdicts) == 1
    assert verdicts[0]["sampled"] is True
