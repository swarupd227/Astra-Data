"""The Gate Inbox's API — story S10.4.1, opening F10.4. See `gate_inbox.py`'s own
module docstring for how the card stack is assembled and why it is role-dispatched
rather than merged across roles, and `gate_notifications.py`'s own docstring for why
"on new request" is this story's own new, disclosed-local-only mechanism.

Reading the inbox is gated broadly (`GateInboxReaderDep`); every card's own real
approve/request-changes/ask-a-question/defer action is the identical existing route
each gate already has (`:approve-g2`, `:request-changes`, `:approve-g3`, `:request-
changes-g3`, `:ask-g3-question`, `:approve-g4`, `:defer-g4`) — this module adds no new
mutation route, only the aggregated read and the notify action.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Request

from ..errors import InvalidRequestError
from ..g2_reminders import LocalNotificationChannel, send_due_reminders
from ..gate_inbox import gate_inbox
from ..gate_notifications import LocalGateNotificationChannel, notify_new_requests
from ..notification_preferences import notify as notify_preference
from .deps import DomainScopeDep, GateInboxReaderDep, PrincipalDep, RepositoryDep

router = APIRouter()


def _stores(request: Request) -> dict[str, Any]:
    state = request.app.state
    names = (
        "pool", "question_store", "promotion_store", "adoption_store",
        "decommission_confirmation_store", "regression_schedule_store", "scope_store",
        "gate_notification_store",
    )
    stores = {name: getattr(state, name, None) for name in names}
    if any(value is None for value in stores.values()):
        raise InvalidRequestError("the Gate Inbox is not available on this deployment")
    return stores


@router.get(
    "/v1/gate-inbox",
    tags=["gate-inbox"],
    summary="Open gate requests for the caller's own role and domain, ordered by due date (§15.3.6)",
)
async def get_gate_inbox(
    request: Request,
    principal: PrincipalDep,
    roles: GateInboxReaderDep,
    domain_scope: DomainScopeDep,
    repository: RepositoryDep,
) -> dict[str, Any]:
    stores = _stores(request)
    return await gate_inbox(
        stores["pool"], repository.graph_name,
        roles=roles, domain_scope=domain_scope,
        question_store=stores["question_store"], promotion_store=stores["promotion_store"],
        adoption_store=stores["adoption_store"], confirmation_store=stores["decommission_confirmation_store"],
        regression_store=stores["regression_schedule_store"], scope_store=stores["scope_store"],
    )


@router.post(
    "/v1/gate-inbox:notify",
    tags=["gate-inbox"],
    summary="Record and send whichever new-request and G2 SLA-threshold notices are now due",
)
async def post_notify_gate_inbox(
    request: Request,
    principal: PrincipalDep,
    roles: GateInboxReaderDep,
    domain_scope: DomainScopeDep,
    repository: RepositoryDep,
) -> dict[str, Any]:
    """Three real mechanisms, called together — see `gate_notifications.py`'s own module
    docstring for why "on new request" (every gate) and "at SLA thresholds" (G2 only,
    the sole gate with a real due-date concept) stay two different, already-established
    idempotent actions rather than one invented for both. Story S10.5.2 adds a third,
    real, preference-gated pass alongside them (not instead of them — `gate_notification`
    stays the honest broadcast-to-the-role record every gate gets regardless): every G2
    item carries a real `detail.approver` principal (`gate_inbox.pending_g2_items`,
    S10.4.1), so it is the one gate today whose own real recipient's own preferences can
    actually be consulted; G3/G4 items carry no real named approver principal (a
    disclosed gap `gate_inbox.py`'s own docstring already names) and are silently
    skipped by `notify_preference` for the identical reason."""
    stores = _stores(request)
    pool, graph_name = stores["pool"], repository.graph_name

    inbox = await gate_inbox(
        pool, graph_name, roles=roles, domain_scope=domain_scope,
        question_store=stores["question_store"], promotion_store=stores["promotion_store"],
        adoption_store=stores["adoption_store"], confirmation_store=stores["decommission_confirmation_store"],
        regression_store=stores["regression_schedule_store"], scope_store=stores["scope_store"],
    )
    new_requests = await notify_new_requests(
        stores["gate_notification_store"], LocalGateNotificationChannel(), inbox["items"],
    )
    sla_reminders = await send_due_reminders(
        pool, graph_name, stores["question_store"], _reminder_store(request), LocalNotificationChannel(),
    )
    preference_notified: list[dict[str, Any]] = []
    preference_store = getattr(request.app.state, "notification_preference_store", None)
    if preference_store is not None:
        for item in inbox["items"]:
            if item["gate"] != "G2":
                continue
            approver = item.get("detail", {}).get("approver")
            if not approver:
                continue
            records = await notify_preference(
                preference_store, event_type="gate_request", subject_ref=item["subject_ref"],
                recipient=approver, summary=f"G2 request waiting on {item['name']}",
                link=f"/inbox?gate=G2&subject={item['subject_ref']}",
            )
            preference_notified.extend(record.as_dict() for record in records)
    return {
        "new_requests_sent": [record.as_dict() for record in new_requests],
        "sla_reminders_sent": [record.as_dict() for record in sla_reminders],
        "preference_notified": preference_notified,
    }


def _reminder_store(request: Request) -> Any:
    store = getattr(request.app.state, "reminder_store", None)
    if store is None:  # pragma: no cover - set in every wiring path
        raise InvalidRequestError("G2 reminders are not available on this deployment")
    return store


__all__ = ["router"]
