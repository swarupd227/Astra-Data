# ADR 0037 — Regression checks real artefacts; there is no fabricated promotion pipeline

Status: accepted · 4 September 2026 · Story S5.2.2 (E5 / F5.2, completing it)

## Context

S5.2.2 closes F5.2, right after S5.2.1 shipped the rules engine itself: *"As a platform
engineer, I want to add or amend a rule through the Pattern Library with CI protection, so
that the rule set grows with the estate safely. New rule is authored as code (pattern +
template + guards + cases), reviewed as a pull request, and promoted to the tenant on merge.
Regression: every rule change re-runs the golden corpus and the PASSED artefacts that used
the rule; any new failure blocks promotion."*

Three questions decided the shape of the work: what "the PASSED artefacts that used the
rule" honestly means when this platform has no Arbiter to grant a pass verdict (the same gap
ADR 0036 already worked through for the rules engine itself); what "promoted to the tenant on
merge" means when this platform has one deployment per client environment, not a queue of
tenants to advance a change through; and whether "through the Pattern Library" in the
story's own scene-setting sentence means building a screen, when its own acceptance criteria
never asks for one.

## Decisions

### 1. "The golden corpus" and "the PASSED artefacts" are two different, already-distinguishable things

"Every rule change re-runs the golden corpus" is already true by construction and needed no
new code: `tests/test_rules.py` (S5.2.1) parametrizes over *every* rule's *every* golden case
on *every* CI run, regardless of which rule was touched — a shared-code change that quietly
breaks another rule's own case is already caught there today. "The PASSED artefacts that
used the rule" names something genuinely new: real `Measure` nodes a rule has actually
produced, in a real graph — not test fixtures. `check_regression()` is built for exactly
that distinction: it reads real `Measure`s (via their own `pattern_ref`, already written by
S5.2.1) rather than re-running synthetic cases a second time under a different name.

### 2. A regressed artefact (blocks) is not the same thing as a changed one (does not block)

A `Measure` a shipped rule can no longer render at all — the rule that produced it has been
retired, tightened, or renamed — is a regression: something that used to work now silently
would not, exactly the failure mode "any new failure blocks promotion" names. A `Measure`
that still renders, only with different text, is a legitimate outcome of a rule *improving*
— re-running the same golden-corpus discipline that already lets a rule's own expected
output change between versions (its golden cases are updated in the same PR). Conflating the
two would either block every deliberate improvement or (by ignoring text differences
entirely) miss real regressions; `RegressionReport` keeps them as separate, both-reported
buckets, and only `regressed` decides `.ok`.

### 3. "The tenant" is this codebase's own ordinary release path, not a fabricated promotion queue

This platform has no per-tenant deployment pipeline — one deployment is a client's own
dev/test/prod environment (§12.2's own three named workspaces), not a fleet of tenants a
change advances through one at a time. Building a bespoke "promotion" mechanism to satisfy
this story's own wording literally would be inventing infrastructure nothing else in this
codebase has or needs. Instead, `tools/rule_regression_check.py` follows the exact shape
`tools/migrate.py`/`ontology_check.py`/`migration_check.py`/`contract_check.py` already
established: a standalone, DB-connected script, wired into both `make
rule-regression-check` and a new CI step (`.github/workflows/ci.yml`, after "Migrate"), that
exits non-zero the moment it finds a real regression in whatever graph it is pointed at. On
this repository's own generic CI (a freshly migrated, empty-of-Measures graph, same footing
`nightly.yml`'s own replay-verification job already has), this is a smoke check with nothing
yet to regress — its real teeth are exercised the moment it runs against a client's own
accumulated graph before a new deployment actually reaches it, the honest reading of
"promoted to the tenant" this platform can support today.

### 4. No console screen — "through the Pattern Library" is the story's own scene-setting, not its acceptance criteria

§15.3.7-adjacent territory: a Pattern Library *screen* — listing patterns by class and
state, promote/retire/edit-guards/export actions — is S5.5.3's own explicit scope ("I want a
Pattern Library screen, so that I can see what the platform has learned and govern it"),
built for *promoted* patterns (CANDIDATE → ACTIVE → RETIRED, F5.5, not built). S5.2.2's own
acceptance criteria never names a screen; its two bullets are entirely about the authoring
and regression-checking mechanism. The regression report's real consumers are CI (automated)
and an operator running the check before a deploy — not an ongoing operational dashboard the
way class-mix/rule-coverage (S5.1.1/S5.2.1's own Programme Board panes) are. `GET /v1/
calculations:rule-regression` exists as a real, tested route (open to any Artizent role, the
same posture rule-coverage already has) without a console consumer, matching this codebase's
own precedent of shipping exactly the capability a story's own acceptance criteria names.

## Consequences

- `rules.py` gains `check_regression()` (reads every `Measure` with a `pattern_ref`,
  re-renders its source `CalculatedField` against the *current* rule set, classifies each as
  unchanged/changed/regressed), `RegressionReport`/`ChangedArtefact`/`RegressedArtefact`.
- New route: `GET /v1/calculations:rule-regression` (any Artizent role).
- New tool: `tools/rule_regression_check.py`, wired into `make rule-regression-check` and a
  new CI step in `.github/workflows/ci.yml`'s `graph-svc` job.
- Zero ontology changes — `check_regression` reads properties S5.2.1 already declared and
  writes nothing.
- No console changes — see decision 4.

## Alternatives considered

**Treat "PASSED artefacts" as a second copy of the golden corpus, re-run under a different
name.** Rejected — see decision 1. The acceptance criteria names two distinct things joined
by "and"; collapsing them would leave real, already-produced artefacts never checked at all.

**Block on any text difference, not just an outright rendering failure.** Rejected — see
decision 2. A rule's own golden cases are already allowed to change between versions (the
same PR that edits the rule updates its expected output); treating every text change as a
blocking failure would make legitimate improvement indistinguishable from breakage.

**Build a real multi-tenant promotion queue (a registry of tenants, a per-tenant "current
rules version," a gate that advances one at a time) to satisfy "promoted to the tenant"
literally.** Rejected — see decision 3. Nothing else in this codebase has or needs
multi-tenant deployment infrastructure; inventing it here would be scope far beyond what a
rules-engine safety story asks for, and the existing drift-guard shape already gives this
exact kind of check a real, precedented home.

**Build the Pattern Library screen now, since the story's own sentence names it.**
Rejected — see decision 4. The screen's own real scope (governing promoted patterns) is
S5.5.3's, not built, and belongs to patterns this platform does not yet produce (F5.5).
Building it early for S5.2.2 would be building ahead of the story that actually owns it.
