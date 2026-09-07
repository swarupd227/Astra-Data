# ADR 0052 — Manual parity cases: a named future screen, and a real mechanism today

Status: accepted · 7 September 2026 · Story S7.2.2, continuing E7/F7.2

## Context

S7.2.2 continues F7.2 — spec §10.1/§15.3: *"As a parity engineer, I want to add a
manual case with specific filters and parameters, so that an owner's 'check this one'
becomes part of the suite."*

- Manual cases are authored on the Parity Run screen, tagged MANUAL with the author,
  and persist across re-runs

S7.2.1 (immediately prior) built the first real `ParityCase` writer — deterministic
derivation from a worksheet's own shelves, filters and parameters, capped by the
Tolerance Charter's own enumeration bound. This story adds the second, human-authored
path onto the same node type.

## Decisions

### 1. "The Parity Run screen" is a real, named future surface — not a vague gap

Unlike earlier "no MU page exists" disclosures, this one has an exact shape to point at.
§15.3's own screen table: *"Parity Run | One run: cases table with verdicts, executor
strategies, timings, sampling flags; charter version; evidence bundle links."* Backlog
story S7.4.2's own AC: *"a Parity Dashboard and per-run view in plain language."* Neither
can be built before a real `ParityRun`/`Verdict` exists — F7.3's own later, explicit
scope, still unbuilt. `add_manual_case` is this story's own real mechanism instead: a
genuine, queryable `ParityCase(state="MANUAL")`, until that screen exists to author one
from — the same "the mechanism is real, the screen is later" posture every prior E6/E7
story in this epic has already taken, this time with a concrete pointer to which future
story owns the screen.

### 2. "Tagged MANUAL with the author" needed no new property

Every node already records `created_by` (`BASE_NODE_PROPERTIES`, since S1.1.1) — the
author the AC asks for was already there. `state="MANUAL"` (alongside S7.2.1's own
`"DERIVED"`) is the tag: an ordinary string on an already-optional property, not an
ontology change. Confirmed directly: this story adds zero new node properties, and
`ontology_check.py --spec`/`migration_check.py` both pass unchanged.

### 3. Grain and measures are still resolved from the real sheet, never supplied by the caller

The AC's own "specific filters and parameters" names exactly what a manual case adds.
Grain and measures are the sheet's own real dimensions and measures, resolved the
identical way any `DERIVED` case's are (`_worksheet_field_index`/
`_resolve_grain_and_measures`, S7.2.1) — a manual case still describes something
genuinely executable against the real sheet, never an invented one a caller could type
in by mistake.

### 4. "Persist across re-runs" is enforced by excluding `state="MANUAL"` from the staleness sweep

`derive_cases_for_workbook`'s own retirement logic (S7.2.1) retires a live case whose
`case_key` doesn't appear in a fresh derivation, reading that absence as "the source
drifted." A manual case's `case_key` was never produced by the deterministic derivation
to begin with — its absence from a fresh derivation's own candidate set proves nothing
about drift. The sweep now checks `state` before ever considering a case stale, so a
manual case survives every re-derivation indefinitely, exactly as the AC requires.

## Consequences

- No ontology change: schema version stays 27.
- `case_derivation.py`: `add_manual_case` (writes one `ParityCase(state="MANUAL")` for a
  real sheet, filters/parameters as authored, grain/measures resolved for real);
  `derive_cases_for_workbook`'s staleness sweep now excludes `state="MANUAL"`;
  `CaseDerivationService.add_manual_case`/`.list_cases` (the app.state-bound wrapper
  shape).
- New routes: `POST /v1/workbooks/{id}:add-manual-parity-case` (`ParityEngineerDep`,
  the same persona that owns deriving), `GET /v1/workbooks/{id}/parity-cases` (any
  Artizent role) — every live case for an MU, derived and manual alike, the real fact
  standing in for the unbuilt Parity Run screen.
- Verified against real PostgreSQL + Apache AGE: a real manual case is written with the
  real author and real, sheet-derived grain/measures; a re-derivation that would
  otherwise retire it (its own `case_key` never appears in a fresh derivation) leaves it
  alone, twice in a row; the combined case list shows both `DERIVED` and `MANUAL` cases
  together; every new route drives its own real role gate — 19 new integration tests,
  full suite green (1,565 passed + 2 skipped, in the same run as the one
  already-flagged, pre-existing, unrelated `test_integration_g2_reminders.py` flake).

## Alternatives considered

**Let the caller supply grain and measures for a manual case too, alongside filters and
parameters.** Rejected — see decision 3. The AC's own "specific filters and parameters"
names exactly two things an owner adds; inventing a caller-suppliable grain would let a
manual case describe something that isn't actually executable against the real sheet.

**Add a `ParityCase.author`/`.tagged_manual` property instead of reusing `created_by`/
`state`.** Rejected — see decision 2. Both facts the AC asks for already have a real,
existing home; adding parallel properties would duplicate data this platform already
records for every node.

**Build a minimal "Parity Run" screen now, scoped to just manual-case authorship.**
Rejected — see decision 1. The screen's own spec definition is inseparable from run
data (verdicts, executor strategies, timings) that does not exist until F7.3; a
same-named screen showing none of that would misrepresent what "the Parity Run screen"
actually is once it's built for real.

## Open questions for the product owner

- Once F7.3/F7.4 build real execution and the Parity Run screen, should a manual case's
  own authorship UI move there wholesale, or does a lighter authoring affordance stay
  available from wherever `GET /v1/workbooks/{id}/parity-cases` is first surfaced?
- Should a manual case ever be edited or withdrawn (not just added), and if so, does that
  reuse the same retirement mechanism `DERIVED` cases already have, or does a human's own
  authored case need its own, distinct removal path?
