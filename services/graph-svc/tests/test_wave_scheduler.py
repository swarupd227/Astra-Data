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

from astra_graph.migration_units import MU_STATES
from astra_graph.ontology.nodes import _FAMILY_STATES as _ONTOLOGY_FAMILY_STATES
from astra_graph.wave_scheduler import (
    _ACTIVE_MU_STATES,
    _FAMILY_STATES_ORDERED,
    _IN_PROGRESS_MU_STATES,
    SchedulerConstraint,
    SchedulerDecision,
    WaveScheduler,
)


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


class TestWaveSchedulerAdmission:
    """Wave scheduler admission evaluation -- constraint checking.

    Constraint evaluation reads real graph state (node properties, edge
    traversal) through ``GraphRepository.run_read_only_cypher``, which only
    Apache AGE really implements (``InMemoryGraphRepository.
    run_read_only_cypher`` raises ``NotImplementedError`` by design -- see its
    own docstring: "executing Cypher is the store's job"). So the constraint
    logic itself is exercised end to end in
    ``test_integration_wave_scheduler.py`` against real PostgreSQL + Apache
    AGE; these tests cover construction and the pure logic around it.
    """

    async def test_scheduler_initializes(self, repository):
        """WaveScheduler can be instantiated with a GraphRepository."""
        scheduler = WaveScheduler(repository)
        assert scheduler.repository is repository

    def test_family_state_order_matches_the_ontology(self):
        """The scheduler's own ordering must be exactly the ontology's ModelFamily
        lifecycle -- if a state is added, renamed or reordered there, this fails
        rather than the scheduler silently admitting against a stale ladder."""
        assert _FAMILY_STATES_ORDERED == _ONTOLOGY_FAMILY_STATES
        assert _FAMILY_STATES_ORDERED.index("BUILT") > _FAMILY_STATES_ORDERED.index("APPROVED")

    def test_mu_state_sets_only_name_real_states(self):
        """Every state the scheduler counts against a limit must be a real §3.2 MU
        state, and 'active' work must never include a terminal state."""
        real = set(MU_STATES)
        assert set(_ACTIVE_MU_STATES) <= real
        assert set(_IN_PROGRESS_MU_STATES) <= real
        assert not set(_ACTIVE_MU_STATES) & {"WITHDRAWN", "DECOMMISSIONED"}
        assert not set(_IN_PROGRESS_MU_STATES) & {"WITHDRAWN", "DECOMMISSIONED", "GENERATED"}
