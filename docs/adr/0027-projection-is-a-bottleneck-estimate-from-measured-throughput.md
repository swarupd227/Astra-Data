# ADR 0027 — Projection is a bottleneck estimate from measured throughput, not a simulation

Status: accepted · 4 September 2026 · Story S3.2.3 (E3 / F3.2)

## Context

S3.2.3 asks: *"Projection uses measured throughput per state over the trailing 14 days and
the MU counts remaining; shown as a date with a confidence band. A train projected to miss
its planned date by more than 5 working days is flagged on the Programme Board."*

Three questions decided the shape of the work: what "measured throughput" can possibly mean
in an estate where no MU state machine has been built (ADR 0026); whether a projection
should simulate each MU's remaining path through §3.2's fifteen states or something
cheaper; and how a "confidence band" and a "5 working days" flag are computed from a
14-day, calendar-day throughput measurement without the two units of time quietly
disagreeing.

## Decisions

### 1. Throughput is mined from the real event stream, never simulated or seeded with defaults

`IN_TRAIN.state` (ADR 0026) is set once, at proposal time, to `DEFAULT_MU_STATE`, and
changes only if something drives it through §3.2's pipeline — the wave scheduler (§14.2,
backlog S12.1.2), which this codebase has not built. That means, honestly, that measured
throughput is zero for every state in every real estate today, and this module does not
pretend otherwise.

`estate_throughput` reads `estate.edge.upserted` history for every `IN_TRAIN` edge and uses
a `LAG() OVER (PARTITION BY subject ORDER BY seq)` window function to find genuine state
*transitions* — a row where the state differs from that same edge's own immediately
preceding state. An edge re-upserted for an unrelated reason (a Wave Board resequence,
ADR 0026) carries its state forward unchanged and is correctly never counted as an exit.
When a state has zero measured exits in the window, the projection reports "insufficient
data," never a fabricated date — the same discipline `NullDirectoryResolver` and
`NullMigrationUnitRegistry` established earlier in this codebase: declare the shape now,
report an honest absence, and let real data replace it once the scheduler exists.

### 2. A projection is a single bottleneck estimate, not a discrete-event simulation of §3.2

For each train, the members occupy some set of states today. A full simulation would walk
every MU forward through every state still ahead of it on the transition graph, using each
state's throughput in turn — technically the more complete answer, but one that multiplies
a measurement this data-sparse (14 days, one estate) across a dozen compounding
projections, and reports false precision the underlying data cannot support.

Instead, `project_state` treats each *currently occupied* state as an independent queue:
`remaining / daily_mean_throughput` days to clear that state's own backlog. The train's
projected finish is the slowest (`max`) of these — the same reasoning behind any
single-resource bottleneck estimate. This deliberately ignores every hop still ahead after
the current one; it answers "when does what's in front of you clear," not "when does the
whole pipeline finish." A full simulation is real future scope this story does not claim,
and is only worth building once the wave scheduler gives §3.2 states an actual reason to
carry meaningfully different throughput profiles.

### 3. Throughput is measured in calendar days; the late flag is decided in working days

The story asks for both units, for different things. "Trailing 14 days" naturally means
calendar days — a daily exit rate that skipped weekends would understate a state nobody
works on weekends anyway, and the trailing window itself is defined in calendar terms. "5
working days" late is the flag's own explicit unit, and using it literally means a train
missing its date across a weekend is not flagged more harshly than one missing it midweek.

Converting between the two for every intermediate figure (throughput, the confidence band)
would just create a second place they could quietly disagree; only the final
planned-vs-projected comparison (`working_days_between`) counts working days. Everything
upstream of that stays in calendar days, matching how it was measured.

### 4. The confidence band comes from the same measurement's variance, not a second model

`daily_stddev` (population stddev over the 14 daily buckets) is already computed alongside
`daily_mean` for free. The optimistic bound uses `mean + stddev` throughput (finishes
faster); the pessimistic bound uses `mean - stddev` (finishes slower). When
`mean - stddev <= 0`, the pessimistic bound is reported as absent (`None`), not as an
infinite or fabricated date — "might never finish at this rate" is not a date this module
will print. No second statistical model (confidence intervals, regression) was introduced;
the story asks for "a confidence band," and the measurement already in hand supports one
without inventing a new input.

### 5. `now` is a first-class, testable parameter, not `date.today()` baked in

Every date-bucketing calculation (`estate_throughput`, `project_trains`) takes an explicit
`now: date | None` and only falls back to `date.today()` when the caller omits it — the same
precedent as `POST /v1/trains:propose`'s `start_date` override. A trailing-14-day feature
is otherwise nearly impossible to integration-test without either waiting real days or
quietly coupling every test to whatever today happens to be when the suite runs; `GET
/v1/trains:projections` exposes the same override as a query parameter for exactly that
reason.

## Consequences

- No ontology change. `train_projection.py` reads `IN_TRAIN.state` (already added by
  ADR 0026) and existing event history; nothing new is written to the graph.
- A day-bucketing bug was found and fixed during this story's own integration testing: naive
  `datetime.combine(date, time)` bound to a `timestamptz` column, compared against Postgres's
  bare `date_trunc('day', time)` (which truncates in the *session's* timezone, not
  necessarily UTC), silently dropped or double-counted a boundary day. Both sides are now
  pinned to UTC explicitly (`tzinfo=UTC` in Python, `time AT TIME ZONE 'UTC'` in SQL) — worth
  recording here because the failure mode (numbers off by almost exactly one day's data) is
  easy to misdiagnose as a logic bug rather than a timezone one.
- Because throughput is honestly zero almost everywhere today, `flagged_count` on
  `GET /v1/trains:projections` will read `0` for essentially every real estate until the wave
  scheduler starts driving state transitions. This is the correct answer, not a bug —
  surfacing it prematurely as a UI-visible "0 trains at risk" would misrepresent confidence
  the estate has not earned yet, which is why the Programme Board's "trains at risk" view
  (below) always shows each train's own `reason` string alongside any count.

## Alternatives considered

**Simulate every MU's full remaining path through §3.2's transition graph.** Rejected — see
decision 2. Compounding several sparse-data measurements produces a more detailed-looking
but less trustworthy number than a single bottleneck estimate.

**Measure throughput in working days to match the flag's unit throughout.** Rejected — see
decision 3. It would not change the flag's correctness, only relocate the unit mismatch to
the throughput measurement itself, where "trailing 14 days" already means calendar days.

**A parametric or regression-based confidence interval instead of mean ± stddev of the raw
daily series.** Rejected — see decision 4. Fourteen daily buckets is too little data to fit
a meaningful distribution; the raw series' own variance is the honest amount of confidence
this measurement supports.

**Bake `date.today()` in and test by seeding data relative to whenever the suite runs.**
Rejected — see decision 5. That couples every test's expected values to wall-clock time and
reintroduces exactly the flakiness `start_date` was added to `:propose` to avoid.
