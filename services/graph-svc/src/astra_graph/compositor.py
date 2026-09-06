"""The Compositor -- E6, stories S6.1.1/S6.1.2/S6.1.3, spec §8.8/§7.1/Appendix B.

    "As a migration engineer, I want each Tableau sheet mapped to a Power BI visual type
    with encodings and filters translated, so that the generated report is structurally
    the same report."

§8.8: "Maps each Worksheet to a Visual using the visual-type mapping in Appendix B, binds
encodings to model columns and measures through MAPS_TO edges, translates filters and
parameters to slicers and report-level filters, ... lays out dashboards from the Tableau
zone tree into PBIR page layouts... Visuals with no mapping ... receive a redesign flag and
a placeholder card so the report still generates and proves for its other visuals."

**Parameters, actions and interactivity (S6.1.3) are resolved per worksheet, alongside
field wells.** A worksheet's own parameters are found through the same calculated-field
wells S6.1.1 already resolves (`DEPENDS_ON` from a referenced `CalculatedField` to a
`Parameter`) -- the only real path this platform has from a worksheet to a parameter, since
`Parameter`/`Action` carry no edge back to their own `Workbook` at all (§4.1.2 declares
none). `Action`s are found by a name match against every worksheet's own source sheet (the
identical trust `Dashboard.contained_sheets` already places in name strings, not ids) --
see `Visual.interactivity`'s own `SpecDeviation` for the real, disclosed limitation this
carries. Committing/deploying to a workspace is S6.1.2's. Layout-collision resolution
(§8.8's own "small model proposing a grid", ASSISTED) has no collision to resolve yet --
this story places one visual per dashboard zone, unchanged from where the zone tree already
put it.

**One Migration Unit per Workbook** (`Workbook`'s own §4.1.1 note) settles what a
`ReportDefinition.mu_ref` names: no real Migration Unit record exists anywhere in this
codebase (confirmed identically by S5.4.1/S5.5.1/S5.5.2/S5.5.3's own research, a fourth
time) -- the workbook id is not a proxy standing in for something else here, it is the
literal, spec-declared MU identity.

**A report binds to a family's current design, not a fixed one.** `modeller.
read_design_document` (S4.1.1/S4.3.3) already resolves "the" `SemanticModel` for a family
as its highest live `version_number` -- reused directly, so a report always binds to
whatever a Semantic Model Engineer has most recently designed, the same "current means
latest" rule every other consumer of a family's design already follows.

**Fields are resolved by name against the worksheet's own datasources, not `ENCODES`
edges.** §4.1.2 declares `ENCODES` (Worksheet -> Field/CalculatedField, with a `shelf`
property); the adapter has never written it (S3.1.1's own finding, still true --
`cartographer.py`'s own docstring). This goes one step further than the Cartographer's own
workaround (which only ever compares shelf *name strings*, never resolves them to a node):
a field well needs a real Field or CalculatedField id to walk `MAPS_TO` from, so
`_worksheet_field_index` resolves each shelf name against the worksheet's
`USES_DATASOURCE -> HAS_FIELD` reach and takes the first match on an exact name.

**"Field wells bound to model columns and measures (through MAPS_TO)" is half-real, and
says so.** `CalculatedField -> Measure` MAPS_TO edges are real and populated by the
Transpiler the moment a field classifies C1/C2 (S5.1.1 onward) -- a calculated-field well
resolves against them for real. `Field -> ModelTable` MAPS_TO edges have never been written
by any story in this codebase (`generation.py`'s own disclosed finding, confirmed unchanged
by this story's own research) -- a plain-field well's `bound` is honestly `False` today,
with the real reason recorded, rather than guessed at by matching names against
`ModelTable`. `_resolve_bindings` queries for the edge for real either way, so the moment a
future story starts writing `Field -> ModelTable`, this code finds it with no change.

**Appendix B.2's own categories are recovered from encodings, not hand-added as more
table rows.** See `visual_mapping.py`'s own module docstring for why the mapping table is
keyed on the raw Tableau mark type alone; `resolve_visual` (below) is where a crosstab,
a highlight table's conditional formatting, a bubble, a dual axis, a stacked/clustered/
horizontal bar and a KPI card are told apart, using the resolved field wells' own roles.

**A re-run replaces the workbook's whole report**, the identical starting posture the
Modeller (S4.1.1) and Cartographer (S3.1.1) each had before their own override stories --
no "engineer has edited this" pin exists yet for a `ReportDefinition`, so composing again
retires every previous `Visual`/`ReportDefinition` this workbook produced and writes fresh
ones.

**PBIR schema validation runs before anything is written**, mirroring `build.py`'s own
"conformance runs before commit, not after" pipeline order -- a schema failure (a real
defect in this Compositor's own emission, never expected to fire) refuses the whole compose
rather than writing something invalid; an unresolved field binding is a warning carried in
the response instead, since today it is a known, disclosed platform gap and not a defect in
what this story built (see `pbir.validate_pbir`'s own docstring).

**A redesign-flagged visual also opens a real work item (S6.2.1).** Every placement whose
`resolve_visual` result is flagged writes a real `ExceptionCase(class=VISUAL_REDESIGN)`
alongside its placeholder `Visual` -- see `visual_redesign.py`'s own module docstring for
what "routed to the Exception Desk" honestly means today, and why the evidence it carries
is a snapshot, not a live read. A recompose retires an open case the same way it retires
the `Visual` it concerns, never silently orphaning one against a node that no longer exists.

**A composed report can also have documentation generated for it, on request (S6.2.2).**
`report_documentation.py`'s own module docstring covers what the generated page says and
why; `Compositor.generate_documentation`/`.read_documentation` are thin wrappers the same
shape as `.compose`/`.read` already are, which is why `Compositor` now also carries an
optional `provenance_store` -- the one dependency this story needed that no earlier E6
story did.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

import asyncpg

from .artefacts import ArtefactStore
from .errors import ElementNotFoundError
from .graph.queries import EDGE_INDEX_TABLE, NODE_INDEX_TABLE
from .ids import new_ulid
from .lineage import children, hydrate
from .modeller import read_design_document
from .pbir import emit_pbir, validate_pbir
from .principal import Principal
from .provenance import ProvenanceStore
from .report_documentation import generate_report_documentation, read_report_documentation
from .visual_mapping import VisualMappingRuleset
from .visual_redesign import (
    find_screenshot_ref,
    open_redesign_exception,
    retire_exceptions_for_visuals,
)
from .writes import EdgeWrite, GraphWriter, NodeWrite

logger = logging.getLogger(__name__)

_AUTOMATIC = "automatic"

#: Marks-shelf channels PBI has a direct field-well analogue for; anything else (shape,
#: detail, label, path -- Tableau encoding channels with no clean Power BI equivalent) is
#: carried through as "detail", an honest generic bucket rather than a guess.
_CHANNEL_ROLE = {"color": "legend", "size": "size", "tooltip": "tooltip"}


class CompositorError(Exception):
    """A workbook cannot be composed into a report right now -- no family, no design, or a
    PBIR document this Compositor itself produced failing its own schema (a real defect,
    never expected to fire)."""


# --------------------------------------------------------------------------- pure functions


@dataclass(frozen=True, slots=True)
class ResolvedWell:
    """One shelf/channel entry, resolved to a real source node and its binding."""

    shelf: str
    role: str
    source_kind: str
    source_id: str | None
    source_name: str
    bound: bool
    table: str | None
    column: str | None
    measure_id: str | None
    reason: str | None

    def as_dict(self) -> dict[str, Any]:
        return {
            "shelf": self.shelf,
            "role": self.role,
            "sourceKind": self.source_kind,
            "sourceId": self.source_id,
            "sourceName": self.source_name,
            "bound": self.bound,
            "table": self.table,
            "column": self.column,
            "measureId": self.measure_id,
            "reason": self.reason,
        }


@dataclass(frozen=True, slots=True)
class VisualResolution:
    visual_type: str
    redesign_flag: bool
    redesign_reason: str | None
    notes: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class ParameterMapping:
    """A Tableau ``Parameter`` this visual's own field wells depend on, classified into a
    Power BI construct by ``domain`` (story S6.1.3)."""

    name: str
    datatype: str
    domain: str
    kind: str | None
    """``"what_if"`` or ``"slicer"``; ``None`` when unsupported."""
    supported: bool
    values: tuple[str, ...]
    default: Any
    reason: str | None
    """Populated when unsupported, or as a disclosed caveat on an otherwise-supported kind."""

    def as_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "datatype": self.datatype,
            "domain": self.domain,
            "kind": self.kind,
            "supported": self.supported,
            "values": list(self.values),
            "default": self.default,
            "reason": self.reason,
        }


@dataclass(frozen=True, slots=True)
class ActionMapping:
    """One live ``Action`` naming this visual's own source sheet, classified into a Power
    BI interactivity setting by ``type`` (story S6.1.3). No ``name`` field: §4.1.1 declares
    none on ``Action`` at all (only ``type``/``source_sheets``/``target_sheets``) -- the
    type plus which other sheet it connects to is the only real, identifying data this
    platform has ever harvested for one."""

    type: str
    role: str
    """``"source"`` or ``"target"`` -- which side of the action this visual plays."""
    other_sheets: tuple[str, ...]
    """The sheet(s) on the opposite side of this action from this visual's own sheet."""
    power_bi_setting: str | None
    supported: bool
    reason: str | None

    def as_dict(self) -> dict[str, Any]:
        return {
            "type": self.type,
            "role": self.role,
            "otherSheets": list(self.other_sheets),
            "powerBiSetting": self.power_bi_setting,
            "supported": self.supported,
            "reason": self.reason,
        }


