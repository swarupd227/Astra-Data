"""The G2 workflow — a data owner reviews a model design. Story S4.2.1.

    "As a data owner, I want to review a model design for my domain in plain language and
    approve it or ask a question, so that I sign off what I understand."

**Questions are a platform table (`public.g2_question`), not an estate-graph node** — the
same footing `grammar_issue` (S1.4.3) already established for "raised as work, tracked with
state, evidence copied in at the moment it was raised." §4.1's ontology has nothing that
models a review thread, and inventing a node for it would be guessing at a graph shape
nothing else needs. See the migration's own docstring for the full reasoning.

**Questions are seeded from the design at submission, not invented here.** `model_lifecycle
.submit_for_review` freezes a version and, at that same moment, this module's
`seed_questions` promotes whatever `design_document["open_questions"]` the Modeller (or a
Semantic Model Engineer's own edits) raised into tracked, threaded, answerable rows — the
open questions a data owner actually sees are the ones the frozen version itself raised,
plus whatever either side asks afterward.

**`GateDecision` is a real graph node — declared since S1.1.1, unused until this story.**
§13.3's own worked example nests `approver`/`countersign` as objects; this ontology is flat
everywhere, so `approver_role`/`countersigner`/`countersigner_role`/`version_hash` are their
own properties (ADR 0030 covers the full reasoning). `evidence_ref` is the `SemanticModel`
id the decision is about — the same node `version_hash` was frozen on.

**Domain scope is an asserted header, the same "real until E11 maps it for real" posture
every other identity/role fact in this codebase already has.** `ModelFamily.domain` has
been declared since S1.1.1 and never once written — no story has needed it before this one.
`model_lifecycle.update_domain` (added by this story) lets a Semantic Model Engineer assign
one while DRAFT; approval checks the caller's `X-Astra-Domain-Scope` header against it, and
treats an *unset* family domain as open to any data owner (a disclosed gap: nobody assigns
one automatically yet, see ADR 0030) rather than a inexplicable universal refusal.

**The cycle count lives on `ModelFamily`, incremented once per request-changes** — the same
node the state machine itself lives on, one more counter alongside the S3.1.x/S4.1.x ones
already there.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import Enum
from typing import Any, Protocol

import asyncpg

from .cartographer import get_family
from .errors import ElementNotFoundError, ForbiddenError, InvalidRequestError
from .ids import new_ulid
from .lineage import hydrate
from .model_lifecycle import require_transition
from .modeller import read_design_document
from .ontology.types import BASE_NODE_PROPERTIES
from .principal import Principal
from .writes import GraphWriter, NodeWrite

logger = logging.getLogger(__name__)

QUESTION_TABLE = "public.g2_question"

MIN_QUESTION_LENGTH = 5
MIN_MESSAGE_LENGTH = 2
MIN_RATIONALE_LENGTH = 8

_NODE_SERVER_MANAGED = frozenset(p.name for p in BASE_NODE_PROPERTIES if p.server_managed) | {
    "id",
    "side",
}


def _writable_node_properties(properties: Mapping[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in properties.items() if k not in _NODE_SERVER_MANAGED}


class QuestionState(str, Enum):
    OPEN = "OPEN"
    ANSWERED = "ANSWERED"


@dataclass(frozen=True, slots=True)
class Question:
    id: str
    family_id: str
    category: str
    question: str
    state: QuestionState
    asked_by: str
    evidence: Mapping[str, Any] = field(default_factory=dict)
    thread: tuple[dict[str, Any], ...] = ()
    asked_at: str | None = None
    answered_by: str | None = None
    answered_at: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "family_id": self.family_id,
            "category": self.category,
            "question": self.question,
            "state": self.state.value,
            "evidence": dict(self.evidence),
            "thread": list(self.thread),
            "asked_by": self.asked_by,
            "asked_at": self.asked_at,
            "answered_by": self.answered_by,
            "answered_at": self.answered_at,
        }


class QuestionStore(Protocol):
    async def open(self, question: Question) -> Question: ...

    async def get(self, question_id: str) -> Question | None: ...

    async def for_family(self, family_id: str) -> list[Question]: ...

    async def count_open(self, family_id: str) -> int: ...

    async def append_message(self, question_id: str, message: dict[str, Any]) -> Question | None: ...

    async def answer(self, question_id: str, *, answered_by: str) -> Question | None: ...


class PostgresQuestionStore:
    def __init__(self, pool: asyncpg.Pool, *, graph_name: str) -> None:
        self._pool = pool
        self._graph = graph_name

    async def open(self, question: Question) -> Question:
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                f"""
                INSERT INTO {QUESTION_TABLE}
                    (id, graph, family_id, category, question, evidence, state, thread,
                     asked_by)
                VALUES ($1, $2, $3, $4, $5, $6::jsonb, $7, $8::jsonb, $9)
             RETURNING *
                """,
                question.id,
                self._graph,
                question.family_id,
                question.category,
                question.question,
                json.dumps(dict(question.evidence)),
                question.state.value,
                json.dumps(list(question.thread)),
                question.asked_by,
            )
        return _from_row(row)

    async def get(self, question_id: str) -> Question | None:
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                f"SELECT * FROM {QUESTION_TABLE} WHERE graph = $1 AND id = $2",
                self._graph,
                question_id,
            )
        return _from_row(row) if row else None

    async def for_family(self, family_id: str) -> list[Question]:
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(
                f"SELECT * FROM {QUESTION_TABLE} WHERE graph = $1 AND family_id = $2 "
                f"ORDER BY asked_at",
                self._graph,
                family_id,
            )
        return [_from_row(row) for row in rows]

    async def count_open(self, family_id: str) -> int:
        async with self._pool.acquire() as conn:
            count = await conn.fetchval(
                f"SELECT count(*) FROM {QUESTION_TABLE} "
                f"WHERE graph = $1 AND family_id = $2 AND state = 'OPEN'",
                self._graph,
                family_id,
            )
        return int(count)

    async def append_message(self, question_id: str, message: dict[str, Any]) -> Question | None:
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                f"""
                UPDATE {QUESTION_TABLE}
                   SET thread = thread || $3::jsonb
                 WHERE graph = $1 AND id = $2
             RETURNING *
                """,
                self._graph,
                question_id,
                json.dumps([message]),
            )
        return _from_row(row) if row else None

    async def answer(self, question_id: str, *, answered_by: str) -> Question | None:
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                f"""
                UPDATE {QUESTION_TABLE}
                   SET state = 'ANSWERED', answered_by = $3, answered_at = now()
                 WHERE graph = $1 AND id = $2 AND state = 'OPEN'
             RETURNING *
                """,
                self._graph,
                question_id,
                answered_by,
            )
        return _from_row(row) if row else None


# ------------------------------------------------------------------------- the workflow


async def seed_questions(
    store: QuestionStore, family_id: str, design_document: Mapping[str, Any], *, principal: Principal
) -> list[Question]:
    """Promote a frozen design's own open questions into tracked, answerable rows.

    Called once, by `model_lifecycle.submit_for_review`'s own route — see that story's
    (S4.1.2) and this one's own module docstrings. Re-submitting after request-changes
    (a new review cycle) seeds again from whatever the design says *now*; a question a
    previous cycle already answered is not re-asked because the design's own
    `open_questions` list only ever names what is unresolved as of generation.
    """
    seeded: list[Question] = []
    for entry in design_document.get("open_questions") or []:
        question = Question(
            id=new_ulid(),
            family_id=family_id,
            category=str(entry.get("category") or "general"),
            question=str(entry.get("question") or ""),
            state=QuestionState.OPEN,
            asked_by=principal.value,
            evidence=dict(entry.get("evidence") or {}),
        )
        seeded.append(await store.open(question))
    return seeded


async def list_questions(store: QuestionStore, family_id: str) -> list[Question]:
    return await store.for_family(family_id)


async def ask_question(
    store: QuestionStore,
    family_id: str,
    *,
    category: str,
    question: str,
    principal: Principal,
) -> Question:
    cleaned = question.strip()
    if len(cleaned) < MIN_QUESTION_LENGTH:
        raise InvalidRequestError(
            f"a question needs at least {MIN_QUESTION_LENGTH} characters"
        )
    record = Question(
        id=new_ulid(),
        family_id=family_id,
        category=category.strip() or "general",
        question=cleaned,
        state=QuestionState.OPEN,
        asked_by=principal.value,
    )
    opened = await store.open(record)
    logger.info("G2 question asked on family %s by %s", family_id, principal.value)
    return opened


async def reply_to_question(
    store: QuestionStore, question_id: str, *, message: str, principal: Principal
) -> Question:
    cleaned = message.strip()
    if len(cleaned) < MIN_MESSAGE_LENGTH:
        raise InvalidRequestError(f"a reply needs at least {MIN_MESSAGE_LENGTH} characters")
    updated = await store.append_message(
        question_id,
        {"from": principal.value, "text": cleaned, "at": _now()},
    )
    if updated is None:
        raise ElementNotFoundError(f"no G2 question '{question_id}'")
    return updated


async def answer_question(store: QuestionStore, question_id: str, *, principal: Principal) -> Question:
    """Mark a question resolved. The asker's own judgement that it is closed — a reply
    alone does not resolve a question, because a reply may just be a clarifying question
    back (§15.2's "every action is a record": marking answered is its own deliberate act,
    not inferred from the last message existing)."""
    updated = await store.answer(question_id, answered_by=principal.value)
    if updated is None:
        existing = await store.get(question_id)
        if existing is None:
            raise ElementNotFoundError(f"no G2 question '{question_id}'")
        raise InvalidRequestError(f"question '{question_id}' is already {existing.state.value}")
    return updated


def check_domain_scope(family_domain: str | None, domain_scope: frozenset[str]) -> None:
    if family_domain is None:
        # A real, disclosed gap: nothing assigns a domain automatically yet (ADR 0030).
        # Refusing every approval until every family has one would make this criterion
        # impossible to satisfy in practice; requiring the role (checked separately, at
        # the route) without a domain to check against is the honest floor today.
        return
    if family_domain.strip().lower() not in domain_scope:
        raise ForbiddenError(
            f"family domain {family_domain!r} is not in the caller's asserted domain "
            f"scope; declare it in X-Astra-Domain-Scope"
        )


async def _family_properties(pool: asyncpg.Pool, graph_name: str, family_id: str) -> dict[str, Any]:
    async with pool.acquire() as conn:
        hydrated = await hydrate(conn, graph_name, "ModelFamily", [family_id])
    properties = hydrated.get(family_id)
    if properties is None:
        raise ElementNotFoundError(f"no ModelFamily '{family_id}'")
    return properties


async def approve(
    pool: asyncpg.Pool,
    graph_name: str,
    writer: GraphWriter,
    store: QuestionStore,
    family_id: str,
    *,
    principal: Principal,
    domain_scope: frozenset[str],
    countersigned_by: str,
    rationale: str,
) -> dict[str, Any]:
    """`IN_REVIEW` -> `APPROVED`. Requires: no open question, a frozen version, and a
    named countersigner — §13.1's own "approver: client data owner; countersigned by
    Semantic Model Engineer" pair, both recorded on the `GateDecision`."""
    properties = await _family_properties(pool, graph_name, family_id)
    require_transition(properties.get("state"), "APPROVED")
    check_domain_scope(properties.get("domain"), domain_scope)

    open_count = await store.count_open(family_id)
    if open_count > 0:
        raise InvalidRequestError(
            f"{open_count} open question(s) must be answered before this design can be "
            f"approved — 'the design cannot be approved with an unanswered question'"
        )

    document = await read_design_document(pool, graph_name, family_id)
    version = document.get("version")
    if not version:
        raise InvalidRequestError(
            f"family '{family_id}' has not been submitted for G2 review — no frozen version"
        )

    countersigner = countersigned_by.strip()
    if not countersigner:
        raise InvalidRequestError(
            "an approval needs the Semantic Model Engineer who countersigns it"
        )
    cleaned_rationale = rationale.strip()
    if len(cleaned_rationale) < MIN_RATIONALE_LENGTH:
        raise InvalidRequestError(
            f"an approval needs a rationale of at least {MIN_RATIONALE_LENGTH} characters"
        )

    decision_id = new_ulid()
    await writer.write_nodes(
        [
            NodeWrite(
                type="GateDecision",
                id=decision_id,
                properties={
                    "gate": "G2",
                    "subject_ref": family_id,
                    "decision": "APPROVED",
                    "approver": principal.value,
                    "approver_role": "client_data_owner",
                    "countersigner": countersigner,
                    "countersigner_role": "semantic_model_engineer",
                    "version_hash": version,
                    "evidence_ref": document["semantic_model_id"],
                    "rationale": cleaned_rationale,
                    "timestamp": _now(),
                },
            )
        ],
        principal=principal,
    )
    await writer.upsert_nodes(
        [
            NodeWrite(
                type="ModelFamily",
                id=family_id,
                properties={**_writable_node_properties(properties), "state": "APPROVED"},
            )
        ],
        principal=principal,
    )
    logger.info(
        "family %s approved at G2 by %s, countersigned by %s (version %s)",
        family_id,
        principal.value,
        countersigner,
        version,
    )
    return {"gate_decision_id": decision_id, "family_id": family_id, "state": "APPROVED", "version": version}


