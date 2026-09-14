"""The Gate Inbox -- story S10.4.1, opening F10.4 (§15.3.6's own Governance surface).

    "As a data owner, I want a Gate Inbox that shows only the requests waiting for me,
    so that I do my part in minutes and get out.

    Acceptance criteria:
    - Card stack of open gate requests for my role and domain, ordered by due date;
      each card is the §15.5 anatomy; filters by gate type and site
    - Approve / request changes / ask a question in place; an approval that needs a
      countersign shows who is next
    - Email and Teams notification on new request and at SLA thresholds, with a deep
      link"

§15.3.6's own row, verbatim: *"Gate Inbox (client default) | Card stack of open gate
requests for the user's role and domain, ordered by due date; each card in the §15.5
anatomy; filters by gate type and site. | Approve; request changes; reject; delegate;
ask a question."* This story's own AC narrows that action list to Approve/request
changes/ask a question — "reject" and "delegate" are named by §15.3.6 but absent from
this story's own AC and from every existing gate mechanism (`GateDecision.decision`'s
`REJECTED` value is declared in the ontology but never written anywhere in this
codebase; "delegate" has no mechanism anywhere) — real, disclosed gaps this story does
not close.

**"Data owner" is not one existing role — §15.1's own role table ties one distinct
client role to each of G2/G3/G4 (`client_data_owner`, `client_report_owner`, `client_
licence_admin`), and the console's own single-role "Acting as" identity model
(confirmed directly: `App.tsx`'s own docstring, "it sends a role a person picks")
means a caller only ever legitimately approves one gate type per session anyway.** So
the inbox is role-*dispatched*, not multi-role-merged: which of the three real
per-gate pending-list functions below actually run is decided by the caller's own
declared role, the identical dispatch `App.tsx`'s `LANDING_SURFACE` table already uses
to route a role to a screen. An Artizent role sees the union of all three, the
identical "reader is broader than the approver" shape `G3CardReaderDep`/`Decommission
TrackerReaderDep` already set for their own gates. **G1 (`client_analytics_lead`) is
out of scope for this story, disclosed rather than silently included or excluded** —
neither F10.4's own epic preamble nor S10.4.1's own AC names it, and G1 has exactly one
live subject (the platform-wide `"tolerance_charter"` singleton per `tolerance_charter.
SUBJECT_REF`), so "a card stack of open G1 requests" is structurally meaningless: there
is at most one G1 decision cycle in flight at a time.

**"My domain" is real and enforced only for G2 today.** `g2.check_domain_scope` checks
`ModelFamily.domain` against the caller's `X-Astra-Domain-Scope` header; `Workbook`
(G3's subject) and `Site` (G4's subject) carry no comparable `domain` property in the
ontology — domain is declared as a `ModelFamily`-only concept, confirmed by direct
research. This module filters G2 items by domain scope (mirroring `check_domain_scope`'s
own "an unset family domain is open to anyone" rule); G3/G4 items carry `domain: None`
honestly rather than a fabricated derivation through a workbook's own family.

**Only G2 has a real due-date/SLA concept — G3/G4 items are ordered by a real "waiting
since" timestamp instead, not a fabricated SLA.** `pending_g2_reviews` (`g2_reminders.
py`) already computes `days_waiting`/`breached` from the real event log; no equivalent
exists for G3 (no "entered review" event a Workbook fires) or G4 (readiness has no
"became ready at" timestamp anywhere). `_sort_key` below ranks a breached G2 item
first, then by real `days_waiting` where known, then by the real timestamp each item
does carry (a G3 report's own `created_at`; a G2 review's own `entered_review_at`) —
G4 items, which carry no comparable timestamp either, sort last within their tier
rather than being assigned an invented one.

**G3's own "pending" definition is a real, disclosed reading, not a spec-given
one — no explicit G3 review-state machine exists the way G2's `IN_REVIEW` does.** A
workbook is "awaiting G3 decision" when it has a real, composed `ReportDefinition`
*and* a `ParityRun` that already passes the charter (`parity_dashboard.
passes_the_charter`) *and* its own latest G3 `GateDecision` (if any) is not
`APPROVED` — i.e. never decided, or sent back with `CHANGES_REQUESTED` and not yet
re-approved. A report that has not yet passed the charter is not "waiting for a
decision," it is waiting on more work; this module does not surface it.

**G4's own "pending" definition finally builds the real queue S9.3.1's own docstring
said it was deliberately not building** ("a fuller 'readiness met opens the request
automatically' mechanism... is not built" — g4_card.py's own module docstring). A site
is "awaiting G4 decision" when `g4_card.readiness_checklist` reports every item met
*and* its own latest G4 decision (if any) is not `APPROVED` (a `DEFERRED` site is still
open, since deferral is explicitly a "not yet, try again later" outcome, not a
terminal one). Computing readiness for every site to find which ones qualify accepts
the identical fan-out cost `g4_card.readiness_checklist`/`DecommissionTracker.tsx`
already have for a first cut — this story's AC carries no latency budget the way
S10.3.1's Migration Unit page did.
"""

