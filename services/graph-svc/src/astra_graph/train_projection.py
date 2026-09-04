"""Projected versus planned dates per train — story S3.2.3.

    "Projection uses measured throughput per state over the trailing 14 days and the MU
    counts remaining; shown as a date with a confidence band. A train projected to miss its
    planned date by more than 5 working days is flagged on the Programme Board."

**Throughput is measured from the event stream, never simulated.** An MU's ``IN_TRAIN``
state is set once, at proposal time (``trains.py``'s ``TrainPlanner.run``, to
``DEFAULT_MU_STATE``) and changes only if something actually drives it through §3.2's
pipeline — the wave scheduler (§14.2, backlog S12.1.2), which this codebase has not built.
This module does not pretend otherwise: it reads the real ``estate.edge.upserted`` history
for every ``IN_TRAIN`` edge, using a SQL window function to find genuine state
*transitions* (an edge re-upserted for an unrelated reason — a Wave Board resequence,
S3.2.2 — carries its state forward unchanged and is correctly not counted as an exit). When
a state has zero measured exits in the trailing window — true for every state in this
estate today, since nothing yet drives a transition — the projection honestly reports
"insufficient data," never a fabricated date. See ADR 0027.

**A projection is a bottleneck estimate, not a full multi-stage simulation.** For each
train, this measures how long it will take to clear the MUs sitting at each state they
occupy *today*, using that state's own measured throughput, and reports the slowest
(bottleneck) of those as the train's projected finish — the same logic behind any
single-resource queueing estimate. It does not simulate each MU's remaining hops through
every state still ahead of it; a full discrete-event simulation of §3.2's transition graph
is real future scope this story does not claim, and an honest partial measure beats a
precise-looking guess extrapolated from data that does not exist yet.

**Throughput is measured in calendar days; the late flag is worked out in working days.**
The story asks for both, for different things: a daily exit rate is what "trailing 14
days" naturally means, and "5 working days" late is the flag's own stated unit. Converting
between them for every intermediate figure would just be a second place the two could
disagree; only the final flag comparison counts working days.
"""

from __future__ import annotations

import math
import statistics
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from typing import Any

import asyncpg

from .events import EventType

EVENT_TABLE = "public.estate_event"

#: The story's own figures. No derivation for either exists in the spec.
DEFAULT_TRAILING_DAYS = 14
DEFAULT_LATE_THRESHOLD_WORKING_DAYS = 5

_INSUFFICIENT_DATA = (
    "no measured throughput for any state this train's MUs currently occupy — nothing in "
    "this estate has yet transitioned an MU's state, so there is nothing to project from"
)


@dataclass(frozen=True, slots=True)
class StateThroughput:
    """One state's measured daily exit rate over the trailing window."""

    state: str
    exits: int
    """Distinct MUs that transitioned out of this state in the window."""
    daily_mean: float
    daily_stddev: float

    @property
    def has_data(self) -> bool:
        return self.daily_mean > 0

    def as_dict(self) -> dict[str, Any]:
        return {
            "state": self.state,
            "exits": self.exits,
            "daily_mean": round(self.daily_mean, 3),
            "daily_stddev": round(self.daily_stddev, 3),
        }


@dataclass(frozen=True, slots=True)
class TrainProjection:
    train_id: str
    train_name: str
    planned_end: str | None
    bottleneck_state: str | None
    remaining_in_bottleneck: int
    projected_end: str | None
    projected_end_early: str | None
    """The optimistic bound of the confidence band (mean + 1 stddev throughput)."""
    projected_end_late: str | None
    """The pessimistic bound (mean - 1 stddev throughput), absent when that would be at or
    below zero — an unbounded pessimistic date is not a date, it is a "cannot say"."""
    days_late: int | None
    """Working days ``projected_end`` falls after ``planned_end``; negative means early."""
    flagged: bool
    reason: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "train_id": self.train_id,
            "train_name": self.train_name,
            "planned_end": self.planned_end,
            "bottleneck_state": self.bottleneck_state,
            "remaining_in_bottleneck": self.remaining_in_bottleneck,
            "projected_end": self.projected_end,
            "projected_end_early": self.projected_end_early,
            "projected_end_late": self.projected_end_late,
            "days_late": self.days_late,
            "flagged": self.flagged,
            "reason": self.reason,
        }


