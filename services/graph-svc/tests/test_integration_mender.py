"""§11.2/§8.10 the bounded repair loop, against real PostgreSQL + Apache AGE -- story
S8.2.1, continuing F8.2/E8.

What only the real stack can answer: that a real ACTIVE `Pattern` really gets applied
deterministically to a real `CalculatedField`'s own AST shape and a fresh `Measure` and
`MAPS_TO` edge really get written; that a real `MENDER_REPAIR` model call really is
genuinely unroutable in this deployment (`gateway.null_gateway()`'s own real
`GatewayRoutingError`, not a stand-in for one); that a real re-proof really re-executes
the target side, writes a real `Verdict`, and a real revert really happens when a repair
regresses a real, previously-PASSing sibling case sharing the same artefact; that
`passes_consumed`/`MenderPass` nodes are really written, one per pass, with every pass in
evidence, win or lose; and that the new `:mend` route really drives its own real role
gate.

**Determinism, honestly.** `FixtureTargetAdapter.evaluate` (S7.3.1) is a disclosed stand-in
seeded by the query text alone, never by a `Measure`'s own DAX -- confirmed directly (see
`target_fake.py`'s own docstring): no live analysis-services engine exists locally to run
real DAX against, so a repair's own correctness can never be proven by this stack the way a
real deployment would. What *can* be proven for real: the loop's own mechanics. Each test
below calls the real `evaluate()` once up front (the same query `reprove_cases` will later
build from the same case's own grain/measures/filters/sheet) and stores that exact,
byte-identical result as the case's own `expected_ref` -- guaranteeing a real PASS on
re-proof regardless of which pass produced the repair -- or a deliberately altered copy,
guaranteeing a real, large FAIL. Both are real diffs over real stored ResultSets; only the
*question being answered* (does re-proof observe what the fixture will produce again) is
narrower than the AC's own "against a live model" case, honestly, the same narrowing this
epic's own prior integration suites already carry openly.
"""

from __future__ import annotations

import asyncio
import json
import os
import threading
from collections.abc import Callable, Coroutine, Sequence
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

import pytest

pytestmark = pytest.mark.integration

asyncpg = pytest.importorskip("asyncpg")

from astra_adapter import ParityCase as SdkParityCase  # noqa: E402
from astra_adapter.target_fake import FixtureTargetAdapter  # noqa: E402

from astra_graph.artefacts import PostgresArtefactStore  # noqa: E402
from astra_graph.case_execution import (  # noqa: E402
    RESULT_SET_MEDIA_TYPE,
    _table_map_for_sheet,
    result_set_to_parquet,
)
from astra_graph.case_execution_query import (  # noqa: E402
    build_dax_query,
    to_sdk_filters,
    to_sdk_parameters,
)
from astra_graph.config import Settings  # noqa: E402
from astra_graph.gateway import (  # noqa: E402
    RawModelResponse,
    StaticGateway,
    SupportsAsDict,
    null_gateway,
)
from astra_graph.graph import AgeGraphRepository, create_pool  # noqa: E402
from astra_graph.graph.queries import EDGE_INDEX_TABLE, NODE_INDEX_TABLE, accessor  # noqa: E402
from astra_graph.ids import new_ulid  # noqa: E402
from astra_graph.lineage import hydrate  # noqa: E402
from astra_graph.mender import (  # noqa: E402
    InMemoryMenderConfigStore,
    MenderConfig,
    MenderError,
    MenderService,
)
from astra_graph.migrations import run as run_migrations  # noqa: E402
from astra_graph.ontology import EDGE_LABELS, NODE_LABELS  # noqa: E402
from astra_graph.principal import Principal  # noqa: E402
from astra_graph.provenance import PostgresProvenanceStore  # noqa: E402
from astra_graph.tolerance_charter import PostgresToleranceCharterStore  # noqa: E402
from astra_graph.writes import EdgeWrite, GraphWriter, NodeWrite  # noqa: E402

PRINCIPAL = Principal("agent:harvester", run_id="run-harvester")
PARITY_ENGINEER = Principal("user:parity@artizent.example")

_WORKSPACE = "dev"

