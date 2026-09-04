"""Provenance, graph versions, and the audit path between them.

S1.3.2: "from a ProvenanceRecord the console can re-materialise the context at the recorded
graph version and show that the hash matches", and "graph versions are addressable by event
offset".

The interesting endpoint is `:verify`. It does not look up a stored document — nothing
stores one. It re-runs the assembler over the graph as it stood at the recorded offset and
compares the hash it computes with the one the record claims. A stored copy would only
prove that a copy was stored; a re-materialisation proves the record.

A failed verification is a **200 with a finding**, not an error status. An auditor's tool
that returned 4xx for the interesting case would be one an auditor learns to distrust —
and MISMATCH and UNVERIFIABLE are different findings, never conflated.
"""

from __future__ import annotations

import logging
from typing import Annotated, Any

from fastapi import APIRouter, Path, Query, Request, status
from pydantic import BaseModel, ConfigDict, Field

from ..cartographer import count_families
from ..context import ContractName
from ..errors import ElementNotFoundError, InvalidRequestError
from ..provenance import AgentMode, ContextVerifier, ProvenanceStore, new_record
from ..retention import ProgrammeStore, prunable_before
from .deps import ArtizentDep, PrincipalDep, ProgrammeManagerDep

logger = logging.getLogger(__name__)

router = APIRouter()

_PROVENANCE_ID = Path(min_length=5, max_length=64, description="Provenance record id.")


class RecordProvenanceRequest(BaseModel):
    """§4.2's record, as an agent submits it."""

    model_config = ConfigDict(extra="forbid")

    artefact_kind: str = Field(min_length=1, max_length=64, examples=["MEASURE"])
    artefact_ref: str = Field(min_length=1, max_length=256)
    artefact_content_hash: str = Field(min_length=8, max_length=128)
    agent: str = Field(min_length=1, max_length=64, examples=["transpiler"])
    agent_version: str = Field(min_length=1, max_length=32, examples=["1.4.2"])
    mode: AgentMode
    contract: ContractName
    subject_id: str = Field(min_length=26, max_length=26)
    context_hash: str = Field(
        min_length=8,
        max_length=128,
        description="The hash the assembler returned for the context this agent was given.",
    )
    graph_version: int = Field(
        ge=0,
        description="Event offset the context was assembled at. Without it the record is "
        "descriptive rather than verifiable — the graph moves.",
    )
    prompt_hash: str | None = Field(default=None, max_length=128)
    model: str | None = Field(default=None, max_length=128)
    tokens_in: int | None = Field(default=None, ge=0)
    tokens_out: int | None = Field(default=None, ge=0)
    confidence: float | None = Field(default=None, ge=0.0, le=1.0)
    pattern_ref: str | None = Field(default=None, max_length=128)
    supersedes_id: str | None = Field(default=None, max_length=64)


class VerifyClaimRequest(BaseModel):
    """A claim about a context, verified without a stored record.

    The same check the record path runs. Separated so an artefact store that holds its own
    provenance — §5.2 gives that to artefact-svc — can use the audit path without this
    service having to hold the record too.
    """

    model_config = ConfigDict(extra="forbid")

    contract: ContractName
    subject_id: str = Field(min_length=26, max_length=26)
    graph_version: int = Field(ge=0)
    context_hash: str = Field(min_length=8, max_length=128)


class OpenProgrammeRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=256)
    started_at: str = Field(description="RFC 3339. When the programme began.")


class CloseProgrammeRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    closed_at: str = Field(description="RFC 3339. When the programme ended.")


def _store(request: Request) -> ProvenanceStore:
    store: ProvenanceStore | None = getattr(request.app.state, "provenance_store", None)
    if store is None:  # pragma: no cover - set in every wiring path
        raise InvalidRequestError("provenance is not available on this deployment")
    return store


def _verifier(request: Request) -> ContextVerifier:
    verifier: ContextVerifier | None = getattr(request.app.state, "verifier", None)
    if verifier is None:  # pragma: no cover - set in every wiring path
        raise InvalidRequestError("the context verifier is not available")
    return verifier


def _programmes(request: Request) -> ProgrammeStore:
    store: ProgrammeStore | None = getattr(request.app.state, "programme_store", None)
    if store is None:  # pragma: no cover - set in every wiring path
        raise InvalidRequestError("programmes are not available on this deployment")
    return store


