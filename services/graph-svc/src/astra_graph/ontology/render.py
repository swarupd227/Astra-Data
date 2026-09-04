"""Render the ontology reference from the schema.

S1.1.1 criterion 3: "the ontology table in the spec is generated from the schema in CI, so
the two cannot drift". The generated file is committed; CI regenerates it and fails on any
difference, and separately checks the schema against the specification's own tables.
"""

from __future__ import annotations

from .registry import SCHEMA_VERSION, SPEC_DEVIATIONS, sorted_edge_types, sorted_node_types
from .types import BASE_EDGE_PROPERTIES, BASE_NODE_PROPERTIES, PropertySpec

GENERATED_HEADER = (
    "<!-- Generated from services/graph-svc/src/astra_graph/ontology by "
    "`make ontology`. Do not edit by hand; CI fails if this file and the schema differ. -->"
)


def _property_cell(spec: PropertySpec) -> str:
    marks = [spec.render_type()]
    if spec.required:
        marks.append("required")
    if spec.server_managed:
        marks.append("server-set")
    return f"`{spec.name}` <br> {', '.join(marks)}"


def _escape(text: str) -> str:
    return text.replace("|", "\\|").replace("\n", " ").strip()


def _property_table(specs: tuple[PropertySpec, ...]) -> list[str]:
    lines = ["| Property | Type | Required | Notes |", "|---|---|---|---|"]
    for spec in specs:
        lines.append(
            f"| `{spec.name}` | {spec.render_type()} | "
            f"{'yes' if spec.required else 'no'}"
            f"{' (server-set)' if spec.server_managed else ''} | {_escape(spec.note)} |"
        )
    return lines


def render_markdown() -> str:
    """The full ontology reference."""
    out: list[str] = [
        GENERATED_HEADER,
        "",
        "# Estate Graph ontology",
        "",
        f"Schema version **{SCHEMA_VERSION}**. "
        f"{len(sorted_node_types())} node types, {len(sorted_edge_types())} edge types.",
        "",
        "Transcribed from Product Specification v1.0 §4.1.1 (nodes) and §4.1.2 (edges) and "
        "enforced at write time by `graph-svc`. Where this file and the specification differ, "
        "the difference is declared under [Declared deviations](#declared-deviations) — an "
        "undeclared difference fails CI.",
        "",
        "## Base properties",
        "",
        "Every node carries these regardless of type:",
        "",
    ]
    out += _property_table(BASE_NODE_PROPERTIES)
    out += ["", "Every edge carries these regardless of type:", ""]
    out += _property_table(BASE_EDGE_PROPERTIES)

    out += ["", "## Node types", "", "| Node | Side | Spec | Properties |", "|---|---|---|---|"]
    for node in sorted_node_types():
        side = node.side.value if node.side is not None else "source \\| target (declared per node)"
        props = " <br> ".join(_property_cell(p) for p in node.properties) or "—"
        out.append(f"| **{node.label}** | {side} | {node.spec_ref} | {props} |")

    for node in sorted_node_types():
        out += ["", f"### {node.label}", ""]
        if node.note:
            out += [_escape(node.note), ""]
        out += _property_table(node.properties) if node.properties else ["No type-specific properties."]

    out += [
        "",
        "## Edge types",
        "",
        "| Edge | Permitted endpoints | Written by | Spec | Properties |",
        "|---|---|---|---|---|",
    ]
    for edge in sorted_edge_types():
        props = " <br> ".join(_property_cell(p) for p in edge.properties) or "—"
        out.append(
            f"| **{edge.label}** | {_escape(edge.render_pairs())} | {_escape(edge.written_by)} | "
            f"{edge.spec_ref} | {props} |"
        )

    for edge in sorted_edge_types():
        out += ["", f"### {edge.label}", ""]
        out += [f"Endpoints: {_escape(edge.render_pairs())}. Written by {_escape(edge.written_by)}.", ""]
        if edge.note:
            out += [_escape(edge.note), ""]
        out += _property_table(edge.properties) if edge.properties else ["No type-specific properties."]

    out += [
        "",
        "## Declared deviations",
        "",
        "Differences between the specification's tables and this schema, each with its reason. "
        "`tools/ontology_check.py --spec` fails on any difference not listed here.",
        "",
        "| Element | Why the specification differs | Decision |",
        "|---|---|---|",
    ]
    for deviation in SPEC_DEVIATIONS:
        out.append(
            f"| {_escape(deviation.element)} | {_escape(deviation.reason)} | "
            f"{_escape(deviation.detail)} |"
        )

    out.append("")
    return "\n".join(out)
