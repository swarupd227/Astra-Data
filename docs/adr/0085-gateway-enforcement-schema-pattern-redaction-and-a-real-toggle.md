# ADR 0085 — Gateway enforcement: schema, pattern redaction, and a real toggle

Status: accepted · 15 September 2026 · Story S11.4.2, closes F11.4 (E11)

## Context

S11.4.2 — the backlog's own AC, verbatim: *"As a platform engineer, I want the gateway
to enforce the boundary, not rely on agents to respect it, so that a prompt bug cannot
leak data."*

- Gateway validates every request against the task class's allowed field schema; a
  request with an unexpected field or a field over size is refused
- Redaction pass removes literals that match data-like patterns (account numbers,
  emails, long numeric literals) from free-text fields, logging the redaction count
- All gateway requests and responses are logged with hashes; content logging is off by
  default and requires the InfoSec role to enable for a bounded window

Spec §18.3 names the same construct. Before any code was written, research (an
`Explore` agent's own full pass over `gateway.py`, `context/contract.py`,
`GenerationRequest`/`RepairContext`, `live_replay_policy.py`, and every existing
bounded-duration concept in this codebase) found: no per-task-class request-field
schema exists anywhere (`context/contract.py`'s own `ContextContract` is real but tied
to the unused `ContextAssembler` path neither real gateway caller goes through); no
per-field byte cap exists to reuse; no pattern-based (regex) redaction scanner exists
anywhere in this codebase; nothing before S11.4.1 logged a response at all; and no
"a role enables a feature for a bounded window, then it auto-expires" mechanism exists
to reuse, though `workload_identity.SvidRecord`'s own `expires_at`/`revoked_at` shape
and `data_handling.py`'s own "validity is a computed comparison, never a stored flag"
convention were both real, directly reusable precedents. One explicit question was
asked and answered before writing any code:

1. **Bounded-window duration** → *"InfoSec specifies duration, capped at a sane max."*
   The InfoSec reviewer names how long a content-logging window runs (a quick check and
   a formal audit need different windows), capped at a fixed platform-wide maximum
   (`MAX_CONTENT_LOGGING_MINUTES = 1440`, 24 hours) so it can never be left on
   indefinitely by accident.

## Decisions

### 1. A hand-maintained field-schema registry, deliberately not derived from the real request dataclasses

`TASK_CLASS_FIELD_SCHEMAS: dict[TaskClass, frozenset[str]]` (`gateway.py`) is a real,
explicit allow-list per task class, built by hand from `GenerationRequest`/
`RepairContext`'s own real, currently-declared fields — but **not wired to read those
fields live**. A schema that auto-synced with whatever a caller's own dataclass
happened to declare would enforce nothing at all: the entire point of "the gateway
enforces the boundary, not the agent" is a boundary a caller's own future field
addition cannot silently widen. `validate_task_class_schema` checks two things against
this registry — an unexpected field, and a field whose own JSON-encoded value exceeds
`MAX_FIELD_BYTES` (32,768 bytes, a fresh, disclosed number: no existing per-field byte
cap exists to reuse, only count-based caps like `RowRule.max_failing_cells=50`; chosen
generous enough for a real, legitimate dependency closure or a widened failing-cell set
and tight enough to refuse a request a bug has stuffed with something this codebase
never intends a prompt to carry). Raises `GatewayValidationError`, refusing the request
before it is ever redacted, logged, or sent — the identical "refuse before anything
happens" footing `GatewayRoutingError` already has for a request with no routable
provider.

### 2. Pattern redaction mutates the real outbound request, not merely the log

