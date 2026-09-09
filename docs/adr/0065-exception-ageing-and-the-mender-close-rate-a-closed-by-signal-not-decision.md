# ADR 0065 — Exception ageing and the Mender close rate: a `closed_by` signal, not `decision`

Status: accepted · 9 September 2026 · Story S8.3.2, continuing F8.3/E8

## Context

S8.3.2 continues F8.3, on the Programme Board — the backlog's own AC: *"As a programme
manager, I want exception ageing and close rate on the Programme Board, so that residue
does not accumulate unseen."*

- Tile shows open exceptions by class and age band; Mender close rate (failures closed
  without an ExceptionCase ÷ failures) with the R1 target ≥ 0.70

§16.6's own Accuracy metrics table, verbatim: *"Mender close rate | Failing MUs closed
without an ExceptionCase ÷ failing MUs | ≥ 0.70 | Parity Dashboard."* §25 repeats the
identical target under *"Accuracy of repair; size of the human residue."*

## Decisions

### 1. "Failures closed without an ExceptionCase" cannot be built literally

Confirmed, again: `classification.classify_run` (S8.1.1) opens a real `ExceptionCase`
for every FAIL it reads — its own two silent skips are for evidence it cannot read at
all, never for a failure that "didn't need a case." No live failure is ever resolved
*without* one existing. The honest reading — and the one both §16.6's own routing
("Parity Dashboard", the same surface `parity_dashboard.py`'s own Mender-passes trend
already lives) and §25's own "size of the human residue" wording point to — is "closed
without ever needing a human decision at the Exception Desk": the Mender's own repair
loop closed it unassisted.

### 2. The real signal is `closed_by IS NULL`, not `decision IS NULL`

`mender.mend_exception`'s own success-close write (confirmed by direct read) sets only
`state`/`passes_consumed` — `closed_by`/`closed_at` are left null. Every human-driven
close sets `closed_by`/`closed_at` for real. This includes S6.2.1's own older
`visual_redesign.close_redesign_exception`, which was confirmed by direct read to set
`closed_by`/`closed_at`/`desktop_commit_hash` but **never `decision`** — so a naive
`decision IS NULL` check would have wrongly counted a real, human-closed
`VISUAL_REDESIGN` case as Mender-closed. `state == "CLOSED" and closed_by is None` is
the real, unambiguous fact this module checks instead, proven against exactly that
scenario in both the unit and integration test suites.

### 3. `VISUAL_REDESIGN` is excluded from the close-rate ratio, both sides, but not from the ageing breakdown

It is a real `ExceptionCase` but not a real *failure* in §16.6's own sense — opened by
report composition finding an unmapped/flagged visual (S6.1.1/S6.2.1), never by a parity
diff. The Mender has no artefact to repair against one (no calculated field, no DAX) and
never attempts to; counting it would understate the real repair-accuracy signal the
metric exists to give. The open-exceptions-by-class-and-age-band breakdown is
deliberately *not* narrowed the same way — that half of the tile is honestly about every
real kind of residue sitting in the queue, `VISUAL_REDESIGN` included, exactly as the
AC's own plain "open exceptions by class" asks.

### 4. Age bands are a new, invented, disclosed bucketing, reusing `estate.Band`/`estate._band_of` verbatim

