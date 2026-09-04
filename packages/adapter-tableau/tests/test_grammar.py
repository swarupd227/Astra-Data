"""S2.3.1 — the Tableau calculation grammar and its AST.

    "Grammar covers the Tableau function set in Appendix B of the spec, LOD expressions
    (FIXED / INCLUDE / EXCLUDE), table calculations with addressing and partitioning,
    parameters, type conversions, string, date, logical and aggregation functions.
    AST nodes carry source spans so that a failing case can point to the exact text.
    Golden corpus parse rate is 100%; a construct outside the grammar is captured verbatim
    and flagged, never dropped. Grammar is versioned; parse results record the grammar
    version."

The tests that matter most are the last two criteria. A grammar that parses the happy cases
is a morning's work; one that behaves well on a calculation written in 2011 by somebody who
has left, containing a function Tableau itself no longer documents, is the difference between
a harvest that completes and one that stops.
"""

from __future__ import annotations

import pytest
from astra_adapter import CalcAST, NodeKind, canonical_text, check_round_trip, without_spans

from astra_adapter_tableau.corpus import GOLDEN_EXPRESSIONS
from astra_adapter_tableau.grammar import (
    GRAMMAR_VERSION,
    Family,
    TableauGrammar,
    family_of,
    is_table_calc,
    parse_calculation,
)
from astra_adapter_tableau.grammar.functions import KNOWN_FUNCTIONS


def parsed(expression: str) -> CalcAST:
    return parse_calculation(expression)


def kinds(ast: CalcAST) -> set[NodeKind]:
    return {node.kind for node in ast.root.walk()}


def names(ast: CalcAST) -> set[str]:
    return {node.name for node in ast.root.walk() if node.name}


# ------------------------------------------------------- Appendix B.1 coverage


@pytest.mark.parametrize("expression", GOLDEN_EXPRESSIONS)
def test_the_golden_corpus_parses_completely(expression: str) -> None:
    """S2.3.1's third criterion: *"Golden corpus parse rate is 100%"*.

    Parametrised so a failure names the expression rather than reporting "39 of 40" and
    leaving somebody to find which.
    """
    ast = parsed(expression)

    assert not ast.unrecognised, f"unread: {ast.unrecognised}"
    assert ast.parse_quality == 1.0
    assert ast.root.kind is not NodeKind.UNKNOWN


@pytest.mark.parametrize("expression", GOLDEN_EXPRESSIONS)
async def test_every_golden_expression_round_trips(expression: str) -> None:
    """§6.3's AST → canonical text → AST stability, over the whole corpus."""

    async def parse(text: str) -> CalcAST:
        return parsed(text)

    trip = await check_round_trip(parse, expression)

    assert trip.stable, trip.detail


def test_the_corpus_exercises_every_family_appendix_b_names() -> None:
    """A grammar defect in a family the corpus never contains is a defect no check can see —
    which is the gap S2.1.2's AST-coverage check reports against the *corpus*."""
    seen = {
        family_of(node.name)
        for expression in GOLDEN_EXPRESSIONS
        for node in parsed(expression).root.walk()
        if node.name
    }

    for family in (
        Family.AGGREGATE,
        Family.LOGICAL,
        Family.STRING,
        Family.DATE,
        Family.TYPE,
        Family.TABLE_CALC_SIMPLE,
        Family.TABLE_CALC_COMPLEX,
        Family.RAWSQL,
        Family.ATTR,
        Family.USER,
    ):
        assert family in seen, f"the golden corpus never exercises {family.value}"


def test_lod_expressions_carry_their_grain() -> None:
    """Appendix B.1 maps FIXED / INCLUDE / EXCLUDE to different DAX patterns — ALLEXCEPT,
    ALL, VALUES — so the grain is what the Transpiler branches on and it belongs on the node
    rather than being re-derived from the text."""
    for grain in ("FIXED", "INCLUDE", "EXCLUDE"):
        ast = parsed(f"{{ {grain} [Desk] : SUM([Notional]) }}")
        root = ast.root

        assert root.kind is NodeKind.AGGREGATE
        assert root.name == grain
        assert ("grain", grain) in root.detail
        # children[0] is the measure and the dimensions follow — the SDK's contract.
        assert root.children[0].name == "SUM"
        assert root.children[1].name == "Desk"


def test_an_lod_with_no_dimensions_is_accepted() -> None:
    """``{FIXED : SUM(x)}`` is legal Tableau and appears in real workbooks. A grammar that
    required the dimension list would reject a calculation a client has."""
    ast = parsed("{ FIXED : SUM([Notional]) }")

    assert ast.root.kind is NodeKind.AGGREGATE
    assert len(ast.root.children) == 1
    assert ast.parse_quality == 1.0


