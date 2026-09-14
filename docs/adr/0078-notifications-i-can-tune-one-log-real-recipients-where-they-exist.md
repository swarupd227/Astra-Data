# ADR 0078 — Notification preferences: one general log, real recipients where they already exist

Status: accepted · 14 September 2026 · Story S10.5.2, opening F10.5

## Context

S10.5.2 — the backlog's own AC, verbatim: *"As a report owner, I want notifications I
can tune, so that I hear about my reports, not everyone's."*

- Per-user preferences: channels (email, Teams), events (gate request, exception
  assigned, regression fail, train re-plan), digest mode (immediate, daily)
- Notification content never includes data values; it links to the console

**"Per-user" is confirmed real, not per-role, by direct reading of `roles.py`/
`Principal`** — nothing ties a preference to one role, and the four named events each
address a genuinely different kind of real recipient: a G2 item's own `review.approver`,
an `ExceptionCase.assignee`, and (for the other two) a workbook's own real `OWNED_BY`
owner — the exact mechanism `ontology/edges.py`'s own `OWNED_BY` note already names:
*"Ownership routes gate requests to a named person (spec §15.1)"*, story S1.2.3. This
story reuses that mechanism for three more events rather than inventing a second one.

## Decisions

### 1. One general table, not a fourth narrow one

`g2_reminder` (v0017) and `gate_notification` (v0038) each earned their own table
because each was the *first and only* mechanism of its own kind. This story is the first
to ask for one *tunable* mechanism spanning several event types at once, so
`public.notification_log` generalises the identical `(subject_ref, sent_at)` shape those
two already set, adding `event_type`/`recipient`/`channel` — the honest generalisation
now that a real, shared concept (preferences) sits above all four, not a fourth
near-duplicate schema. `public.notification_preference` is a second, small table: one
row per `(graph, principal)`, overwritten in place (not versioned) — a person's own
mutable setting, the identical shape `App.tsx`'s own `locale` selector (S10.5.1) has on
the console side, made durable here since a preference needs to survive a session.

### 2. A queued row and a sent row are the same record, distinguished by `sent_at`

`digest_mode="immediate"` records-and-logs the instant an event fires; `digest_mode=
"daily"` records the identical row with `sent_at` left `NULL` — a real, durable queue
entry, not an in-memory one a restart would lose. `POST /v1/notifications:send-digests`
is what flips queued rows to sent, batched one digest per `(recipient, channel)` — the
identical "manually/repeatably-triggered POST action, not a live cron job" disclosed
limitation `g2_reminders.send_due_reminders` already carries (confirmed by direct
search: no APScheduler/Temporal/cron mechanism exists anywhere in this codebase, and
this story does not add one).

### 3. Real recipients are reused where they already exist, not invented where they do not

Four different real write-site integrations, each additive and backward-compatible
(`preference_store: NotificationPreferenceStore | None = None`, the identical shape
`ExceptionDeskService.__init__`'s own `notification_channel` parameter already takes —
no store, no notification, every existing caller unaffected):

- **Gate request** — a *second*, real, preference-gated pass added to the Gate Inbox's
  existing notify action, alongside (not instead of) `gate_notification`'s own broadcast
  record. Only G2 items carry a real named principal (`detail.approver`,
  `gate_inbox.pending_g2_items`, S10.4.1); G3/G4 items carry no real named approver (a
  disclosed gap `gate_inbox.py`'s own docstring already names) and are silently skipped.
- **Exception assigned** — `exception_desk.bulk_assign` fires directly to `assignee`,
  the real, immediate recipient an assignment already names; no owner resolution needed.
- **Regression fail** — `regression._run_regression_check` keeps its own existing
  role-broadcast `notifier.notify_regression` call unchanged, and *adds* a second,
  real, preference-gated notification to the workbook's own resolved `OWNED_BY` owner
  when one exists.
- **Train re-plan** — `train_overrides.move_mu` (only — not `resequence_mu`/
  `set_wip_limits`, a real, disclosed narrowing to the one action that actually changes
  a workbook's own train, not every within-train reorder) fires to the same real
  resolved owner.

### 4. A recipient string is only ever trusted when it is already shaped like a real `Principal`

`notify()` checks every `recipient` against `principal.py`'s own `_PRINCIPAL_RE`
(`user:`/`agent:`/`service:<value>`) before ever looking up a preferences row —
`exception_desk.bulk_assign`'s own `assignee` is free text (confirmed: no format
validation exists), the identical "plain, unverified typed string" limitation this
codebase's own countersigner fields already disclose everywhere else. A recipient typed
as a plain display name simply matches no preferences row and is silently skipped — the
honest behaviour for an unresolvable recipient, not a fabricated one.

### 5. Content is structurally incapable of carrying a data value

Every call site passes a `summary` (a subject name/id, a role or event label) and a
`link` (a real console deep link — reusing `gate_notifications.deep_link`'s own
convention where a screen already has one: `ExceptionDesk.tsx`'s `?case=`,
`RegressionMonitor.tsx`'s `?workbook=`, or `/trains` for a re-plan, which has no
per-train deep link of its own). There is nowhere in `notify()`'s own signature to pass
a measure, a cell value, or a formula — the AC's own "never includes data values" is
enforced by the shape of the call, not a filter applied after the fact.

### 6. Two real, unrelated CSS bugs found while showing the new screen live

Adding a 21st real top-level surface (Notification Preferences) surfaced two real,
pre-existing gaps neither caught by the test suite (jsdom applies no real CSS layout):
the new screen's own workspace class was left out of the single-column override list
(the identical mistake S10.5.1's own ADR 0077 had just fixed for two other screens —
made again, here, on the very next story); and the nav strip itself, now carrying 21
real tabs, no longer fit a normal 1200px-wide window with no wrap active outside the
≤860px breakpoint and `.topbar`'s own default `overflow: visible` — the overflow tabs
rendered past the window's own right edge with no scrollbar and no way to reach them at
all. Both found live (via `document.body.scrollWidth`/`getBoundingClientRect()`
measurement, not by eye) and fixed before this story shipped: the workspace class joined
the existing override list, `.surfaces` gained `overflow-x: auto` with `flex: 1 1 auto;
min-width: 0` so the tab strip scrolls horizontally rather than overflowing invisibly,
at any width, not only the ones a future story might happen to test.

## Consequences

- `services/graph-svc`: new `notification_preferences.py` (`NotificationPreferences`,
  `NotificationPreferenceStore`, `PostgresNotificationPreferenceStore`, `notify`,
  `resolve_workbook_owner`, `send_pending_digests`); new migration
  `v0039_notification_preferences.py` (`public.notification_preference`, `public.
  notification_log`, no ontology change); new routes `GET`/`PUT /v1/notification-
  preferences`, `GET /v1/notification-preferences:options`, `POST /v1/notifications:
  send-digests` (`routes_notifications.py`, the first two/three open to any
  authenticated principal, the last `ArtizentDep`). Four additive, backward-compatible
  integrations: `exception_desk.bulk_assign`, `regression._run_regression_check`/
  `RegressionScheduler`, `train_overrides.move_mu`, `routes_gate_inbox.py`'s own notify
  action.
- `services/console-web`: new `notifications/NotificationPreferences.tsx` (visible to
  every role — the one screen in this console with no role gate of its own, since
  preferences are self-service); `lib/api.ts` gained `notificationPreferences`/
  `saveNotificationPreferences`/`notificationPreferenceOptions`/
  `sendNotificationDigests` and a new `put()` helper (the first `PUT` route this console
  has ever called). `App.tsx` gained a `notifications` surface, appended to every client
  role's own `CLIENT_VISIBLE_SURFACES` entry (including `client_programme_sponsor`,
  previously absent from that table entirely).
