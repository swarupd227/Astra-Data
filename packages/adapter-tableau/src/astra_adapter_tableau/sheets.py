"""Sheets, filters, parameters, actions and dashboards — story S2.3.2.

    "I want sheets, filters, parameters, actions and dashboards parsed with their context, so
    that the Proof Engine can derive cases that respect what the user actually sees."

The *purpose* is the part that decides the design. §10.2 derives parity cases at the grain a
sheet shows, and a case derived without the sheet's filters compares a report nobody has: the
client's dashboard shows last quarter's top ten desks, and a case that queried all desks over
all time would "fail" on rows the user has never seen. So a filter is not decoration to be
recorded for completeness — it is part of the question the Proof Engine asks.

Three things this module reads out of `<worksheets>`, `<dashboards>` and `<actions>`:

1. **Filters**, typed as §4.1.1's closed enum: categorical, range, relative_date, top_n,
   condition. With their values, because a range filter without its bounds is not a filter.
2. **Dashboards** — size, zone tree, which sheets are placed where — and **actions**, because
   §10.6's visual comparison and the Compositor's layout both need them.
3. **Row-level security**, which is the criterion with consequences: a workbook whose rows
   depend on who is looking has an access model the target must reproduce, and parity cases
   run under a service identity that sees everything.

Sheet *shelves* and mark type are read too. They are the visual specification §4.1.1 names on
Worksheet, and Appendix B.2 maps mark type to a Power BI visual — so leaving them for the
Compositor would mean re-reading the workbook later.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import Any
from xml.etree import ElementTree

logger = logging.getLogger(__name__)

#: §4.1.1's closed enum for Filter.type. A filter outside it is recorded as ``condition``,
#: which is Tableau's own catch-all, and logged — never invented.
FILTER_TYPES = ("categorical", "range", "relative_date", "top_n", "condition")

#: §4.1.1's closed enum for Action.type.
ACTION_TYPES = ("filter", "highlight", "url", "parameter", "set")

#: Tableau's action element names, mapped to the enum. ``set`` actions arrived in 2020 and are
#: written differently from the rest, which is why the mapping is a table rather than a prefix
#: match.
_ACTION_ELEMENTS = {
    "filter": "filter",
    "highlight": "highlight",
    "url": "url",
    "parameter": "parameter",
    "set": "set",
    "change-parameter": "parameter",
    "change-set": "set",
}

#: The functions that make a calculation user-dependent (S2.3.2's third criterion).
#: ISMEMBEROF and USERNAME are named in the story; the rest are the same family and a
#: workbook using FULLNAME() to restrict rows restricts rows just as much.
RLS_FUNCTIONS = ("ISMEMBEROF", "USERNAME", "FULLNAME", "ISUSERNAME", "ISFULLNAME", "USERDOMAIN")

_RLS_PATTERN = re.compile(r"\b(" + "|".join(RLS_FUNCTIONS) + r")\s*\(", re.IGNORECASE)


@dataclass(frozen=True, slots=True)
class Filter:
    """One sheet-level filter, typed and with its values (S2.3.2's first criterion).

    ``field_ref`` rather than ``field``: §4.1.1 names the property that way, and an attribute
    called ``field`` on a dataclass shadows ``dataclasses.field`` — which made the very next
    line, ``values: dict = field(default_factory=dict)``, a call on a string.
    """

    field_ref: str
    kind: str
    values: dict[str, Any] = field(default_factory=dict)
    context: bool = False
    """A Tableau context filter, which is applied *before* other filters and changes what an
    LOD expression computes over. §10.2's case derivation has to know: the same FIXED
    expression means different things inside and outside a context filter."""

    exclude: bool = False

    def as_properties(self) -> dict[str, Any]:
        return {
            "field_ref": self.field_ref,
            "type": self.kind,
            "values": {**self.values, "exclude": self.exclude} if self.exclude else self.values,
            "context_flag": self.context,
        }


@dataclass(frozen=True, slots=True)
class Sheet:
    """A worksheet's visual specification (§4.1.1's own phrase)."""

    name: str
    mark_type: str = ""
    rows: tuple[str, ...] = ()
    cols: tuple[str, ...] = ()
    marks: tuple[str, ...] = ()
    sort: tuple[dict[str, Any], ...] = ()
    filters: tuple[Filter, ...] = ()
    reference_lines: tuple[dict[str, Any], ...] = ()
    datasources: tuple[str, ...] = ()

    def as_properties(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "mark_type": self.mark_type or None,
            "rows_shelf": list(self.rows),
            "cols_shelf": list(self.cols),
            "marks_shelf": list(self.marks),
            "sort": list(self.sort),
            # Also on the Worksheet as JSON, as §4.1.1 has it, *and* as Filter nodes with
            # FILTERED_BY edges. Both because §4.1.1 lists `filters[]` on Worksheet and §4.1.2
            # gives Filter its own node — the JSON is what a screen renders without a
            # traversal, the nodes are what the Proof Engine walks.
            "filters": [item.as_properties() for item in self.filters],
            "reference_lines": list(self.reference_lines),
        }


@dataclass(frozen=True, slots=True)
class Zone:
    """One region of a dashboard's layout. Nested, as Tableau nests them."""

    kind: str
    name: str = ""
    x: int = 0
    y: int = 0
    width: int = 0
    height: int = 0
    children: tuple[Zone, ...] = ()

    def as_dict(self) -> dict[str, Any]:
        return {
            "type": self.kind,
            "name": self.name,
            "x": self.x,
            "y": self.y,
            "w": self.width,
            "h": self.height,
            "children": [child.as_dict() for child in self.children],
        }

    def sheets(self) -> tuple[str, ...]:
        found: tuple[str, ...] = (self.name,) if self.kind == "worksheet" and self.name else ()
        for child in self.children:
            found += child.sheets()
        return found


