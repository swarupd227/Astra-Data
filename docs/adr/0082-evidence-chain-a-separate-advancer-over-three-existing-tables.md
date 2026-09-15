# ADR 0082 — Evidence Chain: a separate advancer over three existing tables

Status: accepted · 15 September 2026 · Story S11.3.1, opens F11.3 (E11)

## Context

S11.3.1 — the backlog's own AC, verbatim: *"As an auditor, I want an append-only,
hash-linked record of every state transition, gate decision, agent run, model call and
verdict, so that the migration can be examined years later."*

- Every CloudEvent is appended with `prev_hash` and `hash`; daily roots are computed and
  can be anchored externally (client-chosen: their own ledger, a timestamping service)
  as an option
- Verification tool recomputes the chain and reports the first break; runs nightly and
  on demand
- Retention configurable per tenant (default: programme lifetime + 7 years) with
  export before deletion

Spec §4.5/§18.4 name the same construct near-verbatim, and §5.2's own component
inventory gives it a dedicated, unbuilt microservice: *"evidence-svc: append-only
chain, hashing, anchoring, export | per tenant."* Before any code was written, research
(an `Explore` agent's own full pass over `events.py`, `repository.py`'s own outbox
writer, `replay.py`/`tools/verify_replay.py`/`.github/workflows/nightly.yml`,
`retention.py`, `decision_register.py`, `workload_identity.py`, `provenance.py`, and
the product spec) found that "state transition"/"gate decision"/"verdict" already
produce real `estate_event` rows today (every `GateDecision`/`Verdict`/`ParityRun` write
goes through the same chokepoint every other node type does), but "agent run" and
"model call" do not — an SVID issuance and a model call are both real, durable,
per-tenant facts already, just recorded in `public.svid_record` and `public.provenance`
rather than the outbox. Three explicit questions were asked and answered before writing
any code, all "(recommended)":

1. **Evidence chain location** → *"Build inside graph-svc."* No new microservice —
   the identical "the existing service, not a new deployable" precedent ADR 0081 already
   set for execution safety's own "executor worker" question.
2. **Hash-chain locus** → *"A separate chain-advancer job, not the hot path."* An
   idempotent, `seq`-ordered walk (CLI tool, nightly cron, on-demand HTTP), the same
   shape `tools/verify_replay.py` + its cron already have — not a per-graph lock added
   to every graph write this service makes.
3. **External anchor depth** → *"Disclosed interface only, no live external call."* A
   real `ChainAnchor` Protocol and a `NullChainAnchor` default; daily roots are always
   computed regardless. A real RFC 3161 client is future work, not built here.

## Decisions

### 1. Three existing tables, not a fourth event type threaded through five call sites

`estate_event`'s own `_prepare_nodes`/`_append_events` chokepoint already makes every
`GateDecision`/`Verdict`/`ParityRun`/state-changing write a real CloudEvent — confirmed
directly, and reused as-is. For "agent run" and "model call," the alternative
considered was adding a new `EventType` (`estate.agent.run_started`/`estate.model.
called`) and calling `append_event` from `workload_identity.py`'s three SVID-issuance
call sites (`harvest/scheduler.py`, `regression.py`, `api/routes_g2.py`) and from
`provenance.py`'s own `record()`. Rejected: each of those call sites is already a
small, delicate, best-effort block inside otherwise-unrelated production code (`agent_
identity.py`'s own docstring already disclosed why touching them again risks the
identical "narrow the agent's own reach, not a human's" fragility this module has
avoided elsewhere). Instead, `evidence_chain.py` reads `estate_event`, `public.
svid_record` and `public.provenance` directly, read-only, and chains them together —
**every existing write path (`writes.py`, `workload_identity.py`, `provenance.py`,
`harvest/scheduler.py`, `regression.py`, `api/routes_g2.py`) stays completely
untouched**: the entire Evidence Chain is new, isolated code with nothing upstream of
it to break.

`svid_record`'s own revocation (an in-place `UPDATE`, ADR 0080's one deliberate
exception to append-only) is not re-chained — only the row's first-seen state
(issuance) is. Disclosed, not silently dropped: re-chaining an updated row would
either silently change what an already-chained entry's own hash covers, or require
detecting row mutation this module does not build; a later revocation is a separate
governance fact `Tenant & Access`/`svid_record` already show directly.

