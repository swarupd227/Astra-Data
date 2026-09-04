"""The Lineage View's read: workbooks, the tables and fields behind them, and how much
they share.

Specification §15.3.2: "Force-directed graph of workbooks ↔ tables ↔ fields for a family or
a selection; edge weight = shared lineage strength; colour = state." Story S1.4.2 gives the
purpose, which is the part that decides the design: *so I can see why the Cartographer
grouped a family and challenge it*.

**Challenging a grouping means seeing the evidence the grouping was made from.** §12.1
defines that evidence exactly — for every workbook, the source tables it reaches, the
fields it encodes, and the multiset of calculated-field AST shapes it defines — and scores
a pair as ``0.5·J(tables) + 0.3·J(fields) + 0.2·shared_shapes / max_shapes``.

So this reads those three sets per workbook and scores every pair that shares anything.

**Stored edges win.** The ontology has ``SHARES_LINEAGE`` with those three properties,
written by the Cartographer (E3). When those edges exist the view shows *them*, because
they are the numbers the clustering actually used — a recomputed figure could differ, and a
model engineer challenging a family needs the evidence that produced it, not a second
opinion that happens to be close. When they do not exist the same formula is computed
read-only from the same inputs, and the response says which it is. Nothing is written
either way: this module makes no families and no decisions.

**Why pairs are found through an inverted index.** A scope of 250 workbooks is 31,125
pairs, almost all of which share nothing. Building table→workbooks, field→workbooks *and*
shape→workbooks maps, then scoring only pairs that co-occur in one of them, turns the
quadratic into something proportional to the sharing actually present — sparse on a real
estate, and bounded by the scope limit on a pathological one.

All three are indexed, and the third is not optional. Two workbooks that share every
calculation shape and no lineage at all score 0.2, which is above the default threshold —
so indexing only tables and fields would have silently dropped a link the formula says
exists.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from dataclasses import dataclass, field
from itertools import combinations
from typing import Any

import asyncpg

from .context.signature import SignatureError, ast_shape
from .graph.queries import EDGE_INDEX_TABLE, NODE_INDEX_TABLE

logger = logging.getLogger(__name__)

#: A force-directed graph of more than this is a hairball nobody can read, and the pairwise
#: scoring is bounded by it. The view is "for a family or a selection" (§15.3.2), not for
#: the whole estate, so the limit is part of the design rather than a guard against it.
MAX_WORKBOOKS = 250

#: §12.1's weights. Named rather than inlined because the same numbers appear in the
#: Cartographer's own scoring and the two must not drift.
WEIGHT_TABLES = 0.5
WEIGHT_FIELDS = 0.3
WEIGHT_SHAPES = 0.2

#: Below this two workbooks share so little that an edge between them is noise on the
#: screen. The caller can lower it; it exists so the default view is readable.
DEFAULT_MIN_STRENGTH = 0.15

#: What the console may colour nodes by, and what it cannot yet.
#: §15.3.2 asks for "colour = state". MU state is the §3.2 state machine, which begins when
#: the Cartographer creates a Migration Unit (E3/F3.2) — so it is offered by name, with the
#: reason, rather than silently replaced by something else that happens to be colourful.
COLOUR_MODES: tuple[dict[str, Any], ...] = (
    {"key": "type", "label": "Node type", "available": True},
    {
        "key": "parse_status",
        "label": "Parse status",
        "available": True,
        "note": "Held workbooks are below the §4.1.4 threshold.",
    },
    {
        "key": "family",
        "label": "Model family",
        "available": True,
        "note": "Colours by IN_FAMILY membership. Empty until the Cartographer clusters.",
    },
    {
        "key": "mu_state",
        "label": "Migration Unit state",
        "available": False,
        "reason": "The §3.2 state machine begins when the Cartographer creates the MU "
        "(E3/F3.2). A harvested workbook has a parse and nothing downstream of it.",
    },
)

#: Node types the view can draw. The order is the order the legend renders in.
NODE_TYPES: tuple[str, ...] = ("Workbook", "Datasource", "Table", "Field", "CalculatedField")


@dataclass(frozen=True, slots=True)
class LineageNode:
    id: str
    type: str
    name: str
    detail: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {"id": self.id, "type": self.type, "name": self.name, **self.detail}


@dataclass(frozen=True, slots=True)
class LineageEdge:
    """A structural edge: this workbook reaches that table."""

    source: str
    target: str
    type: str

    def as_dict(self) -> dict[str, Any]:
        return {"source": self.source, "target": self.target, "type": self.type}


@dataclass(frozen=True, slots=True)
class SharedLineage:
    """How much two workbooks have in common, and what of."""

    source: str
    target: str
    strength: float
    jaccard_tables: float
    jaccard_fields: float
    shared_shapes: int
    origin: str
    """``graph`` when the Cartographer wrote it, ``computed`` when this read scored it."""

    def as_dict(self) -> dict[str, Any]:
        return {
            "source": self.source,
            "target": self.target,
            "strength": round(self.strength, 4),
            "jaccard_tables": round(self.jaccard_tables, 4),
            "jaccard_fields": round(self.jaccard_fields, 4),
            "shared_calc_shapes": self.shared_shapes,
            "origin": self.origin,
        }


@dataclass(frozen=True, slots=True)
class Family:
    id: str
    name: str
    state: str | None
    members: list[str]

    def as_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "state": self.state,
            "members": self.members,
            "size": len(self.members),
        }


@dataclass(frozen=True, slots=True)
class LineageGraph:
    nodes: list[LineageNode]
    edges: list[LineageEdge]
    shared: list[SharedLineage]
    families: list[Family]
    origin: str
    """Where the shared-lineage figures came from, for the whole graph."""

    truncated: bool
    auto_scoped_to: str | None
    """The site this read narrowed itself to when the caller named no scope and the estate
    was larger than the cap. ``None`` when the caller chose, or when everything fitted."""

    workbook_count: int
    read_ms: float

    def as_dict(self) -> dict[str, Any]:
        return {
            "nodes": [node.as_dict() for node in self.nodes],
            "edges": [edge.as_dict() for edge in self.edges],
            "shared_lineage": [link.as_dict() for link in self.shared],
            "families": [family.as_dict() for family in self.families],
            "shared_lineage_origin": self.origin,
            "colour_modes": [dict(mode) for mode in COLOUR_MODES],
            "node_types": list(NODE_TYPES),
            "truncated": self.truncated,
            "auto_scoped_to": self.auto_scoped_to,
            "workbook_count": self.workbook_count,
            "weights": {
                "tables": WEIGHT_TABLES,
                "fields": WEIGHT_FIELDS,
                "calc_shapes": WEIGHT_SHAPES,
                "spec_ref": "§12.1",
            },
            "read_ms": self.read_ms,
        }


def similarity(
    tables: tuple[set[str], set[str]],
    fields: tuple[set[str], set[str]],
    shapes: tuple[set[str], set[str]],
) -> tuple[float, float, float, int]:
    """§12.1's score for one pair, and its three components.

    ``max_shapes`` is the larger of the two shape sets, not their union: the specification
    divides shared shapes by ``max_calc_shapes``, so a workbook with two shapes that shares
    both with a workbook that has twenty scores 1.0 on nothing — it scores 2/20.
    """
    j_tables = _jaccard(*tables)
    j_fields = _jaccard(*fields)
    shared_shapes = len(shapes[0] & shapes[1])
    largest = max(len(shapes[0]), len(shapes[1]))
    shape_score = shared_shapes / largest if largest else 0.0
    strength = (
        WEIGHT_TABLES * j_tables + WEIGHT_FIELDS * j_fields + WEIGHT_SHAPES * shape_score
    )
    return strength, j_tables, j_fields, shared_shapes


def _jaccard(left: set[str], right: set[str]) -> float:
    if not left and not right:
        return 0.0
    union = len(left | right)
    return len(left & right) / union if union else 0.0


# ------------------------------------------------------------- shared graph-hop plumbing
#
# S3.1.1's Cartographer needs the same three inputs this reader does — what a workbook
# reaches, and the shape of its calculations — over the *whole* estate rather than one
# scope. Rather than a second set of near-identical queries (the drift this codebase has
# been bitten by more than once: two things meant to be the same, quietly not), the hop
# and hydrate queries live here as free functions and both readers call them.


async def children(
    conn: asyncpg.Connection,
    graph: str,
    from_ids: Sequence[str],
    edge: str,
    to_label: str,
) -> dict[str, set[str]]:
    """One hop, grouped by where it started."""
    if not from_ids:
        return {}
    rows = await conn.fetch(
        f"""
        SELECT e.from_id AS parent, e.to_id AS child
        FROM {EDGE_INDEX_TABLE} e
        JOIN {NODE_INDEX_TABLE} n ON n.id = e.to_id AND n.kind = 'node'
         AND n.graph = $1 AND n.label = $3 AND n.retired_at IS NULL
        WHERE e.graph = $1 AND e.label = $2 AND e.from_id = ANY($4::text[])
          AND e.retired_at IS NULL
        """,
        graph,
        edge,
        to_label,
        list(dict.fromkeys(from_ids)),
    )
    out: dict[str, set[str]] = {}
    for row in rows:
        out.setdefault(row["parent"], set()).add(row["child"])
    return out


async def hydrate(
    conn: asyncpg.Connection, graph: str, label: str, ids: Sequence[str]
) -> dict[str, dict[str, Any]]:
    from .graph import queries

    wanted = list(dict.fromkeys(ids))
    if not wanted:
        return {}
    sql = queries.hydrate_nodes(graph, [label])
    rows = await conn.fetch(sql, [queries.agtype_literal(i) for i in wanted])
    out: dict[str, dict[str, Any]] = {}
    for row in rows:
        properties = queries.decode_properties(row["properties"])
        out[str(properties["id"])] = properties
    return out


async def calc_shapes(
    conn: asyncpg.Connection, graph: str, calc_ids: Sequence[str]
) -> dict[str, str]:
    """The AST shape of each calculated field.

    The same normaliser the Pattern Library matches on (S1.3.1), so "these two workbooks
    share a calculation shape" means the same thing everywhere it is asked.
    """
    if not calc_ids:
        return {}
    hydrated = await hydrate(conn, graph, "CalculatedField", calc_ids)
    shapes: dict[str, str] = {}
    for node_id, properties in hydrated.items():
        try:
            shapes[node_id] = ast_shape(properties.get("formula_ast"))
        except SignatureError:
            # A shape that cannot be computed contributes to no similarity, which is true
            # rather than convenient: nothing is known about what it resembles.
            continue
    return shapes


class LineageReader:
    """Reads one scope's lineage graph."""

    def __init__(self, pool: asyncpg.Pool, *, graph_name: str) -> None:
        self._pool = pool
        self._graph = graph_name

    async def read(
        self,
        *,
        site: str | None = None,
        project: str | None = None,
        family: str | None = None,
        workbook_ids: Sequence[str] | None = None,
        min_strength: float = DEFAULT_MIN_STRENGTH,
        limit: int = MAX_WORKBOOKS,
    ) -> LineageGraph:
        import time

        started = time.perf_counter()
        bound = min(limit, MAX_WORKBOOKS)

        async with self._pool.acquire() as conn:
            workbooks = await self._scope(
                conn, site=site, project=project, family=family, ids=workbook_ids
            )
            workbooks, auto_scoped, truncated = _narrow(
                workbooks, bound, asked_for_a_scope=bool(site or project or family or workbook_ids)
            )
            ids = [workbook["id"] for workbook in workbooks]

            sheets = await self._children(conn, ids, "CONTAINS", "Worksheet")
            sheet_ids = sorted({sheet for owned in sheets.values() for sheet in owned})

            datasources = await self._children(conn, sheet_ids, "USES_DATASOURCE", "Datasource")
            connections = await self._children(
                conn, _flatten(datasources), "CONNECTS_TO", "Connection"
            )
            tables = await self._children(conn, _flatten(connections), "CONNECTS_TO", "Table")
            encoded_fields = await self._children(conn, sheet_ids, "ENCODES", "Field")
            encoded_calcs = await self._children(
                conn, sheet_ids, "ENCODES", "CalculatedField"
            )

            names = await self._names(
                conn,
                {
                    "Datasource": _flatten(datasources),
                    "Table": _flatten(tables),
                    "Field": _flatten(encoded_fields),
                    "CalculatedField": _flatten(encoded_calcs),
                },
            )
            shapes = await self._calc_shapes(conn, _flatten(encoded_calcs))
            families = await self._families(conn, ids)
            stored = await self._stored_shared_lineage(conn, ids)

        # Roll the per-worksheet reach up to the workbook that contains the worksheet.
        per_workbook = {
            workbook["id"]: reach(
                sheets.get(workbook["id"], set()),
                datasources,
                connections,
                tables,
                encoded_fields,
                encoded_calcs,
            )
            for workbook in workbooks
        }

        nodes, edges = _elements(workbooks, per_workbook, names)
        shared, origin = _shared_lineage(
            per_workbook, shapes, stored=stored, min_strength=min_strength
        )

        elapsed = (time.perf_counter() - started) * 1000
        logger.info(
            "lineage: %s workbooks, %s shared-lineage links (%s) in %.0f ms",
            len(workbooks),
            len(shared),
            origin,
            elapsed,
        )
        return LineageGraph(
            nodes=nodes,
            edges=edges,
            shared=shared,
            families=families,
            origin=origin,
            truncated=truncated,
            auto_scoped_to=auto_scoped,
            workbook_count=len(workbooks),
            read_ms=round(elapsed, 2),
        )

    # ------------------------------------------------------------------ the queries

    async def _scope(
        self,
        conn: asyncpg.Connection,
        *,
        site: str | None,
        project: str | None,
        family: str | None,
        ids: Sequence[str] | None,
    ) -> list[dict[str, Any]]:
        """The workbooks in scope, with the properties the view draws them with."""
        from .graph import queries

        if ids:
            wanted = list(dict.fromkeys(ids))
        elif family:
            rows = await conn.fetch(
                f"""
                SELECT e.from_id AS id
                FROM {EDGE_INDEX_TABLE} e
                JOIN {NODE_INDEX_TABLE} n ON n.id = e.from_id AND n.graph = $1
                 AND n.label = 'Workbook' AND n.retired_at IS NULL
                WHERE e.graph = $1 AND e.label = 'IN_FAMILY' AND e.to_id = $2
                """,
                self._graph,
                family,
            )
            wanted = [row["id"] for row in rows]
        else:
            rows = await conn.fetch(
                queries.ELEMENTS_OF_LABEL_SQL, self._graph, "Workbook", MAX_WORKBOOKS * 8
            )
            wanted = [row["id"] for row in rows]

        if not wanted:
            return []
        hydrated = await self._hydrate(conn, "Workbook", wanted)

        # Placing each workbook needs its project and site, which is what the site/project
        # filter is applied against — the same two hops the Estate Explorer makes.
        placed = await self._parents(conn, wanted, "Project")
        project_ids = sorted({parent["id"] for parent in placed.values()})
        sites = await self._parents(conn, project_ids, "Site")

        out: list[dict[str, Any]] = []
        for node_id in wanted:
            properties = hydrated.get(node_id)
            if properties is None:
                continue
            parent = placed.get(node_id)
            parent_site = sites.get(parent["id"]) if parent else None
            if site and (parent_site or {}).get("name") != site:
                continue
            if project and (parent or {}).get("name") != project:
                continue
            out.append(
                {
                    "id": node_id,
                    "name": str(properties.get("name", "")),
                    "project": (parent or {}).get("name"),
                    "site": (parent_site or {}).get("name"),
                    "parse_quality": properties.get("parse_quality"),
                    "views_90d": properties.get("views_90d"),
                }
            )
        out.sort(key=lambda workbook: (workbook["name"], workbook["id"]))
        return out

    async def _children(
        self,
        conn: asyncpg.Connection,
        from_ids: Sequence[str],
        edge: str,
        to_label: str,
    ) -> dict[str, set[str]]:
        """One hop, grouped by where it started."""
        return await children(conn, self._graph, from_ids, edge, to_label)

    async def _parents(
        self, conn: asyncpg.Connection, child_ids: Sequence[str], label: str
    ) -> dict[str, dict[str, Any]]:
        if not child_ids:
            return {}
        rows = await conn.fetch(
            f"""
            SELECT e.to_id AS child, e.from_id AS parent
            FROM {EDGE_INDEX_TABLE} e
            JOIN {NODE_INDEX_TABLE} n ON n.id = e.from_id AND n.kind = 'node'
             AND n.graph = $1 AND n.label = $3 AND n.retired_at IS NULL
            WHERE e.graph = $1 AND e.label = 'CONTAINS' AND e.to_id = ANY($2::text[])
            """,
            self._graph,
            list(dict.fromkeys(child_ids)),
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

    async def _names(
        self, conn: asyncpg.Connection, by_label: dict[str, list[str]]
    ) -> dict[str, dict[str, Any]]:
        out: dict[str, dict[str, Any]] = {}
        for label, ids in by_label.items():
            hydrated = await self._hydrate(conn, label, ids)
            for node_id, properties in hydrated.items():
                out[node_id] = {
                    "type": label,
                    "name": str(
                        properties.get("name") or properties.get("luid") or node_id
                    ),
                }
        return out

    async def _calc_shapes(
        self, conn: asyncpg.Connection, calc_ids: Sequence[str]
    ) -> dict[str, str]:
        """The AST shape of each calculated field.

        The same normaliser the Pattern Library matches on (S1.3.1), so "these two
        workbooks share a calculation shape" means here exactly what it means there.
        """
        return await calc_shapes(conn, self._graph, calc_ids)

    async def _families(
        self, conn: asyncpg.Connection, workbook_ids: Sequence[str]
    ) -> list[Family]:
        if not workbook_ids:
            return []
        rows = await conn.fetch(
            f"""
            SELECT e.from_id AS workbook, e.to_id AS family
            FROM {EDGE_INDEX_TABLE} e
            JOIN {NODE_INDEX_TABLE} n ON n.id = e.to_id AND n.kind = 'node'
             AND n.graph = $1 AND n.label = 'ModelFamily' AND n.retired_at IS NULL
            WHERE e.graph = $1 AND e.label = 'IN_FAMILY' AND e.from_id = ANY($2::text[])
            """,
            self._graph,
            list(workbook_ids),
        )
        if not rows:
            return []
        members: dict[str, list[str]] = {}
        for row in rows:
            members.setdefault(row["family"], []).append(row["workbook"])
        hydrated = await self._hydrate(conn, "ModelFamily", list(members))
        return sorted(
            (
                Family(
                    id=family_id,
                    name=str(hydrated.get(family_id, {}).get("name", family_id)),
                    state=hydrated.get(family_id, {}).get("state"),
                    members=sorted(workbooks),
                )
                for family_id, workbooks in members.items()
                if family_id in hydrated
            ),
            key=lambda family: family.name,
        )

    async def _stored_shared_lineage(
        self, conn: asyncpg.Connection, workbook_ids: Sequence[str]
    ) -> dict[tuple[str, str], dict[str, Any]]:
        """``SHARES_LINEAGE`` edges the Cartographer has written, if any."""
        if not workbook_ids:
            return {}
        from .graph import queries

        rows = await conn.fetch(
            f"""
            SELECT e.id, e.from_id, e.to_id
            FROM {EDGE_INDEX_TABLE} e
            WHERE e.graph = $1 AND e.label = 'SHARES_LINEAGE'
              AND e.from_id = ANY($2::text[]) AND e.to_id = ANY($2::text[])
            """,
            self._graph,
            list(workbook_ids),
        )
        if not rows:
            return {}
        sql = queries.hydrate_nodes(self._graph, ["SHARES_LINEAGE"])
        hydrated = await conn.fetch(
            sql, [queries.agtype_literal(row["id"]) for row in rows]
        )
        properties = {
            str(queries.decode_properties(row["properties"])["id"]): queries.decode_properties(
                row["properties"]
            )
            for row in hydrated
        }
        out: dict[tuple[str, str], dict[str, Any]] = {}
        for row in rows:
            found = properties.get(row["id"])
            if found is None:
                continue
            out[_pair(row["from_id"], row["to_id"])] = found
        return out

    async def _hydrate(
        self, conn: asyncpg.Connection, label: str, ids: Sequence[str]
    ) -> dict[str, dict[str, Any]]:
        return await hydrate(conn, self._graph, label, ids)


# --------------------------------------------------------------------------- assembly


@dataclass(frozen=True, slots=True)
class Reach:
    """What one workbook reaches. §12.1's three inputs, per workbook."""

    datasources: set[str]
    tables: set[str]
    fields: set[str]
    calcs: set[str]


def _narrow(
    workbooks: list[dict[str, Any]], bound: int, *, asked_for_a_scope: bool
) -> tuple[list[dict[str, Any]], str | None, bool]:
    """Choose what to show when an unscoped estate is larger than the cap.

    Truncating the sorted list is the obvious thing and it is wrong. The workbooks arrive
    ordered by name, so the first ``bound`` of them is an alphabetical slice: it looks like
    the estate, it is not, and every shared-lineage link that crosses the cut simply is not
    there. A model engineer's first question would be "why is this workbook missing?", which
    is a bad first question for a screen whose whole job is showing evidence.

    So an unscoped read that does not fit narrows to the **largest site** — a complete,
    coherent sub-graph rather than a fragment of several — and says which one it picked, so
    the screen can tell the reader rather than quietly deciding for them.

    A caller who *named* a scope gets the cap applied as before: they asked for something
    specific, and silently substituting a different scope would be worse than truncating.
    """
    if len(workbooks) <= bound:
        return workbooks, None, False

    if asked_for_a_scope:
        return workbooks[:bound], None, True

    by_site: dict[str, list[dict[str, Any]]] = {}
    for workbook in workbooks:
        by_site.setdefault(str(workbook["site"] or ""), []).append(workbook)

    # Largest first, then by name, so the choice is deterministic when two sites tie.
    site, chosen = max(by_site.items(), key=lambda item: (len(item[1]), item[0]))
    return chosen[:bound], site or None, len(chosen) > bound


def _flatten(mapping: dict[str, set[str]]) -> list[str]:
    return sorted({value for values in mapping.values() for value in values})


def reach(
    sheet_ids: set[str],
    datasources: dict[str, set[str]],
    connections: dict[str, set[str]],
    tables: dict[str, set[str]],
    fields: dict[str, set[str]],
    calcs: dict[str, set[str]],
) -> Reach:
    """Roll a workbook's worksheets up into the sets §12.1 scores on."""
    reached_datasources: set[str] = set()
    reached_fields: set[str] = set()
    reached_calcs: set[str] = set()
    for sheet in sheet_ids:
        reached_datasources |= datasources.get(sheet, set())
        reached_fields |= fields.get(sheet, set())
        reached_calcs |= calcs.get(sheet, set())

    reached_tables: set[str] = set()
    for datasource in reached_datasources:
        for connection in connections.get(datasource, set()):
            reached_tables |= tables.get(connection, set())

    return Reach(
        datasources=reached_datasources,
        tables=reached_tables,
        fields=reached_fields,
        calcs=reached_calcs,
    )


def _elements(
    workbooks: list[dict[str, Any]],
    reach: dict[str, Reach],
    names: dict[str, dict[str, Any]],
) -> tuple[list[LineageNode], list[LineageEdge]]:
    """Nodes and structural edges. Workbook → Datasource → Table, and Workbook → Field.

    The worksheets are left out. §15.3.2 asks for "workbooks ↔ tables ↔ fields"; a
    worksheet is how a workbook reaches them, not something a model engineer is grouping
    on, and drawing one node per sheet triples the graph for no added meaning.
    """
    nodes: list[LineageNode] = []
    edges: list[LineageEdge] = []
    seen: set[str] = set()

    for workbook in workbooks:
        nodes.append(
            LineageNode(
                id=workbook["id"],
                type="Workbook",
                name=workbook["name"],
                detail={
                    "site": workbook["site"],
                    "project": workbook["project"],
                    "parse_quality": workbook["parse_quality"],
                    "views_90d": workbook["views_90d"],
                },
            )
        )
        seen.add(workbook["id"])

    for workbook_id, reached in reach.items():
        for kind, members in (
            ("Datasource", reached.datasources),
            ("Table", reached.tables),
            ("Field", reached.fields),
            ("CalculatedField", reached.calcs),
        ):
            for member in sorted(members):
                if member not in seen:
                    described = names.get(member, {"type": kind, "name": member})
                    nodes.append(
                        LineageNode(
                            id=member,
                            type=str(described.get("type", kind)),
                            name=str(described.get("name", member)),
                        )
                    )
                    seen.add(member)
                edges.append(LineageEdge(source=workbook_id, target=member, type="REACHES"))

    nodes.sort(key=lambda node: (NODE_TYPES.index(node.type) if node.type in NODE_TYPES else 9, node.name))
    edges.sort(key=lambda edge: (edge.source, edge.target))
    return nodes, edges


def _shared_lineage(
    reach: dict[str, Reach],
    shapes: dict[str, str],
    *,
    stored: dict[tuple[str, str], dict[str, Any]],
    min_strength: float,
) -> tuple[list[SharedLineage], str]:
    """Every pair worth drawing an edge between, and where the numbers came from."""
    shape_sets = {
        workbook_id: {shapes[calc] for calc in reached.calcs if calc in shapes}
        for workbook_id, reached in reach.items()
    }

    # An inverted index, so pairs that share nothing are never considered. On a real estate
    # most pairs share nothing; on a pathological one the scope limit bounds it.
    #
    # All three of §12.1's inputs are indexed, including the calculation shapes. Indexing
    # only tables and fields looks like an optimisation and is a bug: two workbooks that
    # share every calculation shape and no lineage score 0.2, which is above the default
    # threshold, and the pair would never have been considered. Found by pointing the
    # screen at an estate whose workbooks all define the same ratio.
    candidates: set[tuple[str, str]] = set()
    indexes: list[dict[str, list[str]]] = []
    for attribute in ("tables", "fields"):
        index: dict[str, list[str]] = {}
        for workbook_id, reached in reach.items():
            for member in getattr(reached, attribute):
                index.setdefault(member, []).append(workbook_id)
        indexes.append(index)

    shape_index: dict[str, list[str]] = {}
    for workbook_id, workbook_shapes in shape_sets.items():
        for shape in workbook_shapes:
            shape_index.setdefault(shape, []).append(workbook_id)
    indexes.append(shape_index)

    for index in indexes:
        for holders in index.values():
            if len(holders) < 2:
                continue
            candidates.update(_pair(a, b) for a, b in combinations(sorted(holders), 2))

    links: list[SharedLineage] = []
    used_stored = False
    for left, right in sorted(candidates):
        found = stored.get((left, right))
        if found is not None:
            used_stored = True
            j_tables = float(found.get("jaccard_tables", 0.0))
            j_fields = float(found.get("jaccard_fields", 0.0))
            shared_shapes = int(found.get("shared_calc_count", 0))
            largest = max(len(shape_sets.get(left, ())), len(shape_sets.get(right, ())))
            strength = (
                WEIGHT_TABLES * j_tables
                + WEIGHT_FIELDS * j_fields
                + WEIGHT_SHAPES * (shared_shapes / largest if largest else 0.0)
            )
            origin = "graph"
        else:
            strength, j_tables, j_fields, shared_shapes = similarity(
                (reach[left].tables, reach[right].tables),
                (reach[left].fields, reach[right].fields),
                (shape_sets.get(left, set()), shape_sets.get(right, set())),
            )
            origin = "computed"

        if strength < min_strength:
            continue
        links.append(
            SharedLineage(
                source=left,
                target=right,
                strength=strength,
                jaccard_tables=j_tables,
                jaccard_fields=j_fields,
                shared_shapes=shared_shapes,
                origin=origin,
            )
        )

    links.sort(key=lambda link: (-link.strength, link.source, link.target))
    graph_origin = "graph" if used_stored else "computed"
    return links, graph_origin


def _pair(left: str, right: str) -> tuple[str, str]:
    """SHARES_LINEAGE is symmetric (§4.1.2's note), so a pair has one canonical order."""
    return (left, right) if left <= right else (right, left)


__all__ = [
    "COLOUR_MODES",
    "DEFAULT_MIN_STRENGTH",
    "MAX_WORKBOOKS",
    "NODE_TYPES",
    "WEIGHT_FIELDS",
    "WEIGHT_SHAPES",
    "WEIGHT_TABLES",
    "Family",
    "LineageEdge",
    "LineageGraph",
    "LineageNode",
    "LineageReader",
    "Reach",
    "SharedLineage",
    "calc_shapes",
    "children",
    "hydrate",
    "reach",
    "similarity",
]
