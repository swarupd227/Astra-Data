"""Where harvest runs and per-workbook state live.

Progress has to survive the process: S1.2.1 asks for it to be visible while a run is in
flight, and a run over a 1,000-workbook site lasts hours. It is relational rather than
graph-shaped — a harvest is a platform record, not an estate fact — which is the same
split the specification makes in §21.

The per-workbook state is what makes a re-harvest a no-op: the content hash the adapter
last reported for a workbook, and what came of it.
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Protocol

import asyncpg

from ..adapters.contract import AssetRef
from .model import (
    HarvestFailure,
    HarvestMode,
    HarvestProgress,
    HarvestState,
    ProjectProgress,
    WorkbookOutcome,
)

RUN_TABLE = "public.harvest_run"
WORKBOOK_TABLE = "public.harvest_workbook"


@dataclass(frozen=True, slots=True)
class WorkbookState:
    """What the last harvest saw of one workbook."""

    workbook_luid: str
    site: str
    revision: str
    content_hash: str
    outcome: WorkbookOutcome
    parse_quality: float | None
    harvested_at: str
    source_updated_at: str | None = None
    """The source's own ``updatedAt`` when this was recorded. What an incremental run
    compares against to decide whether to fetch at all (S1.2.4)."""

    grammar_version: str | None = None
    """Which grammar parsed it. A workbook that has not changed still needs re-parsing
    under a new grammar, or a grammar extension would never reach it."""


class HarvestStore(Protocol):
    async def create(self, progress: HarvestProgress) -> None: ...

    async def update(self, progress: HarvestProgress) -> None: ...

    async def get(self, harvest_id: str) -> HarvestProgress | None: ...

    async def recent(self, *, limit: int = 50) -> list[HarvestProgress]: ...

    async def workbook_state(
        self, graph: str, site: str, workbook_luid: str
    ) -> WorkbookState | None: ...

    async def record_workbook(
        self,
        graph: str,
        ref: AssetRef,
        content_hash: str,
        *,
        outcome: WorkbookOutcome,
        parse_quality: float | None = None,
        unrecognised: Sequence[dict[str, Any]] = (),
        recognised: int = 0,
        ignorable: int = 0,
        total: int = 0,
        grammar_version: str | None = None,
    ) -> None: ...

    async def counts(
        self, graph: str, site: str, workbook_luid: str
    ) -> tuple[int, int, int, float | None] | None: ...

    async def set_parse_quality(
        self,
        graph: str,
        site: str,
        workbook_luid: str,
        *,
        parse_quality: float,
        ignorable: int,
    ) -> None: ...

    async def record_failure(self, harvest_id: str, failure: HarvestFailure) -> None: ...

    async def failures(self, harvest_id: str, *, limit: int = 500) -> list[HarvestFailure]: ...


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _parse_iso(value: str | None) -> datetime | None:
    """The source's own timestamp, as a datetime.

    asyncpg binds ``timestamptz`` from a datetime and not from a string. A source that
    reports an unparseable time is treated as reporting none: an incremental run then
    falls back to fetching and comparing content, which is slower and always correct.
    """
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(UTC)
    except ValueError:
        return None


def _parse_ts(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, str):
        return value
    rendered: str = value.astimezone(UTC).isoformat(timespec="milliseconds")
    return rendered.replace("+00:00", "Z")


def _to_datetime(value: str | None) -> datetime | None:
    if not value:
        return None
    text = value[:-1] + "+00:00" if value.endswith(("z", "Z")) else value
    return datetime.fromisoformat(text)


class PostgresHarvestStore:
    """The harvest record in PostgreSQL."""

    def __init__(self, pool: asyncpg.Pool, *, graph_name: str) -> None:
        self._pool = pool
        self._graph = graph_name

    async def create(self, progress: HarvestProgress) -> None:
        async with self._pool.acquire() as conn:
            await conn.execute(
                f"""
                INSERT INTO {RUN_TABLE}
                    (id, graph, state, scope, adapter, principal, started_at, progress)
                VALUES ($1, $2, $3, $4::jsonb, $5::jsonb, $6, $7, $8::jsonb)
                """,
                progress.id,
                self._graph,
                progress.state.value,
                json.dumps(progress.scope),
                json.dumps(progress.adapter),
                progress.principal,
                _to_datetime(progress.started_at),
                json.dumps(progress.as_dict()),
            )

    async def update(self, progress: HarvestProgress) -> None:
        async with self._pool.acquire() as conn:
            await conn.execute(
                f"""
                UPDATE {RUN_TABLE}
                   SET state = $2, finished_at = $3, progress = $4::jsonb, error = $5
                 WHERE id = $1
                """,
                progress.id,
                progress.state.value,
                _to_datetime(progress.finished_at),
                json.dumps(progress.as_dict()),
                progress.error,
            )
            if progress.state.terminal:
                return

    async def get(self, harvest_id: str) -> HarvestProgress | None:
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                f"SELECT progress FROM {RUN_TABLE} WHERE id = $1 AND graph = $2",
                harvest_id,
                self._graph,
            )
        return _from_json(json.loads(row["progress"])) if row else None

    async def recent(self, *, limit: int = 50) -> list[HarvestProgress]:
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(
                f"SELECT progress FROM {RUN_TABLE} WHERE graph = $1 "
                f"ORDER BY started_at DESC NULLS LAST LIMIT $2",
                self._graph,
                limit,
            )
        return [_from_json(json.loads(row["progress"])) for row in rows]

    async def workbook_state(
        self, graph: str, site: str, workbook_luid: str
    ) -> WorkbookState | None:
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                f"""
                SELECT workbook_luid, site, revision, content_hash, outcome,
                       parse_quality, harvested_at, source_updated_at, grammar_version
                  FROM {WORKBOOK_TABLE}
                 WHERE graph = $1 AND site = $2 AND workbook_luid = $3
                """,
                graph,
                site,
                workbook_luid,
            )
        if row is None:
            return None
        return WorkbookState(
            workbook_luid=row["workbook_luid"],
            site=row["site"],
            revision=row["revision"],
            content_hash=row["content_hash"],
            outcome=WorkbookOutcome(row["outcome"]),
            parse_quality=row["parse_quality"],
            harvested_at=_parse_ts(row["harvested_at"]) or "",
            source_updated_at=_parse_ts(row["source_updated_at"]),
            grammar_version=row["grammar_version"],
        )

    async def record_workbook(
        self,
        graph: str,
        ref: AssetRef,
        content_hash: str,
        *,
        outcome: WorkbookOutcome,
        parse_quality: float | None = None,
        unrecognised: Sequence[dict[str, Any]] = (),
        recognised: int = 0,
        ignorable: int = 0,
        total: int = 0,
        grammar_version: str | None = None,
    ) -> None:
        async with self._pool.acquire() as conn:
            await conn.execute(
                f"""
                INSERT INTO {WORKBOOK_TABLE}
                    (graph, site, workbook_luid, workbook_name, project, revision,
                     content_hash, outcome, parse_quality, unrecognised, harvested_at,
                     constructs_recognised, constructs_ignorable, constructs_total,
                     grammar_version, source_updated_at)
                VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10::jsonb, now(),
                        $11, $12, $13, $14, $15)
                ON CONFLICT (graph, site, workbook_luid) DO UPDATE SET
                    workbook_name         = EXCLUDED.workbook_name,
                    project               = EXCLUDED.project,
                    revision              = EXCLUDED.revision,
                    content_hash          = EXCLUDED.content_hash,
                    outcome               = EXCLUDED.outcome,
                    parse_quality         = EXCLUDED.parse_quality,
                    unrecognised          = EXCLUDED.unrecognised,
                    harvested_at          = now(),
                    constructs_recognised = EXCLUDED.constructs_recognised,
                    constructs_ignorable  = EXCLUDED.constructs_ignorable,
                    constructs_total      = EXCLUDED.constructs_total,
                    grammar_version       = EXCLUDED.grammar_version,
                    source_updated_at     = EXCLUDED.source_updated_at
                """,
                graph,
                ref.site,
                ref.luid,
                ref.name,
                ref.project,
                ref.revision,
                content_hash,
                outcome.value,
                parse_quality,
                json.dumps(list(unrecognised)),
                recognised,
                ignorable,
                total,
                grammar_version,
                _parse_iso(ref.updated_at),
            )

    async def counts(
        self, graph: str, site: str, workbook_luid: str
    ) -> tuple[int, int, int, float | None] | None:
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                f"SELECT constructs_recognised, constructs_ignorable, constructs_total, "
                f"parse_quality FROM {WORKBOOK_TABLE} "
                f"WHERE graph = $1 AND site = $2 AND workbook_luid = $3",
                graph,
                site,
                workbook_luid,
            )
        if row is None:
            return None
        return (
            row["constructs_recognised"] or 0,
            row["constructs_ignorable"] or 0,
            row["constructs_total"] or 0,
            row["parse_quality"],
        )

    async def set_parse_quality(
        self,
        graph: str,
        site: str,
        workbook_luid: str,
        *,
        parse_quality: float,
        ignorable: int,
    ) -> None:
        async with self._pool.acquire() as conn:
            await conn.execute(
                f"UPDATE {WORKBOOK_TABLE} SET parse_quality = $4, constructs_ignorable = $5 "
                f"WHERE graph = $1 AND site = $2 AND workbook_luid = $3",
                graph,
                site,
                workbook_luid,
                parse_quality,
                ignorable,
            )

    async def record_failure(self, harvest_id: str, failure: HarvestFailure) -> None:
        async with self._pool.acquire() as conn:
            await conn.execute(
                "INSERT INTO public.harvest_failure "
                "(harvest_id, workbook_luid, workbook_name, project, stage, error, retryable) "
                "VALUES ($1, $2, $3, $4, $5, $6, $7)",
                harvest_id,
                failure.workbook_luid,
                failure.workbook_name,
                failure.project,
                failure.stage,
                failure.error,
                failure.retryable,
            )

    async def failures(self, harvest_id: str, *, limit: int = 500) -> list[HarvestFailure]:
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(
                "SELECT workbook_luid, workbook_name, project, stage, error, retryable "
                "FROM public.harvest_failure WHERE harvest_id = $1 ORDER BY id LIMIT $2",
                harvest_id,
                limit,
            )
        return [
            HarvestFailure(
                workbook_luid=row["workbook_luid"],
                workbook_name=row["workbook_name"],
                project=row["project"],
                stage=row["stage"],
                error=row["error"],
                retryable=row["retryable"],
            )
            for row in rows
        ]


def _from_json(payload: dict[str, Any]) -> HarvestProgress:
    totals = payload.get("totals", {})
    return HarvestProgress(
        id=payload["id"],
        state=HarvestState(payload["state"]),
        scope=payload.get("scope", {}),
        adapter=payload.get("adapter", {}),
        principal=payload.get("principal", ""),
        mode=HarvestMode(payload.get("mode", HarvestMode.FULL.value)),
        schedule_id=payload.get("schedule_id"),
        started_at=payload.get("started_at"),
        finished_at=payload.get("finished_at"),
        queued=totals.get("queued", 0),
        parsed=totals.get("parsed", 0),
        skipped_unchanged=totals.get("skipped_unchanged", 0),
        skipped_not_modified=totals.get("skipped_not_modified", 0),
        held=totals.get("held", 0),
        failed=totals.get("failed", 0),
        drifted=totals.get("drifted", 0),
        parse_quality_p50=payload.get("parse_quality_p50"),
        projects=[
            ProjectProgress(
                project=item["project"],
                queued=item.get("queued", 0),
                parsed=item.get("parsed", 0),
                skipped_unchanged=item.get("skipped_unchanged", 0),
                skipped_not_modified=item.get("skipped_not_modified", 0),
                held=item.get("held", 0),
                failed=item.get("failed", 0),
            )
            for item in payload.get("projects", [])
        ],
        error=payload.get("error"),
    )


class InMemoryHarvestStore:
    """The same store without a database, for unit tests and local dry runs."""

    def __init__(self) -> None:
        self.runs: dict[str, HarvestProgress] = {}
        self.workbooks: dict[tuple[str, str, str], WorkbookState] = {}
        self.recorded_failures: dict[str, list[HarvestFailure]] = {}
        self.unrecognised: dict[tuple[str, str, str], list[dict[str, Any]]] = {}
        self.counts_by_workbook: dict[
            tuple[str, str, str], tuple[int, int, int, float | None]
        ] = {}
        #: What the queue reports alongside the score, mirroring the columns the SQL
        #: implementation joins from harvest_workbook.
        self.workbook_meta: dict[tuple[str, str, str], dict[str, Any]] = {}

    async def create(self, progress: HarvestProgress) -> None:
        self.runs[progress.id] = progress

    async def update(self, progress: HarvestProgress) -> None:
        self.runs[progress.id] = progress

    async def get(self, harvest_id: str) -> HarvestProgress | None:
        return self.runs.get(harvest_id)

    async def recent(self, *, limit: int = 50) -> list[HarvestProgress]:
        return list(self.runs.values())[:limit]

    async def workbook_state(
        self, graph: str, site: str, workbook_luid: str
    ) -> WorkbookState | None:
        return self.workbooks.get((graph, site, workbook_luid))

    async def record_workbook(
        self,
        graph: str,
        ref: AssetRef,
        content_hash: str,
        *,
        outcome: WorkbookOutcome,
        parse_quality: float | None = None,
        unrecognised: Sequence[dict[str, Any]] = (),
        recognised: int = 0,
        ignorable: int = 0,
        total: int = 0,
        grammar_version: str | None = None,
    ) -> None:
        self.counts_by_workbook[(graph, ref.site, ref.luid)] = (
            recognised,
            ignorable,
            total,
            parse_quality,
        )
        self.workbook_meta[(graph, ref.site, ref.luid)] = {
            "workbook_name": ref.name,
            "project": ref.project,
            "grammar_version": grammar_version,
        }
        self.workbooks[(graph, ref.site, ref.luid)] = WorkbookState(
            workbook_luid=ref.luid,
            site=ref.site,
            revision=ref.revision,
            content_hash=content_hash,
            outcome=outcome,
            parse_quality=parse_quality,
            harvested_at=_now(),
            source_updated_at=_parse_ts(_parse_iso(ref.updated_at)),
            grammar_version=grammar_version,
        )
        self.unrecognised[(graph, ref.site, ref.luid)] = list(unrecognised)

    async def counts(
        self, graph: str, site: str, workbook_luid: str
    ) -> tuple[int, int, int, float | None] | None:
        return self.counts_by_workbook.get((graph, site, workbook_luid))

    async def set_parse_quality(
        self,
        graph: str,
        site: str,
        workbook_luid: str,
        *,
        parse_quality: float,
        ignorable: int,
    ) -> None:
        key = (graph, site, workbook_luid)
        recognised, _, total, _ = self.counts_by_workbook.get(key, (0, 0, 0, None))
        self.counts_by_workbook[key] = (recognised, ignorable, total, parse_quality)
        previous = self.workbooks.get(key)
        if previous is not None:
            self.workbooks[key] = WorkbookState(
                workbook_luid=previous.workbook_luid,
                site=previous.site,
                revision=previous.revision,
                content_hash=previous.content_hash,
                outcome=previous.outcome,
                parse_quality=parse_quality,
                harvested_at=previous.harvested_at,
            )

    async def record_failure(self, harvest_id: str, failure: HarvestFailure) -> None:
        self.recorded_failures.setdefault(harvest_id, []).append(failure)

    async def failures(self, harvest_id: str, *, limit: int = 500) -> list[HarvestFailure]:
        return self.recorded_failures.get(harvest_id, [])[:limit]
