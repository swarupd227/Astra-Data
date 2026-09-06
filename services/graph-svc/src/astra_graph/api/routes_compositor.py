"""The Compositor's own API -- stories S6.1.1 and S6.1.2.

    "Mapping table from Appendix B ... is data, versioned, and editable by the architect."
    "I want the report definition committed to Git and deployed to the dev workspace bound
    to the family model, so that I can open the generated report in Fabric within minutes
    of generation."

Reading the visual-mapping ruleset is open to any Artizent role, the identical posture
`routes_conformance.py` already set for the same shape of table; saving a new version is
the architect's (`MigrationArchitectDep`), spec §2.4: "Owns target architecture and
conformance rules" -- a visual-type mapping is target architecture by the same reading.
Composing and deploying a workbook are both the migration engineer's
(`MigrationEngineerDep`), the persona both stories' own acceptance criteria name. Reading a
workbook's current report or deploy log back is open to any Artizent role, the same posture
every other "read what a prior action produced" route in this API has.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Path, Request
from pydantic import BaseModel, ConfigDict, Field

from ..compositor import Compositor, CompositorError
from ..errors import ElementNotFoundError, InvalidRequestError
from ..pbir import VISUAL_TYPE_WHITELIST
from ..report_deploy import ReportDeployError, ReportDeployStore, deploy_report
from ..visual_mapping import MappingRule, VisualMappingRulesetStore
from .deps import ArtizentDep, MigrationArchitectDep, MigrationEngineerDep, PrincipalDep

router = APIRouter()

_WORKBOOK_ID = Path(min_length=5, max_length=64, description="ULID of the Workbook.")


def _compositor(request: Request) -> Compositor:
    compositor: Compositor | None = getattr(request.app.state, "compositor", None)
    if compositor is None:
        raise InvalidRequestError("the Compositor is not available on this deployment")
    return compositor


def _mapping_store(request: Request) -> VisualMappingRulesetStore:
    store: VisualMappingRulesetStore | None = getattr(request.app.state, "visual_mapping_store", None)
    if store is None:
        raise InvalidRequestError("the visual-mapping ruleset is not available on this deployment")
    return store


def _deploy_store(request: Request) -> ReportDeployStore:
    store: ReportDeployStore | None = getattr(request.app.state, "report_deploy_store", None)
    if store is None:
        raise InvalidRequestError("report deployment is not available on this deployment")
    return store


@router.get(
    "/v1/compositor/visual-mappings",
    tags=["compositor"],
    summary="The latest visual-mapping ruleset a compose is checked against (Appendix B.2)",
)
async def get_visual_mappings(request: Request, principal: PrincipalDep, roles: ArtizentDep) -> dict[str, Any]:
    ruleset = await _mapping_store(request).latest()
    return {"ruleset": ruleset.as_dict()}


class MappingRuleRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    mark_type: str = Field(min_length=1, max_length=64)
    target_visual_type: str | None = Field(default=None, max_length=100)
    redesign_reason: str | None = Field(default=None, max_length=2000)
    notes: str = Field(default="", max_length=2000)


class SaveVisualMappingsRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    rules: list[MappingRuleRequest] = Field(min_length=1)


@router.post(
    "/v1/compositor/visual-mappings",
    tags=["compositor"],
    summary="Save a new version of the visual-mapping ruleset -- the architect's (§2.4)",
)
async def save_visual_mappings(
    body: SaveVisualMappingsRequest,
    request: Request,
    principal: PrincipalDep,
    roles: MigrationArchitectDep,
) -> dict[str, Any]:
    seen: set[str] = set()
    rules: list[MappingRule] = []
    for item in body.rules:
        mark_type = item.mark_type.strip().lower()
        if mark_type in seen:
            raise InvalidRequestError(f"duplicate mark type '{mark_type}' in the submitted ruleset")
        seen.add(mark_type)
        if item.target_visual_type and item.target_visual_type not in VISUAL_TYPE_WHITELIST:
            raise InvalidRequestError(
                f"'{item.target_visual_type}' is not a whitelisted Power BI visual type "
                f"(pbir.VISUAL_TYPE_WHITELIST) -- a compose would only fail on it later"
            )
        try:
            rules.append(
                MappingRule(
                    mark_type=mark_type,
                    target_visual_type=item.target_visual_type,
                    redesign_reason=item.redesign_reason,
                    notes=item.notes,
                )
            )
        except ValueError as exc:
            raise InvalidRequestError(str(exc)) from exc

    ruleset = await _mapping_store(request).save(rules, updated_by=principal.value)
    return {"ruleset": ruleset.as_dict()}


@router.post(
    "/v1/workbooks/{workbook_id}:compose",
    tags=["compositor"],
    summary="Compose a workbook's Tableau sheets into a PBIR report definition (§8.8)",
)
async def compose_workbook(
    request: Request,
    principal: PrincipalDep,
    roles: MigrationEngineerDep,
    workbook_id: str = _WORKBOOK_ID,
) -> dict[str, Any]:
    ruleset = await _mapping_store(request).latest()
    try:
        return await _compositor(request).compose(workbook_id, ruleset=ruleset, principal=principal)
    except (ElementNotFoundError, CompositorError) as exc:
        raise InvalidRequestError(str(exc)) from exc


@router.get(
    "/v1/workbooks/{workbook_id}:report",
    tags=["compositor"],
    summary="A workbook's current report definition and visuals, as last composed (§8.8)",
)
async def get_workbook_report(
    request: Request, principal: PrincipalDep, roles: ArtizentDep, workbook_id: str = _WORKBOOK_ID
) -> dict[str, Any]:
    report = await _compositor(request).read(workbook_id)
    if report is None:
        raise ElementNotFoundError(f"workbook '{workbook_id}' has not been composed into a report yet")
    return report


# --------------------------------------------------------------------------- story S6.1.2


@router.post(
    "/v1/workbooks/{workbook_id}:deploy",
    tags=["compositor"],
    summary="Commit and deploy a workbook's own current report to the dev workspace (§7.1/§7.2)",
)
async def deploy_workbook(
    request: Request,
    principal: PrincipalDep,
    roles: MigrationEngineerDep,
    workbook_id: str = _WORKBOOK_ID,
) -> dict[str, Any]:
    target_adapter = getattr(request.app.state, "target_adapter", None)
    if target_adapter is None:
        raise InvalidRequestError("report deployment is not available on this deployment")
    workspace = getattr(request.app.state, "target_workspace", "dev")
    engine = _compositor(request)
    try:
        record = await deploy_report(
            engine.pool, engine.graph_name, engine.writer, target_adapter, _deploy_store(request),
            workbook_id=workbook_id, workspace=workspace, principal=principal,
        )
    except (ElementNotFoundError, ReportDeployError) as exc:
        raise InvalidRequestError(str(exc)) from exc
    return record.as_dict()


@router.get(
    "/v1/workbooks/{workbook_id}/deploy",
    tags=["compositor"],
    summary="The most recent deploy attempt for one workbook's own report (§15.3.3)",
)
async def get_workbook_deploy(
    request: Request, principal: PrincipalDep, roles: ArtizentDep, workbook_id: str = _WORKBOOK_ID
) -> dict[str, Any]:
    record = await _deploy_store(request).latest(workbook_id)
    return {"workbook_id": workbook_id, "deploy": record.as_dict() if record else None}


__all__ = ["router"]