#: `SUM(a)` -- see `context/signature.py`'s own real wire-shape walk. Matches the fixture
#: `CalculatedField.formula_ast` every test below writes for `MarginCalc`.
_MARGIN_CALC_AST: dict[str, Any] = {
    "kind": "AGGREGATE", "name": "SUM",
    "children": [{"kind": "REFERENCE", "name": "Margin", "children": []}],
}


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
    """Function-scoped -- see `test_integration_case_derivation.py`'s own identical
    fixture for why a shared graph would let one test's exceptions pollute another's."""
    config = _settings(f"astra_mender_{new_ulid()[10:22].lower()}")

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
                "public.mender_config",
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


@pytest.fixture
async def estate(settings: Settings, tmp_path: Path):
    """One workbook, one `CalculatedField` (`MarginCalc`, a real `SUM(a)`-shaped AST) on
    one sheet, mapped to a real, deliberately 'broken' `Measure` -- the shared graph
    every test below opens its own `ExceptionCase` against."""
    pool = await create_pool(settings)
    try:
        repository = AgeGraphRepository(pool, graph_name=settings.graph_name)
        writer = GraphWriter(repository, event_source="/astra/graph-svc")
        artefact_store = PostgresArtefactStore(pool, graph_name=settings.graph_name)
        provenance_store = PostgresProvenanceStore(pool, graph_name=settings.graph_name)
        charter_store = PostgresToleranceCharterStore(pool, graph_name=settings.graph_name)
        target_adapter = FixtureTargetAdapter(repo_path=tmp_path / "repo", workspace_root=tmp_path / "workspaces")

        suffix = new_ulid()[10:18].lower()
        site = await _write(writer, "Site", luid=f"s-{suffix}", name=f"rqa-{suffix}")
        project = await _write(writer, "Project", luid=f"p-{suffix}", name="Risk Core")
        await _edge(writer, "CONTAINS", site, project)
        book = await _write(writer, "Workbook", luid=f"wb-{suffix}", name="Daily VaR", revision="1")
        await _edge(writer, "CONTAINS", project, book)

        datasource = await _write(
            writer, "Datasource", name="VaR ds", type="published", luid=f"ds-{suffix}", extract_flag=True,
        )
        desk = await _write(writer, "Field", name="Desk", datatype="string", role="dimension")
        await _edge(writer, "HAS_FIELD", datasource, desk)

        margin_calc = await _write(
            writer, "CalculatedField", name="MarginCalc", formula="SUM([Margin])", formula_ast=_MARGIN_CALC_AST,
        )
        await _edge(writer, "HAS_FIELD", datasource, margin_calc)

        sheet = await _write(
            writer, "Worksheet", name="Bar sheet", mark_type="bar",
            rows_shelf=["Desk"], cols_shelf=["MarginCalc"], marks_shelf=[],
        )
        await _edge(writer, "CONTAINS", book, sheet)
        await _edge(writer, "USES_DATASOURCE", sheet, datasource)

        measure = await _write(
            writer, "Measure", name="MarginCalc", dax="SUM([WrongField])", provenance_ref=f"prov_seed_{suffix}",
        )
        await _edge(writer, "MAPS_TO", margin_calc, measure)

        yield {
            "pool": pool, "settings": settings, "writer": writer, "artefact_store": artefact_store,
            "provenance_store": provenance_store, "charter_store": charter_store, "target_adapter": target_adapter,
            "site": site, "project": project, "workbook": book, "datasource": datasource, "desk": desk,
            "margin_calc": margin_calc, "sheet": sheet, "measure": measure,
        }
    finally:
        await pool.close()


# --------------------------------------------------------------------- deterministic cases


async def _query_text_and_result(
    pool: asyncpg.Pool, graph_name: str, target_adapter: FixtureTargetAdapter, *,
    workbook_id: str, sheet_ref: str, case_key: str, grain: tuple[str, ...], measures: tuple[str, ...],
) -> Any:
    """The exact real query `reprove_cases` will independently build for this case, and
    the exact real synthetic `ResultSet` `evaluate()` deterministically returns for it --
    called once here, and again for real inside `reprove_cases` later; both calls are a
    pure function of `query_text` alone (see `target_fake.py`'s own docstring), so the two
    calls agree byte for byte."""
    sdk_filters = to_sdk_filters({})
    sdk_parameters = to_sdk_parameters({})
    async with pool.acquire() as conn:
        table_map = await _table_map_for_sheet(conn, graph_name, sheet_ref, grain)
    query_text = build_dax_query(
        grain=grain, measures=measures, sdk_filters=sdk_filters, sdk_parameters=sdk_parameters, table_map=table_map,
    )
    sdk_case = SdkParityCase(
        id=case_key, workbook_luid=workbook_id, sheet=sheet_ref, grain=grain, measures=measures,
        filters=sdk_filters, parameters=sdk_parameters,
    )
    return await target_adapter.evaluate(query_text=query_text, case=sdk_case, workspace=_WORKSPACE)


