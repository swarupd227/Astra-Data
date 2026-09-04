"""Ontology snapshot and change classification.

S1.1.1 criterion 4: "Schema changes are versioned migrations; a migration that removes a
property fails CI unless it also supplies a backfill."

``ontology.lock.json`` is the committed snapshot of the schema as last migrated. The
migration guard compares the live schema against it and classifies each difference as
either *additive* (safe) or *breaking* (needs a migration that declares a backfill).

Breaking, for this purpose, means a change that can invalidate data already in the graph:

* a node or edge type removed
* a property removed
* an optional property made required, or a required property added
* a property's type changed
* a value removed from an enum
* a node type's side changed, or an edge type's endpoint pair withdrawn
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .registry import SCHEMA_VERSION, sorted_edge_types, sorted_node_types
from .types import BASE_EDGE_PROPERTIES, BASE_NODE_PROPERTIES, PropertySpec

LOCK_FILENAME = "ontology.lock.json"


def _property_snapshot(spec: PropertySpec) -> dict[str, Any]:
    return {
        "name": spec.name,
        "type": spec.type.value,
        "required": spec.required,
        "enum": list(spec.enum) if spec.enum else None,
        "server_managed": spec.server_managed,
    }


def snapshot() -> dict[str, Any]:
    """The current schema, in the shape the lock file stores."""
    return {
        "schema_version": SCHEMA_VERSION,
        "base": {
            "node": [_property_snapshot(p) for p in BASE_NODE_PROPERTIES],
            "edge": [_property_snapshot(p) for p in BASE_EDGE_PROPERTIES],
        },
        "nodes": {
            node.label: {
                "side": node.side.value if node.side else None,
                "properties": [_property_snapshot(p) for p in node.properties],
            }
            for node in sorted_node_types()
        },
        "edges": {
            edge.label: {
                "pairs": [list(pair) for pair in edge.pairs],
                "properties": [_property_snapshot(p) for p in edge.properties],
            }
            for edge in sorted_edge_types()
        },
    }


def write_lock(path: Path) -> None:
    path.write_text(json.dumps(snapshot(), indent=2, sort_keys=True) + "\n", encoding="utf-8")


def read_lock(path: Path) -> dict[str, Any]:
    loaded: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
    return loaded


@dataclass(frozen=True, slots=True)
class Change:
    """One difference between the locked schema and the live schema."""

    kind: str
    element_key: str
    """Stable machine identifier, e.g. ``node:Workbook`` or ``base:node``."""

    element_label: str
    """Human-readable form used in messages."""

    property: str | None
    detail: str
    breaking: bool

    def key(self) -> str:
        """The identifier a migration declares in order to claim this change."""
        if self.property is None:
            return f"{self.kind}:{self.element_key}"
        return f"{self.kind}:{self.element_key}.{self.property}"

    def describe(self) -> str:
        severity = "breaking" if self.breaking else "additive"
        return f"[{severity}] {self.key()} — {self.detail}"


def _index(properties: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    return {p["name"]: p for p in properties}


def _diff_properties(
    element_key: str,
    element_label: str,
    locked: list[dict[str, Any]],
    live: list[dict[str, Any]],
) -> list[Change]:
    changes: list[Change] = []
    locked_index = _index(locked)
    live_index = _index(live)

    def change(kind: str, prop: str | None, detail: str, *, breaking: bool) -> Change:
        return Change(kind, element_key, element_label, prop, detail, breaking)

    for name, was in locked_index.items():
        now = live_index.get(name)
        if now is None:
            changes.append(
                change("remove_property", name,
                       f"property '{name}' was removed from {element_label}", breaking=True)
            )
            continue
        if was["type"] != now["type"]:
            changes.append(
                change("change_property_type", name,
                       f"property '{name}' on {element_label} changed type from "
                       f"{was['type']} to {now['type']}", breaking=True)
            )
        if not was["required"] and now["required"]:
            changes.append(
                change("require_property", name,
                       f"property '{name}' on {element_label} became required", breaking=True)
            )
        if was["required"] and not now["required"]:
            changes.append(
                change("relax_property", name,
                       f"property '{name}' on {element_label} became optional", breaking=False)
            )
        was_enum, now_enum = was.get("enum"), now.get("enum")
        if was_enum and now_enum:
            dropped = [v for v in was_enum if v not in now_enum]
            if dropped:
                changes.append(
                    change("remove_enum_value", name,
                           f"property '{name}' on {element_label} dropped enum value(s) "
                           f"{', '.join(dropped)}", breaking=True)
                )
        elif was_enum and not now_enum:
            changes.append(
                change("remove_enum", name,
                       f"property '{name}' on {element_label} is no longer an enum",
                       breaking=True)
            )

    for name, now in live_index.items():
        if name in locked_index:
            continue
        if now["required"]:
            changes.append(
                change("require_property", name,
                       f"property '{name}' was added to {element_label} as required, so "
                       f"elements already in the graph have no value for it", breaking=True)
            )
        else:
            changes.append(
                change("add_property", name,
                       f"property '{name}' was added to {element_label}", breaking=False)
            )
    return changes


def diff(locked: dict[str, Any]) -> list[Change]:
    """Classify every difference between the locked snapshot and the live schema."""
    live = snapshot()
    changes: list[Change] = []

    changes += _diff_properties(
        "base:node", "the base node properties", locked["base"]["node"], live["base"]["node"]
    )
    changes += _diff_properties(
        "base:edge", "the base edge properties", locked["base"]["edge"], live["base"]["edge"]
    )

    for prefix, collection, noun in (("node", "nodes", "node type"), ("edge", "edges", "edge type")):
        locked_elements: dict[str, Any] = locked[collection]
        live_elements: dict[str, Any] = live[collection]

        for label in locked_elements:
            if label not in live_elements:
                changes.append(
                    Change(f"remove_{prefix}_type", f"{prefix}:{label}", f"{noun} '{label}'",
                           None, f"{noun} '{label}' was removed", breaking=True)
                )
        for label, definition in live_elements.items():
            element_key = f"{prefix}:{label}"
            element_label = f"{noun} '{label}'"
            if label not in locked_elements:
                changes.append(
                    Change(f"add_{prefix}_type", element_key, element_label, None,
                           f"{noun} '{label}' was added", breaking=False)
                )
                continue
            was = locked_elements[label]
            changes += _diff_properties(
                element_key, element_label, was["properties"], definition["properties"]
            )
            if prefix == "node" and was.get("side") != definition.get("side"):
                changes.append(
                    Change("change_side", element_key, element_label, None,
                           f"{noun} '{label}' changed side from {was.get('side')} to "
                           f"{definition.get('side')}", breaking=True)
                )
            if prefix == "edge":
                was_pairs = {tuple(p) for p in was["pairs"]}
                now_pairs = {tuple(p) for p in definition["pairs"]}
                dropped = was_pairs - now_pairs
                if dropped:
                    changes.append(
                        Change("remove_edge_pair", element_key, element_label, None,
                               f"{noun} '{label}' no longer permits "
                               + ", ".join(f"{a}→{b}" for a, b in sorted(dropped)),
                               breaking=True)
                    )
    return changes


def breaking(changes: list[Change]) -> list[Change]:
    return [change for change in changes if change.breaking]
