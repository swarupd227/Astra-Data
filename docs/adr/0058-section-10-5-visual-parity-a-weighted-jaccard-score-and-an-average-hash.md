# ADR 0058 — §10.5 visual parity: a weighted Jaccard score and an average hash

Status: accepted · 8 September 2026 · Story S7.6.1, opening F7.6

## Context

S7.6.1 opens F7.6 (Visual parity, advisory) — the backlog's own AC: *"As a report owner,
I want a structural visual-similarity score and side-by-side images, so that I can spot
a report that is numerically right and visually wrong."*

- Structural score from mark type, encodings, axes, sort, reference lines (0–1); image
  similarity from source screenshot and Power BI export API render
- Score is shown on the Parity Dashboard and the G3 card; it never gates; the human
  visual review at G3 is the gate

§10.5 itself, verbatim: *"Structural comparison of the visual specification — mark type,
encodings, axis fields, sort, reference lines — produces a visual parity score per
sheet. A screenshot of the source view (via the adapter) and a rendered image of the
target visual (via the Power BI export API) are compared perceptually and the score is
shown next to the structural score. Neither gates acceptance: G3 requires a passing
data-parity verdict and a human visual review, and the advisory scores exist to direct
that review to the visuals most likely to have drifted."* (Several earlier modules cite
this as "§10.6" — a pre-existing off-by-one in this codebase's own citations, not in the
spec document itself; this story's own code cites it correctly and leaves the earlier
citations alone as unrelated to its own scope.)

## Decisions

### 1. The structural score is five weighted components, the identical shape `lineage.similarity` (the Cartographer's own strength score) already established

The only existing precedent in this codebase for a deterministic 0-1 score built from
comparing discrete facts is the Cartographer's own weighted-Jaccard strength score
(S3.1.1: `0.5·J(tables) + 0.3·J(fields) + 0.2·shared_calc_shapes/max`). Applied here to
§10.5's own five named facts, with invented, disclosed weights (mark type 0.3, encodings
0.3, axes 0.2, sort 0.1, reference lines 0.1) — §10.5 names the five facts, not how to
weight them; mark type and encodings weighted highest since they are what a reader
notices first, sort and reference lines weighted lowest as finer detail.

### 2. Mark type reads the already-computed `Visual.redesign_flag`, rather than re-deriving `compositor.resolve_visual`'s own refinement logic a second time

`resolve_visual`'s own stacked-vs-clustered/dual-axis/matrix-vs-table refinement needs
live graph reads (`ResolvedWell`s reconstructed with real bindings) this pure scoring
function does not have, and duplicating business logic that already lives in one place
is a real maintenance risk, not a shortcut. `redesign_flag` is the right signal instead:
a mark type Appendix B.2 could only resolve by flagging it for redesign *is* a real
structural mismatch; one that resolved cleanly is not.

### 3. Axes are read from each field well's own `shelf` tag, already stored by the compositor

`compositor._resolve_one_well` already tags every well `"rows"`/`"cols"`/a marks
channel; this module reads the stored tag against the source `Worksheet`'s own
`rows_shelf`/`cols_shelf` rather than re-deriving which wells count as axes.

### 4. Reference lines score 0 whenever the source has any — a disclosed, honest structural gap, not a bug

Confirmed by direct inspection: `compose_report` never reads `Worksheet.reference_lines`
at all, and `visual_mapping.py`'s own docstring already lists reference lines among the
Appendix B.2 entries "not reachable... by design" — there is no Power BI-side property
anywhere in this ontology for a preserved reference line. A sheet with reference lines
the migration silently dropped is exactly the "numerically right, visually wrong" case
§10.5's own AC names; scoring it 0 is the honest answer, not a gap to paper over by
inventing a property nothing writes.

### 5. Image similarity is a real, deterministic average hash (aHash) — with a proven, disclosed limitation

Resize both images to 8×8 grayscale, threshold each pixel against the image's own mean,
compare the two 64-bit hashes by Hamming distance — a standard, well-known "advisory
grade" metric appropriate to an AC that is explicitly never a gate, using Pillow (added
as a graph-svc-only dependency, the identical "this story needs it" precedent pyarrow
already set for S7.3.1). `None` (not zero) when either side's bytes cannot be decoded as
an image at all. **Proven by its own test, not assumed**: two solid-colour images of any
two different colours hash identically (every pixel equals the image's own mean,
regardless of what that colour actually is) — aHash detects pattern, not absolute
colour. Disclosed plainly in the module's own docstring: a visual whose entire drift is
a colour-palette change would score a perfect 1.0 despite looking different; a future
story wanting colour-sensitive comparison needs a different metric, not a patch to this
one.

