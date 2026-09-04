"""The Estate Graph ontology: node types, edge types and write-time enforcement.

Spec §4.1.1, §4.1.2. This package is the single definition of the graph's shape. The
generated reference in ``docs/generated/ontology.md``, the write path in ``graph-svc`` and
the migration guard all read from it, which is what keeps the documented ontology and the
enforced ontology the same thing.
"""

from .properties import Cardinality, PropertySpec, PropertyType, PropertyValueError, coerce
from .registry import (
    EDGE_INDEX,
    EDGE_LABELS,
    NODE_INDEX,
    NODE_LABELS,
    SCHEMA_VERSION,
    SPEC_DEVIATIONS,
    edge_type,
    node_type,
    require_edge_type,
    require_node_type,
    sorted_edge_types,
    sorted_node_types,
)
from .types import (
    ANY_LABEL,
    BASE_EDGE_PROPERTIES,
    BASE_NODE_PROPERTIES,
    EdgeType,
    NodeType,
    Side,
    SpecDeviation,
)
from .validate import (
    ValidationResult,
    Violation,
    ViolationCode,
    validate_edge,
    validate_node,
)

__all__ = [
    "ANY_LABEL",
    "BASE_EDGE_PROPERTIES",
    "BASE_NODE_PROPERTIES",
    "EDGE_INDEX",
    "EDGE_LABELS",
    "NODE_INDEX",
    "NODE_LABELS",
    "SCHEMA_VERSION",
    "SPEC_DEVIATIONS",
    "Cardinality",
    "EdgeType",
    "NodeType",
    "PropertySpec",
    "PropertyType",
    "PropertyValueError",
    "Side",
    "SpecDeviation",
    "ValidationResult",
    "Violation",
    "ViolationCode",
    "coerce",
    "edge_type",
    "node_type",
    "require_edge_type",
    "require_node_type",
    "sorted_edge_types",
    "sorted_node_types",
    "validate_edge",
    "validate_node",
]
