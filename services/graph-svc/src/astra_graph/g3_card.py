"""The G3 gate card -- story S9.1.1, opening F9.1, opening E9 (Release and Decommission).

    "As a report owner, I want a gate card that tells me in 30 seconds what I am
    approving, so that acceptance is informed and quick.

    Acceptance criteria:
    - Card anatomy per §15.5: what (report, pages, visuals), proof (cases, charter
      version, sampled flag, waivers), visual (structural score, human review status),
      changes (C4 decisions, redesigns), next (promotion, parallel window), buttons
      Approve / Request changes / Ask a question / Open report
    - Approve requires the report owner role for that report; countersigned by the
      Migration Engineer; both recorded
    - A PASSED (waiver) MU shows the waivers and their justification on the card;
      approving records that the owner saw them
    - Card renders identically on desktop, mobile and as a Teams adaptive card"

§13.1's own G3 row, verbatim: "Migration Unit | Client report owner; countersigned by
Migration Engineer | Passing ParityRun (or waived cases with justification); visual
review record | Invoice trigger; release permitted." §13.3's own worked example gives
the real `GateDecision` shape this module writes.

**No real Migration Unit exists (confirmed, again, directly against `migration_units.py`
-- its own docstring says plainly "this is a port, not an implementation").** Every
prior G3-adjacent story has used the workbook id as the real MU proxy
(`ExceptionCase.mu_ref`, ADR 0060's own finding) -- this module does the identical
thing: `GateDecision(gate="G3", subject_ref=<workbook_id>)`, the same subject a live
`ExceptionCase` already names.

**Approve/Request changes reuse `g2.approve`/`.request_changes`'s own proven shape --
approver + countersigner, both recorded on one `GateDecision`, no new enum values
needed.** `GateDecision.decision` already legally allows `APPROVED`/`CHANGES_REQUESTED`
(declared since S4.2.1); this story writes them for a G3 subject for the first time,
the identical "first real write of an already-legal value" footing S8.3.1's own
`AgentMode.HUMAN` had. Approver role is `client_report_owner` (this AC's own persona);
countersigner is a plain, unverified name string, the identical "the approver types who
countersigned, not a second authenticated action" convention `g2.approve`'s own
`countersigned_by` parameter already established -- confirmed by direct read, not
invented for this story.

**"Approving records that the owner saw them" is a real, frozen JSON snapshot of the
card's own rendered anatomy at approval time, stored as an artefact and named by the
decision's own `evidence_ref`.** The waivers (or their honest absence) the owner saw are
embedded in that snapshot -- a real fact about what was shown, not a separate boolean
that could drift from what the card actually rendered. The identical "evidence_ref
points at a real stored JSON artefact" shape `Verdict.evidence_ref` already has (S7.4.1).

**"Waivers and their justification" is a real, live `GateDecision(decision="WAIVED")`
query, honestly empty today.** Confirmed, again, directly: no story has ever written
one (`parity_dashboard.py`'s own identical finding, S7.4.2) -- `exception_desk.py`
(S8.3.1) writes `PATCHED`/`REDESIGN`/`MODEL_DEFECT`/`SOURCE_DEFECT`, never `WAIVED`.
This module reads the same real, disclosed-absent-until-driven fact, not a fabricated
one; `tolerance_rules.WaiverRule` (`allowed_classes`, `requires`,
`justification_min_chars`) is real policy config a future waiver-recording story would
validate against, still entirely unused today.

**"Visual... human review status" is a new, disclosed-absent property, `Visual.
reviewed_by`/`.reviewed_at` -- see `nodes.py`'s own `SpecDeviation` for the full
reasoning.** No action in this codebase writes it yet.

**"Changes (C4 decisions, redesigns)" reads two real, distinct facts, both scoped to
this workbook's own live `ExceptionCase`s.** "Redesigns" is `GateDecision(gate="G3",
decision="REDESIGN")` rows whose `subject_ref` names one of this workbook's own cases --
S8.3.1's own Exception Desk decision. "C4 decisions" is the real `CalculatedField.
redesign_decision` (+ reason/by/at) flag on whichever calculated field each such case's
own `artefact_ref` resolves to (`mender._resolve_calculated_field`, reused verbatim) --
S5.4.1's own disclosed MU-BLOCKED proxy (`redesign.py`). Scoped to calc fields with a
live case pointing at them, not every calculated field the workbook has ever had --
a real, disclosed narrowing, not a silent one.

**"What (report, pages, visuals)" reads the real `ReportDefinition` this workbook's own
`mu_ref` names, plus every real `Visual` naming one of its own `pages`.** A workbook
with no `ReportDefinition` yet (never composed) shows `pages`/`visuals` honestly as
zero, not an error -- composition (S6.1.1) can genuinely not have happened yet.

**"Next (promotion, parallel window)" is informational text, not an executed
pipeline.** E9's own goal names it plainly: "accepted reports are promoted through the
*client's* pipeline" -- promotion is the client's own deployment mechanism, external to
this codebase, the same way `migration_units.py` already disclosed the whole §3.2 state
machine "belongs to the control plane." Approving this card never triggers a real
promotion; it records a real `GateDecision` and states, as prose, what §14.4's own
literal default (`DEFAULT_PARALLEL_WINDOW_WEEKS = 4`, "default 4 weeks, configurable")
says happens next.

**"Ask a question" is a new, minimal platform table (`public.g3_question`,
`v0032_g3_questions.py`), the identical "not an estate-graph node" footing `g2_question`
(S4.2.1) already has -- deliberately without `g2_question`'s own thread/answer columns,
since this story's own AC names one button, asking, not a resolution workflow. A future
story can widen it the identical additive way nothing has ever needed to widen
`g2_question` since it was built.**

**"Open report" links to the Parity Dashboard for this workbook -- the closest real
screen that exists.** No dedicated "view the deployed report" screen exists anywhere in
this codebase (confirmed); the identical "no MU page exists, point at the closest real
thing" posture ADR 0048 already took for a redesign case's own MU link.

**Story S9.1.2, closing F9.1: `approve` now also triggers `invoicing.record_acceptance`
-- "G3 acceptance... triggers the invoicing event under the fixed-price contract."**
Only `approve` calls it; `request_changes`/`ask_question` never do, since only a real
APPROVED decision is a real acceptance. See `invoicing.py`'s own docstring for the full
reasoning (tier resolution, unit prices, the commercial ledger, the double-invoice
guard).
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

import asyncpg

from .artefacts import ArtefactStore
from .errors import ElementNotFoundError, InvalidRequestError
from .foundry_routing import _family_for_workbook  # cross-epic private helper; see module docstring
from .graph.queries import NODE_INDEX_TABLE
from .ids import new_ulid
from .invoicing import UnitPriceStore, record_acceptance
from .lineage import hydrate
from .mender import _resolve_calculated_field  # cross-epic private helper; see module docstring
from .principal import Principal
from .scope import ScopeStore
from .writes import GraphWriter, NodeWrite

GATE = "G3"

#: "Approve and Request Changes require a reason of at least one sentence" (§15.5) --
#: the identical "a sentence is a fuller bar than a reason" footing `exception_desk.
#: MIN_RATIONALE_LENGTH` (S8.3.1) already set, reused verbatim rather than a fresh
#: smaller number.
MIN_RATIONALE_LENGTH = 20

#: §14.4's own literal default -- "a parallel-running window per site (default 4 weeks,
#: configurable)". Informational only; see module docstring.
DEFAULT_PARALLEL_WINDOW_WEEKS = 4

MIN_QUESTION_LENGTH = 5

QUESTION_TABLE = "public.g3_question"


class G3CardError(Exception):
    """A G3 card could not be read or a decision could not be recorded as asked."""


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")


async def _report_definition_for_workbook(
    pool: asyncpg.Pool, graph_name: str, workbook_id: str,
) -> dict[str, Any] | None:
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            f"""SELECT id FROM {NODE_INDEX_TABLE}
                 WHERE graph = $1 AND kind = 'node' AND label = 'ReportDefinition' AND retired_at IS NULL""",
            graph_name,
        )
        reports = await hydrate(conn, graph_name, "ReportDefinition", [row["id"] for row in rows])
    for report_id, properties in reports.items():
        if properties.get("mu_ref") == workbook_id:
            return {"id": report_id, **properties}
    return None


async def _visuals_for_pages(pool: asyncpg.Pool, graph_name: str, pages: tuple[str, ...]) -> list[dict[str, Any]]:
    if not pages:
        return []
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            f"""SELECT id FROM {NODE_INDEX_TABLE}
                 WHERE graph = $1 AND kind = 'node' AND label = 'Visual' AND retired_at IS NULL""",
            graph_name,
        )
        visuals = await hydrate(conn, graph_name, "Visual", [row["id"] for row in rows])
    page_set = set(pages)
    return [{"id": vid, **props} for vid, props in visuals.items() if props.get("page") in page_set]


async def _live_gate_decisions(pool: asyncpg.Pool, graph_name: str) -> dict[str, dict[str, Any]]:
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            f"""SELECT id FROM {NODE_INDEX_TABLE}
                 WHERE graph = $1 AND kind = 'node' AND label = 'GateDecision' AND retired_at IS NULL""",
            graph_name,
        )
        return await hydrate(conn, graph_name, "GateDecision", [row["id"] for row in rows])


async def _workbook_exception_cases(
    pool: asyncpg.Pool, graph_name: str, workbook_id: str,
) -> dict[str, dict[str, Any]]:
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            f"""SELECT id FROM {NODE_INDEX_TABLE}
                 WHERE graph = $1 AND kind = 'node' AND label = 'ExceptionCase' AND retired_at IS NULL""",
            graph_name,
        )
        cases = await hydrate(conn, graph_name, "ExceptionCase", [row["id"] for row in rows])
    return {cid: props for cid, props in cases.items() if props.get("mu_ref") == workbook_id}


async def _proof(pool: asyncpg.Pool, graph_name: str, workbook_id: str) -> dict[str, Any]:
    """"proof (cases, charter version, sampled flag, waivers)" -- reads the real Parity
    Dashboard (S7.4.2) and the real latest ParityRun (S7.4.1) rather than recomputing
    either."""
    from .parity_dashboard import parity_dashboard
    from .verdicts import latest_parity_run

    dashboard = await parity_dashboard(pool, graph_name, workbook_id=workbook_id)
    if dashboard is None:
        return {
            "cases_run": 0, "cases_pass": 0, "charter_version": None,
            "sampled": False, "passes_the_charter": False, "waivers": [],
        }

    run = await latest_parity_run(pool, graph_name, workbook_id=workbook_id)
    verdicts = (run or {}).get("verdicts") or []
    sampled = any(bool(v.get("sampled")) for v in verdicts)

    cases_run = sum(int(sheet.get("cases_run") or 0) for sheet in dashboard["sheets"])
    cases_pass = sum(int(sheet.get("pass") or 0) for sheet in dashboard["sheets"])

    cases = await _workbook_exception_cases(pool, graph_name, workbook_id)
    case_refs: set[str] = set()
    for props in cases.values():
        case_refs.update(props.get("case_refs") or ())
    decisions = await _live_gate_decisions(pool, graph_name)
    waivers = [
        {
            "subject_ref": props.get("subject_ref"), "approver": props.get("approver"),
            "rationale": props.get("rationale"), "timestamp": props.get("timestamp"),
        }
        for props in decisions.values()
        if props.get("decision") == "WAIVED" and props.get("subject_ref") in case_refs
    ]

    return {
        "cases_run": cases_run, "cases_pass": cases_pass,
        "charter_version": dashboard["charter_version"], "sampled": sampled,
        "passes_the_charter": dashboard["passes_the_charter"], "waivers": waivers,
    }


def _visual_summary(visuals: list[dict[str, Any]]) -> dict[str, Any]:
    structural = [v["structural_score"] for v in visuals if v.get("structural_score") is not None]
    image = [v["image_score"] for v in visuals if v.get("image_score") is not None]
    reviewed = [
        {"visual_id": v["id"], "reviewed_by": v.get("reviewed_by"), "reviewed_at": v.get("reviewed_at")}
        for v in visuals if v.get("reviewed_by")
    ]
    return {
        "structural_score": (sum(structural) / len(structural)) if structural else None,
        "image_score": (sum(image) / len(image)) if image else None,
        "reviewed": reviewed,
        "human_review_status": "reviewed" if reviewed else "not yet reviewed",
    }


async def _changes(pool: asyncpg.Pool, graph_name: str, workbook_id: str) -> dict[str, Any]:
    cases = await _workbook_exception_cases(pool, graph_name, workbook_id)
    decisions = await _live_gate_decisions(pool, graph_name)
    case_ids = set(cases)

    redesigns = [
        {
            "subject_ref": props.get("subject_ref"), "approver": props.get("approver"),
            "rationale": props.get("rationale"), "timestamp": props.get("timestamp"),
        }
        for props in decisions.values()
        if props.get("decision") == "REDESIGN" and props.get("subject_ref") in case_ids
    ]

    c4_decisions: list[dict[str, Any]] = []
    for props in cases.values():
        artefact_ref = props.get("artefact_ref")
        if not artefact_ref:
            continue
        resolved = await _resolve_calculated_field(pool, graph_name, artefact_ref)
        if resolved is None:
            continue
        calc_id, calc_properties = resolved
        if calc_properties.get("redesign_decision"):
            c4_decisions.append({
                "calc_id": calc_id, "name": calc_properties.get("name"),
                "redesign_decision": calc_properties.get("redesign_decision"),
                "redesign_decision_reason": calc_properties.get("redesign_decision_reason"),
                "redesign_decision_by": calc_properties.get("redesign_decision_by"),
                "redesign_decision_at": calc_properties.get("redesign_decision_at"),
            })

    family_id = await _family_for_workbook(pool, graph_name, workbook_id)
    model: dict[str, Any] | None = None
    if family_id is not None:
        async with pool.acquire() as conn:
            family = (await hydrate(conn, graph_name, "ModelFamily", [family_id])).get(family_id)
        approval = next(
            (
                props for props in decisions.values()
                if props.get("gate") == "G2" and props.get("decision") == "APPROVED"
                and props.get("subject_ref") == family_id
            ),
            None,
        )
        model = {
            "family_id": family_id, "name": (family or {}).get("name"), "state": (family or {}).get("state"),
            "approved_at": approval.get("timestamp") if approval else None,
        }

    return {"c4_decisions": c4_decisions, "redesigns": redesigns, "model": model}


async def g3_card(pool: asyncpg.Pool, graph_name: str, *, workbook_id: str) -> dict[str, Any]:
    """The AC's own card anatomy, assembled from real graph facts alone -- see this
    module's own docstring for how each section is resolved."""
    async with pool.acquire() as conn:
        workbook = (await hydrate(conn, graph_name, "Workbook", [workbook_id])).get(workbook_id)
    if workbook is None:
        raise ElementNotFoundError(f"no Workbook '{workbook_id}'")

    report = await _report_definition_for_workbook(pool, graph_name, workbook_id)
    pages = tuple(report.get("pages") or ()) if report else ()
    visuals = await _visuals_for_pages(pool, graph_name, pages)

    proof = await _proof(pool, graph_name, workbook_id)
    visual_summary = _visual_summary(visuals)
    changes = await _changes(pool, graph_name, workbook_id)

    decisions = await _live_gate_decisions(pool, graph_name)
    latest_decision = max(
        (
            props for props in decisions.values()
            if props.get("gate") == GATE and props.get("subject_ref") == workbook_id
        ),
        key=lambda p: str(p.get("timestamp") or ""),
        default=None,
    )

    return {
        "workbook_id": workbook_id,
        "what": {
            "name": workbook.get("name"), "site": workbook.get("site"),
            "pages": len(pages), "visuals": len(visuals),
        },
        "proof": proof,
        "visual": visual_summary,
        "changes": changes,
        "next": {
            "on_approval": "promote to test, then a parallel run before your sign-off "
                            "(§14.4) -- run through the client's own deployment pipeline, "
                            "not executed by this platform",
            "parallel_window_weeks": DEFAULT_PARALLEL_WINDOW_WEEKS,
        },
        "latest_decision": (
            {
                "decision": latest_decision.get("decision"), "approver": latest_decision.get("approver"),
                "countersigner": latest_decision.get("countersigner"),
                "timestamp": latest_decision.get("timestamp"), "rationale": latest_decision.get("rationale"),
            }
            if latest_decision else None
        ),
    }