#: Appendix B.2's own literal row: "Dashboard actions (filter, highlight, URL) ->
#: Cross-filter/highlight settings, drill-through, URL via conditional formatting;
#: Parameter and set actions -> C3 or C4." Filter and highlight share one outcome in the
#: appendix (a cross-filter/highlight setting); the backlog's own S6.1.3 AC text names only
#: filter and URL explicitly, but does not carve highlight out either -- treated identically
#: to filter here, the fuller, spec-consistent reading, disclosed as such (see the module's
#: own ADR).
_SUPPORTED_ACTION_SETTINGS = {"filter": "crossFilter", "highlight": "highlight", "url": "url"}

_UNSUPPORTED_ACTION_REASON = (
    "Appendix B.2: 'Parameter and set actions -> C3 or C4' -- not translated directly; "
    "redesign as a bookmark/button-driven navigation, or treat the underlying "
    "calculation as its own C3/C4 case if it changes what is computed, not just what is "
    "shown."
)

#: Tableau's own "any" domain (unconstrained: no min/max, no fixed member list) has no
#: bounded Power BI equivalent -- a what-if parameter needs a start/end/increment, and a
#: slicer needs a set of values, neither of which this domain provides.
_UNBOUNDED_PARAMETER_REASON = (
    "Tableau's 'any' domain (unconstrained) has no bounded Power BI equivalent -- neither "
    "a range (no min/max) nor a fixed list (no members) can be derived from it."
)

