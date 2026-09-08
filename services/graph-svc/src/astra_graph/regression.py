"""§10.6 Regression mode -- story S7.7.1, closing F7.7 and E7.

    "As a programme manager, I want parity suites re-run on a schedule after acceptance
    and on demand, so that a source change during parallel run is caught before the
    report owner notices.

    Acceptance criteria:
    - Steward schedules re-runs (default: after model publish, weekly during parallel
      run, on SOURCE_DRIFT); a regression FAIL creates an ExceptionCase tagged
      REGRESSION and notifies the report owner
    - Regression Monitor screen lists released MUs with last result, schedule and drift
      alerts
    - At handover suites and a runner are exported so the client can keep running them"

§10.6 itself, verbatim: *"Every parity suite is retained after acceptance. The Steward
re-runs a site's suites on a schedule (default: after every model publish and weekly
during parallel running) and on demand from the Parity Dashboard. A regression FAIL on a
released report raises an ExceptionCase tagged REGRESSION and notifies the report owner;
it does not change the MU's state. At handover the suites are exported with a runner so
the client can keep running them without the platform."*

**"On demand" already exists -- a Parity Engineer already has both buttons on the Parity
Dashboard (`POST .../:execute-parity-cases`, S7.3.1, then `POST .../:run-parity`,
S7.4.1) -- and needed nothing new here.** This module builds the *scheduled* half only:
a workbook-level `RegressionSchedule`, a `RegressionScheduler` loop that fires it, and
what happens on a FAIL.

**A scheduled check re-executes before it re-diffs -- it does not call `:run-parity`
alone.** Confirmed by direct reading: `VerdictsService.run` (`verdicts.py`'s own
docstring) diffs whatever Parquet `expected_ref`/`candidate_ref` a case's last execution
already stored -- it is `diff.py`'s graph-coupled other half, not a second execution.
Diffing the same stale snapshot on a schedule would report the identical verdict every
time and could never itself notice a source change, defeating the AC's own "so that a
source change... is caught" -- so each scheduled check calls
`CaseExecutionService.execute` (S7.3.1's own dual execution, the identical call
`:execute-parity-cases` makes) first, and only then `VerdictsService.run`, the same two-
step a Parity Engineer already performs by hand for "on demand." `workspace` -- a
required, no-default argument on `execute()` because no property anywhere binds a
workbook to its own Fabric workspace, confirmed by the same gap `:execute-parity-cases`
itself already discloses via its own `workspace` query parameter -- is therefore stored
on the schedule itself, supplied once at scheduling time rather than asked for on every
tick.

**No `steward.py` module exists anywhere in this codebase, confirmed by direct research
-- `"agent:steward"` is a bare `Principal` string used at exactly one call site
(`routes_g2.py`), never backed by a real service.** `report_documentation.py` already
disclosed the identical gap for its own "the Steward drafts documentation" AC and
attributed its own agent as `"compositor"` instead. This module is the first to actually
*build* Steward-shaped work (scheduling and running regressions); it does so as this
module's own real functions rather than inventing an empty `Steward` class with one
method, and attributes every write to `STEWARD_PRINCIPAL = "agent:steward"` -- the first
story to make that principal do something, not just name it.

**The scheduler is a second, parallel copy of `HarvestScheduler`'s own shape
(S1.2.4), not a generalisation of it.** `HarvestScheduler` is constructed around one
`Harvester` and one harvest-shaped `Schedule`; making it job-agnostic would mean
widening a already-shipped, tested class for a second caller it was never designed for.
`Cadence` (`harvest/schedule.py`) is genuinely job-agnostic already and is imported
directly, unchanged; everything else here -- `RegressionSchedule`,
`RegressionScheduleStore`, `RegressionScheduler` -- mirrors the harvest module's own
shape (`due()`'s claim-and-advance `FOR UPDATE SKIP LOCKED` discipline, one run per
schedule at a time, `tick`/`run_forever`/`status`) applied to a workbook instead of a
site.

**"After model publish" is a real, narrow hook into `model_lifecycle.promote_family`,
not a new schedule created out of nowhere.** Every schedule in this codebase (harvest,
now regression) is created by an explicit action, never as a side effect -- a publish
that silently enrolled a workbook nobody asked to monitor would be a surprise, not a
convenience. `trigger_after_publish` only *re-bases* an *already-existing* schedule's
own `next_run_at` to now, for whichever of the family's own workbooks already have one;
a workbook with no schedule is untouched, exactly as `POST .../:schedule-regression`
left it.

**"On SOURCE_DRIFT" reads the real event outbox `EventType.SOURCE_DRIFT` already
carries (S1.2.4), rather than building a new pub/sub layer.** No consumer of this event
type existed before this story -- confirmed by direct research, the Harvester only ever
calls `mark_for_reproof` and appends the notice; nothing reacts to it. Each scheduler
tick reads events newer than the schedule's own `last_seen_drift_seq` (a real, stored
watermark, not a re-scan) for workbooks with an active schedule, and triggers an
immediate check when one names that workbook -- the identical "claim, then advance a
watermark" discipline `due()` already uses for time-based firing, applied to an event
stream instead of a clock.

**A regression FAIL never touches an MU's state, per §10.6's own literal words** -- and
could not, in any case: no Migration Unit graph node or state machine exists anywhere in
this codebase (confirmed a great many times over this epic). `ExceptionCase.mu_ref` is
the workbook id directly, the same anchor `ParityCase.mu_ref` already uses.

**`REGRESSION` is a new, disclosed `ExceptionCase.class` value -- §10.6 names it
explicitly ("tagged REGRESSION"), but §11.1's own failure-taxonomy table does not list
it** (the same gap `VISUAL_REDESIGN` and `SOURCE_DRIFT` each already had before their own
stories added them). No new property is needed on `ExceptionCase` itself: `evidence_ref`
already exists and points at a real stored artefact -- here, a small JSON bundle
(`run_id`, `pass`/`fail`/`inconclusive` counts, `checked_at`) -- the identical "evidence_
ref points at a real artefact" shape the parity evidence bundle (S7.4.1) and the
screenshot reference (S2.4.2) already established.

**"Notifies the report owner" is honestly a role, not a named person.** Confirmed by
direct research (S7.4.2's own finding, still true): no property anywhere links a
`ReportDefinition` to a specific report-owner principal or email -- access is all-or-
nothing per role, not per-report ownership. `NotificationChannel`/`LocalNotificationChannel`
mirrors `g2_reminders.py`'s own honest disclosure verbatim: "notified" means "logged,
addressed to the role" until a real outward channel exists, never a claim of delivery
nobody could verify.

**"Released" has no real property anywhere to read** -- confirmed: no MU node, no
"released" flag, "parallel running" is prose-only. `ReportDefinition.deploy_state ==
"GENERATED"` (S6.1.2's own real, already-written fact -- a report that has actually been
deployed) is the disclosed proxy the Regression Monitor screen (`regression_monitor`,
below) uses for "released," the same "closest real fact stands in for a state this
codebase has never built" reasoning this epic has already applied repeatedly.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Protocol

import asyncpg

from .artefacts import ArtefactStore
from .case_derivation import CaseDerivationService
from .case_execution import CaseExecutionService
from .events import EventType
from .graph.queries import EDGE_INDEX_TABLE, NODE_INDEX_TABLE
from .harvest.schedule import Cadence
from .ids import new_ulid
from .lineage import hydrate
from .principal import Principal
from .tolerance_charter import ToleranceCharterStore
from .verdicts import VerdictsService
from .writes import GraphWriter, NodeWrite

logger = logging.getLogger(__name__)

REGRESSION_TABLE = "public.regression_schedule"

#: §11.1's own failure taxonomy never lists it; §10.6 names it explicitly. See this
#: module's own docstring.
REGRESSION_CLASS = "REGRESSION"

EVIDENCE_KIND = "regression_evidence"
EVIDENCE_MEDIA_TYPE = "application/json"

#: The principal every scheduled regression write is attributed to -- the first story to
#: make "agent:steward" do something rather than merely name it (see this module's own
#: docstring).
STEWARD_PRINCIPAL = "agent:steward"

DEFAULT_POLL_SECONDS = 30
#: §10.6's own literal default cadence: "weekly during parallel running."
DEFAULT_CADENCE_MINUTES = 7 * 24 * 60
MAX_CONSECUTIVE_FAILURES = 5

#: Deploy state §6.1.2 writes on a real successful deploy -- the disclosed "released"
#: proxy, see this module's own docstring.
RELEASED_DEPLOY_STATE = "GENERATED"


class RegressionError(Exception):
    """A regression schedule or run could not be produced as asked."""


# --------------------------------------------------------------------------- schedule


@dataclass(slots=True)
class RegressionSchedule:
    """One workbook's own recurring regression check, and how it has been going."""

    id: str
    workbook_id: str
    cadence: Cadence
    next_run_at: str
    workspace: str = "dev"
    """The Fabric workspace to execute the target side against -- see this module's own
    docstring on why no property anywhere resolves this for a workbook automatically;
    the default mirrors `:execute-parity-cases`'s own `workspace` query parameter."""

    enabled: bool = True
    paused_reason: str | None = None
    last_run_at: str | None = None
    last_run_id: str | None = None
    last_result: str | None = None
    """``PASS``, ``FAIL`` or ``INCONCLUSIVE`` -- the most recent regression run's own
    outcome (never a per-case detail; that lives in the evidence artefact)."""

    last_error: str | None = None
    consecutive_failures: int = 0
    last_seen_drift_seq: int = 0
    """The event outbox's own sequence number this schedule has already checked for
    SOURCE_DRIFT up to -- a real, stored watermark, not a re-scan every tick."""

    created_by: str = "unknown"
    created_at: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "workbook_id": self.workbook_id,
            "workspace": self.workspace,
            "cadence": self.cadence.as_dict(),
            "cadence_description": self.cadence.describe(),
            "enabled": self.enabled,
            "paused_reason": self.paused_reason,
            "next_run_at": self.next_run_at if self.enabled else None,
            "last_run": {
                "id": self.last_run_id,
                "at": self.last_run_at,
                "result": self.last_result,
                "error": self.last_error,
            },
            "consecutive_failures": self.consecutive_failures,
            "created_by": self.created_by,
            "created_at": self.created_at,
        }


