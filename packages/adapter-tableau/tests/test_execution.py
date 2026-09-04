"""S2.4.1 — executing a parity case against Tableau itself.

    "…so that the expected side of every proof comes from Tableau itself, not from a
    re-implementation."

That is the constraint these tests hold the code to. The platform could compute the expected
answer from the AST it parsed in F2.3 — and every disagreement would then be between two of
our own implementations, proving nothing about the client's report.
"""

from __future__ import annotations

from typing import Any

from astra_adapter import (
    Column,
    ColumnRole,
    ExecutionCharter,
    ExecutionOutcome,
    ExecutionStrategy,
    ParityCase,
    Scope,
)

from astra_adapter_tableau.ports import NoExtractReader, NoLiveQueryRunner

from .conftest import adapter_for
from .fake_tableau import FakeTableau

SCOPE = Scope(site="golden")


def case(**kwargs: Any) -> ParityCase:
    settings: dict[str, Any] = {
        "id": "case-1",
        "workbook_luid": "wb-00000",
        "sheet": "Workbook 0 sheet 0",
        "grain": ("Desk",),
        "measures": ("Amount",),
    }
    settings.update(kwargs)
    return ParityCase(**settings)


class WorkingExtractReader:
    """A stand-in for the Hyper API, so the extract-read *path* is exercised.

    Not a substitute for it: the real reader queries a `.hyper` and this returns fixed rows.
    What it proves is that the strategy is selected, the result is typed and stamped, and the
    reader's kind reaches the result — the parts that are this adapter's rather than Tableau's.
    """

    kind = "test-double"
    available = True

    async def read(self, parity_case: ParityCase) -> tuple[Any, Any, bool]:
        columns = (
            Column("Desk", ColumnRole.DIMENSION, "string"),
            Column("Amount", ColumnRole.MEASURE, "real"),
        )
        return columns, (("Rates", 1000.0), ("Credit", None)), False


class WorkingLiveRunner:
    kind = "test-double"
    available = True

    async def run(self, parity_case: ParityCase) -> tuple[Any, Any, bool, str]:
        columns = (
            Column("Desk", ColumnRole.DIMENSION, "string"),
            Column("Amount", ColumnRole.MEASURE, "real"),
        )
        return columns, (("Rates", 2000.0),), False, "select desk, sum(amount) from positions"


async def executed(adapter, **kwargs: Any):
    [ref async for ref in adapter.enumerate(SCOPE)]
    return await adapter.execute_case(case(**kwargs))


# ------------------------------------------------------------------- view data


async def test_view_data_executes_against_tableau(adapter) -> None:
    """The strategy that works against any Tableau, because it needs nothing but the REST API
    the adapter already speaks."""
    result = await executed(adapter)

    assert result.outcome is ExecutionOutcome.OK
    assert result.strategy is ExecutionStrategy.VIEW_DATA
    assert result.rows
    assert result.detail["view_id"]


async def test_the_result_has_ordered_typed_columns(adapter) -> None:
    """S2.4.1's third criterion, and §10.2's "ordered list of column descriptors (name, role,
    type)". The role is what §10.3's diff matches rows on versus what it compares under
    tolerance — a result set that did not carry it would make the Proof Engine guess."""
    result = await executed(adapter)

    assert [column.name for column in result.columns] == ["Desk", "Amount"]
    assert result.grain == ("Desk",)
    assert result.measures == ("Amount",)
    assert result.columns[1].type == "real"


async def test_nulls_are_preserved_as_nulls(adapter) -> None:
    """Not a formality. §4.4's charter has one rule for `source_null_vs_target_zero` (FAIL)
    and another for `source_null_vs_target_blank` (PASS), so an executor that coerced a null
    to an empty string or a zero would decide a verdict the charter is supposed to decide."""
    result = await executed(adapter)

    values = [row[1] for row in result.rows]
    assert None in values, "an empty CSV field is a null"
    assert "" not in values
    assert 0 not in values


async def test_tableaus_other_spelling_of_null_is_also_a_null(adapter) -> None:
    """Tableau writes an empty field for a null and the literal `%null%` for a null aggregate.
    `csv` gives back `''` for one and `'%null%'` for the other, and both mean null."""
    result = await executed(adapter)

    assert "%null%" not in [row[1] for row in result.rows]
    assert [row[1] for row in result.rows].count(None) == 2


