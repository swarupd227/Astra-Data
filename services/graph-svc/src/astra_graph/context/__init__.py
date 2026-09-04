"""Context contracts and the shared assembler (spec §4.1.3, story S1.3.1).

Agents do not receive raw graph dumps. Each declares the sub-graph shape it needs for one
unit of work; this materialises exactly that, canonically, with a hash over it and a
budget it refuses to exceed.
"""

from .assembler import (
    CONTRACTS,
    AssembledContext,
    ContextAssembler,
    ContextReader,
    describe,
    validate_registry,
)
from .canonical import CanonicalisationError, canonical_json, context_hash
from .contract import (
    Budget,
    ContextBudgetExceededError,
    ContextContract,
    ContractDefinitionError,
    ContractName,
    SectionSpec,
    serialise,
)
from .signature import SignatureError, ast_shape, matches, signature_of
from .transpiler import TRANSPILER_CALC

__all__ = [
    "CONTRACTS",
    "TRANSPILER_CALC",
    "AssembledContext",
    "Budget",
    "CanonicalisationError",
    "ContextAssembler",
    "ContextBudgetExceededError",
    "ContextContract",
    "ContextReader",
    "ContractDefinitionError",
    "ContractName",
    "SectionSpec",
    "SignatureError",
    "ast_shape",
    "canonical_json",
    "context_hash",
    "describe",
    "matches",
    "serialise",
    "signature_of",
    "validate_registry",
]