@dataclass(frozen=True, slots=True)
class Dashboard:
    """A dashboard, its size and its zone tree (§4.1.1: "Layout retained for Compositor")."""

    name: str
    width: int = 0
    height: int = 0
    zones: tuple[Zone, ...] = ()

    @property
    def contained_sheets(self) -> tuple[str, ...]:
        seen: list[str] = []
        for zone in self.zones:
            for sheet in zone.sheets():
                if sheet not in seen:
                    seen.append(sheet)
        return tuple(seen)

    def as_properties(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "size": {"width": self.width, "height": self.height},
            "layout_json": [zone.as_dict() for zone in self.zones],
            "contained_sheets": list(self.contained_sheets),
        }


@dataclass(frozen=True, slots=True)
class Action:
    """A dashboard action (§4.1.1's closed enum)."""

    name: str
    kind: str
    source_sheets: tuple[str, ...] = ()
    target_sheets: tuple[str, ...] = ()

    def as_properties(self) -> dict[str, Any]:
        return {
            "type": self.kind,
            "source_sheets": list(self.source_sheets),
            # §4.1.1: "Absent for a URL action." None rather than an empty list, so "this
            # action targets nothing" and "we did not read the targets" stay distinguishable.
            "target_sheets": list(self.target_sheets) if self.target_sheets else None,
        }


@dataclass(frozen=True, slots=True)
class Parameter:
    """A workbook parameter (§4.1.1: "Domain bounds the Arbiter's enumeration")."""

    name: str
    datatype: str = "string"
    domain: str = "any"
    default: Any = None
    values: tuple[str, ...] = ()

    def as_properties(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "datatype": self.datatype,
            "domain": self.domain,
            "default": self.default,
            "current_values_seen": list(self.values),
        }


@dataclass(frozen=True, slots=True)
class RowLevelSecurity:
    """What restricts which rows a person sees (S2.3.2's third criterion)."""

    present: bool = False
    expressions: tuple[str, ...] = ()
    functions: tuple[str, ...] = ()
    sources: tuple[str, ...] = ()

    @property
    def expression(self) -> str:
        """The expressions, joined. Verbatim, because "this workbook restricts rows" without
        saying how is not something a Modeller can act on."""
        return "\n".join(self.expressions)


