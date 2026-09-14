# ADR 0076 — The Decision Register is one node type, not two tables

Status: accepted · 14 September 2026 · Story S10.4.2

## Context

S10.4.2 — the backlog's own AC, verbatim: *"As an auditor, I want a Decision Register of
every gate decision and adjudication, so that I can answer 'who approved this and on
what evidence' without asking anyone."*

- Register lists GateDecisions and adjudications with approver, countersigner, evidence
  references, rationale, timestamps; search and filter; export to CSV and signed PDF
- Each row opens the evidence bundle

§15.3.6's own row, verbatim: *"Decision Register | All GateDecisions and adjudications
with approver, evidence, rationale; search and export. | Open evidence; export."* §4.5's
own "Evidence Chain" section names the exact question this register answers: *"who
approved this report and on what evidence"* — word for word this story's own AC.

**"Auditor" is not one existing role — the identical real gap S10.4.1 found for "data
owner."** §2.4's own eleven roles, plus `CLIENT_ANALYTICS_LEAD` (S7.1.1, closing a real
gate-approver gap), name no auditor, confirmed by direct research. Unlike the Gate
Inbox, this is not a per-gate approver a workflow structurally needs — it is a
cross-cutting read, so `roles.py`'s own bar for adding a new role ("a gate genuinely
needed one") does not apply here.

## Decisions

### 1. "GateDecisions and adjudications" are the same ontology node — this register reads one label, not two

Confirmed by direct reading of `ontology/nodes.py`'s own `GateDecision.decision` enum
note: the Exception Desk's own four remediation outcomes (`PATCHED`, `REDESIGN`,
`MODEL_DEFECT`, `SOURCE_DEFECT` — §11.3's "adjudications", story S8.3.1) are themselves
written as a real `GateDecision(gate="G3")` row (`exception_desk._write_gate_decision`),
the identical node type the G1-G4 approval workflow writes. `decision_register.py`
reuses `g3_card._live_gate_decisions` (a cross-epic private helper already exactly
"every live `GateDecision` node, hydrated") rather than inventing a second query or a
second table — there was never a second table to invent from.

### 2. No new "auditor" role — `client_infosec_reviewer` stands in, gated the identical shape S10.3.1 already set

§15.1's own role table gives `client_infosec_reviewer` the nearest real remit to an
auditor: *"Reviews the data-handling position, inference boundary and evidence
export"* — immediately next to this exact screen's own "Open evidence; export" action
pair. `require_decision_register_reader` (`deps.py`) is gated "any Artizent role, or
`client_infosec_reviewer`," the identical "any Artizent role, or this one named client
role" shape `require_mu_page_reader` already set for the report owner (S10.3.1). No new
role is declared — this is a cross-cutting read, not a gate a workflow needs an approver
for, the bar `roles.py`'s own docstring sets before adding a twelfth role.

### 3. A row's subject is resolved from (gate, decision), not gate alone

G3 is written from two different call sites with two different subject shapes: `g3_card.
approve`/`request_changes` write `subject_ref=<Workbook id>` (`APPROVED`/`CHANGES_
REQUESTED`); `exception_desk._write_gate_decision` writes `subject_ref=<ExceptionCase
id>` for `PATCHED`/`REDESIGN`/`MODEL_DEFECT`/`SOURCE_DEFECT` (and `WAIVED`, per `g3_card.
py`'s/`parity_dashboard.py`'s own comments — though, confirmed by direct search, no live
write site exists for it yet, a real pre-existing gap this story does not close).
`_subject_label(gate, decision)` is this dispatch: G1 is always the one platform-wide
`tolerance_charter` singleton; G2 is a `ModelFamily`; G4 is a `Site`; G3 is `ExceptionCase`
for the four (five) adjudication outcomes and `Workbook` otherwise. An `ExceptionCase`
carries no `name` property of its own (confirmed against `ontology/nodes.py`), so its
display name is built from its `class` plus its owning Workbook's own real name
(`"FILTER_CONTEXT on Daily VaR"`), not a fabricated label.

### 4. The evidence bundle resolves `evidence_ref` only when it really is a stored artefact — never guessed

