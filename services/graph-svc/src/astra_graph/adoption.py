"""Adoption tracking during parallel run, and the Decommission Tracker's own read --
story S9.2.2, continuing F9.2.

    "As a report owner, I want adoption of the released report tracked against the
    source during parallel run, so that we know users have moved before the source is
    switched off.

    Acceptance criteria:
    - Views on the Power BI report (Fabric activity) and the Tableau view (Metadata
      API) are both captured weekly; the ratio is shown on the Decommission Tracker
    - Configurable adoption threshold contributes to G4 readiness"

§14.4 itself, verbatim: "...tracks decommission readiness: all MUs released, regression
green, adoption sessions held, owner confirmation received." §15.3.4's own Decommission
Tracker row (a distinct screen from the Release Board, gated to the client licence
administrator, confirmed by direct read of both rows in the same table): "Per site:
readiness checklist, MUs released, regression green, owner confirmations, licence
value; G4 card when ready."

**"Adoption sessions held" (§14.4's own prose) and this story's own views-ratio metric
are two different notions of "adoption."** The spec's own prose never mentions a views
ratio anywhere; this backlog story's own AC introduces the more concrete, measurable
definition used here. Nothing here claims to satisfy "adoption sessions held" — that
remains its own, separate, unbuilt fact (a real scheduling mechanism nothing in this
codebase drives yet, matching the Release Board's own "schedule adoption session"
action, which records nothing today either).

**"Released" is the identical real signal `release.py` already established: a
SUCCEEDED `promotion_run` row for `to_stage="prod"`.** Queried directly here (a small,
duplicated `SELECT DISTINCT workbook_id` rather than a new export from `release.py`,
matching this codebase's own tolerance for a short, self-contained query over adding a
cross-module dependency for one read).

**Source-side views reuse the real, already-declared `SourceAdapter.usage()` contract
(§6.1, story S1.2.3) verbatim, capability-gated exactly the way `harvest/runner.py`'s
own `_context` already gates it.** The real `TableauAdapter` still declares
`usage=False` (confirmed by direct read — S1.2.3's own Metadata API integration has
never been built), so a real deployment's own weekly capture honestly records
`source_views=None` for every workbook rather than fabricating a source-side number;
the fixture source adapter (`usage=True`) exercises the real code path end to end in
this platform's own demo/test estate.

**Target-side views are a genuinely new `TargetAdapter.usage()` method (`target_
contract.py`, interface bumped to 1.3)** — no target-side activity method existed
before this story (confirmed by direct read: `manifest`/`commit`/`deploy`/
`smoke_query`/`evaluate`/`render_visual` were the whole contract). `FixtureTargetAdapter`
implements it with the identical "check something real first, then disclosed
deterministic synthetic data" posture `evaluate`/`smoke_query` already established.

**No per-workbook schedule, unlike `harvest`'s or `regression.py`'s own scheduling —
deliberately.** Both of those are opt-in, per-workbook, and materially expensive (a
regression re-run re-executes real parity DAX), so each earns its own enable/pause/
retry state machine. Adoption capture is the opposite: a uniform, cheap read across
*every* currently released workbook in one pass, with nothing to opt into or pause
per-workbook. `is_capture_due` checks a single, graph-wide fact — how long since
`adoption_snapshot`'s own most recent row — rather than maintaining a second schedule
table whose only real content would be "capture everything, weekly" duplicated once per
workbook.

**A configurable threshold is a real, versioned store (`AdoptionConfig`/
`PostgresAdoptionConfigStore`), the identical shape `mender.MenderConfig`/
`PostgresMenderConfigStore` already set** — the AC's own literal word "configurable"
is what earns a real store here, unlike `g3_card.DEFAULT_PARALLEL_WINDOW_WEEKS`, which
stays a bare constant because nothing in its own AC ever asked to change it.

**G4 readiness itself is not this story's own scope.** The AC's own words are
"contributes to," not "computes" — `meets_threshold` is a real, computed, exposed fact
a later story (the G4/decommission-authorisation story this epic has not yet reached)
folds into a fuller readiness rollup alongside regression status and owner
confirmation, neither of which exists as a real, driven fact anywhere in this codebase
today (confirmed, again, by direct search — the identical gap `release.py`'s own
docstring already disclosed for F9.2's later scope).
"""