`under 1 day` / `1-3 days` / `3-7 days` / `7+ days` — the same "a real, defensible,
disclosed number" footing `estate.USAGE_BANDS` already set for view-count banding.
`estate.Band`/`estate._band_of` are reused directly (cross-epic private reuse, the same
accepted convention `case_execution._resolve_site`'s own import already established)
rather than reinventing an identical bucketing shape. Age itself reuses
`exception_desk._age_seconds` verbatim — the identical fact the Exception Desk's own
queue already sorts by.

### 5. The tile is one estate-wide aggregate, not scoped to a programme or workbook

`ExceptionCase` carries no `programme_ref`, and every other read-only Programme Board
pane this session has built (G2 cycle time, calculation class mix, rule coverage) is
estate-wide too. A per-programme scope would need a real programme-to-case link this
codebase has never had reason to build.

### 6. The new route reads `_compositor`, not a new service, and is gated the same broad way every other read-only Programme Board pane already is

`exception_ageing.exception_ageing` needs only `pool`/`graph_name`, so `GET
/v1/exceptions:ageing` calls it directly rather than adding a needless method to
`ExceptionDeskService`. Gated `ArtizentDep` — the identical posture `GET /v1/programmes`,
`GET /v1/families:awaiting-g2` and `GET /v1/calculations:class-mix` already have; this
tile has no action of its own to gate more narrowly.

### 7. `aggregate_ageing`/`exception_ageing` keep the "pure core, graph-coupled shell" split

`aggregate_ageing` takes already-hydrated `ExceptionCase` properties and does no I/O —
the identical split `parity_dashboard.aggregate_dashboard`/`.parity_dashboard` already
established, and the reason the pure unit suite could exhaustively cover every band
boundary and close-rate edge case without a database.

## Consequences

- New `services/graph-svc/src/astra_graph/exception_ageing.py`: `aggregate_ageing`
  (pure), `exception_ageing` (graph-coupled), `AGE_BANDS`, `MENDER_CLOSE_RATE_TARGET`.
- New route in `services/graph-svc/src/astra_graph/api/routes_exceptions.py`: `GET
  /v1/exceptions:ageing` (`ArtizentDep`). No ontology change, no migration, no new role.
- New console-web pane, `ExceptionAgeingPane`, the sixth on the Programme Board
  (`services/console-web/src/programme/ProgrammeBoard.tsx`) — a class × age-band grid
  and the close-rate pill/footer, read-only, no action.
- Verified: 9 new pure unit tests (`aggregate_ageing`'s own grouping, age-band labels,
  the `closed_by`-not-`decision` distinction proven directly against the scenario that
  would break a naive check, the `VISUAL_REDESIGN` exclusion, the honestly-`None` rate
  with no eligible failures, meeting and missing the R1 target); 7 new integration tests
  against real PostgreSQL + Apache AGE (real cases really grouped by class and age band;
  a real Mender-style close really counted; a real Exception Desk decision's own
  `closed_by` really excluding it; a real `VISUAL_REDESIGN` case really excluded from
  both sides of the ratio; an honestly-empty estate; the new route's own real role gate,
  including a real refusal for a client role); 4 new console tests (the grid, the honest
  "no failures yet" state, the R1-target pill in both directions, a read failure); the
  full existing graph-svc suite and console-web suite (255 passed, up from 251) both
  green alongside them; `ruff`/`mypy` clean.
- A real, fixed bug found and fixed in this story's own first integration-test draft,
  not in production code: a `Pool` created and closed inside `_run_off_loop`'s own
  throwaway thread/event loop is genuinely unstable on this platform (an intermittent
  `RuntimeError: Event loop is closed` from a `Pool.close()`-scheduled callback still
  pending when that loop tears down) — fixed by matching
  `test_integration_exception_desk.py`'s own proven convention: a single raw
  `asyncpg.connect()` for schema setup/teardown, and a plain `async def` fixture (no
  `_run_off_loop`) for the pool the test body itself uses.

## Alternatives considered

**Read `decision IS NULL` as "the Mender closed this."** Rejected — see decision 2. A
real, human-closed `VISUAL_REDESIGN` case (`visual_redesign.close_redesign_exception`)
never sets `decision` either, so this check would have silently misattributed a real
human close to the Mender.

**Include every `ExceptionCase` class, `VISUAL_REDESIGN` included, in the close-rate
ratio.** Rejected — see decision 3. The Mender can never act on one; counting it would
understate real repair accuracy without ever being fixable by better repair logic.

**Scope the tile per programme.** Rejected — see decision 5. No real link from
`ExceptionCase` to a programme exists, and no other Programme Board pane is scoped that
way either.
