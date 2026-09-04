"""The adapter conformance suite — specification §6.3.

    "An adapter ships with a corpus of source assets and expected graph fragments. The
    suite checks: enumeration completeness against a known site; parse quality ≥ 0.98 on
    the corpus; AST round-trip (AST → canonical text → AST) stability; executor result-set
    determinism (same case, same result across three runs); and usage and ownership
    mapping. The suite runs in CI and on tenant enablement against a client-provided
    sample."

Five checks, and this module is all five. §6.1 makes passing it the condition of an adapter
being enabled on a tenant, which puts a weight on it that is worth stating plainly: **a check
that cannot fail is worse than no check**, because it converts an unknown into a false
assurance that somebody will rely on. Each check below is written so that the obvious way to
break the adapter breaks the check, and the SDK's own tests break each one deliberately to
prove it.

**Capabilities gate the checks.** An adapter that does not claim `usage` is not failed for
having none — §6.1 makes an absent capability a fact about the deployment. But an adapter
that *claims* a capability and cannot deliver it fails, and a claim is the only thing that
turns an absence into a failure. Skips are reported, never silently dropped: a suite that
prints "passed" after running two of five checks is the false assurance again.

**S2.1.2 makes this suite the definition of "an adapter works"** — *"adapter acceptance is a
test result, not an opinion"* — and widens the coverage to the story's seven areas. §6.3's
list and S2.1.2's overlap but are not identical; both are covered, and the mapping is:

| S2.1.2 asks for | Check here | §6.3 |
|---|---|---|
| discovery completeness | `discovery completeness` | enumeration completeness |
| parse round-trip (parse → serialise → parse identity) | `parse round-trip` | — |
| AST coverage on the golden corpus | `AST coverage` | AST round-trip (also kept) |
| execution determinism | `executor determinism` | same, three runs |
| visual capture | `visual capture` | — |
| error taxonomy | `error taxonomy` | — |
| throttling behaviour | `throttling` | — |
| — | `parse quality`, `usage and ownership` | §6.3 |
"""

from __future__ import annotations

import json
import time
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from ..calc import NodeKind, check_round_trip
from ..contract import (
    INTERFACE_VERSION,
    AdapterError,
    AdapterManifest,
    AssetRef,
    Scope,
    UnsupportedCapability,
)
from ..faults import Fault, FaultInjector, RateLimited, classify
from ..proof import ExecutionOutcome, ParityCase, VisualCase


class Outcome(str, Enum):
    PASSED = "PASSED"
    FAILED = "FAILED"
    SKIPPED = "SKIPPED"
    """The adapter does not claim the capability this check tests. Reported, not hidden."""


@dataclass(frozen=True, slots=True)
class CheckResult:
    name: str
    outcome: Outcome
    summary: str
    detail: tuple[str, ...] = ()
    duration_ms: int = 0

    @property
    def failed(self) -> bool:
        return self.outcome is Outcome.FAILED


@dataclass(frozen=True, slots=True)
class ConformanceReport:
    adapter: str
    adapter_version: str
    interface_version: str
    grammar_version: str
    checks: tuple[CheckResult, ...]
    corpus: str = ""

    @property
    def passed(self) -> bool:
        return not any(check.failed for check in self.checks)

    @property
    def counts(self) -> dict[str, int]:
        counts = {outcome.value: 0 for outcome in Outcome}
        for check in self.checks:
            counts[check.outcome.value] += 1
        return counts

    def as_dict(self) -> dict[str, Any]:
        return {
            "adapter": self.adapter,
            "adapter_version": self.adapter_version,
            "interface_version": self.interface_version,
            "grammar_version": self.grammar_version,
            "corpus": self.corpus,
            "passed": self.passed,
            "counts": self.counts,
            "checks": [
                {
                    "name": check.name,
                    "outcome": check.outcome.value,
                    "summary": check.summary,
                    "detail": list(check.detail),
                    "duration_ms": check.duration_ms,
                }
                for check in self.checks
            ],
        }