def _estate_graph(request: Request) -> tuple[Any, str]:
    """The pool and graph name ``count_families`` needs (story S3.1.3) — reached through
    the Cartographer's own state, the same object ``routes_families.py`` reads its pool
    from, so there is one place that decides which graph a deployment reads."""
    engine = getattr(request.app.state, "cartographer", None)
    if engine is None:
        raise InvalidRequestError("clustering is not available on this deployment")
    return engine.pool, engine.graph_name


# ------------------------------------------------------------------- graph versions


@router.get(
    "/v1/graph-versions/current",
    tags=["provenance"],
    summary="The graph's current version, as an event offset",
)
async def current_version(
    request: Request, principal: PrincipalDep, roles: ArtizentDep
) -> dict[str, Any]:
    """S1.3.2: versions are addressable by event offset.

    An agent records this alongside its context hash, and an auditor quotes it back. It is
    not a separate identifier the platform mints — it is the sequence number of the last
    event in the stream this service already publishes.
    """
    version, at = await request.app.state.repository.current_version()
    return {"graph_version": version, "at": at}


# ------------------------------------------------------------------------ provenance


@router.post(
    "/v1/provenance",
    status_code=status.HTTP_201_CREATED,
    tags=["provenance"],
    summary="Record how an artefact was produced",
)
async def record_provenance(
    body: RecordProvenanceRequest,
    request: Request,
    principal: PrincipalDep,
    roles: ArtizentDep,
) -> dict[str, Any]:
    """Store a §4.2 provenance record.

    The graph version is not validated against the claimed hash here. Recording is what an
    agent does at the moment it produces an artefact; verifying is what an auditor does
    later, and folding the two together would mean a verification failure could stop an
    artefact being recorded at all — losing the very evidence that shows something went
    wrong.
    """
    record = await _store(request).record(
        new_record(
            artefact_kind=body.artefact_kind,
            artefact_ref=body.artefact_ref,
            artefact_content_hash=body.artefact_content_hash,
            agent=body.agent,
            agent_version=body.agent_version,
            mode=body.mode,
            contract=body.contract,
            subject_id=body.subject_id,
            context_hash=body.context_hash,
            graph_version=body.graph_version,
            prompt_hash=body.prompt_hash,
            model=body.model,
            tokens_in=body.tokens_in,
            tokens_out=body.tokens_out,
            confidence=body.confidence,
            pattern_ref=body.pattern_ref,
            supersedes_id=body.supersedes_id,
            created_by=principal.value,
        )
    )
    logger.info(
        "provenance %s recorded by %s: %s %s at graph version %s",
        record.id,
        principal.value,
        record.agent,
        record.artefact_kind,
        record.graph_version,
    )
    return record.as_dict()


@router.get(
    "/v1/provenance/{provenance_id}",
    tags=["provenance"],
    summary="One provenance record",
)
async def get_provenance(
    request: Request,
    principal: PrincipalDep,
    roles: ArtizentDep,
    provenance_id: Annotated[str, _PROVENANCE_ID],
) -> dict[str, Any]:
    record = await _store(request).get(provenance_id)
    if record is None:
        raise ElementNotFoundError(f"no provenance record '{provenance_id}'")
    return record.as_dict()


@router.post(
    "/v1/provenance/{provenance_id}:verify",
    tags=["provenance"],
    summary="Re-materialise the context this record describes and compare the hash",
)
async def verify_provenance(
    request: Request,
    principal: PrincipalDep,
    roles: ArtizentDep,
    provenance_id: Annotated[str, _PROVENANCE_ID],
    include_document: Annotated[
        bool,
        Query(description="Return the re-materialised context, not just the verdict."),
    ] = False,
) -> dict[str, Any]:
    """S1.3.2's headline. Returns a finding, including when the finding is bad news."""
    record = await _store(request).get(provenance_id)
    if record is None:
        raise ElementNotFoundError(f"no provenance record '{provenance_id}'")

    verification = await _verifier(request).verify_record(
        record, include_document=include_document
    )
    logger.info(
        "provenance %s verified by %s: %s",
        provenance_id,
        principal.value,
        verification.outcome.value,
    )
    return {
        "record": record.as_dict(),
        "verification": verification.as_dict(include_document=include_document),
    }


