# ADR 0007 — Incremental harvest, a schedule that is data, and the first notice in the outbox

Status: accepted · 2 September 2026 · Story S1.2.4 (E1 / F1.2)

## Context

S1.2.4 is the platform engineer's story: keep the graph current through a long programme
without re-parsing the whole site. It asks for scheduled runs that detect new revisions
through the Metadata API's `updatedAt` and re-parse only those workbooks, a `SOURCE_DRIFT`
event when a changed workbook already has a Migration Unit in progress, and the schedule
and its last run visible on Platform Health.

S1.2.1 already skipped unchanged workbooks — but only after downloading them. The content
hash is computed from the fetch, so a nightly run over a 1,000-workbook site cost 1,000
downloads to discover that four had changed. That is the gap this story closes, and it is
the difference between a schedule a client will tolerate and one they will turn off.

## Decisions

### 1. Two modes, and the default is the honest one

`HarvestMode.FULL` fetches everything and compares content; `HarvestMode.INCREMENTAL`
consults the enumeration's `updatedAt` and skips the fetch. A request from the API defaults
to FULL, because an operator asking for a harvest by hand nearly always means "look
properly"; a schedule asks for INCREMENTAL, because that is what makes it affordable.

The two skips are counted separately — `skipped_unchanged` (fetched, found identical) and
`skipped_not_modified` (never fetched). The difference between them *is* the saving, and a
single counter would hide whether incremental detection was working at all.

### 2. Six conditions, every one of them a way to be wrong

A workbook is skipped without fetching only when: it has been harvested before, the source
reports an `updatedAt`, the platform recorded one last time, the revision has not moved, the
timestamp has not moved, and the grammar is the one it was last parsed under.

The grammar clause is the one worth arguing for. Without it, extending the grammar — S1.2.2's
other route out of the Parse Quality Queue — would never reach a workbook that had not
changed, so a held workbook would stay held however far the grammar advanced. It appears
twice: in the incremental predicate, and in the content-hash comparison, because a *full*
run never consults the first one. That second placement was a real defect, found by a test
written for the first.

Timestamps are compared as instants, not as text: `...Z` and `...+00:00` are the same moment
and a string comparison calls one of them newer.

The migration adds `source_updated_at` with **no backfill**. A workbook harvested before this
story has no recorded source timestamp, and inventing one — `harvested_at`, say — would claim
the source had not changed since a moment the source never mentioned. NULL makes the first
incremental run fetch it once and record the truth: one extra pass over the estate, in
exchange for never skipping a workbook on a guess.

### 3. The schedule is a row, not a cron entry

A cron entry in a container image cannot be paused from the console, does not survive a
redeploy with its history, and cannot tell Platform Health when it last ran. §15.3.3 puts
"scheduler pauses" on that screen and §12.3.1 alerts on "scheduler starvation"; both need
the schedule to be something an engineer can read and change.

Due schedules are claimed with `FOR UPDATE SKIP LOCKED`, and `next_run_at` is advanced in the
same transaction. Two replicas polling the same second start one run between them, not two —
which matters because the deployment target scales out, and a double harvest of a large site
is hours of wasted source I/O against the client's server. `next_run_at` is computed from
*now* rather than from the old value, so a service that was down for a day harvests once when
it comes back rather than once for every firing it slept through.

Cadence is deliberately small: every N minutes (floor of five), or daily at a UTC time. Not
cron — cron without timezones is a trap for a client in Sydney, and cron with them is a
library and a class of bugs this story does not need.

A schedule that fails five times running is paused automatically, with the last error as its
reason. A site whose credential expired should raise an alert, not hammer the source every
night until somebody notices the graph is stale.

### 4. There is no delete, and that stayed true

The first draft of the API had `DELETE /v1/harvest-schedules/{id}`. S1.1.3's guard — asserted
against the router, not against a URL — failed, which is exactly what it is for. The guard was
right and the endpoint was wrong: "there used to be a nightly harvest of this site" is
precisely the fact somebody needs when they ask why the graph went stale in March.

So a schedule that should stop is paused, with a reason; one that is simply wrong is amended
in place with `PATCH`, which keeps its run history attached to its scope. Amending a cadence
re-bases the next firing, or a schedule changed from quarter-hourly to daily would fire in
eleven minutes. The scope itself is not amendable: a schedule of a different site is a
different schedule.

### 5. A notice shares the mutation outbox, and replay skips it

