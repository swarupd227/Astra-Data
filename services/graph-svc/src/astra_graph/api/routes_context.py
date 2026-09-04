"""Context contracts, and the assembler that materialises them.

S1.3.1. Two reads: what contracts exist and what each declares, and one contract
materialised for one subject.

The materialised document is returned whole and unshaped. A caller cannot ask for part of
it, because the ``context_hash`` describes the whole and a partial response would carry a
hash that does not match what was returned. That is the difference between this and
``/graphql``, where selecting fields is the point.
"""

from __future__ import annotations

import logging
from typing import Annotated, Any

from fastapi import APIRouter, Path, Request
from pydantic import BaseModel

from ..context import ContextAssembler, ContractName, describe
from ..errors import InvalidRequestError
from .deps import ArtizentDep, PrincipalDep

logger = logging.getLogger(__name__)

router = APIRouter()

_SUBJECT_ID = Path(min_length=26, max_length=26, description="ULID of the subject node.")


class ContractsResponse(BaseModel):
    contracts: list[dict[str, Any]]
    count: int


def _assembler(request: Request) -> ContextAssembler:
    assembler: ContextAssembler | None = getattr(request.app.state, "assembler", None)
    if assembler is None:  # pragma: no cover - set in every wiring path
        raise InvalidRequestError("the context assembler is not available")
    return assembler


@router.get(
    "/v1/contexts",
    response_model=ContractsResponse,
    tags=["context"],
    summary="Every declared context contract, with its fields and budget",
)
async def list_contracts(
    request: Request, principal: PrincipalDep, roles: ArtizentDep
) -> ContractsResponse:
    """The contracts as data, including their fragments.

    Published rather than internal: §18.3 makes "what crosses the inference boundary" a
    thing the client's InfoSec signs off, and the fragments are that answer. A reviewer
    reads this endpoint, not the source.
    """
    contracts = describe()
    return ContractsResponse(contracts=contracts, count=len(contracts))


@router.get(
    "/v1/contexts/{name}/{subject_id}",
    tags=["context"],
    summary="Materialise a contract for one subject",
)
async def assemble(
    request: Request,
    principal: PrincipalDep,
    roles: ArtizentDep,
    name: str,
    subject_id: Annotated[str, _SUBJECT_ID],
) -> dict[str, Any]:
    """The canonical document, its hash, and what it cost.

    Fails with 413 when the assembled context exceeds the contract's declared budget. It
    does not truncate: an agent cannot tell a shortened dependency closure from a complete
    one, and the provenance record would carry a hash of the partial context as if it were
    the whole (spec §4.2).
    """
    try:
        contract = ContractName(name)
    except ValueError as exc:
        known = ", ".join(sorted(c.value for c in ContractName))
        raise InvalidRequestError(
            f"unknown context contract '{name}'. Declared contracts: {known}"
        ) from exc

    assembled = await _assembler(request).assemble(contract, subject_id)
    logger.info(
        "context %s for %s assembled by %s: %s bytes, %s nodes, %s",
        contract.value,
        subject_id,
        principal.value,
        assembled.size_bytes,
        assembled.node_count,
        assembled.context_hash,
    )
    return {
        "contract": contract.value,
        "version": assembled.version,
        "subject_id": assembled.subject_id,
        "context_hash": assembled.context_hash,
        "usage": assembled.usage(),
        "document": assembled.document,
    }


__all__ = ["router"]
