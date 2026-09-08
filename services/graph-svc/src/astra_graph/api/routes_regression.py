"""§10.6 regression mode, over HTTP -- story S7.7.1, closing F7.7 and E7.

Scheduling a regression is the AC's own persona ("As a programme manager, I want ...
schedules re-runs") -- gated on `ProgrammeManagerDep`, not the Parity Engineer roles the
rest of F7 uses, since this is a programme-level commitment (how often a released report
gets re-checked), not a parity-suite authoring action. The Regression Monitor read
reuses `ParityDashboardReaderDep` -- the identical "any Artizent role, or the report
owner" shape the Parity Dashboard (S7.4.2) already set, since this screen is the same
audience looking at the same workbooks from a different angle (schedule and drift,
rather than pass/fail counts). Exporting a handover bundle is `ArtizentDep`-only: the
identical "Artizent generates and hands over" shape `GET /v1/artefacts/{id}/content`
already has (see `routes_artefacts.py`'s own docstring) -- the export artefact itself is
served by that same existing route once stored, not a second content-serving endpoint.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Path, Request, status
from pydantic import BaseModel, ConfigDict, Field, model_validator

from ..artefacts import ArtefactStore
from ..errors import InvalidRequestError
from ..harvest.schedule import MAX_INTERVAL_MINUTES, MIN_INTERVAL_MINUTES, Cadence, ScheduleError
from ..regression import (
    RegressionError,
    RegressionScheduleStore,
    RegressionService,
    new_regression_schedule,
)
from ..regression_export import EXPORT_KIND, EXPORT_MEDIA_TYPE
from .deps import ArtizentDep, ParityDashboardReaderDep, PrincipalDep, ProgrammeManagerDep

router = APIRouter()

_WORKBOOK_ID = Path(min_length=5, max_length=64, description="ULID of the Workbook (MU identity).")


class CadenceModel(BaseModel):
    model_config = ConfigDict(extra="forbid")

    every_minutes: int | None = Field(default=None, ge=MIN_INTERVAL_MINUTES, le=MAX_INTERVAL_MINUTES)
    daily_at: str | None = Field(default=None, examples=["02:00"])

    @model_validator(mode="after")
    def exactly_one(self) -> CadenceModel:
        given = [f for f in (self.every_minutes, self.daily_at) if f is not None]
        if len(given) != 1:
            raise ValueError("a cadence is either 'every_minutes' or 'daily_at', and exactly one of them")
        return self


class ScheduleRegressionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    cadence: CadenceModel | None = Field(
        default=None, description="Defaults to weekly (§10.6's own literal default)."
    )
    workspace: str = Field(default="dev", min_length=1, max_length=100)


def _regression_store(request: Request) -> RegressionScheduleStore:
    store: RegressionScheduleStore | None = getattr(request.app.state, "regression_schedule_store", None)
    if store is None:
        raise InvalidRequestError("regression scheduling is not available on this deployment")
    return store


def _artefact_store(request: Request) -> ArtefactStore:
    store: ArtefactStore | None = getattr(request.app.state, "artefact_store", None)
    if store is None:  # pragma: no cover - set in every wiring path
        raise InvalidRequestError("the artefact store is not available on this deployment")
    return store


def _service(request: Request) -> RegressionService:
    service: RegressionService | None = getattr(request.app.state, "regression_service", None)
    if service is None:
        raise InvalidRequestError("regression monitoring and export are not available on this deployment")
    return service


@router.post(
    "/v1/workbooks/{workbook_id}:schedule-regression",
    status_code=status.HTTP_201_CREATED,
    tags=["regression"],
    summary="Enrol a workbook's own retained parity suite in scheduled regression (§10.6)",
)
async def schedule_regression(
    body: ScheduleRegressionRequest,
    request: Request,
    principal: PrincipalDep,
    roles: ProgrammeManagerDep,
    workbook_id: str = _WORKBOOK_ID,
) -> dict[str, Any]:
    """Create a schedule. Like a harvest schedule, it does not run now -- the first
    firing is one cadence away."""
    try:
        cadence = Cadence(every_minutes=body.cadence.every_minutes, daily_at=body.cadence.daily_at) if body.cadence else None
    except ScheduleError as exc:
        raise InvalidRequestError(str(exc)) from exc

    schedule = new_regression_schedule(
        workbook_id=workbook_id, cadence=cadence, workspace=body.workspace, created_by=principal.value,
    )
    try:
        created = await _regression_store(request).create(schedule)
    except RegressionError as exc:
        raise InvalidRequestError(str(exc)) from exc
    return created.as_dict()


@router.get(
    "/v1/regression-monitor",
    tags=["regression"],
    summary="Every released workbook, its own regression schedule, last result and drift alerts (§10.6)",
)
async def get_regression_monitor(
    request: Request, principal: PrincipalDep, roles: ParityDashboardReaderDep,
) -> dict[str, Any]:
    rows = await _service(request).monitor()
    return {"workbooks": rows, "count": len(rows)}


@router.post(
    "/v1/workbooks/{workbook_id}:export-regression-suite",
    status_code=status.HTTP_201_CREATED,
    tags=["regression"],
    summary="Export this workbook's own retained parity suite and a standalone runner for handover (§10.6)",
)
async def export_regression_suite(
    request: Request,
    principal: PrincipalDep,
    roles: ArtizentDep,
    workbook_id: str = _WORKBOOK_ID,
) -> dict[str, Any]:
    """Builds the zip and stores it as a real artefact -- fetch its bytes from
    `GET /v1/artefacts/{id}/content`, the identical existing route every other stored
    artefact in this platform is already served from."""
    try:
        content = await _service(request).export(workbook_id)
    except LookupError as exc:
        raise InvalidRequestError(str(exc)) from exc

    record = await _artefact_store(request).store(
        kind=EXPORT_KIND, mu_ref=workbook_id, case_id="", content=content,
        media_type=EXPORT_MEDIA_TYPE, created_by=principal.value,
    )
    return record.as_dict()


__all__ = ["router"]
