#!/usr/bin/env python
"""Verify that the event stream accounts for the live graph.

S1.1.3: "A replay of the event stream from empty produces a graph identical to the live
graph (verified by a nightly CI job on the test estate)."

Rebuilds the estate from the mutation events into a scratch Apache AGE graph, compares it
against the live one element by element, and exits non-zero on any difference. Prints
what differs, not just that something does — a failure here means the record and the
estate disagree, and the first question is where.

    python tools/verify_replay.py                     verify, then drop the scratch graph
    python tools/verify_replay.py --keep              leave the scratch graph for inspection
    python tools/verify_replay.py --into other_graph  name the scratch graph
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        _stream.reconfigure(encoding="utf-8", errors="replace")


SERVICE_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SERVICE_ROOT / "src"))

import asyncpg  # noqa: E402

from astra_graph.config import settings  # noqa: E402
from astra_graph.graph import AgeGraphRepository, create_pool  # noqa: E402
from astra_graph.graph.queries import accessor  # noqa: E402
from astra_graph.logging_setup import configure_logging  # noqa: E402
from astra_graph.ontology import EDGE_LABELS, NODE_LABELS  # noqa: E402
from astra_graph.replay import compare, replay  # noqa: E402

MAX_REPORTED_DIFFERENCES = 40


async def _prepare_scratch_graph(conn: asyncpg.Connection, graph: str) -> None:
    """An empty graph with the same labels and indexes as a migrated one."""
    await conn.execute("LOAD 'age'")
    await conn.execute('SET search_path = ag_catalog, "$user", public')
    exists = await conn.fetchval(
        "SELECT EXISTS (SELECT 1 FROM ag_catalog.ag_graph WHERE name = $1)", graph
    )
    if exists:
        await conn.execute("SELECT ag_catalog.drop_graph($1, true)", graph)
    await conn.execute("SELECT ag_catalog.create_graph($1)", graph)

    for label in sorted(NODE_LABELS):
        await conn.execute("SELECT ag_catalog.create_vlabel($1, $2)", graph, label)
        await conn.execute(f'CREATE INDEX ON {graph}."{label}" USING BTREE ({accessor("id")})')
    for label in sorted(EDGE_LABELS):
        await conn.execute("SELECT ag_catalog.create_elabel($1, $2)", graph, label)
        await conn.execute(f'CREATE INDEX ON {graph}."{label}" USING BTREE ({accessor("id")})')

    await _drop_scratch_index_rows(conn, graph)


async def _drop_scratch_index_rows(conn: asyncpg.Connection, graph: str) -> None:
    await conn.execute("DELETE FROM public.estate_edge_index WHERE graph = $1", graph)
    await conn.execute("DELETE FROM public.estate_element_index WHERE graph = $1", graph)


async def _teardown(conn: asyncpg.Connection, graph: str) -> None:
    await conn.execute("LOAD 'age'")
    await _drop_scratch_index_rows(conn, graph)
    await conn.execute("SELECT ag_catalog.drop_graph($1, true)", graph)


async def verify(scratch_graph: str, *, keep: bool) -> int:
    config = settings()
    if scratch_graph == config.graph_name:
        print("the scratch graph must not be the live graph", file=sys.stderr)
        return 2

    conn = await asyncpg.connect(dsn=config.dsn)
    try:
        await _prepare_scratch_graph(conn, scratch_graph)
    finally:
        await conn.close()

    pool = await create_pool(config)
    try:
        live = AgeGraphRepository(pool, graph_name=config.graph_name)
        target = AgeGraphRepository(pool, graph_name=scratch_graph)

        print(f"replaying {config.graph_name} into {scratch_graph} ...")
        result = await replay(live, target)
        print(
            f"applied {result.events_applied} events "
            f"({result.nodes} node upserts, {result.edges} edge upserts, "
            f"{result.retirements} retirements) up to sequence {result.last_sequence}"
        )

        comparison = compare(await live.dump(), await target.dump())
    finally:
        await pool.close()

    if not keep:
        conn = await asyncpg.connect(dsn=config.dsn)
        try:
            await _teardown(conn, scratch_graph)
        finally:
            await conn.close()

    if comparison.identical:
        print(f"OK — {comparison.summary()}")
        return 0

    print(f"FAIL — {comparison.summary()}", file=sys.stderr)
    for difference in comparison.differences[:MAX_REPORTED_DIFFERENCES]:
        print(f"  {difference.kind} {difference.element_id}: {difference.detail}", file=sys.stderr)
    remaining = len(comparison.differences) - MAX_REPORTED_DIFFERENCES
    if remaining > 0:
        print(f"  ... and {remaining} more", file=sys.stderr)
    print(
        "\nThe event stream and the graph disagree. Either a mutation reached the graph "
        "without its event, or an event does not describe what it did.",
        file=sys.stderr,
    )
    return 1


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--into", default=None, help="scratch graph name")
    parser.add_argument("--keep", action="store_true", help="do not drop the scratch graph")
    args = parser.parse_args()

    configure_logging("WARNING")
    scratch = args.into or f"{settings().graph_name}_replay"
    return asyncio.run(verify(scratch, keep=args.keep))


if __name__ == "__main__":
    raise SystemExit(main())