class RegressionScheduleStore(Protocol):
    async def create(self, schedule: RegressionSchedule) -> RegressionSchedule: ...

    async def get(self, schedule_id: str) -> RegressionSchedule | None: ...

    async def get_by_workbook(self, workbook_id: str) -> RegressionSchedule | None: ...

    async def list_schedules(self) -> list[RegressionSchedule]: ...

    async def due(self, *, now: datetime, limit: int = 10) -> list[RegressionSchedule]: ...

    async def due_for_drift(self, *, latest_seq: int, limit: int = 50) -> list[RegressionSchedule]:
        """Enabled schedules whose own `last_seen_drift_seq` is behind the outbox's own
        latest SOURCE_DRIFT sequence number -- candidates to check for a drift notice
        naming their workbook."""
        ...

    async def mark_drift_checked(self, schedule_id: str, *, seq: int) -> None: ...

    async def touch_next_run(self, schedule_id: str, *, next_run_at: datetime) -> None:
        """Re-base a schedule's own next firing to (at latest) ``next_run_at`` -- used by
        `trigger_after_publish` to bring a firing forward, never to push one back."""
        ...

    async def record_run(
        self, schedule_id: str, *, run_id: str, result: str, error: str | None, finished_at: datetime,
    ) -> None: ...

    async def set_enabled(self, schedule_id: str, *, enabled: bool, reason: str | None) -> RegressionSchedule | None: ...


