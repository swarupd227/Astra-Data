# ADR 0055 — The §10.3 diff: a pure core, and a suite_ref-anchored run

Status: accepted · 7 September 2026 · Story S7.4.1, closing F7.4

## Context

S7.4.1 closes F7.4 — spec §10.3: *"As a parity engineer, I want the diff algorithm from
§10.3 implemented exactly and tested against a fixture set, so that every verdict is
explainable in terms of the charter."*

- Normalisation: column mapping via `MAPS_TO`, type coercion to the lattice, date
  truncation, string folding, null canonicalisation; key by grain tuple
- Key-set comparison, cell comparison with numeric epsilon per charter, row count and
  totals check; verdict PASS/FAIL/INCONCLUSIVE with failing cells (first N, default 50)
  and the delta
- Fixture set of 200 hand-verified pairs covering each charter rule; CI runs them on
  every change
- Evidence bundle per run: charter version, both queries, both result hashes, key
  differences, failing cells, timings

S7.1.1 (the Tolerance Charter, ADR 0050) already built `compare_numeric`/`compare_null`/
`compare_string` as "the real logic each charter block actually means"; S7.3.1/S7.3.2
(dual execution, ADRs 0053/0054) already produce and store both sides' `ResultSet`s as
Parquet. This story is what finally reads them back and diffs them for real.

## Decisions

### 1. `ParityRun`/`Verdict`/`PROVED_BY` were already fully declared — confirmed, not assumed

Direct research against `ontology/nodes.py` and `ontology/edges.py` confirmed both node
types and the `PROVED_BY` edge have carried their full §4.1.1 property set since the
ontology's very first declaration, and — equally confirmed — no story before this one
has ever written a single row of either. **This story needs zero ontology or migration
change**, verified by `ontology_check.py --spec`/`migration_check.py` both passing
unchanged (28 node types, schema version 27).

### 2. The diff algorithm is a pure function (`diff.py`) with no database, separate from its graph-coupled orchestration (`verdicts.py`)

`diff_result_sets(expected, candidate, charter, *, column_target_map=None) -> DiffResult`
takes two already-loaded `astra_adapter.ResultSet`s and returns a plain dataclass;
nothing in `diff.py` awaits anything. This is what lets the AC's own 200-case fixture
set run at unit-test speed with no Postgres involved, and is the same "pure core,
graph-coupled shell" split `case_execution.py` already drew between `build_dax_query`
(pure) and `_table_map_for_sheet` (graph-coupled) — applied here to the whole algorithm,
not just one helper. `verdicts.py` is the graph-coupled other half: reading a case's own
stored Parquet artefacts, resolving a real `MAPS_TO` binding, and writing what the pure
step returns.

### 3. The type lattice is exactly the spec's own three chains, with a broad, disclosed alias list

§10.3 names three families and states where they collapse: `integer ⊂ decimal ⊂ double`
→ one "numeric" comparison; `date ⊂ datetime` → one "date" comparison;
`everything ⊂ string` → the lattice's own top element, the fallback whenever the two
sides do not already agree on a family. `classify_type` recognises a disclosed alias
list per family (`int`/`bigint`/`smallint` alongside `integer`, etc.) since neither the
source adapter's own type strings nor a Parquet-derived type name are standardised
anywhere in this codebase; an unrecognised name safely falls back to `"string"`.

### 4. `compare_date` and `RowRule.max_failing_cells` extend `tolerance_charter.py` directly, rather than duplicating cell-comparison logic in `diff.py`