`evidence_ref` is heterogeneous by design across this codebase's own gates: sometimes a
stored JSON/image artefact (`g3_card`/`g4_card`'s own decision snapshot, `exception_
desk`'s own measure id passed straight through), sometimes a bare `SemanticModel` node
id (`g2.approve`), sometimes absent entirely (G1, most Exception Desk outcomes, every
`request_changes`/`defer`). `artefacts.ArtefactStore.get()` already returns `None` for
an id it does not hold (confirmed directly) — `evidence_bundle()` reads that real signal
rather than inferring from `gate`/`decision` which shape a given ref is. A row whose
evidence does not resolve to a stored artefact still returns its own full decision
record; the bundle honestly reports `artefact: null` rather than fabricating content.
The bytes themselves are never re-served by this story's own new route — the console
fetches them from the identical, already-existing `GET /v1/artefacts/{id}/content`
(S10.3.1) once it has the id from the bundle.

### 5. "Signed PDF" is a rendered attestation, not a persisted, re-fetchable baseline

`calibration_wave.sign_report`'s own AC names two separate actions, "Sign report" and
"export" — a real, versioned `calibration_baseline` table backs the first. This story's
own AC names only one action, "export ... to ... signed PDF": no separate sign step, no
prior version to compare against. `render_decision_register_pdf` (real `reportlab`
tables, the identical library/shape `calibration_wave.render_calibration_report_pdf`/
`status_pack.render_pdf` already use) carries a footer stating who exported it and when,
read from the calling principal and the render instant — the same "typed, attributed
act, not a cryptographic signature" honesty every other "signed" concept in this
codebase already discloses (`calibration_wave`'s own `signed_by`/`countersigned_by`,
every gate's own countersigner string). No PKI, no hash chain, no external anchor exists
anywhere in this codebase (confirmed by direct search); §4.5's own "signed bundle ...
with a verification tool" is the separate, later Evidence Export screen (§15.3.7), not
this story's own AC.

### 6. Filters and search are client-side over one fetched list; export re-sends the current filter as real server query params

The identical "one server round trip, then filter locally" convention `GateInbox.tsx`'s
own gate/site filters already established — free-text search updates on every keystroke
with no debounce and no re-fetch. The backend's own `gate`/`decision`/`approver`/`q`
query parameters exist for a different real purpose: CSV/PDF export re-sends the
console's *current* filter state as those same params, so "export" means "export what
you are looking at," not a second, unfiltered dump — the one place in this story where a
server-side filter is actually exercised over HTTP.

### 7. No new table, no migration

Every field this register shows is already written by an existing gate action across
G1-G4 and the Exception Desk; this story only reads, resolves, filters, sorts, and
renders it. `ontology_check.py --spec`/`--generated` and `migration_check.py` all pass
unchanged.

## Consequences

- `services/graph-svc`: new `decision_register.py` (`list_decisions`, `decision_row`,
  `evidence_bundle`, `decisions_to_csv`, `render_decision_register_pdf`); new routes
  `GET /v1/decisions`, `GET /v1/decisions/{id}/evidence`, `GET /v1/decisions.csv`,
  `GET /v1/decisions.pdf` (`routes_decision_register.py`); new `DecisionRegisterReaderDep`/
  `require_decision_register_reader` in `deps.py` (Artizent, or `client_infosec_
  reviewer`). No new table, no ontology change, no migration.
- `services/console-web`: new `register/DecisionRegister.tsx`; `lib/api.ts` gained
  `decisionRegister`/`decisionEvidence`/`decisionRegisterCsv`/`decisionRegisterPdf` and
  their real response types (`DecisionRegisterItem`, `DecisionRegisterResponse`,
  `DecisionEvidenceBundle`); `App.tsx` gained a new `register` surface, added to
  `CLIENT_VISIBLE_SURFACES` for `client_infosec_reviewer` without changing its existing
  `estate` landing surface — the identical deliberately narrow addition every prior F10
  story took.
- Verified: `services/graph-svc` — 16 new integration tests against real PostgreSQL +
  Apache AGE (a G2 decision resolving its real `ModelFamily` name; a G3 report review and
  a G3 Exception Desk adjudication resolving to two different real subject shapes under
  the identical gate; a G4 decision resolving its real `Site` name; G1 naming the
  platform singleton; gate/decision/approver/search filters each proven to narrow a real
  set; an evidence bundle opening a real stored artefact; a decision whose evidence does
  not resolve honestly reporting no artefact; a real CSV carrying every real field; a
  real PDF starting with a real `%PDF` signature; 6 HTTP-level tests including a real
  role refusal); the full integration suite re-run clean afterward (787 passed, 2
  skipped, no new failures, up from 771 before this story); `ruff`/`mypy`/
  `ontology_check.py`/`migration_check.py` all clean. `services/console-web` — 421 tests
  passing (9 new: the real rows rendering, the empty and read-failure states, a G3
  review and adjudication rendering as two distinct real rows, gate/decision/approver/
  search filtering, opening a real evidence bundle with and without a resolving
  artefact, closing the panel, and the CSV/signed-PDF export actions); `tsc --noEmit`/
  `eslint`/`vite build` all clean.
- A real, pre-existing test assumption broke as a direct, intended consequence of this
  story (`client_infosec_reviewer` was `app.test.tsx`'s own "single-surface client role"
  example, retargeted there by ADR 0075 after `client_data_owner` gained a second
  surface — it now has two of its own) and was retargeted again, at `client_programme_
  sponsor` (§15.1 names the role; no story gates it onto any real second screen yet), the
  same "retarget the example, never loosen the assertion" discipline both prior ADRs
  already used.
- Live-smoke-tested against the real Docker stack (both images rebuilt): the Decision
  Register, read as `client_infosec_reviewer`, listed the real demo estate's own
  existing `GateDecision` rows (G2 approvals from earlier smoke-tested families,
  including this session's own "Mismatch Smoke (singleton)" family from the Gate Inbox's
  own prior live test) with correct subject names, approver/countersigner fields, and
  timestamps; filtering by gate and free-text search both narrowed the real list
  correctly. Opening evidence on the real approved family's own decision returned its
  real `SemanticModel` reference honestly as `evidence_ref` with `artefact: null` (a
  `SemanticModel` id is not a stored artefact — the disclosed heterogeneity decision 4
  describes, observed live, not only in the test suite). `GET /v1/decisions.csv` and
  `GET /v1/decisions.pdf` both returned real, non-empty files against the running
  service — the PDF opened as a real document with the real table and a real "Exported
  and signed by ..." attestation line naming the real calling principal. A refused
  request from a `client_data_owner` identity (not the gated role) returned a real 403.

## Alternatives considered

**Add a twelfth role, `auditor`, the same way `CLIENT_ANALYTICS_LEAD` was added for
G1.** Rejected — see decision 2. That precedent closed a real *gate-approver* gap
(§13.1's own table named a client approver for G1 that §2.4 never declared); an auditor
approves nothing here, so the bar that justified adding a role does not apply. Reusing
`client_infosec_reviewer`, the nearest real client remit, is the more honest reading.

**Give the Exception Desk's own adjudications a second table or a second response
shape, so "GateDecisions" and "adjudications" read as two distinct things the AC's own
wording suggests.** Rejected — see decision 1. They are not two things in this codebase;
building a second shape to match the AC's own phrasing would be inventing a distinction
the ontology itself never drew, purely to look more literal.

**Resolve every `evidence_ref` by trying each gate's own "expected" node label
(`SemanticModel` for G2, an artefact for G3/G4) instead of asking the artefact store
directly.** Rejected — see decision 4. `evidence_ref`'s real shape is not uniform even
within one gate (G2's `CHANGES_REQUESTED` path can leave it `None`); asking the artefact
store what it actually holds is the one honest source of truth, not a per-gate guess
that would silently mis-resolve the day a gate's own evidence shape changes.

**Debounce or server-round-trip the free-text search, the way a high-volume log
viewer would.** Rejected — see decision 6. This register's own real decision volume
across the whole estate is not remotely log-scale (the identical "load everything, this
is not a high-volume log store" posture `gate_inbox.py`'s own per-gate queries already
take); a second network round trip per keystroke would be slower than filtering the
list already sitting in memory, with no real benefit at this scale.
