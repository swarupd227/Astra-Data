#!/usr/bin/env python
"""Apply pending migrations.

Run before the service starts. Safe to run concurrently from several replicas: the runner
takes a PostgreSQL advisory lock for the duration.

    python tools/migrate.py            apply pending migrations
    python tools/migrate.py --status   list applied and pending, change nothing
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

# Output carries the specification's own punctuation — em dashes, and arrows in edge
# endpoint pairs. A console on a legacy code page cannot encode those, and a guard that
# crashes while reporting a difference is worse than no guard.
for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        _stream.reconfigure(encoding="utf-8", errors="replace")


SERVICE_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SERVICE_ROOT / "src"))

import asyncpg  # noqa: E402

from astra_graph.config import settings  # noqa: E402
from astra_graph.logging_setup import configure_logging  # noqa: E402
from astra_graph.migrations import applied_versions, discover, run  # noqa: E402
from astra_graph.provisioning import ensure_graph  # noqa: E402


async def _status() -> int:
    config = settings()
    conn = await asyncpg.connect(dsn=config.dsn)
    try:
        applied = await applied_versions(conn)
    finally:
        await conn.close()
    for migration in discover():
        state = "applied" if migration.version in applied else "pending"
        print(f"{migration.version:>4}  {state:<8}  {migration.description}")
    return 0


async def _apply() -> int:
    config = settings()
    conn = await asyncpg.connect(dsn=config.dsn)
    try:
        applied = await run(conn)
        # Migration state is per database; the graph, its labels and its indexes are per
        # graph. Reconciled every time, so pointing a deployment at a graph name it has
        # not used before actually creates it (see provisioning.py).
        provisioned = await ensure_graph(conn, config.graph_name)
    finally:
        await conn.close()

    if not applied:
        print("no pending migrations")
    for migration in applied:
        print(f"applied {migration.version}: {migration.description}")

    if provisioned["graph_created"]:
        print(f"created graph {config.graph_name}")
    if provisioned["labels_created"]:
        print(f"created {provisioned['labels_created']} label(s) in {config.graph_name}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--status", action="store_true", help="list migrations, change nothing")
    args = parser.parse_args()
    configure_logging(settings().log_level)
    return asyncio.run(_status() if args.status else _apply())


if __name__ == "__main__":
    raise SystemExit(main())