async def _write_case(
    estate: dict[str, Any], *, sheet_ref: str, matching: bool,
) -> tuple[str, str]:
    """A real `ParityCase` on `sheet_ref`, always naming `grain=(Desk,)`/`measures=
    (MarginCalc,)`. `matching=True` stores the real, exact `evaluate()` output as
    `expected_ref` -- a guaranteed real PASS on any future re-proof, whatever DAX the
    Measure carries at the time (see this module's own docstring on why the fixture
    target cannot answer a repair's own correctness). `matching=False` stores a
    deliberately altered copy -- a guaranteed real, large FAIL. Returns `(case_id,
    case_key)`."""
    writer, artefact_store = estate["writer"], estate["artefact_store"]
    pool, graph_name = estate["pool"], estate["settings"].graph_name
    case_key = f"case_{new_ulid()}"
    case_id = await _write(
        writer, "ParityCase", mu_ref=estate["workbook"], sheet_ref=sheet_ref, grain=["Desk"],
        measures=["MarginCalc"], filter_ctx={}, param_values={}, case_key=case_key, state="EXECUTED",
    )
    real = await _query_text_and_result(
        pool, graph_name, estate["target_adapter"], workbook_id=estate["workbook"], sheet_ref=sheet_ref,
        case_key=case_key, grain=("Desk",), measures=("MarginCalc",),
    )
    if matching:
        stored = real
    else:
        altered_rows = tuple((*row[:-1], float(row[-1]) + 999_999.0) for row in real.rows)
        stored = replace(real, rows=altered_rows)
    expected_bytes = result_set_to_parquet(stored)
    expected_artefact = await artefact_store.store(
        kind="mender_test_expected", mu_ref=estate["workbook"], case_id=case_id,
        content=expected_bytes, media_type=RESULT_SET_MEDIA_TYPE, created_by=PRINCIPAL.value,
    )
    await writer.set_node_properties(case_id, {"expected_ref": expected_artefact.id}, principal=PRINCIPAL)
    return case_id, case_key


async def _write_fail_verdict(estate: dict[str, Any], *, case_id: str) -> None:
    """A real, seed `Verdict(result='FAIL')` with a real evidence bundle in S7.4.1's own
    shape -- what `_gather_parity_evidence` (mender.py) reads to assemble a repair
    context. Deliberately not produced by a real diff: this module's own docstring
    explains why the initial FAIL is seeded rather than driven through the full derive/
    execute/diff pipeline."""
    writer, artefact_store = estate["writer"], estate["artefact_store"]
    bundle: dict[str, Any] = {
        "diff": {
            "failing_cells": [
                {"row": {"Desk": "Desk-0"}, "measure": "MarginCalc", "expected": 100.0, "candidate": None},
            ],
        },
        "filter_ctx": {}, "expected_columns": [{"name": "MarginCalc", "type": "double"}],
        "candidate_columns": [{"name": "MarginCalc", "type": "double"}],
    }
    evidence = await artefact_store.store(
        kind="mender_test_verdict_evidence", mu_ref=estate["workbook"], case_id=case_id,
        content=json.dumps(bundle).encode("utf-8"), media_type="application/json", created_by=PRINCIPAL.value,
    )
    await _write(writer, "Verdict", case_ref=case_id, result="FAIL", failing_cells=bundle["diff"]["failing_cells"], evidence_ref=evidence.id)


async def _write_pass_verdict(estate: dict[str, Any], *, case_id: str) -> None:
    """A real, seed `Verdict(result='PASS')` for a sibling case -- what `check_and_
    revert_regressions` needs to find a 'previously passing' case worth re-proving."""
    writer = estate["writer"]
    await _write(writer, "Verdict", case_ref=case_id, result="PASS", failing_cells=[])