### 6. `TargetAdapter.render_visual` is a new, required protocol method — not gated by a `Capabilities` flag, matching the contract's own stated design

`TargetManifest`'s own docstring already states the design: "No `Capabilities`... an
adapter either implements this whole narrow contract or it is not one." Rather than
introduce a new optional-capability mechanism this contract has deliberately never had,
`render_visual` is a plain required method (`TARGET_INTERFACE_VERSION` 1.1 → 1.2,
additive) — costing nothing today since `FixtureTargetAdapter` is the only implementer
in this codebase. It reuses `VisualCase`/`VisualCapture` verbatim from the source side's
own `capture_visual` (§6.2) rather than a second, parallel pair of types — the identical
"both sides return the identical shape" symmetry §10.2 already established for
`ResultSet` (ADR 0053).

### 7. `FixtureTargetAdapter.render_visual` returns the identical one-pixel placeholder `FixtureSourceAdapter.capture_visual` already does — deliberately breaking from `evaluate`'s own "useful synthetic data" precedent

`fake/source.py`'s own `capture_visual` already drew this line: *"the fake has nothing
to render, and inventing a picture would make the perceptual comparison score noise as
similarity."* A synthetic image cannot exercise §10.5's own comparison the way synthetic
rows exercise a diff, since an image's whole meaning is its actual visual appearance —
so in this platform's own local/demo environment, any computed `image_score` is real
arithmetic over content-free bytes, not a meaningful similarity, disclosed rather than
hidden. The orchestration and the algorithm are both real and correct; only a live
Tableau screenshot and a live Power BI export would give the number real content.

### 8. Scores, and the two capture refs, are stored on `Visual` — not `ParityCase`/`Verdict`

A structural/image score is a fact about one sheet's own mapping — static until the
sheet or the mapping changes — not about a specific grain/measure/filter combination a
parity case represents. Six new, disclosed `SpecDeviation` properties: `structural_
score`, `structural_score_breakdown`, `image_score`, `visual_score_computed_at`,
`source_screenshot_ref`, `target_render_ref` — all additive, schema version 28 → 29, no
migration file. The source screenshot is stored under the already-established
`"visual_capture"` kind (S2.4.2); the target render under a new `"visual_render"` kind,
its own counterpart.

### 9. "Side-by-side images" is a real, base64-encoded read route — not only a similarity number

`GET /v1/workbooks/{id}/visual-captures/{visual_id}` returns both stored captures
base64-encoded, so the console renders them with a plain `<img src="data:...">` and
needs no separate binary-serving route. Gated by the identical `ParityDashboardReaderDep`
the rest of the dashboard already uses (any Artizent role, or the report owner
specifically) — the existing generic `GET /v1/artefacts/{id}/content` route stays
`ArtizentDep`-only and untouched, since widening it would affect every artefact kind
this codebase has, a much larger surface than this story's own scope.

### 10. Visual parity scores are folded into the existing Parity Dashboard read, not a second dashboard endpoint

The AC's own "shown on the Parity Dashboard" already names where a report owner sees
this; `parity_dashboard.aggregate_dashboard` gained a `visuals_by_sheet` parameter and
each per-sheet row gained `structural_score`/`structural_score_breakdown`/`image_score`/
`visual_score_computed_at`/`source_screenshot_ref`/`target_render_ref`/`visual_id` (the
last needed only so the console can address the captures route). `None` throughout for a
sheet whose visual has never been scored — an honest absence, the same posture
`first_pass_rate`/`waived_count` already carry. "On the G3 card" cannot be built by this
story: G3 itself is F9.1/S9.1.1's own later, entirely unbuilt scope (confirmed by direct
research, the identical disclosure S7.5.1 already gave for its own SAMPLED label) — these
are the real facts that card will read from once it exists.

## Consequences

- `packages/adapter-sdk`: `TargetAdapter.render_visual` (new, required);
  `TARGET_INTERFACE_VERSION` 1.1 → 1.2; `FixtureTargetAdapter.render_visual` (the
  disclosed one-pixel placeholder).
- New `services/graph-svc/src/astra_graph/visual_parity.py`: `StructuralScore`,
  `compute_structural_score`, `compute_image_similarity` (pure);
  `run_visual_parity_for_workbook`, `get_visual_captures`, `VisualParityService`
  (graph-coupled).
- New `services/graph-svc/src/astra_graph/api/routes_visual_parity.py`:
  `POST /v1/workbooks/{id}:run-visual-parity` (`ParityEngineerDep`),
  `GET /v1/workbooks/{id}/visual-captures/{visual_id}` (`ParityDashboardReaderDep`).
- `parity_dashboard.py`: `aggregate_dashboard` gains `visuals_by_sheet`; each per-sheet
  row gains the six visual-parity fields plus `visual_id`.
