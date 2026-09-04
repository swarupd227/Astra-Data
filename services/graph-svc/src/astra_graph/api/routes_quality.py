"""The Parse Quality Queue.

S1.2.2, the parity engineer's story: know before the Calibration Wave which workbooks the
grammar cannot yet read, and be able to do something about it.

The queue is readable two ways round, and both matter. By **workbook** it answers "what is
held"; by **construct** it answers "what should we fix next", because one grammar gap
typically blocks many workbooks and the number that decides the work is how many a single
fix would release.

The console screen that renders this is S1.4.3; this is the API behind it.
"""

from __future__ import annotations

import logging
from typing import Annotated, Any

from fastapi import APIRouter, Query, Request, status
from pydantic import BaseModel, ConfigDict, Field

from ..errors import ElementNotFoundError, InvalidRequestError
from ..grammar import (
    MIN_DETAIL as MIN_GRAMMAR_DETAIL,
)
from ..grammar import (
    GrammarIssueError,
    IssueState,
    IssueStore,
    IssueTracker,
    new_issue,
)
from ..harvest.quality import ParseQualityStore
from ..harvest.rescore import Rescorer
from .deps import ArtizentDep, PrincipalDep

logger = logging.getLogger(__name__)

router = APIRouter()

#: Spec §4.1.4 and S1.2.2: the default, and configurable.
DEFAULT_THRESHOLD = 0.98

#: A decision to accept a construct the grammar cannot read is a decision on the record,
#: so it has to say why. The same reasoning as a retirement reason (spec P4).
MIN_IGNORABLE_REASON_LENGTH = 8


class GrammarIssueRequest(BaseModel):
    """S1.4.3: "open grammar issue (creates a ticket with the construct text and
    locations)"."""

    model_config = ConfigDict(extra="forbid")

    construct: str = Field(  # type: ignore[assignment]
        min_length=1,
        max_length=8192,
        description="The construct text, verbatim as the queue reports it.",
    )
    summary: str = Field(
        default="",
        max_length=256,
        description="One line. Defaults to the construct when omitted.",
    )
    detail: str = Field(
        min_length=MIN_GRAMMAR_DETAIL,
        max_length=8192,
        description="What the grammar should do with it. Whoever picks this up will not "
        "have been in the conversation.",
    )


class ResolveIssueRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    state: IssueState = Field(description="RESOLVED when the grammar reads it now; "
                              "WONT_FIX when it never will.")
    resolution: str = Field(min_length=MIN_GRAMMAR_DETAIL, max_length=4096)


class IgnorableRequest(BaseModel):
    # The field is named for the domain and for the wire. Pydantic's BaseModel carries a
    # deprecated `construct` classmethod, so the name shadows it; that is harmless here —
    # nothing calls `IgnorableRequest.construct()` — and an alias was tried first but is
    # dropped by the Field()/Annotated interaction on a request body. The narrow ignore
    # below is preferred to renaming the field the console sends.
    model_config = ConfigDict(extra="forbid")

    construct: str = Field(  # type: ignore[assignment]
        min_length=1,
        max_length=8192,
        description="The construct text, verbatim as the queue reports it.",
    )
    reason: str = Field(
        min_length=1,
        max_length=4096,
        description="Why this construct can be accepted rather than read. Recorded "
        "against every occurrence and shown to whoever asks why a workbook was released.",
    )
    site: str | None = Field(
        default=None,
        description="Restrict the decision to one site. Omit to accept it estate-wide.",
    )


class QueueResponse(BaseModel):
    threshold: float
    held: list[dict[str, Any]]
    count: int


class ConstructsResponse(BaseModel):
    threshold: float
    constructs: list[dict[str, Any]]
    count: int


def _quality(request: Request) -> ParseQualityStore:
    store: ParseQualityStore | None = getattr(request.app.state, "quality_store", None)
    if store is None:
        raise InvalidRequestError("parse-quality records are not available on this deployment")
    return store


def _rescorer(request: Request) -> Rescorer:
    rescorer: Rescorer | None = getattr(request.app.state, "rescorer", None)
    if rescorer is None:
        raise InvalidRequestError("re-scoring is not available on this deployment")
    return rescorer


