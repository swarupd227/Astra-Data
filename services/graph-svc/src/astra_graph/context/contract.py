"""What a context contract is: a named GraphQL fragment, a shape, and a budget.

S1.3.1: "a context contract is a named GraphQL fragment plus a serialiser".

**Why a fragment and not a list of field names.** The fragment is *validated against the
generated GraphQL schema* — when the first assembler is built, and by
``tools/contract_check.py`` in CI — so a contract that names a property the ontology does
not declare fails the build rather than producing a context with a missing key. The
schema is generated from the ontology registry (S1.1.2), which means the contract, the
API and the write path are all checked against one definition of what a CalculatedField
is. A hand-written list of strings would be checked against nothing.

The fragment also does the §18.3 work. Everything the agent sees is what the fragment
selects; a property that is not in the fragment cannot cross the inference boundary,
whatever the graph holds. That makes the boundary reviewable — an InfoSec reader is shown
one page of GraphQL rather than asked to trace a serialiser.

**Why the fragment is not the whole contract.** GraphQL cannot express "the transitive
DEPENDS_ON closure": a fragment selects fields from a shape somebody already navigated to.
So a contract is a fragment (which fields) plus a resolution plan (which nodes), and the
assembler runs the second and applies the first. Pretending otherwise would mean a
recursive fragment with a hard-coded depth, which is the same guess written less honestly.

**Budgets are declared, not discovered.** Each contract states the largest context it is
prepared to hand an agent. Exceeding it fails the call. Truncating would produce a
plausible, smaller, wrong context — an agent asked to transpile a calculation whose
dependency closure was silently cut would generate confidently from a partial picture, and
the provenance record would carry a hash of the truncated context as if it were the whole.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from enum import Enum
from functools import lru_cache
from typing import Any

from graphql import (
    FragmentDefinitionNode,
    GraphQLSchema,
    build_schema,
    parse,
    validate,
)
from graphql.language.ast import DocumentNode, FieldNode
from graphql.validation import NoUnusedFragmentsRule, specified_rules

from ..errors import AstraGraphError

#: Everything graphql-core checks, except that fragments must be used by an operation.
#: A contract's fragments *are* the document — there is no query for them to appear in, and
#: what uses them is a section declaration, which this module checks separately. Every other
#: rule stays: an unknown field, an unknown type or a bad selection all still fail.
_FRAGMENT_RULES = tuple(rule for rule in specified_rules if rule is not NoUnusedFragmentsRule)


class ContractName(str, Enum):
    """Contracts declared in code. One per agent, as that agent's epic specifies it.

    §4.1.3 specifies the Transpiler's in full, and that is the one that exists. The
    Compositor's shape is settled by E6, the Mender's by E8; declaring them here from the
    one-line summaries in the §8.3 catalogue would be guessing at input shapes that those
    epics exist to specify.

    **MODELLER_FAMILY is a name only — no ``ContextContract`` is registered for it in
    ``CONTRACTS`` (story S4.1.1).** The fragment-validated contract machinery exists to
    police what crosses the inference boundary to a real external model call (§18.3); the
    Modeller's naming and grain-statement drafting is, today, a deterministic template — no
    model gateway exists yet (§5.5, not built) for there to be a boundary to police. The
    label still appears on every ``ProvenanceRecord`` this drafting step writes, with a
    ``context_hash`` computed the same way (``context.canonical.canonical_json`` /
    ``context_hash``) over the gathered family evidence, so the record is still honest and
    reproducible — just not routed through ``ContextAssembler``. Once a real model call
    exists, register a full contract here and this deviation note can go.

    **TRANSPILER_C4_REDESIGN is the same "name only" deviation (story S5.4.1).** A C4
    redesign suggestion is a real, deterministic template composed from Appendix B's own
    guidance text and the calculation's own rule id — never a model call — so there is no
    inference boundary here either. Same reasoning as ``MODELLER_FAMILY``, a second time.
    """

    TRANSPILER_CALC = "transpiler_calc"
    MODELLER_FAMILY = "modeller_family"
    TRANSPILER_C4_REDESIGN = "transpiler_c4_redesign"


class ContractDefinitionError(Exception):
    """A contract is not well formed. Raised by the guard, so CI catches it."""


class ContextBudgetExceededError(AstraGraphError):
    """The assembled context is larger than the contract allows.

    413 rather than 500: the request is well formed and the service is healthy — this
    particular subject simply does not fit the contract it was asked for, and the caller
    is told by how much.
    """

    status_code = 413
    error_code = "context_budget_exceeded"

    def __init__(
        self, message: str, *, budget: Mapping[str, Any], actual: Mapping[str, Any]
    ) -> None:
        super().__init__(message)
        self.budget = dict(budget)
        self.actual = dict(actual)

    def payload(self) -> dict[str, Any]:
        return {
            "error": self.error_code,
            "message": str(self),
            "budget": self.budget,
            "actual": self.actual,
        }


@dataclass(frozen=True, slots=True)
class SectionSpec:
    """One named part of a contract's shape."""

    name: str
    description: str
    fragment: str
    """The GraphQL fragment that selects this section's fields."""

    spec_ref: str
    kind: str = "node"
    """``node`` or ``edge``. Some of what an agent needs is carried on a relationship
    rather than on either end of it — a source field's target column is a property of the
    MAPS_TO edge, because §4.1.1 declares no column node."""

    def __post_init__(self) -> None:
        if self.kind not in {"node", "edge"}:
            raise ContractDefinitionError(
                f"section '{self.name}' has kind {self.kind!r}; a section is over nodes "
                f"or over edges"
            )


