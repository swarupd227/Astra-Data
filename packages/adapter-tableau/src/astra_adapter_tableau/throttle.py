"""Rate limits, 429s and the site concurrency cap — S2.2.1's third criterion.

    "Rate limits and 429s are handled with backoff; a site-level concurrency cap is
    configurable (default 4)"

§6.2 puts it as "Adaptive concurrency per site; Metadata API paging; backoff on 429".

**Why this is a module and not three lines in the client.** The two mechanisms interact.
Concurrency decides how many requests are in flight; backoff decides what happens when the
source says there are too many. Handled separately, four workers each backing off
independently arrive back at the source together — the classic thundering herd — and a
Tableau site that was throttling one client is then throttling four in lockstep. Jitter is
what breaks that up, and jitter is only meaningful if the same component knows about both.

**What "adaptive" means here.** The cap comes down when the source throttles and climbs back
slowly when it stops. Not a control loop — a Tableau site's limits are not published and are
not stable, so a model of them would be a model of a guess. Halving on a 429 and adding one
back after a run of successes is crude, converges, and is explicable to whoever reads the
logs at three in the morning.
"""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import logging
import time
from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass, field

from astra_adapter import RateLimited

logger = logging.getLogger(__name__)

#: Backoff schedule, in seconds, when the source gives no ``Retry-After``. Capped rather than
#: doubling forever: a harvest that waits eight minutes between attempts has stopped being a
#: harvest, and the run should surface the throttling so a person can decide.
BACKOFF_SECONDS = (1.0, 2.0, 5.0, 15.0, 30.0)

#: Successful calls in a row before the cap climbs by one.
RECOVERY_RUN = 20


@dataclass(slots=True)
class ThrottleState:
    """What the adapter has learned about this site's tolerance. Reported, not hidden."""

    configured: int
    current: int
    throttled: int = 0
    waited_seconds: float = 0.0
    reductions: int = 0
    recoveries: int = 0
    successes_since_change: int = 0

    def as_dict(self) -> dict[str, float | int]:
        return {
            "configured_concurrency": self.configured,
            "current_concurrency": self.current,
            "throttle_responses": self.throttled,
            "waited_seconds": round(self.waited_seconds, 2),
            "reductions": self.reductions,
            "recoveries": self.recoveries,
        }