#: Appendix B.1's own "Parameters" row: "What-if parameter tables" — real Power BI what-if
#: parameters are themselves a bounded range (start/end/increment); this platform's own
#: harvested `Parameter` never captures those bounds (`sheets.py`'s own dataclass has only
#: `default`/`values`, no min/max/step), so a range parameter is classified correctly but
#: discloses the gap rather than inventing bounds nobody supplied.
_UNBOUNDED_RANGE_REASON = (
    "range bounds (start/end/increment) are not captured by the harvester -- Parameter "
    "carries only a default and any observed values, not a real min/max/step -- so the "
    "what-if parameter table itself must still be configured by hand in Power BI Desktop."
)


def classify_parameter(parameter: Mapping[str, Any]) -> ParameterMapping:
    domain = str(parameter.get("domain") or "any")
    name = str(parameter.get("name") or "")
    datatype = str(parameter.get("datatype") or "")
    values = tuple(parameter.get("current_values_seen") or ())
    default = parameter.get("default")
    if domain == "list":
        return ParameterMapping(name, datatype, domain, "slicer", True, values, default, None)
    if domain == "range":
        return ParameterMapping(
            name, datatype, domain, "what_if", True, values, default, _UNBOUNDED_RANGE_REASON
        )
    return ParameterMapping(
        name, datatype, domain, None, False, values, default, _UNBOUNDED_PARAMETER_REASON
    )


def classify_action(action_type: str, *, role: str, other_sheets: Sequence[str]) -> ActionMapping:
    setting = _SUPPORTED_ACTION_SETTINGS.get(action_type)
    other = tuple(other_sheets)
    if setting is not None:
        return ActionMapping(action_type, role, other, setting, True, None)
    return ActionMapping(action_type, role, other, None, False, _UNSUPPORTED_ACTION_REASON)


def _shelf_role(shelf: str, *, is_measure: bool) -> str:
    if shelf in ("rows", "cols"):
        return "values" if is_measure else "axis"
    return _CHANNEL_ROLE.get(shelf, "detail")


def _is_measure_well(well: ResolvedWell) -> bool:
    return well.role == "values"


def _effective_mark(wells: Sequence[ResolvedWell]) -> str:
    """Tableau's own choice, made the same way -- `sheets.py`'s own comment on
    `_mark_type`: "Tableau chooses from the shelves, and the Compositor will have to make
    the same choice." Only ever called for mark type ``automatic`` (or absent)."""
    on_axes = [w for w in wells if w.shelf in ("rows", "cols")]
    dimensions = sum(1 for w in on_axes if not _is_measure_well(w))
    measures = sum(1 for w in on_axes if _is_measure_well(w))
    if measures == 1 and dimensions == 0:
        return "card"
    if dimensions >= 1 and measures >= 1:
        return "bar"
    if measures >= 2 and dimensions == 0:
        return "circle"
    if dimensions >= 1 and measures == 0:
        return "text"
    return _AUTOMATIC