def new_regression_schedule(
    *,
    workbook_id: str,
    cadence: Cadence | None = None,
    workspace: str = "dev",
    created_by: str,
    now: datetime | None = None,
) -> RegressionSchedule:
    """A schedule whose first firing is one cadence away -- creating one does not start
    a run immediately, the identical "typing four schedules should not start four runs"
    reasoning `harvest.schedule.new_schedule` already gives."""
    moment = now or datetime.now(UTC)
    resolved_cadence = cadence or Cadence(every_minutes=DEFAULT_CADENCE_MINUTES)
    return RegressionSchedule(
        id=new_ulid(),
        workbook_id=workbook_id,
        cadence=resolved_cadence,
        next_run_at=_iso(resolved_cadence.next_after(moment)),
        workspace=workspace,
        created_by=created_by,
        created_at=_iso(moment),
    )


class PostgresRegressionScheduleStore:
    """Schedules in PostgreSQL, claimed with ``FOR UPDATE SKIP LOCKED`` -- the identical
    discipline `harvest.schedule.PostgresScheduleStore` already established."""

    def __init__(self, pool: asyncpg.Pool, *, graph_name: str) -> None:
        self._pool = pool
        self._graph = graph_name

    async def create(self, schedule: RegressionSchedule) -> RegressionSchedule:
        async with self._pool.acquire() as conn:
            try:
                await conn.execute(
                    f"""INSERT INTO {REGRESSION_TABLE}
                            (id, graph, workbook_id, workspace, cadence, enabled, next_run_at, created_by)
                        VALUES ($1, $2, $3, $4, $5::jsonb, $6, $7, $8)""",
                    schedule.id, self._graph, schedule.workbook_id, schedule.workspace,
                    json.dumps(schedule.cadence.as_dict()), schedule.enabled,
                    _parse(schedule.next_run_at), schedule.created_by,
                )
            except Exception as exc:
                if "regression_schedule_workbook_idx" in str(exc):
                    raise RegressionError(
                        f"a regression schedule already exists for workbook '{schedule.workbook_id}'; "
                        f"change that one rather than adding a second"
                    ) from exc
                raise
        return schedule

    async def get(self, schedule_id: str) -> RegressionSchedule | None:
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                f"SELECT * FROM {REGRESSION_TABLE} WHERE graph = $1 AND id = $2", self._graph, schedule_id,
            )
        return _from_row(row) if row else None

    async def get_by_workbook(self, workbook_id: str) -> RegressionSchedule | None:
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                f"SELECT * FROM {REGRESSION_TABLE} WHERE graph = $1 AND workbook_id = $2",
                self._graph, workbook_id,
            )
        return _from_row(row) if row else None

    async def list_schedules(self) -> list[RegressionSchedule]:
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(
                f"SELECT * FROM {REGRESSION_TABLE} WHERE graph = $1 ORDER BY created_at", self._graph,
            )
        return [_from_row(row) for row in rows]

    async def due(self, *, now: datetime, limit: int = 10) -> list[RegressionSchedule]:
        claimed: list[RegressionSchedule] = []
        async with self._pool.acquire() as conn, conn.transaction():
            rows = await conn.fetch(
                f"""SELECT * FROM {REGRESSION_TABLE}
                     WHERE graph = $1 AND enabled AND next_run_at <= $2
                     ORDER BY next_run_at LIMIT $3
                       FOR UPDATE SKIP LOCKED""",
                self._graph, now, limit,
            )
            for row in rows:
                schedule = _from_row(row)
                following = schedule.cadence.next_after(now)
                await conn.execute(
                    f"UPDATE {REGRESSION_TABLE} SET next_run_at = $3, updated_at = now() "
                    f"WHERE graph = $1 AND id = $2",
                    self._graph, schedule.id, following,
                )
                schedule.next_run_at = _iso(following)
                claimed.append(schedule)
        return claimed

    async def due_for_drift(self, *, latest_seq: int, limit: int = 50) -> list[RegressionSchedule]:
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(
                f"""SELECT * FROM {REGRESSION_TABLE}
                     WHERE graph = $1 AND enabled AND last_seen_drift_seq < $2
                     ORDER BY last_seen_drift_seq LIMIT $3""",
                self._graph, latest_seq, limit,
            )
        return [_from_row(row) for row in rows]

    async def mark_drift_checked(self, schedule_id: str, *, seq: int) -> None:
        async with self._pool.acquire() as conn:
            await conn.execute(
                f"UPDATE {REGRESSION_TABLE} SET last_seen_drift_seq = $3, updated_at = now() "
                f"WHERE graph = $1 AND id = $2",
                self._graph, schedule_id, seq,
            )

    async def touch_next_run(self, schedule_id: str, *, next_run_at: datetime) -> None:
        async with self._pool.acquire() as conn:
            await conn.execute(
                f"UPDATE {REGRESSION_TABLE} SET next_run_at = LEAST(next_run_at, $3), updated_at = now() "
                f"WHERE graph = $1 AND id = $2",
                self._graph, schedule_id, next_run_at,
            )

    async def record_run(
        self, schedule_id: str, *, run_id: str, result: str, error: str | None, finished_at: datetime,
    ) -> None:
        async with self._pool.acquire() as conn:
            await conn.execute(
                f"""UPDATE {REGRESSION_TABLE}
                       SET last_run_id = $3, last_run_at = $4, last_result = $5, last_error = $6,
                           consecutive_failures = CASE WHEN $5 = 'PASS' THEN 0 ELSE consecutive_failures + 1 END,
                           updated_at = now()
                     WHERE graph = $1 AND id = $2""",
                self._graph, schedule_id, run_id, finished_at, result, error,
            )

    async def set_enabled(self, schedule_id: str, *, enabled: bool, reason: str | None) -> RegressionSchedule | None:
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                f"""UPDATE {REGRESSION_TABLE} SET enabled = $3, paused_reason = $4, updated_at = now()
                     WHERE graph = $1 AND id = $2 RETURNING *""",
                self._graph, schedule_id, enabled, None if enabled else reason,
            )
        return _from_row(row) if row else None