def to_adaptive_card(card: dict[str, Any]) -> dict[str, Any]:
    """A real Adaptive Card 1.5 JSON document carrying the identical anatomy the console
    renders -- "mirrored into Teams as an adaptive card" (§15.5). This platform has no
    live Teams bot/webhook (confirmed, no such integration exists anywhere in this
    codebase) -- this is a real, schema-correct export a future integration can post
    as-is, the same "build the real, disclosed output even with nothing to consume it
    yet" posture S5.3.3's own calibration report already took."""
    what, proof, visual, changes, nxt = card["what"], card["proof"], card["visual"], card["changes"], card["next"]
    waiver_count = len(proof["waivers"])
    facts = [
        {"title": "What", "value": f"{what['name']} -- {what['pages']} page(s), {what['visuals']} visual(s)"},
        {
            "title": "Proof",
            "value": f"{proof['cases_pass']}/{proof['cases_run']} parity cases PASS -- "
                     f"charter {proof['charter_version']}"
                     f"{' -- sampled' if proof['sampled'] else ' -- full compare'}"
                     f"{f' -- {waiver_count} waiver(s)' if waiver_count else ''}",
        },
        {
            "title": "Visual",
            "value": (
                f"structural {visual['structural_score']:.2f}" if visual["structural_score"] is not None
                else "not yet scored"
            ) + f" -- {visual['human_review_status']}",
        },
        {
            "title": "Changes",
            "value": f"{len(changes['c4_decisions'])} C4 decision(s), {len(changes['redesigns'])} redesign(s)",
        },
        {"title": "Next", "value": f"{nxt['on_approval']} ({nxt['parallel_window_weeks']} weeks)"},
    ]
    return {
        "type": "AdaptiveCard",
        "$schema": "http://adaptivecards.io/schemas/adaptive-card.json",
        "version": "1.5",
        "body": [
            {"type": "TextBlock", "text": f"G3 · Parity acceptance · {what['name']}", "weight": "Bolder", "size": "Medium"},
            {"type": "FactSet", "facts": facts},
        ],
        "actions": [
            {"type": "Action.Submit", "title": "Approve", "data": {"action": "approve"}},
            {"type": "Action.Submit", "title": "Request changes", "data": {"action": "request_changes"}},
            {"type": "Action.Submit", "title": "Ask a question", "data": {"action": "ask_question"}},
            {"type": "Action.OpenUrl", "title": "Open report", "url": f"/parity?workbook={card['workbook_id']}"},
        ],
    }


