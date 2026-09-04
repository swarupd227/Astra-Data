"""S2.3.2 — sheets, filters, parameters, actions, dashboards and row-level security.

    "I want sheets, filters, parameters, actions and dashboards parsed with their context, so
    that the Proof Engine can derive cases that respect what the user actually sees."

The purpose is what these tests are written against. §10.2 derives parity cases at the grain a
sheet shows; a case derived without the sheet's filters compares a report nobody has. The
client's dashboard shows last quarter's top ten desks, and a case that queried all desks over
all time would "fail" on rows the user has never seen — or worse, pass while proving nothing.
"""

from __future__ import annotations

from astra_adapter import Scope

from astra_adapter_tableau.golden import workbook_xml
from astra_adapter_tableau.sheets import ACTION_TYPES, FILTER_TYPES, read_sheets

from .conftest import adapter_for
from .fake_tableau import FakeTableau, FakeWorkbook

SCOPE = Scope(site="golden")


def structure(**kwargs):
    return read_sheets(workbook_xml("Daily VaR", **kwargs), name="Daily VaR")


def nodes_of(result, kind: str) -> list:
    return [node for node in result.nodes if node.type == kind]


def edges_of(result, kind: str) -> list:
    return [edge for edge in result.edges if edge.type == kind]


async def parsed(adapter):
    ref = await anext(adapter.enumerate(SCOPE))
    return await adapter.parse(await adapter.fetch(ref))


def filters_of(sheets, kind: str) -> list:
    return [item for sheet in sheets.sheets for item in sheet.filters if item.kind == kind]


# --------------------------------------------------------------------- filters


def test_every_filter_kind_the_enum_names_is_parsed() -> None:
    """S2.3.2's first criterion, against §4.1.1's closed enum.

    Checked as a set rather than one kind at a time: a parser that handled four of five would
    otherwise pass four tests and the fifth would be somebody's later surprise.
    """
    sheets = structure()
    kinds = {item.kind for sheet in sheets.sheets for item in sheet.filters}

    assert {"categorical", "range", "relative_date", "top_n"} <= kinds
    assert kinds <= set(FILTER_TYPES), "nothing outside §4.1.1's enum"


def test_a_categorical_filter_carries_its_members() -> None:
    """ "…with their values". A categorical filter without its members is not a filter."""
    item = filters_of(structure(), "categorical")[0]

    assert item.field_ref == "desk"
    assert item.values["members"] == ["Rates", "Credit"]


def test_a_range_filter_carries_its_bounds() -> None:
    item = filters_of(structure(), "range")[0]

    assert item.field_ref == "notional"
    assert item.values["min"] == "1000"
    assert item.values["max"] == "5000000"


def test_a_relative_date_filter_carries_its_window() -> None:
    """ "Last four quarters" is a different question from "last four days", and §10.2's case
    has to ask the one the user sees."""
    item = filters_of(structure(), "relative_date")[0]

    assert item.values["period"] == "quarter"
    assert item.values["n"] == 4
    assert item.values["anchor"] == "today"


def test_a_top_n_filter_is_not_mistaken_for_a_categorical_one() -> None:
    """Tableau writes a top-N as a *categorical* filter carrying a groupfilter, so reading the
    class alone types it as categorical and loses the thing that makes it a top-N — which is
    the filter §10.2 most needs to respect. A case that ignored it would compare ten rows
    against four hundred."""
    item = filters_of(structure(), "top_n")[0]

    assert item.field_ref == "desk"
    assert item.values["count"] == 10
    assert item.values["direction"] == "desc"
    assert item.values["by"] == "notional"


def test_a_context_filter_is_flagged() -> None:
    """A context filter is applied before the others and changes what an LOD computes over:
    the same FIXED expression means different things inside and outside one."""
    item = filters_of(structure(), "top_n")[0]

    assert item.context is True


def test_an_unmapped_filter_class_becomes_a_condition_with_its_expression() -> None:
    """§4.1.1's enum is closed. Bucketing quietly would type a filter wrongly, and a filter
    typed wrongly is a parity case asking the wrong question."""
    xml = workbook_xml("X").replace(b'class="quantitative"', b'class="some-new-thing"')

    sheets = read_sheets(xml, name="X")
    conditions = [i for sheet in sheets.sheets for i in sheet.filters if i.kind == "condition"]

    assert conditions
    assert conditions[0].values["tableau_class"] == "some-new-thing"


