"""Harvest schedules: what runs, how often, and when it last ran.

S1.2.4: "the Harvester runs incrementally on a schedule ... so the graph stays current
through a long programme without re-parsing the whole site", with "schedule and last run
visible on Platform Health".

**The schedule is data, not configuration.** A cron entry in a container image cannot be
paused from the console, does not survive a redeploy with its history, and cannot tell
Platform Health when it last ran. Specification §15.3.3 puts "scheduler pauses" on that
screen and §12.3.1 alerts on "scheduler starvation", both of which require the schedule to
be a row somebody can read and change.

**Claiming is done in the database.** ``due()`` takes the rows it returns, using
``FOR UPDATE SKIP LOCKED``, and advances ``next_run_at`` in the same transaction. Two
replicas of this service polling the same second therefore start one run between them, not
two — which matters because the deployment target is a container app that scales out, and
a double harvest of a 1,000-workbook site is hours of wasted source I/O.

**Cadence is deliberately small.** Every N minutes, or daily at a UTC time. Not cron: cron
without timezones is a trap for a client in Sydney, and cron *with* timezones is a library
and a class of bugs this story does not need. What the story needs is "nightly", and this
says nightly. ADR 0007 records it as an open question.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import UTC, datetime, time, timedelta
from typing import Any, Protocol

from ..ids import new_ulid

SCHEDULE_TABLE = "public.harvest_schedule"

#: Below this a "schedule" is a busy loop against the client's source.
MIN_INTERVAL_MINUTES = 5

#: A cadence longer than a fortnight is a reminder, not a schedule; and it is nearly always
#: a units mistake (minutes where hours were meant).
MAX_INTERVAL_MINUTES = 20_160

_DAILY_AT = re.compile(r"^(?P<hour>[01]\d|2[0-3]):(?P<minute>[0-5]\d)$")


class ScheduleError(Exception):
    """The schedule as described cannot be honoured."""


@dataclass(frozen=True, slots=True)
class Cadence:
    """How often a schedule fires. Exactly one of the two forms."""

    every_minutes: int | None = None
    daily_at: str | None = None
    """``HH:MM`` in UTC."""

    def __post_init__(self) -> None:
        given = [f for f in (self.every_minutes, self.daily_at) if f is not None]
        if len(given) != 1:
            raise ScheduleError(
                "a cadence is either 'every_minutes' or 'daily_at', and exactly one of them"
            )
        if self.every_minutes is not None and not (
            MIN_INTERVAL_MINUTES <= self.every_minutes <= MAX_INTERVAL_MINUTES
        ):
            raise ScheduleError(
                f"every_minutes must be between {MIN_INTERVAL_MINUTES} and "
                f"{MAX_INTERVAL_MINUTES}, got {self.every_minutes}"
            )
        if self.daily_at is not None and not _DAILY_AT.match(self.daily_at):
            raise ScheduleError(
                f"daily_at must be a 24-hour UTC time as HH:MM, got {self.daily_at!r}"
            )

    def next_after(self, moment: datetime) -> datetime:
        """The first firing strictly after ``moment``."""
        moment = moment.astimezone(UTC)
        if self.every_minutes is not None:
            return moment + timedelta(minutes=self.every_minutes)
        assert self.daily_at is not None
        hour, minute = (int(part) for part in self.daily_at.split(":"))
        today = datetime.combine(moment.date(), time(hour, minute), tzinfo=UTC)
        return today if today > moment else today + timedelta(days=1)

    def describe(self) -> str:
        if self.every_minutes is not None:
            return f"every {self.every_minutes} minutes"
        return f"daily at {self.daily_at} UTC"

    def as_dict(self) -> dict[str, Any]:
        if self.every_minutes is not None:
            return {"every_minutes": self.every_minutes}
        return {"daily_at": self.daily_at}

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> Cadence:
        unknown = set(raw) - {"every_minutes", "daily_at"}
        if unknown:
            raise ScheduleError(f"unknown cadence field(s): {', '.join(sorted(unknown))}")
        return cls(
            every_minutes=raw.get("every_minutes"), daily_at=raw.get("daily_at")
        )


@dataclass(slots=True)
class Schedule:
    """One recurring harvest, and the record of how it has been going."""

    id: str
    site: str
    project: str | None
    credential_reference: str
    cadence: Cadence
    next_run_at: str
    enabled: bool = True
    paused_reason: str | None = None
    last_run_at: str | None = None
    last_run_id: str | None = None
    last_run_state: str | None = None
    last_error: str | None = None
    consecutive_failures: int = 0
    created_by: str = "unknown"
    created_at: str | None = None
    parse_quality_threshold: float | None = None
    concurrency: int | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "site": self.site,
            "project": self.project,
            "credential_reference": self.credential_reference,
            "cadence": self.cadence.as_dict(),
            "cadence_description": self.cadence.describe(),
            "enabled": self.enabled,
            "paused_reason": self.paused_reason,
            "next_run_at": self.next_run_at if self.enabled else None,
            "last_run": {
                "id": self.last_run_id,
                "at": self.last_run_at,
                "state": self.last_run_state,
                "error": self.last_error,
            },
            "consecutive_failures": self.consecutive_failures,
            "created_by": self.created_by,
            "created_at": self.created_at,
        }


class ScheduleStore(Protocol):
    async def create(self, schedule: Schedule) -> Schedule: ...

    async def get(self, schedule_id: str) -> Schedule | None: ...

    async def list_schedules(self) -> list[Schedule]: ...

    async def set_enabled(
        self, schedule_id: str, *, enabled: bool, reason: str | None
    ) -> Schedule | None: ...

    async def update(
        self,
        schedule_id: str,
        *,
        cadence: Cadence | None,
        credential_reference: str | None,
        now: datetime,
    ) -> Schedule | None: ...

    async def due(self, *, now: datetime, limit: int = 10) -> list[Schedule]: ...

    async def record_run(
        self,
        schedule_id: str,
        *,
        run_id: str,
        state: str,
        error: str | None,
        finished_at: datetime,
    ) -> None: ...


def new_schedule(
    *,
    site: str,
    project: str | None,
    credential_reference: str,
    cadence: Cadence,
    created_by: str,
    now: datetime | None = None,
    parse_quality_threshold: float | None = None,
    concurrency: int | None = None,
) -> Schedule:
    """A schedule whose first firing is one cadence away, not immediately.

    Creating a schedule should not start a harvest: somebody setting up four sites would
    kick off four full runs by typing, and the first run of a site is nearly always one an
    engineer wants to watch. ``POST /v1/harvests`` is how you start one now.
    """
    moment = now or datetime.now(UTC)
    return Schedule(
        id=new_ulid(),
        site=site,
        project=project,
        credential_reference=credential_reference,
        cadence=cadence,
        next_run_at=_iso(cadence.next_after(moment)),
        created_by=created_by,
        created_at=_iso(moment),
        parse_quality_threshold=parse_quality_threshold,
        concurrency=concurrency,
    )


# --------------------------------------------------------------------------- postgres


class PostgresScheduleStore:
    """Schedules in PostgreSQL, claimed with ``FOR UPDATE SKIP LOCKED``."""

    def __init__(self, pool: Any, *, graph_name: str) -> None:
        self._pool = pool
        self._graph = graph_name

    async def create(self, schedule: Schedule) -> Schedule:
        async with self._pool.acquire() as conn:
            try:
                await conn.execute(
                    f"""
                    INSERT INTO {SCHEDULE_TABLE}
                        (id, graph, site, project, credential_reference, cadence, enabled,
                         next_run_at, created_by, parse_quality_threshold, concurrency)
                    VALUES ($1, $2, $3, $4, $5, $6::jsonb, $7, $8, $9, $10, $11)
                    """,
                    schedule.id,
                    self._graph,
                    schedule.site,
                    schedule.project,
                    schedule.credential_reference,
                    json.dumps(schedule.cadence.as_dict()),
                    schedule.enabled,
                    _parse(schedule.next_run_at),
                    schedule.created_by,
                    schedule.parse_quality_threshold,
                    schedule.concurrency,
                )
            except Exception as exc:  # asyncpg.UniqueViolationError, without the import
                if "harvest_schedule_scope" in str(exc):
                    raise ScheduleError(
                        f"a schedule already exists for {_scope_text(schedule)}; change "
                        f"that one rather than adding a second"
                    ) from exc
                raise
        return schedule

    async def get(self, schedule_id: str) -> Schedule | None:
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                f"SELECT * FROM {SCHEDULE_TABLE} WHERE graph = $1 AND id = $2",
                self._graph,
                schedule_id,
            )
        return _from_row(row) if row else None

    async def list_schedules(self) -> list[Schedule]:
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(
                f"SELECT * FROM {SCHEDULE_TABLE} WHERE graph = $1 "
                f"ORDER BY site, coalesce(project, '')",
                self._graph,
            )
        return [_from_row(row) for row in rows]

    async def set_enabled(
        self, schedule_id: str, *, enabled: bool, reason: str | None
    ) -> Schedule | None:
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                f"""
                UPDATE {SCHEDULE_TABLE}
                   SET enabled = $3,
                       paused_reason = $4,
                       updated_at = now()
                 WHERE graph = $1 AND id = $2
             RETURNING *
                """,
                self._graph,
                schedule_id,
                enabled,
                None if enabled else reason,
            )
        return _from_row(row) if row else None

    async def update(
        self,
        schedule_id: str,
        *,
        cadence: Cadence | None,
        credential_reference: str | None,
        now: datetime,
    ) -> Schedule | None:
        """Change a schedule's cadence or credential, keeping its history.

        A new cadence re-bases the next firing from now, because the old ``next_run_at``
        was computed under a cadence that no longer applies — leaving it would fire a
        "daily" schedule in eleven minutes because it used to run every quarter hour.
        """
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                f"""
                UPDATE {SCHEDULE_TABLE}
                   SET cadence = coalesce($3::jsonb, cadence),
                       credential_reference = coalesce($4, credential_reference),
                       next_run_at = coalesce($5, next_run_at),
                       updated_at = now()
                 WHERE graph = $1 AND id = $2
             RETURNING *
                """,
                self._graph,
                schedule_id,
                json.dumps(cadence.as_dict()) if cadence else None,
                credential_reference,
                cadence.next_after(now) if cadence else None,
            )
        return _from_row(row) if row else None

    async def due(self, *, now: datetime, limit: int = 10) -> list[Schedule]:
        """Claim the schedules that are due, advancing each one's next firing.

        The claim and the advance are one transaction, so a second replica polling at the
        same moment sees rows that are no longer due. ``next_run_at`` is computed from
        *now* rather than from the old value: a service that was down for a day should
        harvest once when it comes back, not once for every firing it slept through.
        """
        claimed: list[Schedule] = []
        async with self._pool.acquire() as conn, conn.transaction():
            rows = await conn.fetch(
                f"""
                SELECT * FROM {SCHEDULE_TABLE}
                 WHERE graph = $1 AND enabled AND next_run_at <= $2
                 ORDER BY next_run_at
                 LIMIT $3
                   FOR UPDATE SKIP LOCKED
                """,
                self._graph,
                now,
                limit,
            )
            for row in rows:
                schedule = _from_row(row)
                following = schedule.cadence.next_after(now)
                await conn.execute(
                    f"UPDATE {SCHEDULE_TABLE} SET next_run_at = $3, updated_at = now() "
                    f"WHERE graph = $1 AND id = $2",
                    self._graph,
                    schedule.id,
                    following,
                )
                schedule.next_run_at = _iso(following)
                claimed.append(schedule)
        return claimed

    async def record_run(
        self,
        schedule_id: str,
        *,
        run_id: str,
        state: str,
        error: str | None,
        finished_at: datetime,
    ) -> None:
        async with self._pool.acquire() as conn:
            await conn.execute(
                f"""
                UPDATE {SCHEDULE_TABLE}
                   SET last_run_id = $3,
                       last_run_at = $4,
                       last_run_state = $5,
                       last_error = $6,
                       consecutive_failures = CASE WHEN $5 = 'COMPLETED'
                                                   THEN 0
                                                   ELSE consecutive_failures + 1 END,
                       updated_at = now()
                 WHERE graph = $1 AND id = $2
                """,
                self._graph,
                schedule_id,
                run_id,
                finished_at,
                state,
                error,
            )


# -------------------------------------------------------------------------- in-memory


class InMemoryScheduleStore:
    """The same contract without a database. Unit tests and the fixture stack."""

    def __init__(self) -> None:
        self.schedules: dict[str, Schedule] = {}

    async def create(self, schedule: Schedule) -> Schedule:
        for existing in self.schedules.values():
            if (existing.site, existing.project) == (schedule.site, schedule.project):
                raise ScheduleError(
                    f"a schedule already exists for {_scope_text(schedule)}; change that "
                    f"one rather than adding a second"
                )
        self.schedules[schedule.id] = schedule
        return schedule

    async def get(self, schedule_id: str) -> Schedule | None:
        return self.schedules.get(schedule_id)

    async def list_schedules(self) -> list[Schedule]:
        return sorted(self.schedules.values(), key=lambda s: (s.site, s.project or ""))

    async def set_enabled(
        self, schedule_id: str, *, enabled: bool, reason: str | None
    ) -> Schedule | None:
        schedule = self.schedules.get(schedule_id)
        if schedule is None:
            return None
        schedule.enabled = enabled
        schedule.paused_reason = None if enabled else reason
        return schedule

    async def update(
        self,
        schedule_id: str,
        *,
        cadence: Cadence | None,
        credential_reference: str | None,
        now: datetime,
    ) -> Schedule | None:
        schedule = self.schedules.get(schedule_id)
        if schedule is None:
            return None
        if cadence is not None:
            schedule.cadence = cadence
            schedule.next_run_at = _iso(cadence.next_after(now))
        if credential_reference is not None:
            schedule.credential_reference = credential_reference
        return schedule

    async def due(self, *, now: datetime, limit: int = 10) -> list[Schedule]:
        ready = sorted(
            (
                s
                for s in self.schedules.values()
                if s.enabled and _parse(s.next_run_at) <= now
            ),
            key=lambda s: s.next_run_at,
        )[:limit]
        for schedule in ready:
            schedule.next_run_at = _iso(schedule.cadence.next_after(now))
        return ready

    async def record_run(
        self,
        schedule_id: str,
        *,
        run_id: str,
        state: str,
        error: str | None,
        finished_at: datetime,
    ) -> None:
        schedule = self.schedules.get(schedule_id)
        if schedule is None:
            return
        schedule.last_run_id = run_id
        schedule.last_run_at = _iso(finished_at)
        schedule.last_run_state = state
        schedule.last_error = error
        schedule.consecutive_failures = (
            0 if state == "COMPLETED" else schedule.consecutive_failures + 1
        )


# --------------------------------------------------------------------------- helpers


def _scope_text(schedule: Schedule) -> str:
    if schedule.project:
        return f"site '{schedule.site}', project '{schedule.project}'"
    return f"site '{schedule.site}'"


def _iso(moment: datetime) -> str:
    return (
        moment.astimezone(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")
    )


def _parse(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(UTC)


def _from_row(row: Any) -> Schedule:
    cadence_raw = row["cadence"]
    cadence = Cadence.from_dict(
        json.loads(cadence_raw) if isinstance(cadence_raw, str) else dict(cadence_raw)
    )
    return Schedule(
        id=row["id"],
        site=row["site"],
        project=row["project"],
        credential_reference=row["credential_reference"],
        cadence=cadence,
        next_run_at=_iso(row["next_run_at"]),
        enabled=row["enabled"],
        paused_reason=row["paused_reason"],
        last_run_at=_iso(row["last_run_at"]) if row["last_run_at"] else None,
        last_run_id=row["last_run_id"],
        last_run_state=row["last_run_state"],
        last_error=row["last_error"],
        consecutive_failures=row["consecutive_failures"],
        created_by=row["created_by"],
        created_at=_iso(row["created_at"]) if row["created_at"] else None,
        parse_quality_threshold=row["parse_quality_threshold"],
        concurrency=row["concurrency"],
    )


__all__ = [
    "MAX_INTERVAL_MINUTES",
    "MIN_INTERVAL_MINUTES",
    "SCHEDULE_TABLE",
    "Cadence",
    "InMemoryScheduleStore",
    "PostgresScheduleStore",
    "Schedule",
    "ScheduleError",
    "ScheduleStore",
    "new_schedule",
]
