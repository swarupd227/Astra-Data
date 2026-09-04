"""The reads behind the Estate Explorer.

Specification §15.3.2 and story S1.4.1: a site/project tree with counts and parse status, a
workbook table with facets, and one workbook's summary with a lineage mini-graph — over a
1,067-workbook site, in under two seconds.

**Why this is one module rather than a screen calling the graph API.** The Explorer's left
pane is a rollup, its centre pane is a filtered page, and its facet counts are aggregates
over the *unfiltered* set. Assembled from per-workbook reads that is a thousand round
trips; assembled here it is four queries, none of them per workbook:

1. every Workbook node of the scope, from the label table;
2. the CONTAINS edges that place them in projects, and the Project and Site nodes;
3. the OWNED_BY edges that name their owners, and the User nodes;
4. a relational count of each workbook's calculated fields.

Filtering, banding and facet counting then happen in one pass over that set. A thousand
workbooks is a small object in memory and a large number of round trips, so the trade is
not close.

**What the Explorer cannot show yet, and why it says so rather than inventing it.** §15.3.2's
centre pane lists tier, score, family, train and state. Every one of those is a Migration
Unit property (§3.1), and the MU is created by the Cartographer in E3 — there is no
assessment, no clustering and no state machine yet. Showing "MEDIUM" or "HARVESTED" for
every workbook would be a screen full of confident fiction. So those columns are reported
as unavailable with the epic that fills them, and the facets over them are absent rather
than empty: a filter with no values is a filter a user will try.

Class mix is the same shape of answer for a different reason. ``CalculatedField.class`` is
set by the Transpiler (E5) and absent at harvest, so what is available now is *how many*
calculated fields a workbook has, which is real and cheap, rather than how they are
classified, which does not exist.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from dataclasses import dataclass, field, replace
from typing import Any

import asyncpg

from .graph.queries import EDGE_INDEX_TABLE, NODE_INDEX_TABLE

logger = logging.getLogger(__name__)

#: Bound on one Explorer read. §15.3.2's target is a 1,067-workbook site; this leaves room
#: for an estate several times larger before the screen has to page the tree itself.
MAX_WORKBOOKS = 20_000

#: Spec §4.1.4 holds a workbook below this for review. The Explorer bands around it.
PARSE_QUALITY_THRESHOLD = 0.98

#: Columns §15.3.2 asks for that no epic has produced yet. Reported by name, with what
#: will produce them, so the screen can render an honest empty state instead of a blank.
PENDING_COLUMNS: dict[str, str] = {
    "score": "Complexity score is produced by assessment, which is the Cartographer's "
    "(E3/F3.1). A tier declared by a programme manager carries no score.",
    "family": "Model family is assigned by clustering (E3/F3.2) and confirmed at G2.",
    "train": "Release train membership is proposed by clustering and confirmed by the "
    "programme manager (E3/F3.3).",
    "state": "The Migration Unit state machine (spec §3.2) begins when the Cartographer "
    "creates the MU (E3/F3.2). A harvested workbook has no MU yet.",
    "class_mix": "Calculation classes are assigned by the Transpiler (E5/F5.1). Until then "
    "a workbook's calculated fields are counted but not classified.",
}


@dataclass(frozen=True, slots=True)
class Band:
    """One bucket of a banded facet."""

    key: str
    label: str
    low: float | None
    high: float | None

    def contains(self, value: float | None) -> bool:
        if value is None:
            return self.key == "unknown"
        if self.low is not None and value < self.low:
            return False
        return not (self.high is not None and value >= self.high)


#: §4.1.4's threshold is the line that matters, so it is a band boundary rather than a
#: number a reader has to compare against in their head.
PARSE_QUALITY_BANDS: tuple[Band, ...] = (
    Band("clean", "100%", 1.0, None),
    Band("good", "98–99%", PARSE_QUALITY_THRESHOLD, 1.0),
    Band("held", "90–97%", 0.90, PARSE_QUALITY_THRESHOLD),
    Band("poor", "under 90%", 0.0, 0.90),
    Band("unknown", "not parsed", None, None),
)

#: Usage bands. The boundaries are the ones a programme manager actually sorts on: nobody
#: has looked at it, a handful of people have, it is in weekly use, it is load-bearing.
USAGE_BANDS: tuple[Band, ...] = (
    Band("unused", "no views", 0, 1),
    Band("low", "1–49 views", 1, 50),
    Band("medium", "50–499 views", 50, 500),
    Band("high", "500+ views", 500, None),
    Band("unknown", "no usage data", None, None),
)


@dataclass(frozen=True, slots=True)
class WorkbookRow:
    """One row of the centre pane."""

    id: str
    luid: str
    name: str
    project: str | None
    project_id: str | None
    site: str | None
    site_id: str | None
    parse_quality: float | None
    views_90d: int | None
    distinct_viewers_90d: int | None
    owner: str | None
    owner_id: str | None
    calculated_fields: int
    held: bool
    """Below §4.1.4's threshold: written to the graph, not clear to advance."""

    tier: str | None = None
    """Declared by a programme manager (S1.4.1). Absent until somebody declares one:
    assessment, which would produce it automatically, is E3's."""

    withdrawn: bool = False
    withdrawn_reason: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "luid": self.luid,
            "name": self.name,
            "site": self.site,
            "site_id": self.site_id,
            "project": self.project,
            "project_id": self.project_id,
            "parse_quality": self.parse_quality,
            "parse_quality_band": _band_of(PARSE_QUALITY_BANDS, self.parse_quality),
            "views_90d": self.views_90d,
            "usage_band": _band_of(USAGE_BANDS, self.views_90d),
            "distinct_viewers_90d": self.distinct_viewers_90d,
            "owner": self.owner,
            "owner_id": self.owner_id,
            "calculated_fields": self.calculated_fields,
            "held": self.held,
            "tier": self.tier,
            "withdrawn": self.withdrawn,
            "withdrawn_reason": self.withdrawn_reason,
        }


