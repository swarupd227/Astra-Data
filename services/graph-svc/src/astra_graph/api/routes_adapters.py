"""Adapter conformance and promotion — story S2.1.2.

    "Suite output is a signed report stored in the artefact store and linked from Platform
    Health. A failing conformance run blocks adapter promotion to a tenant."

The suite runs outside the platform, by design: `astra-adapter conformance --adapter tableau
--remote --out report.json` needs the adapter, not the tenant. These endpoints are where the
result is *recorded*, and where the tenant decides what to do about it.

**The platform does not run the suite for you.** It could — and then a report would attest to
whatever the platform happened to have loaded, which is not the adapter image that will be
deployed. §6.1 enables an adapter as a versioned worker image, so the report has to come from
running the suite against that image, and the platform's job is to hold the result and act on
it. `--remote` is the mode that produces such a report.

**Recording a failing report is not an error.** A failing run is the evidence that an adapter
must not be promoted, and refusing to store it would leave the platform able to say "no" but
not "why".
"""

from __future__ import annotations

import logging
from typing import Any, cast

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from ..adapters.conformance import (
    AdapterBuild,
    ConformanceStore,
    PromotionError,
    build_from_report,
)
from .deps import ArtizentDep, PrincipalDep

logger = logging.getLogger(__name__)

router = APIRouter()

#: Minimum characters of reason on a promotion or a revocation. Same floor as every other
#: recorded decision in this platform (§15.2): whoever reads it later was not in the room.
MIN_REASON = 10


class SignedReportBody(BaseModel):
    """A report as `astra-adapter conformance --out` writes it."""

    report: dict[str, Any]
    content_hash: str
    signed: bool = False
    signature: str | None = None
    algorithm: str | None = None
    key_id: str | None = None


class PromoteRequest(BaseModel):
    adapter_version: str
    interface_version: str
    grammar_version: str | None = None
    reason: str = Field(min_length=MIN_REASON)


class RevokeRequest(BaseModel):
    reason: str = Field(min_length=MIN_REASON)


def _store(request: Request) -> ConformanceStore:
    store = getattr(request.app.state, "conformance_store", None)
    if store is None:  # pragma: no cover - set in every wiring path
        raise HTTPException(
            status_code=503,
            detail={
                "error": "unavailable",
                "message": "adapter conformance records are not available",
            },
        )
    # cast rather than isinstance: ConformanceStore is a structural Protocol and the
    # wiring is what guarantees the shape, not a runtime check.
    return cast(ConformanceStore, store)


@router.post(
    "/v1/adapters/conformance",
    tags=["adapters"],
    status_code=201,
    summary="Record a signed conformance report",
)
async def record_report(
    request: Request, body: SignedReportBody, principal: PrincipalDep, roles: ArtizentDep
) -> dict[str, Any]:
    """Store a report, passing or failing.

    The signature is **not** verified here, and that is deliberate rather than an omission:
    this deployment may have no key (§18.1 puts it in Key Vault, which arrives with E11), and
    a platform that rejected unsigned reports would be unable to record anything at all until
    then. What is stored is what was submitted, including whether it claimed to be signed and
    with which key — so a verifier that does have the key can check it later, and a report
    that was never signed is visibly never signed rather than quietly accepted as if it were.
    """
    record = await _store(request).record(body.model_dump(), principal=principal.value)
    logger.info(
        "conformance report recorded id=%s adapter=%s passed=%s signed=%s",
        record.id,
        record.build.describe(),
        record.passed,
        record.signed,
    )
    return record.as_dict()


@router.get(
    "/v1/adapters/conformance",
    tags=["adapters"],
    summary="Recent conformance reports",
)
async def list_reports(
    request: Request, principal: PrincipalDep, roles: ArtizentDep, limit: int = 20
) -> dict[str, Any]:
    records = await _store(request).recent(limit=min(limit, 100))
    return {"reports": [record.as_dict() for record in records], "count": len(records)}


@router.get(
    "/v1/adapters/conformance/{report_id}",
    tags=["adapters"],
    summary="One conformance report, in full",
)
async def read_report(
    request: Request, report_id: str, principal: PrincipalDep, roles: ArtizentDep
) -> dict[str, Any]:
    record = await _store(request).get(report_id)
    if record is None:
        raise HTTPException(
            status_code=404,
            detail={"error": "not_found", "message": f"no conformance report {report_id!r}"},
        )
    return record.as_dict(full=True)


