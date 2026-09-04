"""S2.1.2 — the suite as the definition of "an adapter works".

    "Suite covers: discovery completeness, parse round-trip (parse → serialise → parse
    identity), AST coverage on the golden corpus, execution determinism (same case twice,
    same result), visual capture, error taxonomy, throttling behaviour"

Each new area gets the same treatment as S2.1.1's: a deliberately broken adapter that the
check must catch. *"Adapter acceptance is a test result, not an opinion"* is the story's own
phrasing, and a check that cannot fail turns the test result back into an opinion.
"""

from __future__ import annotations

import contextlib
import json
import os

import pytest

from astra_adapter import (
    Capabilities,
    ConformanceSuite,
    Corpus,
    Fault,
    Outcome,
    RateLimited,
    Scope,
    classify,
)
from astra_adapter.conformance import SIGNING_KEY_ENV, SignedReport, sign, verify
from astra_adapter.contract import AdapterError, EdgeFragment, NodeFragment, ParseResult, RawAsset
from astra_adapter.fake import (
    REQUIRED_NODE_KINDS,
    FixtureSourceAdapter,
    build,
    build_site,
    corpus,
    round_trip_corpus,
)
from astra_adapter.proof import VisualCapture, VisualCase

KEY = "a-local-development-key"


def check(report, name: str):
    return next(item for item in report.checks if item.name == name)


def broken(cls: type[FixtureSourceAdapter]) -> FixtureSourceAdapter:
    """One broken adapter claiming every capability — so a break is failed, not skipped."""
    return cls(
        [build_site("conformance", 6)],
        name="fake",
        capabilities=Capabilities(
            live_query=False, extract_read=True, usage=True, ownership=True, screenshot=True
        ),
    )


# ------------------------------------------------------- every area is covered


async def test_the_suite_covers_every_area_the_story_names() -> None:
    """S2.1.2 criterion 1, checked as a list rather than trusted.

    The seven areas are the story's, and a suite that quietly stopped running one of them
    would still print "CONFORMANT" — which is the failure this test exists to prevent.
    """
    report = await ConformanceSuite(build(), corpus()).run()
    names = {item.name for item in report.checks}

    assert {
        "discovery completeness",
        "parse round-trip",
        "AST coverage",
        "executor determinism",
        "visual capture",
        "error taxonomy",
        "throttling",
    } <= names
    assert report.passed, "\n".join(f"{c.name}: {c.summary}" for c in report.checks)


# ------------------------------------------------------------ parse round-trip


async def test_parse_round_trip_fails_when_parsing_is_not_a_function_of_the_bytes() -> None:
    """An adapter carrying state between parses produces a graph that differs between two
    harvests of an unchanged workbook, which the platform reads as drift."""

    class Drifts(FixtureSourceAdapter):
        def __init__(self, *args, **kwargs) -> None:
            super().__init__(*args, **kwargs)
            self._seen = 0

        async def parse(self, raw: RawAsset) -> ParseResult:
            self._seen += 1
            result = await super().parse(raw)
            return ParseResult(
                nodes=result.nodes[: len(result.nodes) - (self._seen % 2)],
                edges=result.edges,
                parse_quality=result.parse_quality,
                unrecognised=result.unrecognised,
                constructs_total=result.constructs_total,
                constructs_recognised=result.constructs_recognised,
            )

    result = check(await ConformanceSuite(broken(Drifts), corpus()).run(), "parse round-trip")

    assert result.outcome is Outcome.FAILED
    assert any("different fragments" in line for line in result.detail)


