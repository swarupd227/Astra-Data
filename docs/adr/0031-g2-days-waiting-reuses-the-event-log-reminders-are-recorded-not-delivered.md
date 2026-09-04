# ADR 0031 — G2 days-waiting reuses the event log; reminders are recorded, not delivered

Status: accepted · 4 September 2026 · Story S4.2.2 (E4 / F4.2)

## Context

S4.2.2 asks for the Programme Board's own view of G2: *"Board tile shows families awaiting
G2, days waiting, and the approver; SLA breach (default 5 working days) is highlighted.
Reminder notifications are sent at 3 and 5 days."*

Three questions decided the shape of the work: where "days waiting" comes from when nothing
in this codebase has ever measured a wait against `IN_REVIEW` before; what "the approver"
refers to when no story has ever needed one before; and what "sent" can honestly mean for a
notification channel this codebase has never built.

## Decisions

### 1. Days waiting is read from the event log, the same technique `family_transition_history` already uses

`model_lifecycle.family_transition_history` (S4.1.2) already finds genuine `ModelFamily`
state transitions with a `LAG() OVER (PARTITION BY subject ORDER BY seq)` window over
`estate_event` — a re-upsert that carries the same state forward is correctly not counted.
`g2_reminders._entered_review_at` asks the identical question one step further: not every
transition, only the most recent one into `IN_REVIEW`, batched across every family
currently awaiting G2 in one query. `train_projection.working_days_between` — the exact
function S3.2.3 already built for "5 working days late" — turns that timestamp into a
working-day count; a second implementation of Monday-to-Friday counting would just be a
second place it could disagree with the train projection's own figure.

### 2. "The approver" is `ModelFamily.owner` — declared since S1.1.1, never written until now

`owner` has been part of `ModelFamily` since S1.1.1, is already read by
`cartographer._family_summary`, and — like `domain` before S4.2.1 (ADR 0030) — no story
before this one ever wrote it. `model_lifecycle.update_owner` is added on the identical
footing as `update_domain`: DRAFT-only, a plain string, no directory resolution (E11's own
future scope). This means assigning an approver can only happen *before* a family reaches
`IN_REVIEW` — the very state the board tile lists — so a family a Semantic Model Engineer
never assigned an owner to reaches the tile as `unassigned`, not defaulted to anyone. No
console screen currently edits it (the same gap `domain` has had since S4.2.1 shipped with
no UI either); a future story can wire either into Model Detail's Design tab without
touching this one's own read.

### 3. SLA breach reuses S3.2.3's own 5-working-day default; reminders are recorded, not delivered

`DEFAULT_SLA_WORKING_DAYS` is imported from `train_projection.DEFAULT_LATE_THRESHOLD_
WORKING_DAYS` rather than restated — the spec names "5 working days" for two different
lateness flags (§14.2's train projection, §15.3.1's G2 wait) and nothing before this story
needed the second one.

"Reminder notifications are sent at 3 and 5 days" cannot mean genuine outward delivery: no
email, chat or webhook channel exists anywhere in this codebase, the same footing §21 gives
work-tracker mirroring. `g2_reminders.NotificationChannel`/`LocalNotificationChannel` is the
exact `IssueTracker`/`LocalIssueTracker` precedent (`grammar.py`, S1.4.3) — a reminder is
recorded and logged, not silently dropped because nothing real is wired. What *is* real:
`public.g2_reminder` records one row per `(family, day)`, unique, so
`POST /v1/g2/reminders:send` is safe to call from anywhere — a Programme Manager's own
click, a future scheduler tick — without ever sending the same threshold twice. *When* it
runs is not yet automated; a background loop on a timer (the same shape `HarvestScheduler`,
S1.2.4, already gives this codebase) is real future scope this story does not claim, so the
Programme Board's own "Send reminders" button is, today, the trigger.

## Consequences

- A new platform table, `public.g2_reminder` (migration v0017) — the same "record work, no
  ontology node" footing `g2_question` (v0016) already established.
- No ontology change: `ModelFamily.owner` was already declared; this story is the first to
  write it, the identical trajectory `domain` took at S4.2.1.
- The Programme Board gains a third pane, `G2 reviews` — family, domain, days waiting,
  approver, open questions and an SLA pill, plus the reminder trigger. Read access is
  `ArtizentDep`, the same posture the board's other two panes already have (a Programme
  Manager is who this pane is for, but any Artizent role may read it, matching every other
  families/trains read in this API).
- Assigning an approver has no console UI yet — an API-only action, same as `editDomain`.

## Alternatives considered

**A second working-day-counting implementation, local to this module.** Rejected — see
decision 1. `train_projection.working_days_between` is already the checked, tested
implementation; importing it is strictly better than a second copy that could drift.

**Default an unassigned family's approver to the Semantic Model Engineer who submitted it,
or to "unknown".** Rejected — see decision 2. Either would misrepresent who is actually
expected to act; `unassigned` on the tile is the honest reading of "nobody has said," and is
itself the signal a Programme Manager needs to chase the right person into existing.

**A background scheduler that sends reminders on a timer, mirroring `HarvestScheduler`.**
Rejected for this story — see decision 3. The recorded, idempotent mechanism this story
builds is what a scheduler would call; adding the scheduler itself before any story asks for
automated timing would be speculative infrastructure ahead of a real requirement.
