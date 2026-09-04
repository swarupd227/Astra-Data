"""A small, real calculation grammar for the fake source.

**Why a real parser rather than a stub.** §6.3 checks "AST round-trip (AST → canonical text →
AST) stability", and a stub that returns a fixed AST round-trips trivially — which would make
the suite's hardest check the one it cannot fail. This parser is small but genuine: a
recursive-descent expression parser with precedence, function calls, LOD-style aggregates,
window functions, conditionals, casts, references, literals, and an UNKNOWN escape for what
it cannot read. It is enough to break, which is what makes passing mean something.

**It is not a Tableau grammar.** §5.4 commits those to Lark, versioned with the adapter, and
F2.3 builds Tableau's against the real language. What is shared is the *AST* — the fake
produces the same `CalcAST` a Tableau grammar will, so the suite that checks one checks the
other.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from ..calc import CalcAST, CalcNode, Grammar, NodeKind

GRAMMAR_VERSION = "fake-1"

#: What this grammar claims to read. A construct outside it is UNKNOWN, and the difference
#: between "not in this language" and "not implemented" is exactly this set.
# fmt: off
FUNCTIONS = frozenset({
    "SUM", "AVG", "MIN", "MAX", "COUNT", "COUNTD", "ABS", "ROUND", "FLOOR", "CEILING", "LEN",
    "LEFT", "RIGHT", "MID", "TRIM", "UPPER", "LOWER", "CONTAINS", "REPLACE", "DATEPART",
    "DATEDIFF", "DATEADD", "DATETRUNC", "TODAY", "NOW", "YEAR", "MONTH", "IFNULL", "ISNULL",
    "ZN"
})
# fmt: on
AGGREGATES = frozenset({"FIXED", "INCLUDE", "EXCLUDE"})
WINDOWS = frozenset({"WINDOW_SUM", "WINDOW_AVG", "WINDOW_MIN", "WINDOW_MAX", "RANK", "INDEX"})
OPERATORS = frozenset(
    {"+", "-", "*", "/", "%", "=", "<>", "<", "<=", ">", ">=", "AND", "OR", "NOT"}
)

FAKE_GRAMMAR = Grammar(
    version=GRAMMAR_VERSION,
    functions=FUNCTIONS | AGGREGATES | WINDOWS,
    operators=OPERATORS,
)

#: Precedence, loosest first. Each tier is left-associative.
_PRECEDENCE: tuple[frozenset[str], ...] = (
    frozenset({"OR"}),
    frozenset({"AND"}),
    frozenset({"=", "<>", "<", "<=", ">", ">="}),
    frozenset({"+", "-"}),
    frozenset({"*", "/", "%"}),
)

_TOKEN = re.compile(
    r"""
      (?P<space>\s+)
    | (?P<reference>\[[^\]]*\])
    | (?P<string>"(?:[^"\\]|\\.)*"|'(?:[^'\\]|\\.)*')
    | (?P<number>\d+(?:\.\d+)?)
    | (?P<punct><>|<=|>=|[-+*/%=<>(),{}])
    | (?P<word>[A-Za-z_][A-Za-z_0-9]*)
    | (?P<other>\S)
    """,
    re.VERBOSE,
)


class ParseFailure(Exception):
    """The parser reached something it could not make sense of at all.

    Distinct from an UNKNOWN node: UNKNOWN means "a construct I could not read, retained
    verbatim, parse quality lowered" (§6.2), which is a normal outcome. This means the text
    is not an expression — unbalanced brackets, an empty operand — and there is nothing
    honest to put in the tree.
    """


@dataclass(slots=True)
class _Token:
    kind: str
    text: str
    position: int


def _tokenise(text: str) -> list[_Token]:
    tokens: list[_Token] = []
    position = 0
    while position < len(text):
        match = _TOKEN.match(text, position)
        if match is None:  # pragma: no cover - `other` matches any non-space
            raise ParseFailure(f"cannot tokenise at offset {position}")
        position = match.end()
        kind = match.lastgroup or "other"
        if kind == "space":
            continue
        tokens.append(_Token(kind, match.group(), match.start()))
    return tokens


class _Parser:
    """Recursive descent over the token list.

    ``recognised`` and ``total`` are counted as constructs are consumed, because §4.1.4
    defines parse quality as recognised ÷ total constructs and the only place that ratio can
    be counted honestly is where the constructs are actually seen.
    """

    def __init__(self, tokens: list[_Token], source: str) -> None:
        self._tokens = tokens
        self._source = source
        self._at = 0
        self.recognised = 0
        self.total = 0

    # ------------------------------------------------------------------ helpers

    @property
    def _current(self) -> _Token | None:
        return self._tokens[self._at] if self._at < len(self._tokens) else None

    def _take(self) -> _Token:
        token = self._current
        if token is None:
            raise ParseFailure("expression ended sooner than expected")
        self._at += 1
        return token

    def _accept(self, text: str) -> bool:
        token = self._current
        if token is not None and token.text.upper() == text.upper():
            self._at += 1
            return True
        return False

    def _expect(self, text: str) -> None:
        if not self._accept(text):
            found = self._current.text if self._current else "the end of the expression"
            raise ParseFailure(f"expected {text!r} but found {found!r}")

    def _count(self, *, recognised: bool) -> None:
        self.total += 1
        if recognised:
            self.recognised += 1

    # ------------------------------------------------------------------ grammar

    def parse(self) -> CalcNode:
        node = self._binary(0)
        if self._current is not None:
            raise ParseFailure(f"unexpected {self._current.text!r} after a complete expression")
        return node

    def _binary(self, tier: int) -> CalcNode:
        if tier >= len(_PRECEDENCE):
            return self._unary()
        node = self._binary(tier + 1)
        while (token := self._current) is not None and token.text.upper() in _PRECEDENCE[tier]:
            self._take()
            self._count(recognised=True)
            right = self._binary(tier + 1)
            node = CalcNode(NodeKind.OPERATOR, name=token.text.upper(), children=(node, right))
        return node

    def _unary(self) -> CalcNode:
        token = self._current
        if token is not None and token.text.upper() in {"-", "NOT"}:
            self._take()
            self._count(recognised=True)
            return CalcNode(NodeKind.OPERATOR, name=token.text.upper(), children=(self._unary(),))
        return self._primary()

    def _primary(self) -> CalcNode:
        token = self._take()

        if token.text == "(":
            node = self._binary(0)
            self._expect(")")
            return node

        if token.kind == "reference":
            self._count(recognised=True)
            return CalcNode(NodeKind.REFERENCE, name=token.text[1:-1])

        if token.kind == "number":
            self._count(recognised=True)
            value: float | int = float(token.text) if "." in token.text else int(token.text)
            return CalcNode(NodeKind.LITERAL, name="number", value=value)

        if token.kind == "string":
            self._count(recognised=True)
            return CalcNode(NodeKind.LITERAL, name="string", value=_unquote(token.text))

        if token.kind == "word":
            return self._word(token)

        if token.text == "{":
            return self._aggregate()

        raise ParseFailure(f"unexpected {token.text!r} at offset {token.position}")

    def _word(self, token: _Token) -> CalcNode:
        name = token.text.upper()

        if name in {"TRUE", "FALSE"}:
            self._count(recognised=True)
            return CalcNode(NodeKind.LITERAL, name="boolean", value=name == "TRUE")

        if name == "NULL":
            self._count(recognised=True)
            return CalcNode(NodeKind.LITERAL, name="null", value=None)

        # ---- the one canonical form that is not source syntax.
        #
        # §6.3 round-trips an AST through its canonical text, so this grammar must read back
        # everything ``canonical_text`` writes. It writes the *language* — an LOD as
        # ``{FIXED … : …}``, a conditional as ``IF … THEN … END`` — which the rules below
        # already read. ``UNKNOWN("…")`` is the exception: it has no source syntax because it
        # stands for text this grammar could not read.
        #
        # An earlier version also read ``IF(a, b, c)`` and ``NAME{k=v}(…)``, because
        # ``canonical_text`` used to emit those. It stopped: a canonical form no grammar can
        # read back could only round-trip for a parser taught a syntax its own language does
        # not have. Removing the branches here was not tidying — the ``IF(`` branch actively
        # broke, swallowing ``IF (cond) THEN`` as a one-argument call.

        if name == "UNKNOWN" and self._current is not None and self._current.text == "(":
            self._take()
            captured = self._take()
            self._expect(")")
            self._count(recognised=False)
            text = _unquote(captured.text) if captured.kind == "string" else captured.text
            return CalcNode(NodeKind.UNKNOWN, value=text)

        if name == "IF":
            return self._conditional()

        if name == "CAST":
            self._count(recognised=True)
            self._expect("(")
            inner = self._binary(0)
            self._expect("AS")
            target = self._take().text.upper()
            self._expect(")")
            return CalcNode(NodeKind.CAST, name=target, children=(inner,))

        if self._current is not None and self._current.text == "(":
            if name not in FUNCTIONS and name not in WINDOWS:
                # §6.2: "Unrecognised constructs are retained verbatim as UNKNOWN(text) nodes
                # and lower parse quality." Retained means the *whole call*, skipped over
                # without parsing its arguments — an unreadable function very often contains
                # unreadable syntax too, and RAWSQL_INT('select 1') is the canonical example.
                # A parser that tried to read the arguments of a call it does not know would
                # fail the whole expression instead of recording one gap, which is the
                # difference between a workbook held with a named construct to fix and a
                # workbook that simply would not parse.
                self._count(recognised=False)
                return CalcNode(NodeKind.UNKNOWN, value=self._skip_call(token))
            arguments = self._arguments()
            self._count(recognised=True)
            kind = NodeKind.WINDOW if name in WINDOWS else NodeKind.FUNCTION
            detail = (
                (("addressing", "table"), ("partitioning", "sheet"))
                if kind is NodeKind.WINDOW
                else ()
            )
            return CalcNode(kind, name=name, children=tuple(arguments), detail=detail)

        # A bare word is a reference written without brackets.
        self._count(recognised=True)
        return CalcNode(NodeKind.REFERENCE, name=token.text)

    def _conditional(self) -> CalcNode:
        self._count(recognised=True)
        parts: list[CalcNode] = [self._binary(0)]
        self._expect("THEN")
        parts.append(self._binary(0))
        while self._accept("ELSEIF"):
            parts.append(self._binary(0))
            self._expect("THEN")
            parts.append(self._binary(0))
        if self._accept("ELSE"):
            parts.append(self._binary(0))
        self._expect("END")
        return CalcNode(NodeKind.CONDITIONAL, name="IF", children=tuple(parts))

    def _aggregate(self) -> CalcNode:
        """``{FIXED [Desk] : SUM([Amount])}`` — an LOD, in the shape §6.2 asks for."""
        self._count(recognised=True)
        grain = self._take().text.upper()
        if grain not in AGGREGATES:
            raise ParseFailure(f"expected FIXED, INCLUDE or EXCLUDE, found {grain!r}")
        dimensions: list[CalcNode] = []
        while self._current is not None and self._current.text != ":":
            dimensions.append(self._binary(0))
            if not self._accept(","):
                break
        self._expect(":")
        measure = self._binary(0)
        self._expect("}")
        return CalcNode(
            NodeKind.AGGREGATE,
            name=grain,
            children=(measure, *dimensions),
            detail=(("grain", grain),),
        )

    def _arguments(self) -> list[CalcNode]:
        self._expect("(")
        arguments: list[CalcNode] = []
        if self._accept(")"):
            return arguments
        while True:
            arguments.append(self._binary(0))
            if self._accept(","):
                continue
            self._expect(")")
            return arguments

    def _skip_call(self, name: _Token) -> str:
        """Consume ``name( ... )`` without interpreting it, and return the text verbatim.

        Balanced on parentheses only. That is enough for the constructs this matters for and
        deliberately not more: a grammar being clever about the inside of a call it does not
        understand is a grammar guessing.
        """
        self._expect("(")
        depth = 1
        while depth:
            token = self._take()
            if token.text == "(":
                depth += 1
            elif token.text == ")":
                depth -= 1
        return _slice(self._source, name, self._prev())

    def _prev(self) -> _Token:
        return self._tokens[self._at - 1]


def _unquote(text: str) -> str:
    return text[1:-1].replace('\\"', '"').replace("\\\\", "\\")


def _slice(source: str, first: _Token, last: _Token) -> str:
    return source[first.position : last.position + len(last.text)]


def parse_calc(expression: str, *, grammar_version: str = GRAMMAR_VERSION) -> CalcAST:
    """Parse one calculation into a `CalcAST` (§6.1 ``parseCalc``)."""
    tokens = _tokenise(expression)
    if not tokens:
        return CalcAST(
            root=CalcNode(NodeKind.LITERAL, name="empty", value=None),
            expression=expression,
            grammar_version=grammar_version,
        )
    parser = _Parser(tokens, expression)
    root = parser.parse()
    return CalcAST(
        root=root,
        expression=expression,
        grammar_version=grammar_version,
        recognised=parser.recognised,
        total=parser.total,
    )