class InMemoryRegressionScheduleStore:
    """The same contract without a database -- unit tests and the fixture stack."""

    def __init__(self) -> None:
        self.schedules: dict[str, RegressionSchedule] = {}

    async def create(self, schedule: RegressionSchedule) -> RegressionSchedule:
        for existing in self.schedules.values():
            if existing.workbook_id == schedule.workbook_id:
                raise RegressionError(
                    f"a regression schedule already exists for workbook '{schedule.workbook_id}'; "
                    f"change that one rather than adding a second"
                )
        self.schedules[schedule.id] = schedule
        return schedule

    async def get(self, schedule_id: str) -> RegressionSchedule | None:
        return self.schedules.get(schedule_id)

    async def get_by_workbook(self, workbook_id: str) -> RegressionSchedule | None:
        for schedule in self.schedules.values():
            if schedule.workbook_id == workbook_id:
                return schedule
        return None

    async def list_schedules(self) -> list[RegressionSchedule]:
        return sorted(self.schedules.values(), key=lambda s: s.created_at or "")

    async def due(self, *, now: datetime, limit: int = 10) -> list[RegressionSchedule]:
        ready = sorted(
            (s for s in self.schedules.values() if s.enabled and _parse(s.next_run_at) <= now),
            key=lambda s: s.next_run_at,
        )[:limit]
        for schedule in ready:
            schedule.next_run_at = _iso(schedule.cadence.next_after(now))
        return ready

    async def due_for_drift(self, *, latest_seq: int, limit: int = 50) -> list[RegressionSchedule]:
        ready = sorted(
            (s for s in self.schedules.values() if s.enabled and s.last_seen_drift_seq < latest_seq),
            key=lambda s: s.last_seen_drift_seq,
        )
        return ready[:limit]

    async def mark_drift_checked(self, schedule_id: str, *, seq: int) -> None:
        schedule = self.schedules.get(schedule_id)
        if schedule is not None:
            schedule.last_seen_drift_seq = seq

    async def touch_next_run(self, schedule_id: str, *, next_run_at: datetime) -> None:
        schedule = self.schedules.get(schedule_id)
        if schedule is not None and _parse(schedule.next_run_at) > next_run_at:
            schedule.next_run_at = _iso(next_run_at)

    async def record_run(
        self, schedule_id: str, *, run_id: str, result: str, error: str | None, finished_at: datetime,
    ) -> None:
        schedule = self.schedules.get(schedule_id)
        if schedule is None:
            return
        schedule.last_run_id = run_id
        schedule.last_run_at = _iso(finished_at)
        schedule.last_result = result
        schedule.last_error = error
        schedule.consecutive_failures = 0 if result == "PASS" else schedule.consecutive_failures + 1

    async def set_enabled(self, schedule_id: str, *, enabled: bool, reason: str | None) -> RegressionSchedule | None:
        schedule = self.schedules.get(schedule_id)
        if schedule is None:
            return None
        schedule.enabled = enabled
        schedule.paused_reason = None if enabled else reason
        return schedule


