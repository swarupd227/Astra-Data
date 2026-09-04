"""S2.2.1's third criterion — rate limits, 429s and the site concurrency cap.

    "Rate limits and 429s are handled with backoff; a site-level concurrency cap is
    configurable (default 4)"

The property that matters is that **a throttled site slows a harvest down rather than failing
it**. At 1,067 workbooks against a rate-limited Tableau site, an adapter that turns a 429 into
a failure loses most of the estate to a condition that would have cleared in thirty seconds.
"""

from __future__ import annotations

import asyncio

import pytest
from astra_adapter import AdapterError, RateLimited, Scope

from astra_adapter_tableau import DEFAULT_CONCURRENCY, TableauConfig, retry_after_seconds
from astra_adapter_tableau.throttle import BACKOFF_SECONDS, RECOVERY_RUN, SiteThrottle

from .conftest import adapter_for
from .fake_tableau import FakeTableau

SCOPE = Scope(site="golden")


# --------------------------------------------------------------------- the cap


def test_the_default_cap_is_four() -> None:
    """The story fixes the default; §6.2 asks for it to be per site."""
    assert DEFAULT_CONCURRENCY == 4
    assert TableauConfig(base_url="https://x").concurrency == 4


def test_the_cap_is_configurable_from_the_environment() -> None:
    config = TableauConfig.from_environment(
        {"ASTRA_TABLEAU_URL": "https://x", "ASTRA_TABLEAU_CONCURRENCY": "9"}
    )
    assert config.concurrency == 9


def test_a_cap_below_one_is_refused() -> None:
    """Zero would mean the adapter never calls the source, which is not a configuration
    anybody wants and is a typo everybody makes."""
    with pytest.raises(AdapterError, match="at least 1"):
        TableauConfig.from_environment(
            {"ASTRA_TABLEAU_URL": "https://x", "ASTRA_TABLEAU_CONCURRENCY": "0"}
        )


async def test_the_cap_actually_bounds_concurrency() -> None:
    """Asserted by observation rather than by reading the semaphore: the number that matters
    is how many calls were in flight at once, and a cap that is held but never enforced looks
    identical from the outside."""
    throttle = SiteThrottle(concurrency=3, site="rqa", sleep=_no_wait)
    in_flight = 0
    peak = 0

    async def call() -> None:
        nonlocal in_flight, peak
        async with throttle.slot():
            in_flight += 1
            peak = max(peak, in_flight)
            await asyncio.sleep(0)
            in_flight -= 1

    await asyncio.gather(*(call() for _ in range(20)))

    assert peak <= 3
    assert peak > 1, "and it is genuinely concurrent below the cap"


# ---------------------------------------------------------------------- backoff


async def test_a_burst_of_429s_is_ridden_out(server: FakeTableau) -> None:
    """The core property. Three 429s and the call still completes."""
    server.throttle_next = 3
    adapter = adapter_for(server)
    try:
        refs = [ref async for ref in adapter.enumerate(SCOPE)]
    finally:
        await adapter.aclose()

    assert refs, "the estate was discovered despite the throttling"
    assert adapter.throttle.state.throttled >= 3


async def test_persistent_throttling_surfaces_as_rate_limited(server: FakeTableau) -> None:
    """Not as a generic failure. The platform must be able to tell "wait" from "this workbook
    is broken" — the first is a run to resume, the second a workbook to record (S2.1.2)."""
    server.throttle_next = 10_000
    adapter = adapter_for(server, max_retries=2)
    try:
        with pytest.raises(RateLimited) as caught:
            await adapter.sites(SCOPE)
    finally:
        await adapter.aclose()

    assert caught.value.retryable, "throttling is always retryable; it is a wait, not a fault"
    assert "still rate limiting" in str(caught.value)
    assert "lower the concurrency cap" in str(caught.value), "say what can be done about it"


async def test_the_sources_retry_after_wins_over_the_schedule() -> None:
    """A server that says thirty seconds knows something the adapter does not, and ignoring
    it is how a client's rate limit becomes a client's incident."""
    waited: list[float] = []

    async def record(seconds: float) -> None:
        waited.append(seconds)

    throttle = SiteThrottle(concurrency=4, site="rqa", sleep=record)
    await throttle.attempt(0, 30.0, "GET /workbooks")

    assert waited[0] >= 30.0
    assert waited[0] < 30.0 * 1.3, "with jitter, but not a different number"


async def test_the_schedule_is_used_when_the_source_says_nothing() -> None:
    waited: list[float] = []

    async def record(seconds: float) -> None:
        waited.append(seconds)

    throttle = SiteThrottle(concurrency=4, site="rqa", sleep=record)
    for attempt in range(3):
        await throttle.attempt(attempt, None, "GET /workbooks")

    assert waited[0] >= BACKOFF_SECONDS[0]
    assert waited[1] > waited[0], "and it backs further off each time"
    assert waited[2] > waited[1]


