# ADR 0050 — The Tolerance Charter: a versioned document, and a real spec role gap

Status: accepted · 6 September 2026 · Story S7.1.1, opening E7/F7.1

## Context

S7.1.1 opens epic E7 (Proof Engine and Tolerance Charter) — spec §4.4/§10/§13.1: *"As a
parity engineer, I want the Tolerance Charter as a versioned document the platform
enforces, so that 'the same result' is defined once, agreed at G1, and applied
identically to every report."*

- Charter schema per §4.4: numeric (abs and rel epsilon, rounding, currency scale),
  nulls, dates (grain alignment, timezone, fiscal year start), strings (trim, case,
  collation), ordering, rows (missing key policy, row-count tolerance), sampling, params
  (enumeration), waiver rules
- Editor in the console with inline explanation of each rule's effect; 'simulate'
  re-diffs the last run under the edited charter without executing
- Versions are immutable; G1 records the version; every ParityRun records the version it
  ran under
- Changing the charter after G1 requires the parity engineer and the client analytics
  lead and re-proves affected MUs

E7 is entirely unbuilt before this story — confirmed directly, not assumed:
`packages/adapter-sdk/src/astra_adapter/proof.py`'s own words, *"The Proof Engine is E7
and does not exist"*; no `arbiter.py`/`parity.py` module exists anywhere in this
codebase. `ParityRun`/`ParityCase`/`Verdict` were already declared in §4.1.1's own node
table and this codebase's ontology, but no story has ever written one.

## Decisions

### 1. `CLIENT_ANALYTICS_LEAD` is a real, twelfth role — closing a genuine spec-internal gap

§13.1's own gate table names G1's client-side approver as "the client analytics lead."
§2.4's own roles table — the one this codebase's `Role` enum transcribes verbatim — never
declares this role at all; confirmed by direct research, not assumed. `client_data_owner`
(G2's own approver) and `client_report_owner` (G3's) are each already spoken for by a
different gate with a different meaning, so substituting either would conflate two
approvals the spec itself keeps separate. Added as a real twelfth `Role` instead — the
same "declare it for real" call this codebase already made for `migration_architect`/
`parity_engineer`/`platform_engineer`/`migration_engineer` each the first time their own
story needed them driven, just one step earlier here since the role itself, not only its
gate, was missing.

### 2. Not a graph node — the same versioned-table template a third time

`public.tolerance_charter_version` (migration v0026) holds one immutable row per saved
version, the identical `conformance_ruleset` (v0019)/`visual_mapping_ruleset` (v0024)
template: an edit is always `version = max + 1`, never an update. §4.1.1's own node table
declares no `ToleranceCharter`, and a charter version is bookkeeping about rules, not a
fact about the source or target estate — the same reasoning `v0019`'s own docstring
already gives for its own table. §4.4 itself also says the charter is "stored in Git" —
neither of this table's own two precedents ever wrote to Git either; the identical,
already-accepted gap, not a new one this story introduces.

### 3. G1's `GateDecision` reuses G2's exact approver/countersigner shape

