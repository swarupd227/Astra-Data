"""C4 redesign decisions — story S5.4.1.

    "The MU is BLOCKED until a Migration Engineer records the redesign decision (implement
    as suggested / alternative / drop with report-owner agreement). Decisions are visible
    to the report owner and referenced at G3."

Recording a decision is the migration engineer's (`MigrationEngineerDep`, the persona this
story's own acceptance criteria names); reading the estate's C4 redesign state is open to
any Artizent role or the report owner specifically (`C4RedesignReaderDep`) — `redesign.py`'s
own module docstring explains why that is deliberately narrower than every client role.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, Path, Request
from pydantic import BaseModel, ConfigDict, Field

from ..classify import ClassificationEngine
from ..errors import ElementNotFoundError, InvalidRequestError
from ..graph.queries import NODE_INDEX_TABLE
from ..lineage import hydrate
from ..redesign import C4_PROPERTIES, RedesignDecisionError, validate_decision
from .deps import C4RedesignReaderDep, MigrationEngineerDep, PrincipalDep

router = APIRouter()

_CALC_ID = Path(min_length=5, max_length=64, description="ULID of the CalculatedField.")


def _engine(request: Request) -> ClassificationEngine:
    engine: ClassificationEngine | None = getattr(request.app.state, "classifier", None)
    if engine is None:
        raise InvalidRequestError("calculation classification is not available on this deployment")
    return engine


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _redesign_view(calc_id: str, properties: dict[str, Any]) -> dict[str, Any]:
    view: dict[str, Any] = {
        "calculated_field_id": calc_id,
        "name": properties.get("name"),
        "reason": properties.get("reason"),
    }
    for key in C4_PROPERTIES:
        if key in properties:
            view[key] = properties[key]
    return view


class RedesignDecisionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    decision: str = Field(min_length=1, max_length=32)
    reason: str = Field(min_length=1, max_length=4000)


@router.post(
    "/v1/calculations/{calc_id}:redesign-decision",
    tags=["classification"],
    summary="Record a C4 redesign decision — implement as suggested / alternative / drop (§3.2)",
)
async def record_redesign_decision(
    body: RedesignDecisionRequest,
    request: Request,
    principal: PrincipalDep,
    roles: MigrationEngineerDep,
    calc_id: str = _CALC_ID,
) -> dict[str, Any]:
    engine = _engine(request)
    async with engine.pool.acquire() as conn:
        properties = (await hydrate(conn, engine.graph_name, "CalculatedField", [calc_id])).get(calc_id)
    if properties is None:
        raise ElementNotFoundError(f"no CalculatedField with id '{calc_id}'")
    if properties.get("class") != "C4":
        raise InvalidRequestError(
            f"CalculatedField '{calc_id}' is class {properties.get('class')!r}, not C4 — a "
            f"redesign decision only applies to a construct the Transpiler flagged as C4"
        )

    try:
        validate_decision(body.decision, reason=body.reason)
    except RedesignDecisionError as exc:
        raise InvalidRequestError(str(exc)) from exc

    updated = await engine.writer.set_node_properties(
        calc_id,
        {
            "redesign_decision": body.decision,
            "redesign_decision_reason": body.reason,
            "redesign_decision_by": principal.value,
            "redesign_decision_at": _now(),
        },
        principal=principal,
    )
    return _redesign_view(calc_id, updated["properties"])


@router.get(
    "/v1/calculations:c4-redesigns",
    tags=["classification"],
    summary="Every C4 CalculatedField's guidance, suggestion and decision state (§3.2, §9.1)",
)
async def list_c4_redesigns(request: Request, principal: PrincipalDep, roles: C4RedesignReaderDep) -> dict[str, Any]:
    engine = _engine(request)
    async with engine.pool.acquire() as conn:
        rows = await conn.fetch(
            f"""SELECT id FROM {NODE_INDEX_TABLE}
             WHERE graph = $1 AND kind = 'node' AND label = 'CalculatedField' AND retired_at IS NULL""",
            engine.graph_name,
        )
        properties = await hydrate(conn, engine.graph_name, "CalculatedField", [row["id"] for row in rows])

    redesigns = [
        _redesign_view(calc_id, props) for calc_id, props in properties.items() if props.get("class") == "C4"
    ]
    blocked = sum(1 for r in redesigns if "redesign_decision" not in r)
    return {"redesigns": redesigns, "count": len(redesigns), "blocked_count": blocked}


__all__ = ["router"]