from __future__ import annotations

import contextlib
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, Protocol

import asyncpg
from astra_adapter import AdapterError, Scope, SourceAdapter, TargetAdapter, UsageKind

from .events import adoption_captured
from .ids import new_ulid
from .lineage import hydrate
from .principal import Principal
from .release import (  # cross-epic private reuse; see module docstring
    PROMOTION_TABLE,
    _sites_for_workbooks,
)
from .tmdl import safe_name
from .writes import GraphWriter

CONFIG_TABLE = "public.adoption_config"
SNAPSHOT_TABLE = "public.adoption_snapshot"

#: The AC's own literal cadence: "captured weekly."
CAPTURE_WINDOW_DAYS = 7

#: A real, invented, disclosed default -- the AC's own words ("we know users have
#: moved") read as "most," not "some," usage having shifted before a source is turned
#: off. The identical footing `mender.DEFAULT_PASS_BUDGET`/`invoicing.DEFAULT_UNIT_
#: PRICES` already have for their own invented planning constants.
DEFAULT_ADOPTION_THRESHOLD = 0.8


class AdoptionError(Exception):
    """An adoption config or capture could not be produced as asked."""


# ----------------------------------------------------------------------------- config


@dataclass(frozen=True, slots=True)
class AdoptionConfig:
    threshold: float = DEFAULT_ADOPTION_THRESHOLD

    def as_dict(self) -> dict[str, Any]:
        return {"threshold": self.threshold}


class AdoptionConfigStore(Protocol):
    async def latest(self) -> AdoptionConfig: ...

    async def save(self, config: AdoptionConfig, *, updated_by: str) -> AdoptionConfig: ...


class PostgresAdoptionConfigStore:
    """Versioned, per-graph ('per tenant') -- the identical shape `mender.
    PostgresMenderConfigStore` already set."""

    def __init__(self, pool: asyncpg.Pool, *, graph_name: str) -> None:
        self._pool = pool
        self._graph = graph_name

    async def latest(self) -> AdoptionConfig:
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                f"SELECT threshold FROM {CONFIG_TABLE} WHERE graph = $1 ORDER BY version DESC LIMIT 1",
                self._graph,
            )
        return AdoptionConfig(threshold=float(row["threshold"])) if row else AdoptionConfig()

    async def save(self, config: AdoptionConfig, *, updated_by: str) -> AdoptionConfig:
        if not 0.0 <= config.threshold <= 1.0:
            raise AdoptionError(f"threshold must be between 0 and 1; got {config.threshold!r}")
        async with self._pool.acquire() as conn, conn.transaction():
            current = await conn.fetchval(
                f"SELECT MAX(version) FROM {CONFIG_TABLE} WHERE graph = $1", self._graph,
            )
            version = (current or 0) + 1
            await conn.execute(
                f"""INSERT INTO {CONFIG_TABLE} (id, graph, version, threshold, updated_by)
                    VALUES ($1, $2, $3, $4, $5)""",
                f"adoptionconf_{new_ulid()}", self._graph, version, config.threshold, updated_by,
            )
        return config


class InMemoryAdoptionConfigStore:
    def __init__(self, config: AdoptionConfig | None = None) -> None:
        self._config = config or AdoptionConfig()

    async def latest(self) -> AdoptionConfig:
        return self._config

    async def save(self, config: AdoptionConfig, *, updated_by: str) -> AdoptionConfig:
        if not 0.0 <= config.threshold <= 1.0:
            raise AdoptionError(f"threshold must be between 0 and 1; got {config.threshold!r}")
        self._config = config
        return config


# --------------------------------------------------------------------------- snapshot