def _issues(request: Request) -> IssueStore:
    store: IssueStore | None = getattr(request.app.state, "issue_store", None)
    if store is None:  # pragma: no cover - set in every wiring path
        raise InvalidRequestError("grammar issues are not available on this deployment")
    return store


def _tracker(request: Request) -> IssueTracker:
    tracker: IssueTracker | None = getattr(request.app.state, "issue_tracker", None)
    if tracker is None:  # pragma: no cover - set in every wiring path
        raise InvalidRequestError("no issue tracker is configured")
    return tracker


def _graph(request: Request) -> str:
    from ..config import settings

    return settings().graph_name


def _threshold(value: float | None) -> float:
    if value is None:
        return DEFAULT_THRESHOLD
    if not 0.0 <= value <= 1.0:
        raise InvalidRequestError(f"threshold must be between 0 and 1, got {value}")
    return value


@router.get(
    "/v1/parse-quality/queue",
    response_model=QueueResponse,
    tags=["parse quality"],
    summary="Workbooks the grammar could not fully read",
)
async def parse_quality_queue(
    request: Request,
    principal: PrincipalDep,
    roles: ArtizentDep,
    threshold: Annotated[float | None, Query(ge=0.0, le=1.0)] = None,
    limit: int = 200,
) -> QueueResponse:
    """The queue itself: workbooks below the threshold, worst first.

    These are the workbooks that "do not advance to CLUSTERED" (S1.2.2). The state machine
    that word belongs to is E3's; what exists here is the fact it will gate on, and
    ``GET /v1/parse-quality/gate/{site}/{luid}`` answers it directly.
    """
    if not 1 <= limit <= 1000:
        raise InvalidRequestError(f"limit must be between 1 and 1000, got {limit}")
    resolved = _threshold(threshold)
    held = await _quality(request).held(_graph(request), threshold=resolved, limit=limit)
    return QueueResponse(
        threshold=resolved, held=[item.as_dict() for item in held], count=len(held)
    )


@router.get(
    "/v1/parse-quality/constructs",
    response_model=ConstructsResponse,
    tags=["parse quality"],
    summary="Unrecognised constructs, grouped, with what each is holding up",
)
async def parse_quality_constructs(
    request: Request,
    principal: PrincipalDep,
    roles: ArtizentDep,
    threshold: Annotated[float | None, Query(ge=0.0, le=1.0)] = None,
    include_resolved: bool = False,
    limit: int = 200,
) -> ConstructsResponse:
    """Grouped by construct text and ordered by how much each is blocking.

    ``workbooks_released_if_resolved`` counts the held workbooks for which this is the only
    remaining unrecognised construct — the estate-wide figure S1.4.3 wants on the screen.
    """
    if not 1 <= limit <= 1000:
        raise InvalidRequestError(f"limit must be between 1 and 1000, got {limit}")
    resolved = _threshold(threshold)
    groups = await _quality(request).construct_groups(
        _graph(request), threshold=resolved, include_resolved=include_resolved, limit=limit
    )
    # Each construct carries whether an issue is already open against it, so the queue
    # can show "raised" rather than inviting a second ticket for the same gap.
    raised = await _issues(request).by_construct()
    constructs = []
    for group in groups:
        entry = group.as_dict()
        issue = raised.get(group.construct)
        entry["issue"] = (
            None
            if issue is None
            else {
                "id": issue.id,
                "state": issue.state.value,
                "opened_by": issue.opened_by,
                "opened_at": issue.opened_at,
                "external": {"ref": issue.external_ref, "url": issue.external_url},
            }
        )
        constructs.append(entry)

    return ConstructsResponse(
        threshold=resolved,
        constructs=constructs,
        count=len(constructs),
    )


@router.get(
    "/v1/parse-quality/workbooks/{site}/{workbook_luid}",
    tags=["parse quality"],
    summary="One workbook's unrecognised constructs, verbatim and located",
)
async def workbook_constructs(
    request: Request,
    principal: PrincipalDep,
    roles: ArtizentDep,
    site: str,
    workbook_luid: str,
) -> dict[str, Any]:
    """S1.2.2: stored verbatim, with location (sheet, field) and flagged."""
    constructs = await _quality(request).constructs_for(_graph(request), site, workbook_luid)
    if not constructs:
        raise ElementNotFoundError(
            f"no parse-quality record for workbook '{workbook_luid}' in site '{site}'"
        )
    return {
        "site": site,
        "workbook_luid": workbook_luid,
        "constructs": [construct.as_dict() for construct in constructs],
        "unrecognised_count": len([c for c in constructs if c.unrecognised]),
    }