def _clean_rationale(rationale: str) -> str:
    cleaned = rationale.strip()
    if len(cleaned) < MIN_RATIONALE_LENGTH:
        raise InvalidRequestError(
            f"a G3 decision needs a rationale of at least {MIN_RATIONALE_LENGTH} characters "
            f"-- a real sentence, not a placeholder"
        )
    return cleaned


async def _write_gate_decision(
    writer: GraphWriter, *, workbook_id: str, decision: str, rationale: str,
    approver_role: str, countersigner: str | None, countersigner_role: str | None,
    evidence_ref: str | None, principal: Principal,
) -> str:
    decision_id = new_ulid()
    await writer.write_nodes(
        [
            NodeWrite(
                type="GateDecision",
                id=decision_id,
                properties={
                    "gate": GATE, "subject_ref": workbook_id, "decision": decision,
                    "approver": principal.value, "approver_role": approver_role,
                    "countersigner": countersigner, "countersigner_role": countersigner_role,
                    "rationale": rationale, "evidence_ref": evidence_ref, "timestamp": _now(),
                },
            )
        ],
        principal=principal,
    )
    return decision_id


async def _snapshot_card(
    artefact_store: ArtefactStore, card: dict[str, Any], *, workbook_id: str, principal: Principal,
) -> str:
    """"Approving records that the owner saw them" -- a frozen JSON snapshot of the card
    the owner actually approved against, including whichever waivers it showed."""
    record = await artefact_store.store(
        kind="g3_card_snapshot", mu_ref=workbook_id, case_id=workbook_id,
        content=json.dumps(card).encode("utf-8"), media_type="application/json",
        created_by=principal.value,
    )
    return record.id