def test_a_table_calculation_says_its_addressing_is_unresolved() -> None:
    """§6.2: a table calculation's addressing and partitioning come from the *sheet*, not
    from the expression. Recording a default would let something downstream mistake it for a
    fact; S2.3.2 reads the sheet and fills them in."""
    ast = parsed("WINDOW_SUM(SUM([PnL]), FIRST(), LAST())")

    assert ast.root.kind is NodeKind.WINDOW
    assert ("addressing", "unresolved") in ast.root.detail
    assert ("partitioning", "unresolved") in ast.root.detail
    assert is_table_calc("WINDOW_SUM")


def test_a_parameter_reference_is_a_reference() -> None:
    """Tableau writes a parameter reference identically to a field reference. Guessing here
    would put the wrong kind on a node the Transpiler branches on; the workbook's parameter
    list is what decides."""
    ast = parsed("[Notional] * [Stress Factor]")

    references = [n for n in ast.root.walk() if n.kind is NodeKind.REFERENCE]
    assert {n.name for n in references} == {"Notional", "Stress Factor"}


def test_a_qualified_reference_keeps_its_datasource() -> None:
    ast = parsed("SUM([Orders].[Sales])")

    reference = next(n for n in ast.root.walk() if n.kind is NodeKind.REFERENCE)
    assert reference.name == "Sales"
    assert ("qualifier", "Orders") in reference.detail


def test_type_conversions_parse_both_ways() -> None:
    """Appendix B.1's Type family. Tableau writes them as calls; some clients' calculations
    use SQL-style CAST, and both are in real estates."""
    call = parsed("FLOAT([Notional])")
    sql = parsed("CAST([Notional] AS FLOAT)")

    assert family_of("FLOAT") is Family.TYPE
    assert sql.root.kind is NodeKind.CAST
    assert sql.root.name == "FLOAT"
    assert call.parse_quality == sql.parse_quality == 1.0


def test_operator_precedence_and_associativity() -> None:
    """Folding right would make ``10 - 3 - 2`` evaluate to 9 — a defect that survives review
    because the AST *looks* right."""
    assert canonical_text(parsed("10 - 3 - 2").root) == "((10 - 3) - 2)"
    assert canonical_text(parsed("1 + 2 * 3").root) == "(1 + (2 * 3))"
    assert canonical_text(parsed("[a] > 1 AND [b] < 2").root) == "(([a] > 1) AND ([b] < 2))"


def test_comments_and_doubled_quotes_are_handled() -> None:
    """Real workbooks are full of both, and a grammar that choked on a comment would fail a
    workbook for a line somebody wrote to be helpful."""
    commented = parsed("// the desk's own view\nSUM([Notional])")
    quoted = parsed("IF [Book] = 'O''Brien' THEN 1 ELSE 0 END")

    assert commented.root.name == "SUM"
    assert commented.parse_quality == 1.0
    literal = next(n for n in quoted.root.walk() if n.kind is NodeKind.LITERAL and n.value)
    assert literal.value == "O'Brien"


def test_a_field_named_like_a_keyword_is_not_a_keyword() -> None:
    """``[IFRS Rating]`` must not be read as ``IF`` followed by ``RS Rating``. The keyword
    terminals are anchored with word boundaries for exactly this."""
    ast = parsed("SUM([IFRS Rating]) + [Ended]")

    assert ast.parse_quality == 1.0
    assert "IFRS Rating" in names(ast)
    assert "Ended" in names(ast)


# ------------------------------------------------------------- source spans


def test_every_parsed_node_carries_a_span() -> None:
    """S2.3.1's second criterion. The difference between a parity failure reading "the
    calculation is wrong" and one that underlines the offending divide."""
    expression = "SUM([Notional]) / COUNTD([Desk])"
    ast = parsed(expression)

    for node in ast.root.walk():
        assert node.span is not None, node
        start, end = node.span
        assert 0 <= start < end <= len(expression)


def test_a_span_points_at_the_exact_text() -> None:
    expression = "IF [Sales] > 1000 THEN SUM([Notional]) ELSE 0 END"
    ast = parsed(expression)

    aggregate = next(n for n in ast.root.walk() if n.name == "SUM")

    assert aggregate.text_in(expression) == "SUM([Notional])"


def test_a_span_survives_the_wire() -> None:
    """A span that did not cross the adapter RPC would be useless: the platform is where the
    parity failure is reported, and the adapter is where the span was made."""
    from astra_adapter.rpc import wire

    ast = parsed("SUM([Notional]) / 2")
    restored = wire.decode_calc_node(wire.encode_calc_node(ast.root))

    assert restored == ast.root
    assert restored.span == ast.root.span


