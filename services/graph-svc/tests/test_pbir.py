"""PBIR emission and schema validation -- stories S6.1.1 and S6.1.3, spec §7.1.

    "JSON schema validation against PBIR schema; visual-type whitelist; binding check that
    every field reference resolves in the model."

Pure functions, no database -- `emit_pbir`/`validate_pbir` are reproducible from an
already-gathered visual list alone.
"""

from __future__ import annotations

from astra_graph.pbir import VISUAL_TYPE_WHITELIST, emit_pbir, validate_pbir


def _visual(**overrides: object) -> dict[str, object]:
    base: dict[str, object] = {
        "id": "v1",
        "page": "p1",
        "type": "clusteredColumnChart",
        "source_sheet_ref": "w1",
        "encodings": {"field_wells": [], "sort": [], "filters": []},
        "redesign_flag": False,
        "redesign_reason": None,
        "layout": {"x": 0, "y": 0, "width": 4, "height": 4},
    }
    base.update(overrides)
    return base


def _well(**overrides: object) -> dict[str, object]:
    base: dict[str, object] = {
        "shelf": "rows", "role": "values", "sourceKind": "Field", "sourceId": "f1",
        "sourceName": "sales", "bound": True, "table": "mt_sales", "column": "sales",
        "measureId": None, "reason": None,
    }
    base.update(overrides)
    return base


# --------------------------------------------------------------------------------- emit_pbir


def test_emit_pbir_writes_one_report_one_page_and_one_visual_document() -> None:
    bundle = emit_pbir(visuals=[_visual()])
    assert set(bundle) == {
        "definition/report.json", "pages/p1/page.json", "pages/p1/visuals/v1/visual.json",
    }
    assert bundle["definition/report.json"]["pages"] == [{"name": "p1"}]
    assert bundle["pages/p1/page.json"]["visuals"] == ["v1"]


def test_two_visuals_on_one_page_share_the_page_document() -> None:
    bundle = emit_pbir(visuals=[_visual(id="v1"), _visual(id="v2")])
    assert bundle["pages/p1/page.json"]["visuals"] == ["v1", "v2"]


def test_two_pages_each_get_their_own_page_document() -> None:
    bundle = emit_pbir(visuals=[_visual(id="v1", page="p1"), _visual(id="v2", page="p2")])
    assert {p["name"] for p in bundle["definition/report.json"]["pages"]} == {"p1", "p2"}


def test_a_placeholder_visual_still_emits_a_full_document() -> None:
    bundle = emit_pbir(
        visuals=[_visual(type="placeholder", redesign_flag=True, redesign_reason="no mapping", layout=None)]
    )
    document = bundle["pages/p1/visuals/v1/visual.json"]
    assert document["visualType"] == "placeholder"
    assert document["redesignFlag"] is True
    assert document["redesignReason"] == "no mapping"
    assert document["position"] is None


# ----------------------------------------------------------------------------- validate_pbir


def test_a_well_formed_bundle_has_no_errors_or_warnings() -> None:
    bundle = emit_pbir(visuals=[_visual(encodings={"field_wells": [_well()], "sort": [], "filters": []})])
    errors, warnings = validate_pbir(bundle)
    assert errors == []
    assert warnings == []


def test_an_unrecognised_visual_type_is_an_error() -> None:
    bundle = emit_pbir(visuals=[_visual(type="notARealType")])
    errors, _ = validate_pbir(bundle)
    assert any("whitelist" in e for e in errors)


def test_every_default_target_type_is_on_the_whitelist() -> None:
    from astra_graph.visual_mapping import DEFAULT_MAPPINGS

    for rule in DEFAULT_MAPPINGS:
        if rule.target_visual_type:
            assert rule.target_visual_type in VISUAL_TYPE_WHITELIST


def test_an_unbound_field_well_is_a_warning_not_an_error() -> None:
    unbound = _well(bound=False, table=None, column=None, reason="no Field->ModelTable MAPS_TO edge exists for this field yet")
    bundle = emit_pbir(visuals=[_visual(encodings={"field_wells": [unbound], "sort": [], "filters": []})])
    errors, warnings = validate_pbir(bundle)
    assert errors == []
    assert len(warnings) == 1
    assert "sales" in warnings[0]


def test_a_document_missing_a_required_field_is_a_schema_error() -> None:
    bundle = emit_pbir(visuals=[_visual()])
    del bundle["definition/report.json"]["pages"]
    errors, _ = validate_pbir(bundle)
    assert any("report.json" in e and "pages" in e for e in errors)


def test_an_unrecognised_document_path_is_reported() -> None:
    errors, _ = validate_pbir({"pages/p1/theme.json": {}})
    assert any("not a recognised PBIR document path" in e for e in errors)


# ----------------------------------------------------------------------------- interactivity


def _parameter(**overrides: object) -> dict[str, object]:
    base: dict[str, object] = {
        "name": "Region", "datatype": "string", "domain": "list", "kind": "slicer",
        "supported": True, "values": ["EMEA", "APAC"], "default": "EMEA", "reason": None,
    }
    base.update(overrides)
    return base


def _action(**overrides: object) -> dict[str, object]:
    base: dict[str, object] = {
        "type": "filter", "role": "source", "otherSheets": ["Detail"],
        "powerBiSetting": "crossFilter", "supported": True, "reason": None,
    }
    base.update(overrides)
    return base


def test_emit_pbir_always_carries_an_interactivity_block_even_when_absent() -> None:
    bundle = emit_pbir(visuals=[_visual()])
    document = bundle["pages/p1/visuals/v1/visual.json"]
    assert document["interactivity"] == {"parameters": [], "actions": []}


def test_emit_pbir_carries_through_parameters_and_actions() -> None:
    bundle = emit_pbir(
        visuals=[_visual(interactivity={"parameters": [_parameter()], "actions": [_action()]})]
    )
    document = bundle["pages/p1/visuals/v1/visual.json"]
    assert document["interactivity"]["parameters"] == [_parameter()]
    assert document["interactivity"]["actions"] == [_action()]


def test_a_well_formed_interactivity_block_has_no_errors_or_warnings() -> None:
    bundle = emit_pbir(
        visuals=[_visual(interactivity={"parameters": [_parameter()], "actions": [_action()]})]
    )
    errors, warnings = validate_pbir(bundle)
    assert errors == []
    assert warnings == []


def test_an_unsupported_parameter_is_a_warning_not_an_error() -> None:
    unsupported = _parameter(kind=None, supported=False, reason="Tableau's 'any' domain ...")
    bundle = emit_pbir(visuals=[_visual(interactivity={"parameters": [unsupported], "actions": []})])
    errors, warnings = validate_pbir(bundle)
    assert errors == []
    assert len(warnings) == 1
    assert "Region" in warnings[0]


def test_an_unsupported_action_is_a_warning_not_an_error() -> None:
    unsupported = _action(type="parameter", powerBiSetting=None, supported=False, reason="Appendix B.2 ...")
    bundle = emit_pbir(visuals=[_visual(interactivity={"parameters": [], "actions": [unsupported]})])
    errors, warnings = validate_pbir(bundle)
    assert errors == []
    assert len(warnings) == 1
    assert "parameter" in warnings[0]
    assert "Detail" in warnings[0]


def test_a_malformed_interactivity_block_is_a_schema_error() -> None:
    bundle = emit_pbir(visuals=[_visual()])
    bundle["pages/p1/visuals/v1/visual.json"]["interactivity"]["parameters"] = [{"name": "incomplete"}]
    errors, _ = validate_pbir(bundle)
    assert any("visual.json" in e for e in errors)
