"""Evidence Export's own API -- story S11.3.2, closes F11.3.

    "Export selects scope (programme, site, train, MU, date range) and produces a
    bundle... Bundle is signed; the console shows the signature and a verification
    instruction... Export of the BlackRock-scale programme completes in under 30
    minutes."

Triggering and reading are both gated `TenantAccessReaderDep` (Artizent, or the
InfoSec reviewer) -- this story's own literal persona, unlike S11.1.2/S11.2.1/S11.3.1's
own platform-safety actions (`PlatformEngineerDep`): an InfoSec reviewer is the one who
asks for this export, so they are the one who triggers it, the identical "any Artizent
role, or this one named client role" shape `decision_register.py`'s own export routes
already set for the same persona.

Assembly is a background task with a polling status route -- the one existing
precedent for a potentially-long operation this codebase has (`routes_rebuild.py`'s own
`RebuildStatus`/`202 ACCEPTED`/poll shape), reused here because this story's own AC
names a 30-minute budget no HTTP request should sensibly block for.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime
from typing import Annotated, Any

from fastapi import APIRouter, Path, Request, Response, status
from pydantic import BaseModel, ConfigDict, Field

from ..artefacts import ArtefactStore
from ..config import settings
from ..errors import ElementNotFoundError, InvalidRequestError
from ..evidence_export import (
    EvidenceExportError,
    EvidenceSigner,
    ExportProgress,
    ExportScope,
    LocalEvidenceSigner,
    assemble_manifest,
    build_export_zip,
)
from ..ids import new_ulid
from .deps import PrincipalDep, TenantAccessReaderDep

logger = logging.getLogger(__name__)

router = APIRouter()


class StartExportRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: str = Field(pattern="^(programme|site|train|mu)$")
    ref: str | None = Field(default=None, max_length=64)
    date_from: str | None = None
    date_to: str | None = None


def _progress(request: Request) -> ExportProgress:
    existing: ExportProgress | None = getattr(request.app.state, "evidence_export_progress", None)
    if existing is None:  # pragma: no cover - set in every wiring path
        existing = ExportProgress()
        request.app.state.evidence_export_progress = existing
    return existing


def _signer(request: Request) -> EvidenceSigner:
    existing: EvidenceSigner | None = getattr(request.app.state, "evidence_signer", None)
    if existing is None:  # pragma: no cover - set in every wiring path
        existing = LocalEvidenceSigner(private_key_pem=settings().evidence_export_private_key_pem)
        request.app.state.evidence_signer = existing
    return existing


def _pool_and_graph(request: Request) -> tuple[Any, str]:
    engine = getattr(request.app.state, "cartographer", None)
    if engine is None:
        raise InvalidRequestError("Evidence Export is not available on this deployment")
    return engine.pool, engine.graph_name


def _artefact_store(request: Request) -> ArtefactStore:
    store: ArtefactStore | None = getattr(request.app.state, "artefact_store", None)
    if store is None:  # pragma: no cover - set in every wiring path
        raise InvalidRequestError("the artefact store is not available on this deployment")
    return store


async def _run(
    tracker: ExportProgress,
    pool: Any,
    graph_name: str,
    scope: ExportScope,
    signer: EvidenceSigner,
    artefact_store: ArtefactStore,
    *,
    export_id: str,
    generated_by: str,
) -> None:
    try:
        def on_progress(category: str, count: int) -> None:
            counts = dict(tracker.counts or {})
            counts[category] = count
            tracker.counts = counts

        manifest = await assemble_manifest(
            pool, graph_name, scope, export_id=export_id, generated_by=generated_by,
            on_progress=on_progress,
        )
        zip_bytes, signature, public_key_pem = build_export_zip(manifest, signer=signer)

        record = await artefact_store.store(
            kind="evidence_export", mu_ref="evidence_export", case_id=export_id,
            content=zip_bytes, media_type="application/zip", created_by=generated_by,
        )
        tracker.artefact_id = record.id
        tracker.signature = signature
        tracker.public_key_pem = public_key_pem.decode("utf-8")
        tracker.counts = manifest["counts"]
        tracker.last_error = None
        logger.info(
            "evidence export %s finished by %s: %s", export_id, generated_by, manifest["counts"],
        )
    except Exception as exc:  # reported on the status, not swallowed
        logger.exception("evidence export %s failed", export_id)
        tracker.last_error = str(exc)
    finally:
        tracker.running = False
        tracker.finished_at = datetime.now(UTC).isoformat()


@router.post(
    "/v1/evidence-export",
    status_code=status.HTTP_202_ACCEPTED,
    tags=["evidence-export"],
    summary="Start a signed Evidence Export for a scope (programme, site, train or MU)",
)
async def start_export(
    body: StartExportRequest, request: Request, principal: PrincipalDep, roles: TenantAccessReaderDep,
) -> dict[str, Any]:
    tracker = _progress(request)
    if tracker.running:
        raise InvalidRequestError(
            "an Evidence Export is already in progress on this deployment; only one "
            "runs at a time"
        )

    try:
        scope = ExportScope(kind=body.kind, ref=body.ref, date_from=body.date_from, date_to=body.date_to)
    except EvidenceExportError as exc:
        raise InvalidRequestError(str(exc)) from exc

    pool, graph_name = _pool_and_graph(request)
    export_id = f"evexp_{new_ulid()}"

    tracker.running = True
    tracker.export_id = export_id
    tracker.started_at = datetime.now(UTC).isoformat()
    tracker.finished_at = None
    tracker.scope = scope.as_dict()
    tracker.counts = None
    tracker.artefact_id = None
    tracker.signature = None
    tracker.public_key_pem = None
    tracker.last_error = None

    task = asyncio.create_task(
        _run(
            tracker, pool, graph_name, scope, _signer(request), _artefact_store(request),
            export_id=export_id, generated_by=principal.value,
        )
    )
    tasks: set[asyncio.Task[Any]] = getattr(request.app.state, "evidence_export_tasks", set())
    tasks.add(task)
    task.add_done_callback(tasks.discard)
    request.app.state.evidence_export_tasks = tasks

    logger.info("evidence export %s accepted by %s: %s", export_id, principal.value, scope.as_dict())
    return {"state": "QUEUED", "export_id": export_id}


@router.get(
    "/v1/evidence-export/status",
    tags=["evidence-export"],
    summary="Whether an export is in progress, its progress, and the last result",
)
async def export_status(
    request: Request, principal: PrincipalDep, roles: TenantAccessReaderDep,
) -> dict[str, Any]:
    return _progress(request).as_dict()


@router.get(
    "/v1/evidence-export/{export_id}/download",
    tags=["evidence-export"],
    summary="The finished bundle's own bytes -- a real zip, for a human viewer",
)
async def download_export(
    request: Request,
    principal: PrincipalDep,
    roles: TenantAccessReaderDep,
    export_id: Annotated[str, Path(min_length=5, max_length=64)],
) -> Response:
    """A dedicated route, not `GET /v1/artefacts/{id}/content` -- that route is gated
    `ArtefactReaderDep` (Artizent or the client report owner), not the InfoSec reviewer
    this story's own persona actually is; widening it would grant InfoSec access to
    every other artefact kind too, a broader change than this story asks for."""
    tracker = _progress(request)
    if tracker.export_id != export_id or tracker.artefact_id is None:
        raise ElementNotFoundError(f"no finished Evidence Export '{export_id}'")
    content = await _artefact_store(request).content(tracker.artefact_id)
    if content is None:  # pragma: no cover - a record with no bytes is a store defect
        raise ElementNotFoundError(f"Evidence Export '{export_id}' has no stored content")
    return Response(
        content=content, media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="{export_id}.zip"'},
    )


@router.get(
    "/v1/evidence-export/public-key",
    tags=["evidence-export"],
    summary="This deployment's own current Evidence Export public key, for offline verification",
)
async def export_public_key(
    request: Request, principal: PrincipalDep, roles: TenantAccessReaderDep,
) -> dict[str, Any]:
    return {"public_key_pem": _signer(request).public_key_pem().decode("utf-8")}


__all__ = ["router"]
