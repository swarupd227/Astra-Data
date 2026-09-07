# ADR 0053 — Dual execution: a symmetric ResultSet, and two persistent concurrency pools

Status: accepted · 7 September 2026 · Story S7.3.1, opening F7.3

## Context

S7.3.1 opens F7.3 — spec §10.2: *"As a parity engineer, I want each case executed on
the source via the adapter and on the target via XMLA and the results stored, so that
the comparison is between two executions, never between an execution and a
re-implementation."*

- Target side: DAX `EVALUATE` over XMLA against the dev or test model, with filters and
  parameter values applied per §10.2; query text stored
- Source side: adapter `execute_case` with the chosen strategy; strategy stored
- Both ResultSets stored as Parquet in the artefact store with content hash; retention
  per charter
- Execution is parallel per MU with a configurable concurrency per Fabric workspace
  (default 8) and per Tableau site (default 4)

S7.2.1/S7.2.2 (immediately prior, ADR 0051/0052) built real `ParityCase` nodes — grain,
measures, filter context, parameter values, a stable `case_key` — but never ran one.
This story is the first to actually execute a case on either side.

## Decisions

### 1. Both sides return the identical `ResultSet` type — §10.2's own words, not a design choice

§10.2, verbatim: *"The expected side is produced by the source adapter's executeCase...
and the candidate side by the target executor as a DAX query over XMLA. Both return a
ResultSet: an ordered list of column descriptors (name, role, type) and rows."* Rather
than invent a second, target-shaped type, `astra_adapter.proof.ResultSet` — already the
source side's own §6.1 return type — is reused for the candidate side too, with one new
`ExecutionStrategy.XMLA_DAX` member added to name it. This is what lets a future diff
(F7.4, §10.3-§10.6) treat expected and candidate symmetrically instead of writing a
type-specific comparison for each side.

### 2. `TargetAdapter.evaluate` takes an already-built query string, not a case to build one from

The identical "platform decides what, adapter decides how" split this codebase already
draws for TMDL emission (`TmdlBundle`'s own docstring: *"TMDL emission is deliberately
not a method here"*) is drawn again here. Building the DAX query needs graph access
(field names, and whatever `Field -> ModelTable` binding exists) that an adapter has no
business having; running it against XMLA does not need graph access at all. `evaluate(*,
query_text, case, workspace)` takes the finished string; `case` rides along only for
identification (`ResultSet.case_id`) and capability checks. `build_dax_query` (this
module) does the building, from a case's own real grain/measures/filters/parameters,
producing §10.2's own worked-example shape (`EVALUATE SUMMARIZECOLUMNS`, `TREATAS`/
`FILTER` for filter and parameter values, named measure expressions, `ORDER BY`).

### 3. `FixtureTargetAdapter.evaluate` returns deterministic synthetic rows, not an honest empty result

`smoke_query` (S4.3.2) returns an honest empty/false result rather than fabricate an
integrity check — a fake positive there would mislead. `evaluate` chooses differently,
mirroring `FixtureSourceAdapter.execute_case`'s own precedent instead: a parity case's
result exists to be *compared*, and a future diff (F7.4) needs comparable data on both
sides to exercise against. The synthetic rows are seeded from the query text itself (not
the case id alone), so a case whose filters or parameters changed genuinely produces a
different candidate — the same way a real re-query would. `detail["note"]` discloses
plainly that no live Fabric analysis-services engine is configured.

### 4. Neither adapter method takes the Tolerance Charter

Source-side strategy selection is the adapter's own capability-driven decision
(`FixtureSourceAdapter.execute_case` chooses from its own declared capabilities, not a
passed-in charter). Target-side execution has exactly one strategy (`XMLA_DAX`). This
module therefore never reads the charter — `params.enumerate_max_values` is S7.2.1's own
concern, at derivation time, not execution time.

### 5. No diff, no verdict, no retry — confirmed against the backlog's own two-story scope

