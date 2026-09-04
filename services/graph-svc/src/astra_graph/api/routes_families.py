"""Model families — the Cartographer's API. Story S3.1.1.

``POST /v1/families:cluster`` starts a run and returns immediately, the same shape as
``POST /v1/harvests``: a clustering pass over a large estate is not a request a caller
should sit blocked on, and the criterion's own "under 30 minutes" says it can be a while
even when it works. Progress is a status flag rather than a persisted run history — see
``cartographer.py``'s migration for why a full run ledger is not what this story asks for;
the durable result of the *last* run is the programme record, which ``GET
/v1/families:cluster/status`` also surfaces for convenience.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Annotated, Any

from fastapi import APIRouter, Path, Query, Request, status
from pydantic import BaseModel, ConfigDict, Field

from ..cartographer import (
    DEFAULT_MIN_FAMILY_SIZE,
    DEFAULT_THRESHOLD,
    Cartographer,
    get_family,
    list_families,
)
from ..errors import ElementNotFoundError, InvalidRequestError
from ..family_overrides import merge_families, move_member, split_family
from ..principal import Principal
from .deps import ArtizentDep, PrincipalDep

logger = logging.getLogger(__name__)

router = APIRouter()

_FAMILY_ID = Path(min_length=5, max_length=64, description="ULID of the ModelFamily.")


@dataclass
class ClusteringStatus:
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


class StartClusteringRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    threshold: float = Field(default=DEFAULT_THRESHOLD, ge=0.0, le=1.0)
    min_family_size: int = Field(default=DEFAULT_MIN_FAMILY_SIZE, ge=1, le=1000)
    confirm_family_ids: list[str] = Field(
        default_factory=list,
        max_length=10_000,
        description="Overridden families (S3.1.2) to unpin for this run — their members "
        "re-enter clustering and the family may be retired and replaced. Everything else "
        "overridden is left exactly as it is, reported in the result's `would_change`.",
    )


class SplitFamilyRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    member_ids: list[str] = Field(min_length=1, max_length=10_000)
    reason: str = Field(min_length=1, max_length=2000)


class MergeFamiliesRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    family_ids: tuple[str, str]
    reason: str = Field(min_length=1, max_length=2000)


class MoveMemberRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    workbook_id: str = Field(min_length=1, max_length=64)
    reason: str = Field(min_length=1, max_length=2000)


def _cartographer(request: Request) -> Cartographer:
    engine: Cartographer | None = getattr(request.app.state, "cartographer", None)
    if engine is None:
        raise InvalidRequestError("clustering is not available on this deployment")
    return engine


def _status(request: Request) -> ClusteringStatus:
    existing: ClusteringStatus | None = getattr(request.app.state, "cartographer_status", None)
    if existing is None:  # pragma: no cover - set in every wiring path
        existing = ClusteringStatus()
        request.app.state.cartographer_status = existing
    return existing


async def _run(
    engine: Cartographer,
    tracker: ClusteringStatus,
    request_body: StartClusteringRequest,
    principal: Principal,
) -> None:
    try:
        result = await engine.run(
            principal=principal,
            threshold=request_body.threshold,
            min_family_size=request_body.min_family_size,
            confirm_family_ids=frozenset(request_body.confirm_family_ids),
        )
        tracker.last_result = result.as_dict()
        tracker.last_error = None
    except Exception as exc:  # reported on the status, not swallowed
        logger.exception("clustering run failed")
        tracker.last_error = str(exc)
    finally:
        tracker.running = False
        tracker.finished_at = datetime.now(UTC).isoformat()


@router.post(
    "/v1/families:cluster",
    status_code=status.HTTP_202_ACCEPTED,
    tags=["families"],
    summary="Cluster the estate into candidate model families (§12.1)",
)
async def start_clustering(
    body: StartClusteringRequest,
    request: Request,
    principal: PrincipalDep,
    roles: ArtizentDep,
) -> dict[str, Any]:
    tracker = _status(request)
    if tracker.running:
        raise InvalidRequestError(
            "a clustering run is already in progress on this deployment; only one runs at "
            "a time, so a concurrent run cannot leave the graph in a state neither run "
            "actually produced"
        )

    tracker.running = True
    tracker.started_at = datetime.now(UTC).isoformat()
    tracker.finished_at = None

    task = asyncio.create_task(_run(_cartographer(request), tracker, body, principal))
    tracker.tasks.add(task)
    task.add_done_callback(tracker.tasks.discard)

    logger.info(
        "clustering run accepted by %s (threshold=%s, min_family_size=%s)",
        principal.value,
        body.threshold,
        body.min_family_size,
    )
    return {"state": "QUEUED", "threshold": body.threshold, "min_family_size": body.min_family_size}


@router.get(
    "/v1/families:cluster/status",
    tags=["families"],
    summary="Whether a clustering run is in progress, and the last one's figures",
)
async def clustering_status(
    request: Request, principal: PrincipalDep, roles: ArtizentDep
) -> dict[str, Any]:
    return _status(request).as_dict()


@router.get(
    "/v1/families",
    tags=["families"],
    summary="Every model family, with its members",
)
async def get_families(
    request: Request,
    principal: PrincipalDep,
    roles: ArtizentDep,
    state: Annotated[str | None, Query(max_length=32)] = None,
) -> dict[str, Any]:
    engine = _cartographer(request)
    families = await list_families(engine.pool, engine.graph_name, state=state)
    return {"families": families, "count": len(families)}


@router.get(
    "/v1/families/{family_id}",
    tags=["families"],
    summary="One model family: members, grain, evidence",
)
async def get_one_family(
    request: Request,
    principal: PrincipalDep,
    roles: ArtizentDep,
    family_id: Annotated[str, _FAMILY_ID],
) -> dict[str, Any]:
    engine = _cartographer(request)
    family = await get_family(engine.pool, engine.graph_name, family_id)
    if family is None:
        raise ElementNotFoundError(f"no model family '{family_id}'")
    return family


@router.post(
    "/v1/families/{family_id}:split",
    status_code=status.HTTP_201_CREATED,
    tags=["families"],
    summary="Move selected members out of a family into a new one",
)
async def split(
    body: SplitFamilyRequest,
    request: Request,
    principal: PrincipalDep,
    roles: ArtizentDep,
    family_id: Annotated[str, _FAMILY_ID],
) -> dict[str, Any]:
    engine = _cartographer(request)
    remainder, new_family = await split_family(
        engine.pool,
        engine.graph_name,
        engine.writer,
        family_id=family_id,
        member_ids=body.member_ids,
        reason=body.reason,
        principal=principal,
    )
    logger.info(
        "family %s split by %s into %s and %s: %s",
        family_id, principal.value, remainder.id, new_family.id, body.reason,
    )
    return {"remainder": remainder.as_dict(), "new_family": new_family.as_dict()}


@router.post(
    "/v1/families:merge",
    status_code=status.HTTP_201_CREATED,
    tags=["families"],
    summary="Combine two families into one",
)
async def merge(
    body: MergeFamiliesRequest,
    request: Request,
    principal: PrincipalDep,
    roles: ArtizentDep,
) -> dict[str, Any]:
    engine = _cartographer(request)
    merged = await merge_families(
        engine.pool,
        engine.graph_name,
        engine.writer,
        family_ids=body.family_ids,
        reason=body.reason,
        principal=principal,
    )
    logger.info(
        "families %s merged by %s into %s: %s",
        body.family_ids, principal.value, merged.id, body.reason,
    )
    return merged.as_dict()


@router.post(
    "/v1/families/{family_id}:add-member",
    tags=["families"],
    summary="Move one workbook into this family, out of wherever it is now",
)
async def add_member(
    body: MoveMemberRequest,
    request: Request,
    principal: PrincipalDep,
    roles: ArtizentDep,
    family_id: Annotated[str, _FAMILY_ID],
) -> dict[str, Any]:
    engine = _cartographer(request)
    moved = await move_member(
        engine.pool,
        engine.graph_name,
        engine.writer,
        workbook_id=body.workbook_id,
        to_family_id=family_id,
        reason=body.reason,
        principal=principal,
    )
    logger.info(
        "workbook %s moved by %s into %s: %s",
        body.workbook_id, principal.value, family_id, body.reason,
    )
    return {
        "target": moved.target.as_dict(),
        "source": moved.source.as_dict() if moved.source is not None else None,
    }
