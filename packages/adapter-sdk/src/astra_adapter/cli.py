"""``astra-adapter`` — the SDK's command line.

S2.1.1: *"astra-adapter conformance --adapter tableau runs the suite end to end"*.

Four subcommands, and the first is the one the story names:

    astra-adapter conformance --adapter fake        run the suite against an adapter
    astra-adapter verify report.json                check a report's hash and signature
    astra-adapter list                              what is registered here
    astra-adapter manifest --adapter fake           an adapter's identity

``conformance --out report.json`` writes the **signed** report S2.1.2 requires — the same
document the platform stores and links from Platform Health, and the one a failing run
prevents an adapter from being promoted on.

``--remote`` runs the suite through the adapter RPC against a *supervised child process*
rather than in-process. That is the mode that matters for tenant enablement: §6.1 enables an
adapter as a versioned worker image, and a suite that only ever ran in-process would have
certified something other than what is deployed.
"""

from __future__ import annotations

import argparse
import asyncio
import importlib
import json
import sys
from typing import Any

from .conformance.report import SignedReport, sign, verify
from .conformance.suite import ConformanceSuite, Corpus, render
from .registry import UnknownAdapter, load_adapter, registered_names
from .rpc.supervisor import AdapterSupervisor


def _readable_output() -> None:
    """Let the report print its own punctuation.

    It carries the specification's — section signs, em dashes, arrows in edge endpoints — and
    a console on a legacy code page cannot encode those. A suite that crashes while reporting
    a failure is worse than no suite; the platform's drift guards do the same thing for the
    same reason.
    """
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")


def _corpus_for(name: str, adapter: Any, path: str | None) -> Corpus:
    """The corpus to check against: a file if one was given, otherwise the adapter's own.

    §6.3 has an adapter ship its corpus and be re-checked "on tenant enablement against a
    client-provided sample", so both have to be possible. An adapter with neither is refused
    rather than passed on a corpus of nothing.
    """
    if path:
        return _load_corpus_file(path)

    # The adapter itself, then the package that defines it. The second is how a third-party
    # adapter ships a corpus: §6.3 makes the corpus part of what an adapter *is*, and an
    # adapter author should not have to register it separately from the adapter.
    #
    # An earlier version looked under ``astra_adapter.<name>``, which could only ever find
    # the SDK's own — so the first real adapter to ship a corpus could not be checked against
    # it. Found the moment `tableau` arrived.
    supplier = getattr(adapter, "corpus", None) or _corpus_supplier(adapter)
    if supplier is None:
        raise SystemExit(
            f"adapter {name!r} ships no conformance corpus and none was given.\n"
            f"§6.3 requires an adapter to ship 'a corpus of source assets and expected "
            f"graph fragments'. Pass one with --corpus — on tenant enablement that is the "
            f"client-provided sample — or add a corpus() to the adapter or to the package "
            f"that defines it."
        )
    built = supplier()
    if not isinstance(built, Corpus):
        raise SystemExit(
            f"adapter {name!r} supplied a {type(built).__name__} where §6.3 expects a Corpus"
        )
    return built


def _corpus_supplier(adapter: Any) -> Any:
    """Find a ``corpus()`` on the packages that define this adapter, most specific first.

    ``astra_adapter_tableau.adapter`` → ``astra_adapter_tableau``; ``astra_adapter.fake.source``
    → ``astra_adapter.fake``. Walking outwards rather than jumping to the top-level package
    lets an adapter put its corpus wherever it naturally lives, which for the SDK's own is a
    subpackage and for a third party's is the package itself.
    """
    parts = type(adapter).__module__.split(".")
    for depth in range(len(parts), 0, -1):
        try:
            module = importlib.import_module(".".join(parts[:depth]))
        except ImportError:
            continue
        if supplier := getattr(module, "corpus", None):
            return supplier
    return None


def _load_corpus_file(path: str) -> Corpus:
    from .contract import Scope

    with open(path, encoding="utf-8") as handle:
        raw = json.load(handle)
    return Corpus(
        name=str(raw.get("name", path)),
        scope=Scope(site=raw.get("site"), project=raw.get("project")),
        expected_assets=frozenset(raw.get("expected_assets") or ()),
        expected_nodes={
            luid: frozenset(types) for luid, types in (raw.get("expected_nodes") or {}).items()
        },
        expressions=tuple(raw.get("expressions") or ()),
        expected_owners=frozenset(raw.get("expected_owners") or ()),
        usage_window_days=int(raw.get("usage_window_days", 90)),
        parse_quality_floor=float(raw.get("parse_quality_floor", 0.98)),
    )


