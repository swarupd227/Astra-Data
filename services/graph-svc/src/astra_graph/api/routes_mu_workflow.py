"""Each MU is a real Temporal workflow -- story S12.1.1, opening E12/F12.1. See
`mu_workflow.py`'s own module docstring for the full design; this is the HTTP surface
a platform engineer (this story's own literal persona) uses to start one, and the one
any Artizent role uses to check on or advance one.

Starting a workflow and sending a gate decision are the platform engineer's own real
actions (`PlatformEngineerDep`) -- the identical "gated narrower than the read" posture
`routes_gateway.py`'s own eval-trigger routes already take. Reading a workflow's own
current state is open to any Artizent role (`ArtizentDep`), matching every other
read-only Programme-Board-adjacent route in this codebase.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Request
from pydantic import BaseModel, ConfigDict, Field
from temporalio.client import Client, WorkflowHandle
from temporalio.service import RPCError

from ..errors import InvalidRequestError
from ..mu_worker import TASK_QUEUE
from ..mu_workflow import (
    GateDecisionSignal,
    MigrationUnitWorkflow,
    MigrationUnitWorkflowInput,
)
from .deps import ArtizentDep, PlatformEngineerDep, PrincipalDep, RepositoryDep

router = APIRouter()


def workflow_id_for(workbook_id: str) -> str:
    """A deterministic, human-readable workflow id -- Temporal best practice, and what
    lets every route below compute the id directly from `workbook_id` rather than
    needing a second, separately-persisted workbook-id-to-workflow-id lookup table."""
    return f"mu-{workbook_id}"


async def _temporal_client(request: Request) -> Client:
    client: Client | None = getattr(request.app.state, "temporal_client", None)
    if client is None:
        raise InvalidRequestError("Temporal is not available on this deployment")
    return client


async def _handle(request: Request, workbook_id: str) -> WorkflowHandle[Any, Any]:
    client = await _temporal_client(request)
    return client.get_workflow_handle(workflow_id_for(workbook_id))


class StartMuWorkflowRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    calc_ids: list[str] = Field(min_length=1)
    workspace: str = "dev"


class SubmitGateDecisionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    gate: str = Field(min_length=1, max_length=8)
    decision: str = Field(min_length=1, max_length=32)


@router.post(
    "/v1/mu/{workbook_id}:start-workflow",
    tags=["mu-workflow"],
    summary="Start this Migration Unit's own real, durable Temporal workflow (story S12.1.1)",
)
async def post_start_mu_workflow(
    workbook_id: str,
    body: StartMuWorkflowRequest,
    request: Request,
    principal: PrincipalDep,
    roles: PlatformEngineerDep,
    repository: RepositoryDep,
) -> dict[str, Any]:
    client = await _temporal_client(request)
    handle = await client.start_workflow(
        MigrationUnitWorkflow.run,
        MigrationUnitWorkflowInput(
            workbook_id=workbook_id, calc_ids=tuple(body.calc_ids),
            principal=principal.value, workspace=body.workspace,
        ),
        id=workflow_id_for(workbook_id),
        task_queue=TASK_QUEUE,
    )
    return {"workflow_id": handle.id, "run_id": handle.result_run_id}


@router.post(
    "/v1/mu/{workbook_id}:submit-gate-decision",
    tags=["mu-workflow"],
    summary="Signal a real, durable gate wait -- the AC's own literal \"gate waits are durable signals\"",
)
async def post_submit_gate_decision(
    workbook_id: str,
    body: SubmitGateDecisionRequest,
    request: Request,
    principal: PrincipalDep,
    roles: PlatformEngineerDep,
) -> dict[str, str]:
    handle = await _handle(request, workbook_id)
    try:
        await handle.signal(
            MigrationUnitWorkflow.submit_gate_decision,
            GateDecisionSignal(gate=body.gate, decision=body.decision, principal=principal.value),
        )
    except RPCError as exc:
        raise InvalidRequestError(f"no running workflow for workbook '{workbook_id}': {exc}") from exc
    return {"workbook_id": workbook_id, "gate": body.gate, "decision": body.decision}


@router.get(
    "/v1/mu/{workbook_id}/workflow",
    tags=["mu-workflow"],
    summary="This Migration Unit's own real, live workflow status",
)
async def get_mu_workflow(
    workbook_id: str, request: Request, principal: PrincipalDep, roles: ArtizentDep,
) -> dict[str, Any]:
    handle = await _handle(request, workbook_id)
    try:
        description = await handle.describe()
        current_state = await handle.query(MigrationUnitWorkflow.current_state)
    except RPCError as exc:
        raise InvalidRequestError(f"no workflow found for workbook '{workbook_id}': {exc}") from exc
    return {
        "workbook_id": workbook_id,
        "workflow_id": handle.id,
        "run_id": description.run_id,
        "status": description.status.name if description.status else None,
        "mu_state": current_state,
    }


__all__ = ["router", "workflow_id_for"]
