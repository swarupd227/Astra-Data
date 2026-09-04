"""Executing a parity case against Tableau — story S2.4.1.

    "I want the adapter to execute a parity case on the source side and return a typed
    ResultSet, so that the expected side of every proof comes from Tableau itself, not from a
    re-implementation."

That *so-that* is the whole design constraint. The platform could compute the expected answer
from the AST it parsed in F2.3 — and every disagreement would then be a disagreement between
two of our own implementations, proving nothing about the client's report. The expected side
has to come from Tableau, which means the executor's job is to *ask Tableau* and carry back
what it said, unmodified.

§6.2 names three strategies in preference order, and they are three different kinds of
evidence:

1. **Extract read** — query the packaged or published ``.hyper`` at the case grain. Proves
   what the published extract contains, which is what the client's report actually rendered
   from.
2. **View data** — the REST ``queryViewData`` endpoint with the sheet's filters and parameters
   applied through ``vf_`` parameters. Proves what the sheet shows a user.
3. **Live replay** — reconstruct the datasource SQL (with custom SQL verbatim) and run it
   against the source connection. Proves what the database returns *now*.

They can legitimately disagree — a stale extract against a live warehouse is the commonest
finding in a migration — which is why §6.2 records the strategy on the case and why a verdict
that did not name its strategy could not be audited.

**Two of the three need something this deployment does not have**, and both say so rather
than pretending. See `ports.py`.
"""

from __future__ import annotations

import asyncio
import csv
import io
import logging
import time
from typing import Any

from astra_adapter import (
    INTERFACE_VERSION,
    AdapterError,
    Column,
    ColumnRole,
    ExecutionCharter,
    ExecutionOutcome,
    ExecutionStrategy,
    ParityCase,
    ResultSet,
    UnsupportedCapability,
)

from .ports import ExtractReader, LiveQueryRunner
from .rest import TableauRestClient
from .views import resolve_view_id

logger = logging.getLogger(__name__)

#: Tableau's own null marker in a view-data CSV. It sends an empty field for a null and the
#: literal string "%null%" for a null *aggregate* — and the two mean the same thing to a
#: reader and different things to a naive CSV parse.
TABLEAU_NULL_MARKERS = ("", "%null%", "%missing%")

#: Measure-looking Tableau type names, for typing a view-data column. Tableau's CSV carries no
#: types, so the case's own `measures` list is the authority and this is only the fallback for
#: a column the case did not name.
_NUMERIC_HINTS = ("sum", "avg", "count", "min", "max", "total", "amount", "value")