@dataclass(frozen=True, slots=True)
class SheetStructure:
    """Everything S2.3.2 reads out of a workbook."""

    sheets: tuple[Sheet, ...] = ()
    dashboards: tuple[Dashboard, ...] = ()
    actions: tuple[Action, ...] = ()
    parameters: tuple[Parameter, ...] = ()
    rls: RowLevelSecurity = field(default_factory=RowLevelSecurity)


def read_sheets(xml: bytes, *, name: str = "") -> SheetStructure:
    """Parse the sheet, dashboard and action structure of a workbook."""
    try:
        root = ElementTree.fromstring(xml)
    except ElementTree.ParseError:
        # `datasource.read_structure` already reported this with the detail; returning empty
        # rather than raising twice keeps one failure one message.
        return SheetStructure()

    sheets = tuple(
        _sheet(element) for element in root.findall("./worksheets/worksheet") if element.get("name")
    )
    dashboards = tuple(
        _dashboard(element)
        for element in root.findall("./dashboards/dashboard")
        if element.get("name")
    )
    actions = tuple(
        action
        for element in root.findall("./actions/action")
        if (action := _action(element)) is not None
    )
    parameters = tuple(_parameters(root))
    rls = _row_level_security(root, sheets)

    logger.debug(
        "%s: %d sheet(s), %d dashboard(s), %d action(s), %d parameter(s), rls=%s",
        name or "workbook",
        len(sheets),
        len(dashboards),
        len(actions),
        len(parameters),
        rls.present,
    )
    return SheetStructure(
        sheets=sheets,
        dashboards=dashboards,
        actions=actions,
        parameters=parameters,
        rls=rls,
    )


# ---------------------------------------------------------------------- sheets


def _sheet(element: ElementTree.Element) -> Sheet:
    table = element.find("./table")
    view = table.find("./view") if table is not None else None

    return Sheet(
        name=str(element.get("name", "")),
        mark_type=_mark_type(table),
        rows=_shelf(table, "rows"),
        cols=_shelf(table, "cols"),
        marks=_encodings(table),
        sort=_sorts(table),
        filters=_filters(view),
        reference_lines=_reference_lines(table),
        datasources=tuple(
            str(child.get("datasource", ""))
            for child in element.findall(".//datasource-dependencies")
            if child.get("datasource")
        ),
    )


def _mark_type(table: ElementTree.Element | None) -> str:
    """Appendix B.2 maps this to a Power BI visual, so it is worth reading now.

    Tableau writes ``<mark class='Bar'/>`` inside the pane. Absent means Automatic, which is
    a real answer and not a missing one — Tableau chooses from the shelves, and the Compositor
    will have to make the same choice.
    """
    if table is None:
        return ""
    mark = table.find(".//mark")
    return str(mark.get("class", "")).lower() if mark is not None else "automatic"


def _shelf(table: ElementTree.Element | None, shelf: str) -> tuple[str, ...]:
    """The rows or cols shelf, as the field references on it.

    Tableau writes the shelf as an expression string — ``([federated].[none:Desk:nk])`` — and
    the field name is the readable part. Kept as written *and* unwrapped would be two forms of
    the same fact; the readable one is what a person recognises on the Estate Explorer, and
    §10.2's grain comes from the model rather than from this string.
    """
    if table is None:
        return ()
    element = table.find(f"./{shelf}")
    if element is None or not (element.text or "").strip():
        return ()
    return tuple(_field_names(element.text or ""))


def _encodings(table: ElementTree.Element | None) -> tuple[str, ...]:
    if table is None:
        return ()
    # Tableau writes the encoded field as ``column``, not ``field`` — ``<color
    # column="[fed].[none:book:nk]" .../>``. Reading only ``field`` returned an empty marks
    # shelf on every sheet, which looks like "this sheet encodes nothing" rather than like a
    # parser missing an attribute, and Appendix B.2 binds encodings to the target visual.
    return tuple(
        f"{child.get('attr', child.tag)}:{_field_name(str(child.get('column') or child.get('field') or ''))}"
        for child in table.findall(".//encodings/*")
        if child.get("column") or child.get("field")
    )