from __future__ import annotations

from typing import Any

import asyncpg

from .adoption import AdoptionStore
from .g2 import QuestionStore
from .g2_reminders import pending_g2_reviews
from .g3_card import _live_gate_decisions  # cross-epic private helper; see module docstring
from .g4_card import DecommissionConfirmationStore, readiness_checklist
from .graph.queries import NODE_INDEX_TABLE
from .lineage import hydrate
from .mu_page import _parent  # cross-epic private helper; see module docstring
from .parity_dashboard import parity_dashboard
from .regression import RegressionScheduleStore
from .release import PromotionStore
from .roles import Role, RoleSet
from .scope import ScopeStore

#: The countersigner role each gate's own approve action writes on a `GateDecision` --
#: real, structural facts (`g2.approve`/`g3_card._write_gate_decision`/`g4_card.
#: _write_gate_decision`'s own `countersigner_role` literals), read here rather than
#: restated, so the Gate Inbox can honestly answer "who is next" with the real role
#: that must countersign -- not a named individual, since no gate anywhere in this
#: codebase pre-assigns one (the countersigner is a plain string the approver themselves
#: types at decision time -- confirmed by direct research, a real, disclosed gap every
#: existing gate already carries).
COUNTERSIGNER_ROLE = {"G2": "semantic_model_engineer", "G3": "migration_engineer", "G4": "programme_manager"}


async def _all_nodes(pool: asyncpg.Pool, graph_name: str, label: str) -> dict[str, dict[str, Any]]:
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            f"""SELECT id FROM {NODE_INDEX_TABLE}
                 WHERE graph = $1 AND kind = 'node' AND label = $2 AND retired_at IS NULL""",
            graph_name, label,
        )
        return await hydrate(conn, graph_name, label, [row["id"] for row in rows])


def _latest_decision(decisions: dict[str, dict[str, Any]], *, gate: str, subject_ref: str) -> dict[str, Any] | None:
    matches = [
        props for props in decisions.values()
        if props.get("gate") == gate and props.get("subject_ref") == subject_ref
    ]
    if not matches:
        return None
    return max(matches, key=lambda props: str(props.get("timestamp") or ""))


async def pending_g2_items(
    pool: asyncpg.Pool, graph_name: str, question_store: QuestionStore, *, domain_scope: frozenset[str] | None,
) -> list[dict[str, Any]]:
    """`domain_scope=None` means unrestricted (an Artizent reader); otherwise the
    identical "an unset family domain is open to anyone" rule `g2.check_domain_scope`
    already applies."""
    reviews = await pending_g2_reviews(pool, graph_name, question_store)
    items: list[dict[str, Any]] = []
    for review in reviews:
        if (
            domain_scope is not None
            and review.domain is not None
            and review.domain.strip().lower() not in domain_scope
        ):
            continue
        items.append({
            "gate": "G2", "subject_ref": review.family_id, "name": review.name or review.family_id,
            "site": None, "domain": review.domain,
            "days_waiting": review.days_waiting, "breached": review.breached,
            "waiting_since": review.entered_review_at,
            "open_questions": review.open_questions,
            "approver_role": "client_data_owner", "countersigner_role": COUNTERSIGNER_ROLE["G2"],
            "can_ask_question": True, "can_request_changes": True, "can_defer": False,
            "detail": {"approver": review.approver},
        })
    return items


