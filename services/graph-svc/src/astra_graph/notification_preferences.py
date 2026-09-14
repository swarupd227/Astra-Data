"""Per-user notification preferences -- story S10.5.2, opening F10.5.

    "As a report owner, I want notifications I can tune, so that I hear about my
    reports, not everyone's.

    Acceptance criteria:
    - Per-user preferences: channels (email, Teams), events (gate request, exception
      assigned, regression fail, train re-plan), digest mode (immediate, daily)
    - Notification content never includes data values; it links to the console"

**"Per-user" means per real `Principal` string, not per role.** Confirmed by direct
research: nothing in `roles.py`/`Principal` ties preferences to one role, and the four
named events each address a different, real kind of recipient -- a G2 item's own real
`review.approver` (`gate_inbox.py`), an `ExceptionCase.assignee` (`exception_desk.py`),
a workbook's own real `OWNED_BY` owner (`ontology/edges.py`'s own note: "Ownership routes
gate requests to a named person," §15.1 -- the exact, already-established mechanism this
story reuses rather than reinvents), and again a workbook's own owner for a train
re-plan. `client_report_owner` is this story's own AC persona, not this module's own
access boundary -- a `migration_engineer` assigned an exception is exactly as real a
recipient as a report owner whose workbook regressed.

**One general table, not a fourth narrow one.** `g2_reminder` (v0017) and
`gate_notification` (v0038) each earned their own table because each was the *first* and
*only* mechanism of its own kind at the time. This story is the first to ask for one
*tunable* mechanism spanning several event types at once -- recording every one of them
in `public.notification_log` (`event_type` alongside the identical `subject_ref`
`gate_notification` already keys on) is the honest generalisation now that a real,
shared concept (preferences) sits above all four, not a fourth near-duplicate schema.

**A queued row and a sent row are the same real record, distinguished by `sent_at`.**
`digest_mode="immediate"` records-and-logs the instant an event fires (`sent_at` set
there and then); `digest_mode="daily"` records the identical row with `sent_at` left
`NULL` -- a real, durable queue entry, not an in-memory one that would be lost on a
restart -- and `send_pending_digests` (this module's own new action) is what flips it to
sent, batched one digest per `(recipient, channel)`. Modelled on the identical
"manually/repeatably-triggered POST action, not a live cron job" disclosed limitation
`g2_reminders.send_due_reminders` already carries -- no APScheduler/Temporal/cron
mechanism exists anywhere in this codebase (confirmed by direct search), and this story
does not add one; `POST /v1/notifications:send-digests` is the honest equivalent.

**No outward email/Teams delivery exists anywhere in this codebase** (confirmed,
again, by direct search: no SMTP library, no live Teams bot/webhook) -- `channels`
picks which *local, logged* record a preference produces, not a live wire. "Record and
log, disclosed as no real outward delivery" is the identical posture every other
notification mechanism in this codebase already discloses (`g2_reminders`,
`gate_notifications`, this module's own regression/exception/train integrations).

**Content is a `summary` string and a `link`, never a data value -- enforced by this
module's own call shape, not by a filter.** Every call site that builds a
`NotificationContent` below passes a subject name/id, a role or event label, and a
console deep link (reusing `gate_notifications.deep_link`'s own `?query=` convention
where a screen already has one, e.g. `ExceptionDesk.tsx`'s `?case=`, `RegressionMonitor.
tsx`'s `?workbook=`) -- never a measure, a cell value, a formula, or any other real
report content. There is structurally nowhere in this module's own signature to pass one.

**Exception assignment routes to whatever string `assignee` is typed as -- the identical
"a plain, unverified typed string, no separate authenticated identity" limitation this
codebase's own countersigner fields already disclose everywhere else.** `exception_desk.
bulk_assign`'s own `assignee: str` is free text (confirmed: no format validation exists).
A notification only reaches a real inbox when that string happens to be typed as the
assignee's own real `Principal` value (`user:<upn>`, matching the header format
`principal.py` defines) -- this module does not validate or coerce it; an assignee typed
as a plain display name simply has no matching preferences row and is silently skipped,
the honest behaviour for an unresolvable recipient rather than a fabricated one.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Protocol

import asyncpg

from .graph.queries import EDGE_INDEX_TABLE, NODE_INDEX_TABLE
from .ids import new_ulid
from .lineage import hydrate
from .principal import _PRINCIPAL_RE  # cross-epic private helper; see module docstring

logger = logging.getLogger(__name__)

PREFERENCE_TABLE = "public.notification_preference"
LOG_TABLE = "public.notification_log"

EVENT_TYPES: frozenset[str] = frozenset(
    {"gate_request", "exception_assigned", "regression_fail", "train_replan"}
)
CHANNELS: frozenset[str] = frozenset({"email", "teams"})
DIGEST_MODES: frozenset[str] = frozenset({"immediate", "daily"})

#: Opted into everything until tuned down -- the honest default for a preference nobody
#: has ever saved, matching this story's own "I want notifications I can tune" framing
#: (there is something to *turn down*, not something to opt into from nothing).
DEFAULT_CHANNELS = frozenset(CHANNELS)
DEFAULT_EVENTS = frozenset(EVENT_TYPES)
DEFAULT_DIGEST_MODE = "immediate"


class NotificationPreferenceError(ValueError):
    """A preference could not be saved as asked."""


@dataclass(frozen=True, slots=True)
class NotificationPreferences:
    principal: str
    channels: frozenset[str]
    events: frozenset[str]
    digest_mode: str
    updated_at: str | None

    def wants(self, event_type: str) -> bool:
        return event_type in self.events and bool(self.channels)

    def as_dict(self) -> dict[str, Any]:
        return {
            "principal": self.principal,
            "channels": sorted(self.channels),
            "events": sorted(self.events),
            "digest_mode": self.digest_mode,
            "updated_at": self.updated_at,
        }


def _defaults(principal: str) -> NotificationPreferences:
    return NotificationPreferences(
        principal=principal, channels=DEFAULT_CHANNELS, events=DEFAULT_EVENTS,
        digest_mode=DEFAULT_DIGEST_MODE, updated_at=None,
    )


def _validate(channels: list[str], events: list[str], digest_mode: str) -> tuple[frozenset[str], frozenset[str], str]:
    channel_set = frozenset(channels)
    event_set = frozenset(events)
    if not channel_set <= CHANNELS:
        raise NotificationPreferenceError(f"channels must be a subset of {sorted(CHANNELS)}")
    if not event_set <= EVENT_TYPES:
        raise NotificationPreferenceError(f"events must be a subset of {sorted(EVENT_TYPES)}")
    if digest_mode not in DIGEST_MODES:
        raise NotificationPreferenceError(f"digest_mode must be one of {sorted(DIGEST_MODES)}")
    return channel_set, event_set, digest_mode


@dataclass(frozen=True, slots=True)
class NotificationLogRecord:
    id: str
    event_type: str
    subject_ref: str
    recipient: str
    channel: str
    summary: str
    link: str
    queued_at: str
    sent_at: str | None

    def as_dict(self) -> dict[str, Any]:
        return {
            "id": self.id, "event_type": self.event_type, "subject_ref": self.subject_ref,
            "recipient": self.recipient, "channel": self.channel, "summary": self.summary,
            "link": self.link, "queued_at": self.queued_at, "sent_at": self.sent_at,
        }


class NotificationPreferenceStore(Protocol):
    async def get(self, principal: str) -> NotificationPreferences: ...

    async def set(
        self, principal: str, *, channels: list[str], events: list[str], digest_mode: str,
    ) -> NotificationPreferences: ...

    async def record(
        self, *, event_type: str, subject_ref: str, recipient: str, channel: str,
        summary: str, link: str, digest_mode: str,
    ) -> NotificationLogRecord | None:
        """Idempotent: `None` when this `(event_type, subject_ref, recipient, channel)`
        was already recorded (queued or sent), the identical "a second call finds the
        row already there" posture `gate_notification` already established."""
        ...

    async def pending(self, graph_name: str | None = None) -> list[NotificationLogRecord]: ...

    async def mark_sent(self, ids: list[str]) -> None: ...


