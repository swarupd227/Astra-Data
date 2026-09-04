# ADR 0034 — A semantic model version lives on its own node; ModelFamily.state tracks the newest

Status: accepted · 4 September 2026 · Story S4.3.3 (E4 / F4.3)

## Context

S4.3.3 asks for the third piece of Build: *"As a model engineer, I want a second version of
a published model to be produced without breaking released reports, so that a Mender repair
or a design change does not regress what is live. Change request on a PUBLISHED family
creates a DRAFT v(n+1); v(n) stays PUBLISHED until v(n+1) passes regression on all released
MUs bound to it. Promoting v(n+1) marks v(n) DEPRECATED with the date; the console shows
both."*

Every prior Modeller story (S4.1.1 through S4.3.2) built on one silent assumption: a family
has exactly one live design at a time. `ModelFamily.state` is a single field; every read
(`read_design_document`, every G2/build action) has always meant "the" `SemanticModel` for a
family because there was only ever one. §12.2's PUBLISHED row is the entire spec text on the
mechanic this story drives: *"In test/prod; regression suites attached | Change request →
DRAFT (new version); DEPRECATED at retirement."* No `v(n)`/`v(n+1)` numbering exists in the
spec — that notation is the backlog's own invention, and no Migration Unit registry or
regression-suite execution exists anywhere in this codebase to check against.

## Decisions

### 1. Per-version lifecycle moves onto `SemanticModel.state`; `ModelFamily.state` keeps meaning "the newest/most in-progress version"

`SemanticModel.state` has been declared since S1.1.1 for exactly this split — "Deployment
state within an environment; the family lifecycle is on ModelFamily.state" — but nothing
had ever driven it until now, because nothing needed to: family and version state were the
same fact under one version. The moment a family can have a PUBLISHED v(n) and a DRAFT
v(n+1) alive at once, that single fact splits in two. `ModelFamily.state` keeps tracking
whichever version is newest — the one every existing G2/build action already means by "the"
design — so **zero changes** were needed to `accept_family`, `submit_for_review`, `approve`,
`request_changes`, or `build_family`; they operate on "the current version," and
`read_design_document`'s own version-resolution logic (decision 2) is what makes "current"
resolve correctly once two versions can coexist.

### 2. "Current" becomes "latest by `version_number`," not "first found" — a real bug this story's own precondition exposed

`read_design_document` used `next()` first-match lookup by `family_ref`. This was correct
by accident: with exactly one `SemanticModel` per family, first-match and latest-match were
the same query. It stops being well-defined the instant a second version exists. Fixed by
sorting candidates by `version_number` (absent treated as 1, since every family had exactly
one version before this story could ever produce a second) and taking the highest — plus an
optional `semantic_model_id` parameter so the Versions tab can read a *specific*, possibly
DEPRECATED, version without disturbing which one every edit/build action still means by
default.

### 3. `ModelTable.semantic_model_ref` disambiguates table ownership; `family_ref` alone stops being enough

A `ModelTable` was only ever looked up by `family_ref` because only one `SemanticModel` per
family ever existed to own it. Two live versions means two sets of tables can share a
`family_ref`. `semantic_model_ref` (new, additive) is written on every table going forward;
`read_design_document`'s table filter matches it first and falls back to `family_ref`-only
matching for tables written before this story (safe, because those families only ever had
one version to begin with — the fallback is exact, not approximate).

### 4. A change request deep-copies the design, not the graph node

`request_new_version` copies the PUBLISHED `SemanticModel`'s own design fields and every one
of its `ModelTable`s under fresh IDs, remapping `design_document["relationships"]`'s
`from_table`/`to_table` references through an old-id→new-id map. v(n)'s own node is never
written to — it keeps `state="PUBLISHED"` and every property it already had, byte for byte,
for exactly as long as it is live. This is the load-bearing half of "does not regress what
is live": nothing about the copy can touch the original, because nothing about the copy
*references* the original.

### 5. `regression_status` is an honestly vacuous gate, not a fabricated one — the same posture `harvest.UngatedPromotions` already set

"v(n+1) passes regression on all released MUs bound to it" names two things that do not
exist in this codebase yet: a Migration Unit graph node (§3.1's own words — "a record of
work... the control plane, which E3 builds when the Cartographer starts creating MUs" —
nothing has ever driven the §3.2 state machine a "released" MU would need) and a regression
suite that can actually re-run and verdict (§10.6, F7.7/E7, not built). With zero released
MUs bound to any family today, "passes regression on all released MUs" is vacuously true —
an honest reading of real platform state, not a placeholder pretending to check something.
The day a real MU registry reports a released MU bound to a family, this fails closed
instead of fabricating a verdict nothing here can compute; `promote`'s own route checks it
before every deploy.

