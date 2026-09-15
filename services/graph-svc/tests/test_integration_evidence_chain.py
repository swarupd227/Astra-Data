"""The Evidence Chain, against real PostgreSQL + Apache AGE -- story S11.3.1, opens
F11.3.

What only the real stack can answer: that `advance_chain` really hash-links real
`estate_event`/`svid_record`/`provenance` rows, that the chain is really idempotent,
that a real `GateDecision`/`Verdict`/`ParityRun` write is really categorised correctly,
that `verify_chain` really recomputes and reports the first real break, and that a real
daily root really rolls the previous day's root in.
"""

from __future__ import annotations

import asyncio
import os
import threading
from datetime import UTC, date, datetime, timedelta
from typing import Any

import pytest

pytestmark = pytest.mark.integration

asyncpg = pytest.importorskip("asyncpg")

from astra_graph.config import Settings  # noqa: E402
from astra_graph.context import ContractName  # noqa: E402
from astra_graph.events import source_for  # noqa: E402
from astra_graph.evidence_chain import (  # noqa: E402
    GENESIS_HASH,
    NullChainAnchor,
    advance_chain,
    chain_status,
    compute_daily_root,
    list_daily_roots,
    verify_chain,
)
from astra_graph.graph import AgeGraphRepository, create_pool  # noqa: E402
from astra_graph.graph.queries import accessor  # noqa: E402
from astra_graph.ids import new_ulid  # noqa: E402
from astra_graph.migrations import run as run_migrations  # noqa: E402
from astra_graph.ontology import EDGE_LABELS, NODE_LABELS  # noqa: E402
from astra_graph.principal import Principal  # noqa: E402
from astra_graph.provenance import AgentMode, PostgresProvenanceStore, new_record  # noqa: E402
from astra_graph.workload_identity import (  # noqa: E402
    LocalWorkloadIdentityProvider,
    PostgresSvidStore,
    record_from_svid,
)
from astra_graph.writes import GraphWriter, NodeWrite  # noqa: E402