async def request_changes(
    pool: asyncpg.Pool,
    graph_name: str,
    writer: GraphWriter,
    family_id: str,
    *,
    principal: Principal,
    domain_scope: frozenset[str],
    comment: str,
) -> dict[str, Any]:
    """`IN_REVIEW` -> `DRAFT`, with the comment attached to a `GateDecision` and the
    family's `g2_cycle_count` incremented — story S4.2.1's own "the cycle count is
    stored"."""
    properties = await _family_properties(pool, graph_name, family_id)
    require_transition(properties.get("state"), "DRAFT")
    check_domain_scope(properties.get("domain"), domain_scope)

    cleaned_comment = comment.strip()
    if len(cleaned_comment) < MIN_RATIONALE_LENGTH:
        raise InvalidRequestError(
            f"requesting changes needs a comment of at least {MIN_RATIONALE_LENGTH} characters"
        )

    document = await read_design_document(pool, graph_name, family_id)
    version = document.get("version")

    decision_id = new_ulid()
    await writer.write_nodes(
        [
            NodeWrite(
                type="GateDecision",
                id=decision_id,
                properties={
                    "gate": "G2",
                    "subject_ref": family_id,
                    "decision": "CHANGES_REQUESTED",
                    "approver": principal.value,
                    "approver_role": "client_data_owner",
                    "version_hash": version,
                    "evidence_ref": document.get("semantic_model_id"),
                    "rationale": cleaned_comment,
                    "timestamp": _now(),
                },
            )
        ],
        principal=principal,
    )

    cycle_count = int(properties.get("g2_cycle_count") or 0) + 1
    await writer.upsert_nodes(
        [
            NodeWrite(
                type="ModelFamily",
                id=family_id,
                properties={
                    **_writable_node_properties(properties),
                    "state": "DRAFT",
                    "g2_cycle_count": cycle_count,
                },
            )
        ],
        principal=principal,
    )
    logger.info(
        "family %s sent back to DRAFT by %s (cycle %d): %s",
        family_id,
        principal.value,
        cycle_count,
        cleaned_comment,
    )
    return {
        "gate_decision_id": decision_id,
        "family_id": family_id,
        "state": "DRAFT",
        "g2_cycle_count": cycle_count,
    }


