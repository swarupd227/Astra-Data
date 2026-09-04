"""Wiring the adapter to a fake Tableau over an in-process transport.

Everything below the socket is the adapter's real code: the sign-in flow, the paging, the
backoff, the zip handling. Only the network is skipped, and `httpx.ASGITransport` skips it in
the same way for the platform's own tests.
"""

from __future__ import annotations

from collections.abc import AsyncIterator

import httpx
import pytest

from astra_adapter_tableau import TableauAdapter, TableauConfig
from astra_adapter_tableau.config import Credential
from astra_adapter_tableau.rest import TableauRestClient
from astra_adapter_tableau.throttle import SiteThrottle

from .fake_tableau import FakeTableau, credential_json, estate


def config_for(
    server: FakeTableau, *, kind: str = "personal_access_token", **overrides: object
) -> TableauConfig:
    settings: dict[str, object] = {
        "base_url": "https://tableau.client.example",
        "site": server.site,
        "credential": Credential.from_json(credential_json(kind)),
        "page_size": 2,
        "max_retries": 4,
    }
    settings.update(overrides)
    return TableauConfig(**settings)  # type: ignore[arg-type]


def client_for(server: FakeTableau, config: TableauConfig) -> httpx.AsyncClient:
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=server.app()),
        base_url=config.base_url,
        timeout=config.request_timeout,
    )


def adapter_for(
    server: FakeTableau, *, kind: str = "personal_access_token", **overrides: object
) -> TableauAdapter:
    """A `TableauAdapter` whose REST client talks to the fake.

    The adapter builds its own faulting client by default; a test that wants the fake has to
    hand one in. That asymmetry is deliberate — the fault-injection path and the fake-server
    path are both real and a test should say which it is exercising.
    """
    config = config_for(server, kind=kind, **overrides)
    throttle = SiteThrottle(
        concurrency=config.concurrency,
        max_retries=config.max_retries,
        site=config.site_label,
        sleep=_no_wait,
    )
    rest = TableauRestClient(config, throttle=throttle, client=client_for(server, config))
    adapter = TableauAdapter(config, rest=rest)
    adapter._throttle = throttle
    return adapter


async def _no_wait(seconds: float) -> None:
    """Skip the backoff wall-clock. The *schedule* is under test; sleeping through it would
    only test the clock, and would make the throttling tests take half a minute."""
    return None


@pytest.fixture
def server() -> FakeTableau:
    return estate(5)


@pytest.fixture
async def adapter(server: FakeTableau) -> AsyncIterator[TableauAdapter]:
    built = adapter_for(server)
    try:
        yield built
    finally:
        await built.aclose()