- Verified: `services/graph-svc` — 21 new integration tests against real PostgreSQL +
  Apache AGE (honest defaults; a saved preference overriding them; validation errors;
  `notify()` silent on an opted-out event, an empty channel set, or an unshaped
  recipient; idempotency; `digest_mode="daily"` queuing and `send_pending_digests`
  flushing one batch per recipient/channel; `resolve_workbook_owner` reading the real
  `OWNED_BY` edge and honestly returning `None` for an unowned workbook; `bulk_assign`
  and `move_mu` each firing a real, end-to-end notification row; 6 HTTP-level tests
  including the `ArtizentDep` gate on `:send-digests`); the full integration suite
  re-run clean afterward (808 passed, 2 skipped, up from 787); the full unit suite
  re-run clean (1504 passed, confirming the four additive integrations broke nothing
  already there); `ruff`/`mypy`/`ontology_check.py`/`migration_check.py` all clean.
  `services/console-web` — 446 tests passing (10 new: 8 for the screen itself, 2 new
  axe/WCAG checks reusing S10.5.1's own tooling); one pre-existing test retargeted a
  third time (`app.test.tsx`'s own "single-surface client role" — no client role has
  exactly one surface left once Notification Preferences reaches all of them, so the
  test now checks the still-meaningful invariant of a minimally-privileged role's own
  nav instead); `tsc --noEmit`/`eslint`/`vite build` all clean.
- Live-verified against the real Docker stack (both images rebuilt, since this story
  touches both services): the screen shown running, loaded with real honest defaults, a
  real channel/digest-mode change saved via a real `PUT` that returned 200 and updated
  the on-screen `saved <timestamp>`, confirmed by re-reading the page; "Send digests
  now" shown for a real Artizent identity, absent for a real client one; the two CSS
  bugs (decision 6) found and fixed live, each re-verified afterward by direct
  measurement (`bodyScrollWidth` equal to the viewport, the nav's own content correctly
  contained and scrollable) before the story was considered done.

## Alternatives considered

**A fourth narrow table, `notification_preference_sent`, mirroring `g2_reminder`/
`gate_notification` exactly.** Rejected — see decision 1. Those two tables each existed
before any shared "preferences" concept did; building a third and fourth copy of the
identical shape now that one real, general concept spans all of them would be repeating
a pattern past the point it still fit.

**Resolve a recipient for every event the same way (always via `OWNED_BY`), for
consistency.** Rejected — see decision 3. `exception_assigned`'s own real, immediate
recipient is `assignee` itself; resolving a workbook's owner instead would be answering
"who owns this workbook" when the AC's own question is "who was this just assigned to,"
a different, real fact already sitting in the write that triggers the notification.

**Validate/coerce `assignee` into a real `Principal` shape as part of this story**, so
`exception_assigned` never silently misses a recipient. Rejected — out of scope: `bulk_
assign`'s own free-text `assignee` field is `exception_desk.py`'s (S8.3.1's) own design,
unrelated to this story's own AC; changing its validation would be a second story's
worth of change bundled into this one, for a persona (the Exception Desk's own assignee
UI) this story never touches.

**Build a real, always-on background scheduler for daily digests**, matching
`RegressionScheduler`'s own shape more literally than a manually-triggered POST does.
Rejected — see decision 2. No cron/scheduling mechanism exists anywhere in this
codebase to trigger such a thing on a real calendar cadence; building one would be
materially more infrastructure than this story's own AC asks for, and the identical
"manually triggered for now, disclosed" posture `g2_reminders.send_due_reminders`
already established for the closest real precedent.
