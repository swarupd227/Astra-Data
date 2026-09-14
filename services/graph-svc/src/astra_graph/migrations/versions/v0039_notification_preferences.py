"""Per-user notification preferences and a general notification log — story S10.5.2,
opening F10.5.

    "Per-user preferences: channels (email, Teams), events (gate request, exception
    assigned, regression fail, train re-plan), digest mode (immediate, daily)."

**Two platform tables, the same footing as `g2_reminder` (v0017) and `gate_notification`
(v0038).** `public.notification_preference` is one row per `(graph, principal)` --
overwritten in place, not versioned, since this is a person's own mutable setting (the
same "plain, session-local choice" shape `App.tsx`'s own `locale` selector already has
for its console-side sibling, S10.5.1 -- except this one is real enough to need server
persistence across sessions). `public.notification_log` generalises the narrower,
one-table-per-event-type precedent `g2_reminder`/`gate_notification` each set: this
story's own AC is the first to ask for one *tunable* mechanism spanning several event
types, so recording them in one table (keyed `event_type` + `subject_ref`, exactly the
`gate_notification` shape widened by one column) is the honest generalisation, not a
fourth near-duplicate table.

`sent_at IS NULL` is a *queued* row (a `digest_mode="daily"` preference deferred it);
`sent_at` set is a *sent* (recorded-and-logged) row -- both are the identical real,
idempotent record `gate_notification` already establishes, `UNIQUE(graph, event_type,
subject_ref, recipient, channel)` closing the loop so an event already recorded (queued
or sent) for a given recipient/channel is never recorded twice.

No ontology changes -- every subject this story's four events name (`GateDecision`,
`ExceptionCase`, a regression's own `Workbook`, a train re-plan's own `Workbook`) is
already declared.
"""

from __future__ import annotations

import asyncpg

VERSION = 39
DESCRIPTION = "Per-user notification preferences and a general notification log"

ONTOLOGY_CHANGES: list[dict[str, str]] = []


_DDL = """
CREATE TABLE IF NOT EXISTS public.notification_preference (
    graph       text        NOT NULL,
    principal   text        NOT NULL,
    channels    text[]      NOT NULL,
    events      text[]      NOT NULL,
    digest_mode text        NOT NULL CHECK (digest_mode IN ('immediate', 'daily')),
    updated_at  timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (graph, principal)
);

CREATE TABLE IF NOT EXISTS public.notification_log (
    id          text        PRIMARY KEY,
    graph       text        NOT NULL,
    event_type  text        NOT NULL CHECK (event_type IN
        ('gate_request', 'exception_assigned', 'regression_fail', 'train_replan')),
    subject_ref text        NOT NULL,
    recipient   text        NOT NULL,
    channel     text        NOT NULL CHECK (channel IN ('email', 'teams')),
    summary     text        NOT NULL,
    link        text        NOT NULL,
    queued_at   timestamptz NOT NULL DEFAULT now(),
    sent_at     timestamptz,
    UNIQUE (graph, event_type, subject_ref, recipient, channel)
)
"""

_INDEXES = (
    "CREATE INDEX IF NOT EXISTS notification_log_pending_idx "
    "ON public.notification_log (graph, recipient, channel) WHERE sent_at IS NULL",
)


async def up(conn: asyncpg.Connection) -> None:
    await conn.execute(_DDL)
    for statement in _INDEXES:
        await conn.execute(statement)
