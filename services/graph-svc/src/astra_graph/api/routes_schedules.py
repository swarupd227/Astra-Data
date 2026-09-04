"""Harvest schedules.

S1.2.4: the Harvester runs incrementally on a schedule. This is where a schedule is
created, paused and removed; ``GET /v1/platform/health`` is where it is read alongside
everything else about the platform's condition, because that is the screen the story names.

**There is no delete.** S1.1.3 made "hard deletes are not possible through the API" a
property of the whole service, and a schedule is not an exception worth carving out: "there
used to be a nightly harvest of this site" is exactly the fact somebody needs when they ask
why the graph went stale in March. A schedule that should stop is paused, with a reason. A
schedule that is simply wrong is amended in place, which keeps its run history attached to
the scope it belongs to.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Annotated, Any

from fastapi import APIRouter, Path, Request, status
from pydantic import BaseModel, ConfigDict, Field, model_validator

from ..credentials import CredentialError, validate_reference
from ..errors import ElementNotFoundError, InvalidRequestError
from ..harvest import (
    MAX_INTERVAL_MINUTES,
    MIN_INTERVAL_MINUTES,
    Cadence,
    ScheduleError,
    ScheduleStore,
    new_schedule,
)
from .deps import ArtizentDep, PrincipalDep

logger = logging.getLogger(__name__)

router = APIRouter()

_SCHEDULE_ID = Path(min_length=26, max_length=26, description="ULID of the schedule.")


class CadenceModel(BaseModel):
    model_config = ConfigDict(extra="forbid")

    every_minutes: int | None = Field(
        default=None,
        ge=MIN_INTERVAL_MINUTES,
        le=MAX_INTERVAL_MINUTES,
        description="Fire this often. Minimum five minutes.",
    )
    daily_at: str | None = Field(
        default=None,
        description="Fire once a day at this UTC time, as HH:MM.",
        examples=["02:00"],
    )

    @model_validator(mode="after")
    def exactly_one(self) -> CadenceModel:
        given = [f for f in (self.every_minutes, self.daily_at) if f is not None]
        if len(given) != 1:
            raise ValueError(
                "a cadence is either 'every_minutes' or 'daily_at', and exactly one of them"
            )
        return self


class CreateScheduleRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    site: str = Field(min_length=1, max_length=256)
    project: str | None = Field(default=None, max_length=256)
    credential: str = Field(
        min_length=3,
        max_length=128,
        description="Reference to the credential the run will use. Never a secret.",
        examples=["tableau/rqa"],
    )
    cadence: CadenceModel
    concurrency: int | None = Field(default=None, ge=1, le=64)
    parse_quality_threshold: float | None = Field(default=None, ge=0.0, le=1.0)


class UpdateScheduleRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    cadence: CadenceModel | None = None
    credential: str | None = Field(default=None, min_length=3, max_length=128)

    @model_validator(mode="after")
    def at_least_one(self) -> UpdateScheduleRequest:
        if self.cadence is None and self.credential is None:
            raise ValueError("give a cadence, a credential, or both")
        return self


class PauseRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    reason: str = Field(
        min_length=3,
        max_length=500,
        description="Why it is paused. Required: a schedule found paused months later "
        "with no reason is indistinguishable from one that was never set up.",
    )


class ScheduleListResponse(BaseModel):
    schedules: list[dict[str, Any]]
    count: int


def _store(request: Request) -> ScheduleStore:
    store: ScheduleStore | None = getattr(request.app.state, "schedule_store", None)
    if store is None:  # pragma: no cover - set in every wiring path
        raise InvalidRequestError("schedules are not available on this deployment")
    return store


def _cadence(model: CadenceModel) -> Cadence:
    try:
        return Cadence(every_minutes=model.every_minutes, daily_at=model.daily_at)
    except ScheduleError as exc:
        raise InvalidRequestError(str(exc)) from exc


@router.post(
    "/v1/harvest-schedules",
    status_code=status.HTTP_201_CREATED,
    tags=["harvest"],
    summary="Schedule a recurring incremental harvest",
)
async def create_schedule(
    body: CreateScheduleRequest,
    request: Request,
    principal: PrincipalDep,
    roles: ArtizentDep,
) -> dict[str, Any]:
    """Create a schedule. It does not run now — the first firing is one cadence away."""
    try:
        validate_reference(body.credential)
    except CredentialError as exc:
        raise InvalidRequestError(str(exc)) from exc

    schedule = new_schedule(
        site=body.site,
        project=body.project,
        credential_reference=body.credential,
        cadence=_cadence(body.cadence),
        created_by=principal.value,
        concurrency=body.concurrency,
        parse_quality_threshold=body.parse_quality_threshold,
    )
    try:
        created = await _store(request).create(schedule)
    except ScheduleError as exc:
        raise InvalidRequestError(str(exc)) from exc

    logger.info(
        "schedule %s created by %s: %s, %s",
        created.id,
        principal.value,
        created.site,
        created.cadence.describe(),
    )
    return created.as_dict()


@router.get(
    "/v1/harvest-schedules",
    response_model=ScheduleListResponse,
    tags=["harvest"],
    summary="Every harvest schedule, with its last run",
)
async def list_schedules(
    request: Request, principal: PrincipalDep, roles: ArtizentDep
) -> ScheduleListResponse:
    schedules = await _store(request).list_schedules()
    return ScheduleListResponse(
        schedules=[schedule.as_dict() for schedule in schedules], count=len(schedules)
    )


@router.post(
    "/v1/harvest-schedules/{schedule_id}:pause",
    tags=["harvest"],
    summary="Stop a schedule firing, keeping it and its history",
)
async def pause_schedule(
    body: PauseRequest,
    request: Request,
    principal: PrincipalDep,
    roles: ArtizentDep,
    schedule_id: Annotated[str, _SCHEDULE_ID],
) -> dict[str, Any]:
    updated = await _store(request).set_enabled(
        schedule_id, enabled=False, reason=f"{body.reason} (paused by {principal.value})"
    )
    if updated is None:
        raise ElementNotFoundError(f"no schedule with id '{schedule_id}'")
    logger.info("schedule %s paused by %s: %s", schedule_id, principal.value, body.reason)
    return updated.as_dict()


@router.post(
    "/v1/harvest-schedules/{schedule_id}:resume",
    tags=["harvest"],
    summary="Let a schedule fire again",
)
async def resume_schedule(
    request: Request,
    principal: PrincipalDep,
    roles: ArtizentDep,
    schedule_id: Annotated[str, _SCHEDULE_ID],
) -> dict[str, Any]:
    """Resume a paused schedule.

    The next firing is whatever ``next_run_at`` already said, which for a long pause is in
    the past — so it fires on the next poll. That is the intent: a schedule resumed after
    a change freeze should catch the estate up, not wait another day to start.
    """
    updated = await _store(request).set_enabled(schedule_id, enabled=True, reason=None)
    if updated is None:
        raise ElementNotFoundError(f"no schedule with id '{schedule_id}'")
    logger.info("schedule %s resumed by %s", schedule_id, principal.value)
    return updated.as_dict()


@router.patch(
    "/v1/harvest-schedules/{schedule_id}",
    tags=["harvest"],
    summary="Change a schedule's cadence or credential",
)
async def update_schedule(
    body: UpdateScheduleRequest,
    request: Request,
    principal: PrincipalDep,
    roles: ArtizentDep,
    schedule_id: Annotated[str, _SCHEDULE_ID],
) -> dict[str, Any]:
    """Amend a schedule in place, keeping its history.

    The scope is not amendable: a schedule of a different site is a different schedule, and
    moving one would attach one site's run history to another's.
    """
    if body.credential is not None:
        try:
            validate_reference(body.credential)
        except CredentialError as exc:
            raise InvalidRequestError(str(exc)) from exc

    updated = await _store(request).update(
        schedule_id,
        cadence=_cadence(body.cadence) if body.cadence else None,
        credential_reference=body.credential,
        now=datetime.now(UTC),
    )
    if updated is None:
        raise ElementNotFoundError(f"no schedule with id '{schedule_id}'")
    logger.info(
        "schedule %s amended by %s: %s", schedule_id, principal.value, updated.cadence.describe()
    )
    return updated.as_dict()


__all__ = ["router"]
