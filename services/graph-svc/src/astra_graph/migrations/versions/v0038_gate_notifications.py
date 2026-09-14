"""Gate Inbox "new request" notification records — story S10.4.1, opening F10.4.

    "Email and Teams notification on new request and at SLA thresholds, with a deep
    link."

**A platform table, the same footing as `g2_reminder` (v0017).** A notification-sent
record is bookkeeping for this story's own idempotency (`gate_notifications.
notify_new_requests` is meant to be called repeatedly, safely, the identical "a second
call finds the row already there" posture `g2_reminder` already established) — not a
fact about the source or target estate.

**A separate table from `g2_reminder`, not a widened one.** `g2_reminder` is keyed by a
`(family_id, day)` pair — one row per G2-specific 3-or-5-working-day threshold crossed.
"On new request" is a one-time event per gate item, across three different subject
grains (a family, a workbook, a site) `g2_reminder`'s own schema has no column for —
`(graph, gate, subject_ref)` is the natural key here instead. `g2_reminder`/
`send_due_reminders` stay entirely untouched; this table is `gate_notifications.py`'s
own, new mechanism, called alongside the existing one for a complete Gate Inbox
notification action.

No ontology changes — every gate's own `GateDecision`/subject nodes are declared already.
"""

from __future__ import annotations

import asyncpg

VERSION = 38
DESCRIPTION = 'Gate Inbox "new request" notification records (public.gate_notification)'

ONTOLOGY_CHANGES: list[dict[str, str]] = []


_DDL = """
CREATE TABLE IF NOT EXISTS public.gate_notification (
    id          text        PRIMARY KEY,
    graph       text        NOT NULL,
    gate        text        NOT NULL CHECK (gate IN ('G2', 'G3', 'G4')),
    subject_ref text        NOT NULL,
    sent_at     timestamptz NOT NULL DEFAULT now(),
    UNIQUE (graph, gate, subject_ref)
)
"""

_INDEXES = (
    "CREATE INDEX IF NOT EXISTS gate_notification_subject_idx "
    "ON public.gate_notification (graph, gate, subject_ref)",
)


async def up(conn: asyncpg.Connection) -> None:
    await conn.execute(_DDL)
    for statement in _INDEXES:
        await conn.execute(statement)
