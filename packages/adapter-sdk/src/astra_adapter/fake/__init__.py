"""The fake source, its grammar, and the corpus the conformance suite runs against it.

``build()`` is the entry point the registry loads for the name ``fake``: a zero-argument
callable returning a §6.1 adapter, which is exactly what a real adapter package declares.
The SDK's own adapter is registered the same way as anyone else's, so the path that finds
`tableau` when F2.2 ships is the path that is exercised on every run today.
"""

from __future__ import annotations

from ..calc import NodeKind
from ..conformance.suite import Corpus
from ..contract import Capabilities, Scope
from ..proof import ParityCase, VisualCase
from .grammar import FAKE_GRAMMAR, GRAMMAR_VERSION, ParseFailure, parse_calc
from .source import (
    FIXTURE_GRAMMAR_GAPS,
    FixtureSite,
    FixtureSourceAdapter,
    FixtureWorkbook,
    build_site,
)

#: The site the corpus is built from. Small on purpose: §6.3's corpus is a *check*, and a
#: check that takes four minutes is one people stop running.
CORPUS_SITE = "conformance"
CORPUS_WORKBOOKS = 6

#: The **golden corpus** of calculations (S2.1.2). Two checks read it and they ask different
#: questions: *round-trip* asks whether the printer and the parser agree, and *coverage* asks
#: whether the grammar reads the corpus and the corpus exercises the grammar.
#:
#: The unreadable expressions at the end are here for the round-trip check — UNKNOWN nodes are
#: where a canonical form is most easily lost — and are excluded from the golden set the
#: coverage floor is measured against, because an expression the grammar is *known* not to
#: read is not a gap it is being asked to close.
CORPUS_EXPRESSIONS: tuple[str, ...] = (
    "[Amount]",
    "1 + 2 * 3",
    "SUM([Amount]) / COUNTD([Desk])",
    'IF [Amount] > 1000 THEN "large" ELSEIF [Amount] > 100 THEN "medium" ELSE "small" END',
    "{FIXED [Desk] : SUM([Amount])}",
    "WINDOW_SUM(SUM([Amount]))",
    "CAST([Amount] AS FLOAT)",
    'NOT ([Desk] = "Rates" AND [Amount] < 0)',
    "RAWSQL_INT('select 1')",
    "SUM(RAWSQL_REAL('x')) + [Amount]",
)

#: The subset the AST coverage floor applies to: everything the grammar claims to read.
GOLDEN_EXPRESSIONS: tuple[str, ...] = CORPUS_EXPRESSIONS[:-2]

#: The AST shapes the golden corpus must exercise (S2.1.2). A grammar defect in a shape the
#: corpus never contains is a defect the suite cannot see, so the gap is named here and the
#: check reports it against the *corpus*.
REQUIRED_NODE_KINDS = frozenset(
    {
        NodeKind.LITERAL,
        NodeKind.REFERENCE,
        NodeKind.FUNCTION,
        NodeKind.OPERATOR,
        NodeKind.CONDITIONAL,
        NodeKind.AGGREGATE,
        NodeKind.WINDOW,
        NodeKind.CAST,
    }
)


def build() -> FixtureSourceAdapter:
    """The fake adapter over the conformance corpus's site.

    Screenshot is claimed here and nowhere else in the codebase: the platform has no use for
    it before §10.6, but the conformance suite has to be able to exercise a claimed
    capability, and an SDK whose only adapter claims nothing would leave the "claimed but
    broken" path untested.
    """
    # No grammar gaps. §6.3 requires the corpus to clear the §4.1.4 floor of 0.98, so a
    # corpus seeded with constructs the adapter cannot read fails by construction — it would
    # be testing the fixture's ability to hold a workbook, not the adapter's ability to parse
    # one. Unreadable constructs belong in the *round-trip* corpus, where retaining them
    # verbatim is the property under test, and in the local demo estate, where the Parse
    # Quality Queue needs something to work down.
    return FixtureSourceAdapter(
        [build_site(CORPUS_SITE, CORPUS_WORKBOOKS)],
        name="fake",
        capabilities=Capabilities(
            live_query=False,
            extract_read=True,
            usage=True,
            ownership=True,
            screenshot=True,
        ),
    )


def corpus() -> Corpus:
    """The corpus §6.3 requires an adapter to ship with.

    Built from the same site definition the adapter serves, which is legitimate for a *fake*
    — it has no real source to sample — and is exactly what a real adapter must not do. A
    Tableau corpus is a set of checked-in ``.twbx`` files and the fragments they are expected
    to produce; on tenant enablement it is replaced by a client-provided sample (§6.3).
    """
    site = build_site(CORPUS_SITE, CORPUS_WORKBOOKS)
    luids = frozenset(workbook.luid for workbook in site.workbooks)
    expected_nodes = {
        workbook.luid: frozenset(
            {"Workbook", "Worksheet", "Datasource", "Field", "CalculatedField"}
        )
        for workbook in site.workbooks
    }
    cases = tuple(
        ParityCase(
            id=f"case-{workbook.luid}",
            workbook_luid=workbook.luid,
            sheet=f"{workbook.name} sheet 0",
            grain=("Desk",),
            measures=("Amount",),
        )
        for workbook in site.workbooks[:3]
    )
    visuals = tuple(
        VisualCase(
            id=f"visual-{workbook.luid}",
            workbook_luid=workbook.luid,
            view_name=f"{workbook.name} sheet 0",
        )
        for workbook in site.workbooks[:2]
    )
    return Corpus(
        name=f"fake/{CORPUS_SITE}",
        scope=Scope(site=CORPUS_SITE),
        expected_assets=luids,
        expected_nodes=expected_nodes,
        expressions=GOLDEN_EXPRESSIONS,
        parity_cases=cases,
        visual_cases=visuals,
        required_node_kinds=REQUIRED_NODE_KINDS,
        expected_owners=frozenset({workbook.owner_upn for workbook in site.workbooks}),
    )


def round_trip_corpus() -> Corpus:
    """The golden corpus plus the expressions the grammar is known not to read.

    Separate because the two checks want different sets: round-trip *needs* an unreadable
    construct, and coverage must not be failed for one. Used by the SDK's own tests; the
    shipped corpus is the golden one.
    """
    base = corpus()
    return Corpus(
        name=f"{base.name}/round-trip",
        scope=base.scope,
        expected_assets=base.expected_assets,
        expected_nodes=base.expected_nodes,
        expressions=CORPUS_EXPRESSIONS,
        parity_cases=base.parity_cases,
        visual_cases=base.visual_cases,
        required_node_kinds=base.required_node_kinds,
        expected_owners=base.expected_owners,
        ast_coverage_floor=0.8,
    )


__all__ = [
    "CORPUS_EXPRESSIONS",
    "CORPUS_SITE",
    "CORPUS_WORKBOOKS",
    "FAKE_GRAMMAR",
    "FIXTURE_GRAMMAR_GAPS",
    "GOLDEN_EXPRESSIONS",
    "GRAMMAR_VERSION",
    "REQUIRED_NODE_KINDS",
    "FixtureSite",
    "FixtureSourceAdapter",
    "FixtureWorkbook",
    "ParseFailure",
    "build",
    "build_site",
    "corpus",
    "parse_calc",
    "round_trip_corpus",
]