@router.post(
    "/v1/parse-quality/constructs:ignorable",
    tags=["parse quality"],
    summary="Accept a construct the grammar cannot read, and re-score",
)
async def mark_ignorable(
    body: IgnorableRequest,
    request: Request,
    principal: PrincipalDep,
    roles: ArtizentDep,
) -> dict[str, Any]:
    """Mark a construct ignorable with a reason.

    S1.2.2: "either action re-scores the workbook without a full re-harvest". This one
    needs no source access at all — the constructs and counts are already stored, so every
    affected workbook is re-scored from them and the new score written to its node.
    """
    reason = body.reason.strip()
    if len(reason) < MIN_IGNORABLE_REASON_LENGTH:
        raise InvalidRequestError(
            f"accepting a construct needs a reason of at least "
            f"{MIN_IGNORABLE_REASON_LENGTH} characters; it is the record of why a "
            f"workbook the grammar could not read was allowed through"
        )

    graph = _graph(request)
    affected = await _quality(request).mark_ignorable(
        graph, body.construct, reason=reason, principal=principal.value, site=body.site
    )
    if not affected:
        raise ElementNotFoundError(
            f"no unrecognised construct matching {body.construct!r}"
            + (f" in site '{body.site}'" if body.site else "")
        )

    result = await _rescorer(request).rescore(affected, principal=principal)
    logger.info(
        "construct accepted as ignorable by %s: %s workbook(s) re-scored, %s released",
        principal.value,
        len(result.rescored),
        len(result.released),
    )
    return {
        "construct": body.construct,
        "occurrences_accepted": len(affected),
        "workbooks_rescored": len(result.rescored),
        "workbooks_released": len(result.released),
        "rescored": [item.as_dict() for item in result.rescored],
    }


@router.get(
    "/v1/parse-quality/gate/{site}/{workbook_luid}",
    tags=["parse quality"],
    summary="May this workbook advance past HARVESTED?",
)
async def parse_quality_gate(
    request: Request,
    principal: PrincipalDep,
    roles: ArtizentDep,
    site: str,
    workbook_luid: str,
    threshold: Annotated[float | None, Query(ge=0.0, le=1.0)] = None,
) -> dict[str, Any]:
    """The check the Cartographer will make before clustering a workbook.

    Spec §4.1.4: a workbook below the threshold "cannot leave HARVESTED until a Platform
    Engineer has reviewed the unrecognised constructs". The Migration Unit state machine
    is E3's, so this is the gate it consults rather than the transition itself.
    """
    resolved = _threshold(threshold)
    graph = _graph(request)

    counts = getattr(request.app.state, "harvest_store", None)
    record = await counts.counts(graph, site, workbook_luid) if counts else None
    if record is None:
        raise ElementNotFoundError(
            f"workbook '{workbook_luid}' in site '{site}' has not been harvested"
        )
    _recognised, _ignorable, _total, parse_quality = record

    constructs = await _quality(request).constructs_for(graph, site, workbook_luid)
    unrecognised = len([item for item in constructs if item.unrecognised])
    blocked = parse_quality is not None and parse_quality < resolved

    return {
        "site": site,
        "workbook_luid": workbook_luid,
        "threshold": resolved,
        "may_advance": not blocked,
        "parse_quality": parse_quality,
        "unrecognised_constructs": unrecognised,
        "reason": (
            f"parse quality {parse_quality:.3f} is below the {resolved} threshold, with "
            f"{unrecognised} unrecognised construct(s) awaiting review"
            if blocked
            else None
        ),
    }


__all__ = ["DEFAULT_THRESHOLD", "router"]


# ------------------------------------------------------------------ grammar issues