@dataclass(frozen=True, slots=True)
class Corpus:
    """The source assets an adapter is checked against, and what is expected of them.

    §6.3: "An adapter ships with a corpus of source assets and expected graph fragments."
    The corpus travels with the adapter, not with the SDK — only the adapter knows what its
    own source looks like. On tenant enablement it is replaced by "a client-provided sample",
    which is the same shape with the client's own workbooks in it.
    """

    name: str
    scope: Scope
    expected_assets: frozenset[str] = frozenset()
    """LUIDs enumeration must return. Completeness is a set comparison, not a count: an
    adapter that drops one workbook and duplicates another has the right count."""

    expected_nodes: dict[str, frozenset[str]] = field(default_factory=dict)
    """Per asset LUID, the graph node types its parse must produce. §6.3's "expected graph
    fragments" — deliberately node *types* rather than whole nodes, so a corpus does not have
    to be regenerated every time a property is added, while still failing an adapter that
    stops emitting worksheets."""

    expressions: tuple[str, ...] = ()
    """Calculations the grammar must round-trip. Should include at least one the grammar
    cannot read: UNKNOWN nodes are the case where round-tripping is most easily lost."""

    parity_cases: tuple[ParityCase, ...] = ()
    expected_owners: frozenset[str] = frozenset()
    usage_window_days: int = 90
    parse_quality_floor: float = 0.98
    """§4.1.4's threshold, which §6.3 requires the corpus to clear."""

    visual_cases: tuple[VisualCase, ...] = ()
    """Views to capture, for the visual-capture check. Only run where the adapter claims
    ``screenshot``."""

    required_node_kinds: frozenset[NodeKind] = frozenset()
    """AST node kinds the **golden corpus** must exercise (S2.1.2).

    Coverage is a property of the corpus as much as of the grammar. A corpus of ten
    arithmetic expressions can be parsed perfectly by a grammar that cannot read an LOD, and
    a suite that only measured "did every expression parse" would certify it. Naming the
    kinds makes the gap in the *corpus* visible, which is the gap that lets a grammar bug
    reach a client.

    Empty means "whatever the grammar declares it covers", which is weaker and is the
    default only so an adapter is not forced to enumerate them before it has a grammar."""

    ast_coverage_floor: float = 1.0
    """Fraction of the golden corpus's expressions that must parse with **no** unrecognised
    construct. One by default: the golden corpus is the set an adapter claims to read, and a
    grammar that cannot read its own golden corpus is not ready to meet a client's."""


#: How many times the executor check runs each case. §6.3 says three.
DETERMINISM_RUNS = 3


