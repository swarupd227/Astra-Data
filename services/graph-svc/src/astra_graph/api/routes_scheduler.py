"""Wave scheduler routes: programme manager control of throughput and admission.

Story S12.1.2: Scheduler admits MUs by train sequence subject to concurrency, budget,
and WIP limits. Routes for pause/resume per train and per site, and querying scheduler
decisions visible on the Wave Board.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Request
from pydantic import BaseModel, ConfigDict, Field

from ..errors import InvalidRequestError
from ..wave_scheduler import SchedulerConstraint, WaveScheduler
from .deps import ArtizentDep, PlatformEngineerDep, PrincipalDep, RepositoryDep

router = APIRouter()


class PauseTrainRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    reason: str = Field(min_length=1, max_length=500)


class ResumeTrainRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    reason: str = Field(min_length=1, max_length=500)


class PauseSiteRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    reason: str = Field(min_length=1, max_length=500)


class ResumeSiteRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    reason: str = Field(min_length=1, max_length=500)


class SchedulerDecisionResponse(BaseModel):
    """Scheduler's admission decision for an MU."""
    workbook_id: str
    train_id: str
    admitted: bool
    blocking_constraint: SchedulerConstraint | None = None
    reason: str


@router.get(
    "/v1/scheduler/decision/{workbook_id}/{train_id}",
    tags=["scheduler"],
    summary="Get the wave scheduler's admission decision for an MU",
    response_model=SchedulerDecisionResponse,
)
async def get_admission_decision(
    workbook_id: str,
    train_id: str,
    request: Request,
    principal: PrincipalDep,
    roles: ArtizentDep,
    repository: RepositoryDep,
) -> dict[str, Any]:
    """Query the scheduler to see why an MU is waiting or whether it can be admitted."""
    db = repository.db
    scheduler = WaveScheduler(db)

    # Query the graph to find the site for this MU
    row = await db.fetchrow(
        """
        SELECT site.nid as site_id
        FROM nodes wb
        JOIN edges e ON e.head = wb.nid AND e.label = 'IN_ESTATE'
        JOIN nodes site ON site.nid = e.tail AND site.label = 'Site'
        WHERE wb.nid = $1 AND wb.label = 'Workbook'
        LIMIT 1
        """,
        workbook_id,
    )
    if not row:
        raise InvalidRequestError(f"Workbook {workbook_id} not found or has no site")

    site_id = row["site_id"]
    decision = await scheduler.evaluate_admission(workbook_id, train_id, site_id)

    return {
        "workbook_id": workbook_id,
        "train_id": train_id,
        "admitted": decision.admitted,
        "blocking_constraint": decision.blocking_constraint,
        "reason": decision.reason,
    }


@router.post(
    "/v1/scheduler/trains/{train_id}:pause",
    tags=["scheduler"],
    summary="Pause a release train, holding all its MUs at their current state",
)
async def post_pause_train(
    train_id: str,
    body: PauseTrainRequest,
    request: Request,
    principal: PrincipalDep,
    roles: PlatformEngineerDep,
    repository: RepositoryDep,
) -> dict[str, Any]:
    """Pause a train to prevent new MUs from being admitted to execution."""
    db = repository.db
    writer = repository.graph_writer

    # Check train exists
    row = await db.fetchrow(
        "SELECT nid FROM nodes WHERE nid = $1 AND label = 'ReleaseTrain'",
        train_id,
    )
    if not row:
        raise InvalidRequestError(f"Train {train_id} not found")

    # Update the train's paused property
    await writer.set_node_properties(
        train_id,
        {"paused": True, "pause_reason": body.reason},
        principal=principal,
    )

    return {
        "train_id": train_id,
        "paused": True,
        "reason": body.reason,
    }


@router.post(
    "/v1/scheduler/trains/{train_id}:resume",
    tags=["scheduler"],
    summary="Resume a paused release train",
)
async def post_resume_train(
    train_id: str,
    body: ResumeTrainRequest,
    request: Request,
    principal: PrincipalDep,
    roles: PlatformEngineerDep,
    repository: RepositoryDep,
) -> dict[str, Any]:
    """Resume a paused train to allow MU admissions again."""
    db = repository.db
    writer = repository.graph_writer

    # Check train exists
    row = await db.fetchrow(
        "SELECT nid FROM nodes WHERE nid = $1 AND label = 'ReleaseTrain'",
        train_id,
    )
    if not row:
        raise InvalidRequestError(f"Train {train_id} not found")

    # Update the train's paused property
    await writer.set_node_properties(
        train_id,
        {"paused": False, "pause_reason": None},
        principal=principal,
    )

    return {
        "train_id": train_id,
        "paused": False,
        "reason": body.reason,
    }


@router.post(
    "/v1/scheduler/sites/{site_id}:pause",
    tags=["scheduler"],
    summary="Pause a source site, holding all its MUs at their current state",
)
async def post_pause_site(
    site_id: str,
    body: PauseSiteRequest,
    request: Request,
    principal: PrincipalDep,
    roles: PlatformEngineerDep,
    repository: RepositoryDep,
) -> dict[str, Any]:
    """Pause a site to prevent any of its MUs from being admitted to execution."""
    db = repository.db
    writer = repository.graph_writer

    # Check site exists
    row = await db.fetchrow(
        "SELECT nid FROM nodes WHERE nid = $1 AND label = 'Site'",
        site_id,
    )
    if not row:
        raise InvalidRequestError(f"Site {site_id} not found")

    # Update the site's paused property
    await writer.set_node_properties(
        site_id,
        {"paused": True, "pause_reason": body.reason},
        principal=principal,
    )

    return {
        "site_id": site_id,
        "paused": True,
        "reason": body.reason,
    }


@router.post(
    "/v1/scheduler/sites/{site_id}:resume",
    tags=["scheduler"],
    summary="Resume a paused source site",
)
async def post_resume_site(
    site_id: str,
    body: ResumeSiteRequest,
    request: Request,
    principal: PrincipalDep,
    roles: PlatformEngineerDep,
    repository: RepositoryDep,
) -> dict[str, Any]:
    """Resume a paused site to allow MU admissions again."""
    db = repository.db
    writer = repository.graph_writer

    # Check site exists
    row = await db.fetchrow(
        "SELECT nid FROM nodes WHERE nid = $1 AND label = 'Site'",
        site_id,
    )
    if not row:
        raise InvalidRequestError(f"Site {site_id} not found")

    # Update the site's paused property
    await writer.set_node_properties(
        site_id,
        {"paused": False, "pause_reason": None},
        principal=principal,
    )

    return {
        "site_id": site_id,
        "paused": False,
        "reason": body.reason,
    }
