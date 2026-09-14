# ADR 0075 — The Gate Inbox is role-dispatched, not role-merged

Status: accepted · 14 September 2026 · Story S10.4.1, opening F10.4

## Context

S10.4.1 opens F10.4 — the backlog's own AC, verbatim: *"As a data owner, I want a Gate
Inbox that shows only the requests waiting for me, so that I do my part in minutes and
get out."*

- Card stack of open gate requests for my role and domain, ordered by due date; each
  card is the §15.5 anatomy; filters by gate type and site
- Approve / request changes / ask a question in place; an approval that needs a
  countersign shows who is next
- Email and Teams notification on new request and at SLA thresholds, with a deep link

§15.3.6's own Governance-surface row, quoted verbatim: *"Gate Inbox (client default) |
Card stack of open gate requests for the user's role and domain, ordered by due date;
each card in the §15.5 anatomy; filters by gate type and site. | Approve; request
changes; reject; delegate; ask a question."* This story's own AC narrows that action
list — "reject" (a real, declared `GateDecision.decision` enum value never written
anywhere in this codebase) and "delegate" (no mechanism anywhere) are both real,
disclosed gaps this story does not close.

**"Data owner" is not one existing role.** §15.1's own role table ties one distinct
client role to each of G2/G3/G4 (`client_data_owner`, `client_report_owner`,
`client_licence_admin`), confirmed by direct research; §13.1's own gate-ownership table
repeats the same 1:1 binding. §15.4's own gate card anatomy (§15.5) is itself the G3
card's own worked example — `g3_card.py`'s own docstring already reads §15.5 as its
own spec, not a generic template G1/G2/G4 all implement identically (each of those
three has a materially different real card shape today). G1 (`client_analytics_lead`)
is out of scope for this story: neither F10.4's own epic preamble nor S10.4.1's own AC
names it, and G1 has exactly one live subject (the platform-wide `"tolerance_charter"`
singleton), so "a card stack of open G1 requests" is structurally meaningless.

**App.tsx's own prior docstring had already flagged this exact gap.** Landing
`client_data_owner` on Model Proposal and `client_report_owner` on G3 Acceptance was
disclosed there as a stand-in specifically because *"no real multi-item [G3] queue
exists... building one is exactly the 'no screen without an engine feature' scope this
story does not take on."* S10.4.1 is that later story.

## Decisions

### 1. The inbox is role-dispatched, not a multi-role merge