# ------------------------------------------------------------------------- exception


async def open_regression_exception(
    writer: GraphWriter, *, workbook_id: str, evidence_ref: str, principal: Principal,
) -> str:
    """A real `ExceptionCase(class=REGRESSION)`, `state="OPEN"` -- see this module's own
    docstring on why `REGRESSION` is a disclosed addition to `_FAILURE_CLASSES`."""
    case_id = new_ulid()
    await writer.write_nodes(
        [NodeWrite(type="ExceptionCase", id=case_id, properties={
            "mu_ref": workbook_id, "class": REGRESSION_CLASS, "state": "OPEN", "evidence_ref": evidence_ref,
        })],
        principal=principal,
    )
    return case_id


# ---------------------------------------------------------------------- notification


class NotificationChannel(Protocol):
    @property
    def kind(self) -> str: ...

    async def notify_regression(
        self, *, workbook_id: str, exception_case_id: str, fail_count: int,
    ) -> None: ...


class LocalNotificationChannel:
    """No outward channel exists in this codebase -- the identical honest disclosure
    `g2_reminders.LocalNotificationChannel` already gives, applied to a regression FAIL
    instead of a G2 reminder. "Notify the report owner" is a role, not a named person --
    see this module's own docstring on why no specific recipient can be addressed."""

    kind = "local"

    async def notify_regression(self, *, workbook_id: str, exception_case_id: str, fail_count: int) -> None:
        logger.info(
            "regression notification for the client_report_owner role, workbook %s: "
            "%d parity case(s) now FAIL, ExceptionCase %s opened — no outward notification "
            "channel is configured, recorded locally",
            workbook_id, fail_count, exception_case_id,
        )


# ------------------------------------------------------------------------ scheduler