def _sorts(table: ElementTree.Element | None) -> tuple[dict[str, Any], ...]:
    if table is None:
        return ()
    return tuple(
        {
            "field": _field_name(str(child.get("column", ""))),
            "direction": str(child.get("direction", "ASC")).lower(),
            "using": str(child.get("using", "")),
        }
        for child in table.findall(".//sort")
        if child.get("column")
    )


def _reference_lines(table: ElementTree.Element | None) -> tuple[dict[str, Any], ...]:
    """§4.1.1 lists these on Worksheet, and Appendix B.2 maps them to the analytics pane."""
    if table is None:
        return ()
    return tuple(
        {
            "scope": str(child.get("scope", "")),
            "value": str(child.get("value", "")),
            "aggregation": str(child.get("aggregation", "")),
        }
        for child in table.findall(".//reference-line")
    )


# --------------------------------------------------------------------- filters


def _filters(view: ElementTree.Element | None) -> tuple[Filter, ...]:
    if view is None:
        return ()
    return tuple(_filter(element) for element in view.findall("./filter"))


def _filter(element: ElementTree.Element) -> Filter:
    """One filter, typed into §4.1.1's closed enum with its values.

    Tableau's own vocabulary does not line up with the enum: it writes ``class='categorical'``,
    ``class='quantitative'``, ``class='relative-date'``, and expresses a top-N as a
    *categorical* filter carrying a ``<groupfilter function='end'>``. Reading the class alone
    would type a top-N filter as categorical and lose the thing that makes it a top-N — which
    is exactly the filter §10.2 most needs to respect, because a case that ignored it would
    compare ten rows against four hundred.
    """
    field_ref = _field_name(str(element.get("column", "")))
    tableau_class = str(element.get("class", "")).lower()
    group = element.find(".//groupfilter")

    if group is not None and str(group.get("function", "")) in {"end", "top", "order"}:
        return Filter(
            field_ref=field_ref,
            kind="top_n",
            values={
                "count": _int(group.get("count")),
                "direction": str(group.get("direction", "DESC")).lower(),
                "by": _field_name(str(group.get("expression", ""))),
            },
            context=_is_context(element),
        )

    if tableau_class in {"relative-date", "relativedate"}:
        return Filter(
            field_ref=field_ref,
            kind="relative_date",
            values={
                "period": str(element.get("period-type", "")),
                "range": str(element.get("range-type", "")),
                "n": _int(element.get("range-n")),
                "anchor": str(element.get("anchor", "")),
            },
            context=_is_context(element),
        )

    if tableau_class in {"quantitative", "range"}:
        minimum = element.find(".//min")
        maximum = element.find(".//max")
        return Filter(
            field_ref=field_ref,
            kind="range",
            values={
                "min": (minimum.text or "").strip() if minimum is not None else element.get("min"),
                "max": (maximum.text or "").strip() if maximum is not None else element.get("max"),
                "included_null": str(element.get("included-values", "")) == "non-null",
            },
            context=_is_context(element),
        )

    if tableau_class == "categorical":
        members = [
            _unquote_member(str(child.get("member", "")))
            for child in element.findall(".//groupfilter")
            if child.get("member")
        ]
        return Filter(
            field_ref=field_ref,
            kind="categorical",
            values={"members": members},
            context=_is_context(element),
            exclude=(group is not None and str(group.get("function", "")) == "except"),
        )

    # Tableau's catch-all, and ours. Logged rather than silently bucketed: §4.1.1's enum is
    # closed and a filter typed wrongly is a parity case asking the wrong question.
    if tableau_class:
        logger.info(
            "filter class %r on %r has no direct mapping; recorded as 'condition' with its "
            "expression",
            tableau_class,
            field_ref,
        )
    formula = element.find(".//formula")
    return Filter(
        field_ref=field_ref,
        kind="condition",
        values={
            "tableau_class": tableau_class,
            "expression": str(formula.get("value", "")) if formula is not None else "",
        },
        context=_is_context(element),
    )