@dataclass(frozen=True, slots=True)
class AdoptionSnapshot:
    id: str
    workbook_id: str
    captured_at: str
    source_views: int | None
    """Honestly `None` when the source adapter's own usage capability was absent at
    capture time -- never a fabricated zero."""
    target_views: int
    ratio: float | None
    """`target_views / source_views`, honestly `None` when `source_views` is `None` or
    zero -- there is no real ratio to report without a real denominator."""
    threshold: float
    """The configured threshold *at capture time* -- frozen on the row, never
    recomputed from whatever `AdoptionConfigStore.latest()` says today."""
    meets_threshold: bool | None
    triggered_by: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "workbook_id": self.workbook_id,
            "captured_at": self.captured_at,
            "source_views": self.source_views,
            "target_views": self.target_views,
            "ratio": self.ratio,
            "threshold": self.threshold,
            "meets_threshold": self.meets_threshold,
            "triggered_by": self.triggered_by,
        }


class AdoptionStore(Protocol):
    async def record(self, snapshot: AdoptionSnapshot) -> AdoptionSnapshot: ...

    async def latest_for_workbook(self, workbook_id: str) -> AdoptionSnapshot | None: ...

    async def latest_for_workbooks(
        self, workbook_ids: Sequence[str]
    ) -> dict[str, AdoptionSnapshot]: ...

    async def last_captured_at(self) -> str | None:
        """The most recent `captured_at` across every workbook, graph-wide -- the one
        fact `is_capture_due` needs; `None` if nothing has ever been captured."""
        ...


class PostgresAdoptionStore:
    def __init__(self, pool: asyncpg.Pool, *, graph_name: str) -> None:
        self._pool = pool
        self._graph = graph_name

    async def record(self, snapshot: AdoptionSnapshot) -> AdoptionSnapshot:
        async with self._pool.acquire() as conn:
            await conn.execute(
                f"""INSERT INTO {SNAPSHOT_TABLE}
                     (id, graph, workbook_id, captured_at, source_views, target_views,
                      ratio, threshold, meets_threshold, triggered_by)
                    VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10)""",
                snapshot.id, self._graph, snapshot.workbook_id,
                datetime.fromisoformat(snapshot.captured_at),
                snapshot.source_views, snapshot.target_views, snapshot.ratio,
                snapshot.threshold, snapshot.meets_threshold, snapshot.triggered_by,
            )
        return snapshot

    async def latest_for_workbook(self, workbook_id: str) -> AdoptionSnapshot | None:
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                f"""SELECT * FROM {SNAPSHOT_TABLE} WHERE graph = $1 AND workbook_id = $2
                     ORDER BY captured_at DESC LIMIT 1""",
                self._graph, workbook_id,
            )
        return _from_row(row) if row else None

    async def latest_for_workbooks(
        self, workbook_ids: Sequence[str]
    ) -> dict[str, AdoptionSnapshot]:
        if not workbook_ids:
            return {}
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(
                f"""SELECT DISTINCT ON (workbook_id) *
                      FROM {SNAPSHOT_TABLE}
                     WHERE graph = $1 AND workbook_id = ANY($2::text[])
                     ORDER BY workbook_id, captured_at DESC""",
                self._graph, list(workbook_ids),
            )
        return {row["workbook_id"]: _from_row(row) for row in rows}

    async def last_captured_at(self) -> str | None:
        async with self._pool.acquire() as conn:
            value = await conn.fetchval(
                f"SELECT MAX(captured_at) FROM {SNAPSHOT_TABLE} WHERE graph = $1", self._graph,
            )
        return _iso(value) if value else None


def _from_row(row: asyncpg.Record) -> AdoptionSnapshot:
    return AdoptionSnapshot(
        id=row["id"],
        workbook_id=row["workbook_id"],
        captured_at=_iso(row["captured_at"]) or "",
        source_views=row["source_views"],
        target_views=row["target_views"],
        ratio=float(row["ratio"]) if row["ratio"] is not None else None,
        threshold=float(row["threshold"]),
        meets_threshold=row["meets_threshold"],
        triggered_by=row["triggered_by"],
    )


def _iso(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, str):
        return value
    return str(value.isoformat())


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")


# ------------------------------------------------------------------------ the sweep


