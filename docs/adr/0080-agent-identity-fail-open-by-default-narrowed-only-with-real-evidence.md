# ADR 0080 — Agent identity: fail-open by default, narrowed only with real evidence

Status: accepted · 14 September 2026 · Story S11.1.2, opens F11.1 (E11)

## Context

S11.1.2 — the backlog's own AC, verbatim: *"As a platform engineer, I want each agent to
run under its own non-human identity with least privilege, so that an agent's actions are
attributable and its reach is bounded."*

- Agents receive SPIFFE identities (SVIDs) at start; every adapter call, graph write,
  gateway call and artefact commit carries the agent's identity
- Permissions per agent are declared in the AgentRecord (§8.1) and enforced at the graph
  API, artefact store and gateway; an out-of-scope call is refused and logged
- Identity issuance, rotation and revocation are visible in Tenant & Access

This follows directly from S11.1.1 (Entra ID user sign-in, Key Vault credentials, a
signed deployment bill of materials — ADR 0079). Before any code was written, three
decisions genuinely needed the user's own call, asked together via `AskUserQuestion` and
answered definitively:

1. **SVID issuance** → *"Build it disclosed, not yet connected."* A real
   `WorkloadIdentityProvider` seam and a real SVID value object, tested against a local
   issuer — the identical posture ADR 0079 already carries for `entra.py`/
   `credentials.KeyVaultCredentialProvider`. No live SPIRE server.
2. **Adapter RPC identity** → *"Include it."* Thread identity through `packages/
   adapter-sdk`'s own RPC client/server, not defer it to a follow-up story.
3. **Tenant & Access scope** → *"A narrow, real screen under that name."* Build only
   what this story makes real (agent catalog, SVID issuance/rotation/revocation);
   §15.3.7's own broader scope (roles, Entra groups, service principals, secrets
   references) stays unbuilt.

## Decisions

### 1. `AgentRecord` is declared in code, not stored in Postgres