async def _run_regression_check(
    pool: asyncpg.Pool,
    graph_name: str,
    writer: GraphWriter,
    artefact_store: ArtefactStore,
    verdicts: VerdictsService,
    case_execution: CaseExecutionService,
    charter_store: ToleranceCharterStore,
    store: RegressionScheduleStore,
    notifier: NotificationChannel,
    schedule: RegressionSchedule,
    run_id: str,
) -> None:
    """Run one scheduled regression check and write its outcome back -- never raises,
    the identical "a schedule that fails is a fact to record, not an exception to lose
    inside a background task" discipline `HarvestScheduler._run` already established.
    Re-executes both sides fresh (`CaseExecutionService.execute`) before re-diffing under
    the site's own *current* Tolerance Charter -- see this module's own docstring on why
    a re-diff alone could never catch a source change."""
    principal = Principal(STEWARD_PRINCIPAL, run_id=run_id)
    result = "FAIL"
    error: str | None = None
    try:
        await case_execution.execute(schedule.workbook_id, workspace=schedule.workspace, principal=principal)
        version = await charter_store.latest()
        outcome = await verdicts.run(
            schedule.workbook_id, charter=version.charter, charter_version=str(version.version), principal=principal,
        )
        if outcome["inconclusive"] and not outcome["fail"]:
            result = "INCONCLUSIVE"
        elif not outcome["fail"]:
            result = "PASS"
        else:
            result = "FAIL"

        if outcome["fail"]:
            evidence = {
                "run_id": outcome["run_id"], "workbook_id": schedule.workbook_id,
                "pass": outcome["pass"], "fail": outcome["fail"], "inconclusive": outcome["inconclusive"],
                "checked_at": _iso(datetime.now(UTC)),
            }
            evidence_artefact = await artefact_store.store(
                kind=EVIDENCE_KIND, mu_ref=schedule.workbook_id, case_id=outcome["run_id"],
                content=json.dumps(evidence).encode("utf-8"), media_type=EVIDENCE_MEDIA_TYPE,
                created_by=principal.value,
            )
            case_id = await open_regression_exception(
                writer, workbook_id=schedule.workbook_id, evidence_ref=evidence_artefact.id, principal=principal,
            )
            await notifier.notify_regression(
                workbook_id=schedule.workbook_id, exception_case_id=case_id, fail_count=outcome["fail"],
            )
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        logger.exception("scheduled regression check %s failed outright", run_id)
        result = "FAIL"
        error = f"{type(exc).__name__}: {exc}"

    await store.record_run(schedule.id, run_id=run_id, result=result, error=error, finished_at=datetime.now(UTC))
    await _pause_if_persistently_failing(store, schedule.id)


async def _pause_if_persistently_failing(store: RegressionScheduleStore, schedule_id: str) -> None:
    current = await store.get(schedule_id)
    if current is None or not current.enabled or current.consecutive_failures < MAX_CONSECUTIVE_FAILURES:
        return
    await store.set_enabled(
        schedule_id, enabled=False,
        reason=f"paused automatically after {current.consecutive_failures} consecutive failed checks; "
               f"last error: {current.last_error}",
    )
    logger.error(
        "regression schedule %s paused after %s consecutive failed checks", schedule_id, current.consecutive_failures,
    )


