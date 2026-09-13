# ADR 0072 — Rebuild, stream and explain reuse the real outbox, not a new projection layer

Status: accepted · 13 September 2026 · Story S10.1.2, continuing F10.1

## Context

S10.1.2 continues F10.1 — the backlog's own AC, verbatim: *"As a platform engineer, I
want screens to be event-sourced projections with a query API, so that the console never
shows a state the evidence chain does not have."*

- Projections are rebuilt from the event stream; a rebuild from empty is a supported
  operation with a progress indicator
- Live updates over server-sent events for queues and boards; p95 screen update within 2
  seconds of the event
- Every number on a screen has an "explain" affordance that opens the query or the
  events behind it

Confirmed by direct research before writing anything: every console screen already
computes its response by querying the live Apache AGE graph fresh on every request — no
materialised read-model, cache, or second projection layer exists anywhere in this
codebase. A real, durable, replayable event log already does exist (`public.
estate_event`, one row per mutation, written in the same transaction as the change it
describes — `events.py`'s own docstring), and a real replay-and-compare engine already
proves it (`replay.py`, exercised nightly by `tools/verify_replay.py`, S1.1.3's own
"a replay of the event stream from empty produces a graph identical to the live graph").
No SSE, WebSocket, or other push mechanism exists anywhere, and no generic "explain this
number" affordance exists either (`provenance.py`'s own "provenance" is the Transpiler's
model-call record, a different, already-real concept). §5.4 states "consoles are
event-sourced views," but the services §5.2 names for this (`evidence-svc`, `console-api`,
`event-bus`) do not exist as code anywhere in this repository.

## Decisions

### 1. "Rebuild from empty" proves the event stream; it does not switch the console over to it

A hot cutover of the live serving graph to a freshly-replayed one is a materially
different, riskier operation than anything this AC's own words ask for, and no story has
built the infrastructure a safe cutover would need (dual-write reconciliation, a
promotion gate, rollback). Instead, `POST /v1/graph:rebuild` runs the identical
replay-and-compare machinery the nightly CI job already runs — `replay.py`'s `replay`/
`compare`, `graph/scratch.py`'s `prepare_scratch_graph`/`teardown_scratch_graph` (both
newly extracted from `tools/verify_replay.py`'s own local copies, so the CI job and this
route are provably the same code, not a second implementation that could quietly
diverge) — into a scratch graph, then reports whether the replay reproduces the live one
exactly. `routes_rebuild.py`'s own module docstring states this reading explicitly.

Progress is an in-memory `RebuildStatus`, the identical shape `TrainProposalStatus`
(`routes_trains.py`, S3.2.1) already set for the same reason: a rebuild is a rare,
operator-triggered verification, not a fact anything else in this platform depends on
later. `replay()` gained an optional `on_progress` callback (called with the cumulative
count after each page) — additive; the CI job calls the identical function with no
callback at all, unchanged.

