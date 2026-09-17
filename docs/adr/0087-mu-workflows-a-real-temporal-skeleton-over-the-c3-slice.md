# ADR 0087 — MU workflows: a real Temporal skeleton over the C3 slice

Status: accepted · 17 September 2026 · Story S12.1.1, opens F12.1 (E12)

## Context

S12.1.1 — the story's own text, verbatim: *"As a platform engineer, I want each MU to
be a Temporal workflow that encodes the §3.2 state machine, so that long-running,
retried, resumable migration work with the state always known."*

- Workflow activities: agent runs, adapter calls, gate waits; timeouts yield
  INCONCLUSIVE not FAIL; compensation reverts artefact commits on repair failure
- Gate waits are durable signals; a G2 that takes three weeks holds without resources
- Every activity start and finish is a bus event and an evidence record
- Workflow versioning: a code deployment does not break in-flight MUs

Spec §14.1, verbatim: *"Each MU is a Temporal workflow whose activities are agent runs,
adapter calls and gate waits. The workflow encodes the state machine in §3.2, the
Mender bound, retries with backoff for adapter and executor calls, timeouts that yield
INCONCLUSIVE rather than FAIL, and compensation (revert the artefact commit) when a
repair makes a passing case fail. Long waits — a G2 or G3 that takes days — are
durable; the workflow resumes on the gate event. Every activity start and finish is an
event on the bus and a record in evidence."*

E12's own goal line names this "the engine everything runs on" — the first story of a
new platform layer, not an addition to an existing one. Research (a full pass over
`services/graph-svc`, the Helm chart, and every module already disclosing this gap)
found: the Helm chart already pinned a real Temporal server dependency
(`deploy/helm/astra-data/Chart.yaml`, chart 1.7.0, wired to this project's own
PostgreSQL for persistence/visibility) and several module docstrings already disclosed
"durable orchestration is Temporal's (E12/F12.1)" (`harvest/scheduler.py`,
`api/routes_harvest.py`, `main.py`) — but **nothing in `graph-svc`'s own Python code
talked to Temporal before this story**: `temporalio` was not a dependency, no
workflow or activity existed, `docker-compose.yml` had no Temporal service.
`migration_units.py`'s own `MU_STATES` tuple already named §3.2's real 15-state
vocabulary faithfully (its own docstring: "a port, not implementation") but no real
transition table or backing enforcement existed. `INCONCLUSIVE` was already a mature,
first-class outcome (story S7.3.2, ADR 0054); `mender.mend_exception` already called
`check_and_revert_regressions` internally the moment a repair regressed a
previously-passing sibling case — compensation, for real, already existed.

Given this, four genuinely blocking design questions were put to the user before
writing any code (each answered "(recommended)"):

