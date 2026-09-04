"""The calculation AST an adapter's grammar produces — specification §6.1 ``parseCalc``.

**What this module is.** The *shape* of a parsed calculation and the canonical text form of
it, plus the round-trip property §6.3 checks. It is deliberately source-agnostic: a Tableau
LOD expression, a Power BI measure and a Looker dimension are all function calls, operators,
literals and references over some grammar, and the platform's pattern matching (§9) works on
shapes rather than on any one language's syntax.

**What this module is not.** A Tableau grammar. §5.4 commits the real ones to Lark, versioned
with the adapter, and F2.3 builds Tableau's. An adapter supplies the parser; this supplies
the type it must produce and the rules that type must obey.

**Why the canonical text matters.** §6.3 requires "AST round-trip (AST → canonical text →
AST) stability". That property is not decoration: the Pattern Library matches on AST shape
(S1.3.1's ``ast_shape``), the Transpiler emits from an AST, and the Mender diagnoses by
comparing one AST to another. If printing an AST and re-parsing it does not produce the same
AST, then none of those three can be trusted to be talking about the same calculation — and
the failure would surface as a wrong DAX measure months later rather than as a red test now.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class NodeKind(str, Enum):
    """The node types §6.2 requires a Tableau grammar to produce, generalised.

    §6.2's list — "function calls, operators, LOD expressions (FIXED|INCLUDE|EXCLUDE), table
    calculations (with addressing/partitioning taken from the sheet), IF/CASE, type casts,
    date functions, string functions and parameter references" — is a Tableau list. The
    kinds here are the shapes behind it: a date function and a string function are both
    ``FUNCTION``, and what distinguishes them is the name, not the node type. A grammar that
    needed a genuinely new *shape* would be a contract change, which is what
    ``INTERFACE_VERSION`` is for.
    """

    LITERAL = "LITERAL"
    """A constant. ``value`` carries it; ``name`` carries its type name where the source
    distinguishes them (``integer``, ``string``, ``date``)."""

    REFERENCE = "REFERENCE"
    """A field, column or parameter. ``name`` is the source's name for it."""

    FUNCTION = "FUNCTION"
    """A named call. ``children`` are its arguments, in order."""

    OPERATOR = "OPERATOR"
    """An infix or prefix operator. ``name`` is the symbol; arity is ``len(children)``."""

    CONDITIONAL = "CONDITIONAL"
    """IF / CASE. Children alternate condition and result, with an optional final else."""

    AGGREGATE = "AGGREGATE"
    """An aggregation whose grain is not the row — a Tableau LOD, a DAX CALCULATE. ``name``
    is the aggregation; ``detail`` carries the grain (``FIXED``, ``INCLUDE``, ``EXCLUDE``)
    and ``children[1:]`` the dimensions it is taken at."""

    WINDOW = "WINDOW"
    """A table calculation. Its addressing and partitioning come from the *sheet*, not from
    the expression (§6.2), so they live in ``detail`` and an AST without them is incomplete
    rather than merely unadorned."""

    CAST = "CAST"
    """A type cast. ``name`` is the target type."""

    UNKNOWN = "UNKNOWN"
    """A construct the grammar could not read, retained **verbatim** in ``value`` (§6.2:
    "Unrecognised constructs are retained verbatim as UNKNOWN(text) nodes and lower parse
    quality"). Keeping the text is what lets the Parse Quality Queue show an engineer the
    thing itself rather than a description of it."""


@dataclass(frozen=True, slots=True)
class CalcNode:
    """One node of a parsed calculation."""

    kind: NodeKind
    name: str = ""
    value: Any = None
    children: tuple[CalcNode, ...] = ()
    detail: tuple[tuple[str, str], ...] = ()
    """Grammar-specific qualifiers as sorted key/value pairs — a window's partitioning, an
    LOD's grain. A tuple rather than a dict so the node stays hashable and comparable, which
    is what makes AST equality a usable test assertion."""

    span: tuple[int, int] | None = None
    """Character offsets into the source expression: ``(start, end)``, end-exclusive.

    S2.3.1: *"AST nodes carry source spans so that a failing case can point to the exact
    text."* That is the difference between a parity failure reading "the calculation is wrong"
    and one reading ``SUM([Notional]) / [FX Rate]`` with the offending divide underlined — and
    the second is what an engineer can act on at two in the morning.

    Offsets rather than line and column because a Tableau calculation is one string, and a
    caller that wants a line can derive it. ``None`` where a node was synthesised rather than
    parsed — a canonical form re-parsed from text has spans into *that* text, so a node with
    no span is a node nobody should be pointing a user at.

    Excluded from the canonical text and therefore from AST shape: two calculations differing
    only in whitespace must produce the same shape, and a span is a fact about a particular
    piece of text rather than about the calculation. See ``canonical_text``."""

    @property
    def source(self) -> str:
        """A hint of what this node is, for a message. Empty without a span; use
        ``text_of`` with the expression to get the actual source."""
        return "" if self.span is None else f"[{self.span[0]}:{self.span[1]}]"

    def text_in(self, expression: str) -> str:
        """The exact source text this node was parsed from (S2.3.1)."""
        if self.span is None:
            return ""
        start, end = self.span
        return expression[start:end]

    def walk(self) -> list[CalcNode]:
        """This node and every descendant, parents before children."""
        found = [self]
        for child in self.children:
            found.extend(child.walk())
        return found

    @property
    def unrecognised(self) -> tuple[str, ...]:
        """Every verbatim construct the grammar could not read, in encounter order."""
        return tuple(str(node.value) for node in self.walk() if node.kind is NodeKind.UNKNOWN)