**A real, live bug found and fixed by testing this against real data, not a fixture:**
`current_version()`'s own returned value (the highest `seq` for a graph) was the wrong
number for a progress denominator — `seq` is one `bigserial` shared across every graph
`estate_event` has ever recorded, so a brand-new test graph's own `current_version()`
was `2,253,395` (the shared demo estate's own running total) before a single event of
its own existed. `AgeGraphRepository.count_events()` is new: a real `count(*) WHERE
graph = $1`, the actual number of this graph's own events, added to `GraphRepository`'s
own protocol alongside a new `graph_name` property (`routes_rebuild.py` reads the
injected repository's own graph name rather than re-deriving it from global `settings()`
— the second real bug this testing found: a route that re-reads global config instead of
asking the object it was actually given drifts the moment a caller wires a different one,
exactly what a test fixture does and a multi-tenant deployment eventually would too).

**A third real bug, also found only against real PostgreSQL + Apache AGE:** tearing down
the scratch graph reused a connection from the same pool everything else in the rebuild
used, and Apache AGE caches label relations per session — a second `drop_graph` on a
connection that already did graph DDL fails with "label (relation) cache corrupted" and
closes the connection (the identical, already-disclosed quirk `test_integration_events.
py`'s own fixture teardown works around with "one connection per drop"). Because this
failure landed inside `_run`'s own `finally` block, after the two lines that reset
`tracker.running`, a real rebuild that finished correctly still left the console's own
progress indicator spinning forever. Fixed two ways: `prepare_scratch_graph`/`teardown_
scratch_graph` now each open and close a dedicated `asyncpg.connect()`, never borrowed
from the shared pool; and the teardown's own failure is caught and logged rather than
allowed to skip the tracker's own completion — a scratch graph that could not be dropped
is a real, secondary problem, not a reason to hide that the rebuild itself succeeded.

### 2. Live updates are the same outbox, pushed instead of pulled — no message bus invented

`GET /v1/events:stream` polls the identical `repository.read_events(after=...)` the
existing `GET /v1/events` route already reads, in a loop, forwarding each new row as an
SSE frame — 0.5-second poll interval, comfortably inside the 2-second budget without
`LISTEN`/`NOTIFY` or a real broker (`published_at` on every outbox row stays `NULL`
until E12's own bus publisher exists; this story does not build one, since nothing in
its own AC asks for delivery guarantees beyond "the console catches up quickly").

**Deliberately open, inheriting `GET /v1/events`'s own posture, for two reasons.** That
route already carries no `PrincipalDep`/role gate (confirmed by direct reading) — the
raw outbox names no fact a gated screen does not already render from it. The second
reason applies only to the stream: a browser's native `EventSource` cannot send custom
request headers, so it cannot carry this console's own `X-Astra-Principal`/`X-Astra-
Roles` identity headers no matter how the route were gated — an open route is not a
weaker posture chosen for convenience, it is the only one a native `EventSource` client
could ever satisfy.

**Tested by calling the real async generator directly, not through a full HTTP round
trip.** `StreamingResponse`'s own generator never terminates on its own (a live feed has
no natural end), and consuming an unbounded stream through `httpx`'s `ASGITransport`
with a bounded timeout hung indefinitely in practice — confirmed live: the connection
never even completed opening within `asyncio.wait_for`'s own timeout, a specific,
reproducible incompatibility between that combination and this shape of response, not a
flake. The tests instead call `_stream` (the real generator `events_stream` hands to
`StreamingResponse`) directly, against the real repository, with a minimal stand-in for
`Request` (`_stream` only ever calls `is_disconnected()`) — the identical production
code, exercised without the one layer that would not cooperate with a bounded test. The
route function itself is covered by a separate, non-streaming test checking its response
shape (media type, the `X-Accel-Buffering: no` header that defeats nginx's default
response buffering — a real, necessary addition to `console-web`'s own nginx.conf's
`/v1/` proxy location, since buffering would silently blow the 2-second budget
regardless of how fast the loop itself pushes frames).

**One `EventSource` for the whole console shell, not one per screen.** `lib/live-events.
ts`'s `useLiveTick` opens a single connection in `App.tsx` and exposes a plain
incrementing counter; every screen that already re-fetches on some other counter
changing (`nonce`, `queueNonce`, ...) adds this one to the same dependency array — no new
fetch plumbing, and no risk of the same event describing something different to two
screens that each parsed its payload their own way. The counter deliberately carries no
payload: a screen that wants to know what changed re-fetches and reads the real,
current, freshly-queried state back — the identical "the read is the fact, not the push"
posture this whole story is built on. Wired into the AC's own named "queues and boards"
— Exception Desk, Regression Monitor, Wave Board, and every one of the Programme Board's
six panes (all sharing one `Props` shape, so one prop threads through all of them).

### 3. Explain is a registry of the real thing, not a description of it

`explain.py`'s `EXPLAIN_REGISTRY` maps a metric key to an entry whose `text` is copied
verbatim from the real implementing code and whose `source` names the exact file and
line range — found by reading the actual computation, not written fresh for this
screen. Most entries are `kind: "computation"` (plain Python arithmetic over rows a
query already fetched), confirmed the more common shape by direct research across every
screen covered; a `kind: "sql"` entry carries the literal query string instead. Either
way, a platform engineer who opens the panel reads the code deciding their number at
that moment, not a paraphrase that could drift from it.

`GET /v1/explain/{metric_key}` is deliberately open, the identical reasoning `GET /v1/
events`/`GET /v1/events:stream` already give: a query's own text is not client data, and
gating it would protect nothing the number's own screen does not already protect. "The
events behind it" needed no new backend route at all — `Api.subjectEvents` on the
console side reads the existing `GET /v1/events?subject=` directly.

**Coverage is a representative first pass, not literally every digit on every screen** —
the identical disclosed-scope reading ADR 0071 already gave S10.1.1's own "every role."
Nine registry entries cover the SSE AC's own named queues and boards (Exception Desk,
Regression Monitor, Wave Board's train members/WIP, all four of Programme Board's
panes) plus Estate Explorer's workbook count and the G3 gate card's parity cases — eight
of the nine wired to a real `<Explain>` trigger in this pass (Wave Board's own
`trains.wip_status` entry is registered but not yet wired to a UI trigger, since its
table-column figure lives in a sub-component that does not currently receive `api`/
`identity` — a mechanical follow-up, not a design gap). `components/Explain.tsx` and
the registry are both generic: wiring the next screen's own number is one dict entry and
one `<Explain metricKey="...">`, not new infrastructure.

## Consequences

- `services/graph-svc`: new `explain.py` (`ExplainEntry`, `EXPLAIN_REGISTRY`); new
  `graph/scratch.py` (`prepare_scratch_graph`/`teardown_scratch_graph`, extracted from
  `tools/verify_replay.py`); `replay.py`'s `replay()` gained an optional `on_progress`
  callback; `graph/repository.py`'s `GraphRepository` protocol gained `graph_name`
  (property) and `count_events()`; new routes `POST /v1/graph:rebuild`, `GET /v1/graph:
  rebuild/status` (`routes_rebuild.py`, `RebuildStatus`), `GET /v1/events:stream`
  (`routes_events_stream.py`), `GET /v1/explain/{metric_key}` (`routes_explain.py`); no
  ontology change, no migration (`ontology_check.py`/`migration_check.py`/`contract_
  check.py` all confirm — schema version unchanged at 37).
- `services/console-web`: new `lib/live-events.ts` (`useLiveTick`); new `components/
  Explain.tsx`; `lib/api.ts` gained `explain`, `subjectEvents`, `startRebuild`,
  `rebuildStatus` plus their real response types; `App.tsx` opens one `useLiveTick()`
  and threads `liveTick` to `ExceptionDesk`, `RegressionMonitor`, `WaveBoard`,
  `ProgrammeBoard`; each of those screens' own top-level fetch effect(s) gained
  `liveTick` in their dependency array (mechanical, one line per effect); `quality/
  ParseQualityQueue.tsx` gained a new `RebuildPanel` (visible only to the platform
  engineer, the hide-not-disable convention every other role-gated action in this
  console already uses) with a real progress bar and the real comparison result;
  `<Explain>` wired onto eight real figures across Estate Explorer, Exception Desk,
  Regression Monitor, Programme Board (all four panes with a headline number), and the
  G3 gate card; no `nginx.conf` change was needed for the stream's own buffering —
  the route's `X-Accel-Buffering: no` response header is nginx's own built-in override,
  honoured automatically on any proxied response unless a location sets `proxy_ignore_
  headers` to suppress it (confirmed: `console-web`'s `/v1/` location sets no such
  override).
- Verified: `services/graph-svc` — 1,499 non-integration tests passed; the full
  integration suite (724 passed, 2 skipped) with two known, pre-existing, disclosed
  flakes confirmed unrelated by a clean `git diff` on every file either touches:
  `test_a_clustering_run_started_over_http_lands_in_the_graph` (a background-task/pool-
  teardown race under sustained load — passed cleanly in isolation) and `test_
  integration_g2_reminders.py::test_due_reminders_are_sent_and_recorded` (the same
  working-day/calendar-day-backdate-versus-today's-weekday flake ADR 0070 already
  disclosed in this exact test, confirmed still present in isolation since it is
  date-dependent, not load-dependent); `ruff`/`mypy`/`ontology_check.py`/`migration_
  check.py`/`contract_check.py` all clean on every file this story touches (the ruff/
  mypy findings elsewhere in the tree are pre-existing and untouched by this story,
  confirmed by `git diff`). `services/console-web` — 357 tests passing (22 new: seven
  for `Explain`, five for `useLiveTick` — using a new `FakeEventSource` test double,
  since jsdom implements no real `EventSource`, kept deliberately minimal in `tests/
  fake-event-source.ts` — six for the rebuild panel, and four confirming `liveTick`
  actually triggers a re-fetch on each of the four wired screens); one real regression
  found and fixed by the full suite: the rebuild panel's own background poll and the
  explain component's own on-demand fetch were consuming the test fixtures' single
  shared `failNext()` failure slot meant for an unrelated, intentional test action
  elsewhere in the same render — fixed by not queuing those four fake methods against
  that shared slot, since they are incidental background/on-demand calls, not the
  action a test is asserting on; `tsc --noEmit`/`eslint`/`vite build` all clean.
- Live-smoke-tested against the real Docker stack (both `graph-svc` and `console-web`
  images rebuilt): a real rebuild against the actual demo estate's own real event history
  (`count_events()`'s own fix confirmed live: 12,578 real events for `astra_estate`
  itself, not the multi-million shared `bigserial` watermark every graph this database
  has ever recorded shares) ran for just under 3.5 minutes with a real, live-polled
  progress indicator, finishing with a real `identical: true` comparison — 2,262 nodes
  and 3,067 edges reproduced exactly — shown verbatim on the console's own panel, and its
  scratch graph confirmed torn down afterward. A real `POST /v1/nodes` write while a raw
  `curl` connection held the stream open delivered the matching `estate.node.upserted`
  frame, naming the real new node's own id, in well under a second — the smoke-test node
  was retired (not deleted) afterward. The running console's own browser tab held a real,
  correctly nginx-proxied `GET /v1/events:stream` connection (confirmed via its own
  network log) while `<Explain>` opened live on `programme.class_mix` (Programme Board)
  and rendered on `exceptions.queue_count` (Exception Desk after a direct URL visit),
  each showing the real query/computation text and source line fetched from the running
  service, not a fixture.

## Alternatives considered

**Build a real materialised read-model/projection table per screen**, so "screens are
event-sourced projections" is literally true of a new data structure, not just of the
graph the event stream already built incrementally. Rejected — see the Context section:
no story before this one needed one, every screen's live-query read budget is already
measured and inside its own target (ADR 0002/0010), and inventing a cache-invalidation
problem this codebase does not have yet is exactly the "no screen without an engine
feature" scope creep E10's own risk register (cited in ADR 0071) exists to block.

**Gate `GET /v1/events:stream` behind the same identity headers every other route
uses.** Rejected — see decision 2. A browser's native `EventSource` cannot send custom
headers at all; gating this route would not make it more secure, it would make it
unusable from an actual browser.

**Test the SSE route end-to-end through a real HTTP client and a real running server**
(e.g. `uvicorn` in a subprocess) rather than calling the generator directly. Deferred,
not rejected outright — a real, live smoke test in Docker already covers this end-to-end
path (see Consequences); the in-process `httpx`/`ASGITransport` combination specifically
does not cooperate with an intentionally-unbounded streaming response in a fast unit-
style integration test, and standing up a real server process for one test file was
judged more machinery than the AC's own claim needs proven twice.

**Switch the live serving graph over to the rebuilt one on a successful, identical
rebuild** (an automatic or one-click promotion). Rejected — see decision 1. No dual-write
reconciliation or rollback path exists for a live cutover, and the AC's own words ask for
a supported *operation with a progress indicator*, not a replacement mechanism for the
graph every other screen already reads directly.