`compare_numeric`/`compare_null`/`compare_string` already lived in `tolerance_charter.py`
as of S7.1.1, dispatched by `compare_cell(kind, ...)`. A fourth comparator,
`compare_date`, and a fourth `kind` (`"date"`) complete that same dispatch rather than
building a second, parallel comparison layer inside `diff.py` — `simulate_charter`
(S7.1.1's own re-diff-without-executing feature) picks up date-kind failing cells for
free, since it already calls `compare_cell` generically by whatever `kind` a stored
failing cell carries. `RowRule.max_failing_cells = 50` (the AC's own literal default)
lives on the charter for the identical reason `missing_key`/`extra_key`/
`row_count_tolerance` already do: it is a row/cell-evidence-scope fact, not a new,
tenth charter block neither §4.4 nor §10.3 names.

### 5. Date normalisation for *keying* is a disclosed, symmetric simplification of `compare_date`'s own asymmetric cell rule

Cell comparison keeps §10.3's own literal rule: the source's own grain is
authoritative, and a candidate with finer precision truncates down to it. Building a
*key* from a grain column happens independently on each side, before the two sides are
ever paired up — there is no "source" to truncate "to" yet. `_key_component` instead
truncates to date-only whenever *either* side carries a `datetime`, a symmetric
simplification that lets a midnight-timestamped grain value on one side still match a
plain date on the other. Confirmed by the fixture corpus itself: two of the original 20
date-category cases assumed the asymmetric cell rule applied to *grain* values too and
had to be corrected — grain values now exercise the documented symmetric key rule, and
two new `_date_cell_case`-built cases exercise the real, asymmetric `compare_date` rule
via a measure column instead.

### 6. The row-count-and-totals check is evidence, not a third verdict condition

§10.3's own "Verdict" bullet names exactly two conditions — *"PASS if no key
differences and no failing cells; FAIL otherwise"* — and calls the row-count/totals
check *"a cheap early signal"* in the bullet immediately above, language describing a
diagnostic, not a gate. Implemented literally: `DiffResult.row_count_within_tolerance`
and `.totals` are always computed and always included in the evidence bundle, but never
independently flip an otherwise-PASS verdict to FAIL — the same "spec wins, implemented
exactly as written" discipline this codebase already applied when the spec's own
wording and a looser paraphrase disagreed (S5.5.2's own dual-condition retirement rule).

### 7. Column mapping via MAPS_TO reads a real `target_column` edge property, honestly empty today

`verdicts._column_target_map_for_sheet` runs the identical real query
`compositor._maps_to` already established for reading `target_column` off a live
`MAPS_TO` edge — confirmed via `hydrate`, the standard property-read path, not a
name-matched guess. Honestly empty in every real deployment today, the same
already-disclosed binding gap `case_execution._table_map_for_sheet` already found for
DAX table qualification; an unmapped column falls back to its own source name.

### 8. The evidence bundle's own "candidate DAX" is recomputed, not persisted separately

S7.3.1/S7.3.2 never persisted the query text, filter context, or parameter values
anywhere beyond one execution response — only Parquet rows/columns survive. Rather than
reopen that already-shipped ontology to add a new `ParityCase` property, the evidence
bundle's own query text is recomputed fresh via the identical, deterministic
`build_dax_query` (and a fresh `_table_map_for_sheet` read) the original execution used
— a real, disclosed choice: a `MAPS_TO` binding added or removed between execution and
diffing would change what the evidence bundle shows versus what was literally asked at
execution time. **The executed strategy itself is not recomputed** — it is read back
from `public.execution_observation` (S7.3.2's own real, persisted execution history),
so "the source strategy used" in evidence is always the true historical fact.

### 9. `ParityRun.suite_ref` is the workbook id directly, and `latest_parity_run` reads by it, not via `PROVED_BY`

The identical anchor `ParityCase.mu_ref` already uses (S7.2.1's own "mu_ref anchors
directly, not `ReportDefinition`" precedent, ADR 0051) — not `public.parity_suite`'s own
relational row id, which would need a second query to resolve for no benefit
`suite_ref`'s own stated purpose ("which coverage universe was this run over") does not
already get from the workbook id alone. **Found live, during this story's own
integration testing**: an earlier draft of `latest_parity_run` followed
`simulate_charter`'s own `ReportDefinition --PROVED_BY--> ParityRun` lookup — correct
for `simulate_charter`'s own G1/re-proof context, but wrong here, since it made
`GET .../parity-run` return a false 404 for any workbook that had been derived,
executed and diffed but never composed into a report. Fixed before this story's own
docs were written: `latest_parity_run` now scans live `ParityRun` nodes by
`suite_ref == workbook_id` directly, so the read route works the moment a run exists,
matching case derivation's and execution's own "reads only the source side, can run
before any report exists" precedent. `PROVED_BY` is still written whenever a real
`ReportDefinition` exists (S7.1.1's own re-proof-marking consumer still wants it), just
no longer required to *read a run back*.

### 10. The 200-case fixture set is generated by named, per-rule builder functions in a checked-in test module, not a data file

The closest precedents (`rules.py`'s `GoldenCase`, `generation.py`'s
`TRANSPILE_C3_EVAL_CASES`) are both small, hand-written Python tuples in-source — the
established shape for a fixture corpus in this codebase, with no external data-file
format used anywhere. At 200 cases (an order of magnitude larger than either
precedent), a single flat literal would not support the AC's own "covering each charter
rule" traceability — `diff_fixtures.py` instead groups cases into one generator
function per charter rule/algorithm step, each producing several genuinely distinct,
boundary-varied cases, so `test_diff_fixtures.py` can assert real per-category coverage
(minimum count per category, not just a total). Two of the charter's nine blocks
(`ParamRule`, `WaiverRule`) and several individual fields (`NumericRule.rounding`/
`.currency_scale`, `DateRule.timezone`/`.fiscal_year_start`, `StringRule.collation`,
`OrderingRule.top_n_tie_break`) are disclosed as declared-but-unconsumed by this
story's own algorithm, each confirmed by direct inspection and pinned by a dedicated
`"unconsumed"` category rather than silently left untested.

### 11. "CI runs them on every change" needed no new CI step

`tests/test_diff_fixtures.py` is an ordinary, parametrized pytest module; the existing
"Unit tests" CI step (`pytest -m "not integration" -q`) already runs every one of the
200 cases on every change, the identical way `tests/test_rules.py` already runs every
rule's own golden corpus. A standalone CLI + Makefile target
(`tools/rule_regression_check.py`'s own shape) was considered and declined: that tool's
real job is re-rendering a *tenant's live accumulated graph* against the *current* rule
set (S5.2.2's own, different concern) — there is no live graph data for this story's own
static fixture corpus to regress against, so building a second mechanism would add
infrastructure this AC does not actually need.

## Consequences

- New `services/graph-svc/src/astra_graph/diff.py`: `classify_type`, `join_kind`,
  `DiffResult`/`FailingCell`/`TotalCheck`, `diff_result_sets` (pure).
- New `services/graph-svc/src/astra_graph/verdicts.py`: `run_parity_for_workbook`,
  `latest_parity_run`, `VerdictsService`, `VerdictError` (graph-coupled orchestration).
- `tolerance_charter.py`: `compare_date` added; `compare_cell` dispatches `"date"`;
  `RowRule.max_failing_cells: int = 50` added.
- `case_execution.py`: `result_set_from_parquet` added (the read-back symmetric pair to
  `result_set_to_parquet`), reconstructing `Column.role` from the case's own
  `grain`/`measures` and `Column.type` from the Parquet schema's own pyarrow dtype.
- New routes: `POST /v1/workbooks/{id}:run-parity` (`ParityEngineerDep`),
  `GET /v1/workbooks/{id}/parity-run` (any Artizent role).
- No ontology or migration change — see decision 1.
- New `tests/diff_fixtures.py` (200 cases, 12 named categories) and
  `tests/test_diff_fixtures.py` (parametrized driver + coverage assertions).
- Verified: 204 fixture cases pass (all 200-plus, with real per-category coverage
  assertions); the full existing graph-svc suite green alongside them; real dual
  execution → real diff → real `Verdict`/`ParityRun` nodes and a real, readable
  evidence-bundle artefact against real PostgreSQL + Apache AGE; `PROVED_BY` really
  written when a `ReportDefinition` exists and honestly absent when none does; a real
  source-side luid mismatch really produces a real `INCONCLUSIVE` verdict, not a crash;
  both new routes drive their own real role gate.

## Alternatives considered

**Build the diff algorithm as a single graph-coupled function, reading artefacts and
writing nodes in one pass.** Rejected — see decision 2. Splitting the pure comparison
step out is what makes the AC's own 200-case fixture set testable without a database at
all, matching this codebase's own established pure/graph-coupled split everywhere else.

**Give `diff.py` its own cell comparators, separate from `tolerance_charter.py`'s.**
Rejected — see decision 4. `compare_numeric`/`compare_null`/`compare_string` already
existed; a fourth comparator belongs beside them, not duplicated, so `simulate_charter`
keeps working on date cells for free.

**Apply `compare_date`'s own asymmetric per-pair truncation rule to grain keys too.**
Rejected — see decision 5. There is no fixed source/candidate pairing available yet at
key-build time (both sides are normalised independently before matching), so only a
symmetric simplification is computable; the asymmetric rule stays exact at the cell
level, where a real pair exists.

**Let the row-count-and-totals check independently fail an otherwise-PASS verdict.**
Rejected — see decision 6. §10.3's own "Verdict" bullet names exactly two conditions and
calls the row/totals check a "cheap early signal" — a diagnostic, not a gate; broadening
it would contradict the spec's own literal wording without a stated reason to.

**Read `latest_parity_run` via `PROVED_BY`/`ReportDefinition`, matching
`simulate_charter`'s own precedent.** Rejected after being tried and found wrong live —
see decision 9. `simulate_charter`'s own G1/re-proof context genuinely needs the report
link; a general "what's the latest run for this workbook" read does not, and requiring
one would wrongly gate a source-side-only read on report composition having happened.

**Store the 200 fixture cases as an external JSON/YAML corpus rather than Python
generator functions.** Rejected — see decision 10. No fixture corpus anywhere in this
codebase uses an external data format; a data file would also lose the ability to
express "boundary variations of one scenario" as a small parametrised loop, which the
Python-function shape gets for free.

## Open questions for the product owner

- Should `RowRule.max_failing_cells` become editable from the console's own Tolerance
  Charter editor (S7.1.1's own screen) now that a real diff produces real evidence
  bundles for it to bound, the same way every other charter field already is?
- Now that `compare_date` is real, should `DateRule.timezone`/`.fiscal_year_start`
  actually be wired up (fiscal-grain date comparison), or do they stay declared-only
  until a real fiscal-year use case is named?
- Should the evidence bundle's own recomputed "candidate DAX" instead be persisted at
  execution time (a `ParityCase.candidate_query` property), closing the gap decision 8
  discloses, the next time `case_execution.py` itself is revisited?