async def test_jitter_differs_between_sites() -> None:
    """Four workers backing off identically arrive back at the source together — the classic
    thundering herd, and the reason jitter exists at all."""
    waits: dict[str, float] = {}

    for site in ("rqa", "gtaa", "emea"):

        async def record(seconds: float, *, site: str = site) -> None:
            waits[site] = seconds

        await SiteThrottle(concurrency=4, site=site, sleep=record).attempt(0, 10.0, "x")

    assert len(set(waits.values())) == 3, "three sites, three different waits"


async def test_the_backoff_is_reproducible() -> None:
    """Derived rather than random: a harvest whose timings cannot be replayed is a harvest
    whose throttling behaviour cannot be investigated."""
    runs = []
    for _ in range(2):
        waits: list[float] = []

        async def record(seconds: float, *, sink: list[float] = waits) -> None:
            sink.append(seconds)

        throttle = SiteThrottle(concurrency=4, site="rqa", sleep=record)
        for attempt in range(3):
            await throttle.attempt(attempt, None, "x")
        runs.append(waits)

    assert runs[0] == runs[1]


def test_a_retry_after_date_is_understood() -> None:
    """RFC 9110 allows seconds or an HTTP date, and Tableau has been seen sending each. A
    parser that read only the integer form would silently back off on its own schedule
    against a server that had said exactly when to return."""
    assert retry_after_seconds("30") == 30.0
    assert retry_after_seconds(None) is None
    assert retry_after_seconds("nonsense") is None

    from datetime import UTC, datetime, timedelta
    from email.utils import format_datetime

    soon = datetime.now(UTC) + timedelta(seconds=45)
    parsed = retry_after_seconds(format_datetime(soon))
    assert parsed is not None and 30 < parsed <= 46


# ------------------------------------------------------------------- adaptation


async def test_the_cap_comes_down_when_the_source_throttles(server: FakeTableau) -> None:
    """§6.2 asks for "adaptive concurrency per site". Halving is crude, converges, and is
    explicable to whoever reads the logs at three in the morning."""
    server.throttle_next = 2
    adapter = adapter_for(server, concurrency=8)
    try:
        [ref async for ref in adapter.enumerate(SCOPE)]
    finally:
        await adapter.aclose()

    state = adapter.throttle.state
    assert state.configured == 8
    assert state.current < 8, "the cap came down"
    assert state.reductions >= 1


async def test_the_cap_climbs_back_after_a_run_of_clean_calls() -> None:
    """Otherwise one 429 in an eight-hour harvest halves the throughput for the rest of it."""
    throttle = SiteThrottle(concurrency=4, site="rqa", sleep=_no_wait)
    await throttle.attempt(0, 0.0, "x")
    assert throttle.state.current == 2

    for _ in range(RECOVERY_RUN):
        await throttle.succeeded()

    assert throttle.state.current == 3
    assert throttle.state.recoveries == 1


async def test_the_cap_never_falls_below_one() -> None:
    """A cap of zero is an adapter that has stopped, and "stopped" should be a raised
    RateLimited that a person can see, not a silent halt."""
    throttle = SiteThrottle(concurrency=2, site="rqa", sleep=_no_wait)

    for attempt in range(4):
        await throttle.attempt(attempt, 0.0, "x")

    assert throttle.state.current == 1


async def test_reducing_never_interrupts_a_call_in_flight() -> None:
    """Slots are withheld rather than reclaimed. Reducing by cancelling would throw away a
    download that was nearly finished — the most expensive thing to throw away."""
    throttle = SiteThrottle(concurrency=4, site="rqa", sleep=_no_wait)
    finished = False

    async def slow_call() -> None:
        nonlocal finished
        async with throttle.slot():
            await asyncio.sleep(0.05)
            finished = True

    task = asyncio.create_task(slow_call())
    await asyncio.sleep(0)
    await throttle.attempt(0, 0.0, "x")
    await task

    assert finished, "the call in flight completed"
    assert throttle.state.current == 2


async def test_the_throttle_state_is_reported_not_hidden(server: FakeTableau) -> None:
    """An operator asking why a harvest is slow should find the answer on Platform Health
    rather than in a log grep."""
    server.throttle_next = 2
    adapter = adapter_for(server, concurrency=4)
    try:
        [ref async for ref in adapter.enumerate(SCOPE)]
        sites = await adapter.sites(SCOPE)
    finally:
        await adapter.aclose()

    reported = sites[0].detail["concurrency"]
    assert reported["configured_concurrency"] == 4
    assert reported["throttle_responses"] >= 2
    assert reported["waited_seconds"] >= 0


async def test_each_site_throttles_independently() -> None:
    """Two sites on one Tableau Server have separate limits (§5.2 scales per site). One
    shared throttle would let a busy site slow a quiet one."""
    from astra_adapter_tableau.throttle import ThrottleRegistry

    registry = ThrottleRegistry(concurrency=4)
    busy = registry.for_site("rqa")
    quiet = registry.for_site("gtaa")

    busy._sleep = _no_wait
    await busy.attempt(0, 0.0, "x")

    assert busy.state.current == 2
    assert quiet.state.current == 4
    assert registry.for_site("rqa") is busy, "one throttle per site, not one per call"


async def _no_wait(seconds: float) -> None:
    return None