`GateDecision.gate` already declares `"G1"` in its own closed enum, and
`.approver`/`.approver_role`/`.countersigner`/`.countersigner_role` already exist for
exactly this "two named parties" pattern (`countersigner`'s own note: *"§13.1's
approver/countersign pairs (e.g. G2: data owner approves, Semantic Model Engineer
countersigns)"*). G1 mirrors it exactly: the client analytics lead approves (the API
caller, matching G2's own client-role-as-caller shape), the Parity Engineer countersigns
(a named string, not a second authenticated call) — the identical shape `g2.py::approve()`
already established, applied to a different gate rather than inventing a second pattern.

### 4. "Changing the charter after G1" is enforced inside `save()`, not a second endpoint

Once at least one G1 `GateDecision` exists, `save_charter` requires the caller (still only
the Parity Engineer — nobody else may ever call save) to also name the client analytics
lead's own sign-off and a reason in the same request — the AC's own "requires the parity
engineer and the client analytics lead," read as both parties represented in one
transaction rather than a second, separate approval workflow. A fresh `GateDecision(gate=
"G1")` is written recording the revision's own re-approval. The console never predicts
this requirement client-side; the two fields are always offered, and an attempt without
them when they are actually required comes back as a plain API refusal — the same "the
server is the source of truth for validity" posture `Admin`'s own save path already has.

### 5. "Re-proves affected MUs" reuses the Harvester's own `mark_for_reproof` seam

`affected_workbook_ids` walks `ParityRun.charter_version` (matching the superseded
version) through the real `ReportDefinition --PROVED_BY--> ParityRun` edge back to each
`ReportDefinition.mu_ref` — the workbook id, the literal MU identity this codebase has
used since S6.1.1. Each one found is passed to `MigrationUnitRegistry.mark_for_reproof`,
the exact existing seam `migration_units.py`'s own Harvester source-drift path already
calls, reused here for a charter revision instead of a workbook change. **This correctly
marks zero workbooks in this platform's real, current state**: no story has ever written a
`ParityRun`, so there is today no real "which MUs ran under version N" set to query — the
same "a real, honest function over real, live data, correct today even though nothing
populates it yet" posture `visual_redesign.can_enter_proving` (S6.2.1) already took before
any real MU state machine existed either.

### 6. `simulate` is a real, pure cell comparator — not a rebuild of §10.3's own diff

§10.1-§10.6 (case derivation, dual execution, the full row/key diff, sampling, visual
parity, regression scheduling) are F7.2/F7.3's own later, explicit scope — the backlog's
own next stories, not this one. What this story owns instead: `compare_numeric`/
`compare_null`/`compare_string` are the real, fully tested logic each of §4.4's own
numeric/nulls/strings blocks actually means — doubling as both the console's own "inline
explanation of each rule's effect" and the exact computation `simulate_charter` runs.
`simulate_charter` looks for the workbook's most recent `ParityRun` (via the same
`PROVED_BY` edge) and, when one exists, re-applies the comparators to each of its
`Verdict.failing_cells` under the edited charter — computed only, nothing written, the
AC's own "without executing." **Every real workbook today gets an honest "no ParityRun
exists yet"** rather than a fabricated result, since no `Verdict` has ever been written
either — proven both ways in the integration suite (absent by default; a real recompute
once a hand-built `Verdict` exists).

### 7. Waiver rules are a declared policy, not the waiver-recording mechanism

`WaiverRule` (`allowed_classes`/`requires`/`justification_min_chars`) is exactly what
§4.4's own worked example asks this story to declare. `GateDecision.decision` already
includes `"WAIVED"` and `ExceptionCase.decision` is a plain string — neither reads or
enforces this policy today; wiring a waiver decision to actually check it against the
charter is a later Exception Desk/G3 story's own scope (F8.3/§11.3), not this one's.

### 8. Reading (and simulating) is open to the client analytics lead too — found live, not designed up front

The first cut gated every read (`GET /v1/tolerance-charter`, the historical-version read,
`simulate`) on `ArtizentDep` alone, mirroring `routes_conformance.py`. Driving the console
against the real, rebuilt Docker stack surfaced the gap directly: switching to the client
analytics lead role to test the G1 approval panel showed a real 403 on the charter read
itself — a client-side role approving a document it could never read first. Fixed with
`ToleranceCharterReaderDep`, the identical "any Artizent role, or this one client role"
shape `C4RedesignReaderDep` already established for the report owner (S5.4.1) — deliberately
narrower than opening every read route to every client role, since a different client
persona (the licence admin, say) still has no reason to see a Tolerance Charter.

### 9. A dedicated top-level console surface, not an Admin sub-screen

§2.4 names "Parity Dashboard, Charter editor" as the Parity Engineer's own surfaces —
distinct from Admin, which is the Migration Architect's own single-purpose surface
(S4.3.2) for a different concern. The same call the Pattern Library (S5.5.3) already made
for an analogous situation: a spec section nominally files something under a screen that
does not exist yet, so it stands on its own rather than waiting on that screen or bolting
onto an unrelated one.

## Consequences

