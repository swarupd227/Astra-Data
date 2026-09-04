# ADR 0032 — TMDL emission is pure; the target adapter is a new, narrower boundary

Status: accepted · 4 September 2026 · Story S4.3.1 (E4 / F4.3)

## Context

S4.3.1 asks for the first build: *"As a model engineer, I want an approved design built as
TMDL and deployed to the dev workspace automatically, so that the model exists as code the
moment it is approved. Emission is deterministic from the approved design version; the same
version always produces byte-identical TMDL. TMDL is committed to the client's Git
repository through the target adapter with a commit message referencing the family and G2
decision id. Deployment to the dev workspace uses Fabric Git integration; a smoke query per
table (row count, one measure) runs and the result is stored. BUILT is entered only when
deployment and smoke queries pass; failures show on the Build tab with the log."*

Four questions decided the shape of the work: where the §6/§7 boundary this platform
already draws for a *source* adapter should draw its *target* counterpart, given §7.1 is
the first story to need one; what "byte-identical" can mean when the frozen design carries
no column-level schema; what a target adapter can honestly do in an environment with no
live Fabric tenant; and how a build's own failure should relate to the G2 approval that
triggered it.

## Decisions

### 1. TMDL emission is the platform's own pure function; commit/deploy/query are the target adapter's

§7.1's own table assigns TMDL emission to "Modeller" and commit/deploy/the post-deploy
smoke query to "Steward" and the target system — the identical split §6.1 already draws
between the Harvester (the platform) and a source adapter (the one component that knows
the source). `tmdl.emit_tmdl` is a pure function of `modeller.read_design_document`'s own
frozen output — no graph read, no clock, no random id — so "the same version always
produces byte-identical TMDL" is true by construction, not by care. A new `TargetAdapter`
Protocol (`astra_adapter.target_contract`, mirroring `SourceAdapter`'s own placement and
shape) draws the second half of the boundary: `commit`, `deploy`, `smoke_query` — nothing
about *what* to build, only *how* to reach the target system.

**No out-of-process RPC boundary for target adapters, unlike the source side.** §5.4/ADR
0013 run a source adapter out of process because its own parsing logic is untrusted,
adapter-authored code; every target adapter this story ships is platform-authored, so an
in-process `Protocol` is the honest shape today. A third-party target adapter's own RPC
boundary is real future scope this story does not build — the same "declare the contract,
a later story drives the transport" precedent §6/§7's own split from S2.1.1 already set.

### 2. Column-level schema is a disclosed gap, not a fabricated one

`design_document["tables"]` carries what S4.1.1's Modeller produces — name, schema, mode,
row estimate — never a per-table column list; no story has threaded `Field`/`MAPS_TO`
detail into the frozen design. Each table's own TMDL says so in a comment rather than
inventing columns, the same honesty the console's Measures tab already gives "class"/
"pattern" (both "pending" until the Transpiler, E5). Measures carry no DAX for the
identical reason — `candidate_measures` (S4.1.1) names a measure and what it was
deduplicated from, never a formula; each measure's TMDL is a syntactically valid DAX string
literal naming what stands in for it, a real loadable artefact today and a slot the
Transpiler fills later, not a broken placeholder.

### 3. The fixture target adapter's Git commit is genuinely real; deploy and the smoke query are disclosed stand-ins, implemented with Dulwich rather than a shelled-out `git`

