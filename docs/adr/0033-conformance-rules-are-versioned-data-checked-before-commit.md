# ADR 0033 — Conformance rules are versioned data, checked before commit

Status: accepted · 4 September 2026 · Story S4.3.2 (E4 / F4.3)

## Context

S4.3.2 asks for the second half of Build: *"As an architect, I want conformance rules
enforced at emission, so that no model reaches the client repository that breaks the
target architecture. Rules from §12.3: star schema only (no many-to-many without a
bridge), single active relationship path, conformed dimensions shared by reference,
measures in display folders by source family, naming convention, RLS roles tested with a
fixture user. A rule failure blocks BUILT and lists the violation with the offending
object. Rules are data, editable by the architect in Admin, versioned, and recorded on the
ModelFamily at build."*

Five questions decided the shape of the work: how the backlog's own six rules diverge from
§12.3's own six; how each rule can be checked structurally against a design document that
carries no column data and no per-measure table association; where "rules are data" should
live and what "versioned" commits this platform to; where in the build pipeline a
violation must actually block; and what "Admin," a screen no other backlog story claims,
should look like.

## Decisions

### 1. The backlog's rule set substitutes "naming convention" for §12.3's column-description check — deliberately, following an established precedent

§12.3 names six checks; one is "every column with a description (drafted, ASSISTED)." No
`Field`/column data has ever been threaded into `design_document` (`tmdl.py`'s own
disclosed gap, S4.3.1) — the input that rule needs does not exist yet. The backlog's own
acceptance criteria drops it and adds "naming convention" instead, over data
(tables/measures/roles' own names) this platform already has. This is the identical "spec
wins unless the backlog answers with real data the spec's own version cannot reach"
precedent this codebase has followed since S4.2.x, applied in the direction it was written
for: backlog wins here because it names a checkable input, not because it is newer.

### 2. Each rule is a pure, disclosed heuristic over the frozen design document — the same discipline `tmdl.emit_tmdl` already established

- **Star schema**: an unresolved (`None`) cardinality is treated as an unconfirmed
  many-to-many with no bridge — this platform has no primary-key metadata to call an
  ambiguous join anything else (`modeller.infer_cardinality`'s own reasoning, reused).
- **Single active relationship path**: a union-find cycle check over the *undirected*
  graph of `from_table`/`to_table` edges. A star or snowflake is a tree and has none; a
  redundant join back to an already-reachable table does — the same shape Power BI's own
  "only one relationship may be active" rule polices.
- **Conformed dimensions by reference**: a dimension `conformed_dimensions` already names as
  shared with other families, mapped by name to its `ModelTable`, fails if that table's
  `mode` is `import` — a physical copy directly contradicts "by reference," checkable
  without this platform building the real cross-family reference mechanism first.
- **Measures in display folders by source family**: assigning the folder itself
  (`tmdl._measures_file`, `displayFolder` set to `document["family_name"]`) is
  unconditional and so can never fail; what the requirement actually guards against — two
  measures colliding by name inside that one folder — is what this rule checks.
- **Naming convention**: no spec wording exists to anchor this one (see decision 1) — a
  disclosed, minimal definition: non-blank, no leading/trailing whitespace, under a
  configurable length, does not start with a digit, carries no unescaped double quote.
- **RLS fixture user**: no live engine exists to evaluate a filter expression for real, and
  the expression is still Tableau syntax, not DAX (the Transpiler, E5, not built) — "tested"
  means the expression names a field and calls a recognised user-context function
  (`USERNAME`/`ISMEMBEROF`/`FULLNAME`), the same honesty
  `FixtureTargetAdapter.smoke_query` (S4.3.1) already applies to a check nothing here can
  run for real.

### 3. Rules are data — one versioned platform table, the same "history, not a mutable row" footing `g2_question`/`g2_reminder`/`build_run` already established

`public.conformance_ruleset` (migration v0019) holds one row per saved version; an
architect's edit is always a new version, never an overwrite, so a build recorded against
version 3 stays checkable against exactly what version 3 said even after version 4 ships.
What is genuinely *data* is which of the six rules are enabled and their parameters
(`naming_convention.max_length`, `rls_fixture_user.fixture_username`) — the rule
implementations themselves (`RULES`, the Python callables) and their descriptions
(`RULE_METADATA`, for Admin to render without guessing) stay code, the same "declare the
mechanism, let an operator tune the knobs" split `harvest.PromotionGate` already draws
between a fixed gate shape and an administered promotion record. A fresh graph builds
against an in-memory default (version 0, never persisted) so a tenant is never blocked from
building before an architect has visited Admin.

### 4. Conformance runs before `commit`, and is stamped on `ModelFamily` every attempt, pass or fail

"No model reaches the client repository that breaks the target architecture" is an ordering
requirement, not just a state-machine one: a violation must block the Git write itself, so
`build_family` runs `check_conformance` immediately after `emit` and before `commit` —
nothing that fails ever reaches the target adapter. `ModelFamily.conformance_ruleset_version`
(additive, no migration entry — schema version 14 → 15) is written on *every* attempt inside
the same `finish()` closure that records the `BuildRecord`, whether the build succeeds or
not; only a `SUCCEEDED` build additionally advances `state` to `BUILT`. Violations
themselves are not duplicated onto the family — they already have a home
(`build_run.steps`, a single `"conformance"` step whose `detail` lists every violation with
its offending object, `"; ".join(str(v) for v in violations)`) — the family carries only the
one fact that outlives any single build: which ruleset version it was last measured
against.

### 5. Admin is a new, single-purpose screen this story adds, not a sixth story waiting on someone else's design

§2.4 gives the Migration Architect "Admin" among their own surfaces; §15.3.7 names five
Admin screens (Platform Health, Pattern Library, Model Gateway & TokenOps, Data Handling,
Tenant & Access) and none of them is this one, and no other backlog story (F10.4's own text
is explicit about which screens it owns) claims a rules-editing screen either. Rather than
invent scope for a screen nobody has specified, this story ships the minimum: one table,
one row per rule, an enable checkbox and whatever parameters that rule has, a version
number and who/when it was last saved, and a Save button gated to
`migration_architect` — hidden, not disabled, for anyone else, the same convention every
other role-gated console action already follows.

## Consequences

- New modules: `conformance_rules.py` (six rule functions, `RuleConfig`/`ConformanceRuleset`,
  `ConformanceRulesetStore`/`PostgresConformanceRulesetStore`), a new platform table
  (`public.conformance_ruleset`, migration v0019), a new `ModelFamily` property
  (`conformance_ruleset_version`, additive, schema version 15).
- `build_family` gains a `conformance` step between `emit` and `commit`; a violation is a
  `FAILED` build, the same recorded-not-raised outcome every other step failure already is.
- New routes: `GET`/`POST /v1/conformance/rules` (`ArtizentDep` read, `MigrationArchitectDep`
  write) and a new console surface, `/admin`, plus `migration_architect` as a selectable
  role — the first story to actually drive a role declared since S1.1.1 and gated nowhere
  until now.
- `tmdl.py`'s measures gain a `displayFolder` property, and `cartographer._family_summary`
  gains the new field — a real, pre-existing gap (`g2_cycle_count`, added in S4.2.1, was
  never added to this same function) was found in passing and flagged separately rather
  than folded into this story's own diff.

## Alternatives considered

**Implement §12.3's column-description rule and drop naming convention, following "spec
wins" literally.** Rejected — see decision 1. The precedent's own carve-out (backlog wins
when it answers with real data the spec's version cannot reach) applies exactly here;
following "spec wins" mechanically would mean shipping a rule against data that does not
exist.

**A generic, admin-authored expression language for rules, instead of six fixed
functions with data-driven enable/parameters.** Rejected. Nothing asks for an
architect to invent new rule *kinds*, only to tune the six named ones; a fixed rule set
with data-driven knobs is the honest reading of "rules are data" without building an
expression engine nobody asked for.

**Run conformance after `deploy`, gating only `BUILT`.** Rejected — see decision 4. "No
model reaches the client repository" is explicit about *where* the boundary is; gating only
the state transition would still let a non-conforming model land in Git.

**Store every violation on `ModelFamily` alongside the ruleset version.** Rejected — see
decision 4. `build_run.steps` already carries this per attempt; duplicating it onto the
family would be a second, driftable copy of the same fact.
