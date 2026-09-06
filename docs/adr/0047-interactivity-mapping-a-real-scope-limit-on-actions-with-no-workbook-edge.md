# ADR 0047 — Interactivity mapping: a real scope limit on Actions with no Workbook edge

Status: accepted · 6 September 2026 · Story S6.1.3 (E6, F6.1, closing the feature)

## Context

S6.1.3 closes F6.1 — spec §8.8, §7.1, Appendix B: *"As a report owner, I want parameters,
actions and interactivity carried across where Power BI supports them, so that the report
behaves the way users expect."*

- Tableau parameters become what-if parameters or slicers by type; filter actions become
  cross-filter settings; URL actions become URL fields; unsupported actions are listed on
  the MU page with the Appendix B guidance
- Interactivity mapping is recorded on the Visual node

§8.8 itself: *"...translates filters and parameters to slicers and report-level filters,
translates actions to drill-through and cross-filter settings..."* Appendix B.2's own row:
*"Dashboard actions (filter, highlight, URL) → Cross-filter/highlight settings,
drill-through, URL via conditional formatting; Parameter and set actions → C3 or C4."*
Appendix B.1: *"Parameters | Parameter references in calcs and filters | What-if parameter
tables; SELECTEDVALUE | C2."* Neither text states the exact domain→construct split the
backlog's own "by type" phrase implies; this story reads it from the two texts together.

`Parameter` and `Action` were both already declared (§4.1.1) and already harvested
(S2.3.2) — driven-for-the-first-time ground, the same trajectory every E5/E6 node type has
taken before its own story arrived. What research confirmed, and what shapes every decision
below: **neither node type carries any edge back to its own `Workbook`** — §4.1.2 gives
`Action` no containing edge at all (only `source_sheets`/`target_sheets` name strings) and
`Parameter` only `DEPENDS_ON` from a `CalculatedField` that happens to reference it.

## Decisions

### 1. A parameter is found only through a calculated field that already depends on it

The real `DEPENDS_ON` (`CalculatedField → Parameter`) edge is the *only* path this
platform has from a worksheet to a parameter — reused directly from `_resolve_worksheet_wells`'s
own already-computed calculated-field wells (S6.1.1), the same "don't re-derive what's
already in hand" discipline this module has followed since it opened. A parameter used only
through a native Tableau parameter action, or driving a filter's own threshold with no
calculation in between, is genuinely unreachable this way — a real, disclosed gap (see
`Visual.interactivity`'s own `SpecDeviation`), not a guess dressed up as a finding.

### 2. Parameters are classified by `domain`, with the real data gap disclosed rather than papered over

`domain == "list"` → a slicer (the harvested `values`/`default` are real and sufficient — a
slicer needs exactly a set of values and nothing more). `domain == "range"` → a what-if
parameter, correctly classified — but `sheets.py`'s own `Parameter` dataclass never
captures a range's own start/end/increment (only `default`/`values`, the list-domain
shape), so the mapping discloses this as a caveat rather than inventing bounds nobody
supplied. `domain == "any"` (Tableau's unconstrained domain) → unsupported: neither a what-if
parameter (no bounds) nor a slicer (no fixed members) has any real data to build from.

### 3. Highlight actions are treated identically to filter actions — the fuller, spec-consistent reading

The backlog's own AC text names only "filter actions become cross-filter settings" and
"URL actions become URL fields" — silent on highlight. Appendix B.2's own row groups filter
and highlight under one identical outcome ("Cross-filter/highlight settings"). Given the
backlog's silence is not a contradiction of the spec (it simply doesn't mention the third
case), and every `Action.type` enum value deserves an explicit, justified fate rather than
an arbitrary gap, highlight is classified as supported (`powerBiSetting: "highlight"`)
alongside filter (`"crossFilter"`) and url (`"url"`) — leaving only `parameter`/`set`
genuinely unsupported, exactly Appendix B.2's own "Parameter and set actions → C3 or C4."

### 4. `Action.name` does not exist — `ActionMapping` was corrected to not invent one

§4.1.1 declares `Action` with only `type`/`source_sheets`/`target_sheets`; there is no
`name` property (confirmed directly against the ontology and the adapter's own
`as_properties()`). An early draft of `ActionMapping` carried a `name` field anyway,
sourced from nothing real — caught before it shipped by writing the first integration
fixture and finding no property to read it from. `ActionMapping` instead carries
`other_sheets` (the sheet(s) on the opposite side of the action from the visual being
described) — real data every action genuinely has.

### 5. "Unsupported actions listed on the MU page" is the same disclosed-MU-page proxy ADR 0045/0046 already used twice — not a new `ExceptionCase`

The backlog's own F6.2/S6.2.1 (unbuilt) is what will eventually make a redesign flag a real
`ExceptionCase` of class `VISUAL_REDESIGN` routed to a real Exception Desk — neither the
failure class nor the console screen exists yet (confirmed directly: `_FAILURE_CLASSES`
has no `VISUAL_REDESIGN` member anywhere in code, and no console/`apps/`-level directory
exists in this repository at all). Minting `VISUAL_REDESIGN` now, or claiming a route to
"the Exception Desk," would reach into a later story's own explicit scope — the identical
trap ADR 0045 already named and avoided for `redesign_flag`, and ADR 0046 for "the MU
page" itself. Every unsupported parameter/action is instead recorded directly on
`Visual.interactivity`, satisfying the AC's own second bullet ("recorded on the Visual
node") for both halves of the first bullet at once, until F10.3's own MU page exists to
read it.