async def _open_exception(
    estate: dict[str, Any], *, failure_class: str, case_ids: Sequence[str], artefact_ref: str | None,
) -> str:
    writer, artefact_store = estate["writer"], estate["artefact_store"]
    evidence = await artefact_store.store(
        kind="mender_test_classification_evidence", mu_ref=estate["workbook"], case_id=case_ids[0],
        content=b"{}", media_type="application/json", created_by=PRINCIPAL.value,
    )
    return await _write_props(writer, "ExceptionCase", {
        "mu_ref": estate["workbook"], "class": failure_class, "state": "OPEN",
        "evidence_ref": evidence.id, "artefact_ref": artefact_ref, "case_refs": list(case_ids),
        "classification_signals": {"note": "test-seeded"},
    })


async def _active_pattern(estate: dict[str, Any]) -> str:
    """An ACTIVE Pattern whose `source_signature` matches `_MARGIN_CALC_AST`'s own real
    shape (`SUM(a)`) -- `render_target("SUM({a})", {"a": "Margin"})` renders
    `SUM([Margin])`, a real, `dax_sanity_check`-clean DAX string (`SUM` is a known
    function)."""
    return await _write_props(estate["writer"], "Pattern", {
        "name": "sum-margin-repair", "class": "C2",
        "source_signature": {"ast_shape": "SUM(a)", "adapter": "tableau"},
        "target_template": "SUM({a})", "promotion_state": "ACTIVE",
    })


async def _exception_case_properties(pool: asyncpg.Pool, graph_name: str, case_id: str) -> dict[str, Any]:
    async with pool.acquire() as conn:
        hydrated = await hydrate(conn, graph_name, "ExceptionCase", [case_id])
    return hydrated[case_id]


async def _hydrate_one(pool: asyncpg.Pool, graph_name: str, label: str, node_id: str) -> dict[str, Any]:
    async with pool.acquire() as conn:
        hydrated = await hydrate(conn, graph_name, label, [node_id])
    return hydrated[node_id]


async def _mender_passes(pool: asyncpg.Pool, graph_name: str, exception_case_id: str) -> list[dict[str, Any]]:
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            f"""SELECT id FROM {NODE_INDEX_TABLE}
                 WHERE graph = $1 AND kind = 'node' AND label = 'MenderPass' AND retired_at IS NULL""",
            graph_name,
        )
        passes = await hydrate(conn, graph_name, "MenderPass", [row["id"] for row in rows])
    matching = [{"id": pid, **props} for pid, props in passes.items() if props.get("exception_case_ref") == exception_case_id]
    return sorted(matching, key=lambda p: p["pass_number"])


def _service(estate: dict[str, Any], *, gateway: Any, pass_budget: int = 3) -> MenderService:
    return MenderService(
        estate["pool"], graph_name=estate["settings"].graph_name, writer=estate["writer"],
        artefact_store=estate["artefact_store"], provenance_store=estate["provenance_store"], gateway=gateway,
        target_adapter=estate["target_adapter"], config_store=InMemoryMenderConfigStore(MenderConfig(pass_budget=pass_budget)),
        charter_store=estate["charter_store"],
    )


@dataclass
class _ScriptedModelCaller:
    provider = "test"
    model = "test-model"
    responses: Sequence[dict[str, Any]]

    def __post_init__(self) -> None:
        self._calls = 0

    async def generate(self, request: SupportsAsDict, *, previous_error: str | None) -> RawModelResponse:
        raw = self.responses[self._calls]
        self._calls += 1
        return RawModelResponse(
            raw=raw, gateway_request_id=f"req_{self._calls}", provider="test", model="test-model",
            prompt_hash="hash", temperature=0.0, tokens_in=10, tokens_out=5,
        )


# ------------------------------------------------------------------------- pass 1: pattern


