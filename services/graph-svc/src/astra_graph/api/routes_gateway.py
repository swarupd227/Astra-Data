"""The Model Gateway's own API — story S5.3.2.

    "Routing is by task class and tenant policy; both configured providers pass the
    Transpiler eval set at >= 0.80 first-pass proof before being routable for
    transpile_c3."

Reading the routing policy is open to any Artizent role, the same posture every other
gateway-adjacent read in this API already has (provenance, class mix); running an eval set
against a real, configured provider is the platform engineer's (`PlatformEngineerDep`), the
persona this story's own acceptance criteria names — and the one action here that makes a
real, billed call to an external model, not something a read-only role should trigger.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Query, Request
from pydantic import BaseModel

from ..errors import ElementNotFoundError, InvalidRequestError
from ..gateway import TRANSPILE_C3, ModelGateway
from ..generation import run_transpile_c3_eval
from .deps import ArtizentDep, PlatformEngineerDep, PrincipalDep

router = APIRouter()


def _gateway(request: Request) -> ModelGateway:
    gateway: ModelGateway | None = getattr(request.app.state, "gateway", None)
    if gateway is None:
        raise InvalidRequestError("the Model Gateway is not available on this deployment")
    return gateway


class RunEvalBody(BaseModel):
    provider: str


@router.get(
    "/v1/model-gateway:policy",
    tags=["gateway"],
    summary="The tenant policy for one task class: every configured provider's last eval score and whether it is routable",
)
async def get_policy(
    request: Request, principal: PrincipalDep, roles: ArtizentDep,
    task_class: str = Query(default=TRANSPILE_C3),
) -> dict[str, Any]:
    gateway = _gateway(request)
    entries = await gateway.policy_store.policy_for(task_class)
    return {"task_class": task_class, "policy": [e.as_dict() for e in entries]}


@router.post(
    "/v1/model-gateway:run-eval",
    tags=["gateway"],
    summary="Run the transpile_c3 eval set against one real, configured provider and record the verdict",
)
async def run_eval(
    request: Request, principal: PrincipalDep, roles: PlatformEngineerDep, body: RunEvalBody
) -> dict[str, Any]:
    gateway = _gateway(request)
    caller = gateway.providers.get(body.provider)
    if caller is None:
        raise ElementNotFoundError(
            f"no provider '{body.provider}' is registered on this gateway "
            f"(configured: {', '.join(sorted(gateway.providers)) or 'none'})"
        )
    report = await run_transpile_c3_eval(caller)
    await gateway.policy_store.record_eval(
        task_class=TRANSPILE_C3, report=report, updated_by=principal.value
    )
    return report.as_dict()


__all__ = ["router"]
