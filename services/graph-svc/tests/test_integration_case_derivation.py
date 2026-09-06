"""Parity case derivation, against real PostgreSQL + Apache AGE -- story S7.2.1,
continuing E7/F7.2, spec §10.1.

What only the real stack can answer: that a real worksheet's own shelves, a real
categorical `Filter` (via `FILTERED_BY`) and a real `Parameter` (via `DEPENDS_ON`)
combine into real `ParityCase` nodes with a real, stable `case_key`; that re-deriving
against unchanged source data writes nothing new; that a case whose source has drifted
away is really retired; that the charter's own bound really caps the sheet's case count
and the excess is really recorded on the suite; and that every new route drives its own
real role gate.
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

from astra_graph.case_derivation import (  # noqa: E402
    CaseDerivationError,
    CaseDerivationService,
    PostgresParitySuiteStore,
)
from astra_graph.config import Settings  # noqa: E402
from astra_graph.events import source_for  # noqa: E402
from astra_graph.graph import AgeGraphRepository, create_pool  # noqa: E402
from astra_graph.graph.queries import NODE_INDEX_TABLE, accessor  # noqa: E402
from astra_graph.ids import new_ulid  # noqa: E402
from astra_graph.migrations import run as run_migrations  # noqa: E402
from astra_graph.ontology import EDGE_LABELS, NODE_LABELS  # noqa: E402
from astra_graph.principal import Principal  # noqa: E402
from astra_graph.tolerance_charter import DEFAULT_CHARTER, ParamRule, ToleranceCharter  # noqa: E402
from astra_graph.writes import EdgeWrite, GraphWriter, NodeWrite  # noqa: E402

PRINCIPAL = Principal("agent:harvester", run_id="run-harvester")
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
    """Function-scoped -- `ParityCase`/the suite are per-MU, but a stray, differently-
    shaped case from an earlier test in a shared graph could otherwise pollute a later
    test's own 'which cases are live for this MU' assertions."""
    config = _settings(f"astra_case_derivation_{new_ulid()[10:22].lower()}")

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
                "public.parity_suite",
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


@pytest.fixture
async def estate(settings: Settings):
    """One workbook, one sheet: Desk (dimension) x MarginCalc (measure), a categorical
    Region filter with three members, and a Growth Rate parameter with two observed
    values -- everything §10.1's own worked example needs."""
    pool = await create_pool(settings)
    try:
        repository = AgeGraphRepository(pool, graph_name=settings.graph_name)
        writer = GraphWriter(repository, event_source=source_for(settings.graph_name))
        suite_store = PostgresParitySuiteStore(pool, graph_name=settings.graph_name)
        service = CaseDerivationService(pool, graph_name=settings.graph_name, writer=writer, suite_store=suite_store)
        suffix = new_ulid()[10:18].lower()

        site = await _write(writer, "Site", luid=f"s-{suffix}", name=f"RQA {suffix}")
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
            writer, "CalculatedField", name="MarginCalc", formula="SUM([Margin])",
            formula_ast={"kind": "FUNCTION", "name": "SUM", "children": [], "detail": {}},
        )
        await _edge(writer, "HAS_FIELD", datasource, margin_calc)

        growth_rate = await _write(
            writer, "Parameter", name="Growth Rate", datatype="real", domain="list",
            default="0.05", current_values_seen=["0.05", "0.10"],
        )
        await _edge(writer, "DEPENDS_ON", margin_calc, growth_rate)

        region_filter = await _write(
            writer, "Filter", field_ref="Region", type="categorical",
            values={"members": ["EMEA", "APAC", "APJ"]}, context_flag=False,
        )

        sheet = await _write(
            writer, "Worksheet", name="Bar sheet", mark_type="bar",
            rows_shelf=["Desk"], cols_shelf=["MarginCalc"], marks_shelf=[],
        )
        await _edge(writer, "CONTAINS", book, sheet)
        await _edge(writer, "USES_DATASOURCE", sheet, datasource)
        await _edge(writer, "FILTERED_BY", sheet, region_filter)

        yield {
            "pool": pool, "settings": settings, "writer": writer, "suite_store": suite_store,
            "service": service, "workbook": book, "sheet": sheet, "margin_calc": margin_calc,
            "growth_rate": growth_rate, "region_filter": region_filter,
        }
    finally:
        await pool.close()


