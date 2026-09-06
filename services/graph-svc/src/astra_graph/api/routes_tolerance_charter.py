"""The Tolerance Charter's own API -- story S7.1.1, opening E7/F7.1.

    "The Tolerance Charter as a versioned document the platform enforces... editor in
    the console with inline explanation of each rule's effect; 'simulate' re-diffs the
    last run under the edited charter without executing."

Reading (and simulating) is open to any Artizent role, or the client analytics lead
specifically (`ToleranceCharterReaderDep`) -- a live gap found by driving the console
against this route for real: the client analytics lead approves a charter at G1 and must
be able to read it first, and `ArtizentDep` alone would refuse a client-side role
entirely, the identical reasoning `C4RedesignReaderDep` already established for the
report owner. Saving a new version is the Parity Engineer's alone (§2.4: "Owns the
Tolerance Charter") -- `save_charter` itself enforces the AC's own "changing the charter
after G1 requires... the client analytics lead" by requiring two extra fields in the same
call once a G1 decision already exists. Approving a version at G1 for the first time is
the client analytics lead's own act, countersigned by the Parity Engineer -- the identical
`approver`/`countersigner` shape `g2.py`'s own G2 approval already uses.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Path, Request
from pydantic import BaseModel, ConfigDict, Field

from ..errors import ElementNotFoundError, InvalidRequestError
from ..tolerance_charter import (
    CHARTER_FIELD_METADATA,
    ToleranceCharter,
    ToleranceCharterError,
    ToleranceCharterService,
)
from .deps import ClientAnalyticsLeadDep, ParityEngineerDep, PrincipalDep, ToleranceCharterReaderDep

router = APIRouter()

_VERSION = Path(ge=0, description="Charter version number.")
_WORKBOOK_ID = Path(min_length=5, max_length=64, description="ULID of the Workbook (MU identity).")


def _service(request: Request) -> ToleranceCharterService:
    service: ToleranceCharterService | None = getattr(request.app.state, "tolerance_charter", None)
    if service is None:
        raise InvalidRequestError("the Tolerance Charter is not available on this deployment")
    return service


class NumericRuleRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    abs_epsilon: float = Field(ge=0)
    rel_epsilon: float = Field(ge=0)
    rounding: str = Field(min_length=1, max_length=32)
    currency_scale: int = Field(ge=0, le=10)


class NullRuleRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source_null_vs_target_zero: str = Field(pattern="^(PASS|FAIL)$")
    source_null_vs_target_blank: str = Field(pattern="^(PASS|FAIL)$")
    empty_string_is_null: bool


class DateRuleRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    grain_alignment: str = Field(min_length=1, max_length=64)
    timezone: str = Field(min_length=1, max_length=64)
    fiscal_year_start: int = Field(ge=1, le=12)


class StringRuleRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    trim: bool
    case_sensitive: bool
    collation: str = Field(min_length=1, max_length=32)


class OrderingRuleRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    sort_sensitive: bool
    top_n_tie_break: str = Field(min_length=1, max_length=32)


class RowRuleRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    missing_key: str = Field(pattern="^(PASS|FAIL)$")
    extra_key: str = Field(pattern="^(PASS|FAIL)$")
    row_count_tolerance: int = Field(ge=0)


class SamplingRuleRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    full_compare_max_rows: int = Field(ge=1)
    sample_rows: int = Field(ge=1)
    stratify_by: str = Field(min_length=1, max_length=64)


class ParamRuleRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    enumerate_max_values: int = Field(ge=1)
    enumerate_strategy: str = Field(min_length=1, max_length=64)


class WaiverRuleRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    allowed_classes: list[str]
    requires: list[str]
    justification_min_chars: int = Field(ge=0)


class ToleranceCharterRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    numeric: NumericRuleRequest
    nulls: NullRuleRequest
    dates: DateRuleRequest
    strings: StringRuleRequest
    ordering: OrderingRuleRequest
    rows: RowRuleRequest
    sampling: SamplingRuleRequest
    params: ParamRuleRequest
    waiver: WaiverRuleRequest

    def to_charter(self) -> ToleranceCharter:
        return ToleranceCharter.from_dict(self.model_dump())


@router.get(
    "/v1/tolerance-charter",
    tags=["tolerance-charter"],
    summary="The latest Tolerance Charter version, and what each rule means (§4.4)",
)
async def get_charter(request: Request, principal: PrincipalDep, roles: ToleranceCharterReaderDep) -> dict[str, Any]:
    version = await _service(request).latest()
    return {"charter": version.as_dict(), "field_metadata": CHARTER_FIELD_METADATA}


@router.get(
    "/v1/tolerance-charter/{version}",
    tags=["tolerance-charter"],
    summary="One historical, immutable Tolerance Charter version",
)
async def get_charter_version(
    request: Request, principal: PrincipalDep, roles: ToleranceCharterReaderDep, version: int = _VERSION
) -> dict[str, Any]:
    found = await _service(request).get(version)
    if found is None:
        raise ElementNotFoundError(f"no Tolerance Charter version {version}")
    return {"charter": found.as_dict(), "field_metadata": CHARTER_FIELD_METADATA}


class SaveCharterRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    charter: ToleranceCharterRequest
    client_analytics_lead_ack: str | None = Field(default=None, max_length=200)
    reason: str | None = Field(default=None, max_length=2000)


@router.post(
    "/v1/tolerance-charter",
    tags=["tolerance-charter"],
    summary="Save a new, immutable Tolerance Charter version -- the Parity Engineer's (§2.4)",
)
async def save_charter_route(
    body: SaveCharterRequest,
    request: Request,
    principal: PrincipalDep,
    roles: ParityEngineerDep,
) -> dict[str, Any]:
    try:
        return await _service(request).save(
            body.charter.to_charter(), principal=principal,
            client_analytics_lead_ack=body.client_analytics_lead_ack, reason=body.reason,
        )
    except ToleranceCharterError as exc:
        raise InvalidRequestError(str(exc)) from exc


class ApproveG1Request(BaseModel):
    model_config = ConfigDict(extra="forbid")

    countersigned_by: str = Field(min_length=1, max_length=200)
    rationale: str = Field(min_length=1, max_length=2000)


@router.post(
    "/v1/tolerance-charter/{version}:approve-g1",
    tags=["tolerance-charter"],
    summary="Approve a charter version at G1 -- the client analytics lead's, countersigned by the Parity Engineer",
)
async def approve_g1_route(
    body: ApproveG1Request,
    request: Request,
    principal: PrincipalDep,
    roles: ClientAnalyticsLeadDep,
    version: int = _VERSION,
) -> dict[str, Any]:
    service = _service(request)
    found = await service.get(version)
    if found is None:
        raise ElementNotFoundError(f"no Tolerance Charter version {version}")
    try:
        return await service.approve_g1(
            version=version, principal=principal,
            countersigned_by=body.countersigned_by, rationale=body.rationale,
        )
    except ToleranceCharterError as exc:
        raise InvalidRequestError(str(exc)) from exc


class SimulateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    charter: ToleranceCharterRequest


@router.post(
    "/v1/workbooks/{workbook_id}/tolerance-charter:simulate",
    tags=["tolerance-charter"],
    summary="Re-diff this workbook's last run under an edited charter, without executing",
)
async def simulate_route(
    body: SimulateRequest,
    request: Request,
    principal: PrincipalDep,
    roles: ToleranceCharterReaderDep,
    workbook_id: str = _WORKBOOK_ID,
) -> dict[str, Any]:
    return await _service(request).simulate(workbook_id=workbook_id, charter=body.charter.to_charter())


__all__ = ["router"]
