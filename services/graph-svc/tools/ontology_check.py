#!/usr/bin/env python
"""Ontology drift guards.

Two checks, both run in CI (S1.1.1 criterion 3 — "the ontology table in the spec is
generated from the schema in CI, so the two cannot drift"):

``--generated``
    Regenerate ``docs/generated/ontology.md`` from the schema and fail if the committed
    file differs. ``--write`` updates it instead of failing.

``--spec``
    Parse the ontology tables out of the Product Specification and compare them against
    the schema: the same node labels, the same edge labels, and the same property names
    on each. Any difference must appear in the schema's declared deviations, each of
    which carries a reason. An undeclared difference fails.

The second check is the one that matters. Generating a document from the schema proves
the document matches the code; comparing against the specification proves the code
matches the product.
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

# Output carries the specification's own punctuation — em dashes, and arrows in edge
# endpoint pairs. A console on a legacy code page cannot encode those, and a guard that
# crashes while reporting a difference is worse than no guard.
for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        _stream.reconfigure(encoding="utf-8", errors="replace")


REPO_ROOT = Path(__file__).resolve().parents[3]
SERVICE_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SERVICE_ROOT / "src"))

from astra_graph.ontology import (  # noqa: E402
    BASE_EDGE_PROPERTIES,
    BASE_NODE_PROPERTIES,
    SPEC_DEVIATIONS,
    sorted_edge_types,
    sorted_node_types,
)
from astra_graph.ontology.render import render_markdown  # noqa: E402

GENERATED_PATH = REPO_ROOT / "docs" / "generated" / "ontology.md"
SPEC_PATH = (
    REPO_ROOT
    / "docs"
    / "reference"
    / "Astra-Data-Migration-Accelerator-Product-Spec-v1.0.md"
)

_NODE_SECTION = "### 4.1.1 Ontology — node types"
_EDGE_SECTION = "### 4.1.2 Ontology — edge types"

#: Property-name aliases: the specification abbreviates some names. Each is a declared
#: deviation with a reason; this map tells the checker what the abbreviation stands for.
_SPEC_PROPERTY_EXPANSIONS: dict[str, tuple[str, ...]] = {
    "actual_*": ("actual_start", "actual_end"),
}

#: Rows in the specification tables that declare two types on one line.
_SPEC_ROW_SPLITS: dict[str, tuple[str, ...]] = {
    "ReleaseTrain / Wave": ("ReleaseTrain", "Wave"),
    "OWNED_BY / VIEWED_BY": ("OWNED_BY", "VIEWED_BY"),
}


class DriftError(Exception):
    pass


# ------------------------------------------------------------------ spec table parsing


_EXPECTED_COLUMNS = 4


def _read_table(text: str, heading: str) -> list[list[str]]:
    """Rows of the first ``[TABLE]`` block after ``heading``, header row dropped.

    Both ontology tables have four columns, and the third holds the property list. That
    list contains pipe characters — ``type (embedded|published)`` — which a naive split
    turns into extra columns. Any surplus cells therefore belong to column three and are
    rejoined.
    """
    start = text.find(heading)
    if start < 0:
        raise DriftError(f"could not find {heading!r} in {SPEC_PATH}")
    block_start = text.find("[TABLE]", start)
    block_end = text.find("[/TABLE]", block_start)
    if block_start < 0 or block_end < 0:
        raise DriftError(f"no table block under {heading!r}")

    rows: list[list[str]] = []
    for line in text[block_start + len("[TABLE]") : block_end].splitlines():
        line = line.strip()
        if not line.startswith("|"):
            continue
        cells = [cell.strip() for cell in line.strip("|").split("|")]
        if len(cells) > _EXPECTED_COLUMNS:
            cells = [cells[0], cells[1], "|".join(cells[2:-1]), cells[-1]]
        rows.append(cells)
    return rows[1:]  # drop the header row


def _split_labels(cell: str) -> tuple[str, ...]:
    cell = cell.strip().strip("*")
    if cell in _SPEC_ROW_SPLITS:
        return _SPEC_ROW_SPLITS[cell]
    return (cell,)


def _parse_property_names(cell: str) -> set[str]:
    """Property names from a specification 'Key properties' cell.

    The cell is prose: ``type (embedded|published)``, ``contained_sheets[]``,
    ``class (C1..C4, set by Transpiler)``, ``actual_*``. Strip the annotations, keep the
    names.
    """
    # Remove parenthesised annotations first: they may contain commas, as in
    # `class (C1..C4, set by Transpiler)`.
    cleaned = re.sub(r"\([^()]*\)", "", cell)
    names: set[str] = set()
    for raw in cleaned.split(","):
        fragment = raw.strip()
        if not fragment or fragment in {"—", "-"}:
            continue
        # Take the leading identifier. A fragment can carry a trailing qualifier, as in
        # `join_clause on Table edges`; the property is the identifier that starts it.
        match = re.match(r"^([A-Za-z_][A-Za-z0-9_]*\*?)", fragment)
        if not match:
            continue
        token = match.group(1)
        if token in _SPEC_PROPERTY_EXPANSIONS:
            names.update(_SPEC_PROPERTY_EXPANSIONS[token])
        else:
            names.add(token)
    return names


def _spec_nodes(text: str) -> dict[str, set[str]]:
    out: dict[str, set[str]] = {}
    for row in _read_table(text, _NODE_SECTION):
        if len(row) < 3:
            continue
        properties = _parse_property_names(row[2])
        for label in _split_labels(row[0]):
            out[label] = set(properties)
    return out


def _spec_edges(text: str) -> dict[str, set[str]]:
    out: dict[str, set[str]] = {}
    for row in _read_table(text, _EDGE_SECTION):
        if len(row) < 3:
            continue
        properties = _parse_property_names(row[2])
        for label in _split_labels(row[0]):
            out[label] = set(properties)
    return out


# ----------------------------------------------------------------------------- checks


def check_generated(*, write: bool) -> list[str]:
    rendered = render_markdown()
    GENERATED_PATH.parent.mkdir(parents=True, exist_ok=True)
    if write:
        GENERATED_PATH.write_text(rendered, encoding="utf-8", newline="\n")
        print(f"wrote {GENERATED_PATH.relative_to(REPO_ROOT)}")
        return []
    if not GENERATED_PATH.exists():
        return [
            f"{GENERATED_PATH.relative_to(REPO_ROOT)} does not exist. "
            f"Run: python tools/ontology_check.py --generated --write"
        ]
    committed = GENERATED_PATH.read_text(encoding="utf-8")
    if committed.replace("\r\n", "\n") != rendered:
        return [
            f"{GENERATED_PATH.relative_to(REPO_ROOT)} is out of date with the schema. "
            f"Run: python tools/ontology_check.py --generated --write"
        ]
    return []


def check_spec() -> list[str]:
    if not SPEC_PATH.exists():
        return [f"specification not found at {SPEC_PATH}"]
    text = SPEC_PATH.read_text(encoding="utf-8")

    declared = {deviation.element for deviation in SPEC_DEVIATIONS}
    declared_atoms: set[str] = set()
    for element in declared:
        declared_atoms.update(part.strip() for part in element.split(","))

    problems: list[str] = []

    def is_declared(*candidates: str) -> bool:
        return any(candidate in declared_atoms for candidate in candidates)

    for kind, spec_table, schema_types in (
        ("node", _spec_nodes(text), sorted_node_types()),
        ("edge", _spec_edges(text), sorted_edge_types()),
    ):
        schema_labels = {t.label for t in schema_types}
        spec_labels = set(spec_table)

        for label in sorted(spec_labels - schema_labels):
            if not is_declared(label):
                problems.append(
                    f"{kind} type '{label}' is in the specification but not in the schema, "
                    f"and is not a declared deviation"
                )
        for label in sorted(schema_labels - spec_labels):
            if not is_declared(label):
                problems.append(
                    f"{kind} type '{label}' is in the schema but not in the specification, "
                    f"and is not a declared deviation"
                )

        base_names = {
            p.name for p in (BASE_NODE_PROPERTIES if kind == "node" else BASE_EDGE_PROPERTIES)
        }
        for declared_type in schema_types:
            expected = spec_table.get(declared_type.label)
            if expected is None:
                continue
            actual = set(declared_type.declared_property_names) | base_names
            for name in sorted(expected - actual):
                if not is_declared(f"{declared_type.label}.{name}"):
                    problems.append(
                        f"{kind} type '{declared_type.label}': the specification declares "
                        f"property '{name}' and the schema does not"
                    )
            for name in sorted(actual - expected - base_names):
                if not is_declared(f"{declared_type.label}.{name}"):
                    problems.append(
                        f"{kind} type '{declared_type.label}': the schema declares property "
                        f"'{name}' and the specification does not"
                    )
    return problems


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--generated", action="store_true", help="check the generated reference")
    parser.add_argument("--spec", action="store_true", help="check the schema against the spec")
    parser.add_argument("--write", action="store_true", help="rewrite the generated reference")
    args = parser.parse_args()

    if not args.generated and not args.spec:
        args.generated = args.spec = True

    problems: list[str] = []
    if args.generated:
        problems += check_generated(write=args.write)
    if args.spec:
        problems += check_spec()

    if problems:
        print("Ontology drift:\n", file=sys.stderr)
        for problem in problems:
            print(f"  - {problem}", file=sys.stderr)
        print(
            "\nEvery difference between the specification and the schema must be a declared "
            "deviation with a reason (see SPEC_DEVIATIONS in ontology/nodes.py and "
            "ontology/edges.py).",
            file=sys.stderr,
        )
        return 1

    print(
        f"Ontology check passed: {len(sorted_node_types())} node types, "
        f"{len(sorted_edge_types())} edge types, {len(SPEC_DEVIATIONS)} declared deviations."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