def resolve_visual(
    mark_type: str, wells: Sequence[ResolvedWell], ruleset: VisualMappingRuleset
) -> VisualResolution:
    """Appendix B.2's own mark-type table, refined by the resolved encodings -- AC #1's own
    "mark type x encodings -> visual type", and AC #3's placeholder-with-reason fallback."""
    normalised = (mark_type or "").strip().lower()
    lookup_key = normalised
    notes: list[str] = []

    if normalised in ("", _AUTOMATIC):
        resolved = _effective_mark(wells)
        if resolved != _AUTOMATIC:
            notes.append(
                f"Tableau mark type {mark_type or 'automatic'!r} resolved to "
                f"{resolved!r} from the shelves."
            )
            lookup_key = resolved

    rule = ruleset.rule_for(lookup_key)
    if rule is None:
        described = f"{mark_type or '(none)'!r}" if lookup_key == normalised else (
            f"{mark_type or 'automatic'!r} (resolved to {lookup_key!r})"
        )
        return VisualResolution(
            visual_type="placeholder",
            redesign_flag=True,
            redesign_reason=f"no mapping rule for Tableau mark type {described}",
            notes=tuple(notes),
        )
    if rule.redesign_reason:
        return VisualResolution(
            visual_type="placeholder",
            redesign_flag=True,
            redesign_reason=rule.redesign_reason,
            notes=tuple(notes),
        )

    assert rule.target_visual_type is not None
    visual_type = rule.target_visual_type

    if visual_type == "clusteredColumnChart":
        colour_present = any(w.shelf == "color" for w in wells)
        row_measure = any(w.shelf == "rows" and _is_measure_well(w) for w in wells)
        col_measure = any(w.shelf == "cols" and _is_measure_well(w) for w in wells)
        # Columns lay out horizontally, rows vertically -- a Tableau vertical (column)
        # bar has its dimension on cols and its measure on rows (height); swapping which
        # shelf holds the measure is exactly how a horizontal bar is built in Tableau.
        horizontal = col_measure and not row_measure
        if colour_present:
            visual_type = "stackedBarChart" if horizontal else "stackedColumnChart"
            notes.append("a colour encoding is present -- resolved to a stacked chart.")
        elif horizontal:
            visual_type = "clusteredBarChart"
            notes.append(
                "the measure sits on the columns shelf -- resolved to a horizontal bar chart."
            )
    elif visual_type in ("lineChart", "areaChart"):
        row_measures = sum(1 for w in wells if w.shelf == "rows" and _is_measure_well(w))
        if row_measures >= 2:
            if visual_type == "lineChart":
                visual_type = "lineStackedColumnComboChart"
            notes.append(
                "two or more measures on the rows shelf -- resolved to a dual-axis combo; "
                "synchronised axes are flagged for review (Appendix B.2)."
            )
    elif visual_type == "tableEx":
        if any(w.shelf == "rows" for w in wells) and any(w.shelf == "cols" for w in wells):
            visual_type = "matrix"
            notes.append(
                "both a rows and a columns shelf are populated -- resolved to a matrix "
                "(crosstab)."
            )
    elif visual_type == "scatterChart" and any(w.shelf == "size" for w in wells):
        notes.append("a size encoding is present -- this is a bubble chart.")

    if rule.notes:
        notes.append(rule.notes)

    return VisualResolution(
        visual_type=visual_type, redesign_flag=False, redesign_reason=None, notes=tuple(notes)
    )


def _zone_list(layout_json: Any) -> list[Any]:
    """``Dashboard.layout_json`` normalised to a bare list of zones.

    The real Tableau adapter writes a bare list (`sheets.py`'s own `Dashboard.
    as_properties`: ``"layout_json": [zone.as_dict() for zone in self.zones]``); the
    fixture source adapter writes ``{"zones": [{"sheet": ref}, ...]}`` instead --
    discovered live, by this story's own smoke test, composing a real harvested workbook
    (found: task flagged to bring the fixture's own shape in line with the real one, since
    its own zone entries carry no geometry at all — this function only stops the mismatch
    from crashing a compose; it cannot recover geometry the fixture never recorded)."""
    if isinstance(layout_json, list):
        return layout_json
    if isinstance(layout_json, dict):
        nested = layout_json.get("zones")
        if isinstance(nested, list):
            return nested
    return []


def _find_zone(zones: Sequence[Any], sheet_name: str) -> Mapping[str, Any] | None:
    for zone in zones:
        if not isinstance(zone, dict):
            continue
        if zone.get("type") == "worksheet" and zone.get("name") == sheet_name:
            return zone
        found = _find_zone(_zone_list(zone.get("children")), sheet_name)
        if found is not None:
            return found
    return None


def _zone_layout(zone: Mapping[str, Any] | None) -> dict[str, Any] | None:
    if zone is None:
        return None
    return {
        "x": zone.get("x", 0),
        "y": zone.get("y", 0),
        "width": zone.get("w", 0),
        "height": zone.get("h", 0),
    }


# ----------------------------------------------------------------------------- graph reads


async def _maps_to(
    conn: asyncpg.Connection, graph: str, from_ids: Sequence[str], to_label: str
) -> dict[str, list[dict[str, Any]]]:
    """Every live ``MAPS_TO`` edge out of ``from_ids`` whose target is ``to_label``, with
    the edge's own properties -- `target_column` (§4.1.1: "the column is carried on the
    edge because columns are not nodes in this ontology") lives nowhere else."""
    if not from_ids:
        return {}
    rows = await conn.fetch(
        f"""
        SELECT e.id AS edge_id, e.from_id AS parent, e.to_id AS target
          FROM {EDGE_INDEX_TABLE} e
          JOIN {NODE_INDEX_TABLE} n ON n.graph = e.graph AND n.id = e.to_id
             AND n.kind = 'node' AND n.label = $3 AND n.retired_at IS NULL
         WHERE e.graph = $1 AND e.label = 'MAPS_TO' AND e.from_id = ANY($2::text[])
           AND e.retired_at IS NULL
        """,
        graph,
        list(dict.fromkeys(from_ids)),
        to_label,
    )
    if not rows:
        return {}
    edge_properties = await hydrate(conn, graph, "MAPS_TO", [row["edge_id"] for row in rows])
    out: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        props = edge_properties.get(row["edge_id"], {})
        out.setdefault(row["parent"], []).append({"to_id": row["target"], **props})
    return out