class ConformanceSuite:
    """Runs §6.3's checks against one adapter."""

    def __init__(self, adapter: Any, corpus: Corpus) -> None:
        self._adapter = adapter
        self._corpus = corpus

    async def run(self) -> ConformanceReport:
        manifest = await self._manifest()
        checks = [
            await self._timed("interface version", self._check_interface, manifest),
            await self._timed("discovery completeness", self._check_enumeration),
            await self._timed("parse quality", self._check_parse_quality, manifest),
            await self._timed("parse round-trip", self._check_parse_round_trip, manifest),
            await self._timed("AST round-trip", self._check_round_trip, manifest),
            await self._timed("AST coverage", self._check_ast_coverage, manifest),
            await self._timed("executor determinism", self._check_determinism, manifest),
            await self._timed("visual capture", self._check_visual_capture, manifest),
            await self._timed("usage and ownership", self._check_usage_and_ownership, manifest),
            await self._timed("error taxonomy", self._check_error_taxonomy, manifest),
            await self._timed("throttling", self._check_throttling, manifest),
        ]
        return ConformanceReport(
            adapter=manifest.name,
            adapter_version=manifest.version,
            interface_version=manifest.interface_version,
            grammar_version=manifest.grammar_version,
            checks=tuple(checks),
            corpus=self._corpus.name,
        )

    # ------------------------------------------------------------------ plumbing

    async def _manifest(self) -> AdapterManifest:
        manifest: Any = self._adapter.manifest()
        if hasattr(manifest, "__await__"):  # a remote adapter may answer asynchronously
            manifest = await manifest
        if not isinstance(manifest, AdapterManifest):
            raise TypeError(
                f"manifest() returned {type(manifest).__name__}, not an AdapterManifest; "
                f"§6.1 makes the manifest the adapter's identity and everything the suite "
                f"reports is keyed on it"
            )
        return manifest

    async def _timed(self, name: str, check: Any, *args: Any) -> CheckResult:
        started = time.perf_counter()
        try:
            outcome, summary, detail = await check(*args)
        except Exception as exc:
            outcome, summary, detail = (
                Outcome.FAILED,
                f"the check itself raised {type(exc).__name__}: {exc}",
                (),
            )
        return CheckResult(
            name=name,
            outcome=outcome,
            summary=summary,
            detail=tuple(detail),
            duration_ms=int((time.perf_counter() - started) * 1000),
        )

    # -------------------------------------------------------------------- checks

    async def _check_interface(
        self, manifest: AdapterManifest
    ) -> tuple[Outcome, str, Sequence[str]]:
        """Not one of §6.3's five, and deliberately first.

        Every other check reads a result whose meaning depends on the contract both sides
        think they are speaking. Running them against a mismatched interface produces
        failures that describe the wrong problem.
        """
        if manifest.interface_version != INTERFACE_VERSION:
            return (
                Outcome.FAILED,
                f"adapter is built against interface {manifest.interface_version}, "
                f"suite against {INTERFACE_VERSION}",
                (),
            )
        return (
            Outcome.PASSED,
            f"interface {INTERFACE_VERSION}, adapter {manifest.name} {manifest.version}, "
            f"grammar {manifest.grammar_version}",
            (),
        )

    async def _check_enumeration(self) -> tuple[Outcome, str, Sequence[str]]:
        """§6.3: enumeration completeness against a known site."""
        expected = self._corpus.expected_assets
        if not expected:
            return (Outcome.SKIPPED, "the corpus names no expected assets", ())

        found: list[AssetRef] = []
        async for ref in self._adapter.enumerate(self._corpus.scope):
            found.append(ref)
        luids = [ref.luid for ref in found]
        seen = set(luids)

        detail: list[str] = []
        if missing := sorted(expected - seen):
            detail.append(f"never enumerated: {', '.join(missing[:10])}")
        if extra := sorted(seen - expected):
            detail.append(f"not in the corpus: {', '.join(extra[:10])}")
        if duplicates := sorted({luid for luid in luids if luids.count(luid) > 1}):
            # Worth its own line: a duplicate is invisible in a count and produces a second
            # harvest of the same workbook, which looks like drift rather than like a bug.
            detail.append(f"enumerated more than once: {', '.join(duplicates[:10])}")

        if detail:
            return (Outcome.FAILED, f"{len(seen)} of {len(expected)} expected assets", detail)
        return (Outcome.PASSED, f"all {len(expected)} assets enumerated exactly once", ())

    async def _check_parse_quality(
        self, manifest: AdapterManifest
    ) -> tuple[Outcome, str, Sequence[str]]:
        """§6.3: parse quality ≥ 0.98 on the corpus, and the expected fragments.

        Both in one check because they are the same claim about the same call: an adapter
        that parses cleanly but emits no worksheets has not parsed the workbook, and one that
        emits every node type at a quality of 0.4 has not read it.
        """
        floor = self._corpus.parse_quality_floor
        detail: list[str] = []
        qualities: list[float] = []

        async for ref in self._adapter.enumerate(self._corpus.scope):
            try:
                raw = await self._adapter.fetch(ref)
                result = await self._adapter.parse(raw)
            except AdapterError as exc:
                detail.append(f"{ref.luid}: {exc}")
                continue

            qualities.append(result.parse_quality)
            if result.parse_quality < floor:
                unread = ", ".join(u.construct for u in result.unrecognised[:5]) or "none reported"
                detail.append(
                    f"{ref.luid} ({ref.name}) parsed at {result.parse_quality:.3f}; "
                    f"unread: {unread}"
                )

            expected_types = self._corpus.expected_nodes.get(ref.luid)
            if expected_types:
                produced = {node.type for node in result.nodes}
                if absent := sorted(expected_types - produced):
                    detail.append(f"{ref.luid}: no {', '.join(absent)} node produced")

        if not qualities:
            return (Outcome.FAILED, "nothing was parsed", tuple(detail))
        mean = sum(qualities) / len(qualities)
        summary = (
            f"{len(qualities)} assets, mean parse quality {mean:.3f}, "
            f"lowest {min(qualities):.3f} (floor {floor})"
        )
        return (Outcome.FAILED if detail else Outcome.PASSED, summary, tuple(detail))

    async def _check_round_trip(
        self, manifest: AdapterManifest
    ) -> tuple[Outcome, str, Sequence[str]]:
        """§6.3: AST → canonical text → AST stability."""
        if not self._corpus.expressions:
            return (Outcome.SKIPPED, "the corpus names no expressions", ())

        parse = getattr(self._adapter, "parse_calc", None)
        if parse is None:
            return (Outcome.FAILED, "the adapter has no parse_calc; §6.1 requires it", ())

        detail: list[str] = []
        for expression in self._corpus.expressions:
            trip = await check_round_trip(parse, expression)
            if not trip.stable:
                detail.append(f"{expression}\n    {trip.detail}")

        count = len(self._corpus.expressions)
        if detail:
            return (
                Outcome.FAILED,
                f"{len(detail)} of {count} expressions did not round-trip",
                tuple(detail),
            )
        return (Outcome.PASSED, f"all {count} expressions round-tripped", ())

    async def _check_determinism(
        self, manifest: AdapterManifest
    ) -> tuple[Outcome, str, Sequence[str]]:
        """§6.3: same case, same result across three runs.

        **The gate is behaviour, not a capability flag.** Until S2.4.1 this check skipped
        unless the adapter claimed ``extract_read`` or ``live_query`` — which was right while
        those were the only ways to execute. §6.2's third strategy, view data, needs neither:
        it is a REST call any Tableau answers. Gating on the two flags would have skipped this
        check forever on an adapter that executes every case, and a check that cannot fail is
        the false assurance this module exists to avoid.

        So: run the cases. An adapter that genuinely cannot execute returns INCONCLUSIVE with
        a reason (interface 1.1), and that is a fact about the deployment — reported as a skip
        with its reason, never hidden. An adapter that *claims* extract read or live query and
        still cannot execute has made a claim it did not honour, and that is a failure.
        """
        if not self._corpus.parity_cases:
            return (Outcome.SKIPPED, "the corpus names no parity cases", ())

        claims = manifest.capabilities.extract_read or manifest.capabilities.live_query
        detail: list[str] = []
        inconclusive: list[str] = []
        executed = 0

        for case in self._corpus.parity_cases:
            try:
                results = [await self._adapter.execute_case(case) for _ in range(DETERMINISM_RUNS)]
            except UnsupportedCapability as exc:
                if claims:
                    detail.append(f"{case.id}: claimed the capability but raised: {exc}")
                else:
                    detail.append(
                        f"{case.id}: execute_case raised ({exc}); interface 1.1 asks for an "
                        f"INCONCLUSIVE result with a reason, so an operator can see why"
                    )
                continue

            fingerprints = {result.fingerprint for result in results}
            if len(fingerprints) > 1:
                detail.append(
                    f"{case.id}: {len(fingerprints)} different result sets across "
                    f"{DETERMINISM_RUNS} runs"
                )
            versions = {r.interface_version for r in results}
            if versions != {INTERFACE_VERSION}:
                # S2.1.1 criterion 4. Checked here because a result set is what a ParityRun
                # is assembled from, so this is where an unstamped run becomes impossible.
                detail.append(
                    f"{case.id}: result sets carry interface version {sorted(versions)}, "
                    f"expected {INTERFACE_VERSION!r}"
                )
            if any(r.truncated for r in results):
                detail.append(f"{case.id}: hit the row limit; a truncated case cannot be compared")

            outcomes = {r.outcome for r in results}
            if len(outcomes) > 1:
                # Worse than a moving row count: the same case sometimes produced evidence and
                # sometimes did not, so a verdict on it depends on when it ran.
                detail.append(
                    f"{case.id}: outcome moved between runs "
                    f"({', '.join(sorted(o.value for o in outcomes))})"
                )
            elif outcomes == {ExecutionOutcome.INCONCLUSIVE}:
                reason = results[0].reason or "no reason given"
                if claims:
                    detail.append(
                        f"{case.id}: the adapter claims extract read or live query and still "
                        f"returned INCONCLUSIVE: {reason}"
                    )
                else:
                    inconclusive.append(f"{case.id}: {reason}")
            else:
                executed += 1

        count = len(self._corpus.parity_cases)
        if detail:
            return (Outcome.FAILED, f"{len(detail)} of {count} cases", tuple(detail))
        if not executed:
            return (
                Outcome.SKIPPED,
                f"this deployment executes none of the {count} cases "
                f"(§6.1: an absent capability is a fact, not a defect)",
                tuple(inconclusive),
            )
        return (
            Outcome.PASSED,
            f"{executed} of {count} cases identical across {DETERMINISM_RUNS} runs, each "
            f"stamped with interface {INTERFACE_VERSION}"
            + (f"; {len(inconclusive)} inconclusive" if inconclusive else ""),
            tuple(inconclusive),
        )

    async def _check_parse_round_trip(
        self, manifest: AdapterManifest
    ) -> tuple[Outcome, str, Sequence[str]]:
        """S2.1.2: parse → serialise → parse identity.

        Two properties in one, because they fail the same way and for the same reason:

        1. **Parsing is a function of the bytes.** The same asset parsed twice must give the
           same fragment. An adapter that carried state between calls — a cache keyed on
           something that moves, a counter in a generated id — would produce a graph that
           differs between two harvests of an unchanged workbook, which the platform reads
           as drift and re-opens Migration Units over (§8.4).
        2. **The fragment survives serialisation.** Every parse result crosses the adapter
           RPC and is then written to the graph. A field that encodes but does not decode is
           a field that silently vanishes on the way in, and the first symptom is a missing
           edge in an estate nobody is looking at.

        Compared as whole `ParseResult` objects rather than field by field, so a field added
        to the contract later is covered without anyone remembering to add it here.
        """
        from ..rpc import wire

        detail: list[str] = []
        checked = 0

        async for ref in self._adapter.enumerate(self._corpus.scope):
            try:
                raw = await self._adapter.fetch(ref)
                first = await self._adapter.parse(raw)
                second = await self._adapter.parse(raw)
            except AdapterError as exc:
                detail.append(f"{ref.luid}: {exc}")
                continue
            checked += 1

            if first != second:
                detail.append(
                    f"{ref.luid}: parsing the same bytes twice produced different fragments "
                    f"({len(first.nodes)} vs {len(second.nodes)} nodes, "
                    f"{len(first.edges)} vs {len(second.edges)} edges)"
                )
                continue

            # Through **JSON**, not just through the codecs. The codecs alone move objects
            # between shapes and would carry a tuple, a set or a Decimal straight across; the
            # wire does not. A property that becomes a list on the way to the platform is a
            # property the graph stores differently from what the adapter parsed, and the
            # difference shows up as drift on the next harvest rather than as an error here.
            try:
                encoded = json.loads(json.dumps(wire.encode_parse_result(first)))
            except (TypeError, ValueError) as exc:
                detail.append(f"{ref.luid}: cannot be serialised at all — {exc}")
                continue
            restored = wire.decode_parse_result(encoded)
            if restored != first:
                lost = _first_difference(first, restored)
                detail.append(f"{ref.luid}: did not survive serialisation — {lost}")

        if not checked:
            return (Outcome.FAILED, "nothing was parsed", tuple(detail))
        if detail:
            return (Outcome.FAILED, f"{len(detail)} of {checked} assets", tuple(detail))
        return (
            Outcome.PASSED,
            f"{checked} assets parsed identically twice and survived serialisation",
            (),
        )

    async def _check_ast_coverage(
        self, manifest: AdapterManifest
    ) -> tuple[Outcome, str, Sequence[str]]:
        """S2.1.2: AST coverage on the golden corpus.

        Distinct from the round-trip check, which asks whether the printer and the parser
        agree. This asks whether the **corpus exercises the grammar** and whether the
        **grammar reads the corpus** — two different gaps, both invisible to the other checks.
        """
        expressions = self._corpus.expressions
        if not expressions:
            return (Outcome.SKIPPED, "the corpus names no expressions", ())

        detail: list[str] = []
        unread: list[str] = []
        clean = 0
        kinds: set[NodeKind] = set()

        for expression in expressions:
            try:
                parsed = await self._adapter.parse_calc(expression)
            except Exception as exc:
                # A grammar that *raises* is a failure at any coverage floor: §6.2 requires
                # an unreadable construct to be retained, not to stop the parse.
                detail.append(f"{expression}\n    raised {type(exc).__name__}: {exc}")
                continue
            kinds.update(node.kind for node in parsed.root.walk())
            if parsed.unrecognised:
                unread.append(f"{expression}\n    unread: {', '.join(parsed.unrecognised)}")
            else:
                clean += 1

        coverage = clean / len(expressions)
        floor = self._corpus.ast_coverage_floor
        if coverage < floor:
            # The unread expressions are evidence for *this* failure, and only for it. Listing
            # them unconditionally made any unread construct a failure and the floor
            # unreachable — a corpus with a floor of 0.8 could never sit at 0.8.
            detail.append(
                f"{clean} of {len(expressions)} golden expressions parsed with no unread "
                f"construct ({coverage:.0%}), below the corpus's floor of {floor:.0%}"
            )
            detail.extend(unread)

        required = self._corpus.required_node_kinds
        if required and (unexercised := sorted(k.value for k in required - kinds)):
            # A gap in the *corpus*, not the grammar — and the more dangerous of the two,
            # because a grammar bug in a shape the corpus never exercises reaches a client.
            detail.append(
                f"the golden corpus never exercises: {', '.join(unexercised)}. "
                f"A grammar defect in a shape the corpus does not contain is a defect the "
                f"suite cannot see."
            )

        summary = (
            f"{coverage:.0%} of {len(expressions)} golden expressions read cleanly "
            f"(floor {floor:.0%}); {len(kinds)} AST node kinds exercised"
        )
        if unread and coverage >= floor:
            # Said in the summary rather than as failure detail: within the floor, an unread
            # construct is a known gap the corpus tolerates, not a defect. Silence would hide
            # a corpus drifting towards its floor.
            summary += f"; {len(unread)} within the floor still unread"
        return (Outcome.FAILED if detail else Outcome.PASSED, summary, tuple(detail))

    async def _check_visual_capture(
        self, manifest: AdapterManifest
    ) -> tuple[Outcome, str, Sequence[str]]:
        """S2.1.2: visual capture. §6.2's Screenshot row, for §10.6's advisory comparison.

        Checked for being a real image of the size that was asked for, because §10.6 compares
        two images perceptually and a blank or wrongly-sized capture scores as *difference* —
        which sends a reviewer to a visual that never drifted.
        """
        if not manifest.capabilities.screenshot:
            return (Outcome.SKIPPED, "the adapter does not claim screenshot", ())
        if not self._corpus.visual_cases:
            return (Outcome.FAILED, "screenshot is claimed but the corpus names no views", ())

        detail: list[str] = []
        for case in self._corpus.visual_cases:
            try:
                capture = await self._adapter.capture_visual(case)
            except UnsupportedCapability as exc:
                detail.append(f"{case.id}: claimed the capability but raised: {exc}")
                continue
            except AdapterError as exc:
                detail.append(f"{case.id}: {exc}")
                continue

            if not capture.image:
                detail.append(f"{case.id}: returned no image bytes")
            elif not _looks_like_an_image(capture.image, capture.media_type):
                detail.append(
                    f"{case.id}: {len(capture.image)} bytes that are not a {capture.media_type}"
                )
            if capture.case_id != case.id:
                detail.append(f"{case.id}: capture is labelled {capture.case_id!r}")
            if (capture.width, capture.height) != (case.width, case.height):
                detail.append(
                    f"{case.id}: asked for {case.width}x{case.height}, got "
                    f"{capture.width}x{capture.height}"
                )
            if capture.interface_version != INTERFACE_VERSION:
                detail.append(f"{case.id}: capture carries no interface version")

        count = len(self._corpus.visual_cases)
        if detail:
            return (Outcome.FAILED, f"{len(detail)} problems over {count} views", tuple(detail))
        return (Outcome.PASSED, f"{count} views captured at the size requested", ())

    async def _check_error_taxonomy(
        self, manifest: AdapterManifest
    ) -> tuple[Outcome, str, Sequence[str]]:
        """S2.1.2: error taxonomy.

        The platform's response to an adapter failure is decided entirely by which kind it
        is — retry, record and continue, or stop the run. An adapter that raises the wrong
        kind gets a workbook permanently failed when the answer was "ask again", or a whole
        run marked as 1,067 individual failures when the credential was simply wrong.
        """
        injector = await _injector(self._adapter)
        if injector is None:
            return (Outcome.FAILED, _NO_INJECTOR, ())

        expectations: tuple[tuple[Fault, type[BaseException], str], ...] = (
            (Fault.TRANSIENT, AdapterError, "retryable"),
            (Fault.PERMANENT, AdapterError, "not retryable"),
            (Fault.UNAUTHORISED, AdapterError, "not retryable"),
        )

        ref = await anext(self._adapter.enumerate(self._corpus.scope))
        detail: list[str] = []
        observed: list[str] = []

        for fault, expected_type, expected_retry in expectations:
            await injector.set_fault(fault)
            try:
                await self._adapter.fetch(ref)
            except BaseException as exc:
                seen = classify(exc)
                observed.append(f"{fault.value} → {seen}")
                if not isinstance(exc, expected_type):
                    detail.append(
                        f"{fault.value}: raised {seen}, expected {expected_type.__name__}"
                    )
                elif isinstance(exc, AdapterError):
                    wanted = expected_retry == "retryable"
                    if exc.retryable is not wanted:
                        detail.append(
                            f"{fault.value}: raised {seen}, expected {expected_retry}. "
                            + _WHY_RETRYABLE[fault]
                        )
            else:
                detail.append(f"{fault.value}: the adapter succeeded; nothing was raised")
            finally:
                await injector.set_fault(Fault.NONE)

        # And the fault must actually clear, or every later check runs against a broken
        # source and reports the wrong thing.
        try:
            await self._adapter.fetch(ref)
        except AdapterError as exc:
            detail.append(f"the adapter did not recover after the fault was cleared: {exc}")

        if detail:
            return (
                Outcome.FAILED,
                f"{len(detail)} of {len(expectations)} conditions",
                tuple(detail),
            )
        return (Outcome.PASSED, "; ".join(observed), ())

    async def _check_throttling(
        self, manifest: AdapterManifest
    ) -> tuple[Outcome, str, Sequence[str]]:
        """S2.1.2: throttling behaviour. §6.2: "Adaptive concurrency per site … backoff on 429".

        The property is that a throttled source **slows a harvest down rather than failing
        it**. An adapter that turns a 429 into a permanent failure loses a workbook to a
        condition that would have cleared in thirty seconds — and at 1,067 workbooks against
        a rate-limited site, loses most of the estate.

        The suite asks for a burst of throttling and then requires the call to complete. It
        does not measure how long the backoff took: a fixed timing assertion would be a
        flaky test on a busy machine, and the adapter's backoff schedule is its own business
        as long as it converges.
        """
        injector = await _injector(self._adapter)
        if injector is None:
            return (Outcome.FAILED, _NO_INJECTOR, ())

        ref = await anext(self._adapter.enumerate(self._corpus.scope))
        detail: list[str] = []

        # 1. A throttled source that recovers: the adapter must ride it out.
        await injector.set_fault(Fault.THROTTLE, count=3)
        try:
            await self._adapter.fetch(ref)
        except RateLimited as exc:
            detail.append(
                f"gave up after being throttled rather than backing off: {classify(exc)}. "
                f"§6.2 requires backoff on 429."
            )
        except AdapterError as exc:
            detail.append(f"a throttled source produced {classify(exc)}; expected it to recover")
        finally:
            await injector.set_fault(Fault.NONE)

        # 2. Throttling that does not clear must surface *as throttling*, not as a failure.
        #    The distinction decides whether the platform waits or gives up on the estate.
        await injector.set_fault(Fault.THROTTLE, count=10_000)
        try:
            await self._adapter.fetch(ref)
        except RateLimited as exc:
            if exc.retry_after is not None and exc.retry_after < 0:
                detail.append(f"reported a negative retry_after ({exc.retry_after})")
        except AdapterError as exc:
            detail.append(
                f"persistent throttling surfaced as {classify(exc)}; the platform cannot "
                f"tell 'wait' from 'this workbook is broken', and records the workbook as "
                f"failed"
            )
        else:
            detail.append("persistent throttling was never surfaced; the adapter retried forever")
        finally:
            await injector.set_fault(Fault.NONE)

        if detail:
            return (Outcome.FAILED, f"{len(detail)} problems", tuple(detail))
        return (
            Outcome.PASSED,
            "backs off through a burst of 429s and surfaces persistent throttling as RateLimited",
            (),
        )

    async def _check_usage_and_ownership(
        self, manifest: AdapterManifest
    ) -> tuple[Outcome, str, Sequence[str]]:
        """§6.3: usage and ownership mapping."""
        claims = manifest.capabilities
        if not (claims.usage or claims.ownership):
            return (Outcome.SKIPPED, "the adapter claims neither usage nor ownership", ())

        known = {ref.luid async for ref in self._adapter.enumerate(self._corpus.scope)}
        detail: list[str] = []
        summary: list[str] = []

        if claims.usage:
            records = await self._adapter.usage(self._corpus.scope, self._corpus.usage_window_days)
            summary.append(f"{len(records)} usage records")
            if not records:
                detail.append("usage is claimed but no records were returned")
            for record in records:
                # A usage record for a workbook nobody enumerated cannot be attached to
                # anything, and would land in the graph as usage of a workbook that does not
                # exist. The mapping is the thing under test, not the counting.
                owner_luid = record.workbook_luid or record.asset_luid
                if owner_luid not in known:
                    detail.append(
                        f"usage for {record.asset_luid!r} maps to workbook "
                        f"{owner_luid!r}, which enumeration never returned"
                    )
                if record.views < 0 or record.distinct_viewers < 0:
                    detail.append(f"usage for {record.asset_luid!r} has negative counts")
                if record.distinct_viewers > record.views:
                    detail.append(
                        f"usage for {record.asset_luid!r} reports more distinct viewers "
                        f"({record.distinct_viewers}) than views ({record.views})"
                    )

        if claims.ownership:
            owners = await self._adapter.owners(self._corpus.scope)
            summary.append(f"{len(owners)} ownership records")
            found = {record.asset_luid for record in owners}
            if unowned := sorted(known - found):
                detail.append(f"no owner reported for: {', '.join(unowned[:10])}")
            expected = self._corpus.expected_owners
            if expected and (missing := sorted(expected - {r.owner_upn for r in owners})):
                detail.append(f"expected owners never reported: {', '.join(missing)}")
            for record in owners:
                if not record.owner_upn.strip():
                    detail.append(f"{record.asset_luid} has an empty owner")

        text = ", ".join(summary) or "nothing claimed"
        return (Outcome.FAILED if detail else Outcome.PASSED, text, tuple(detail))