@dataclass(slots=True)
class TreeNode:
    """A site or a project in the left pane, with its rollup."""

    id: str
    name: str
    kind: str
    workbooks: int = 0
    held: int = 0
    unparsed: int = 0
    views_90d: int = 0
    children: list[TreeNode] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "kind": self.kind,
            "workbooks": self.workbooks,
            "held": self.held,
            "unparsed": self.unparsed,
            "views_90d": self.views_90d,
            "children": [child.as_dict() for child in self.children],
        }


@dataclass(frozen=True, slots=True)
class EstateFilter:
    """The facets S1.4.1 asks for, restricted to the ones the estate can answer."""

    site: str | None = None
    project: str | None = None
    owner: str | None = None
    parse_quality_band: str | None = None
    usage_band: str | None = None
    tier: str | None = None
    held_only: bool = False
    unowned_only: bool = False
    include_withdrawn: bool = False
    """Withdrawn workbooks are hidden by default and counted separately. They are out of
    scope, not deleted — §15.2's "every action is a record" cuts both ways."""

    search: str | None = None

    def matches(self, row: WorkbookRow) -> bool:
        if self.site and row.site != self.site:
            return False
        if self.project and row.project != self.project:
            return False
        if self.owner and row.owner != self.owner:
            return False
        if self.tier and row.tier != self.tier:
            return False
        if row.withdrawn and not self.include_withdrawn:
            return False
        if self.held_only and not row.held:
            return False
        if self.unowned_only and row.owner:
            return False
        if self.parse_quality_band and _band_of(
            PARSE_QUALITY_BANDS, row.parse_quality
        ) != self.parse_quality_band:
            return False
        if self.usage_band and _band_of(USAGE_BANDS, row.views_90d) != self.usage_band:
            return False
        if self.search:
            needle = self.search.casefold()
            haystack = f"{row.name} {row.luid} {row.project or ''}".casefold()
            if needle not in haystack:
                return False
        return True

    #: Facets a count can be computed "without". The booleans are not here: clearing
    #: ``held_only`` to count its own options is meaningless, and typing it as a set makes
    #: that a checked fact rather than a convention.
    CLEARABLE = frozenset(
        {"site", "project", "owner", "tier", "parse_quality_band", "usage_band", "search"}
    )

    def without(self, facet: str) -> EstateFilter:
        """The same filter with one facet cleared.

        Facet counts are computed against the set filtered by *everything else*, which is
        what makes the numbers beside a facet's options mean "how many would I get if I
        picked this" rather than "how many did I already get".
        """
        if facet not in self.CLEARABLE:
            raise ValueError(f"{facet!r} is not a clearable facet")
        replacements: dict[str, Any] = {facet: None}
        return replace(self, **replacements)