class PostgresNotificationPreferenceStore:
    def __init__(self, pool: asyncpg.Pool, *, graph_name: str) -> None:
        self._pool = pool
        self._graph = graph_name

    async def get(self, principal: str) -> NotificationPreferences:
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                f"""SELECT channels, events, digest_mode, updated_at FROM {PREFERENCE_TABLE}
                     WHERE graph = $1 AND principal = $2""",
                self._graph, principal,
            )
        if row is None:
            return _defaults(principal)
        return NotificationPreferences(
            principal=principal, channels=frozenset(row["channels"]), events=frozenset(row["events"]),
            digest_mode=row["digest_mode"], updated_at=row["updated_at"].isoformat(),
        )

    async def set(
        self, principal: str, *, channels: list[str], events: list[str], digest_mode: str,
    ) -> NotificationPreferences:
        channel_set, event_set, mode = _validate(channels, events, digest_mode)
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                f"""INSERT INTO {PREFERENCE_TABLE} (graph, principal, channels, events, digest_mode)
                     VALUES ($1, $2, $3, $4, $5)
                     ON CONFLICT (graph, principal) DO UPDATE
                       SET channels = EXCLUDED.channels, events = EXCLUDED.events,
                           digest_mode = EXCLUDED.digest_mode, updated_at = now()
                     RETURNING updated_at""",
                self._graph, principal, sorted(channel_set), sorted(event_set), mode,
            )
        return NotificationPreferences(
            principal=principal, channels=channel_set, events=event_set, digest_mode=mode,
            updated_at=row["updated_at"].isoformat(),
        )

    async def record(
        self, *, event_type: str, subject_ref: str, recipient: str, channel: str,
        summary: str, link: str, digest_mode: str,
    ) -> NotificationLogRecord | None:
        record_id = new_ulid()
        sent_at = datetime.now(UTC) if digest_mode == "immediate" else None
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                f"""INSERT INTO {LOG_TABLE}
                        (id, graph, event_type, subject_ref, recipient, channel, summary, link, sent_at)
                     VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9)
                     ON CONFLICT (graph, event_type, subject_ref, recipient, channel) DO NOTHING
                     RETURNING id, queued_at, sent_at""",
                record_id, self._graph, event_type, subject_ref, recipient, channel, summary, link, sent_at,
            )
        if row is None:
            return None
        if sent_at is not None:
            logger.info(
                "notification (immediate) recorded for %s via %s: %s -- %s",
                recipient, channel, summary, link,
            )
        return NotificationLogRecord(
            id=row["id"], event_type=event_type, subject_ref=subject_ref, recipient=recipient,
            channel=channel, summary=summary, link=link,
            queued_at=row["queued_at"].isoformat(), sent_at=row["sent_at"].isoformat() if row["sent_at"] else None,
        )

    async def pending(self, graph_name: str | None = None) -> list[NotificationLogRecord]:
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(
                f"""SELECT id, event_type, subject_ref, recipient, channel, summary, link, queued_at, sent_at
                      FROM {LOG_TABLE} WHERE graph = $1 AND sent_at IS NULL
                     ORDER BY recipient, channel, queued_at""",
                graph_name or self._graph,
            )
        return [
            NotificationLogRecord(
                id=r["id"], event_type=r["event_type"], subject_ref=r["subject_ref"], recipient=r["recipient"],
                channel=r["channel"], summary=r["summary"], link=r["link"],
                queued_at=r["queued_at"].isoformat(), sent_at=None,
            )
            for r in rows
        ]

    async def mark_sent(self, ids: list[str]) -> None:
        if not ids:
            return
        async with self._pool.acquire() as conn:
            await conn.execute(
                f"UPDATE {LOG_TABLE} SET sent_at = now() WHERE id = ANY($1::text[])", ids,
            )