@dataclass(frozen=True, slots=True)
class G3DecisionResult:
    workbook_id: str
    gate_decision_id: str
    decision: str
    invoiced: bool = False
    tier: str | None = None
    unit_price: float | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "workbook_id": self.workbook_id, "gate_decision_id": self.gate_decision_id,
            "decision": self.decision, "invoiced": self.invoiced, "tier": self.tier,
            "unit_price": self.unit_price,
        }


async def approve(
    pool: asyncpg.Pool, graph_name: str, writer: GraphWriter, artefact_store: ArtefactStore,
    scope_store: ScopeStore, unit_price_store: UnitPriceStore, *,
    workbook_id: str, rationale: str, countersigned_by: str, principal: Principal,
) -> G3DecisionResult:
    """"G3 acceptance" is the AC's own literal trigger for `invoicing.record_acceptance`
    (S9.1.2) -- called only from here, only on a real APPROVED decision, never on
    Request changes or Ask a question. See that module's own docstring for why a
    workbook with no real tier is honestly not invoiced, and why re-approving an
    already-accepted workbook never double-invoices it."""
    cleaned_rationale = _clean_rationale(rationale)
    cleaned_countersigner = countersigned_by.strip()
    if not cleaned_countersigner:
        raise InvalidRequestError("approving G3 needs a countersigning Migration Engineer's name")

    card = await g3_card(pool, graph_name, workbook_id=workbook_id)
    snapshot_id = await _snapshot_card(artefact_store, card, workbook_id=workbook_id, principal=principal)
    decision_id = await _write_gate_decision(
        writer, workbook_id=workbook_id, decision="APPROVED", rationale=cleaned_rationale,
        approver_role="client_report_owner", countersigner=cleaned_countersigner,
        countersigner_role="migration_engineer", evidence_ref=snapshot_id, principal=principal,
    )
    ledger_entry = await record_acceptance(
        pool, graph_name, writer, scope_store, unit_price_store,
        workbook_id=workbook_id, gate_decision_id=decision_id, principal=principal,
    )
    return G3DecisionResult(
        workbook_id=workbook_id, gate_decision_id=decision_id, decision="APPROVED",
        invoiced=ledger_entry is not None,
        tier=ledger_entry.tier if ledger_entry else None,
        unit_price=ledger_entry.unit_price if ledger_entry else None,
    )