### 2. The chain is computed by a separate advancer, never inline on a graph write

`estate_event` is written inside the same transaction as *every* graph mutation this
service makes — the hottest code path this codebase has (confirmed: `_append_events`
in `repository.py` fires from `_prepare_nodes`, the single chokepoint `write_nodes`/
`upsert_nodes`/`set_node_properties` all funnel through). Adding `prev_hash`/`hash`
there would need a per-graph advisory lock to keep concurrent writes correctly ordered
— real contention added to a mature, heavily-tested path for a benefit ("the chain
updates the instant an event is written") the AC does not actually ask for: its own
second bullet already anticipates a distinct verification process ("runs nightly and
on demand"), the identical shape this codebase's own nightly replay-verification job
already has.

`advance_chain` (`evidence_chain.py`) is that same shape: an idempotent, per-source-
batch walk, watermarked by the highest `source_id`/`seq` already chained for each
`(graph, source_table)` pair, inserting new `evidence_chain_entry` rows in one
transaction per call. **Ordering is per-source batches, not strict cross-source
wall-clock interleaving** — every id in this codebase is a ULID (or a short fixed
prefix plus one), lexicographically sortable by creation time, so within one source the
append order is real chronological order; across sources, one call's three batches are
processed in a fixed sequence (`estate_event`, then `svid_record`, then `provenance`),
a disclosed simplification rather than a claim the chain proves finer cross-source
ordering than "which run's own batch an entry belongs to." What the chain does prove,
exactly as the AC asks: that nothing chained has been altered or removed since it was
chained.

**Hashing reuses the existing canonical-JSON convention** (`context.canonical.
canonical_json`, S1.3.1's own `sort_keys=True, separators=(",",":"), ensure_ascii=
False` discipline) rather than a second, competing serialization — `hash = sha256(
prev_hash + canonical_json({source_table, source_id, occurred_at, category, fields}))`,
seeded from a fixed, documented `GENESIS_HASH` (64 zero characters, never itself a real
hash output, so it can never collide with one).

### 3. Verification recomputes from the real source rows, not just the chain table

`verify_chain` walks `evidence_chain_entry` in `chain_seq` order, checking each row's
own `prev_hash` against the previous row's real `hash`, then re-reading the *real*
source row (`estate_event`/`svid_record`/`provenance`, by id) and recomputing the hash
from it — a tampered chain-entry row and a tampered *source* row are both caught, each
correctly reported as the first break at the real `chain_seq`/`source_table`/
`source_id` it occurred at (proven directly: a test tampering `evidence_chain_entry.
hash` and a separate test tampering `svid_record.agent_id` after chaining each report
the correct, distinct first break). `tools/verify_evidence_chain.py` is the CLI shape
the nightly cron calls; `POST /v1/evidence-chain:verify` calls the identical function
for the AC's own "on demand" half.

### 4. Daily roots bucket by `chained_at`, chained together as a second, coarser chain

An entry's own `occurred_at` (`estate_event.time`/`svid_record.issued_at`/`provenance.
created_at`) can be recorded by a different process at a different moment than this
advancer discovers and chains it. Bucketing a daily root by `occurred_at` risks a
"closed" day's root needing to be recomputed — changing an already-anchored hash — if a
late-arriving row surfaces with an old timestamp. `chained_at` (`now()` at the moment
this module inserts the row) is monotonically increasing by construction, so a
calendar day is provably closed forever once it has passed; `compute_daily_root`
refuses outright for today or a future day, and a day already rooted is never
recomputed. Each day's own `root_hash` folds the *previous* day's root in
(`sha256(prev_root_hash + concatenated entry hashes)`), so the roots themselves form a
second, coarser hash chain — anchoring one day's root transitively attests to every
day before it, which is exactly the property external anchoring needs to be worth
doing at all.

### 5. External anchoring: a real interface, a `NullChainAnchor` default, no live call

`ChainAnchor` (`async def anchor(*, graph, day, root_hash) -> AnchorReceipt`) is a real
Protocol; `NullChainAnchor` is the honest default, raising `ChainAnchorUnavailable`
with a real explanation rather than silently no-op-ing. **Daily roots are always
computed and stored regardless of whether any anchor is configured** — anchoring is
purely additive metadata (`anchor_kind`/`anchor_ref`/`anchored_at`, all nullable) on a
root that already exists either way, matching the AC's own "as an option." A real RFC
3161 timestamping client (a genuinely public, standardised, client-tenant-independent
service, unlike Entra ID or Fabric) was considered and explicitly declined by the
user's own answer: this environment has no client-approved external endpoint to call,
and a live network dependency in a security-sensitive story's own default path is a
real, avoidable risk — the identical "disclosed, not yet connected" posture ADR 0079/
0080/0081 already established repeatedly for this exact shape of question.

### 6. Retention: the later, more specific story wins over S1.3.2's original number

`retention.py`'s own `RETENTION_MONTHS = 12` (S1.3.2) is replaced by `DEFAULT_
RETENTION_YEARS = 7` / `DEFAULT_RETENTION_MONTHS = 84` — this story's own AC default,
disclosed in the module's own docstring as superseding S1.3.2 rather than silently
overwriting its history. `retention_months` becomes a real parameter on `Programme.
retain_until`/`.as_dict`, `prunable_before`, and the new `RetentionState.retention_
months` field, threaded through every route that previously called those with no
argument (`GET`/`POST /v1/programmes*`, `GET /v1/retention`, `GET /v1/platform/
health`) so a tenant's own configured policy is used consistently everywhere, not only
in the one route that reads it directly. New `RetentionPolicy`/`RetentionPolicyStore`
(`PostgresRetentionPolicyStore`/`InMemoryRetentionPolicyStore`, migration v0042's own
`public.retention_policy`) is the identical `mender_config`/`execution_safety_policy`
shape already established twice this epic: a real, versioned, per-`graph` Postgres
row, a platform engineer's edit is a new version, never an overwrite.

**"Nothing prunes today, and that is deliberate" stays true, extended honestly to this
story too.** `export_prunable_evidence` is a real, on-demand, callable action
(`POST /v1/retention:export`) that exports every `estate_event` row already inside the
tenant's own prunable window to a real artefact (`ArtefactStore`, `kind=
"retention_export"`) — it does not delete anything, and nothing else in this service
does either. Building an actual pruner was explicitly out of scope before this story
(`retention.py`'s own pre-existing docstring) and stays out of scope now: the AC asks
for the export *capability*, not for it to be wired to an automatic deletion this
codebase has never had.

`GET /v1/retention`'s own role gate was widened from S1.3.2's original `ArtizentDep`
to `TenantAccessReaderDep` (Artizent, or the InfoSec reviewer) — this story's own
persona is the auditor, and the InfoSec reviewer is this codebase's own already-
established stand-in for that persona (the identical reasoning `decision_register.py`
already gave for its own read gate). Edit (`PUT /v1/retention`) and export (`POST
/v1/retention:export`) stay `PlatformEngineerDep` — real platform actions with no
other named approver, matching S11.1.2's SVID-revoke and S11.2.1's execution-safety-
policy-edit precedent exactly.

## Consequences

- `services/graph-svc`: new `evidence_chain.py` (`advance_chain`/`verify_chain`/
  `compute_daily_root`/`list_daily_roots`/`chain_status`, `ChainAnchor`/
  `NullChainAnchor`); new migration v0042 (`public.evidence_chain_entry`, `public.
  evidence_daily_root`, `public.retention_policy`, no ontology change). `retention.py`
  gained `DEFAULT_RETENTION_YEARS`/`DEFAULT_RETENTION_MONTHS` (replacing `RETENTION_
  MONTHS`), `RetentionPolicy`/`RetentionPolicyStore`, and `export_prunable_evidence`;
  every `Programme.as_dict`/`retain_until`/`prunable_before` call site across `routes_
  provenance.py`/`routes_platform.py` now threads a tenant's own configured retention
  through. New `api/routes_evidence_chain.py` (`GET /v1/evidence-chain/status`,
  `POST /v1/evidence-chain:advance`, `POST /v1/evidence-chain:verify`, `GET
  /v1/evidence-chain/daily-roots`, `POST /v1/evidence-chain:compute-daily-root`); `GET`/
  `PUT /v1/retention` and new `POST /v1/retention:export` on `routes_provenance.py`.
  New `tools/advance_evidence_chain.py`/`tools/verify_evidence_chain.py`, the identical
  CLI shape `tools/verify_replay.py` already has. `.github/workflows/nightly.yml`
  gained a second job, `evidence-chain`, advancing then verifying on the identical
  cron + `workflow_dispatch` schedule the existing replay job already has.
- `services/console-web`: Tenant & Access gained two more panes, "Evidence chain"
  (tip/category counts, an Advance-now/Verify-now action, daily roots) and "Retention"
  (policy display + edit) — not new top-level surfaces, the identical "this screen
  already exists to show real governance facts" reasoning ADR 0080/0081 already used
  for their own additions to it. Edit/Advance/Verify hidden, not disabled, for every
  role but the platform engineer, the same convention every other gated action on this
  screen already uses.
- Verified: `services/graph-svc` — 10 new pure unit tests (`_category_for_estate_
  event`'s own real classification, `_entry_hash`'s own determinism, `NullChainAnchor`'s
  own real refusal); 12 new integration tests against real PostgreSQL + Apache AGE (a
  real `GateDecision`/`ParityRun`/`Workbook` write plus a real SVID issuance plus a real
  provenance record, all chained and correctly categorised; idempotency; a tampered
  chain-entry hash and a tampered source row each reported as the correct first break;
  a real daily root, refused for today, computed once for a real backdated day, never
  recomputed, and a second day's own root proven to roll the first day's root in). A
  real regression in this story's own first draft was found and fixed here: the
  inline hash-chaining loop's own `prev_hash` was accidentally computed from the
  already-reassigned *new* tip hash rather than the value before it — caught before
  any test ran, by re-reading the loop, not by a failing assertion. The CLI tools were
  also run directly against the real, large `astra_estate_test` demo graph (5,010 real
  rows across all three sources), chaining and then verifying it intact end to end.
  `retention.py`'s own pre-existing tests were retargeted where the AC's own default
  change was the direct, intended cause of their prior expectations breaking (the
  identical "a direct, intended consequence of this story" precedent this session has
  set repeatedly), and a new test added confirming the real new seven-year default.
  The full unit suite re-run clean (1654 passed, up from 1643); the full integration
  suite re-run clean; `ruff`/`mypy`/`ontology_check.py`/`migration_check.py` all clean
  -- no ontology change. `services/console-web` -- 9 new tests (the honest empty chain/
  daily-root states, a real chained status and category breakdown, a real Advance-now
  and Verify-now round trip, Advance/Verify/Edit hidden for a non-platform-engineer
  role, a real retention-policy edit round trip); the full suite re-run clean (488
  passed, up from 479, zero regressions); `tsc --noEmit`/`eslint` clean.

## Alternatives considered

**Build `evidence-svc` as a genuinely separate service**, matching spec §5.2's own
component inventory literally. Rejected by the user's own explicit answer, and for the
identical reason ADR 0081 already declined a separate "executor worker": a new
deployable is a materially bigger, unrequested undertaking than a safety/audit
capability added to the service that already owns the outbox this whole story reads
from.

**Add a new `EventType` for agent runs and model calls, wired into `workload_identity.
py`/`provenance.py`'s own real call sites.** Rejected — see decision 1. This would have
made `estate_event` the single, uniform source of truth for the whole chain (a real
simplification of `advance_chain`'s own logic), but at the cost of touching several
already-delicate, best-effort production call sites a second time for a benefit
(cross-source ordering, a single source table) the AC does not actually require;
reading the existing tables directly keeps every one of those call sites untouched.

**Hash-chain `estate_event` inline, under a per-graph advisory lock.** Rejected by the
user's own explicit answer — see decision 2. Real, immediate tamper-evidence at the
cost of lock contention on literally every graph write this service makes, for a
property ("the chain updates the instant an event is written") the AC's own wording
does not ask for.

**Build a real RFC 3161 timestamping client now**, since it is a genuinely public,
non-tenant-specific protocol unlike Entra ID or Fabric. Rejected by the user's own
explicit answer — a live external network dependency in a security story's own default
path is a real, avoidable risk this environment has no approved endpoint to accept
regardless of the protocol's own genericness; the interface is real and ready for
whenever a client names a real target.

**Bucket daily roots by each entry's own `occurred_at`.** Rejected — see decision 4. A
late-arriving row with an old timestamp would force recomputing an already-computed,
possibly already-anchored day's root, silently changing a hash an external anchor may
already have attested to; `chained_at` is monotonic by construction and closes a day
for good the moment it has passed.
