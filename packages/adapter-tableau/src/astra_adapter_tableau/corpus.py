"""The conformance corpus this adapter ships — §6.3, and what S2.1.2 gates promotion on.

    "An adapter ships with a corpus of source assets and expected graph fragments … The suite
    runs in CI and on tenant enablement against a client-provided sample." — §6.3

Two corpora, and the difference is the whole point:

- `golden()` — the deployment in `golden.py`, which travels with the adapter. Run in CI on
  every change. Its expectations are exact because its estate is known.
- A **client-provided sample** — a real Tableau site, passed to
  `astra-adapter conformance --adapter tableau --corpus sample.json` at tenant enablement.
  Same suite, same checks, a different estate.

**What this corpus expects.** Discovery complete, fetch round-tripping, every golden
expression parsed and round-tripped, and every AST shape Appendix B.1 names exercised. It was
written before the grammar was, listing expressions that failed at the time — so the target
was written down rather than derived from whatever the parser turned out to accept.

S2.4.1 added **parity cases**, because the adapter can now execute one. They run against the
golden deployment's own published views by view data — the strategy that needs nothing but the
REST API — and the suite runs each three times and compares fingerprints (§6.3).

S2.4.2 added **visual cases** for the same reason: `capture_visual` now claims `screenshot`,
so §6.1's rule that a claim binds means the suite must actually run it rather than skip it.
One case names a sheet and one names the workbook's dashboard — S2.4.2's own "a PNG per sheet
and per dashboard" — so a regression in either lookup path fails the suite that ships with the
adapter, not only the one this package's own tests happen to run.
"""

from __future__ import annotations

from astra_adapter import Corpus, NodeKind, ParityCase, Scope, VisualCase

from .golden import GOLDEN_SITE, estate

#: The golden corpus of calculations (S2.3.1: "Golden corpus parse rate is 100%").
#:
#: Grouped by Appendix B.1's families, and deliberately including the awkward cases a real
#: estate is full of — comments, doubled quotes, an LOD with no dimensions, a field named like
#: a keyword — because a corpus of clean expressions certifies a parser against a language
#: nobody writes.
GOLDEN_EXPRESSIONS: tuple[str, ...] = (
    # Aggregate
    "SUM([Sales])",
    "COUNTD([Counterparty])",
    "PERCENTILE([PnL], 0.95)",
    # Arithmetic / logical
    "[Profit] / [Sales]",
    "10 - 3 - 2",
    "ZN([Notional]) * -1",
    "IF [Sales] > 1000 THEN 'large' ELSEIF [Sales] > 100 THEN 'medium' ELSE 'small' END",
    "CASE [Region] WHEN 'EU' THEN 1 WHEN 'US' THEN 2 ELSE 0 END",
    "IIF([Notional] > 0, 'long', 'short')",
    "NOT ISNULL([Maturity]) AND [Notional] > 0",
    "IFNULL([Rating], 'unrated')",
    # String
    "LEFT([Book], 3) + '-' + STR([Desk])",
    "CONTAINS(UPPER(TRIM([Counterparty])), 'BANK')",
    "REGEXP_REPLACE([Book], '[0-9]+', '')",
    # Date
    "DATEDIFF('day', [Trade Date], [Settle Date])",
    "DATETRUNC('month', [Trade Date])",
    "DATEADD('year', -1, TODAY())",
    "YEAR([Trade Date]) * 100 + MONTH([Trade Date])",
    # Type
    "FLOAT([Notional]) * [FX Rate]",
    "CAST([Notional] AS FLOAT)",
    "STR(INT([Quantity]))",
    # LOD — all three grains
    "{ FIXED [Desk] : SUM([Notional]) }",
    "{ INCLUDE [Trade Id] : AVG([PnL]) }",
    "{ EXCLUDE [Book] : SUM([Notional]) }",
    "{ FIXED : SUM([Notional]) }",
    # Table calc — simple and complex
    "WINDOW_SUM(SUM([PnL]), FIRST(), LAST())",
    "RUNNING_SUM(SUM([PnL]))",
    "TOTAL(SUM([Notional]))",
    "RANK(SUM([Notional]), 'desc')",
    "LOOKUP(SUM([PnL]), -1)",
    "INDEX() <= 10",
    # Parameters — written identically to a field reference; the AST records a REFERENCE and
    # the workbook's parameter list decides which it is.
    "[Notional] * [Stress Factor]",
    # Sets
    "[Desk] IN [Top Desks]",
    # RAWSQL — Appendix B.1 classifies it C4; recognising it is not the same as translating
    # it, and an adapter that could not even name it would leave the Transpiler nothing.
    "RAWSQL_INT('select count(*) from t')",
    # ATTR
    "ATTR([Book])",
    # User functions — S2.3.2 detects row-level security from exactly these.
    "USERNAME()",
    "ISMEMBEROF('Risk Managers')",
    # Comments and quoting, which real workbooks are full of
    "// the desk's own view\nSUM([Notional])",
    "IF [Book] = 'O''Brien' THEN 1 ELSE 0 END",
    # Nesting deep enough to be a real formula rather than a token test
    "IF {FIXED [Desk] : SUM([Notional])} > 1e6 THEN DATETRUNC('month', [Trade Date]) ELSE NULL END",
)


