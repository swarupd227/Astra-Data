"""The Pattern Library's own API — stories S5.5.1, S5.5.2 and S5.5.3.

    "Promotion CANDIDATE -> ACTIVE requires N distinct proof passes (default 5), zero
    failures, and a Platform Engineer approval (MA-11, L2)."

    "Actions: promote, retire with reason, edit guards (creates a new version), export."

Reading the library (what exists, and whether a candidate is eligible) is open to any
Artizent role, the same posture every other Programme Board-adjacent read in this API
already has; every governing action — promoting, retiring, editing guards — is the
platform engineer's (`PlatformEngineerDep`), the persona S5.5.3's own acceptance criteria
names for "governing" the library, the same role S5.2.1's apply-rules route and S5.3.2's
eval-run route already drive. "Export" needs no route of its own: the console screen
downloads the same `GET /v1/patterns` response it already rendered.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Path, Query, Request
from pydantic import BaseModel, ConfigDict, Field

from ..errors import InvalidRequestError
from ..generation import GenerationEngine
from ..patterns import (
    PROMOTION_THRESHOLD_DEFAULT,
    PatternPromotionError,
    PatternRetirementError,
    edit_guards,
    list_patterns,
    promote_pattern,
    promotion_status,
    retire_pattern,
)
from .deps import ArtizentDep, PlatformEngineerDep, PrincipalDep

router = APIRouter()

_PATTERN_ID = Path(min_length=5, max_length=64, description="ULID of the Pattern.")
_THRESHOLD = Query(
    default=PROMOTION_THRESHOLD_DEFAULT, ge=1, le=1000,
    description="N distinct proof passes required for promotion (the AC's own default: 5).",
)


def _engine(request: Request) -> GenerationEngine:
    engine: GenerationEngine | None = getattr(request.app.state, "generation_engine", None)
    if engine is None:
        raise InvalidRequestError("the Pattern Library is not available on this deployment")
    return engine


@router.get(
    "/v1/patterns",
    tags=["patterns"],
    summary="Every live Pattern, with its real pass/failure counts (§4.3/§9.3)",
)
async def list_patterns_route(request: Request, principal: PrincipalDep, roles: ArtizentDep) -> dict[str, Any]:
    engine = _engine(request)
    patterns = await list_patterns(engine.pool, engine.graph_name)
    return {"patterns": patterns, "count": len(patterns)}


@router.get(
    "/v1/patterns/{pattern_id}:promotion-status",
    tags=["patterns"],
    summary="Whether a CANDIDATE pattern meets the AC's own promotion conditions (§9.3)",
)
async def get_promotion_status(
    request: Request,
    principal: PrincipalDep,
    roles: ArtizentDep,
    pattern_id: str = _PATTERN_ID,
    threshold: int = _THRESHOLD,
) -> dict[str, Any]:
    engine = _engine(request)
    try:
        status = await promotion_status(engine.pool, engine.graph_name, pattern_id, threshold=threshold)
    except PatternPromotionError as exc:
        raise InvalidRequestError(str(exc)) from exc
    return status.as_dict()


@router.post(
    "/v1/patterns/{pattern_id}:promote",
    tags=["patterns"],
    summary="Promote a CANDIDATE pattern to ACTIVE -- MA-11, autonomy ceiling L2 (§13.2)",
)
async def promote_pattern_route(
    request: Request,
    principal: PrincipalDep,
    roles: PlatformEngineerDep,
    pattern_id: str = _PATTERN_ID,
    threshold: int = _THRESHOLD,
) -> dict[str, Any]:
    engine = _engine(request)
    try:
        return await promote_pattern(
            engine.pool, engine.graph_name, engine.writer,
            pattern_id=pattern_id, principal=principal, threshold=threshold,
        )
    except PatternPromotionError as exc:
        raise InvalidRequestError(str(exc)) from exc


class RetirePatternRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    reason: str = Field(min_length=1, max_length=4000)


@router.post(
    "/v1/patterns/{pattern_id}:retire",
    tags=["patterns"],
    summary="Retire a pattern manually, with a reason (§13.2's own MA-12, the human path)",
)
async def retire_pattern_route(
    body: RetirePatternRequest,
    request: Request,
    principal: PrincipalDep,
    roles: PlatformEngineerDep,
    pattern_id: str = _PATTERN_ID,
) -> dict[str, Any]:
    engine = _engine(request)
    try:
        return await retire_pattern(
            engine.pool, engine.graph_name, engine.writer,
            pattern_id=pattern_id, reason=body.reason, principal=principal,
        )
    except PatternRetirementError as exc:
        raise InvalidRequestError(str(exc)) from exc


class EditGuardsRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    guards: list[str] = Field(default_factory=list, max_length=50)
    reason: str = Field(min_length=1, max_length=4000)


@router.post(
    "/v1/patterns/{pattern_id}:edit-guards",
    tags=["patterns"],
    summary="Edit a pattern's guards -- creates a new version, the old one retired (§4.3)",
)
async def edit_guards_route(
    body: EditGuardsRequest,
    request: Request,
    principal: PrincipalDep,
    roles: PlatformEngineerDep,
    pattern_id: str = _PATTERN_ID,
) -> dict[str, Any]:
    engine = _engine(request)
    try:
        return await edit_guards(
            engine.pool, engine.graph_name, engine.writer,
            pattern_id=pattern_id, guards=body.guards, reason=body.reason, principal=principal,
        )
    except PatternRetirementError as exc:
        raise InvalidRequestError(str(exc)) from exc


__all__ = ["router"]
