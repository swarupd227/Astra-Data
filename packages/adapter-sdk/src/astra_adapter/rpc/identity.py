"""Which principal is making the current outbound adapter call — story S11.1.2.

    "Every adapter call ... carries the agent's identity" (spec §18.1, this story's own
    AC).

`RemoteAdapter` is typically constructed once per deployment and reused across many
harvest/parity runs (§5.2's own "an adapter is a worker", not a per-call object), so
identity cannot be a constructor argument; it has to be ambient, request-scoped state the
caller sets around one call (or one whole run) and the transport layer reads without
every one of the §6.1 Protocol's own methods needing a new parameter threaded through
every implementation, every conformance-suite fixture and every call site — the identical
reason a web framework's own request-scoped state (a logged-in user, a trace id) is
carried this way rather than passed as an extra argument everywhere it might be needed.

**Attribution only, not authorization.** `RemoteAdapter` attaches these as real request
headers so the adapter worker's own log can say who asked; nothing on this side of the
RPC boundary grants or refuses a call based on them. `agent_identity.py`'s own real
enforcement lives in graph-svc, at the graph API, artefact store and gateway — exactly
where this story's own AC names it, not here.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar

PRINCIPAL_HEADER = "X-Astra-Principal"
RUN_HEADER = "X-Astra-Run-Id"

_principal: ContextVar[str | None] = ContextVar("astra_adapter_principal", default=None)
_run_id: ContextVar[str | None] = ContextVar("astra_adapter_run_id", default=None)


@contextmanager
def identity(principal: str, run_id: str | None = None) -> Iterator[None]:
    """Every `RemoteAdapter` call made inside this block carries `principal`/`run_id` as
    real request headers. Nests correctly — a caller inside another's block sees its own,
    narrower identity for the duration, restored afterward."""
    principal_token = _principal.set(principal)
    run_token = _run_id.set(run_id)
    try:
        yield
    finally:
        _principal.reset(principal_token)
        _run_id.reset(run_token)


def current_principal() -> str | None:
    return _principal.get()


def current_run_id() -> str | None:
    return _run_id.get()


__all__ = ["PRINCIPAL_HEADER", "RUN_HEADER", "current_principal", "current_run_id", "identity"]