@dataclass(frozen=True, slots=True)
class Estate:
    """Everything the Explorer needs for one scope, read once."""

    rows: list[WorkbookRow]
    read_ms: float

    def tree(self, where: EstateFilter) -> list[TreeNode]:
        """The left pane's rollup.

        Takes the filter for one reason only: whether withdrawn workbooks count. It
        ignores the site and project selection, because the tree is how you *change* that
        selection and a tree that pruned itself to the current one could not.

        The withdrawn part is not a nicety. With "show withdrawn" on and the tree counting
        only live work, the two panes showed 64 and 65 for the same estate — visible on
        screen, and exactly the kind of disagreement §15.2 exists to prevent.
        """
        return _build_tree(self.rows, include_withdrawn=where.include_withdrawn)

    def page(
        self, where: EstateFilter, *, offset: int = 0, limit: int = 100, sort: str = "name"
    ) -> dict[str, Any]:
        matching = [row for row in self.rows if where.matches(row)]
        matching.sort(key=_sorter(sort))
        window = matching[offset : offset + limit]
        return {
            "workbooks": [row.as_dict() for row in window],
            "total": len(matching),
            "offset": offset,
            "limit": limit,
            "estate_total": len(self.rows),
        }

    def facets(self, where: EstateFilter) -> dict[str, Any]:
        """Facet options with the count each would yield."""
        return {
            "parse_quality_band": _band_counts(
                PARSE_QUALITY_BANDS,
                [r for r in self.rows if where.without("parse_quality_band").matches(r)],
                lambda row: row.parse_quality,
            ),
            "usage_band": _band_counts(
                USAGE_BANDS,
                [r for r in self.rows if where.without("usage_band").matches(r)],
                lambda row: row.views_90d,
            ),
            "owner": _value_counts(
                [r for r in self.rows if where.without("owner").matches(r)],
                lambda row: row.owner,
            ),
            "tier": _value_counts(
                [r for r in self.rows if where.without("tier").matches(r)],
                lambda row: row.tier,
            ),
            "project": _value_counts(
                [r for r in self.rows if where.without("project").matches(r)],
                lambda row: row.project,
            ),
            "site": _value_counts(
                [r for r in self.rows if where.without("site").matches(r)],
                lambda row: row.site,
            ),
            # Facets §15.3.2 asks for that nothing can populate yet. Named with what
            # will populate them, so the screen can say so rather than showing an empty
            # dropdown a user will keep trying.
            "pending": [
                {"facet": name, "reason": reason}
                for name, reason in sorted(PENDING_COLUMNS.items())
                if name in {"state", "family", "train"}
            ],
            "withdrawn": sum(1 for row in self.rows if row.withdrawn),
        }


