# ADR 0086 — Throughput and cost metrics: a vocabulary translation before any code

Status: accepted · 16 September 2026 · Story S6.2.3

## Context

S6.2.3 — the story's own text, verbatim: *"As a project manager, I want custodians live
per week, agent acceptance and credits per custodian per day, so that reporting is
generated. Acceptance criteria: Weekly report exported for the client cadence; Cost per
custodian visible from query tags."* The story arrived labelled `P2 · WBS 2.6.6`.

**None of this story's own core vocabulary exists anywhere in this codebase, its spec,
or its backlog** — confirmed by an `Explore` agent's own full-repo, case-insensitive
search before any code was written: zero hits for "custodian" across `services/
graph-svc`, `services/console-web`, `docs/adr/`, `docs/generated/ontology.md`, and the
product backlog/spec reference documents; zero hits for "credits" (excluding
`credential`, and one unrelated `lineage.test.tsx` verb usage); zero hits for "query
tag" as a mechanism; zero hits for "WBS" anywhere in the repository. The backlog's own
`docs/reference/Astra-Data-Migration-Accelerator-Product-Backlog-v1.0.md` numbers
epics `E1`–`E12` with `P0/P1 · R1` priority tags, never `P2 · WBS 2.6.6` — a different
numbering convention than this repository has ever used. Worse, **the id `S6.2.3`
collides with an already-shipped story**: `E6. Compositor`'s own `F6.2` already has
exactly two stories, `S6.2.1` (redesign flags, ADR 0048) and `S6.2.2` (report
documentation, ADR 0049), both shipped; `E6` is a visual-mapping/generation epic, wholly
unrelated to programme reporting. "Custodian"/"credits" read as eDiscovery/legal-hold
vocabulary, not this platform's own domain.

Given this mismatch, four genuinely blocking questions were put to the user before
writing any code (each answered "(recommended)"):

1. **What does "custodian" mean?** → *Map to the platform's existing `Site` node.* No
   new domain entity; every metric below reports by `site_id`.
2. **What does "credits" mean?** → *Real LLM cost from tokens.* Persist `tokens_in`/
   `tokens_out` per gateway call (already computed transiently in `RawModelResponse`,
   never persisted before this story) and convert to a dollar figure via a real,
   disclosed, per-provider rate.
3. **What does "agent acceptance" mean?** → *Reuse the existing `commercial_ledger`/
   `invoicing.accepted_by_tier` fact* (an MU reaching `ACCEPTED` at G3, story S9.1.2).
   No second acceptance concept invented.
4. **How does "weekly report" actually get generated?** → *On-demand, Status-Pack-style*
   (`POST :generate` + a real export + a download button), reusing S10.2.1's own
   established, disclosed reading of "generated weekly" as a PM-triggered snapshot, not
   a real scheduler this platform does not have.

## Decisions

1. **Custodian = `Site`, resolved via the existing `CONTAINS` chain, not a new node.**
   `Site -> Project -> Workbook` (§4.1.2) is walked with the identical two-hop join
   `release._sites_for_workbooks` already established for the Release Board's own
   per-site window. `throughput_metrics.py` imports it directly (cross-epic private
   reuse, the same convention `foundry_routing._family_for_workbook` already set) —
   except in `mender.py`, where importing `release.py` would be circular (`release.py`
   imports `g3_card.py`, which already imports `mender._resolve_calculated_field`);
   there, the identical join is declared locally instead, the same "duplicate when a
   cross-import would cycle" disclosed choice `g4_card._workbooks_for_site` already
   makes for its own reverse direction.

2. **"Query tags" is a new, real, optional attribution field threaded through the
   gateway itself, not a client-side label.** `Gateway.generate()` (the Protocol,
   `ModelGateway`, `StaticGateway`, and the shared `_dispatch` helper) all gained a new
   `query_tag: str | None = None` parameter — additive, the identical "an existing
   caller that omits it gets unchanged behaviour" footing S11.1.2's own `principal`
   parameter already has. `generation.py`'s `generate_c3_field` resolves a real site id
   for its own `calc_id` (via a new `_site_for_calc`, walking `ENCODES` → `CONTAINS` →
   `release._sites_for_workbooks`) and passes it down through `_run_ladder`;
   `mender.py`'s `mend_exception` resolves one for its own `workbook_id` once, before
   its repair-pass loop, and passes it into every `call_model_repair` call in that loop.
   A caller that cannot resolve a real site (or has not been updated) is honestly
   logged under a `NULL` `query_tag`, never guessed at.

