"""The Modeller's API — stories S4.1.1 and S4.1.2.

Synchronous, unlike ``POST /v1/trains:propose`` and ``POST /v1/families:cluster``: those
read the whole estate; this reads one family's own reach, and the story's own budget (a
40-workbook family generates in under 5 minutes) is well inside an HTTP request's own
timeout. Generating a proposal, editing it and submitting it for G2 are all Semantic Model
Engineer actions (§8.6, §15.1); the reads are open to any Artizent role, the same posture
every other families/trains read has.

The state-machine and editing routes (S4.1.2) are ``:action``-suffixed POSTs, matching every
other write in this API (``:move-member``, ``:set-wip-limits``, ``:confirm-family-count``)
rather than introducing PATCH for the first time — see ``model_lifecycle.py``.
"""

from __future__ import annotations

import logging
from typing import Any

from astra_adapter import TargetAdapterError
from fastapi import APIRouter, Path, Request
from pydantic import BaseModel, ConfigDict, Field

from ..build import BuildStore, build_family
from ..conformance_rules import ConformanceRulesetStore
from ..errors import InvalidRequestError
from ..g2 import seed_questions
from ..model_lifecycle import (
    accept_family,
    family_transition_history,
    list_model_versions,
    promote_family,
    regression_status,
    request_new_version,
    submit_for_review,
    update_domain,
    update_grain_statement,
    update_owner,
    update_relationship_cardinality,
    update_table_mode,
)
from ..modeller import Modeller, read_design_document
from .deps import ArtizentDep, PrincipalDep, SemanticModelEngineerDep

logger = logging.getLogger(__name__)

router = APIRouter()

_FAMILY_ID = Path(min_length=5, max_length=64, description="ULID of the ModelFamily.")
_TABLE_ID = Path(min_length=5, max_length=64, description="ULID of the ModelTable.")


def _modeller(request: Request) -> Modeller:
    modeller: Modeller | None = getattr(request.app.state, "modeller", None)
    if modeller is None:
        raise InvalidRequestError("model design proposals are not available on this deployment")
    return modeller


def _build_store(request: Request) -> BuildStore:
    store: BuildStore | None = getattr(request.app.state, "build_store", None)
    if store is None:
        raise InvalidRequestError("builds are not available on this deployment")
    return store


def _conformance_store(request: Request) -> ConformanceRulesetStore:
    store: ConformanceRulesetStore | None = getattr(request.app.state, "conformance_store", None)
    if store is None:
        raise InvalidRequestError("builds are not available on this deployment")
    return store


@router.post(
    "/v1/families/{family_id}:propose-design",
    tags=["modeller"],
    summary="Generate a model design proposal for one family, from the graph (§8.6)",
)
async def propose_design(
    request: Request,
    principal: PrincipalDep,
    roles: SemanticModelEngineerDep,
    family_id: str = _FAMILY_ID,
) -> dict[str, Any]:
    engine = _modeller(request)
    proposal = await engine.run(family_id, principal=principal)
    logger.info(
        "design proposal generated for family %s by %s: %d tables, %d relationships, "
        "%d measures, %d RLS roles, %d open questions (%.2fs)",
        family_id,
        principal.value,
        len(proposal.tables),
        len(proposal.relationships),
        len(proposal.measures),
        len(proposal.rls_roles),
        len(proposal.open_questions),
        proposal.elapsed_seconds,
    )
    return proposal.as_dict()


@router.get(
    "/v1/families/{family_id}/design",
    tags=["modeller"],
    summary="The most recently generated model design proposal for one family",
)
async def get_design(
    request: Request,
    principal: PrincipalDep,
    roles: ArtizentDep,
    family_id: str = _FAMILY_ID,
    semantic_model_id: str | None = None,
) -> dict[str, Any]:
    """Without ``semantic_model_id``, the current version — the one every edit/build
    action already means. With it, a specific version instead (story S4.3.3's own
    Versions tab, viewing an older published-or-deprecated design without disturbing
    which one is "current")."""
    engine = _modeller(request)
    return await read_design_document(
        engine.pool, engine.graph_name, family_id, semantic_model_id=semantic_model_id
    )


