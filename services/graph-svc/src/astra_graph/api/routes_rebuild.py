"""Rebuilding the estate from the event stream — story S10.1.2, opening F10.1's second AC:
"projections are rebuilt from the event stream; a rebuild from empty is a supported
operation with a progress indicator."

**What "rebuild" means here.** Every console screen already reads the live Apache AGE
graph directly (confirmed by direct research: no materialised read-model or cache layer
exists anywhere in this codebase) — so a screen is already, in the sense that matters, a
projection of whatever the graph currently holds. What has never been *proven*, only
promised (S1.1.3: "a replay of the event stream from empty produces a graph identical to
the live graph"), is that the event stream is a *complete* record of that graph — that
the console never shows a state the evidence chain does not have, this story's own "So
that". This route does not switch the console over to a rebuilt graph (a hot cutover of
the live serving graph is a materially different, riskier operation this story's AC never
asks for); it proves the claim, on demand, with a progress indicator, using the identical
replay-and-compare machinery `tools/verify_replay.py`'s own nightly CI job already runs
(`replay.py`, `graph/scratch.py`) — one code path, not a second one that could quietly
diverge from what the CI job actually checks.

Progress is an in-memory status flag, not a persisted run history, the identical
`TrainProposalStatus` shape `routes_trains.py` already set for the same reason (`trains.
py`'s own module docstring) — a rebuild is a rare, operator-triggered verification, not a
fact anything else in this platform depends on later.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

import asyncpg
from fastapi import APIRouter, Request, status

from ..config import settings
from ..errors import InvalidRequestError
from ..graph import AgeGraphRepository, prepare_scratch_graph, teardown_scratch_graph
from ..principal import Principal
from ..replay import compare, replay
from .deps import ArtizentDep, PlatformEngineerDep, PrincipalDep, RepositoryDep

logger = logging.getLogger(__name__)

router = APIRouter()


@dataclass
class RebuildStatus:
    """In-memory, one per process — see this module's own docstring for why this is not
    a persisted run history."""

    running: bool = False
    started_at: str | None = None
    finished_at: str | None = None
    events_total: int = 0
    events_applied: int = 0
    last_result: dict[str, Any] | None = None
    last_error: str | None = None
    tasks: set[asyncio.Task[Any]] = field(default_factory=set)

    def as_dict(self) -> dict[str, Any]:
        return {
            "running": self.running,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "events_total": self.events_total,
            "events_applied": self.events_applied,
            "last_result": self.last_result,
            "last_error": self.last_error,
        }


def _status(request: Request) -> RebuildStatus:
    existing: RebuildStatus | None = getattr(request.app.state, "rebuild_status", None)
    if existing is None:  # pragma: no cover - set in every wiring path
        existing = RebuildStatus()
        request.app.state.rebuild_status = existing
    return existing


def _pool(request: Request) -> asyncpg.Pool:
    pool: asyncpg.Pool | None = getattr(request.app.state, "pool", None)
    if pool is None:  # pragma: no cover - set in every wiring path
        raise InvalidRequestError("graph store is not ready")
    return pool


async def _run(
    tracker: RebuildStatus,
    pool: asyncpg.Pool,
    live_graph_name: str,
    scratch_graph_name: str,
    principal: Principal,
) -> None:
    # A dedicated connection, not one borrowed from `pool` -- Apache AGE caches label
    # relations per session, and a `create_graph`/`drop_graph` on a connection the pool
    # later hands to unrelated Cypher work leaves that connection's own cache stale for
    # it (`test_integration_events.py`'s own fixture teardown carries the identical
    # comment: "one connection per drop"). Using the shared pool here once left a
    # `finally` block's own `drop_graph` call raising after a successful rebuild,
    # skipping the two lines right after it -- `tracker.running` never returned to
    # `False`, so the console's own progress indicator would have spun forever despite
    # the rebuild having already finished correctly.
    dsn = settings().dsn
    conn = await asyncpg.connect(dsn=dsn)
    try:
        await prepare_scratch_graph(conn, scratch_graph_name)
    finally:
        await conn.close()

    try:
        live = AgeGraphRepository(pool, graph_name=live_graph_name)
        target = AgeGraphRepository(pool, graph_name=scratch_graph_name)

        def on_progress(applied: int) -> None:
            tracker.events_applied = applied

        result = await replay(live, target, on_progress=on_progress)
        comparison = compare(await live.dump(), await target.dump())

        tracker.last_result = {
            "events_applied": result.events_applied,
            "nodes": result.nodes,
            "edges": result.edges,
            "retirements": result.retirements,
            "notices": result.notices,
            "identical": comparison.identical,
            "live_nodes": comparison.live_nodes,
            "live_edges": comparison.live_edges,
            "summary": comparison.summary(),
            "differences": [
                {"kind": d.kind, "element_id": d.element_id, "detail": d.detail}
                for d in comparison.differences[:40]
            ],
            "principal": principal.value,
        }
        tracker.last_error = None
    except Exception as exc:  # reported on the status, not swallowed
        logger.exception("event stream rebuild failed")
        tracker.last_error = str(exc)
    finally:
        # A teardown failure must never leave `running` stuck `True` -- the rebuild's
        # own real result (recorded above) is worth more than a clean scratch graph, and
        # a failed drop here is the identical, disclosed AGE quirk this function's own
        # docstring names, not a reason to hide that the rebuild itself finished.
        try:
            conn = await asyncpg.connect(dsn=dsn)
            try:
                await teardown_scratch_graph(conn, scratch_graph_name)
            finally:
                await conn.close()
        except Exception:
            logger.exception("could not tear down the rebuild's own scratch graph %s", scratch_graph_name)
        tracker.running = False
        tracker.finished_at = datetime.now(UTC).isoformat()


@router.post(
    "/v1/graph:rebuild",
    status_code=status.HTTP_202_ACCEPTED,
    tags=["events"],
    summary="Rebuild the estate from the event stream, from empty, and compare it against the live graph",
)
async def start_rebuild(
    request: Request, principal: PrincipalDep, roles: PlatformEngineerDep, repository: RepositoryDep
) -> dict[str, Any]:
    tracker = _status(request)
    if tracker.running:
        raise InvalidRequestError(
            "a rebuild is already in progress on this deployment; only one runs at a "
            "time, so a concurrent run cannot leave the scratch graph in a state neither "
            "run actually produced"
        )

    pool = _pool(request)
    live_graph_name = repository.graph_name
    events_total = await repository.count_events()

    tracker.running = True
    tracker.started_at = datetime.now(UTC).isoformat()
    tracker.finished_at = None
    tracker.events_total = events_total
    tracker.events_applied = 0

    task = asyncio.create_task(
        _run(tracker, pool, live_graph_name, f"{live_graph_name}_rebuild", principal)
    )
    tracker.tasks.add(task)
    task.add_done_callback(tracker.tasks.discard)

    logger.info("event stream rebuild accepted by %s (events_total=%s)", principal.value, events_total)
    return {"state": "QUEUED", "events_total": events_total}


@router.get(
    "/v1/graph:rebuild/status",
    tags=["events"],
    summary="Whether a rebuild is in progress, its progress, and the last comparison",
)
async def rebuild_status(request: Request, principal: PrincipalDep, roles: ArtizentDep) -> dict[str, Any]:
    return _status(request).as_dict()
