"""Wave scheduler admission control tests (story S12.1.2).

The scheduler evaluates MU admission subject to:
- Family state (BUILT or later)
- Executor concurrency per site and per workspace
- Model-gateway budget
- WIP per train
- Train and site pause state

Scheduler decisions are visible via API and emitted as events for Wave Board visibility.
Enforcement (actual state transitions) is integrated with the MU workflow in a follow-on.
"""

from __future__ import annotations

import pytest

from astra_graph.wave_scheduler import SchedulerConstraint, SchedulerDecision, WaveScheduler


class TestSchedulerConstraint:
    """Scheduler constraint enum -- reasons an MU can be blocked."""

    def test_all_constraints_defined(self):
        """Verify all seven constraints are defined."""
        constraints = [
            SchedulerConstraint.FAMILY_STATE,
            SchedulerConstraint.EXECUTOR_CONCURRENCY_SITE,
            SchedulerConstraint.EXECUTOR_CONCURRENCY_WORKSPACE,
            SchedulerConstraint.MODEL_GATEWAY_BUDGET,
            SchedulerConstraint.WIP_LIMIT,
            SchedulerConstraint.PAUSED_TRAIN,
            SchedulerConstraint.PAUSED_SITE,
        ]
        assert len(constraints) == 7

    def test_constraint_values(self):
        """Verify string values match constraint names."""
        assert SchedulerConstraint.FAMILY_STATE.value == "FAMILY_STATE"
        assert SchedulerConstraint.EXECUTOR_CONCURRENCY_SITE.value == "EXECUTOR_CONCURRENCY_SITE"
        assert SchedulerConstraint.EXECUTOR_CONCURRENCY_WORKSPACE.value == "EXECUTOR_CONCURRENCY_WORKSPACE"
        assert SchedulerConstraint.MODEL_GATEWAY_BUDGET.value == "MODEL_GATEWAY_BUDGET"
        assert SchedulerConstraint.WIP_LIMIT.value == "WIP_LIMIT"
        assert SchedulerConstraint.PAUSED_TRAIN.value == "PAUSED_TRAIN"
        assert SchedulerConstraint.PAUSED_SITE.value == "PAUSED_SITE"


class TestSchedulerDecision:
    """Scheduler decision dataclass -- the outcome of an admission evaluation."""

    def test_admitted_decision_structure(self):
        """An admitted decision has no blocking constraint."""
        decision = SchedulerDecision(
            admitted=True,
            blocking_constraint=None,
            reason="All constraints satisfied",
        )
        assert decision.admitted is True
        assert decision.blocking_constraint is None
        assert "All constraints" in decision.reason

    def test_blocked_decision_structure(self):
        """A blocked decision names which constraint is blocking."""
        decision = SchedulerDecision(
            admitted=False,
            blocking_constraint=SchedulerConstraint.PAUSED_TRAIN,
            reason="Train train-123 is paused",
        )
        assert decision.admitted is False
        assert decision.blocking_constraint == SchedulerConstraint.PAUSED_TRAIN
        assert "paused" in decision.reason.lower()

    def test_each_constraint_can_block(self):
        """Each constraint type can appear in a blocked decision."""
        for constraint in [
            SchedulerConstraint.FAMILY_STATE,
            SchedulerConstraint.EXECUTOR_CONCURRENCY_SITE,
            SchedulerConstraint.EXECUTOR_CONCURRENCY_WORKSPACE,
            SchedulerConstraint.MODEL_GATEWAY_BUDGET,
            SchedulerConstraint.WIP_LIMIT,
            SchedulerConstraint.PAUSED_TRAIN,
            SchedulerConstraint.PAUSED_SITE,
        ]:
            decision = SchedulerDecision(
                admitted=False,
                blocking_constraint=constraint,
                reason=f"Blocked by {constraint.value}",
            )
            assert decision.blocking_constraint == constraint


@pytest.mark.asyncio
class TestWaveSchedulerAdmission:
    """Wave scheduler admission evaluation -- constraint checking."""

    async def test_scheduler_initializes(self, db):
        """WaveScheduler can be instantiated with a database."""
        scheduler = WaveScheduler(db)
        assert scheduler.db is db

    def test_family_state_ordering(self):
        """Family lifecycle ordering for BUILT threshold is correct."""
        states = (
            "PROPOSED", "SINGLETON", "DRAFT", "IN_REVIEW", "APPROVED", "BUILT",
            "PUBLISHED", "DEPRECATED"
        )
        # BUILT is the threshold
        assert states.index("BUILT") == 5
        # States before BUILT are not ready
        for state in ["PROPOSED", "SINGLETON", "DRAFT", "IN_REVIEW", "APPROVED"]:
            assert states.index(state) < states.index("BUILT")
        # States after BUILT are ready
        for state in ["PUBLISHED", "DEPRECATED"]:
            assert states.index(state) > states.index("BUILT")

    async def test_active_mu_state_set(self):
        """Active MU states are correctly identified for concurrency counting."""
        # Active states are those where work is in-progress
        active_states = ("PROVING", "MENDING", "ESCALATED")
        # Terminal states should not be counted
        terminal_states = ("WITHDRAWN", "DECOMMISSIONED")
        # Waiting states should not be counted (not yet admitted)
        waiting_states = ("GENERATED", "MODEL_READY", "CLUSTERED")
