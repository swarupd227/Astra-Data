"""Context assembly against a real PostgreSQL + Apache AGE.

What only the real store can answer. The determinism criterion is the reason this file
exists: in the in-memory repository, two reads of the same data come back in the order a
Python dictionary happens to hold them, so a hash that depended on row order would still
look stable. PostgreSQL is under no such obligation — it may return rows in a different
order between two identical queries, and AGE hydrates through its own label tables — so
this is where "two calls with the same graph state produce the same hash" is actually
tested.
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
from astra_graph.context import (  # noqa: E402
    ContextAssembler,
    ContextBudgetExceededError,
    ContractName,
    canonical_json,
)
from astra_graph.events import source_for  # noqa: E402
from astra_graph.graph import AgeGraphRepository, create_pool  # noqa: E402
from astra_graph.graph.queries import accessor  # noqa: E402
from astra_graph.ids import new_ulid  # noqa: E402
from astra_graph.migrations import run as run_migrations  # noqa: E402
from astra_graph.ontology import EDGE_LABELS, NODE_LABELS  # noqa: E402
from astra_graph.principal import Principal  # noqa: E402
from astra_graph.writes import GraphWriter, NodeWrite  # noqa: E402

from .conftest import seed_estate  # noqa: E402

PRINCIPAL = Principal("agent:transpiler", run_id="run-context-integration")

MARGIN_AST = {
    "op": "DIV",
    "args": [
        {"fn": "SUM", "arg": {"field": "Margin"}},
        {"fn": "SUM", "arg": {"field": "Revenue"}},
    ],
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


@pytest.fixture(scope="module")
def settings() -> Settings:
    config = _settings(f"astra_ctx_{new_ulid()[10:22].lower()}")

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
            ):
                await conn.execute(f"DELETE FROM {table} WHERE graph = $1", config.graph_name)
            await conn.execute("SELECT ag_catalog.drop_graph($1, true)", config.graph_name)
        finally:
            await conn.close()

    if not _run_off_loop(setup):
        pytest.skip("PostgreSQL with Apache AGE not reachable")

    yield config
    _run_off_loop(teardown)


@pytest.fixture
async def stack(settings: Settings):
    pool = await create_pool(settings)
    try:
        repository = AgeGraphRepository(pool, graph_name=settings.graph_name)
        writer = GraphWriter(repository, event_source=source_for(settings.graph_name))
        seeded = await seed_estate(writer, suffix=f"-{new_ulid()[10:18].lower()}")
        yield ContextAssembler(repository, adapter="tableau"), writer, seeded
    finally:
        await pool.close()


async def _assemble(assembler, seeded):
    return await assembler.assemble(ContractName.TRANSPILER_CALC, seeded["calc"])


async def test_the_contract_materialises_from_the_real_store(stack) -> None:
    """S1.3.1 criterion 3, through AGE's own hydration."""
    assembler, _writer, seeded = stack

    assembled = await _assemble(assembler, seeded)
    document = assembled.document

    assert document["subject"]["id"] == seeded["calc"]
    assert [c["id"] for c in document["dependency_calculations"]] == [seeded["nested_calc"]]
    assert [f["id"] for f in document["dependency_fields"]] == [seeded["field"]]
    assert [p["id"] for p in document["parameters"]] == [seeded["parameter"]]
    assert [t["id"] for t in document["model_tables"]] == [seeded["model_table"]]
    assert document["model_columns"] == [
        {
            "from_id": seeded["field"],
            "to_id": seeded["model_table"],
            "target_column": "notional",
        }
    ]


async def test_the_hash_is_stable_across_calls_against_postgres(stack) -> None:
    """S1.3.1 criterion 2, where it can actually fail.

    Five assemblies rather than two: an ordering bug that depends on which rows PostgreSQL
    happens to return first will not necessarily show up on the second call.
    """
    assembler, _writer, seeded = stack

    hashes = {(await _assemble(assembler, seeded)).context_hash for _ in range(5)}

    assert len(hashes) == 1, f"the same graph state produced {len(hashes)} different hashes"


async def test_the_hash_is_stable_across_two_independent_assemblers(stack, settings) -> None:
    """A fresh pool, fresh connections, and no shared state: still the same hash.

    This is the property the gateway's context-hash cache depends on (§5.4) — two
    processes assembling the same calculation must agree, or the cache never hits.
    """
    assembler, _writer, seeded = stack
    first = await _assemble(assembler, seeded)

    pool = await create_pool(settings)
    try:
        other = ContextAssembler(
            AgeGraphRepository(pool, graph_name=settings.graph_name), adapter="tableau"
        )
        second = await _assemble(other, seeded)
    finally:
        await pool.close()

    assert first.context_hash == second.context_hash
    assert first.payload == second.payload


async def test_a_matching_pattern_is_carried_from_the_real_store(stack) -> None:
    """S1.3.1 criterion 3's last clause, with the signature stored as AGE holds JSON."""
    assembler, writer, seeded = stack
    await writer.set_node_properties(
        seeded["calc"], {"formula_ast": MARGIN_AST}, principal=PRINCIPAL
    )
    created = await writer.write_nodes(
        [
            NodeWrite(
                type="Pattern",
                properties={
                    "name": f"Ratio of sums {seeded['calc'][:6]}",
                    "class": "C2",
                    "source_signature": {
                        "ast_shape": "DIV(SUM(a), SUM(b))",
                        "adapter": "tableau",
                    },
                    "target_template": "DIVIDE(SUM({a}), SUM({b}))",
                    "promotion_state": "ACTIVE",
                },
            )
        ],
        principal=PRINCIPAL,
    )
    pattern_id = str(created[0]["properties"]["id"])

    document = (await _assemble(assembler, seeded)).document

    assert pattern_id in [p["id"] for p in document["patterns"]]
    matched = next(p for p in document["patterns"] if p["id"] == pattern_id)
    assert matched["source_signature"]["ast_shape"] == "DIV(SUM(a), SUM(b))"


async def test_size_is_reported_and_the_budget_is_enforced(stack) -> None:
    """S1.3.1 criterion 4, against a document the real store produced."""
    assembler, writer, seeded = stack

    assembled = await _assemble(assembler, seeded)
    assert assembled.size_bytes == len(canonical_json(assembled.document))
    assert assembled.usage()["bytes_used"] < 1

    await writer.set_node_properties(
        seeded["calc"], {"formula": "X" * 400_000}, principal=PRINCIPAL
    )

    with pytest.raises(ContextBudgetExceededError) as raised:
        await _assemble(assembler, seeded)

    assert raised.value.actual["bytes"] > raised.value.budget["bytes"]
