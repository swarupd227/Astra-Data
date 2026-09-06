"""The Compositor's pure logic -- stories S6.1.1 and S6.1.3, spec §8.8/Appendix B.

    "Mapping table from Appendix B (mark type x encodings -> visual type) is data,
    versioned, and editable by the architect."
    "Sheets whose mark type has no mapping are emitted as a placeholder visual with
    redesign_flag: true and the reason."
    "Tableau parameters become what-if parameters or slicers by type; filter actions
    become cross-filter settings; URL actions become URL fields; unsupported actions are
    listed on the MU page with the Appendix B guidance."

`resolve_visual`, `classify_parameter`, `classify_action` and the zone-layout helpers are
pure functions of already-resolved data -- every test here runs with no database, matching
`test_conformance_rules.py`'s own footing.
"""

from __future__ import annotations

from astra_graph.compositor import (
    ResolvedWell,
    _find_zone,
    _worksheet_action_mappings,
    _zone_layout,
    _zone_list,
    classify_action,
    classify_parameter,
    resolve_visual,
)
from astra_graph.visual_mapping import DEFAULT_MAPPINGS, VisualMappingRuleset

RULESET = VisualMappingRuleset(version=0, rules=DEFAULT_MAPPINGS, updated_by="system", updated_at=None)


def _well(shelf: str, role: str, **overrides: object) -> ResolvedWell:
    base = {
        "shelf": shelf, "role": role, "source_kind": "Field", "source_id": "f1",
        "source_name": "x", "bound": True, "table": "t", "column": "c", "measure_id": None,
        "reason": None,
    }
    base.update(overrides)
    return ResolvedWell(**base)


# ------------------------------------------------------------------------------------- bar


def test_a_plain_bar_stays_clustered_column() -> None:
    wells = [_well("rows", "values"), _well("cols", "axis")]
    result = resolve_visual("bar", wells, RULESET)
    assert result.visual_type == "clusteredColumnChart"
    assert not result.redesign_flag


def test_a_colour_encoded_bar_becomes_stacked_column() -> None:
    wells = [_well("rows", "values"), _well("cols", "axis"), _well("color", "legend")]
    result = resolve_visual("bar", wells, RULESET)
    assert result.visual_type == "stackedColumnChart"


def test_a_measure_on_columns_alone_is_a_horizontal_bar() -> None:
    """Columns lay out horizontally in Tableau; a measure there (not on rows) is how a
    horizontal bar is built -- the swap of the plain vertical case above."""
    wells = [_well("cols", "values")]
    result = resolve_visual("bar", wells, RULESET)
    assert result.visual_type == "clusteredBarChart"


def test_a_colour_encoded_horizontal_bar_is_stacked_bar() -> None:
    wells = [_well("cols", "values"), _well("color", "legend")]
    result = resolve_visual("bar", wells, RULESET)
    assert result.visual_type == "stackedBarChart"


# ------------------------------------------------------------------------------ line/area


def test_a_single_measure_line_stays_a_line_chart() -> None:
    result = resolve_visual("line", [_well("rows", "values"), _well("cols", "axis")], RULESET)
    assert result.visual_type == "lineChart"


def test_two_measures_on_rows_is_a_dual_axis_combo() -> None:
    wells = [
        _well("rows", "values", source_id="f1"), _well("rows", "values", source_id="f2"),
        _well("cols", "axis"),
    ]
    result = resolve_visual("line", wells, RULESET)
    assert result.visual_type == "lineStackedColumnComboChart"
    assert any("dual-axis" in n for n in result.notes)


def test_dual_axis_only_applies_to_line_not_area_type_name() -> None:
    wells = [_well("rows", "values", source_id="f1"), _well("rows", "values", source_id="f2")]
    result = resolve_visual("area", wells, RULESET)
    assert result.visual_type == "areaChart"
    assert any("dual-axis" in n for n in result.notes)


# ------------------------------------------------------------------------------------ text


def test_text_with_only_rows_stays_a_table() -> None:
    result = resolve_visual("text", [_well("rows", "axis")], RULESET)
    assert result.visual_type == "tableEx"


def test_text_with_rows_and_cols_becomes_a_matrix() -> None:
    result = resolve_visual("text", [_well("rows", "axis"), _well("cols", "axis")], RULESET)
    assert result.visual_type == "matrix"


# --------------------------------------------------------------------------------- scatter


_BUBBLE_NOTE = "this is a bubble chart"