class TableauExecutor:
    """Runs one parity case, by whichever strategy the charter and capabilities allow."""

    def __init__(
        self,
        rest: TableauRestClient,
        *,
        adapter_name: str,
        adapter_version: str,
        grammar_version: str,
        extract_reader: ExtractReader | None = None,
        live_runner: LiveQueryRunner | None = None,
    ) -> None:
        self._rest = rest
        self._adapter_name = adapter_name
        self._adapter_version = adapter_version
        self._grammar_version = grammar_version
        self._extract_reader = extract_reader
        self._live_runner = live_runner

    # ------------------------------------------------------------- capabilities

    def strategies(self) -> tuple[ExecutionStrategy, ...]:
        """The strategies this deployment can actually perform.

        Capabilities are claims and claims are binding (S2.1.2): a strategy is listed only
        when the thing that performs it is present. `VIEW_DATA` always is — it is a REST call
        the adapter already knows how to make.
        """
        available = [ExecutionStrategy.VIEW_DATA]
        if self._extract_reader is not None and self._extract_reader.available:
            available.insert(0, ExecutionStrategy.EXTRACT_READ)
        if self._live_runner is not None and self._live_runner.available:
            available.append(ExecutionStrategy.LIVE_REPLAY)
        return tuple(available)

    # ---------------------------------------------------------------- executing

    async def execute(
        self, case: ParityCase, *, charter: ExecutionCharter | None = None
    ) -> ResultSet:
        """Execute one case. Returns a `ResultSet`; raises only for a broken adapter.

        A case that cannot be executed comes back **INCONCLUSIVE with a reason**, not as an
        exception. §10.2 is explicit that a timeout "yields INCONCLUSIVE, not FAIL", and the
        same reasoning covers a missing extract reader or a strategy the charter asked for and
        the deployment cannot do: none of them is evidence that the report is wrong, and a
        verdict of FAIL would send somebody to look for a bug in a correct report.
        """
        policy = charter or ExecutionCharter()
        available = self.strategies()
        strategy = policy.strategy_for(case, available)

        if strategy is None:
            return self._inconclusive(
                case,
                ExecutionStrategy.VIEW_DATA,
                self._no_strategy_reason(case, policy, available),
            )

        started = time.perf_counter()
        try:
            result = await asyncio.wait_for(
                self._run(case, strategy), timeout=policy.timeout_seconds
            )
        except TimeoutError:
            # S2.4.1's fourth criterion. The elapsed time is in the reason because "it timed
            # out" and "it timed out after 120 seconds having read nothing" send a parity
            # engineer to different places.
            elapsed = time.perf_counter() - started
            return self._inconclusive(
                case,
                strategy,
                f"execution timed out after {elapsed:.0f}s (budget "
                f"{policy.timeout_seconds:.0f}s) using {strategy.value}. §10.2 retries once "
                f"with a longer budget before surfacing this.",
            )
        except UnsupportedCapability as exc:
            return self._inconclusive(case, strategy, str(exc))
        except AdapterError as exc:
            # An adapter error executing a case is still not a parity failure: the source
            # refused, and what the report contains is unknown rather than wrong.
            return self._inconclusive(case, strategy, f"{strategy.value} failed: {exc}")

        return result

    async def _run(self, case: ParityCase, strategy: ExecutionStrategy) -> ResultSet:
        if strategy is ExecutionStrategy.EXTRACT_READ:
            return await self._extract_read(case)
        if strategy is ExecutionStrategy.LIVE_REPLAY:
            return await self._live_replay(case)
        return await self._view_data(case)

    # ------------------------------------------------------------- view data

    async def _view_data(self, case: ParityCase) -> ResultSet:
        """§6.2: "REST queryViewData for the sheet with filter and parameter values applied
        via ``vf_`` parameters".

        The strategy that works against any Tableau, because it is the one that needs nothing
        but the REST API the adapter already speaks. It returns CSV — Tableau's own rendering
        of what the sheet shows — which is exactly the evidence this story asks for.
        """
        if not case.sheet:
            raise AdapterError(
                f"case {case.id!r} names no sheet, and view data is read per view",
                retryable=False,
            )

        view_id = await resolve_view_id(self._rest, case.workbook_luid, case.sheet)
        query = "&".join(
            f"vf_{_encode(name)}={_encode(value)}"
            for name, value in (*case.filters, *case.parameters)
        )
        path = (
            "/api/{version}/sites/{site_id}/views/"
            + view_id
            + "/data"
            + (f"?{query}" if query else "")
        )
        response = await self._rest.call("GET", path)
        columns, rows, truncated = _read_csv(response.text, case)

        return ResultSet(
            case_id=case.id,
            columns=columns,
            rows=rows,
            strategy=ExecutionStrategy.VIEW_DATA,
            interface_version=INTERFACE_VERSION,
            adapter_name=self._adapter_name,
            adapter_version=self._adapter_version,
            grammar_version=self._grammar_version,
            truncated=truncated,
            detail={"view_id": view_id, "vf_parameters": query or None},
        )

    # ---------------------------------------------------------- extract read

    async def _extract_read(self, case: ParityCase) -> ResultSet:
        """§6.2: "query the packaged or published .hyper with the Hyper API at the case
        grain"."""
        if self._extract_reader is None or not self._extract_reader.available:
            raise UnsupportedCapability("extract_read", adapter=self._adapter_name)

        columns, rows, truncated = await self._extract_reader.read(case)
        return ResultSet(
            case_id=case.id,
            columns=columns,
            rows=rows,
            strategy=ExecutionStrategy.EXTRACT_READ,
            interface_version=INTERFACE_VERSION,
            adapter_name=self._adapter_name,
            adapter_version=self._adapter_version,
            grammar_version=self._grammar_version,
            truncated=truncated,
            detail={"reader": self._extract_reader.kind},
        )

    # ----------------------------------------------------------- live replay

    async def _live_replay(self, case: ParityCase) -> ResultSet:
        """§6.2: "reconstruct the datasource SQL (with custom SQL verbatim) and execute
        against the source connection under the client's service account"."""
        if self._live_runner is None or not self._live_runner.available:
            raise UnsupportedCapability("live_query", adapter=self._adapter_name)

        columns, rows, truncated, sql = await self._live_runner.run(case)
        return ResultSet(
            case_id=case.id,
            columns=columns,
            rows=rows,
            strategy=ExecutionStrategy.LIVE_REPLAY,
            interface_version=INTERFACE_VERSION,
            adapter_name=self._adapter_name,
            adapter_version=self._adapter_version,
            grammar_version=self._grammar_version,
            truncated=truncated,
            # The query is kept because "the live replay disagreed" is a question about a
            # query, and a question about a query nobody can produce is unanswerable.
            detail={"runner": self._live_runner.kind, "sql": sql},
        )

    # ------------------------------------------------------------------ helpers

    def _inconclusive(
        self, case: ParityCase, strategy: ExecutionStrategy, reason: str
    ) -> ResultSet:
        logger.info("case %s is inconclusive: %s", case.id, reason)
        return ResultSet(
            case_id=case.id,
            columns=(),
            rows=(),
            strategy=strategy,
            interface_version=INTERFACE_VERSION,
            adapter_name=self._adapter_name,
            adapter_version=self._adapter_version,
            grammar_version=self._grammar_version,
            outcome=ExecutionOutcome.INCONCLUSIVE,
            reason=reason,
        )

    def _no_strategy_reason(
        self,
        case: ParityCase,
        charter: ExecutionCharter,
        available: tuple[ExecutionStrategy, ...],
    ) -> str:
        override = charter.per_case.get(case.id)
        if override is not None:
            return (
                f"charter {charter.version} requires {override.value} for this case and this "
                f"deployment cannot perform it (available: "
                f"{', '.join(s.value for s in available) or 'none'}). Substituting another "
                f"strategy would produce evidence of a kind nobody agreed to."
            )
        return (
            f"no strategy in charter {charter.version} "
            f"({', '.join(s.value for s in charter.strategy_order)}) is available on this "
            f"deployment ({', '.join(s.value for s in available) or 'none'})"
        )