async def test_an_active_pattern_closes_the_case_in_one_pass(estate) -> None:
    case_id, _key = await _write_case(estate, sheet_ref=estate["sheet"], matching=True)
    await _write_fail_verdict(estate, case_id=case_id)
    await _active_pattern(estate)
    exception_id = await _open_exception(
        estate, failure_class="NULL_HANDLING", case_ids=[case_id], artefact_ref=estate["margin_calc"],
    )

    result = await _service(estate, gateway=null_gateway()).mend(
        exception_id, workspace=_WORKSPACE, principal=PARITY_ENGINEER,
    )
    assert result["outcome"] == "closed"
    assert result["passes_consumed"] == 1
    assert len(result["passes"]) == 1
    assert result["passes"][0]["strategy"] == "PATTERN"
    assert result["passes"][0]["result"] == "PROVED"
    assert result["passes"][0]["measure_id"]

    properties = await _exception_case_properties(estate["pool"], estate["settings"].graph_name, exception_id)
    assert properties["state"] == "CLOSED"
    assert properties["passes_consumed"] == 1

    passes = await _mender_passes(estate["pool"], estate["settings"].graph_name, exception_id)
    assert len(passes) == 1
    assert passes[0]["strategy"] == "PATTERN"
    assert passes[0]["result"] == "PROVED"

    new_measure = await _hydrate_one(estate["pool"], estate["settings"].graph_name, "Measure", result["passes"][0]["measure_id"])
    assert new_measure["dax"] == "SUM([Margin])"


# --------------------------------------------------------------------------- pass 2: model


async def test_model_pass_closes_the_case_when_no_pattern_matches(estate) -> None:
    """No ACTIVE pattern exists this time -- pass 1 is a real `NO_PATTERN_MATCH`, and
    pass 2's own scripted model response supplies a real, valid DAX fix."""
    case_id, _key = await _write_case(estate, sheet_ref=estate["sheet"], matching=True)
    await _write_fail_verdict(estate, case_id=case_id)
    exception_id = await _open_exception(
        estate, failure_class="NULL_HANDLING", case_ids=[case_id], artefact_ref=estate["margin_calc"],
    )
    gateway = StaticGateway(_ScriptedModelCaller(
        responses=[{"dax": "COALESCE([Margin], 0)", "m": None, "assumptions": [], "confidence": 0.8, "notes": "fixed"}],
    ))

    result = await _service(estate, gateway=gateway).mend(exception_id, workspace=_WORKSPACE, principal=PARITY_ENGINEER)
    assert result["outcome"] == "closed"
    assert result["passes_consumed"] == 2
    assert [p["strategy"] for p in result["passes"]] == ["PATTERN", "MODEL"]
    assert result["passes"][0]["result"] == "NO_PATTERN_MATCH"
    assert result["passes"][1]["result"] == "PROVED"

    passes = await _mender_passes(estate["pool"], estate["settings"].graph_name, exception_id)
    assert [p["result"] for p in passes] == ["NO_PATTERN_MATCH", "PROVED"]


# ---------------------------------------------------------------- unroutable model escalation


async def test_a_genuinely_unroutable_gateway_escalates_as_model_unavailable(estate) -> None:
    """`null_gateway()` -- the real, always-`GatewayRoutingError` gateway `gateway.py`
    itself ships, the identical footing `MENDER_REPAIR` genuinely has in this deployment
    today (see mender.py's own docstring)."""
    case_id, _key = await _write_case(estate, sheet_ref=estate["sheet"], matching=True)
    await _write_fail_verdict(estate, case_id=case_id)
    exception_id = await _open_exception(
        estate, failure_class="NULL_HANDLING", case_ids=[case_id], artefact_ref=estate["margin_calc"],
    )

    result = await _service(estate, gateway=null_gateway()).mend(exception_id, workspace=_WORKSPACE, principal=PARITY_ENGINEER)
    assert result["outcome"] == "escalated"
    assert result["passes_consumed"] == 2
    assert [p["strategy"] for p in result["passes"]] == ["PATTERN", "MODEL"]
    assert result["passes"][1]["result"] == "MODEL_UNAVAILABLE"

    properties = await _exception_case_properties(estate["pool"], estate["settings"].graph_name, exception_id)
    assert properties["state"] == "OPEN"
    assert properties["passes_consumed"] == 2


# --------------------------------------------------------------------------- KEY_MISSING