def _is_context(element: ElementTree.Element) -> bool:
    return str(element.get("context", "false")).lower() == "true"


# ------------------------------------------------------------------ dashboards


def _dashboard(element: ElementTree.Element) -> Dashboard:
    size = element.find("./size")
    zones = element.find("./zones")
    return Dashboard(
        name=str(element.get("name", "")),
        width=_int(size.get("maxwidth") or size.get("width")) if size is not None else 0,
        height=_int(size.get("maxheight") or size.get("height")) if size is not None else 0,
        zones=tuple(_zone(child) for child in zones) if zones is not None else (),
    )


def _zone(element: ElementTree.Element) -> Zone:
    """One zone, and its children.

    Tableau's layout is a tree of nested containers, and flattening it to a list of sheet
    placements would lose the containers — which is what the Compositor lays a Power BI page
    out from (§11.3). A list of rectangles is not a layout.
    """
    kind = str(element.get("type-v2") or element.get("type") or "container").lower()
    return Zone(
        kind="worksheet" if element.get("name") and kind in {"", "layout-basic"} else kind,
        name=str(element.get("name", "")),
        x=_int(element.get("x")),
        y=_int(element.get("y")),
        width=_int(element.get("w")),
        height=_int(element.get("h")),
        children=tuple(_zone(child) for child in element.findall("./zone")),
    )


def _action(element: ElementTree.Element) -> Action | None:
    """A dashboard action, typed into §4.1.1's closed enum.

    Returns None for an action whose kind has no mapping, rather than guessing: Appendix B.1
    puts parameter and set actions at C3/C4 already, and an action typed wrongly would be
    translated into the wrong interaction.
    """
    kind = str(element.get("type", "")).lower()
    command = element.find("./command")
    if not kind and command is not None:
        kind = str(command.get("command", "")).split(".")[-1].lower()

    mapped = _ACTION_ELEMENTS.get(kind)
    if mapped is None:
        for candidate, value in _ACTION_ELEMENTS.items():
            if candidate in kind:
                mapped = value
                break
    if mapped is None:
        logger.info("dashboard action %r has no mapping into §4.1.1's enum; not recorded", kind)
        return None

    source = element.find("./source")
    target = element.find("./target")
    return Action(
        name=str(element.get("caption") or element.get("name") or mapped),
        kind=mapped,
        source_sheets=_sheet_names(source),
        target_sheets=_sheet_names(target),
    )


def _sheet_names(element: ElementTree.Element | None) -> tuple[str, ...]:
    if element is None:
        return ()
    named = [str(element.get("worksheet", ""))] if element.get("worksheet") else []
    named += [
        str(child.get("worksheet", ""))
        for child in element.findall(".//*[@worksheet]")
        if child.get("worksheet")
    ]
    return tuple(dict.fromkeys(name for name in named if name))


# ------------------------------------------------------------------ parameters


def _parameters(root: ElementTree.Element) -> list[Parameter]:
    """Tableau models a parameter as a column on a reserved ``Parameters`` datasource.

    Reading them here rather than in `datasource.py` because §4.1.1 gives a Parameter its own
    node with a *domain*, and the domain is what bounds the Arbiter's enumeration (§10.1) — a
    list parameter with four members is four parity cases and a range parameter is not.
    """
    found: list[Parameter] = []
    for datasource in root.findall("./datasources/datasource"):
        if str(datasource.get("name", "")).lower() not in {"parameters"}:
            continue
        for column in datasource.findall("./column"):
            members = [
                _unquote_member(str(alias.get("value", "")))
                for alias in column.findall(".//member")
                if alias.get("value")
            ]
            domain = str(column.get("param-domain-type", "any")).lower()
            found.append(
                Parameter(
                    name=str(column.get("name", "")).strip("[]"),
                    datatype=str(column.get("datatype", "string")),
                    domain=domain if domain in {"list", "range", "any"} else "any",
                    default=_unquote_member(str(column.get("value", ""))) or None,
                    values=tuple(members),
                )
            )
    return found


