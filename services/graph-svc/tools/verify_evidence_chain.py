#!/usr/bin/env python
"""Recompute this graph's own Evidence Chain and report the first break -- story
S11.3.1's own AC: "verification tool recomputes the chain and reports the first break;
runs nightly and on demand". This is the CLI shape `.github/workflows/nightly.yml`'s own
cron calls; the console's `POST /v1/evidence-chain:verify` route calls the identical
`verify_chain` function for the "on demand" half of that same AC bullet.

    python tools/verify_evidence_chain.py                 the deployment's own graph
    python tools/verify_evidence_chain.py --graph other    a specific graph
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
from astra_graph.evidence_chain import verify_chain  # noqa: E402
from astra_graph.graph import create_pool  # noqa: E402
from astra_graph.logging_setup import configure_logging  # noqa: E402


async def verify(graph_name: str) -> int:
    config = settings()
    pool = await create_pool(config)
    try:
        result = await verify_chain(pool, graph_name)
    finally:
        await pool.close()

    if result.intact:
        print(f"OK -- {result.entries_checked} entries verified, chain intact")
        return 0

    assert result.first_break is not None
    brk = result.first_break
    print(
        f"FAIL -- break at chain_seq {brk.chain_seq} "
        f"({brk.source_table}/{brk.source_id}) after {result.entries_checked} entries checked",
        file=sys.stderr,
    )
    print(f"  {brk.detail}", file=sys.stderr)
    print(f"  expected: {brk.expected_hash}", file=sys.stderr)
    print(f"  stored:   {brk.stored_hash}", file=sys.stderr)
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