async def test_filters_and_parameters_are_applied_through_vf(adapter, server) -> None:
    """§6.2: "REST queryViewData for the sheet with filter and parameter values applied via
    vf_ parameters". A case executed without its filters compares a report nobody has."""
    result = await executed(adapter, filters=(("desk", "Rates"),))

    assert result.outcome is ExecutionOutcome.OK
    assert "vf_desk" in (result.detail["vf_parameters"] or "")
    assert [row[0] for row in result.rows] == ["Rates"], "the filter reached Tableau"


async def test_a_sheet_that_is_not_a_published_view_is_reported(adapter) -> None:
    """A sheet not published is a sheet no user sees, and §10.1 derives cases from what the
    user sees. Inconclusive rather than failed: nothing about the report is known to be
    wrong."""
    result = await executed(adapter, sheet="A Sheet Nobody Published")

    assert result.outcome is ExecutionOutcome.INCONCLUSIVE
    assert "not a published view" in result.reason


# --------------------------------------------------------- strategy selection


async def test_the_charter_chooses_the_strategy(server: FakeTableau) -> None:
    """S2.4.1's second criterion. §4.4 makes the charter the contract the Proof Engine
    enforces, and which strategy to execute with is policy like any other."""
    adapter = adapter_for(server)
    adapter._extract_reader = WorkingExtractReader()
    adapter._executor._extract_reader = WorkingExtractReader()
    try:
        preferred = await adapter.execute_case(case())
        assert preferred.strategy is ExecutionStrategy.EXTRACT_READ, "the default order"

        adapter._charter = ExecutionCharter(
            version="v3", strategy_order=(ExecutionStrategy.VIEW_DATA,)
        )
        [ref async for ref in adapter.enumerate(SCOPE)]
        chosen = await adapter.execute_case(case())
        assert chosen.strategy is ExecutionStrategy.VIEW_DATA
    finally:
        await adapter.aclose()


async def test_capabilities_filter_before_the_charter_orders(adapter) -> None:
    """A charter naming a strategy the deployment cannot perform was written before somebody
    looked. This deployment has no Hyper API, so the order falls through to view data."""
    adapter._charter = ExecutionCharter(
        version="v3",
        strategy_order=(ExecutionStrategy.EXTRACT_READ, ExecutionStrategy.VIEW_DATA),
    )
    result = await executed(adapter)

    assert result.strategy is ExecutionStrategy.VIEW_DATA
    assert result.outcome is ExecutionOutcome.OK


async def test_a_per_case_override_is_refused_rather_than_downgraded(adapter) -> None:
    """The sharpest judgement in the story. A client agreed this case would be proved a
    particular way; silently substituting another produces evidence of a kind nobody agreed
    to, and a verdict resting on it would be worse than no verdict."""
    adapter._charter = ExecutionCharter(
        version="v3", per_case={"case-1": ExecutionStrategy.EXTRACT_READ}
    )
    result = await executed(adapter)

    assert result.outcome is ExecutionOutcome.INCONCLUSIVE
    assert "requires EXTRACT_READ" in result.reason
    assert "nobody agreed to" in result.reason


async def test_the_strategy_used_is_recorded_on_the_result(adapter) -> None:
    """§6.2 and S2.4.1: the three strategies are different evidence, and a verdict that did
    not name its strategy could not be audited."""
    result = await executed(adapter)

    assert result.strategy.value in {"EXTRACT_READ", "VIEW_DATA", "LIVE_REPLAY"}
    assert result.interface_version == "1.1"
    assert result.adapter_name == "tableau"


async def test_a_parity_run_stamp_carries_the_strategy_evidence(adapter) -> None:
    """S2.1.1 put the stamp on the ResultSet so an unstamped ParityRun is impossible."""
    from astra_adapter import ParityRunStamp

    result = await executed(adapter)
    stamp = ParityRunStamp.from_results([result])

    assert stamp.interface_version == "1.1"
    assert stamp.adapter_name == "tableau"


# ------------------------------------------------------------------- timeouts


async def test_a_timeout_is_inconclusive_with_its_reason(server: FakeTableau) -> None:
    """S2.4.1's fourth criterion, and §10.2's: "a timeout on either side yields INCONCLUSIVE,
    not FAIL". A timeout recorded as a failure puts a Migration Unit into remediation over a
    slow warehouse, and somebody spends a day looking for a bug in a correct report."""
    server.view_data_delay = 1.0
    adapter = adapter_for(server)
    adapter._charter = ExecutionCharter(version="v3", timeout_seconds=0.05)
    try:
        [ref async for ref in adapter.enumerate(SCOPE)]
        result = await adapter.execute_case(case())
    finally:
        await adapter.aclose()

    assert result.outcome is ExecutionOutcome.INCONCLUSIVE
    assert "timed out" in result.reason
    assert "budget" in result.reason, "say what the budget was"
    assert not result.comparable, "§10.3 must not diff this"


