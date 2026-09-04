"""The AST shape of a calculation, as the Pattern Library keys off it.

Specification §4.3 gives a pattern's ``source_signature`` as
``{ ast_shape: 'DIV(SUM(a), LOD_FIXED(dims, SUM(b)))', adapter: tableau }`` — a compact
string with leaf identifiers abstracted to capture names, not a tree. §4.1.3 then has the
Transpiler's context include "the Pattern records whose source_signature matches its AST
shape", which is what this computes.

**What this is not.** Ranking candidate patterns, partial and fuzzy matching, guard
evaluation, promotion and pass statistics are the Pattern Library's, which is E5/F5.3.
This does one thing: turn an AST into the shape string that selects patterns by equality,
so the contract can carry the matches rather than a promise of them.

**Why structural rather than grammar-aware.** The calculation grammar belongs to the
Tableau adapter (E2/F2.3) and does not exist yet. Encoding a guess at its node types here
would be a second grammar to keep in step with the first. So the walk is generic: a
dictionary carrying an operator key is a call, a dictionary carrying a single identifier
key is a leaf, everything else is structure. An adapter that names its AST nodes
differently declares the extra keys here rather than rewriting the walk.

Captures are named in order of first appearance — ``a``, ``b``, ``c`` — and the same
identifier appearing twice gets the same name, because ``DIV(a, a)`` and ``DIV(a, b)`` are
different shapes and a pattern for one must not match the other. Identity is the pair
(kind, text): a field called ``Region`` and a parameter called ``Region`` are two things.
"""

from __future__ import annotations

import json
from string import ascii_lowercase
from typing import Any

#: Keys whose value names the operation being applied.
OPERATOR_KEYS: tuple[str, ...] = ("op", "fn", "func", "function", "operator")

#: Keys whose value is the operation's argument list. Flattened into the call's own
#: parentheses, so ``{op: DIV, args: [x, y]}`` reads ``DIV(x, y)`` as §4.3 writes it rather
#: than ``DIV([x, y])``. A list under any other key keeps its brackets, because there it is
#: one argument that happens to be a collection — a level-of-detail expression's dimension
#: list is one thing, and its arity is part of the shape.
ARGUMENT_KEYS: tuple[str, ...] = ("args", "arguments", "operands")

#: Keys whose value is a leaf identifier — a name in the source, to be abstracted.
IDENTIFIER_KEYS: tuple[str, ...] = (
    "field",
    "parameter",
    "column",
    "table",
    "datasource",
    "dimension",
    "calculation",
    "set",
)

#: What a literal of each type renders as. Literal *values* never reach a shape: two
#: calculations differing only in a constant are the same shape and should match the same
#: pattern, and a literal can carry client data (§18.3).
_LITERAL: dict[type, str] = {bool: "<bool>", int: "<num>", float: "<num>", str: "<str>"}


class SignatureError(ValueError):
    """The AST cannot be reduced to a shape."""


def ast_shape(ast: Any) -> str:
    """The shape string for one calculation AST.

    Deterministic: the same AST always produces the same string, on any deployment, with
    no dependence on dictionary insertion order.
    """
    captures: dict[tuple[str, str], str] = {}
    return _render(ast, captures, depth=0)


def signature_of(ast: Any, *, adapter: str | None = None) -> dict[str, Any]:
    """The signature document, in the shape §4.3 gives it."""
    signature: dict[str, Any] = {"ast_shape": ast_shape(ast)}
    if adapter:
        signature["adapter"] = adapter
    return signature


def matches(signature: Any, *, shape: str, adapter: str | None) -> bool:
    """Does a stored ``source_signature`` match this calculation?

    Shape equality is required. The adapter is required to agree only when both sides
    declare one: a pattern that names no adapter is adapter-agnostic, and a deployment
    that cannot name its own adapter should not silently exclude every pattern that can.
    """
    if isinstance(signature, str):
        signature = _loads(signature)
    if not isinstance(signature, dict):
        return False
    if signature.get("ast_shape") != shape:
        return False
    declared = signature.get("adapter")
    return not (declared and adapter and declared != adapter)


# --------------------------------------------------------------------------- internals

#: A calculation nested this deep is a defect in the parser, not a calculation.
MAX_DEPTH = 64


def _render(node: Any, captures: dict[tuple[str, str], str], *, depth: int) -> str:
    if depth > MAX_DEPTH:
        raise SignatureError(f"AST nests deeper than {MAX_DEPTH} levels")

    if isinstance(node, dict):
        return _render_dict(node, captures, depth=depth)
    if isinstance(node, list):
        parts = [_render(item, captures, depth=depth + 1) for item in node]
        return f"[{', '.join(parts)}]"
    if node is None:
        return "<null>"
    return _LITERAL.get(type(node), "<unknown>")


def _render_dict(node: dict[str, Any], captures: dict[tuple[str, str], str], *, depth: int) -> str:
    leaf = _leaf(node)
    if leaf is not None:
        return _capture(leaf, captures)

    operator = next(
        (str(node[key]) for key in OPERATOR_KEYS if key in node and node[key] is not None),
        None,
    )
    # Children in sorted key order, so two parsers that build the same call with the keys
    # in a different order still produce the same shape.
    children = [
        (key, value)
        for key, value in sorted(node.items())
        if key not in OPERATOR_KEYS
    ]

    rendered: list[str] = []
    for key, value in children:
        if operator is not None and key in ARGUMENT_KEYS and isinstance(value, list):
            rendered.extend(_render(item, captures, depth=depth + 1) for item in value)
        else:
            rendered.append(_render(value, captures, depth=depth + 1))

    if operator is None:
        # Structure with no operation of its own — a wrapper the parser emitted. Rendered
        # as a braced group so it is visible in the shape rather than silently flattened,
        # which would let two different ASTs collide on one signature.
        pairs = ", ".join(
            f"{key}: {text}" for (key, _value), text in zip(children, rendered, strict=True)
        )
        return "{" + pairs + "}"
    return f"{operator}({', '.join(rendered)})"


def _leaf(node: dict[str, Any]) -> tuple[str, str] | None:
    """A leaf is a dictionary whose only meaningful key names an identifier."""
    present = [key for key in IDENTIFIER_KEYS if key in node]
    if len(present) != 1:
        return None
    key = present[0]
    value = node[key]
    if not isinstance(value, str):
        return None
    # A leaf may carry description alongside its name; anything structural means it is not
    # a leaf, and the walk should keep descending.
    if any(isinstance(v, dict | list) for k, v in node.items() if k != key):
        return None
    return key, value


def _capture(leaf: tuple[str, str], captures: dict[tuple[str, str], str]) -> str:
    if leaf not in captures:
        captures[leaf] = _name(len(captures))
    return captures[leaf]


def _name(index: int) -> str:
    """a, b, ... z, a1, b1, ... — enough for any calculation a person wrote."""
    letter = ascii_lowercase[index % 26]
    cycle = index // 26
    return letter if cycle == 0 else f"{letter}{cycle}"


def _loads(raw: str) -> Any:
    try:
        return json.loads(raw)
    except ValueError:
        return None


__all__ = [
    "ARGUMENT_KEYS",
    "IDENTIFIER_KEYS",
    "OPERATOR_KEYS",
    "SignatureError",
    "ast_shape",
    "matches",
    "signature_of",
]
