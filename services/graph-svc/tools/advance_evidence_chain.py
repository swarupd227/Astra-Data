#!/usr/bin/env python
"""Hash-link every not-yet-chained state transition, gate decision, agent run, model
call and verdict onto this graph's own Evidence Chain -- story S11.3.1, opens F11.3.

Idempotent: run it as often as you like. Calls `advance_chain` in a loop until it stops
finding anything new, so a large backlog is processed in bounded batches rather than one
unbounded transaction.

    python tools/advance_evidence_chain.py                 the deployment's own graph
    python tools/advance_evidence_chain.py --graph other    a specific graph
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

from astra_graph.config import settings  # noqa: E402
from astra_graph.evidence_chain import BATCH_SIZE, advance_chain  # noqa: E402
from astra_graph.graph import create_pool  # noqa: E402
from astra_graph.logging_setup import configure_logging  # noqa: E402


async def advance(graph_name: str) -> int:
    config = settings()
    pool = await create_pool(config)
    try:
        total_added = 0
        while True:
            result = await advance_chain(pool, graph_name)
            total_added += result.entries_added
            print(
                f"chained {result.entries_added} entries -- tip now seq={result.tip_seq} "
                f"hash={result.tip_hash[:12]}..."
            )
            if result.entries_added < 3 * BATCH_SIZE:
                break
        print(f"done -- {total_added} entries chained in this run")
    finally:
        await pool.close()
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--graph", default=None, help="graph name (default: the deployment's own)")
    args = parser.parse_args()

    configure_logging("WARNING")
    graph_name = args.graph or settings().graph_name
    return asyncio.run(advance(graph_name))


if __name__ == "__main__":
    raise SystemExit(main())
