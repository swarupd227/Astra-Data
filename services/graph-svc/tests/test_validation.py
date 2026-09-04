"""Write-time enforcement.

S1.1.1 criterion 1: "a write with an unknown type or a missing required property is
rejected ... with a message naming the property".
"""

from __future__ import annotations

import pytest

from astra_graph.ontology import ViolationCode, validate_edge, validate_node
from astra_graph.ontology.properties import PropertySpec, PropertyType, PropertyValueError, coerce

SERVER_NODE = {
    "id": "01HX7ZZZZZZZZZZZZZZZZZZZZZ",
    "created_by": "agent:harvester",
    "created_at": "2027-01-14T09:12:07.000Z",
}
SERVER_EDGE = {
    "id": "01HX7ZZZZZZZZZZZZZZZZZZZZY",
    "written_by": "agent:harvester",
    "created_at": "2027-01-14T09:12:07.000Z",
}


def codes(result) -> set[ViolationCode]:
    return {v.code for v in result.violations}


def properties_named(result) -> set[str]:
    return {v.property for v in result.violations if v.property}


# ------------------------------------------------------------------- unknown types


def test_unknown_node_type_is_rejected() -> None:
    result = validate_node("Sheetish", {"name": "x"}, server_supplied=SERVER_NODE)
    assert not result.ok
    assert codes(result) == {ViolationCode.UNKNOWN_NODE_TYPE}
    assert "not a node type" in result.violations[0].message


def test_unknown_node_type_suggests_a_case_correction() -> None:
    result = validate_node("workbook", {}, server_supplied=SERVER_NODE)
    assert "Did you mean 'Workbook'?" in result.violations[0].message


def test_unknown_edge_type_is_rejected() -> None:
    result = validate_edge(
        "CONTAINSS", {}, from_label="Site", to_label="Project", server_supplied=SERVER_EDGE
    )
    assert codes(result) == {ViolationCode.UNKNOWN_EDGE_TYPE}


# --------------------------------------------------------------------- properties


def test_missing_required_property_names_the_property() -> None:
    result = validate_node("Workbook", {"luid": "abc"}, server_supplied=SERVER_NODE)
    assert not result.ok
    assert codes(result) == {ViolationCode.MISSING_REQUIRED_PROPERTY}
    assert properties_named(result) == {"name", "revision"}
    for violation in result.violations:
        assert violation.property in violation.message


def test_every_missing_property_is_reported_not_just_the_first() -> None:
    result = validate_node("CalculatedField", {}, server_supplied=SERVER_NODE)
    assert properties_named(result) == {"name", "formula", "formula_ast"}


def test_undeclared_property_is_rejected() -> None:
    result = validate_node(
        "Workbook",
        {"luid": "a", "name": "b", "revision": "1", "colour": "blue"},
        server_supplied=SERVER_NODE,
    )
    assert codes(result) == {ViolationCode.UNKNOWN_PROPERTY}
    assert result.violations[0].property == "colour"
    assert "is not declared on node type 'Workbook'" in result.violations[0].message


def test_explicit_null_on_a_required_property_is_a_missing_property() -> None:
    result = validate_node(
        "Workbook", {"luid": "a", "name": None, "revision": "1"}, server_supplied=SERVER_NODE
    )
    assert codes(result) == {ViolationCode.MISSING_REQUIRED_PROPERTY}
    assert properties_named(result) == {"name"}


def test_null_on_an_optional_property_is_simply_absent() -> None:
    result = validate_node(
        "Workbook",
        {"luid": "a", "name": "b", "revision": "1", "size": None},
        server_supplied=SERVER_NODE,
    )
    assert result.ok
    assert "size" not in result.properties


def test_server_managed_property_cannot_be_supplied_by_the_caller() -> None:
    result = validate_node(
        "Workbook",
        {"luid": "a", "name": "b", "revision": "1", "created_by": "agent:impostor"},
        server_supplied=SERVER_NODE,
    )
    assert ViolationCode.SERVER_MANAGED_PROPERTY in codes(result)
    assert properties_named(result) == {"created_by"}


# ------------------------------------------------------------------- typed values


@pytest.mark.parametrize(
    ("properties", "bad_property"),
    [
        ({"luid": "a", "name": "b", "revision": "1", "size": "large"}, "size"),
        ({"luid": "a", "name": "b", "revision": "1", "extract_flag": "yes"}, "extract_flag"),
        ({"luid": "a", "name": "b", "revision": "1", "last_published": "not-a-date"}, "last_published"),
        ({"luid": "a", "name": 42, "revision": "1"}, "name"),
    ],
)
def test_wrong_type_is_rejected_and_names_the_property(properties, bad_property) -> None:
    result = validate_node("Workbook", properties, server_supplied=SERVER_NODE)
    assert codes(result) == {ViolationCode.INVALID_PROPERTY_VALUE}
    assert properties_named(result) == {bad_property}
    assert bad_property in result.violations[0].message


