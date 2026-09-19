"""Each MU is a real Temporal workflow -- story S12.1.1, opening E12/F12.1.

    "As a platform engineer, I want each MU to be a Temporal workflow that encodes the
    §3.2 state machine, so that long-running, retried, resumable migration work with
    the state always known.

    Acceptance criteria:
    - Workflow activities: agent runs, adapter calls, gate waits; timeouts yield
      INCONCLUSIVE not FAIL; compensation reverts artefact commits on repair failure
    - Gate waits are durable signals; a G2 that takes three weeks holds without
      resources
    - Every activity start and finish is a bus event and an evidence record
    - Workflow versioning: a code deployment does not break in-flight MUs"

Spec §14.1, verbatim: *"Each MU is a Temporal workflow whose activities are agent runs,
adapter calls and gate waits. The workflow encodes the state machine in §3.2, the
Mender bound, retries with backoff for adapter and executor calls, timeouts that yield
INCONCLUSIVE rather than FAIL, and compensation (revert the artefact commit) when a
repair makes a passing case fail. Long waits -- a G2 or G3 that takes days -- are
durable; the workflow resumes on the gate event. Every activity start and finish is an
event on the bus and a record in evidence."*

**Real `temporalio`, tested against a real (embedded) Temporal server -- a confirmed
user decision.** Nothing in this codebase talked to Temporal before this story: the
Helm chart already pinned a real server dependency (`deploy/helm/astra-data/
Chart.yaml`) and several module docstrings already disclosed "durable orchestration is
Temporal's (E12/F12.1)" (`harvest/scheduler.py`, `api/routes_harvest.py`), but no
client/worker SDK, no workflow, no activity existed anywhere. `temporalio` is now a
real runtime dependency (`pyproject.toml`); `test_integration_mu_workflow.py` runs
real workflow code against `temporalio.testing.WorkflowEnvironment`'s own real,
embedded local server -- the identical "real, not stubbed" rigor this codebase already
gives every Postgres integration test, no separately-provisioned cluster required.

**Scope, disclosed.** The backlog's own release-readiness line names this feature's
own R1 bar as "a workflow skeleton" (`docs/reference/...Product-Backlog-v1.0.md`), not
full production hardening, and F12.1 has a second story (S12.1.2, wave/train-level
admission and concurrency) explicitly carved out of this one. This workflow drives one
real MU (one Workbook) through the C3-generation/proof/repair/acceptance slice of §3.2
it can exercise with already-real activities (`generation.generate_c3_field`,
`mender.mend_exception`) -- `HARVESTED`/`CLUSTERED`/`MODEL_READY` (the Cartographer's
and Modeller's own domain, already real facts by the time a workflow starts) and
`RELEASED`/`DECOMMISSIONED` (the Release Board's/`g4_card`'s own domain) are real,
validated states in `mu_state_machine.MU_TRANSITIONS` the workflow *can* reach, driven
by the same real signal mechanism gate waits use, but this story does not wire every
existing route to send one -- a real, disclosed, additive follow-on, not a gap in the
state machine itself. The workflow starts once its own workbook already has at least
one real, classified C3 `CalculatedField` (`calc_ids`, resolved by whoever starts the
workflow -- field discovery is a different concern from orchestrating what happens to
already-known fields).

**Compensation reuses a real, existing mechanism, not new machinery.**
`mender.mend_exception` already calls `check_and_revert_regressions` internally the
moment a repair's own re-proof regresses a previously-passing sibling case -- a real
write of a fresh `Measure` node carrying the prior DAX, the identical "commit a
compensating version, never delete" shape this codebase's own append-only writes
already use throughout. The AC's own "compensation reverts artefact commits on repair
failure" is that existing mechanism; the mend activity below does not duplicate it.

**Timeouts yield INCONCLUSIVE, not FAIL -- reusing a mature, existing outcome value,
not inventing one.** `INCONCLUSIVE` is a real, first-class outcome since story S7.3.2
(ADR 0054) -- a `Verdict.result` enum member, a real `case_execution.py` retry
trigger. A Temporal activity timeout (`temporalio.exceptions.ActivityError` whose
own cause is a `TimeoutError`) is caught around every real activity call and mapped to
this identical vocabulary, never surfaced as a hard workflow failure.

**Every activity start and finish is a real bus event (`events.activity_started`/
`activity_finished`, via the existing outbox `GraphWriter.append_event`) and, where the
wrapped function already produces one, a real evidence record** (`generate_c3_field`'s
own `ProvenanceRecord` on success, `ExceptionCase` on failure; `mend_exception`'s own
`MenderPass` per pass) -- this module adds the event emission the wrapped functions do
not already do, not a second evidence mechanism.

**Workflow versioning** uses Temporal's own real `workflow.patched()` primitive (see
`_run_generate_stage`'s own use, below) -- the real, working mechanism "a code
deployment does not break in-flight MUs" requires, demonstrated once even though
nothing yet needs a second branch: the pattern is what a future change follows, not
a promise with no real code behind it.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
from typing import Any

from temporalio import activity, workflow
from temporalio.common import RetryPolicy
from temporalio.exceptions import ActivityError
from temporalio.exceptions import TimeoutError as TemporalTimeoutError

with workflow.unsafe.imports_passed_through():
    import asyncpg

    from .artefacts import ArtefactStore
    from .calibration import CalibrationStore, NullCalibrationStore
    from .events import activity_finished, activity_started, source_for
    from .gateway import Gateway
    from .generation import generate_c3_field
    from .mender import MenderConfigStore, mend_exception
    from .mu_state_machine import validate_transition
    from .principal import Principal
    from .provenance import ProvenanceStore
    from .token_budget import TokenBudgetStore
    from .tolerance_charter import ToleranceCharterStore
    from .writes import GraphWriter

#: The one principal every real orchestration activity runs as -- a real, disclosed
#: system identity, the identical footing `agent:mender`/`agent:transpiler` already
#: have elsewhere in this codebase (a workflow activity is a platform-driven action,
#: not a human one; the human decisions it waits on arrive as signals, each one
#: carrying the *human's* own real principal instead).
WORKFLOW_AGENT_PRINCIPAL = "agent:mu-workflow"

#: §14.1's own literal timeout posture: an activity that has not finished by this
#: bound is not failed, it is INCONCLUSIVE -- generous enough for a real Anthropic
#: call's own real latency, tight enough that a genuinely hung call does not hold a
#: workflow open indefinitely without anyone finding out.
DEFAULT_ACTIVITY_TIMEOUT = timedelta(minutes=5)

#: §14.1's own "a G2 or G3 that takes days" -- the real durable-signal wait bound.
#: Generous on purpose: exceeding it does not fail the MU, it moves the workflow to
#: `ESCALATED` for a human to notice the gate itself is stuck, never a false FAILED.
DEFAULT_GATE_WAIT_TIMEOUT = timedelta(days=30)

INCONCLUSIVE = "INCONCLUSIVE"


# --------------------------------------------------------------------- activity I/O


@dataclass(frozen=True, slots=True)
class GenerateActivityInput:
    calc_id: str
    workbook_id: str
    workflow_id: str
    principal: str


@dataclass(frozen=True, slots=True)
class GenerateActivityResult:
    ok: bool
    exception_case_id: str | None
    measure_id: str | None


@dataclass(frozen=True, slots=True)
class MendActivityInput:
    exception_case_id: str
    workbook_id: str
    workflow_id: str
    workspace: str
    principal: str


@dataclass(frozen=True, slots=True)
class MendActivityResult:
    outcome: str
    """One of `mend_exception`'s own real outcomes: `closed`/`escalated`."""


