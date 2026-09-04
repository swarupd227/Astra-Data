"""GraphQL object types, generated from the ontology registry.

S1.1.2 asks for a *typed* query API. Rather than returning a property bag, one GraphQL
object type is built per node type and per edge type, with the properties that type
declares and their declared nullability. The schema therefore cannot describe a shape the
write path would reject, and adding a property to the ontology adds it to the API without
anyone editing this file.

Two conventions worth stating:

* **Field names are the ontology's names.** ``auto_camel_case`` is off, so ``views_90d``
  is ``views_90d`` in GraphQL, not ``views90D``. The property names come from the
  specification and a reader comparing the two should not have to translate.
* **Enums are strings.** The closed sets are on ``GET /v1/ontology`` and are enforced at
  write time. Minting a GraphQL enum per property would add thirty types to the schema
  for a constraint the writer already guarantees.
"""

from __future__ import annotations

from typing import Any, Optional

import strawberry
from strawberry.scalars import JSON

from ...graph.model import EdgeRecord, NodeRecord
from ...ontology import (
    BASE_EDGE_PROPERTIES,
    BASE_NODE_PROPERTIES,
    EdgeType,
    NodeType,
    PropertySpec,
    PropertyType,
    sorted_edge_types,
    sorted_node_types,
)

#: Ontology property type to Python annotation. TIMESTAMP and DATE stay strings: they are
#: stored canonicalised (RFC 3339 UTC, ISO 8601) by the write path, and re-parsing them
#: into datetimes here would let a serialiser reformat what the graph recorded.
_ANNOTATION: dict[PropertyType, Any] = {
    PropertyType.STRING: str,
    PropertyType.TEXT: str,
    PropertyType.INT: int,
    PropertyType.FLOAT: float,
    PropertyType.BOOL: bool,
    PropertyType.TIMESTAMP: str,
    PropertyType.DATE: str,
    PropertyType.ULID: strawberry.ID,
    PropertyType.LUID: str,
    PropertyType.ENUM: str,
    PropertyType.STRING_LIST: list[str],
    PropertyType.JSON: JSON,
}

#: GraphQL field names that are not valid Python identifiers. ``class`` is a property of
#: CalculatedField, Measure, Pattern, ExceptionCase and the MAPS_TO edge; dataclasses
#: generate ``__init__`` as source, so the attribute has to be spelled differently even
#: though the GraphQL field keeps the ontology's name.
_PYTHON_NAME = {"class": "class_"}


def python_name(property_name: str) -> str:
    return _PYTHON_NAME.get(property_name, property_name)


def _annotate(spec: PropertySpec) -> Any:
    annotation = _ANNOTATION[spec.type]
    # Optional[...] rather than `| None`: the annotation is a runtime value here, and
    # `X | None` on a subscripted generic is not constructible this way.
    return annotation if spec.required else Optional[annotation]  # noqa: UP045


# The interfaces carry only the base properties. The ontology type of an element is read
# with GraphQL's built-in `__typename`, not a field of our own: Datasource, Filter, Action
# and Visual each declare a property called `type`, and a field of that name on the
# interface would be shadowed by the property — `Datasource.type` would answer
# "published" where a caller asked what kind of node it was.


@strawberry.interface(description="Anything in the Estate Graph that is a node.")
class EstateNode:
    id: strawberry.ID
    side: str
    created_by: str
    created_at: str
    created_in_run: str | None = None
    # Who last changed it (S1.2.2). Null on a node that has never been changed since it
    # was created; an upsert preserves created_by and sets these instead.
    updated_by: str | None = None
    updated_at: str | None = None
    # Retirement (S1.1.3). Present on every node because a node is never deleted; these
    # are null for a node still in the working estate.
    retired_at: str | None = None
    retired_by: str | None = None
    retirement_reason: str | None = None


@strawberry.interface(description="Anything in the Estate Graph that is an edge.")
class EstateEdge:
    id: strawberry.ID
    written_by: str
    created_at: str
    from_id: strawberry.ID
    to_id: strawberry.ID
    created_in_run: str | None = None
    # Retirement (S3.1.2). An edge's endpoints cannot change once created, so a changed
    # relationship is retire-and-recreate, the same shape S1.1.3 gives nodes — these are
    # null for an edge still live.
    retired_at: str | None = None
    retired_by: str | None = None
    retirement_reason: str | None = None


