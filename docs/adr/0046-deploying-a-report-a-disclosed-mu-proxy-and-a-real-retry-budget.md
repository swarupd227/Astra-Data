# ADR 0046 — Deploying a report: a disclosed MU proxy, and a real retry budget

Status: accepted · 6 September 2026 · Story S6.1.2 (E6, F6.1, closing it)

## Context

S6.1.2 closes F6.1 — spec §7.1/§7.2, §3.2 (the Migration Unit state machine): *"As a
migration engineer, I want the report definition committed to Git and deployed to the dev
workspace bound to the family model, so that I can open the generated report in Fabric
within minutes of generation."*

- Commit through the target adapter with the MU id in the message; deployment through
  Fabric Git integration to the dev workspace; report bound to the PUBLISHED or BUILT model
  for its family
- Deployment failure returns the MU to GENERATED with the error on the MU page; three
  retries with backoff

S6.1.1 (ADR 0045) deliberately stopped at "PBIR output validates ... before commit" — this
story is the commit. `build_family` (S4.3.1) is the direct precedent for "emit, commit,
deploy" against the same `TargetAdapter` contract, but research confirmed two things build_
family never needed: it has **zero retry logic anywhere in its own call chain** (a build
failure is simply left for a human to retry via its own manual route), and **`SemanticModel.
state` is never actually written as `"BUILT"` anywhere in this codebase** — only `ModelFamily.
state` is, even though `SemanticModel.state`'s own declared note already promised
"deployment state within an environment." Both gaps are real, not assumed, and both had to
be resolved for this story to do what its own acceptance criteria asks.

## Decisions

### 1. Deploy is its own action, not a hidden step chained onto compose

