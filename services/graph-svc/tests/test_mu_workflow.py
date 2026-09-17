"""Workflow-level unit tests for story S12.1.1 -- `MigrationUnitWorkflow`'s own control
flow, isolated from the real activities it drives in production.

Uses `temporalio.testing.WorkflowEnvironment.start_time_skipping()`, a real embedded
Temporal test server (not a mock of Temporal itself), with scripted fake activities
standing in for `MuActivities`'s own real `write_mu_state`/`run_generate`/`run_mend` --
those real activities (and the real `generate_c3_field`/`mend_exception` they wrap) are
exercised end-to-end in `test_integration_mu_workflow.py` instead. This file is about
the workflow's own decisions: which state a given activity outcome moves it to, that a
gate wait really is a durable signal, and that a genuine activity timeout is reported
as INCONCLUSIVE rather than raised as a hard failure.
"""

from __future__ import annotations

import asyncio
from datetime import timedelta

import pytest
from temporalio import activity, workflow
from temporalio.testing import WorkflowEnvironment
from temporalio.worker import Worker

from astra_graph.mu_workflow import (
    DEFAULT_GATE_WAIT_TIMEOUT,
    GateDecisionSignal,
    GenerateActivityInput,
    GenerateActivityResult,
    MendActivityInput,
    MendActivityResult,
    MigrationUnitWorkflow,
    MigrationUnitWorkflowInput,
    WriteMuStateInput,
    _run_activity_or_inconclusive,
)

# No `pytest.mark.integration` here: that marker means "requires PostgreSQL with
# Apache AGE" (`pyproject.toml`) and this file needs neither -- only the embedded
# Temporal test server `WorkflowEnvironment.start_time_skipping()` downloads and runs
# on its own. `test_integration_mu_workflow.py` is where the real Postgres-backed
# activities are exercised, and carries that marker for real.

TASK_QUEUE = "mu-workflow-test"


class ScriptedActivities:
    """Stands in for the real `MuActivities` -- registered under the identical
    activity-type names (`write_mu_state`/`run_generate`/`run_mend`) the workflow
    itself references via `workflow.execute_activity_method(MuActivities.xxx, ...)`,
    so the workflow cannot tell it isn't talking to the real thing."""

    def __init__(self) -> None:
        self.write_calls: list[WriteMuStateInput] = []
        self.generate_calls: list[GenerateActivityInput] = []
        self.mend_calls: list[MendActivityInput] = []
        self.generate_result: GenerateActivityResult | None = None
        self.mend_result: MendActivityResult | None = None

    @activity.defn(name="write_mu_state")
    async def write_mu_state(self, input: WriteMuStateInput) -> None:
        self.write_calls.append(input)

    @activity.defn(name="run_generate")
    async def run_generate(self, input: GenerateActivityInput) -> GenerateActivityResult:
        self.generate_calls.append(input)
        assert self.generate_result is not None, "test forgot to script a generate_result"
        return self.generate_result

    @activity.defn(name="run_mend")
    async def run_mend(self, input: MendActivityInput) -> MendActivityResult:
        self.mend_calls.append(input)
        assert self.mend_result is not None, "test forgot to script a mend_result"
        return self.mend_result


def _input(workbook_id: str) -> MigrationUnitWorkflowInput:
    return MigrationUnitWorkflowInput(
        workbook_id=workbook_id, calc_ids=("calc-1",), principal="agent:transpiler",
    )


async def _run(env: WorkflowEnvironment, activities: ScriptedActivities, workbook_id: str) -> str:
    async with Worker(
        env.client, task_queue=TASK_QUEUE, workflows=[MigrationUnitWorkflow],
        activities=[activities.write_mu_state, activities.run_generate, activities.run_mend],
    ):
        return await env.client.execute_workflow(
            MigrationUnitWorkflow.run, _input(workbook_id),
            id=f"mu-{workbook_id}", task_queue=TASK_QUEUE,
        )


@pytest.fixture(scope="module")
async def env() -> WorkflowEnvironment:
    """One real embedded Temporal test server for the whole file -- each test opens
    its own `Worker` and uses a workflow id unique to itself, so sharing the server
    (and the real time-skipping it's already doing) across tests only saves the real
    wall-clock cost of starting a fresh one per test, not test isolation."""
    async with await WorkflowEnvironment.start_time_skipping() as environment:
        yield environment


