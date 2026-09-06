# ADR 0049 — Report documentation: a deterministic ASSISTED draft, linked from ReportDefinition

Status: accepted · 6 September 2026 · Story S6.2.2 (F6.2)

## Context

S6.2.2 continues F6.2 — spec §8.8/§8.11: *"As a report owner, I want report
documentation generated from the graph, so that users get a page that says what changed
and where things moved."*

- One markdown page per report: purpose (from workbook description), pages and visuals
  with their Tableau sheet of origin, measures with source calc names, parameters, known
  differences (C4 decisions, redesigns), model and refresh
- Generated in ASSISTED mode with provenance; stored as an artefact and linked from the
  MU page

Every fact this page states was already resolved by an earlier E6 story: `Visual`/
`ReportDefinition` by the Compositor (S6.1.1), `CalculatedField`/`Measure` by the
Transpiler, C4 guidance by `redesign.py` (S5.4.1), `Visual.redesign_flag`/`.reason` and
`ExceptionCase` by S6.1.1/S6.2.1. This story is a rendering step over already-composed
evidence, not a second pass of composition.

## Decisions

### 1. `agent="compositor"`, not the Steward the spec names — a disclosed departure

§8.3's own agent catalog and §8.11's own narrative both attribute "report and model
documentation... (ASSISTED drafting, deterministic facts)" to the Steward (E9), gated at
G4. No `steward.py` module exists anywhere in this codebase, and E9 has not been built
(confirmed directly, not assumed). The backlog's own S6.2.2 sits inside F6.2, immediately
after S6.2.1, driven by "report owner," with no G4/ACCEPTED gate — the identical position
every other E6 story in this epic has already been written from. `agent="compositor"` is
recorded rather than borrowing a Steward identity that does not exist; the day E9 ships, a
real Steward can take over generation and this ADR's own departure note is what a reader
checks first.

### 2. `ContractName.COMPOSITOR_REPORT_DOC` is a third "name only" contract — never a model call

The markdown is composed entirely from graph facts by a deterministic renderer
(`report_documentation.render_markdown`) — the identical "real, reproducible template, no
inference boundary to police" footing `MODELLER_FAMILY` (S3.1.1/S4.1.2) and
`TRANSPILER_C4_REDESIGN` (S5.4.1) already established, for the same reason a third time.

### 3. "Purpose (from workbook description)" reads `Workbook.name` — `Workbook` has no `description`

Confirmed directly: §4.1.1's own node table declares no `description`, and no comment/
notes/free-text field exists on `Workbook` at all. Harvesting a real description would be
adapter-side work (`packages/adapter-tableau`), out of scope for a Compositor-layer story
— the same "a real, disclosed gap, not invented data" posture ADR 0047's own decision 6
already took for a comparable adapter-side limit. The workbook's own `name` is the one
real, human-authored fact this platform has for "what this report is," disclosed as a
proxy rather than presented as a real description.

### 4. Refresh lists every distinct schedule found, not one assumed

