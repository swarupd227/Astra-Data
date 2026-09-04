"""The conformance fault hook, injected at the socket — S2.1.2, implemented for real.

S2.1.2 defined `FaultInjector` and said an adapter that does not implement it fails the error
taxonomy and throttling checks. This is the first *real* adapter to implement it, and it is
worth being precise about where the fault goes.

**The fault is on the transport, not on the adapter.** `FaultingTransport` wraps the HTTP
client and makes it answer as a misbehaving Tableau would: a 429 with a `Retry-After`, a
connection reset, a 401. Everything above it — the backoff schedule, the concurrency
reduction, the re-sign-in, the classification of a rejected credential as not-retryable — is
the adapter's own code, running unchanged.

That is what makes the conformance result mean something. A hook that made the *adapter*
raise the expected error would be testing the hook, and would pass with the adapter's own
error handling deleted.
"""

from __future__ import annotations

import logging
from typing import Any

import httpx
from astra_adapter.faults import Fault

from .config import TableauConfig

logger = logging.getLogger(__name__)

#: What each fault makes the transport do. Modelled on what Tableau actually returns, because
#: an adapter certified against a fiction is certified against a fiction.
_RESPONSES: dict[Fault, tuple[int, dict[str, str], bytes]] = {
    Fault.THROTTLE: (
        429,
        {"Retry-After": "1", "Content-Type": "application/json"},
        b'{"error":{"summary":"Rate limit exceeded","detail":"Too many requests to this '
        b'site","code":"429000"}}',
    ),
    Fault.UNAUTHORISED: (
        401,
        {"Content-Type": "application/json"},
        b'{"error":{"summary":"Signin Error","detail":"Error signing in to Tableau Server",'
        b'"code":"401001"}}',
    ),
    Fault.PERMANENT: (
        404,
        {"Content-Type": "application/json"},
        b'{"error":{"summary":"Resource Not Found","detail":"Workbook not found","code":"404006"}}',
    ),
}


class FaultingTransport:
    """An `httpx` transport that can be told to misbehave.

    Wraps whatever transport the client would otherwise use, so a deployment with no faults
    set pays one `if` per request and behaves exactly as it would without this class.
    """

    def __init__(self) -> None:
        self._fault = Fault.NONE
        self._remaining = 0
        self.injected = 0

    async def set_fault(self, fault: Fault, *, count: int = 1) -> None:
        self._fault = fault
        if fault is Fault.NONE:
            self._remaining = 0
        elif fault is Fault.UNAUTHORISED:
            # A rejected credential does not un-reject itself after one call. Injecting a
            # single 401 models an *expired session*, which the adapter correctly recovers
            # from by signing in again — so a one-shot 401 tested the recovery path and
            # reported it as the adapter failing to classify a rejection. The two conditions
            # look identical on one call and differ on the second, which is exactly how the
            # adapter tells them apart.
            self._remaining = 1_000_000
        else:
            self._remaining = count
        if fault is not Fault.NONE:
            logger.info("conformance: source calls will see %s (%d)", fault.value, self._remaining)

    async def before_call(self) -> None:
        """A hook the adapter can await before work that does not go through HTTP.

        Nothing needs it today; it exists so a future code path that answers from a cache
        still encounters the fault the suite asked for, rather than passing a check by not
        making the call.
        """
        return None

    def client(self, config: TableauConfig) -> httpx.AsyncClient:
        return httpx.AsyncClient(
            base_url=config.base_url,
            timeout=config.request_timeout,
            verify=config.verify_tls,
            follow_redirects=True,
            transport=_Transport(self),
        )

    # -------------------------------------------------------------------- internal

    def _next(self) -> tuple[int, dict[str, str], bytes] | None:
        if self._fault is Fault.NONE or self._remaining <= 0:
            return None
        self._remaining -= 1
        self.injected += 1
        if self._fault is Fault.TRANSIENT:
            return None  # signalled by raising, not by a status; see _Transport
        return _RESPONSES.get(self._fault)

    @property
    def transient_pending(self) -> bool:
        return self._fault is Fault.TRANSIENT and self._remaining > 0


class _Transport(httpx.AsyncBaseTransport):
    def __init__(self, faults: FaultingTransport) -> None:
        self._faults = faults
        self._real = httpx.AsyncHTTPTransport()

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        if self._faults.transient_pending:
            self._faults._remaining -= 1
            self._faults.injected += 1
            # A reset connection, which is what a network blip is. `httpx` raises this from
            # the transport, so the adapter sees exactly what it would see in production.
            raise httpx.ConnectError("connection reset by peer", request=request)

        canned = self._faults._next()
        if canned is not None:
            status, headers, body = canned
            return httpx.Response(status, headers=headers, content=body, request=request)

        return await self._real.handle_async_request(request)

    async def aclose(self) -> None:
        await self._real.aclose()


def as_dict(faults: FaultingTransport) -> dict[str, Any]:
    return {"faults_injected": faults.injected}
