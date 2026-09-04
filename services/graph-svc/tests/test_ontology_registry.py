"""The ontology declares what the specification says it declares.

S1.1.1 criterion 1, first half: "All node types in §4.1.1 and edge types in §4.1.2 are
defined with typed properties".
"""

from __future__ import annotations

import pytest

from astra_graph.ontology import (
    ANY_LABEL,
    BASE_EDGE_PROPERTIES,
    BASE_NODE_PROPERTIES,
    EDGE_INDEX,
    NODE_INDEX,
    Side,
    edge_type,
    node_type,
    sorted_edge_types,
    sorted_node_types,
)
from astra_graph.ontology.properties import PropertyType

# Every node type named in specification §4.1.1, with ReleaseTrain / Wave split.
SPEC_NODE_TYPES = {
    "Site", "Project", "Workbook", "Dashboard", "Worksheet", "Datasource", "Connection",
    "Table", "Field", "CalculatedField", "Parameter", "Filter", "Action", "User",
    "ModelFamily", "SemanticModel", "ModelTable", "Measure", "ReportDefinition", "Visual",
    "ParityCase", "ParityRun", "Verdict", "ExceptionCase", "Pattern", "GateDecision",
    "ReleaseTrain", "Wave",
}

# Every edge type named in §4.1.2, with OWNED_BY / VIEWED_BY split.
SPEC_EDGE_TYPES = {
    "CONTAINS", "USES_DATASOURCE", "CONNECTS_TO", "HAS_FIELD", "DEPENDS_ON", "ENCODES",
    "FILTERED_BY", "OWNED_BY", "VIEWED_BY", "SHARES_LINEAGE", "IN_FAMILY", "IN_TRAIN",
    "MAPS_TO", "PROVED_BY", "DECIDED_BY",
}


def test_every_specification_node_type_is_declared() -> None:
    assert set(NODE_INDEX) == SPEC_NODE_TYPES


def test_every_specification_edge_type_is_declared() -> None:
    assert set(EDGE_INDEX) == SPEC_EDGE_TYPES


@pytest.mark.parametrize("declared", sorted_node_types(), ids=lambda n: n.label)
def test_node_types_declare_typed_properties(declared) -> None:
    assert declared.properties, f"{declared.label} declares no properties"
    for spec in declared.properties:
        assert isinstance(spec.type, PropertyType)
        assert spec.name.islower() or "_" in spec.name
        if spec.type is PropertyType.ENUM:
            assert spec.enum


@pytest.mark.parametrize("declared", sorted_edge_types(), ids=lambda e: e.label)
def test_edge_types_declare_endpoints(declared) -> None:
    assert declared.pairs
    for from_label, to_label in declared.pairs:
        assert from_label == ANY_LABEL or from_label in NODE_INDEX
        assert to_label in NODE_INDEX


def test_sides_match_the_specification() -> None:
    assert node_type("Workbook").side is Side.SOURCE
    assert node_type("Measure").side is Side.TARGET
    assert node_type("Pattern").side is Side.PLATFORM
    # §4.1.1 marks User as 'both', so the writer declares it per node.
    assert node_type("User").side is None


def test_base_properties_are_on_every_node() -> None:
    """S1.1.1 criterion 2."""
    required = {"id", "side", "created_by", "created_at"}
    for declared in sorted_node_types():
        names = {p.name for p in declared.all_properties}
        assert required <= names, f"{declared.label} is missing {required - names}"
        for name in required:
            assert declared.property_spec(name).required


def test_every_edge_carries_written_by() -> None:
    """S1.1.1 criterion 3."""
    for declared in sorted_edge_types():
        written_by = declared.property_spec("written_by")
        assert written_by is not None, f"{declared.label} has no written_by"
        assert written_by.required
        assert written_by.server_managed


def test_base_properties_are_not_redeclared_by_types() -> None:
    base_node = {p.name for p in BASE_NODE_PROPERTIES}
    base_edge = {p.name for p in BASE_EDGE_PROPERTIES}
    for declared in sorted_node_types():
        assert not base_node & set(declared.declared_property_names)
    for declared in sorted_edge_types():
        assert not base_edge & set(declared.declared_property_names)


def test_edge_endpoint_permission() -> None:
    contains = edge_type("CONTAINS")
    assert contains.permits("Site", "Project")
    assert contains.permits("Workbook", "Worksheet")
    assert not contains.permits("Site", "Worksheet")

    decided_by = edge_type("DECIDED_BY")
    assert decided_by.permits("Workbook", "GateDecision")
    assert decided_by.permits("ModelFamily", "GateDecision")
    assert not decided_by.permits("Workbook", "Site")


def test_closed_sets_come_from_the_specification() -> None:
    assert node_type("Verdict").property_spec("result").enum == ("PASS", "FAIL", "INCONCLUSIVE")
    assert node_type("Pattern").property_spec("promotion_state").enum == (
        "CANDIDATE",
        "ACTIVE",
        "RETIRED",
    )
    assert node_type("GateDecision").property_spec("gate").enum == ("G1", "G2", "G3", "G4")
    assert node_type("ModelTable").property_spec("mode").enum == (
        "import",
        "directlake",
        "directquery",
    )
    # §11.1 failure taxonomy.
    assert "LOD_SCOPE" in node_type("ExceptionCase").property_spec("class").enum
