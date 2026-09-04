"""How long a graph version stays answerable.

S1.3.2: "retention for versions is the programme lifetime plus 12 months".

A graph version is an event offset (``versions.py``), so retaining a version means
retaining the events at and below it. Retention is therefore a statement about the outbox,
and it is the reason nothing in this service deletes an event.

**The floor is computed, not configured.** A programme that is still running has no end
date, so its retention floor is open: nothing may be pruned at all. A closed programme's
floor is its close date plus twelve months. Expressing it as a computation rather than a
number means a programme that runs eighteen months longer than planned does not silently
lose the first year of its own evidence because somebody set a date once.

**Nothing prunes today, and that is deliberate.** There is no pruner, no scheduled
deletion, no TTL. What exists is ``prunable_before``, which any future pruner has to ask
and which refuses while a programme is open. Building the policy before the deletion is
the right order: an audit trail that was pruned by a job written before anybody decided
the rule is not an audit trail.

The programme record here is the minimum §21 needs for this question — id, name, when it
started, whether it has closed. The rest of §21's ``programme`` columns (charter version,
calibration baseline, scope) arrive with the epics that read them.

**S3.1.1 is the first of those epics.** ``clustering`` carries the Cartographer's latest
run — family count, distribution, the member-count histogram — because "family count...
becomes a measured number in Month 1" (S3.1.1) is exactly the kind of figure this record
exists to hold. See ``migrations/versions/v0012_clustering_record.py``.

**S3.1.3 is the second.** ``family_count``/``family_count_confirmed_at``/
``family_count_confirmed_by`` are a Programme Manager's own confirmation that the measured
count is *the* count — distinct from ``clustering``, which is only ever the last run's
figures and can be overwritten by a re-cluster nobody has signed off on. See
``migrations/versions/v0014_family_count_confirmation.py``.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Protocol

PROGRAMME_TABLE = "public.programme"

#: S1.3.2. Months past the end of the programme that versions stay addressable.
RETENTION_MONTHS = 12

#: §14.3 / Appendix A: "~150 shared governed models (planning assumption, measured in
#: Month 1)". A spec constant, not a per-programme value — every programme is measured
#: against the same assumption until the spec itself revises it.
PLANNED_FAMILY_COUNT = 150


@dataclass(frozen=True, slots=True)
class Programme:
    """A migration programme, as far as retention is concerned."""

    id: str
    name: str
    started_at: str
    closed_at: str | None = None
    clustering: dict[str, Any] | None = None
    """The Cartographer's latest clustering run (story S3.1.1). ``None`` until one has run."""

    family_count: int | None = None
    """The Programme Manager's confirmed measured family count (story S3.1.3). ``None``
    until confirmed — distinct from ``clustering["family_count"]``, which is only ever the
    last run's figure and carries no one's sign-off."""

    family_count_confirmed_at: str | None = None
    family_count_confirmed_by: str | None = None

    @property
    def open(self) -> bool:
        return self.closed_at is None

    def retain_until(self) -> str | None:
        """When the earliest version may first be pruned. ``None`` while open."""
        if self.closed_at is None:
            return None
        return _iso(_add_months(_parse(self.closed_at), RETENTION_MONTHS))

    def as_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "started_at": self.started_at,
            "closed_at": self.closed_at,
            "open": self.open,
            "retain_until": self.retain_until(),
            "clustering": self.clustering,
            "family_count": self.family_count,
            "family_count_confirmed_at": self.family_count_confirmed_at,
            "family_count_confirmed_by": self.family_count_confirmed_by,
            "planned_family_count": PLANNED_FAMILY_COUNT,
            "family_count_delta": (
                self.family_count - PLANNED_FAMILY_COUNT if self.family_count is not None else None
            ),
        }


class ProgrammeStore(Protocol):
    async def programmes(self) -> list[Programme]: ...

    async def open_programme(
        self, *, name: str, started_at: str, created_by: str
    ) -> Programme: ...

    async def close_programme(self, programme_id: str, *, closed_at: str) -> Programme | None: ...

    async def record_clustering(
        self, programme_id: str, *, stats: dict[str, Any], principal: str
    ) -> Programme | None:
        """Overwrite the programme's clustering figures with a fresh run's (story S3.1.1).

        Overwritten rather than appended: this record answers "what did the last run find",
        not "what has every run ever found" — see the migration's own reasoning for why a
        run-history table is not what this criterion asks for.
        """
        ...

    async def confirm_family_count(
        self, programme_id: str, *, count: int, confirmed_by: str
    ) -> Programme | None:
        """Stamp the measured family count, who confirmed it and when (story S3.1.3).

        Overwrites rather than appends, the same as ``record_clustering`` — a later
        confirmation supersedes an earlier one; it does not need a history of every time
        someone pressed the button.
        """
        ...


