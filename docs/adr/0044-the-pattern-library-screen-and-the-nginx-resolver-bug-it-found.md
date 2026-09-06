# ADR 0044 — The Pattern Library screen, and the nginx resolver bug it found

Status: accepted · 6 September 2026 · Story S5.5.3 (F5.5, closing it)

## Context

S5.5.3 closes F5.5, following S5.5.1 (ADR 0042, generalisation and promotion) and S5.5.2
(ADR 0043, automatic retirement): *"As a platform engineer, I want a Pattern Library
screen, so that I can see what the platform has learned and govern it. Lists patterns by
class and state with applications, pass/fail, first seen, provenance origin; candidates
awaiting promotion are a queue. Actions: promote, retire with reason, edit guards (creates
a new version), export."*

This is the first console (`services/console-web`) story since early F1.4 work — every
story in between (S5.1.1 through S5.5.2) was `graph-svc`-only. Two of the AC's own four
actions already had a real backend route (`:promote`, S5.5.1); two did not: "retire with
reason" (S5.5.2 only ever retires a pattern *automatically*, on a threshold, with no route
a human calls) and "edit guards (creates a new version)" (`Pattern` had no version concept
at all). Building the screen therefore meant building the missing backend surface first,
then a screen against the complete API — the same order S1.4.x's own console stories
already established (a screen is only as honest as the API it renders).

The console-web test suite's own "Docker smoke test before calling a story done" habit,
applied for the first time to an actual browser load of a console screen through its own
nginx proxy (`localhost:5173`, not `graph-svc`'s own exposed port this session has always
curled directly), surfaced a real, pre-existing infrastructure defect: every `/v1/*` call
the console ever made through its own local Docker deployment has silently 502'd since
S5.3.2, because `nginx.conf`'s own `resolver` directive was hardcoded to Azure Container
Apps' internal DNS server. No story before this one ever loaded the console through this
exact path to notice.

## Decisions

### 1. Manual retirement and guards-editing needed real, new backend routes — not a frontend-only story

`retire_pattern` (any live, non-RETIRED pattern — CANDIDATE included, unlike S5.5.2's own
ACTIVE-only automatic mechanism) and `edit_guards` (a new `Pattern` node, `version`
incremented, `supersedes_id` naming the one it replaces, the old node retired via
`GraphWriter.retire_node`) both share the exact retirement execution S5.5.2 already built
(`_perform_retirement`, refactored out here) so a manual and an automatic retirement are
identical to everything downstream — the re-queue, the event — differing only in who
decided and why. Both are gated to `PlatformEngineerDep`, reused a third time (S5.2.1,
S5.3.2, now this), matching the AC's own "govern it" naming the same persona MA-11/MA-12
already do (§13.2).

### 2. Editing guards inherits promotion_state and the point-in-time count snapshots, but starts a fresh observation ledger

Guards are descriptive text, never evaluated (§9.3, established since S5.5.1) — editing
them changes nothing about matching or rendering, so there is no reason to throw away an
ACTIVE pattern's own already-earned trust; the new version inherits `promotion_state`
directly. `pass_count`/`failure_count` are copied as their own point-in-time snapshots
(the identical footing they already have). What is **not** carried forward is
`pattern_observation` itself: the new version's own append-only ledger starts empty under
its own fresh id, an honest reading of "a new version" for an observation log whose whole
point is per-identity history — a silent merge would let one version's own proof stand in
for another's, exactly the kind of "quietly persisting as clutter" this session has
avoided everywhere else. This means a freshly-edited CANDIDATE cannot be promoted until it
re-accumulates its own evidence, and a freshly-edited ACTIVE pattern's own future failures
are judged on its own new record, not one it inherited.

### 3. `promote_pattern`/`retire_pattern`/`edit_guards` all return the same row shape `list_patterns` gives every row

Refactored `list_patterns`'s own per-row projection into `_pattern_row`/`pattern_row`
(a real, useful de-duplication, not scope creep for its own sake): before this story, the
three mutation routes each returned raw hydrated node properties — a different, wider
shape than the console's own list state, missing the live `applications`/`pass_total`/
`distinct_passing_calcs` fields the AC itself asks for. Returning the identical row shape
everywhere means the console can merge a mutation's own response straight into the list it
already rendered rather than needing a second, differently-shaped type and a bespoke
reconciliation — reflected directly in this story's own two pre-existing S5.5.1 test
assertions that needed updating (`pass_count` → `distinct_passing_calcs`; `source_signature`
moved to a direct node hydration, since the row shape does not carry an internal matching
detail the console has no use for).

### 4. The screen follows the established console conventions exactly — no new patterns invented

