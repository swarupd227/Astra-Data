"""The context assembler.

S1.3.1: "each agent's context contract ... materialised by a shared assembler", returning
"a canonical JSON document and its sha256 (context_hash)", with "context size reported per
call" and "a contract that exceeds its declared budget fails the call rather than
truncating silently".

**Shared** is the load-bearing word. Every agent's context comes through this one object,
so the canonicalisation, the hashing, the size accounting and the budget refusal are
written once and cannot drift between agents. What differs per agent is its contract — the
fragments and the resolution plan — and nothing else.

The resolution plan is per contract because traversal is not something GraphQL can
express; see ``contract.py``. It lives here rather than in the contract module so that a
contract stays a declaration a reviewer can read in one screen, without the graph reads
mixed into it.

**Ordering is part of the answer.** Every collection is sorted by id before serialisation.
Without that, two calls over the same graph could hash differently because PostgreSQL
returned rows in a different order — which is exactly the failure the determinism
criterion exists to catch, and it would be intermittent.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any, Protocol

from ..errors import ElementNotFoundError, InvalidRequestError
from ..graph.model import EdgeRecord, Neighbour, NodeRecord
from .canonical import canonical_json, context_hash
from .contract import (
    ContextBudgetExceededError,
    ContextContract,
    ContractName,
    SectionSpec,
    serialise,
)
from .signature import SignatureError, ast_shape, matches
from .transpiler import CLOSURE_DEPTH, TRANSPILER_CALC

logger = logging.getLogger(__name__)

#: The registry. Contracts are validated against the generated GraphQL schema — once, the
#: first time an assembler is built, and again by ``tools/contract_check.py`` in CI. Not at
#: import time, because the schema this validates against needs ``ContractName`` to declare
#: its own enum, and a contract that validated itself while being imported would close that
#: circle. The guard is what makes a malformed contract fail the build rather than a caller.
CONTRACTS: dict[ContractName, ContextContract] = {
    ContractName.TRANSPILER_CALC: TRANSPILER_CALC,
}

#: Patterns are a library, not an estate — hundreds, not millions — so matching reads them
#: all and filters in the service. A bound rather than a page: a deployment that has more
#: patterns than this has a Pattern Library problem, and E5 owns paging when it does.
MAX_PATTERNS = 5_000


@dataclass(frozen=True, slots=True)
class AssembledContext:
    """A materialised contract: the document, its hash, and what it cost."""

    contract: ContractName
    version: str
    subject_id: str
    document: dict[str, Any]
    context_hash: str
    size_bytes: int
    node_count: int
    payload: bytes
    """The exact bytes that were hashed. An agent sends these; re-serialising the
    document could produce different bytes and a hash that no longer describes them."""

    def usage(self) -> dict[str, Any]:
        """Size against budget, for the caller's log and for Platform Health."""
        contract = CONTRACTS[self.contract]
        return {
            "size_bytes": self.size_bytes,
            "node_count": self.node_count,
            "budget_bytes": contract.budget.bytes,
            "budget_nodes": contract.budget.nodes,
            "bytes_used": round(self.size_bytes / contract.budget.bytes, 4),
            "nodes_used": round(self.node_count / contract.budget.nodes, 4),
        }


class ContextReader(Protocol):
    """The reads an assembler needs. Satisfied by the graph repository."""

    async def get_node_record(self, node_id: str) -> NodeRecord | None: ...

    async def closure(
        self, anchor_id: str, *, edge_type: str, depth: int, limit: int = ...
    ) -> list[Neighbour]: ...

    async def outgoing_edges(
        self, from_ids: Sequence[str], *, edge_type: str
    ) -> list[EdgeRecord]: ...

    async def get_nodes(self, ids: Sequence[str]) -> list[NodeRecord]: ...

    async def nodes_of_type(self, label: str, *, limit: int = ...) -> list[NodeRecord]: ...


