"""G2 reminder records — story S4.2.2.

    "As a programme manager, I want G2 cycle time and open questions per family on the
    Programme Board, so that I can chase the right person before the train slips...
    Reminder notifications are sent at 3 and 5 days."

**A platform table, the same footing as `g2_question` (v0016) and `grammar_issue`
(S1.4.3).** A reminder-sent record is not a fact about the source or target estate — it is
bookkeeping for this story's own idempotency, so a family already reminded at day 3 is not
reminded again the next time somebody loads the Programme Board or triggers the send action.

**One row per (family, threshold-day), unique.** `POST /v1/g2/reminders:send`
(`g2_reminders.send_due_reminders`) is meant to be called repeatedly — on every board load,
or by a future scheduler — and the unique constraint is what makes that safe: a second call
on the same day finds the row already there and sends nothing a second time, rather than
this module tracking "what have I already sent" in memory across restarts.

No ontology changes — `ModelFamily.owner`, the property this story reads as "the approver",
has been declared since S1.1.1 already.
"""

from __future__ import annotations

import asyncpg

VERSION = 17
DESCRIPTION = "G2 reminder records (public.g2_reminder)"

ONTOLOGY_CHANGES: list[dict[str, str]] = []


_DDL = """
CREATE TABLE IF NOT EXISTS public.g2_reminder (
    id        text        PRIMARY KEY,
    graph     text        NOT NULL,
    family_id text        NOT NULL,
    day       int         NOT NULL CHECK (day IN (3, 5)),
    sent_at   timestamptz NOT NULL DEFAULT now(),
    UNIQUE (graph, family_id, day)
)
"""

_INDEXES = (
    "CREATE INDEX IF NOT EXISTS g2_reminder_family_idx "
    "ON public.g2_reminder (graph, family_id)",
)


async def up(conn: asyncpg.Connection) -> None:
    await conn.execute(_DDL)
    for statement in _INDEXES:
        await conn.execute(statement)
