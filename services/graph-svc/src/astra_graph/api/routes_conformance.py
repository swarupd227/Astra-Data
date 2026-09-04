"""The conformance ruleset's own API — story S4.3.2.

    "Rules are data, editable by the architect in Admin, versioned."

Reads are open to any Artizent role (a Semantic Model Engineer reading what a build will be
checked against is a reasonable thing to want, same posture every other families/design
read in this API already has); saving a new version is the architect's
(`MigrationArchitectDep`) — spec §2.4: "Owns target architecture and conformance rules."
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Request
from pydantic import BaseModel, ConfigDict, Field

from ..conformance_rules import (
    RULE_METADATA,
    RULES,
    ConformanceRulesetStore,
    RuleConfig,
)
from ..errors import InvalidRequestError
from .deps import ArtizentDep, MigrationArchitectDep, PrincipalDep

router = APIRouter()


def _store(request: Request) -> ConformanceRulesetStore:
    store: ConformanceRulesetStore | None = getattr(request.app.state, "conformance_store", None)
    if store is None:
        raise InvalidRequestError("the conformance ruleset is not available on this deployment")
    return store


@router.get(
    "/v1/conformance/rules",
    tags=["conformance"],
    summary="The latest conformance ruleset a build is checked against, and what each rule means (§12.3)",
)
async def get_rules(request: Request, principal: PrincipalDep, roles: ArtizentDep) -> dict[str, Any]:
    ruleset = await _store(request).latest()
    return {"ruleset": ruleset.as_dict(), "rule_metadata": RULE_METADATA}


class RuleConfigRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    rule_id: str = Field(min_length=1, max_length=100)
    enabled: bool = True
    params: dict[str, Any] = Field(default_factory=dict)


class SaveRulesRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    rules: list[RuleConfigRequest]


@router.post(
    "/v1/conformance/rules",
    tags=["conformance"],
    summary="Save a new version of the conformance ruleset — the architect's, in Admin (§2.4)",
)
async def save_rules(
    body: SaveRulesRequest,
    request: Request,
    principal: PrincipalDep,
    roles: MigrationArchitectDep,
) -> dict[str, Any]:
    submitted = {r.rule_id for r in body.rules}
    known = set(RULES)
    missing = known - submitted
    unknown = submitted - known
    if missing or unknown:
        raise InvalidRequestError(
            "the ruleset must configure exactly the known rules"
            + (f"; missing: {sorted(missing)}" if missing else "")
            + (f"; unknown: {sorted(unknown)}" if unknown else "")
        )
    rules = [RuleConfig(rule_id=r.rule_id, enabled=r.enabled, params=r.params) for r in body.rules]
    ruleset = await _store(request).save(rules, updated_by=principal.value)
    return {"ruleset": ruleset.as_dict(), "rule_metadata": RULE_METADATA}


__all__ = ["router"]