_NO_INJECTOR = (
    "the adapter does not implement FaultInjector, so its behaviour under source failure "
    "and throttling has never been observed. §6.2 requires backoff on 429; an adapter "
    "cannot be certified for behaviour nobody has seen, and 'could not check' recorded as a "
    "pass is exactly the false assurance this suite exists to prevent."
)

_WHY_RETRYABLE = {
    Fault.TRANSIENT: "A network blip clears; a workbook lost to one is a workbook lost for "
    "no reason.",
    Fault.PERMANENT: "Retrying a corrupt file forever turns one bad workbook into a harvest "
    "that never finishes.",
    Fault.UNAUTHORISED: "Retrying with the same rejected credential fails identically, and "
    "marks every workbook in the estate as individually failed.",
}


async def _injector(adapter: Any) -> FaultInjector | None:
    """The adapter's fault hook, or None with the reason reported by the caller.

    Probed by *using* it rather than by ``isinstance`` alone: a `RemoteAdapter` always has
    the method, and whether the adapter on the other end has the hook is something only the
    call can discover. Guessing from the presence of a method would report a remote adapter
    with no hook as one whose taxonomy is wrong.
    """
    if not isinstance(adapter, FaultInjector):
        return None
    try:
        await adapter.set_fault(Fault.NONE)
    except AdapterError:
        return None
    return adapter


