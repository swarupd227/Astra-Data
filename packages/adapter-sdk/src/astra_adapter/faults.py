"""The error taxonomy, and the hook that lets the suite drive an adapter into each state.

S2.1.2 asks the conformance suite to cover **error taxonomy** and **throttling behaviour**.
Neither can be checked by watching an adapter succeed, and neither can be checked at all
unless the suite can make the source misbehave on demand.

**Why a taxonomy at all.** The platform's behaviour on an adapter error is entirely decided
by which kind it is:

| Kind | What the platform does |
|---|---|
| `RateLimited` | Waits `retry_after` and retries; the run continues (§6.2: "backoff on 429") |
| `AdapterError(retryable=True)` | Retries the asset; on repeated failure records it and carries on |
| `AdapterError(retryable=False)` | Records the failure against the asset and moves on (S1.2.1) |
| `UnsupportedCapability` | Reports a fact about the deployment; not a failure at all |
| Anything else | A bug, surfaced with its traceback |

An adapter that raises a bare `Exception` for a 429 gets a workbook recorded as permanently
failed when the truthful answer was "ask again in thirty seconds". The taxonomy is the thing
that makes an adapter's failures actionable, so the suite checks it.

**Why a test hook is legitimate.** An adapter cannot be certified for backoff behaviour that
has never been observed, and a client's Tableau Server will not oblige by returning 429s to
order. So conformance defines a small, standard way to ask an adapter to behave as if the
source had misbehaved. It is not a mock of the adapter — the adapter's own error handling,
backoff and retry run for real; only the source's response is forced.

An adapter that does not implement `FaultInjector` is **failed**, not skipped, on these two
checks. §6.2 requires backoff on 429 of the Tableau adapter, and "we could not check"
recorded as a pass is the false assurance the whole suite exists to avoid.

**Exposure.** The RPC serves this hook only when the adapter implements it, and the platform
never calls it — nothing outside the conformance suite has a reason to. An adapter image that
must not be drivable in production should not implement the protocol, and will then fail
these two checks, which is the honest trade: an adapter is certified for the behaviour it can
be shown to have.
"""

from __future__ import annotations

from enum import Enum
from typing import Protocol, runtime_checkable

from .contract import AdapterError


class RateLimited(AdapterError):
    """The source is throttling. Not a failure — a request to wait.

    ``retry_after`` is what the source said, in seconds, where it said anything: Tableau's
    429 carries ``Retry-After``. ``None`` means throttled without a stated interval, and the
    caller backs off on its own schedule.

    Always retryable. A rate limit that was not retryable would be a quota, and a quota is a
    different conversation with the client.
    """

    def __init__(self, message: str, *, retry_after: float | None = None) -> None:
        self.retry_after = retry_after
        super().__init__(message, retryable=True)


class Fault(str, Enum):
    """A source misbehaviour the suite can ask an adapter to encounter."""

    NONE = "none"
    """Behave normally. Clearing a fault is part of the contract: a suite that could set one
    and not clear it could only ever run its checks in one order."""

    THROTTLE = "throttle"
    """The source returns 429 for a while, then recovers. The adapter must back off and
    complete, not fail (§6.2)."""

    TRANSIENT = "transient"
    """A network blip on one call. The adapter must raise a *retryable* error."""

    PERMANENT = "permanent"
    """The asset cannot be read at all — a corrupt file, a deleted workbook. Not retryable,
    and recorded against that asset (S1.2.1)."""

    UNAUTHORISED = "unauthorised"
    """The credential is rejected. Not retryable, and not per-asset: retrying the run with
    the same credential fails the same way, so this must stop the run rather than mark 1,067
    workbooks as individually failed."""


@runtime_checkable
class FaultInjector(Protocol):
    """A conformance-only hook: make the *source* misbehave, not the adapter.

    An adapter implements this by making its source client return the stated condition. The
    adapter's own handling — backoff, retry, error classification — runs unchanged, which is
    the whole point: what is under test is the adapter, and only the source is faked.
    """

    async def set_fault(self, fault: Fault, *, count: int = 1) -> None:
        """Make the next ``count`` source calls encounter ``fault``.

        ``Fault.NONE`` clears whatever is set. Setting a fault twice replaces it rather than
        queueing: a suite that had to remember what it had already asked for could not report
        clearly on what it observed.

        **Async, like everything else on this interface.** An adapter runs out of process
        (S2.1.1), so the suite reaches this hook over the RPC exactly as it reaches ``fetch``.
        A synchronous hook would work in process and be unreachable in ``--remote`` mode —
        which is the mode that certifies what is actually deployed, and therefore the mode
        where these two checks matter most.
        """
        ...


def classify(error: BaseException) -> str:
    """Name the taxonomy bucket an exception falls into.

    Used by the suite to report what an adapter actually raised, in the same words the table
    above uses — so a failure reads "raised AdapterError(retryable=False) where a rate limit
    was expected" rather than as a type name an engineer has to look up.
    """
    from .contract import UnsupportedCapability

    if isinstance(error, RateLimited):
        after = "" if error.retry_after is None else f", retry_after={error.retry_after}"
        return f"RateLimited{after}"
    if isinstance(error, UnsupportedCapability):
        return "UnsupportedCapability"
    if isinstance(error, AdapterError):
        return f"AdapterError(retryable={error.retryable})"
    return f"{type(error).__name__} (outside the taxonomy)"