F7.3 has exactly two stories: S7.3.1 (this one) and S7.3.2 ("INCONCLUSIVE... the
orchestrator retries once with a longer budget"). §10.3-§10.6 (normalisation, the
row/key diff, sampling, visual parity, regression) are F7.4's own later scope. A case
that fails or times out on either side is recorded `INCONCLUSIVE` once, honestly — §10.2
itself already gives a failed execution this outcome (*"a timeout... yields
INCONCLUSIVE, not FAIL"*) — without the retry loop S7.3.2 owns next.

### 6. Concurrency is two independent, persistent semaphore pools — a first for this codebase

`harvest/runner.py`'s own `_harvest_all` bounds parallel work with one flat
`asyncio.Semaphore` per run. This story needs two independent named bounds instead (a
Fabric workspace's own XMLA concurrency, default 8; a Tableau site's own adapter
concurrency, default 4), and they must persist across calls — two different MUs
executing concurrently against the same workspace share the same real bound, not a fresh
one each time. `CaseExecutionService` holds both pools (`dict[str, asyncio.Semaphore]`,
lazily populated by workspace/site name) for its own lifetime. Each side of a case's dual
execution acquires only the one semaphore relevant to it — the source call under the
site's pool, the target call under the workspace's pool — since the two calls already run
concurrently via `asyncio.gather`, not sequentially under one shared bound.

### 7. `ParityCase.state` is left alone; `expected_ref`/`candidate_ref` are what execution actually populates

`state` already carries S7.2.1/S7.2.2's own origin tag (`DERIVED`/`MANUAL`); overwriting
it with an execution outcome would destroy that fact for no gain, since
`expected_ref`/`candidate_ref` — both already declared by §4.1.1 (*"Result set from the
source/target side"*), unused until this story — are already a real, sufficient "has
this case been executed" signal on their own. Confirmed by direct research: these two
properties have existed in the ontology since the very first §4.1.1 declaration, so
**this story required zero ontology or migration change** — `ontology_check.py --spec`
and `migration_check.py` both pass unchanged (28 node types, 15 edge types, 36 declared
deviations; schema version 27).

### 8. DAX column references are qualified via a real, currently-empty `Field -> ModelTable` `MAPS_TO` lookup

The identical, already-disclosed gap this codebase has found repeatedly (`compositor.py`'s
own field-well binding, most recently) applies here too: `_table_map_for_sheet` runs a
real `MAPS_TO` query and uses a real binding when one exists, but honestly falls back to
a field's own name as its own table when none does — a disclosed placeholder, not a
guess dressed up as a real one. The query text is stored either way
(`ResultSet.detail["dax_query"]`), so what was actually asked stays auditable regardless.

### 9. "Retention per charter" is a real, disclosed gap this story does not close

Confirmed by direct research: neither §10 nor §4.4's own Tolerance Charter schema
(numeric, nulls, dates, strings, ordering, rows, sampling, params, waiver) has a
retention concept at all. The only "retention" anywhere in the spec is an unrelated,
tenant-wide Data Handling setting for the AI inference boundary. This module stores
Parquet artefacts and never prunes them — the identical "nothing prunes today, and that
is deliberate" posture `retention.py` already established for a different kind of
retention, extended honestly to a charter field that does not exist yet rather than
inventing one.

### 10. Parquet via pyarrow directly, not pandas

`result_set_to_parquet` writes a `ResultSet`'s own columns/rows straight to Parquet
bytes with `pyarrow.parquet.write_table` alone — pandas would be a second dependency for
what pyarrow, the reference Parquet implementation pandas itself delegates to, already
does. `pyarrow` is a `graph-svc`-only dependency, not added to `adapter-sdk`, which
stays deliberately small. `ArtefactStore.store`'s own `content_hash` (sha256 over the
stored bytes, S2.4.2) is already the AC's own "content hash" — no new hashing logic
needed.

## Consequences

- `astra_adapter.proof`: `ExecutionStrategy.XMLA_DAX` added.
- `astra_adapter.target_contract`: `TARGET_INTERFACE_VERSION` 1.0 → 1.1; `TargetAdapter`
  gains `evaluate(*, query_text, case, workspace) -> ResultSet`.
- `astra_adapter.target_fake`: `FixtureTargetAdapter.evaluate` — deterministic synthetic
  rows, disclosed as fixture data, never a live analysis-services engine.
- New module `services/graph-svc/src/astra_graph/case_execution.py`: `to_sdk_filters`,
  `to_sdk_parameters`, `build_dax_query`, `result_set_to_parquet` (pure);
  `_table_map_for_sheet`, `_resolve_site`, `_execute_one_case`,
  `execute_cases_for_workbook` (graph-coupled); `CaseExecutionService` (the
  `Compositor`/`CaseDerivationService` app.state-bound-object shape, holding both
  concurrency pools for its own lifetime).
- New route: `POST /v1/workbooks/{id}:execute-parity-cases` (`ParityEngineerDep`,
  `?workspace=` query param, default `"dev"`). No new read route — execution results
  land on the same `ParityCase` nodes S7.2.1's own `GET .../parity-cases` already lists.
- `services/graph-svc/pyproject.toml`: `pyarrow>=17,<19` added; mypy override for
  `pyarrow.*`.
- No ontology or migration change — see decision 7.
- Verified: `packages/adapter-sdk`'s own suite green (118 tests, 5 new); real dual
  execution against real PostgreSQL + Apache AGE produces real Parquet artefacts with
  real content hashes, `expected_ref`/`candidate_ref` really written back while `state`
  stays untouched, a real `MAPS_TO` binding really qualifies the DAX text when one
  exists, a source-side adapter failure (an unmatched workbook luid) is really recorded
  `INCONCLUSIVE` rather than crashing the run, and every new route drives its own real
  role gate — 19 new unit tests, 11 new integration tests, full graph-svc suite run
  clean alongside the one already-flagged, pre-existing, unrelated
  `test_integration_g2_reminders.py` flake.

## Alternatives considered

**Give the target side its own result type, distinct from the source side's
`ResultSet`.** Rejected — see decision 1. §10.2 states plainly that both sides return a
ResultSet; a second type would need its own diff logic later for no benefit.

**Let `TargetAdapter.evaluate` build its own DAX query from the case.** Rejected — see
decision 2. Building the query needs graph access (table bindings) an adapter has no
business having, and this codebase has already drawn the identical "what vs. how" line
for TMDL.

**Return an honest empty/INCONCLUSIVE result from `FixtureTargetAdapter.evaluate`,
matching `smoke_query`'s own honesty.** Rejected — see decision 3. A smoke query is an
integrity check where a fake positive misleads; a parity result exists to be compared,
and comparable synthetic data is more useful for exercising the diff a future story
builds.

**One flat semaphore per run, matching `harvest/runner.py`'s own precedent.**
Rejected — see decision 6. The AC asks for two independently-configurable, named bounds
(Fabric workspace, Tableau site) that must persist across calls, not reset per run.

**Overwrite `ParityCase.state` with an execution outcome (e.g. `EXECUTED`).**
Rejected — see decision 7. `state` already carries a different fact (origin:
DERIVED/MANUAL) this story has no reason to destroy; `expected_ref`/`candidate_ref`
already say "this case has been executed," and reusing already-declared, previously-
unpopulated properties needed zero ontology change.

## Open questions for the product owner

- Should "retention per charter" become a real Tolerance Charter field once a Parity
  Engineer actually needs to bound how long Parquet artefacts are kept, the same way
  `params.enumerate_max_values` already is?
- Once a real Fabric XMLA endpoint and a real Tableau adapter both exist, should
  `evaluate`'s own workspace concurrency bound be read from deployment configuration
  per workspace, or does the flat default-8 bound (this story's own reading of "a
  configurable concurrency per Fabric workspace") stay a single number across every
  workspace?
- Should a future story add a real `Field -> ModelTable` binding-writer (closing the gap
  decision 8 discloses), or does DAX column qualification stay a disclosed placeholder
  until F7.4's own diff work makes an unqualified reference visibly wrong?