class SiteThrottle:
    """One site's concurrency cap and backoff schedule.

    Per site, not per adapter: §5.2 scales `adapter-tableau` "per site parallelism", and two
    sites on one Tableau Server have separate limits. An adapter serving several would
    otherwise let a busy site throttle a quiet one.
    """

    def __init__(
        self,
        *,
        concurrency: int,
        max_retries: int = 5,
        site: str = "",
        sleep: Callable[[float], Awaitable[None]] | None = None,
    ) -> None:
        if concurrency < 1:
            raise ValueError("concurrency must be at least 1")
        self._site = site or "(default)"
        self._max_retries = max_retries
        self._state = ThrottleState(configured=concurrency, current=concurrency)
        self._slots = asyncio.Semaphore(concurrency)
        self._held_back = 0
        self._lock = asyncio.Lock()
        #: Injectable so tests do not spend the backoff schedule in real time. The *schedule*
        #: is what is under test; sleeping through it would only test the clock.
        self._sleep = sleep or asyncio.sleep

    @property
    def state(self) -> ThrottleState:
        return self._state

    @contextlib.asynccontextmanager
    async def slot(self) -> AsyncIterator[None]:
        """Hold one of the site's concurrency slots for the duration of a call."""
        await self._slots.acquire()
        try:
            yield
        finally:
            self._slots.release()

    async def attempt(self, attempt: int, retry_after: float | None, reason: str) -> None:
        """Wait before retrying a throttled call, or refuse to retry any longer.

        ``retry_after`` is the source's own instruction and wins over the schedule when it is
        given: a server that says thirty seconds knows something the adapter does not, and
        ignoring it is how a client's rate limit becomes a client's incident.
        """
        if attempt >= self._max_retries:
            raise RateLimited(
                f"Tableau site {self._site!r} is still rate limiting after "
                f"{self._max_retries} attempts ({reason}). The harvest is being throttled, "
                f"not failing: retry when the site is quieter, or lower the concurrency cap.",
                retry_after=retry_after,
            )

        await self._reduce()
        delay = self._delay(attempt, retry_after)
        self._state.throttled += 1
        self._state.waited_seconds += delay
        logger.info(
            "throttled by %s (%s); waiting %.1fs before attempt %d, concurrency now %d",
            self._site,
            reason,
            delay,
            attempt + 2,
            self._state.current,
        )
        await self._sleep(delay)

    def _delay(self, attempt: int, retry_after: float | None) -> float:
        if retry_after is not None and retry_after >= 0:
            base = retry_after
        else:
            base = BACKOFF_SECONDS[min(attempt, len(BACKOFF_SECONDS) - 1)]
        return base + self._jitter(attempt, base)

    def _jitter(self, attempt: int, base: float) -> float:
        """Up to 25% extra, derived rather than random.

        Derived from the site name and the attempt so a run is reproducible — a harvest that
        cannot be replayed with the same timings is a harvest whose throttling behaviour
        cannot be investigated. Different sites get different jitter, which is what the
        jitter is for; the same site retrying does too, because the attempt is in the seed.
        """
        seed = f"{self._site}:{attempt}".encode()
        fraction = int(hashlib.blake2b(seed, digest_size=2).hexdigest(), 16) / 0xFFFF
        return base * 0.25 * fraction

    async def _reduce(self) -> None:
        """Halve the cap, to a floor of one. Crude on purpose — see the module docstring."""
        async with self._lock:
            if self._state.current <= 1:
                return
            target = max(1, self._state.current // 2)
            for _ in range(self._state.current - target):
                # Slots are withheld by acquiring them and not giving them back, so a call
                # already in flight is never interrupted. Reducing by cancelling work would
                # throw away a download that was nearly finished.
                await self._slots.acquire()
                self._held_back += 1
            self._state.current = target
            self._state.reductions += 1
            self._state.successes_since_change = 0
            logger.info("reduced %s concurrency to %d after a 429", self._site, target)

    async def succeeded(self) -> None:
        """Record a clean call, and give a slot back after a run of them."""
        async with self._lock:
            self._state.successes_since_change += 1
            if (
                self._held_back
                and self._state.successes_since_change >= RECOVERY_RUN
                and self._state.current < self._state.configured
            ):
                self._slots.release()
                self._held_back -= 1
                self._state.current += 1
                self._state.recoveries += 1
                self._state.successes_since_change = 0
                logger.info(
                    "raised %s concurrency to %d after %d clean calls",
                    self._site,
                    self._state.current,
                    RECOVERY_RUN,
                )


def retry_after_seconds(header: str | None) -> float | None:
    """Read a ``Retry-After`` header. Seconds, or an HTTP date, or nothing.

    RFC 9110 allows both forms and Tableau has been observed sending each. A parser that
    handled only the integer form would silently ignore the date form and back off on its own
    schedule against a server that had told it exactly when to return.
    """
    if not header:
        return None
    text = header.strip()
    try:
        return max(0.0, float(text))
    except ValueError:
        pass
    from email.utils import parsedate_to_datetime

    try:
        when = parsedate_to_datetime(text)
    except (TypeError, ValueError):
        return None
    delta = when.timestamp() - time.time()
    return max(0.0, delta)


@dataclass(slots=True)
class ThrottleRegistry:
    """One `SiteThrottle` per site, made on demand."""

    concurrency: int
    max_retries: int = 5
    _by_site: dict[str, SiteThrottle] = field(default_factory=dict)

    def for_site(self, site: str) -> SiteThrottle:
        if site not in self._by_site:
            self._by_site[site] = SiteThrottle(
                concurrency=self.concurrency, max_retries=self.max_retries, site=site
            )
        return self._by_site[site]

    def as_dict(self) -> dict[str, dict[str, float | int]]:
        return {site: throttle.state.as_dict() for site, throttle in sorted(self._by_site.items())}