@router.get(
    "/v1/families/{family_id}/versions",
    tags=["modeller"],
    summary="Every version of this family's model, newest first — the console's own 'shows both' (§12.2)",
)
async def get_versions(
    request: Request,
    principal: PrincipalDep,
    roles: ArtizentDep,
    family_id: str = _FAMILY_ID,
) -> dict[str, Any]:
    engine = _modeller(request)
    versions = await list_model_versions(engine.pool, engine.graph_name, family_id)
    return {"family_id": family_id, "versions": versions}


# --------------------------------------------------------------------- the state machine


@router.post(
    "/v1/families/{family_id}:accept",
    tags=["modeller"],
    summary="Accept a proposed family for design — PROPOSED/SINGLETON -> DRAFT (§12.2)",
)
async def accept(
    request: Request,
    principal: PrincipalDep,
    roles: SemanticModelEngineerDep,
    family_id: str = _FAMILY_ID,
) -> dict[str, Any]:
    engine = _modeller(request)
    family = await accept_family(engine.pool, engine.graph_name, engine.writer, family_id, principal=principal)
    logger.info("family %s accepted into DRAFT by %s", family_id, principal.value)
    return family


@router.post(
    "/v1/families/{family_id}:submit-for-review",
    tags=["modeller"],
    summary="Submit a DRAFT design for G2 — DRAFT -> IN_REVIEW, freezing a version hash (§12.2)",
)
async def submit(
    request: Request,
    principal: PrincipalDep,
    roles: SemanticModelEngineerDep,
    family_id: str = _FAMILY_ID,
) -> dict[str, Any]:
    engine = _modeller(request)
    result = await submit_for_review(
        engine.pool, engine.graph_name, engine.writer, family_id, principal=principal
    )
    # Story S4.2.1: the design's own open questions become tracked, answerable rows the
    # moment they are frozen — see g2.py's own module docstring for why this happens here
    # rather than inside submit_for_review itself (no circular import between the two
    # modules, and this route already orchestrates more than one module's write, the same
    # way routes_provenance.py's confirm_family_count already does).
    store = getattr(request.app.state, "question_store", None)
    if store is not None:
        document = await read_design_document(engine.pool, engine.graph_name, family_id)
        seeded = await seed_questions(store, family_id, document, principal=principal)
        logger.info(
            "family %s submitted for G2 by %s: version %s, %d question(s) seeded",
            family_id, principal.value, result.get("version"), len(seeded),
        )
    else:  # pragma: no cover - set in every wiring path
        logger.info(
            "family %s submitted for G2 by %s: version %s",
            family_id,
            principal.value,
            result.get("version"),
        )
    return result


@router.get(
    "/v1/families/{family_id}/transitions",
    tags=["modeller"],
    summary="Every state transition this family has been through, with who and when (§12.2)",
)
async def transitions(
    request: Request,
    principal: PrincipalDep,
    roles: ArtizentDep,
    family_id: str = _FAMILY_ID,
) -> dict[str, Any]:
    engine = _modeller(request)
    history = await family_transition_history(engine.pool, engine.graph_name, family_id)
    return {"family_id": family_id, "transitions": history}


# ------------------------------------------------------------------------------- editing


class EditGrainStatementRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    grain_statement: str = Field(min_length=1, max_length=2000)


class EditDomainRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    domain: str = Field(min_length=1, max_length=200)


class EditOwnerRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    owner: str = Field(min_length=1, max_length=200)


class SetTableModeRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    mode: str = Field(min_length=1, max_length=32)


class SetRelationshipCardinalityRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    from_table: str = Field(min_length=1, max_length=64)
    to_table: str = Field(min_length=1, max_length=64)
    cardinality: str = Field(min_length=1, max_length=32)


