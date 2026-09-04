"""G2 cycle time and reminders on the Programme Board — story S4.2.2.

    "As a programme manager, I want G2 cycle time and open questions per family on the
    Programme Board, so that I can chase the right person before the train slips.
    Board tile shows families awaiting G2, days waiting, and the approver; SLA breach
    (default 5 working days) is highlighted. Reminder notifications are sent at 3 and 5
    days."

**Days waiting reuses the same event-log technique `family_transition_history`
(`model_lifecycle.py`, S4.1.2) already established**, one step further: instead of every
transition, `_entered_review_at` asks the same `LAG() OVER (...)` window for only the most
recent genuine move into `IN_REVIEW`, batched across every family awaiting G2 in one query
rather than one round trip per row.

**"The approver" is `ModelFamily.owner`.** Declared since S1.1.1, read by
`cartographer._family_summary`, and — like `domain` before S4.2.1 — never once written by
any code before this story. `model_lifecycle.update_owner` lets a Semantic Model Engineer
assign one while DRAFT, the exact `update_domain` precedent. An unset owner is reported as
absent, not defaulted to anyone; nothing assigns one automatically yet, the same disclosed
gap `check_domain_scope` (g2.py, S4.2.1) already carries for domain.

**SLA breach reuses `train_projection.working_days_between` and its own 5-working-day
default (S3.2.3)** rather than a second copy of either: the spec names "5 working days" for
two different lateness flags (§14.2's train projection, §15.3.1's G2 wait) and no story
before this one needed the second, so this module imports both instead of restating them.

**Reminders are a real, recorded, idempotent mechanism — not real outward delivery.**
`public.g2_reminder` records a reminder as sent, once per `(family, day)` pair;
`NotificationChannel`/`LocalNotificationChannel` is the exact `IssueTracker`/
`LocalIssueTracker` precedent (`grammar.py`, S1.4.3): §21 makes an outward channel optional
infrastructure this codebase has never built, so "sent" means "recorded, and logged" until
a real channel exists — a disclosed gap, not a claim of delivery nobody could verify.
Likewise, *when* the send action runs is not yet automated: `send_due_reminders` is a
`POST`-triggered action (`POST /v1/g2/reminders:send`), safe to call repeatedly — on every
Programme Board load, or from a future scheduler — because the unique constraint makes a
second call on an already-reminded family a no-op. A background loop that calls it on a
timer (the same shape `HarvestScheduler`, S1.2.4, already gives this codebase) is real
future scope this story does not claim.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime
from typing import Any, Protocol

import asyncpg

from .cartographer import list_families
from .g2 import QuestionStore
from .ids import new_ulid
from .train_projection import DEFAULT_LATE_THRESHOLD_WORKING_DAYS, working_days_between
from .versions import EVENT_TABLE

logger = logging.getLogger(__name__)

REMINDER_TABLE = "public.g2_reminder"

#: Same figure S3.2.3 already flags a late train with (§14.2) — this story's own "SLA
#: breach (default 5 working days)" names the identical number, not a coincidence worth a
#: second constant.
DEFAULT_SLA_WORKING_DAYS = DEFAULT_LATE_THRESHOLD_WORKING_DAYS

#: "Reminder notifications are sent at 3 and 5 days" — transcribed, not derived.
REMINDER_DAYS: tuple[int, ...] = (3, 5)


def is_breached(days_waiting: int | None, sla_working_days: int = DEFAULT_SLA_WORKING_DAYS) -> bool:
    """"SLA breach (default 5 working days)" (S4.2.2) — a family waiting exactly the SLA
    is not yet over it; one working day past is. A pure predicate so the off-by-one is
    checked directly, not only through a database-backed integration test."""
    return days_waiting is not None and days_waiting > sla_working_days


def due_reminder_days(days_waiting: int | None) -> tuple[int, ...]:
    """Which of `REMINDER_DAYS` are due for a family that has waited this many working
    days — "reminder notifications are sent at 3 and 5 days" (S4.2.2), the exact decision
    `send_due_reminders` makes idempotent per `(family, day)` via `ReminderStore`."""
    if days_waiting is None:
        return ()
    return tuple(day for day in REMINDER_DAYS if days_waiting >= day)


@dataclass(frozen=True, slots=True)
class PendingReview:
    """One family awaiting G2 — one row of the Programme Board's tile."""

    family_id: str
    name: str | None
    domain: str | None
    approver: str | None
    entered_review_at: str | None
    days_waiting: int | None
    """``None`` when the family is IN_REVIEW but no transition into it was ever recorded
    as an event — a state set by a means other than `model_lifecycle.submit_for_review`
    (there is none in this codebase, but the tile should not crash if one ever exists)."""
    breached: bool
    open_questions: int

    def as_dict(self) -> dict[str, Any]:
        return {
            "family_id": self.family_id,
            "name": self.name,
            "domain": self.domain,
            "approver": self.approver,
            "entered_review_at": self.entered_review_at,
            "days_waiting": self.days_waiting,
            "breached": self.breached,
            "open_questions": self.open_questions,
        }


