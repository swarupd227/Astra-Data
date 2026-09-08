# ADR 0060 — §11.1 failure classification: priority-ordered signals and two AST-informed classes

Status: accepted · 8 September 2026 · Story S8.1.1, opening F8.1 and E8

## Context

S8.1.1 opens E8 (Mender and Exception Desk) and its first feature F8.1 (Failure
classification) — the backlog's own AC: *"As a migration engineer, I want every failing
case classified into the §11.1 taxonomy from its evidence bundle, so that the fix path
is chosen from evidence, not from guessing."*

- Classes: FILTER_CONTEXT, NULL_HANDLING, DATE_GRAIN, AGGREGATION, TYPE_COERCION,
  LOD_SCOPE, TABLE_CALC, SORT_LIMIT, KEY_MISSING, SOURCE_DRIFT, UNKNOWN; classification
  is deterministic from the diff signals
- Classification precision on the labelled fixture set ≥ 0.90; the class and the
  signals that produced it are recorded on the ExceptionCase or repair record
- Cases are grouped by artefact so one measure used by many sheets is repaired once

§11.1 itself is a table (`Class | Signal in the evidence | Typical cause | Usual fix
path`) — the eleven rows this story implements, each with a stated "signal in the
evidence" this module's own decision logic is built directly from. §11.2 adds the
grouping instruction verbatim: *"Classify every failing case; group by artefact (a
measure used by several sheets is repaired once)."*

## Decisions

### 1. `ExceptionCase` is the one real work-item mechanism — no repair record exists to choose between

Confirmed by direct research: E8 is entirely unbuilt before this story — no
`RepairRecord`/`MenderPass`-shaped node exists anywhere in this codebase. The AC's own
"recorded on the ExceptionCase or repair record" is disjunctive only in principle;
`ExceptionCase` (already declared, §4.1.1) is reused for a fourth disclosed class beyond
its own base seven properties, the identical choice `VISUAL_REDESIGN` (S6.2.1) and
`REGRESSION` (S7.7.1) each already made for their own class-specific facts.

### 2. This is the first story to open an ExceptionCase for a plain first-pass parity FAIL at all

Confirmed directly: `run_parity_for_workbook` (S7.4.1) writes a `Verdict(result="FAIL")`
and nothing has ever read it since. The only three `ExceptionCase` writers before this
story — `visual_redesign.py`, `regression.py`, `generation.py` — each open one for a
different moment (a pre-proof redesign flag, a later re-proof after acceptance, a
pre-proof generation-ladder exhaustion), never a first-pass parity FAIL. `classify_run`
reads `verdicts.latest_parity_run`'s own FAIL verdicts directly.

### 3. Two of the eleven signals are facts about the failing measure's own formula, not the diff — so the pure classifier accepts two small, real, optional extra signals rather than leaving them permanently unreachable