### 6. Actions are matched to a worksheet by a global name scan — a real, disclosed cross-workbook collision risk

With no edge from `Action` to `Workbook`, finding "this workbook's own actions" means
scanning every live `Action` node and matching `source_sheets`/`target_sheets` against this
workbook's own worksheet names — the identical name-matching trust `Dashboard.
contained_sheets` already places in strings, extended to a node type that, unlike
Dashboard's own sheets, has no scoping at all. This is a real limitation, not a
hypothetical one: this story's own integration suite demonstrated it directly — a shared
test-database graph accumulating multiple fixtures that each name a sheet "Bar sheet"
caused one test's own action query to return entries from every other test's fixture too,
forcing the test itself to assert "the expected entries are present" rather than "these are
the only entries." A real Tableau estate with two workbooks that happen to share a
worksheet name would see the identical cross-attribution. Fixing it for real needs a new
containing edge from `Action`/`Parameter` to their own `Workbook` — an adapter-side
ontology change (`fragments.py`, S2.3.2's own territory) out of scope for a Compositor
story; flagged as a real, disclosed gap rather than silently accepted.

## Consequences

- `ontology/nodes.py`: `Visual.interactivity` (`T.JSON`, additive), schema version 24 (up
  from 23); new `SpecDeviation` entry naming both the parameter-discovery and the
  action-matching limitations explicitly.
- `compositor.py`: `ParameterMapping`/`ActionMapping` dataclasses, `classify_parameter`/
  `classify_action` (pure), `_worksheet_parameters`/`_gather_workbook_actions`/
  `_worksheet_action_mappings` (graph-reading), wired into `compose_report`'s own
  per-worksheet loop alongside field-well resolution.
- `pbir.py`/`schemas/pbir/visual.schema.json`: `interactivity` is now a required key on
  every emitted visual document (`{parameters: [...], actions: [...]}`, both always
  present, empty when none found); `validate_pbir` gained two more warning classes
  (unsupported parameter, unsupported action) on the identical "warning, not an error"
  footing the unresolved-field-well check already has.
- No console screen: the same "the MU page" is F10.3's own unbuilt future screen finding
  ADR 0045/0046 already made.
- Full suite green: 1,010 unit (up from 993, +17 new `test_compositor.py`/`test_pbir.py`
  cases); 420 integration + 2 skipped (up from 418, +2 new `test_integration_compositor.py`
  cases, both written to tolerate, and directly demonstrate, decision 6's own disclosed
  cross-workbook limitation) — one already-flagged, pre-existing, unrelated
  `test_integration_g2_reminders.py` flake, unaffected by this story.

## Alternatives considered

**Invent a `name` field for `Action` from something else (e.g. a synthetic
`f"{type} action"` label).** Rejected — see decision 4. `ActionMapping.other_sheets` is
real, identifying data; a fabricated name would look like harvested fact and isn't.

**Classify highlight actions as unsupported, matching the backlog AC's own literal
silence.** Rejected — see decision 3. Appendix B.2 explicitly gives highlight the same
outcome as filter; treating it as unsupported would contradict a spec sentence the backlog
never actually disputes, only omits from its own summary.

**Guess plausible what-if parameter bounds for a range-domain parameter** (e.g. derive
min/max from `current_values_seen`, when present). Rejected — see decision 2. `sheets.py`'s
own `Parameter` dataclass does not populate `values` for a range domain at all (only for
`list`); there would be nothing real to derive bounds from even if this were attempted, and
Tableau's own range parameters can carry values well outside anything ever observed in
practice, unlike a list domain's own closed set.

**Route unsupported actions to a real `ExceptionCase`, minting `VISUAL_REDESIGN` early.**
Rejected — see decision 5. Pre-empts F6.2/S6.2.1's own explicit, not-yet-built scope for a
console surface (the Exception Desk) that does not exist to receive it.

**Add a `Workbook → Action`/`Workbook → Parameter` containing edge now, to fix decision 6
for real.** Rejected for this story. A genuine, worthwhile fix — but it is an adapter-side
ontology change (`packages/adapter-tableau/src/astra_adapter_tableau/fragments.py`,
S2.3.2's own territory) with its own knock-on surface (every existing harvested estate would
need a re-harvest to backfill the edge), not something a Compositor-layer story should
reach into unilaterally. Flagged as a real, disclosed gap instead.

## Open questions for the product owner

- Should a follow-up story add the missing `Workbook → Action`/`Workbook → Parameter`
  containing edges (decision 6), closing the cross-workbook collision risk for real? This
  would need `fragments.py` (S2.3.2's own adapter code) and a re-harvest of any existing
  estate to backfill.
- Once F6.2/S6.2.1 builds a real `VISUAL_REDESIGN`-class `ExceptionCase`/Exception Desk,
  should this story's own unsupported parameters/actions also be routed there, in addition
  to (or instead of) staying on `Visual.interactivity`?