async def _run_conformance(args: argparse.Namespace) -> int:
    try:
        local = load_adapter(args.adapter)
    except UnknownAdapter as exc:
        print(str(exc), file=sys.stderr)
        return 2

    corpus = _corpus_for(args.adapter, local, args.corpus)

    if args.remote:
        supervisor = AdapterSupervisor(args.adapter)
        try:
            async with supervisor.adapter() as remote:
                report = await ConformanceSuite(remote, corpus).run()
        finally:
            await supervisor.stop()
    else:
        report = await ConformanceSuite(local, corpus).run()

    signed = sign(report)

    if args.out:
        # Written whether or not the run passed. A failing report is the evidence that an
        # adapter must not be promoted (S2.1.2 criterion 3); discarding it would leave the
        # platform unable to say *why* it refused.
        with open(args.out, "w", encoding="utf-8") as handle:
            json.dump(signed.as_dict(), handle, indent=2)

    if args.json:
        print(json.dumps(signed.as_dict(), indent=2))
    else:
        print(render(report))
        if args.remote:
            print("  Run through the adapter RPC against a supervised worker process.")
        print(f"  {signed.content_hash}")
        if signed.signed:
            print(f"  signed with {signed.algorithm} (key {signed.key_id})")
        else:
            print(f"  UNSIGNED — {signed.key_id}")
        if args.out:
            print(f"  written to {args.out}")
    return 0 if report.passed else 1


def main(argv: list[str] | None = None) -> int:
    _readable_output()

    parser = argparse.ArgumentParser(
        prog="astra-adapter",
        description="Source Adapter SDK — specification §6",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    conformance = sub.add_parser(
        "conformance", help="run the §6.3 conformance suite against an adapter"
    )
    conformance.add_argument("--adapter", required=True, help="registered adapter name")
    conformance.add_argument(
        "--corpus",
        help="a corpus JSON file; defaults to the corpus the adapter ships (§6.3). "
        "On tenant enablement this is the client-provided sample.",
    )
    conformance.add_argument(
        "--remote",
        action="store_true",
        help="run through the adapter RPC against a supervised worker process, the way the "
        "adapter is actually deployed",
    )
    conformance.add_argument("--json", action="store_true", help="machine-readable report")
    conformance.add_argument(
        "--out",
        help="write the signed report here. Signed with ASTRA_CONFORMANCE_SIGNING_KEY where "
        "one is set; hashed and explicitly unsigned where none is.",
    )

    checking = sub.add_parser("verify", help="check a signed report's content hash and signature")
    checking.add_argument("report", help="path to a report written by --out")

    listing = sub.add_parser("list", help="adapters registered in this environment")
    listing.add_argument("--json", action="store_true")

    manifest = sub.add_parser("manifest", help="an adapter's identity and capabilities")
    manifest.add_argument("--adapter", required=True)

    args = parser.parse_args(argv)

    if args.command == "conformance":
        return asyncio.run(_run_conformance(args))

    if args.command == "verify":
        with open(args.report, encoding="utf-8") as handle:
            signed = SignedReport.from_dict(json.load(handle))
        ok, why = verify(signed)
        verdict = "VERIFIED" if ok else "NOT VERIFIED"
        passed = "passed" if signed.passed else "FAILED"
        print(f"{verdict} — {why}")
        print(f"  adapter    {signed.adapter} {signed.adapter_version}")
        print(f"  conformance {passed}")
        return 0 if ok else 1

    if args.command == "list":
        names = registered_names()
        if args.json:
            print(json.dumps({"adapters": names}))
        elif names:
            print("Registered source adapters:")
            for name in names:
                print(f"  {name}")
        else:
            print("No source adapters are registered in this environment.")
        return 0

    try:
        adapter = load_adapter(args.adapter)
    except UnknownAdapter as exc:
        print(str(exc), file=sys.stderr)
        return 2
    identity = adapter.manifest()
    print(
        json.dumps(
            {
                "name": identity.name,
                "version": identity.version,
                "grammar_version": identity.grammar_version,
                "interface_version": identity.interface_version,
                "capabilities": {
                    "live_query": identity.capabilities.live_query,
                    "extract_read": identity.capabilities.extract_read,
                    "usage": identity.capabilities.usage,
                    "ownership": identity.capabilities.ownership,
                    "screenshot": identity.capabilities.screenshot,
                },
            },
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
