#!/usr/bin/env python
"""Context contract guard.

S1.3.1: a context contract is "a named GraphQL fragment plus a serialiser". This is what
makes the first half of that sentence mean something — every declared contract is checked
against the GraphQL schema generated from the ontology, so a contract naming a property
the ontology does not have fails CI rather than producing a context with a missing key.

It runs here rather than at import time because the schema being validated against needs
``ContractName`` to declare its own enum, and a contract that validated itself while being
imported would close that circle. The assembler validates once on construction too, so a
deployment cannot start with a malformed contract even if this guard were skipped.

Prints what each contract sends across the inference boundary (§18.3), because that list
is the thing a reviewer actually wants to see and a diff of it is the thing a reviewer
actually wants to review.
"""

from __future__ import annotations

import sys
from pathlib import Path

# The specification's own punctuation reaches this output through contract descriptions.
for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        _stream.reconfigure(encoding="utf-8", errors="replace")

SERVICE_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SERVICE_ROOT / "src"))

from astra_graph.context import (  # noqa: E402
    CONTRACTS,
    ContractDefinitionError,
    describe,
    validate_registry,
)


def main() -> int:
    try:
        validate_registry(force=True)
    except ContractDefinitionError as exc:
        print(f"Contract check FAILED: {exc}", file=sys.stderr)
        return 1

    for contract in describe():
        fields = sum(len(section["fields"]) for section in contract["sections"])
        print(
            f"{contract['name']} v{contract['version']} "
            f"({contract['subject_type']}, {contract['spec_ref']}): "
            f"{len(contract['sections'])} sections, {fields} fields, "
            f"budget {contract['budget']['bytes']} bytes / {contract['budget']['nodes']} nodes"
        )
        for section in contract["sections"]:
            print(f"    {section['name']:<24} {' '.join(section['fields'])}")

    print(f"Contract check passed: {len(CONTRACTS)} contract(s) validated against the schema.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
