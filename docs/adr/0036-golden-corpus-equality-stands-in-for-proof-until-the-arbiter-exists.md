# ADR 0036 — Golden-corpus equality stands in for proof until the Arbiter exists

Status: accepted · 4 September 2026 · Story S5.2.1 (E5 / F5.2)

## Context

S5.2.1 asks for the second piece of the Transpiler, right after S5.1.1's classifier:
*"As a platform engineer, I want a rules engine that maps Tableau AST shapes to DAX and
Power Query M templates, so that most of the estate converts without a model call. Rules
are AST-pattern → target-template with guards; shipped set covers the function families in
Appendix B and the common LOD and table-calc shapes. Rule application is DETERMINISTIC
mode: no model call; provenance records the rule id and version. Each rule ships with at
least three golden-corpus cases that must pass proof in CI. Rule coverage report: percentage
of estate calcs matched by rule, by rule family."*

Four questions decided the shape of the work: what "proof" can honestly mean when neither a
DAX parser/compiler nor the Arbiter (§10, E7) exists anywhere in this codebase; how a rule's
own `target_template` handles a model-context fact (which target table a field maps to) the
rules engine itself cannot resolve; where node-level composition (§9.2's own "walks the AST
bottom-up... rewritten") and shape-level pattern matching (§9.3's own "matched by AST
shape... a signature is the AST with leaf identifiers abstracted") fit together as one
engine rather than two; and how large a rule set "covers the function families in Appendix
B" honestly requires shipping in one story.

## Decisions

### 1. "Must pass proof in CI" means golden-text equality plus a structural DAX sanity check — not §16.1's rung-4 parity verdict

§16.1's own validation ladder names rung 4, "Proof," as a full parity verdict, and rung 2,
"Parse," as "DAX / M / TMDL / PBIR parses under the target grammar." Neither exists: no
Arbiter/diff engine has ever been built (only the source-side execution shapes exist,
`packages/adapter-sdk/src/astra_adapter/proof.py`'s own docstring says so outright — *"The
Proof Engine is E7 and does not exist"*), and no DAX parser exists either.
`FixtureTargetAdapter.smoke_query` (S4.3.1) already discloses exactly this gap for query
execution — *"no live Fabric analysis-services engine is configured to run a real ... 
query"* — and this story inherits the identical honest floor. Fabricating a check that
claims rung-4 proof without a live DAX engine to run against would be worse than naming
what this platform can verify today: that a rule's golden cases — real `(source AST,
expected DAX)` pairs, at least three per rule, run in the ordinary `pytest -m "not
integration"` sweep the same way S2.3.1's own calc-grammar corpus already runs in CI — render
to byte-exact expected text, deterministically, plus a structural sanity check (balanced
delimiters, every function name against a small known-DAX allowlist) standing in for rung 2
since no real parser exists to ask.

### 2. A rendered DAX string may carry a literal, unresolved model-context placeholder

§4.3's own worked `Pattern` example ships a `target_template` containing exactly this:
`ALLEXCEPT({table}, {dims})` — an unresolved token, because which target table a field maps
to is the Modeller/Compositor's own fact, not the rules engine's. This story follows the
identical convention rather than inventing a fake table reference: the `c2_lod_fixed` rule's
own rendered output carries the literal string `{table}` wherever DAX's `ALLEXCEPT` needs a
table argument this module cannot supply, disclosed in the rule's own `RuleMeta.description`
and in every affected golden case. AST-derived captures (a measure, a dimension list) are
always fully substituted; only genuinely external, not-yet-bound facts are left as a visible
placeholder — never silently guessed at.

### 3. One engine, two matching strategies, ordered specific-before-general — not two separate systems

§9.2 describes node-level composition (walk bottom-up, rewrite each node, build up a DAX
AST); §9.3 describes whole-shape pattern matching (an AST shape with typed placeholder
captures, matched exactly). Rather than build these as two disconnected mechanisms, `rules.
py`'s own `_render_node` tries a small number of specific *shape* rules (the LOD-fixed
expression, the ZN/IFNULL null-idiom) at every node before falling through to the generic
per-kind/per-family map — the same "specific pattern, then general map, then failure" order
§9.2 itself names for its own C1→C2→C3 downgrade cascade. Captured sub-expressions inside a
shape rule are rendered by recursively calling the same function, so a C1 expression nested
inside a C2 shape (e.g. `SUM(...)` inside a LOD's own measure) still converts correctly
rather than needing a second, separate composition pass.

### 4. The shipped rule set is real and deliberately narrower than Appendix B.1's full table — eight rules, not one per function

Following `classify.py`/`functions.py`'s own established precedent (a registry "wider than
[an early demo] and narrower than Tableau... where it is narrower, the construct is flagged
and kept"), this story ships eight rules: six covering C1 (operators, aggregate functions,
casts/type functions, conditionals, a numeric-function subset, bare leaf references) and two
covering C2 (the null-idiom, and the LOD-fixed shape spec's own §4.3 example names). Every
rule composes recursively, so real coverage is broader than eight patterns literally
suggests — `SUM(ZN([X])) + 1` converts correctly through three different rules composing.
Functions Appendix B.1 lists but this story does not map (STDEV, VAR, table-calc-simple
shapes needing sheet-derived addressing, date-truncation idioms needing a date table,
REGEXP/RAWSQL) are left unconverted — the calculation simply gets no `Measure`, exactly
C3/C4's own existing boundary, not a guess this module cannot back up. Table-calc and
date-truncation rules both need real model-context (sheet partitioning, a date table) this
story does not have reliable data for yet — deliberately deferred rather than shipped with a
fabricated binding.

### 5. Hand-shipped rules stay code; nothing here writes a `Pattern` graph node

§4.3 names three sources for patterns: "the deterministic rule set shipped with the adapter
... LLM-produced transformations that have passed proof repeatedly and been promoted ...
and engineer adjudications that were generalised." Only the second and third ever become
`Pattern` graph nodes (F5.5, not built) — the first is, by its own name, shipped *with the
adapter*, i.e. code, authored via PR (S5.2.2's own scope, also not built). `classify.py`'s
own ADR 0035 already settled this distinction for classification rule ids; this story keeps
it for generation rule ids. A rule's own id and version are recorded on the artefact's
provenance (`ProvenanceRecord.pattern_ref`, already declared since S4.1.1, now written for
the first time with a compound `"<rule_id>:v<version>"` value) rather than a graph-node
reference nothing here has ever created.

## Consequences

- New module `rules.py`: `render_calc()` (pure, one AST → DAX text or a reason it could
  not), `RuleMeta`/`GoldenCase` (documentation and golden corpus, consulted by tests and the
  catalog route, never by the renderer), `dax_sanity_check()` (the structural rung-2 stand-
  in), `apply_rules_estate()` (the estate-wide pass: writes a real `Measure`, a `MAPS_TO`
  edge, and a DETERMINISTIC `ProvenanceRecord` for every field a shipped rule covers; a
  second pass never duplicates an already-converted field), `rule_coverage()` (a live read
  of what the last pass wrote, by rule family).
- Zero ontology changes: `Measure.dax`/`.class`/`.pattern_ref`/`.provenance_ref`/
  `.validation_state` and `MAPS_TO.class`/`.pattern_ref` were all already declared (§4.1.1,
  §4.1.2), waiting for exactly this story to write them for the first time.
- New routes: `GET /v1/calculations:rule-catalog` and `GET /v1/calculations:rule-coverage`
  (any Artizent role), `POST /v1/calculations:apply-rules` (`PlatformEngineerDep`, new — the
  first route to drive that role, declared since S1.1.1 and gated nowhere until now).
- Console: a fifth Programme Board pane, "Rule coverage" — matched fields by rule family
  against the total, with an "Apply rules" action gated to the platform engineer.
- **A real bug found and fixed before it ever reached CI**: a `Measure` node id built as
  `f"msr_{calc_id}"` (a 4-character prefix over a 26-character ULID) exceeded the ontology's
  node-id length limit — the identical shape of defect S3.2.1's own README already
  documents ("every other node id in this codebase is a bare `new_ulid()`... prefixes are
  only used for platform-table ids"). Fixed by using a bare ULID, caught immediately by the
  integration suite's own real-database write path.

## Alternatives considered

**Wait for the Arbiter (E7) before shipping any rules engine, since "proof" cannot be
satisfied honestly without it.** Rejected. The backlog places F5.2 before F5.3/E7 for a
reason — most of the acceleration case rests on deterministic C1/C2 conversion, which does
not need a live target engine to be real and useful; withholding it until E7 exists would
block real, working code behind a dependency the acceptance criteria do not actually name.

**Fabricate a table binding (e.g. a synthetic `'Fact'` table name) so every rendered DAX
string is syntactically complete.** Rejected — see decision 2. A guessed table name that
happens to be wrong is worse than a disclosed placeholder a later Compositor step fills in
correctly; spec's own worked Pattern example ships the identical kind of placeholder.

**Build two separate mechanisms — a node-level rewriter for §9.2 and a shape-matching
Pattern engine for §9.3 — with no shared entry point.** Rejected — see decision 3. Real
calculations nest a C2 shape (an LOD, a null-idiom) inside otherwise-C1 structure and vice
versa; two disconnected passes would each need to re-invoke the other to compose correctly,
which is exactly what one recursive function already does.

**Ship a rule for every function Appendix B.1 names, to maximise coverage in this story.**
Rejected — see decision 4. Several Appendix B.1 rows (table-calc, date-truncation) need
real model-context data (sheet addressing, a date table) this platform does not yet bind
reliably per calculation; shipping a rule that guesses at it would produce DAX that looks
complete but is not trustworthy, the opposite of what a *deterministic* rules engine is for.