`estate.source.drift` is the first event that records no graph change. It goes into
`estate_event` alongside the mutations rather than into a table of its own, because the bus is
one ordered stream: a consumer that sees a drift notice at sequence 412 needs to know it comes
after the upserts at 400–411, and two tables cannot say that.

`EventType.mutates_graph` divides the two. Replay applies only the mutating ones and counts
what it passed over; the repository refuses to append a *mutating* event outside a
transaction, which keeps S1.1.3's argument — an event committed apart from its mutation
breaks replayability — true rather than assumed. The class was renamed `MutationEvent` →
`PlatformEvent` to stop it lying about what it holds.

The outbox's `subject`, `label` and `element_kind` columns still describe an element: a drift
notice's subject is the Workbook node it is about. "What is this event about" is a useful
question of every event; it is the *type* that says what happened to it.

### 6. Drift is announced after the write, not on detection

Two conditions, both required: the content actually changed since the platform last recorded
it — a first harvest is not drift, and neither is a re-publish of identical bytes — and an MU
over the workbook is past HARVESTED.

The notice is emitted after the new parse is written, so "re-prove this" is a request somebody
can act on: there is something in the graph to prove against. A workbook that changed but
failed to parse is a recorded failure instead, and the MU still stands on the last version
that did parse. The counter-argument is real — the source moved either way — and the open
questions below carry it.

At HARVESTED nothing has been built from the old version, so the re-parse *is* the update and
there is nobody to interrupt. RELEASED is deliberately in scope: the backlog has the Steward
re-running parity "weekly during parallel run, on SOURCE_DRIFT", and a source changing under a
report already in production is the case that costs a client money.

### 7. The Migration Unit is a port with nothing behind it

`MigrationUnitRegistry`, with `NullMigrationUnitRegistry` as the only production
implementation. §4.1 defines no MU node, because an MU is a record of *work*, not a fact about
the client's estate; it belongs to the control plane E3 builds. Until then no workbook has
work in progress over it, so no harvest can disturb any — and Platform Health reports which
registry answered, so "no drift" and "nothing was asked" stay distinguishable. Same shape as
the directory resolver in ADR 0006.

A registry that refuses the re-proof mark does not fail the harvest. The workbook is correctly
in the graph either way, and losing a parse because a downstream service was unreachable would
be the worse outcome; the notice records whether the mark was accepted, so it never claims a
re-proof was requested when nothing took it.

## Consequences

- Migration 6. The ontology is unchanged — a schedule is a platform record, not an estate
  fact — so the schema version stays 5 and the lock file is untouched.
- `estate_event` gains an index on `(graph, type, seq DESC)`. Platform Health lists recent
  drift notices, and finding them by paging the whole outbox would scan every mutation ever
  written.
- The fixture adapter's content hash no longer includes `revision` or `updated_at`. They are
  what the source says *about* a workbook, and folding them into the hash made every metadata
  touch look like a content change.
- Measured on the fixture estate: a full harvest of 40 workbooks takes 12 seconds and 40
  downloads; the scheduled incremental run that follows takes under a second and none.

## Open questions for the product owner

1. **Drift on a workbook that changed but would not parse.** Today that is a recorded failure
   and no notice, on the grounds that there is nothing new to prove against. The other reading
   is that an MU whose source has moved *and* can no longer be parsed is in more trouble, not
   less, and should be shouted about sooner. This is a process question about what the
   Exception Desk should see.
2. **Cron expressions and client timezones.** "Daily at 02:00 UTC" is 13:00 in Sydney. A
   client who wants the harvest in their own overnight window cannot express it. Adding a
   timezone to the daily form is small; adding cron is not, and neither should be built before
   somebody says which is wanted.
3. **The automatic pause threshold is fixed at five.** It should be a per-tenant setting, and
   §22 has no store for one yet. Five consecutive nightly failures is five days of a silently
   stale graph, which may already be too long.
4. **Nothing tells anyone.** A paused schedule, a drift notice and a failing run are all
   visible on Platform Health and none of them are pushed anywhere. Notification is E12's, but
   the question of *who* should hear about source drift — the migration engineer holding the
   MU, the programme manager, the client report owner — is a programme decision, not a
   technical one.
5. **The scheduler is in-process.** The schedule row is durable and the claim is safe across
   replicas, but a run interrupted by a restart is left showing RUNNING and is not resumed;
   the next firing picks the work up. Durable orchestration is Temporal's (E12/F12.1). Whether
   that is soon enough depends on how long the programme runs before E12 lands.