- Ontology: `Visual` gains six additive properties; `SCHEMA_VERSION` 28 → 29, one new
  declared `SpecDeviation`, no migration file.
- `services/graph-svc/pyproject.toml`: `Pillow>=10,<12` added (graph-svc-only).
- Console: `api.ts` gains `StructuralScoreBreakdown`, `RunVisualParityResult`,
  `VisualCapturePair`, `Api.runVisualParity`/`.visualCaptures`; `SheetParityStats` gains
  the six fields plus `visual_id`. `ParityDashboard.tsx`'s per-sheet table gains
  Structural/Image columns; selecting a scored sheet shows both captures side by side (or
  a "not yet scored" disclosure); a new "Score visuals" action, hidden-with-explanation
  for anyone but the Parity Engineer, the identical convention "Re-run parity" already
  set.
- Verified: 16 new unit tests against the pure algorithm (perfect/partial/zero component
  scores, the disclosed aHash colour-blindness limitation proven directly, undecodable
  bytes returning `None`); 7 integration tests against real PostgreSQL + Apache AGE (a
  real composed `Visual` scored for real, a workbook with no composed report refused, a
  real captured screenshot/render pair stored and compared, an honest `None` when the
  source adapter does not claim the screenshot capability, visual parity never writing a
  `ParityCase`/`Verdict`, both HTTP routes' own role gate) plus 2 more for the captures
  route (a real round-trip and an honest absence before any score exists); 4 new console
  tests (the two score columns, the side-by-side images, the "not yet scored"
  disclosure, the Score visuals action and its own role gate); the full existing
  graph-svc suite and console-web suite (225 passed, up from 221) both green alongside
  them; `ruff`/`mypy`/`tsc`/`eslint` all clean; `ontology_check.py --spec`/`--generated`
  and `migration_check.py` all pass.

## Alternatives considered

**Re-derive `compositor.resolve_visual`'s own refinement logic inside the pure scoring
function, for a more precise mark-type match.** Rejected — see decision 2. That logic
needs live graph reads this function does not have, and duplicating business logic
already living in one place is a real maintenance risk; `redesign_flag` is the correct,
already-computed signal for the real structural question ("did this mark type resolve
cleanly").

**Score reference lines as an unscored/neutral component (e.g. excluded from the
weighted total) rather than 0 whenever the source has any.** Rejected — see decision 4.
A dropped reference line is a real, visible drift a report owner would want flagged;
excluding it from the score would hide exactly the kind of gap §10.5's own AC exists to
surface.

**Use a fuller perceptual-diff library (SSIM, a proper perceptual hash library) instead
of a hand-rolled average hash.** Rejected — see decision 5. §10.5's own AC is explicit
that this is advisory, never a gate; aHash is a real, standard, well-understood metric
appropriate to that grade, and this codebase has no other use for a heavier image
library. The colour-blindness limitation is disclosed rather than engineered around,
since fixing it would mean choosing and justifying a materially different (and heavier)
algorithm this story's own AC does not ask for.

**Gate `TargetAdapter.render_visual` behind a new `Capabilities`-style flag, mirroring
the source side's `screenshot` capability.** Rejected — see decision 6. The contract's
own `TargetManifest` docstring already states its design philosophy explicitly: no
`Capabilities`, all-or-nothing. Introducing an exception for this one method would
contradict a design choice already made and stated, for a cost (supporting a capability
gate with exactly one real caller) this story does not need to pay.

**Serve captured images through the existing generic `GET /v1/artefacts/{id}/content`
route, widening its gate to the report owner.** Rejected — see decision 9. That route
serves every artefact kind this codebase has (evidence bundles, Parquet result sets,
generated documentation); widening its access for one new kind would open every other
kind to the same audience, a much larger and unreviewed surface than this story's own
"let a report owner see two images" ask.

## Open questions for the product owner

- Now that `TargetAdapter.render_visual` is a real, required contract method with
  exactly one (disclosed placeholder) implementer, should a future story's own real
  Power BI export API integration land here directly, or does it want its own follow-up
  ADR given how different a live REST/export-API client is from everything
  `FixtureTargetAdapter` currently does?
- Should the aHash algorithm's own disclosed colour-blindness be closed with a second,
  colour-aware component (e.g. a coarse colour-histogram comparison) once a real
  screenshot/render pair exists to validate it against, or does §10.5's own "advisory,
  never gates" framing mean the current metric is good enough indefinitely?
- Should `Visual.structural_score_breakdown`'s own five components be individually
  visible on the Parity Dashboard (not just the combined score), the next time a Parity
  Engineer needs to know *which* of the five facts actually drifted, not just that one
  did?