async def _released_workbook_ids(pool: asyncpg.Pool, graph_name: str) -> list[str]:
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            f"""SELECT DISTINCT workbook_id FROM {PROMOTION_TABLE}
                 WHERE graph = $1 AND to_stage = 'prod' AND state = 'SUCCEEDED'""",
            graph_name,
        )
    return [row["workbook_id"] for row in rows]


async def is_capture_due(
    adoption_store: AdoptionStore, *, now: datetime | None = None
) -> bool:
    """A single, graph-wide fact -- see this module's own docstring on why no
    per-workbook schedule exists."""
    moment = now or datetime.now(UTC)
    last = await adoption_store.last_captured_at()
    if last is None:
        return True
    return moment - datetime.fromisoformat(last.replace("Z", "+00:00")) >= timedelta(days=CAPTURE_WINDOW_DAYS)


def _adoption_ratio(
    *, source_views: int | None, target_views: int, threshold: float,
) -> tuple[float | None, bool | None]:
    """(ratio, meets_threshold) -- honestly `(None, None)` with no real source-side
    denominator (an absent or zero source-views count), never a fabricated ratio."""
    if not source_views:
        return None, None
    ratio = target_views / source_views
    return ratio, ratio >= threshold


async def capture_adoption_sweep(
    pool: asyncpg.Pool,
    graph_name: str,
    writer: GraphWriter,
    source_adapter: SourceAdapter,
    target_adapter: TargetAdapter,
    adoption_store: AdoptionStore,
    config_store: AdoptionConfigStore,
    *,
    target_workspace: str,
    principal: Principal,
) -> list[AdoptionSnapshot]:
    """One weekly sweep: every currently released (prod-promoted) workbook gets a real
    snapshot. Never raises on a single workbook's own target-side failure -- one
    report's own Fabric activity outage should not abort every other workbook's real
    capture; that workbook is honestly skipped for this sweep and picked up again next
    week."""
    workbook_ids = await _released_workbook_ids(pool, graph_name)
    if not workbook_ids:
        return []

    config = await config_store.latest()

    source_usage_by_luid: dict[str, int] = {}
    manifest = source_adapter.manifest()
    if manifest.capabilities.usage:
        with contextlib.suppress(AdapterError):
            for record in await source_adapter.usage(Scope(), CAPTURE_WINDOW_DAYS):
                if record.kind is UsageKind.WORKBOOK:
                    source_usage_by_luid[record.asset_luid] = record.views

    async with pool.acquire() as conn:
        workbooks = await hydrate(conn, graph_name, "Workbook", workbook_ids)

    now = _now()
    snapshots: list[AdoptionSnapshot] = []
    for workbook_id in workbook_ids:
        properties = workbooks.get(workbook_id)
        if properties is None:
            continue
        luid = properties.get("luid")
        name = str(properties.get("name") or workbook_id)
        source_views = source_usage_by_luid.get(str(luid)) if luid else None

        item_path = f"{safe_name(name)}.Report"
        try:
            activity = await target_adapter.usage(
                workspace=target_workspace, item_path=item_path, window_days=CAPTURE_WINDOW_DAYS,
            )
        except Exception:
            continue

        ratio, meets_threshold = _adoption_ratio(
            source_views=source_views, target_views=activity.views, threshold=config.threshold,
        )

        snapshot = AdoptionSnapshot(
            id=f"adoption_{new_ulid()}",
            workbook_id=workbook_id,
            captured_at=now,
            source_views=source_views,
            target_views=activity.views,
            ratio=ratio,
            threshold=config.threshold,
            meets_threshold=meets_threshold,
            triggered_by=principal.value,
        )
        await adoption_store.record(snapshot)
        await writer.append_event(
            adoption_captured(
                source=writer.event_source, workbook_id=workbook_id,
                source_views=source_views, target_views=activity.views,
                ratio=ratio, meets_threshold=meets_threshold, principal=principal,
            )
        )
        snapshots.append(snapshot)

    return snapshots


# ------------------------------------------------------------------- Decommission Tracker