1. **Test strategy for real Temporal workflow code?** → *Real `temporalio` SDK, tested
   against Temporal's own embedded test server* (`temporalio.testing.
   WorkflowEnvironment`) — not stubbed or deferred. Confirmed directly: `start_time_
   skipping()` starts a real, embedded local Temporal server successfully in this
   sandbox in under 60 seconds, no separately-provisioned cluster required.
2. **Where does §3.2 MU state live?** → *A real, queryable `mu_state` property on the
   `Workbook` node* — the graph stays the one real record, matching every other
   "console never derives state from anything but the graph" decision already made in
   this codebase.
3. **What does "every activity start and finish is a bus event" mean without a real
   Kafka/Event Hubs publisher anywhere in this codebase?** → *Real writes now, via the
   existing transactional outbox* (`GraphWriter.append_event`) — durable and real
   today; the actual Kafka-protocol publish step is a separate, disclosed, later gap
   this local environment has no real message bus to test against regardless.
4. **Add a real Temporal service to `docker-compose.yml` now, for a live local demo?**
   → *Yes* — `temporalio/auto-setup`, wired to the same shared `postgres` service the
   Helm chart's own `temporal.server.config.persistence.datastores` already names for
   the real AKS deployment.

## Decisions

1. **A new, pure, Temporal-free module owns the state machine: `mu_state_machine.py`.**
   `MU_TRANSITIONS: dict[str, frozenset[str]]` names every real §3.2 edge (`HARVESTED →
   CLUSTERED → {MODEL_READY, BLOCKED} → MODEL_READY → GENERATED → PROVING → {PASSED,
   FAILED} → FAILED → {MENDING, ESCALATED} → MENDING → {PROVING, ESCALATED} →
   ESCALATED → ADJUDICATED → {PROVING, PASSED (waiver)} → PASSED → ACCEPTED → RELEASED
   → DECOMMISSIONED`), plus `WITHDRAWN` — reachable from any non-terminal state via
   out-of-band Programme Manager change control, not named in any other row's own
   "exits to" list. A module-level assertion (`set(MU_TRANSITIONS) == set(MU_STATES)`)
   keeps this table honest against `migration_units.py`'s own real state vocabulary
   forever, not just at the moment this story wrote it. `validate_transition` is the
   one real enforcement point both the workflow and any future caller share.

2. **`MigrationUnitWorkflow` (`mu_workflow.py`) drives one real MU (one Workbook)
   through the C3-generation/proof/repair/acceptance slice of §3.2 — a disclosed,
   deliberate scope boundary, not a gap.** The backlog's own release-readiness line
   names this feature's own R1 bar as "a workflow skeleton," and F12.1 carves out a
   second story (S12.1.2, wave/train-level admission and concurrency) explicitly. The
   workflow reuses already-real activities (`generation.generate_c3_field`,
   `mender.mend_exception`) unmodified; `HARVESTED`/`CLUSTERED`/`MODEL_READY` (the
   Cartographer's/Modeller's own domain, already real facts by the time a workflow
   starts) and `RELEASED`/`DECOMMISSIONED` (the Release Board's/`g4_card`'s own domain)
   are real, validated states in `MU_TRANSITIONS` the workflow *can* reach via the same
   real signal mechanism gate waits use — this story does not wire every existing
   route (harvest completion, G4 approval) to send one, a real, disclosed, additive
   follow-on. The workflow starts once its own workbook already has at least one real,
   classified C3 `CalculatedField` (`calc_ids`, resolved by whoever starts the workflow
   — field discovery is a different concern from orchestrating what happens to
   already-known fields).

3. **Activities are bound methods on `MuActivities`, an instance constructed once by
   the real worker process (`mu_worker.py`) at startup, not passed as workflow
   arguments.** Temporal activities cannot receive heavy dependency-injected objects
   (a connection pool, a writer, a gateway) directly as workflow-serializable
   arguments; binding real collaborators as instance attributes and registering the
   bound methods (`activities.write_mu_state`, `.run_generate`, `.run_mend`) with the
   `Worker` is the SDK's own documented pattern for sharing them across every
   invocation without reconstructing them per call. `write_mu_state` writes through
   `GraphWriter.set_node_properties` like every other real property write in this
   codebase, so the state change itself raises its own real `NODE_UPSERTED` event with
   no second event-emission mechanism needed for that one. `run_generate`/`run_mend`
   each emit a real `events.activity_started`/`activity_finished` pair around the real
   wrapped call — the AC's own "every activity start and finish is a bus event," via
   the existing outbox, not new infrastructure — and the wrapped function's own real
   evidence write (`Measure`/`ExceptionCase`, `MenderPass`) is the "and an evidence
   record" half, not a second evidence mechanism.

4. **Timeouts yield INCONCLUSIVE, not FAIL, by catching a real Temporal activity
   timeout and mapping it, never by inventing a new outcome.** `_run_activity_or_
   inconclusive` calls `workflow.execute_activity_method` with real retries
   (`RetryPolicy(maximum_attempts=3, backoff_coefficient=2.0)`) and catches
   `ActivityError`; only when its `cause` is a real `temporalio.exceptions.
   TimeoutError` does it return `None` (read by the caller as INCONCLUSIVE, reusing
   the mature S7.3.2/ADR-0054 outcome) — any other activity error is re-raised as a
   genuine hard failure. On a `None` from `run_generate`, the MU stays in `PROVING`
   (the state it was already validly moving toward) rather than a state the real §3.2
   chain never actually reached; a human or retry decides what happens next.

5. **Compensation reuses `mend_exception`'s own existing `check_and_revert_
   regressions` call — no new compensation machinery was built.** The AC's own
   "compensation reverts artefact commits on repair failure" was already true of
   `mend_exception` before this story (a real write of a fresh `Measure` node carrying
   the prior DAX, append-only, the identical "commit a compensating version, never
   delete" shape this codebase's own writes already use throughout); `run_mend` simply
   wraps that existing, already-tested mechanism.

6. **Gate waits are a real Temporal signal (`submit_gate_decision`) and `workflow.
   wait_condition`, not polling.** `_await_gate_decision` blocks durably — holding no
   worker/compute resources — for up to `DEFAULT_GATE_WAIT_TIMEOUT` (30 days, §14.1's
   own "a G2 or G3 that takes days"); a decision resumes it via the signal, a genuine
   timeout leaves the MU at `ESCALATED` (already the state a human needs to look at)
   rather than silently retried. Workflow ids are deterministic and human-readable
   (`f"mu-{workbook_id}"`), so any HTTP route computes the workflow handle directly
   from `workbook_id` with no separate lookup table.

7. **Workflow versioning uses Temporal's own real `workflow.patched()` primitive,
   demonstrated once even though nothing yet needs a second branch.** `_await_gate_
   decision` gates its adjudication-outcome branch behind `workflow.patched("s12-1-1-
   adjudication-outcomes-v1")` — the real, working mechanism the AC's own "a code
   deployment does not break in-flight MUs" requires: a future deploy can add a third
   real branch here without invalidating the deterministic replay history of any MU
   workflow already running past this point, which is the literal claim, demonstrated
   with real working code rather than promised with none.

8. **A new HTTP surface, `routes_mu_workflow.py`, starts a workflow, signals a gate
   decision, and reads live status — gated the identical way every other real,
   consequential route in this codebase already is.** Starting a workflow and
   submitting a gate decision require the platform engineer role
   (`PlatformEngineerDep` — this story's own literal persona, the same "gated narrower
   than the read" posture `routes_gateway.py`'s own eval-trigger routes already take);
   reading a workflow's live status (`GET /v1/mu/{workbook_id}/workflow`, backed by a
   real `workflow.query`) is open to any Artizent role, matching every other
   read-only Programme-Board-adjacent route. `app.state.temporal_client` connects at
   startup with a 5-second timeout and is `None`, disclosed and non-fatal, if
   unreachable — the identical posture this codebase already gives every other
   optional real integration (Azure Key Vault, Entra ID) it cannot assume is
   configured; the routes themselves refuse cleanly (`InvalidRequestError`) rather
   than the service failing to start.

9. **`Workbook.mu_state` is a new, optional ontology property — a real, tracked
   ontology change (`SCHEMA_VERSION` 37→38) but not a breaking one, and no SQL
   migration file.** Confirmed directly: `ontology/lock.py`'s own `diff()` classifies
   a new optional property as `add_property`, `breaking=False`; only a *required* new
   property or a *removed* enum value is ever flagged as breaking, and Apache AGE has
   no fixed per-label DB schema (properties are flexible `agtype`), so there is no DDL
   to run regardless. This differs from — and sits alongside — the already-established
   pattern that a new *enum value* on an *existing* property needs neither a version
   bump nor a migration at all.

## Consequences

- `services/graph-svc`: new `mu_state_machine.py` (`MU_TRANSITIONS`, `TERMINAL_STATES`,
  `validate_transition`, `InvalidTransitionError`, `is_terminal`); new `mu_workflow.py`
  (`MigrationUnitWorkflow`, `MuActivities`, the activity I/O dataclasses,
  `_run_activity_or_inconclusive`); new `mu_worker.py` (the real worker process
  entry point, `TASK_QUEUE = "mu-workflow"`); new `api/routes_mu_workflow.py`
  (`:start-workflow`, `:submit-gate-decision`, `GET .../workflow`). `events.py` gained
  `EventType.ACTIVITY_STARTED`/`ACTIVITY_FINISHED` and their constructor functions,
  both excluded from `mutates_graph`. `config.py` gained `temporal_address`/
  `temporal_namespace` (`ASTRA_TEMPORAL_ADDRESS`/`ASTRA_TEMPORAL_NAMESPACE`, defaulting
  to `localhost:7233`/`default`). `main.py`'s lifespan connects a real Temporal client,
  disclosed non-fatal if unreachable. `ontology/nodes.py`: `Workbook.mu_state`, a new
  `SpecDeviation`. `ontology/registry.py`: `SCHEMA_VERSION` 37→38. `pyproject.toml`:
  `temporalio>=1.7,<2` (installed: `temporalio` 1.33.0). `docker-compose.yml`: new
  `temporal` (`temporalio/auto-setup:1.24.2`, sharing the existing `postgres` service —
  auto-creates the `temporal`/`temporal_visibility` databases and the `default`
  namespace on first start, the same namespace the Helm chart's own values.yaml
  already names for the real AKS deployment), `temporal-ui`
  (`temporalio/ui:2.31.3`, port 8088), and `graph-svc-worker` (the same image, running
  `python -m astra_graph.mu_worker`) services; `graph-svc` gained `ASTRA_TEMPORAL_
  ADDRESS` and a `depends_on: temporal` (`service_started`, not `service_healthy` —
  `auto-setup` ships no built-in healthcheck this file adds one for, the identical
  disclosed "not fatal if briefly unready" posture `main.py`'s own lifespan already
  gives this exact dependency).
- **A real bug I introduced and caught myself**, not by review: `MuActivities.
  run_mend` initially hardcoded a placeholder target adapter instead of using the
  injected `self._target_adapter` — caught reviewing my own draft, fixed by wiring
  `target_adapter` through `__init__` properly.
- **Two real bugs mypy caught**, project-wide, not story-specific: `workflow.
  execute_activity` cannot call an *instance-method* activity — the correct API is
  `workflow.execute_activity_method`, used at both call sites (`_run_activity_or_
  inconclusive`, `MigrationUnitWorkflow._transition`). And a pre-existing, unrelated
  leftover from the already-committed S11.4.3 story: `gateway.py` had kept an unused
  `dataclasses.replace` import after that story's own `_dispatch` rework removed the
  call it supported — found by this story's own full-project `ruff check .` (not just
  the files this story touched) and fixed in passing.