def _build(
    name: str,
    declared: tuple[PropertySpec, ...],
    base: type,
    *,
    description: str,
) -> type:
    """Create one Strawberry object type from an ontology type's declared properties."""
    annotations: dict[str, Any] = {}
    namespace: dict[str, Any] = {}
    for spec in declared:
        attribute = python_name(spec.name)
        annotations[attribute] = _annotate(spec)
        namespace[attribute] = strawberry.field(
            name=spec.name, description=spec.note or None, default=None
        )
    namespace["__annotations__"] = annotations
    namespace["__doc__"] = description
    created = type(name, (base,), namespace)
    return strawberry.type(created, name=name, description=description)  # type: ignore[no-any-return]


def _describe_node(declared: NodeType) -> str:
    side = declared.side.value if declared.side else "source or target, declared per node"
    note = f" {declared.note}" if declared.note else ""
    return f"Spec {declared.spec_ref}. Side: {side}.{note}".strip()


def _describe_edge(declared: EdgeType) -> str:
    note = f" {declared.note}" if declared.note else ""
    return (
        f"Spec {declared.spec_ref}. {declared.render_pairs()}. "
        f"Written by {declared.written_by}.{note}"
    ).strip()


NODE_TYPES: dict[str, type] = {
    declared.label: _build(
        declared.label, declared.properties, EstateNode, description=_describe_node(declared)
    )
    for declared in sorted_node_types()
}

EDGE_TYPES: dict[str, type] = {
    declared.label: _build(
        declared.label, declared.properties, EstateEdge, description=_describe_edge(declared)
    )
    for declared in sorted_edge_types()
}

_BASE_NODE_NAMES = tuple(spec.name for spec in BASE_NODE_PROPERTIES)
_BASE_EDGE_NAMES = tuple(spec.name for spec in BASE_EDGE_PROPERTIES)


def node_from_record(record: NodeRecord) -> EstateNode:
    """Build the generated GraphQL type for a stored node."""
    cls = NODE_TYPES.get(record.label)
    if cls is None:
        raise ValueError(f"node label {record.label!r} is not in the ontology")
    kwargs = {
        python_name(name): record.properties.get(name)
        for name in _BASE_NODE_NAMES
        if name != "id"
    }
    kwargs["id"] = strawberry.ID(str(record.properties["id"]))
    for spec in _declared_specs(cls):
        kwargs[python_name(spec)] = record.properties.get(spec)
    return cls(**kwargs)  # type: ignore[no-any-return]


def edge_from_record(record: EdgeRecord) -> EstateEdge:
    """Build the generated GraphQL type for a stored edge."""
    cls = EDGE_TYPES.get(record.label)
    if cls is None:
        raise ValueError(f"edge label {record.label!r} is not in the ontology")
    kwargs = {
        python_name(name): record.properties.get(name)
        for name in _BASE_EDGE_NAMES
        if name != "id"
    }
    kwargs["id"] = strawberry.ID(str(record.properties["id"]))
    kwargs["from_id"] = strawberry.ID(record.from_id)
    kwargs["to_id"] = strawberry.ID(record.to_id)
    for spec in _declared_specs(cls):
        kwargs[python_name(spec)] = record.properties.get(spec)
    return cls(**kwargs)  # type: ignore[no-any-return]


_DECLARED_CACHE: dict[type, tuple[str, ...]] = {}


def _declared_specs(cls: type) -> tuple[str, ...]:
    cached = _DECLARED_CACHE.get(cls)
    if cached is not None:
        return cached
    from ...ontology import edge_type, node_type

    declared_node = node_type(cls.__name__)
    declared_edge = edge_type(cls.__name__)
    names = tuple(
        (declared_node or declared_edge).declared_property_names  # type: ignore[union-attr]
    )
    _DECLARED_CACHE[cls] = names
    return names


ALL_TYPES: list[type] = [*NODE_TYPES.values(), *EDGE_TYPES.values()]