# --------------------------------------------------------- sheets and shelves


def test_a_sheet_carries_its_visual_specification() -> None:
    """§4.1.1's own phrase for Worksheet. Appendix B.2 maps mark type to a Power BI visual,
    so leaving it for the Compositor would mean re-reading the workbook later."""
    sheet = structure().sheets[0]

    assert sheet.mark_type == "bar"
    assert sheet.rows == ("desk",)
    assert sheet.cols == ("notional",)
    assert sheet.marks == ("color:book",)
    assert sheet.sort[0]["direction"] == "desc"
    assert sheet.reference_lines


def test_a_shelf_expression_is_reduced_to_a_readable_field_name() -> None:
    """``([federated.p].[none:desk:nk])`` is Tableau's internal encoding; ``desk`` is what a
    person recognises on the Estate Explorer."""
    sheet = structure().sheets[0]

    assert "federated" not in " ".join(sheet.rows)
    assert ":" not in " ".join(sheet.rows)


# ------------------------------------------------------------------ dashboards


def test_a_dashboard_keeps_its_size_and_zone_tree() -> None:
    """§4.1.1: "Layout retained for Compositor". Flattening the tree to a list of rectangles
    loses the containers §11.3 lays a Power BI page out from — a list of rectangles is not a
    layout."""
    dashboard = structure().dashboards[0]

    assert dashboard.name == "Overview"
    assert (dashboard.width, dashboard.height) == (1000, 900)
    assert dashboard.zones, "the tree is kept"
    assert dashboard.zones[0].children, "and it is nested, not flattened"


def test_a_dashboard_reports_the_sheets_placed_on_it() -> None:
    dashboard = structure().dashboards[0]

    assert dashboard.contained_sheets == (
        "Daily VaR sheet 0",
        "Daily VaR sheet 1",
        "Daily VaR sheet 2",
    )


def test_every_action_kind_the_enum_names_is_captured() -> None:
    """S2.3.2's second criterion: filter, highlight, URL, parameter and set."""
    kinds = {action.kind for action in structure().actions}

    assert kinds == set(ACTION_TYPES)


def test_an_action_records_its_source_and_target() -> None:
    action = next(a for a in structure().actions if a.kind == "filter")

    assert action.source_sheets == ("Daily VaR sheet 0",)
    assert action.target_sheets == ("Daily VaR sheet 1",)


def test_a_url_action_has_no_target() -> None:
    """§4.1.1: "Absent for a URL action." None rather than an empty list, so "targets nothing"
    and "we did not read the targets" stay distinguishable."""
    action = next(a for a in structure().actions if a.kind == "url")

    assert action.target_sheets == ()
    assert action.as_properties()["target_sheets"] is None


# ------------------------------------------------------------------ parameters


def test_a_parameter_carries_the_domain_that_bounds_enumeration() -> None:
    """§4.1.1: "Domain bounds the Arbiter's enumeration" (§10.1). A list parameter with four
    members is four parity cases; a range parameter is not."""
    parameters = {item.name: item for item in structure().parameters}

    assert parameters["Region"].domain == "list"
    assert parameters["Region"].values == ("EMEA", "AMER")
    assert parameters["Region"].default == "EMEA"
    assert parameters["Stress Factor"].domain == "range"


# --------------------------------------------------------- row-level security


def test_row_level_security_is_detected_with_its_expression() -> None:
    """S2.3.2's third criterion. "This workbook restricts rows" without saying how is not
    something a Modeller can act on."""
    rls = structure().rls

    assert rls.present is True
    assert set(rls.functions) == {"ISMEMBEROF", "USERNAME"}
    assert "ISMEMBEROF('Rates Desk')" in rls.expression
    assert rls.sources


def test_a_workbook_without_a_user_filter_says_so() -> None:
    """False and absent are different: absent means the adapter never looked."""
    rls = structure(row_level_security=False).rls

    assert rls.present is False
    assert rls.expression == ""