def test_the_default_timeout_is_120_seconds() -> None:
    """S2.4.1 fixes it; §10.2 asks for one."""
    assert ExecutionCharter().timeout_seconds == 120.0


async def test_an_inconclusive_result_is_still_stamped(adapter, server: FakeTableau) -> None:
    """It is evidence about the run even though it is not evidence about the report."""
    server.view_data_delay = 1.0
    adapter._charter = ExecutionCharter(version="v3", timeout_seconds=0.05)
    result = await executed(adapter)

    assert result.outcome is ExecutionOutcome.INCONCLUSIVE
    assert result.interface_version == "1.1"
    assert result.adapter_name == "tableau"


# ---------------------------------------------------------- the absent strategies


async def test_extract_read_says_why_it_is_unavailable(adapter) -> None:
    """A missing capability that reports itself is a fact the Estate surface can show. One
    that quietly returned an empty result set would be a parity case that passed against
    nothing."""
    sites = await adapter.sites(SCOPE)

    execution = sites[0].detail["execution"]
    assert execution["extract_read"]["available"] is False
    assert "Hyper API" in execution["extract_read"]["detail"]
    assert "licence" in execution["extract_read"]["detail"]


async def test_live_replay_says_why_it_is_unavailable(adapter) -> None:
    sites = await adapter.sites(SCOPE)

    execution = sites[0].detail["execution"]
    assert execution["live_replay"]["available"] is False
    assert "E11" in execution["live_replay"]["detail"]


async def test_an_absent_reader_reports_its_kind(adapter) -> None:
    """`absent` rather than an empty string, so a result claiming to have been read by
    "absent" is obviously wrong rather than plausibly empty."""
    assert NoExtractReader().kind == "absent"
    assert NoLiveQueryRunner().kind == "absent"


async def test_a_present_reader_makes_the_capability_claimed(server: FakeTableau) -> None:
    """Capabilities reflect the deployment, not the code. With a reader installed the
    strategy is claimed and the conformance suite will check it."""
    adapter = adapter_for(server)
    adapter._extract_reader = WorkingExtractReader()
    adapter._executor._extract_reader = WorkingExtractReader()
    try:
        assert adapter.manifest().capabilities.extract_read is True
        result = await adapter.execute_case(case())
    finally:
        await adapter.aclose()

    assert result.strategy is ExecutionStrategy.EXTRACT_READ
    assert result.detail["reader"] == "test-double"
    assert None in [row[1] for row in result.rows], "nulls survive this strategy too"


async def test_live_replay_keeps_the_query_it_ran(server: FakeTableau) -> None:
    """ "The live replay disagreed" is a question about a query, and a question about a query
    nobody can produce is unanswerable."""
    adapter = adapter_for(server)
    adapter._live_runner = WorkingLiveRunner()
    adapter._executor._live_runner = WorkingLiveRunner()
    adapter._charter = ExecutionCharter(
        version="v3", strategy_order=(ExecutionStrategy.LIVE_REPLAY,)
    )
    try:
        result = await adapter.execute_case(case())
    finally:
        await adapter.aclose()

    assert result.strategy is ExecutionStrategy.LIVE_REPLAY
    assert "select desk" in result.detail["sql"]


# -------------------------------------------------------------- determinism


async def test_the_same_case_gives_the_same_result(adapter) -> None:
    """§6.3's determinism check, which the conformance suite runs three times. A parity
    verdict resting on an executor that answered differently each time would be noise."""
    [ref async for ref in adapter.enumerate(SCOPE)]

    first = await adapter.execute_case(case())
    second = await adapter.execute_case(case())

    assert first.fingerprint == second.fingerprint
    assert first.rows == second.rows


async def test_a_result_survives_the_wire(adapter) -> None:
    """Typed columns, an outcome and a reason all cross the adapter RPC now."""
    import json

    from astra_adapter.rpc import wire

    result = await executed(adapter)
    restored = wire.decode_result_set(json.loads(json.dumps(wire.encode_result_set(result))))

    assert restored == result
    assert restored.columns[0].role is ColumnRole.DIMENSION
