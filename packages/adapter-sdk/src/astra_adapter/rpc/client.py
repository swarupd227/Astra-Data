"""The platform side of the RPC: a `SourceAdapter` that lives in another process.

`RemoteAdapter` satisfies §6.1 exactly, so the Harvester cannot tell it from an in-process
adapter and does not need to. That is the point of S2.1.1's second criterion: the isolation
is a deployment fact, not a change to how the platform is written.

**What a crash looks like from here.** A dead adapter, a refused connection, a truncated
response and a timeout all arrive as ``AdapterError(retryable=True)`` against the asset being
worked on. The Harvester already records a per-asset ``AdapterError`` and carries on
(S1.2.1), so an adapter that dies mid-harvest costs the workbooks in flight and nothing else
— the worker keeps running and the run completes with those workbooks listed as failures.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Sequence
from typing import Any

import httpx

from ..calc import CalcAST
from ..contract import (
    INTERFACE_VERSION,
    AdapterError,
    AdapterManifest,
    AssetRef,
    OwnershipRecord,
    ParseResult,
    RawAsset,
    Scope,
    SiteRecord,
    UnsupportedCapability,
    UsageRecord,
    ViewerRecord,
)
from ..faults import Fault, RateLimited
from ..proof import ParityCase, ResultSet, VisualCapture, VisualCase
from . import wire

#: Generous, and deliberately so. Fetching a large .twbx over a client's network is slow,
#: and a timeout tuned for a fast link turns a slow estate into a run full of failures.
DEFAULT_TIMEOUT = 300.0


class RemoteAdapter:
    """A source adapter running in another process, spoken to over REST (§5.4).

    Construct with a base URL. ``manifest()`` is synchronous on the §6.1 interface but a
    remote call is not, so the manifest is fetched once at ``connect()`` and cached — an
    adapter build's identity does not change while it is running, and re-reading it per
    harvest would be a network round trip to learn a constant.
    """

    def __init__(
        self,
        base_url: str,
        *,
        timeout: float = DEFAULT_TIMEOUT,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self._base = base_url.rstrip("/")
        self._client = client or httpx.AsyncClient(base_url=self._base, timeout=timeout)
        self._manifest: AdapterManifest | None = None

    # ------------------------------------------------------------------ lifecycle

    async def connect(self) -> AdapterManifest:
        """Read the manifest and refuse an interface version this platform cannot speak."""
        raw = await self._get("/v1/manifest")
        manifest = wire.decode_manifest(raw)
        wire.check_interface(manifest.interface_version)
        self._manifest = manifest
        return manifest

    async def aclose(self) -> None:
        await self._client.aclose()

    async def __aenter__(self) -> RemoteAdapter:
        await self.connect()
        return self

    async def __aexit__(self, *_: object) -> None:
        await self.aclose()

    # ------------------------------------------------------------------ transport

    async def _get(self, path: str) -> dict[str, Any]:
        try:
            response = await self._client.get(path)
        except httpx.HTTPError as exc:
            raise self._unreachable(exc) from exc
        return self._read(response)

    async def _post(self, path: str, body: dict[str, Any]) -> dict[str, Any]:
        payload = {**body, "interface_version": INTERFACE_VERSION}
        try:
            response = await self._client.post(path, json=payload)
        except httpx.HTTPError as exc:
            raise self._unreachable(exc) from exc
        return self._read(response)

    def _unreachable(self, exc: httpx.HTTPError) -> AdapterError:
        """Transport failure — retryable, and named as an adapter problem.

        Retryable because the honest reading of "the connection was refused" is "not now",
        not "not ever": a supervisor restarting a crashed adapter makes the next attempt
        succeed. The Harvester decides whether to retry; this only says that retrying is
        not obviously pointless.
        """
        return AdapterError(
            f"adapter at {self._base} is unreachable: {type(exc).__name__}: {exc}",
            retryable=True,
        )

    def _read(self, response: httpx.Response) -> dict[str, Any]:
        if response.status_code >= 400:
            try:
                body = response.json()
            except ValueError:
                raise AdapterError(
                    f"adapter returned {response.status_code} with an unreadable body",
                    retryable=response.status_code >= 500,
                ) from None
            message = str(body.get("message") or body.get("error") or response.status_code)
            if body.get("error") == "UnsupportedCapability" or response.status_code == 409:
                raise UnsupportedCapability(
                    str(body.get("capability") or message),
                    adapter=self._manifest.name if self._manifest else "",
                )
            if body.get("error") == "RateLimited" or response.status_code == 429:
                # Rebuilt as itself, with its interval. A rate limit that arrived as a plain
                # retryable error would be indistinguishable from a network blip, and the
                # platform would retry on its own schedule instead of the source's.
                raise RateLimited(message, retry_after=body.get("retry_after"))
            raise AdapterError(message, retryable=bool(body.get("retryable", False)))
        try:
            decoded = response.json()
        except ValueError as exc:
            raise AdapterError(
                f"adapter returned a body that is not JSON: {exc}", retryable=True
            ) from exc
        if not isinstance(decoded, dict):
            raise wire.WireError(f"expected a JSON object, got {type(decoded).__name__}")
        return decoded

    # ------------------------------------------------------------------- contract

    def manifest(self) -> AdapterManifest:
        if self._manifest is None:
            raise AdapterError(
                "manifest is not known yet; call connect() before using a remote adapter",
                retryable=False,
            )
        return self._manifest

    async def enumerate(self, scope: Scope) -> AsyncIterator[AssetRef]:
        body = await self._post("/v1/enumerate", {"scope": wire.encode_scope(scope)})
        for raw in body["assets"]:
            yield wire.decode_asset(raw)

    async def fetch(self, asset: AssetRef) -> RawAsset:
        return wire.decode_raw_asset(
            await self._post("/v1/fetch", {"asset": wire.encode_asset(asset)})
        )

    async def parse(self, raw: RawAsset) -> ParseResult:
        return wire.decode_parse_result(
            await self._post("/v1/parse", {"raw": wire.encode_raw_asset(raw)})
        )

    async def parse_calc(self, expression: str) -> CalcAST:
        return wire.decode_calc(await self._post("/v1/parse-calc", {"expression": expression}))

    async def usage(self, scope: Scope, window_days: int) -> Sequence[UsageRecord]:
        body = await self._post(
            "/v1/usage", {"scope": wire.encode_scope(scope), "window_days": window_days}
        )
        return [wire.decode_usage(raw) for raw in body["records"]]

    async def viewers(self, scope: Scope, window_days: int) -> Sequence[ViewerRecord]:
        body = await self._post(
            "/v1/viewers", {"scope": wire.encode_scope(scope), "window_days": window_days}
        )
        return [wire.decode_viewer(raw) for raw in body["records"]]

    async def owners(self, scope: Scope) -> Sequence[OwnershipRecord]:
        body = await self._post("/v1/owners", {"scope": wire.encode_scope(scope)})
        return [wire.decode_owner(raw) for raw in body["records"]]

    async def sites(self, scope: Scope) -> Sequence[SiteRecord]:
        body = await self._post("/v1/sites", {"scope": wire.encode_scope(scope)})
        return [wire.decode_site(raw) for raw in body["records"]]

    async def execute_case(self, case: ParityCase) -> ResultSet:
        return wire.decode_result_set(
            await self._post("/v1/execute-case", {"case": wire.encode_parity_case(case)})
        )

    async def capture_visual(self, case: VisualCase) -> VisualCapture:
        return wire.decode_visual_capture(
            await self._post("/v1/capture-visual", {"case": wire.encode_visual_case(case)})
        )

    # ------------------------------------------------------------- conformance only

    async def set_fault(self, fault: Fault, *, count: int = 1) -> None:
        """Drive the remote adapter's source into a fault state (S2.1.2).

        Not part of §6.1. Present on the client so `astra-adapter conformance --remote` can
        check error taxonomy and throttling against a deployed worker; the platform never
        calls it. An adapter without the hook answers with a message saying so, which the
        suite reports rather than guessing at.
        """
        await self._post("/v1/conformance/fault", {"fault": fault.value, "count": count})
