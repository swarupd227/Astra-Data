# ADR 0051 — Parity case derivation: a backlog elaboration beyond §10.1

Status: accepted · 6 September 2026 · Story S7.2.1, continuing E7/F7.2

## Context

S7.2.1 continues F7.2 — spec §10.1/§14: *"As a parity engineer, I want parity cases
derived deterministically from each sheet, so that coverage is explicit and
reproducible."*

- Cases = sheet × (parameter combinations from charter enumeration strategy) ×
  (filter contexts: default, and each categorical filter's top-N values); each case has
  grain, measures, filters, parameter values and a stable id
- Charter bounds cap enumeration; combinations above the bound are recorded
  NOT_ENUMERATED on the suite
- Case count and coverage are shown on the MU page; a ParityCase is a graph node with a
  §10 schema

S7.1.1 (the Tolerance Charter) deliberately built none of §10.1-§10.6; this story is the
first to derive a real `ParityCase`. Confirmed by direct research: no story before this
one has ever written a `ParityCase`, and no `arbiter.py`/`parity.py` module exists
anywhere in this codebase.

## Decisions

### 1. §10.1 itself describes one filter context per case; the AC asks for more — implemented as a disclosed elaboration

§10.1, verbatim: *"Filter context: the sheet's filters ... resolved to concrete values
from the source's current state."* One context, not a multiplication. The spec's own
worked example ("three parameters of domain sizes 4, 3 and 2 produce up to 24 cases")
multiplies only over parameters. The backlog's own AC goes further, asking for an
explicit filter-context axis too ("default, and each categorical filter's top-N
values"). This is additive to §10.1, not a contradiction of it, so it is implemented as
the AC describes: one default context (the sheet's own harvested filters, unchanged)
plus one additional context per categorical filter's own top member values —
*additive*, not a cross-product across filters, since verifying each filter's own most
significant values is what the AC's wording describes, not exhaustive combination of
every filter against every other.

### 2. Grain and measures are resolved by name, not `ENCODES` edges — the identical fix `compositor.py` already made

`ENCODES` (`Worksheet -> Field/CalculatedField`) is declared with a required `shelf`
property whose own note names this exact story: *"parity case grain is derived from
shelf placement (spec §10.1)."* Confirmed by direct research: the real Tableau adapter
has never written this edge — only the fixture adapter does, for its own synthetic
estate. Using it would derive real cases against the demo estate and silently nothing
against a real Tableau harvest. `compositor.py` (S6.1.1) already found and fixed the
identical gap for field wells; this story reuses the same fix independently (resolving
shelf names against `Worksheet -> USES_DATASOURCE -> Datasource -> HAS_FIELD`), since
`_worksheet_field_index` is a private helper of a different epic's own module and E7 has
no other reason to depend on the Compositor.

### 3. Filters are read from real `Filter` nodes via `FILTERED_BY`, not `Worksheet.filters` JSON

Both carry the same data — `Sheet.as_properties()` writes both — but the adapter's own
comment settles which one this story reads: *"the JSON is what a screen renders without
a traversal, the nodes are what the Proof Engine walks."* This module is that walk.

### 4. The charter's enumeration bound applies to the sheet's total candidate count, not the parameter axis alone

§10.1's own worked example computes its 24-combination figure from parameters alone,
since it predates the AC's own filter-context elaboration. Since the AC's own bullet
places "combinations above the bound" immediately after describing both axes together,
`params.enumerate_max_values` is applied here to the full candidate set (filter contexts
× parameter combinations), prioritising the default filter context paired with the
default-then-most-observed parameter combination first. The excess — from either axis —
is recorded as `NOT_ENUMERATED`, exactly where the spec says it must be.

### 5. "The suite" is a relational record, not a graph node — the spec's own words

§14's own storage table gives `parity_suite` a *relational* shape (`mu_id, sheet_refs`),
under a header stating relational tables hold "platform records that are not
graph-shaped" — unlike `ParityCase`/`ParityRun`/`Verdict`, which that same table's own
column marks as real graph nodes. `public.parity_suite` (migration v0027) is one
current-coverage row per MU, recomputed (not versioned) on every derivation — a
recomputable fact, not a governed document like the Tolerance Charter.

### 6. A case's own `id` stays a server-issued ULID; `case_key` is the AC's own "stable id"

The ontology's base `id` property is a validated ULID (26 Crockford-base32 characters).
A sha256 digest cannot be reshaped into that format without inventing an encoding nobody
asked for. `ParityCase.case_key` — a sha256 digest of `(sheet_ref, grain, measures,
filter_ctx, param_values)`, computed the identical `context.canonical` way every other
content-derived key in this codebase already is — carries the AC's own "stable id"
instead, the same `ArtefactRecord.id`/`.content_hash` split (S2.4.2) applied to a graph
node. Re-deriving the same sheet against unchanged source data reproduces the same
`case_key`, so an already-live case is left alone rather than duplicated; a case whose
`case_key` no longer appears is retired — the identical "recompose retires what no
longer applies" discipline `compositor._retire_previous_report` already established.

### 7. "Shown on the MU page" anchors on `mu_ref` directly, not `ReportDefinition`

No MU page exists (F10.3, unbuilt — the identical gap every E6/E7 ADR has already
found). Unlike ADR 0049's own choice of `ReportDefinition` for documentation, this
story's real anchor is `ParityCase.mu_ref`/the suite's own `mu_ref` directly: case
derivation reads only the source side (`Worksheet`, `Filter`, `Parameter`), and a
workbook can have live cases long before any report is ever composed. Requiring a
`ReportDefinition` to exist first would wrongly gate case derivation on report
composition having already happened, which §10.1 gives no reason to require ("Cases are
derived from the source, not the target").

## Consequences

- `ontology/nodes.py`: `ParityCase.case_key` (required, additive in the sense that the
  node type has never had a live row before this story — a no-op backfill, the identical
  `ModelTable.family_ref` (v0015) precedent); schema version 27 (up from 26); one new
  `SpecDeviation`.
- New table `public.parity_suite` (migration v0027) — one current-coverage row per MU.
- New module `case_derivation.py`: `derive_filter_contexts`, `derive_parameter_
  combinations`, `derive_sheet_cases`, `compute_case_key` (pure), `derive_cases_for_
  workbook` (graph-coupled orchestration: writes only new cases, retires stale ones,
  records the suite), `CaseDerivationService` (the `Compositor`/`ToleranceCharterService`
  app.state-bound-object shape).
- New routes: `POST /v1/workbooks/{id}:derive-parity-cases` (`ParityEngineerDep`), `GET
  /v1/workbooks/{id}/parity-suite` (any Artizent role).
- `MAX_FILTER_VALUES_PER_FILTER` (5) — a bound this module had to invent, since neither
  §10.1 nor the Tolerance Charter bounds how many of a categorical filter's own member
  values become their own case.
- No console screen: the MU page is F10.3's own unbuilt future surface; coverage is real
  and queryable by `mu_ref` today.
- Verified against real PostgreSQL + Apache AGE: a real worksheet's own shelves, a real
  categorical `Filter` and a real `Parameter` combine into real cases with a real,
  stable `case_key`; re-deriving against unchanged data writes nothing new; a case whose
  source drifted away is really retired; the charter's own bound really caps the case
  count and the excess is really recorded on the suite; every new route drives its own
  real role gate — 18 new unit tests, 12 new integration tests, full suite green (1,558
  passed + 2 skipped, in the same run as the one already-flagged, pre-existing, unrelated
  `test_integration_g2_reminders.py` flake).

## Alternatives considered

**Derive filter contexts as a full cross-product across every categorical filter's own
values.** Rejected — see decision 1. The AC's own wording ("default, and each
categorical filter's top-N values") describes an additive union; a cross-product would
multiply case count combinatorially for sheets with several categorical filters, for no
benefit the AC actually asks for.

**Use `ENCODES` edges for grain/measures, since the ontology's own note names this
story.** Rejected — see decision 2. The note describes the edge's *intended* purpose,
not its actual population; the real adapter has never written it, so relying on it
would silently produce zero cases against any real Tableau harvest.

**Give `ParityCase.id` itself a content-derived value.** Rejected — see decision 6. The
base `id` property is validated as a ULID; reshaping a hash into that format would be an
invented encoding serving no real purpose the separate `case_key` doesn't already serve
more honestly.

**Anchor "shown on the MU page" on `ReportDefinition`, matching ADR 0049's own
precedent.** Rejected — see decision 7. Case derivation is a source-side concern that
can run before any report exists; requiring `ReportDefinition` first would impose an
ordering §10.1 never asks for.

## Open questions for the product owner

- Should `MAX_FILTER_VALUES_PER_FILTER` become a real Tolerance Charter field once a
  Parity Engineer actually wants to tune it, the same way `params.enumerate_max_values`
  already is?
- Once F7.3 builds real case execution, should the charter's own enumeration bound stay
  applied to the *combined* candidate count (this story's own reading), or should a
  future revision split it back into independent per-axis bounds (parameters vs. filter
  contexts)?
- Should a future story add a real `Worksheet -> ParityCase` edge (e.g. `DERIVED_FROM`),
  or does `sheet_ref` stay a plain reference indefinitely, the same footing `mu_ref`
  already has throughout this epic?
