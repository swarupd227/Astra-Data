"""Platform Health, as far as this service can answer it.

S1.2.4: "schedule and last run are visible on Platform Health". Specification §15.3.3 wants
more on that screen than one service holds — executor latencies, gateway error rates,
pattern promotions — which arrive with the epics that own them (E12/F12.3). This is the
graph service's contribution: what its adapter is, what is scheduled, what ran, and what
drifted.

``/health`` stays what it is: a readiness probe for the container platform, cheap enough to
call every few seconds. This is the operator's view, and it costs real queries.
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Request

from ..adapters.conformance import AdapterBuild
from ..events import EventType
from ..harvest import HarvestState, ScheduleStore
from ..harvest_setup import UNGATED_ADAPTERS
from ..ontology import SCHEMA_VERSION
from ..retention import POLICY, prunable_before
from .deps import ArtizentDep, PrincipalDep

logger = logging.getLogger(__name__)

router = APIRouter()

#: Recent runs to summarise. Enough to see a nightly schedule's last week.
RECENT_RUNS = 10

#: Recent drift notices to surface. A long list means a client is still actively editing
#: what the platform is migrating, which is a programme conversation, not a bug. ``count``
#: reports what was returned, not the total — ``GET /v1/events`` is the whole record.
RECENT_DRIFT = 20


@router.get(
    "/v1/platform/health",
    tags=["operations"],
    summary="Adapter, schedules, recent runs and source drift",
)
async def platform_health(
    request: Request, principal: PrincipalDep, roles: ArtizentDep
) -> dict[str, Any]:
    """What Platform Health reads from this service.

    Every section degrades to a stated absence rather than an error: a deployment with no
    adapter enabled, or no scheduler running, is a real and reportable condition, and a
    screen that 500s because nothing is scheduled tells an engineer nothing.
    """
    state = request.app.state
    return {
        "service": "graph-svc",
        "graph": _graph_name(),
        "schema_version": SCHEMA_VERSION,
        "graph_version": await _version(state),
        "retention": await _retention(state),
        "adapter": await _adapter(state),
        "scheduler": _scheduler(state),
        "schedules": await _schedules(state),
        "harvests": await _harvests(state),
        "source_drift": await _drift(state),
        "directory_resolver": str(getattr(getattr(state, "directory", None), "kind", "none")),
        "migration_units": str(
            getattr(getattr(state, "migration_units", None), "kind", "none")
        ),
    }


async def _version(state: Any) -> dict[str, Any]:
    """The graph's current version (S1.3.2). An operator needs it to read a provenance
    record's ``graph_version`` as near or far."""
    repository = getattr(state, "repository", None)
    if repository is None:  # pragma: no cover - set in every wiring path
        return {"graph_version": None}
    version, at = await repository.current_version()
    return {"graph_version": version, "at": at}


async def _retention(state: Any) -> dict[str, Any]:
    store = getattr(state, "programme_store", None)
    if store is None:  # pragma: no cover - set in every wiring path
        return {"policy": POLICY, "prunable_before": None, "programmes": []}
    return {**prunable_before(await store.programmes()).as_dict(), "pruning_implemented": False}


def _graph_name() -> str:
    from ..config import settings

    return settings().graph_name


async def _adapter(state: Any) -> dict[str, Any]:
    harvester = getattr(state, "harvester", None)
    if harvester is None:
        return {
            "enabled": False,
            "detail": "no source adapter is enabled on this deployment (spec §6.3)",
        }
    manifest = harvester.manifest()
    return {
        "enabled": True,
        "name": manifest.name,
        "version": manifest.version,
        "grammar_version": manifest.grammar_version,
        "interface_version": manifest.interface_version,
        "capabilities": {
            "usage": manifest.capabilities.usage,
            "ownership": manifest.capabilities.ownership,
            "live_query": manifest.capabilities.live_query,
            "screenshot": manifest.capabilities.screenshot,
            "extract_read": manifest.capabilities.extract_read,
        },
        "conformance": await _conformance(state, manifest),
    }


