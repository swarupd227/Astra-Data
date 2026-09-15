# ADR 0081 — Execution safety: real enforcement around a fixture executor

Status: accepted · 15 September 2026 · Story S11.2.1, opens F11.2 (E11)

## Context

S11.2.1 — the backlog's own AC, verbatim: *"As an InfoSec reviewer, I want generated
DAX, M and adapter queries to execute only in sandboxed, read-only contexts, so that a
generated artefact cannot change data or reach anything it should not."*

- Target execution uses XMLA read-only against dev/test workspaces with a service
  principal that has no write on data; production execution is limited to the
  regression runner with the same read-only principal
- Source execution uses the adapter's read-only credential; custom SQL replay is
  disabled unless the tenant policy enables it and then runs with a statement
  allow-list (SELECT only)
- Executor workers run with no outbound network except the two data endpoints;
  resource limits per query

Before any code was written, research (an `Explore` agent's own full pass over
`case_execution.py`, `target_contract.py`/`target_fake.py`, `ports.py`/`execution.py`,
`credentials.py`, `deploy/terraform`, and the product spec) found the AC's own three
bullets each rest on infrastructure this codebase does not have yet: target/XMLA
execution is 100% fixture (`FixtureTargetAdapter`, no adapter-fabric package exists
anywhere); live SQL replay is an existing, disclosed, permanently-unavailable stub
(`NoLiveQueryRunner`, no database driver for any source warehouse); and no "executor
worker" exists as its own deployable — execution runs inline inside graph-svc's own
process, and the spec's own `adapter-fabric`/`agent-runtime` worker units (§5.2) are
declared but never built. Building any of these three for real would each be a much
larger, separate undertaking than this one safety story. Three explicit questions were
asked and answered before writing any code, all "(recommended)":

1. **Executor sandbox scope** → *"Harden the existing process, don't build a new
   worker."* Real, disclosed egress hardening and a real per-query resource limit on the
   process that executes today, not a new Kubernetes Deployment.
2. **XMLA execution scope** → *"Real read-only scope + regression-only gating, no live
   XMLA client."* A real, enforced credential/workspace boundary around the existing
   fixture-based `TargetAdapter`, disclosed as not connected to a live Fabric tenant.
3. **SQL replay policy scope** → *"Real policy gate + allow-list, live replay stays a
   stub."* A real, tested tenant-policy gate and SELECT-only validator wired into the
   existing (still-unavailable) live-replay call path, ready for whenever a real
   `LiveQueryRunner` lands.

## Decisions

### 1. "XMLA read-only... a service principal that has no write on data" is already structurally true

