"""Tableau calculations → a typed AST — story S2.3.1.

    "I want Tableau calculation language parsed into a typed AST, so that the Transpiler
    works on structure, not text."

Lark reads `tableau.lark` (§5.4 commits the calc parsers to Lark); this turns its parse tree
into the SDK's `CalcAST`, which is the shape every downstream component works on — §9's
pattern matching, S1.3.1's `ast_shape`, the Transpiler, the Mender.

**Three properties this file is responsible for.**

1. **Spans.** Every node carries the character offsets it was parsed from, so a parity failure
   can point at the exact text (S2.3.1's second criterion). Lark gives them; the work is not
   losing them through the rules that flatten.
2. **Nothing is dropped.** A construct the grammar cannot read becomes an `UNKNOWN` node
   holding the source verbatim, with its span — never an exception, and never a silently
   omitted subtree. S2.3.1's third criterion, and §6.2's.
3. **Recognition is separate from parsing.** The grammar accepts any call; `functions.py`
   decides whether the platform knows it. An unknown *function* still parses into a FUNCTION
   node with its arguments — the structure is real and useful — and is flagged.

**Why the recovery path matters more than the happy one.** A client's estate contains
calculations written over fifteen years by people who have left, including ones Tableau itself
renders as errors. An adapter that raises on the first of them fails the harvest; one that
returns a partial tree loses the rest of the workbook silently. Neither is acceptable, so an
unparseable expression yields a single UNKNOWN node carrying the whole text, parse quality
zero, and a workbook the Parse Quality Queue can show somebody.
"""

from __future__ import annotations

import logging
from functools import lru_cache
from pathlib import Path
from typing import Any

from astra_adapter import CalcAST, CalcNode, Grammar, NodeKind
from lark import Lark, Token, Tree
from lark.exceptions import LarkError

from .functions import KNOWN_FUNCTIONS, Family, family_of, is_known, is_table_calc

logger = logging.getLogger(__name__)

#: The grammar's version, recorded on every parse result (S2.3.1's fourth criterion).
#:
#: Bumped when the *language the parser accepts* changes — a new function recognised, a
#: precedence corrected, a construct newly read. Not bumped for a refactor of this file. Every
#: CalcAST and every ParseResult carries it, so a workbook parsed months ago can be read
#: against the grammar that read it, and a re-harvest under a newer grammar is a visible
#: change rather than a mysterious one.
GRAMMAR_VERSION = "tableau-1"

GRAMMAR_PATH = Path(__file__).with_name("tableau.lark")

#: Rule names that carry an operator symbol rather than a name of their own.
_BINARY_RULES = {"or_expr", "and_expr", "comparison", "additive", "multiplicative", "power"}


class TableauGrammar:
    """A parser for one version of the Tableau calculation language."""

    def __init__(self, *, version: str = GRAMMAR_VERSION) -> None:
        self.version = version
        self._lark = _lark()

    @property
    def declared(self) -> Grammar:
        """What this grammar claims to cover, for the Parse Quality Queue and §6.3."""
        return Grammar(
            version=self.version,
            functions=frozenset(KNOWN_FUNCTIONS),
            operators=frozenset(
                {
                    "+",
                    "-",
                    "*",
                    "/",
                    "%",
                    "^",
                    "=",
                    "==",
                    "<>",
                    "!=",
                    "<",
                    "<=",
                    ">",
                    ">=",
                    "AND",
                    "OR",
                    "NOT",
                    "IN",
                }
            ),
        )

    def parse(self, expression: str) -> CalcAST:
        """Parse one calculation. Never raises for a calculation's *content*.

        A `CalcAST` always comes back. What varies is how much of it is UNKNOWN, and the
        recognised/total counts that become §4.1.4's parse quality.
        """
        text = expression or ""
        if not text.strip():
            # An empty formula is a real thing in a real workbook — a field somebody started
            # and abandoned. Quality one, because there is nothing unread in it; scoring it
            # zero would hold a workbook for a calculation with nothing in it.
            return CalcAST(
                root=CalcNode(NodeKind.LITERAL, name="empty", value=None, span=(0, 0)),
                expression=text,
                grammar_version=self.version,
            )

        try:
            tree = self._lark.parse(text)
        except LarkError as exc:
            logger.info("calculation did not parse under %s: %s", self.version, exc)
            return CalcAST(
                root=CalcNode(NodeKind.UNKNOWN, value=text, span=(0, len(text))),
                expression=text,
                grammar_version=self.version,
                recognised=0,
                total=1,
            )

        counter = _Counter()
        root = _node(tree, text, counter)
        return CalcAST(
            root=root,
            expression=text,
            grammar_version=self.version,
            recognised=counter.recognised,
            total=counter.total,
        )