def _charter(max_values: int = 12) -> ToleranceCharter:
    return ToleranceCharter(params=ParamRule(enumerate_max_values=max_values, enumerate_strategy="DEFAULT_PLUS_OBSERVED"))


async def _live_cases(pool: asyncpg.Pool, graph_name: str, mu_ref: str) -> dict[str, dict[str, Any]]:
    from astra_graph.lineage import hydrate

    async with pool.acquire() as conn:
        rows = await conn.fetch(
            f"""SELECT id FROM {NODE_INDEX_TABLE}
             WHERE graph = $1 AND kind = 'node' AND label = 'ParityCase' AND retired_at IS NULL""",
            graph_name,
        )
        ids = [row["id"] for row in rows]
        cases = await hydrate(conn, graph_name, "ParityCase", ids)
    return {cid: props for cid, props in cases.items() if props.get("mu_ref") == mu_ref}


# ------------------------------------------------------------------------------ deriving


async def test_deriving_writes_real_parity_cases_with_grain_measures_and_case_key(estate) -> None:
    result = await estate["service"].derive(
        estate["workbook"], charter_version="1", charter=_charter(), principal=PARITY_ENGINEER,
    )
    assert result["cases_written"] > 0

    cases = await _live_cases(estate["pool"], estate["settings"].graph_name, estate["workbook"])
    assert cases
    for properties in cases.values():
        assert properties["grain"] == ["Desk"]
        assert properties["measures"] == ["MarginCalc"]
        assert properties["case_key"].startswith("sha256:")
        assert properties["state"] == "DERIVED"


async def test_deriving_combines_the_categorical_filter_and_the_parameter(estate) -> None:
    result = await estate["service"].derive(
        estate["workbook"], charter_version="1", charter=_charter(), principal=PARITY_ENGINEER,
    )
    # 4 filter contexts (default + 3 Region members) x 2 param combos (0.05, 0.10) = 8
    assert result["suite"]["total_combinations"] == 8
    assert result["cases_written"] == 8


async def test_re_deriving_against_unchanged_data_writes_nothing_new(estate) -> None:
    first = await estate["service"].derive(
        estate["workbook"], charter_version="1", charter=_charter(), principal=PARITY_ENGINEER,
    )
    second = await estate["service"].derive(
        estate["workbook"], charter_version="1", charter=_charter(), principal=PARITY_ENGINEER,
    )
    assert first["cases_written"] > 0
    assert second["cases_written"] == 0
    assert second["cases_retired"] == 0

    cases = await _live_cases(estate["pool"], estate["settings"].graph_name, estate["workbook"])
    assert len(cases) == first["cases_written"]


async def test_a_case_whose_source_has_drifted_away_is_retired(estate) -> None:
    await estate["service"].derive(
        estate["workbook"], charter_version="1", charter=_charter(), principal=PARITY_ENGINEER,
    )
    # A hand-written case for this MU that no real derivation would ever produce again.
    stale_id = await _write(
        estate["writer"], "ParityCase", mu_ref=estate["workbook"], sheet_ref=estate["sheet"],
        grain=["Nonexistent"], measures=["Nonexistent"], state="DERIVED", case_key="sha256:stale",
    )
    result = await estate["service"].derive(
        estate["workbook"], charter_version="1", charter=_charter(), principal=PARITY_ENGINEER,
    )
    assert result["cases_retired"] == 1

    async with estate["pool"].acquire() as conn:
        rows = await conn.fetch(
            f"SELECT retired_at FROM {NODE_INDEX_TABLE} WHERE graph = $1 AND id = $2",
            estate["settings"].graph_name, stale_id,
        )
    assert rows[0]["retired_at"] is not None


