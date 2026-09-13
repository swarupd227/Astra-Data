# ADR 0074 — The Migration Unit page: one shared assembly, sliced by role

Status: accepted · 13 September 2026 · Story S10.3.1, opening F10.3

## Context

S10.3.1 opens F10.3 — the backlog's own AC, verbatim: *"As a migration engineer, I want
one page per report with everything about it, so that there is a single URL to send to
anyone about any report."*

- Header: state, tier, family, train, owners, gate status strip; sections per §15.4:
  Source, Artefacts, Parity, Exceptions, Gates, Provenance, Timeline
- Client roles see Source, Artefacts (thumbnails and documentation only), Parity
  (summary and verdict), Gates and Timeline
- Page loads in under 500 ms p95; artefact previews lazy-load

§15.4's own anatomy, quoted verbatim: *"One URL per report. The header carries the
state, tier, family, train, owners and the gate status strip. Below it, sections that
expand: Source (sheets, dashboards, datasources, calcs with class labels, usage,
screenshot); Artefacts (model reference, measures with source alongside and provenance
badges, report definition with page thumbnails, Git links); Parity (latest run summary,
case table, per-sheet verdict grid, evidence links, visual parity scores); Exceptions
(open and closed, with decisions); Gates (G2 inherited from family, G3 card, G4 site
status); Timeline (every event, from harvest to release, with who and what); Provenance
(every artefact's record, filterable by mode). Client roles see Source, Artefacts
(thumbnails and documentation only), Parity (summary and verdict grid), Gates and
Timeline."* §15.1's own role table names the client persona this reading serves:
**Client Report Owner**, not "client roles" generically. §15.6/§22 name the latency
budgets directly: the Migration Unit page opens in ≤500 ms overall, with its own graph
queries bounded at ≤300 ms p95.

**One Migration Unit = one Workbook, confirmed yet again.** No `MigrationUnit` graph
node exists (`migration_units.py`'s own docstring: "this is a port, not an
implementation"; `ontology/nodes.py`'s own `Workbook` note: "One Migration Unit per
Workbook"). This story's `workbook_id` is the identical MU proxy every prior G3-adjacent
story already uses (`ExceptionCase.mu_ref`, `ReportDefinition.mu_ref`,
`GateDecision.subject_ref` for G3).

## Decisions

### 1. One shared assembly, sliced for the client response — not a second, separately-worded document

`g2.client_proposal_view`'s own precedent (S4.2.1) reads a *different, calmer-worded*
document for a client screen, not the Artizent one with fields hidden — appropriate
there because a client's own business-language summary is genuinely different prose
from an Artizent design document. Nothing in the Migration Unit page needs rewording:
the parity dashboard, the G3 card and the source/artefact facts are already exactly
what `ParityDashboard`/`G3Card` show a report owner today. The only real difference is
*how much* of the same real facts a client role sees, per §15.4's own literal list.

`mu_page()` computes the full document once; `mu_page_client_view()` slices it down to
a strict subset of keys (`exceptions` omitted entirely, `artefacts.model_ref`/
`.measures`/`.git` nulled) before it ever reaches the wire. `routes_mu_page.py`'s own
`GET /v1/mu/{workbook_id}` handler picks between the two based on
`roles.is_artizent()`, computed server-side — a client caller never receives the extra
keys to begin with, the same "the server decides, not a console-side filter" posture
every other role-gated screen in this codebase already takes. Computing two, wholly
separate assemblies would mean running Source/Artefacts/Parity/Gates/Timeline's own
real queries twice per client-role request — the only way to hit this story's own
≤500 ms budget without that cost was to share the read.

### 2. Provenance is deliberately excluded, and deliberately not derived from the shared assembly either

§15.4's own client-visibility sentence excludes Provenance outright, and it is also the
one real, structurally un-avoidable fan-out on this whole page:
`ProvenanceStore.for_subject` reads one artefact id at a time, with no batch primitive
anywhere in this codebase. Keeping it off the main response — and off the ≤300 ms
graph-query budget the rest of the page has to hit — means it has to be its own,
separately-fetched, separately-lazy-loaded read: `GET /v1/mu/{workbook_id}/provenance`,
gated `is_artizent()`-only at the route (a real 403, not a narrower response, since the
whole section is absent for a client role rather than merely empty).

**It deliberately does not call `mu_page()` to get there, either.** A first draft did —
reusing the already-assembled `full` document to find the subject ids Provenance needs
(the report id, its visuals, the source calc fields, the family id, the documentation
artefact id). That would mean every Provenance request re-ran Parity/Exceptions/Gates/
Timeline's own real queries — facts Provenance never reads — purely to reach a handful
of ids. Fixed before it shipped by giving `mu_provenance()` its own, much lighter
`_provenance_subjects()` helper that collects exactly those ids directly (the calc field
list via `lineage.children`, the report/documentation via `compositor.read_report`/
`report_documentation.read_report_documentation`) — a real, deliberate divergence from
"reuse the shared assembly" specifically because Provenance's own lazy-load promise
would be broken by paying for the rest of the page just to open it.

### 3. Gates reads four different subject grains, exactly as §15.4 names them

- **G1** (Tolerance Charter) is global — `subject_ref == "tolerance_charter"`
  (`tolerance_charter.SUBJECT_REF`) — and applies identically to every MU; read once,
  not filtered per workbook.
- **G2** is keyed by this workbook's own family id (`foundry_routing.
  _family_for_workbook`, the identical cross-epic private-helper import `g3_card.py`
  already makes) — the AC's own literal "G2 inherited from family."
- **G3** reuses `g3_card.g3_card` wholesale. It already *is* the §15.5 card this
  section names — not a narrower re-read of the same facts, and not re-implemented
  here.
- **G4** is a real, disclosed *summary* — the latest live `GateDecision` for the
  workbook's own site — not the full readiness checklist `g4_card.g4_card` builds for
  the Decommission Tracker (that function needs five extra stores this page has no
  other reason to depend on). "G4 site status" is read as a status, not the fuller
  card; a reader who wants the full checklist already has the Decommission Tracker.

### 4. Evidence links, Measures/Git and Exceptions each get a real, disclosed narrower reading

**"Evidence links" in Parity** are the per-sheet screenshot references
`parity_dashboard.parity_dashboard` already returns (`source_screenshot_ref`/
`target_render_ref`) — not a second, heavier fetch of full per-case evidence bundles
(`exception_desk._gather_case_evidence`'s own job, Artizent-only and already reachable
from the Exception Desk). A narrower reading in service of the same page-open budget.

**Measures and Git links are Artizent-only within Artefacts**, per §15.4's own literal
"thumbnails and documentation only" for a client reader. Provenance badges on a measure
are not fetched inline for the same reason Provenance itself is lazy — every measure's
own source calc field is reachable through that section once a real id is known.

**Exceptions is Artizent-only** (absent from §15.4's own client-visibility list),
reusing `g3_card._workbook_exception_cases`/`_live_gate_decisions` wholesale — the
identical cross-epic private-helper precedent `g3_card.py` itself already sets for
these two functions, matched against each case's own real `decision` (open and closed
cases both included, exactly as the AC asks).

### 5. Timeline copies `regression.py`'s multi-subject raw-SQL pattern rather than looping the single-subject route

`repository.read_events`/`GET /v1/events` take one `subject` at a time. "Every event,
from harvest to release" for one MU needs several — the workbook, its family, its
train, its site, its report, every exception case id. `regression.py`'s own
`regression_monitor` already solved the identical problem with
`subject = ANY($1::text[])` against `public.estate_event` directly, in one round trip;
this module copies that pattern rather than calling the single-subject route once per
id this MU touches.

### 6. Artefact reading widened to the report owner — the same header-carrying-identity problem SSE and PDF/PPTX export already had

A client role's own "Artefacts (thumbnails and documentation only)" is only real if the
client can actually fetch the bytes. `GET /v1/artefacts/{id}/content` (and its sibling
metadata/listing routes) were `ArtizentDep`-only; a plain `<img src>` cannot carry this
console's own identity headers regardless, so the console fetches artefact bytes with
`getBlob()` (identity headers, then an object URL) — the identical mechanism ADR 0072's
SSE and ADR 0073's PDF/PPTX export already established. Widening the three GET routes
to a new `ArtefactReaderDep` (Artizent or the client report owner) was the minimal fix;
`POST /v1/artefacts` (producing one) stays Artizent-only, unaffected. The identical
"checks the bare role, no per-report ownership binding" disclosed gap
`require_g3_approver` already carries applies here too — nothing ties a
`client_report_owner` principal to the specific report whose artefacts they are asking
for, the same limitation every other client-role gate in this codebase already has.

### 7. The console's own URL is `?workbook=`, not a path segment

This SPA has no path-param router — `App.tsx`'s own `SURFACES` is a flat array matched
by an exact top-level path segment. "A single URL to send to anyone about any report"
is built the identical way `G3Card.tsx`/`ParityDashboard.tsx` already give it: a new
`mu` surface, `?workbook=` read once at mount and written back on load
(`lib/deep-link.ts`). The result, `/mu?workbook=<id>`, is one real URL that reopens the
same page — the only shape achievable without adding a router dependency this codebase
does not have. `client_report_owner` gains `mu` in `CLIENT_VISIBLE_SURFACES`
(§15.1's own "Migration Unit page (client view)" reading) without changing that role's
own existing `g3` landing surface — a deliberately narrow addition, not a
re-litigation of where that role lands by default.

## Consequences

- `services/graph-svc`: new `mu_page.py` (`mu_page`, `mu_page_client_view`,
  `mu_provenance`); new routes `GET /v1/mu/{workbook_id}`, `GET /v1/mu/{workbook_id}/
  provenance` (`routes_mu_page.py`); new `MuPageReaderDep`/`require_mu_page_reader` and
  `ArtefactReaderDep`/`require_artefact_reader` in `deps.py`; `routes_artefacts.py`'s
  three GET routes widened from `ArtizentDep` to `ArtefactReaderDep`. No migration, no
  ontology change (`ontology_check.py`/`migration_check.py` both confirm schema version
  unchanged at 37) — this story is a pure read-model aggregation over facts every prior
  story already writes.
- `services/console-web`: new `mu/MigrationUnitPage.tsx`; `lib/api.ts` gained
  `muPage`/`muProvenance`/`getArtefactContent` and their real response types
  (`MuPageResponse`, `MuPageHeader`, `MuPageSource`, `MuPageArtefacts`, `MuPageGates`,
  `MuProvenanceResponse`, ...); `App.tsx` gained a new `mu` surface with a `?workbook=`
  deep link, and `client_report_owner` gained it in `CLIENT_VISIBLE_SURFACES`.
- Verified: `services/graph-svc` — 17 new integration tests against real PostgreSQL +
  Apache AGE (an honest near-empty page for a fresh workbook, a real `ElementNotFound
  Error` for an unknown one, the header's real train/family/owner/tier once each is
  written, Source's real worksheets/dashboards/datasources/calcs, G2 read from the real
  family and G4 from the real site, G3 reusing the real card wholesale, Exceptions with
  real decisions matched, a real multi-subject Timeline, the client view proven a
  strict slice — not the full document — of the identical shared facts, Provenance
  honestly empty until a real record exists and correctly mode-filtered, five HTTP-level
  role-gate tests, and a widened-artefact-route test proving a report owner can now
  reach `.../content` while an unrelated role still cannot); the full integration suite
  re-run clean afterward (759 passed, 2 skipped, no new failures); `ruff`/`mypy`/
  `ontology_check.py`/`migration_check.py` all clean on every file this story touches.
  `services/console-web` — 400 tests passing (17 new: opening the page by search and by
  deep link, every header field including honest absences, Source/Artefacts/Parity
  rendering, Artefacts' measures/git hidden for a client role, Exceptions present for
  Artizent and entirely absent for a client role, Gates showing G1/G2/G4 summaries and
  the real G3 card, the Timeline, Provenance's own lazy fetch — proven not called until
  requested — and its own read failure, and an artefact preview's own lazy fetch); a
  real gating bug was caught before it shipped: the component's own `isArtizent` check
  first read as `!roles.includes('client_report_owner')`, which would have
  misclassified any *other* client role (client_analytics_lead, client_data_owner, ...)
  as Artizent if it ever reached this screen by a direct URL — fixed to use the
  existing `lib/roles.ts` `isArtizentRole` helper instead, the same real role-membership
  check `App.tsx` already uses; `tsc --noEmit`/`eslint`/`vite build` all clean.
- Live-smoke-tested against the real Docker stack (both images rebuilt, schema version
  confirmed at 37 on startup): `GET /v1/mu/{id}` against two real demo-estate workbooks
  returned in 78 ms and 152 ms — comfortably inside the ≤300 ms graph-query budget —
  with real facts across every section (a real site/project/family/train/owner header
  for one; a real tier, a real composed report, real generated documentation, and a
  real closed `ExceptionCase` with a real `REDESIGN` decision for the other). The
  client-narrowed response, fetched with a real `client_report_owner` identity, came
  back with the `exceptions` key entirely absent and `artefacts.model_ref`/`.measures`/
  `.git` all honestly null/empty, while `source`/`artefacts.report`/`artefacts.
  documentation`/`parity`/`gates`/`timeline` carried the identical real facts the
  Artizent response had; an unrelated client role (`client_data_owner`) was refused
  with a real 403 on both `GET /v1/mu/{id}` and `GET /v1/mu/{id}/provenance`, and the
  widened `GET /v1/artefacts/{id}/content` served a real 200 to the report owner while
  still refusing the unrelated role with a real 403. In the running console itself: the
  Migration Unit tab appeared in the report owner's own narrowed nav (three tabs total,
  matching `CLIENT_VISIBLE_SURFACES`), and its own rendered page showed no Exceptions
  section, no Measures/Git headings, and no Provenance section at all — the identical
  slice confirmed at the API layer, now confirmed rendered. Clicking "Load provenance"
  as an Artizent reader fired the lazy `GET /v1/mu/{id}/provenance` call only on click
  (confirmed via the browser's own network log — no request before the click) and
  rendered two real `ASSISTED`-mode provenance records; clicking "Show screenshot"
  fired a real, separate `GET /v1/artefacts/{id}/content` call only on click and
  rendered a real `<img>` from the fetched bytes.

## Alternatives considered

**Build `mu_page_client_view` as a second, independently-computed function** (the
`g2.client_proposal_view` shape). Rejected — see decision 1. Nothing in this page's own
client-visible sections needs rewording, only narrowing; a second full computation
would double the real query cost per client-role request against a page whose own AC
names a tight latency budget.

**Give the Migration Unit page its own path-based route** (`/mu/:id`) rather than a
query-string deep link. Rejected — see decision 7. This SPA has no path-param router
today, and adding one is a materially larger change than this story's own AC asks for;
`?workbook=` already gives "a single URL to send to anyone," the identical shape two
other screens already use successfully.

**Fetch Provenance eagerly, as part of the main `GET /v1/mu/{workbook_id}` response.**
Rejected — see decision 2. It is the one section with a real, un-avoidable fan-out
(no batch `ProvenanceStore` primitive exists), §15.4 excludes it from every client view
anyway, and the AC's own literal "artefact previews lazy-load" already establishes the
precedent of deferring the expensive, optional parts of this page.

**Build the full G4 readiness checklist into Gates**, reusing `g4_card.g4_card`
wholesale the way G3 reuses `g3_card.g3_card`. Rejected — see decision 3. §15.4's own
words ask for "G4 site status," not the fuller readiness card, and `g4_card.g4_card`
needs five stores (promotion, adoption, confirmation, regression, scope) this page has
no other reason to depend on; a reader who wants the full checklist already has the
Decommission Tracker.