async def _worksheet_field_index(
    conn: asyncpg.Connection, graph: str, worksheet_id: str
) -> dict[str, tuple[str, str, Mapping[str, Any]]]:
    """Field/CalculatedField name -> (kind, id, properties), resolved against the
    worksheet's own datasources. On a name collision between a Field and a CalculatedField
    (rare), the Field wins -- an arbitrary but disclosed tie-break, not a silent one."""
    datasource_map = await children(conn, graph, [worksheet_id], "USES_DATASOURCE", "Datasource")
    datasource_ids = sorted(datasource_map.get(worksheet_id, set()))
    if not datasource_ids:
        return {}
    field_map = await children(conn, graph, datasource_ids, "HAS_FIELD", "Field")
    calc_map = await children(conn, graph, datasource_ids, "HAS_FIELD", "CalculatedField")
    field_ids = sorted({f for ids in field_map.values() for f in ids})
    calc_ids = sorted({c for ids in calc_map.values() for c in ids})
    fields = await hydrate(conn, graph, "Field", field_ids)
    calcs = await hydrate(conn, graph, "CalculatedField", calc_ids)

    index: dict[str, tuple[str, str, Mapping[str, Any]]] = {}
    for calc_id, properties in calcs.items():
        name = str(properties.get("name") or "")
        if name:
            index[name] = ("CalculatedField", calc_id, properties)
    for field_id, properties in fields.items():
        name = str(properties.get("name") or "")
        if name:
            index[name] = ("Field", field_id, properties)
    return index


async def _resolve_bindings(conn: asyncpg.Connection, graph: str, kind: str, source_id: str) -> dict[str, Any]:
    if kind == "Field":
        matches = (await _maps_to(conn, graph, [source_id], "ModelTable")).get(source_id) or []
        if not matches:
            return {
                "bound": False, "table": None, "column": None, "measure_id": None,
                "reason": "no Field->ModelTable MAPS_TO edge exists for this field yet",
            }
        target = matches[0]
        return {
            "bound": True, "table": target.get("to_id"), "column": target.get("target_column"),
            "measure_id": None, "reason": None,
        }

    matches = (await _maps_to(conn, graph, [source_id], "Measure")).get(source_id) or []
    if not matches:
        return {
            "bound": False, "table": None, "column": None, "measure_id": None,
            "reason": "not yet transpiled to a Measure",
        }
    target = matches[0]
    return {
        "bound": True, "table": None, "column": None, "measure_id": target.get("to_id"),
        "reason": None,
    }


async def _resolve_one_well(
    conn: asyncpg.Connection,
    graph: str,
    shelf: str,
    name: str,
    index: Mapping[str, tuple[str, str, Mapping[str, Any]]],
) -> ResolvedWell:
    found = index.get(name)
    if found is None:
        return ResolvedWell(
            shelf=shelf, role=_shelf_role(shelf, is_measure=False), source_kind="Field",
            source_id=None, source_name=name, bound=False, table=None, column=None,
            measure_id=None,
            reason=f"{name!r} does not resolve to a Field or CalculatedField in this "
                   f"worksheet's own datasources",
        )
    kind, source_id, properties = found
    is_measure = kind == "CalculatedField" or properties.get("role") == "measure"
    binding = await _resolve_bindings(conn, graph, kind, source_id)
    return ResolvedWell(
        shelf=shelf, role=_shelf_role(shelf, is_measure=is_measure), source_kind=kind,
        source_id=source_id, source_name=name, **binding,
    )


async def _resolve_worksheet_wells(
    conn: asyncpg.Connection, graph: str, worksheet_id: str, properties: Mapping[str, Any]
) -> list[ResolvedWell]:
    index = await _worksheet_field_index(conn, graph, worksheet_id)
    wells: list[ResolvedWell] = []
    for name in properties.get("rows_shelf") or ():
        if name:
            wells.append(await _resolve_one_well(conn, graph, "rows", str(name), index))
    for name in properties.get("cols_shelf") or ():
        if name:
            wells.append(await _resolve_one_well(conn, graph, "cols", str(name), index))
    for entry in properties.get("marks_shelf") or ():
        channel, _, name = str(entry).partition(":")
        if name:
            wells.append(await _resolve_one_well(conn, graph, channel or "detail", name, index))
    return wells


async def _worksheet_parameters(
    conn: asyncpg.Connection, graph: str, wells: Sequence[ResolvedWell]
) -> list[ParameterMapping]:
    """Every ``Parameter`` a calculated-field well on this worksheet depends on
    (``DEPENDS_ON``), classified by domain. The only real path from a worksheet to a
    parameter this platform has -- see `Visual.interactivity`'s own `SpecDeviation` for why
    a parameter reachable only through a filter or a native Tableau action, never through a
    calculation, is a disclosed gap this function cannot close."""
    calc_ids = sorted({w.source_id for w in wells if w.source_kind == "CalculatedField" and w.source_id})
    if not calc_ids:
        return []
    parameter_map = await children(conn, graph, calc_ids, "DEPENDS_ON", "Parameter")
    parameter_ids = sorted({p for owned in parameter_map.values() for p in owned})
    if not parameter_ids:
        return []
    parameters = await hydrate(conn, graph, "Parameter", parameter_ids)
    return [classify_parameter(properties) for properties in parameters.values()]