- `roles.py`: `Role.CLIENT_ANALYTICS_LEAD` (twelfth role, `Organisation.CLIENT`);
  `api/deps.py`: `ClientAnalyticsLeadDep`/`require_client_analytics_lead` and
  `ToleranceCharterReaderDep`/`require_tolerance_charter_reader` (decision 8).
- New module `tolerance_charter.py`: the nine schema dataclasses (`NumericRule` …
  `WaiverRule`), `ToleranceCharter`/`ToleranceCharterVersion`, `DEFAULT_CHARTER` (§4.4's
  own worked example, verbatim), `CHARTER_FIELD_METADATA`, `ToleranceCharterStore`/
  `PostgresToleranceCharterStore`, `compare_numeric`/`compare_null`/`compare_string`/
  `compare_cell`, `approve_g1`, `save_charter`, `affected_workbook_ids`,
  `simulate_charter`, and the `ToleranceCharterService` app.state wrapper.
- New table `public.tolerance_charter_version` (migration v0026); no ontology change —
  schema version stays 26.
- New routes: `GET /v1/tolerance-charter`, `GET /v1/tolerance-charter/{version}` and
  `POST /v1/workbooks/{id}/tolerance-charter:simulate` (all `ToleranceCharterReaderDep` —
  any Artizent role or the client analytics lead), `POST /v1/tolerance-charter`
  (`ParityEngineerDep`), `POST /v1/tolerance-charter/{version}:approve-g1`
  (`ClientAnalyticsLeadDep`).
- New console surface `services/console-web/src/charter/ToleranceCharter.tsx`, its own
  top-level tab; `client_analytics_lead` added to the console's own role switcher
  alongside `parity_engineer`.
- A pre-existing test (`test_every_role_in_the_specification_has_an_organisation`)
  updated from eleven to twelve roles, with its own docstring recording why.
- Verified against real PostgreSQL + Apache AGE: a saved version is really immutable and
  really versioned by insert; a real G1 `GateDecision` records the client lead and the
  countersigning engineer; a revision without the client lead's own ack is really
  refused, and with it really writes a fresh G1 decision; a real workbook proved under
  the superseded version is really found and really marked for re-proof; `simulate`
  really re-diffs a real stored `Verdict`'s own sampled cells without writing anything;
  every new route drives its own real role gate — 23 new unit tests, 24 new integration
  tests, 13 new console tests, full suite green (1,528 passed + 2 skipped, in the same run
  as the one already-flagged, pre-existing, unrelated `test_integration_g2_reminders.py`
  flake; 208 console tests, up from 195).

## Alternatives considered

**Substitute `client_data_owner` or `client_report_owner` for G1's approver, rather than
adding a new role.** Rejected — see decision 1. Each already names a distinct approval
(G2, G3 respectively); reusing either for G1 would make one role's own approval ambiguous
between two unrelated gates the spec itself keeps separate.

**Build the full §10.3 row/key diff now, so `simulate` is a complete re-proof rather than
a cell-level recompute.** Rejected — see decision 6. That is F7.2/F7.3's own later,
explicit scope; this story owns the charter's own schema and the comparison rules each
block actually means, not case derivation or execution.

**A second endpoint for "changing the charter after G1," separate from `save`.**
Rejected — see decision 4. The charter has exactly one real save path; branching it into
two routes for the same underlying action would double the surface for no real gain over
a single call that asks for a second party's sign-off only when one is actually needed.

**Bolt the Charter editor onto the existing Admin screen.** Rejected — see decision 8.
Admin is the Migration Architect's own single-purpose surface for a different concern;
§2.4 names a distinct "Charter editor" surface for the Parity Engineer.

## Open questions for the product owner

- Once F7.2/F7.3 build real case derivation and execution, should `simulate_charter`'s
  own cell-level recompute become the literal core of the real diff engine, or does §10.3
  end up needing a fuller comparator this story's own three (numeric/null/string) don't
  cover (e.g. real date-grain truncation, ordering, sampling)?
- Should a future story extend `WaiverRule` enforcement into `ExceptionCase`'s own
  closing path, the way this ADR's decision 7 anticipates?
- Does §4.4's own "stored in Git" ever get built for real, for this table and its two
  precedents together, or does the versioned-Postgres-table approach stay the accepted
  answer indefinitely?
