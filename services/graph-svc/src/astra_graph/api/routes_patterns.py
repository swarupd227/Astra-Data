"""The Pattern Library's own API — story S5.5.1.

    "Promotion CANDIDATE -> ACTIVE requires N distinct proof passes (default 5), zero
    failures, and a Platform Engineer approval (MA-11, L2)."

Reading the library (what exists, and whether a candidate is eligible) is open to any
Artizent role, the same posture every other Programme Board-adjacent read in this API
already has; promoting a candidate is the platform engineer's (`PlatformEngineerDep`) —
§13.2's own MA-11 action class, ceiling L2 ("approve-first"), the same role S5.2.1's
apply-rules route and S5.3.2's eval-run route already drive.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Path, Query, Request

from ..errors import InvalidRequestError
from ..generation import GenerationEngine
from ..patterns import (
    PROMOTION_THRESHOLD_DEFAULT,
    PatternPromotionError,
    list_patterns,
    promote_pattern,
    promotion_status,
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


__all__ = ["router"]