# ---------------------------------------------------------------- the client-facing view


def plain_language_summary(document: Mapping[str, Any]) -> str:
    """"What changes for the business user" (S4.2.1's own words) — a deterministic
    rendering of the structural facts a business user actually feels: how many tables and
    measures, whether rows are restricted per person, and how fresh the data is. Not an
    ASSISTED draft (§8.2) — there is no judgement call here a model could get wrong, only
    facts the design already states, so a template is the honest, reproducible choice
    (the same reasoning `modeller.draft_grain_statement` gives for its own sentence)."""
    tables = document.get("tables") or []
    measures = document.get("candidate_measures") or []
    rls = document.get("rls_roles") or []
    refresh = document.get("refresh_policy") or {}

    parts = [
        f"This model brings together {len(tables)} "
        f"table{'s' if len(tables) != 1 else ''} into "
        f"{len(measures)} measure{'s' if len(measures) != 1 else ''}."
    ]
    if rls:
        parts.append(
            f"Row-level security is applied ({len(rls)} "
            f"role{'s' if len(rls) != 1 else ''}), so each person sees only the rows "
            f"they are entitled to."
        )
    else:
        parts.append("No row-level security is applied — everyone with access sees every row.")

    mode = refresh.get("mode")
    if mode == "scheduled":
        parts.append(f"Data refreshes on a {refresh.get('schedule') or 'configured'} schedule.")
    elif mode == "directquery":
        parts.append("Data is queried live from the source — there is no refresh schedule.")
    elif mode == "mixed":
        parts.append("Some data refreshes on a schedule; some is queried live.")
    else:
        parts.append("The refresh approach has not been determined yet.")
    return " ".join(parts)