class RegressionScheduler:
    """Starts scheduled regression checks when their schedules come due, or when a
    SOURCE_DRIFT event names one of their workbooks -- see this module's own docstring."""

    def __init__(
        self,
        pool: asyncpg.Pool,
        *,
        graph_name: str,
        writer: GraphWriter,
        artefact_store: ArtefactStore,
        verdicts: VerdictsService,
        case_execution: CaseExecutionService,
        charter_store: ToleranceCharterStore,
        store: RegressionScheduleStore,
        notifier: NotificationChannel | None = None,
        poll_seconds: int = DEFAULT_POLL_SECONDS,
    ) -> None:
        self._pool = pool
        self._graph = graph_name
        self._writer = writer
        self._artefact_store = artefact_store
        self._verdicts = verdicts
        self._case_execution = case_execution
        self._charter_store = charter_store
        self._store = store
        self._notifier = notifier or LocalNotificationChannel()
        self._poll_seconds = poll_seconds
        self._running: dict[str, asyncio.Task[Any]] = {}
        self._last_tick_at: str | None = None
        self._ticks = 0

    @property
    def status(self) -> dict[str, Any]:
        return {
            "poll_seconds": self._poll_seconds, "last_tick_at": self._last_tick_at,
            "ticks": self._ticks, "running_schedules": sorted(self._running),
        }

    async def tick(self, *, now: datetime | None = None) -> list[str]:
        """One round: claim every due schedule, then check for SOURCE_DRIFT naming an
        already-scheduled workbook. Returns the run ids started."""
        moment = now or datetime.now(UTC)
        self._ticks += 1
        self._last_tick_at = _iso(moment)

        started: list[str] = []
        for schedule in await self._store.due(now=moment):
            started.extend(self._maybe_start(schedule))
        started.extend(await self._check_drift())
        return started

    async def _check_drift(self) -> list[str]:
        async with self._pool.acquire() as conn:
            events = await conn.fetch(
                """SELECT seq, subject FROM public.estate_event
                     WHERE graph = $1 AND type = $2 ORDER BY seq DESC LIMIT 200""",
                self._graph, EventType.SOURCE_DRIFT.value,
            )
        if not events:
            return []
        latest_seq = int(events[0]["seq"])
        by_workbook: dict[str, int] = {}
        for row in events:
            by_workbook.setdefault(str(row["subject"]), int(row["seq"]))

        started: list[str] = []
        for schedule in await self._store.due_for_drift(latest_seq=latest_seq):
            drifted_seq = by_workbook.get(schedule.workbook_id)
            if drifted_seq is not None and drifted_seq > schedule.last_seen_drift_seq:
                started.extend(self._maybe_start(schedule))
            await self._store.mark_drift_checked(schedule.id, seq=latest_seq)
        return started

    def _maybe_start(self, schedule: RegressionSchedule) -> list[str]:
        if schedule.id in self._running:
            logger.warning(
                "regression schedule %s is still running its previous check; skipping this firing", schedule.id,
            )
            return []
        run_id = new_ulid()
        task = asyncio.create_task(
            _run_regression_check(
                self._pool, self._graph, self._writer, self._artefact_store, self._verdicts,
                self._case_execution, self._charter_store, self._store, self._notifier, schedule, run_id,
            )
        )
        self._running[schedule.id] = task

        def _forget(_finished: asyncio.Task[Any], sid: str = schedule.id) -> None:
            self._running.pop(sid, None)

        task.add_done_callback(_forget)
        return [run_id]

    async def run_forever(self) -> None:
        logger.info("regression scheduler started, polling every %ss", self._poll_seconds)
        try:
            while True:
                try:
                    await self.tick()
                except Exception:
                    logger.exception("regression scheduler tick failed; continuing")
                await asyncio.sleep(self._poll_seconds)
        except asyncio.CancelledError:
            logger.info("regression scheduler stopping")
            raise
        finally:
            for task in list(self._running.values()):
                task.cancel()
            for task in list(self._running.values()):
                with contextlib.suppress(asyncio.CancelledError, Exception):
                    await task


# --------------------------------------------------------------------- after publish


async def trigger_after_publish(
    pool: asyncpg.Pool, graph_name: str, store: RegressionScheduleStore, *, family_id: str,
) -> list[str]:
    """§10.6's own "after every model publish" default -- re-bases each of this family's
    own already-scheduled workbooks to fire on the next tick. Never creates a schedule
    nobody asked for; see this module's own docstring. Returns the schedule ids nudged."""
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            f"""SELECT e.from_id AS workbook_id
                  FROM {EDGE_INDEX_TABLE} e
                  JOIN {NODE_INDEX_TABLE} n ON n.id = e.from_id AND n.kind = 'node'
                   AND n.graph = $1 AND n.label = 'Workbook' AND n.retired_at IS NULL
                 WHERE e.graph = $1 AND e.label = 'IN_FAMILY' AND e.to_id = $2 AND e.retired_at IS NULL""",
            graph_name, family_id,
        )
    now = datetime.now(UTC)
    nudged: list[str] = []
    for row in rows:
        schedule = await store.get_by_workbook(str(row["workbook_id"]))
        if schedule is not None and schedule.enabled:
            await store.touch_next_run(schedule.id, next_run_at=now)
            nudged.append(schedule.id)
    return nudged


# ---------------------------------------------------------------------- the monitor