@dataclass(frozen=True, slots=True)
class Budget:
    """The largest context this contract will hand an agent."""

    bytes: int
    nodes: int

    def __post_init__(self) -> None:
        if self.bytes < 1 or self.nodes < 1:
            raise ContractDefinitionError("a budget must be positive in both dimensions")


@dataclass(frozen=True, slots=True)
class ContextContract:
    """A named contract: what it takes, what it returns, and how big that may get."""

    name: ContractName
    version: str
    """Bumped whenever the shape changes. It is part of the hashed document, so a context
    assembled under a new version cannot be mistaken for one assembled under the old."""

    subject_type: str
    description: str
    spec_ref: str
    fragments: str
    """GraphQL fragment definitions, validated against the generated schema."""

    sections: tuple[SectionSpec, ...]
    budget: Budget

    def selection(self, fragment_name: str) -> tuple[str, ...]:
        """The field names one fragment selects, in declaration order."""
        return _selections(self.fragments)[fragment_name]

    def validate(self) -> None:
        """Check the fragments against the generated schema and the sections against them.

        Called by ``validate_registry``, so a contract that names a property the
        ontology does not have fails ``make ci`` rather than a caller.
        """
        document = _parse(self.fragments)
        errors = validate(_schema(), document, rules=_FRAGMENT_RULES)
        if errors:
            raise ContractDefinitionError(
                f"contract '{self.name.value}' has a fragment the GraphQL schema rejects: "
                + "; ".join(error.message for error in errors)
            )

        declared = _selections(self.fragments)
        for section in self.sections:
            if section.fragment not in declared:
                raise ContractDefinitionError(
                    f"contract '{self.name.value}' section '{section.name}' names fragment "
                    f"'{section.fragment}', which it does not define"
                )
        used = {section.fragment for section in self.sections}
        unused = set(declared) - used
        if unused:
            raise ContractDefinitionError(
                f"contract '{self.name.value}' defines fragment(s) "
                f"{', '.join(sorted(unused))} that no section uses. A contract's fragments "
                f"are its boundary; one nothing selects is either a mistake or dead."
            )


def serialise(record_properties: Mapping[str, Any], fields: Sequence[str]) -> dict[str, Any]:
    """One node, reduced to the fields its fragment selects.

    Absent optional properties are omitted rather than emitted as null. The graph does not
    distinguish "no value" from "property not written", so emitting null would assert
    something the graph never said — and it would make the hash depend on which optional
    properties the ontology happened to declare at the time.
    """
    return {name: record_properties[name] for name in fields if name in record_properties}


# --------------------------------------------------------------------------- internals


@lru_cache(maxsize=1)
def _schema() -> GraphQLSchema:
    """The generated schema, as graphql-core sees it.

    Built from the SDL that Strawberry prints rather than reaching into Strawberry's
    internals: the SDL is the published contract of the query API, and validating against
    it is validating against exactly what a client sees.
    """
    from ..api.graphql.schema import build_schema as build_strawberry_schema

    return build_schema(build_strawberry_schema().as_str())


def _parse(fragments: str) -> DocumentNode:
    try:
        return parse(fragments)
    except Exception as exc:
        raise ContractDefinitionError(f"a contract fragment does not parse: {exc}") from exc


@lru_cache(maxsize=32)
def _selections(fragments: str) -> dict[str, tuple[str, ...]]:
    """Field names per fragment.

    Only flat selections are supported, and that is deliberate: a nested selection would
    imply the assembler can navigate the graph from inside a fragment, which is exactly
    the thing GraphQL cannot express here. The traversal is the resolution plan's.
    """
    out: dict[str, tuple[str, ...]] = {}
    for definition in _parse(fragments).definitions:
        if not isinstance(definition, FragmentDefinitionNode):
            raise ContractDefinitionError(
                "a contract's fragments block may contain only fragment definitions"
            )
        names: list[str] = []
        for selection in definition.selection_set.selections:
            if not isinstance(selection, FieldNode):
                raise ContractDefinitionError(
                    f"fragment '{definition.name.value}' uses a selection that is not a "
                    f"plain field; contract fragments are flat by design"
                )
            if selection.selection_set is not None:
                raise ContractDefinitionError(
                    f"fragment '{definition.name.value}' selects into '"
                    f"{selection.name.value}'; contract fragments are flat by design, and "
                    f"traversal belongs to the resolution plan"
                )
            names.append(selection.name.value)
        if len(set(names)) != len(names):
            raise ContractDefinitionError(
                f"fragment '{definition.name.value}' selects a field twice"
            )
        out[definition.name.value] = tuple(names)
    if not out:
        raise ContractDefinitionError("a contract must define at least one fragment")
    return out


__all__ = [
    "Budget",
    "ContextBudgetExceededError",
    "ContextContract",
    "ContractDefinitionError",
    "ContractName",
    "SectionSpec",
    "serialise",
]
