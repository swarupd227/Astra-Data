# ADR 0070 — G4 decommission: a readiness checklist, not an automatic request

Status: accepted · 13 September 2026 · Story S9.3.1, opening F9.3

## Context

S9.3.1 opens F9.3 (G4 Decommission) — the backlog's own AC, verbatim: *"As a licence
admin, I want a Decommission Tracker per site with a readiness checklist and a G4 card
when ready, so that I switch off a site once, safely, with a record."*

- Readiness = all in-scope MUs RELEASED + parallel window elapsed + regression green +
  adoption threshold met + owner confirmations received; each item shows its state and
  evidence
- G4 card lists the MUs, the licence tier and count released, the source workbooks to
  be archived, and the confirmation text; approver is the licence admin, countersigned
  by the Programme Manager
- On approval the Steward archives the source workbooks (adapter capability), records
  the licence-release date and value on the Site node, and emits `site.decommissioned`
- Deferral records the reason and a new target date

§13.1's own G4 row, verbatim: *"Site | Client licence administrator; countersigned by
Programme Manager | All site MUs RELEASED; parallel-run period elapsed; regression
suites green | Licence release; source workbooks archived."* §14.4, verbatim:
*"...tracks decommission readiness: all MUs released, regression green, adoption
sessions held, owner confirmation received. When readiness is met the G4 request opens
automatically for the licence administrator. On approval the Steward archives the
source workbooks (adapter capability) and records the licence-release date and value
from the site record."*

Confirmed by direct research before writing anything: no single "regression green" fact
exists per workbook beyond `RegressionSchedule.last_result`; no "owner confirmation"
mechanism exists anywhere; `SourceAdapter` has no archive/retire capability; `Site` has
no decommission-related properties; `GateDecision.decision` has no `DEFERRED` value;
and both `release.py` and `adoption.py` explicitly, repeatedly disclose this exact gap
as future F9.2/F9.3 scope.

## Decisions

### 1. Approve/defer reuse G3's own proven shape — no new mechanism invented

Approver + countersigner on one `GateDecision`, the identical convention `g3_card.
approve`'s own `countersigned_by` already established (a plain, unverified name string
the approver types, not a second authenticated action). `GateDecision.decision` gains
`DEFERRED` (nodes.py, an additive enum widening requiring no migration — confirmed
directly against `tools/migration_check.py`'s own guard logic, which does not even
classify an added enum value as a change to react to). `subject_ref` names the Site
rather than a workbook — the first G4 write this codebase has made, and the first
`GateDecision` whose subject is not a Migration Unit proxy.

### 2. Readiness is a real, computed checklist — never a stored flag, and never blocks reading

Each of the AC's five items is computed live from real facts already established by
prior stories, with real evidence attached:
- **All in-scope MUs released**: `ScopeStore.states()` (excluding withdrawn workbooks)
  against the identical "released" signal `release.py`/`adoption.py` already use — a
  SUCCEEDED `promotion_run` row for `to_stage="prod"`.
- **Parallel window elapsed**: `release._window_end` and `g3_card.DEFAULT_PARALLEL_
  WINDOW_WEEKS`, reused verbatim — the identical per-site window `release_board`'s own
  panel already computes.
- **Regression green**: `RegressionScheduleStore.list_schedules()`, filtered to the
  site's in-scope workbooks. A workbook that was never scheduled does not block
  readiness — "green" reads as "nothing is currently failing," not "everything has
  been checked," since regression scheduling is itself opt-in and making every
  released MU's readiness depend on a separate, unrelated opt-in action would invent a
  precondition the AC's own words never ask for.
- **Adoption threshold met**: `AdoptionStore.latest_for_workbooks`'s own frozen
  `meets_threshold` per released workbook — the identical value `decommission_tracker`
  already shows, never recomputed against whatever the config says today.
- **Owner confirmations received**: see decision 3.