async def client_proposal_view(
    pool: asyncpg.Pool, graph_name: str, store: QuestionStore, family_id: str
) -> dict[str, Any]:
    """Everything the "Model Proposal (client view)" screen renders (S4.2.1's own list):
    what the model is, what reports use it, what changes for the business user, and open
    questions with owner and status. Deliberately a *different* shaped read from
    `modeller.read_design_document` (which is Model Detail's, the Artizent-internal
    screen) — §15.2's "client surfaces are calm" means a different document, not the same
    one with a role check bolted on.
    """
    family = await get_family(pool, graph_name, family_id)
    if family is None:
        raise ElementNotFoundError(f"no ModelFamily '{family_id}'")
    document = await read_design_document(pool, graph_name, family_id)

    member_ids = list(family.get("members") or [])
    async with pool.acquire() as conn:
        workbooks = await hydrate(conn, graph_name, "Workbook", member_ids) if member_ids else {}
    reports = sorted(
        (str(props.get("name") or wb_id) for wb_id, props in workbooks.items()),
    )

    questions = await store.for_family(family_id)

    return {
        "family_id": family_id,
        "name": family.get("name"),
        "domain": family.get("domain"),
        "state": family.get("state"),
        "grain_statement": document.get("grain_statement"),
        "plain_summary": plain_language_summary(document),
        "reports": reports,
        "version": document.get("version"),
        "open_questions": [
            {**q.as_dict(), "owner": q.asked_by, "status": q.state.value} for q in questions
        ],
        "unanswered_count": sum(1 for q in questions if q.state is QuestionState.OPEN),
    }


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _iso(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, str):
        return value
    return str(value.isoformat())


def _from_row(row: asyncpg.Record) -> Question:
    evidence = row["evidence"]
    thread = row["thread"]
    return Question(
        id=row["id"],
        family_id=row["family_id"],
        category=row["category"],
        question=row["question"],
        state=QuestionState(row["state"]),
        asked_by=row["asked_by"],
        evidence=json.loads(evidence) if isinstance(evidence, str) else dict(evidence),
        thread=tuple(json.loads(thread) if isinstance(thread, str) else list(thread)),
        asked_at=_iso(row["asked_at"]),
        answered_by=row["answered_by"],
        answered_at=_iso(row["answered_at"]),
    )


__all__ = [
    "MIN_MESSAGE_LENGTH",
    "MIN_QUESTION_LENGTH",
    "MIN_RATIONALE_LENGTH",
    "PostgresQuestionStore",
    "Question",
    "QuestionState",
    "QuestionStore",
    "answer_question",
    "approve",
    "ask_question",
    "check_domain_scope",
    "client_proposal_view",
    "list_questions",
    "plain_language_summary",
    "reply_to_question",
    "request_changes",
    "seed_questions",
]