async def test_key_missing_escalates_immediately_with_no_repair_attempt(estate) -> None:
    case_id, _key = await _write_case(estate, sheet_ref=estate["sheet"], matching=True)
    await _write_fail_verdict(estate, case_id=case_id)
    exception_id = await _open_exception(
        estate, failure_class="KEY_MISSING", case_ids=[case_id], artefact_ref=estate["margin_calc"],
    )

    result = await _service(estate, gateway=null_gateway()).mend(exception_id, workspace=_WORKSPACE, principal=PARITY_ENGINEER)
    assert result["outcome"] == "escalated"
    assert result["reason"] == "key_missing_model_defect"
    assert result["passes_consumed"] == 1

    passes = await _mender_passes(estate["pool"], estate["settings"].graph_name, exception_id)
    assert len(passes) == 1
    assert passes[0]["strategy"] == "ESCALATE_IMMEDIATE"
    assert passes[0]["result"] == "KEY_MISSING_MODEL_DEFECT"

    properties = await _exception_case_properties(estate["pool"], estate["settings"].graph_name, exception_id)
    assert properties["state"] == "OPEN"


# ---------------------------------------------------------------------------- exhaustion


async def test_exhaustion_after_the_full_budget_writes_one_pass_per_attempt_and_stays_open(estate) -> None:
    """No ACTIVE pattern, and every model attempt returns structurally broken DAX (an
    unbalanced paren) -- `PARSE_ERROR` every time, never `MODEL_UNAVAILABLE`, so the loop
    genuinely reaches and exhausts the real pass budget rather than breaking early."""
    case_id, _key = await _write_case(estate, sheet_ref=estate["sheet"], matching=False)
    await _write_fail_verdict(estate, case_id=case_id)
    exception_id = await _open_exception(
        estate, failure_class="AGGREGATION", case_ids=[case_id], artefact_ref=estate["margin_calc"],
    )
    broken: dict[str, Any] = {"dax": "CALCULATE([Margin]", "m": None, "assumptions": [], "confidence": 0.4, "notes": "n"}
    gateway = StaticGateway(_ScriptedModelCaller(responses=[broken, broken]))

    result = await _service(estate, gateway=gateway, pass_budget=3).mend(
        exception_id, workspace=_WORKSPACE, principal=PARITY_ENGINEER,
    )
    assert result["outcome"] == "escalated"
    assert result["passes_consumed"] == 3
    assert [p["strategy"] for p in result["passes"]] == ["PATTERN", "MODEL", "MODEL_WIDENED"]
    assert result["passes"][0]["result"] == "NO_PATTERN_MATCH"
    assert result["passes"][1]["result"] == "PARSE_ERROR"
    assert result["passes"][2]["result"] == "PARSE_ERROR"

    passes = await _mender_passes(estate["pool"], estate["settings"].graph_name, exception_id)
    assert len(passes) == 3

    properties = await _exception_case_properties(estate["pool"], estate["settings"].graph_name, exception_id)
    assert properties["state"] == "OPEN"
    assert properties["passes_consumed"] == 3


# -------------------------------------------------------------------- configurable budget


async def test_the_pass_budget_is_configurable_and_shortens_the_loop(estate) -> None:
    """The AC's own 'bound (default 3) is configurable per tenant' -- a budget of 1 means
    only the pattern pass is ever attempted, even with no ACTIVE pattern to match."""
    case_id, _key = await _write_case(estate, sheet_ref=estate["sheet"], matching=False)
    await _write_fail_verdict(estate, case_id=case_id)
    exception_id = await _open_exception(
        estate, failure_class="AGGREGATION", case_ids=[case_id], artefact_ref=estate["margin_calc"],
    )

    result = await _service(estate, gateway=null_gateway(), pass_budget=1).mend(
        exception_id, workspace=_WORKSPACE, principal=PARITY_ENGINEER,
    )
    assert result["outcome"] == "escalated"
    assert result["passes_consumed"] == 1
    assert result["passes"][0]["strategy"] == "PATTERN"
    assert result["passes"][0]["result"] == "NO_PATTERN_MATCH"


# --------------------------------------------------------------------- no-change-ends-early