async def test_a_clean_generation_passes_straight_through(env: WorkflowEnvironment) -> None:
    activities = ScriptedActivities()
    activities.generate_result = GenerateActivityResult(ok=True, exception_case_id=None, measure_id="measure-1")

    final_state = await _run(env, activities, "wb-clean")

    assert final_state == "PASSED"
    assert [w.state for w in activities.write_calls] == ["PROVING", "PASSED"]
    assert len(activities.mend_calls) == 0


async def test_a_failure_with_no_exception_case_escalates_directly(env: WorkflowEnvironment) -> None:
    activities = ScriptedActivities()
    activities.generate_result = GenerateActivityResult(ok=False, exception_case_id=None, measure_id=None)

    final_state = await _run(env, activities, "wb-no-case")

    assert final_state == "ESCALATED"
    assert [w.state for w in activities.write_calls] == ["PROVING", "FAILED", "ESCALATED"]
    assert len(activities.mend_calls) == 0


async def test_a_mend_that_closes_the_case_returns_to_passed(env: WorkflowEnvironment) -> None:
    activities = ScriptedActivities()
    activities.generate_result = GenerateActivityResult(ok=False, exception_case_id="case-1", measure_id=None)
    activities.mend_result = MendActivityResult(outcome="closed")

    final_state = await _run(env, activities, "wb-mended")

    assert final_state == "PASSED"
    assert [w.state for w in activities.write_calls] == [
        "PROVING", "FAILED", "MENDING", "PROVING", "PASSED",
    ]
    assert len(activities.mend_calls) == 1


async def test_a_mend_that_cannot_close_the_case_awaits_a_gate_then_honours_the_signal(
    env: WorkflowEnvironment,
) -> None:
    activities = ScriptedActivities()
    activities.generate_result = GenerateActivityResult(ok=False, exception_case_id="case-2", measure_id=None)
    activities.mend_result = MendActivityResult(outcome="escalated")

    async with Worker(
        env.client, task_queue=TASK_QUEUE, workflows=[MigrationUnitWorkflow],
        activities=[activities.write_mu_state, activities.run_generate, activities.run_mend],
    ):
        handle = await env.client.start_workflow(
            MigrationUnitWorkflow.run, _input("wb-gate"),
            id="mu-wb-gate", task_queue=TASK_QUEUE,
        )

        for _ in range(500):
            if await handle.query(MigrationUnitWorkflow.current_state) == "ESCALATED":
                break
            await asyncio.sleep(0.01)
        else:
            raise AssertionError("workflow never reached the ESCALATED gate wait")

        await handle.signal(
            MigrationUnitWorkflow.submit_gate_decision,
            GateDecisionSignal(gate="G3", decision="PASSED", principal="human:adjudicator"),
        )
        final_state = await handle.result()

    assert final_state == "PASSED"
    assert activities.write_calls[-2].state == "ADJUDICATED"
    assert activities.write_calls[-1].state == "PASSED"


async def test_a_waived_adjudication_returns_to_proving_not_passed(env: WorkflowEnvironment) -> None:
    activities = ScriptedActivities()
    activities.generate_result = GenerateActivityResult(ok=False, exception_case_id="case-3", measure_id=None)
    activities.mend_result = MendActivityResult(outcome="escalated")

    async with Worker(
        env.client, task_queue=TASK_QUEUE, workflows=[MigrationUnitWorkflow],
        activities=[activities.write_mu_state, activities.run_generate, activities.run_mend],
    ):
        handle = await env.client.start_workflow(
            MigrationUnitWorkflow.run, _input("wb-waiver"),
            id="mu-wb-waiver", task_queue=TASK_QUEUE,
        )
        for _ in range(500):
            if await handle.query(MigrationUnitWorkflow.current_state) == "ESCALATED":
                break
            await asyncio.sleep(0.01)
        else:
            raise AssertionError("workflow never reached the ESCALATED gate wait")

        await handle.signal(
            MigrationUnitWorkflow.submit_gate_decision,
            GateDecisionSignal(gate="G3", decision="REJECTED", principal="human:adjudicator"),
        )
        final_state = await handle.result()

    assert final_state == "PROVING"