async def decommission_tracker(
    pool: asyncpg.Pool,
    graph_name: str,
    adoption_store: AdoptionStore,
    config_store: AdoptionConfigStore,
) -> dict[str, Any]:
    """"The ratio is shown on the Decommission Tracker" -- per site, every released
    MU's own latest real adoption snapshot. Decommission readiness itself (regression
    status, owner confirmation, G4) is deliberately not computed here -- see this
    module's own docstring."""
    workbook_ids = await _released_workbook_ids(pool, graph_name)
    latest = await adoption_store.latest_for_workbooks(workbook_ids)
    config = await config_store.latest()

    async with pool.acquire() as conn:
        workbooks = await hydrate(conn, graph_name, "Workbook", workbook_ids)
    sites = await _sites_for_workbooks(pool, graph_name, workbook_ids)
    site_ids = sorted({s for s in sites.values() if s})
    async with pool.acquire() as conn:
        site_properties = await hydrate(conn, graph_name, "Site", site_ids)

    by_site: dict[str, list[str]] = {}
    for workbook_id in workbook_ids:
        site_id = sites.get(workbook_id)
        if site_id:
            by_site.setdefault(site_id, []).append(workbook_id)

    site_rows = []
    for site_id, members in by_site.items():
        mu_rows = [
            {
                "workbook_id": workbook_id,
                "name": (workbooks.get(workbook_id) or {}).get("name", workbook_id),
                "snapshot": latest[workbook_id].as_dict() if workbook_id in latest else None,
            }
            for workbook_id in members
        ]
        meeting = sum(1 for mu in mu_rows if (mu["snapshot"] or {}).get("meets_threshold"))
        site_rows.append({
            "site_id": site_id,
            "name": (site_properties.get(site_id) or {}).get("name", site_id),
            "mus": mu_rows,
            "released_mu_count": len(members),
            "meeting_threshold_count": meeting,
        })
    site_rows.sort(key=lambda row: row["name"])

    return {"threshold": config.threshold, "sites": site_rows}


class AdoptionService:
    """Binds the module-level functions to one pool/graph/writer/adapters/stores -- the
    identical "pre-bound object on app.state" shape `ReleaseService` already takes, so
    `routes_adoption.py` needs no `graph_name` of its own to call this."""

    def __init__(
        self,
        pool: asyncpg.Pool,
        *,
        graph_name: str,
        writer: GraphWriter,
        source_adapter: SourceAdapter,
        target_adapter: TargetAdapter,
        adoption_store: AdoptionStore,
        config_store: AdoptionConfigStore,
        target_workspace: str,
    ) -> None:
        self._pool = pool
        self._graph = graph_name
        self._writer = writer
        self._source_adapter = source_adapter
        self._target_adapter = target_adapter
        self._adoption_store = adoption_store
        self._config_store = config_store
        self._target_workspace = target_workspace

    async def tracker(self) -> dict[str, Any]:
        return await decommission_tracker(self._pool, self._graph, self._adoption_store, self._config_store)

    async def config(self) -> AdoptionConfig:
        return await self._config_store.latest()

    async def set_config(self, threshold: float, *, updated_by: str) -> AdoptionConfig:
        return await self._config_store.save(AdoptionConfig(threshold=threshold), updated_by=updated_by)

    async def capture(self, *, principal: Principal) -> list[AdoptionSnapshot]:
        return await capture_adoption_sweep(
            self._pool, self._graph, self._writer, self._source_adapter, self._target_adapter,
            self._adoption_store, self._config_store,
            target_workspace=self._target_workspace, principal=principal,
        )


__all__ = [
    "CAPTURE_WINDOW_DAYS",
    "CONFIG_TABLE",
    "DEFAULT_ADOPTION_THRESHOLD",
    "SNAPSHOT_TABLE",
    "AdoptionConfig",
    "AdoptionConfigStore",
    "AdoptionError",
    "AdoptionService",
    "AdoptionSnapshot",
    "AdoptionStore",
    "InMemoryAdoptionConfigStore",
    "PostgresAdoptionConfigStore",
    "PostgresAdoptionStore",
    "capture_adoption_sweep",
    "decommission_tracker",
    "is_capture_due",
]