`TargetAdapter.evaluate` (`target_contract.py`) takes an already-built query string and
returns a `ResultSet` — no write-shaped parameter exists on that method at all, confirmed
by reading the full Protocol. `commit`/`deploy` are the Protocol's only write-shaped
methods, and they are a different, already-accepted write path (publishing a model,
gated to the Steward, spec §19's own "acting integrations run only through the Steward
and the target adapter") — not something this story's own AC is about. The real,
buildable contribution is the credential *reservation*: `deploy/terraform/identity.tf`
now reserves a **second, distinct** Key Vault secret slot, `fabric-execution-sp`,
separate from the pre-existing `fabric-workspace-sp` (which legitimately needs write
access to publish a model). This module's own header comment already establishes that
registering a real Fabric app and assigning its workspace role is the client's own
Fabric-admin action, not something Terraform can reach into — `fabric-execution-sp`
follows the identical, already-established pattern, disclosed in the module's own
comment and this directory's own README to be granted Fabric's "Viewer" role only, never
Contributor/Member/Admin.

### 2. "Production execution is limited to the regression runner" is the one real, structural gap — and the one this story actually closes

Confirmed by direct research: `workspace` on `POST /v1/workbooks/{id}:execute-parity-cases`
is an unrestricted free string, and the on-demand route and the scheduled regression
check share the exact same `CaseExecutionService`/`target_adapter` instance (`main.py`
constructs `app.state.case_execution` once, passed to both `case_execution_router` and
`RegressionScheduler`). Nothing today stops a Parity Engineer's own on-demand call from
naming a production workspace.

**New `execution_safety.py`**: `ExecutionSafetyPolicy` (`production_workspaces:
frozenset[str]`, `version: int`) is a real, `graph`-scoped, versioned Postgres record —
the identical "a platform engineer's edit is a new version, never an overwrite" shape
`mender_config`/`tolerance_charter_version` already established (migration v0041,
`public.execution_safety_policy`, no ontology change: which workspaces a tenant calls
production is bookkeeping about this deployment, not a fact about the estate).
`authorize_target_workspace(principal_value, *, workspace, policy)` refuses any
principal other than `REGRESSION_RUNNER_PRINCIPAL` (`"agent:steward"`) against a
workspace the policy names — a **literal identity check, not an `AgentScope` gate**:
`agent_identity.py`'s own module docstring is explicit that its enforcement "never
scopes a `user:`/`service:` principal," but this AC bullet has to refuse a *human* Parity
Engineer too, so it does not belong there. `REGRESSION_RUNNER_PRINCIPAL` is duplicated
(not imported from `regression.py`) for the identical "written again rather than
imported since this is a different epic's own module" reason `case_execution.py`'s own
`_maps_to` already set for `compositor._maps_to` — `regression.py` imports
`case_execution.py`, so the reverse import would be circular.

Wired into `CaseExecutionService.execute()` as the very first check, before any case is
even read — an optional `safety_policy_store` parameter, defaulting to `None` (every
caller unaffected until a store is configured, the identical posture `ExecutionCharter`
already has); when configured but nothing has been saved yet, `ExecutionSafetyPolicy()`'s
own empty-set default means every workspace stays exactly as open as it already was. A
new pair of routes (`GET`/`PUT /v1/execution-safety/policy`, `routes_execution_safety.py`)
lets a platform engineer declare the list and lets an InfoSec reviewer see it — read
gated `TenantAccessReaderDep` (Artizent or the InfoSec reviewer, this policy is exactly
the kind of governance fact that reader dependency's own remit already covers), edit
gated `PlatformEngineerDep`, matching S11.1.2's own SVID-revoke precedent for a
platform-safety action with no other named approver. Surfaced on the console's own
Tenant & Access screen (a new "Execution safety" pane, not a new top-level surface —
this is exactly the kind of governance fact that screen already exists to show).

### 3. Resource limits: two real, independent mechanisms, one per side

**Target/XMLA side**: `ExecutionCharter` (adapter-sdk) gains `max_rows: int =
DEFAULT_MAX_ROWS` (100,000, an invented, disclosed bound — the identical footing
`DEFAULT_RETRY_TIMEOUT_MULTIPLIER` already has for its own unspecified knob).
`case_execution_query.build_dax_query` wraps its body in a real DAX `TOPN` when
`max_rows` is given — the limit is embedded in the query text itself, sent to XMLA,
rather than discarding rows after a target engine already spent resources producing
them; `ORDER BY` stays a top-level `EVALUATE` clause outside the wrap, since a display
sort does not need `TOPN`'s own tie-break ordering. Verified directly: the real query
text case_execution.py sends now genuinely contains `TOPN(...)` with balanced brackets.

**Source/live-replay side**: `packages/adapter-tableau`'s own new `LiveReplayPolicy.
max_rows`, enforced by truncating the returned rows and marking `truncated=True` after
the (still-stubbed) runner returns — the identical "truncate and flag" shape
`_read_csv`'s own existing `case.row_limit` handling already has for `VIEW_DATA`. Two
mechanisms, not one, because there is no shared per-call charter channel between
graph-svc and the out-of-process source adapter (confirmed: `SourceAdapter.execute_case`
takes no charter at all — the adapter decides its own strategy from its own internally
held configuration, reached only over the §6.1 RPC).

### 4. Custom SQL replay: a real, tested policy gate around a mechanism that stays a stub

`ports.py`'s own module docstring already states plainly that live replay "needs a
database driver per connection class and network access to the client's warehouse under
their service account... Both arrive with E11" — this is genuinely this epic's own,
already-scoped work, not a gap this story invented. **New `live_replay_policy.py`**:
`LiveReplayPolicy` (`enabled: bool = False`, `max_rows: int`) is this worker's own
tenant policy, read from its own environment (`TableauConfig.live_replay_enabled`/
`.live_replay_max_rows`, new `ASTRA_TABLEAU_LIVE_REPLAY_ENABLED`/`_MAX_ROWS` env vars) —
**never from graph-svc's Postgres**, since this adapter is its own separate deployable
(§5.2, one worker per site) reached only over the RPC, and "the platform names a
credential, it does not send one" (`config.py`'s own module docstring) applies to policy
the identical way. `validate_select_only(sql)` is a real, `sqlglot`-parsed allow-list
(already a dependency, used by `sql.py` for lineage): refuses anything but exactly one
`Select` statement — a stacked statement (`SELECT 1; DROP TABLE x`) is refused by the
same rule that refuses a bare `DROP TABLE x`, and a CTE (`WITH ... SELECT ...`) is
permitted without a special case, since sqlglot parses it to a top-level `Select` whose
`with` clause is one of its own arguments.

`PolicyGatedLiveQueryRunner` wraps whatever `LiveQueryRunner` a deployment is given,
**including the default `NoLiveQueryRunner`** — so the enforcement point already exists
on `TableauAdapter`'s own construction path today, and a future story landing a real
runner changes nothing here. `available` folds the tenant-policy flag directly into the
existing capability-gate mechanism (`ports.describe`, `TableauExecutor.strategies`)
rather than adding a second gate those real, already-exercised call sites would need to
learn about — when policy disables replay, `available` is `False` the identical way an
absent driver already makes it `False`, and `execution.py`/`TableauExecutor` needed no
change at all. `detail` reports whichever blocker is more fundamental: if no real runner
exists (today's actual state), the existing "needs a driver... arrives with E11" message
is unaffected — a platform engineer flipping the policy flag would not change anything
yet — and the tenant-policy message only takes over once a real runner is actually
present and policy is the one thing left standing in its way. Proven end to end against
a real fixture `LiveQueryRunner` double (not `NoLiveQueryRunner`): policy-disabled
refuses before ever calling the inner runner; policy-enabled delegates and then still
refuses a write-shaped reconstruction (`DELETE FROM orders`); rows over `max_rows` are
truncated and marked.

### 5. Executor sandboxing: harden the one process that executes today, disclose the rest

No "executor worker" exists as its own deployable anywhere in this codebase — confirmed
by an exhaustive search of `deploy/`, which returns no hits for "executor" at all; the
spec's own `adapter-fabric`/`agent-runtime` worker units (§5.2) are declared, not built.
Building one from nothing is a new-service-shaped undertaking no story has asked for
yet, so this story hardens graph-svc's own existing egress instead of inventing a
second, narrower deployable around a process that does not exist:
`deploy/terraform/variables.tf` gains `api.powerbi.com` in the default
`egress_allow_list_fqdns` (the real, documented Fabric/Power BI XMLA connection host —
`powerbi://api.powerbi.com/v1.0/myorg/<workspace>`) and a new
`source_warehouse_allow_list_fqdns` variable (empty by default — genuinely
tenant-specific, per spec §19's own five source warehouse kinds, unlike the target
side's one real, fixed Fabric host). Both feed the same Firewall application rule
collection (`egress.tf`) as the AC's own "two data endpoints." **Disclosed, not
silently solved**: the rule is HTTPS-only (443) — correct for `api.powerbi.com` and an
HTTPS-based warehouse (Snowflake), but a SQL Server (1433) or PostgreSQL (5432) source
would need its own network-rule-collection entry once a client actually names one and a
real live-replay driver exists; a comment in `egress.tf` states this rather than
guessing at a port nobody has chosen yet. `deploy/helm/astra-data/templates/
networkpolicy.yaml` needed no change — ADR 0079's own "two layers, not one" design
already delegates FQDN-level allow-listing to the Firewall, with NetworkPolicy staying
generic HTTPS pass-through; the two data endpoints are a Firewall-layer fact, not a
NetworkPolicy one.

## Consequences

- `packages/adapter-sdk`: `ExecutionCharter.max_rows` (new `DEFAULT_MAX_ROWS = 100_000`).
- `services/graph-svc`: new `agent_identity`-adjacent `execution_safety.py`
  (`ExecutionSafetyPolicy`/`ExecutionSafetyPolicyStore`/`PostgresExecutionSafetyPolicyStore`/
  `InMemoryExecutionSafetyPolicyStore`/`authorize_target_workspace`); new migration v0041
  (`public.execution_safety_policy`, no ontology change); `case_execution_query.
  build_dax_query` gained `max_rows` (a real `TOPN` wrap); `case_execution.py`'s
  `CaseExecutionService` gained an optional `safety_policy_store`, checked first in
  `execute()`; new `api/routes_execution_safety.py` (`GET`/`PUT
  /v1/execution-safety/policy`); `main.py` wires a `PostgresExecutionSafetyPolicyStore`
  into both.
- `packages/adapter-tableau`: new `live_replay_policy.py` (`LiveReplayPolicy`,
  `validate_select_only`, `PolicyGatedLiveQueryRunner`); `TableauConfig` gained
  `live_replay_enabled`/`live_replay_max_rows` (new `ASTRA_TABLEAU_LIVE_REPLAY_ENABLED`/
  `_MAX_ROWS` env vars); `TableauAdapter.__init__` wraps its `live_runner` (including the
  default `NoLiveQueryRunner`) in the new policy gate.
- `deploy/terraform`: `variables.tf` gained `api.powerbi.com` in the default egress
  allow-list and a new `source_warehouse_allow_list_fqdns` variable; `egress.tf`'s
  firewall rule concatenates both; `identity.tf` reserves a second, distinct Key Vault
  secret slot (`fabric-execution-sp`) for the read-only XMLA execution principal; the
  module's own README documents both the new secret and its required "Viewer"-only
  Fabric role.
- `services/console-web`: Tenant & Access gained a third pane, "Execution safety" —
  the real `production_workspaces`/`version` read via a new `executionSafetyPolicy`/
  `saveExecutionSafetyPolicy` pair on `lib/api.ts`, edit hidden (not disabled) for every
  role but the platform engineer, the same hide-not-disable convention every other
  gated action on this screen already uses.
- Verified: `packages/adapter-sdk` — full suite re-run clean (126 passed, unchanged).
  `packages/adapter-tableau` — 21 new unit tests (`validate_select_only`'s own real
  SELECT/CTE-permit and DROP/DELETE/UPDATE/INSERT/stacked-statement/empty/unparseable
  refusals; `PolicyGatedLiveQueryRunner`'s own disabled-refuses-before-delegating,
  enabled-delegates-and-still-validates, row-truncation, and "more fundamental blocker
  wins" `detail` behaviour, all against a real fixture `LiveQueryRunner` double); the
  full suite re-run clean (282 passed, up from 261 -- a real regression in this story's
  own first draft was found and fixed here: the pre-existing `test_live_replay_says_why_
  it_is_unavailable` broke because the new policy wrapper's own `detail` was shadowing
  `NoLiveQueryRunner`'s own "arrives with E11" message even though no runner exists yet;
  fixed by making the more fundamental blocker win). `services/graph-svc` — 12 new unit
  tests (`execution_safety.py`'s own policy shape, `authorize_target_workspace`'s human-
  and-agent refusal and regression-runner exemption, the in-memory store's own
  versioning; `case_execution_query`'s own real `TOPN` wrap, bracket-balanced); 9 new
  integration tests against real PostgreSQL + Apache AGE (a real DAX query proven to
  contain `TOPN(...)`; a non-production workspace proven unaffected by a configured
  policy; a production workspace proven to refuse a Parity Engineer and allow the
  regression runner; the new policy routes' own round trip and role gate, both over real
  HTTP; a production-workspace refusal proven end to end over HTTP); the full unit suite
  re-run clean (1643 passed, up from 1631); the full integration suite re-run clean (up
  from 808, confirming zero regressions across the whole service); `ruff`/`mypy`/
  `ontology_check.py`/`migration_check.py` all clean -- no ontology change.
  `deploy/terraform` -- `fmt`/`validate` both clean. `services/console-web` -- 4 new
  tests (the honest empty-policy default, a real saved list rendered, Edit hidden for a
  non-platform-engineer role, a full save round trip); `tsc --noEmit`/`eslint` clean.

## Alternatives considered

**Build a real XMLA client now**, since target execution being 100% fixture is itself a
real gap. Rejected by the user's own explicit answer, and for the same reason S11.1.1/
S11.1.2 chose "disclosed, not yet connected" over building a live-tenant-connected
mechanism: there is no adapter-fabric package to extend, no live Fabric tenant to test
against in this environment, and no story has asked for that package to exist yet — an
XMLA client is a different, much larger undertaking than a *safety* story about the
credential and workspace boundary around whatever executes.

**Build a real live-replay database driver** (at least one, e.g. PostgreSQL, to prove
the whole path end to end). Rejected — `ports.py`'s own docstring already scopes this to
"arrives with E11," and this story is one piece of E11, not the piece that builds
warehouse connectivity; the policy/allow-list layer this story does build is fully,
independently provable against a fixture runner without needing a real driver at all.

**Fail closed on an unconfigured `ExecutionSafetyPolicy`** (refuse every workspace until
a platform engineer explicitly names dev/test as safe). Rejected — the identical
reasoning ADR 0080 already gave for agent scopes: a deployment nobody has configured yet
should not have its existing, already-working on-demand execution silently break; the
honest default is "no workspace is classified production," not "every workspace is
refused."

**Put the production-workspace check in `agent_identity.py`, as a new `AgentScope`
field.** Rejected — that module's own docstring is explicit it never scopes a `user:`/
`service:` principal, and this check has to refuse a human Parity Engineer, not only a
differently-scoped agent; a dedicated `execution_safety.py` function keeps that
boundary honest rather than stretching an agent-scoped module to cover a check that is
not really about an agent's own charter.

**A single, generic "tenant policy" table covering execution safety, SQL replay, and
whatever future policy needs arise.** Rejected as premature generalisation — this
codebase's own established pattern (`mender_config`, `tolerance_charter_version`,
`conformance_ruleset`) is one small, versioned table per real, distinct policy concern,
not one wide table accreting unrelated columns; `execution_safety_policy` follows that
precedent rather than inventing a second, competing convention.
