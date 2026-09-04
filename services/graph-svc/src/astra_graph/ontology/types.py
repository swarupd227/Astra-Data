"""Node and edge type declarations, and the base properties every element carries.

Spec §4.1.1 (nodes) and §4.1.2 (edges). This module is the schema; ``nodes.py`` and
``edges.py`` are the content. The generated ontology reference and the write-time
validator both read from here, which is what keeps the documented ontology and the
enforced ontology from drifting (S1.1.1 acceptance criterion 3).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum

from .properties import Cardinality, PropertySpec, PropertyType

__all__ = [
    "ANY_LABEL",
    "BASE_EDGE_PROPERTIES",
    "BASE_NODE_PROPERTIES",
    "Cardinality",
    "EdgeType",
    "NodeType",
    "PropertySpec",
    "PropertyType",
    "Side",
    "SpecDeviation",
]


class Side(str, Enum):
    """Which side of the migration a node belongs to (spec §4.1.1, column 'Side')."""

    SOURCE = "source"
    TARGET = "target"
    PLATFORM = "platform"


#: Wildcard endpoint label, used by DECIDED_BY whose source is 'any' node (spec §4.1.2).
ANY_LABEL = "*"


def _p(
    name: str,
    type_: PropertyType,
    *,
    required: bool = False,
    enum: tuple[str, ...] | None = None,
    server_managed: bool = False,
    note: str = "",
) -> PropertySpec:
    """Terser PropertySpec constructor; the ontology tables are long enough."""
    return PropertySpec(
        name=name,
        type=type_,
        cardinality=Cardinality.REQUIRED if required else Cardinality.OPTIONAL,
        enum=enum,
        server_managed=server_managed,
        note=note,
    )


# ---------------------------------------------------------------------------
# Base properties
#
# S1.1.1 acceptance criterion 2: every node carries id, side, created_by, created_at.
# Criterion 3: every edge carries written_by. The remaining base properties exist so an
# edge is addressable and so a write can be traced to the run that made it.
# ---------------------------------------------------------------------------

BASE_NODE_PROPERTIES: tuple[PropertySpec, ...] = (
    _p(
        "id",
        PropertyType.ULID,
        required=True,
        note="Platform identifier. Server-issued when the writer does not supply one; "
        "adapters supply deterministic ULIDs so a re-harvest is idempotent.",
    ),
    _p(
        "side",
        PropertyType.ENUM,
        required=True,
        enum=tuple(s.value for s in Side),
        note="Fixed by the node type, except for User which exists on both sides and "
        "must declare it.",
    ),
    _p(
        "created_by",
        PropertyType.STRING,
        required=True,
        server_managed=True,
        note="The agent or user principal that made the write.",
    ),
    _p(
        "created_at",
        PropertyType.TIMESTAMP,
        required=True,
        server_managed=True,
        note="Server clock at the write, UTC.",
    ),
    _p(
        "created_in_run",
        PropertyType.STRING,
        server_managed=True,
        note="The agent run that made the write, where the caller declared one.",
    ),
    _p(
        "updated_by",
        PropertyType.STRING,
        server_managed=True,
        note="The principal that last changed the node. Absent until something does: an "
        "upsert preserves created_by and sets this instead, so creation attribution "
        "survives a re-harvest or a re-score.",
    ),
    _p(
        "updated_at",
        PropertyType.TIMESTAMP,
        server_managed=True,
        note="Server clock at the last change, UTC. Absent on a node never changed.",
    ),
    _p(
        "retired_at",
        PropertyType.TIMESTAMP,
        server_managed=True,
        note="Set when the node is retired. A node is never deleted, so this is how a "
        "node leaves the working estate while staying in the record (S1.1.3).",
    ),
    _p(
        "retired_by",
        PropertyType.STRING,
        server_managed=True,
        note="The principal that retired the node.",
    ),
    _p(
        "retirement_reason",
        PropertyType.TEXT,
        server_managed=True,
        note="Why it was retired. Required at the point of retirement: a retirement with "
        "no stated reason is not a decision an auditor can read (spec P4).",
    ),
)

BASE_EDGE_PROPERTIES: tuple[PropertySpec, ...] = (
    _p(
        "id",
        PropertyType.ULID,
        required=True,
        note="Platform identifier, so an edge can be addressed and superseded.",
    ),
    _p(
        "written_by",
        PropertyType.STRING,
        required=True,
        server_managed=True,
        note="The agent or user principal that wrote the edge (spec §4.1.2 'Written by').",
    ),
    _p(
        "created_at",
        PropertyType.TIMESTAMP,
        required=True,
        server_managed=True,
        note="Server clock at the write, UTC.",
    ),
    _p(
        "created_in_run",
        PropertyType.STRING,
        server_managed=True,
        note="The agent run that made the write, where the caller declared one.",
    ),
    _p(
        "retired_at",
        PropertyType.TIMESTAMP,
        server_managed=True,
        note="Set when the edge is retired — 'superseded' in this property's own note "
        "above, finally used (story S3.1.2). An edge's endpoints cannot change once "
        "created, so replacing one relationship with another is retire-and-recreate, the "
        "same shape S1.1.3 gives nodes.",
    ),
    _p(
        "retired_by",
        PropertyType.STRING,
        server_managed=True,
        note="The principal that retired the edge.",
    ),
    _p(
        "retirement_reason",
        PropertyType.TEXT,
        server_managed=True,
        note="Why. Required at the point of retirement, the same as a node's (spec P4).",
    ),
)

BASE_NODE_PROPERTY_NAMES = frozenset(p.name for p in BASE_NODE_PROPERTIES)
BASE_EDGE_PROPERTY_NAMES = frozenset(p.name for p in BASE_EDGE_PROPERTIES)


@dataclass(frozen=True, slots=True)
class NodeType:
    """One node label in the ontology."""

    label: str
    side: Side | None
    """The side every instance sits on. ``None`` means the type spans both sides and the
    writer must declare ``side`` on the write (spec §4.1.1 marks User as 'both')."""

    spec_ref: str
    properties: tuple[PropertySpec, ...] = ()
    note: str = ""

    @property
    def all_properties(self) -> tuple[PropertySpec, ...]:
        return BASE_NODE_PROPERTIES + self.properties

    def property_spec(self, name: str) -> PropertySpec | None:
        for spec in self.all_properties:
            if spec.name == name:
                return spec
        return None

    @property
    def declared_property_names(self) -> tuple[str, ...]:
        """Type-specific property names, excluding the base properties."""
        return tuple(p.name for p in self.properties)

    def __post_init__(self) -> None:
        seen: set[str] = set()
        for spec in self.properties:
            if spec.name in BASE_NODE_PROPERTY_NAMES:
                raise ValueError(
                    f"node type {self.label!r} redeclares base property {spec.name!r}"
                )
            if spec.name in seen:
                raise ValueError(f"node type {self.label!r} declares {spec.name!r} twice")
            seen.add(spec.name)


@dataclass(frozen=True, slots=True)
class EdgeType:
    """One edge label in the ontology, with the endpoint pairs it permits."""

    label: str
    pairs: tuple[tuple[str, str], ...]
    """Permitted (from_label, to_label) pairs. ``ANY_LABEL`` in the from position means
    any node type, which spec §4.1.2 uses for DECIDED_BY."""

    written_by: str
    """The component the specification names as the writer of this edge. Documentation,
    not enforcement: enforcement of who may write arrives with agent identity (E11)."""

    spec_ref: str
    properties: tuple[PropertySpec, ...] = ()
    note: str = ""

    @property
    def all_properties(self) -> tuple[PropertySpec, ...]:
        return BASE_EDGE_PROPERTIES + self.properties

    def property_spec(self, name: str) -> PropertySpec | None:
        for spec in self.all_properties:
            if spec.name == name:
                return spec
        return None

    @property
    def declared_property_names(self) -> tuple[str, ...]:
        return tuple(p.name for p in self.properties)

    def permits(self, from_label: str, to_label: str) -> bool:
        return any(
            (allowed_from in (ANY_LABEL, from_label)) and (allowed_to in (ANY_LABEL, to_label))
            for allowed_from, allowed_to in self.pairs
        )

    def render_pairs(self) -> str:
        return "; ".join(f"{a}→{b}" for a, b in self.pairs)

    def __post_init__(self) -> None:
        if not self.pairs:
            raise ValueError(f"edge type {self.label!r} declares no endpoint pairs")
        seen: set[str] = set()
        for spec in self.properties:
            if spec.name in BASE_EDGE_PROPERTY_NAMES:
                raise ValueError(
                    f"edge type {self.label!r} redeclares base property {spec.name!r}"
                )
            if spec.name in seen:
                raise ValueError(f"edge type {self.label!r} declares {spec.name!r} twice")
            seen.add(spec.name)


@dataclass(frozen=True, slots=True)
class SpecDeviation:
    """A declared, reasoned difference between the specification table and the schema.

    The specification's ontology tables are prose: they carry annotations, wildcards and
    two types on one row. Where the schema cannot be a literal transcription, the
    difference is declared here with a reason. The spec-conformance check in
    ``tools/ontology_check.py`` fails on any difference that is *not* declared, so a
    deviation is a decision on the record rather than drift.
    """

    element: str
    reason: str
    detail: str = field(default="")
