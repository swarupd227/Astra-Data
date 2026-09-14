"""The deployment bill of materials' HTTP surface — story S11.1.1, spec §18.1.

`POST /v1/deployment/bom` is what `tools/generate_bom.py` calls once a deployment
pipeline has signed a bill of materials (`bom.py`) — Artizent-only, the identical
"producing evidence is the delivery organisation's action" posture `routes_artefacts.py`
already has for `POST /v1/artefacts`. The two GET routes are `DeploymentBomReaderDep`
(Artizent or the InfoSec reviewer), matching `routes_decision_register.py`'s own reader
gate for the same client persona and the same kind of "evidence export" remit.

Stored via the existing `ArtefactStore` (`kind="deployment_bom"`, `mu_ref="deployment"` —
a deployment is not a Migration Unit, and this is the same disclosed sentinel-string
posture that store's own module docstring already uses for "no real id exists yet"), not
a new table: the shape a signed JSON envelope needs — content, a hash, who produced it,
when — is exactly what that store already gives every other binary artefact.
"""

from __future__ import annotations

import json
import logging
from typing import Annotated, Any

from fastapi import APIRouter, Path, Query, Request, status
from pydantic import BaseModel, ConfigDict, Field

from .. import bom
from ..artefacts import ArtefactError, ArtefactStore
from ..config import settings
from ..context.canonical import canonical_json
from ..errors import ElementNotFoundError, InvalidRequestError
from .deps import ArtizentDep, DeploymentBomReaderDep, PrincipalDep

logger = logging.getLogger(__name__)

router = APIRouter()

#: `bom.py`'s own module docstring explains why this is a sentinel, not a real MU ref: a
#: deployment is not a Migration Unit.
_DEPLOYMENT_MU_REF = "deployment"

_ARTEFACT_ID = Path(min_length=5, max_length=64, description="Bill-of-materials record id.")


class ImageRefRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=64)
    repository: str = Field(min_length=1, max_length=256)
    tag: str = Field(min_length=1, max_length=128)
    digest: str = Field(min_length=1, max_length=128, examples=["sha256:abcdef..."])


class BomDocumentRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: int = Field(ge=1)
    chart_name: str = Field(min_length=1, max_length=128)
    chart_version: str = Field(min_length=1, max_length=64)
    app_version: str = Field(min_length=1, max_length=64)
    git_commit: str = Field(min_length=7, max_length=64)
    images: list[ImageRefRequest] = Field(min_length=1)
    chart_values_hash: str = Field(min_length=1, max_length=128)
    generated_at: str = Field(min_length=1, max_length=32)
    generated_by: str = Field(min_length=1, max_length=128)


class SubmitBomRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    document: BomDocumentRequest
    signature: str = Field(
        min_length=1,
        max_length=256,
        description="Base64 Ed25519 signature over the document's canonical JSON bytes.",
    )


def _store(request: Request) -> ArtefactStore:
    store: ArtefactStore | None = getattr(request.app.state, "artefact_store", None)
    if store is None:  # pragma: no cover - set in every wiring path
        raise InvalidRequestError("the artefact store is not available on this deployment")
    return store


def _document_from_request(body: BomDocumentRequest) -> bom.BomDocument:
    return bom.document_from_dict(body.model_dump())


def _verify(document: bom.BomDocument, signature: str) -> bool | None:
    """``None`` — not yet checkable — when no public key is configured; a real `True`/
    `False` once one is. Computed at read time, not stored, so a key configured after a
    bill of materials was already recorded is still checked against it correctly."""
    public_key_pem = settings().bom_public_key_pem
    if not public_key_pem:
        return None
    return bom.verify_signature(document, signature, public_key_pem.encode("utf-8"))


def _envelope_dict(record_dict: dict[str, Any], document: bom.BomDocument, signature: str) -> dict[str, Any]:
    return {
        **record_dict,
        "document": document.as_dict(),
        "signature": signature,
        "signature_verified": _verify(document, signature),
    }


@router.post(
    "/v1/deployment/bom",
    status_code=status.HTTP_201_CREATED,
    tags=["deployment"],
    summary="Record a deployment's signed bill of materials as an evidence record",
)
async def submit_bom(
    body: SubmitBomRequest,
    request: Request,
    principal: PrincipalDep,
    roles: ArtizentDep,
) -> dict[str, Any]:
    document = _document_from_request(body.document)
    content = canonical_json({"document": document.as_dict(), "signature": body.signature})
    case_id = f"{document.chart_name}@{document.chart_version}:{document.git_commit}"

    try:
        record = await _store(request).store(
            kind="deployment_bom",
            mu_ref=_DEPLOYMENT_MU_REF,
            case_id=case_id,
            content=content,
            media_type="application/json",
            created_by=principal.value,
        )
    except ArtefactError as exc:
        raise InvalidRequestError(str(exc)) from None

    verified = _verify(document, body.signature)
    logger.info(
        "deployment bill of materials %s recorded by %s: %s (signature_verified=%s)",
        record.id,
        principal.value,
        case_id,
        verified,
    )
    return _envelope_dict(record.as_dict(), document, body.signature)


@router.get(
    "/v1/deployment/bom",
    tags=["deployment"],
    summary="Recent deployment bills of materials",
)
async def list_boms(
    request: Request,
    principal: PrincipalDep,
    roles: DeploymentBomReaderDep,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
) -> dict[str, Any]:
    store = _store(request)
    records = await store.for_mu(_DEPLOYMENT_MU_REF, kind="deployment_bom", limit=limit)
    envelopes = []
    for record in records:
        content = await store.content(record.id)
        if content is None:  # pragma: no cover - a record with no bytes is a store defect
            continue
        payload = json.loads(content)
        document = bom.document_from_dict(payload["document"])
        envelopes.append(_envelope_dict(record.as_dict(), document, payload["signature"]))
    return {"boms": envelopes}


@router.get(
    "/v1/deployment/bom/{artefact_id}",
    tags=["deployment"],
    summary="One deployment's bill of materials, with its signature checked",
)
async def get_bom(
    request: Request,
    principal: PrincipalDep,
    roles: DeploymentBomReaderDep,
    artefact_id: Annotated[str, _ARTEFACT_ID],
) -> dict[str, Any]:
    store = _store(request)
    record = await store.get(artefact_id)
    if record is None or record.kind != "deployment_bom":
        raise ElementNotFoundError(f"no deployment bill of materials '{artefact_id}'")
    content = await store.content(artefact_id)
    if content is None:  # pragma: no cover - a record with no bytes is a store defect
        raise ElementNotFoundError(f"bill of materials '{artefact_id}' has no stored content")

    payload = json.loads(content)
    document = bom.document_from_dict(payload["document"])
    return _envelope_dict(record.as_dict(), document, payload["signature"])
