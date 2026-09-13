"""Live updates over server-sent events — story S10.1.2's own "live updates over server-
sent events for queues and boards; p95 screen update within 2 seconds of the event."

**The same outbox, pushed instead of pulled.** `GET /v1/events` (`routes.py`) already
reads the mutation outbox directly, paged from an offset — "a consumer that needs the
stream before [E12's own bus] can page it from here." This route is that identical read,
looped: it polls `repository.read_events(after=...)` on a short interval and forwards
each new row to the browser as it arrives, rather than making the client re-poll. No new
storage, no new fact — the durable record is still the one `estate_event` row per
mutation, committed in the same transaction as the change it describes (`events.py`'s own
docstring); this route only removes the client's own polling loop.

**Deliberately open, like `GET /v1/events` already is.** That route carries no
`PrincipalDep`/role gate at all (confirmed by direct reading) — the raw outbox names no
client-sensitive fact beyond what every gated screen already renders from it. A second
reason applies only here: a browser's native `EventSource` cannot send custom request
headers, so it cannot carry this platform's own `X-Astra-Principal`/`X-Astra-Roles`
identity headers no matter how this route is gated — inheriting `GET /v1/events`'s own
open posture is not a new exception, it is the only posture a native `EventSource` client
could ever satisfy.

**Poll-under-the-hood, honestly.** No message bus exists yet (`published_at` on every
outbox row stays NULL until E12's own publisher exists — `events.py`'s own docstring).
`_POLL_INTERVAL_SECONDS` bounds the worst case a client waits for a new row to appear on
its stream; comfortably inside the 2-second budget without needing `LISTEN`/`NOTIFY` or a
real broker this deployment does not have.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator

from fastapi import APIRouter, Request
from fastapi.responses import StreamingResponse

from ..errors import InvalidRequestError
from .deps import RepositoryDep

router = APIRouter()

#: How often an idle connection re-checks the outbox for anything new. Comfortably inside
#: the AC's own 2-second p95 budget even before accounting for the query itself.
_POLL_INTERVAL_SECONDS = 0.5

#: A comment frame sent when nothing has happened for a while, so an idle proxy or load
#: balancer between the browser and this service does not decide the connection is dead.
_HEARTBEAT_EVERY_SECONDS = 15
_HEARTBEAT_TICKS = max(1, int(_HEARTBEAT_EVERY_SECONDS / _POLL_INTERVAL_SECONDS))

#: A page this large would only be reached by a client resuming after being disconnected
#: for a long time; live catch-up is a burst, not the steady state.
_MAX_CATCHUP_PAGE = 500


def _frame(event_type: str, sequence: int, payload: dict[str, object]) -> str:
    return f"event: {event_type}\nid: {sequence}\ndata: {json.dumps(payload)}\n\n"


async def _stream(
    request: Request, repository: RepositoryDep, after: int, subject: str | None
) -> AsyncIterator[str]:
    last_seq = after
    ticks_since_heartbeat = 0
    while True:
        if await request.is_disconnected():
            break
        page = await repository.read_events(after=last_seq, limit=_MAX_CATCHUP_PAGE, subject=subject)
        if page:
            for stored in page:
                yield _frame(stored.type.value, stored.sequence, stored.to_cloudevent())
            last_seq = page[-1].sequence
            ticks_since_heartbeat = 0
            continue  # a full page may mean more is waiting; check again immediately

        ticks_since_heartbeat += 1
        if ticks_since_heartbeat >= _HEARTBEAT_TICKS:
            yield ": keep-alive\n\n"
            ticks_since_heartbeat = 0
        await asyncio.sleep(_POLL_INTERVAL_SECONDS)


@router.get(
    "/v1/events:stream",
    tags=["events"],
    summary="The mutation event stream, pushed as server-sent events",
)
async def events_stream(
    request: Request,
    repository: RepositoryDep,
    after: int | None = None,
    subject: str | None = None,
) -> StreamingResponse:
    """Only events after ``after`` are sent — by default, only what happens *from now
    on*, matching "live updates", not a replay of the whole history over the wire (`GET
    /v1/events?after=0` already serves that, a page at a time)."""
    if after is not None and after < 0:
        raise InvalidRequestError("after must not be negative")
    start_after = after
    if start_after is None:
        start_after, _at = await repository.current_version()

    return StreamingResponse(
        _stream(request, repository, start_after, subject),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            # nginx's own default response buffering would hold every frame until the
            # buffer filled or the connection closed, defeating "≤ 2 s from event"
            # regardless of how fast this loop actually pushes them. `X-Accel-Buffering:
            # no` is nginx's own built-in override, honoured on the upstream response
            # with no location-specific config needed (confirmed: console-web's own
            # nginx.conf sets no `proxy_ignore_headers` that would suppress it).
            "X-Accel-Buffering": "no",
        },
    )
