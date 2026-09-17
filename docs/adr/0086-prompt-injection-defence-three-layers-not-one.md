# ADR 0086 — Prompt-injection defence: three layers, not one

Status: accepted · 17 September 2026 · Story S11.4.3, closes F11.4 (E11)

## Context

S11.4.3 — the story's own text, verbatim: *"As a platform engineer, I want
prompt-injection defence on source content, so that a hostile string in a workbook
cannot steer an agent."*

- Source content enters prompts only inside typed fields, never as instructions; the
  assembler escapes and delimits it
- Gateway runs an injection classifier on typed content; hits are logged and the field
  is replaced with a placeholder plus an ExceptionCase for a human
- Model output is validated against the schema before any use; an output containing
  instructions or references outside the schema is rejected
- Red-team set of 200 injection cases runs in CI against the Transpiler and Mender
  paths; zero successful steering is the bar

Spec §16.5, verbatim: *"Source workbook content — field names, calc comments, custom
SQL, descriptions — is untrusted. It reaches a model only inside typed fields of the
context contract, never in the instruction position; the gateway screens it with an
injection classifier; and model outputs are validated against schema before any use."*

Research (an `Explore` agent's own full pass over `gateway.py`'s `_build_prompt`,
`generation.py`/`mender.py`'s own request/response shapes, `redaction.py`, the
`ExceptionCase` write pattern, and the eval-harness precedent) found: **nothing in
this codebase escaped or delimited a request field before this story** —
`_build_prompt` JSON-encoded each field's value (an incidental, not deliberate,
escaping of embedded control characters) and joined every field as an equal
`key: value` line, source-derived content and platform-controlled instructions alike,
with no boundary between them. **No injection classifier, or anything shaped like
one, existed** — `redaction.py`'s own pattern scanner is structurally close (a
scan-every-string-leaf pass) but solves the opposite problem (hiding outbound PII, not
detecting inbound hostile instructions). **`extra="forbid"` catches an unexpected
top-level response field but nothing embedded inside an allowed field's own string
value** — a genuine, confirmed gap between what schema validation already did and
what AC bullet 3 asks for. **No ML training or serving infrastructure exists
anywhere in this codebase.**

Given this, three genuinely blocking design questions were put to the user before
writing any code (each answered "(recommended)"):

1. **How should the classifier detect hostile content?** → *A heuristic/pattern-based
   scan* — the identical honest-heuristic footing `redaction.py`'s own scanner already
   established; no ML infrastructure exists to build or serve a trained one.
2. **How should the assembler escape and delimit typed content?** → *XML-style tags
   per field*, with the field's own content escaped so it can never syntactically
   close its own tag and claim the instruction position.
3. **How should the 200-case red-team suite actually run in CI?** → *Deterministic
   defense-layer tests, no live Anthropic API* — testing whether this platform's own
   gateway ever lets a hostile instruction reach a model, not whether a live model
   happens to resist one on a given run (not a stable CI gate).

## Decisions

1. **`_build_prompt` delimits and escapes every field as `<field name="...">...
   </field>`.** `_escape_field_value` neutralises `&`/`<`/`>` inside each field's own
   JSON-encoded value, so a field's own content can never syntactically close its tag
   and open a new one in the instruction position. The system prompt states plainly
   that tagged content is untrusted data, never a command, even when it claims
   otherwise — the AC's own first bullet, verbatim.

2. **A new, dedicated module, `injection_defense.py`, owns detection only — never
   dispatch or schema wiring, and deliberately separate from `redaction.py`.** A real,
   checked-in set of seven pattern categories (imperative override, role override,
   delimiter breakout, "reveal your system prompt," and more) scans every string leaf
   of a scanned field's own value. `PLACEHOLDER` is a fixed, generic string — it never
   echoes which pattern matched or any of the withheld content, so the placeholder
   itself cannot become a second injection surface. This is a real, disclosed
   heuristic, not a guarantee: a false positive costs a withheld field and a human
   review; a false negative is the exact steering this story exists to prevent — the
   identical trade-off `redaction.py`'s own docstring already discloses.

