"""The loop that turns schedules into harvests.

S1.2.4. Two methods worth separating: ``tick`` does one round of claim-and-start and is
what the tests drive, ``run_forever`` is the loop that calls it. Anything that sleeps is
untestable without sleeping, so nothing that decides anything lives inside the loop.

**Scheduled runs are incremental.** That is the whole point of the story: a nightly run
over a 1,000-workbook site must not re-fetch 1,000 workbooks to discover that four
changed. ``HarvestMode.INCREMENTAL`` makes the Harvester consult ``updatedAt`` from the
enumeration and skip the fetch entirely for anything that has not moved.

**One run per schedule at a time.** A cadence shorter than a run's duration would
otherwise stack runs on top of each other until the source rate-limits the client. The
scheduler tracks what it started and declines to start a second; the schedule still
advances, so the next firing is a normal one rather than an immediate catch-up.

This is in-process, like the harvest tasks it starts. Durable orchestration is Temporal's
(E12/F12.1); the schedule row is the durable part, so a restart loses the loop and not the
plan.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from datetime import UTC, datetime
from typing import Any

from ..adapters.contract import Scope
from ..ids import new_ulid
from ..principal import Principal
from .model import HarvestMode, HarvestState
from .runner import DEFAULT_CONCURRENCY, DEFAULT_PARSE_QUALITY_THRESHOLD, Harvester, HarvestRequest
from .schedule import Schedule, ScheduleStore

logger = logging.getLogger(__name__)

#: How often the loop looks for due schedules. Well below the five-minute minimum cadence,
#: so a schedule fires within a minute of becoming due, and cheap: one indexed query.
DEFAULT_POLL_SECONDS = 30

#: The principal a scheduled run is attributed to. It is not a person, and pretending it
#: was one would put a name against work nobody did (spec §4.2 provenance).
SCHEDULER_PRINCIPAL = "agent:harvest-scheduler"

#: After this many consecutive failures a schedule stops trying. A site whose credential
#: expired should raise an alert, not hammer the source every night until somebody notices
#: the graph is stale. Spec §12.3.1 alerts on scheduler starvation; this is what it reads.
MAX_CONSECUTIVE_FAILURES = 5


class HarvestScheduler:
    """Starts incremental harvests when their schedules come due."""

    def __init__(
        self,
        *,
        store: ScheduleStore,
        harvester: Harvester,
        poll_seconds: int = DEFAULT_POLL_SECONDS,
        max_consecutive_failures: int = MAX_CONSECUTIVE_FAILURES,
    ) -> None:
        self._store = store
        self._harvester = harvester
        self._poll_seconds = poll_seconds
        self._max_failures = max_consecutive_failures
        self._running: dict[str, asyncio.Task[Any]] = {}
        self._last_tick_at: str | None = None
        self._ticks = 0

    # ------------------------------------------------------------------- reporting

    @property
    def status(self) -> dict[str, Any]:
        """What Platform Health shows about the scheduler itself.

        ``running_schedules``, not ``running``: the health response says whether a
        scheduler is running at all, and an empty list of in-flight runs under the same
        name reads as "not running" to anybody scanning the screen.
        """
        return {
            "poll_seconds": self._poll_seconds,
            "last_tick_at": self._last_tick_at,
            "ticks": self._ticks,
            "running_schedules": sorted(self._running),
        }

    # ------------------------------------------------------------------ one round

    async def tick(self, *, now: datetime | None = None) -> list[str]:
        """Claim every due schedule and start its run. Returns the harvest ids started."""
        moment = now or datetime.now(UTC)
        self._ticks += 1
        self._last_tick_at = _iso(moment)

        due = await self._store.due(now=moment)
        started: list[str] = []
        for schedule in due:
            if schedule.id in self._running:
                logger.warning(
                    "schedule %s (%s) is still running its previous harvest; skipping this "
                    "firing rather than stacking runs",
                    schedule.id,
                    _scope_text(schedule),
                )
                continue
            started.append(self._start(schedule))
        return started

    def _start(self, schedule: Schedule) -> str:
        harvest_id = new_ulid()
        schedule_id = schedule.id
        task = asyncio.create_task(self._run(schedule, harvest_id))
        self._running[schedule_id] = task
        task.add_done_callback(
            lambda _finished: self._running.pop(schedule_id, None)
        )
        logger.info(
            "schedule %s started incremental harvest %s of %s",
            schedule.id,
            harvest_id,
            _scope_text(schedule),
        )
        return harvest_id

    async def _run(self, schedule: Schedule, harvest_id: str) -> None:
        """Run one scheduled harvest and write its outcome back onto the schedule.

        Never raises. A schedule that fails is a fact to record and eventually to alert on,
        not an exception to lose inside a background task.
        """
        request = HarvestRequest(
            scope=Scope(site=schedule.site, project=schedule.project),
            credential_reference=schedule.credential_reference,
            mode=HarvestMode.INCREMENTAL,
            # So a run can be traced back to the schedule that started it. Platform Health
            # shows runs and schedules side by side, and "which of these was the nightly
            # one" should not be a guess from the principal.
            schedule_id=schedule.id,
            concurrency=schedule.concurrency or DEFAULT_CONCURRENCY,
            parse_quality_threshold=(
                schedule.parse_quality_threshold
                if schedule.parse_quality_threshold is not None
                else DEFAULT_PARSE_QUALITY_THRESHOLD
            ),
        )
        principal = Principal(SCHEDULER_PRINCIPAL, run_id=harvest_id)

        state = HarvestState.FAILED.value
        error: str | None = None
        try:
            progress = await self._harvester.run(
                request, principal=principal, harvest_id=harvest_id
            )
            state, error = progress.state.value, progress.error
        except asyncio.CancelledError:
            # Shutdown. The run record already says RUNNING and the next tick will not
            # resume it; leaving the schedule untouched is more honest than recording a
            # failure the source never caused.
            raise
        except Exception as exc:
            logger.exception("scheduled harvest %s failed outright", harvest_id)
            error = f"{type(exc).__name__}: {exc}"

        await self._store.record_run(
            schedule.id,
            run_id=harvest_id,
            state=state,
            error=error,
            finished_at=datetime.now(UTC),
        )
        await self._pause_if_persistently_failing(schedule.id)

    async def _pause_if_persistently_failing(self, schedule_id: str) -> None:
        current = await self._store.get(schedule_id)
        if current is None or not current.enabled:
            return
        if current.consecutive_failures < self._max_failures:
            return
        await self._store.set_enabled(
            schedule_id,
            enabled=False,
            reason=(
                f"paused automatically after {current.consecutive_failures} consecutive "
                f"failures; last error: {current.last_error}"
            ),
        )
        logger.error(
            "schedule %s paused after %s consecutive failures: %s",
            schedule_id,
            current.consecutive_failures,
            current.last_error,
        )

    # ----------------------------------------------------------------------- loop

    async def run_forever(self) -> None:
        """Poll for due schedules until cancelled."""
        logger.info("harvest scheduler started, polling every %ss", self._poll_seconds)
        try:
            while True:
                try:
                    await self.tick()
                except Exception:
                    # A failed tick must not end the loop: the usual cause is the database
                    # being briefly unreachable, and the next poll will find the same
                    # schedules still due.
                    logger.exception("scheduler tick failed; continuing")
                await asyncio.sleep(self._poll_seconds)
        except asyncio.CancelledError:
            logger.info("harvest scheduler stopping")
            raise
        finally:
            for task in list(self._running.values()):
                task.cancel()
            for task in list(self._running.values()):
                with contextlib.suppress(asyncio.CancelledError, Exception):
                    await task


def _scope_text(schedule: Schedule) -> str:
    if schedule.project:
        return f"site '{schedule.site}', project '{schedule.project}'"
    return f"site '{schedule.site}'"


def _iso(moment: datetime) -> str:
    return moment.astimezone(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")


__all__ = [
    "DEFAULT_POLL_SECONDS",
    "MAX_CONSECUTIVE_FAILURES",
    "SCHEDULER_PRINCIPAL",
    "HarvestScheduler",
]
