"""The adapter side of the RPC: an ASGI app that exposes one adapter over REST.

An adapter image runs this. §5.2 makes `adapter-tableau` a *worker*; §5.4 makes the adapter
interface REST; §6.1 packages an adapter as "a versioned worker image with a manifest". This
module is what turns a class implementing `SourceAdapter` into that image's entry point.

**Errors are answers, not crashes.** Every route turns an ``AdapterError`` into a response
the client can read — the message, whether it is retryable, and whether it was an unsupported
capability. That is what makes per-asset failure isolation work across a process boundary: a
workbook that cannot be fetched produces a 502 with a reason, the client raises
``AdapterError`` in the caller's process, and the Harvester records it against that workbook
and carries on, exactly as it does in-process.

An error the adapter does *not* raise deliberately — a bug, a segfault in a parser's C
extension — is not handled here, because it cannot be. That is the supervisor's problem, and
the reason S2.1.1 asks for out-of-process adapters at all.
"""

from __future__ import annotations

import traceback
from collections.abc import Awaitable, Callable

from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route

from ..contract import INTERFACE_VERSION, AdapterError, SourceAdapter, UnsupportedCapability
from ..faults import Fault, FaultInjector, RateLimited
from . import wire

#: Said by the adapter, so the suite reports the adapter's own answer rather than an
#: inference drawn from a missing route.
_NO_FAULT_HOOK = (
    "this adapter does not implement the conformance fault hook, so its behaviour under "
    "source failure and throttling cannot be observed"
)


def _error_response(exc: AdapterError, status: int = 502) -> JSONResponse:
    # ``error`` is the class name and the client rebuilds from it. The taxonomy is the whole
    # of the platform's response to a failure (see ``faults``), so flattening every
    # AdapterError into one shape here would mean the platform could not tell "wait thirty
    # seconds" from "this workbook is broken" across a process boundary — which is precisely
    # the distinction that decides whether an estate is harvested or written off.
    return JSONResponse(
        {
            "error": type(exc).__name__,
            "message": str(exc),
            "retryable": exc.retryable,
            "capability": getattr(exc, "capability", None),
            "retry_after": getattr(exc, "retry_after", None),
        },
        status_code=(
            409
            if isinstance(exc, UnsupportedCapability)
            else 429
            if isinstance(exc, RateLimited)
            else status
        ),
    )


def _guard(
    handler: Callable[[Request], Awaitable[JSONResponse]],
) -> Callable[[Request], Awaitable[JSONResponse]]:
    """Turn any exception into a readable response.

    A bare ``Exception`` catch here is deliberate and is the only one in the SDK. The
    alternative is a 500 with an ASGI traceback in the adapter's log and nothing in the
    platform's — and "the harvest failed" with no reason attached is the failure mode this
    story exists to remove. The traceback is returned in ``detail`` because both processes
    are inside the tenant (§5.3) and an adapter's stack is not a secret from the platform
    that launched it.
    """

    async def wrapped(request: Request) -> JSONResponse:
        try:
            return await handler(request)
        except AdapterError as exc:
            return _error_response(exc)
        except Exception as exc:
            return JSONResponse(
                {
                    "error": type(exc).__name__,
                    "message": str(exc) or type(exc).__name__,
                    "retryable": False,
                    "detail": traceback.format_exc(limit=12),
                },
                status_code=500,
            )

    wrapped.__name__ = handler.__name__
    return wrapped