class EstateReader:
    """Reads the whole Explorer scope in four queries."""

    def __init__(self, pool: asyncpg.Pool, *, graph_name: str) -> None:
        self._pool = pool
        self._graph = graph_name

    async def read(
        self, *, limit: int = MAX_WORKBOOKS, scope: dict[str, Any] | None = None
    ) -> Estate:
        """Every workbook in the graph, placed, owned and counted.

        Read whole rather than paged because the facet counts are over the unfiltered set:
        a page cannot tell you how many workbooks are held, and asking the database once
        per facet per request is more work than reading the set once.
        """
        import time

        started = time.perf_counter()
        async with self._pool.acquire() as conn:
            workbooks = await self._workbooks(conn, limit)
            ids = [row["id"] for row in workbooks]
            projects = await self._parents(conn, ids, "Project")
            sites = await self._parents(conn, list({p["id"] for p in projects.values()}), "Site")
            owners = await self._owners(conn, ids)
            calcs = await self._calculation_counts(conn, ids)

        rows: list[WorkbookRow] = []
        for record in workbooks:
            properties = record["properties"]
            parent = projects.get(record["id"])
            site = sites.get(parent["id"]) if parent else None
            owner = owners.get(record["id"])
            state = (scope or {}).get(record["id"])
            quality = _float(properties.get("parse_quality"))
            rows.append(
                WorkbookRow(
                    id=record["id"],
                    luid=str(properties.get("luid", "")),
                    name=str(properties.get("name", "")),
                    project=parent["name"] if parent else None,
                    project_id=parent["id"] if parent else None,
                    site=site["name"] if site else None,
                    site_id=site["id"] if site else None,
                    parse_quality=quality,
                    views_90d=_int(properties.get("views_90d")),
                    distinct_viewers_90d=_int(properties.get("distinct_viewers_90d")),
                    owner=owner["name"] if owner else None,
                    owner_id=owner["id"] if owner else None,
                    calculated_fields=calcs.get(record["id"], 0),
                    held=quality is not None and quality < PARSE_QUALITY_THRESHOLD,
                    tier=getattr(state, "tier", None),
                    withdrawn=bool(getattr(state, "withdrawn", False)),
                    withdrawn_reason=getattr(state, "withdrawn_reason", None),
                )
            )

        elapsed = (time.perf_counter() - started) * 1000
        logger.info("estate read: %s workbooks in %.0f ms", len(rows), elapsed)
        return Estate(rows=rows, read_ms=round(elapsed, 2))

    # ------------------------------------------------------------------ the queries

    async def _workbooks(
        self, conn: asyncpg.Connection, limit: int
    ) -> list[dict[str, Any]]:
        from .graph import queries

        rows = await conn.fetch(
            queries.ELEMENTS_OF_LABEL_SQL, self._graph, "Workbook", min(limit, MAX_WORKBOOKS)
        )
        if not rows:
            return []
        ids = [row["id"] for row in rows]
        hydrated = await self._hydrate(conn, "Workbook", ids)
        return [{"id": i, "properties": hydrated[i]} for i in ids if i in hydrated]

    async def _parents(
        self, conn: asyncpg.Connection, child_ids: Sequence[str], label: str
    ) -> dict[str, dict[str, Any]]:
        """Who CONTAINS each of these, where the container has the wanted label."""
        if not child_ids:
            return {}
        rows = await conn.fetch(
            f"""
            SELECT e.to_id AS child, e.from_id AS parent
            FROM {EDGE_INDEX_TABLE} e
            JOIN {NODE_INDEX_TABLE} n
              ON n.id = e.from_id AND n.kind = 'node' AND n.graph = $1 AND n.label = $3
            WHERE e.graph = $1 AND e.label = 'CONTAINS' AND e.to_id = ANY($2::text[])
              AND n.retired_at IS NULL
            """,
            self._graph,
            list(child_ids),
            label,
        )
        parent_ids = list({row["parent"] for row in rows})
        hydrated = await self._hydrate(conn, label, parent_ids)
        out: dict[str, dict[str, Any]] = {}
        for row in rows:
            properties = hydrated.get(row["parent"])
            if properties is not None:
                out[row["child"]] = {
                    "id": row["parent"],
                    "name": str(properties.get("name", "")),
                }
        return out

    async def _owners(
        self, conn: asyncpg.Connection, workbook_ids: Sequence[str]
    ) -> dict[str, dict[str, Any]]:
        if not workbook_ids:
            return {}
        rows = await conn.fetch(
            f"""
            SELECT e.from_id AS workbook, e.to_id AS owner
            FROM {EDGE_INDEX_TABLE} e
            JOIN {NODE_INDEX_TABLE} n
              ON n.id = e.to_id AND n.kind = 'node' AND n.graph = $1 AND n.label = 'User'
            WHERE e.graph = $1 AND e.label = 'OWNED_BY' AND e.from_id = ANY($2::text[])
              AND n.retired_at IS NULL
            """,
            self._graph,
            list(workbook_ids),
        )
        owner_ids = list({row["owner"] for row in rows})
        hydrated = await self._hydrate(conn, "User", owner_ids)
        out: dict[str, dict[str, Any]] = {}
        for row in rows:
            properties = hydrated.get(row["owner"])
            if properties is not None:
                out[row["workbook"]] = {
                    "id": row["owner"],
                    "name": str(properties.get("display") or properties.get("upn", "")),
                }
        return out

    async def _calculation_counts(
        self, conn: asyncpg.Connection, workbook_ids: Sequence[str]
    ) -> dict[str, int]:
        """How many calculated fields each workbook holds.

        Two hops, because that is how the ontology models it: §4.1.2 has CONTAINS reach
        Worksheets and Dashboards, and ENCODES reach a Worksheet's fields. There is no
        Workbook→CalculatedField edge, and a single-hop count over CONTAINS returns zero
        for every workbook in every estate — which is exactly what it did until a smoke
        test showed a column of noughts.

        DISTINCT because a calculation encoded on three sheets of one workbook is one
        calculation, not three.

        Counted from the adjacency index rather than by reading the fields: the count is a
        relational aggregate, and the *classes* — which is what §15.3.2 actually wants —
        do not exist until the Transpiler assigns them (E5).
        """
        if not workbook_ids:
            return {}
        rows = await conn.fetch(
            f"""
            SELECT sheets.from_id AS workbook, count(DISTINCT encodes.to_id) AS n
            FROM {EDGE_INDEX_TABLE} sheets
            JOIN {EDGE_INDEX_TABLE} encodes
              ON encodes.graph = sheets.graph
             AND encodes.label = 'ENCODES'
             AND encodes.from_id = sheets.to_id
            JOIN {NODE_INDEX_TABLE} n
              ON n.id = encodes.to_id AND n.kind = 'node' AND n.graph = $1
             AND n.label = 'CalculatedField' AND n.retired_at IS NULL
            WHERE sheets.graph = $1 AND sheets.label = 'CONTAINS'
              AND sheets.from_id = ANY($2::text[])
            GROUP BY sheets.from_id
            """,
            self._graph,
            list(workbook_ids),
        )
        return {row["workbook"]: int(row["n"]) for row in rows}

    async def _hydrate(
        self, conn: asyncpg.Connection, label: str, ids: Sequence[str]
    ) -> dict[str, dict[str, Any]]:
        from .graph import queries

        if not ids:
            return {}
        sql = queries.hydrate_nodes(self._graph, [label])
        rows = await conn.fetch(sql, [queries.agtype_literal(i) for i in ids])
        out: dict[str, dict[str, Any]] = {}
        for row in rows:
            properties = queries.decode_properties(row["properties"])
            out[str(properties["id"])] = properties
        return out


