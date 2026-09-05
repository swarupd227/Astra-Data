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

**Why structural rather than grammar-aware — and the real grammar this predates.** This
module's own generic walk (a dictionary carrying an operator key is a call, one carrying a
single identifier key is a leaf) was written when "the calculation grammar belongs to the
Tableau adapter (E2/F2.3) and does not exist yet." It now does (S2.3.1): every real
``CalculatedField.formula_ast`` is the adapter-sdk wire shape (`packages/adapter-sdk`'s
``CalcNode``) — a uniform ``{kind, name, value, children, detail}`` per node, discriminated
by ``kind`` (one of exactly nine: REFERENCE, LITERAL, FUNCTION, AGGREGATE, OPERATOR,
CONDITIONAL, CAST, WINDOW, UNKNOWN), which none of the generic keys below ever matched.

**This was a real, confirmed defect, not a hypothetical one.** Run against real wire ASTs,
the generic walk could not tell `kind`/`name` apart from any other string field, so it
rendered both as an opaque ``<str>`` literal — collapsing ``SUM([Notional]) / SUM([Margin])``
and ``SUM([Notional]) + SUM([Margin])`` (different operators, different fields) onto the
identical shape string. Every existing caller — `lineage.calc_shapes` (S1.4.2, feeding
S3.1.1's own Cartographer clustering), `generation._matching_patterns` and
`context.assembler._patterns` (both already reading real `formula_ast`, both moot until a
Pattern existed to match) — has been computing this same degenerate shape since S1.4.2.
Fixed here (story S5.5.1, whose own Pattern-matching this bug would otherwise silently
break) by recognising the real wire shape as a first-class case, kind-by-kind, ahead of the
generic dispatch below — which stays exactly as it was, unbroken, for any AST that is not
this shape (an adapter that names its nodes differently still declares its own keys there
rather than rewriting either walk).

Captures are named in order of first appearance — ``a``, ``b``, ``c`` — and the same
identifier appearing twice gets the same name, because ``DIV(a, a)`` and ``DIV(a, b)`` are
different shapes and a pattern for one must not match the other. Identity is the pair
(kind, text): a field called ``Region`` and a parameter called ``Region`` are two things —
including under the real wire shape, whose single REFERENCE kind cannot itself distinguish
a field from a parameter (`classify.py`'s own docstring: "Tableau writes a parameter
reference identically to a field reference"), so identity there is (``"REFERENCE"``, name).
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

#: The real adapter-sdk wire shape's own `kind` vocabulary (`astra_adapter.calc.NodeKind`) —
#: exactly nine values, confirmed exhaustive against both the wire contract and the Tableau
#: parser's own construction sites. `REFERENCE` is the sole leaf-with-an-identifier kind;
#: `LITERAL` is the sole leaf-with-a-value kind; everything else is operator-shaped (`name`
#: is the operator, `children` its arguments) except `UNKNOWN`, which carries neither.
_WIRE_LEAF_KIND = "REFERENCE"
_WIRE_LITERAL_KIND = "LITERAL"
_WIRE_UNKNOWN_KIND = "UNKNOWN"
_WIRE_OPERATOR_KINDS = frozenset(
    {"FUNCTION", "AGGREGATE", "OPERATOR", "CONDITIONAL", "CAST", "WINDOW"}
)
_WIRE_KINDS = _WIRE_OPERATOR_KINDS | {_WIRE_LEAF_KIND, _WIRE_LITERAL_KIND, _WIRE_UNKNOWN_KIND}


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


def capture_identifiers(ast: Any) -> dict[str, str]:
    """The placeholder-name -> original-identifier mapping for one AST (``{"a": "Notional",
    "b": "Margin"}``) — the same captures ``ast_shape`` abstracts away, exposed for the
    Pattern Library (story S5.5.1) to substitute back into a ``target_template`` when
    applying a pattern to one specific calculation's own real field/parameter names.
    """
    captures: dict[tuple[str, str], str] = {}
    _render(ast, captures, depth=0)
    return {placeholder: identifier for (_key, identifier), placeholder in captures.items()}


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
    kind = node.get("kind")
    if isinstance(kind, str) and kind in _WIRE_KINDS:
        return _render_wire_node(node, kind, captures, depth=depth)

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


def _render_wire_node(
    node: dict[str, Any], kind: str, captures: dict[tuple[str, str], str], *, depth: int
) -> str:
    """The real wire shape's own dispatch — see the module docstring's own "the real
    grammar this predates" section. ``detail`` and ``value`` (except on LITERAL) are
    deliberately excluded from the shape: they carry classifier metadata (§9.1's own family
    tag, table-calc addressing) that must not make two calls to the same function look like
    different shapes just because one happened to resolve a fact the other did not.
    """
    if kind == _WIRE_LEAF_KIND:
        return _capture((kind, str(node.get("name") or "")), captures)
    if kind == _WIRE_LITERAL_KIND:
        return _LITERAL.get(type(node.get("value")), "<unknown>")
    if kind == _WIRE_UNKNOWN_KIND:
        # Always C4 (classify.py's own "kept verbatim, never generable") -- never reaches a
        # GENERATED_PROVED artefact for the Pattern Library to generalise from, so a single
        # opaque token (rather than descending into whatever raw text it carries) is enough.
        return "<unknown_construct>"

    operator = str(node.get("name") or "")
    children = node.get("children") or []
    if not isinstance(children, list):
        raise SignatureError(f"{kind} node's children is not a list")
    rendered = [_render(child, captures, depth=depth + 1) for child in children]
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
    "capture_identifiers",
    "matches",
    "signature_of",
]