async def notify(
    store: NotificationPreferenceStore, *, event_type: str, subject_ref: str, recipient: str,
    summary: str, link: str,
) -> list[NotificationLogRecord]:
    """The one real entry point every integration below calls. Looks up `recipient`'s
    own real preferences (defaults, honestly, when none were ever saved), skips silently
    if they opted out of this event type or turned off every channel, and records one
    row per channel they did opt into -- immediately, or queued for their own next
    digest, per their own `digest_mode`. `recipient` that is not typed as a real
    `Principal` (`user:`/`agent:`/`service:<value>`) is skipped up front -- see this
    module's own docstring on why assignment cannot always resolve one."""
    if not _PRINCIPAL_RE.match(recipient):
        return []
    preferences = await store.get(recipient)
    if not preferences.wants(event_type):
        return []
    records: list[NotificationLogRecord] = []
    for channel in sorted(preferences.channels):
        record = await store.record(
            event_type=event_type, subject_ref=subject_ref, recipient=recipient, channel=channel,
            summary=summary, link=link, digest_mode=preferences.digest_mode,
        )
        if record is not None:
            records.append(record)
    return records


async def send_pending_digests(store: NotificationPreferenceStore) -> list[NotificationLogRecord]:
    """`POST /v1/notifications:send-digests`'s own real action -- one batch per
    `(recipient, channel)` covering everything queued for them since their last digest,
    logged as one line and flipped to sent together. The identical "manually/repeatably
    triggered, not a live cron job" disclosed limitation `g2_reminders.send_due_reminders`
    already carries -- see this module's own docstring."""
    pending = await store.pending()
    if not pending:
        return []
    groups: dict[tuple[str, str], list[NotificationLogRecord]] = {}
    for record in pending:
        groups.setdefault((record.recipient, record.channel), []).append(record)
    for (recipient, channel), records in groups.items():
        kinds = sorted({r.event_type for r in records})
        logger.info(
            "notification digest for %s via %s: %d item(s) (%s)",
            recipient, channel, len(records), ", ".join(kinds),
        )
    await store.mark_sent([record.id for record in pending])
    return pending