3. **`gateway._dispatch` runs the scan on `INJECTION_SCAN_FIELDS`
   (the source-derived subset of each task class's own `TASK_CLASS_FIELD_SCHEMAS`,
   never `task`/`constraints`/`output_schema`/`class_instruction`/`charter_excerpt`/
   `widened`/`model_ctx`) before redaction — a field withheld here never reaches the
   pattern-redaction scanner at all.** A hit **skips the real provider call
   entirely**, the more conservative of two possible readings: rather than sending a
   placeholder-bearing request, the agent is never invoked with this field at all, no
   real API cost or latency spent on a call whose response the ladder would discard
   unread anyway — the identical "nothing was ever really sent" footing a
   schema-validation refusal already has. The hit is logged unconditionally
   (`gateway_request_log.injection_flagged_fields`, a new `jsonb` column, the
   identical footing `redaction_count` already has — metadata about the call, never
   gated by the S11.4.2 content-logging grant) and carried on a synthetic
   `RawModelResponse.injection_flagged_fields`, so the caller — `generation.py`'s
   ladder, `mender.py`'s repair loop, the two places with real workbook/
   `ExceptionCase` context — is the one that escalates to a human, not the gateway
   itself.

4. **Model-output validation reuses the existing rung-1 schema-check point, not a new
   failure category.** `generation.ModelResponseSchema`/`mender.RepairResponseSchema`
   gained pydantic validators (`injection_defense.reject_if_injection`) on every
   string field (`dax`, `m`, `notes`, each `assumptions` item) — a response whose own
   string content looks like an injection attempt fails schema validation the
   identical way `extra="forbid"` already does, no retry, the prompt/response
   contract's own fault (§16.1's already-established reasoning).

