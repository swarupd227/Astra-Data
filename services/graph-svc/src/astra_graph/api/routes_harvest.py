"""Harvest control and progress.

S1.2.1: "Harvest is started from the Estate Explorer or the API with site credentials from
Key Vault; progress is visible per project with counts of workbooks queued, parsed,
failed". The Estate Explorer is F1.4; this is the API it will call.

A request names a credential — it never carries one. See ``credentials.py`` for why.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Annotated, Any

from fastapi import APIRouter, Path, Request, status
from pydantic import BaseModel, ConfigDict, Field

from ..adapters.contract import Scope
from ..credentials import CredentialError, validate_reference
from ..errors import ElementNotFoundError, InvalidRequestError
from ..harvest import (
    DEFAULT_CONCURRENCY,
    DEFAULT_PARSE_QUALITY_THRESHOLD,
    DEFAULT_USAGE_WINDOW_DAYS,
    Harvester,
    HarvestRequest,
    HarvestStore,
)
from ..ids import new_ulid
from .deps import ArtizentDep, PrincipalDep

logger = logging.getLogger(__name__)

router = APIRouter()

_HARVEST_ID = Path(min_length=26, max_length=26, description="ULID of the harvest run.")


class StartHarvestRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    site: str | None = Field(
        default=None, max_length=256, description="Site to harvest. Omit for the whole estate."
    )
    project: str | None = Field(
        default=None, max_length=256, description="Restrict to one project within the site."
    )
    credential: str = Field(
        min_length=3,
        max_length=128,
        description="Reference to the credential, e.g. 'tableau/rqa'. Never a secret: the "
        "service resolves the reference against the vault.",
        examples=["tableau/rqa"],
    )
    concurrency: int = Field(default=DEFAULT_CONCURRENCY, ge=1, le=64)
    parse_quality_threshold: float = Field(
        default=DEFAULT_PARSE_QUALITY_THRESHOLD,
        ge=0.0,
        le=1.0,
        description="Workbooks below this are written but held for review (spec §4.1.4).",
    )
    usage_window_days: int = Field(default=DEFAULT_USAGE_WINDOW_DAYS, ge=1, le=730)


class HarvestAcceptedResponse(BaseModel):
    id: str
    state: str
    scope: dict[str, Any]


class HarvestListResponse(BaseModel):
    harvests: list[dict[str, Any]]


class HarvestFailuresResponse(BaseModel):
    harvest_id: str
    failures: list[dict[str, Any]]
    count: int


def _harvester(request: Request) -> Harvester:
    harvester: Harvester | None = getattr(request.app.state, "harvester", None)
    if harvester is None:
        raise InvalidRequestError(
            "no source adapter is enabled on this deployment, so there is nothing to "
            "harvest. Adapters are configured per tenant (spec §6.3)."
        )
    return harvester


def _store(request: Request) -> HarvestStore:
    store: HarvestStore | None = getattr(request.app.state, "harvest_store", None)
    if store is None:
        raise InvalidRequestError("harvest history is not available on this deployment")
    return store


@router.post(
    "/v1/harvests",
    response_model=HarvestAcceptedResponse,
    status_code=status.HTTP_202_ACCEPTED,
    tags=["harvest"],
    summary="Start a harvest",
)
async def start_harvest(
    body: StartHarvestRequest,
    request: Request,
    principal: PrincipalDep,
    roles: ArtizentDep,
) -> HarvestAcceptedResponse:
    """Start harvesting a scope, and return immediately with the run's id.

    A harvest of a large site runs for hours, so the request accepts the work rather than
    waiting for it; progress is read from ``GET /v1/harvests/{id}``. The run is an
    in-process task for now — durable orchestration is Temporal's, which is E12/F12.1, and
    the run record is already persisted so progress survives a restart even though the
    task does not.
    """
    if body.project and not body.site:
        raise InvalidRequestError("a project can only be harvested within a named site")
    try:
        validate_reference(body.credential)
    except CredentialError as exc:
        raise InvalidRequestError(str(exc)) from exc

    harvester = _harvester(request)
    harvest_id = new_ulid()
    harvest_request = HarvestRequest(
        scope=Scope(site=body.site, project=body.project),
        credential_reference=body.credential,
        concurrency=body.concurrency,
        parse_quality_threshold=body.parse_quality_threshold,
        usage_window_days=body.usage_window_days,
    )

    task = asyncio.create_task(
        harvester.run(harvest_request, principal=principal, harvest_id=harvest_id)
    )
    # Held so the task is not garbage-collected mid-run, and discarded when it finishes.
    running: set[asyncio.Task[Any]] = request.app.state.harvest_tasks
    running.add(task)
    task.add_done_callback(running.discard)

    logger.info(
        "harvest %s accepted for %s by %s",
        harvest_id,
        harvest_request.scope.describe(),
        principal.value,
    )
    return HarvestAcceptedResponse(
        id=harvest_id,
        state="QUEUED",
        scope={"site": body.site, "project": body.project},
    )


@router.get(
    "/v1/harvests",
    response_model=HarvestListResponse,
    tags=["harvest"],
    summary="List harvest runs, most recent first",
)
async def list_harvests(
    request: Request, principal: PrincipalDep, roles: ArtizentDep, limit: int = 25
) -> HarvestListResponse:
    if not 1 <= limit <= 200:
        raise InvalidRequestError(f"limit must be between 1 and 200, got {limit}")
    runs = await _store(request).recent(limit=limit)
    return HarvestListResponse(harvests=[run.as_dict() for run in runs])


@router.get(
    "/v1/harvests/{harvest_id}",
    tags=["harvest"],
    summary="Progress of one harvest, per project",
)
async def get_harvest(
    request: Request,
    principal: PrincipalDep,
    roles: ArtizentDep,
    harvest_id: Annotated[str, _HARVEST_ID],
) -> dict[str, Any]:
    progress = await _store(request).get(harvest_id)
    if progress is None:
        raise ElementNotFoundError(f"no harvest with id '{harvest_id}'")
    reported: dict[str, Any] = progress.as_dict()
    return reported


@router.get(
    "/v1/harvests/{harvest_id}/failures",
    response_model=HarvestFailuresResponse,
    tags=["harvest"],
    summary="Workbooks that failed in a harvest, with the error",
)
async def get_harvest_failures(
    request: Request,
    principal: PrincipalDep,
    roles: ArtizentDep,
    harvest_id: Annotated[str, _HARVEST_ID],
    limit: int = 500,
) -> HarvestFailuresResponse:
    """S1.2.1: failures do not stop the run and are listed with the error."""
    if not 1 <= limit <= 2000:
        raise InvalidRequestError(f"limit must be between 1 and 2000, got {limit}")
    store = _store(request)
    if await store.get(harvest_id) is None:
        raise ElementNotFoundError(f"no harvest with id '{harvest_id}'")
    failures = await store.failures(harvest_id, limit=limit)
    return HarvestFailuresResponse(
        harvest_id=harvest_id,
        failures=[failure.as_dict() for failure in failures],
        count=len(failures),
    )


__all__ = ["router"]