A list-plus-detail two-pane layout (a `.pattern-library-workspace` CSS override, the
identical "override the generic three-column grid" move `.admin-workspace` already made
for S4.3.2); the shared `estate/ReasonDialog` reused verbatim for "retire with reason"
(no bespoke duplicate); a new `EditGuardsDialog` modelled line-for-line on `ReasonDialog`'s
own focus/Escape/scrim-close behaviour, since editing needs a second field (the guards
themselves) `ReasonDialog` was never built to carry; `stateTone`-style pill colouring
(`ModelDetail.tsx`'s own precedent) for CANDIDATE/ACTIVE/RETIRED; hide-not-disable for
every governing action, exactly `Admin.tsx`'s own convention; the fake-API test double
extended with `patternRecord`/`patternsResponse` fixtures and five new `FakeApi` methods,
the identical shape `conformanceRuleset`'s own fixture already set. "Export" needed no new
route at all: the screen already holds the full, current list in state, so a client-side
`Blob`/`URL.createObjectURL` download of exactly what is already rendered satisfies the AC
without inventing server-side surface for something a browser can already do.

### 5. The queue is sorted by what proving it further actually unlocks

`distinct_passing_calcs` descending, ties broken by fewest `applications` — a candidate
closer to its own promotion threshold, or accumulating evidence faster, surfaces first.
The identical "ordered by what resolving it unlocks, not by recency" reasoning the Parse
Quality Queue (S1.4.3) already established for its own, differently-shaped queue.

### 6. The nginx resolver bug: found live, fixed at the root, not worked around

`nginx.conf`'s `resolver 168.63.129.16 valid=10s;` was Azure Container Apps' own internal
DNS server, hardcoded with no override mechanism — meaningless and unreachable outside
Azure, so every `/v1/*` proxy call through local Docker Compose's own `console-web`
container has 502'd since the line was introduced (S5.3.2's own Azure deployment
workaround). This was invisible to every story since, because every one of this session's
own Docker smoke tests curled `graph-svc`'s own exposed port directly (`localhost:8080`)
— a real, valid, spec-conformant test of the *service*, but never a test of the *console's
own proxy path*, since no story before this one needed to load a console screen against
live data through it. Fixed the same way `$GRAPH_SVC_UPSTREAM`/`$GRAPH_SVC_HOST` already
are: a third envsubst'd token, `$DNS_RESOLVER`, defaulting in the Dockerfile to the exact
value already deployed to Azure (so a rebuild changes nothing there unless the deployment
itself is also updated) and overridden in `docker-compose.yml`'s own `console-web` service
to Docker Compose's own embedded resolver (`127.0.0.11`) for local development — the
identical "default preserves existing behaviour, docker-compose.yml carries the
local-specific override" split this file already uses for `ASTRA_CONSOLE_PORT`.

## Consequences

- `patterns.py`: `PatternRetirementError`, `_perform_retirement` (shared retirement
  execution), `retire_pattern`, `edit_guards`, `_pattern_row`/`pattern_row` (shared
  single-row projection); `promote_pattern`/`retire_pattern`/`edit_guards` all now return
  via `pattern_row`.
- `ontology/nodes.py`: `Pattern.version`/`Pattern.supersedes_id` (`T.INT`/`T.STRING`),
  schema version 21 (up from 20); new `SpecDeviation` entry.
- `routes_patterns.py`: `POST /v1/patterns/{id}:retire`, `POST /v1/patterns/{id}:edit-guards`,
  both `PlatformEngineerDep`.
- No new migration: both new properties are additive, covered by `tools/migration_check.py`'s
  own additive rule.
- New console module `patterns/PatternLibrary.tsx` (the screen) and
  `patterns/EditGuardsDialog.tsx` (the guards-plus-reason dialog); `App.tsx` gains the
  `patterns` surface (its own stale doc comment, which mis-scoped Pattern Library as a
  future Admin sub-screen, corrected); `styles.css` gains the `.pattern-library-workspace`
  override.
- `lib/api.ts`: `PatternRecord`/`PatternsResponse`/`PatternProvenance`/
  `PatternPromotionStatus` types and five new `Api` methods.
- `tests/fixtures.ts`: `patternRecord`/`patternsResponse` builders, a new trailing
  `initialPatterns` parameter on `fakeApi(...)`, and the five new methods' own fake
  implementations.
- New test file `tests/patterns.test.tsx` (17 cases): reading the list/queue/detail,
  promoting, retiring with a reason, editing guards into a new version, export's own
  visibility, and hide-not-disable for every role but the Platform Engineer.
- `nginx.conf`/`Dockerfile`/`docker-compose.yml`: the `$DNS_RESOLVER` fix (decision 6).
- Two pre-existing S5.5.1 integration test assertions updated to match the `pattern_row`
  return shape (decision 3) — a real, disclosed interface change, not a regression.
- Verified live in the rebuilt Docker stack, end to end, over the console's own real nginx
  proxy (not `graph-svc`'s own exposed port): the full list with all three states and
  correct pill colouring; the promotion queue; a real 400 refusal surfaced verbatim in the
  UI when a candidate lacked enough evidence; a real manual retirement with reason,
  provenance, and the pattern disappearing from the live queue; a real guards edit
  producing a new version, the old one disappearing from the live list, a fresh
  zero-application ledger on the new one, and the full "edited from" provenance chain
  visible in the detail panel.

## Alternatives considered

**Build the console screen against the existing raw-node-shaped mutation responses,
without the `pattern_row` refactor.** Rejected — see decision 3. The console would have
needed a second, wider type and its own reconciliation logic merging a raw node's
properties into the curated list shape; the refactor is less code, not more, and removes a
real duplication `list_patterns` already had within itself.

**Reset a freshly-edited pattern to CANDIDATE regardless of its prior `promotion_state`.**
Rejected — see decision 2. Guards are documented as never affecting matching or rendering;
discarding real, already-earned ACTIVE trust for a wording change would contradict that
same story's own established discipline for no real reason.

**Merge the old version's own `pattern_observation` history into the new version's id on
an edit.** Rejected — see decision 2. The observation table's own value is that it is a
real, append-only, per-identity record; merging would let one version's own real evidence
silently stand in for a different version's own future conduct.

**Route "export" through a new backend endpoint.** Rejected — see decision 4. The screen
already holds everything a JSON export needs, fetched through the existing `GET
/v1/patterns`; a server-side export route would duplicate data the client already has for
no real benefit.

**Patch around the nginx 502s locally (e.g. curl `graph-svc:8080` directly in the browser,
bypassing the console's own proxy) rather than fixing the resolver.** Rejected — see
decision 6. That would test something other than what the story asked to be tested (a
console screen, through its own real deployment path) and would leave the actual,
already-deployed defect undiscovered and unfixed for the next story that needed it.