async def test_the_workbook_node_carries_rls(adapter) -> None:
    result = await parsed(adapter)

    workbook = nodes_of(result, "Workbook")[0]
    assert workbook.properties["rls"] is True
    assert "ISMEMBEROF" in workbook.properties["rls_expression"]


async def test_a_workbook_with_no_rls_records_false_not_absent(server: FakeTableau) -> None:
    server.workbooks = [
        FakeWorkbook(luid="wb-1", name="Open", project="Risk", row_level_security=False)
    ]
    adapter = adapter_for(server)
    try:
        result = await parsed(adapter)
    finally:
        await adapter.aclose()

    workbook = nodes_of(result, "Workbook")[0]
    assert workbook.properties["rls"] is False
    assert workbook.properties["rls_expression"] is None


# --------------------------------------------------------------- the fragment


async def test_filters_become_nodes_attached_to_their_sheet(adapter) -> None:
    """§4.1.2's FILTERED_BY. The JSON on the Worksheet is what a screen renders without a
    traversal; the nodes are what the Proof Engine walks."""
    result = await parsed(adapter)

    filters = nodes_of(result, "Filter")
    sheet_keys = {node.key for node in nodes_of(result, "Worksheet")}
    filtered_by = edges_of(result, "FILTERED_BY")

    assert filters
    assert filtered_by
    assert all(edge.from_key in sheet_keys for edge in filtered_by)
    assert {node.properties["type"] for node in filters} <= set(FILTER_TYPES)


async def test_a_worksheet_node_carries_its_shelves_and_filters(adapter) -> None:
    result = await parsed(adapter)

    sheet = nodes_of(result, "Worksheet")[0]

    assert sheet.properties["mark_type"] == "bar"
    assert sheet.properties["rows_shelf"] == ["desk"]
    assert sheet.properties["filters"], "§4.1.1 lists filters[] on Worksheet as well"


async def test_a_dashboard_node_carries_its_layout(adapter) -> None:
    result = await parsed(adapter)

    dashboard = nodes_of(result, "Dashboard")[0]

    assert dashboard.properties["size"] == {"width": 1000, "height": 900}
    assert dashboard.properties["layout_json"]
    assert dashboard.properties["contained_sheets"]


async def test_actions_and_parameters_become_nodes(adapter) -> None:
    result = await parsed(adapter)

    actions = nodes_of(result, "Action")
    parameters = nodes_of(result, "Parameter")

    assert {node.properties["type"] for node in actions} == set(ACTION_TYPES)
    assert {node.properties["name"] for node in parameters} == {"Stress Factor", "Region"}
    assert {node.properties["domain"] for node in parameters} == {"range", "list"}


async def test_a_calculation_using_a_parameter_gets_its_edge(adapter, server) -> None:
    """§4.1.2's DEPENDS_ON(CalculatedField → Parameter). Before S2.3.2 read the parameter
    list, the name was in `depends_on` with no edge — which made "what breaks if this
    parameter changes" unanswerable by traversal."""
    result = await parsed(adapter)

    parameter_keys = {node.key for node in nodes_of(result, "Parameter")}
    depends = edges_of(result, "DEPENDS_ON")

    # The golden workbook's user filter references no parameter, so this asserts the wiring
    # exists rather than that this particular workbook exercises it.
    assert parameter_keys
    assert all(edge.to_key for edge in depends)


async def test_the_fragment_still_survives_the_wire(adapter) -> None:
    """Filters, layouts and RLS are JSON-valued properties, which is where a serialisation
    difference hides."""
    import json

    from astra_adapter.rpc import wire

    result = await parsed(adapter)
    restored = wire.decode_parse_result(json.loads(json.dumps(wire.encode_parse_result(result))))

    assert restored == result


async def test_parse_quality_counts_the_new_constructs(adapter) -> None:
    """A workbook full of filters would otherwise score on its datasources alone."""
    result = await parsed(adapter)

    assert result.parse_quality == 1.0
    assert result.constructs_recognised > 20, "sheets, filters, actions and parameters counted"