No Azure AD app registration, Fabric workspace or client Git remote has ever been given to
this platform — the identical position `NullDirectoryResolver` and
`EnvironmentCredentialProvider` (Key Vault is E11's) already occupy. `FixtureTargetAdapter`
commits real Git objects to a real local repository via
[Dulwich](https://www.dulwich.io/), a pure-Python Git implementation, rather than shelling
out to a `git` binary: this platform's own runtime image states "the image carries no
shell tooling it does not need" (`Dockerfile`), and this sandbox's own build network
proved the point directly — `apt-get install git` failed outright (egress to Debian's
package mirror is blocked here), while `pip install dulwich` did not, because it is a
dependency, not an OS package the image would otherwise need a shell and a package manager
to fetch. `deploy` materializes the committed tree into a local directory — the same end
state Fabric Git integration produces, a workspace synced to a branch — and `smoke_query`
checks that a table's own file landed there rather than running a live DAX query nothing
in this environment can evaluate; `row_count`/`measure_value` are reported `None`, stated
as an honest gap, not a fabricated number. Dulwich is declared as `astra-adapter-sdk`'s one
optional extra (`[target]`) rather than a core dependency — the package's own stated
principle is "installed inside every adapter image... a dependency added here is a
dependency added to every adapter anybody writes," and a source-adapter author who never
touches the target side should not pay for it.

### 4. A build failure never rolls back the G2 decision that triggered it; a rebuild is not a state-machine transition

`routes_g2.approve_route` calls `build_family` immediately after a successful approval, on
the `agent:steward` principal (§19: "acting integrations run only through the Steward and
the target adapter"; "Steward" is not yet a role a human asserts — it is what this
codebase already calls the agent principal behind an automated action, `agent:modeller`/
`agent:cartographer`'s own footing). The call is defensive: the G2 decision already
recorded is real and stands regardless of what the downstream build does, so a build
failure is caught, logged, and never re-raised into the approval response. `build_family`
itself never raises for an ordinary failure either — `emit`/`commit`/`deploy`/`smoke_query`
failures are all recorded as a `FAILED` `BuildRecord` and returned normally; only a
genuinely illegitimate call (an unknown family, one still `DRAFT`) raises. Because `BUILT`
has no state-machine edge back to itself, a rebuild of an already-`BUILT` family — the
Build tab's own retry after a target-side hiccup, or a redeploy with nothing design-side
changed — is treated as already at the state machine's own destination rather than refused
outright; `require_transition` is consulted only when entering `BUILT` for the first time.

## Consequences

- New package boundary: `astra_adapter.target_contract` (`TargetAdapter`, `TmdlBundle`,
  `CommitResult`, `DeploymentResult`, `SmokeQueryResult`) and
  `astra_adapter.target_fake.FixtureTargetAdapter`, on the identical footing
  `contract.py`/`fake.py` already have for the source side.
- New graph-svc modules: `tmdl.py` (pure emission), `build.py` (orchestration + a new
  platform table, `public.build_run`, migration v0018 — the `g2_question`/`g2_reminder`
  "history, not a mutable row" precedent, one row per attempt).
- No ontology change — nothing here is a fact about the source or target estate; a build
  attempt's own log is bookkeeping, the same reasoning `g2_question`/`g2_reminder` already
  established.
- New routes: `POST /v1/families/{id}:build` (manual retry), `GET /v1/families/{id}/build`
  (the Build tab's own read). The Build tab (`ModelDetail.tsx`) now renders a real build
  log instead of "not built yet."
- The runtime image gains one new pip dependency (`dulwich`, via `astra-adapter-sdk
  [target]`) and no new OS package — the Dockerfile's own `apt-get git` line was tried,
  failed in this sandbox, and was replaced rather than worked around.

## Alternatives considered

**Shell out to a `git` binary, installed via `apt-get`.** Tried first — rejected once this
sandbox's own build proved the network path unreliable (a 403 from Debian's mirror), and,
independently of that failure, rejected on the image's own stated principle: no shell
tooling a pip dependency can replace.

**Thread column-level schema into `design_document` so TMDL tables carry real columns.**
Rejected — see decision 2. `design_document`'s shape is frozen at G2 submission by S4.1.2's
own version-hash discipline; enriching it is a Modeller-side story (real future scope), not
something this one should reach backward to add.

**Roll the G2 approval back (or refuse it) if the automatic build fails.** Rejected — see
decision 4. An approval is a real, independent decision a data owner made; a downstream
build failing is an operational fact the Build tab exists to surface, not a reason to
un-make what a human just did.

**Build a real Fabric REST client now, using whatever credential is configured.** Rejected
for this story. This platform has never been given a live tenant, workspace id or
service-principal credential to test one against, and shipping an integration this
codebase can never exercise would be a worse, less honest gap than the one this story
names plainly.