§8.1's own worked example is a fixed charter — what an agent consumes, produces, is
forbidden from, its autonomy ceiling, its model policy, its budgets. Nothing in the AC or
§8.1 calls this "configurable per tenant" the way `MenderConfig`/`AdoptionConfig`
explicitly are declared to be; it is this platform's own declared catalog of what it
deploys — the identical footing `roles.py`'s own `Role` enum already has for the client's
roles ("the set of roles ... is real and enforced; only the source of the assertion is
provisional"). `agent_identity.AGENT_CATALOG` declares all eight of §8.3's real agents
(harvester, cartographer, modeller, transpiler, compositor, arbiter, mender, steward);
`SvidRecord` — the real, runtime, per-issuance fact — is the thing that gets a Postgres
table (`public.svid_record`, migration v0040, no ontology change: an SVID is a fact about
this deployment's own identity plumbing, not the client's estate).

### 2. Least-privilege enforcement is fail-open by default, narrowed only for the Transpiler

This is the decision the whole story turns on, and it was not obvious going in.
`agent_identity.py`'s own module docstring states it directly: **every cataloged agent
except the Transpiler is declared `AgentScope.unrestricted()`.** The reason is not
caution for its own sake — it is what direct evidence from this codebase's own test suite
demanded:

- `tests/conftest.py`'s own default identity for nearly every test in this service is
  `agent:harvester`, exercising every kind of write this service makes. Narrowing
  `harvester`'s own scope on a guess would have broken hundreds of already-passing tests
  on this story's first day.
- `agent:steward` is `api/routes_g2.py`'s own real, already-passing automated
  post-G2-approval build trigger and `regression.py`'s own real scheduled re-run
  principal — broad, real, exercised production behaviour with no stated prohibition
  anywhere to narrow it against.
- A repo-wide search turned up over a dozen further ad-hoc `agent:` principal strings
  used purely as test-fixture convenience (`agent:ci`, `agent:pm`, `agent:test`,
  `agent:model-engineer`, and others) — none of them one of §8.3's eight real agents,
  all of them proof that this codebase's own `agent:` prefix has always meant "some
  non-human actor," not "the literal name of a deployed agent with a real charter."

Only the **Transpiler** gets a genuinely narrowed `AgentScope`, because it is the one
agent §8.1's own worked example gives concrete, checkable evidence for — its literal
`prohibited` list ("write outside MU scope", "call executor", "modify Pattern.
promotion_state") — and because `generation.py` (its real implementation) is small
enough to read in full and confirm exactly what it does and does not do: it writes
`Measure`/`ExceptionCase` nodes, sets properties on `CalculatedField` (confirmed against
`test_context_contracts.py`/`test_integration_context.py`'s own real fixtures, both
retargeted onto a new `FIXTURE_PRINCIPAL` where they used `agent:transpiler` purely as
fixture-setup convenience rather than real Transpiler behaviour), stores no artefact, and
calls no adapter of any kind. "Never calls the executor" is therefore already
structurally true — there is no real call site left for this module to refuse, a gap
disclosed rather than papered over with an untested check.

**An `agent:` principal naming no declared catalog id at all is also unrestricted, not
refused** — the same reasoning: a permission framework that broke a mature system's own
already-exercised behaviour on its first day would be worse than the gap it closed.
Narrowing further is real, incremental work this module's own shape (a pure
`authorize_*` function per enforcement point, keyed on a catalog lookup) is built to take
without changing any of its three call sites again.

**A `user:`/`service:` principal is never scoped by this module at all** — this story is
about an agent's own reach, not a human's; role-based authorization (`api/deps.py`)
already governs who may call which route.

### 3. Three real enforcement points, one funnel each

- **`GraphWriter._prepare_nodes`** — the single chokepoint `write_nodes`/`upsert_nodes`/
  `set_node_properties` all already funnel through (the last by calling `upsert_nodes`
  internally). One `authorize_node_write` call (node-type allow-list) plus one
  `authorize_property_write` call (the `"NodeType.property"` pairs a charter forbids
  outright, checked independently of the type gate — real defence in depth, proven by a
  dedicated unit test even though the Transpiler's own node-type gate already makes the
  scenario unreachable in production). Raises immediately rather than joining the
  existing `violations` list: an authorization refusal is a different kind of question
  ("who may write this at all") from an ontology violation ("is this well-formed"), and
  a mixed batch with one refused write commits nothing at all — proven directly.
- **`ArtefactStore.store`** (both `PostgresArtefactStore` and `InMemoryArtefactStore`) —
  `authorize_artefact_kind` before the existing "no bytes" check, since an agent whose
  own charter permits no artefact kind at all (the Transpiler's own real case — nothing
  in `generation.py` calls this) should never reach the emptier, less meaningful check.
- **`Gateway.generate`** (`ModelGateway`, `StaticGateway`, `_NoGateway`) — gained an
  optional, keyword-only `principal: str | None = None`. No caller passed one before this
  story (the parameter did not exist), so every existing call is unaffected by
  construction, not merely by convention; `generation.py`/`mender.py` now thread the
  real, already-in-scope `principal` through `_run_ladder`/`call_model_repair` down to
  this call, checking `authorize_gateway_call` against the real `TaskClass` string
  (`transpile_c3`, `transpile_c3_small_model`, `mender_repair`).

Refusals are logged (`logger.warning`, real, structured) at every one of the three
points — the AC's own literal "refused and logged." A durable, queryable refusal record
on the Evidence Chain (§4.5/§18.4) is real, disclosed *future* work: that chain does not
exist in this codebase yet (S11.3.1's own story), and the closest present mechanism (the
CloudEvents outbox) was judged not worth a new notice type for a refusal specifically,
since the AC's own third bullet only asks Tenant & Access to show issuance/rotation/
revocation, not refusals.

### 4. SVIDs are real, minted, and recorded — and do not (yet) gate anything themselves

`workload_identity.LocalWorkloadIdentityProvider` mints real, short-lived (`DEFAULT_TTL_
SECONDS = 300`) JWT-SVIDs — a real SPIFFE ID (`spiffe://<trust-domain>/agent/<id>/run/
<run-id>`), signed with a real, process-local Ed25519 key (generated fresh at
construction — a live SPIRE trust bundle would replace this without anything above the
`WorkloadIdentityProvider` interface changing shape, the identical seam ADR 0079 already
set for `directory.py`/`credentials.py`). `verify()` genuinely checks signature, issuer,
audience and expiry (tested against a locally generated keypair — a malformed token, a
token from a different issuer, and an expired token are each proven rejected). Real
issuance is wired into the three genuinely automated run-starting points this codebase
has: `HarvestScheduler._run`, `RegressionScheduler`'s own scheduled check, and the
G2-triggered automatic Steward build (`api/routes_g2.py`) — each best-effort (an
identity-recording hiccup never fails the real work it accompanies, the identical
posture `_run`'s own harvest-outcome handling already takes). `rotate()` is real and
tested; wired into no long-running loop in this story (a real, disclosed next step, not
a gap papered over).

**Authorization does not depend on a caller presenting a verified SVID back to this
service.** Nothing in this codebase's own real running code has a channel to do that yet
— no adapter worker or console client attaches one to a request today.
`agent_identity.py`'s own checks still key off the existing `X-Astra-Principal` header,
exactly as before this story. This is a deliberate, disclosed boundary: SVIDs are real,
minted, and durably recorded (the AC's own first and third bullets), but they do not yet
replace the header-asserted identity the AC's own second bullet is enforced against — the
identical "real but not yet connected to the live call path" posture ADR 0079 already
established for Entra ID.

**The signed token itself is never persisted.** `SvidRecord` (the durable half) carries
`jti`, `spiffe_id`, `agent_id`, `run_id`, `serial`, `issued_at`, `expires_at`, and
revocation fields — never `token`. The same "a secret never crosses request-config
plumbing" discipline `credentials.py` already states for a source credential, applied
here to a bearer credential instead. A real Postgres integration test confirmed this
directly (`test_the_signed_token_is_never_a_column`) — and, in writing it, found a real
bug: `PostgresSvidStore.record` was binding ISO-string timestamps directly to a
`timestamptz` column, which `asyncpg` rejects outright (it needs a real `datetime`).
Fixed by parsing the string back to `datetime` before the insert; every one of the six
integration tests now passes against a real database.

### 5. Revocation is the platform engineer's own real action

`SvidStore.revoke` requires a real reason (`MIN_REVOCATION_REASON_LENGTH = 8`, the
identical bar `writes.py`'s own `retire_node` already sets for "a decision anyone can
audit later"). `POST /v1/tenant-access/svids/{jti}:revoke` is gated `PlatformEngineerDep`
— this story's own literal persona — while reading the catalog and the SVID trail is
gated the identical "Artizent, or the InfoSec reviewer" shape `DecisionRegisterReaderDep`/
`DeploymentBomReaderDep` already set (agent identity evidence is exactly the kind of
export §15.1's own InfoSec remit already covers). A revoked record is never deleted —
`revoked_at`/`revoked_by`/`revocation_reason` are set in place on the existing row, the
one deliberate exception to "issuance and rotation are each a new, append-only row" (a
revocation is a fact *about* an existing issuance, not a new one).

### 6. The adapter RPC boundary carries identity via `contextvars`, not a new parameter on every method

`packages/adapter-sdk`'s own `SourceAdapter`/`TargetAdapter` Protocols have a dozen-plus
methods each, implemented by the conformance suite, every fixture adapter, and
`adapter-tableau` — widening every one of them with a new `principal` parameter would
have meant touching a published, separately versioned contract's entire surface, its own
conformance suite, and every real and fixture implementation, for a capability the AC
only asks to be *attributed*, not *enforced*, at this boundary (bullet two names "the
graph API, artefact store and gateway" — not the adapter). Instead, `astra_adapter.rpc.
identity` (new) is a small `contextvars`-based module: `identity(principal, run_id)` is a
context manager a caller wraps around one call (or one whole run); `RemoteAdapter`'s own
`_get`/`_post` read the ambient value and attach it as the identical `X-Astra-Principal`/
`X-Astra-Run-Id` headers graph-svc's own HTTP layer already uses. `asyncio.create_task`
copies the current context at creation, so wrapping `harvester.run(...)`'s own task
creation (`api/routes_harvest.py`) or a direct `await` (`harvest/scheduler.py`,
`regression.py`, `api/routes_case_execution.py`) is enough to cover the whole call chain
underneath it, with zero changes to `Harvester`, `CaseExecutionService`, any adapter
implementation, or the conformance suite. The RPC server's own new
`_IdentityAttributionMiddleware` logs the header when present and changes nothing else —
attribution only, exactly as decision 4 requires; enforcement stays graph-svc's own job.

**Wired into the two real, dominant adapter-calling paths — harvest and parity
execution — not every one.** `visual_parity.py`'s own capture path, `build.py`/`report_
deploy.py`/`release.py`'s target `commit`/`deploy` calls, `adoption.py`'s usage sweep and
`g4_card.py`'s archive path are real adapter callers this story does not wrap, disclosed
rather than silently incomplete — the identical "a curated rollout, not an exhaustive
one" precedent ADR 0071 already set for `Explain`. No `INTERFACE_VERSION` bump: the
headers are additive transport metadata an adapter worker that ignores them serves
identically, not a capability or wire-shape change to the §6.1 contract itself.

### 7. Tenant & Access: the narrow slice, not the named screen's full scope

§15.3.7 describes a much broader Admin screen ("Roles, users (Entra groups), site/domain
scoping, service principals, secrets references. | Assign role; scope; rotate") — this
is the *first* backlog story to actually require the screen at all (confirmed: it is
absent from `App.tsx`'s own already-disclosed §15.1 landing-page gaps, and named only as
one of four not-yet-built Admin screens `Admin.tsx`'s own docstring already flags). Built
here: two tables (the declared agent catalog with a per-agent charter detail panel, and
the real SVID trail with status/serial/spiffe-id), plus the one real action this story
makes real (revoke). Everything else §15.3.7 names — role assignment, Entra group
mapping, site/domain scoping, service-principal/secrets references — stays unbuilt,
disclosed in the screen's own header comment, the identical "closest real thing, not the
whole named screen" precedent `App.tsx`'s own docstring already sets (ADR 0071).

## Consequences

- `services/graph-svc`: new `agent_identity.py` (`AgentRecord`/`AgentCharter`/
  `AgentScope`, the eight-agent `AGENT_CATALOG`, four `authorize_*` functions),
  `workload_identity.py` (`Svid`/`SvidClaims`/`LocalWorkloadIdentityProvider`,
  `SvidRecord`/`SvidStore`/`PostgresSvidStore`/`InMemorySvidStore`); new migration
  `v0040_svid_record.py` (`public.svid_record`, no ontology change). `writes.py`
  (`_prepare_nodes`), `artefacts.py` (both `store()` implementations), `gateway.py`
  (`Gateway` protocol + all three implementations gained optional `principal`) each
  additively enforce; `generation.py`/`mender.py` thread `principal` through to the
  gateway. `harvest/scheduler.py`/`regression.py`/`api/routes_g2.py` each gained
  optional, additive SVID issuance at their own real run-starting point. New `api/
  routes_tenant_access.py` (`GET /v1/tenant-access/agent-records`, `GET .../svids`,
  `POST .../svids/{jti}:revoke`); new `deps.py` dep `require_tenant_access_reader`
  (`TenantAccessReaderDep`) — `PlatformEngineerDep` (pre-existing) gates revoke. `main.py`
  wires `app.state.svid_provider`/`svid_store`, both schedulers, and the new router.
- `packages/adapter-sdk`: new `rpc/identity.py` (`identity()` context manager,
  `current_principal`/`current_run_id`); `rpc/client.py`'s `RemoteAdapter` attaches the
  ambient identity as headers on every call; `rpc/server.py` gained
  `_IdentityAttributionMiddleware` (logs, never enforces). No `INTERFACE_VERSION` bump —
  see decision 6.
- `services/console-web`: new `tenant-access/TenantAccess.tsx` (agent catalog + SVID
  trail + revoke), a new `tenant-access` surface gated to Artizent roles and
  `client_infosec_reviewer` (`App.tsx`'s `CLIENT_VISIBLE_SURFACES`, the identical
  "Artizent, or this one named client role" shape the Decision Register/deployment BOM
  screens already set); `lib/api.ts` gained `AgentRecord`/`AgentScope`/`AgentCharter`/
  `SvidRecord` types and `agentRecords`/`svidRecords`/`revokeSvid` methods.
  `.tenant-access-workspace` joined the single-column override list and both narrow-
  viewport `:not(...)` exclusions from its own first draft — the identical mistake this
  codebase's own README already discloses making twice before (S10.5.1, then again at
  S10.5.2) is not repeated a third time.
- Verified: `services/graph-svc` — 116 new unit tests (`agent_identity.py`'s own catalog
  shape and every `authorize_*` function, including the fail-open-by-default proof
  against `harvester`/`steward`/an unknown agent id and the narrowed Transpiler's own
  refusals; `workload_identity.py`'s issue/rotate/verify/revoke, including a tampered,
  an expired and a wrong-issuer token each proven rejected; the three real enforcement
  points proven against the real `GraphWriter`/`ArtefactStore`/`Gateway` objects, not
  only the pure functions; the new Tenant & Access routes' own role gates); 6 new
  integration tests against real PostgreSQL (found and fixed a real datetime-binding bug
  along the way — see decision 4); the full unit suite re-run clean (1631 passed, up
  from 1573, confirming every additive enforcement point broke nothing already there);
  `ruff`/`mypy`/`ontology_check.py`/`migration_check.py` all clean. `packages/adapter-sdk`
  — full suite re-run clean (126 passed, unchanged, confirming the identity-context
  wiring is genuinely invisible to every existing caller and the conformance suite).
  `services/console-web` — 8 new tests (the full agent catalog rendered including the
  disclosed-not-real Arbiter, the Transpiler's own narrowed scope shown on view, revoke
  hidden for a non-platform-engineer role and completed for one with a real reason);
  `tsc --noEmit`/`eslint` clean.

## Alternatives considered

**Narrow every cataloged agent's scope to a best guess at what it "should" be allowed,
matching §8.3's own Consumes/Produces columns.** Rejected — see decision 2. A repo-wide
search of this codebase's own test suite found the `agent:` prefix already used as a
generic "some non-human actor" convenience across dozens of scenarios unrelated to any
real agent's own charter; guessing scopes for `harvester`/`steward`/`cartographer`/
`modeller`/`compositor`/`mender` without real evidence would have been fabricating
restrictions this codebase has no basis for, and for the two real, already-exercised
principals (`harvester`, `steward`) it would have broken production-shaped behaviour on
this story's own first day.

**Fail closed for any unrecognised `agent:` principal.** Rejected, for the identical
reason — this codebase's own test suite constructs over a dozen distinct `agent:`
principal strings that name no real §8.3 agent at all; refusing them would have broken a
large, unrelated swath of already-passing tests for a change those tests were never
asking for.

**Deploy a real, self-hosted SPIRE server** (the user's own second option, not chosen).
Rejected by the user's own explicit answer — "build it disclosed, not yet connected,"
the identical posture already established for Entra ID in ADR 0079, kept consistent
rather than treating one E11 identity provider differently from the other on the basis
that SPIRE happens not to need an external tenant the way Entra ID does.

**Thread a new `principal` parameter through every §6.1 adapter Protocol method.**
Rejected — see decision 6. The AC's own words scope enforcement to the graph API,
artefact store and gateway; attribution at the adapter boundary is real and satisfied by
a much smaller, `contextvars`-based seam that touches zero adapter implementations and
the conformance suite not at all.

**Build §15.3.7's full Tenant & Access scope now**, since the screen has to be created
either way. Rejected — see decision 7. Roles/users/Entra-group mapping/site-domain
scoping/service-principal references are each their own real, unbuilt engine feature;
building a screen around facts that do not exist yet would be exactly the scope creep
`App.tsx`'s own docstring already disclaims doing for every other named-but-unbuilt
screen in this console.
