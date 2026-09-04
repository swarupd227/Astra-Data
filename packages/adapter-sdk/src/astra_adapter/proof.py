"""What an adapter is asked to execute, and what it returns — specification §6.1, §6.2, §10.

**Why these shapes are in the SDK and not in the Proof Engine.** The Proof Engine is E7 and
does not exist. But `execute_case` is on the §6.1 interface, and an interface whose argument
type arrives two epics later is not versioned — the first adapter written against it would
have to be rewritten when the type landed. So the shapes an adapter must accept and produce
are defined here, now, and E7 builds its orchestration on top of them.

They are deliberately the *narrow* part: a case to run, a result set to compare, and the
strategy it was obtained by. Case derivation, diffing, tolerance policy and verdicts are
§10's and belong to E7.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class ExecutionOutcome(str, Enum):
    """Whether a case produced a comparable result (§10.2, S2.4.1).

    ``INCONCLUSIVE`` is not a failure and the distinction is load-bearing: §10.2 says "a
    timeout on either side yields INCONCLUSIVE, not FAIL, and is retried once with a longer
    budget before being surfaced". A timeout recorded as a failure would put a Migration Unit
    into remediation over a slow warehouse, and somebody would spend a day looking for a bug
    in a report that is correct.
    """

    OK = "OK"
    INCONCLUSIVE = "INCONCLUSIVE"


class ColumnRole(str, Enum):
    """§10.1 splits a case into a *grain* and its *measures*, and the diff treats them
    differently: the grain is the key rows are matched on, the measures are what is compared
    under the charter's tolerances. A result set whose columns did not say which is which
    would make the Proof Engine guess."""

    DIMENSION = "dimension"
    MEASURE = "measure"


@dataclass(frozen=True, slots=True)
class Column:
    """One column descriptor: name, role, type (§10.2, S2.4.1's third criterion)."""

    name: str
    role: ColumnRole = ColumnRole.DIMENSION
    type: str = "string"
    """The source's own type name, not a normalised one. §10.3's normalisation is the Proof
    Engine's, and it needs to know what it is normalising *from* — a Tableau ``real`` and a
    DAX ``Decimal`` round differently, and the charter's ``currency_scale`` applies to one of
    them."""


class ExecutionStrategy(str, Enum):
    """How a result set was obtained (§6.2, "The strategy used is recorded on the
    ParityCase").

    Recording it is not bookkeeping. The three strategies are different evidence: an extract
    read proves what the published extract contains, a view-data read proves what the sheet
    shows a user, and a live replay proves what the database returns now. A parity verdict
    that does not say which of those it rests on cannot be audited, and §10 makes verdicts
    auditable.
    """

    EXTRACT_READ = "EXTRACT_READ"
    VIEW_DATA = "VIEW_DATA"
    LIVE_REPLAY = "LIVE_REPLAY"


@dataclass(frozen=True, slots=True)
class ParityCase:
    """One question to ask of both sides, at a stated grain (§10.2).

    ``grain`` is the list of dimensions the result is grouped by, and ``measures`` what is
    aggregated. ``filters`` and ``parameters`` are applied as the sheet applies them — §6.2
    passes them to Tableau through ``vf_`` parameters, which is an adapter concern and not
    visible here.
    """

    id: str
    workbook_luid: str
    sheet: str | None = None
    grain: tuple[str, ...] = ()
    measures: tuple[str, ...] = ()
    filters: tuple[tuple[str, str], ...] = ()
    parameters: tuple[tuple[str, str], ...] = ()
    row_limit: int = 10_000
    """A bound, not a sample. A case whose result exceeds it is reported as truncated and
    must not be compared — a diff over two differently-truncated result sets is noise that
    looks like a finding."""


@dataclass(frozen=True, slots=True)
class ResultSet:
    """The rows an adapter got, and everything needed to read them as evidence.

    ``interface_version`` and ``adapter_version`` are on the result rather than only on the
    run because S2.1.1 requires the interface version on every ParityRun, and a run is
    assembled from result sets. Carrying it here means a run cannot be recorded without it:
    there is no path that produces a result set with no version on it.
    """

    case_id: str
    columns: tuple[Column, ...]
    """Ordered and typed (§10.2). Retyped from ``tuple[str, ...]`` at interface 1.1."""

    rows: tuple[tuple[Any, ...], ...]
    """Rows in column order. **Nulls are preserved as ``None``** — S2.4.1's third criterion,
    and not a formality: the charter has a rule for ``source_null_vs_target_zero`` (FAIL) and
    a different one for ``source_null_vs_target_blank`` (PASS), so an executor that coerced a
    null to an empty string or a zero would decide a verdict the charter is supposed to."""

    strategy: ExecutionStrategy
    interface_version: str
    adapter_name: str
    adapter_version: str
    grammar_version: str | None = None
    outcome: ExecutionOutcome = ExecutionOutcome.OK
    reason: str = ""
    """Why, when the outcome is not OK. A timeout that said only "inconclusive" would leave
    a parity engineer to guess between a slow warehouse, a missing extract and a rejected
    credential."""

    truncated: bool = False
    """The row limit was reached. §10's comparison must refuse rather than diff."""

    executed_at: str | None = None
    detail: dict[str, Any] = field(default_factory=dict)
    """Strategy-specific provenance — which extract file, which view id, which SQL. Kept
    because "the live replay disagreed" is a question about a query, and the query has to be
    producible."""

    @property
    def fingerprint(self) -> str:
        """A stable digest of the *content*, ignoring how and when it was obtained.

        §6.3 requires "executor result-set determinism (same case, same result across three
        runs)". Determinism is about rows and columns; a timestamp or a chosen strategy
        differing between runs is not a determinism failure, and comparing whole objects
        would report it as one.
        """
        import hashlib
        import json

        payload = json.dumps(
            {
                "columns": [
                    [column.name, column.role.value, column.type] for column in self.columns
                ],
                "rows": [list(row) for row in self.rows],
            },
            sort_keys=True,
            default=str,
        )
        return hashlib.sha256(payload.encode()).hexdigest()

    @property
    def comparable(self) -> bool:
        """Whether §10.3 may diff this. An INCONCLUSIVE or truncated result may not."""
        return self.outcome is ExecutionOutcome.OK and not self.truncated

    @property
    def grain(self) -> tuple[str, ...]:
        return tuple(c.name for c in self.columns if c.role is ColumnRole.DIMENSION)

    @property
    def measures(self) -> tuple[str, ...]:
        return tuple(c.name for c in self.columns if c.role is ColumnRole.MEASURE)


@dataclass(frozen=True, slots=True)
class VisualCase:
    """One view to capture, for §10.6's advisory visual comparison."""

    id: str
    workbook_luid: str
    view_name: str
    width: int = 1200
    height: int = 800
    parameters: tuple[tuple[str, str], ...] = ()


@dataclass(frozen=True, slots=True)
class VisualCapture:
    """An image of a source view (§6.2 Screenshot, §10.6).

    Advisory throughout: §10.6 gates on a data-parity verdict and a human review, and this
    exists to direct that review, never to replace it.
    """

    case_id: str
    image: bytes
    media_type: str = "image/png"
    width: int = 0
    height: int = 0
    interface_version: str = ""
    adapter_name: str = ""
    adapter_version: str = ""
    captured_at: str | None = None


#: The order strategies are preferred in when a charter says nothing. §6.2 lists them in this
#: order and calls it a preference, and the reasoning is evidential rather than technical: an
#: extract read proves what the published extract contains, a view-data read proves what the
#: sheet shows a user, and a live replay proves what the database returns *now*. The first is
#: closest to what the client's report actually rendered.
DEFAULT_STRATEGY_ORDER: tuple[ExecutionStrategy, ...] = (
    ExecutionStrategy.EXTRACT_READ,
    ExecutionStrategy.VIEW_DATA,
    ExecutionStrategy.LIVE_REPLAY,
)

#: §10.2 schedules executions "with retry and timeout"; S2.4.1 fixes the default.
DEFAULT_TIMEOUT_SECONDS = 120.0


@dataclass(frozen=True, slots=True)
class ExecutionCharter:
    """The Tolerance Charter's execution policy (§4.4), as an executor needs it.

    §4.4 makes the charter "a versioned configuration document, agreed with the client at gate
    G1 — the contract the Proof Engine enforces and the reason parity can be a test rather
    than an opinion". Its example spells out numeric, null, date, string, ordering, row,
    sampling, parameter and waiver policy; **which strategy to execute with is policy too**,
    and S2.4.1 says so: "chosen per case from the charter and capabilities".

    Only the execution section is modelled here. The tolerances belong to §10.3's diff, which
    is E7's, and an adapter has no business knowing them.
    """

    version: str = "unversioned"
    strategy_order: tuple[ExecutionStrategy, ...] = DEFAULT_STRATEGY_ORDER
    timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS
    per_case: dict[str, ExecutionStrategy] = field(default_factory=dict)
    """A case the client has agreed to prove a particular way. Rare, and always a decision
    somebody made: a sheet whose extract is stale proves nothing useful from an extract read.
    """

    def strategy_for(
        self, case: ParityCase, available: Sequence[ExecutionStrategy]
    ) -> ExecutionStrategy | None:
        """The strategy to use, or None when the deployment can do none of them.

        Capabilities filter and the charter orders — in that order, because a charter naming a
        strategy the deployment cannot perform is a charter written before somebody looked,
        and silently substituting another would produce evidence of a kind nobody agreed to.
        An override for a strategy that is unavailable is therefore *refused*, not downgraded.
        """
        usable = tuple(available)
        override = self.per_case.get(case.id)
        if override is not None:
            return override if override in usable else None
        for strategy in self.strategy_order:
            if strategy in usable:
                return strategy
        return None


@dataclass(frozen=True, slots=True)
class ParityRunStamp:
    """The adapter identity every ParityRun carries (S2.1.1 criterion 4).

    E7 owns the run itself. This is the part S2.1.1 fixes: whatever a run turns out to be,
    it records which interface, which adapter build and which grammar produced its evidence.
    ``from_results`` refuses to stamp a run whose result sets disagree about that, because a
    run assembled from two adapter versions is not one run.
    """

    interface_version: str
    adapter_name: str
    adapter_version: str
    grammar_version: str | None = None

    @classmethod
    def from_results(cls, results: Sequence[ResultSet]) -> ParityRunStamp:
        if not results:
            raise ValueError("a parity run stamp needs at least one result set")
        stamps = {
            (r.interface_version, r.adapter_name, r.adapter_version, r.grammar_version)
            for r in results
        }
        if len(stamps) > 1:
            raise ValueError(
                "result sets in one parity run came from different adapter builds: "
                + "; ".join(
                    sorted(f"{n} {v} (interface {i}, grammar {g})" for i, n, v, g in stamps)
                )
            )
        interface, name, version, grammar = stamps.pop()
        return cls(interface, name, version, grammar)
