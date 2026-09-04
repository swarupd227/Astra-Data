# ADR 0024 — The confirmed family count is measured, not typed; the Calibration Report is not built yet

Status: accepted · 3 September 2026 · Story S3.1.3 (E3 / F3.1)

## Context

S3.1.3 asks that "the family count [is] recorded as a calibration input at the end of
Month 1, so that the planning assumption is replaced by a measured value with a date":
*"A 'Confirm family count' action writes the count, the date and the confirming user to the
programme record and the Calibration Report ... Programme Board shows planned (150) against
measured with the delta."*

Three design questions decided the shape of the work. First: where does "the count" a
Programme Manager confirms come from — typed, or read? Second: the criterion names a
document ("the Calibration Report") that does not exist in this codebase — what, if
anything, does this story owe it? Third: the criterion also names a screen ("Programme
Board") this codebase has never built — how much of it does this one figure justify
building now?

## Decisions

### 1. The confirmed count is read live from the graph, never typed

`POST /v1/programmes/{id}:confirm-family-count` takes no count in its request body.
`cartographer.count_families` counts every live `ModelFamily` node — `PROPOSED`,
`SINGLETON`, and any state a human override (S3.1.2) left it in — at the instant of
confirmation, and that is the number written to the programme record. A free-text count
field would have let a Programme Manager confirm a number that disagreed with the estate,
which defeats the criterion's own reason for existing: replacing a *planning assumption*
with a *measured value*, not replacing one assumption with a different one that merely has
a signature on it.

Every state counts, not only `PROPOSED`: `ClusteringResult.family_count` (S3.1.1) counts
only what one run applied and is deliberately narrower — a `SINGLETON` is still one
workbook's own family, and a family a human split, merged or moved is still a family.
"~150 shared governed models" is a statement about the estate's whole family count as it
stands, not one algorithm's opinion of it.

### 2. Three plain columns, not a JSON blob

`family_count`, `family_count_confirmed_at`, `family_count_confirmed_by` are three columns
on `public.programme` (migration v0014), not a JSON blob like S3.1.1's `clustering_json`.
The two records answer different questions and can legitimately disagree: `clustering_json`
is only ever the *last run's* figures, silently overwritten by every re-cluster whether
anyone looked at it or not; `family_count` is what a Programme Manager stood behind. A
fixed, three-field record is exactly what plain columns are for, and retention.py already
models everything else about a programme that way.

### 3. The delta is computed, never stored

`Programme.as_dict()` computes `family_count_delta` as `family_count - PLANNED_FAMILY_COUNT`
on read. `PLANNED_FAMILY_COUNT = 150` is a spec constant (§14.3 / Appendix A), not a
per-programme value, so there is nothing to reconcile: storing the delta would just be a
second place the same subtraction could drift from the first.

### 4. This story does not write the Calibration Report, because nothing to write into exists yet

§14.3 gives the Calibration Report to the Calibration Wave — E13, F13.1/F13.2, backlog
story S13.1.2 — as one figure among many (class mix, coverage, parity rate, C4 reasons,
family count, cost per report, stage timings), signed by both parties at wave close. None of
that exists in this codebase: no report record, no wave, no signing flow. Building a report
artifact now, to hold one figure, ahead of the epic that defines the other six, would be
exactly the kind of ahead-of-the-story scope this project's stories are deliberately taken
one at a time to avoid (the same call v0012's own migration made about a run-history table).

What this story delivers instead is the durable input that report will read:
`family_count`/`family_count_confirmed_at`/`family_count_confirmed_by` on the programme
record, in the same relationship `clustering_json` already has to the same future report.
When E13 builds the Calibration Report, it reads this row; nothing about today's shape needs
to change for that to work.

### 5. The Programme Board gets one pane, not the board

§15.3.1's Programme Board is a KPI strip, train swimlanes, blocked reasons, exceptions
ageing and milestones — backlog story S10.2.1's screen, not built. Building all of it here
would be building a different story's deliverable early. What this story adds is a new
`/programme` surface carrying exactly the figure S3.1.3 asks for: planned against measured
with the delta, and the "Confirm family count" action, gated client-side (and enforced
server-side) to the Programme Manager role. When S10.2.1 lands, it absorbs this pane into
the fuller board the same way S3.1.1's clustering later gave the Lineage View a graph-backed
figure to prefer over its own computed one — an existing, working fragment becomes one KPI
card in a bigger screen, not scaffolding that gets deleted.

## Consequences

- A Programme Manager can only confirm what the estate actually shows; there is no path
  from this action to a family count that disagrees with `GET /v1/families`.
- The programme record now carries two independent, occasionally-disagreeing family-count
  facts — `clustering_json`'s last-run figure and `family_count`'s confirmed one — and that
  disagreement is itself informative: it is exactly "has anyone signed off on what the last
  re-cluster produced."
- The console gained a fourth surface (`/programme`) ahead of E10, scoped to one card. It
  is additive: S10.2.1 does not need to remove or rework it, only place it inside a larger
  layout.
- Nothing here blocks E13. The Calibration Report, when built, has a real, dated,
  attributed number waiting for it rather than needing to invent where its own family-count
  figure comes from.

## Alternatives considered

**Accept the count as a request parameter, with the live figure only as a default.**
Rejected: a parameter a caller can override is a parameter a caller can supply, and the
whole point of "confirm" here is that the number is not up for negotiation — it either
matches the estate or the estate has changed since and the Programme Manager confirms again.

**Store the delta alongside the count.** Rejected — see decision 3. `PLANNED_FAMILY_COUNT`
is a constant; a stored delta is a derived fact with no independent existence.

**Build a minimal Calibration Report record now, to satisfy the criterion's letter.**
Rejected — see decision 4. A report with one figure and six placeholders would be a document
this codebase would have to migrate again, in more invasive ways, once E13 actually defines
its shape; the programme record's two new columns need no such rework.

**Wait for S10.2.1 and leave this story backend-only.** Rejected: the criterion states a
UI outcome ("Programme Board shows..."), and every prior console story (S1.4.1–S1.4.3)
built the screen its own story needed rather than deferring to a later one. A single-card
`/programme` surface satisfies the criterion at the scope it actually asks for.

## Open questions for the product owner

- Should re-confirming be restricted (e.g. once per Month-1 window, or only while the
  programme is open) rather than allowed at any time, any number of times? Nothing in the
  backlog text constrains this, so the current behaviour is "overwrite, like
  `record_clustering`" — the same choice S3.1.1 made for the clustering figures.
- `PLANNED_FAMILY_COUNT = 150` is hard-coded from the spec. If a future engagement's
  planning assumption differs from BlackRock's, does that become a per-programme value, or
  does every engagement genuinely share one number until the spec itself is revised?