@router.post(
    "/v1/families/{family_id}:edit-grain-statement",
    tags=["modeller"],
    summary="Edit the drafted grain statement while a design is DRAFT (§12.2)",
)
async def edit_grain_statement(
    body: EditGrainStatementRequest,
    request: Request,
    principal: PrincipalDep,
    roles: SemanticModelEngineerDep,
    family_id: str = _FAMILY_ID,
) -> dict[str, Any]:
    engine = _modeller(request)
    return await update_grain_statement(
        engine.pool,
        engine.graph_name,
        engine.writer,
        family_id,
        grain_statement=body.grain_statement,
        principal=principal,
    )


@router.post(
    "/v1/families/{family_id}:edit-domain",
    tags=["modeller"],
    summary="Assign the family's business domain while a design is DRAFT — needed for G2 (§13.1)",
)
async def edit_domain(
    body: EditDomainRequest,
    request: Request,
    principal: PrincipalDep,
    roles: SemanticModelEngineerDep,
    family_id: str = _FAMILY_ID,
) -> dict[str, Any]:
    engine = _modeller(request)
    return await update_domain(
        engine.pool,
        engine.graph_name,
        engine.writer,
        family_id,
        domain=body.domain,
        principal=principal,
    )


@router.post(
    "/v1/families/{family_id}:edit-owner",
    tags=["modeller"],
    summary="Assign the family's approver while a design is DRAFT — the Programme Board's (§15.3.1)",
)
async def edit_owner(
    body: EditOwnerRequest,
    request: Request,
    principal: PrincipalDep,
    roles: SemanticModelEngineerDep,
    family_id: str = _FAMILY_ID,
) -> dict[str, Any]:
    engine = _modeller(request)
    return await update_owner(
        engine.pool,
        engine.graph_name,
        engine.writer,
        family_id,
        owner=body.owner,
        principal=principal,
    )


@router.post(
    "/v1/families/{family_id}/tables/{table_id}:set-mode",
    tags=["modeller"],
    summary="Override a candidate table's storage mode while a design is DRAFT (§12.2)",
)
async def set_table_mode(
    body: SetTableModeRequest,
    request: Request,
    principal: PrincipalDep,
    roles: SemanticModelEngineerDep,
    family_id: str = _FAMILY_ID,
    table_id: str = _TABLE_ID,
) -> dict[str, Any]:
    engine = _modeller(request)
    return await update_table_mode(
        engine.pool,
        engine.graph_name,
        engine.writer,
        family_id,
        table_id,
        mode=body.mode,
        principal=principal,
    )


@router.post(
    "/v1/families/{family_id}/relationships:set-cardinality",
    tags=["modeller"],
    summary="Override a candidate relationship's cardinality while a design is DRAFT (§12.2)",
)
async def set_relationship_cardinality(
    body: SetRelationshipCardinalityRequest,
    request: Request,
    principal: PrincipalDep,
    roles: SemanticModelEngineerDep,
    family_id: str = _FAMILY_ID,
) -> dict[str, Any]:
    engine = _modeller(request)
    return await update_relationship_cardinality(
        engine.pool,
        engine.graph_name,
        engine.writer,
        family_id,
        from_table=body.from_table,
        to_table=body.to_table,
        cardinality=body.cardinality,
        principal=principal,
    )


# ---------------------------------------------------------------- build (story S4.3.1)


@router.post(
    "/v1/families/{family_id}:build",
    tags=["modeller"],
    summary="Emit TMDL, commit, deploy and smoke-test an APPROVED design — APPROVED -> BUILT (§12.2)",
)
async def build(
    request: Request,
    principal: PrincipalDep,
    roles: SemanticModelEngineerDep,
    family_id: str = _FAMILY_ID,
) -> dict[str, Any]:
    """A manual (re)build — the same pipeline `routes_g2.approve_route` triggers
    automatically the moment a design is approved. Exists for the Build tab's own retry:
    a failed automatic build leaves the family APPROVED, and this is how a Semantic Model
    Engineer tries again after fixing whatever the log said was wrong."""
    engine = _modeller(request)
    artefact_store = getattr(request.app.state, "artefact_store", None)
    target_adapter = getattr(request.app.state, "target_adapter", None)
    if artefact_store is None or target_adapter is None:
        raise InvalidRequestError("builds are not available on this deployment")
    workspace = getattr(request.app.state, "target_workspace", "dev")
    record = await build_family(
        engine.pool,
        engine.graph_name,
        engine.writer,
        artefact_store,
        target_adapter,
        _build_store(request),
        _conformance_store(request),
        family_id,
        gate_decision_id=None,
        workspace=workspace,
        principal=principal,
    )
    return record.as_dict()


