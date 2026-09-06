"""The visual-mapping ruleset -- story S6.1.1.

    "Mapping table from Appendix B (mark type x encodings -> visual type) is data,
    versioned, and editable by the architect."

**The table's own key is the raw Tableau mark type, not a synthetic Appendix B.2 row
label.** Appendix B.2 groups several of Tableau's "Show Me" categories (crosstab, highlight
table, scatter, bubble, KPI/BAN) under one heading, but none of those categories is itself a
mark type the adapter records -- `sheets.py`'s own `_mark_type` reads Tableau's literal
`<mark class="...">` attribute, lowercased ("bar", "line", "circle", ... or "automatic" when
absent). Keying this table on the one field the harvester actually captures, and resolving
Appendix B.2's finer categories (crosstab vs. plain table, plain scatter vs. bubble,
clustered vs. stacked bar, a KPI card) from the *encodings* on top of that base lookup, is
the literal reading of the acceptance criteria's own "mark type x encodings -> visual type"
-- not a two-dimensional data table (which Appendix B.2 itself never provides: it is
described as "an excerpt", §B heading), but a small base table plus a deterministic
refinement function. The refinement lives in `compositor.py`, next to the graph reads it
needs (which shelf entries are measures) that this module has no business knowing about;
this module is the versioned, admin-editable half only -- the identical split
`conformance_rules.py` already draws between `RULES` (code) and a saved `ConformanceRuleset`
(data).

**Same footing as `conformance_ruleset` (S4.3.2, migration v0019), start to finish.** One
`jsonb` column holding the whole table, an architect's save is a new version and the old one
is never overwritten, and a fresh graph builds against an in-memory default (version 0)
until an architect visits Admin. `public.visual_mapping_ruleset` (migration v0024) is the
identical shape.
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any, Protocol

import asyncpg

from .ids import new_ulid

RULESET_TABLE = "public.visual_mapping_ruleset"


@dataclass(frozen=True, slots=True)
class MappingRule:
    """One row: a Tableau mark type, and either a Power BI visual target or a standing
    reason it is flagged for redesign instead (Appendix B.2's own disjunctions -- "Gantt
    (custom visual) or C4", "Custom visual or C4" -- read as "flagged by default", since
    nothing in this platform records a client's own approval to use a custom visual)."""

    mark_type: str
    target_visual_type: str | None = None
    redesign_reason: str | None = None
    notes: str = ""

    def __post_init__(self) -> None:
        if bool(self.target_visual_type) == bool(self.redesign_reason):
            raise ValueError(
                f"mapping rule {self.mark_type!r} must set exactly one of "
                f"target_visual_type or redesign_reason"
            )

    def as_dict(self) -> dict[str, Any]:
        return {
            "mark_type": self.mark_type,
            "target_visual_type": self.target_visual_type,
            "redesign_reason": self.redesign_reason,
            "notes": self.notes,
        }


#: Appendix B.2, transcribed against the mark types the adapter actually records
#: (`sheets.py::_mark_type`) -- see the module docstring for why the key is the mark type
#: alone. Appendix B.2 rows with no mark-type identity at all (bullet, box plot, histogram,
#: KPI/BAN, reference lines, dashboard actions, story points) are not reachable through this
#: table by design; the ones this platform can detect from encodings alone (KPI/BAN;
#: clustered vs. stacked bar) are resolved in `compositor.resolve_visual`, not added here as
#: rows that would never literally match a raw mark type.
DEFAULT_MAPPINGS: tuple[MappingRule, ...] = (
    MappingRule(
        "bar", target_visual_type="clusteredColumnChart",
        notes="Sort and colour legend carried (Appendix B.2). Stacked vs. clustered and "
              "horizontal vs. vertical are resolved from encodings, not this row.",
    ),
    MappingRule(
        "line", target_visual_type="lineChart",
        notes="A second measure on the rows shelf is resolved to a combo chart; "
              "synchronised axes are flagged for review (Appendix B.2: dual axis).",
    ),
    MappingRule(
        "area", target_visual_type="areaChart",
        notes="Same dual-axis handling as line (Appendix B.2).",
    ),
    MappingRule(
        "text", target_visual_type="tableEx",
        notes="A sheet with both a rows and a columns shelf populated resolves to a matrix "
              "instead (Appendix B.2: 'Text table / crosstab -> Matrix or table').",
    ),
    MappingRule(
        "square", target_visual_type="matrix",
        notes="Tableau's Highlight Table idiom (Square marks with a colour encoding); "
              "conditional formatting is not translated automatically (Appendix B.2).",
    ),
    MappingRule(
        "circle", target_visual_type="scatterChart",
        notes="Bubble when a size encoding is present; size and colour carried "
              "(Appendix B.2: 'Scatter, bubble -> Scatter').",
    ),
    MappingRule(
        "map", target_visual_type="map",
        notes="A symbol map; requires a geography role on the bound model column, which "
              "this platform does not assign automatically (Appendix B.2).",
    ),
    MappingRule(
        "polygon", target_visual_type="filledMap",
        notes="A filled map; requires a geography role on the bound model column. An "
              "ArcGIS layer has no Power BI equivalent and is Class 4 (Appendix B.2).",
    ),
    MappingRule(
        "multipolygon", target_visual_type="filledMap",
        notes="Same as a filled (polygon) map (Appendix B.2).",
    ),
    MappingRule(
        "card", target_visual_type="card",
        notes="A single measure with no dimensions, resolved from an 'Automatic' mark's own "
              "shelves (Appendix B.2: 'KPI / BAN -> Card / KPI'). Not itself a Tableau mark "
              "type -- see compositor.resolve_visual's own encoding refinement.",
    ),
    MappingRule(
        "pie", target_visual_type="pieChart",
        notes="Tableau does not distinguish a donut from a plain pie at the mark-type "
              "level; defaults to a plain pie chart (Appendix B.2: 'Treemap, pie, donut').",
    ),
    MappingRule(
        "ganttbar", redesign_reason=(
            "Gantt is a Power BI custom visual and requires client approval before use "
            "(Appendix B.2: 'Gantt (custom visual) or C4 -- flagged for redesign unless "
            "the client approves the custom visual')."
        ),
    ),
    MappingRule(
        "density", redesign_reason=(
            "no Power BI visual renders a density mark; Appendix B.2 names no target."
        ),
    ),
    MappingRule(
        "shape", redesign_reason=(
            "Appendix B.2 has no entry for Tableau's Shape mark; a custom marker shape "
            "has no Power BI equivalent."
        ),
    ),
    MappingRule(
        "automatic", redesign_reason=(
            "Tableau resolves 'Automatic' from the shelves at render time -- this row is "
            "the fallback when that resolution (see compositor.resolve_visual) cannot "
            "determine an effective mark type from the shelves alone."
        ),
    ),
)


@dataclass(frozen=True, slots=True)
class VisualMappingRuleset:
    version: int
    rules: tuple[MappingRule, ...]
    updated_by: str
    updated_at: str | None

    def as_dict(self) -> dict[str, Any]:
        return {
            "version": self.version,
            "rules": [r.as_dict() for r in self.rules],
            "updated_by": self.updated_by,
            "updated_at": self.updated_at,
        }

    def rule_for(self, mark_type: str) -> MappingRule | None:
        normalised = (mark_type or "").strip().lower()
        for rule in self.rules:
            if rule.mark_type == normalised:
                return rule
        return None


_DEFAULT_RULESET = VisualMappingRuleset(
    version=0, rules=DEFAULT_MAPPINGS, updated_by="system", updated_at=None
)


class VisualMappingRulesetStore(Protocol):
    async def latest(self) -> VisualMappingRuleset: ...

    async def save(
        self, rules: Sequence[MappingRule], *, updated_by: str
    ) -> VisualMappingRuleset: ...


class PostgresVisualMappingRulesetStore:
    def __init__(self, pool: asyncpg.Pool, *, graph_name: str) -> None:
        self._pool = pool
        self._graph = graph_name

    async def latest(self) -> VisualMappingRuleset:
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                f"SELECT * FROM {RULESET_TABLE} WHERE graph = $1 ORDER BY version DESC LIMIT 1",
                self._graph,
            )
        return _from_row(row) if row else _DEFAULT_RULESET

    async def save(
        self, rules: Sequence[MappingRule], *, updated_by: str
    ) -> VisualMappingRuleset:
        async with self._pool.acquire() as conn:
            async with conn.transaction():
                current = await conn.fetchval(
                    f"SELECT MAX(version) FROM {RULESET_TABLE} WHERE graph = $1", self._graph,
                )
                version = (current or 0) + 1
                row = await conn.fetchrow(
                    f"""
                    INSERT INTO {RULESET_TABLE} (id, graph, version, mappings, updated_by, updated_at)
                    VALUES ($1, $2, $3, $4::jsonb, $5, now())
                 RETURNING *
                    """,
                    f"visualmap_{new_ulid()}",
                    self._graph,
                    version,
                    json.dumps([r.as_dict() for r in rules]),
                    updated_by,
                )
        assert row is not None
        return _from_row(row)


def _from_row(row: asyncpg.Record) -> VisualMappingRuleset:
    raw = row["mappings"]
    rows = json.loads(raw) if isinstance(raw, str) else list(raw)
    updated_at = row["updated_at"]
    return VisualMappingRuleset(
        version=row["version"],
        rules=tuple(MappingRule(**r) for r in rows),
        updated_by=row["updated_by"],
        updated_at=updated_at.isoformat() if updated_at else None,
    )


__all__ = [
    "DEFAULT_MAPPINGS",
    "RULESET_TABLE",
    "MappingRule",
    "PostgresVisualMappingRulesetStore",
    "VisualMappingRuleset",
    "VisualMappingRulesetStore",
]