async def pending_g3_cards(pool: asyncpg.Pool, graph_name: str) -> list[dict[str, Any]]:
    reports = await _all_nodes(pool, graph_name, "ReportDefinition")
    decisions = await _live_gate_decisions(pool, graph_name)
    items: list[dict[str, Any]] = []
    for _report_id, report_properties in reports.items():
        workbook_id = report_properties.get("mu_ref")
        if not workbook_id:
            continue
        latest = _latest_decision(decisions, gate="G3", subject_ref=workbook_id)
        if latest is not None and latest.get("decision") == "APPROVED":
            continue
        dashboard = await parity_dashboard(pool, graph_name, workbook_id=workbook_id)
        if dashboard is None or not dashboard["passes_the_charter"]:
            continue
        async with pool.acquire() as conn:
            workbook_properties = (await hydrate(conn, graph_name, "Workbook", [workbook_id])).get(workbook_id) or {}
            project = await _parent(conn, graph_name, workbook_id, "Project")
            site = await _parent(conn, graph_name, project["id"], "Site") if project else None
        items.append({
            "gate": "G3", "subject_ref": workbook_id, "name": workbook_properties.get("name", workbook_id),
            "site": site.get("name") if site else None, "domain": None,
            "days_waiting": None, "breached": False,
            "waiting_since": report_properties.get("created_at"),
            "open_questions": 0,
            "approver_role": "client_report_owner", "countersigner_role": COUNTERSIGNER_ROLE["G3"],
            "can_ask_question": True, "can_request_changes": True, "can_defer": False,
            "detail": {
                "charter_version": dashboard["charter_version"],
                "sheets": len(dashboard["sheets"]),
                "resubmitted": latest is not None and latest.get("decision") == "CHANGES_REQUESTED",
            },
        })
    return items


async def pending_g4_sites(
    pool: asyncpg.Pool,
    graph_name: str,
    promotion_store: PromotionStore,
    adoption_store: AdoptionStore,
    confirmation_store: DecommissionConfirmationStore,
    regression_store: RegressionScheduleStore,
    scope_store: ScopeStore,
) -> list[dict[str, Any]]:
    sites = await _all_nodes(pool, graph_name, "Site")
    decisions = await _live_gate_decisions(pool, graph_name)
    items: list[dict[str, Any]] = []
    for site_id, site_properties in sites.items():
        latest = _latest_decision(decisions, gate="G4", subject_ref=site_id)
        if latest is not None and latest.get("decision") == "APPROVED":
            continue
        checklist = await readiness_checklist(
            pool, graph_name, promotion_store, adoption_store, confirmation_store, regression_store, scope_store,
            site_id=site_id,
        )
        if not checklist or not all(item.met for item in checklist):
            continue
        items.append({
            "gate": "G4", "subject_ref": site_id, "name": site_properties.get("name", site_id),
            "site": site_properties.get("name"), "domain": None,
            "days_waiting": None, "breached": False, "waiting_since": None,
            "open_questions": 0,
            "approver_role": "client_licence_admin", "countersigner_role": COUNTERSIGNER_ROLE["G4"],
            "can_ask_question": False, "can_request_changes": False, "can_defer": True,
            "detail": {
                "checklist": [item.as_dict() for item in checklist],
                "deferred": latest is not None and latest.get("decision") == "DEFERRED",
            },
        })
    return items


def _sort_key(item: dict[str, Any]) -> tuple[int, float, str]:
    breach_rank = 0 if item["breached"] else 1
    days_waiting = item.get("days_waiting")
    waiting_rank = -float(days_waiting) if days_waiting is not None else 1.0
    return (breach_rank, waiting_rank, item.get("waiting_since") or "9999")


async def gate_inbox(
    pool: asyncpg.Pool,
    graph_name: str,
    *,
    roles: RoleSet,
    domain_scope: frozenset[str],
    question_store: QuestionStore,
    promotion_store: PromotionStore,
    adoption_store: AdoptionStore,
    confirmation_store: DecommissionConfirmationStore,
    regression_store: RegressionScheduleStore,
    scope_store: ScopeStore,
) -> dict[str, Any]:
    """The role-dispatched card stack -- see this module's own docstring for why this is
    a dispatch over the caller's one declared role, not a merge across many."""
    items: list[dict[str, Any]] = []

    if roles.is_artizent() or Role.CLIENT_DATA_OWNER in roles.roles:
        scope = None if roles.is_artizent() else domain_scope
        items.extend(await pending_g2_items(pool, graph_name, question_store, domain_scope=scope))

    if roles.is_artizent() or Role.CLIENT_REPORT_OWNER in roles.roles:
        items.extend(await pending_g3_cards(pool, graph_name))

    if roles.is_artizent() or Role.CLIENT_LICENCE_ADMIN in roles.roles:
        items.extend(
            await pending_g4_sites(
                pool, graph_name, promotion_store, adoption_store, confirmation_store, regression_store, scope_store,
            )
        )

    items.sort(key=_sort_key)
    return {
        "items": items,
        "count": len(items),
        "breached_count": sum(1 for item in items if item["breached"]),
    }


__all__ = [
    "COUNTERSIGNER_ROLE",
    "gate_inbox",
    "pending_g2_items",
    "pending_g3_cards",
    "pending_g4_sites",
]
