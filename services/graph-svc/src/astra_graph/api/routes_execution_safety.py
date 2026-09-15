"""Execution safety's own policy -- story S11.2.1, opens F11.2.

    "Production execution is limited to the regression runner with the same read-only
    principal."

Read is the same "Artizent, or the InfoSec reviewer" shape `TenantAccessReaderDep`
already sets (this policy is exactly the kind of governance fact §15.1's own InfoSec
remit already covers); edit is `PlatformEngineerDep`, matching S11.1.2's own SVID-revoke
precedent for a platform-safety action with no other named approver.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Request
from pydantic import BaseModel, ConfigDict, Field

from ..errors import InvalidRequestError
from ..execution_safety import ExecutionSafetyPolicy, ExecutionSafetyPolicyStore
from .deps import PlatformEngineerDep, PrincipalDep, TenantAccessReaderDep

router = APIRouter()


class SetProductionWorkspacesRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    production_workspaces: list[str] = Field(default_factory=list, max_length=100)


def _policy_store(request: Request) -> ExecutionSafetyPolicyStore:
    store: ExecutionSafetyPolicyStore | None = getattr(
        request.app.state, "execution_safety_policy_store", None
    )
    if store is None:
        raise InvalidRequestError("execution safety policy is not available on this deployment")
    return store


@router.get(
    "/v1/execution-safety/policy",
    tags=["execution-safety"],
    summary="Which workspaces this tenant calls production (spec §18.2)",
)
async def get_execution_safety_policy(
    request: Request, principal: PrincipalDep, roles: TenantAccessReaderDep
) -> dict[str, Any]:
    policy = await _policy_store(request).latest()
    return policy.as_dict()


@router.put(
    "/v1/execution-safety/policy",
    tags=["execution-safety"],
    summary="Record which workspaces this tenant calls production -- the platform engineer's own action",
)
async def set_execution_safety_policy(
    body: SetProductionWorkspacesRequest,
    request: Request,
    principal: PrincipalDep,
    roles: PlatformEngineerDep,
) -> dict[str, Any]:
    policy = ExecutionSafetyPolicy(production_workspaces=frozenset(body.production_workspaces))
    saved = await _policy_store(request).save(policy, updated_by=principal.value)
    return saved.as_dict()


__all__ = ["router"]