async def _gather_workbook_actions(
    conn: asyncpg.Connection, graph: str, worksheet_names: Sequence[str]
) -> list[dict[str, Any]]:
    """Every live ``Action`` naming at least one of this workbook's own worksheets.

    §4.1.2 gives `Action` no containing edge back to its own `Workbook` at all (only
    `source_sheets`/`target_sheets` name strings, matching `Dashboard.contained_sheets`'s
    own convention) -- so this is a scan of every live `Action` in the graph, filtered by
    name. A real, disclosed limitation, not a new one this story introduces: two different
    workbooks whose worksheets happen to share a name could, in principle, cross-attribute
    an action neither one actually has. Harmless at this platform's own current scale, and
    the identical name-matching trust `Dashboard.contained_sheets` already carries.
    """
    wanted = set(worksheet_names)
    if not wanted:
        return []
    rows = await conn.fetch(
        f"""SELECT id FROM {NODE_INDEX_TABLE}
         WHERE graph = $1 AND kind = 'node' AND label = 'Action' AND retired_at IS NULL""",
        graph,
    )
    actions = await hydrate(conn, graph, "Action", [row["id"] for row in rows])
    return [
        properties
        for properties in actions.values()
        if wanted & set(properties.get("source_sheets") or ())
        or wanted & set(properties.get("target_sheets") or ())
    ]


def _worksheet_action_mappings(
    actions: Sequence[Mapping[str, Any]], worksheet_name: str
) -> list[ActionMapping]:
    mappings: list[ActionMapping] = []
    for action in actions:
        action_type = str(action.get("type") or "")
        source_sheets = tuple(action.get("source_sheets") or ())
        target_sheets = tuple(action.get("target_sheets") or ())
        if worksheet_name in source_sheets:
            mappings.append(classify_action(action_type, role="source", other_sheets=target_sheets))
        if worksheet_name in target_sheets:
            mappings.append(classify_action(action_type, role="target", other_sheets=source_sheets))
    return mappings


async def _report_and_visual_ids(
    conn: asyncpg.Connection, graph_name: str, workbook_id: str
) -> tuple[list[str], list[str]]:
    """A workbook's own live ``ReportDefinition`` id(s) and every ``Visual`` it produced.

    There is no edge from a ``ReportDefinition`` to its own ``Visual``s -- §4.1.2 declares
    ``MAPS_TO`` only as ``Worksheet -> Visual`` -- so a report's visuals are found the same
    way a fresh compose finds a workbook's worksheets first: ``Workbook -> CONTAINS ->
    Worksheet -> MAPS_TO -> Visual``.
    """
    rows = await conn.fetch(
        f"""SELECT id FROM {NODE_INDEX_TABLE}
         WHERE graph = $1 AND kind = 'node' AND label = 'ReportDefinition' AND retired_at IS NULL""",
        graph_name,
    )
    reports = await hydrate(conn, graph_name, "ReportDefinition", [row["id"] for row in rows])
    report_ids = [rid for rid, props in reports.items() if props.get("mu_ref") == workbook_id]

    worksheet_map = await children(conn, graph_name, [workbook_id], "CONTAINS", "Worksheet")
    worksheet_ids = sorted(worksheet_map.get(workbook_id, set()))
    visual_map = await children(conn, graph_name, worksheet_ids, "MAPS_TO", "Visual")
    visual_ids = sorted({v for owned in visual_map.values() for v in owned})
    return report_ids, visual_ids


async def _retire_previous_report(
    writer: GraphWriter, pool: asyncpg.Pool, graph_name: str, workbook_id: str, *, principal: Principal
) -> None:
    async with pool.acquire() as conn:
        report_ids, visual_ids = await _report_and_visual_ids(conn, graph_name, workbook_id)

    # Before the Visuals themselves -- an open ExceptionCase must never outlive the Visual
    # it concerns (S6.2.1's own retirement cascade, patterns.py's precedent).
    await retire_exceptions_for_visuals(writer, pool, graph_name, visual_ids, principal=principal)

    for report_id in report_ids:
        await writer.retire_node(
            report_id, reason="superseded by a fresh compose of this workbook", principal=principal
        )
    for visual_id in visual_ids:
        await writer.retire_node(
            visual_id, reason="superseded by a fresh compose of this workbook", principal=principal
        )


# --------------------------------------------------------------------------- orchestration