async def test_deriving_before_any_worksheet_exists_is_refused(estate) -> None:
    with pytest.raises(CaseDerivationError, match="no worksheets"):
        await estate["service"].derive(
            "not-a-real-workbook", charter_version="1", charter=_charter(), principal=PARITY_ENGINEER,
        )


# -------------------------------------------------------------------------- NOT_ENUMERATED


async def test_the_charter_bound_caps_the_case_count_and_records_the_rest(estate) -> None:
    result = await estate["service"].derive(
        estate["workbook"], charter_version="1", charter=_charter(max_values=3), principal=PARITY_ENGINEER,
    )
    assert result["suite"]["total_combinations"] == 8
    assert result["cases_written"] == 3
    assert result["suite"]["not_enumerated_count"] == 5
    assert len(result["suite"]["not_enumerated"]) == 5


async def test_the_suite_is_readable_after_deriving(estate) -> None:
    await estate["service"].derive(
        estate["workbook"], charter_version="7", charter=_charter(max_values=3), principal=PARITY_ENGINEER,
    )
    suite = await estate["service"].suite(estate["workbook"])
    assert suite is not None
    assert suite.mu_ref == estate["workbook"]
    assert suite.charter_version == "7"
    assert suite.sheet_refs == (estate["sheet"],)


async def test_the_suite_is_absent_before_any_derivation(estate) -> None:
    assert await estate["service"].suite(estate["workbook"]) is None


# ---------------------------------------------------------------------------------- API


@pytest.fixture
async def http_client(estate):
    from httpx import ASGITransport, AsyncClient

    from astra_graph.main import create_app

    app = create_app()
    app.state.case_derivation = estate["service"]
    app.state.tolerance_charter = _FakeCharterService()

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://graph-svc") as async_client:
        yield async_client


class _FakeCharterService:
    """A stand-in so the route's own `_charter_service(request).latest()` call doesn't
    need a second isolated graph -- the real Tolerance Charter is S7.1.1's own concern,
    already covered end to end by its own integration suite."""

    async def latest(self) -> Any:
        class _Version:
            version = 1
            charter = DEFAULT_CHARTER

        return _Version()


def _headers(role: str, principal: Principal) -> dict[str, str]:
    from astra_graph.principal import PRINCIPAL_HEADER
    from astra_graph.roles import ROLES_HEADER

    return {PRINCIPAL_HEADER: principal.value, ROLES_HEADER: role}


async def test_derive_over_http_requires_the_parity_engineer_role(estate, http_client) -> None:
    response = await http_client.post(
        f"/v1/workbooks/{estate['workbook']}:derive-parity-cases",
        headers=_headers("programme_manager", PARITY_ENGINEER),
    )
    assert response.status_code == 403


async def test_derive_over_http_succeeds_for_the_parity_engineer(estate, http_client) -> None:
    response = await http_client.post(
        f"/v1/workbooks/{estate['workbook']}:derive-parity-cases",
        headers=_headers("parity_engineer", PARITY_ENGINEER),
    )
    assert response.status_code == 200
    assert response.json()["cases_written"] > 0


async def test_get_suite_over_http_before_derivation_is_a_clean_404(estate, http_client) -> None:
    response = await http_client.get(
        f"/v1/workbooks/{estate['workbook']}/parity-suite",
        headers=_headers("platform_engineer", PARITY_ENGINEER),
    )
    assert response.status_code == 404


async def test_get_suite_over_http_after_derivation(estate, http_client) -> None:
    await http_client.post(
        f"/v1/workbooks/{estate['workbook']}:derive-parity-cases",
        headers=_headers("parity_engineer", PARITY_ENGINEER),
    )
    response = await http_client.get(
        f"/v1/workbooks/{estate['workbook']}/parity-suite",
        headers=_headers("platform_engineer", PARITY_ENGINEER),
    )
    assert response.status_code == 200
    assert response.json()["suite"]["mu_ref"] == estate["workbook"]