class ContextAssembler:
    """Materialises a named contract for one subject."""

    def __init__(self, reader: ContextReader, *, adapter: str | None = None) -> None:
        validate_registry()
        self._reader = reader
        self._adapter = adapter
        """Which adapter's grammar produced the ASTs in this graph, when the deployment
        knows. Patterns declare the adapter their signature is written for (§4.3), and a
        Tableau pattern must not be offered for a Qlik calculation. A deployment with no
        adapter enabled matches on shape alone, which is right for the single-adapter
        tenant this is today."""

    async def assemble(self, name: ContractName, subject_id: str) -> AssembledContext:
        """Materialise a contract. Raises rather than truncating when it will not fit."""
        contract = CONTRACTS.get(name)
        if contract is None:
            # Reachable for a declared ContractName with no registered ContextContract —
            # MODELLER_FAMILY (S4.1.1) is the first one, and its own docstring says why.
            raise InvalidRequestError(
                f"'{name.value}' is a declared contract name with no registered "
                f"ContextContract — nothing can be assembled through this endpoint for it"
            )

        subject = await self._reader.get_node_record(subject_id)
        if subject is None:
            raise ElementNotFoundError(f"no node with id '{subject_id}'")
        if subject.label != contract.subject_type:
            raise InvalidRequestError(
                f"the {contract.name.value} contract takes a {contract.subject_type}; "
                f"'{subject_id}' is a {subject.label}"
            )

        resolved = await self._resolve(contract, subject)
        return self._materialise(contract, subject, resolved)

    # ------------------------------------------------------------ resolution plans

    async def _resolve(
        self, contract: ContextContract, subject: NodeRecord
    ) -> dict[str, list[NodeRecord] | list[EdgeRecord]]:
        if contract.name is ContractName.TRANSPILER_CALC:
            return await self._transpiler_calc(subject)
        raise InvalidRequestError(  # pragma: no cover - unreachable while one contract exists
            f"contract '{contract.name.value}' has no resolution plan"
        )

    async def _transpiler_calc(
        self, subject: NodeRecord
    ) -> dict[str, list[NodeRecord] | list[EdgeRecord]]:
        """§4.1.3's sentence, executed.

        The closure is read once and split by label rather than read once per label: one
        recursive query, and the three sections are guaranteed to be describing the same
        traversal.
        """
        closure = await self._reader.closure(
            subject.id,
            edge_type="DEPENDS_ON",
            depth=CLOSURE_DEPTH,
            # One over the node budget, so a closure that would blow the budget is
            # detected as too large rather than silently cut to the limit.
            limit=TRANSPILER_CALC.budget.nodes + 1,
        )
        # The closure includes its anchor. The subject is its own section, and repeating it
        # among its dependencies would say it depends on itself.
        reached = [n.node for n in closure if n.node.id != subject.id]

        dependency_fields = [n for n in reached if n.label == "Field"]
        dependency_calculations = [n for n in reached if n.label == "CalculatedField"]
        parameters = [n for n in reached if n.label == "Parameter"]

        # "the target ModelTable columns those fields MAPS_TO" — the subject's own mapping
        # is included: a calculated field that already maps to a measure tells the
        # Transpiler it is regenerating rather than generating.
        mappable = [subject.id, *(n.id for n in dependency_fields + dependency_calculations)]
        edges = await self._reader.outgoing_edges(mappable, edge_type="MAPS_TO")
        model_columns = [e for e in edges if e.properties.get("target_column")]
        table_ids = sorted({e.to_id for e in model_columns})
        model_tables = [
            n for n in await self._reader.get_nodes(table_ids) if n.label == "ModelTable"
        ]
        # A MAPS_TO edge can point at a Measure or a Visual too (§4.1.2). Only the ones
        # landing in a model table describe a column, so the others are not this contract's.
        wanted_tables = {n.id for n in model_tables}
        model_columns = [e for e in model_columns if e.to_id in wanted_tables]

        return {
            "dependency_fields": dependency_fields,
            "dependency_calculations": dependency_calculations,
            "parameters": parameters,
            "model_tables": model_tables,
            "model_columns": model_columns,
            "patterns": await self._patterns(subject),
        }

    async def _patterns(self, subject: NodeRecord) -> list[NodeRecord]:
        """Patterns whose ``source_signature`` matches the subject's AST shape (§4.3).

        A RETIRED pattern is excluded: it was withdrawn because it produced wrong output,
        and handing it to an agent as a candidate would invite exactly the failure it was
        retired for. CANDIDATE stays in — a candidate is a pattern accumulating evidence,
        and it cannot accumulate any if nothing is ever offered it.
        """
        try:
            shape = ast_shape(subject.properties.get("formula_ast"))
        except SignatureError as exc:
            # A shape that cannot be computed means no pattern can match, which is a true
            # and useful answer. Failing the whole context would deny the Transpiler a
            # calculation it could still translate from first principles.
            logger.warning(
                "no AST shape for calculated field %s, so no patterns match: %s",
                subject.id,
                exc,
            )
            return []

        candidates = await self._reader.nodes_of_type("Pattern", limit=MAX_PATTERNS)
        return [
            pattern
            for pattern in candidates
            if pattern.properties.get("promotion_state") != "RETIRED"
            and matches(
                pattern.properties.get("source_signature"),
                shape=shape,
                adapter=self._adapter,
            )
        ]

    # ------------------------------------------------------------- materialisation

    def _materialise(
        self,
        contract: ContextContract,
        subject: NodeRecord,
        resolved: dict[str, list[NodeRecord] | list[EdgeRecord]],
    ) -> AssembledContext:
        sections: dict[str, Any] = {}
        node_count = 0

        for section in contract.sections:
            fields = contract.selection(section.fragment)
            if section.name == "subject":
                sections[section.name] = serialise(subject.properties, fields)
                node_count += 1
                continue
            entries = resolved.get(section.name, [])
            sections[section.name] = [
                _entry(record, section, fields) for record in _ordered(entries)
            ]
            if section.kind == "node":
                node_count += len(entries)

        document = {
            "contract": {"name": contract.name.value, "version": contract.version},
            "subject_id": subject.id,
            **sections,
        }
        payload = canonical_json(document)
        size = len(payload)

        self._enforce(contract, size, node_count, subject)

        return AssembledContext(
            contract=contract.name,
            version=contract.version,
            subject_id=subject.id,
            document=document,
            context_hash=context_hash(payload),
            size_bytes=size,
            node_count=node_count,
            payload=payload,
        )

    def _enforce(
        self, contract: ContextContract, size: int, nodes: int, subject: NodeRecord
    ) -> None:
        """Fail the call rather than hand back a context that is over budget.

        Both dimensions are reported even when only one is breached, because the useful
        question on seeing this is "by how much, and in which direction".
        """
        over_bytes = size > contract.budget.bytes
        over_nodes = nodes > contract.budget.nodes
        if not (over_bytes or over_nodes):
            return

        breached = ", ".join(
            part
            for part, breach in (
                (f"{size} bytes against a budget of {contract.budget.bytes}", over_bytes),
                (f"{nodes} nodes against a budget of {contract.budget.nodes}", over_nodes),
            )
            if breach
        )
        logger.warning(
            "context for %s under contract %s exceeds its budget: %s",
            subject.id,
            contract.name.value,
            breached,
        )
        raise ContextBudgetExceededError(
            f"the {contract.name.value} context for '{subject.id}' is {breached}. The call "
            f"fails rather than returning a truncated context: an agent cannot tell a "
            f"shortened dependency closure from a complete one, and would generate "
            f"confidently from a partial picture.",
            budget={"bytes": contract.budget.bytes, "nodes": contract.budget.nodes},
            actual={"bytes": size, "nodes": nodes},
        )