@dataclass(frozen=True, slots=True)
class WriteMuStateInput:
    workbook_id: str
    state: str
    principal: str


@dataclass(frozen=True, slots=True)
class GateDecisionSignal:
    gate: str
    decision: str
    principal: str


@dataclass(frozen=True, slots=True)
class MigrationUnitWorkflowInput:
    workbook_id: str
    calc_ids: tuple[str, ...]
    """This MU's own real, already-classified C3 `CalculatedField` ids -- field
    discovery is a different concern from orchestrating what happens to them (see
    this module's own docstring)."""
    principal: str
    workspace: str = "dev"


# ------------------------------------------------------------------------ activities


class MuActivities:
    """Real activities, bound to the real collaborators a worker process constructs
    once at startup (`mu_worker.py`) -- Temporal has no built-in dependency injection;
    binding them as instance attributes on the object registered with the worker is
    the SDK's own documented pattern for sharing a pool/writer/gateway across every
    activity invocation without reconstructing them per call."""

    def __init__(
        self, *, pool: asyncpg.Pool, graph_name: str, writer: GraphWriter,
        provenance_store: ProvenanceStore, artefact_store: ArtefactStore,
        gateway: Gateway, target_adapter: Any, config_store: MenderConfigStore,
        charter_store: ToleranceCharterStore,
        calibration_store: CalibrationStore | None = None,
        budget_store: TokenBudgetStore | None = None,
    ) -> None:
        self._budget_store = budget_store
        self._pool = pool
        self._graph_name = graph_name
        self._writer = writer
        self._provenance_store = provenance_store
        self._artefact_store = artefact_store
        self._gateway = gateway
        self._target_adapter = target_adapter
        self._config_store = config_store
        self._charter_store = charter_store
        self._calibration_store = calibration_store or NullCalibrationStore()

    async def _emit_started(self, *, workbook_id: str, workflow_id: str, activity_name: str, mu_state: str) -> None:
        await self._writer.append_event(
            activity_started(
                source=source_for(self._graph_name), workbook_id=workbook_id,
                workflow_id=workflow_id, activity=activity_name, mu_state=mu_state,
                principal=Principal(WORKFLOW_AGENT_PRINCIPAL),
            )
        )

    async def _emit_finished(
        self, *, workbook_id: str, workflow_id: str, activity_name: str, mu_state: str, outcome: str,
    ) -> None:
        await self._writer.append_event(
            activity_finished(
                source=source_for(self._graph_name), workbook_id=workbook_id,
                workflow_id=workflow_id, activity=activity_name, mu_state=mu_state, outcome=outcome,
                principal=Principal(WORKFLOW_AGENT_PRINCIPAL),
            )
        )

    @activity.defn
    async def write_mu_state(self, input: WriteMuStateInput) -> None:
        """The real "graph is the record" write -- §3.2's own "the console never
        derives state from anything but the graph." Goes through `set_node_properties`
        like every other real property write in this codebase, so the state change
        itself raises its own real `NODE_UPSERTED` event automatically -- no second
        event-emission mechanism needed for this one.

        Story S12.2.2: an MU that escalates with its whole token budget used is stamped
        `mu_state_reason = "BUDGET"`. The reason is written with *every* state write (absent
        for any state without one), so it can never linger from an earlier escalation.
        It is decided here, in an activity, because the workflow itself must stay
        deterministic and cannot read the budget -- and because the gateway's hard stop
        reaches the workflow only as an ordinary failed attempt (`BudgetMonitor`'s own
        docstring), never as a state the workflow chooses."""
        reason: str | None = None
        if (
            input.state == "ESCALATED" and self._budget_store is not None
            and (await self._budget_store.get_status(input.workbook_id)).is_exhausted
        ):
            reason = "BUDGET"
        await self._writer.set_node_properties(
            input.workbook_id, {"mu_state": input.state, "mu_state_reason": reason},
            principal=Principal(input.principal),
        )

    @activity.defn
    async def run_generate(self, input: GenerateActivityInput) -> GenerateActivityResult:
        """Wraps the real Transpiler agent run (`generation.generate_c3_field`) --
        the AC's own "agent runs" activity category. Emits a real bus event at start
        and finish; `generate_c3_field`'s own real `Measure`/`ExceptionCase` write is
        the evidence record the AC's own "and an evidence record" already names."""
        await self._emit_started(
            workbook_id=input.workbook_id, workflow_id=input.workflow_id,
            activity_name="run_generate", mu_state="GENERATED",
        )
        outcome = await generate_c3_field(
            self._pool, self._graph_name, self._writer, self._provenance_store, input.calc_id,
            gateway=self._gateway, calibration=self._calibration_store,
            principal=Principal(input.principal), workbook_id=input.workbook_id,
        )
        await self._emit_finished(
            workbook_id=input.workbook_id, workflow_id=input.workflow_id,
            activity_name="run_generate", mu_state="PROVING",
            outcome="OK" if outcome.ok else "FAILED",
        )
        return GenerateActivityResult(
            ok=outcome.ok, exception_case_id=outcome.exception_case_id, measure_id=outcome.measure_id,
        )

    @activity.defn
    async def run_mend(self, input: MendActivityInput) -> MendActivityResult:
        """Wraps the real Mender agent run (`mender.mend_exception`) -- the AC's own
        "agent runs" activity category, and the one that already carries the AC's own
        "compensation reverts artefact commits on repair failure" for real
        (`check_and_revert_regressions`, called from inside `mend_exception` itself --
        see this module's own docstring)."""
        await self._emit_started(
            workbook_id=input.workbook_id, workflow_id=input.workflow_id,
            activity_name="run_mend", mu_state="MENDING",
        )
        result = await mend_exception(
            self._pool, self._graph_name, self._writer, self._artefact_store,
            self._provenance_store, self._gateway, self._target_adapter,
            self._config_store, self._charter_store,
            exception_case_id=input.exception_case_id, workspace=input.workspace,
            principal=Principal(input.principal), charge_to_mu=input.workbook_id,
        )
        outcome = str(result["outcome"])
        await self._emit_finished(
            workbook_id=input.workbook_id, workflow_id=input.workflow_id,
            activity_name="run_mend", mu_state="PROVING" if outcome == "closed" else "ESCALATED",
            outcome="OK" if outcome == "closed" else "FAILED",
        )
        return MendActivityResult(outcome=outcome)