# --------------------------------------------------------------------------- helpers


def _build_tree(
    rows: Sequence[WorkbookRow], *, include_withdrawn: bool = False
) -> list[TreeNode]:
    """Site → project, with each level's rollup.

    Workbooks are not nodes of the tree. A thousand leaves is not a tree anybody navigates,
    and the centre pane is the list — the left pane's job is counts and where the trouble is.
    """
    sites: dict[str, TreeNode] = {}
    projects: dict[tuple[str, str], TreeNode] = {}

    for row in rows:
        site_key = row.site_id or "__unplaced__"
        site = sites.get(site_key)
        if site is None:
            site = TreeNode(
                id=site_key, name=row.site or "Unplaced", kind="site"
            )
            sites[site_key] = site

        project_key = (site_key, row.project_id or "__unplaced__")
        project = projects.get(project_key)
        if project is None:
            project = TreeNode(
                id=row.project_id or f"{site_key}:unplaced",
                name=row.project or "Unplaced",
                kind="project",
            )
            projects[project_key] = project
            site.children.append(project)

        if row.withdrawn and not include_withdrawn:
            # Out of scope, so not counted as work — unless the centre pane is showing
            # them, in which case the two panes must agree about how many there are.
            continue
        for node in (site, project):
            node.workbooks += 1
            node.views_90d += row.views_90d or 0
            if row.held:
                node.held += 1
            if row.parse_quality is None:
                node.unparsed += 1

    for site in sites.values():
        site.children.sort(key=lambda node: node.name)
    return sorted(sites.values(), key=lambda node: node.name)


def _band_of(bands: Sequence[Band], value: float | None) -> str:
    for band in bands:
        if band.key != "unknown" and band.contains(value) and value is not None:
            return band.key
    return "unknown"


def _band_counts(
    bands: Sequence[Band], rows: Sequence[WorkbookRow], value: Any
) -> list[dict[str, Any]]:
    counts: dict[str, int] = {band.key: 0 for band in bands}
    for row in rows:
        counts[_band_of(bands, value(row))] += 1
    return [
        {"key": band.key, "label": band.label, "count": counts[band.key]} for band in bands
    ]


def _value_counts(rows: Sequence[WorkbookRow], value: Any) -> list[dict[str, Any]]:
    counts: dict[str, int] = {}
    unset = 0
    for row in rows:
        found = value(row)
        if found:
            counts[found] = counts.get(found, 0) + 1
        else:
            unset += 1
    out = [
        {"key": key, "label": key, "count": count}
        for key, count in sorted(counts.items(), key=lambda item: (-item[1], item[0]))
    ]
    if unset:
        out.append({"key": "__none__", "label": "Unassigned", "count": unset})
    return out


def _sorter(sort: str) -> Any:
    """Sort keys the centre pane offers. Unknown sorts fall back to name rather than
    erroring: a stale bookmark should not be a 400."""
    match sort:
        case "usage":
            return lambda row: (-(row.views_90d or 0), row.name)
        case "parse_quality":
            return lambda row: (
                row.parse_quality if row.parse_quality is not None else 2.0,
                row.name,
            )
        case "calculations":
            return lambda row: (-row.calculated_fields, row.name)
        case _:
            return lambda row: (row.site or "", row.project or "", row.name)


def _float(value: Any) -> float | None:
    return float(value) if isinstance(value, int | float) else None


def _int(value: Any) -> int | None:
    return int(value) if isinstance(value, int | float) else None


__all__ = [
    "MAX_WORKBOOKS",
    "PARSE_QUALITY_BANDS",
    "PARSE_QUALITY_THRESHOLD",
    "PENDING_COLUMNS",
    "USAGE_BANDS",
    "Band",
    "Estate",
    "EstateFilter",
    "EstateReader",
    "TreeNode",
    "WorkbookRow",
]