- Verified: `services/graph-svc` — 14 new pure unit tests (`test_mu_state_machine.py`,
  the full transition table, every real edge, `WITHDRAWN`-from-anywhere-non-terminal,
  terminal-state and unrecognised-state refusal). 8 new workflow-level unit tests
  (`test_mu_workflow.py`) against a real embedded Temporal test server
  (`WorkflowEnvironment.start_time_skipping()`) with scripted fake activities standing
  in for `MuActivities` — the clean-generation-to-PASSED path, direct escalation with
  no exception case, a mend that closes a case, a mend that escalates then honours a
  real gate-decision signal, a waived adjudication returning to PROVING, a gate left
  undecided holding at ESCALATED for the real 30-day durable timer (proving "holds
  without resources," not merely asserting it), the live `current_state` query, and a
  genuine Temporal-enforced activity timeout (a real hanging activity against a short
  injected timeout, not a fabricated exception) correctly reported as INCONCLUSIVE, not
  raised. No `pytest.mark.integration` marker on that file — it needs no PostgreSQL,
  only the embedded Temporal test server, so it rides the default `not integration`
  suite. 3 new integration tests (`test_integration_mu_workflow.py`) against real
  PostgreSQL + Apache AGE and a real embedded Temporal server: a real workflow run
  through real `MuActivities` (a scripted-but-real `StaticGateway` call) really writes
  `Workbook.mu_state = "PASSED"` onto the real graph node and really emits real
  `ACTIVITY_STARTED`/`ACTIVITY_FINISHED` rows into the real `estate_event` outbox; a
  real missing-calc failure really drives the real workflow to `ESCALATED` end to end;
  `MuActivities.run_mend`, called directly (a plain instance method — `activity.defn`
  only tags metadata, no live Temporal execution context is needed for a method that
  never calls `activity.info()`/`heartbeat()`), really closes a real, properly-seeded
  parity-failure case via a real `ACTIVE` `Pattern`, the identical recipe
  `test_integration_mender.py` already proves `mend_exception` with directly. The full
  `services/graph-svc` suite re-ran clean; `ruff`/`mypy`/`ontology_check.py`/
  `migration_check.py` all clean (`add_property:node:Workbook.mu_state`, additive, no
  migration required). `services/console-web`: no change — this story ships no console
  surface for MU workflow status; a future story is where that would land.
- **A real, disclosed scope gap, found by this story's own research, not fixed by
  it**: `generate_c3_field`'s own `ExceptionCase` (a *pre-proof* generation failure)
  carries no real `case_refs` and a synthetic `mu_ref` (`calc:{calc_id}`) — a real
  mismatch with `mend_exception`'s own *post-proof* parity-failure `ExceptionCase`
  shape it otherwise expects. The real generate-then-mend chain inside
  `MigrationUnitWorkflow` is therefore only exercised end to end, in this story's own
  integration suite, for the one real failure shape that never reaches `run_mend` at
  all (a missing/non-C3 calc, `exception_case_id is None`, straight to `ESCALATED`);
  `run_mend`'s own real activity wrapper is proven separately, called directly against
  a properly-seeded parity-failure case. Closing this mismatch — so a real generation
  failure's `ExceptionCase` can flow into a real mend pass with real case evidence —
  is real, disclosed, follow-on work this story does not attempt.

## Alternatives considered

**Pass the pool/writer/gateway/etc. as workflow input, reconstructed fresh per
activity call.** Rejected — Temporal activity arguments must be serializable, and a
connection pool is not; the SDK's own documented instance-method-activity pattern
(bind real collaborators once, register the bound methods) is the correct mechanism,
not a workaround.

**Send a placeholder-bearing request to the real provider on a classifier hit
(S11.4.3's own alternative), reused by analogy for a workflow-level "send a redacted
version" design for a mend/generate retry.** Not actually considered for this story —
noted here only because the identical "skip the call entirely vs. send something
weaker" shape came up again and the same conservative answer (skip, never send a
degraded real call) already won that argument in ADR 0086 and generalises cleanly.

**Poll `ExceptionCase`/`Verdict` state from the workflow instead of a real Temporal
signal for gate waits.** Rejected — the AC's own literal "gate waits are durable
signals" and "holds without resources"; a polling loop would need to stay alive
(consuming worker/compute resources) for the entire wait, exactly what a durable
signal avoids.

**Build a second, workflow-specific bus-event mechanism instead of the existing
outbox.** Rejected by the user's own explicit choice — the real, existing
transactional outbox (`GraphWriter.append_event`) is durable today; a second
mechanism would duplicate it for no real benefit while the actual Kafka-protocol
publish step (E12's own, disclosed, separate gap) remains equally absent either way.

**Wire every existing route (harvest completion, G3/G4 approval) to start or signal a
real MU workflow now, matching the state machine's full real reach.** Rejected as
exceeding this story's own disclosed R1 "workflow skeleton" bar; F12.1's own second
story (S12.1.2) and this story's own module docstring both name this as real,
additive, follow-on work rather than a gap in the state machine itself.

**Fix the pre-proof/post-proof `ExceptionCase` mismatch (`case_refs`/`mu_ref`) as part
of this story, so the real generate-then-mend chain fully composes end to end.**
Rejected — a genuine, real gap in `generation.py`/`mender.py`'s own existing contract
predating this story, not something S12.1.1's own AC asks for; fixing it here would
silently expand this story's scope into changing two already-shipped, already-tested
modules' own behavior. Disclosed instead, for a real, separate follow-on story.
