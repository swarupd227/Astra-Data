# ADR 0067 — mu.accepted invoicing and the commercial ledger: a real write, not a download

Status: accepted · 12 September 2026 · Story S9.1.2, closing F9.1, continuing E9

## Context

S9.1.2 closes F9.1 (opened by S9.1.1) — the backlog's own AC, verbatim: *"As a programme
manager, I want G3 acceptance to trigger the invoicing event under the fixed-price
contract, so that commercial recognition is a platform event, not a spreadsheet."*

- `mu.accepted` event with MU, tier and unit price is emitted and exported to the
  programme's commercial ledger
- The Programme Board shows accepted units by tier against plan

§3.1 itself, verbatim: *"Invoicing under a fixed-price-per-report contract is triggered
by an MU reaching ACCEPTED."* §3.4's own worked example: *"...approves G3. State ->
ACCEPTED; invoice line raised."* §13.1's own G3 row: *"Invoice trigger; release
permitted."*

Confirmed by direct grep of the whole spec document for "unit price", "fixed-price",
"ledger": every hit only ever states that an MU reaching ACCEPTED *is* the trigger,
never what the trigger is worth or how many of each tier are planned. No real Migration
Unit node exists either (`migration_units.py`'s own docstring: *"this is a port, not an
implementation"*) — the identical gap ADR 0066 already worked around for the G3 card
itself.

## Decisions

### 1. `EventType.MU_ACCEPTED` is a new notice, on the identical footing SOURCE_DRIFT/PATTERN_RETIRED already have

No pre-declared-but-unused event value existed for this — confirmed by direct read of
`events.py`. `mu_accepted` mirrors `pattern_retired`'s own factory shape exactly (a flat
`data` dict, keyword-only args), added to the `mutates_graph` exclusion tuple: it is a
statement about a decision the platform just made, not a graph mutation. The real
mutation is `g3_card.approve`'s own `GateDecision(gate="G3", decision="APPROVED")` node
write, already in place since S9.1.1 — `mu.accepted` exists so a billing-side consumer
never has to re-derive the MU/tier/price from that node and a separate lookup.

### 2. The workbook id is the event's own subject — the identical MU proxy every G3-adjacent story has used since S8.1.1

No new subject convention invented here; `mu_accepted(subject=workbook_id, ...)` reuses
`ExceptionCase.mu_ref`/ADR 0060's own finding, restated by ADR 0066 for the G3 card
itself.

### 3. Tier resolution reuses `ScopeStore.states()` verbatim — there is no single-workbook tier getter anywhere, and this story does not add one

Confirmed by direct read of `scope.py`: the only tier read path is the bulk
`states() -> dict[str, ScopeState]` map every other tier consumer already uses.
`invoicing._current_tier` indexes into that same map rather than adding a new
single-workbook getter for one caller. A workbook that has never been re-tiered
honestly has no tier (`ScopeState.tier is None`) — `record_acceptance` returns `None`
rather than guessing one, the identical "a real check, an honest skip" posture this
codebase takes throughout (e.g. `parity_dashboard.py`'s own honestly-absent Mender-pass
trend). Approving G3 is never blocked by a missing tier — S9.1.1's own `approve()` never
checked tier, and retrofitting a hard precondition here would be scope beyond this
story's own AC.

### 4. `DEFAULT_UNIT_PRICES`/`PLANNED_BY_TIER` are real, invented, disclosed planning assumptions — the identical footing `retention.PLANNED_FAMILY_COUNT` already has

Neither a real unit price nor a real per-tier plan is ever stated anywhere in the spec —
only that an MU reaching ACCEPTED triggers invoicing, and one single overall planning
figure (`PLANNED_FAMILY_COUNT = 150`, Appendix A). `DEFAULT_UNIT_PRICES` increases with
tier complexity (8,000 / 15,000 / 28,000 / 40,000), the same intuition §9.1's own C1–C4
calibration targets already reflect. `PLANNED_BY_TIER` (70 / 50 / 20 / 10) is
deliberately built to sum to the identical 150, enforced by a real unit test importing
`retention.PLANNED_FAMILY_COUNT`, so the two planning figures can never silently
disagree.

### 5. Unit prices are a single current-value row per tier, not versioned like `mender_config` — a deliberate simplification

`mender_config` is append-only, versioned (`MAX(version)+1` on every save) — but has no
console UI and no HTTP route; its own defaults are what every deployment actually uses,
confirmed by direct read. This story's AC does not ask for a price-change audit trail,
so `unit_price_schedule` is simpler: `PRIMARY KEY (graph, tier)`, plain
`INSERT ... ON CONFLICT DO UPDATE`. A missing row for a tier falls back to
`DEFAULT_UNIT_PRICES[tier]` rather than failing — the identical "sane defaults, no
config UI required yet" posture `mender.DEFAULT_PASS_BUDGET` already has.

### 6. "Exported to the programme's commercial ledger" is the real write to `commercial_ledger` itself

No "ledger" table existed anywhere before this story — confirmed by search; the only
prior reference is an explicit deferral comment in `v0012_clustering_record.py`
("persisted run ledger... until a real need arises") — this story is that real need.
Writing the row *is* the export: the fact leaves the append-only `estate_event` outbox
stream and lands in a table shaped for a commercial reader (workbook, tier, price,
who/when) — the identical "a second, business-shaped read of a first-class platform
fact" reasoning `parity_dashboard.py` already gives for reading `Verdict`s a second way.
Nothing in the AC or spec names a downloadable file or a real external billing system to
push to, so no export-file feature was built.