class PostgresProgrammeStore:
    """Programmes in PostgreSQL. Graph-scoped, like everything else platform-side."""

    def __init__(self, pool: Any, *, graph_name: str) -> None:
        self._pool = pool
        self._graph = graph_name

    async def programmes(self) -> list[Programme]:
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(
                f"SELECT * FROM {PROGRAMME_TABLE} WHERE graph = $1 ORDER BY started_at",
                self._graph,
            )
        return [_from_row(row) for row in rows]

    async def open_programme(
        self, *, name: str, started_at: str, created_by: str
    ) -> Programme:
        from .ids import new_ulid

        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                f"""
                INSERT INTO {PROGRAMME_TABLE} (id, graph, name, started_at, created_by)
                VALUES ($1, $2, $3, $4, $5)
             RETURNING *
                """,
                f"prg_{new_ulid()}",
                self._graph,
                name,
                _parse(started_at),
                created_by,
            )
        return _from_row(row)

    async def close_programme(self, programme_id: str, *, closed_at: str) -> Programme | None:
        """Closing starts the retention clock; it does not delete anything.

        Idempotent by refusal rather than by overwrite: re-closing with a different date
        would move the retention floor, and a floor that can be moved is not a floor.
        """
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                f"""
                UPDATE {PROGRAMME_TABLE}
                   SET closed_at = $3
                 WHERE graph = $1 AND id = $2 AND closed_at IS NULL
             RETURNING *
                """,
                self._graph,
                programme_id,
                _parse(closed_at),
            )
        return _from_row(row) if row else None

    async def record_clustering(
        self, programme_id: str, *, stats: dict[str, Any], principal: str
    ) -> Programme | None:
        import json

        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                f"""
                UPDATE {PROGRAMME_TABLE}
                   SET clustering_json = $3::jsonb
                 WHERE graph = $1 AND id = $2
             RETURNING *
                """,
                self._graph,
                programme_id,
                json.dumps(stats),
            )
        return _from_row(row) if row else None

    async def confirm_family_count(
        self, programme_id: str, *, count: int, confirmed_by: str
    ) -> Programme | None:
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                f"""
                UPDATE {PROGRAMME_TABLE}
                   SET family_count = $3,
                       family_count_confirmed_at = now(),
                       family_count_confirmed_by = $4
                 WHERE graph = $1 AND id = $2
             RETURNING *
                """,
                self._graph,
                programme_id,
                count,
                confirmed_by,
            )
        return _from_row(row) if row else None


class InMemoryProgrammeStore:
    """The same contract without a database."""

    def __init__(self, programmes: list[Programme] | None = None) -> None:
        self._programmes = {p.id: p for p in (programmes or [])}

    async def programmes(self) -> list[Programme]:
        return sorted(self._programmes.values(), key=lambda p: p.started_at)

    async def open_programme(
        self, *, name: str, started_at: str, created_by: str
    ) -> Programme:
        from .ids import new_ulid

        programme = Programme(id=f"prg_{new_ulid()}", name=name, started_at=started_at)
        self._programmes[programme.id] = programme
        return programme

    async def close_programme(self, programme_id: str, *, closed_at: str) -> Programme | None:
        from dataclasses import replace

        programme = self._programmes.get(programme_id)
        if programme is None or programme.closed_at is not None:
            return None
        closed = replace(programme, closed_at=closed_at)
        self._programmes[programme_id] = closed
        return closed

    async def record_clustering(
        self, programme_id: str, *, stats: dict[str, Any], principal: str
    ) -> Programme | None:
        from dataclasses import replace

        programme = self._programmes.get(programme_id)
        if programme is None:
            return None
        updated = replace(programme, clustering=stats)
        self._programmes[programme_id] = updated
        return updated

    async def confirm_family_count(
        self, programme_id: str, *, count: int, confirmed_by: str
    ) -> Programme | None:
        from dataclasses import replace

        programme = self._programmes.get(programme_id)
        if programme is None:
            return None
        updated = replace(
            programme,
            family_count=count,
            family_count_confirmed_at=_iso(datetime.now(UTC)),
            family_count_confirmed_by=confirmed_by,
        )
        self._programmes[programme_id] = updated
        return updated