PRINCIPAL = Principal("agent:harvester", run_id="run-evidence")


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
    """Function-scoped -- a fresh, uniquely-named graph per test, the identical
    isolation `test_integration_case_execution.py`'s own fixture already established."""
    config = _settings(f"astra_evidence_chain_{new_ulid()[10:22].lower()}")

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
                "public.estate_edge_index",
                "public.estate_element_index",
                "public.estate_event",
                "public.evidence_chain_entry",
                "public.evidence_daily_root",
                "public.svid_record",
                "public.provenance",
                "public.retention_policy",
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

        book_id = (await writer.write_nodes(
            [NodeWrite(type="Workbook", properties={"name": "Daily VaR", "luid": f"wb-{new_ulid()}", "revision": "1"})],
            principal=PRINCIPAL,
        ))[0]["properties"]["id"]

        gate_id = (await writer.write_nodes(
            [NodeWrite(type="GateDecision", properties={
                "gate": "G3", "subject_ref": book_id, "decision": "APPROVED",
                "approver": "user:owner@artizent.example",
                "timestamp": datetime.now(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z"),
            })],
            principal=PRINCIPAL,
        ))[0]["properties"]["id"]

        run_id = (await writer.write_nodes(
            [NodeWrite(type="ParityRun", properties={
                "suite_ref": book_id, "charter_version": "1",
                "started": "2027-06-01T09:00:00.000Z", "finished": "2027-06-01T09:00:02.000Z",
                "verdicts": [],
            })],
            principal=PRINCIPAL,
        ))[0]["properties"]["id"]

        svid_store = PostgresSvidStore(pool, graph_name=settings.graph_name)
        provider = LocalWorkloadIdentityProvider()
        svid = await provider.issue(agent_id="steward", run_id="run-agent-1")
        await svid_store.record(record_from_svid(svid))

        provenance_store = PostgresProvenanceStore(pool, graph_name=settings.graph_name)
        provenance_record = await provenance_store.record(new_record(
            artefact_kind="MEASURE", artefact_ref=f"msr_{new_ulid()[10:18]}",
            artefact_content_hash="sha256:deadbeef", agent="transpiler", agent_version="1.4.2",
            mode=AgentMode.GENERATED_PROVED, contract=ContractName.TRANSPILER_CALC,
            subject_id=book_id, context_hash="sha256:cafef00d", graph_version=1,
            created_by="agent:transpiler",
        ))

        yield {
            "pool": pool, "settings": settings, "writer": writer, "book_id": book_id,
            "gate_id": gate_id, "run_id": run_id, "svid": svid, "provenance_id": provenance_record.id,
        }
    finally:
        await pool.close()


# --------------------------------------------------------------------------- advancing


async def test_advancing_chains_every_real_source(estate) -> None:
    result = await advance_chain(estate["pool"], estate["settings"].graph_name)

    # 3 estate_event rows (Workbook, GateDecision, ParityRun) + 1 svid_record + 1 provenance.
    assert result.entries_added == 5
    assert result.tip_seq == 5
    assert result.tip_hash != GENESIS_HASH

    status = await chain_status(estate["pool"], estate["settings"].graph_name)
    assert status["total_entries"] == 5
    assert status["by_category"]["gate_decision"] == 1
    assert status["by_category"]["verdict"] == 1
    assert status["by_category"]["state_transition"] == 1
    assert status["by_category"]["agent_run"] == 1
    assert status["by_category"]["model_call"] == 1


async def test_advancing_twice_is_idempotent(estate) -> None:
    first = await advance_chain(estate["pool"], estate["settings"].graph_name)
    second = await advance_chain(estate["pool"], estate["settings"].graph_name)

    assert first.entries_added == 5
    assert second.entries_added == 0
    assert second.tip_seq == first.tip_seq
    assert second.tip_hash == first.tip_hash


async def test_a_later_advance_picks_up_only_what_is_new(estate) -> None:
    await advance_chain(estate["pool"], estate["settings"].graph_name)

    provenance_store = PostgresProvenanceStore(estate["pool"], graph_name=estate["settings"].graph_name)
    await provenance_store.record(new_record(
        artefact_kind="MEASURE", artefact_ref=f"msr_{new_ulid()[10:18]}",
        artefact_content_hash="sha256:deadbeef2", agent="transpiler", agent_version="1.4.2",
        mode=AgentMode.GENERATED_PROVED, contract=ContractName.TRANSPILER_CALC,
        subject_id=estate["book_id"], context_hash="sha256:cafef00d2", graph_version=1,
        created_by="agent:transpiler",
    ))

    second = await advance_chain(estate["pool"], estate["settings"].graph_name)
    assert second.entries_added == 1
    assert second.tip_seq == 6


# -------------------------------------------------------------------------- verifying


async def test_a_freshly_advanced_chain_verifies_intact(estate) -> None:
    await advance_chain(estate["pool"], estate["settings"].graph_name)
    result = await verify_chain(estate["pool"], estate["settings"].graph_name)

    assert result.intact is True
    assert result.entries_checked == 5
    assert result.first_break is None


async def test_an_empty_chain_verifies_intact_with_nothing_checked(estate) -> None:
    result = await verify_chain(estate["pool"], estate["settings"].graph_name)
    assert result.intact is True
    assert result.entries_checked == 0


async def test_a_tampered_stored_hash_is_reported_as_the_first_break(estate) -> None:
    await advance_chain(estate["pool"], estate["settings"].graph_name)

    async with estate["pool"].acquire() as conn:
        await conn.execute(
            "UPDATE public.evidence_chain_entry SET hash = $1 WHERE graph = $2 AND chain_seq = 2",
            "f" * 64, estate["settings"].graph_name,
        )

    result = await verify_chain(estate["pool"], estate["settings"].graph_name)
    assert result.intact is False
    assert result.first_break is not None
    assert result.first_break.chain_seq == 2
    assert result.entries_checked == 2


async def test_a_tampered_source_row_is_reported_as_the_first_break(estate) -> None:
    await advance_chain(estate["pool"], estate["settings"].graph_name)

    async with estate["pool"].acquire() as conn:
        await conn.execute(
            "UPDATE public.svid_record SET agent_id = 'tampered' WHERE graph = $1 AND jti = $2",
            estate["settings"].graph_name, estate["svid"].jti,
        )

    result = await verify_chain(estate["pool"], estate["settings"].graph_name)
    assert result.intact is False
    assert result.first_break is not None
    assert result.first_break.source_table == "svid_record"


# ----------------------------------------------------------------------- daily roots


async def test_a_daily_root_cannot_be_computed_for_today_or_the_future(estate) -> None:
    await advance_chain(estate["pool"], estate["settings"].graph_name)
    today = datetime.now(UTC).date()

    with pytest.raises(Exception, match="not yet a closed day"):
        await compute_daily_root(estate["pool"], estate["settings"].graph_name, today)


async def test_a_day_with_nothing_chained_on_it_has_no_root(estate) -> None:
    long_ago = date(2020, 1, 1)
    root = await compute_daily_root(estate["pool"], estate["settings"].graph_name, long_ago)
    assert root is None


async def test_a_real_backdated_day_computes_a_real_root_and_is_not_recomputed(estate) -> None:
    await advance_chain(estate["pool"], estate["settings"].graph_name)
    yesterday = (datetime.now(UTC) - timedelta(days=1)).date()

    async with estate["pool"].acquire() as conn:
        await conn.execute(
            "UPDATE public.evidence_chain_entry SET chained_at = $1 WHERE graph = $2",
            datetime.combine(yesterday, datetime.min.time(), tzinfo=UTC) + timedelta(hours=12),
            estate["settings"].graph_name,
        )

    root = await compute_daily_root(estate["pool"], estate["settings"].graph_name, yesterday)
    assert root is not None
    assert root.entry_count == 5
    assert root.prev_root_hash == GENESIS_HASH
    assert root.root_hash != GENESIS_HASH

    again = await compute_daily_root(estate["pool"], estate["settings"].graph_name, yesterday)
    assert again is None, "a day's root is computed once, never recomputed"

    roots = await list_daily_roots(estate["pool"], estate["settings"].graph_name)
    assert len(roots) == 1
    assert roots[0].root_hash == root.root_hash


async def test_a_second_days_root_rolls_the_first_in(estate) -> None:
    await advance_chain(estate["pool"], estate["settings"].graph_name)
    two_days_ago = (datetime.now(UTC) - timedelta(days=2)).date()
    yesterday = (datetime.now(UTC) - timedelta(days=1)).date()

    async with estate["pool"].acquire() as conn:
        await conn.execute(
            "UPDATE public.evidence_chain_entry SET chained_at = $1 "
            "WHERE graph = $2 AND chain_seq <= 3",
            datetime.combine(two_days_ago, datetime.min.time(), tzinfo=UTC) + timedelta(hours=12),
            estate["settings"].graph_name,
        )
        await conn.execute(
            "UPDATE public.evidence_chain_entry SET chained_at = $1 "
            "WHERE graph = $2 AND chain_seq > 3",
            datetime.combine(yesterday, datetime.min.time(), tzinfo=UTC) + timedelta(hours=12),
            estate["settings"].graph_name,
        )

    first_root = await compute_daily_root(estate["pool"], estate["settings"].graph_name, two_days_ago)
    second_root = await compute_daily_root(estate["pool"], estate["settings"].graph_name, yesterday)

    assert first_root is not None and second_root is not None
    assert second_root.prev_root_hash == first_root.root_hash
    assert second_root.root_hash != first_root.root_hash


async def test_the_null_anchor_leaves_a_root_real_but_unanchored(estate) -> None:
    await advance_chain(estate["pool"], estate["settings"].graph_name)
    yesterday = (datetime.now(UTC) - timedelta(days=1)).date()
    async with estate["pool"].acquire() as conn:
        await conn.execute(
            "UPDATE public.evidence_chain_entry SET chained_at = $1 WHERE graph = $2",
            datetime.combine(yesterday, datetime.min.time(), tzinfo=UTC) + timedelta(hours=12),
            estate["settings"].graph_name,
        )

    root = await compute_daily_root(
        estate["pool"], estate["settings"].graph_name, yesterday, anchor=NullChainAnchor()
    )
    assert root is not None
    assert root.anchor_kind is None
    assert root.anchored_at is None
