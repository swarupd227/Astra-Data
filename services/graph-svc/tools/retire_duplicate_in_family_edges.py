#!/usr/bin/env python
"""One-time cleanup: retire a workbook's own stale duplicate ``IN_FAMILY`` edges.

A real, found-live bug (fixed in ``cartographer.Cartographer.run()``): retiring a
stale ``ModelFamily`` node on a re-cluster never retired a re-clustered member's own
prior ``IN_FAMILY`` edge (``retire_node`` has no cascade to edges pointing at the
node) -- so an estate clustered more than once before the fix can have workbooks with
two or more live ``IN_FAMILY`` edges. Every future clustering run now retires-then-
writes correctly, but an estate the pre-fix code already touched needs this one-time
convergence: keep each affected workbook's most recently created live edge, retire the
rest.

Idempotent and safe to run against a healthy estate (nothing to retire, prints "0
duplicate(s)"), and safe to run more than once.

    python tools/retire_duplicate_in_family_edges.py
    python tools/retire_duplicate_in_family_edges.py --dry-run
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

from astra_graph.cartographer import (  # noqa: E402
    find_duplicate_in_family_edges,
    retire_duplicate_in_family_edges,
)
from astra_graph.config import settings  # noqa: E402
from astra_graph.events import source_for  # noqa: E402
from astra_graph.graph import AgeGraphRepository, create_pool  # noqa: E402
from astra_graph.logging_setup import configure_logging  # noqa: E402
from astra_graph.principal import Principal  # noqa: E402
from astra_graph.writes import GraphWriter  # noqa: E402

PRINCIPAL = Principal("agent:cartographer", run_id="cleanup-duplicate-in-family")


async def _run(dry_run: bool) -> int:
    config = settings()
    pool = await create_pool(config)
    try:
        duplicates = await find_duplicate_in_family_edges(pool, config.graph_name)
        stale_count = sum(len(edges) - 1 for edges in duplicates.values())
        if dry_run:
            for workbook_id, edges in duplicates.items():
                kept, stale = edges[0], edges[1:]
                print(
                    f"{workbook_id}: keeping {kept['family_id']} "
                    f"(created {kept['created_at']}); would retire "
                    f"{[e['family_id'] for e in stale]}"
                )
            print(f"{len(duplicates)} workbook(s), {stale_count} duplicate edge(s) -- dry run, nothing retired")
            return 0

        repository = AgeGraphRepository(pool, graph_name=config.graph_name)
        writer = GraphWriter(repository, event_source=source_for(config.graph_name))
        retired = await retire_duplicate_in_family_edges(
            pool, config.graph_name, writer, principal=PRINCIPAL
        )
        print(f"{len(duplicates)} workbook(s), {len(retired)} duplicate edge(s) retired")
        return 0
    finally:
        await pool.close()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--dry-run", action="store_true", help="Report what would be retired without writing anything."
    )
    args = parser.parse_args()

    configure_logging("WARNING")
    return asyncio.run(_run(args.dry_run))


if __name__ == "__main__":
    raise SystemExit(main())