async def regression_monitor(
    pool: asyncpg.Pool, graph_name: str, store: RegressionScheduleStore,
) -> list[dict[str, Any]]:
    """The Regression Monitor screen's own read: every released workbook (§10.6's own
    "released report", proxied by `ReportDefinition.deploy_state == "GENERATED"` -- see
    this module's own docstring), its own schedule (if any), and its own recent drift
    alerts. Sorted by workbook name for a stable screen."""
    async with pool.acquire() as conn:
        report_rows = await conn.fetch(
            f"""SELECT id FROM {NODE_INDEX_TABLE}
                 WHERE graph = $1 AND kind = 'node' AND label = 'ReportDefinition' AND retired_at IS NULL""",
            graph_name,
        )
        reports = await hydrate(conn, graph_name, "ReportDefinition", [row["id"] for row in report_rows])
        released = {
            str(props["mu_ref"]): props for props in reports.values()
            if props.get("deploy_state") == RELEASED_DEPLOY_STATE and props.get("mu_ref")
        }
        if not released:
            return []
        workbooks = await hydrate(conn, graph_name, "Workbook", list(released))

        drift_rows = await conn.fetch(
            """SELECT subject, seq, time FROM public.estate_event
                 WHERE graph = $1 AND type = $2 AND subject = ANY($3::text[])
                 ORDER BY seq DESC""",
            graph_name, EventType.SOURCE_DRIFT.value, list(released),
        )
    latest_drift_by_workbook: dict[str, dict[str, Any]] = {}
    for row in drift_rows:
        workbook_id = str(row["subject"])
        latest_drift_by_workbook.setdefault(workbook_id, {"seq": int(row["seq"]), "time": row["time"]})

    schedules_by_workbook = {s.workbook_id: s for s in await store.list_schedules()}

    rows_out: list[dict[str, Any]] = []
    for workbook_id in released:
        schedule = schedules_by_workbook.get(workbook_id)
        drift = latest_drift_by_workbook.get(workbook_id)
        drift_unaddressed = (
            drift is not None and (schedule is None or drift["seq"] > schedule.last_seen_drift_seq)
        )
        rows_out.append({
            "workbook_id": workbook_id,
            "workbook_name": (workbooks.get(workbook_id) or {}).get("name") or workbook_id,
            "schedule": schedule.as_dict() if schedule else None,
            "last_result": schedule.last_result if schedule else None,
            "drift_alert": {
                "unaddressed": drift_unaddressed,
                "last_drift_at": drift["time"].isoformat() if drift and drift.get("time") else None,
            } if drift is not None else {"unaddressed": False, "last_drift_at": None},
        })
    rows_out.sort(key=lambda r: str(r["workbook_name"]))
    return rows_out


class RegressionService:
    """Binds the Regression Monitor read and the handover export to one pool/graph/
    schedule store -- the identical "pre-bound object on app.state" shape
    `VerdictsService`/`CaseExecutionService` already take. `case_derivation` and
    `charter_store` are needed only for `export`; both already exist on `app.state`
    under their own names (S7.2.1, S7.1.1) and are threaded straight through rather than
    re-resolved."""

    def __init__(
        self,
        pool: asyncpg.Pool,
        *,
        graph_name: str,
        store: RegressionScheduleStore,
        case_derivation: CaseDerivationService,
        charter_store: ToleranceCharterStore,
    ) -> None:
        self._pool = pool
        self._graph = graph_name
        self._store = store
        self._case_derivation = case_derivation
        self._charter_store = charter_store

    async def monitor(self) -> list[dict[str, Any]]:
        return await regression_monitor(self._pool, self._graph, self._store)

    async def export(self, workbook_id: str) -> bytes:
        from .regression_export import build_regression_export

        return await build_regression_export(
            self._pool, self._graph, self._case_derivation, self._charter_store, workbook_id=workbook_id,
        )


# --------------------------------------------------------------------------- helpers


def _iso(moment: datetime) -> str:
    return moment.astimezone(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _parse(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(UTC)


def _from_row(row: Any) -> RegressionSchedule:
    cadence_raw = row["cadence"]
    cadence = Cadence.from_dict(json.loads(cadence_raw) if isinstance(cadence_raw, str) else dict(cadence_raw))
    return RegressionSchedule(
        id=row["id"], workbook_id=row["workbook_id"], cadence=cadence, workspace=row["workspace"],
        next_run_at=_iso(row["next_run_at"]), enabled=row["enabled"], paused_reason=row["paused_reason"],
        last_run_at=_iso(row["last_run_at"]) if row["last_run_at"] else None,
        last_run_id=row["last_run_id"], last_result=row["last_result"], last_error=row["last_error"],
        consecutive_failures=row["consecutive_failures"], last_seen_drift_seq=row["last_seen_drift_seq"],
        created_by=row["created_by"], created_at=_iso(row["created_at"]) if row["created_at"] else None,
    )


__all__ = [
    "DEFAULT_CADENCE_MINUTES",
    "DEFAULT_POLL_SECONDS",
    "EVIDENCE_KIND",
    "MAX_CONSECUTIVE_FAILURES",
    "REGRESSION_CLASS",
    "RELEASED_DEPLOY_STATE",
    "STEWARD_PRINCIPAL",
    "InMemoryRegressionScheduleStore",
    "LocalNotificationChannel",
    "NotificationChannel",
    "PostgresRegressionScheduleStore",
    "RegressionError",
    "RegressionSchedule",
    "RegressionScheduleStore",
    "RegressionScheduler",
    "RegressionService",
    "new_regression_schedule",
    "open_regression_exception",
    "regression_monitor",
    "trigger_after_publish",
]
