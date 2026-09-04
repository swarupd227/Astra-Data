"""The G2 workflow's API — story S4.2.1.

Reads here are deliberately **not** ``ArtizentDep`` — unlike the raw Cypher endpoint (§2.4)
or the internal Model Detail screen's own reads, a data owner is exactly who this data is
*for*. Every route still requires an identified principal (``PrincipalDep``); none requires
a specific role except the actions §13.1 names one for: asking, approving and requesting
changes are the client data owner's (``ClientDataOwnerDep``), replying and marking a
question answered are open to whichever side is actually in the thread.
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Path, Request
from pydantic import BaseModel, ConfigDict, Field

from ..build import build_family
from ..cartographer import list_families
from ..errors import InvalidRequestError
from ..g2 import (
    QuestionStore,
    answer_question,
    approve,
    ask_question,
    client_proposal_view,
    list_questions,
    reply_to_question,
    request_changes,
)
from ..g2_reminders import (
    DEFAULT_SLA_WORKING_DAYS,
    NotificationChannel,
    ReminderStore,
    pending_g2_reviews,
    send_due_reminders,
)
from ..modeller import Modeller
from ..principal import Principal
from .deps import ArtizentDep, ClientDataOwnerDep, DomainScopeDep, PrincipalDep

#: States a data owner's own G2 review actually concerns — not the whole estate's family
#: list (that stays Artizent-only, `GET /v1/families`; §15.2's "client surfaces are calm"
#: means a narrower list, not the same one with a role check bolted on).
_REVIEW_STATES = ("IN_REVIEW", "APPROVED", "DRAFT")

logger = logging.getLogger(__name__)

router = APIRouter()

_FAMILY_ID = Path(min_length=5, max_length=64, description="ULID of the ModelFamily.")
_QUESTION_ID = Path(min_length=5, max_length=64, description="ULID of the G2 question.")


def _questions(request: Request) -> QuestionStore:
    store: QuestionStore | None = getattr(request.app.state, "question_store", None)
    if store is None:
        raise InvalidRequestError("the G2 workflow is not available on this deployment")
    return store


def _modeller_engine(request: Request) -> Modeller:
    engine: Modeller | None = getattr(request.app.state, "modeller", None)
    if engine is None:
        raise InvalidRequestError("the G2 workflow is not available on this deployment")
    return engine


async def _build_on_approval(request: Request, family_id: str, *, gate_decision_id: str | None) -> None:
    """"The model exists as code the moment it is approved" (S4.3.1) — the automatic
    trigger. A build failure is a normal, already-recorded outcome (`build_family` writes
    a FAILED `BuildRecord` rather than raising for anything short of a genuine bug), but
    this is still defensive: the G2 decision that was just recorded is real and must stand
    regardless of what the downstream build does, the same reasoning that keeps this call
    from rolling anything back on failure.
    """
    build_store = getattr(request.app.state, "build_store", None)
    artefact_store = getattr(request.app.state, "artefact_store", None)
    target_adapter = getattr(request.app.state, "target_adapter", None)
    conformance_store = getattr(request.app.state, "conformance_store", None)
    if build_store is None or artefact_store is None or target_adapter is None or conformance_store is None:
        return
    engine = _modeller_engine(request)
    workspace = getattr(request.app.state, "target_workspace", "dev")
    try:
        await build_family(
            engine.pool,
            engine.graph_name,
            engine.writer,
            artefact_store,
            target_adapter,
            build_store,
            conformance_store,
            family_id,
            gate_decision_id=gate_decision_id,
            workspace=workspace,
            principal=Principal("agent:steward", run_id="run-build"),
        )
    except Exception:
        logger.exception("automatic build failed to even start for family %s", family_id)


def _reminder_store(request: Request) -> ReminderStore:
    store: ReminderStore | None = getattr(request.app.state, "reminder_store", None)
    if store is None:
        raise InvalidRequestError("G2 reminders are not available on this deployment")
    return store


def _notification_channel(request: Request) -> NotificationChannel:
    channel: NotificationChannel | None = getattr(request.app.state, "notification_channel", None)
    if channel is None:
        raise InvalidRequestError("G2 reminders are not available on this deployment")
    return channel


@router.get(
    "/v1/families:for-review",
    tags=["g2"],
    summary="Families a data owner's G2 review concerns — DRAFT, IN_REVIEW, APPROVED (§15.1)",
)
async def families_for_review(request: Request, principal: PrincipalDep) -> dict[str, Any]:
    engine = _modeller_engine(request)
    families = [
        family
        for state in _REVIEW_STATES
        for family in await list_families(engine.pool, engine.graph_name, state=state)
    ]
    return {"families": families, "count": len(families)}


@router.get(
    "/v1/families/{family_id}/proposal",
    tags=["g2"],
    summary="The Model Proposal (client view): what the model is, what changes, open questions (§15.3)",
)
async def get_proposal(
    request: Request,
    principal: PrincipalDep,
    family_id: str = _FAMILY_ID,
) -> dict[str, Any]:
    engine = _modeller_engine(request)
    return await client_proposal_view(engine.pool, engine.graph_name, _questions(request), family_id)


@router.get(
    "/v1/families/{family_id}/questions",
    tags=["g2"],
    summary="Every G2 question asked about one family's design, with its thread",
)
async def get_questions(
    request: Request,
    principal: PrincipalDep,
    family_id: str = _FAMILY_ID,
) -> dict[str, Any]:
    questions = await list_questions(_questions(request), family_id)
    return {"family_id": family_id, "questions": [q.as_dict() for q in questions]}


class AskQuestionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    category: str = Field(default="general", max_length=64)
    question: str = Field(min_length=1, max_length=4000)


@router.post(
    "/v1/families/{family_id}/questions:ask",
    tags=["g2"],
    summary="Ask a question about a family's design — the data owner's (§4.2.1)",
)
async def ask(
    body: AskQuestionRequest,
    request: Request,
    principal: PrincipalDep,
    roles: ClientDataOwnerDep,
    family_id: str = _FAMILY_ID,
) -> dict[str, Any]:
    question = await ask_question(
        _questions(request), family_id, category=body.category, question=body.question, principal=principal,
    )
    return question.as_dict()


class ReplyRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    message: str = Field(min_length=1, max_length=4000)


@router.post(
    "/v1/questions/{question_id}:reply",
    tags=["g2"],
    summary="Reply in a question's thread — visible to both sides (§4.2.1)",
)
async def reply(
    body: ReplyRequest,
    request: Request,
    principal: PrincipalDep,
    question_id: str = _QUESTION_ID,
) -> dict[str, Any]:
    question = await reply_to_question(_questions(request), question_id, message=body.message, principal=principal)
    return question.as_dict()


@router.post(
    "/v1/questions/{question_id}:answer",
    tags=["g2"],
    summary="Mark a question answered — required before its family can be approved (§4.2.1)",
)
async def answer(
    request: Request,
    principal: PrincipalDep,
    question_id: str = _QUESTION_ID,
) -> dict[str, Any]:
    question = await answer_question(_questions(request), question_id, principal=principal)
    return question.as_dict()


class ApproveRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    countersigned_by: str = Field(min_length=1, max_length=200)
    rationale: str = Field(min_length=1, max_length=4000)


@router.post(
    "/v1/families/{family_id}:approve-g2",
    tags=["g2"],
    summary="Approve a design at G2 — IN_REVIEW -> APPROVED (§13.1)",
)
async def approve_route(
    body: ApproveRequest,
    request: Request,
    principal: PrincipalDep,
    roles: ClientDataOwnerDep,
    domain_scope: DomainScopeDep,
    family_id: str = _FAMILY_ID,
) -> dict[str, Any]:
    engine = _modeller_engine(request)
    result = await approve(
        engine.pool,
        engine.graph_name,
        engine.writer,
        _questions(request),
        family_id,
        principal=principal,
        domain_scope=domain_scope,
        countersigned_by=body.countersigned_by,
        rationale=body.rationale,
    )
    logger.info("family %s approved at G2 by %s", family_id, principal.value)
    await _build_on_approval(request, family_id, gate_decision_id=result.get("gate_decision_id"))
    return result


class RequestChangesRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    comment: str = Field(min_length=1, max_length=4000)


@router.post(
    "/v1/families/{family_id}:request-changes",
    tags=["g2"],
    summary="Send a design back to DRAFT with a comment — IN_REVIEW -> DRAFT (§12.2)",
)
async def request_changes_route(
    body: RequestChangesRequest,
    request: Request,
    principal: PrincipalDep,
    roles: ClientDataOwnerDep,
    domain_scope: DomainScopeDep,
    family_id: str = _FAMILY_ID,
) -> dict[str, Any]:
    engine = _modeller_engine(request)
    result = await request_changes(
        engine.pool,
        engine.graph_name,
        engine.writer,
        family_id,
        principal=principal,
        domain_scope=domain_scope,
        comment=body.comment,
    )
    logger.info("family %s sent back to DRAFT by %s", family_id, principal.value)
    return result


# ------------------------------------------------- the Programme Board's tile (story S4.2.2)


@router.get(
    "/v1/families:awaiting-g2",
    tags=["g2"],
    summary="Families awaiting G2: days waiting, the approver, and SLA breach (§15.3.1)",
)
async def awaiting_g2(request: Request, principal: PrincipalDep, roles: ArtizentDep) -> dict[str, Any]:
    engine = _modeller_engine(request)
    reviews = await pending_g2_reviews(engine.pool, engine.graph_name, _questions(request))
    return {
        "sla_working_days": DEFAULT_SLA_WORKING_DAYS,
        "reviews": [review.as_dict() for review in reviews],
        "breached_count": sum(1 for review in reviews if review.breached),
    }


@router.post(
    "/v1/g2/reminders:send",
    tags=["g2"],
    summary="Record and send whichever 3- and 5-day G2 reminders are now due (§15.3.1)",
)
async def send_reminders(request: Request, principal: PrincipalDep, roles: ArtizentDep) -> dict[str, Any]:
    engine = _modeller_engine(request)
    sent = await send_due_reminders(
        engine.pool,
        engine.graph_name,
        _questions(request),
        _reminder_store(request),
        _notification_channel(request),
    )
    if sent:
        logger.info("%d G2 reminder(s) sent by %s", len(sent), principal.value)
    return {"sent": [record.as_dict() for record in sent], "count": len(sent)}