@lru_cache(maxsize=1)
def _lark() -> Lark:
    """One parser, built once.

    Earley rather than LALR: Tableau's grammar is ambiguous in places a LALR table cannot
    resolve without contorting the rules — `IN` as both an operator and a set test, `MIN` as
    both an aggregate and a numeric function — and a grammar contorted to fit a parser
    generator is a grammar nobody can review against the language it describes. Earley is
    slower per expression and a workbook has tens of them, not millions.
    """
    return Lark(
        GRAMMAR_PATH.read_text(encoding="utf-8"),
        parser="earley",
        propagate_positions=True,
        maybe_placeholders=False,
        ambiguity="resolve",
    )


class _Counter:
    """Recognised and total constructs, for §4.1.4's parse quality."""

    def __init__(self) -> None:
        self.recognised = 0
        self.total = 0

    def count(self, *, recognised: bool) -> None:
        self.total += 1
        if recognised:
            self.recognised += 1


def _span(item: Tree[Any] | Token) -> tuple[int, int] | None:
    """Character offsets, from Lark's positions.

    Guarded rather than assumed: `propagate_positions` fills these for rules built from
    tokens, and a rule that matched nothing but other rules can still arrive without them.
    A missing span is better than a wrong one — a caller pointing at the wrong text is worse
    than one that cannot point at all.
    """
    start = getattr(item, "start_pos", None)
    end = getattr(item, "end_pos", None)
    if start is None or end is None:
        meta = getattr(item, "meta", None)
        if meta is None or getattr(meta, "empty", True):
            return None
        start, end = meta.start_pos, meta.end_pos
    return (int(start), int(end))


def _node(item: Tree[Any] | Token, text: str, counter: _Counter) -> CalcNode:
    if isinstance(item, Token):
        return _token(item, counter)

    rule = str(item.data)
    handler = _RULES.get(rule)
    if handler is not None:
        built: CalcNode = handler(item, text, counter)
        return built

    if rule in _BINARY_RULES:
        return _binary(item, text, counter)

    # A rule with one child and no meaning of its own — Lark's inlining leaves a few. Passing
    # through rather than wrapping keeps the AST the shape the grammar describes.
    if len(item.children) == 1:
        return _node(item.children[0], text, counter)

    counter.count(recognised=False)
    return CalcNode(NodeKind.UNKNOWN, value=_text_of(item, text), span=_span(item))


def _token(token: Token, counter: _Counter) -> CalcNode:
    kind = str(token.type)
    span = _span(token)
    if kind == "SIGNED_NUMBER":
        counter.count(recognised=True)
        raw = str(token)
        value: float | int = float(raw) if "." in raw or "e" in raw.lower() else int(raw)
        return CalcNode(NodeKind.LITERAL, name="number", value=value, span=span)
    if kind == "STRING":
        counter.count(recognised=True)
        return CalcNode(NodeKind.LITERAL, name="string", value=_unquote(str(token)), span=span)
    counter.count(recognised=True)
    return CalcNode(NodeKind.REFERENCE, name=str(token).strip("[]"), span=span)


# ----------------------------------------------------------------------- rules


def _binary(tree: Tree[Any], text: str, counter: _Counter) -> CalcNode:
    """``a + b - c`` — left-associative, one node per operator.

    Lark gives the whole chain as one rule with alternating operands and operators, so this
    folds it. Folding right instead would make ``10 - 3 - 2`` evaluate to 9, which is the kind
    of defect that survives review because the AST *looks* right.
    """
    children = list(tree.children)
    if len(children) == 1:
        return _node(children[0], text, counter)

    node = _node(children[0], text, counter)
    index = 1
    while index + 1 < len(children) + 1 and index < len(children):
        operator = children[index]
        right = _node(children[index + 1], text, counter)
        counter.count(recognised=True)
        node = CalcNode(
            NodeKind.OPERATOR,
            name=str(operator).upper(),
            children=(node, right),
            span=(
                node.span[0] if node.span else 0,
                right.span[1] if right.span else len(text),
            ),
        )
        index += 2
    return node


def _unary(kind: str) -> Any:
    def build(tree: Tree[Any], text: str, counter: _Counter) -> CalcNode:
        counter.count(recognised=True)
        operand = _node(tree.children[-1], text, counter)
        return CalcNode(NodeKind.OPERATOR, name=kind, children=(operand,), span=_span(tree))

    return build