`LOD_SCOPE`'s own signal ("rows where the LOD dimensions differ from the sheet grain")
and `TABLE_CALC`'s own signal ("a partition boundary pattern") are both honestly facts
about the calculation's own AST, not the diff's own cell/key evidence — `DiffResult` has
no notion of either. Rather than leave both dead branches (the same disclosed-
unreachable shape `InconclusiveReason.SAMPLING_SHORTFALL` already has elsewhere in this
codebase), `classify_failure` accepts an optional `formula_ast`; `_contains_lod`/
`_contains_table_calc` walk it the identical way `classify.py`'s own §9.1 C1-C4
classifier already walks a calculation's AST (`kind == "AGGREGATE"` and `name in
{"FIXED","INCLUDE","EXCLUDE"}` for LOD — the exact `_LOD_NAMES` constant that module
already declares, mirrored rather than imported since it is a private constant of a
different epic's own module; `kind == "FUNCTION"` and `detail["family"]` starting
`"table_calc"` for a table calculation — the same family tag the Tableau grammar's own
function registry already stamps, reused rather than a second, invented function-name
list). The graph-coupled `classify_run` supplies the real `CalculatedField.formula_ast`,
resolved from the failing cell's own named measure via
`case_derivation._worksheet_field_index` (imported directly, the one already-established
exception this codebase's own cross-epic-private-helper convention already has, since
`verdicts.py` already imports the identical function the same way).

### 4. `SOURCE_DRIFT`'s own signal is a fact about time, resolved from the real event outbox, not invented

"Expected side changed between runs" cannot be read from any single diff's own evidence
either — it is a fact about when the source last changed relative to when this case was
last executed. `classify_run` resolves it from the identical `SOURCE_DRIFT` event stream
§10.6's own `RegressionScheduler._check_drift` already polls (`regression.py`) — "has a
drift event landed for this workbook in the outbox's own recent window" — a real,
already-existing signal, not a new detection mechanism invented for this story.

### 5. A disclosed priority order, since §11.1 gives none, for when several signals could fire at once

A failing cell can, in principle, be simultaneously a date comparison and part of a
subset whose deltas share a consistent factor. `classify_failure` states and follows one
order: `SOURCE_DRIFT` first (if the source changed, nothing else the diff shows is
trustworthy regardless of what it looks like); then key-set signals (`KEY_MISSING`/
`SORT_LIMIT` — unambiguous, a key either exists or does not); then the two AST-informed
classes (a real fact about the formula, checked before the more speculative cell-level
heuristics); then the cell-level classes in order of how specific their own signal is —
a null pairing is unambiguous, checked before a date kind, checked before a string/
number formatting match, checked before the totals-vs-rows XOR, checked last before a
"consistent factor" statistical judgement call, which only fires within an invented,
disclosed tolerance (`FILTER_CONTEXT_SPREAD_TOLERANCE = 0.05`) before finally falling
back to `UNKNOWN`.

### 6. `SORT_LIMIT` versus `KEY_MISSING` is a disclosed heuristic, not a semantically exact distinction

Neither `DiffResult` nor the evidence bundle carries row-ordering information (the
key-set comparison is order-independent by design, S7.4.1), so "row set differs only by
membership under a top-N" cannot be verified directly. The heuristic: an equal-sized,
purely symmetric key exchange (as many keys missing as extra) with no cell-level
mismatches on the shared remainder reads as `SORT_LIMIT` (consistent with a shifted
top-N boundary); anything asymmetric, or with a size mismatch, reads as `KEY_MISSING` (a
real absence). A real, invented, disclosed simplification — not a claim of certainty
§11.1 itself does not offer either.

### 7. "Classification precision" is read as plain accuracy over a labelled fixture set — the first precision-bound test this codebase has for a deterministic classifier

The AC names no per-class precision/recall breakdown, and this codebase's own nearest
precedent (`classify.py`'s §9.1 C1-C4 classifier) has no precision-bound test at all to
follow — S5.1.1's own AC never asked for one. `tests/classification_fixtures.py`
mirrors `diff_fixtures.py`'s own "named generator functions, one per category,
hand-verified" convention (S7.4.1): at least five hand-labelled cases per class (55),
plus six deliberately hard/borderline cases (61 total) proving the bound is real —
including one genuine, disclosed miss (a boolean-string coercion, `"true"` vs `"1"`,
that the algorithm's own numeric-formatting-only `TYPE_COERCION` heuristic does not
catch and correctly falls through to `UNKNOWN` instead) rather than relabelling the
fixture to match whatever the code happens to produce. Measured precision: 60/61 =
0.984, comfortably above the AC's own 0.90 floor, with the one real miss kept and
disclosed rather than removed to inflate the number.

### 8. Grouping needs a real artefact reference `mu_ref`/`evidence_ref` alone cannot give it — two new `ExceptionCase` properties, not a new node type

`mu_ref` names a whole workbook, not one of its many measures; `evidence_ref` names one
case's own evidence artefact, not the several cases a shared artefact's own failure
spans. This narrows `ExceptionCase.visual_ref`'s own note on this node ("every other
failure class... `mu_ref` and `evidence_ref` already identify without a second
reference property", S6.2.1) — true when nothing yet grouped failures by the artefact
actually at fault; no longer true once this story does. Two new, additive properties:
`artefact_ref` (the real `CalculatedField`/`Field` id a failing case's own evidence
resolves to, via the identical measure-name-to-id resolution `case_derivation.py`
already performs when deriving cases) and `case_refs` (every live `ParityCase` id the
one exception covers). A third, `classification_signals` (JSON), records the AC's own
"the class and the signals that produced it" — `class` already carries the class,
`classification_signals` carries why, a snapshot taken when the case opens, the same
"a snapshot, not a live read" discipline `mapping_reason`/`placeholder_location`
(S6.2.1) already established.

### 9. Grouping key is (artefact, class); a case whose evidence names no specific measure falls back to (sheet, class), not to being ungrouped

When a case's own failing cells all name the same measure, that measure — resolved to
its real `CalculatedField`/`Field` id — is the group key. A case with no failing cells
at all (a pure key-set or row-count-only FAIL) has no single measure the evidence can
pin the blame on; rather than leave it out of grouping entirely, it groups by its own
sheet instead (`artefact_ref` stays absent, disclosed) — still one exception per (sheet,
class) rather than one per case, a narrower but still real reading of "repaired once."

### 10. A second classification pass merges into the still-OPEN exception rather than opening a second one

`classify_run` is callable more than once (a re-diff after a partial repair, or simply
re-run by an engineer) — `_find_open_case` looks for an already-OPEN `ExceptionCase` at
the same (`mu_ref`, `artefact_ref`, `class`) before writing a new one; when found, it
merges `case_refs` (union, not replace) and refreshes `evidence_ref`/
`classification_signals` via `upsert_nodes` rather than duplicating the exception. "One
artefact, repaired once" read across classification passes, not only within one.

### 11. No console screen — the Exception Desk is real future scope this story deliberately does not build

`GET /v1/exceptions` (S6.2.1) already lists every live `ExceptionCase`, filterable by
`mu_ref`/`state`; a classified case is simply a new, disclosed use of the same route,
needing no new read endpoint. Confirmed by direct research: no Exception Desk screen or
component exists anywhere in `console-web` yet, and the backlog's own F8.3 (Exception
Desk: queue, case page, decisions) is real, separate, later scope this story's own AC
does not ask for — the identical "an engine feature can precede its own screen by
several stories" precedent `S7.2.1`→`S7.4.2`'s own gap already set in this epic.

## Consequences

- New `services/graph-svc/src/astra_graph/classification.py`: `Classification`
  (`failure_class`, `rule_id`, `reason`, `signals`), `classify_failure` (pure),
  `classify_run`/`ClassificationService` (graph-coupled), `FAILURE_CLASSES`,
  `FILTER_CONTEXT_SPREAD_TOLERANCE`.
- New `services/graph-svc/src/astra_graph/api/routes_failure_classification.py`: `POST
  /v1/workbooks/{id}:classify-failures` (`MigrationEngineerDep`, the AC's own persona).
  Named to avoid collision with the pre-existing, unrelated `routes_classification.py`
  (S5.1.1's own C1-C4 calculation classifier).
- Ontology: `ExceptionCase` gains `classification_signals` (JSON), `artefact_ref`
  (STRING), `case_refs` (STRING_LIST); `SCHEMA_VERSION` 30 → 31, two new declared
  `SpecDeviation`s, no migration file (additive properties only).
- `main.py`: `app.state.classification = ClassificationService(...)`, wired alongside
  the existing `VerdictsService`/`RegressionService`.
- Verified: 18 new pure unit tests (the priority order, the `SOURCE_DRIFT` override, the
  61-case labelled fixture set's own real coverage and precision bound); 10 new
  integration tests against real PostgreSQL + Apache AGE (a real FAIL classified from a
  real evidence bundle, two sheets sharing one real `CalculatedField` collapsing into one
  `ExceptionCase`, a second classification pass merging into the still-OPEN case rather
  than duplicating it, the real graph-coupled LOD/`SOURCE_DRIFT` paths, both the new
  route's and the existing exceptions route's own role gate); the full existing graph-svc
  suite green alongside them; `ruff`/`mypy` clean; `ontology_check.py --spec`/
  `--generated` and `migration_check.py` all pass.

## Alternatives considered

**Leave `LOD_SCOPE`/`TABLE_CALC`/`SOURCE_DRIFT` as dead branches, matching this
codebase's own `SAMPLING_SHORTFALL` precedent of disclosing an unreachable spec-named
class outright.** Rejected — see decisions 3–4. Unlike `SAMPLING_SHORTFALL` (genuinely
unreachable under the chosen sampling algorithm), all three of these have a real,
already-existing fact this codebase can supply (a calculation's own AST; the event
outbox) — leaving them unreachable would have been a choice not to look, not an honest
limit.

**Invent a fixed list of table-calc function names (RANK, WINDOW_SUM, ...) rather than
reading the grammar's own `detail.family` tag.** Rejected — see decision 3. The Tableau
grammar's own function registry already classifies a function's family for §9.1's own
classifier to read; reusing that real, already-computed fact is more accurate and
carries zero risk of drifting out of sync with the grammar's own registry the way a
second, hand-maintained function list eventually would.

**Give `classify_failure` no stated priority order, and simply document that the
"first matching" rule from a fixed iteration order applies.** Rejected implicitly by
building one — see decision 5. §11.1 itself is silent on precedence, so a caller reading
only the taxonomy table has no way to predict behaviour on an overlapping case without
this module's own explicit, written order — stating it plainly (and testing it directly,
`test_source_drift_overrides_every_other_signal`) was the more honest choice than
letting the order be an implementation accident nobody could rely on.

**Relabel the one genuine fixture miss (boolean-string coercion) to whatever class the
algorithm actually produces, so the fixture set clears 100%.** Rejected — see decision
7. A 100%-clearing fixture set the classifier's own author also wrote would prove
nothing about whether the ≥ 0.90 bound is a real threshold; keeping one honest miss (and
still comfortably clearing 0.90) is the only way the number means anything.

**Build the Exception Desk's own queue screen now, since a real, classified
`ExceptionCase` exists to show for the first time.** Rejected — see decision 11. F8.3 is
real, separate, later backlog scope (queue ordering by train sequence, bulk assign, a
three-pane case page, GateDecision-class decisions) this story's own AC does not ask
for; `GET /v1/exceptions` already gives any Artizent role a real, working way to read
what this story writes in the meantime.

## Open questions for the product owner

- `SORT_LIMIT`'s own heuristic (a symmetric key exchange, no cell mismatches) has no way
  to confirm the exchanged keys are actually near a real top-N boundary, only that the
  shape looks like one. Should a future story strengthen this once `ParityCase`'s own
  charter carries an explicit top-N rule to check the missing/extra keys' own rank
  against, or is the current, disclosed heuristic good enough given `SORT_LIMIT`'s own
  "usual fix path" (a charter tie-break rule) is itself advisory?
- The boolean-string coercion gap (`"true"` vs `"1"`) is real and disclosed, not fixed.
  Should `_looks_type_coerced` grow a second, boolean-aware branch once a real workbook
  surfaces one, or does the ≥ 0.90 bound mean this specific gap can stay open
  indefinitely, the same posture ADR 0058 already took for aHash's own colour-blindness?
- `classify_run` currently classifies a workbook's own *most recent* `ParityRun` only.
  Should a future story (S8.2.1's own bounded repair loop, most likely) classify
  automatically as part of `run_parity_for_workbook` itself, or does keeping
  classification a deliberate, separate action (mirroring `:execute-parity-cases` then
  `:run-parity`'s own already-separate two-step) remain the right shape once repair
  passes need to re-classify between attempts?