@dataclass(frozen=True, slots=True)
class CalcAST:
    """A parsed calculation, and the grammar that parsed it.

    ``grammar_version`` is on the AST rather than only on the manifest because an AST
    outlives the call that produced it: it is stored on the graph, matched against patterns
    and compared by the Mender. "Which grammar read this" is a property of the artefact.
    """

    root: CalcNode
    expression: str
    """The source text, verbatim. Kept because a canonical form is a *normalisation* and
    an engineer reviewing a rewrite is entitled to see what was actually written."""

    grammar_version: str
    recognised: int = 0
    total: int = 0

    @property
    def parse_quality(self) -> float:
        """Recognised constructs ÷ total (§4.1.4). One for an empty parse: a calculation
        with nothing in it has no unread constructs, and scoring it zero would drag a
        workbook below the threshold for containing an empty calculated field."""
        return 1.0 if self.total == 0 else self.recognised / self.total

    @property
    def unrecognised(self) -> tuple[str, ...]:
        return self.root.unrecognised


def without_spans(node: CalcNode) -> CalcNode:
    """The same AST with every span removed.

    Two calculations that differ only in whitespace must compare equal as *shapes* — the
    Pattern Library matches on shape (§9.1) and S1.3.1's ``ast_shape`` hashes it — and a span
    is a fact about one piece of text rather than about the calculation. So spans are carried
    for pointing at source and dropped for comparing structure.
    """
    return CalcNode(
        kind=node.kind,
        name=node.name,
        value=node.value,
        children=tuple(without_spans(child) for child in node.children),
        detail=node.detail,
        span=None,
    )


def canonical_text(node: CalcNode) -> str:
    """The canonical text form of an AST — one spelling per shape.

    Fully parenthesised and single-spaced, because canonical means *unambiguous*, not
    *pretty*: this text exists to be re-parsed and hashed, and precedence recovered from
    spacing is precedence waiting to be recovered wrongly. An UNKNOWN node prints as
    ``UNKNOWN(<verbatim>)``, which is what §6.2 asks for and what makes a round-trip through
    an unreadable construct stable rather than lossy.
    """
    match node.kind:
        case NodeKind.LITERAL:
            # NULL rather than JSON's ``null`` because the canonical text has to be readable
            # by the grammar that produced it, and a grammar reading ``null`` as a bare word
            # would give back a reference to a field called "null".
            return "NULL" if node.value is None else json.dumps(node.value)
        case NodeKind.REFERENCE:
            return f"[{node.name}]"
        case NodeKind.UNKNOWN:
            return f"UNKNOWN({json.dumps(node.value)})"
        case NodeKind.CAST:
            return f"CAST({canonical_text(node.children[0])} AS {node.name})"
        case NodeKind.OPERATOR:
            if len(node.children) == 1:
                return f"({node.name} {canonical_text(node.children[0])})"
            joined = f" {node.name} ".join(canonical_text(c) for c in node.children)
            return f"({joined})"
        case NodeKind.CONDITIONAL:
            # Printed in the source language, not as a call.
            #
            # ``IF(a, b, c)`` was the earlier form, and it round-tripped *by text* while
            # silently changing shape: a grammar reading it back sees a function named IF and
            # produces a FUNCTION node. The text check passed and the AST was different, which
            # is the worst outcome available — a check that reports stability it does not have.
            children = list(node.children)
            if node.name == "CASE" and children:
                subject, *rest = children
                clauses = "".join(
                    f" WHEN {canonical_text(rest[index])} THEN {canonical_text(rest[index + 1])}"
                    for index in range(0, len(rest) - 1, 2)
                )
                trailing = f" ELSE {canonical_text(rest[-1])}" if len(rest) % 2 else ""
                return f"CASE {canonical_text(subject)}{clauses}{trailing} END"

            if not children:
                return "IF END"
            head = f"IF {canonical_text(children[0])} THEN {canonical_text(children[1])}"
            middle = "".join(
                f" ELSEIF {canonical_text(children[index])} "
                f"THEN {canonical_text(children[index + 1])}"
                for index in range(2, len(children) - 1, 2)
            )
            trailing = f" ELSE {canonical_text(children[-1])}" if len(children) % 2 else ""
            return f"{head}{middle}{trailing} END"
        case NodeKind.AGGREGATE if node.name in {"FIXED", "INCLUDE", "EXCLUDE"}:
            # A level-of-detail expression, printed in the source language's own syntax:
            # ``{FIXED [Desk] : SUM([Notional])}``. children[0] is the measure and the rest
            # are the dimensions.
            measure, *dimensions = node.children
            grain = ", ".join(canonical_text(child) for child in dimensions)
            separator = f" {grain} " if grain else " "
            return f"{{{node.name}{separator}: {canonical_text(measure)}}}"
        case _:
            # FUNCTION, AGGREGATE and WINDOW share a printed form: a name and its arguments.
            #
            # **``detail`` is deliberately not printed.** An earlier version emitted it as
            # ``NAME{family=aggregate}(...)``, which no source grammar can read back — so the
            # round-trip §6.3 checks could only pass for a grammar taught to read a syntax its
            # own language does not have. And it is not needed: ``family`` is *derived* from
            # the name, so two expressions with the same text have the same family; and a
            # window's addressing and partitioning come from the sheet (§6.2), so they are not
            # part of the expression at all. Canonical text is the language, normalised.
            arguments = ", ".join(canonical_text(child) for child in node.children)
            return f"{node.name}({arguments})"