def test_spans_are_not_part_of_ast_shape() -> None:
    """The Pattern Library matches on shape (§9.1) and S1.3.1 hashes it. Two calculations
    differing only in whitespace are the same calculation; a span is a fact about one piece
    of text."""
    tight = parsed("SUM([Notional])/2")
    loose = parsed("SUM( [Notional] )  /  2")

    assert tight.root != loose.root, "the spans differ"
    assert without_spans(tight.root) == without_spans(loose.root)
    assert canonical_text(tight.root) == canonical_text(loose.root)


# ------------------------------------- outside the grammar: verbatim and flagged


def test_an_unknown_function_keeps_its_structure_and_is_flagged() -> None:
    """S2.3.1's third criterion. Turning it into an opaque blob would throw away arguments
    that are perfectly readable — including the fields it depends on, which the Modeller
    needs whether or not the Transpiler can emit the call."""
    ast = parsed("MADE_UP_FUNCTION([Notional], 3) + 1")

    call = next(n for n in ast.root.walk() if n.name == "MADE_UP_FUNCTION")
    assert call.kind is NodeKind.FUNCTION
    assert ("recognised", "false") in call.detail
    # A literal's `name` is its type ("number"), so the references are what to compare.
    assert [c.name for c in call.children if c.kind is NodeKind.REFERENCE] == ["Notional"]
    assert len(call.children) == 2, "and the literal argument is kept too"
    assert ast.parse_quality < 1.0, "and it counts against parse quality"
    assert ast.parse_quality > 0.5, "but the rest of the expression was read"


def test_an_expression_that_will_not_parse_is_kept_whole() -> None:
    """A client's estate contains calculations Tableau itself renders as errors. Raising
    would fail the harvest; a partial tree would lose the rest of the workbook silently."""
    ast = parsed("SUM([Notional] +++ ")

    assert ast.root.kind is NodeKind.UNKNOWN
    assert ast.root.value == "SUM([Notional] +++ "
    assert ast.root.span == (0, len("SUM([Notional] +++ "))
    assert ast.parse_quality == 0.0
    assert ast.expression == "SUM([Notional] +++ "


def test_nothing_is_ever_dropped() -> None:
    """The property behind the criterion: whatever happens, the source text is recoverable
    from the AST or from the expression it carries."""
    for expression in (
        "MADE_UP([x])",
        "SUM([Notional] +++ ",
        "",
        "{FIXED [a] : NOT_A_FUNCTION([b])}",
    ):
        ast = parsed(expression)
        assert ast.expression == expression


def test_an_empty_formula_is_not_a_failure() -> None:
    """A field somebody started and abandoned is a real thing in a real workbook. Scoring it
    zero would hold a workbook for a calculation with nothing in it."""
    ast = parsed("   ")

    assert ast.parse_quality == 1.0
    assert not ast.unrecognised


# ---------------------------------------------------------------- versioning


def test_the_grammar_is_versioned_and_every_parse_records_it() -> None:
    """S2.3.1's fourth criterion. A workbook parsed months ago has to be readable against the
    grammar that read it, and a re-harvest under a newer one is a visible change."""
    ast = parsed("SUM([Sales])")

    assert GRAMMAR_VERSION == "tableau-1"
    assert ast.grammar_version == GRAMMAR_VERSION

    older = TableauGrammar(version="tableau-0")
    assert older.parse("SUM([Sales])").grammar_version == "tableau-0"


def test_the_grammar_declares_what_it_covers() -> None:
    """The Parse Quality Queue works down what a grammar cannot read, so "not in this
    language" and "not implemented yet" have to be distinguishable."""
    declared = TableauGrammar().declared

    assert declared.version == GRAMMAR_VERSION
    assert declared.covers("SUM")
    assert declared.covers("WINDOW_SUM")
    assert declared.covers("REGEXP_REPLACE")
    assert not declared.covers("MADE_UP_FUNCTION")


def test_the_registry_orders_families_so_a_class_is_never_wrong() -> None:
    """SCRIPT_* and WINDOW_* would be read as ordinary functions and classified C1 if the
    aggregates were checked first — sending an untranslatable construct through the
    Transpiler as if it were a SUM."""
    assert family_of("SCRIPT_REAL") is Family.TABLE_CALC_COMPLEX
    assert family_of("WINDOW_SUM") is Family.TABLE_CALC_SIMPLE
    assert family_of("RAWSQL_INT") is Family.RAWSQL
    assert family_of("SUM") is Family.AGGREGATE
    assert family_of("MADE_UP") is Family.UNKNOWN


def test_appendix_b_families_are_all_represented() -> None:
    """Every family Appendix B.1 names has functions registered against it."""
    families = {family_of(name) for name in KNOWN_FUNCTIONS}

    for family in (
        Family.AGGREGATE,
        Family.LOGICAL,
        Family.STRING,
        Family.DATE,
        Family.TYPE,
        Family.TABLE_CALC_SIMPLE,
        Family.TABLE_CALC_COMPLEX,
        Family.RAWSQL,
        Family.ATTR,
    ):
        assert family in families
