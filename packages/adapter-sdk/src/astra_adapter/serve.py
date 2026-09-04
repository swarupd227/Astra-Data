"""``astra-adapter-serve`` — run an adapter as a worker process.

The entry point of an adapter image (§6.1: "packaged as a versioned worker image with a
manifest"), and what `AdapterSupervisor` launches locally.

**Why its own module.** It is deliberately one that nothing else imports. `rpc/__init__`
imports `rpc.server` for `create_app`, so ``python -m astra_adapter.rpc.server`` re-executes
a module already in ``sys.modules`` — which Python warns about, and warns about for good
reason: the module ends up with two identities and its globals are initialised twice. An
entry point that is only ever an entry point has neither problem.
"""

from __future__ import annotations

import argparse

from .registry import UnknownAdapter, load_adapter
from .rpc.server import serve


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="astra-adapter-serve",
        description="Run a source adapter as a worker over the adapter RPC (§6.1, §5.4)",
    )
    parser.add_argument("--adapter", required=True, help="registered adapter name")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8090)
    args = parser.parse_args(argv)

    try:
        adapter = load_adapter(args.adapter)
    except UnknownAdapter as exc:
        parser.exit(2, f"{exc}\n")

    serve(adapter, host=args.host, port=args.port)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