async def _conformance(state: Any, manifest: Any) -> dict[str, Any]:
    """S2.1.2 criterion 2: the report, linked from Platform Health.

    Three separate facts, kept separate because they fail independently and an operator
    needs to know which one is missing:

    - **promoted** — is *this build* enabled on this tenant (criterion 3)?
    - **report** — is there a conformance report for it, and did it pass?
    - **signed** — was that report signed, or only hashed?

    A running adapter with no report is the condition worth seeing at a glance, because it is
    the one that means nothing is known about whether the adapter works.
    """
    store = getattr(state, "conformance_store", None)
    if store is None:  # pragma: no cover - set in every wiring path
        return {"available": False, "detail": "conformance records are not available"}

    build = AdapterBuild(
        name=manifest.name,
        version=manifest.version,
        interface_version=manifest.interface_version,
        grammar_version=manifest.grammar_version,
    )
    promotion = await store.promotion(manifest.name)
    record = await store.passing_for(build) or await store.latest(manifest.name)

    promoted_build = promotion.build if promotion else None
    running_promoted = promoted_build == build

    summary: dict[str, Any] = {
        "available": True,
        "promoted": running_promoted,
        "gated": manifest.name not in UNGATED_ADAPTERS,
        "report": record.as_dict() if record else None,
        "link": f"/v1/adapters/conformance/{record.id}" if record else None,
    }

    if manifest.name in UNGATED_ADAPTERS:
        # The screen must agree with the gate. Reporting an exempt adapter as "not promoted"
        # reads as "this harvest should be blocked and is not", which is a defect an operator
        # would go looking for and never find.
        summary["detail"] = (
            f"{build.describe()} is exempt from the promotion gate: it generates its own "
            f"estate and reaches no client system. A real source adapter is never exempt."
        )
    elif promotion is not None and promoted_build is not None and not running_promoted:
        # The loudest condition on this screen: the tenant approved one build and is running
        # another. Everything the promotion attests to is about an image that is not here.
        summary["detail"] = (
            f"the promoted build is {promoted_build.describe()} but the running adapter is "
            f"{build.describe()}. What was approved is not what is running."
        )
    elif promotion is None:
        summary["detail"] = (
            f"{build.describe()} is not promoted on this tenant. §6.1 requires a passing "
            f"conformance run before an adapter is enabled; POST "
            f"/v1/adapters/{manifest.name}:promote once one is recorded."
        )
    elif record is not None and not record.signed:
        summary["detail"] = (
            "the report is hashed but unsigned — this deployment has no signing key "
            "(Key Vault arrives with E11, spec §18.1), so the report's origin cannot be "
            "checked, only its integrity."
        )
    return summary


def _scheduler(state: Any) -> dict[str, Any]:
    scheduler = getattr(state, "scheduler", None)
    if scheduler is None:
        return {"running": False, "detail": "no scheduler on this process"}
    return {"running": True, **scheduler.status}


async def _schedules(state: Any) -> dict[str, Any]:
    store: ScheduleStore | None = getattr(state, "schedule_store", None)
    if store is None:  # pragma: no cover - set in every wiring path
        return {"count": 0, "paused": 0, "entries": []}
    schedules = await store.list_schedules()
    entries = [schedule.as_dict() for schedule in schedules]
    return {
        "count": len(entries),
        "paused": sum(1 for s in schedules if not s.enabled),
        "failing": sum(1 for s in schedules if s.consecutive_failures > 0),
        "entries": entries,
    }


async def _harvests(state: Any) -> dict[str, Any]:
    store = getattr(state, "harvest_store", None)
    if store is None:  # pragma: no cover - set in every wiring path
        return {"recent": [], "active": 0}
    runs = await store.recent(limit=RECENT_RUNS)
    return {
        "active": sum(1 for run in runs if not run.state.terminal),
        "last_completed_at": next(
            (run.finished_at for run in runs if run.state is HarvestState.COMPLETED), None
        ),
        "recent": [
            {
                "id": run.id,
                "state": run.state.value,
                "mode": run.mode.value,
                "schedule_id": run.schedule_id,
                "scope": run.scope,
                "started_at": run.started_at,
                "finished_at": run.finished_at,
                "parsed": run.parsed,
                "skipped_unchanged": run.skipped_unchanged,
                "skipped_not_modified": run.skipped_not_modified,
                "held": run.held,
                "failed": run.failed,
                "drifted": run.drifted,
                "error": run.error,
            }
            for run in runs
        ],
    }


async def _drift(state: Any) -> dict[str, Any]:
    """Recent SOURCE_DRIFT notices, newest first.

    Read from the outbox rather than kept in a second table: the event is already the
    record, and a count derived from anywhere else could disagree with it.
    """
    repository = getattr(state, "repository", None)
    if repository is None:  # pragma: no cover - set in every wiring path
        return {"recent": [], "count": 0}

    events = await repository.events_of_type(EventType.SOURCE_DRIFT, limit=RECENT_DRIFT)
    return {
        "count": len(events),
        "capped_at": RECENT_DRIFT,
        "recent": [
            {
                "sequence": event.sequence,
                "at": event.time,
                "workbook": event.data.get("workbook_name"),
                "workbook_luid": event.data.get("workbook_luid"),
                "site": event.data.get("site"),
                "harvest_id": event.data.get("harvest_id"),
                "migration_unit": event.data.get("migration_unit"),
                "reproof_requested": event.data.get("reproof_requested"),
            }
            for event in events
        ],
    }


__all__ = ["router"]