def _conditional(tree: Tree[Any], text: str, counter: _Counter) -> CalcNode:
    """IF/ELSEIF/ELSE/END, flattened to alternating condition and result.

    The alternation is the SDK's contract for `CONDITIONAL` and is what lets the Transpiler
    emit a DAX `SWITCH(TRUE(), …)` without knowing Tableau's syntax. An odd final child is the
    ELSE.
    """
    counter.count(recognised=True)
    parts: list[CalcNode] = []
    for child in tree.children:
        if isinstance(child, Token):
            continue
        rule = str(child.data)
        if rule == "elseif_clause" or rule == "else_clause":
            parts.extend(
                _node(part, text, counter) for part in child.children if not isinstance(part, Token)
            )
        else:
            parts.append(_node(child, text, counter))
    return CalcNode(NodeKind.CONDITIONAL, name="IF", children=tuple(parts), span=_span(tree))


def _case(tree: Tree[Any], text: str, counter: _Counter) -> CalcNode:
    """CASE x WHEN a THEN b … END.

    The subject is the first child and then the pairs follow, which is a different shape from
    IF — a CASE compares one expression against several values and a SWITCH in DAX takes the
    subject too. Flattening it into IF's shape would lose that and force the Transpiler to
    re-derive it.
    """
    counter.count(recognised=True)
    parts: list[CalcNode] = []
    for child in tree.children:
        if isinstance(child, Token):
            continue
        rule = str(child.data)
        if rule in {"when_clause", "else_clause"}:
            parts.extend(
                _node(part, text, counter) for part in child.children if not isinstance(part, Token)
            )
        else:
            parts.append(_node(child, text, counter))
    return CalcNode(NodeKind.CONDITIONAL, name="CASE", children=tuple(parts), span=_span(tree))


def _lod(tree: Tree[Any], text: str, counter: _Counter) -> CalcNode:
    """``{FIXED [Desk] : SUM([Notional])}`` — Appendix B.1's LOD family.

    The measure is `children[0]` and the dimensions follow, which matches the SDK's
    `AGGREGATE` contract. ``detail`` carries the grain, because §4.1.1 puts `lod_type` on the
    CalculatedField node and the Transpiler picks ALLEXCEPT / ALL / VALUES from it.
    """
    counter.count(recognised=True)
    grain = "FIXED"
    dimensions: list[CalcNode] = []
    measure: CalcNode | None = None

    for child in tree.children:
        if isinstance(child, Token):
            continue
        rule = str(child.data)
        if rule == "lod_kind":
            grain = str(child.children[0]).upper()
        elif rule == "dimension_list":
            dimensions = [
                _node(part, text, counter) for part in child.children if not isinstance(part, Token)
            ]
        else:
            measure = _node(child, text, counter)

    if measure is None:  # pragma: no cover - the grammar requires one
        measure = CalcNode(NodeKind.UNKNOWN, value=_text_of(tree, text), span=_span(tree))

    return CalcNode(
        NodeKind.AGGREGATE,
        name=grain,
        children=(measure, *dimensions),
        detail=(("grain", grain),),
        span=_span(tree),
    )


def _function(tree: Tree[Any], text: str, counter: _Counter) -> CalcNode:
    """Any ``NAME(...)``. The registry decides whether the platform knows it.

    An unrecognised function still parses into a FUNCTION node with its arguments — the
    structure is real and the Transpiler can still see what it is applied to — and the
    *recognition* count drops, which is what §4.1.4 measures and what holds the workbook.
    Turning it into an opaque UNKNOWN would throw away arguments that are perfectly readable,
    including the fields it depends on.
    """
    name_token = tree.children[0]
    name = str(name_token)
    arguments: list[CalcNode] = []
    for child in tree.children[1:]:
        if isinstance(child, Token):
            continue
        if str(child.data) == "arguments":
            arguments = [
                _node(part, text, counter) for part in child.children if not isinstance(part, Token)
            ]

    known = is_known(name)
    counter.count(recognised=known)
    family = family_of(name)

    detail: list[tuple[str, str]] = [("family", family.value)]
    kind = NodeKind.FUNCTION
    if is_table_calc(name):
        kind = NodeKind.WINDOW
        # §6.2: a table calculation's addressing and partitioning come from the *sheet*, not
        # from the expression. Recorded as unresolved so nothing downstream mistakes a
        # default for a fact — S2.3.2 reads the sheet and fills them in.
        detail.extend([("addressing", "unresolved"), ("partitioning", "unresolved")])
    if family is Family.AGGREGATE:
        kind = NodeKind.AGGREGATE
    if not known:
        detail.append(("recognised", "false"))

    return CalcNode(
        kind,
        name=name.upper(),
        children=tuple(arguments),
        detail=tuple(sorted(detail)),
        span=_span(tree),
    )


