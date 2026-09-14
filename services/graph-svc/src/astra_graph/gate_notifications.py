"""Gate Inbox notifications -- story S10.4.1, opening F10.4.

    "Email and Teams notification on new request and at SLA thresholds, with a deep
    link."

**No live email or Teams delivery exists anywhere in this codebase, confirmed directly**
(no smtp/email library in `pyproject.toml`; the only Teams-adjacent code is `g3_card.
to_adaptive_card`, a real, schema-correct export with no live bot/webhook to post it —
see that module's own docstring). This module's own notifications are the identical
"a real, recorded, idempotent mechanism -- not real outward delivery" posture `g2_
reminders.py`'s own `NotificationChannel`/`LocalNotificationChannel` already established
for exactly this reason: "sent" means "recorded, and logged" until a real channel
exists, a disclosed gap rather than a claim of delivery nobody could verify.

**"At SLA thresholds" stays entirely `g2_reminders.py`'s own, already-real mechanism —
untouched by this module.** Only G2 has a real, driven SLA/due-date concept
(`DEFAULT_SLA_WORKING_DAYS`/`is_breached`, via `pending_g2_reviews`'s own event-log
read); G1/G3/G4 have none (confirmed by direct research — no `entered_*_at`/
`days_waiting`/SLA constant exists for any of the other three gates). The Gate Inbox's
own notify action calls `g2_reminders.send_due_reminders` for that half of the AC and
this module for the other half, rather than inventing a fake SLA for gates that do not
have one.

**"On new request" is this module's own, new, generalised mechanism, spanning every
gate type the Gate Inbox surfaces (G2/G3/G4).** A new platform table, `public.
gate_notification` (migration v0038), keyed by `(graph, gate, subject_ref)` — a
different key shape from `g2_reminder`'s own `(family_id, day)`, since this is a
one-time "a new open request appeared" event, not a repeating threshold, and it spans
three different subject grains (a family, a workbook, a site) that table's own schema
has no column for.

**`GateNotificationChannel` is a new Protocol, not `g2_reminders.NotificationChannel`
reused directly.** The shapes genuinely differ — G2's channel is typed to its own
`PendingReview` dataclass and a `day: int`; a Gate Inbox item spans three different real
shapes (`gate_inbox.GateInboxItem`, effectively) and carries no day-threshold concept at
all. Same pattern (`kind` property, one `notify` method, a `Local...` implementation
that records and logs only), deliberately not the same class — the identical
"reuse the shape, not necessarily the class, when the underlying subject differs"
footing `g3_question`'s own deliberately-narrower-than-`g2_question` shape already set.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Protocol

import asyncpg

from .ids import new_ulid

logger = logging.getLogger(__name__)

NOTIFICATION_TABLE = "public.gate_notification"

#: The Gate Inbox's own future URL (S10.4.1) -- a real console path a notification
#: recipient's own deep link can point at, the identical `setDeepLinkParam`/
#: `getDeepLinkParam` convention `G3Card.tsx`/`ParityDashboard.tsx`/`MigrationUnitPage.
#: tsx` already use.
def deep_link(gate: str, subject_ref: str) -> str:
    return f"/inbox?gate={gate}&subject={subject_ref}"


@dataclass(frozen=True, slots=True)
class GateNotificationRecord:
    id: str
    gate: str
    subject_ref: str
    sent_at: str

    def as_dict(self) -> dict[str, Any]:
        return {"id": self.id, "gate": self.gate, "subject_ref": self.subject_ref, "sent_at": self.sent_at}


class GateNotificationStore(Protocol):
    async def record_if_new(self, gate: str, subject_ref: str) -> GateNotificationRecord | None: ...


class PostgresGateNotificationStore:
    def __init__(self, pool: asyncpg.Pool, *, graph_name: str) -> None:
        self._pool = pool
        self._graph = graph_name

    async def record_if_new(self, gate: str, subject_ref: str) -> GateNotificationRecord | None:
        """Record this gate item as notified, once. Returns `None` if one is already on
        file -- the unique `(graph, gate, subject_ref)` constraint is what makes a
        repeated notify action safe, not application logic."""
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                f"""
                INSERT INTO {NOTIFICATION_TABLE} (id, graph, gate, subject_ref)
                VALUES ($1, $2, $3, $4)
                ON CONFLICT (graph, gate, subject_ref) DO NOTHING
             RETURNING id, gate, subject_ref, sent_at
                """,
                new_ulid(), self._graph, gate, subject_ref,
            )
        if row is None:
            return None
        return GateNotificationRecord(
            id=row["id"], gate=row["gate"], subject_ref=row["subject_ref"],
            sent_at=row["sent_at"].isoformat(),
        )


class GateNotificationChannel(Protocol):
    @property
    def kind(self) -> str: ...

    async def notify(self, item: dict[str, Any]) -> None: ...


class LocalGateNotificationChannel:
    """No outward channel. The exact `g2_reminders.LocalNotificationChannel` precedent:
    a notice is recorded and logged here rather than silently dropped because no real
    email/Teams channel is configured."""

    kind = "local"

    async def notify(self, item: dict[str, Any]) -> None:
        logger.info(
            "Gate Inbox: new %s request for %s (%s) -- approver role %s, countersign by "
            "%s -- no notification channel is configured, recorded locally -- %s",
            item["gate"], item["subject_ref"], item.get("name") or item["subject_ref"],
            item.get("approver_role") or "unassigned", item.get("countersigner_role"),
            deep_link(item["gate"], item["subject_ref"]),
        )


async def notify_new_requests(
    store: GateNotificationStore, channel: GateNotificationChannel, items: list[dict[str, Any]],
) -> list[GateNotificationRecord]:
    """For every open Gate Inbox item, record and send the "new request" notice if one
    has never gone out for it. Safe to call repeatedly, the identical "a (gate,
    subject_ref) pair already recorded is skipped" idempotency `g2_reminders.
    send_due_reminders` already established for its own, differently-keyed table."""
    sent: list[GateNotificationRecord] = []
    for item in items:
        record = await store.record_if_new(item["gate"], item["subject_ref"])
        if record is None:
            continue
        await channel.notify(item)
        sent.append(record)
    return sent


__all__ = [
    "NOTIFICATION_TABLE",
    "GateNotificationChannel",
    "GateNotificationRecord",
    "GateNotificationStore",
    "LocalGateNotificationChannel",
    "PostgresGateNotificationStore",
    "deep_link",
    "notify_new_requests",
]