async def request_changes(
    pool: asyncpg.Pool, graph_name: str, writer: GraphWriter, *,
    workbook_id: str, rationale: str, principal: Principal,
) -> G3DecisionResult:
    cleaned_rationale = _clean_rationale(rationale)
    decision_id = await _write_gate_decision(
        writer, workbook_id=workbook_id, decision="CHANGES_REQUESTED", rationale=cleaned_rationale,
        approver_role="client_report_owner", countersigner=None, countersigner_role=None,
        evidence_ref=None, principal=principal,
    )
    return G3DecisionResult(workbook_id=workbook_id, gate_decision_id=decision_id, decision="CHANGES_REQUESTED")


@dataclass(frozen=True, slots=True)
class G3Question:
    id: str
    workbook_id: str
    question: str
    asked_by: str
    asked_at: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "id": self.id, "workbook_id": self.workbook_id, "question": self.question,
            "asked_by": self.asked_by, "asked_at": self.asked_at,
        }


async def ask_question(
    pool: asyncpg.Pool, graph_name: str, *, workbook_id: str, question: str, principal: Principal,
) -> G3Question:
    cleaned = question.strip()
    if len(cleaned) < MIN_QUESTION_LENGTH:
        raise InvalidRequestError(f"a question needs at least {MIN_QUESTION_LENGTH} characters")
    question_id = new_ulid()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            f"""INSERT INTO {QUESTION_TABLE} (id, graph, workbook_id, question, asked_by)
                 VALUES ($1, $2, $3, $4, $5)
                 RETURNING id, workbook_id, question, asked_by, asked_at""",
            question_id, graph_name, workbook_id, cleaned, principal.value,
        )
    return G3Question(
        id=str(row["id"]), workbook_id=str(row["workbook_id"]), question=str(row["question"]),
        asked_by=str(row["asked_by"]), asked_at=row["asked_at"].isoformat(),
    )