async def compose_report(
    pool: asyncpg.Pool,
    graph_name: str,
    writer: GraphWriter,
    *,
    workbook_id: str,
    ruleset: VisualMappingRuleset,
    principal: Principal,
    artefact_store: ArtefactStore | None = None,
) -> dict[str, Any]:
    async with pool.acquire() as conn:
        workbook = (await hydrate(conn, graph_name, "Workbook", [workbook_id])).get(workbook_id)
        if workbook is None:
            raise ElementNotFoundError(f"no Workbook '{workbook_id}'")

        family_map = await children(conn, graph_name, [workbook_id], "IN_FAMILY", "ModelFamily")
        family_ids = sorted(family_map.get(workbook_id, set()))
    if not family_ids:
        raise CompositorError(
            f"workbook '{workbook_id}' is not a member of any ModelFamily yet -- the "
            f"Cartographer (E3) groups workbooks into families before a report can bind "
            f"to a model"
        )
    family_id = family_ids[0]

    try:
        design = await read_design_document(pool, graph_name, family_id)
    except ElementNotFoundError as exc:
        raise CompositorError(
            f"no design proposal has been generated for family '{family_id}' yet -- the "
            f"Modeller (S4.1.1) must run before a report can bind to a model"
        ) from exc
    semantic_model_id = design["semantic_model_id"]

    async with pool.acquire() as conn:
        dashboard_map = await children(conn, graph_name, [workbook_id], "CONTAINS", "Dashboard")
        worksheet_map = await children(conn, graph_name, [workbook_id], "CONTAINS", "Worksheet")
        dashboards = await hydrate(conn, graph_name, "Dashboard", sorted(dashboard_map.get(workbook_id, set())))
        worksheets = await hydrate(conn, graph_name, "Worksheet", sorted(worksheet_map.get(workbook_id, set())))

    worksheets_by_name = {props.get("name"): (wid, props) for wid, props in worksheets.items()}

    placements: list[tuple[str, str, dict[str, Any], dict[str, Any] | None]] = []
    contained_names: set[str] = set()
    for dashboard_id, dprops in dashboards.items():
        page_id = str(dprops.get("name") or dashboard_id)
        zones = _zone_list(dprops.get("layout_json"))
        for sheet_name in dprops.get("contained_sheets") or ():
            contained_names.add(sheet_name)
            found = worksheets_by_name.get(sheet_name)
            if found is None:
                # A dashboard names a sheet the harvester never wrote as its own Worksheet
                # node -- a real, if unusual, source-side inconsistency; disclosed by
                # omission (nothing fabricated in its place) rather than raised, since the
                # rest of this workbook's report can still compose correctly.
                continue
            worksheet_id, worksheet_properties = found
            placements.append(
                (page_id, worksheet_id, worksheet_properties, _zone_layout(_find_zone(zones, sheet_name)))
            )

    for worksheet_id, worksheet_properties in worksheets.items():
        name = worksheet_properties.get("name")
        if name not in contained_names:
            placements.append((str(name), worksheet_id, worksheet_properties, None))

    wells_by_worksheet: dict[str, list[ResolvedWell]] = {}
    parameters_by_worksheet: dict[str, list[ParameterMapping]] = {}
    async with pool.acquire() as conn:
        for _, worksheet_id, worksheet_properties, _ in placements:
            if worksheet_id not in wells_by_worksheet:
                wells_by_worksheet[worksheet_id] = await _resolve_worksheet_wells(
                    conn, graph_name, worksheet_id, worksheet_properties
                )
                parameters_by_worksheet[worksheet_id] = await _worksheet_parameters(
                    conn, graph_name, wells_by_worksheet[worksheet_id]
                )
        workbook_actions = await _gather_workbook_actions(
            conn, graph_name, [str(props.get("name")) for _, _, props, _ in placements]
        )

    pages_seen: list[str] = []
    visual_writes: list[NodeWrite] = []
    edge_writes: list[EdgeWrite] = []
    visual_records: list[dict[str, Any]] = []

    pending_exceptions: list[tuple[str, dict[str, Any]]] = []
    for page_id, worksheet_id, worksheet_properties, layout in placements:
        if page_id not in pages_seen:
            pages_seen.append(page_id)
        wells = wells_by_worksheet[worksheet_id]
        resolution = resolve_visual(str(worksheet_properties.get("mark_type") or ""), wells, ruleset)
        visual_id = new_ulid()
        worksheet_name = str(worksheet_properties.get("name") or "")
        properties: dict[str, Any] = {
            "page": page_id,
            "type": resolution.visual_type,
            "source_sheet_ref": worksheet_id,
            "encodings": {
                "field_wells": [w.as_dict() for w in wells],
                "sort": list(worksheet_properties.get("sort") or ()),
                "filters": list(worksheet_properties.get("filters") or ()),
                "notes": list(resolution.notes),
            },
            "redesign_flag": resolution.redesign_flag,
            "redesign_reason": resolution.redesign_reason,
            "layout": layout,
            "interactivity": {
                "parameters": [p.as_dict() for p in parameters_by_worksheet[worksheet_id]],
                "actions": [
                    a.as_dict()
                    for a in _worksheet_action_mappings(workbook_actions, worksheet_name)
                ],
            },
        }
        visual_writes.append(NodeWrite(type="Visual", id=visual_id, properties=properties))
        edge_writes.append(
            EdgeWrite(type="MAPS_TO", from_id=worksheet_id, to_id=visual_id, properties={})
        )
        visual_records.append({"id": visual_id, "exception_case_id": None, **properties})

        if resolution.redesign_flag:
            screenshot_ref = await find_screenshot_ref(
                artefact_store, workbook_id=workbook_id, worksheet_name=worksheet_name
            )
            pending_exceptions.append(
                (
                    visual_id,
                    {
                        "mapping_reason": resolution.redesign_reason or "",
                        "placeholder_location": {
                            "page": page_id, "layout": layout, "source_sheet_ref": worksheet_id,
                        },
                        "screenshot_ref": screenshot_ref,
                    },
                )
            )

    if not visual_records:
        raise CompositorError(f"workbook '{workbook_id}' has no worksheets to compose")

    bundle = emit_pbir(visuals=visual_records)
    errors, warnings = validate_pbir(bundle)
    if errors:
        raise CompositorError(
            "the PBIR document this Compositor produced failed its own schema -- this is a "
            "defect in composition, not in the source workbook: " + "; ".join(errors[:10])
        )

    await _retire_previous_report(writer, pool, graph_name, workbook_id, principal=principal)

    report_id = new_ulid()
    await writer.write_nodes(
        [
            NodeWrite(
                type="ReportDefinition",
                id=report_id,
                properties={
                    "mu_ref": workbook_id,
                    "pages": pages_seen,
                    "model_ref": semantic_model_id,
                    "version": "1",
                    "validation_state": "SCHEMA_VALID",
                },
            ),
            *visual_writes,
        ],
        principal=principal,
    )
    for edge in edge_writes:
        await writer.write_edge(edge, principal=principal)

    if pending_exceptions:
        case_ids_by_visual: dict[str, str] = {}
        for visual_id, exception_properties in pending_exceptions:
            case_ids_by_visual[visual_id] = await open_redesign_exception(
                writer, workbook_id=workbook_id, visual_id=visual_id, principal=principal,
                **exception_properties,
            )
        for record in visual_records:
            if record["id"] in case_ids_by_visual:
                record["exception_case_id"] = case_ids_by_visual[record["id"]]

    return {
        "report_id": report_id,
        "workbook_id": workbook_id,
        "family_id": family_id,
        "model_ref": semantic_model_id,
        "pages": pages_seen,
        "visual_count": len(visual_records),
        "redesign_count": sum(1 for v in visual_records if v["redesign_flag"]),
        "validation_state": "SCHEMA_VALID",
        "validation_warnings": warnings,
        "visuals": visual_records,
    }


