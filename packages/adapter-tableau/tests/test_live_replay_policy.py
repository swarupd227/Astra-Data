"""Live replay's own tenant policy and SELECT-only allow-list -- story S11.2.1, opens
F11.2, spec §18.2.

Live replay itself is still a disclosed, permanently-unavailable stub in this codebase
(`ports.NoLiveQueryRunner`, no real database driver exists) -- these tests prove the
policy/allow-list layer against a real fixture `LiveQueryRunner`, so the mechanism is
independently provable without a live warehouse, ready for whenever a real one lands.
"""

from __future__ import annotations

from typing import Any

import pytest
from astra_adapter import Column, ColumnRole, ParityCase

from astra_adapter_tableau.live_replay_policy import (
    LiveReplayPolicy,
    LiveReplayPolicyError,
    PolicyGatedLiveQueryRunner,
    validate_select_only,
)
from astra_adapter_tableau.ports import NoLiveQueryRunner

CASE = ParityCase(
    id="case-1", workbook_luid="wb-00000", sheet="sheet-0", grain=("Desk",), measures=("Amount",),
)


class FakeLiveQueryRunner:
    """A real, in-memory stand-in for a live SQL driver -- proves the policy layer
    without a live warehouse. Not `NoLiveQueryRunner`: this one really is available and
    really does return a caller-chosen SQL string, the two things the policy layer needs
    to check."""

    kind = "fixture"

    def __init__(self, *, sql: str, rows: tuple[tuple[Any, ...], ...] = ((1, "a"),), available: bool = True) -> None:
        self._sql = sql
        self._rows = rows
        self.available = available
        self.calls = 0

    @property
    def detail(self) -> str:
        return "a fixture runner, standing in for a real database driver"

    async def run(
        self, case: ParityCase
    ) -> tuple[tuple[Column, ...], tuple[tuple[Any, ...], ...], bool, str]:
        self.calls += 1
        columns = (Column("id", ColumnRole.DIMENSION, "integer"), Column("name", ColumnRole.DIMENSION, "string"))
        return columns, self._rows, False, self._sql


# ------------------------------------------------------------------ validate_select_only


class TestValidateSelectOnly:
    def test_a_plain_select_is_permitted(self) -> None:
        validate_select_only("SELECT id, name FROM orders")

    def test_a_cte_select_is_permitted(self) -> None:
        validate_select_only("WITH recent AS (SELECT id FROM orders) SELECT * FROM recent")

    def test_a_join_and_where_select_is_permitted(self) -> None:
        validate_select_only(
            "SELECT o.id FROM orders o JOIN customers c ON c.id = o.customer_id WHERE c.region = 'EMEA'"
        )

    @pytest.mark.parametrize(
        "sql",
        [
            "DELETE FROM orders",
            "UPDATE orders SET total = 0",
            "INSERT INTO orders (id) VALUES (1)",
            "DROP TABLE orders",
            "CREATE TABLE evil (id int)",
            "TRUNCATE orders",
        ],
    )
    def test_a_write_shaped_statement_is_refused(self, sql: str) -> None:
        with pytest.raises(LiveReplayPolicyError, match="SELECT only"):
            validate_select_only(sql)

    def test_a_stacked_statement_is_refused_even_though_the_first_is_a_select(self) -> None:
        with pytest.raises(LiveReplayPolicyError, match="statements"):
            validate_select_only("SELECT 1; DROP TABLE orders")

    def test_empty_sql_is_refused(self) -> None:
        with pytest.raises(LiveReplayPolicyError, match="empty"):
            validate_select_only("   ")

    def test_unparseable_sql_is_refused(self) -> None:
        with pytest.raises(LiveReplayPolicyError):
            validate_select_only("SELECT FROM WHERE :::")


# ------------------------------------------------------------- PolicyGatedLiveQueryRunner


class TestPolicyGatedLiveQueryRunner:
    async def test_disabled_policy_refuses_before_ever_reaching_the_inner_runner(self) -> None:
        inner = FakeLiveQueryRunner(sql="SELECT 1")
        gated = PolicyGatedLiveQueryRunner(inner, LiveReplayPolicy(enabled=False))

        assert gated.available is False
        with pytest.raises(Exception, match="live_query"):
            await gated.run(CASE)
        assert inner.calls == 0

    async def test_enabled_policy_delegates_to_a_real_select(self) -> None:
        inner = FakeLiveQueryRunner(sql="SELECT id, name FROM orders")
        gated = PolicyGatedLiveQueryRunner(inner, LiveReplayPolicy(enabled=True))

        assert gated.available is True
        columns, rows, truncated, sql = await gated.run(CASE)
        assert inner.calls == 1
        assert sql == "SELECT id, name FROM orders"
        assert len(rows) == 1
        assert truncated is False

    async def test_enabled_policy_still_refuses_a_write_shaped_reconstruction(self) -> None:
        inner = FakeLiveQueryRunner(sql="DELETE FROM orders")
        gated = PolicyGatedLiveQueryRunner(inner, LiveReplayPolicy(enabled=True))

        with pytest.raises(LiveReplayPolicyError, match="SELECT only"):
            await gated.run(CASE)
        # The refusal happens after the inner runner reconstructed the SQL (there is no
        # way to see it before running) but the caller never receives write-shaped rows.
        assert inner.calls == 1

    async def test_rows_over_max_rows_are_truncated_and_marked(self) -> None:
        inner = FakeLiveQueryRunner(sql="SELECT id FROM orders", rows=tuple((i,) for i in range(10)))
        gated = PolicyGatedLiveQueryRunner(inner, LiveReplayPolicy(enabled=True, max_rows=3))

        _columns, rows, truncated, _sql = await gated.run(CASE)
        assert len(rows) == 3
        assert truncated is True

    async def test_rows_within_max_rows_are_not_marked_truncated(self) -> None:
        inner = FakeLiveQueryRunner(sql="SELECT id FROM orders", rows=((1,), (2,)))
        gated = PolicyGatedLiveQueryRunner(inner, LiveReplayPolicy(enabled=True, max_rows=100))

        _columns, rows, truncated, _sql = await gated.run(CASE)
        assert len(rows) == 2
        assert truncated is False

    async def test_available_is_false_when_the_inner_runner_is_absent_regardless_of_policy(self) -> None:
        gated = PolicyGatedLiveQueryRunner(NoLiveQueryRunner(), LiveReplayPolicy(enabled=True))
        assert gated.available is False

    def test_detail_reports_the_more_fundamental_blocker_when_no_runner_exists(self) -> None:
        gated = PolicyGatedLiveQueryRunner(NoLiveQueryRunner(), LiveReplayPolicy(enabled=False))
        assert "E11" in gated.detail

    def test_detail_reports_the_tenant_policy_once_a_real_runner_exists(self) -> None:
        inner = FakeLiveQueryRunner(sql="SELECT 1")
        gated = PolicyGatedLiveQueryRunner(inner, LiveReplayPolicy(enabled=False))
        assert "execution-safety policy" in gated.detail

    def test_kind_is_forwarded_from_the_inner_runner(self) -> None:
        gated = PolicyGatedLiveQueryRunner(FakeLiveQueryRunner(sql="SELECT 1"), LiveReplayPolicy())
        assert gated.kind == "fixture"