@router.get("/v1/adapters", tags=["adapters"], summary="Adapters promoted on this tenant")
async def list_adapters(
    request: Request, principal: PrincipalDep, roles: ArtizentDep
) -> dict[str, Any]:
    promotions = await _store(request).promotions()
    return {
        "promoted": [promotion.as_dict() for promotion in promotions],
        "count": len(promotions),
    }


@router.post(
    "/v1/adapters/{adapter}:promote",
    tags=["adapters"],
    summary="Enable an adapter build on this tenant",
)
async def promote(
    request: Request,
    adapter: str,
    body: PromoteRequest,
    principal: PrincipalDep,
    roles: ArtizentDep,
) -> dict[str, Any]:
    """S2.1.2 criterion 3. Refused unless *this exact build* has a passing report.

    The build is named in the request rather than read from whatever adapter happens to be
    running, so promotion is a decision about a specific image and not about whatever is
    currently loaded. A tenant promoting `tableau 1.4` must say so, and must have a report
    for `tableau 1.4`.
    """
    build = AdapterBuild(
        name=adapter,
        version=body.adapter_version,
        interface_version=body.interface_version,
        grammar_version=body.grammar_version,
    )
    try:
        promotion = await _store(request).promote(
            build, reason=body.reason, principal=principal.value
        )
    except PromotionError as exc:
        # 409, not 400: the request is well-formed and the tenant's state is what refuses it.
        raise HTTPException(
            status_code=409,
            detail={"error": "conformance_required", "message": str(exc)},
        ) from exc

    logger.info(
        "adapter promoted adapter=%s report=%s by=%s",
        build.describe(),
        promotion.report_id,
        principal.value,
    )
    return promotion.as_dict()


@router.post(
    "/v1/adapters/{adapter}:revoke",
    tags=["adapters"],
    summary="Withdraw an adapter from this tenant",
)
async def revoke(
    request: Request,
    adapter: str,
    body: RevokeRequest,
    principal: PrincipalDep,
    roles: ArtizentDep,
) -> dict[str, Any]:
    """The other half of a gate worth having.

    A conformance failure found after promotion — a grammar regression, a source API change —
    has to be actionable, and "delete the row" is not an audit trail. Revocation keeps the
    promotion and records who withdrew it and why.
    """
    promotion = await _store(request).revoke(
        adapter, reason=body.reason, principal=principal.value
    )
    if promotion is None:
        raise HTTPException(
            status_code=404,
            detail={
                "error": "not_found",
                "message": f"no adapter named {adapter!r} is promoted on this tenant",
            },
        )
    return promotion.as_dict()


@router.post(
    "/v1/adapters/{adapter}:check",
    tags=["adapters"],
    summary="Would this build be promotable?",
)
async def check(
    request: Request,
    adapter: str,
    body: PromoteRequest,
    principal: PrincipalDep,
    roles: ArtizentDep,
) -> dict[str, Any]:
    """The gate's answer without the side effect.

    Here because a deployment pipeline wants to know before it swaps an image, not after —
    and because the alternative is a pipeline that promotes and then rolls back, which is a
    worse thing to do to a tenant than a refusal.
    """
    build = AdapterBuild(
        name=adapter,
        version=body.adapter_version,
        interface_version=body.interface_version,
        grammar_version=body.grammar_version,
    )
    store = _store(request)
    record = await store.passing_for(build) or await store.latest(adapter)
    try:
        from ..adapters.conformance import check_promotable

        check_promotable(record, build)
    except PromotionError as exc:
        return {
            "adapter": adapter,
            "build": build.describe(),
            "promotable": False,
            "reason": str(exc),
            "report_id": record.id if record else None,
        }
    return {
        "adapter": adapter,
        "build": build.describe(),
        "promotable": True,
        "reason": "a passing conformance report exists for this exact build",
        "report_id": record.id if record else None,
    }


def report_from_suite(report: dict[str, Any]) -> AdapterBuild:
    """The build a report is about. Exported for the startup check."""
    return build_from_report(report)