The card itself is always readable regardless of readiness — only the *approve* action
is refused server-side when the checklist is not fully met, the identical "read shows
real state, the mutating action enforces the real precondition" split `promotion_
blockers`/`promote_workbook` already established.

### 3. Owner confirmation is a new, minimal, overwrite-not-append per-workbook fact

No existing mechanism records "the owner confirms X" generically. The closest
precedent, `retention.Programme.family_count_confirmed_by`/`_at` (a Programme
Manager's confirmation, overwritten rather than appended, on the record it describes),
is reused here per-workbook: `public.decommission_confirmation`
(`PostgresDecommissionConfirmationStore`), `UNIQUE (graph, workbook_id)` with `ON
CONFLICT ... DO UPDATE`. Confirming is the report owner's own action (`Role.
CLIENT_REPORT_OWNER`) — the same persona G3 approval already uses, and the only
"owner" role this codebase already ties to one report.

### 4. Archiving a source workbook is a genuinely new `SourceAdapter.archive()` capability

§13.1's own G4 row names it plainly, "source workbooks archived," and no such method
existed on the contract before this story (confirmed by direct read of every method:
`manifest`/`enumerate`/`fetch`/`parse`/`usage`/`owners`/`viewers`/`sites`/`parse_calc`/
`execute_case`/`capture_visual`). `Capabilities.archive: bool = False` and `SourceAdapter.
archive(asset: AssetRef) -> ArchiveResult` are added (`INTERFACE_VERSION` bumped 1.1 →
1.2, the identical "a new Protocol method widens the contract" bump `TargetAdapter.
usage()` already took on the target side). `FixtureSourceAdapter.archive()` follows the
identical "check something real, then a real state change" posture the rest of the
fixture already sets. The real Tableau adapter's own `archive()` honestly raises
`UnsupportedCapability` — no live archive integration exists, the identical disclosed
gap `usage`/`viewers`/`owners` already have.

**Widening `Capabilities` required updating the RPC wire format too — found live by
the existing out-of-process harvest test, not assumed.** `packages/adapter-sdk/src/
astra_adapter/rpc/wire.py`'s `encode_capabilities`/`decode_capabilities` hand-write
every field rather than deriving the wire shape from the dataclass (a deliberate
choice recorded in that module's own docstring: "the wire format *is* the contract's
compatibility surface"), so a new dataclass field is invisible over the wire until the
codec is told about it. `test_the_platform_can_harvest_through_an_out_of_process_
adapter` caught the mismatch immediately (`archive: True` in-process vs. `archive:
False` after an RPC round trip) — fixed by adding `archive` to both codec functions,
plus a full `archive()` round trip on `RemoteAdapter` (`client.py`) and the RPC server
(`server.py`, `wire.py`'s new `encode_archive_result`/`decode_archive_result`), the
identical shape `usage`/`owners` already have on both sides of the wire.

### 5. "Records the licence-release date and value" writes directly onto the real Site node

`Site.decommissioned_at`/`.licence_release_value` (new, additive, disclosed-deviation
properties — see `nodes.py`'s own `SpecDeviation`) are written at G4 approval, not
duplicated into a second `site_record` table §21 names but this codebase has never
built. `licence_release_value` is this same node's own `licence_cost_annual` at the
moment of approval, honestly `None` if that was never known (an adapter with no
ownership capability) — the identical "the row IS the fact" reasoning `promotion_run`/
`report_deploy_run` already established for history, applied here to a current-state
fact about the node itself.

### 6. Approval is all-or-nothing — archive every in-scope workbook or write nothing

`approve` re-checks the full readiness checklist and refuses (naming every unmet item)
before archiving anything; it also refuses outright if the adapter never claimed the
archive capability at all. Each in-scope workbook's own real `AssetRef` is resolved
from the adapter's own live `enumerate(Scope(site=...))` — not derived from the graph
— and if any workbook's own archive call fails or the source no longer reports it, the
whole approval is refused before the `GateDecision`, the `Site` property write, or the
`site.decommissioned` event are ever written. The identical "no partial or guessed
success" posture `promote_workbook` already takes for its own two deploy calls.

### 7. Scope deliberately stops at approval and deferral — no automatic G4 request

§14.4's fuller sentence — "when readiness is met the G4 request opens automatically" —
names a real, separate mechanism (a notification or scheduled check) nothing in this
codebase drives yet. The Decommission Tracker's own real, live read of the checklist is
how a licence admin learns readiness today, the identical "a real, queryable fact beats
an unbuilt notification" posture every prior gate-adjacent F9 story has already taken.

## Consequences

- `packages/adapter-sdk`: `Capabilities.archive`, `ArchiveResult`, `SourceAdapter.
  archive()` (`INTERFACE_VERSION` 1.1 → 1.2); `FixtureSourceAdapter.archive()`/
  `FixtureWorkbook.archived`; the Tableau adapter's own `archive()` raising
  `UnsupportedCapability`; the RPC wire/client/server's own full `archive()` round trip.
- `services/graph-svc/src/astra_graph/ontology/nodes.py`: `Site.decommissioned_at`,
  `Site.licence_release_value`, `GateDecision.decision` gains `DEFERRED`,
  `GateDecision.target_date` — all additive (`SCHEMA_VERSION` 36 → 37, `ontology.
  lock.json` regenerated, no migration file needed), plus two new `SpecDeviation`
  entries.
- New migration `v0036_decommission_confirmation.py`: `public.decommission_
  confirmation`, a plain Postgres platform table, not an ontology node.
- `events.py`: `EventType.SITE_DECOMMISSIONED` (`estate.site.decommissioned`), a notice
  on the identical footing `MU_PROMOTED`/`ADOPTION_CAPTURED` already have — the first
  event whose `subject` is a Site.
- New `services/graph-svc/src/astra_graph/g4_card.py`: `readiness_checklist`, `g4_card`,
  `approve`, `defer`, `DecommissionConfirmation`/`PostgresDecommissionConfirmationStore`,
  `G4CardService`.
- `api/deps.py`: `require_g4_approver`/`G4ApproverDep` (Client Licence Administrator),
  `require_decommission_confirmer`/`DecommissionConfirmerDep` (Client Report Owner);
  reuses the existing `DecommissionTrackerReaderDep` (S9.2.2) for reading the card.
- New `services/graph-svc/src/astra_graph/api/routes_g4.py`: `GET /v1/sites/{id}:g4-card`,
  `POST /v1/sites/{id}:approve-g4`, `POST /v1/sites/{id}:defer-g4`, `POST /v1/workbooks/
  {id}:confirm-decommission`.
- `main.py`: `app.state.decommission_confirmation_store`, `app.state.g4_card =
  G4CardService(...)`.
- `services/console-web`: `DecommissionTracker.tsx` extended with a per-site readiness
  checklist, an on-demand G4 card panel (approve/defer forms gated to the licence
  administrator), and a per-MU "Confirm" action gated to the report owner — the
  identical single-lookup-on-demand shape `G3Card.tsx` already takes, avoiding an N+1
  fetch across every site on load.
- Verified: 15 new pure unit tests (`_clean_rationale`, `_parse_target_date`,
  `_window_elapsed`'s own date math, the result dataclasses' own round-trips); 2 new
  adapter-sdk unit tests for `archive()` (refused when unclaimed, a real archive
  marking the fixture's own estate); 21 new integration tests against real PostgreSQL +
  Apache AGE and the real fixture source adapter (readiness fully met when every real
  precondition is real; each of the five items independently false with its own real
  evidence; a withdrawn workbook correctly excluded; the card's own MUs/licence
  tier/confirmation text; `approve` refusing an unready site, a missing rationale, a
  missing countersigner, an adapter with no archive capability, and a site the source
  no longer reports, each before archiving anything; a real approval really archiving
  the fixture workbook, writing the real `GateDecision`, and recording the real Site
  properties; an honest `None` licence value for a site with no known cost; `defer`
  recording a real reason and target date and refusing a past one; the `G4CardService`
  wrapper; confirmation's own overwrite semantics); 20 new console tests (the readiness
  checklist, an honest not-ready state, a card read failure, owner confirmation gated to
  the report owner, authorising and deferring both gated to the licence administrator, a
  real API refusal surfaced for each, and both hidden for every other role). The full
  existing graph-svc suite (1,499 passed non-integration; 716 passed, 2 skipped, one
  already-known, unrelated `test_integration_g2_reminders.py` failure in the full
  integration run — confirmed a working-day SLA threshold in that test's own
  calendar-day backdate colliding with this run's own weekday, the identical
  date-boundary flake S9.1.2's own verification already found in this same test, and
  confirmed unrelated to this story by a clean `git diff` on that test's own files;
  adapter-sdk's own 126 tests and adapter-tableau's own 261 both confirmed passing
  after the RPC capability-wire fix) and console-web suite (304 passed) both green
  alongside them; `ruff`/`mypy`/`tsc --noEmit`/`eslint` clean; `ontology_check.py`/
  `migration_check.py --write` confirm the ontology change is purely additive.
- **A real regression found and fixed in this story's own new code, before it ever
  reached a live deployment**: `DecommissionTracker.tsx`'s `approveG4`/`deferG4` both
  called `openG4` again afterward to refresh the card, and `openG4` unconditionally
  reset the very success/failure notice `approveG4`/`deferG4` had just set — a real
  decommission would have silently shown no confirmation of what just happened. Found
  by a console test asserting on the notice text after a successful defer, not by
  inspection; fixed by moving the notice reset to the "Open G4 card" button's own click
  handler (a fresh open is a new session) and leaving an in-place refresh alone.
- **A second real regression found and fixed, one dependency layer down**: widening
  `Capabilities` with `archive` broke `test_the_platform_can_harvest_through_an_
  out_of_process_adapter`, an existing test comparing a harvest run through `Remote
  Adapter` (real RPC over an in-process ASGI transport) against the same run in-process
  — the RPC wire's own hand-written `encode_capabilities`/`decode_capabilities` silently
  dropped the new field, so a real out-of-process adapter would report every deployment
  as lacking the archive capability regardless of what it actually declared. Fixed by
  widening both codec functions and adding the same full round trip for the new
  `archive()` method itself (client, server route, wire codecs) that every other
  optional capability already has, plus one further consequence found only by then
  running the full adapter-sdk suite: `fake/__init__.py`'s own `build()` (the reference
  adapter the SDK's conformance suite runs against) explicitly lists every capability it
  claims rather than relying on `FixtureSourceAdapter`'s own default, so it needed
  `archive=True` added explicitly too, or the conformance suite's own "claimed but
  broken" path would go untested for this capability the same way it already is
  covered for every other one.
- **A third real bug found live, in Docker, not by any test**: `approve`'s own
  `source_adapter.enumerate(Scope(site=...))` call raised a bare `AdapterError` for a
  real Site node whose own name the source adapter no longer recognises — surfaced as
  an unhandled 500 rather than a clean refusal, confirmed directly against the running
  container's own logs and traceback. Fixed by wrapping the call and translating it to
  a real `InvalidRequestError` naming the site, the identical "an adapter's own refusal
  is a fact to show, not a crash" posture every other adapter call in this module
  already takes; a new integration test (`test_approve_is_refused_honestly_when_the_
  source_no_longer_reports_the_site`) proves the fix.
- Live-smoke-tested against the real Docker stack: all four new routes live and
  role-gated correctly (a wrong-role attempt refused with a real 403 on the card, the
  confirmation, and both decisions); a real site built directly against the graph (a
  Site/Project/Workbook triple bypassing the full harvest/promotion chain, matching the
  established "raw-insert the platform-table row a full pipeline would eventually
  produce" precedent) walked through every real readiness fact — a real prod
  `promotion_run` row, a real frozen `adoption_snapshot`, a real owner confirmation —
  until its own G4 card read `"ready": true` with all five items honestly met; a real
  `defer` recorded a real reason and target date, visible on the card's own
  `latest_decision` immediately afterward. Found and fixed the third bug above in the
  process; the smoke-test site and workbook were retired afterward (`writer.
  retire_node`, a disclosed reason) rather than left in the shared demo estate, since
  they were test scaffolding, not real migration progress the way S9.2.1's own smoke
  test's promoted workbook was.

## Alternatives considered

**Model "regression green" as requiring every released MU to have a real, passing
schedule.** Rejected — see decision 2. Regression scheduling is itself opt-in; making
G4 readiness depend on a separate opt-in action nobody has necessarily taken would
invent a precondition the AC's own words ("regression green," not "regression checked")
do not ask for.

**Store owner confirmation as a `GateDecision`, matching every other decision in this
story.** Rejected — see decision 3. A confirmation is not itself a gate decision (no
gate is being decided, no approver role or countersign pair applies); it is a simple
current-state fact about one workbook, the same shape `retention.Programme`'s own
confirmation fields already have.

**Let `approve` archive whatever it can and report partial success.** Rejected — see
decision 6. A G4 approval records a licence release and a terminal decommission; a
report that "12 of 13 workbooks archived, licence released anyway" would misstate what
actually happened to the one that failed, the same "no partial or guessed success"
reasoning `promote_workbook` already established for its own two deploy calls.

**Build a real "readiness met opens the G4 request automatically" scheduler or
notification.** Rejected — see decision 7. No scheduler infrastructure for this exists
anywhere in this codebase yet; the Decommission Tracker's own live checklist read
already answers "is this site ready" without a queue this story would have had to
invent and staff correctly on its own.

## Open question for the product owner

- Should "all in-scope MUs RELEASED" also require the *train* those MUs belong to be
  fully released, or is site-scoped readiness (this story's own reading) correct even
  when a site's workbooks are split across multiple trains at different pipeline
  stages? The spec's own G4 row names "Site" as the subject and "all site MUs" as the
  criterion, which this story reads literally — but no worked example in the spec
  covers a site whose workbooks span more than one train.