def working_days_between(start: date, end: date) -> int:
    """Positive when ``end`` is after ``start``; counts Mon-Fri only, matching the story's
    own "5 working days" — a calendar-day count would flag a train differently depending
    on which weekday its planned date happened to land on."""
    if end == start:
        return 0
    step = 1 if end > start else -1
    count = 0
    current = start
    while current != end:
        current += timedelta(days=step)
        if current.weekday() < 5:
            count += step
    return count


async def estate_throughput(
    pool: asyncpg.Pool,
    graph_name: str,
    *,
    trailing_days: int = DEFAULT_TRAILING_DAYS,
    now: date | None = None,
) -> dict[str, StateThroughput]:
    """Every state's measured exit rate, read from real ``IN_TRAIN`` event history.

    A ``LAG`` window function over each edge's own event history (ordered by sequence)
    finds genuine transitions — a row where the state differs from that same edge's
    immediately preceding state. An edge re-upserted for an unrelated reason (a Wave
    Board resequence carries its state forward unchanged) never produces a row here.

    Days are bucketed by ``time AT TIME ZONE 'UTC'``, explicitly — ``date_trunc('day',
    ...)`` alone truncates in the *session's* timezone, which need not be UTC, and this
    module's own daily buckets (``reference - timedelta(days=...)``) are computed in UTC
    calendar days. A mismatch here would silently drop or double-count a boundary day.
    """
    reference = now or date.today()
    since = datetime.combine(
        reference - timedelta(days=trailing_days), datetime.min.time(), tzinfo=UTC
    )

    async with pool.acquire() as conn:
        rows = await conn.fetch(
            f"""
            WITH state_events AS (
                SELECT
                    time,
                    subject AS edge_id,
                    data -> 'properties' ->> 'state' AS state,
                    LAG(data -> 'properties' ->> 'state') OVER (
                        PARTITION BY subject ORDER BY seq
                    ) AS previous_state
                FROM {EVENT_TABLE}
                WHERE graph = $1 AND type = $2 AND label = 'IN_TRAIN'
            )
            SELECT
                previous_state AS from_state,
                (time AT TIME ZONE 'UTC')::date AS day,
                count(DISTINCT edge_id) AS exits
            FROM state_events
            WHERE previous_state IS NOT NULL
              AND previous_state IS DISTINCT FROM state
              AND time >= $3
            GROUP BY previous_state, (time AT TIME ZONE 'UTC')::date
            """,
            graph_name,
            EventType.EDGE_UPSERTED.value,
            since,
        )

    daily: dict[str, dict[date, int]] = {}
    for row in rows:
        daily.setdefault(row["from_state"], {})[row["day"]] = row["exits"]

    window_days = [reference - timedelta(days=offset) for offset in range(trailing_days)]
    throughput: dict[str, StateThroughput] = {}
    for state, by_day in daily.items():
        series = [by_day.get(day, 0) for day in window_days]
        throughput[state] = StateThroughput(
            state=state,
            exits=sum(series),
            daily_mean=statistics.mean(series),
            daily_stddev=statistics.pstdev(series) if len(series) > 1 else 0.0,
        )
    return throughput


def project_state(
    throughput: StateThroughput | None, remaining: int, *, reference: date
) -> tuple[date | None, date | None, date | None]:
    """(point, early, late) projected-clear dates for one state's remaining MUs, or
    ``(None, None, None)`` when the throughput measured for it cannot support an estimate."""
    if throughput is None or not throughput.has_data or remaining <= 0:
        return None, None, None

    def _end(rate: float) -> date | None:
        if rate <= 0:
            return None
        return reference + timedelta(days=math.ceil(remaining / rate))

    point = _end(throughput.daily_mean)
    early = _end(throughput.daily_mean + throughput.daily_stddev)
    late = _end(throughput.daily_mean - throughput.daily_stddev)
    return point, early, late


