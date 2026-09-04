"""Release trains — the train planner's API. Stories S3.2.1, S3.2.2 and S3.2.3.

``POST /v1/trains:propose`` starts a run and returns immediately, the same shape as
``POST /v1/families:cluster``: proposing trains reads the whole estate's families and
usage, and there is no reason a caller should sit blocked on it. Progress is a status flag
rather than a persisted run history, for the same reason S3.1.1's clustering has none — see
``trains.py``'s module docstring.

The Wave Board's own actions — ``:move-member``, ``:resequence-member``,
``:set-wip-limits`` — are Programme Manager actions (the story's own "As a"), unlike
``:propose`` and the reads, which stay ``ArtizentDep`` like every other families/trains
route. ``GET /v1/trains/{id}/events`` is story S3.2.2's "every change... appears on the
Programme timeline": every Wave Board write already goes through ``GraphWriter`` and so
already emits a CloudEvent (``GET /v1/events`` reads the raw outbox); this route just
resolves which subjects belong to one train and returns their recent history together.

``GET /v1/trains:projections`` is story S3.2.3 — projected versus planned dates. It
measures throughput once for the whole estate and projects every train from that one
pass, rather than one query per train; see ``train_projection.py`` for why "insufficient
data" is the honest answer for every train in an estate nothing has yet driven through
§3.2's states.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from typing import Annotated, Any

from fastapi import APIRouter, Path, Query, Request, status
from pydantic import BaseModel, ConfigDict, Field

from ..errors import ElementNotFoundError, InvalidRequestError
from ..principal import Principal
from ..train_overrides import move_mu, resequence_mu, set_wip_limits
from ..train_projection import (
    DEFAULT_LATE_THRESHOLD_WORKING_DAYS,
    DEFAULT_TRAILING_DAYS,
    project_trains,
)
from ..trains import (
    BLACKROCK_DEFAULT_TRAIN_SIZES,
    DEFAULT_TRAIN_DURATION_DAYS,
    TrainPlanner,
    get_train,
    list_trains,
    train_event_subjects,
)
from .deps import ArtizentDep, PrincipalDep, ProgrammeManagerDep, RepositoryDep

logger = logging.getLogger(__name__)

router = APIRouter()

_TRAIN_ID = Path(min_length=5, max_length=64, description="ULID of the ReleaseTrain.")

#: How far back GET /v1/trains/{id}/events looks — a recent-activity view for the Wave
#: Board, not a full audit archive (GET /v1/events?subject= already serves that, one
#: element at a time). Bounded so a train with a long history costs one query, not one
#: per historical edge.
_EVENT_WINDOW = 2000


@dataclass
class TrainProposalStatus:
    """In-memory, one per process — see the module docstring for why this is not a
    persisted run history."""

    running: bool = False
    started_at: str | None = None
    finished_at: str | None = None
    last_result: dict[str, Any] | None = None
    last_error: str | None = None
    tasks: set[asyncio.Task[Any]] = field(default_factory=set)

    def as_dict(self) -> dict[str, Any]:
        return {
            "running": self.running,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "last_result": self.last_result,
            "last_error": self.last_error,
        }


class StartTrainProposalRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    train_sizes: list[int] = Field(
        default_factory=lambda: list(BLACKROCK_DEFAULT_TRAIN_SIZES),
        min_length=1,
        max_length=1000,
        description="Target MU count per train, in order. Defaults to the BlackRock "
        "5-train plan (277/328/184/177/101); sizes are editable.",
    )
    start_date: date | None = Field(
        default=None, description="First train's planned start. Defaults to today."
    )
    duration_days: int = Field(
        default=DEFAULT_TRAIN_DURATION_DAYS,
        ge=1,
        description="Calendar days each train's planned window spans.",
    )
    confirm_train_ids: list[str] = Field(
        default_factory=list,
        max_length=10_000,
        description="Overridden trains (S3.2.2) to unpin for this run — their members "
        "re-enter packing and the train may be retired and replaced. Everything else "
        "overridden is left exactly as the Wave Board last set it.",
    )


class MoveMemberRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    workbook_id: str = Field(min_length=1, max_length=64)
    reason: str | None = Field(
        default=None,
        max_length=2000,
        description="Only required if the move would exceed a WIP limit configured on "
        "the destination train.",
    )


class ResequenceMemberRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    workbook_id: str = Field(min_length=1, max_length=64)
    position: int = Field(ge=1, description="1-based position within the train.")


class SetWipLimitsRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    train_limit: int | None = Field(default=None, ge=1)
    state_limits: dict[str, int] = Field(default_factory=dict)
    reason: str = Field(min_length=1, max_length=2000)


def _planner(request: Request) -> TrainPlanner:
    planner: TrainPlanner | None = getattr(request.app.state, "train_planner", None)
    if planner is None:
        raise InvalidRequestError("train proposals are not available on this deployment")
    return planner


def _status(request: Request) -> TrainProposalStatus:
    existing: TrainProposalStatus | None = getattr(request.app.state, "train_status", None)
    if existing is None:  # pragma: no cover - set in every wiring path
        existing = TrainProposalStatus()
        request.app.state.train_status = existing
    return existing


async def _run(
    planner: TrainPlanner,
    tracker: TrainProposalStatus,
    request_body: StartTrainProposalRequest,
    principal: Principal,
) -> None:
    try:
        result = await planner.run(
            principal=principal,
            train_sizes=request_body.train_sizes,
            start_date=request_body.start_date,
            duration_days=request_body.duration_days,
            confirm_train_ids=frozenset(request_body.confirm_train_ids),
        )
        tracker.last_result = result.as_dict()
        tracker.last_error = None
    except Exception as exc:  # reported on the status, not swallowed
        logger.exception("train proposal failed")
        tracker.last_error = str(exc)
    finally:
        tracker.running = False
        tracker.finished_at = datetime.now(UTC).isoformat()


@router.post(
    "/v1/trains:propose",
    status_code=status.HTTP_202_ACCEPTED,
    tags=["trains"],
    summary="Propose release trains from the estate's families and usage (§3.3)",
)
async def start_proposal(
    body: StartTrainProposalRequest,
    request: Request,
    principal: PrincipalDep,
    roles: ArtizentDep,
) -> dict[str, Any]:
    tracker = _status(request)
    if tracker.running:
        raise InvalidRequestError(
            "a train proposal is already in progress on this deployment; only one runs at "
            "a time, so a concurrent run cannot leave the graph in a state neither run "
            "actually produced"
        )

    tracker.running = True
    tracker.started_at = datetime.now(UTC).isoformat()
    tracker.finished_at = None

    task = asyncio.create_task(_run(_planner(request), tracker, body, principal))
    tracker.tasks.add(task)
    task.add_done_callback(tracker.tasks.discard)

    logger.info(
        "train proposal accepted by %s (train_sizes=%s, duration_days=%s)",
        principal.value,
        body.train_sizes,
        body.duration_days,
    )
    return {"state": "QUEUED", "train_sizes": body.train_sizes, "duration_days": body.duration_days}


@router.get(
    "/v1/trains:propose/status",
    tags=["trains"],
    summary="Whether a train proposal is in progress, and the last one's figures",
)
async def proposal_status(
    request: Request, principal: PrincipalDep, roles: ArtizentDep
) -> dict[str, Any]:
    return _status(request).as_dict()


@router.get(
    "/v1/trains",
    tags=["trains"],
    summary="Every release train, with its members in sequence",
)
async def get_trains(
    request: Request,
    principal: PrincipalDep,
    roles: ArtizentDep,
) -> dict[str, Any]:
    engine = _planner(request)
    trains = await list_trains(engine.pool, engine.graph_name)
    return {"trains": trains, "count": len(trains)}


@router.get(
    "/v1/trains:projections",
    tags=["trains"],
    summary="Projected versus planned dates per train, from measured throughput (§14.2)",
)
async def get_projections(
    request: Request,
    principal: PrincipalDep,
    roles: ArtizentDep,
    trailing_days: Annotated[int, Query(ge=1, le=365)] = DEFAULT_TRAILING_DAYS,
    late_threshold_working_days: Annotated[int, Query(ge=0, le=365)] = (
        DEFAULT_LATE_THRESHOLD_WORKING_DAYS
    ),
    now: Annotated[
        date | None,
        Query(description="Project as of this date instead of today; mainly for testing."),
    ] = None,
) -> dict[str, Any]:
    engine = _planner(request)
    trains = await list_trains(engine.pool, engine.graph_name)
    projections = await project_trains(
        engine.pool,
        engine.graph_name,
        trains,
        trailing_days=trailing_days,
        late_threshold_working_days=late_threshold_working_days,
        now=now,
    )
    return {
        "trailing_days": trailing_days,
        "late_threshold_working_days": late_threshold_working_days,
        "projections": [p.as_dict() for p in projections],
        "flagged_count": sum(1 for p in projections if p.flagged),
    }


@router.get(
    "/v1/trains/{train_id}",
    tags=["trains"],
    summary="One release train: members, planned dates, gate schedule",
)
async def get_one_train(
    request: Request,
    principal: PrincipalDep,
    roles: ArtizentDep,
    train_id: Annotated[str, _TRAIN_ID],
) -> dict[str, Any]:
    engine = _planner(request)
    train = await get_train(engine.pool, engine.graph_name, train_id)
    if train is None:
        raise ElementNotFoundError(f"no release train '{train_id}'")
    return train


@router.post(
    "/v1/trains/{train_id}:move-member",
    tags=["trains"],
    summary="Move one MU into this train, out of wherever it is now (Wave Board, §3.3)",
)
async def move_member(
    body: MoveMemberRequest,
    request: Request,
    principal: PrincipalDep,
    roles: ProgrammeManagerDep,
    train_id: Annotated[str, _TRAIN_ID],
) -> dict[str, Any]:
    engine = _planner(request)
    result = await move_mu(
        engine.pool,
        engine.graph_name,
        engine.writer,
        workbook_id=body.workbook_id,
        to_train_id=train_id,
        reason=body.reason,
        principal=principal,
    )
    logger.info(
        "workbook %s moved by %s from %s into %s",
        body.workbook_id, principal.value, result.from_train_id, train_id,
    )
    return result.as_dict()


@router.post(
    "/v1/trains/{train_id}:resequence-member",
    tags=["trains"],
    summary="Reorder one MU within this train (Wave Board)",
)
async def resequence_member(
    body: ResequenceMemberRequest,
    request: Request,
    principal: PrincipalDep,
    roles: ProgrammeManagerDep,
    train_id: Annotated[str, _TRAIN_ID],
) -> dict[str, Any]:
    engine = _planner(request)
    resolved_train_id, position = await resequence_mu(
        engine.pool,
        engine.graph_name,
        engine.writer,
        workbook_id=body.workbook_id,
        position=body.position,
        principal=principal,
    )
    if resolved_train_id != train_id:
        raise ElementNotFoundError(
            f"workbook '{body.workbook_id}' is in train '{resolved_train_id}', not '{train_id}'"
        )
    logger.info(
        "workbook %s resequenced by %s to position %d in %s",
        body.workbook_id, principal.value, position, train_id,
    )
    return {"train_id": train_id, "workbook_id": body.workbook_id, "position": position}


@router.post(
    "/v1/trains/{train_id}:set-wip-limits",
    tags=["trains"],
    summary="Configure this train's work-in-progress caps (Wave Board)",
)
async def set_train_wip_limits(
    body: SetWipLimitsRequest,
    request: Request,
    principal: PrincipalDep,
    roles: ProgrammeManagerDep,
    train_id: Annotated[str, _TRAIN_ID],
) -> dict[str, Any]:
    engine = _planner(request)
    wip_limits = await set_wip_limits(
        engine.pool,
        engine.graph_name,
        engine.writer,
        train_id=train_id,
        train_limit=body.train_limit,
        state_limits=body.state_limits,
        reason=body.reason,
        principal=principal,
    )
    logger.info("WIP limits set by %s on %s: %s", principal.value, train_id, wip_limits)
    return {"train_id": train_id, "wip_limits": wip_limits}


@router.get(
    "/v1/trains/{train_id}/events",
    tags=["trains"],
    summary="Recent changes to this train — the Programme timeline's own feed for it",
)
async def train_events(
    request: Request,
    principal: PrincipalDep,
    roles: ArtizentDep,
    repository: RepositoryDep,
    train_id: Annotated[str, _TRAIN_ID],
) -> dict[str, Any]:
    engine = _planner(request)
    train = await get_train(engine.pool, engine.graph_name, train_id)
    if train is None:
        raise ElementNotFoundError(f"no release train '{train_id}'")

    subjects = set(await train_event_subjects(engine.pool, engine.graph_name, train_id))
    current_version, _at = await repository.current_version()
    after = max(0, current_version - _EVENT_WINDOW)
    recent = await repository.read_events(after=after, limit=_EVENT_WINDOW)
    matching = [event for event in recent if event.subject in subjects]
    return {
        "train_id": train_id,
        "events": [
            {"sequence": event.sequence, "event": event.to_cloudevent()} for event in matching
        ],
        "window": _EVENT_WINDOW,
    }