5. **`ExceptionCase.class` and `MenderPass.result` each gain one new, disclosed enum
   value — `INJECTION_SUSPECTED` and `INJECTION_DETECTED` — the fourth and second
   disclosed non-§11.1 uses of these two taxonomies respectively.** `generate_c3_field`
   writes a *new* `ExceptionCase` (none existed yet) with class
   `INJECTION_SUSPECTED` when the ladder stops on an injection hit, and skips
   `record_failure_and_maybe_retire` for it (the source was never really exercised —
   not evidence against a pattern's own correctness). `mend_exception` operates on an
   *already-open* case (from an earlier parity failure), so its own `MenderPass`
   simply records `result="INJECTION_DETECTED"` and the pass loop breaks — never
   retried, the identical "not this pass's own fault" footing `MODEL_UNAVAILABLE`
   already has — the case's own original `class` is never overwritten, preserving its
   real diagnosis. Confirmed, by direct research, that adding an enum value is not a
   breaking ontology change (`ontology/lock.py`'s own `diff` only flags a *removed*
   value); no migration entry or `SCHEMA_VERSION` bump needed, the identical footing
   the three prior disclosed additions to `ExceptionCase.class`
   (`VISUAL_REDESIGN`/`REGRESSION`, and now this story's own) already established.

6. **The 200-case red-team suite is a real cross product, not 200 hand-typed
   near-duplicates: 15 distinct injection phrasings (covering all seven pattern
   categories) × every real typed-content field across both real task classes (5
   Transpiler + 9 Mender) = 210 cases.** Each runs the real gateway
   (`ModelGateway.generate` → `_dispatch` → the real
   `injection_defense.scan_payload_for_injection`) against a `ModelCaller` double that
   raises if it is ever actually called — the strongest, most literal proof of "zero
   successful steering" this suite can give: not that a model resisted an attack, but
   that the model was never invoked with the hostile content in the first place. No
   live Anthropic API calls, no workflow changes needed: the suite carries no
   `pytest.mark.integration` marker, so it rides the existing PR-blocking `Unit tests`
   step in `ci.yml` automatically.

## Consequences

- `services/graph-svc`: new `injection_defense.py` (`scan_text_for_injection`,
  `scan_payload_for_injection`, `reject_if_injection`, `PLACEHOLDER`,
  `INJECTION_SUSPECTED_CLASS`). `gateway.py` gained `INJECTION_SCAN_FIELDS`,
  `_escape_field_value`, a delimited `_build_prompt`, an injection-aware
  `_SYSTEM_PROMPT`, `RawModelResponse.injection_flagged_fields`, and a `_dispatch`
  that skips the real provider call on a hit. `GatewayRequestLogStore.record`
  (Protocol, Postgres, in-memory) gained `injection_flagged_fields`. New migration
  v0045 (`gateway_request_log.injection_flagged_fields jsonb`, additive, no ontology
  change). `generation.py`: `ModelResponseSchema` gained injection validators;
  `LadderAttempt.injection_flagged_fields`; `_run_ladder` stops immediately on a hit;
  `generate_c3_field` writes a real `ExceptionCase` of class
  `INJECTION_SUSPECTED`, skipping `record_failure_and_maybe_retire` for it.
  `mender.py`: `RepairResponseSchema` gained the identical validators;
  `call_model_repair` returns a new `INJECTION_DETECTED` result; `mend_exception`'s
  pass loop breaks on it, the identical footing `MODEL_UNAVAILABLE` already has.
  `ontology/nodes.py`: `ExceptionCase.class`/`MenderPass.result` each gained one new
  enum value, both disclosed as `SpecDeviation`s. `exception_ageing.py`:
  `INJECTION_SUSPECTED` joins `VISUAL_REDESIGN` as excluded from the Mender
  close-rate ratio — neither is a real failure the Mender ever attempts to fix.
- **A real, disclosed regex bug was found and fixed by the red-team suite itself, not
  by review**: `ROLE_OVERRIDE`'s own pattern read `\back\s+as\b` (a typo — "ack as,"
  never actually matching "act as") until the 210-case corpus's own "Act as a system
  administrator..." case failed across all 14 real (task class, field) combinations
  at once, pinpointing the exact bug by its own uniform failure signature. Fixed to
  `\bact\s+as\b`; every case then passed. Disclosed here as the story's own proof that
  the red-team suite's real value is catching exactly this kind of defect before it
  ships, not only after.
- Verified: `services/graph-svc` — 240 new unit tests (20 in `test_injection_
  defense.py`'s own pure-function coverage of every pattern category and the
  whole-field-replacement/never-mutates-input contract; 211 in the red-team suite,
  210 real cases plus its own corpus-size check; 7 new `gateway.py` tests, dispatch-
  level flagging/logging plus prompt-delimiting; 1 new `generation.py`
  ladder-short-circuit test; 1 new `mender.py` `call_model_repair` test). 2 new
  integration tests against real PostgreSQL + Apache AGE: a real
  `generate_c3_field` call on a hostile `CalculatedField.formula` writes a real
  `ExceptionCase` of class `INJECTION_SUSPECTED`, the real provider never called; a
  real `mend_exception` call on a hostile formula records a real `MenderPass` of
  result `INJECTION_DETECTED` and leaves the case `OPEN`, never retried into a third
  pass. The full graph-svc suite re-ran clean (2848 passed, up from 2606, 2 skipped —
  the pre-existing, disclosed, no-live-Anthropic-key gate — zero regressions);
  `ruff`/`mypy`/`ontology_check.py`/`migration_check.py` all clean — two additive,
  non-breaking enum values, no `SCHEMA_VERSION` bump, no migration entry needed for
  either. `services/console-web` — confirmed, by direct research, that no change is
  needed: the Exception Desk's own class filter is a plain text input, not a fixed
  dropdown, and no component anywhere hardcodes the `ExceptionCase.class` value set —
  a case of class `INJECTION_SUSPECTED` renders in the existing generic UI with zero
  code changes.

## Alternatives considered

**Send a placeholder-bearing request to the real provider instead of skipping the
call entirely.** This was the first design, and unit tests were written against it
before being reworked. Rejected on reflection: it spends a real API call and real
latency on a response the ladder is about to discard unread the moment it sees the
flag, and "so that a hostile string ... cannot steer an agent" reads more strongly as
"the agent is never given the chance" than "the agent is given a redacted chance."
Skipping the call is strictly more conservative and costs nothing extra to implement.

**A trained ML classifier for injection detection**, closer to what "classifier" can
suggest outside this codebase. Rejected by the user's own explicit choice: no ML
training or serving infrastructure exists anywhere in this platform, and building one
for this single story would be new, disclosed-absent-elsewhere infrastructure far
beyond this story's own real scope.

**Reuse `redaction.py` for injection detection** rather than a new module. Rejected —
the two mechanisms solve opposite problems (hiding outbound sensitive data vs.
detecting inbound hostile instructions) and have different replacement semantics
(edit a matched substring in place vs. replace a whole field with a fixed
placeholder); merging them would obscure that a "redaction hit" and an "injection
hit" mean structurally different things to a human reviewing the log.

**Overwrite the Mender's own existing `ExceptionCase.class` to `INJECTION_SUSPECTED`
when a repair pass detects one**, matching `generate_c3_field`'s own fresh-case
behavior exactly. Rejected — that case already carries a real, earlier diagnosis (a
genuine parity failure); overwriting it would erase that fact and misrepresent why
the case was ever opened. The `MenderPass.result` value alone is real, sufficient,
human-visible evidence of the injection hit without destroying the case's own
history.

**A live-API red-team suite (all 200 cases actually calling Anthropic), matching "runs
... against the Transpiler and Mender paths" most literally.** Rejected by the user's
own explicit choice: real cost, real latency, and a probabilistic result (a live
model's own resistance to a given prompt is not guaranteed reproducible run to run) —
not a stable, blocking CI gate. Testing whether this platform's own gateway ever lets
a hostile instruction reach the model is a stronger, deterministic claim than testing
whether a model happened to resist one on a given day.
