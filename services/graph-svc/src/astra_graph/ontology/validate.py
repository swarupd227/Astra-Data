"""Write-time enforcement of the ontology.

S1.1.1: "a write with an unknown type or a missing required property is rejected with a
422 and a message naming the property".

The validator collects *every* violation in a submission rather than stopping at the
first. A harvest writes thousands of nodes; an adapter author fixing one property per
round trip is the difference between a morning and a week.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from .properties import PropertySpec, PropertyValueError, coerce
from .registry import (
    EDGE_LABELS,
    NODE_LABELS,
    edge_type,
    node_type,
    suggest_label,
)
from .types import EdgeType, NodeType, Side


class ViolationCode(str, Enum):
    UNKNOWN_NODE_TYPE = "unknown_node_type"
    UNKNOWN_EDGE_TYPE = "unknown_edge_type"
    UNKNOWN_PROPERTY = "unknown_property"
    MISSING_REQUIRED_PROPERTY = "missing_required_property"
    INVALID_PROPERTY_VALUE = "invalid_property_value"
    SERVER_MANAGED_PROPERTY = "server_managed_property"
    INVALID_SIDE = "invalid_side"
    INVALID_EDGE_ENDPOINTS = "invalid_edge_endpoints"
    UNKNOWN_ENDPOINT_NODE = "unknown_endpoint_node"


@dataclass(frozen=True, slots=True)
class Violation:
    """One reason a write was rejected."""

    code: ViolationCode
    message: str
    property: str | None = None
    element_type: str | None = None
    index: int | None = None
    """Position in a batch write, so a caller can locate the offending element."""

    def with_index(self, index: int) -> Violation:
        return Violation(
            code=self.code,
            message=self.message,
            property=self.property,
            element_type=self.element_type,
            index=index,
        )

    def as_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {"code": self.code.value, "message": self.message}
        if self.property is not None:
            out["property"] = self.property
        if self.element_type is not None:
            out["type"] = self.element_type
        if self.index is not None:
            out["index"] = self.index
        return out


@dataclass(slots=True)
class ValidationResult:
    """Outcome of validating one element."""

    violations: list[Violation] = field(default_factory=list)
    properties: dict[str, Any] = field(default_factory=dict)
    """Coerced, canonical properties. Only meaningful when ``ok`` is true."""

    @property
    def ok(self) -> bool:
        return not self.violations


def _validate_properties(
    *,
    element_type: str,
    specs: tuple[PropertySpec, ...],
    submitted: Mapping[str, Any],
    server_supplied: Mapping[str, Any],
) -> ValidationResult:
    """Validate one element's properties against its declared specs.

    ``submitted`` comes from the client; ``server_supplied`` holds values the service
    owns. A client that submits a server-managed property is rejected rather than
    silently overridden — a harvest that thinks it set ``created_by`` and did not would
    produce provenance nobody can trust.
    """
    result = ValidationResult()
    by_name = {spec.name: spec for spec in specs}

    for name in submitted:
        spec = by_name.get(name)
        if spec is None:
            result.violations.append(
                Violation(
                    code=ViolationCode.UNKNOWN_PROPERTY,
                    message=(
                        f"property '{name}' is not declared on {element_type}. "
                        f"Declared properties: {', '.join(sorted(by_name))}."
                    ),
                    property=name,
                    element_type=element_type,
                )
            )
            continue
        if spec.server_managed:
            result.violations.append(
                Violation(
                    code=ViolationCode.SERVER_MANAGED_PROPERTY,
                    message=(
                        f"property '{name}' on {element_type} is set by the service and must "
                        f"not be supplied by the caller."
                    ),
                    property=name,
                    element_type=element_type,
                )
            )

    # Nulls are the absence of a value, not a value: strip them, then let the required
    # check report the ones that mattered.
    values: dict[str, Any] = {
        name: value
        for name, value in submitted.items()
        if value is not None and name in by_name and not by_name[name].server_managed
    }
    values.update(server_supplied)

    for spec in specs:
        if spec.name not in values:
            if spec.required:
                result.violations.append(
                    Violation(
                        code=ViolationCode.MISSING_REQUIRED_PROPERTY,
                        message=(
                            f"property '{spec.name}' is required on {element_type} "
                            f"({spec.render_type()})."
                        ),
                        property=spec.name,
                        element_type=element_type,
                    )
                )
            continue
        try:
            result.properties[spec.name] = coerce(spec, values[spec.name])
        except PropertyValueError as exc:
            result.violations.append(
                Violation(
                    code=ViolationCode.INVALID_PROPERTY_VALUE,
                    message=f"on {element_type}, {exc.args[0]}",
                    property=spec.name,
                    element_type=element_type,
                )
            )

    return result


def validate_node(
    label: str,
    submitted: Mapping[str, Any],
    *,
    server_supplied: Mapping[str, Any],
) -> ValidationResult:
    """Validate one node write.

    ``server_supplied`` must carry id, created_by and created_at, and may carry
    created_in_run. ``side`` is taken from the node type unless the type spans both sides,
    in which case the caller must have supplied it.
    """
    declared: NodeType | None = node_type(label)
    if declared is None:
        suggestion = suggest_label(label, among=NODE_LABELS)
        hint = f" Did you mean '{suggestion}'?" if suggestion else ""
        return ValidationResult(
            violations=[
                Violation(
                    code=ViolationCode.UNKNOWN_NODE_TYPE,
                    message=f"'{label}' is not a node type in the ontology.{hint}",
                    element_type=label,
                )
            ]
        )

    effective_server: dict[str, Any] = dict(server_supplied)
    if declared.side is not None:
        # Side is a property of the type, not of the instance. Reject a contradicting
        # value rather than quietly correcting it.
        submitted_side = submitted.get("side")
        if submitted_side is not None and submitted_side != declared.side.value:
            return ValidationResult(
                violations=[
                    Violation(
                        code=ViolationCode.INVALID_SIDE,
                        message=(
                            f"property 'side' on {label} is fixed at '{declared.side.value}' "
                            f"by the ontology; got '{submitted_side}'."
                        ),
                        property="side",
                        element_type=label,
                    )
                ]
            )
        effective_server["side"] = declared.side.value

    filtered = {k: v for k, v in submitted.items() if k != "side" or declared.side is None}
    result = _validate_properties(
        element_type=f"node type '{label}'",
        specs=declared.all_properties,
        submitted=filtered,
        server_supplied=effective_server,
    )
    return result


def validate_edge(
    label: str,
    submitted: Mapping[str, Any],
    *,
    from_label: str | None,
    to_label: str | None,
    server_supplied: Mapping[str, Any],
) -> ValidationResult:
    """Validate one edge write.

    ``from_label`` and ``to_label`` are the labels of the endpoint nodes as they exist in
    the graph. ``None`` means the endpoint was not found; that is reported here so a
    caller gets one rejection listing everything wrong with the write.
    """
    declared: EdgeType | None = edge_type(label)
    if declared is None:
        suggestion = suggest_label(label, among=EDGE_LABELS)
        hint = f" Did you mean '{suggestion}'?" if suggestion else ""
        return ValidationResult(
            violations=[
                Violation(
                    code=ViolationCode.UNKNOWN_EDGE_TYPE,
                    message=f"'{label}' is not an edge type in the ontology.{hint}",
                    element_type=label,
                )
            ]
        )

    result = _validate_properties(
        element_type=f"edge type '{label}'",
        specs=declared.all_properties,
        submitted=submitted,
        server_supplied=server_supplied,
    )

    # Both labels are None when the endpoint node does not exist; that is reported by the
    # caller, which knows which id was missing.
    endpoints_known = from_label is not None and to_label is not None
    if endpoints_known and not declared.permits(from_label, to_label):  # type: ignore[arg-type]
        result.violations.append(
            Violation(
                code=ViolationCode.INVALID_EDGE_ENDPOINTS,
                message=(
                    f"edge type '{label}' does not permit {from_label}→{to_label}. "
                    f"Permitted: {declared.render_pairs()}."
                ),
                element_type=label,
            )
        )
    return result


def side_for(label: str) -> Side | None:
    """The fixed side of a node type, or ``None`` if the writer must declare it."""
    declared = node_type(label)
    return declared.side if declared is not None else None