@dataclass(frozen=True, slots=True)
class RoundTrip:
    """The result of §6.3's AST round-trip check for one expression."""

    expression: str
    stable: bool
    first_text: str
    second_text: str
    error: str | None = None
    failed_on: str = ""
    """Which parse raised — ``"source"`` or ``"canonical"``. They are different defects: the
    first is a grammar that cannot read the expression at all, the second a canonical form
    the grammar cannot read back. Reporting both as "re-parsing raised" sends the reader to
    the printer when the parser is at fault."""

    @property
    def detail(self) -> str:
        if self.error and self.failed_on == "source":
            return f"the grammar could not parse the expression at all: {self.error}"
        if self.error:
            return f"re-parsing the canonical text raised: {self.error}"
        if self.stable:
            return "canonical text re-parses to the same AST"
        return f"canonical text changed on re-parse:\n  {self.first_text}\n  {self.second_text}"


async def check_round_trip(parse: Any, expression: str) -> RoundTrip:
    """AST → canonical text → AST, and the two must agree (§6.3).

    Compared on the **canonical text** of both ASTs rather than on the AST objects: the
    property under test is that the text form determines the shape, and comparing text
    reports a readable difference when it does not. Comparing dataclasses would report that
    two large nested objects are unequal, which is true and useless.

    ``parse`` is §6.1's ``parse_calc``, which is async — so this is too, and it works
    unchanged against an in-process adapter and one on the other end of a socket.
    """
    try:
        first = await parse(expression)
        first_text = canonical_text(first.root)
    except Exception as exc:  # an adapter's grammar, not ours
        return RoundTrip(
            expression, False, "", "", error=f"{type(exc).__name__}: {exc}", failed_on="source"
        )
    try:
        second = await parse(first_text)
        second_text = canonical_text(second.root)
    except Exception as exc:
        return RoundTrip(
            expression,
            False,
            first_text,
            "",
            error=f"{type(exc).__name__}: {exc}",
            failed_on="canonical",
        )
    return RoundTrip(expression, first_text == second_text, first_text, second_text)


@dataclass(frozen=True, slots=True)
class Grammar:
    """What an adapter's calculation grammar is, as a fact rather than a version string.

    §5.4 versions the grammar *with the adapter*, and the Parse Quality Queue works down the
    constructs a grammar cannot read. Both need to know what a grammar claims to cover, so a
    grammar declares its functions rather than leaving "unrecognised" to mean both "not in
    this language" and "not implemented yet".
    """

    version: str
    functions: frozenset[str] = field(default_factory=frozenset)
    operators: frozenset[str] = field(default_factory=frozenset)

    def covers(self, name: str) -> bool:
        return name.upper() in self.functions or name in self.operators