The console's own identity model is genuinely single-role per session (`App.tsx`'s own
"Acting as" `<select>` sends exactly one role). A caller acting as `client_data_owner`
can only ever legitimately approve G2 (`g2.approve`'s own hardcoded `approver_role=
"client_data_owner"`); `client_report_owner` only G3; `client_licence_admin` only G4.
So `gate_inbox()` is a *dispatch* over the caller's own declared role — it decides
*which* real per-gate pending-list function(s) to call at all, not a single query that
computes everything and filters afterward. An Artizent role sees the union of all
three, the identical "reader is broader than the approver" shape `G3CardReaderDep`/
`DecommissionTrackerReaderDep` already set for their own gates.

### 2. "My domain" is real and enforced only for G2

`g2.check_domain_scope` checks `ModelFamily.domain` against the caller's own
`X-Astra-Domain-Scope` header — domain is declared as a `ModelFamily`-only concept in
this ontology; `Workbook` (G3's subject) and `Site` (G4's subject) carry no comparable
property. `pending_g2_items` filters by domain scope, mirroring `check_domain_scope`'s
own "an unset family domain is open to any data owner" rule (ADR 0030); G3/G4 items
carry `domain: None` honestly rather than a fabricated derivation through a workbook's
own family.

### 3. Due date is real only for G2; G3/G4 order by the real timestamp they do carry

Only `pending_g2_reviews` (`g2_reminders.py`, S4.2.2) computes a real `days_waiting`/
`breached` from the event log. No equivalent exists for G3 (no "entered review" event a
Workbook fires) or G4 (readiness carries no "became ready at" timestamp). `_sort_key`
ranks a breached G2 item first, then by real `days_waiting` where known, then by
whatever real timestamp an item does carry (a G3 report's own `created_at`) — never an
invented SLA for the two gates that do not have one.

### 4. G3's and G4's own "pending" definitions are new, disclosed readings — no prior story defined them

**G3**: a workbook is "awaiting G3 decision" when it has a real, composed
`ReportDefinition` *and* a `ParityRun` that already passes the charter *and* its own
latest G3 `GateDecision` (if any) is not `APPROVED` — never decided, or sent back with
`CHANGES_REQUESTED` and not yet re-approved. A report that has not yet passed the
charter is waiting on more work, not a decision; this module does not surface it.

**G4 finally builds the real queue S9.3.1's own docstring said it was deliberately not
building** ("a fuller 'readiness met opens the request automatically' mechanism... is
not built"). A site is "awaiting G4 decision" when `g4_card.readiness_checklist`
reports every item met *and* its own latest G4 decision (if any) is not `APPROVED` — a
`DEFERRED` site stays open, since deferral is explicitly "not yet, try again later,"
not terminal. Computing readiness for every site to find the ready ones accepts the
identical fan-out cost `g4_card.readiness_checklist`/`DecommissionTracker.tsx` already
have for a first cut; this story's own AC carries no latency budget the way S10.3.1's
Migration Unit page did.

### 5. "Shows who is next" is the real countersigner role, not a named individual

No gate anywhere in this codebase pre-assigns a specific countersigning person — the
countersigner is a plain, unverified name string the *approver themselves types at
decision time* (confirmed directly across G2/G3/G4's own write sites). "Who is next"
is answered with the real, structural fact this codebase can honestly state: which
*role* must countersign (`semantic_model_engineer` for G2, `migration_engineer` for
G3, `programme_manager` for G4 — each already a literal constant on the real approve
action), not a fabricated named assignee.

### 6. Every action reuses the identical existing per-gate route — no new mutation exists

The Gate Inbox adds exactly two new endpoints: `GET /v1/gate-inbox` (the aggregated
read) and `POST /v1/gate-inbox:notify`. Approve/request-changes/ask-a-question/defer
all call the identical existing routes each gate already had before this story
(`:approve-g2`, `:request-changes`, `:approve-g3`, `:request-changes-g3`, `:ask-g3-
question`, `:approve-g4`, `:defer-g4`) — the console's `GateInbox.tsx` calls the same
`Api` methods `ModelProposal.tsx`/`G3Card.tsx`/`DecommissionTracker.tsx` already use.

### 7. Notifications are a new, generalised, disclosed-local-only mechanism — G2's own SLA reminders stay untouched

No live email or Teams delivery exists anywhere in this codebase (no smtp library in
`pyproject.toml`; the only Teams-adjacent code, `g3_card.to_adaptive_card`, is a real
export with no live bot/webhook to post it). "At SLA thresholds" stays G2's own,
already-real `send_due_reminders` mechanism (S4.2.2), called unchanged. "On new
request" is this story's own new, generalised mechanism spanning G2/G3/G4: a new
platform table, `public.gate_notification` (migration v0038), keyed by `(graph, gate,
subject_ref)` — a different key shape from `g2_reminder`'s own `(family_id, day)`,
since this is a one-time "a new open request appeared" event across three different
subject grains, not a repeating threshold. `GateNotificationChannel`/`LocalGateNotifi
cationChannel` are a new Protocol/implementation, not `g2_reminders.NotificationChan
nel` reused directly — the shapes genuinely differ (a generic gate item vs. G2's own
typed `PendingReview` plus a day threshold) — but the identical "record and log,
disclosed as no real outward delivery" posture. `POST /v1/gate-inbox:notify` calls both
mechanisms together, so one action covers the AC's own full sentence.

### 8. Artefact-reading routes needed no further widening

The Migration Unit page (S10.3.1) already widened `GET /v1/artefacts/{id}(/content)` to
the report owner; the Gate Inbox introduces no new artefact-serving need beyond what
that story already opened.

## Consequences

- `services/graph-svc`: new `gate_inbox.py` (`gate_inbox`, `pending_g2_items`,
  `pending_g3_cards`, `pending_g4_sites`); new `gate_notifications.py`
  (`GateNotificationChannel`, `LocalGateNotificationChannel`, `PostgresGateNotification
  Store`, `notify_new_requests`); new migration `v0038_gate_notifications.py` (`public.
  gate_notification`, no ontology change); new routes `GET /v1/gate-inbox`, `POST /v1/
  gate-inbox:notify` (`routes_gate_inbox.py`); new `GateInboxReaderDep`/`require_gate_
  inbox_reader` in `deps.py` (Artizent, or any of `client_data_owner`/`client_report_
  owner`/`client_licence_admin`).
- `services/console-web`: new `inbox/GateInbox.tsx`; `lib/api.ts` gained `gateInbox`/
  `notifyGateInbox` and their real response types (`GateInboxItem`, `GateInboxResponse`,
  `GateInboxNotifyResponse`); `App.tsx` gained a new `inbox` surface, added to
  `CLIENT_VISIBLE_SURFACES` for all three client roles without changing any of their
  existing `LANDING_SURFACE` entries — a deliberately narrow addition, the identical
  posture the Migration Unit page just took for `client_report_owner`.
- Verified: `services/graph-svc` — 12 new integration tests against real PostgreSQL +
  Apache AGE (a real family `IN_REVIEW` appearing and correctly domain-filtered; a real
  composed report with a real passing `ParityRun` appearing as an open G3 card,
  disappearing once really `APPROVED` but staying open after a real `CHANGES_
  REQUESTED`; a real, fully-ready site appearing as an open G4 card, excluded once
  approved, kept after a real deferral; the inbox proven role-dispatched across data
  owner/report owner/Artizent; a real "new request" notification proven idempotent; 3
  HTTP-level tests including a real role refusal); the full integration suite re-run
  clean afterward (771 passed, 2 skipped, no new failures); `ruff`/`mypy`/
  `ontology_check.py`/`migration_check.py` all clean. `services/console-web` — 412
  tests passing (12 new: the real role-dispatched items rendering, the honest absence
  of a fabricated G3/G4 due date, the countersigner-role disclosure, gate-type/site
  filtering, decision controls hidden from a non-approver, real approve/request-
  changes/ask-a-question/defer actions each reusing the identical existing per-gate
  fake, and the notify action); a real, pre-existing test assumption broke as a direct,
  intended consequence of this story (`client_data_owner` was the fixture's own
  "single-surface client role" example — it now has two) and was retargeted at
  `client_infosec_reviewer`, still genuinely single-surface, rather than merely
  loosened; `tsc --noEmit`/`eslint`/`vite build` all clean.
- Live-smoke-tested against the real Docker stack (both images rebuilt): every role's
  own inbox read returned in under 30 ms against the real demo estate; with nothing
  currently open, all four roles honestly saw an empty inbox. A real family was driven
  through its own real lifecycle (propose design → accept → assign owner → submit for
  review, all via the real existing routes) to produce a genuine open G2 item, which
  then appeared correctly for a real `client_data_owner` identity and was correctly
  absent for a real `client_report_owner` identity. A real `POST /v1/gate-inbox:notify`
  call recorded and logged one real new-request notice; an immediate second call sent
  none (real idempotency confirmed live). In the running console, the Gate Inbox screen
  rendered the real card with its real countersigner-role disclosure; a real Approve
  action (typed rationale and countersigner) through the UI wrote a real `GateDecision`
  and the family's own real lifecycle state advanced past `APPROVED`, after which the
  item correctly disappeared from the inbox. G3/G4 populated-card scenarios were not
  separately live-smoke-tested (no real candidate existed in the demo estate and
  producing one would need materially more live setup than G2's); both paths are
  already proven against the real database by the integration suite above.

## Alternatives considered

**Build one flat "any client role" reader dependency and filter items by role inside
the handler.** Rejected — see decision 1. The console's own identity model asserts
exactly one role per session; a caller's role already determines which gate(s) they
could ever act on, so dispatching which pending-list functions even run (rather than
computing all three and discarding two) is both the honest reading of "shows only the
requests waiting for me" and the cheaper one.

**Reuse `g2_reminders.NotificationChannel` directly for Gate Inbox notifications
instead of a new Protocol.** Rejected — see decision 7. That Protocol's own `notify`
signature is typed to G2's own `PendingReview` dataclass plus a day-threshold `int`;
a Gate Inbox item spans three different real subject shapes and carries no day-
threshold concept. The same pattern, a different, correctly-typed implementation.

**Give the Gate Inbox its own new approve/request-changes/ask-a-question/defer
routes**, so its own API surface is self-contained. Rejected — see decision 6. Every
one of those actions already exists, gated to the identical real approver role; a
second copy would be a second implementation of an identical decision that could
quietly diverge from the first, the exact risk this codebase has avoided everywhere
else a "cross-epic private helper" is reused instead of restated.

**Compute a derived "domain" for G3/Site or G4/Workbook**, so the "my domain" filter
applies uniformly across all three gates. Rejected — see decision 2. No such concept
exists in the ontology today; inventing one solely to make a filter feel complete would
be exactly the kind of screen-only fiction this codebase's own risk register exists to
block. An honest `domain: null` for those two gates is the correct reading until a real
story gives `Workbook`/`Site` a real domain concept of their own.