### 6. Promotion deploys for real before any state changes — orchestrated at the route layer, not inside `model_lifecycle`

`promote_family` (in `model_lifecycle.py`) does only state and DB writes: it marks the
current version PUBLISHED, its predecessor (if any) DEPRECATED with the date, and advances
`ModelFamily.state`. It does not import `TargetAdapter` — `g2.py`/`build.py` already import
`model_lifecycle.py` and never the reverse, and this story does not break that one-directional
layering. The actual "deploy to prod" call — reusing the exact `TargetAdapter.deploy()`
contract S4.3.1 built, against a new `target_workspace_published` config value — lives in the
`promote` route: it checks `regression_status`, requires the family's latest build to be
`SUCCEEDED` with a `git_ref`, deploys, and only calls `promote_family` once that deploy
actually succeeds. Nothing is ever marked PUBLISHED on the strength of a state flip alone.

## Consequences

- New ontology properties (schema version 15 → 16, additive, no migration entry):
  `SemanticModel.version_number`/`.published_at`/`.deprecated_at`,
  `ModelTable.semantic_model_ref`/`.schema`/`.mode_reason`/`.row_estimate`/`.custom_sql`.
- **A real, pre-existing bug found and fixed in passing**: `Modeller._write` had never
  persisted `schema`/`mode_reason`/`row_estimate`/`custom_sql` onto `ModelTable` since
  S4.1.1, despite `TableCandidate` computing all four — every build since S4.3.1 silently
  emitted TMDL without schema qualifiers, and the console always showed "—" for row
  estimates. Fixed at the source because this story's own table-copy logic needed to read
  those fields faithfully to be correct in the first place.
- New functions in `model_lifecycle.py`: `RegressionStatus`/`regression_status`,
  `list_model_versions`, `request_new_version`, `promote_family`. `FAMILY_TRANSITIONS
  ["PUBLISHED"]` changes from `frozenset()` to `frozenset({"DRAFT"})` — the first story to
  drive that edge since it was declared.
- New routes: `GET /v1/families/{id}/versions`, `POST /v1/families/{id}:request-new-version`
  (`SemanticModelEngineerDep`), `POST /v1/families/{id}:promote` (`SemanticModelEngineerDep`);
  `GET /v1/families/{id}/design` gains an optional `semantic_model_id` query parameter.
- New config: `target_workspace_published` (env `ASTRA_TARGET_WORKSPACE_PUBLISHED`, default
  `"prod"`) — the same one-name-is-the-honest-floor posture `target_workspace` already has.
- Console: a sixth "Versions" tab on Model Detail — a list (version, state, generated,
  published, deprecated dates), not a diff view (§15.3.2's fuller "Versions (hash, approver,
  diff)" tab is later, unclaimed scope); "Request new version" shown only while PUBLISHED,
  "Promote to PUBLISHED" shown only while BUILT, both gated to `semantic_model_engineer`.

## Alternatives considered

**A `ModelVersion` wrapper node distinct from `SemanticModel`, with `SemanticModel` staying
single-instance-per-family.** Rejected. `SemanticModel.state` already existed, declared and
undriven, for exactly this purpose since S1.1.1; adding a second node type to hold what one
already-declared property was built to hold would be a parallel structure duplicating a
fact this ontology already has a home for.

**Keep `ModelFamily.state` as the only state, and represent "v(n) PUBLISHED, v(n+1) DRAFT"
some other way (a list property, a separate flag).** Rejected — see decision 1. A single
field cannot represent two independent per-version lifecycles without either losing
information or reintroducing exactly the wrapper node the first alternative already
rejected; splitting the fact onto the node it is actually about is the direct fix.

**Fabricate a regression check against an empty/synthetic MU set so the acceptance
criterion "looks" enforced.** Rejected — see decision 5. This platform has no MU registry
and no regression executor; a gate that always reports "checked, passed" over data that was
never checked is worse than one that says plainly it has nothing to check yet, and it would
have to be silently redefined the day real MUs exist instead of failing closed on arrival.

**Have `promote_family` call `TargetAdapter.deploy` directly.** Rejected — see decision 6.
`model_lifecycle.py` is imported by both `g2.py` and `build.py`; giving it its own import of
`TargetAdapter` (or of `build.py`, to reuse its deploy step) would create either a new
cross-cutting dependency or a cycle. The route layer already orchestrates exactly this shape
for `build` (S4.3.1); promotion reuses the same seam rather than inventing a new one.
