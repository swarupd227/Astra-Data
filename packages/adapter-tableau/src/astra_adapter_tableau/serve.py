"""``astra-tableau`` — run this adapter as a worker.

Thin on purpose: the SDK already knows how to serve a §6.1 adapter over the RPC
(``astra-adapter-serve --adapter tableau`` does the same thing). This exists so the worker
image has an entry point named after what it is, which is what an operator reads in a pod
spec and a crash log.
"""

from __future__ import annotations

import argparse

from astra_adapter.rpc.server import serve

from . import build


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="astra-tableau",
        description="Run the Tableau source adapter as a worker (spec §6.1, §6.2)",
    )
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=8090)
    args = parser.parse_args(argv)

    serve(build(), host=args.host, port=args.port)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