def test_enum_rejects_a_value_outside_the_closed_set() -> None:
    result = validate_node(
        "Field",
        {"name": "Region", "datatype": "string", "role": "attribute"},
        server_supplied=SERVER_NODE,
    )
    assert codes(result) == {ViolationCode.INVALID_PROPERTY_VALUE}
    assert "must be one of dimension|measure" in result.violations[0].message


def test_string_list_rejects_a_non_string_element() -> None:
    result = validate_node(
        "Action",
        {"type": "filter", "source_sheets": ["a", 3]},
        server_supplied=SERVER_NODE,
    )
    assert codes(result) == {ViolationCode.INVALID_PROPERTY_VALUE}
    assert "element 1 is int" in result.violations[0].message


def test_naive_timestamp_is_rejected_rather_than_assumed_utc() -> None:
    spec = PropertySpec("last_published", PropertyType.TIMESTAMP)
    with pytest.raises(PropertyValueError, match="missing a UTC offset"):
        coerce(spec, "2027-01-14T09:12:07")


def test_timestamps_are_normalised_to_utc() -> None:
    spec = PropertySpec("last_published", PropertyType.TIMESTAMP)
    assert coerce(spec, "2027-01-14T09:12:07-05:00") == "2027-01-14T14:12:07+00:00".replace(
        "+00:00", "Z"
    )


def test_boolean_is_not_an_integer() -> None:
    spec = PropertySpec("views_90d", PropertyType.INT)
    with pytest.raises(PropertyValueError, match="expects an integer, got bool"):
        coerce(spec, True)


# -------------------------------------------------------------------------- side


def test_side_is_taken_from_the_node_type() -> None:
    result = validate_node(
        "Workbook", {"luid": "a", "name": "b", "revision": "1"}, server_supplied=SERVER_NODE
    )
    assert result.ok
    assert result.properties["side"] == "source"


def test_contradicting_the_types_side_is_rejected() -> None:
    result = validate_node(
        "Workbook",
        {"luid": "a", "name": "b", "revision": "1", "side": "target"},
        server_supplied=SERVER_NODE,
    )
    assert codes(result) == {ViolationCode.INVALID_SIDE}


def test_a_both_sided_type_requires_the_writer_to_declare_side() -> None:
    missing = validate_node("User", {"upn": "a@b.example"}, server_supplied=SERVER_NODE)
    assert codes(missing) == {ViolationCode.MISSING_REQUIRED_PROPERTY}
    assert properties_named(missing) == {"side"}

    declared = validate_node(
        "User", {"upn": "a@b.example", "side": "source"}, server_supplied=SERVER_NODE
    )
    assert declared.ok
    assert declared.properties["side"] == "source"


# ------------------------------------------------------------------------- edges


def test_edge_endpoints_must_be_permitted() -> None:
    result = validate_edge(
        "USES_DATASOURCE",
        {},
        from_label="Site",
        to_label="Datasource",
        server_supplied=SERVER_EDGE,
    )
    assert codes(result) == {ViolationCode.INVALID_EDGE_ENDPOINTS}
    assert "Worksheet→Datasource" in result.violations[0].message


def test_permitted_edge_endpoints_pass() -> None:
    result = validate_edge(
        "CONTAINS", {}, from_label="Workbook", to_label="Worksheet", server_supplied=SERVER_EDGE
    )
    assert result.ok
    assert result.properties["written_by"] == "agent:harvester"


def test_wildcard_source_endpoint_accepts_any_node_type() -> None:
    for label in ("Workbook", "ModelFamily", "Site"):
        result = validate_edge(
            "DECIDED_BY",
            {},
            from_label=label,
            to_label="GateDecision",
            server_supplied=SERVER_EDGE,
        )
        assert result.ok


def test_edge_required_property_is_enforced() -> None:
    result = validate_edge(
        "ENCODES",
        {"aggregation": "SUM"},
        from_label="Worksheet",
        to_label="Field",
        server_supplied=SERVER_EDGE,
    )
    assert codes(result) == {ViolationCode.MISSING_REQUIRED_PROPERTY}
    assert properties_named(result) == {"shelf"}


def test_violations_accumulate_across_categories() -> None:
    result = validate_node(
        "Datasource",
        {"name": "ds", "type": "inline", "colour": "blue", "created_at": "2020-01-01T00:00:00Z"},
        server_supplied=SERVER_NODE,
    )
    assert codes(result) == {
        ViolationCode.UNKNOWN_PROPERTY,
        ViolationCode.SERVER_MANAGED_PROPERTY,
        ViolationCode.INVALID_PROPERTY_VALUE,
    }