### 7. `UNIQUE (graph, workbook_id)` plus `ON CONFLICT ... DO NOTHING` makes re-approving an already-accepted workbook a real no-op, not a double-billed line

"Commercial recognition is a platform event, not a spreadsheet" only holds if the
platform cannot invoice the same MU twice. `record_acceptance` inserts and only emits
`mu_accepted` when a row is actually, newly written — proven directly by
`test_a_re_approved_workbook_is_never_invoiced_twice` (two approvals of the same
workbook; the second is honestly not invoiced, and `accepted_by_tier` still shows
exactly one).

### 8. `GET /v1/programmes:acceptance` lives directly in the existing `routes_provenance.py`, reusing `_estate_graph`

No new service/app.state binding was needed purely for pool/graph_name access — this
route reuses the same helper `/v1/programmes` already uses. Only a small new
`_unit_price_store(request)` helper and `app.state.unit_price_store` binding were
required.

## Consequences

- New `services/graph-svc/src/astra_graph/invoicing.py`: `DEFAULT_UNIT_PRICES`,
  `PLANNED_BY_TIER`, `LedgerEntry`, `UnitPriceStore` (Protocol),
  `PostgresUnitPriceStore`, `record_acceptance`, `accepted_by_tier`,
  `programme_acceptance_summary`.
- `events.py`: `EventType.MU_ACCEPTED`, `mu_accepted(...)` factory, widened
  `mutates_graph` exclusion tuple (now `SOURCE_DRIFT`, `PATTERN_RETIRED`,
  `MU_ACCEPTED`).
- New migration `v0033_commercial_ledger.py`: `public.unit_price_schedule`,
  `public.commercial_ledger` (both plain Postgres platform tables, not ontology
  nodes — no ontology change, schema version stays at 36).
- `g3_card.py`: `approve()` now also calls `invoicing.record_acceptance` after writing
  its own real `GateDecision`; `G3DecisionResult` widened with `invoiced`, `tier`,
  `unit_price` (default `False`/`None`/`None` — `request_changes`/`ask_question` never
  invoice). `G3CardService` widened to take `scope_store`/`unit_price_store`.
- `main.py`: `app.state.unit_price_store = PostgresUnitPriceStore(...)`, wired into
  `G3CardService`.
- `routes_provenance.py`: `GET /v1/programmes:acceptance` -> `programme_acceptance_summary`.
- console-web: `api.ts` widens `G3DecisionResult`, adds `AcceptanceTierRow`/
  `AcceptanceSummary`/`acceptanceSummary()`; `ProgrammeBoard.tsx` gains a seventh pane,
  `AcceptanceByTierPane` — accepted units by tier against `PLANNED_BY_TIER`, each
  tier's own current unit price, and total accepted value.
- Verified: 7 new pure unit tests (`invoicing.py`'s own real defaults/planning-sum
  cross-check/round-trips/notice classification) plus 2 new/updated `g3_card.py` unit
  tests for the widened `G3DecisionResult`; 12 new integration tests against real
  PostgreSQL + Apache AGE (a real ledger row and a real `mu.accepted` event, an honest
  `None` with no real tier, never double-invoicing, a real updated price, the price
  store's own defaults/persistence/tier validation, `accepted_by_tier` grouping, the
  programme summary combining accepted/planned/price, the honestly-empty summary, the
  new route's own role gate) plus 3 new integration tests in
  `test_integration_g3_card.py` (`approve` really invoices a tiered workbook, honestly
  skips an untiered one, never double-invoices on re-approval); 5 new console tests
  (real rows across all six columns, the honest zero-accepted state, a bad delta pill
  behind plan, a good delta pill at/ahead of plan, a read failure); the full existing
  graph-svc suite (1458 tests) and console-web suite both green alongside them;
  `ruff`/`tsc --noEmit`/`eslint` clean; `ontology_check.py`/`migration_check.py --write`
  confirm no ontology drift.

## Alternatives considered

**Build a real Migration Unit node so `mu.accepted` has a "proper" subject.** Rejected —
see decision 2. Identical reasoning to ADR 0066's own decision 1: E3/F3.2 remains
entirely unbuilt, and inventing an MU node for this story alone would fabricate a state
machine no sibling story shares.

**Version `unit_price_schedule` like `mender_config`.** Rejected — see decision 5. No AC
or spec text asks for a price-change audit trail, and `mender_config`'s own versioning
has never been surfaced through a UI or route in the two years since it was built.

**Build a separate downloadable ledger export (CSV/zip) as "exported to the ledger."**
Rejected — see decision 6. Nothing in the AC or spec names a file or an external billing
system; the real, durable table write is the export.

## Open question for the product owner

- `DEFAULT_UNIT_PRICES`/`PLANNED_BY_TIER` are this story's own invented planning
  assumptions, not values stated anywhere in the spec (the identical situation ADR
  0066's own open question raised for waivers). Should a near-future story add a real
  console screen for a Migration Architect to set per-tier unit prices for a live
  engagement, given `PostgresUnitPriceStore.set` already exists but has no route or UI
  yet — the identical "the store exists, nothing calls it" posture `mender_config` has
  had since it was built?