# --------------------------------------------------------- row-level security


def _row_level_security(root: ElementTree.Element, sheets: tuple[Sheet, ...]) -> RowLevelSecurity:
    """S2.3.2's third criterion.

    Three places a Tableau workbook restricts rows by who is looking:

    - a **user filter**, which Tableau writes as a calculated field over ``ISMEMBEROF`` or
      ``USERNAME`` and then applies as an ordinary filter;
    - the same functions used directly in any calculation;
    - a filter whose condition expression calls them.

    All three are looked for, because a programme needs to know the *workbook* is
    user-dependent — §10's parity cases run under a service identity that sees everything, so
    a case derived from one of these compares rows the client's user never sees, and it will
    "pass" while proving nothing.
    """
    expressions: list[str] = []
    functions: set[str] = set()
    sources: list[str] = []

    for datasource in root.findall("./datasources/datasource"):
        for column in datasource.findall("./column"):
            calculation = column.find("./calculation")
            formula = str(calculation.get("formula", "")) if calculation is not None else ""
            if matches := _RLS_PATTERN.findall(formula):
                expressions.append(formula)
                functions.update(match.upper() for match in matches)
                sources.append(f"calculation:{str(column.get('name', '')).strip('[]')}")

    for sheet in sheets:
        for item in sheet.filters:
            expression = str(item.values.get("expression", ""))
            if matches := _RLS_PATTERN.findall(expression):
                expressions.append(expression)
                functions.update(match.upper() for match in matches)
                sources.append(f"filter:{sheet.name}/{item.field_ref}")

    if not expressions:
        return RowLevelSecurity()

    logger.info(
        "row-level security detected: %s used in %d place(s)",
        ", ".join(sorted(functions)),
        len(sources),
    )
    return RowLevelSecurity(
        present=True,
        expressions=tuple(dict.fromkeys(expressions)),
        functions=tuple(sorted(functions)),
        sources=tuple(dict.fromkeys(sources)),
    )


# --------------------------------------------------------------------- helpers

_FIELD = re.compile(r"\[([^\]]+)\]")


def _field_names(text: str) -> list[str]:
    """Readable field names out of a Tableau shelf expression.

    ``([federated.1abc].[none:Desk:nk])`` → ``Desk``. The qualifier and the aggregation
    prefix are Tableau's internal encoding; the last bracketed part with its ``none:``/``sum:``
    prefix stripped is the name a person recognises.
    """
    names: list[str] = []
    for match in _FIELD.findall(text):
        name = _strip_prefix(match)
        if name and not name.startswith("federated") and name not in names:
            names.append(name)
    return names


def _field_name(text: str) -> str:
    names = _field_names(text)
    return names[-1] if names else _strip_prefix(text.strip("[]"))


def _strip_prefix(name: str) -> str:
    """``none:Desk:nk`` → ``Desk``. Tableau prefixes with the aggregation and suffixes with
    the role; both are its own encoding rather than part of the field's name."""
    parts = name.split(":")
    return parts[1] if len(parts) >= 3 else name


def _unquote_member(value: str) -> str:
    """Strip the quotes Tableau wraps a member value in.

    Either kind, and only from the ends. Written as a helper rather than an escaped
    ``strip()`` argument because the escaping is exactly the sort of thing that is wrong
    without looking wrong.
    """
    text = value.strip()
    if len(text) >= 2 and text[0] == text[-1] and text[0] in {"'", '"'}:
        return text[1:-1]
    return text


def _int(value: Any) -> int:
    try:
        return int(float(str(value)))
    except (TypeError, ValueError):
        return 0