#: Magic numbers, so a capture can be checked for being an image rather than for being bytes.
_IMAGE_MAGIC: dict[str, tuple[bytes, ...]] = {
    "image/png": (b"\x89PNG\r\n\x1a\n",),
    "image/jpeg": (b"\xff\xd8\xff",),
    "image/gif": (b"GIF87a", b"GIF89a"),
    "image/webp": (b"RIFF",),
}


def _looks_like_an_image(payload: bytes, media_type: str) -> bool:
    magic = _IMAGE_MAGIC.get(media_type)
    if magic is None:
        # An unknown media type is not a failure here — the check is that the bytes match
        # what the adapter said they are, and an adapter naming a format the SDK has not
        # heard of is making a claim the SDK cannot check rather than a false one.
        return bool(payload)
    return any(payload.startswith(prefix) for prefix in magic)


def _first_difference(before: Any, after: Any) -> str:
    """Name what changed, rather than reporting two large objects as unequal."""
    if len(before.nodes) != len(after.nodes):
        return f"{len(before.nodes)} nodes became {len(after.nodes)}"
    if len(before.edges) != len(after.edges):
        return f"{len(before.edges)} edges became {len(after.edges)}"
    for first, second in zip(before.nodes, after.nodes, strict=True):
        if first != second:
            lost = sorted(set(first.properties) - set(second.properties))
            if lost:
                return f"node {first.key!r} lost the properties {', '.join(lost)}"
            return f"node {first.key!r} changed"
    for first, second in zip(before.edges, after.edges, strict=True):
        if first != second:
            return f"edge {first.type} {first.from_key} → {first.to_key} changed"
    if tuple(before.unrecognised) != tuple(after.unrecognised):
        return "the unrecognised constructs changed"
    return "the counts match but the contents differ"