def _cast(tree: Tree[Any], text: str, counter: _Counter) -> CalcNode:
    counter.count(recognised=True)
    inner = next(child for child in tree.children if not isinstance(child, Token))
    target = next(
        (
            str(child)
            for child in tree.children
            if isinstance(child, Token) and child.type == "TYPE_NAME"
        ),
        "STRING",
    )
    return CalcNode(
        NodeKind.CAST,
        name=target.upper(),
        children=(_node(inner, text, counter),),
        span=_span(tree),
    )


def _in_set(negated: bool) -> Any:
    def build(tree: Tree[Any], text: str, counter: _Counter) -> CalcNode:
        counter.count(recognised=True)
        operands = [
            _node(child, text, counter) for child in tree.children if not isinstance(child, Token)
        ]
        return CalcNode(
            NodeKind.OPERATOR,
            name="NOT IN" if negated else "IN",
            children=tuple(operands),
            detail=(("family", Family.SET.value),),
            span=_span(tree),
        )

    return build


def _literal(kind: str, value: Any) -> Any:
    def build(tree: Tree[Any], text: str, counter: _Counter) -> CalcNode:
        counter.count(recognised=True)
        return CalcNode(NodeKind.LITERAL, name=kind, value=value, span=_span(tree))

    return build


def _passthrough(tree: Tree[Any], text: str, counter: _Counter) -> CalcNode:
    return _node(tree.children[0], text, counter)


def _reference(tree: Tree[Any], text: str, counter: _Counter) -> CalcNode:
    """``[Field]``, ``[Datasource].[Field]``, or a bare name.

    Tableau writes a parameter reference identically to a field reference, so the AST records
    a REFERENCE and the caller — which knows the workbook's parameter list — decides. Encoding
    a guess here would put the wrong kind on a node the Transpiler branches on.
    """
    counter.count(recognised=True)
    token = tree.children[0]
    raw = str(token)
    if "]" in raw and "." in raw:
        parts = [part.strip().strip("[]") for part in raw.split("].[")]
        return CalcNode(
            NodeKind.REFERENCE,
            name=parts[-1],
            detail=(("qualifier", parts[0]),),
            span=_span(tree),
        )
    return CalcNode(NodeKind.REFERENCE, name=raw.strip("[]"), span=_span(tree))


def _number(tree: Tree[Any], text: str, counter: _Counter) -> CalcNode:
    counter.count(recognised=True)
    raw = str(tree.children[0])
    value: float | int = float(raw) if "." in raw or "e" in raw.lower() else int(raw)
    return CalcNode(NodeKind.LITERAL, name="number", value=value, span=_span(tree))


def _string(tree: Tree[Any], text: str, counter: _Counter) -> CalcNode:
    counter.count(recognised=True)
    return CalcNode(
        NodeKind.LITERAL, name="string", value=_unquote(str(tree.children[0])), span=_span(tree)
    )


#: Rule name → builder. Typed loosely because the builders differ in signature only in the
#: closures `_unary`, `_literal` and `_in_set` return, and a Protocol for four shapes would be
#: more machinery than the dispatch it describes.
_RULES: dict[str, Any] = {
    "conditional": _conditional,
    "case_expression": _case,
    "lod_expression": _lod,
    "function_call": _function,
    "type_cast": _cast,
    "reference": _reference,
    "literal": _passthrough,
    "number": _number,
    "string": _string,
    "true": _literal("boolean", True),
    "false": _literal("boolean", False),
    "null": _literal("null", None),
    "negate": _unary("-"),
    "unary_plus": _unary("+"),
    "logical_not": _unary("NOT"),
    "in_set": _in_set(negated=False),
    "not_in_set": _in_set(negated=True),
}


# --------------------------------------------------------------------- helpers


def _text_of(tree: Tree[Any], text: str) -> str:
    span = _span(tree)
    return text[span[0] : span[1]] if span else text


def _unquote(raw: str) -> str:
    """Tableau strings use single or double quotes, with doubling as the escape."""
    if len(raw) < 2:
        return raw
    quote = raw[0]
    body = raw[1:-1]
    return body.replace(quote * 2, quote).replace(f"\\{quote}", quote)


_GRAMMAR: TableauGrammar | None = None


def grammar() -> TableauGrammar:
    """The process's parser. Built once; Lark's Earley construction is not free."""
    global _GRAMMAR
    if _GRAMMAR is None:
        _GRAMMAR = TableauGrammar()
    return _GRAMMAR


def parse_calculation(expression: str) -> CalcAST:
    return grammar().parse(expression)