async def test_parse_round_trip_fails_when_a_fragment_does_not_survive_serialisation() -> None:
    """Every parse result crosses the adapter RPC. A field that encodes but does not decode
    vanishes silently on the way into the graph."""

    class Unencodable(FixtureSourceAdapter):
        async def parse(self, raw: RawAsset) -> ParseResult:
            result = await super().parse(raw)
            # A node type the wire keeps but whose properties will not survive: a set is not
            # JSON, and json.dumps would raise — so use a value that *round-trips wrongly*,
            # which is the quieter and more dangerous case.
            first = result.nodes[0]
            return ParseResult(
                nodes=(
                    NodeFragment(
                        key=first.key,
                        type=first.type,
                        properties={**first.properties, "tuple_property": (1, 2)},
                    ),
                    *result.nodes[1:],
                ),
                edges=result.edges,
                parse_quality=result.parse_quality,
                unrecognised=result.unrecognised,
                constructs_total=result.constructs_total,
                constructs_recognised=result.constructs_recognised,
            )

    result = check(await ConformanceSuite(broken(Unencodable), corpus()).run(), "parse round-trip")

    assert result.outcome is Outcome.FAILED
    assert any("serialisation" in line for line in result.detail)


async def test_parse_round_trip_names_what_was_lost() -> None:
    """ "Two objects are unequal" is true and useless; a name is actionable."""

    class LosesAnEdge(FixtureSourceAdapter):
        async def parse(self, raw: RawAsset) -> ParseResult:
            result = await super().parse(raw)
            edges = tuple(result.edges)
            return ParseResult(
                nodes=result.nodes,
                edges=(
                    EdgeFragment(
                        type=edges[0].type,
                        from_key=edges[0].from_key,
                        to_key=edges[0].to_key,
                        properties={"weight": (1, 2)},
                    ),
                    *edges[1:],
                ),
                parse_quality=result.parse_quality,
                unrecognised=result.unrecognised,
                constructs_total=result.constructs_total,
                constructs_recognised=result.constructs_recognised,
            )

    result = check(await ConformanceSuite(broken(LosesAnEdge), corpus()).run(), "parse round-trip")
    assert result.outcome is Outcome.FAILED
    assert any("edge" in line for line in result.detail)


# --------------------------------------------------------------- AST coverage


async def test_ast_coverage_fails_when_the_grammar_cannot_read_its_golden_corpus() -> None:
    """The golden corpus is the set an adapter claims to read. A grammar that cannot read
    its own is not ready to meet a client's."""
    unreadable = Corpus(
        name="unreadable",
        scope=Scope(site="conformance"),
        expressions=("TOTALLY_MADE_UP(1)", "ALSO_MADE_UP(2)", "SUM([Amount])"),
    )
    result = check(await ConformanceSuite(build(), unreadable).run(), "AST coverage")

    assert result.outcome is Outcome.FAILED
    assert any("below the corpus's floor" in line for line in result.detail)


async def test_ast_coverage_fails_when_the_corpus_never_exercises_a_shape() -> None:
    """The gap in the *corpus*, which is the more dangerous of the two: a grammar defect in
    a shape the corpus does not contain is a defect the suite cannot see."""
    thin = Corpus(
        name="thin",
        scope=Scope(site="conformance"),
        expressions=("SUM([Amount])", "[Desk]"),
        required_node_kinds=REQUIRED_NODE_KINDS,
    )
    result = check(await ConformanceSuite(build(), thin).run(), "AST coverage")

    assert result.outcome is Outcome.FAILED
    assert any("never exercises" in line for line in result.detail)
    assert any("AGGREGATE" in line or "WINDOW" in line for line in result.detail)


async def test_the_golden_corpus_exercises_every_required_shape() -> None:
    """The shipped corpus, held to its own standard."""
    result = check(await ConformanceSuite(build(), corpus()).run(), "AST coverage")

    assert result.outcome is Outcome.PASSED
    assert "100%" in result.summary


async def test_round_trip_and_coverage_read_different_corpora() -> None:
    """Round-trip *needs* an unreadable construct; coverage must not be failed for one.

    Same set for both would force a choice between an untested round-trip through UNKNOWN
    and a coverage floor that can never be met.
    """
    report = await ConformanceSuite(build(), round_trip_corpus()).run()

    assert check(report, "AST round-trip").outcome is Outcome.PASSED
    assert check(report, "AST coverage").outcome is Outcome.PASSED
    assert report.passed


# -------------------------------------------------------------- visual capture


