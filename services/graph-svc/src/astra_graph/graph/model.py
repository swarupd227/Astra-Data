"""What a read returns.

Plain records rather than dicts, so the GraphQL layer and the tests agree on the shape
and mypy can check it. These are storage-facing: the GraphQL types in ``api/graphql`` are
generated from the ontology and built from these.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True, slots=True)
class NodeRecord:
    label: str
    properties: dict[str, Any]

    @property
    def id(self) -> str:
        return str(self.properties["id"])


@dataclass(frozen=True, slots=True)
class EdgeRecord:
    label: str
    properties: dict[str, Any]
    from_id: str
    to_id: str

    @property
    def id(self) -> str:
        return str(self.properties["id"])


@dataclass(frozen=True, slots=True)
class Neighbour:
    """A node reached by a traversal, with the length of the shortest path to it."""

    node: NodeRecord
    depth: int


@dataclass(slots=True)
class NeighbourhoodResult:
    anchor: NodeRecord
    depth: int
    neighbours: list[Neighbour] = field(default_factory=list)
    edges: list[EdgeRecord] = field(default_factory=list)
    truncated: bool = False
    """True when the traversal hit its element limit; the result is a prefix, not the
    whole neighbourhood, and a caller must not treat it as complete."""
