"""The conformance suite — S2.1.1 criterion 3, and §6.3.

**Every check here is tested by breaking the adapter.** §6.1 makes passing this suite the
condition of enabling an adapter on a tenant, so a check that cannot fail is not a weak test
— it is a false assurance somebody will act on. Each of §6.3's five checks gets a
deliberately broken adapter and must report it, and must report *why* in terms an engineer
can act on.

The repository already learned this once: S1.1.3's replay guard was proved non-vacuous by
tampering with the graph and confirming the check went red. Same discipline, same reason.
"""

from __future__ import annotations

from collections.abc import AsyncIterator

import pytest

from astra_adapter import Capabilities, ConformanceSuite, Corpus, Outcome, Scope
from astra_adapter.calc import CalcAST, CalcNode, NodeKind, canonical_text, check_round_trip
from astra_adapter.conformance import render
from astra_adapter.contract import INTERFACE_VERSION, AssetRef
from astra_adapter.fake import CORPUS_EXPRESSIONS, FixtureSourceAdapter, build, build_site, corpus
from astra_adapter.proof import ExecutionStrategy, ParityCase, ResultSet


def check(report, name: str):
    return next(item for item in report.checks if item.name == name)


# --------------------------------------------------------------- the suite passes


async def test_the_fake_is_conformant() -> None:
    """The baseline every "it fails when broken" test below is measured against."""
    report = await ConformanceSuite(build(), corpus()).run()

    assert report.passed, render(report)
    assert report.counts["FAILED"] == 0
    assert report.interface_version == INTERFACE_VERSION


async def test_all_five_of_section_6_3s_checks_ran() -> None:
    report = await ConformanceSuite(build(), corpus()).run()
    names = {item.name for item in report.checks}

    assert {
        "discovery completeness",
        "parse quality",
        "AST round-trip",
        "executor determinism",
        "usage and ownership",
    } <= names


# ------------------------------------------------------- each check fails when broken


async def test_enumeration_completeness_fails_on_a_missing_asset() -> None:
    class DropsOne(FixtureSourceAdapter):
        async def enumerate(self, scope: Scope) -> AsyncIterator[AssetRef]:
            seen = 0
            async for ref in super().enumerate(scope):
                seen += 1
                if seen == 2:
                    continue
                yield ref

    report = await ConformanceSuite(_as_fake(DropsOne), corpus()).run()
    result = check(report, "discovery completeness")

    assert result.outcome is Outcome.FAILED
    assert any("never enumerated" in line for line in result.detail)


async def test_enumeration_completeness_fails_on_a_duplicate() -> None:
    """A duplicate is invisible in a count and produces a second harvest of the same
    workbook, which looks like drift rather than like a bug."""

    class Repeats(FixtureSourceAdapter):
        async def enumerate(self, scope: Scope) -> AsyncIterator[AssetRef]:
            async for ref in super().enumerate(scope):
                yield ref
                yield ref

    report = await ConformanceSuite(_as_fake(Repeats), corpus()).run()
    result = check(report, "discovery completeness")

    assert result.outcome is Outcome.FAILED
    assert any("more than once" in line for line in result.detail)


async def test_parse_quality_fails_below_the_floor() -> None:
    """§6.3's floor is §4.1.4's 0.98, and the failure names the construct that caused it."""
    adapter = FixtureSourceAdapter(
        [build_site("conformance", 6, grammar_gaps=True)],
        name="fake",
        capabilities=Capabilities(extract_read=True, usage=True, ownership=True, screenshot=True),
    )
    report = await ConformanceSuite(adapter, corpus()).run()
    result = check(report, "parse quality")

    assert result.outcome is Outcome.FAILED
    assert any("unread:" in line for line in result.detail)


async def test_parse_quality_fails_on_a_missing_fragment() -> None:
    """ "Expected graph fragments" (§6.3). An adapter that parses cleanly but emits no
    worksheets has not parsed the workbook."""
    base = corpus()
    demanding = Corpus(
        name=base.name,
        scope=base.scope,
        expected_assets=base.expected_assets,
        expected_nodes={
            luid: frozenset({"Dashboard", "StoryPoint"}) for luid in base.expected_assets
        },
        expressions=(),
    )
    report = await ConformanceSuite(build(), demanding).run()
    result = check(report, "parse quality")

    assert result.outcome is Outcome.FAILED
    assert any("StoryPoint" in line for line in result.detail)


async def test_the_round_trip_check_fails_on_a_lossy_canonical_form() -> None:
    """The check the whole grammar rests on. If it cannot fail, nothing about the AST is
    being verified at all."""

    class Lossy(FixtureSourceAdapter):
        async def parse_calc(self, expression: str) -> CalcAST:
            # Drops everything after the first argument. A plausible bug: an off-by-one in
            # argument collection looks exactly like this.
            parsed = await super().parse_calc(expression)
            root = parsed.root
            if root.children:
                root = CalcNode(root.kind, root.name, root.value, root.children[:1], root.detail)
            return CalcAST(
                root, expression, parsed.grammar_version, parsed.recognised, parsed.total
            )

    report = await ConformanceSuite(_as_fake(Lossy), corpus()).run()
    result = check(report, "AST round-trip")

    assert result.outcome is Outcome.FAILED
    assert result.detail