async def resolve_workbook_owner(pool: asyncpg.Pool, graph_name: str, workbook_id: str) -> str | None:
    """The real `Principal` value (`user:<upn>`) for a workbook's own `OWNED_BY` owner,
    or `None` when it has none or the owner never resolved against the directory --
    the identical real mechanism `estate.py`'s own `_owners` already reads for the
    Estate Explorer, and the one `ontology/edges.py`'s own `OWNED_BY` note names as
    "routes gate requests to a named person" (§15.1, story S1.2.3) -- reused here for
    the three other events this story adds, not a second, competing resolution."""
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            f"""SELECT e.to_id AS owner
                  FROM {EDGE_INDEX_TABLE} e
                  JOIN {NODE_INDEX_TABLE} n
                    ON n.id = e.to_id AND n.kind = 'node' AND n.graph = $1 AND n.label = 'User'
                 WHERE e.graph = $1 AND e.label = 'OWNED_BY' AND e.from_id = $2 AND e.retired_at IS NULL
                 LIMIT 1""",
            graph_name, workbook_id,
        )
        if row is None:
            return None
        owner = await hydrate(conn, graph_name, "User", [row["owner"]])
    properties = owner.get(row["owner"])
    upn = properties.get("upn") if properties else None
    return f"user:{upn}" if upn else None


__all__ = [
    "CHANNELS",
    "DEFAULT_CHANNELS",
    "DEFAULT_DIGEST_MODE",
    "DEFAULT_EVENTS",
    "DIGEST_MODES",
    "EVENT_TYPES",
    "NotificationLogRecord",
    "NotificationPreferenceError",
    "NotificationPreferenceStore",
    "NotificationPreferences",
    "PostgresNotificationPreferenceStore",
    "notify",
    "resolve_workbook_owner",
    "send_pending_digests",
]