`compose_report` (S6.1.1) already shipped, is tested, and its own acceptance criteria
stops at validation. Silently bolting a commit-and-deploy call onto it would change a
previous story's contract for behaviour that story never asked for. A new function,
`deploy_report` (`report_deploy.py`), reads back the workbook's *already-composed* report
(`compositor.read_report`) and commits/deploys it — the same "read the frozen artefact back,
independently re-emit, then act" shape `build_family` already uses against `modeller.
read_design_document`. A Migration Engineer composes, reviews S6.1.1's own disclosed
binding warnings, and deploys as a second, deliberate action — mirroring the two-step shape
`build_family`'s own manual retry route already gives a Semantic Model Engineer.

### 2. `TargetAdapter.commit`/`.deploy` are reused verbatim — `TmdlBundle` included

`target_setup.py`'s own docstring already promises this: a real target adapter changes
nothing about how `build_family` calls it. The same holds for reports. `TmdlBundle` (`files:
Mapping[str, bytes]`) is reused for the PBIR bundle's own JSON documents (each encoded as
UTF-8 bytes) rather than inventing a same-shaped `PbirBundle` type to satisfy a label —
the contract's own shape is a named byte bundle, not something structurally specific to
TMDL despite its name. No smoke query: the AC names none for a report (unlike `build_
family`'s own per-table check), and inventing one now would be data this story has no real
use for yet.

### 3. "PUBLISHED or BUILT model" is checked on `SemanticModel.state` directly — closing a real, disclosed gap in `build.py`, not working around it

Tracing every write site confirmed `SemanticModel.state` was only ever set to `DRAFT`
(`request_new_version`) or `PUBLISHED`/`DEPRECATED` (`promote_family`) — never `BUILT`, even
though `ModelFamily.state` gets it on every successful build. Reading `ModelFamily.state`
here instead would be a workaround, not a fix, and would get the wrong answer the moment a
family has moved on to a v(n+1) DRAFT while its v(n) `SemanticModel` — the one a report is
actually bound to — stays the live, buildable one. `build.py`'s own `finish()` now also
stamps `SemanticModel.state = "BUILT"` on a successful build, alongside the `ModelFamily`
write it already made — a small, surgical, well-justified extension of a previous story's
own code, the identical shape this session has already applied repeatedly (`patterns.py`
across S5.5.1–S5.5.3). `deploy_report` then checks `state in ("BUILT", "PUBLISHED")` on the
one real node the AC's own wording names.

### 4. "Returns the MU to GENERATED with the error" is a disclosed proxy — the sixth time this exact gap has been found and disclosed, not worked around differently each time

No real Migration Unit node or §3.2 state machine exists anywhere in this codebase
(reconfirmed directly against `migration_units.py`: `MigrationUnitRegistry` declares no
`set_state`/`transition` method at all, and `NullMigrationUnitRegistry`/
`InMemoryMigrationUnitRegistry` have nothing to write GENERATED to) — the identical gap
S5.4.1, S5.5.1, S5.5.2, S5.5.3 and S6.1.1 each already found and disclosed in their own way.
"The MU page" is F10.3's own unbuilt future screen (confirmed: every "...on the MU page"
phrase across F6.1/F6.2/F10.x points at the same not-yet-built generic page, never a
Compositor-specific one). Two new, additive `ReportDefinition` properties — `deploy_state`
(`"GENERATED"` / `"DEPLOY_FAILED"`) and `deploy_error` — carry exactly this fact on the one
real node this action touches. Nothing here claims to have moved a real MU backward from
PROVING, because nothing here ever moved one forward into it either; "returns to GENERATED"
is read as "never claims the deploy succeeded," which is the honest, checkable half of the
AC's own sentence.

### 5. Retries apply to the deploy call only, with a fixed backoff schedule, read as a three-attempt budget

A local Git commit is not the flaky hop; a Fabric Git-integration sync is — retries wrap
`target_adapter.deploy()` only. No spec text names a retry count or backoff shape for
deployment anywhere (checked directly: every "retry"/"backoff" mention in the spec is
generic or names a different number for a different mechanism — the Arbiter's own "retried
once," the Mender's own "pass budget (default 3)"); "three retries with backoff" is the
backlog's own invented number, so this story owns the shape. `DEPLOY_RETRY_DEFAULT = 3` is
read as a **total attempt budget** (matching the Mender's own "budget" reading of a very
similar backlog phrase), not three retries *after* a first attempt. Backoff is a small
fixed schedule (`(2.0, 5.0)` seconds between attempts), the same "a table, not a formula"
shape `adapter-tableau`'s own `throttle.py` already established for its own (unrelated)
retries — sized down for a three-attempt budget rather than reusing that module's own
five-attempt HTTP-429 schedule, a different package (graph-svc does not depend on
`astra_adapter_tableau`) solving a different failure domain. `sleep` is an injectable
parameter (`Callable[[float], Awaitable[None]]`, defaulting to `asyncio.sleep`) so tests
prove the retry *count* and *order* without spending real wall-clock seconds.

## Consequences

- New module `report_deploy.py`: `DeployStep`, `DeployRecord`, `ReportDeployStore`/
  `PostgresReportDeployStore`, `ReportDeployError`, `deploy_report`,
  `DEPLOY_RETRY_DEFAULT = 3`.
- Migration v0025: `public.report_deploy_run`, the identical shape `build_run` (v0018)
  already set, keyed by `workbook_id` (not `report_id`, since a recompose retires and
  replaces the report id — the workbook is what a Migration Unit actually is).
- `build.py`: `finish()` now also stamps `SemanticModel.state = "BUILT"` on a successful
  build (decision 3) — a real, disclosed behaviour change verified against `build.py`'s own
  existing integration suite (14 tests, unaffected).
- `ontology/nodes.py`: `ReportDefinition.deploy_state`/`.deploy_error` (additive), schema
  version 23 (up from 22); `ReportDefinition.pbir_ref`'s own note extended — finally
  written, as the deploy's own Git commit sha, having been declared-but-unused since §4.1.1.
- `compositor.py`: `Compositor` gains public `pool`/`graph_name`/`writer` properties (the
  same shape `Modeller` already has) so a route can call `deploy_report` without a second
  `graph_name` of its own.
- New routes: `POST /v1/workbooks/{id}:deploy`, `GET /v1/workbooks/{id}/deploy` — both
  reusing `MigrationEngineerDep`/`ArtizentDep`, no new role.
- No console screen: the generic future MU page (F10.3/S10.3.1) is what "the MU page"
  means; nothing nearby claims a deploy-status-specific screen.
- Verified against real PostgreSQL + Apache AGE and a real local Git repository (Dulwich):
  a real commit lands under the workbook's own item path with the MU id in the message; a
  real deploy syncs into the dev workspace; a report bound to a not-yet-BUILT model is
  refused before any Git write happens; a simulated transient deploy failure retries and
  recovers; a deploy that never recovers is retried exactly `DEPLOY_RETRY_DEFAULT` times and
  records `DEPLOY_FAILED` with the real error on the `ReportDefinition`; both new routes
  drive their real role gates — 18 new integration tests, full suite green (993 unit,
  unchanged; 418 integration + 2 skipped, up from 400 — one already-flagged, pre-existing,
  unrelated `test_integration_g2_reminders.py` flake, unaffected by this story). Also
  verified live against the running Docker stack's own real fixture-harvested estate: a
  real `POST /v1/families/{id}:build` stamped a family's `SemanticModel.state = "BUILT"`
  for the first time, after which `POST /v1/workbooks/{id}:deploy` committed and deployed
  the workbook's own already-composed report for real, recording `deploy_state:
  "GENERATED"` on its `ReportDefinition`.

## Alternatives considered

**Chain deploy automatically onto `compose_report`.** Rejected — see decision 1. Would
silently expand a previously-shipped story's own contract, and removes the deliberate
"review the warnings, then deploy" step a Migration Engineer gets from two separate actions.

**Check `ModelFamily.state` instead of fixing `SemanticModel.state`.** Rejected — see
decision 3. Correct today only by coincidence (before any story ever created a second live
`SemanticModel` version); wrong the moment a family has a DRAFT v(n+1) alongside a BUILT/
PUBLISHED v(n), which is exactly the case this check exists to get right.

**Leave `SemanticModel.state` as a gap and refuse every deploy until a later story fixes
it.** Rejected. `build.py` already computes everything the fix needs (`semantic_model_id`
is already in hand at the exact point `ModelFamily.state` is set); refusing to close a
one-line, well-understood gap in favour of leaving this story permanently unable to satisfy
its own acceptance criteria would be a worse outcome than the small, disclosed extension.

**Reuse `adapter-tableau`'s own `throttle.py` backoff module.** Rejected — see decision 5.
Wrong package (graph-svc does not depend on `astra_adapter_tableau`), wrong failure domain
(HTTP 429 concurrency-adaptive backoff against one site, not a fixed-budget deploy retry),
and its own five-attempt schedule does not match this story's own three-attempt budget.

**Model `deploy_state`/`deploy_error` as a real, closed enum with ontology-level
validation.** Rejected, matching `ReportDefinition.validation_state`'s own precedent
(`T.STRING`, free text, "left as free text until E5/E7 fix the closed set") — a disclosed
proxy for a state machine this codebase does not own is not the place to invent premature
closed-set enforcement.

## Open questions for the product owner

- Should a successful deploy automatically re-trigger anything downstream (e.g. an Arbiter
  proof run once E7 exists), the way a successful G2 approval already auto-triggers a
  model build? Nothing today consumes `deploy_state = "GENERATED"`.
- Is `DEPLOY_RETRY_DEFAULT = 3` (read as a total attempt budget) the right reading of the
  backlog's own "three retries", or should a future story make this explicitly four total
  attempts (three retries *after* a first)? No spec text disambiguates this either way.