@router.get(
    "/v1/families/{family_id}/build",
    tags=["modeller"],
    summary="The most recent build attempt for one family — TMDL, deploy log, smoke queries (§15.3.3)",
)
async def get_build(
    request: Request,
    principal: PrincipalDep,
    roles: ArtizentDep,
    family_id: str = _FAMILY_ID,
) -> dict[str, Any]:
    record = await _build_store(request).latest(family_id)
    return {"family_id": family_id, "build": record.as_dict() if record else None}


# ------------------------------------------------------------ versioning (story S4.3.3)


class RequestNewVersionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    reason: str = Field(min_length=1, max_length=2000)


@router.post(
    "/v1/families/{family_id}:request-new-version",
    tags=["modeller"],
    summary="Change request on a PUBLISHED family — creates a DRAFT v(n+1), v(n) stays PUBLISHED (§12.2)",
)
async def request_new_version_route(
    body: RequestNewVersionRequest,
    request: Request,
    principal: PrincipalDep,
    roles: SemanticModelEngineerDep,
    family_id: str = _FAMILY_ID,
) -> dict[str, Any]:
    engine = _modeller(request)
    result = await request_new_version(
        engine.pool, engine.graph_name, engine.writer, family_id,
        reason=body.reason, principal=principal,
    )
    logger.info(
        "family %s: change request opened v%d (from v%d) by %s",
        family_id, result["version_number"], result["previous_version_number"], principal.value,
    )
    return result


@router.post(
    "/v1/families/{family_id}:promote",
    tags=["modeller"],
    summary="Promote a BUILT version to PUBLISHED, deprecating its predecessor with the date (§12.2)",
)
async def promote(
    request: Request,
    principal: PrincipalDep,
    roles: SemanticModelEngineerDep,
    family_id: str = _FAMILY_ID,
) -> dict[str, Any]:
    """Deploys the already-committed build to the published workspace *before* any state
    changes — a family is only ever marked PUBLISHED (and its predecessor DEPRECATED)
    once that deploy has actually succeeded, never on the strength of a state flip alone.
    """
    engine = _modeller(request)
    target_adapter = getattr(request.app.state, "target_adapter", None)
    if target_adapter is None:
        raise InvalidRequestError("builds are not available on this deployment")

    status = await regression_status(engine.pool, engine.graph_name, family_id)
    if not status.passed:
        raise InvalidRequestError(f"family '{family_id}' cannot be promoted: {status.detail}")

    latest_build = await _build_store(request).latest(family_id)
    if latest_build is None or latest_build.state != "SUCCEEDED" or not latest_build.git_ref:
        raise InvalidRequestError(
            f"family '{family_id}' has no successful build to promote — build it first"
        )

    published_workspace = getattr(request.app.state, "target_workspace_published", "prod")
    try:
        deployment = await target_adapter.deploy(workspace=published_workspace, git_ref=latest_build.git_ref)
    except TargetAdapterError as exc:
        raise InvalidRequestError(f"could not deploy to '{published_workspace}': {exc}") from exc
    if not deployment.ok:
        raise InvalidRequestError(
            f"could not deploy to '{published_workspace}': {deployment.detail or 'deployment failed'}"
        )

    result = await promote_family(engine.pool, engine.graph_name, engine.writer, family_id, principal=principal)
    result["published_workspace"] = published_workspace
    result["deployment_id"] = deployment.deployment_id
    logger.info(
        "family %s promoted to v%d in %s by %s (deprecating v%s)",
        family_id, result["version_number"], published_workspace, principal.value,
        result["deprecated_version_number"],
    )
    return result
