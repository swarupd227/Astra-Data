"""Confidence calibration — specification §16.3, story S5.3.3.

    "Model declares a confidence in the output schema; the platform records it and, per
    §16.3, reports calibration (declared vs observed proof rate) in ten buckets. Below a
    configurable calibration floor a task class is routed to the small-model-plus-proof
    path rather than trusted."

**What this module is.** A real, Postgres-backed record of every declared confidence a
generation call ever produced (`PostgresCalibrationStore.record` — one row per
`LadderAttempt` that got far enough to have one, for both successful and failed/declined
generations, not only survivors: a curve built only from successes would be trivially 100%
at every bucket), and a real ten-bucket calibration report computed from that history
(`build_report`) — declared vs observed, plus §16.6's own "mean |declared - observed|"
calibration-error metric — and the plain floor check (`is_below_floor`) this story's own
routing decision reads.

**"Observed" is the same real, disclosed proxy for proof this epic has already established
twice.** §16.3's own worked example ("declared 0.9 passing at 0.7") means passing *real*
proof — no Arbiter exists to grant one (E7, the identical gap ADR 0038/0039 already
disclosed for the ladder and the eval gate). "Observed pass" here means exactly what those
two already meant: a candidate that cleared rung 1 (schema) and rung 2 (parse) on its own
attempt — `LadderAttempt.parse_ok`. Not a stand-in dressed up as more; the same honest
floor, a third time, for the same reason.

**The small-model-plus-proof path is a disclosed-absent routing destination, not a second
model.** No story anywhere in this backlog stands up a real small-model provider — "small
model tier" is only ever a routing *name* (§5.4/§16.3), never an integration task, and a
real second model was this story's own explicit scope decision to *not* build. `gateway.py`
names a second, real `TaskClass` for it (`TRANSPILE_C3_SMALL_MODEL`); the routing decision
that picks it, below, is real. No `ModelCaller` is ever registered under it, so a
calibration-triggered reroute correctly finds nothing routable and raises
`GatewayRoutingError` — the identical "disclosed absent, not a fake failing provider"
footing `gateway.py`'s own module docstring already gives Azure OpenAI.

**Once triggered, a reroute stays triggered — by design, not by omission.** With no real
small-model provider, every subsequent call to a rerouted task class immediately raises
`GatewayRoutingError` before any model is ever called, so no new observations are ever
recorded for it and the calibration behind the reroute can never move again on its own.
This is not a bug this module failed to close: §16.3's own wording is "pins routing to the
reasoning tier until *reviewed*" — a sticky, human-reviewed state by the spec's own design,
not something meant to self-heal. Building the review/un-pin workflow is real, separate,
unbuilt scope (S13.2.2-adjacent), not silently assumed here.

**No console screen.** A calibration-curve *screen* is S13.2.2's own later, explicit
differentiator ("flagged on the Pattern Library", F13.2, milestone I6, after the
Calibration Wave — E13/F13.1 — exists to generate real evaluation data at scale). This
story's own acceptance criteria asks for a report, not a screen; building one now would
duplicate scope a sibling story already, explicitly, owns.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol

import asyncpg

from .ids import new_ulid

CALIBRATION_TABLE = "public.calibration_observation"

#: §16.6's own "Class 3 proof rate >= 0.80" target, and `gateway.ROUTABLE_THRESHOLD`'s own
#: number — reused rather than a second, arbitrary threshold invented for this story.
DEFAULT_CALIBRATION_FLOOR = 0.80

#: Fewer observations than this and a task class's calibration is undetermined, not "below
#: the floor" — §16.3's own example is about drift *from* a measured calibration, not a
#: judgement made on no data at all. Ten, matching this story's own "ten buckets".
MIN_OBSERVATIONS_FOR_FLOOR_CHECK = 10

_BUCKET_COUNT = 10


@dataclass(frozen=True, slots=True)
class CalibrationBucket:
    """One of the AC's own "ten buckets" — `[index/10, (index+1)/10)`, except the last,
    which includes 1.0. `None` fields mean no observation ever landed in this bucket, not
    zero."""

    index: int
    lower: float
    upper: float
    count: int
    passed: int
    mean_declared: float | None
    observed_pass_rate: float | None

    def as_dict(self) -> dict[str, Any]:
        return {
            "index": self.index,
            "lower": self.lower,
            "upper": self.upper,
            "count": self.count,
            "passed": self.passed,
            "mean_declared": self.mean_declared,
            "observed_pass_rate": self.observed_pass_rate,
        }


@dataclass(frozen=True, slots=True)
class CalibrationReport:
    task_class: str
    floor: float
    total_observations: int
    overall_pass_rate: float | None
    calibration_error: float | None
    """§16.6's own "mean |declared - observed| across confidence buckets" — averaged only
    over buckets that have at least one observation; an empty bucket contributes no term,
    the same way it contributes no evidence either way."""
    buckets: tuple[CalibrationBucket, ...]
    below_floor: bool
    routable_tier: str
    """"reasoning" or "small_model_plus_proof" — the AC's own routing destination names,
    disclosed here as data rather than left implicit in `below_floor` alone."""

    def as_dict(self) -> dict[str, Any]:
        return {
            "task_class": self.task_class,
            "floor": self.floor,
            "total_observations": self.total_observations,
            "overall_pass_rate": self.overall_pass_rate,
            "calibration_error": self.calibration_error,
            "below_floor": self.below_floor,
            "routable_tier": self.routable_tier,
            "buckets": [b.as_dict() for b in self.buckets],
        }


def _bucket_index(confidence: float) -> int:
    clamped = min(max(confidence, 0.0), 1.0)
    return min(int(clamped * _BUCKET_COUNT), _BUCKET_COUNT - 1)


def build_report(
    observations: list[tuple[float, bool]],
    *,
    task_class: str,
    floor: float = DEFAULT_CALIBRATION_FLOOR,
) -> CalibrationReport:
    """Pure aggregation: given every `(declared_confidence, observed_pass)` pair recorded
    for a task class, computes the ten-bucket report. Kept separate from the store so the
    arithmetic is unit-testable without a database."""
    buckets_raw: list[list[tuple[float, bool]]] = [[] for _ in range(_BUCKET_COUNT)]
    for confidence, observed_pass in observations:
        buckets_raw[_bucket_index(confidence)].append((confidence, observed_pass))

    buckets: list[CalibrationBucket] = []
    error_terms: list[float] = []
    for i, rows in enumerate(buckets_raw):
        lower, upper = i / _BUCKET_COUNT, (i + 1) / _BUCKET_COUNT
        if not rows:
            buckets.append(CalibrationBucket(i, lower, upper, 0, 0, None, None))
            continue
        count = len(rows)
        passed = sum(1 for _, p in rows if p)
        mean_declared = sum(c for c, _ in rows) / count
        observed_rate = passed / count
        buckets.append(CalibrationBucket(i, lower, upper, count, passed, mean_declared, observed_rate))
        error_terms.append(abs(mean_declared - observed_rate))

    total = len(observations)
    overall_pass_rate = (sum(1 for _, p in observations if p) / total) if total else None
    calibration_error = (sum(error_terms) / len(error_terms)) if error_terms else None
    below_floor = (
        total >= MIN_OBSERVATIONS_FOR_FLOOR_CHECK
        and overall_pass_rate is not None
        and overall_pass_rate < floor
    )
    return CalibrationReport(
        task_class=task_class,
        floor=floor,
        total_observations=total,
        overall_pass_rate=overall_pass_rate,
        calibration_error=calibration_error,
        buckets=tuple(buckets),
        below_floor=below_floor,
        routable_tier="small_model_plus_proof" if below_floor else "reasoning",
    )


class CalibrationStore(Protocol):
    async def record(
        self,
        *,
        task_class: str,
        agent: str,
        model: str,
        provider: str,
        confidence: float,
        observed_pass: bool,
        created_by: str,
    ) -> None: ...

    async def report(
        self, task_class: str, *, floor: float = DEFAULT_CALIBRATION_FLOOR
    ) -> CalibrationReport: ...

    async def is_below_floor(
        self, task_class: str, *, floor: float = DEFAULT_CALIBRATION_FLOOR
    ) -> bool: ...


class NullCalibrationStore:
    """No calibration data has ever been recorded — the honest default before a real store
    is wired. `record` is silently a no-op: dropping a calibration observation is a real,
    but soft, loss, unlike `gateway.NullGatewayPolicyStore` (whose caller has no generation
    left to run at all without a real gateway). `is_below_floor` always says no, matching
    "no data yet" rather than "already failing", so generation is never blocked by this
    store being absent."""

    async def record(
        self,
        *,
        task_class: str,
        agent: str,
        model: str,
        provider: str,
        confidence: float,
        observed_pass: bool,
        created_by: str,
    ) -> None:
        return None

    async def report(
        self, task_class: str, *, floor: float = DEFAULT_CALIBRATION_FLOOR
    ) -> CalibrationReport:
        return build_report([], task_class=task_class, floor=floor)

    async def is_below_floor(
        self, task_class: str, *, floor: float = DEFAULT_CALIBRATION_FLOOR
    ) -> bool:
        return False


class PostgresCalibrationStore:
    """Append-only, the same footing `gateway.PostgresGatewayPolicyStore` already set for
    S5.3.2's own eval history: every observation is a new row, never an update, so the
    full history a report is computed from is always the complete one this platform has
    ever seen."""

    def __init__(self, pool: asyncpg.Pool, *, graph_name: str) -> None:
        self._pool = pool
        self._graph = graph_name

    async def record(
        self,
        *,
        task_class: str,
        agent: str,
        model: str,
        provider: str,
        confidence: float,
        observed_pass: bool,
        created_by: str,
    ) -> None:
        async with self._pool.acquire() as conn:
            await conn.execute(
                f"""INSERT INTO {CALIBRATION_TABLE}
                 (id, graph, task_class, agent, model, provider, declared_confidence,
                  observed_pass, created_by, recorded_at)
                 VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, now())""",
                f"calib_{new_ulid()}",
                self._graph,
                task_class,
                agent,
                model,
                provider,
                confidence,
                observed_pass,
                created_by,
            )

    async def _observations(self, task_class: str) -> list[tuple[float, bool]]:
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(
                f"""SELECT declared_confidence, observed_pass FROM {CALIBRATION_TABLE}
                 WHERE graph = $1 AND task_class = $2""",
                self._graph,
                task_class,
            )
        return [(float(r["declared_confidence"]), bool(r["observed_pass"])) for r in rows]

    async def report(
        self, task_class: str, *, floor: float = DEFAULT_CALIBRATION_FLOOR
    ) -> CalibrationReport:
        observations = await self._observations(task_class)
        return build_report(observations, task_class=task_class, floor=floor)

    async def is_below_floor(
        self, task_class: str, *, floor: float = DEFAULT_CALIBRATION_FLOOR
    ) -> bool:
        report = await self.report(task_class, floor=floor)
        return report.below_floor


__all__ = [
    "CALIBRATION_TABLE",
    "DEFAULT_CALIBRATION_FLOOR",
    "MIN_OBSERVATIONS_FOR_FLOOR_CHECK",
    "CalibrationBucket",
    "CalibrationReport",
    "CalibrationStore",
    "NullCalibrationStore",
    "PostgresCalibrationStore",
    "build_report",
]