`redaction.redact_data_like_literals` (extending S11.4.1's own module) is a
field-name-agnostic scan over every string leaf value in a request's own `as_dict()`
payload — regex-matched against an email shape, an account-number-shaped digit group
(3–4 groups of 4 digits), or a long bare numeric literal (9+ consecutive digits) —
replacing each match with a labelled placeholder (`[REDACTED:EMAIL]`, etc.) and
counting every redaction. **Applied to the payload that is actually handed to the real
`ModelCaller`** (wrapped in a new `_DictRequest`, a minimal `SupportsAsDict` adapter
over the redacted dict), not only to what gets logged — the AC's own "so a prompt bug
cannot leak data" is a claim about what reaches the provider. A real, disclosed
heuristic, not a guarantee: a regex will occasionally redact a legitimate large
constant that merely looks like an account number (accepted — a false positive costs a
slightly less specific prompt; a false negative is the exact leak this story exists to
prevent), and will just as certainly miss a real value in an unrecognised shape,
disclosed as one bounded layer alongside structural redaction and schema validation,
never the only one.

### 3. Content logging is off by default; hashes are always recorded; text only behind a real, computed grant

`ModelGateway.generate`/`StaticGateway.generate` share a new `_dispatch` helper:
validate → redact → wrap → call the provider inside `try`/`finally` (so a request is
always logged even if the call itself fails, now also capturing a real response when
the call succeeds — the AC's own "requests **and responses**," nothing logged a
response before this story). `PostgresGatewayRequestLogStore.record` always persists a
real hash of the request and (when one exists) the response, plus the AC's own
"redaction count"; it persists the literal *text* only when a real, unexpired,
unrevoked `gateway_content_logging_grant` row exists for this graph, checked fresh on
every single write (never cached), so a grant that has just expired or just been
revoked takes effect on the very next request. `ContentLoggingGrant.active` is always
computed (`revoked_at is None and expires_at > now()`), never a stored column — the
identical discipline `data_handling.boundary_status`'s own `signed` field and
`SvidRecord.status` both already established, applied a third time. Migration v0044
alters `gateway_request_log` (`request_text` becomes nullable; new `response_hash`/
`response_text`/`redaction_count` columns) and adds `gateway_content_logging_grant`.

### 4. `POST .../enable-content-logging`/`disable-content-logging` reuse the InfoSec-reviewer-alone gate S11.4.1 already built

Gated `InfosecReviewerDep` (`Role.CLIENT_INFOSEC_REVIEWER` alone), the identical,
unmodified dependency `sign_data_handling` already uses — this is the client's own real
control over whether literal text is ever persisted, not Artizent's to switch on for
them, the same reasoning S11.4.1's own ADR already gave for signing. Reading the
grant's own current status stays on the screen's shared reader gate (any Artizent role,
or the InfoSec reviewer).

### 5. S11.4.1's own boundary test needed a real, self-granted, self-revoked window to keep proving anything

Content logging defaulting off is a real, structural threat to `data_handling.
run_boundary_test` (S11.4.1): its own `contains_text` assertions need real text in the
log to search, and its own deliberate self-check (`marker_logged` must be `True`, "an
empty result can never be mistaken for nothing was ever logged") means the check would
not pass vacuously if this were left unaddressed — confirmed directly: running the
existing boundary test against the redesigned gateway before this fix produced a real,
loud failure (`"no request was logged at all"`), never a silent pass. Fixed by having
`run_boundary_test` itself grant a short, real content-logging window
(`_BOUNDARY_TEST_GRANT_MINUTES = 5`) immediately before its own real gateway call, and
revoke it again in a `finally` block regardless of outcome — a real, minimal use of the
exact mechanism the check is verifying, not a bypass of it, and further, direct proof
(re-checked over HTTP) that the grant really is gone again once the function returns.

## Consequences

- `services/graph-svc`: `gateway.py` gained `GatewayValidationError`, `TASK_CLASS_
  FIELD_SCHEMAS`, `MAX_FIELD_BYTES`, `validate_task_class_schema`, `_DictRequest`,
  `ContentLoggingGrant`/`ContentLoggingGrantStore`/`PostgresContentLoggingGrantStore`/
  `InMemoryContentLoggingGrantStore`, `MAX_CONTENT_LOGGING_MINUTES`, and a shared
  `_dispatch` helper replacing S11.4.1's own request-only `_log_request`. `redaction.py`
  gained `redact_data_like_literals`/`EMAIL_PATTERN`/`ACCOUNT_NUMBER_PATTERN`/
  `LONG_NUMERIC_LITERAL_PATTERN`. `data_handling.run_boundary_test` now grants and
  revokes its own real, scoped content-logging window. New migration v0044 (`gateway_
  request_log.request_text` now nullable; new `response_hash`/`response_text`/
  `redaction_count` columns; new `public.gateway_content_logging_grant`, no ontology
  change). `api/routes_data_handling.py` gained `POST /v1/data-handling:enable-
  content-logging`/`:disable-content-logging`, and `GET /v1/data-handling` now includes
  `content_logging_grant`. **Two real, intended regressions were found and fixed by the
  full unit suite, not by review**: `test_agent_enforcement.py`'s own two gateway tests
  passed a bare `object()` as a request — valid before this story, since `_log_request`
  short-circuited on `log_store is None` before ever touching the request; invalid now,
  since schema validation and redaction run unconditionally, on every real dispatch,
  regardless of whether a log store happens to be configured. Fixed by giving both
  tests a minimal real `SupportsAsDict` double, disclosed in the test file itself as a
  direct, intended consequence.
- `services/console-web`: the Data Handling screen gained a fourth pane, Content
  logging — off/active status, a real InfoSec-specified duration field (capped at
  `MAX_CONTENT_LOGGING_MINUTES`), Enable/Disable, both hidden for every role but the
  InfoSec reviewer (the identical hide-not-disable convention "Sign boundary" already
  uses). New `ContentLoggingGrant` type and `enableContentLogging`/
  `disableContentLogging` methods in `lib/api.ts`.
- Verified: `services/graph-svc` — 26 new unit tests (`redaction.py`'s own pattern
  scanner against every named shape, nested structures, non-string leaves, and
  never-mutates-the-input; `gateway.py`'s own schema validation at and over the byte
  limit, an unexpected field refused, a real payload redacted before a captured
  provider call ever sees it, and the in-memory grant store's own full lifecycle
  including its own duration bounds). 15 new integration tests against real
  PostgreSQL: a real unexpected field and a real oversized field each refused before
  the provider is ever called and before anything is logged; a real email redacted in
  the payload a captured provider actually receives; content logging off by default
  persisting only real hashes; a real grant making both request and response text
  visible, and revoking it hiding new text again; grant duration bounds enforced for
  real; a real multi-match redaction count persisted; a request that never routes
  never logged; the full `enable`/`disable` HTTP surface and its own InfoSec-reviewer-
  alone role gate; the boundary test's own real self-granted window confirmed revoked
  again afterward. The full graph-svc suite re-ran clean (2606 passed, up from 2564,
  2 skipped — the pre-existing, disclosed, no-live-Anthropic-key gate — zero
  regressions beyond the two real, intended ones fixed above); an earlier full run
  surfaced one additional failure, `test_integration_cartographer.py`'s own
  background-task/pool-teardown race, the identical category ADR 0072 already
  disclosed for a different test — confirmed unrelated to this story by an isolated
  re-run passing clean, and absent entirely from the final, clean run reported here.
  `ruff`/`mypy`/`ontology_check.py`/`migration_check.py` all clean — no ontology
  change. `services/console-web` — 5 new tests (the honest off
  state, the enable control hidden for a non-InfoSec role, a real enable round trip
  showing the real duration/enabler, a real early-disable round trip, a real revoked
  window shown as off); the full suite re-ran clean (511 passed, up from 506, zero
  regressions); `tsc --noEmit`/`eslint`/`vite build` all clean.

## Alternatives considered

**Derive `TASK_CLASS_FIELD_SCHEMAS` from `GenerationRequest`/`RepairContext`'s own
declared dataclass fields via introspection**, so the schema could never drift out of
sync with the real request shape. Rejected — see decision 1: a schema that always
matches whatever a caller currently sends enforces nothing; the whole value of this
AC is a boundary the caller's own future change cannot silently widen without a
deliberate, reviewed edit to the gateway's own table.

**Redact only the copy that gets logged, leaving the real outbound request to the
provider untouched.** Rejected — see decision 2: the AC's own title is "so that a
prompt bug cannot leak data," a claim about the real call, not about the audit trail;
redacting only the log would leave the actual leak in place while merely hiding the
evidence of it.

**A single, fixed content-logging duration, no InfoSec-specified input.** Rejected by
the user's own explicit answer — a quick debugging session and a formal audit need
materially different windows; one fixed duration would force either a needless re-
enable for a longer legitimate need or an unnecessarily long window for a short one.

**Skip schema validation/redaction when no log store is configured**, treating them as
logging-adjacent rather than always-on. Rejected implicitly by the AC's own framing
("the gateway to enforce the boundary... so a prompt bug cannot leak data") and
confirmed by decision 5's own real regression: these are safety properties of every
real dispatch, independent of whether anything is being logged at all.