async def project_trains(
    pool: asyncpg.Pool,
    graph_name: str,
    trains: Sequence[dict[str, Any]],
    *,
    trailing_days: int = DEFAULT_TRAILING_DAYS,
    late_threshold_working_days: int = DEFAULT_LATE_THRESHOLD_WORKING_DAYS,
    now: date | None = None,
) -> list[TrainProjection]:
    """One projection per train in ``trains`` (as returned by ``trains.list_trains``),
    sharing a single estate-wide throughput measurement rather than recomputing it once
    per train."""
    reference = now or date.today()
    throughput = await estate_throughput(pool, graph_name, trailing_days=trailing_days, now=reference)

    projections: list[TrainProjection] = []
    for train in trains:
        members = train.get("members") or []
        by_state: dict[str, int] = {}
        for member in members:
            by_state[member["state"]] = by_state.get(member["state"], 0) + 1

        candidates: list[tuple[str, int, date, date | None, date | None]] = []
        unestimable_states: list[str] = []
        for state, remaining in by_state.items():
            point, early, late = project_state(
                throughput.get(state), remaining, reference=reference
            )
            if point is None:
                unestimable_states.append(state)
                continue
            candidates.append((state, remaining, point, early, late))

        planned_end = train.get("planned_end")
        planned_end_date = date.fromisoformat(planned_end) if planned_end else None

        if not candidates:
            reason = _INSUFFICIENT_DATA
            if unestimable_states:
                named = sorted(set(unestimable_states))
                reason = (
                    f"no measured throughput for {', '.join(named)} — nothing has yet "
                    f"transitioned an MU out of {'that state' if len(named) == 1 else 'these states'}"
                )
            projections.append(
                TrainProjection(
                    train_id=train["id"],
                    train_name=train.get("name") or train["id"],
                    planned_end=planned_end,
                    bottleneck_state=None,
                    remaining_in_bottleneck=0,
                    projected_end=None,
                    projected_end_early=None,
                    projected_end_late=None,
                    days_late=None,
                    flagged=False,
                    reason=reason,
                )
            )
            continue

        # The bottleneck is whichever occupied state takes longest to clear on its own.
        bottleneck_state, remaining, point, early, late = max(candidates, key=lambda c: c[2])

        days_late = (
            working_days_between(planned_end_date, point) if planned_end_date else None
        )
        flagged = days_late is not None and days_late > late_threshold_working_days

        reason = (
            f"bottleneck is {bottleneck_state} ({remaining} MU"
            f"{'s' if remaining != 1 else ''} remaining there, "
            f"{throughput[bottleneck_state].daily_mean:.2f}/day measured over the "
            f"trailing {trailing_days} days)"
        )
        if unestimable_states:
            named = sorted(set(unestimable_states))
            reason += f"; {', '.join(named)} could not be estimated and are excluded from this projection"

        projections.append(
            TrainProjection(
                train_id=train["id"],
                train_name=train.get("name") or train["id"],
                planned_end=planned_end,
                bottleneck_state=bottleneck_state,
                remaining_in_bottleneck=remaining,
                projected_end=point.isoformat(),
                projected_end_early=early.isoformat() if early else None,
                projected_end_late=late.isoformat() if late else None,
                days_late=days_late,
                flagged=flagged,
                reason=reason,
            )
        )
    return projections


__all__ = [
    "DEFAULT_LATE_THRESHOLD_WORKING_DAYS",
    "DEFAULT_TRAILING_DAYS",
    "StateThroughput",
    "TrainProjection",
    "estate_throughput",
    "project_state",
    "project_trains",
    "working_days_between",
]