@dataclass(frozen=True, slots=True)
class RetentionState:
    """What the retention policy currently permits."""

    policy: str
    programmes: list[Programme]
    prunable_before: str | None
    """The instant before which events may be deleted. ``None`` means nothing may be."""

    reason: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "policy": self.policy,
            "prunable_before": self.prunable_before,
            "reason": self.reason,
            "programmes": [programme.as_dict() for programme in self.programmes],
        }


POLICY = f"programme lifetime plus {RETENTION_MONTHS} months"


def prunable_before(programmes: list[Programme], *, now: datetime | None = None) -> RetentionState:
    """The cutoff any pruner must respect.

    Three cases, and only the third permits anything:

    * no programme is recorded — nothing may be pruned, because the platform cannot tell
      whether it is holding evidence for one. An empty table is not permission;
    * any programme is still open — nothing may be pruned, because its own evidence is
      still accruing and its lifetime has no end yet;
    * every programme has closed — the cutoff is the *earliest* close plus twelve months,
      and only if that has already passed. The earliest rather than the latest, because a
      cutoff has to be safe for every programme sharing this graph.
    """
    moment = (now or datetime.now(UTC)).astimezone(UTC)

    if not programmes:
        return RetentionState(
            policy=POLICY,
            programmes=[],
            prunable_before=None,
            reason=(
                "no programme is recorded, so the platform cannot tell whether it is "
                "holding evidence for one. Nothing may be pruned."
            ),
        )

    still_open = [programme for programme in programmes if programme.open]
    if still_open:
        names = ", ".join(sorted(programme.name for programme in still_open))
        return RetentionState(
            policy=POLICY,
            programmes=programmes,
            prunable_before=None,
            reason=f"{names} still running, so every version remains addressable.",
        )

    floors = [
        _add_months(_parse(str(programme.closed_at)), RETENTION_MONTHS)
        for programme in programmes
    ]
    cutoff = min(floors)
    if cutoff > moment:
        return RetentionState(
            policy=POLICY,
            programmes=programmes,
            prunable_before=None,
            reason=(
                f"every programme has closed, but the retention floor is {_iso(cutoff)}, "
                f"which has not passed."
            ),
        )
    return RetentionState(
        policy=POLICY,
        programmes=programmes,
        prunable_before=_iso(cutoff),
        reason=(
            f"every programme closed more than {RETENTION_MONTHS} months ago; events "
            f"committed before {_iso(cutoff)} are outside the retention floor. Note that "
            f"nothing in this service prunes them."
        ),
    )


# --------------------------------------------------------------------------- helpers


def _add_months(moment: datetime, months: int) -> datetime:
    """Calendar months, not 30-day approximations.

    A retention floor a client's auditor reads as "a year after we closed" should fall on
    the anniversary, and 365 days is not that in a leap year. The day is clamped for the
    months that are short, so 31 August plus six months is 28 February.
    """
    month_index = moment.month - 1 + months
    year = moment.year + month_index // 12
    month = month_index % 12 + 1
    day = min(moment.day, _days_in_month(year, month))
    return moment.replace(year=year, month=month, day=day)


def _days_in_month(year: int, month: int) -> int:
    if month == 2:
        leap = year % 4 == 0 and (year % 100 != 0 or year % 400 == 0)
        return 29 if leap else 28
    return 30 if month in {4, 6, 9, 11} else 31


def _from_row(row: Any) -> Programme:
    import json

    clustering_raw = row["clustering_json"]
    return Programme(
        id=row["id"],
        name=row["name"],
        started_at=_iso(row["started_at"]),
        closed_at=_iso(row["closed_at"]) if row["closed_at"] else None,
        clustering=json.loads(clustering_raw) if clustering_raw else None,
        family_count=row["family_count"],
        family_count_confirmed_at=(
            _iso(row["family_count_confirmed_at"]) if row["family_count_confirmed_at"] else None
        ),
        family_count_confirmed_by=row["family_count_confirmed_by"],
    )


def _parse(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(UTC)


def _iso(moment: datetime) -> str:
    return moment.astimezone(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")


__all__ = [
    "PLANNED_FAMILY_COUNT",
    "POLICY",
    "PROGRAMME_TABLE",
    "RETENTION_MONTHS",
    "InMemoryProgrammeStore",
    "PostgresProgrammeStore",
    "Programme",
    "ProgrammeStore",
    "RetentionState",
    "prunable_before",
]