@router.post(
    "/v1/parse-quality/constructs:issue",
    status_code=status.HTTP_201_CREATED,
    tags=["parse quality"],
    summary="Raise a grammar issue for a construct the adapter cannot read",
)
async def open_grammar_issue(
    body: GrammarIssueRequest,
    request: Request,
    principal: PrincipalDep,
    roles: ArtizentDep,
) -> dict[str, Any]:
    """S1.4.3's second action.

    The construct's locations and the number of workbooks it is holding up are read from
    the queue **now** and copied onto the issue. They are a snapshot on purpose: the estate
    moves, and by the time somebody picks the issue up a live lookup would describe wherever
    the construct is then rather than the evidence it was raised on.
    """
    groups = await _quality(request).construct_groups(
        _graph(request), threshold=_threshold(None), include_resolved=True, limit=1000
    )
    group = next((g for g in groups if g.construct == body.construct), None)
    if group is None:
        raise ElementNotFoundError(
            f"no unrecognised construct {body.construct!r} in this estate. The queue "
            f"reports the text verbatim; it has to match."
        )

    found = await _quality(request).occurrences_of(_graph(request), body.construct, limit=25)
    locations = [
        {
            "site": occurrence.site,
            "workbook": occurrence.workbook_name,
            "workbook_luid": occurrence.workbook_luid,
            "project": occurrence.project,
            "sheet": occurrence.sheet,
            "field": occurrence.field,
            "detail": occurrence.detail,
        }
        for occurrence in found
    ]

    try:
        issue = new_issue(
            construct=body.construct,
            summary=body.summary,
            detail=body.detail,
            opened_by=principal.value,
            locations=locations,
            occurrences=group.occurrences,
            workbooks_held=group.workbooks_held,
        )
    except GrammarIssueError as exc:
        raise InvalidRequestError(str(exc)) from exc

    try:
        stored = await _issues(request).open(issue)
    except GrammarIssueError as exc:
        raise InvalidRequestError(str(exc)) from exc

    # Mirroring is one-way and optional (§21); a tracker that cannot be reached must not
    # lose the issue, which is already recorded by the time this runs.
    tracker = _tracker(request)
    try:
        ref, url = await tracker.mirror(stored)
    except Exception:
        logger.exception("could not mirror grammar issue %s to %s", stored.id, tracker.kind)
        ref, url = None, None

    logger.info(
        "grammar issue %s opened by %s for %r (%s workbooks held)",
        stored.id,
        principal.value,
        body.construct,
        group.workbooks_held,
    )
    return {**stored.as_dict(), "tracker": tracker.kind, "mirrored": bool(ref or url)}


@router.get(
    "/v1/parse-quality/issues",
    tags=["parse quality"],
    summary="Grammar issues raised from the queue",
)
async def list_grammar_issues(
    request: Request, principal: PrincipalDep, roles: ArtizentDep, limit: int = 100
) -> dict[str, Any]:
    if not 1 <= limit <= 500:
        raise InvalidRequestError(f"limit must be between 1 and 500, got {limit}")
    issues = await _issues(request).recent(limit=limit)
    return {
        "issues": [issue.as_dict() for issue in issues],
        "count": len(issues),
        "tracker": _tracker(request).kind,
    }


@router.post(
    "/v1/parse-quality/issues/{issue_id}:resolve",
    tags=["parse quality"],
    summary="Close a grammar issue",
)
async def resolve_grammar_issue(
    body: ResolveIssueRequest,
    request: Request,
    principal: PrincipalDep,
    roles: ArtizentDep,
    issue_id: str,
) -> dict[str, Any]:
    """Closing the issue does not re-score anything.

    Extending the grammar changes what a *re-parse* produces, so the workbooks it releases
    are released by re-harvesting them under the new grammar (S1.2.4 makes an incremental
    run re-parse when the grammar version has moved). Marking the issue closed here and
    silently re-scoring would claim a result the parser has not produced.
    """
    if body.state.active:
        raise InvalidRequestError(
            f"{body.state.value} does not close an issue; use RESOLVED or WONT_FIX"
        )
    resolved = await _issues(request).resolve(
        issue_id,
        state=body.state,
        resolution=body.resolution,
        resolved_by=principal.value,
    )
    if resolved is None:
        raise ElementNotFoundError(f"no open grammar issue '{issue_id}'")
    logger.info("grammar issue %s %s by %s", issue_id, body.state.value, principal.value)
    return {
        **resolved.as_dict(),
        "note": "Re-harvest the affected workbooks to re-parse them under the new grammar.",
    }