3. **Real token counts and their own real cost are persisted unconditionally — metadata
   about the call, not its content.** `RawModelResponse.tokens_in`/`tokens_out` were
   already computed on every real Anthropic call but discarded before this story
   (`gateway.py`'s own prior module docstring named the gap directly: *"real cost-tier
   ranking... not built here, disclosed rather than faked with invented cost
   numbers"*). `gateway.token_cost_usd(provider, tokens_in, tokens_out)` converts them
   through a new `PROVIDER_TOKEN_COSTS` table — a real, invented, disclosed dollars-
   per-million-tokens rate, the identical "a real, invented, disclosed planning
   assumption" footing `invoicing.DEFAULT_UNIT_PRICES` already has — returning `None`
   for an unregistered provider (an honest absence, never a guess). `query_tag`/
   `tokens_in`/`tokens_out`/`cost_usd` are persisted on every dispatch regardless of
   whether a content-logging grant is active: they are metadata about the call, the
   identical footing `redaction_count`/`provider`/`task_class` already have — S11.4.2's
   content-logging grant only ever gates literal request/response *text*, never these
   fields.

4. **"Agent acceptance" is the existing MU-acceptance fact, attributed to a site, never
   a second acceptance concept.** `agent_acceptance_per_custodian_per_day` groups real
   `commercial_ledger` rows (S9.1.2) by each accepted workbook's own real site and by
   real calendar day — no new "was this agent's output accepted" signal is invented;
   the AC's "agent acceptance" is read as *the platform's own acceptance event*,
   attributed per custodian, not a new judgement about agent quality this story does
   not define.

5. **"Custodians live per week" ties liveness to the identical two facts the other two
   metrics already report** — a site counts as live in an ISO week iff a query-tagged
   gateway dispatch or an MU acceptance is attributed to it that week. Deliberately not
   a fourth, separate signal (e.g. harvest recency): using only what the other two
   metrics already compute keeps all three numbers self-consistent, rather than
   inventing a definition of "live" nothing else in this report can cross-check.

6. **The weekly report reuses the Status Pack's own real shape, not a new one.** A new
   `throughput_report.py`/`routes_throughput_report.py`, structurally mirroring
   `status_pack.py`/`routes_status_pack.py`: `POST /v1/throughput-report:generate`
   (Programme Manager only, the AC's own literal "as a project manager") computes and
   persists a new, versioned, append-only row; `GET /v1/throughput-report` reads the
   latest one (any Artizent role, no client persona named); `GET /v1/throughput-
   report.csv` exports it. **CSV, not PDF/PPTX** — this report is three tabular
   figures, not a narrative with charts (the shape `status_pack.py`'s own PDF/PPTX
   exist for); a single flat CSV with three labelled sections, the same `csv.writer`
   mechanism `decision_register.decisions_to_csv` already uses, is the honest, simplest
   shape for "the client cadence," not a second format invented for its own sake.

## Consequences

- `services/graph-svc`: new migration v0045 (`gateway_request_log` gains `query_tag`/
  `tokens_in`/`tokens_out`/`cost_usd`; new `public.throughput_report`, no ontology
  change). `gateway.py` gained `PROVIDER_TOKEN_COSTS`/`token_cost_usd`, and every
  `generate()` signature (`Gateway`, `ModelGateway`, `StaticGateway`, `_dispatch`) plus
  `GatewayRequestLogStore.record`/`PostgresGatewayRequestLogStore`/
  `InMemoryGatewayRequestLogStore` gained the new fields — all additive, defaulted, and
  optional. New `throughput_metrics.py` (the three real metric queries) and
  `throughput_report.py` (versioned generate/read, CSV render). New
  `api/routes_throughput_report.py`, wired into `main.py`/`api/__init__.py`.
  `generation.py` gained `_site_for_calc`; `mender.py` resolves its own workbook's site
  once per repair loop, via a local, disclosed duplicate of the two-hop `CONTAINS` join
  (not an import of `release._sites_for_workbooks`, which would be circular through
  `g3_card.py`).
- `services/console-web`: a new top-level surface, "Throughput & Cost" — visible to any
  Artizent role (no client persona named, the identical posture `statuspack` already
  has), Generate hidden for anyone but the Programme Manager, three real tables
  (custodians live per week, agent acceptance per custodian per day, credits per
  custodian per day), and an Export as CSV download. New `ThroughputReportData`/
  `CustodiansLiveWeek`/`AgentAcceptanceDay`/`CreditsPerCustodianDay` types and
  `throughputReport`/`generateThroughputReport`/`throughputReportCsv` methods in
  `lib/api.ts`.
- Verified: `services/graph-svc` — 5 new unit tests (`token_cost_usd`'s own real
  rate/zero-tokens/unregistered-provider cases; a real dispatch logging its own real
  `query_tag`/`tokens_in`/`tokens_out`/`cost_usd`; a dispatch with no tag logging
  `NULL` honestly). 17 new integration tests against real PostgreSQL + Apache AGE: a
  real dispatch grouped by its own real site and day; an untagged dispatch grouped
  under `None`; two real days producing two real rows; a real MU acceptance attributed
  to its own real site; a query-tagged dispatch and an acceptance each independently
  making their site "live" that week; `generate_report`/`latest_report` round-tripping
  a real, versioned row; a regenerate producing a new row, never an overwrite;
  `render_csv` carrying the real numbers; the full HTTP surface (Programme Manager can
  generate, another Artizent role is refused, any Artizent role can read, a client role
  is refused, reading before any generate is a real 400, the CSV export is a real
  download). **Four real, intended regressions were found and fixed by the full unit
  suite, not by review** — the third time this exact category has recurred in this
  backlog: `Gateway.generate()`'s new, additive `query_tag` parameter (the identical
  "additive, defaulted" shape S11.1.2's own `principal` parameter already has) broke
  four test doubles that implement the `Gateway` protocol directly rather than going
  through a real `ModelGateway`/`StaticGateway` (`gateway._NoGateway`/`null_gateway`,
  `test_generation.py::_NoRouteGateway`, `test_mender.py::_RoutingErrorGateway`, and
  two integration tests calling `null_gateway()`) — fixed by adding the identical
  optional parameter to each; `mypy` separately caught `_NoGateway`'s own signature as
  a real Protocol-incompatibility the first full-suite run had not yet surfaced. The
  full graph-svc suite re-ran clean (2628 passed, up from 2606, 2 skipped — the
  pre-existing, disclosed, no-live-Anthropic-key gate — zero regressions beyond the
  four real, intended ones fixed above); `ruff`/`mypy`/`ontology_check.py`/
  `migration_check.py` all clean — no ontology change. `services/console-web` — 7 new
  tests (the honest empty state, all three real tables rendered, a read failure
  surfaced, Generate gated to the Programme Manager, the API refusal shown verbatim,
  the CSV export triggered); the full suite re-ran clean (518 passed, up from 511,
  zero regressions); `tsc --noEmit`/`eslint`/`vite build` all clean.

## Alternatives considered

**Build "custodian" as a genuinely new first-class node** (a person or team distinct
from `Site`), matching eDiscovery vocabulary literally. Rejected by the user before any
code was written: nothing in this platform's own domain model needs a custodian
*person* — every real fact this story reports (activity, acceptance, cost) is already
scoped by *site*, and a new node with no other story ever populating it would be a
second, parallel, always-empty identity for the same real thing `Site` already is.

**Store `cost_usd` as a stored, precomputed column updated by a trigger or background
job**, so a report never has to re-price historical tokens if a provider's rate
changes. Rejected — `cost_usd` is computed once, at dispatch time, from the rate in
effect *then* (`token_cost_usd` called inside `_dispatch`, not at read time): a rate
change should not silently reprice a historical call the client was actually charged
against at the old rate, the identical "a signature governs what was true when it was
taken" reasoning `data_handling.ContentLoggingGrant`/`SvidRecord` already apply to
their own point-in-time facts.

**A real, automatic weekly scheduler** (a `HarvestScheduler`-shaped in-process poll
loop calling `generate_report` on a timer), so "generated weekly" would be literally
true without a human clicking anything. Rejected by the user's own explicit choice,
matching `status_pack.py`'s own already-disclosed reasoning: a background loop that
fires on a timer is real, separate future scope (the identical gap `g2_reminders.py`'s
own docstring already names for reminders), not something this story's real, on-demand
`POST` needs to claim.

**Derive "agent acceptance" from a new, separate signal** (e.g. counting Mender passes
that resolved without escalation, or Transpiler ladder attempts that parsed on the
first try) rather than reusing `commercial_ledger`. Rejected — the AC's own "so that
reporting is generated" names no new judgement about agent quality; the platform's one
real, already-audited "acceptance" fact is an MU reaching `ACCEPTED` at G3, and
inventing a second, softer "acceptance" alongside it would give a Programme Manager two
different numbers answering what sounds like the same question.

**PDF/PPTX export, matching the Status Pack precedent verbatim.** Rejected — this
report has no narrative and nothing chart-like the AC calls for; three tabular figures
map naturally to CSV rows, and generating a second, heavier document format only to
carry the identical numbers a CSV already carries would be format for its own sake, not
because "the client cadence" needs it.