@router.post(
    "/v1/provenance:verify",
    tags=["provenance"],
    summary="Verify a context claim without a stored record",
)
async def verify_claim(
    body: VerifyClaimRequest,
    request: Request,
    principal: PrincipalDep,
    roles: ArtizentDep,
    include_document: Annotated[bool, Query()] = False,
) -> dict[str, Any]:
    verification = await _verifier(request).verify(
        contract=body.contract,
        subject_id=body.subject_id,
        graph_version=body.graph_version,
        claimed_hash=body.context_hash,
        include_document=include_document,
    )
    return verification.as_dict(include_document=include_document)


# ------------------------------------------------------- programmes and retention


@router.get(
    "/v1/retention",
    tags=["provenance"],
    summary="How long graph versions stay addressable, and what may be pruned",
)
async def retention(
    request: Request, principal: PrincipalDep, roles: ArtizentDep
) -> dict[str, Any]:
    """S1.3.2: retention is the programme lifetime plus twelve months.

    ``prunable_before`` is null while any programme is open, and null when no programme is
    recorded at all — an empty table is not permission to delete. Nothing in this service
    prunes; this is the policy a pruner would have to ask.
    """
    state = prunable_before(await _programmes(request).programmes())
    return {**state.as_dict(), "pruning_implemented": False}


@router.post(
    "/v1/programmes",
    status_code=status.HTTP_201_CREATED,
    tags=["provenance"],
    summary="Record a programme, which starts its retention clock",
)
async def open_programme(
    body: OpenProgrammeRequest,
    request: Request,
    principal: PrincipalDep,
    roles: ArtizentDep,
) -> dict[str, Any]:
    programme = await _programmes(request).open_programme(
        name=body.name, started_at=body.started_at, created_by=principal.value
    )
    logger.info("programme %s opened by %s: %s", programme.id, principal.value, programme.name)
    return programme.as_dict()


@router.post(
    "/v1/programmes/{programme_id}:close",
    tags=["provenance"],
    summary="Close a programme, starting the twelve-month retention floor",
)
async def close_programme(
    body: CloseProgrammeRequest,
    request: Request,
    principal: PrincipalDep,
    roles: ArtizentDep,
    programme_id: str,
) -> dict[str, Any]:
    """Closing starts the clock. It deletes nothing, and it cannot be undone or re-dated:
    a retention floor that can be moved is not a floor."""
    programme = await _programmes(request).close_programme(
        programme_id, closed_at=body.closed_at
    )
    if programme is None:
        raise ElementNotFoundError(
            f"no open programme '{programme_id}'. A closed programme cannot be re-closed: "
            f"that would move its retention floor."
        )
    logger.info("programme %s closed by %s", programme_id, principal.value)
    return programme.as_dict()


@router.get(
    "/v1/programmes",
    tags=["provenance"],
    summary="Every programme, with its clustering and family-count figures",
)
async def list_programmes(
    request: Request, principal: PrincipalDep, roles: ArtizentDep
) -> dict[str, Any]:
    programmes = await _programmes(request).programmes()
    return {"programmes": [p.as_dict() for p in programmes]}


@router.post(
    "/v1/programmes/{programme_id}:confirm-family-count",
    tags=["provenance"],
    summary="Confirm the estate's current family count as the Month 1 calibration input",
)
async def confirm_family_count(
    request: Request,
    principal: PrincipalDep,
    roles: ProgrammeManagerDep,
    programme_id: str,
) -> dict[str, Any]:
    """Story S3.1.3. No count in the request body: the figure a Programme Manager confirms
    is read live from the estate (``count_families``), never typed — see ADR 0024."""
    pool, graph_name = _estate_graph(request)
    count = await count_families(pool, graph_name)
    programme = await _programmes(request).confirm_family_count(
        programme_id, count=count, confirmed_by=principal.value
    )
    if programme is None:
        raise ElementNotFoundError(f"no programme '{programme_id}'")
    result = programme.as_dict()
    logger.info(
        "family count confirmed by %s for programme %s: %d (planned %d, delta %+d)",
        principal.value,
        programme_id,
        count,
        result["planned_family_count"],
        result["family_count_delta"],
    )
    return result


__all__ = ["router"]