#: Visual cases against the golden deployment's own published views (S2.4.2).
#:
#: One sheet and one dashboard, at a size that is not the golden server's native 960x720
#: (`golden._NATIVE_VIEW_SIZE`) — a corpus that only ever asked for the native size could not
#: show that the adapter's resize path runs at all.
GOLDEN_VISUAL_CASES: tuple[VisualCase, ...] = (
    VisualCase(
        id="golden-sheet",
        workbook_luid="wb-00000",
        view_name="Workbook 0 sheet 0",
        width=800,
        height=600,
    ),
    VisualCase(
        id="golden-dashboard",
        workbook_luid="wb-00000",
        view_name="Overview",
        width=800,
        height=600,
    ),
)


#: Parity cases against the golden deployment's own published views (S2.4.1).
#:
#: They name **real views of the golden estate** — `wb-00000`'s first sheet, and a filtered
#: read of it — because §6.3's determinism check is worth nothing against a case the source
#: cannot answer. The filtered case is here deliberately: filters reach Tableau as `vf_`
#: parameters, and a case that never carried one would let a broken filter path through the
#: gate while every unfiltered case stayed green.
#:
#: Executed by view data, which needs no Hyper API and no warehouse. That is what makes them
#: runnable on any deployment rather than only on one that bought Tableau's SDK.
GOLDEN_PARITY_CASES: tuple[ParityCase, ...] = (
    ParityCase(
        id="golden-desk-amount",
        workbook_luid="wb-00000",
        sheet="Workbook 0 sheet 0",
        grain=("Desk",),
        measures=("Amount",),
    ),
    ParityCase(
        id="golden-desk-amount-filtered",
        workbook_luid="wb-00000",
        sheet="Workbook 0 sheet 1",
        grain=("Desk",),
        measures=("Amount",),
        filters=(("desk", "Rates"),),
    ),
    ParityCase(
        id="golden-second-workbook",
        workbook_luid="wb-00001",
        sheet="Workbook 1 sheet 0",
        grain=("Desk",),
        measures=("Amount",),
    ),
)


def golden() -> Corpus:
    """The corpus the adapter ships with (§6.3).

    ``expected_assets`` are the golden deployment's LUIDs, so discovery completeness is a set
    comparison against a known estate rather than a count of whatever came back.
    """
    site = estate()
    return Corpus(
        name=f"tableau/golden/{GOLDEN_SITE}",
        scope=Scope(site=GOLDEN_SITE),
        expected_assets=frozenset(item.luid for item in site.workbooks),
        # Deliberately empty until F2.3. A corpus asserting node types no parser produces
        # would report the same failure twice and say nothing extra.
        expected_nodes={},
        expressions=GOLDEN_EXPRESSIONS,
        parity_cases=GOLDEN_PARITY_CASES,
        visual_cases=GOLDEN_VISUAL_CASES,
        # Every AST shape the grammar produces has to be exercised by the corpus. A grammar
        # defect in a shape the corpus never contains is a defect the suite cannot see, and
        # S2.1.2's coverage check reports that gap against the *corpus* rather than the
        # grammar — which is the direction that gets it fixed.
        required_node_kinds=frozenset(
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
        ),
        expected_owners=frozenset(),
    )
