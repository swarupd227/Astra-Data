"""Parse quality, and the constructs the grammar could not read.

S1.2.2 is the parity engineer's story: know, before the Calibration Wave, which workbooks
the grammar cannot yet read.

**How the score is computed.** Specification §4.1.4 defines parse quality as the fraction
of source constructs the adapter grammar recognised. A construct an engineer has accepted
as *ignorable* counts towards the score, because the score answers "may the platform
proceed with this workbook" and that decision says it may:

    parse_quality = (recognised + ignorable) / total

The honest grammar-coverage figure is not lost: ``recognised``, ``ignorable`` and
``total`` are all stored, so the Calibration Report can show grammar coverage separately
from workbook readiness.

**Where the constructs live.** Relationally, not in the graph. An unrecognised construct is
a fact about a parse, not a fact about the estate — the same reasoning that puts harvest
runs in §21's relational tables rather than in the ontology. It also has to be grouped by
construct text across the whole estate, which is a query the graph is the wrong shape for.
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Protocol

import asyncpg

CONSTRUCT_TABLE = "public.parse_construct"
WORKBOOK_TABLE = "public.harvest_workbook"


def score(recognised: int, ignorable: int, total: int) -> float:
    """Specification §4.1.4, with accepted constructs counted as read.

    A workbook with no constructs at all scores 1.0: there was nothing the grammar failed
    to read, and treating that as zero would hold an empty workbook forever.
    """
    if total <= 0:
        return 1.0
    return min(1.0, (recognised + ignorable) / total)


@dataclass(frozen=True, slots=True)
class Construct:
    """One construct the grammar could not read, where it was found, and its decision."""

    id: int
    graph: str
    site: str
    workbook_luid: str
    workbook_name: str
    project: str
    construct: str
    """Verbatim, as the source had it (spec §4.1.4)."""

    sheet: str | None
    field: str | None
    detail: str
    unrecognised: bool
    """True while the construct still counts against parse quality. Marking it ignorable
    sets this false; that is what 're-scores the workbook' means (S1.2.2)."""

    ignorable_reason: str | None = None
    decided_by: str | None = None
    decided_at: str | None = None
    grammar_version: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "site": self.site,
            "workbook_luid": self.workbook_luid,
            "workbook_name": self.workbook_name,
            "project": self.project,
            "construct": self.construct,
            "location": {"sheet": self.sheet, "field": self.field},
            "detail": self.detail,
            "unrecognised": self.unrecognised,
            "ignorable_reason": self.ignorable_reason,
            "decided_by": self.decided_by,
            "decided_at": self.decided_at,
            "grammar_version": self.grammar_version,
        }


@dataclass(frozen=True, slots=True)
class ConstructGroup:
    """One construct text, and how much of the estate it is holding up.

    The Parse Quality Queue is worked construct-first, not workbook-first: one grammar gap
    typically blocks many workbooks, and "fixing this releases 38 workbooks" is the number
    that decides what to do next (S1.4.3).
    """

    construct: str
    occurrences: int
    workbooks: int
    workbooks_held: int
    """How many workbooks below the threshold would be released by resolving this alone."""

    sites: tuple[str, ...]
    example_location: dict[str, str | None]
    unrecognised: bool

    def as_dict(self) -> dict[str, Any]:
        return {
            "construct": self.construct,
            "occurrences": self.occurrences,
            "workbooks": self.workbooks,
            "workbooks_released_if_resolved": self.workbooks_held,
            "sites": list(self.sites),
            "example_location": self.example_location,
            "unrecognised": self.unrecognised,
        }


@dataclass(frozen=True, slots=True)
class HeldWorkbook:
    """A workbook below the parse-quality threshold."""

    site: str
    workbook_luid: str
    workbook_name: str
    project: str
    parse_quality: float
    recognised: int
    ignorable: int
    total: int
    unrecognised_constructs: int
    grammar_version: str | None
    harvested_at: str | None

    def as_dict(self) -> dict[str, Any]:
        return {
            "site": self.site,
            "workbook_luid": self.workbook_luid,
            "workbook_name": self.workbook_name,
            "project": self.project,
            "parse_quality": self.parse_quality,
            # Flat, like every other count the console reads. An earlier version nested these
            # four under a "constructs" object; nothing else in the API does that, and the
            # console read them flat and rendered "undefined unrecognised of undefined".
            "recognised": self.recognised,
            "ignorable": self.ignorable,
            "unrecognised_constructs": self.unrecognised_constructs,
            "total": self.total,
            "grammar_version": self.grammar_version,
            "harvested_at": self.harvested_at,
        }


@dataclass(frozen=True, slots=True)
class Rescore:
    """What a re-score did to one workbook."""

    site: str
    workbook_luid: str
    previous_quality: float | None
    parse_quality: float
    released: bool
    """True when the workbook was below the threshold and now is not."""

    def as_dict(self) -> dict[str, Any]:
        return {
            "site": self.site,
            "workbook_luid": self.workbook_luid,
            "previous_parse_quality": self.previous_quality,
            "parse_quality": self.parse_quality,
            "released": self.released,
        }


class ParseQualityStore(Protocol):
    async def record_constructs(
        self, graph: str, site: str, workbook_luid: str, constructs: Sequence[dict[str, Any]]
    ) -> None: ...

    async def held(
        self, graph: str, *, threshold: float, limit: int = 200
    ) -> list[HeldWorkbook]: ...

    async def construct_groups(
        self, graph: str, *, threshold: float, include_resolved: bool = False, limit: int = 200
    ) -> list[ConstructGroup]: ...

    async def constructs_for(
        self, graph: str, site: str, workbook_luid: str
    ) -> list[Construct]: ...

    async def occurrences_of(
        self, graph: str, construct: str, *, limit: int = 25
    ) -> list[Construct]: ...

    async def mark_ignorable(
        self, graph: str, construct: str, *, reason: str, principal: str, site: str | None = None
    ) -> list[tuple[str, str]]: ...


def _now() -> datetime:
    return datetime.now(UTC)


def _iso(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, str):
        return value
    rendered: str = value.astimezone(UTC).isoformat(timespec="milliseconds")
    return rendered.replace("+00:00", "Z")


class PostgresParseQualityStore:
    """Parse-quality records in PostgreSQL."""

    def __init__(self, pool: asyncpg.Pool) -> None:
        self._pool = pool

    async def record_constructs(
        self, graph: str, site: str, workbook_luid: str, constructs: Sequence[dict[str, Any]]
    ) -> None:
        """Replace this workbook's constructs with what the latest parse found.

        Decisions already made about a construct text are carried forward: an engineer who
        marked ``RAWSQL_INT(...)`` ignorable last week should not have to do it again
        because the workbook was re-parsed.
        """
        async with self._pool.acquire() as conn, conn.transaction():
            decided = {
                row["construct"]: row
                for row in await conn.fetch(
                    f"SELECT DISTINCT ON (construct) construct, ignorable_reason, decided_by, "
                    f"decided_at FROM {CONSTRUCT_TABLE} "
                    f"WHERE graph = $1 AND unrecognised = false ORDER BY construct, decided_at DESC",
                    graph,
                )
            }
            await conn.execute(
                f"DELETE FROM {CONSTRUCT_TABLE} "
                f"WHERE graph = $1 AND site = $2 AND workbook_luid = $3",
                graph,
                site,
                workbook_luid,
            )
            if not constructs:
                return
            await conn.executemany(
                f"""
                INSERT INTO {CONSTRUCT_TABLE}
                    (graph, site, workbook_luid, construct, sheet, field, detail,
                     unrecognised, ignorable_reason, decided_by, decided_at, grammar_version)
                VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12)
                """,
                [
                    (
                        graph,
                        site,
                        workbook_luid,
                        item["construct"],
                        item.get("sheet"),
                        item.get("field"),
                        item.get("detail", ""),
                        item["construct"] not in decided,
                        decided.get(item["construct"], {}).get("ignorable_reason"),
                        decided.get(item["construct"], {}).get("decided_by"),
                        decided.get(item["construct"], {}).get("decided_at"),
                        item.get("grammar_version"),
                    )
                    for item in constructs
                ],
            )

    async def held(
        self, graph: str, *, threshold: float, limit: int = 200
    ) -> list[HeldWorkbook]:
        """The Parse Quality Queue: workbooks the grammar could not fully read."""
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(
                f"""
                SELECT w.site, w.workbook_luid, w.workbook_name, w.project, w.parse_quality,
                       w.constructs_recognised, w.constructs_ignorable, w.constructs_total,
                       w.grammar_version, w.harvested_at,
                       (SELECT count(*) FROM {CONSTRUCT_TABLE} c
                         WHERE c.graph = w.graph AND c.site = w.site
                           AND c.workbook_luid = w.workbook_luid AND c.unrecognised) AS live
                  FROM {WORKBOOK_TABLE} w
                 WHERE w.graph = $1 AND w.parse_quality IS NOT NULL AND w.parse_quality < $2
                 ORDER BY w.parse_quality, w.workbook_name
                 LIMIT $3
                """,
                graph,
                threshold,
                limit,
            )
        return [
            HeldWorkbook(
                site=row["site"],
                workbook_luid=row["workbook_luid"],
                workbook_name=row["workbook_name"],
                project=row["project"],
                parse_quality=row["parse_quality"],
                recognised=row["constructs_recognised"] or 0,
                ignorable=row["constructs_ignorable"] or 0,
                total=row["constructs_total"] or 0,
                unrecognised_constructs=row["live"],
                grammar_version=row["grammar_version"],
                harvested_at=_iso(row["harvested_at"]),
            )
            for row in rows
        ]

    async def construct_groups(
        self, graph: str, *, threshold: float, include_resolved: bool = False, limit: int = 200
    ) -> list[ConstructGroup]:
        """Constructs grouped by text, with how much of the estate each is holding.

        ``workbooks_released_if_resolved`` is the figure that orders the work: it counts
        the held workbooks for which this is the *only* remaining unrecognised construct.
        """
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(
                f"""
                WITH live AS (
                    SELECT c.*, w.parse_quality
                      FROM {CONSTRUCT_TABLE} c
                      JOIN {WORKBOOK_TABLE} w
                        ON w.graph = c.graph AND w.site = c.site
                       AND w.workbook_luid = c.workbook_luid
                     WHERE c.graph = $1 AND ($3 OR c.unrecognised)
                ), blocking AS (
                    SELECT site, workbook_luid, count(DISTINCT construct) AS distinct_constructs
                      FROM live
                     WHERE unrecognised AND parse_quality < $2
                     GROUP BY site, workbook_luid
                )
                SELECT l.construct,
                       count(*) AS occurrences,
                       count(DISTINCT (l.site, l.workbook_luid)) AS workbooks,
                       count(DISTINCT (l.site, l.workbook_luid))
                         FILTER (WHERE b.distinct_constructs = 1) AS workbooks_held,
                       array_agg(DISTINCT l.site) AS sites,
                       bool_or(l.unrecognised) AS unrecognised,
                       (array_agg(l.sheet))[1] AS example_sheet,
                       (array_agg(l.field))[1] AS example_field
                  FROM live l
                  LEFT JOIN blocking b
                    ON b.site = l.site AND b.workbook_luid = l.workbook_luid
                 GROUP BY l.construct
                 ORDER BY workbooks_held DESC, occurrences DESC
                 LIMIT $4
                """,
                graph,
                threshold,
                include_resolved,
                limit,
            )
        return [
            ConstructGroup(
                construct=row["construct"],
                occurrences=row["occurrences"],
                workbooks=row["workbooks"],
                workbooks_held=row["workbooks_held"] or 0,
                sites=tuple(row["sites"]),
                example_location={"sheet": row["example_sheet"], "field": row["example_field"]},
                unrecognised=row["unrecognised"],
            )
            for row in rows
        ]

    async def constructs_for(
        self, graph: str, site: str, workbook_luid: str
    ) -> list[Construct]:
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(
                f"""
                SELECT c.*, w.workbook_name, w.project
                  FROM {CONSTRUCT_TABLE} c
                  JOIN {WORKBOOK_TABLE} w
                    ON w.graph = c.graph AND w.site = c.site
                   AND w.workbook_luid = c.workbook_luid
                 WHERE c.graph = $1 AND c.site = $2 AND c.workbook_luid = $3
                 ORDER BY c.id
                """,
                graph,
                site,
                workbook_luid,
            )
        return [_construct(row) for row in rows]

    async def occurrences_of(
        self, graph: str, construct: str, *, limit: int = 25
    ) -> list[Construct]:
        """Where one construct appears across the estate.

        The queue groups by construct text and reports one example; raising an issue needs
        the list, because "it is in these eleven workbooks, on these sheets" is what makes
        a grammar gap actionable rather than abstract.
        """
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(
                f"""
                SELECT c.*, w.workbook_name, w.project
                  FROM {CONSTRUCT_TABLE} c
                  JOIN {WORKBOOK_TABLE} w
                    ON w.graph = c.graph AND w.site = c.site
                   AND w.workbook_luid = c.workbook_luid
                 WHERE c.graph = $1 AND c.construct = $2
                 ORDER BY c.site, w.workbook_name, c.id
                 LIMIT $3
                """,
                graph,
                construct,
                limit,
            )
        return [_construct(row) for row in rows]

    async def mark_ignorable(
        self, graph: str, construct: str, *, reason: str, principal: str, site: str | None = None
    ) -> list[tuple[str, str]]:
        """Accept a construct the grammar cannot read.

        Returns the (site, workbook_luid) pairs affected, so the caller can re-score them.
        The decision is recorded against every occurrence: an auditor asking why a
        workbook was released gets the reason and the person, not just a number that moved.
        """
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(
                f"""
                UPDATE {CONSTRUCT_TABLE}
                   SET unrecognised = false,
                       ignorable_reason = $3,
                       decided_by = $4,
                       decided_at = $5
                 WHERE graph = $1 AND construct = $2 AND unrecognised
                   AND ($6::text IS NULL OR site = $6)
             RETURNING site, workbook_luid
                """,
                graph,
                construct,
                reason,
                principal,
                _now(),
                site,
            )
        return sorted({(row["site"], row["workbook_luid"]) for row in rows})


def _construct(row: Any) -> Construct:
    return Construct(
        id=row["id"],
        graph=row["graph"],
        site=row["site"],
        workbook_luid=row["workbook_luid"],
        workbook_name=row["workbook_name"],
        project=row["project"],
        construct=row["construct"],
        sheet=row["sheet"],
        field=row["field"],
        detail=row["detail"],
        unrecognised=row["unrecognised"],
        ignorable_reason=row["ignorable_reason"],
        decided_by=row["decided_by"],
        decided_at=_iso(row["decided_at"]),
        grammar_version=row["grammar_version"],
    )


class InMemoryParseQualityStore:
    """The same store without a database, for unit tests.

    Takes the harvest store rather than duplicating its records: the SQL implementation
    joins ``parse_construct`` to ``harvest_workbook``, and this does the same join in
    Python so the two cannot answer differently.
    """

    def __init__(self, harvest_store: Any = None) -> None:
        self.rows: list[dict[str, Any]] = []
        self.harvest_store = harvest_store
        self._next_id = 1

    @property
    def _records(self) -> dict[tuple[str, str, str], dict[str, Any]]:
        if self.harvest_store is None:
            return {}
        counts = self.harvest_store.counts_by_workbook
        meta = self.harvest_store.workbook_meta
        out: dict[tuple[str, str, str], dict[str, Any]] = {}
        for key, (recognised, ignorable, total, quality) in counts.items():
            state = self.harvest_store.workbooks.get(key)
            out[key] = {
                "parse_quality": quality,
                "constructs_recognised": recognised,
                "constructs_ignorable": ignorable,
                "constructs_total": total,
                "harvested_at": state.harvested_at if state else None,
                **meta.get(key, {}),
            }
        return out

    async def record_constructs(
        self, graph: str, site: str, workbook_luid: str, constructs: Sequence[dict[str, Any]]
    ) -> None:
        decided = {
            row["construct"]: row for row in self.rows
            if row["graph"] == graph and not row["unrecognised"]
        }
        self.rows = [
            row
            for row in self.rows
            if not (
                row["graph"] == graph
                and row["site"] == site
                and row["workbook_luid"] == workbook_luid
            )
        ]
        for item in constructs:
            previous = decided.get(item["construct"])
            self.rows.append(
                {
                    "id": self._next_id,
                    "graph": graph,
                    "site": site,
                    "workbook_luid": workbook_luid,
                    "construct": item["construct"],
                    "sheet": item.get("sheet"),
                    "field": item.get("field"),
                    "detail": item.get("detail", ""),
                    "unrecognised": previous is None,
                    "ignorable_reason": previous["ignorable_reason"] if previous else None,
                    "decided_by": previous["decided_by"] if previous else None,
                    "decided_at": previous["decided_at"] if previous else None,
                    "grammar_version": item.get("grammar_version"),
                }
            )
            self._next_id += 1

    async def held(
        self, graph: str, *, threshold: float, limit: int = 200
    ) -> list[HeldWorkbook]:
        out = []
        for (g, site, luid), record in self._records.items():
            if g != graph or record.get("parse_quality") is None:
                continue
            if record["parse_quality"] >= threshold:
                continue
            live = len(
                [
                    row
                    for row in self.rows
                    if row["graph"] == graph
                    and row["site"] == site
                    and row["workbook_luid"] == luid
                    and row["unrecognised"]
                ]
            )
            out.append(
                HeldWorkbook(
                    site=site,
                    workbook_luid=luid,
                    workbook_name=record.get("workbook_name", ""),
                    project=record.get("project", ""),
                    parse_quality=record["parse_quality"],
                    recognised=record.get("constructs_recognised", 0),
                    ignorable=record.get("constructs_ignorable", 0),
                    total=record.get("constructs_total", 0),
                    unrecognised_constructs=live,
                    grammar_version=record.get("grammar_version"),
                    harvested_at=record.get("harvested_at"),
                )
            )
        return sorted(out, key=lambda w: (w.parse_quality, w.workbook_name))[:limit]

    async def construct_groups(
        self, graph: str, *, threshold: float, include_resolved: bool = False, limit: int = 200
    ) -> list[ConstructGroup]:
        held_keys = {(w.site, w.workbook_luid) for w in await self.held(graph, threshold=threshold)}
        blocking: dict[tuple[str, str], set[str]] = {}
        for row in self.rows:
            key = (row["site"], row["workbook_luid"])
            if row["graph"] == graph and row["unrecognised"] and key in held_keys:
                blocking.setdefault(key, set()).add(row["construct"])

        grouped: dict[str, list[dict[str, Any]]] = {}
        for row in self.rows:
            if row["graph"] != graph:
                continue
            if not include_resolved and not row["unrecognised"]:
                continue
            grouped.setdefault(row["construct"], []).append(row)

        out = []
        for construct, rows in grouped.items():
            keys = {(row["site"], row["workbook_luid"]) for row in rows}
            releases = len([k for k in keys if blocking.get(k) == {construct}])
            out.append(
                ConstructGroup(
                    construct=construct,
                    occurrences=len(rows),
                    workbooks=len(keys),
                    workbooks_held=releases,
                    sites=tuple(sorted({row["site"] for row in rows})),
                    example_location={"sheet": rows[0]["sheet"], "field": rows[0]["field"]},
                    unrecognised=any(row["unrecognised"] for row in rows),
                )
            )
        return sorted(out, key=lambda g: (-g.workbooks_held, -g.occurrences))[:limit]

    async def constructs_for(
        self, graph: str, site: str, workbook_luid: str
    ) -> list[Construct]:
        return [
            _construct(
                {
                    **row,
                    "workbook_name": self._records.get(
                        (graph, site, workbook_luid), {}
                    ).get("workbook_name", ""),
                    "project": self._records.get((graph, site, workbook_luid), {}).get(
                        "project", ""
                    ),
                }
            )
            for row in self.rows
            if row["graph"] == graph
            and row["site"] == site
            and row["workbook_luid"] == workbook_luid
        ]

    async def occurrences_of(
        self, graph: str, construct: str, *, limit: int = 25
    ) -> list[Construct]:
        matching = [
            row
            for row in self.rows
            if row["graph"] == graph and row["construct"] == construct
        ]
        matching.sort(key=lambda row: (row["site"], row["workbook_luid"], row["id"]))
        return [
            _construct(
                {
                    **row,
                    "workbook_name": self._records.get(
                        (graph, row["site"], row["workbook_luid"]), {}
                    ).get("workbook_name", ""),
                    "project": self._records.get(
                        (graph, row["site"], row["workbook_luid"]), {}
                    ).get("project", ""),
                }
            )
            for row in matching[:limit]
        ]

    async def mark_ignorable(
        self, graph: str, construct: str, *, reason: str, principal: str, site: str | None = None
    ) -> list[tuple[str, str]]:
        affected = set()
        for row in self.rows:
            if (
                row["graph"] == graph
                and row["construct"] == construct
                and row["unrecognised"]
                and (site is None or row["site"] == site)
            ):
                row["unrecognised"] = False
                row["ignorable_reason"] = reason
                row["decided_by"] = principal
                row["decided_at"] = _iso(_now())
                affected.add((row["site"], row["workbook_luid"]))
        return sorted(affected)


__all__ = [
    "CONSTRUCT_TABLE",
    "Construct",
    "ConstructGroup",
    "HeldWorkbook",
    "InMemoryParseQualityStore",
    "ParseQualityStore",
    "PostgresParseQualityStore",
    "Rescore",
    "json",
    "score",
]