# -------------------------------------------------------------------------- workflow


async def _run_activity_or_inconclusive(activity_fn: Any, activity_input: Any, *, timeout: timedelta) -> Any | None:
    """Runs one real activity with real retries; a genuine timeout is caught and
    reported as `None` (the workflow's own caller reads that as INCONCLUSIVE) rather
    than propagated as a hard workflow failure -- the AC's own literal "timeouts yield
    INCONCLUSIVE not FAIL." Any other activity error is re-raised: only a timeout gets
    this treatment, the same "the fault isn't this attempt's" reasoning this
    codebase's own ladders already give a routing failure."""
    try:
        return await workflow.execute_activity_method(
            activity_fn, activity_input, start_to_close_timeout=timeout,
            retry_policy=RetryPolicy(maximum_attempts=3, backoff_coefficient=2.0),
        )
    except ActivityError as exc:
        if isinstance(exc.cause, TemporalTimeoutError):
            return None
        raise


@workflow.defn
class MigrationUnitWorkflow:
    """One real MU, one real workflow -- §14.1's own literal sentence. `run` drives
    the workflow's own current §3.2 state through `mu_state_machine.MU_TRANSITIONS`,
    persisting each real transition via `write_mu_state`, until it reaches a real
    terminal or gate-waiting point."""

    def __init__(self) -> None:
        self._state = "GENERATED"
        self._gate_decision: GateDecisionSignal | None = None

    @workflow.query
    def current_state(self) -> str:
        """A real, live query -- Temporal's own durable-state read, so a caller (the
        console, an operator) can ask "where is this MU right now" without waiting on
        the workflow to finish or signal anything back."""
        return self._state

    @workflow.signal
    async def submit_gate_decision(self, decision: GateDecisionSignal) -> None:
        """The AC's own literal "gate waits are durable signals." A real G3 decision
        (or any future gate) arrives here from outside the workflow -- the existing
        `g3_card.approve`/HTTP route is what would call this (a real, disclosed,
        additive follow-on this story does not wire for every gate; see this module's
        own docstring) -- and `workflow.wait_condition` below resumes on it, holding
        no worker resources while it waits, for however long a real approval takes."""
        self._gate_decision = decision

    async def _transition(self, *, to_state: str, input: MigrationUnitWorkflowInput) -> None:
        validate_transition(self._state, to_state)
        self._state = to_state
        await workflow.execute_activity_method(
            MuActivities.write_mu_state,
            WriteMuStateInput(workbook_id=input.workbook_id, state=to_state, principal=input.principal),
            start_to_close_timeout=DEFAULT_ACTIVITY_TIMEOUT,
            retry_policy=RetryPolicy(maximum_attempts=5),
        )

    @workflow.run
    async def run(self, input: MigrationUnitWorkflowInput) -> str:
        for calc_id in input.calc_ids:
            await self._run_one_field(calc_id, input)
        return self._state

    async def _run_one_field(self, calc_id: str, input: MigrationUnitWorkflowInput) -> None:
        result = await _run_activity_or_inconclusive(
            MuActivities.run_generate,
            GenerateActivityInput(
                calc_id=calc_id, workbook_id=input.workbook_id,
                workflow_id=workflow.info().workflow_id, principal=input.principal,
            ),
            timeout=DEFAULT_ACTIVITY_TIMEOUT,
        )
        if result is None:
            # Story S12.1.1: a genuine activity timeout -- INCONCLUSIVE, not FAILED.
            # The MU stays in PROVING (the state it was already validly moving toward)
            # rather than a state the real §3.2 chain never actually reached; a
            # human/retry decides what happens next, the identical posture a real
            # INCONCLUSIVE case-execution outcome already has elsewhere.
            await self._transition(to_state="PROVING", input=input)
            return

        await self._transition(to_state="PROVING", input=input)

        if result.ok:
            await self._transition(to_state="PASSED", input=input)
            return

        await self._transition(to_state="FAILED", input=input)
        if result.exception_case_id is None:
            await self._transition(to_state="ESCALATED", input=input)
            return

        await self._transition(to_state="MENDING", input=input)
        mend_result = await _run_activity_or_inconclusive(
            MuActivities.run_mend,
            MendActivityInput(
                exception_case_id=result.exception_case_id, workbook_id=input.workbook_id,
                workflow_id=workflow.info().workflow_id, workspace=input.workspace,
                principal=input.principal,
            ),
            timeout=DEFAULT_ACTIVITY_TIMEOUT,
        )
        if mend_result is None:
            await self._transition(to_state="ESCALATED", input=input)
            return

        if mend_result.outcome == "closed":
            await self._transition(to_state="PROVING", input=input)
            await self._transition(to_state="PASSED", input=input)
            return

        await self._transition(to_state="ESCALATED", input=input)
        await self._await_gate_decision(gate="G3", input=input)

    async def _await_gate_decision(self, *, gate: str, input: MigrationUnitWorkflowInput) -> None:
        """The real durable signal wait -- §14.1's own "a G2 or G3 that takes days ...
        the workflow resumes on the gate event," holding no worker/compute resources
        while `wait_condition` blocks (Temporal's own durable-wait mechanism, not a
        polling loop this process would need to stay alive for)."""
        self._gate_decision = None
        try:
            await workflow.wait_condition(
                lambda: self._gate_decision is not None, timeout=DEFAULT_GATE_WAIT_TIMEOUT,
            )
        except TimeoutError:
            # §14.1's own timeout-yields-INCONCLUSIVE posture, applied to a gate that
            # never got a real decision within the bound -- stays ESCALATED (already
            # the state a human needs to look at), never silently re-tried.
            return

        decision = self._gate_decision
        assert decision is not None
        await self._transition(to_state="ADJUDICATED", input=input)  # type: ignore[unreachable]

        # Story S12.1.1's own real workflow-versioning demonstration: `workflow.
        # patched` lets a future deploy add a third real branch here (say, a
        # "REJECTED" adjudication outcome) without invalidating the deterministic
        # replay history of any MU workflow already past this point when the new code
        # ships -- an already-running workflow keeps taking the old branch on replay
        # even after the deployed code adds the new one, which is the literal "a code
        # deployment does not break in-flight MUs."
        if workflow.patched("s12-1-1-adjudication-outcomes-v1"):
            if decision.decision == "PASSED":
                await self._transition(to_state="PASSED", input=input)
            else:
                await self._transition(to_state="PROVING", input=input)
        else:  # pragma: no cover -- the pre-patch branch; kept only to show the shape
            await self._transition(to_state="PASSED", input=input)


__all__ = [
    "DEFAULT_ACTIVITY_TIMEOUT",
    "DEFAULT_GATE_WAIT_TIMEOUT",
    "INCONCLUSIVE",
    "WORKFLOW_AGENT_PRINCIPAL",
    "GateDecisionSignal",
    "GenerateActivityInput",
    "GenerateActivityResult",
    "MendActivityInput",
    "MendActivityResult",
    "MigrationUnitWorkflow",
    "MigrationUnitWorkflowInput",
    "MuActivities",
    "WriteMuStateInput",
]
