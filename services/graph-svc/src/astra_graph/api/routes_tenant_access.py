"""Tenant & Access — story S11.1.2, opens F11.1.

    "Identity issuance, rotation and revocation are visible in Tenant & Access."

§15.3.7 names a much broader Admin screen under this title (roles, users/Entra groups,
site/domain scoping, service principals, secrets references — "Assign role; scope;
rotate"). This story builds only what it makes real: the declared `AgentRecord` catalog
(`agent_identity.py`) and the real SVID issuance/rotation/revocation trail
(`workload_identity.py`) — the rest of §15.3.7's own scope stays undeclared here, the
identical "the closest real thing, not the whole named screen" precedent `App.tsx`'s own
docstring already sets for §15.1's landing-page gaps (ADR 0071).
"""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Path, Query, Request
from pydantic import BaseModel, ConfigDict, Field

from ..agent_identity import AGENT_CATALOG
from ..errors import InvalidRequestError
from ..workload_identity import SvidStore, WorkloadIdentityRequestError
from .deps import PlatformEngineerDep, PrincipalDep, TenantAccessReaderDep

router = APIRouter()

_JTI = Path(min_length=1, max_length=64, description="The SVID's own jti claim.")


class RevokeSvidRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    reason: str = Field(min_length=1, max_length=2000)


def _svid_store(request: Request) -> SvidStore:
    store: SvidStore | None = getattr(request.app.state, "svid_store", None)
    if store is None:
        raise InvalidRequestError("workload identity is not available on this deployment")
    return store


@router.get(
    "/v1/tenant-access/agent-records",
    tags=["tenant-access"],
    summary="This platform's own declared agent catalog (spec §8.1)",
)
async def list_agent_records(principal: PrincipalDep, roles: TenantAccessReaderDep) -> dict[str, Any]:
    return {"agents": [record.as_dict() for record in AGENT_CATALOG.values()]}


@router.get(
    "/v1/tenant-access/svids",
    tags=["tenant-access"],
    summary="SVID issuance, rotation and revocation records",
)
async def list_svids(
    request: Request,
    principal: PrincipalDep,
    roles: TenantAccessReaderDep,
    agent_id: Annotated[str | None, Query(max_length=64)] = None,
    limit: Annotated[int, Query(ge=1, le=500)] = 100,
) -> dict[str, Any]:
    records = await _svid_store(request).list_records(agent_id=agent_id, limit=limit)
    return {"svids": [record.as_dict() for record in records]}


@router.post(
    "/v1/tenant-access/svids/{jti}:revoke",
    tags=["tenant-access"],
    summary="Revoke an issued SVID -- the platform engineer's own action",
)
async def revoke_svid(
    body: RevokeSvidRequest,
    request: Request,
    principal: PrincipalDep,
    roles: PlatformEngineerDep,
    jti: Annotated[str, _JTI],
) -> dict[str, Any]:
    try:
        record = await _svid_store(request).revoke(jti, reason=body.reason, revoked_by=principal.value)
    except WorkloadIdentityRequestError as exc:
        raise InvalidRequestError(str(exc)) from exc
    return record.as_dict()


__all__ = ["router"]