async def test_the_round_trip_check_distinguishes_which_parse_failed() -> None:
    """A grammar that cannot read the expression and a canonical form that cannot be read
    back are different defects, and the message must send the reader to the right one."""

    async def refuses(expression: str) -> CalcAST:
        raise ValueError("no")

    trip = await check_round_trip(refuses, "SUM([x])")
    assert trip.failed_on == "source"
    assert "could not parse the expression at all" in trip.detail


async def test_the_determinism_check_fails_on_a_moving_result() -> None:
    class Drifts(FixtureSourceAdapter):
        def __init__(self, *args, **kwargs) -> None:
            super().__init__(*args, **kwargs)
            self._calls = 0

        async def execute_case(self, case: ParityCase) -> ResultSet:
            self._calls += 1
            result = await super().execute_case(case)
            return ResultSet(
                case_id=result.case_id,
                columns=result.columns,
                rows=result.rows[: max(1, len(result.rows) - self._calls)],
                strategy=result.strategy,
                interface_version=result.interface_version,
                adapter_name=result.adapter_name,
                adapter_version=result.adapter_version,
            )

    report = await ConformanceSuite(_as_fake(Drifts), corpus()).run()
    result = check(report, "executor determinism")

    assert result.outcome is Outcome.FAILED
    assert any("different result sets" in line for line in result.detail)


async def test_the_determinism_check_fails_an_unstamped_result() -> None:
    """S2.1.1 criterion 4, enforced where a ParityRun's evidence is produced."""

    class Unstamped(FixtureSourceAdapter):
        async def execute_case(self, case: ParityCase) -> ResultSet:
            result = await super().execute_case(case)
            return ResultSet(
                case_id=result.case_id,
                columns=result.columns,
                rows=result.rows,
                strategy=ExecutionStrategy.EXTRACT_READ,
                interface_version="",
                adapter_name=result.adapter_name,
                adapter_version=result.adapter_version,
            )

    report = await ConformanceSuite(_as_fake(Unstamped), corpus()).run()
    result = check(report, "executor determinism")

    assert result.outcome is Outcome.FAILED
    assert any("interface version" in line for line in result.detail)


async def test_usage_that_maps_to_nothing_fails() -> None:
    """The mapping is what is under test, not the counting: usage attached to a workbook
    nobody enumerated lands in the graph as usage of a workbook that does not exist."""
    from astra_adapter.contract import UsageKind, UsageRecord

    class Misattributes(FixtureSourceAdapter):
        async def usage(self, scope: Scope, window_days: int):
            return [
                UsageRecord(
                    asset_luid="ghost", views=10, distinct_viewers=2, kind=UsageKind.WORKBOOK
                )
            ]

    report = await ConformanceSuite(_as_fake(Misattributes), corpus()).run()
    result = check(report, "usage and ownership")

    assert result.outcome is Outcome.FAILED
    assert any("enumeration never returned" in line for line in result.detail)


async def test_more_viewers_than_views_fails() -> None:
    from astra_adapter.contract import UsageRecord

    class Impossible(FixtureSourceAdapter):
        async def usage(self, scope: Scope, window_days: int):
            refs = [ref async for ref in self.enumerate(scope)]
            return [UsageRecord(asset_luid=refs[0].luid, views=2, distinct_viewers=9)]

    report = await ConformanceSuite(_as_fake(Impossible), corpus()).run()
    result = check(report, "usage and ownership")

    assert result.outcome is Outcome.FAILED
    assert any("more distinct viewers" in line for line in result.detail)


async def test_a_workbook_with_no_owner_fails() -> None:
    class Anonymous(FixtureSourceAdapter):
        async def owners(self, scope: Scope):
            return []

    report = await ConformanceSuite(_as_fake(Anonymous), corpus()).run()
    result = check(report, "usage and ownership")

    assert result.outcome is Outcome.FAILED
    assert any("no owner reported" in line for line in result.detail)


async def test_an_interface_mismatch_is_reported_before_anything_else() -> None:
    """Running the other checks against a mismatched interface produces failures that
    describe the wrong problem."""
    from astra_adapter.contract import AdapterManifest

    class Old(FixtureSourceAdapter):
        def manifest(self) -> AdapterManifest:
            base = super().manifest()
            return AdapterManifest(
                name=base.name,
                version=base.version,
                grammar_version=base.grammar_version,
                interface_version="0.9",
                capabilities=base.capabilities,
            )

    report = await ConformanceSuite(_as_fake(Old), corpus()).run()

    assert report.checks[0].name == "interface version"
    assert report.checks[0].outcome is Outcome.FAILED
    assert not report.passed