async def read_report(pool: asyncpg.Pool, graph_name: str, workbook_id: str) -> dict[str, Any] | None:
    """The current report for one workbook, read back -- not recomputed. ``None`` if this
    workbook has never been composed."""
    async with pool.acquire() as conn:
        report_ids, visual_ids = await _report_and_visual_ids(conn, graph_name, workbook_id)
        if not report_ids:
            return None
        reports = await hydrate(conn, graph_name, "ReportDefinition", report_ids)
        report_id, report_properties = next(iter(reports.items()))
        visuals = await hydrate(conn, graph_name, "Visual", visual_ids)

    return {
        "id": report_id,
        **report_properties,
        "visuals": sorted(
            ({"id": vid, **props} for vid, props in visuals.items()), key=lambda v: (v["page"], v["id"])
        ),
    }


class Compositor:
    """Binds ``compose_report``/``read_report`` to one pool/graph/writer, the identical
    "pre-bound object on app.state" shape ``Modeller``/``TrainPlanner``/``Cartographer``
    already each take -- a route needs no ``graph_name`` of its own to call this."""

    def __init__(
        self,
        pool: asyncpg.Pool,
        *,
        graph_name: str,
        writer: GraphWriter,
        artefact_store: ArtefactStore | None = None,
        provenance_store: ProvenanceStore | None = None,
    ) -> None:
        self._pool = pool
        self._graph = graph_name
        self._writer = writer
        self._artefact_store = artefact_store
        self._provenance_store = provenance_store

    @property
    def pool(self) -> asyncpg.Pool:
        return self._pool

    @property
    def graph_name(self) -> str:
        return self._graph

    @property
    def writer(self) -> GraphWriter:
        return self._writer

    async def compose(
        self, workbook_id: str, *, ruleset: VisualMappingRuleset, principal: Principal
    ) -> dict[str, Any]:
        return await compose_report(
            self._pool, self._graph, self._writer,
            workbook_id=workbook_id, ruleset=ruleset, principal=principal,
            artefact_store=self._artefact_store,
        )

    async def read(self, workbook_id: str) -> dict[str, Any] | None:
        return await read_report(self._pool, self._graph, workbook_id)

    async def generate_documentation(self, workbook_id: str, *, principal: Principal) -> dict[str, Any]:
        if self._artefact_store is None or self._provenance_store is None:
            raise CompositorError("report documentation is not available on this deployment")
        report = await self.read(workbook_id)
        if report is None:
            raise CompositorError(
                f"workbook '{workbook_id}' has not been composed into a report yet -- "
                f"documentation is generated from an already-composed report"
            )
        return await generate_report_documentation(
            self._pool, self._graph, self._writer,
            workbook_id=workbook_id, report=report,
            artefact_store=self._artefact_store, provenance_store=self._provenance_store,
            principal=principal,
        )

    async def read_documentation(self, workbook_id: str) -> dict[str, Any] | None:
        if self._artefact_store is None:
            return None
        report = await self.read(workbook_id)
        if report is None:
            return None
        return await read_report_documentation(self._artefact_store, report=report)


__all__ = [
    "Compositor",
    "CompositorError",
    "ResolvedWell",
    "VisualResolution",
    "compose_report",
    "read_report",
    "resolve_visual",
]