No edge connects a `Workbook`/`ReportDefinition`/`SemanticModel` directly to a
`Datasource` — the only real path is `Worksheet -> USES_DATASOURCE -> Datasource`
(`compositor.py`'s own docstring). Two worksheets in one report can use different
datasources with different `refresh_schedule`s; every distinct schedule actually found is
listed, with a disclosed note when there is more than one, rather than picking one and
silently hiding the rest.

### 5. Known differences read `Visual`/`CalculatedField` directly — not each visual's own `ExceptionCase`

A redesign's *work-item* state (open/closed, who closed it, the Desktop commit) is the
Migration Engineer's own tracking concern, already served in full by `GET /v1/exceptions`
(S6.2.1). This page's job is to tell a report owner *what* differs and *why*, which
`Visual.redesign_reason` and a C4 field's own `appendix_b_guidance`/`redesign_suggestion`/
`redesign_decision` already say completely, without a second lookup against a queue this
reader has no reason to open.

### 6. Generation is a deliberate, separate action — not automatic on every compose

The AC's own wording ("I want report documentation generated") names a distinct
after-the-fact step, the same shape `deploy_workbook` (S6.1.2) already took relative to
`compose_workbook` (S6.1.1): composing is cheap and iterative during mapping-rule
tuning, and generating a documentation artefact on every one of those composes would
spam the artefact store with drafts nobody asked to see. This is unlike S6.2.1's
`ExceptionCase`, which the AC phrased causally ("redesign flags *create* ExceptionCases")
and which therefore opens automatically during compose — this story's own AC has no such
causal wording, so it is not read that way.

### 7. "Linked from the MU page" is the same disclosed proxy ADRs 0045–0048 already used four times

No MU page exists (F10.3, unbuilt — the identical gap every E6 ADR has already found).
`ReportDefinition.documentation_artefact_ref`/`.documentation_provenance_ref` make the
link real and queryable today, from the one real, existing node this touches, the same
"a real fact, additive, on the one node a later story's proxy can attach to" shape
`ReportDefinition.pbir_ref`/`.deploy_state` (S6.1.2) already set on this exact node.

### 8. `Compositor` gains an optional `provenance_store` — the one dependency this story needed that none before it did

`Compositor.__init__` now accepts `provenance_store: ProvenanceStore | None = None`
alongside its existing optional `artefact_store` — an additive, backward-compatible
constructor parameter, wired from `main.py`'s already-existing `app.state.provenance_store`.
`generate_documentation`/`read_documentation` are thin wrappers the same shape
`.compose`/`.read` already are.

## Consequences

- `ontology/nodes.py`: `ReportDefinition` gains `documentation_artefact_ref`/
  `documentation_provenance_ref` (both additive); schema version 26 (up from 25); one new
  `SpecDeviation` entry.
- `context/contract.py`: `ContractName.COMPOSITOR_REPORT_DOC` — name only, no
  `ContextContract` registered, same footing as its two siblings.
- New module `report_documentation.py`: `render_markdown` (pure), `_gather_facts` (reads
  back already-composed evidence, nothing recomputed), `generate_report_documentation`,
  `read_report_documentation`, `ReportDocumentationError`.
- `compositor.py`: `Compositor` gains an optional `provenance_store` constructor
  parameter and `.generate_documentation`/`.read_documentation` methods.
- New routes: `POST /v1/workbooks/{id}:generate-documentation` (`MigrationEngineerDep`,
  the same persona `compose_workbook`/`deploy_workbook` already use) and
  `GET /v1/workbooks/{id}:documentation` (`C4RedesignReaderDep` — any Artizent role or the
  report owner, reused verbatim from `routes_redesign.py`'s own precedent for this exact
  reading persona).
- Verified against real PostgreSQL + Apache AGE: a real bound calculated-field measure,
  an open C4 calculation with no decision yet, a redesigned sheet, a real refresh
  schedule and a real semantic model all appear correctly in the rendered page; the
  artefact is really stored (kind `report_documentation`, media type `text/markdown`);
  the provenance record is really `ASSISTED`, `agent="compositor"`, no model call; the
  link survives a fresh read of the report; a regenerate produces a fresh artefact and
  moves the link; every new route drives its own real role gate (Migration Engineer to
  generate; Artizent or report owner to read; an unrelated client role refused) — 13 new
  integration tests, 14 new unit tests, full suite green (1,033 unit, up from 1,019; 448
  integration passed + 2 skipped, up from 435 + 2, in the same run as the one
  already-flagged, pre-existing, unrelated `test_integration_g2_reminders.py` flake).

## Alternatives considered

**Attribute documentation generation to a stub `"steward"` agent name, anticipating
E9.** Rejected — see decision 1. Naming an agent that does not exist yet would misstate
what actually produced the record; `agent="compositor"` is honest about which epic's own
code ran, and is easy to find and change the day a real Steward ships.

**Generate documentation automatically as part of every compose, mirroring S6.2.1's
`ExceptionCase`.** Rejected — see decision 6. S6.2.1's own AC used causal wording
("redesign flags create..."); this story's does not, and composing is iterated on far
more often than documentation should be regenerated.

**Surface each redesign's own `ExceptionCase` state (open/closed, commit hash) on the
documentation page.** Rejected — see decision 5. That is Migration Engineer work-item
tracking, already served by `GET /v1/exceptions`; duplicating it here would blur what
this page is for and require a second live lookup this reader does not need.

**Add a real `Workbook.description` property now, sourced from nothing the harvester
captures.** Rejected — see decision 3. Inventing an ontology property with no real
harvested source would be worse than disclosing the gap; the workbook's own `name` is
real data, not a guess.

## Open questions for the product owner

- Should a future story teach the Tableau adapter to harvest a workbook's own
  description (closing decision 3's own gap for real), or does `Workbook.name` remain
  the accepted proxy for "purpose" indefinitely?
- Once F10.3 builds a real MU page, should it embed this page's own rendered markdown
  directly, or only link to `GET /v1/workbooks/{id}:documentation`?
- Once E9's Steward exists, should it take over generation entirely (retiring
  `agent="compositor"` in favour of a real Steward identity), or does the Compositor
  keep generating its own report's documentation as one more thing E6 owns end to end?