# ------------------------------------------------------------- capabilities and skips


async def test_a_capability_the_adapter_does_not_claim_is_skipped_not_failed() -> None:
    """§6.1: an absent capability is a fact about the deployment, not a defect."""
    adapter = FixtureSourceAdapter(
        [build_site("conformance", 6)],
        name="fake",
        capabilities=Capabilities(
            extract_read=False, live_query=False, usage=False, ownership=False
        ),
    )
    report = await ConformanceSuite(adapter, corpus()).run()

    assert check(report, "executor determinism").outcome is Outcome.SKIPPED
    assert check(report, "usage and ownership").outcome is Outcome.SKIPPED
    assert report.passed, "not claiming a capability is not a failure"


async def test_a_skip_is_reported_rather_than_hidden() -> None:
    """A suite that prints "passed" after running two of five checks is a false assurance."""
    adapter = FixtureSourceAdapter(
        [build_site("conformance", 6)],
        name="fake",
        capabilities=Capabilities(usage=False, ownership=False),
    )
    text = render(await ConformanceSuite(adapter, corpus()).run())

    assert "SKIP" in text
    assert "does not claim" in text


async def test_a_claimed_capability_that_does_not_work_fails() -> None:
    """A claim is the only thing that turns an absence into a failure — so a claim that is
    not honoured has to be one."""
    from astra_adapter import UnsupportedCapability

    class Liar(FixtureSourceAdapter):
        async def execute_case(self, case: ParityCase) -> ResultSet:
            raise UnsupportedCapability("execute_case", adapter="liar")

    report = await ConformanceSuite(_as_fake(Liar), corpus()).run()
    result = check(report, "executor determinism")

    assert result.outcome is Outcome.FAILED
    assert any("claimed the capability" in line for line in result.detail)


async def test_a_check_that_raises_is_a_check_that_failed() -> None:
    """Not an aborted suite. The other four checks still have something to say, and an
    engineer reading the report needs all of it."""

    class Explodes(FixtureSourceAdapter):
        async def enumerate(self, scope: Scope):
            raise RuntimeError("boom")
            yield  # pragma: no cover

    report = await ConformanceSuite(_as_fake(Explodes), corpus()).run()

    assert not report.passed
    assert len(report.checks) >= 6, "every check still ran"
    assert any("raised" in item.summary for item in report.checks)


# -------------------------------------------------------------------- the grammar


@pytest.mark.parametrize("expression", CORPUS_EXPRESSIONS)
async def test_every_corpus_expression_round_trips(expression: str) -> None:
    trip = await check_round_trip(build().parse_calc, expression)
    assert trip.stable, trip.detail


async def test_an_unreadable_construct_is_kept_verbatim() -> None:
    """§6.2: "Unrecognised constructs are retained verbatim as UNKNOWN(text) nodes and lower
    parse quality." Verbatim is what lets the Parse Quality Queue show an engineer the thing
    itself rather than a description of it."""
    parsed = await build().parse_calc("SUM(RAWSQL_REAL('x')) + [Amount]")

    assert parsed.unrecognised == ("RAWSQL_REAL('x')",)
    assert parsed.parse_quality < 1.0
    assert "RAWSQL_REAL('x')" in canonical_text(parsed.root)


async def test_an_unreadable_call_does_not_fail_the_whole_expression() -> None:
    """The difference between a workbook held with a named construct to fix and a workbook
    that simply would not parse."""
    parsed = await build().parse_calc("RAWSQL_INT('select 1 from t where x = (2)')")

    assert parsed.root.kind is NodeKind.UNKNOWN
    assert parsed.root.value == "RAWSQL_INT('select 1 from t where x = (2)')"


async def test_the_canonical_form_is_one_spelling_per_shape() -> None:
    """Two spellings of the same calculation must print the same, or an AST hash is a hash
    of whitespace."""
    adapter = build()
    first = await adapter.parse_calc("SUM([Amount])/COUNTD([Desk])")
    second = await adapter.parse_calc("  SUM( [Amount] )  /  COUNTD( [Desk] )  ")

    assert canonical_text(first.root) == canonical_text(second.root)


async def test_precedence_is_recovered_by_the_parser_not_by_the_printer() -> None:
    adapter = build()
    parsed = await adapter.parse_calc("1 + 2 * 3")

    assert canonical_text(parsed.root) == "(1 + (2 * 3))"


def _as_fake(cls: type[FixtureSourceAdapter]) -> FixtureSourceAdapter:
    """One broken adapter over the corpus's site, claiming everything.

    Claiming everything on purpose: a broken adapter that quietly stopped claiming the
    capability it broke would be *skipped* rather than failed, and the test would pass for
    the wrong reason.
    """
    return cls(
        [build_site("conformance", 6)],
        name="fake",
        capabilities=Capabilities(
            live_query=False, extract_read=True, usage=True, ownership=True, screenshot=True
        ),
    )
