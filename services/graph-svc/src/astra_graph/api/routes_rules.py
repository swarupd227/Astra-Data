"""The deterministic rules engine's own API — stories S5.2.1 and S5.2.2.

    "Rule coverage report: percentage of estate calcs matched by rule, by rule family."
    "Regression: every rule change re-runs the golden corpus and the PASSED artefacts that
    used the rule; any new failure blocks promotion." (S5.2.2)

Every read here is open to any Artizent role — the same posture every other Programme
Board/coverage figure in this console already has; applying rules is the platform
engineer's (`PlatformEngineerDep`), the persona this story's own acceptance criteria names.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Request

from ..errors import InvalidRequestError
from ..rules import RULES, RulesEngine, apply_rules_estate, check_regression, rule_coverage
from .deps import ArtizentDep, PlatformEngineerDep, PrincipalDep

router = APIRouter()


def _engine(request: Request) -> RulesEngine:
    engine: RulesEngine | None = getattr(request.app.state, "rules_engine", None)
    if engine is None:
        raise InvalidRequestError("the rules engine is not available on this deployment")
    return engine


@router.get(
    "/v1/calculations:rule-catalog",
    tags=["rules"],
    summary="The shipped rule set — id, class, family, description and guards (§9.2/§9.3)",
)
async def get_rule_catalog(request: Request, principal: PrincipalDep, roles: ArtizentDep) -> dict[str, Any]:
    return {
        "rules": [
            {
                "id": rule.id,
                "version": rule.version,
                "class": rule.class_,
                "family": rule.family,
                "description": rule.description,
                "guards": list(rule.guards),
                "golden_case_count": len(rule.golden_cases),
            }
            for rule in RULES
        ]
    }


@router.get(
    "/v1/calculations:rule-coverage",
    tags=["rules"],
    summary="Percentage of estate calcs matched by rule, by rule family (§9.5)",
)
async def get_rule_coverage(request: Request, principal: PrincipalDep, roles: ArtizentDep) -> dict[str, Any]:
    engine = _engine(request)
    return await rule_coverage(engine.pool, engine.graph_name)


@router.get(
    "/v1/calculations:rule-regression",
    tags=["rules"],
    summary="Every already-produced Measure re-checked against the current rule set (§S5.2.2)",
)
async def get_rule_regression(request: Request, principal: PrincipalDep, roles: ArtizentDep) -> dict[str, Any]:
    engine = _engine(request)
    report = await check_regression(engine.pool, engine.graph_name)
    return report.as_dict()


@router.post(
    "/v1/calculations:apply-rules",
    tags=["rules"],
    summary="Render every rule-coverable CalculatedField into a Measure, deterministically (§9.2)",
)
async def apply_rules(
    request: Request, principal: PrincipalDep, roles: PlatformEngineerDep
) -> dict[str, Any]:
    engine = _engine(request)
    result = await apply_rules_estate(
        engine.pool, engine.graph_name, engine.writer, engine.provenance, principal=principal
    )
    return result.as_dict()


__all__ = ["router"]
