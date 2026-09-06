# ADR 0045 — The Compositor: visual mapping as data, a disclosed-subset PBIR schema

Status: accepted · 6 September 2026 · Story S6.1.1 (E6, F6.1, opening the epic)

## Context

S6.1.1 opens Epic E6, the Compositor — spec §8.8, §7.1, Appendix B: *"As a migration
engineer, I want each Tableau sheet mapped to a Power BI visual type with encodings and
filters translated, so that the generated report is structurally the same report."*

- Mapping table from Appendix B (mark type × encodings → visual type) is data, versioned,
  and editable by the architect
- For each sheet the Compositor emits a Visual with type, field wells bound to model
  columns and measures (through MAPS_TO), sort, and visual-level filters; dashboard
  containers become report pages with layout preserved at the container level
- Sheets whose mark type has no mapping are emitted as a placeholder visual with
  `redesign_flag: true` and the reason
- PBIR output validates against the published PBIR JSON schema before commit

`ReportDefinition`/`Visual` were already declared in the ontology (§4.1.1), and `MAPS_TO
Worksheet→Visual` already declared as this story's own edge to write (§4.1.2) — this is
driven-for-the-first-time ground, the same trajectory `Pattern`/`Measure` took before E5,
not new ground. `Field→ModelTable` MAPS_TO edges, by contrast, have never been written by
any story in this codebase (`generation.py`'s own disclosed finding, confirmed unchanged);
`CalculatedField→Measure` edges are real, written by the Transpiler since S5.1.1. No
Page/Container node exists, and `Visual` had no layout-carrying property. F6.1's own later
stories (S6.1.2, commit/deploy; S6.1.3, parameters/actions/interactivity) and F6.2 (redesign
flags routed to the Exception Desk; report documentation) are explicitly out of scope here.

## Decisions

### 1. The mapping table is keyed on the raw Tableau mark type, not a synthetic Appendix B.2 row

Appendix B.2 is an *excerpt*, grouping several "Show Me" categories (crosstab, highlight
table, scatter/bubble, KPI/BAN) under headings that are not themselves values the adapter
records — `sheets.py`'s own `_mark_type` reads Tableau's literal, lowercased `<mark
class="...">` attribute (`"bar"`, `"line"`, `"circle"`, … or `"automatic"` when absent).
Keying the versioned table on that one real field, and recovering Appendix B.2's finer
categories from the *encodings* (`compositor.resolve_visual`) on top of the base lookup, is
the literal reading of "mark type × encodings → visual type" this codebase's own data can
actually support — not a two-dimensional table Appendix B.2 never supplies either.

### 2. The mapping table follows `conformance_rules.py`'s own template, not `redesign.py`'s

Three candidates existed for "a mapping table that is data, versioned, and editable by the
architect": `redesign.APPENDIX_B_GUIDANCE` (a hardcoded dict — data, but not versioned or
editable), `model_gateway_policy` (append-only eval history — the wrong shape entirely), and
`conformance_ruleset` (S4.3.2 — exactly this shape: a `jsonb` column, one row per saved
version, an edit is always a new version). `visual_mapping.py`/migration v0024/
`routes_compositor.py` mirror `conformance_rules.py`/v0019/`routes_conformance.py` line for
line, including its `Role.MIGRATION_ARCHITECT`/`MigrationArchitectDep` gate (§2.4: "Owns
target architecture and conformance rules") and its one accepted `SIM117` lint pattern
(nested `async with`/`transaction()`), now two files sharing it rather than one.

### 3. Fields are resolved by name against the worksheet's own datasources, one step further than the Cartographer's own workaround

§4.1.2 declares `ENCODES` (Worksheet→Field/CalculatedField, with a `shelf` property); the
adapter has never written it (S3.1.1's own finding, confirmed still true). The
Cartographer's own workaround (`cartographer.py`) only ever compares shelf *name strings*
for similarity scoring — it never needs a real node id. This story does: a field well needs
a real id to walk `MAPS_TO` from. `_worksheet_field_index` resolves each shelf name against
the worksheet's `USES_DATASOURCE → HAS_FIELD` reach (first match; a `Field`/`CalculatedField`
name collision resolves to the `CalculatedField`, an arbitrary but disclosed tie-break).

### 4. "Bound through MAPS_TO" is honestly half-real, and the honesty is structural, not a guess

A calculated-field well resolves against real `CalculatedField→Measure` MAPS_TO edges (the
Transpiler writes them from S5.1.1 onward). A plain-field well's `Field→ModelTable` MAPS_TO
edge has never been written by any story — `_resolve_bindings` queries for it for real
either way (so the moment a future story starts writing it, this code finds it with no
change), and reports `bound: false` with the specific reason when it is absent, exactly
`generation.py`'s own "disclosed empty, not guessed at" posture for the identical gap.
Guessing a binding by matching a `Field`'s name against `ModelTable`/`Table` names was
considered and rejected — see Alternatives.

### 5. Appendix B.2's finer categories are recovered by a deterministic refinement function, not more table rows

`resolve_visual` looks up the base target by (effective) mark type, then refines it using
the resolved wells' own roles: a colour encoding on a bar promotes clustered→stacked; a
measure on the columns shelf (Tableau lays columns out horizontally) rather than rows
promotes column→bar; two or more measures on rows promotes line/area→a dual-axis combo,
flagged for review per the appendix's own note; both a rows and a columns shelf populated
on a `"text"` mark promotes table→matrix (crosstab); a size encoding on a scatter is noted
as a bubble chart. `"automatic"` (Tableau's own "choose from the shelves" mark, `sheets.py`'s
own comment: *"the Compositor will have to make the same choice"*) is resolved to an
effective mark (a single measure with no dimension → `"card"`, Appendix B.2's own KPI/BAN
row) before the table lookup runs at all. This is code — like `conformance_rules.RULES` — 
sitting beside the versioned data it refines, the same split that file already draws.

### 6. Every sheet gets a page — a dashboard's own zones, or its own single-visual page

A worksheet placed in one or more dashboards gets one `Visual` per placement (a Tableau
sheet reused across dashboards has no single shared visual in PBIR — each page owns its own
visual instances); a worksheet contained in no dashboard gets its own page, named for itself,
so nothing in a workbook is silently dropped from "the generated report is structurally the
same report." `Visual.layout` (new, additive, `Visual.layout`) carries the x/y/width/height
of the dashboard zone whose own name matches the sheet — a plain read of `Dashboard.
layout_json`'s zone tree, not a second layout engine; absent for a standalone sheet's own
page (nothing to preserve) and for a placeholder (§8.8's own "small model proposing a grid"
collision resolution is unbuilt, future scope this story does not attempt, since a compose
that only ever adds one visual per zone never collides).

**Found live, composing a real fixture-harvested workbook over HTTP (not caught by the
integration suite's own hand-built fixture data, which used the real Tableau adapter's own
documented zone shape): the fixture *source* adapter (`astra_adapter.fake.source`) writes
`Dashboard.layout_json` as `{"zones": [{"sheet": ref}, ...]}` — a dict wrapping a list of
bare sheet references with no geometry at all — not the real Tableau adapter's own bare
list of `{type, name, x, y, w, h, children}` zone dicts (`sheets.py`'s `Dashboard.
as_properties`). Iterating the fixture's own dict directly walked its *keys* (the string
`"zones"`) as if each were a zone, crashing with `AttributeError: 'str' object has no
attribute 'get'` on the very first real compose attempted against harvested data. Fixed at
the root with `_zone_list` (accepts either shape, or neither, and defensively skips a
non-dict zone during the walk) rather than only guarding the one crash site — the fixture's
own zone entries carry no geometry regardless, so the honest outcome either way is
`layout: None`, not a fabricated rectangle. Fixing the fixture generator itself, so a local
demo estate can show real preserved geometry, is real, disclosed, out-of-scope follow-up
work — a cross-cutting change to a shared fixture module used by every earlier console/API
story's own tests, not something to touch inside this one.**

### 7. PBIR validation is a real, self-authored, disclosed-subset schema — not Microsoft's own published one

§7.1 names "the published PBIR JSON schema"; Microsoft's real schema is real, external,
versioned tooling this platform has not vendored or pinned to a version — the identical
honesty this codebase already applies everywhere it cannot reach a real external system for
real (no live Fabric workspace exists for `tmdl.py`/`build.py` to validate against either).
`schemas/pbir/{report,page,visual}.schema.json` are real JSON Schema documents, checked with
`jsonschema` (new dependency), covering exactly the subset of PBIR structure this
Compositor emits today. Vendoring Microsoft's own schema is real, disclosed future work.

### 8. The whitelist and binding checks are real, but split into errors and warnings

§7.1 names two more checks beyond schema validation: a visual-type whitelist and "a binding
check that every field reference resolves in the model." Both are cheap to add at the same
gate and are already real facts this module holds (`resolve_visual` already decided the
type; every well already carries `bound`). The whitelist is a real error, both at compose
time (`pbir.validate_pbir`) and — found while testing — at *save* time on the ruleset route
too, so an architect gets the refusal immediately rather than the next engineer's compose
failing on it. The binding check is a **warning**, not an error: because `Field→ModelTable`
edges are universally absent today (decision 4), treating every plain-dimension well as a
schema failure would mark nearly every real report `INVALID` for a platform gap nothing in
this story caused. A schema/whitelist failure refuses the whole compose before anything is
written (mirroring `build.py`'s own "conformance runs before commit, not after"); a binding
warning is carried in the response instead.

### 9. A re-compose replaces the workbook's whole report

The identical starting posture the Modeller (S4.1.1) and Cartographer (S3.1.1) each had
before their own override stories — no "engineer has edited this" pin exists yet for a
`ReportDefinition`. Since no edge exists from a `ReportDefinition` to its own `Visual`s
(`MAPS_TO` is declared only as `Worksheet→Visual`), a workbook's prior visuals are found the
same way a fresh compose finds its worksheets first: `Workbook → CONTAINS → Worksheet →
MAPS_TO → Visual`.

## Consequences

- New modules: `visual_mapping.py` (`MappingRule`, `VisualMappingRuleset`,
  `DEFAULT_MAPPINGS` transcribed from Appendix B.2, `PostgresVisualMappingRulesetStore`),
  `compositor.py` (`resolve_visual`, well resolution, `compose_report`, `read_report`,
  `Compositor`), `pbir.py` (`emit_pbir`, `validate_pbir`, `VISUAL_TYPE_WHITELIST`), and
  `schemas/pbir/*.schema.json`.
- `ontology/nodes.py`: `Visual.layout` (`T.JSON`, additive), schema version 22 (up from 21);
  new `SpecDeviation` entry. `docs/generated/ontology.md` regenerated.
- Migration v0024: `public.visual_mapping_ruleset`, the identical shape v0019's
  `conformance_ruleset` already set.
- New routes: `GET`/`POST /v1/compositor/visual-mappings`, `POST /v1/workbooks/{id}:compose`,
  `GET /v1/workbooks/{id}:report`. `Role.MIGRATION_ARCHITECT`/`MigrationEngineerDep`/
  `ArtizentDep` all reused, none new.
- New dependency: `jsonschema>=4,<5` (`pyproject.toml`), plus a `mypy` override
  (`ignore_missing_imports`) matching the existing `asyncpg.*` one.
- No console screen: F6.1/F6.2/F10.x name none for the Compositor (confirmed by direct
  research against the full backlog text, not assumed) — API/engine only, matching S5.1.1's
  and S5.5.1's own precedent for a mechanism-first story.
- Verified against real PostgreSQL + Apache AGE: a real Dashboard/Worksheet/Field/
  CalculatedField/Measure graph composes into real `Visual` nodes with resolved,
  MAPS_TO-bound field wells, dashboard zone geometry preserved, an unmapped mark type
  correctly placeholdered without blocking the rest of the report, a re-compose retiring
  every previous visual, and every route's own role gate — 20 new integration tests, 42 new
  unit tests, full suite green (993 unit, up from 951; 400 integration + 2 skipped, up from
  380 — one already-flagged, pre-existing, unrelated `test_integration_g2_reminders.py`
  flake, unaffected by this story). Also verified live against the running Docker stack's
  own real fixture-harvested estate — see decision 6's own finding.

## Alternatives considered

**Guess a `Field→ModelTable` binding by matching names against `ModelTable`/`Table`
nodes.** Rejected — see decision 4. This platform's own standing practice (S5.3.1's
`model_ctx.tables=[]`, S4.3.1's TMDL column gap, every MU-shaped gap since S5.4.1) is to
disclose an absent real link rather than fabricate one from a heuristic; a name match would
also be silently wrong the moment two tables share a column name, with nothing to warn a
reviewer it was ever guessed.

**Treat an unresolved field binding as a schema error, refusing the whole compose.**
Rejected — see decision 8. Given decision 4's own finding, this would mark nearly every
real workbook's report `INVALID` today for a gap this story did not create and the compose
step cannot fix, which says "this report is malformed" about a report whose actual PBIR
structure is fine.

**A two-dimensional (mark type, encoding signature) mapping table as the versioned data
itself**, rather than a base lookup plus a code-side refinement. Rejected — see decisions 1
and 5. Appendix B.2 itself never provides encoding-keyed rows to transcribe, and enumerating
every real encoding combination as data rows would still need code to compute which row
applies — the refinement function is that code either way; making it explicit is more
honest than pretending the data table alone answers "mark type × encodings."

**Vendor Microsoft's own published PBIR schema now**, rather than a disclosed subset.
Rejected for this story — see decision 7. It is real work (licence and version-pinning
research this story did not have grounds to resolve unilaterally) better raised as an open
question than done hastily; the disclosed-subset schema is real and enforced in the
meantime, not a stub.

**Emit one shared `Visual` per Tableau sheet even when it appears on multiple dashboard
pages.** Rejected — see decision 6. PBIR has no "visual referenced from two pages"
concept; a shared node would need an invented page-scoping mechanism nowhere in the
declared ontology, for a case (page reuse) that will need its own answer whenever it
actually arises rather than one guessed at now.

## Open questions for the product owner

- Should the visual-mapping ruleset ship a minimal Admin console tab now, the way
  `conformance_ruleset` got one alongside its own S4.3.2 story? Nothing in F6.1/F6.2/F10.x
  claims this screen, so the table is real, versioned, editable data via API today — but
  not yet clickable by a non-technical architect.
- Is treating every "Gantt"/"Bullet, box plot, histogram"/density mark as flagged-for-
  redesign *by default* (no per-tenant "client approved this custom visual" flag anywhere
  in this platform) the right default indefinitely, or should F6.2/a later story add a
  per-client override?
- When should vendoring Microsoft's own published PBIR schema (decision 7) actually happen
  — before S6.1.2 (commit/deploy) reaches a real Fabric workspace, or later?