def _ordered(records: Sequence[Any]) -> list[Any]:
    """By id, always. Two calls must not hash differently because rows came back in a
    different order."""
    return sorted(records, key=lambda record: record.id)


def _entry(record: Any, section: SectionSpec, fields: Sequence[str]) -> dict[str, Any]:
    if section.kind == "edge":
        # from_id and to_id are the edge's endpoints rather than entries in its property
        # bag, so they are merged in before the fragment selects.
        properties = {**record.properties, "from_id": record.from_id, "to_id": record.to_id}
        return serialise(properties, fields)
    return serialise(record.properties, fields)


def describe() -> list[dict[str, Any]]:
    """Every declared contract, as data. What ``GET /v1/contexts`` returns."""
    return [
        {
            "name": contract.name.value,
            "version": contract.version,
            "subject_type": contract.subject_type,
            "description": contract.description,
            "spec_ref": contract.spec_ref,
            "budget": {"bytes": contract.budget.bytes, "nodes": contract.budget.nodes},
            "sections": [
                {
                    "name": section.name,
                    "description": section.description,
                    "kind": section.kind,
                    "spec_ref": section.spec_ref,
                    "fields": list(contract.selection(section.fragment)),
                }
                for section in contract.sections
            ],
            "fragments": contract.fragments.strip(),
        }
        for contract in CONTRACTS.values()
    ]


_validated = False


def validate_registry(*, force: bool = False) -> None:
    """Check every declared contract against the generated GraphQL schema.

    Called when the first assembler is built, and by ``tools/contract_check.py`` so a
    contract naming a property the ontology does not declare fails ``make ci``. Memoised:
    the check is pure and the schema does not change while the process runs.
    """
    global _validated
    if _validated and not force:
        return
    for contract in CONTRACTS.values():
        contract.validate()
    _validated = True


__all__ = [
    "CONTRACTS",
    "MAX_PATTERNS",
    "AssembledContext",
    "ContextAssembler",
    "ContextReader",
    "describe",
    "validate_registry",
]