def test_a_plain_scatter_has_no_bubble_specific_note() -> None:
    """The static Appendix B.2 row text mentions 'bubble' unconditionally (it describes
    both cases); only the size-encoding refinement's own added note is conditional."""
    result = resolve_visual("circle", [_well("rows", "values"), _well("cols", "values")], RULESET)
    assert result.visual_type == "scatterChart"
    assert not any(_BUBBLE_NOTE in n for n in result.notes)


def test_a_sized_scatter_is_noted_as_a_bubble_chart() -> None:
    wells = [_well("rows", "values"), _well("cols", "values"), _well("size", "size")]
    result = resolve_visual("circle", wells, RULESET)
    assert result.visual_type == "scatterChart"
    assert any(_BUBBLE_NOTE in n for n in result.notes)


# --------------------------------------------------------------------------- automatic/KPI


def test_automatic_with_one_measure_and_no_dimension_is_a_card() -> None:
    result = resolve_visual("automatic", [_well("rows", "values")], RULESET)
    assert result.visual_type == "card"
    assert not result.redesign_flag


def test_automatic_with_a_dimension_and_a_measure_resolves_to_bar() -> None:
    result = resolve_visual("automatic", [_well("rows", "axis"), _well("cols", "values")], RULESET)
    assert result.visual_type == "clusteredColumnChart" or result.visual_type == "clusteredBarChart"


def test_automatic_with_nothing_placed_is_flagged() -> None:
    result = resolve_visual("automatic", [], RULESET)
    assert result.redesign_flag
    assert "Automatic" in result.redesign_reason


# ------------------------------------------------------------------------------ redesign


def test_gantt_is_a_placeholder_with_the_appendix_reason() -> None:
    result = resolve_visual("ganttbar", [], RULESET)
    assert result.visual_type == "placeholder"
    assert result.redesign_flag
    assert "client approval" in result.redesign_reason


def test_an_unknown_mark_type_is_a_placeholder_with_a_generic_reason() -> None:
    result = resolve_visual("hexbin", [], RULESET)
    assert result.visual_type == "placeholder"
    assert result.redesign_flag
    assert "hexbin" in result.redesign_reason


def test_an_editable_ruleset_change_takes_effect_immediately() -> None:
    from astra_graph.visual_mapping import MappingRule

    custom = VisualMappingRuleset(
        version=1,
        rules=(MappingRule("bar", target_visual_type="ribbonChart"),),
        updated_by="architect",
        updated_at=None,
    )
    result = resolve_visual("bar", [], custom)
    assert result.visual_type == "ribbonChart"


# ------------------------------------------------------------------------------ zone layout


def test_find_zone_locates_a_nested_worksheet_zone() -> None:
    zones = [
        {
            "type": "layout-basic", "name": "", "x": 0, "y": 0, "w": 800, "h": 600,
            "children": [
                {"type": "worksheet", "name": "VaR sheet", "x": 10, "y": 20, "w": 300, "h": 200, "children": []},
                {"type": "worksheet", "name": "Other sheet", "x": 400, "y": 20, "w": 300, "h": 200, "children": []},
            ],
        }
    ]
    zone = _find_zone(zones, "VaR sheet")
    assert zone is not None
    assert _zone_layout(zone) == {"x": 10, "y": 20, "width": 300, "height": 200}


def test_find_zone_returns_none_for_an_absent_sheet() -> None:
    assert _find_zone([{"type": "worksheet", "name": "Other", "children": []}], "Missing") is None


def test_zone_layout_of_none_is_none() -> None:
    assert _zone_layout(None) is None


def test_zone_list_accepts_a_bare_list_the_real_tableau_adapter_writes() -> None:
    zones = [{"type": "worksheet", "name": "a"}]
    assert _zone_list(zones) == zones


def test_zone_list_accepts_the_fixture_adapters_own_wrapped_shape() -> None:
    """Found live, composing a real harvested workbook: the fixture source adapter
    (`astra_adapter.fake.source`) writes `{"zones": [{"sheet": ref}, ...]}`, not the real
    Tableau adapter's own bare list -- this must never crash a compose."""
    assert _zone_list({"zones": [{"sheet": "workbook:x/worksheet:0"}]}) == [{"sheet": "workbook:x/worksheet:0"}]


def test_zone_list_of_none_or_garbage_is_empty() -> None:
    assert _zone_list(None) == []
    assert _zone_list("not a zone tree") == []
    assert _zone_list({"no_zones_key": True}) == []