async def test_a_pass_that_produces_no_change_in_the_failing_set_ends_the_loop_early(estate) -> None:
    """§11.2's own literal words. `matching=False` guarantees every re-proof attempt
    keeps failing, so pass 2's own real repair still leaves the identical failing-case
    set behind -- the loop must end there, never spending pass 3."""
    case_id, _key = await _write_case(estate, sheet_ref=estate["sheet"], matching=False)
    await _write_fail_verdict(estate, case_id=case_id)
    exception_id = await _open_exception(
        estate, failure_class="AGGREGATION", case_ids=[case_id], artefact_ref=estate["margin_calc"],
    )
    gateway = StaticGateway(_ScriptedModelCaller(
        responses=[{"dax": "SUM([Margin])", "m": None, "assumptions": [], "confidence": 0.7, "notes": "n"}],
    ))

    result = await _service(estate, gateway=gateway, pass_budget=3).mend(
        exception_id, workspace=_WORKSPACE, principal=PARITY_ENGINEER,
    )
    assert result["outcome"] == "escalated"
    assert result["passes_consumed"] == 2
    assert [p["strategy"] for p in result["passes"]] == ["PATTERN", "MODEL"]
    assert result["passes"][1]["result"] == "STILL_FAILING"
    assert result["passes"][1]["cases_still_failing"] == [case_id]


# ----------------------------------------------------------------- UNKNOWN one-shot escalation


async def test_unknown_class_escalates_after_one_model_diagnosis_not_three_passes(estate) -> None:
    case_id, _key = await _write_case(estate, sheet_ref=estate["sheet"], matching=False)
    await _write_fail_verdict(estate, case_id=case_id)
    exception_id = await _open_exception(
        estate, failure_class="UNKNOWN", case_ids=[case_id], artefact_ref=estate["margin_calc"],
    )
    gateway = StaticGateway(_ScriptedModelCaller(
        responses=[{"dax": "SUM([Margin])", "m": None, "assumptions": [], "confidence": 0.5, "notes": "n"}],
    ))

    result = await _service(estate, gateway=gateway, pass_budget=3).mend(
        exception_id, workspace=_WORKSPACE, principal=PARITY_ENGINEER,
    )
    assert result["outcome"] == "escalated"
    assert result["passes_consumed"] == 2
    assert [p["strategy"] for p in result["passes"]] == ["PATTERN", "MODEL"]


# -------------------------------------------------------------------------- regression revert


async def test_a_regression_on_a_sibling_case_is_detected_and_the_artefact_is_reverted(estate) -> None:
    """A second sheet also carries `MarginCalc` -- the AC's own 'a measure used by many
    sheets'. That sibling case already has a real `Verdict(PASS)`; its own `expected_ref`
    is deliberately mismatched, so re-proving it after the repair produces a real FAIL --
    a real regression `check_and_revert_regressions` must catch and revert."""
    writer = estate["writer"]
    second_sheet = await _write(
        writer, "Worksheet", name="Line sheet", mark_type="line",
        rows_shelf=["Desk"], cols_shelf=["MarginCalc"], marks_shelf=[],
    )
    await _edge(writer, "CONTAINS", estate["workbook"], second_sheet)
    await _edge(writer, "USES_DATASOURCE", second_sheet, estate["datasource"])

    failing_case_id, _key = await _write_case(estate, sheet_ref=estate["sheet"], matching=True)
    await _write_fail_verdict(estate, case_id=failing_case_id)

    sibling_case_id, _sibling_key = await _write_case(estate, sheet_ref=second_sheet, matching=False)
    await _write_pass_verdict(estate, case_id=sibling_case_id)

    await _active_pattern(estate)
    exception_id = await _open_exception(
        estate, failure_class="NULL_HANDLING", case_ids=[failing_case_id], artefact_ref=estate["margin_calc"],
    )

    result = await _service(estate, gateway=null_gateway()).mend(exception_id, workspace=_WORKSPACE, principal=PARITY_ENGINEER)
    assert result["passes"][0]["strategy"] == "PATTERN"
    assert result["passes"][0]["result"] == "REGRESSED"
    # A regression looks like "no change in the originally-failing set" -- the loop ends
    # that same pass rather than spending a second one on an artefact already reverted.
    assert result["outcome"] == "escalated"
    assert result["passes_consumed"] == 1

    reverted_measure = await _hydrate_one(
        estate["pool"], estate["settings"].graph_name, "Measure", result["passes"][0]["measure_id"],
    )
    # The regressed measure itself carries the pattern's own repair...
    assert reverted_measure["dax"] == "SUM([Margin])"
    # ...but MarginCalc's own live MAPS_TO now points at a fresh Measure carrying the
    # original DAX back -- a new write, never an in-place undo (see mender.py's own
    # docstring).
    async with estate["pool"].acquire() as conn:
        rows = await conn.fetch(
            f"""SELECT e.to_id AS measure_id FROM {EDGE_INDEX_TABLE} e
                 WHERE e.graph = $1 AND e.label = 'MAPS_TO' AND e.from_id = $2 AND e.retired_at IS NULL""",
            estate["settings"].graph_name, estate["margin_calc"],
        )
    live_measure_id = str(rows[0]["measure_id"])
    live_measure = await _hydrate_one(estate["pool"], estate["settings"].graph_name, "Measure", live_measure_id)
    assert live_measure["dax"] == "SUM([WrongField])"