async def test_visual_capture_fails_on_a_blank_image() -> None:
    """§10.6 compares two images perceptually; a blank capture scores as *difference* and
    sends a reviewer to a visual that never drifted."""

    class Blank(FixtureSourceAdapter):
        async def capture_visual(self, case: VisualCase) -> VisualCapture:
            capture = await super().capture_visual(case)
            return VisualCapture(
                case_id=capture.case_id,
                image=b"",
                width=case.width,
                height=case.height,
                interface_version=capture.interface_version,
            )

    result = check(await ConformanceSuite(broken(Blank), corpus()).run(), "visual capture")

    assert result.outcome is Outcome.FAILED
    assert any("no image bytes" in line for line in result.detail)


async def test_visual_capture_fails_on_bytes_that_are_not_the_declared_format() -> None:
    class NotAPng(FixtureSourceAdapter):
        async def capture_visual(self, case: VisualCase) -> VisualCapture:
            return VisualCapture(
                case_id=case.id,
                image=b"this is not a png",
                media_type="image/png",
                width=case.width,
                height=case.height,
                interface_version="1.0",
            )

    result = check(await ConformanceSuite(broken(NotAPng), corpus()).run(), "visual capture")

    assert result.outcome is Outcome.FAILED
    assert any("not a image/png" in line for line in result.detail)


async def test_visual_capture_fails_on_the_wrong_size() -> None:
    class WrongSize(FixtureSourceAdapter):
        async def capture_visual(self, case: VisualCase) -> VisualCapture:
            capture = await super().capture_visual(case)
            return VisualCapture(
                case_id=capture.case_id,
                image=capture.image,
                width=99,
                height=99,
                interface_version=capture.interface_version,
            )

    result = check(await ConformanceSuite(broken(WrongSize), corpus()).run(), "visual capture")

    assert result.outcome is Outcome.FAILED
    assert any("asked for" in line for line in result.detail)


async def test_visual_capture_is_skipped_when_screenshot_is_not_claimed() -> None:
    adapter = FixtureSourceAdapter(
        [build_site("conformance", 6)],
        name="fake",
        capabilities=Capabilities(extract_read=True, usage=True, ownership=True, screenshot=False),
    )
    report = await ConformanceSuite(adapter, corpus()).run()

    assert check(report, "visual capture").outcome is Outcome.SKIPPED
    assert report.passed


async def test_claiming_screenshot_with_no_views_in_the_corpus_fails() -> None:
    """A claim nothing exercises is a claim nobody has checked."""
    base = corpus()
    without = Corpus(
        name=base.name,
        scope=base.scope,
        expected_assets=base.expected_assets,
        expressions=base.expressions,
        visual_cases=(),
    )
    result = check(await ConformanceSuite(build(), without).run(), "visual capture")

    assert result.outcome is Outcome.FAILED
    assert "no views" in result.summary


# --------------------------------------------------------------- error taxonomy


async def test_the_error_taxonomy_check_passes_on_a_correct_adapter() -> None:
    result = check(await ConformanceSuite(build(), corpus()).run(), "error taxonomy")

    assert result.outcome is Outcome.PASSED
    assert "transient → AdapterError(retryable=True)" in result.summary


async def test_a_transient_failure_marked_permanent_fails() -> None:
    """A network blip clears. A workbook lost to one is a workbook lost for no reason."""

    class MislabelsTransient(FixtureSourceAdapter):
        async def _source_call(self) -> None:
            try:
                await super()._source_call()
            except AdapterError as exc:
                if exc.retryable:
                    raise AdapterError(str(exc), retryable=False) from exc
                raise

    result = check(
        await ConformanceSuite(broken(MislabelsTransient), corpus()).run(), "error taxonomy"
    )

    assert result.outcome is Outcome.FAILED
    assert any("expected retryable" in line for line in result.detail)
    assert any("A network blip clears" in line for line in result.detail)


async def test_a_rejected_credential_marked_retryable_fails() -> None:
    """Retrying with the same rejected credential fails identically, and marks every
    workbook in the estate as individually failed."""

    class RetriesForever(FixtureSourceAdapter):
        async def _source_call(self) -> None:
            try:
                await super()._source_call()
            except AdapterError as exc:
                raise AdapterError(str(exc), retryable=True) from exc

    result = check(await ConformanceSuite(broken(RetriesForever), corpus()).run(), "error taxonomy")

    assert result.outcome is Outcome.FAILED
    assert any("unauthorised" in line for line in result.detail)