def render(report: ConformanceReport) -> str:
    """The report as a terminal reads it."""
    lines = [
        "Adapter conformance — specification §6.3",
        f"  adapter    {report.adapter} {report.adapter_version}",
        f"  interface  {report.interface_version}",
        f"  grammar    {report.grammar_version}",
        f"  corpus     {report.corpus or '(none)'}",
        "",
    ]
    marks = {Outcome.PASSED: "PASS", Outcome.FAILED: "FAIL", Outcome.SKIPPED: "SKIP"}
    for check in report.checks:
        lines.append(
            f"  {marks[check.outcome]}  {check.name} — {check.summary} [{check.duration_ms} ms]"
        )
        for line in check.detail:
            for part in str(line).splitlines():
                lines.append(f"          {part}")
    counts = report.counts
    lines.append("")
    lines.append(
        f"  {counts['PASSED']} passed, {counts['FAILED']} failed, {counts['SKIPPED']} skipped — "
        + ("CONFORMANT" if report.passed else "NOT CONFORMANT")
    )
    if counts["SKIPPED"]:
        lines.append(
            "  Skipped checks test capabilities this adapter does not claim. §6.1 makes an "
            "absent capability a fact about the deployment, not a defect — but an adapter "
            "cannot be enabled for what it has not been shown to do."
        )
    return "\n".join(lines)


def iter_failures(report: ConformanceReport) -> Iterable[CheckResult]:
    return (check for check in report.checks if check.failed)