async def list_questions(pool: asyncpg.Pool, graph_name: str, *, workbook_id: str) -> list[G3Question]:
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            f"""SELECT id, workbook_id, question, asked_by, asked_at FROM {QUESTION_TABLE}
                 WHERE graph = $1 AND workbook_id = $2 ORDER BY asked_at""",
            graph_name, workbook_id,
        )
    return [
        G3Question(
            id=str(row["id"]), workbook_id=str(row["workbook_id"]), question=str(row["question"]),
            asked_by=str(row["asked_by"]), asked_at=row["asked_at"].isoformat(),
        )
        for row in rows
    ]


class G3CardService:
    """Binds the module-level functions to one pool/graph/writer/artefact store/scope
    store/unit price store -- the identical "pre-bound object on app.state" shape
    `ExceptionDeskService` already takes. `scope_store`/`unit_price_store` are S9.1.2's
    own addition, needed only by `approve` -- see `invoicing.py`'s own docstring."""

    def __init__(
        self, pool: asyncpg.Pool, *, graph_name: str, writer: GraphWriter, artefact_store: ArtefactStore,
        scope_store: ScopeStore, unit_price_store: UnitPriceStore,
    ) -> None:
        self._pool = pool
        self._graph = graph_name
        self._writer = writer
        self._artefact_store = artefact_store
        self._scope_store = scope_store
        self._unit_price_store = unit_price_store

    async def card(self, workbook_id: str) -> dict[str, Any]:
        return await g3_card(self._pool, self._graph, workbook_id=workbook_id)

    async def approve(self, workbook_id: str, *, rationale: str, countersigned_by: str, principal: Principal) -> G3DecisionResult:
        return await approve(
            self._pool, self._graph, self._writer, self._artefact_store, self._scope_store, self._unit_price_store,
            workbook_id=workbook_id, rationale=rationale, countersigned_by=countersigned_by, principal=principal,
        )

    async def request_changes(self, workbook_id: str, *, rationale: str, principal: Principal) -> G3DecisionResult:
        return await request_changes(
            self._pool, self._graph, self._writer,
            workbook_id=workbook_id, rationale=rationale, principal=principal,
        )

    async def ask_question(self, workbook_id: str, *, question: str, principal: Principal) -> G3Question:
        return await ask_question(self._pool, self._graph, workbook_id=workbook_id, question=question, principal=principal)

    async def questions(self, workbook_id: str) -> list[G3Question]:
        return await list_questions(self._pool, self._graph, workbook_id=workbook_id)


__all__ = [
    "DEFAULT_PARALLEL_WINDOW_WEEKS",
    "GATE",
    "MIN_QUESTION_LENGTH",
    "MIN_RATIONALE_LENGTH",
    "G3CardError",
    "G3CardService",
    "G3DecisionResult",
    "G3Question",
    "approve",
    "ask_question",
    "g3_card",
    "list_questions",
    "request_changes",
    "to_adaptive_card",
]