def test_find_zone_skips_non_dict_entries_without_crashing() -> None:
    """The fixture adapter's own zone entries (`{"sheet": ref}`) carry no `name`/`type`, so
    they never match -- honest absence, not fabricated geometry -- but must not raise."""
    zones = _zone_list({"zones": [{"sheet": "workbook:x/worksheet:0"}]})
    assert _find_zone(zones, "VaR sheet") is None
    assert _find_zone(["not-a-dict"], "VaR sheet") is None


# --------------------------------------------------------------------------- parameters


def _parameter(**overrides: object) -> dict[str, object]:
    base: dict[str, object] = {
        "name": "Growth Rate", "datatype": "real", "domain": "range",
        "current_values_seen": [], "default": "0.05",
    }
    base.update(overrides)
    return base


def test_a_list_domain_parameter_becomes_a_slicer() -> None:
    mapping = classify_parameter(_parameter(domain="list", current_values_seen=["EMEA", "APAC"]))
    assert mapping.kind == "slicer"
    assert mapping.supported
    assert mapping.reason is None
    assert mapping.values == ("EMEA", "APAC")


def test_a_range_domain_parameter_becomes_a_what_if_but_discloses_missing_bounds() -> None:
    mapping = classify_parameter(_parameter(domain="range"))
    assert mapping.kind == "what_if"
    assert mapping.supported
    assert "bounds" in mapping.reason


def test_an_any_domain_parameter_is_unsupported() -> None:
    mapping = classify_parameter(_parameter(domain="any"))
    assert mapping.kind is None
    assert not mapping.supported
    assert "unconstrained" in mapping.reason


def test_a_missing_domain_defaults_to_any_and_is_unsupported() -> None:
    parameter = _parameter()
    del parameter["domain"]
    mapping = classify_parameter(parameter)
    assert mapping.domain == "any"
    assert not mapping.supported


# -------------------------------------------------------------------------------- actions


def test_a_filter_action_becomes_a_cross_filter_setting() -> None:
    mapping = classify_action("filter", role="source", other_sheets=["Detail"])
    assert mapping.power_bi_setting == "crossFilter"
    assert mapping.supported
    assert mapping.reason is None
    assert mapping.other_sheets == ("Detail",)


def test_a_highlight_action_becomes_a_highlight_setting() -> None:
    """Appendix B.2 groups highlight with filter under one outcome (`Cross-filter/
    highlight settings`); the backlog's own AC text names only filter and URL explicitly
    but does not carve highlight out either -- treated identically here."""
    mapping = classify_action("highlight", role="target", other_sheets=["Overview"])
    assert mapping.power_bi_setting == "highlight"
    assert mapping.supported


def test_a_url_action_becomes_a_url_field() -> None:
    mapping = classify_action("url", role="source", other_sheets=[])
    assert mapping.power_bi_setting == "url"
    assert mapping.supported


def test_a_parameter_action_is_unsupported_with_the_appendix_b_guidance() -> None:
    mapping = classify_action("parameter", role="target", other_sheets=["Overview"])
    assert mapping.power_bi_setting is None
    assert not mapping.supported
    assert "Appendix B.2" in mapping.reason
    assert "C3 or C4" in mapping.reason


def test_a_set_action_is_unsupported_with_the_appendix_b_guidance() -> None:
    mapping = classify_action("set", role="source", other_sheets=["Overview"])
    assert not mapping.supported
    assert "Appendix B.2" in mapping.reason


def test_worksheet_action_mappings_matches_by_source_and_target_sheet_name() -> None:
    actions = [
        {"type": "filter", "source_sheets": ["Overview"], "target_sheets": ["Detail"]},
        {"type": "url", "source_sheets": ["Other sheet"], "target_sheets": []},
    ]
    source_side = _worksheet_action_mappings(actions, "Overview")
    assert len(source_side) == 1
    assert source_side[0].role == "source"

    target_side = _worksheet_action_mappings(actions, "Detail")
    assert len(target_side) == 1
    assert target_side[0].role == "target"

    assert _worksheet_action_mappings(actions, "Neither sheet") == []


def test_worksheet_action_mappings_can_be_both_source_and_target() -> None:
    actions = [{"type": "filter", "source_sheets": ["Overview"], "target_sheets": ["Overview"]}]
    mappings = _worksheet_action_mappings(actions, "Overview")
    assert {m.role for m in mappings} == {"source", "target"}