def create_app(adapter: SourceAdapter) -> Starlette:
    """An ASGI app exposing one adapter over the §6.1 routes."""

    async def manifest(_: Request) -> JSONResponse:
        return JSONResponse(wire.encode_manifest(adapter.manifest()))

    async def health(_: Request) -> JSONResponse:
        # Deliberately does not call the adapter: this answers "is the process alive and
        # serving", which is what a supervisor and a readiness probe ask. Whether the
        # *source* is reachable is a different question with a different answer, and
        # conflating them makes a Tableau outage look like a crashed adapter.
        return JSONResponse(
            {
                "status": "ok",
                "interface_version": INTERFACE_VERSION,
                "adapter": adapter.manifest().name,
            }
        )

    @_guard
    async def enumerate_assets(request: Request) -> JSONResponse:
        body = await request.json()
        wire.check_interface(str(body.get("interface_version", INTERFACE_VERSION)))
        scope = wire.decode_scope(body.get("scope") or {})
        assets = [wire.encode_asset(ref) async for ref in adapter.enumerate(scope)]
        # Collected rather than streamed. §6.1 types enumeration as an AsyncIterable and the
        # platform consumes it lazily in-process; over HTTP, a streamed body that fails
        # halfway leaves the caller unable to tell a short estate from a broken connection.
        # A site's asset refs are small — 1,067 workbooks is well under a megabyte — and the
        # unit of work is a site (§5.2, "per site parallelism"), so the whole list is one
        # answer. If an estate ever makes that false, paging is a contract change with a
        # version bump, not a silent truncation.
        return JSONResponse({"assets": assets, "count": len(assets)})

    @_guard
    async def fetch(request: Request) -> JSONResponse:
        body = await request.json()
        wire.check_interface(str(body.get("interface_version", INTERFACE_VERSION)))
        raw = await adapter.fetch(wire.decode_asset(body["asset"]))
        return JSONResponse(wire.encode_raw_asset(raw))

    @_guard
    async def parse(request: Request) -> JSONResponse:
        body = await request.json()
        wire.check_interface(str(body.get("interface_version", INTERFACE_VERSION)))
        result = await adapter.parse(wire.decode_raw_asset(body["raw"]))
        return JSONResponse(wire.encode_parse_result(result))

    @_guard
    async def parse_calc(request: Request) -> JSONResponse:
        body = await request.json()
        wire.check_interface(str(body.get("interface_version", INTERFACE_VERSION)))
        return JSONResponse(wire.encode_calc(await adapter.parse_calc(str(body["expression"]))))

    @_guard
    async def usage(request: Request) -> JSONResponse:
        body = await request.json()
        wire.check_interface(str(body.get("interface_version", INTERFACE_VERSION)))
        records = await adapter.usage(
            wire.decode_scope(body.get("scope") or {}), int(body.get("window_days", 90))
        )
        return JSONResponse({"records": [wire.encode_usage(r) for r in records]})

    @_guard
    async def viewers(request: Request) -> JSONResponse:
        body = await request.json()
        wire.check_interface(str(body.get("interface_version", INTERFACE_VERSION)))
        records = await adapter.viewers(
            wire.decode_scope(body.get("scope") or {}), int(body.get("window_days", 90))
        )
        return JSONResponse({"records": [wire.encode_viewer(r) for r in records]})

    @_guard
    async def owners(request: Request) -> JSONResponse:
        body = await request.json()
        wire.check_interface(str(body.get("interface_version", INTERFACE_VERSION)))
        records = await adapter.owners(wire.decode_scope(body.get("scope") or {}))
        return JSONResponse({"records": [wire.encode_owner(r) for r in records]})

    @_guard
    async def sites(request: Request) -> JSONResponse:
        body = await request.json()
        wire.check_interface(str(body.get("interface_version", INTERFACE_VERSION)))
        records = await adapter.sites(wire.decode_scope(body.get("scope") or {}))
        return JSONResponse({"records": [wire.encode_site(r) for r in records]})

    @_guard
    async def execute_case(request: Request) -> JSONResponse:
        body = await request.json()
        wire.check_interface(str(body.get("interface_version", INTERFACE_VERSION)))
        result = await adapter.execute_case(wire.decode_parity_case(body["case"]))
        return JSONResponse(wire.encode_result_set(result))

    @_guard
    async def capture_visual(request: Request) -> JSONResponse:
        body = await request.json()
        wire.check_interface(str(body.get("interface_version", INTERFACE_VERSION)))
        capture = await adapter.capture_visual(wire.decode_visual_case(body["case"]))
        return JSONResponse(wire.encode_visual_capture(capture))

    @_guard
    async def set_fault(request: Request) -> JSONResponse:
        """The conformance fault hook (S2.1.2), served only where the adapter has one.

        Not part of §6.1: it exists so `--remote` can check error taxonomy and throttling
        against the image that will actually be deployed, rather than certifying those two
        behaviours in process and shipping something else.
        """
        body = await request.json()
        wire.check_interface(str(body.get("interface_version", INTERFACE_VERSION)))
        if not isinstance(adapter, FaultInjector):
            raise AdapterError(_NO_FAULT_HOOK, retryable=False)
        await adapter.set_fault(Fault(str(body["fault"])), count=int(body.get("count", 1)))
        return JSONResponse({"fault": str(body["fault"]), "count": int(body.get("count", 1))})

    return Starlette(
        routes=[
            Route("/v1/conformance/fault", set_fault, methods=["POST"]),
            Route("/healthz", health, methods=["GET"]),
            Route("/v1/manifest", manifest, methods=["GET"]),
            Route("/v1/enumerate", enumerate_assets, methods=["POST"]),
            Route("/v1/fetch", fetch, methods=["POST"]),
            Route("/v1/parse", parse, methods=["POST"]),
            Route("/v1/parse-calc", parse_calc, methods=["POST"]),
            Route("/v1/usage", usage, methods=["POST"]),
            Route("/v1/viewers", viewers, methods=["POST"]),
            Route("/v1/owners", owners, methods=["POST"]),
            Route("/v1/sites", sites, methods=["POST"]),
            Route("/v1/execute-case", execute_case, methods=["POST"]),
            Route("/v1/capture-visual", capture_visual, methods=["POST"]),
        ]
    )


def serve(adapter: SourceAdapter, *, host: str = "127.0.0.1", port: int = 8090) -> None:
    """Run an adapter as a worker. The entry point of an adapter image."""
    import uvicorn

    uvicorn.run(create_app(adapter), host=host, port=port, log_level="warning")
