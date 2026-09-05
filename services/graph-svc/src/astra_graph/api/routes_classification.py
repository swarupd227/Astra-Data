"""The classifier's own API — story S5.1.1.

    "Estate-wide class mix is reported on the Programme Board against the calibration
    targets 45 / 30 / 18 / 7. Re-classification runs when the rule set or pattern library
    changes and reports what moved class."

The class-mix read is open to any Artizent role — the same posture every other Programme
Board figure already has (family count, train projections, G2 reviews); re-classifying is
the parity engineer's (`ParityEngineerDep`), the persona this story's own acceptance
criteria names.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Request

from ..classify import ClassificationEngine, reclassify_estate
from ..classify import class_mix as compute_class_mix
from ..errors import InvalidRequestError
from .deps import ArtizentDep, ParityEngineerDep, PrincipalDep

router = APIRouter()


def _engine(request: Request) -> ClassificationEngine:
    engine: ClassificationEngine | None = getattr(request.app.state, "classifier", None)
    if engine is None:
        raise InvalidRequestError("calculation classification is not available on this deployment")
    return engine


@router.get(
    "/v1/calculations:class-mix",
    tags=["classification"],
    summary="Estate-wide C1-C4 class mix against the calibration targets 45/30/18/7 (§9.1, §9.5)",
)
async def get_class_mix(request: Request, principal: PrincipalDep, roles: ArtizentDep) -> dict[str, Any]:
    engine = _engine(request)
    return await compute_class_mix(engine.pool, engine.graph_name)


@router.post(
    "/v1/calculations:reclassify",
    tags=["classification"],
    summary="Re-classify every live CalculatedField and report what moved class (§9.1)",
)
async def reclassify(
    request: Request, principal: PrincipalDep, roles: ParityEngineerDep
) -> dict[str, Any]:
    engine = _engine(request)
    result = await reclassify_estate(
        engine.pool, engine.graph_name, engine.writer,
        provenance_store=engine.provenance, principal=principal,
    )
    return result.as_dict()


__all__ = ["router"]