async def test_an_adapter_with_no_fault_injector_fails_rather_than_skips() -> None:
    """The judgement this story turns on: "we could not check" recorded as a pass is exactly
    the false assurance the suite exists to prevent."""

    class NoHook:
        """A §6.1 adapter with no conformance hook, by composition rather than inheritance."""

        def __init__(self, inner: FixtureSourceAdapter) -> None:
            self._inner = inner

        def manifest(self):
            return self._inner.manifest()

        def enumerate(self, scope):
            return self._inner.enumerate(scope)

        def __getattr__(self, name: str):
            return getattr(self._inner, name)

    report = await ConformanceSuite(NoHook(build()), corpus()).run()

    for name in ("error taxonomy", "throttling"):
        result = check(report, name)
        assert result.outcome is Outcome.FAILED, name
        assert "cannot be certified for behaviour nobody has seen" in result.summary
    assert not report.passed


async def test_classify_names_the_bucket_rather_than_the_type() -> None:
    assert classify(RateLimited("slow down", retry_after=30)) == "RateLimited, retry_after=30"
    assert classify(AdapterError("x", retryable=True)) == "AdapterError(retryable=True)"
    assert "outside the taxonomy" in classify(ValueError("x"))


# ------------------------------------------------------------------- throttling


async def test_the_throttling_check_passes_when_the_adapter_backs_off() -> None:
    adapter = build()
    result = check(await ConformanceSuite(adapter, corpus()).run(), "throttling")

    assert result.outcome is Outcome.PASSED
    assert adapter.throttle_waits > 0, "the backoff must actually have happened"


async def test_an_adapter_that_gives_up_on_a_429_fails() -> None:
    """§6.2 requires backoff on 429. At 1,067 workbooks against a rate-limited site, an
    adapter that gives up loses most of the estate."""

    class GivesUp(FixtureSourceAdapter):
        MAX_THROTTLE_RETRIES = 0

    result = check(await ConformanceSuite(broken(GivesUp), corpus()).run(), "throttling")

    assert result.outcome is Outcome.FAILED
    assert any("backing off" in line or "backoff" in line for line in result.detail)


async def test_persistent_throttling_surfaced_as_a_plain_failure_fails() -> None:
    """The platform cannot tell "wait" from "this workbook is broken", and records the
    workbook as failed."""

    class LosesTheDistinction(FixtureSourceAdapter):
        async def _source_call(self) -> None:
            try:
                await super()._source_call()
            except RateLimited as exc:
                raise AdapterError(str(exc), retryable=False) from exc

    result = check(
        await ConformanceSuite(broken(LosesTheDistinction), corpus()).run(), "throttling"
    )

    assert result.outcome is Outcome.FAILED
    assert any("cannot tell 'wait' from" in line for line in result.detail)


async def test_an_adapter_that_never_surfaces_throttling_fails() -> None:
    """Retrying forever is a harvest that never finishes and never says why."""

    class RetriesForever(FixtureSourceAdapter):
        async def _source_call(self) -> None:
            while True:
                try:
                    await super()._source_call()
                    return
                except RateLimited:
                    await self.set_fault(Fault.NONE)

    result = check(await ConformanceSuite(broken(RetriesForever), corpus()).run(), "throttling")

    assert result.outcome is Outcome.FAILED
    assert any("retried forever" in line for line in result.detail)


async def test_a_fault_that_does_not_clear_is_caught() -> None:
    """Otherwise every check after this one runs against a broken source and reports the
    wrong thing."""

    class Sticks(FixtureSourceAdapter):
        async def set_fault(self, fault: Fault, *, count: int = 1) -> None:
            # Ignores the clear, which is the realistic form of this bug: a client whose
            # throttle state is per-connection and whose reset was never wired up.
            if fault is not Fault.NONE:
                await super().set_fault(fault, count=10_000)

    result = check(await ConformanceSuite(broken(Sticks), corpus()).run(), "error taxonomy")

    assert result.outcome is Outcome.FAILED
    assert any("did not recover" in line for line in result.detail)


