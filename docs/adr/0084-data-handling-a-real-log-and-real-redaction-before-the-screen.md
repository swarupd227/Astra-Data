# ADR 0084 — Data Handling: a real log and real redaction before the screen

Status: accepted · 15 September 2026 · Story S11.4.1, opens F11.4 (E11)

## Context

S11.4.1 — the backlog's own AC, verbatim: *"As an InfoSec reviewer, I want a Data
Handling screen that states exactly what reaches a model endpoint and lets me confirm
it, so that the inference boundary is a signed position, not an assurance."*

- Screen shows: provider(s) and endpoint location, the inference boundary table from
  §18.3 (what is sent: metadata, calc expressions, schema, error messages, patterns;
  what is never sent: row-level data, result sets, screenshots, credentials), retention
  terms, and the gateway's redaction rules
- "Sign boundary" records the reviewer, the version of the position and the date;
  changing a provider or a redaction rule invalidates the signature and requires re-sign
- Boundary test: a CI and on-demand check sends sentinel row data through every agent
  path and asserts it never appears in a gateway request log

Spec §15.3.7/§18.3 name the same construct. Before any code was written, research (an
`Explore` agent's own full pass over `gateway.py`, `mender.py`, `generation.py`,
`observability.py`, `events.py`, and the product spec, followed by a second, deeper
pass tracing exactly what data flows into a Mender repair prompt) found: **nothing in
this codebase logged a gateway request's own text, and no redaction of any kind existed
anywhere** — the AC's own third bullet, taken literally, would either need new
infrastructure or run vacuously (nothing ever logged, so a sentinel trivially "never
appears" in a log that never records anything). Three explicit questions were asked and
answered before writing any code, all "(recommended)":

1. **Gateway log** → *"Build a minimal, real, always-on request log now."* A deliberate,
   disclosed, narrow pull-forward of the one storage primitive S11.4.2 (the next story
   in this feature) actually needs, so this story's own boundary test has something
   real to check.
2. **Provider config** → *"Real, versioned, editable config."* The same
   `execution_safety_policy`/`mender_config` shape every other tenant-configuration
   table in this epic already uses — and the only way "changing a provider... invalidates
   the signature" can mean anything real.
3. **Screen placement** → *"A new top-level surface."* §15.3.7 names Data Handling as
   its own distinct Admin row; this story (unlike S11.1.2's own InfoSec-Estate-Explorer
   landing redirect) actually builds the real, named screen.

## Decisions

### 1. The one real row-level-data channel this codebase has, found by tracing both real gateway call sites

Direct research into exactly what data reaches `gateway.generate` confirmed only two
real call sites exist: the Transpiler's own `generation.build_generation_request`
(confirmed to touch only calc formulas/ASTs and field/parameter/worksheet *names* — no
`ParityCase`/`Verdict`/`ResultSet`/evidence-artefact read anywhere in that path, so
nothing there could ever leak row-level content) and the Mender's own
`mender.assemble_repair_context`, which reads each failing case's real evidence
artefact bundle (`Verdict.evidence_ref`) and places its own real `failing_cells` —
genuine dimension/key values and genuine expected/candidate measure values,
`diff.FailingCell.as_dict()`'s own real shape — directly into `RepairContext`, the one
request shape that ever reaches `MENDER_REPAIR`. **This module's own docstring
(`mender.py`) already said as much** ("Mender repair context is assembled from evidence
bundles after redaction," spec §18.3) — but no redaction of any kind existed before
this story; the sentence was aspirational, not yet true. The boundary test exercises
this one real channel only, disclosing (in its own module docstring) that the
Transpiler's own path is not exercised because there is nothing there for a sentinel to
leak through.

### 2. Redaction: two rules, matching §18.3's own two named transformations exactly

`redaction.py` — `hash_key_value` (a short, non-reversible SHA-256 digest, deterministic
so two cells sharing a real key are still visibly linked after redaction) and
`bucket_measure_value` (sign-and-magnitude only: `"+1e2"`, `"null"`, `"0"`,
`"non-numeric"` — never the real number). Applied inside `mender.assemble_repair_
context`, immediately after `_gather_parity_evidence` returns real cells and *after*
§8.1.1's own `classification_signals` have already been read from the `ExceptionCase`'s
own stored properties (confirmed: classification is never re-derived from
`failing_cells` at this point) — so redaction here changes nothing about the Mender's
own deterministic classification or pattern-matching, only what a reasoning-tier model
call actually receives. Verified directly: a real integration test writes a real
`expected` value and a real grain/key value into a real evidence bundle, calls
`assemble_repair_context` for real, and confirms neither the real number nor the real
key string appears anywhere in the resulting `RepairContext.as_dict()`.

### 3. A real, always-on gateway request log — narrower than S11.4.2's own full scope, on purpose

`gateway.GatewayRequestLogStore` (`PostgresGatewayRequestLogStore`, migration v0043's
`public.gateway_request_log`) records the literal outbound request text `_build_prompt`
renders, written inside `ModelGateway.generate`/`StaticGateway.generate` immediately
before the real provider call — so a request that never routes (`GatewayRoutingError`,
the honest, expected outcome of every `MENDER_REPAIR` call in this deployment today,
per `mender.py`'s own pre-existing docstring) is never logged at all, since nothing was
ever really about to be sent. This is **not** S11.4.2's own fuller mechanism (secret-
pattern redaction of the log itself, content-logging off by default for a bounded
window) — those stay that later story's scope; this table exists only so S11.4.1's own
boundary test has a real log to assert sentinel absence against, rather than a vacuous,
nothing-ever-recorded pass.

### 4. The boundary test drives the real pipeline end to end, with a built-in self-check against a vacuous pass

`data_handling.run_boundary_test` plants a real sentinel inside a real, disposable
evidence bundle referenced by a real, disposable `Verdict` node, calls `assemble_repair_
context` for real, then routes the resulting `RepairContext` through a real
`ModelGateway` — a synthetic, network-free `ModelCaller` (so the check needs no live
provider credentials in CI) wired to the real `GatewayRequestLogStore` and a real,
seeded-routable `GatewayPolicyStore` row. It asserts the sentinel is absent from *both*
the assembled context and the resulting real log row — **and asserts a real, non-
sentinel marker (the case's own failure class) is present**, so an empty result can
never be silently mistaken for "nothing was ever logged." The disposable `Verdict` is
retired (never deleted) afterward — the identical soft-delete-only discipline every
other write in this codebase already keeps, leaving a real, small audit trail that the
test actually ran. Matches `evidence_chain.py`'s own CLI-tool/CI-job/on-demand-route
shape exactly: `tools/verify_boundary.py`, a new `nightly.yml` job (`workflow_dispatch`
already on the workflow), and `POST /v1/data-handling:verify-boundary` all call the
identical function.

### 5. Validity is a computed comparison, never a stored flag; signing is the InfoSec reviewer's own, narrower than every other gate in this epic

`data_handling.boundary_status` computes `signed` as `latest_signoff.position_version ==
latest_position.version` — editing the position (`save_position`) simply outpaces
whatever version was last signed, "requiring re-sign" for free, with no separate
invalidation write anywhere. `sign_position` accepts no caller-supplied version; it
always signs the *current* latest position, the only version a signature can honestly
attest to. **`POST /v1/data-handling:sign` is gated `InfosecReviewerDep`
(`Role.CLIENT_INFOSEC_REVIEWER` alone) — not "any Artizent role," the shape every other
gate in this epic uses.** The AC's own "a signed position, not an assurance" is a
statement about *whose* attestation this is: Artizent signing its own data-handling
claim on the client's behalf would be exactly the assurance the AC contrasts against.
Reading the screen and running the boundary test stay open to any Artizent role too
(`DataHandlingReaderDep`, the identical `TenantAccessReaderDep` shape) — only the sign
action itself is this one role's own.

## Consequences

- `services/graph-svc`: new `redaction.py` (`hash_key_value`/`bucket_measure_value`/
  `redact_failing_cell`, `REDACTION_RULES`); `mender.assemble_repair_context` now
  redacts `failing_cells` before they reach `RepairContext`. `gateway.py` gained
  `GatewayRequestLogStore`/`PostgresGatewayRequestLogStore`/
  `InMemoryGatewayRequestLogStore`, an optional `log_store` on both `ModelGateway` and
  `StaticGateway`, and a shared `_log_request` helper both call before their own real
  provider call. New `data_handling.py` (`DataHandlingPosition`/`DataHandlingSignoff`
  and their stores, `INFERENCE_BOUNDARY_TABLE` — a fixed, spec-verbatim constant, never
  tenant-editable, `boundary_status`, `sign_position`, `run_boundary_test`). New
  migration v0043 (`public.data_handling_position`, `public.data_handling_signoff`,
  `public.gateway_request_log`, no ontology change). New `api/deps.py`
  `require_data_handling_reader`/`require_infosec_reviewer`; new
  `api/routes_data_handling.py` (`GET /v1/data-handling`, `PUT /v1/data-handling/
  position`, `POST /v1/data-handling:sign`, `POST /v1/data-handling:verify-boundary`).
  New `tools/verify_boundary.py`; `.github/workflows/nightly.yml` gained a third job,
  `boundary-test`, on the identical cron + `workflow_dispatch` schedule its own two
  siblings already have.
- `services/console-web`: a new top-level surface, Data Handling (`data-handling/
  DataHandling.tsx`) — providers/retention/redaction-rules display and edit (platform
  engineer), the fixed inference-boundary table, sign-off status with a real
  "requires re-sign" state, "Sign boundary" (hidden for every role but the InfoSec
  reviewer, the identical hide-not-disable convention every gated action in this
  console already uses), and "Run boundary test." **`client_infosec_reviewer`'s own
  landing surface changed from `estate` to `data-handling`** — this file's own prior
  "nothing InfoSec-shaped exists yet" reading stood only until this exact screen was
  built; `App.tsx`'s own module docstring and `CLIENT_VISIBLE_SURFACES` both updated to
  match. `styles.css` gained `.data-handling-workspace` in the single-column-override
  list (the same fix S10.5.1 already made proactively for every screen since).
- Verified: `services/graph-svc` — 27 new unit tests (`redaction.py`'s own hash/bucket/
  cell-redaction functions including every non-numeric/None edge case; `data_handling.py`'s
  own in-memory sign/re-sign/invalidation logic; `gateway.py`'s own request-logging —
  a real request logged before the provider call, a routing failure never logged, no
  log store configured is a silent no-op, `StaticGateway` logs too when given one). 14
  new integration tests against real PostgreSQL + Apache AGE: a real `expected` value
  and a real key string proven absent from a real `assemble_repair_context` result (the
  existing Mender integration suite re-ran clean alongside it, confirming the redaction
  change broke nothing already there); real position/sign-off versioning and real
  invalidation-on-edit; the real boundary test passing against a clean estate, its own
  disposable `Verdict` confirmed retired afterward, and a real logged row confirmed
  present with the real marker and without the real sentinel; the full HTTP surface
  including all three distinct role gates (a client report owner refused read, the
  platform engineer allowed to edit and refused to sign, the InfoSec reviewer allowed
  to sign and refused Artizent's own attempt). The CLI tool was also run directly
  against the real, large `astra_estate_test` demo graph and passed. The full graph-svc
  suite re-ran clean (2564 passed, up from 2524, one unrelated pre-existing flake in
  `test_integration_cartographer.py` — a background-task/pool-teardown race, the same
  category ADR 0072 already disclosed for a different test — confirmed unrelated by
  isolated re-run); `ruff`/`mypy`/`ontology_check.py`/`migration_check.py` all clean —
  no ontology change. `services/console-web` — 12 new tests (the real provider/
  retention display, Edit hidden for a non-platform-engineer role, a real edit-and-
  reversion round trip, the fixed boundary table and current redaction rules, the
  honest unsigned state, Sign hidden for every role but the InfoSec reviewer, a real
  sign round trip, a real previously-signed state, a real stale-after-edit re-sign-
  required state, a real boundary-test pass, the test offered to Artizent too, and a
  real API-refusal surfaced); two real, intended regressions found by the full suite
  and fixed as direct consequences of the landing-surface change (`app.test.tsx`'s own
  InfoSec-lands-on-Estate-Explorer case retargeted to the real new landing pane;
  `app-entra.test.tsx`'s own bearer-token capture retargeted from `api.estate` to
  `api.dataHandling`, the real call the new landing screen actually makes); the full
  suite re-ran clean (one unrelated, pre-existing flake in `calibration.test.tsx`
  confirmed by isolated re-run — 9/9 passing alone); `tsc --noEmit`/`eslint`/`vite
  build` all clean.

## Alternatives considered

**Defer real gateway logging to S11.4.2 and run the boundary test against an in-memory
capture used only during the test itself.** Rejected by the user's own explicit
answer — a durable log an InfoSec reviewer can actually audit later, not just a
test-harness artifact, is what "asserts it never appears in a gateway request log"
honestly means; a capture that exists only inside the test process proves the check's
own logic works, not that a real deployment's own real traffic is boundary-safe.

**Hardcode provider/region/retention as a static constant, disclosed as not yet
configurable.** Rejected by the user's own explicit answer — the AC's own second bullet
("changing a provider... invalidates the signature") needs something real to change; a
constant that never changes makes that bullet permanently untestable, not merely
narrower.

**Keep Data Handling as a fifth Tenant & Access pane**, avoiding a new nav entry.
Rejected by the user's own explicit answer — §15.3.7 names Data Handling as its own
distinct Admin row, separate from Tenant & Access, and this story (unlike S11.1.2's own
landing-redirect precedent) genuinely builds the real, named screen; the same "own
top-level surface, not an Admin sub-screen" call the Tolerance Charter and Pattern
Library already made for an analogous situation.

**Re-derive `failing_cells`' own real classification signals from the redacted cells at
prompt-assembly time**, rather than trusting `classification_signals` already stored on
the `ExceptionCase`. Not attempted — confirmed unnecessary by direct reading:
classification happens earlier, in §8.1.1's own pass, and is read back as an opaque,
already-computed dict; redacting the cells that flow into the *prompt* changes nothing
about a decision already made and stored before this story's own code ever runs.
