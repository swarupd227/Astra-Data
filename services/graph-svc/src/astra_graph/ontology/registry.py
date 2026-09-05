"""The ontology registry: lookup over the declared node and edge types.

Import this rather than ``nodes.py``/``edges.py`` directly. The registry is built once at
import and validated for internal consistency, so a malformed declaration fails at
start-up rather than on the first write that happens to touch it.
"""

from __future__ import annotations

from functools import lru_cache

from .edges import EDGE_SPEC_DEVIATIONS, EDGE_TYPES
from .nodes import NODE_SPEC_DEVIATIONS, NODE_TYPES
from .types import ANY_LABEL, EdgeType, NodeType, SpecDeviation

#: Bumped whenever the ontology changes. The lock file records the version the committed
#: schema was locked at; the migration guard uses it to name the migration it expects.
SCHEMA_VERSION = 19


class OntologyDeclarationError(Exception):
    """The declared ontology is internally inconsistent. Raised at import."""


def _build_node_index() -> dict[str, NodeType]:
    index: dict[str, NodeType] = {}
    for node_type in NODE_TYPES:
        if node_type.label in index:
            raise OntologyDeclarationError(f"node type {node_type.label!r} declared twice")
        index[node_type.label] = node_type
    return index


def _build_edge_index(node_labels: frozenset[str]) -> dict[str, EdgeType]:
    index: dict[str, EdgeType] = {}
    for edge_type in EDGE_TYPES:
        if edge_type.label in index:
            raise OntologyDeclarationError(f"edge type {edge_type.label!r} declared twice")
        for from_label, to_label in edge_type.pairs:
            for label in (from_label, to_label):
                if label != ANY_LABEL and label not in node_labels:
                    raise OntologyDeclarationError(
                        f"edge type {edge_type.label!r} references unknown node type {label!r}"
                    )
            if to_label == ANY_LABEL:
                # A wildcard target would make the endpoint check meaningless in the
                # direction that matters; the specification only wildcards the source.
                raise OntologyDeclarationError(
                    f"edge type {edge_type.label!r} may not wildcard its target"
                )
        index[edge_type.label] = edge_type
    return index


NODE_INDEX: dict[str, NodeType] = _build_node_index()
NODE_LABELS: frozenset[str] = frozenset(NODE_INDEX)
EDGE_INDEX: dict[str, EdgeType] = _build_edge_index(NODE_LABELS)
EDGE_LABELS: frozenset[str] = frozenset(EDGE_INDEX)

SPEC_DEVIATIONS: tuple[SpecDeviation, ...] = NODE_SPEC_DEVIATIONS + EDGE_SPEC_DEVIATIONS


def node_type(label: str) -> NodeType | None:
    return NODE_INDEX.get(label)


def edge_type(label: str) -> EdgeType | None:
    return EDGE_INDEX.get(label)


def require_node_type(label: str) -> NodeType:
    found = NODE_INDEX.get(label)
    if found is None:
        raise KeyError(f"unknown node type {label!r}")
    return found


def require_edge_type(label: str) -> EdgeType:
    found = EDGE_INDEX.get(label)
    if found is None:
        raise KeyError(f"unknown edge type {label!r}")
    return found


@lru_cache(maxsize=1)
def sorted_node_types() -> tuple[NodeType, ...]:
    """Node types in declaration order — source, then both-sides, then target, then
    platform — which is the order the specification's table uses."""
    return NODE_TYPES


@lru_cache(maxsize=1)
def sorted_edge_types() -> tuple[EdgeType, ...]:
    return EDGE_TYPES


def suggest_label(label: str, *, among: frozenset[str]) -> str | None:
    """Closest known label to ``label``, for a 'did you mean' in a rejection message.

    Case-insensitive exact match first, since ``workbook`` for ``Workbook`` is the common
    mistake; otherwise nothing, because a guess that is wrong is worse than no guess.
    """
    lowered = label.lower()
    for known in among:
        if known.lower() == lowered:
            return known
    return None