# ---------------------------------------------------------------------- signing


async def test_a_report_is_hashed_and_signed() -> None:
    report = await ConformanceSuite(build(), corpus()).run()
    signed = sign(report, key=KEY)

    assert signed.signed
    assert signed.algorithm == "hmac-sha256"
    assert signed.content_hash.startswith("sha256:")
    ok, why = verify(signed, key=KEY)
    assert ok, why


async def test_a_deployment_with_no_key_hashes_and_says_it_is_unsigned() -> None:
    """A report that claimed a signature it did not have would be worse than an unsigned
    one, because an unsigned report is obviously unsigned."""
    report = await ConformanceSuite(build(), corpus()).run()
    signed = sign(report, key="")

    assert not signed.signed
    assert signed.signature is None
    assert signed.content_hash.startswith("sha256:")
    assert SIGNING_KEY_ENV in (signed.key_id or "")

    ok, why = verify(signed, key=KEY)
    assert not ok
    assert "hashed but not signed" in why


async def test_altering_a_report_breaks_its_hash() -> None:
    """The forgery worth preventing: a failing run and a report that says it passed are
    indistinguishable to whoever reads the second."""
    report = await ConformanceSuite(build(), corpus()).run()
    signed = sign(report, key=KEY)

    tampered = SignedReport(
        report={**signed.report, "passed": True, "adapter_version": "9.9.9"},
        content_hash=signed.content_hash,
        signed=True,
        signature=signed.signature,
        algorithm=signed.algorithm,
        key_id=signed.key_id,
    )
    ok, why = verify(tampered, key=KEY)

    assert not ok
    assert "altered since it was written" in why


async def test_a_signature_from_another_key_is_rejected() -> None:
    report = await ConformanceSuite(build(), corpus()).run()
    signed = sign(report, key=KEY)

    ok, why = verify(signed, key="somebody-elses-key")

    assert not ok
    assert "different key" in why


async def test_the_signature_covers_the_content_not_the_encoding() -> None:
    """Canonical JSON, for the same reason `context_hash` uses it (S1.3.1): a hash that
    depends on how a dictionary happened to be serialised is a hash of the serialiser."""
    report = await ConformanceSuite(build(), corpus()).run()
    signed = sign(report, key=KEY)

    # Round-tripping through JSON reorders nothing semantically but changes byte order.
    reordered = SignedReport.from_dict(json.loads(json.dumps(signed.as_dict())))
    ok, why = verify(reordered, key=KEY)

    assert ok, why


async def test_the_key_comes_from_the_environment_when_not_passed(monkeypatch) -> None:
    """So the caller never holds it, which keeps it out of argument lists and tracebacks."""
    monkeypatch.setenv(SIGNING_KEY_ENV, KEY)
    report = await ConformanceSuite(build(), corpus()).run()

    signed = sign(report)

    assert signed.signed
    assert os.environ[SIGNING_KEY_ENV] == KEY
    ok, _ = verify(signed)
    assert ok


async def test_a_failing_report_is_signed_too() -> None:
    """A failing report is the evidence that an adapter must not be promoted; it is the one
    somebody has the strongest motive to replace."""

    class Broken(FixtureSourceAdapter):
        async def owners(self, scope: Scope):
            return []

    report = await ConformanceSuite(broken(Broken), corpus()).run()
    signed = sign(report, key=KEY)

    assert not signed.passed
    assert signed.signed
    ok, _ = verify(signed, key=KEY)
    assert ok


@pytest.mark.parametrize("fault", list(Fault))
async def test_every_fault_can_be_set_and_cleared(fault: Fault) -> None:
    """The hook's own contract: setting a fault must not leave the adapter unusable."""
    adapter = build()
    ref = await anext(adapter.enumerate(Scope(site="conformance")))

    await adapter.set_fault(fault, count=1)
    with contextlib.suppress(AdapterError):
        await adapter.fetch(ref)
    await adapter.set_fault(Fault.NONE)

    assert await adapter.fetch(ref), "the adapter must work again once the fault is cleared"
