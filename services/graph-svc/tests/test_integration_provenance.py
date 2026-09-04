"""Reading the graph at a past version, against real PostgreSQL + Apache AGE.

This file carries most of S1.3.2's weight. ``HistoricalGraphReader`` answers from indexed
lookups over the outbox rather than by replaying it, and that is a second implementation of
"what the graph held" — so the thing worth testing is that it agrees with the first. Replay
is the definition (S1.1.3 proves a replay from empty reproduces the graph exactly); the
historical reader is an optimisation of it, and an optimisation that disagreed would make
every audit wrong in a way nothing else would catch.

So the central test replays the stream to a version, reads the same version through the
indexed reader, and requires the two to produce the same context hash.
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
from astra_graph.context import ContextAssembler, ContractName  # noqa: E402
from astra_graph.events import source_for  # noqa: E402
from astra_graph.graph import AgeGraphRepository, create_pool  # noqa: E402
from astra_graph.graph.queries import accessor  # noqa: E402
from astra_graph.ids import new_ulid  # noqa: E402
from astra_graph.migrations import run as run_migrations  # noqa: E402
from astra_graph.ontology import EDGE_LABELS, NODE_LABELS  # noqa: E402
from astra_graph.principal import Principal  # noqa: E402
from astra_graph.provenance import (  # noqa: E402
    AgentMode,
    ContextVerifier,
    PostgresProvenanceStore,
    VerificationOutcome,
    new_record,
)
from astra_graph.retention import PostgresProgrammeStore, prunable_before  # noqa: E402
from astra_graph.versions import HistoricalGraphReader  # noqa: E402
from astra_graph.writes import GraphWriter  # noqa: E402

from .conftest import seed_estate  # noqa: E402
from .fakes import InMemoryGraphRepository  # noqa: E402

PRINCIPAL = Principal("agent:transpiler", run_id="run-provenance")


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
    config = _settings(f"astra_prov_{new_ulid()[10:22].lower()}")

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
                "public.provenance",
                "public.programme",
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

        async def assembler_at(version: int) -> ContextAssembler:
            return ContextAssembler(
                HistoricalGraphReader(
                    pool, graph_name=settings.graph_name, version=version
                )
            )

        async def current_version() -> int:
            version, _at = await repository.current_version()
            return version

        yield {
            "pool": pool,
            "settings": settings,
            "repository": repository,
            "writer": writer,
            "seeded": seeded,
            "verifier": ContextVerifier(assembler_at, current_version=current_version),
            "assembler_at": assembler_at,
        }
    finally:
        await pool.close()


async def _assemble_now(stack) -> tuple[Any, int]:
    assembled = await ContextAssembler(stack["repository"]).assemble(
        ContractName.TRANSPILER_CALC, stack["seeded"]["calc"]
    )
    version, _at = await stack["repository"].current_version()
    return assembled, version


# ------------------------------- the historical reader agrees with a replay of the same


async def test_the_indexed_reader_agrees_with_a_replay_of_the_same_version(stack) -> None:
    """The test this whole file exists for.

    Replay is the definition of what the graph held (S1.1.3). The historical reader is an
    indexed shortcut to the same answer. If they disagreed, every audit would be wrong and
    nothing else in the suite would notice.
    """
    _assembled, version = await _assemble_now(stack)

    # The indexed reader, against PostgreSQL.
    indexed = await (await stack["assembler_at"](version)).assemble(
        ContractName.TRANSPILER_CALC, stack["seeded"]["calc"]
    )

    # The same version, rebuilt by replaying the event stream from empty.
    replayed_target = InMemoryGraphRepository()
    await _replay_into(stack["repository"], replayed_target, version)
    replayed = await ContextAssembler(replayed_target).assemble(
        ContractName.TRANSPILER_CALC, stack["seeded"]["calc"]
    )

    assert indexed.context_hash == replayed.context_hash
    assert indexed.document == replayed.document


async def test_the_reader_reproduces_the_context_the_agent_saw(stack) -> None:
    """S1.3.2 criterion 1, against the real store."""
    assembled, version = await _assemble_now(stack)

    historical_view = await stack["assembler_at"](version)
    again = await historical_view.assemble(
        ContractName.TRANSPILER_CALC, stack["seeded"]["calc"]
    )

    assert again.context_hash == assembled.context_hash


async def test_a_record_verifies_after_the_graph_has_moved_on(stack) -> None:
    """The reason a version is recorded at all."""
    assembled, version = await _assemble_now(stack)
    record = new_record(
        artefact_kind="MEASURE",
        artefact_ref=f"msr_{new_ulid()[10:18]}",
        artefact_content_hash="sha256:deadbeef",
        agent="transpiler",
        agent_version="1.4.2",
        mode=AgentMode.GENERATED_PROVED,
        contract=ContractName.TRANSPILER_CALC,
        subject_id=stack["seeded"]["calc"],
        context_hash=assembled.context_hash,
        graph_version=version,
        created_by=PRINCIPAL.value,
    )
    store = PostgresProvenanceStore(stack["pool"], graph_name=stack["settings"].graph_name)
    stored = await store.record(record)

    # The estate moves on: a re-harvest changes the formula.
    await stack["writer"].set_node_properties(
        stack["seeded"]["calc"],
        {"formula": "SUM([M]) / SUM([R]) * 100"},
        principal=Principal("agent:harvester"),
    )
    current = await ContextAssembler(stack["repository"]).assemble(
        ContractName.TRANSPILER_CALC, stack["seeded"]["calc"]
    )
    assert current.context_hash != assembled.context_hash

    verification = await stack["verifier"].verify_record(await store.get(stored.id))

    assert verification.outcome is VerificationOutcome.MATCH
    assert verification.recomputed_hash == assembled.context_hash


async def test_a_context_at_a_later_version_reflects_the_change(stack) -> None:
    """The other direction: history is not frozen, it is addressed."""
    before, version_before = await _assemble_now(stack)

    await stack["writer"].set_node_properties(
        stack["seeded"]["calc"],
        {"formula": "SUM([M]) / SUM([R]) / 2"},
        principal=Principal("agent:harvester"),
    )
    version_after, _at = await stack["repository"].current_version()

    at_before = await (await stack["assembler_at"](version_before)).assemble(
        ContractName.TRANSPILER_CALC, stack["seeded"]["calc"]
    )
    at_after = await (await stack["assembler_at"](version_after)).assemble(
        ContractName.TRANSPILER_CALC, stack["seeded"]["calc"]
    )

    assert at_before.context_hash == before.context_hash
    assert at_after.context_hash != before.context_hash
    assert at_after.document["subject"]["formula"].endswith("/ 2")


async def test_a_retired_node_is_absent_from_a_later_version_and_present_earlier(
    stack,
) -> None:
    """Retirement is a mutation like any other, so it has a version on either side of it."""
    seeded = stack["seeded"]
    before, version_before = await _assemble_now(stack)
    assert [p["id"] for p in before.document["parameters"]] == [seeded["parameter"]]

    await stack["writer"].retire_node(
        seeded["parameter"],
        reason="Superseded by a model-side date table",
        principal=Principal("user:a.mehta@artizent.example"),
    )
    version_after, _at = await stack["repository"].current_version()

    at_before = await (await stack["assembler_at"](version_before)).assemble(
        ContractName.TRANSPILER_CALC, seeded["calc"]
    )
    at_after = await (await stack["assembler_at"](version_after)).assemble(
        ContractName.TRANSPILER_CALC, seeded["calc"]
    )

    assert [p["id"] for p in at_before.document["parameters"]] == [seeded["parameter"]]
    assert at_after.document["parameters"] == []


async def test_the_subject_does_not_exist_before_it_was_written(stack) -> None:
    verification = await stack["verifier"].verify(
        contract=ContractName.TRANSPILER_CALC,
        subject_id=stack["seeded"]["calc"],
        graph_version=1,
        claimed_hash="sha256:" + "0" * 64,
    )

    assert verification.outcome is VerificationOutcome.UNVERIFIABLE
    assert "could not be re-materialised" in verification.detail


async def test_a_version_beyond_the_stream_is_refused(stack) -> None:
    _assembled, version = await _assemble_now(stack)

    verification = await stack["verifier"].verify(
        contract=ContractName.TRANSPILER_CALC,
        subject_id=stack["seeded"]["calc"],
        graph_version=version + 10_000,
        claimed_hash="sha256:" + "0" * 64,
    )

    assert verification.outcome is VerificationOutcome.UNVERIFIABLE
    assert "future state" in verification.detail


async def test_a_tampered_hash_is_a_mismatch_against_the_real_store(stack) -> None:
    _assembled, version = await _assemble_now(stack)

    verification = await stack["verifier"].verify(
        contract=ContractName.TRANSPILER_CALC,
        subject_id=stack["seeded"]["calc"],
        graph_version=version,
        claimed_hash="sha256:" + "0" * 64,
    )

    assert verification.outcome is VerificationOutcome.MISMATCH
    assert verification.recomputed_hash is not None


# ---------------------------------------------------------------- programmes


async def test_a_programme_round_trips_and_sets_the_retention_floor(stack) -> None:
    store = PostgresProgrammeStore(stack["pool"], graph_name=stack["settings"].graph_name)
    programme = await store.open_programme(
        name=f"RQA {new_ulid()[10:16]}",
        started_at="2027-01-01T00:00:00Z",
        created_by="user:pm@artizent.example",
    )

    assert prunable_before(await store.programmes()).prunable_before is None

    closed = await store.close_programme(programme.id, closed_at="2027-09-30T00:00:00Z")
    assert closed is not None
    assert closed.retain_until() == "2028-09-30T00:00:00.000Z"

    assert await store.close_programme(programme.id, closed_at="2027-02-01T00:00:00Z") is None


async def _replay_into(source: Any, target: InMemoryGraphRepository, version: int) -> None:
    """Replay the source's event stream up to a version into an empty graph."""
    from astra_graph.replay import replay

    class _Bounded:
        async def read_events(
            self, *, after: int = 0, limit: int = 1000, subject: str | None = None
        ) -> Any:
            events = await source.read_events(after=after, limit=limit)
            return [event for event in events if event.sequence <= version]

        async def dump(self) -> Any:  # pragma: no cover - replay does not call this
            return await source.dump()

    await replay(_Bounded(), target)