# --------------------------------------------------------------------------------- refused


async def test_mending_a_non_open_exception_is_refused(estate) -> None:
    case_id, _key = await _write_case(estate, sheet_ref=estate["sheet"], matching=True)
    await _write_fail_verdict(estate, case_id=case_id)
    exception_id = await _open_exception(
        estate, failure_class="NULL_HANDLING", case_ids=[case_id], artefact_ref=estate["margin_calc"],
    )
    await estate["writer"].set_node_properties(exception_id, {"state": "CLOSED"}, principal=PRINCIPAL)

    with pytest.raises(MenderError, match="not OPEN"):
        await _service(estate, gateway=null_gateway()).mend(exception_id, workspace=_WORKSPACE, principal=PARITY_ENGINEER)


async def test_mending_an_unknown_exception_is_refused(estate) -> None:
    with pytest.raises(MenderError, match="no ExceptionCase"):
        await _service(estate, gateway=null_gateway()).mend(new_ulid(), workspace=_WORKSPACE, principal=PARITY_ENGINEER)


# ---------------------------------------------------------------------------------- API


@pytest.fixture
async def http_client(estate):
    from httpx import ASGITransport, AsyncClient

    from astra_graph.main import create_app

    app = create_app()
    app.state.mender = _service(estate, gateway=null_gateway())

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://graph-svc") as async_client:
        yield async_client


def _headers(role: str, principal: Principal) -> dict[str, str]:
    from astra_graph.principal import PRINCIPAL_HEADER
    from astra_graph.roles import ROLES_HEADER

    return {PRINCIPAL_HEADER: principal.value, ROLES_HEADER: role}


async def test_mend_over_http_requires_the_parity_engineer_role(estate, http_client) -> None:
    case_id, _key = await _write_case(estate, sheet_ref=estate["sheet"], matching=True)
    await _write_fail_verdict(estate, case_id=case_id)
    exception_id = await _open_exception(
        estate, failure_class="NULL_HANDLING", case_ids=[case_id], artefact_ref=estate["margin_calc"],
    )
    response = await http_client.post(
        f"/v1/exceptions/{exception_id}:mend", headers=_headers("migration_engineer", PARITY_ENGINEER),
    )
    assert response.status_code == 403


async def test_mend_over_http_succeeds_for_the_parity_engineer(estate, http_client) -> None:
    case_id, _key = await _write_case(estate, sheet_ref=estate["sheet"], matching=True)
    await _write_fail_verdict(estate, case_id=case_id)
    await _active_pattern(estate)
    exception_id = await _open_exception(
        estate, failure_class="NULL_HANDLING", case_ids=[case_id], artefact_ref=estate["margin_calc"],
    )
    response = await http_client.post(
        f"/v1/exceptions/{exception_id}:mend", headers=_headers("parity_engineer", PARITY_ENGINEER),
    )
    assert response.status_code == 200
    body = response.json()
    assert body["outcome"] == "closed"


async def test_mend_over_http_on_a_nonexistent_case_is_a_clean_400(estate, http_client) -> None:
    response = await http_client.post(
        f"/v1/exceptions/{new_ulid()}:mend", headers=_headers("parity_engineer", PARITY_ENGINEER),
    )
    assert response.status_code == 400