async def _entered_review_at(
    pool: asyncpg.Pool, graph_name: str, family_ids: Sequence[str]
) -> dict[str, datetime]:
    if not family_ids:
        return {}
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            f"""
            WITH state_events AS (
                SELECT
                    subject,
                    time,
                    data -> 'properties' ->> 'state' AS state,
                    LAG(data -> 'properties' ->> 'state')
                        OVER (PARTITION BY subject ORDER BY seq) AS previous_state
                FROM {EVENT_TABLE}
                WHERE graph = $1 AND type = 'estate.node.upserted' AND label = 'ModelFamily'
                  AND subject = ANY($2::text[])
            )
            SELECT subject, MAX(time) AS entered_at
            FROM state_events
            WHERE previous_state IS DISTINCT FROM state AND state = 'IN_REVIEW'
            GROUP BY subject
            """,
            graph_name,
            list(family_ids),
        )
    return {row["subject"]: row["entered_at"] for row in rows}


async def pending_g2_reviews(
    pool: asyncpg.Pool,
    graph_name: str,
    store: QuestionStore,
    *,
    sla_working_days: int = DEFAULT_SLA_WORKING_DAYS,
    now: date | None = None,
) -> list[PendingReview]:
    """Every family currently `IN_REVIEW`, worst-waiting first — the Programme Board
    tile's own data, and `send_due_reminders`'s own source of what is due."""
    today = now if now is not None else datetime.now(UTC).date()
    families = await list_families(pool, graph_name, state="IN_REVIEW")
    if not families:
        return []

    family_ids = [family["id"] for family in families]
    entered_at = await _entered_review_at(pool, graph_name, family_ids)

    reviews = []
    for family in families:
        family_id = family["id"]
        entered = entered_at.get(family_id)
        days_waiting = working_days_between(entered.date(), today) if entered else None
        reviews.append(
            PendingReview(
                family_id=family_id,
                name=family.get("name"),
                domain=family.get("domain"),
                approver=family.get("owner"),
                entered_review_at=_iso(entered),
                days_waiting=days_waiting,
                breached=is_breached(days_waiting, sla_working_days),
                open_questions=await store.count_open(family_id),
            )
        )
    reviews.sort(key=lambda review: (review.days_waiting is None, -(review.days_waiting or 0)))
    return reviews


# ------------------------------------------------------------------------------ reminders


@dataclass(frozen=True, slots=True)
class ReminderRecord:
    id: str
    family_id: str
    day: int
    sent_at: str

    def as_dict(self) -> dict[str, Any]:
        return {"id": self.id, "family_id": self.family_id, "day": self.day, "sent_at": self.sent_at}


class ReminderStore(Protocol):
    async def record_if_new(self, family_id: str, day: int) -> ReminderRecord | None: ...


class PostgresReminderStore:
    def __init__(self, pool: asyncpg.Pool, *, graph_name: str) -> None:
        self._pool = pool
        self._graph = graph_name

    async def record_if_new(self, family_id: str, day: int) -> ReminderRecord | None:
        """Record day-N's reminder for this family. Returns the new record, or ``None``
        if one is already on file — the unique `(graph, family_id, day)` constraint is
        what makes a repeated `send_due_reminders` call safe, not application logic."""
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                f"""
                INSERT INTO {REMINDER_TABLE} (id, graph, family_id, day)
                VALUES ($1, $2, $3, $4)
                ON CONFLICT (graph, family_id, day) DO NOTHING
             RETURNING id, family_id, day, sent_at
                """,
                new_ulid(),
                self._graph,
                family_id,
                day,
            )
        if row is None:
            return None
        return ReminderRecord(
            id=row["id"],
            family_id=row["family_id"],
            day=row["day"],
            sent_at=_iso(row["sent_at"]) or "",
        )


class NotificationChannel(Protocol):
    @property
    def kind(self) -> str: ...

    async def notify(self, review: PendingReview, day: int) -> None: ...


class LocalNotificationChannel:
    """No outward channel. The exact `LocalIssueTracker` precedent (`grammar.py`,
    S1.4.3): a reminder is recorded and logged here rather than silently dropped because
    no real notification channel (email, chat, ...) is configured."""

    kind = "local"

    async def notify(self, review: PendingReview, day: int) -> None:
        logger.info(
            "G2 reminder (day %d) for family %s (%s): %s working day(s) waiting, approver "
            "%s — no notification channel is configured, recorded locally",
            day,
            review.family_id,
            review.name,
            review.days_waiting,
            review.approver or "unassigned",
        )


async def send_due_reminders(
    pool: asyncpg.Pool,
    graph_name: str,
    store: QuestionStore,
    reminder_store: ReminderStore,
    channel: NotificationChannel,
    *,
    now: date | None = None,
) -> list[ReminderRecord]:
    """For every family awaiting G2, record and send whichever of the 3- and 5-day
    reminders are now due. Safe to call repeatedly: a `(family, day)` pair already
    recorded is skipped, so this is never sent twice for the same threshold."""
    reviews = await pending_g2_reviews(pool, graph_name, store, now=now)
    sent: list[ReminderRecord] = []
    for review in reviews:
        for day in due_reminder_days(review.days_waiting):
            record = await reminder_store.record_if_new(review.family_id, day)
            if record is None:
                continue
            await channel.notify(review, day)
            sent.append(record)
    return sent


def _iso(value: datetime | None) -> str | None:
    if value is None:
        return None
    return value.isoformat()


__all__ = [
    "DEFAULT_SLA_WORKING_DAYS",
    "REMINDER_DAYS",
    "LocalNotificationChannel",
    "NotificationChannel",
    "PendingReview",
    "PostgresReminderStore",
    "ReminderRecord",
    "ReminderStore",
    "due_reminder_days",
    "is_breached",
    "pending_g2_reviews",
    "send_due_reminders",
]
