"""The G3 gate card's own API -- story S9.1.1, opening F9.1/E9.

Reading the card is open to any Artizent role or the report owner (`G3CardReaderDep`) --
an Artizent role preparing the card for review needs to see it too, the identical
"reader is broader than approver" shape `require_c4_redesign_reader`/`require_exception_
desk_reader` already set. Deciding it (Approve/Request changes/Ask a question) is the
report owner's own action alone (`G3ApproverDep`) -- see `deps.require_g3_approver`'s
own docstring for why "the report owner role for *that* report" still checks the bare
role, not a per-report binding that does not exist.

**`g3_card`/`g3_approve`/etc. read `_compositor`, not a bespoke service, for everything
except approving** -- approving needs an `ArtefactStore` too (the card's own snapshot,
see `g3_card.py`'s own docstring), so `app.state.g3_card` (`G3CardService`) is the
identical "pre-bound object on app.state" shape `MenderService`/`ExceptionDeskService`
already take.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Path, Query, Request
from pydantic import BaseModel, ConfigDict, Field

from ..errors import ElementNotFoundError, InvalidRequestError
from ..g3_card import G3CardError, G3CardService, to_adaptive_card
from .deps import G3ApproverDep, G3CardReaderDep, PrincipalDep

router = APIRouter()

_WORKBOOK_ID = Path(min_length=5, max_length=64, description="ULID of the Workbook.")


def _g3_card_service(request: Request) -> G3CardService:
    service: G3CardService | None = getattr(request.app.state, "g3_card", None)
    if service is None:
        raise InvalidRequestError("the G3 gate card is not available on this deployment")
    return service


@router.get(
    "/v1/workbooks/{workbook_id}:g3-card",
    tags=["g3"],
    summary="The G3 gate card: what, proof, visual, changes, next (§15.5)",
)
async def get_g3_card(
    request: Request, principal: PrincipalDep, roles: G3CardReaderDep,
    workbook_id: str = _WORKBOOK_ID,
    card_format: str | None = Query(
        default=None, alias="format",
        description="'adaptive_card' returns the identical anatomy as a real Adaptive Card 1.5 document (§15.5).",
    ),
) -> dict[str, Any]:
    service = _g3_card_service(request)
    try:
        card = await service.card(workbook_id)
    except ElementNotFoundError as exc:
        raise InvalidRequestError(str(exc)) from exc
    if card_format == "adaptive_card":
        return to_adaptive_card(card)
    return card


class G3DecisionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    rationale: str = Field(min_length=1)


class G3ApproveRequest(G3DecisionRequest):
    countersigned_by: str = Field(min_length=1)


@router.post(
    "/v1/workbooks/{workbook_id}:approve-g3",
    tags=["g3"],
    summary="Approve: records a real GateDecision(gate='G3'), countersigned (§13.1, §13.3)",
)
async def approve_g3(
    body: G3ApproveRequest, request: Request, principal: PrincipalDep, roles: G3ApproverDep,
    workbook_id: str = _WORKBOOK_ID,
) -> dict[str, Any]:
    service = _g3_card_service(request)
    try:
        result = await service.approve(
            workbook_id, rationale=body.rationale, countersigned_by=body.countersigned_by, principal=principal,
        )
    except (ElementNotFoundError, G3CardError) as exc:
        raise InvalidRequestError(str(exc)) from exc
    return result.as_dict()


@router.post(
    "/v1/workbooks/{workbook_id}:request-changes-g3",
    tags=["g3"],
    summary="Request changes: records a real GateDecision(gate='G3') (§13.3)",
)
async def request_changes_g3(
    body: G3DecisionRequest, request: Request, principal: PrincipalDep, roles: G3ApproverDep,
    workbook_id: str = _WORKBOOK_ID,
) -> dict[str, Any]:
    service = _g3_card_service(request)
    try:
        result = await service.request_changes(workbook_id, rationale=body.rationale, principal=principal)
    except (ElementNotFoundError, G3CardError) as exc:
        raise InvalidRequestError(str(exc)) from exc
    return result.as_dict()


class G3QuestionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    question: str = Field(min_length=1)


@router.post(
    "/v1/workbooks/{workbook_id}:ask-g3-question",
    tags=["g3"],
    summary="Ask a question about this workbook's own G3 card (§15.5)",
)
async def ask_g3_question(
    body: G3QuestionRequest, request: Request, principal: PrincipalDep, roles: G3ApproverDep,
    workbook_id: str = _WORKBOOK_ID,
) -> dict[str, Any]:
    service = _g3_card_service(request)
    question = await service.ask_question(workbook_id, question=body.question, principal=principal)
    return question.as_dict()


@router.get(
    "/v1/workbooks/{workbook_id}/g3-questions",
    tags=["g3"],
    summary="Every question asked against this workbook's own G3 card",
)
async def list_g3_questions(
    request: Request, principal: PrincipalDep, roles: G3CardReaderDep, workbook_id: str = _WORKBOOK_ID,
) -> dict[str, Any]:
    service = _g3_card_service(request)
    questions = await service.questions(workbook_id)
    return {"questions": [q.as_dict() for q in questions], "count": len(questions)}


__all__ = ["router"]
