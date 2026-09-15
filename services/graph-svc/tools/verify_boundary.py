#!/usr/bin/env python
"""S11.4.1's own boundary test -- "a CI and on-demand check sends sentinel row data
through every agent path and asserts it never appears in a gateway request log". This
is the CLI shape `.github/workflows/nightly.yml`'s own cron calls; the console's
`POST /v1/data-handling:verify-boundary` route calls the identical `run_boundary_test`
function for the "on demand" half of that same AC bullet.

    python tools/verify_boundary.py                 the deployment's own graph
    python tools/verify_boundary.py --graph other    a specific graph
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

from astra_graph.artefacts import PostgresArtefactStore  # noqa: E402
from astra_graph.config import settings  # noqa: E402
from astra_graph.data_handling import run_boundary_test  # noqa: E402
from astra_graph.events import source_for  # noqa: E402
from astra_graph.graph import AgeGraphRepository, create_pool  # noqa: E402
from astra_graph.logging_setup import configure_logging  # noqa: E402
from astra_graph.writes import GraphWriter  # noqa: E402


async def verify(graph_name: str) -> int:
    config = settings()
    pool = await create_pool(config)
    try:
        repository = AgeGraphRepository(pool, graph_name=graph_name)
        writer = GraphWriter(repository, event_source=source_for(graph_name))
        artefact_store = PostgresArtefactStore(pool, graph_name=graph_name)
        result = await run_boundary_test(pool, graph_name, artefact_store=artefact_store, writer=writer)
    finally:
        await pool.close()

    if result.passed:
        print(result.detail)  # already starts with "OK -- "
        return 0

    print(result.detail, file=sys.stderr)  # already starts with "FAIL -- "
    print(f"  sentinel: {result.sentinel}", file=sys.stderr)
    return 1


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--graph", default=None, help="graph name (default: the deployment's own)")
    args = parser.parse_args()

    configure_logging("WARNING")
    graph_name = args.graph or settings().graph_name
    return asyncio.run(verify(graph_name))


if __name__ == "__main__":
    raise SystemExit(main())