async def test_a_gate_left_undecided_stays_escalated_holding_no_resources(env: WorkflowEnvironment) -> None:
    """§14.1's own "a G2 that takes three weeks holds without resources": no signal is
    ever sent, so this relies entirely on the time-skipping test server auto-advancing
    virtual time past the real `DEFAULT_GATE_WAIT_TIMEOUT` (30 days) -- a real durable
    timer, not a polling loop, or this test would never return."""
    activities = ScriptedActivities()
    activities.generate_result = GenerateActivityResult(ok=False, exception_case_id="case-4", measure_id=None)
    activities.mend_result = MendActivityResult(outcome="escalated")

    assert DEFAULT_GATE_WAIT_TIMEOUT >= timedelta(days=30)

    final_state = await _run(env, activities, "wb-undecided")

    assert final_state == "ESCALATED"


async def test_current_state_query_reflects_live_progress(env: WorkflowEnvironment) -> None:
    """The AC's own "the console never derives state from anything but the graph" has
    a live-query counterpart too: a caller can ask `current_state` at any point and get
    a real answer without waiting for the workflow to finish. Transitions can happen
    faster than a fixed poll interval samples them (the fakes have no real latency), so
    this only asserts on states a slow-enough poll is guaranteed to still catch: the
    very first live state, and the durable gate wait it lands in and holds at."""
    activities = ScriptedActivities()
    activities.generate_result = GenerateActivityResult(ok=False, exception_case_id="case-5", measure_id=None)
    activities.mend_result = MendActivityResult(outcome="escalated")

    async with Worker(
        env.client, task_queue=TASK_QUEUE, workflows=[MigrationUnitWorkflow],
        activities=[activities.write_mu_state, activities.run_generate, activities.run_mend],
    ):
        handle = await env.client.start_workflow(
            MigrationUnitWorkflow.run, _input("wb-query"),
            id="mu-wb-query", task_queue=TASK_QUEUE,
        )
        first_state = await handle.query(MigrationUnitWorkflow.current_state)

        seen: set[str] = set()
        for _ in range(500):
            seen.add(await handle.query(MigrationUnitWorkflow.current_state))
            if "ESCALATED" in seen:
                break
            await asyncio.sleep(0.01)
        else:
            raise AssertionError("workflow never reached the ESCALATED gate wait")

        await handle.signal(
            MigrationUnitWorkflow.submit_gate_decision,
            GateDecisionSignal(gate="G3", decision="PASSED", principal="human:adjudicator"),
        )
        await handle.result()

    assert first_state == "GENERATED"
    assert "ESCALATED" in seen


class _HangingActivities:
    """A real activity that never returns -- the only honest way to provoke a real
    Temporal-enforced `start_to_close_timeout`, since an activity function raising
    `TimeoutError` itself would surface to the workflow as an `ApplicationError`, not
    the real `temporalio.exceptions.TimeoutError` `_run_activity_or_inconclusive`
    actually looks for."""

    @activity.defn(name="hang")
    async def hang(self, _: None) -> None:
        await asyncio.Event().wait()


@workflow.defn
class _InconclusiveProbeWorkflow:
    """A minimal probe workflow, real Temporal machinery all the way down, exercising
    the exact helper (`_run_activity_or_inconclusive`) the real `MigrationUnitWorkflow`
    calls -- without waiting on the real, hardcoded 5-minute `DEFAULT_ACTIVITY_TIMEOUT`
    this test has no need to actually sit through."""

    @workflow.run
    async def run(self) -> bool:
        result = await _run_activity_or_inconclusive(
            _HangingActivities.hang, None, timeout=timedelta(seconds=1),
        )
        return result is None


async def test_a_genuine_activity_timeout_is_reported_as_inconclusive_not_raised(
    env: WorkflowEnvironment,
) -> None:
    activities = _HangingActivities()
    async with Worker(
        env.client, task_queue=TASK_QUEUE, workflows=[_InconclusiveProbeWorkflow],
        activities=[activities.hang],
    ):
        was_inconclusive = await env.client.execute_workflow(
            _InconclusiveProbeWorkflow.run, id="probe-timeout", task_queue=TASK_QUEUE,
        )

    assert was_inconclusive is True