# --------------------------------------------------------------------- CSV


def _read_csv(text: str, case: ParityCase) -> tuple[tuple[Column, ...], tuple[Any, ...], bool]:
    """Tableau's view-data CSV into typed columns and rows, nulls preserved.

    **Nulls are the part that matters.** Tableau writes an empty field for a null and the
    literal ``%null%`` for a null aggregate; `csv` gives back ``''`` for both, and an executor
    that let that through would hand the Proof Engine an empty string where the source had a
    null. §4.4's charter has one rule for ``source_null_vs_target_zero`` (FAIL) and another for
    ``source_null_vs_target_blank`` (PASS) — so coercing here would decide a verdict the
    charter is supposed to decide.
    """
    reader = csv.reader(io.StringIO(text))
    try:
        header = next(reader)
    except StopIteration:
        return (), (), False

    measures = {name.lower() for name in case.measures}
    columns = tuple(
        Column(
            name=name.strip(),
            role=(
                ColumnRole.MEASURE
                if name.strip().lower() in measures or _looks_numeric(name)
                else ColumnRole.DIMENSION
            ),
            type="real" if name.strip().lower() in measures or _looks_numeric(name) else "string",
        )
        for name in header
    )

    rows: list[tuple[Any, ...]] = []
    truncated = False
    for record in reader:
        if len(rows) >= case.row_limit:
            truncated = True
            break
        rows.append(
            tuple(
                _value(record[index] if index < len(record) else "", columns[index])
                for index in range(len(columns))
            )
        )
    return columns, tuple(rows), truncated


def _value(raw: str, column: Column) -> Any:
    text = raw.strip()
    if text in TABLEAU_NULL_MARKERS:
        return None
    if column.role is ColumnRole.MEASURE:
        try:
            return float(text.replace(",", ""))
        except ValueError:
            # A measure column carrying something unparseable is a fact about the source, not
            # a reason to drop the row: §10.3 compares it as text and the charter decides.
            return text
    return text


def _looks_numeric(name: str) -> bool:
    lowered = name.lower()
    return any(hint in lowered for hint in _NUMERIC_HINTS)


def _encode(value: str) -> str:
    from urllib.parse import quote

    return quote(str(value), safe="")
