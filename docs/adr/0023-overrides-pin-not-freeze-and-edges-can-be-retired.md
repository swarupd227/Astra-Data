# ADR 0023 — Overrides pin, they don't freeze; and edges can now be retired

Status: accepted · 3 September 2026 · Story S3.1.2 (E3 / F3.1)

## Context

S3.1.2 asks for three edits a model engineer makes to the Cartographer's proposal — split,
merge, move — each recording who, when and why, and asks that these overrides *survive* a
re-cluster: *"Overrides are preserved across re-clustering runs; a re-run reports what it
would change and does not change overridden families without confirmation."*

Two design questions decided everything else. First: what does "preserved" mean when the
Cartographer's own re-run (S3.1.1) unconditionally retires and replaces every family it owns?
Second: "move" needs a workbook's `IN_FAMILY` edge to point somewhere else, and a property
graph edge's endpoints are fixed at creation — so what does "move" actually do to the graph?

## Decisions

### 1. Overrides are read by `Cartographer.run`, not enforced by a separate gate

A family becomes `overridden = True` the moment any of split, merge or move touches it. A
re-run reads that flag directly: an overridden family's members are excluded from clustering
entirely (never a merge candidate, never left to drift into someone else's proposal), and the
family itself is left un-retired. There is no separate "override registry" — the fact lives on
the `ModelFamily` node the override already had to write, the same way `S1.1.1`'s retirement
fields live on the node they retire rather than in a side table.

This is deliberately the SAME module enforcing it: `cartographer.py` already owns "which
families does a re-run retire and replace" (`CARTOGRAPHER_OWNED_STATES`), and `overridden` is
one more condition on that same decision, not a second mechanism next to it.

### 2. Confirmation is per family, not per workbook or per estate

`run(confirm_family_ids=...)` un-pins exactly the families named. A workbook can be excluded
from clustering by a family it is NOT the one confirmed — moving Alpha out of a split remainder
pins Alpha via its *new* family, not the remainder — so reuniting a fully split group needs
every family in the chain named. This is more ceremony than "confirm the workbook" would be,
and it is the correct ceremony: confirmation is a statement about a *decision* (this family, as
it stands, may be superseded), and a decision was made about a family, not a workbook.

### 3. What a re-run reports is a real, secondary clustering run — not a cached guess

`ClusteringResult.would_change` is not derived by inspecting the pinned families in isolation.
`_compute` runs the free algorithm **twice** when anything is pinned: once excluding pinned
workbooks (what gets written), once including them (what the estate's own lineage says they
would become). Both runs share the same expensive read (`gather_reach`, one pass over the
whole estate) and reuse the same `pair_scores` — only the cheap, pure clustering step
(`agglomerative_clusters` + `resolve_undersized`) runs twice. So "what it would change" is
never stale relative to "what it did" — they are computed from the identical evidence in the
same call.

### 4. Result states are always PROPOSED — SINGLETON is not re-derived

A split, merge or move never produces a `SINGLETON` family, even a one- or two-member one.
SINGLETON is §12.1's label for what the *algorithm* concludes when it cannot responsibly grow
a family under the minimum size; a human composing a small family on purpose has already made
that judgement, and re-applying the algorithm's own escape hatch to a human's decision would
be the software second-guessing the person the whole story is about giving control to.

### 5. An edge can now be retired — the seam S1.1.1 always intended

"Move" needs a workbook's `IN_FAMILY` edge to point at a different family. Cypher edges are
immutable once created — `SET e = {...}` can replace properties, never endpoints — so a move
is unavoidably *retire the old edge, write a new one*. `BASE_EDGE_PROPERTIES.id`'s own note,
written at S1.1.1, already said an edge id exists "so an edge can be addressed and
**superseded**" — this is that seam, used for the first time.

Implemented exactly like node retirement (`GraphWriter.retire_edge`,
`AgeGraphRepository.retire_edge`, an `EDGE_RETIRED` event, a replay branch that reapplies the
retired property set rather than calling `retire_edge` again, so replay stays idempotent).
`estate_edge_index` gets its own `retired_at` column (migration v0013) rather than requiring
every adjacency query to join the element index for it — the same reasoning that table was
built on in the first place (v0002): a fact checked on every hop is worth a column, not a join.

Every direct query this story's own code depends on (`children()` in `lineage.py`,
`_family_members`/`_members_of` in `cartographer.py`/`family_overrides.py`) now filters
`retired_at IS NULL` on the edge as well as the target node. The general-purpose graph
traversal (`neighbourhood`/`closure`, used by the Estate Explorer and arbitrary Lineage View
queries) does **not** yet — nothing in this story's critical path calls it for `IN_FAMILY`
edges, and teaching the whole traversal layer about edge retirement is a real, separate piece
of work. Flagged as a follow-up (task_4d2f9af4) rather than folded in here.

### 6. A workbook's own `IN_FAMILY` edge id is looked up, not tracked

`family_overrides.py` finds a workbook's current family by querying `estate_edge_index`
directly for its live (non-retired) `IN_FAMILY` edge, rather than requiring a caller to supply
one. A model engineer splitting or moving a workbook knows the workbook and the family; making
them also pass an edge id would leak an implementation detail (that membership is edge-shaped
at all) into the one surface this story is building specifically so a person does not have to
think in graph primitives.

### 7. New `ModelFamily` node must exist before an edge can point at it

Both `split_family` and `merge_families` write the new family node *before* relinking any
member's edge to it — an edge write is refused when either endpoint is missing from the graph,
and a fresh family's id does not exist until its node does. `move_member` has no such ordering
requirement: both its endpoints (the source and target families) already exist. Found the hard
way, by the ontology validator refusing exactly the edge this story exists to write, in the
first integration test run against real PostgreSQL — the in-memory fake was never exercised
against this path, so nothing caught the ordering bug before real endpoint-existence
validation did.

## Consequences

- A tenant's family list is always exactly two kinds of thing: a live Cartographer proposal
  (which a re-run may replace outright) or a human decision (which a re-run reports on and
  leaves alone). Nothing is ever silently in between.
- Confirming a family is a real, auditable act — it is a parameter on the same `run` call
  everything else goes through, not a separate endpoint with its own semantics to keep in
  sync.
- Retiring an edge is now general infrastructure, not an S3.1.2 special case. The next story
  that needs to change what an edge points at reaches for `retire_edge` rather than inventing
  its own "supersede" convention.
- `neighbourhood`/`closure` traversal is a known, named gap until its own follow-up lands —
  a retired `IN_FAMILY` edge would still surface there today. Nothing in this codebase
  currently reads family membership through that path, so the gap is real but currently inert.

## Alternatives considered

**A boolean lock on the family instead of reading `overridden` inside the clustering
algorithm.** Rejected: it would need clustering to run twice regardless — once to see what it
would produce, once filtered — which is exactly what `_compute(pinned=...)` already does in
one pass over the shared reach data. A separate lock would just be a second place the same
fact could drift from the first.

**Confirm by workbook id instead of family id.** Rejected — see decision 2. A workbook-level
confirm would let a caller un-pin a family by naming none of its members explicitly, which
reads as confirming a decision nobody stated.

**Mutate the existing `IN_FAMILY` edge's `to_id` in place for a move.** Not possible in Cypher
— endpoints are immutable — and even if the underlying store allowed it, the resulting edge
would have no honest `created_at` for the relationship it now represents.

**Fix `neighbourhood`/`closure` inline rather than flagging it.** Rejected on proportion, the
same call S3.1.1 made for the `ENCODES` gap: real, but touches a different surface (general
traversal) than this story's own critical path needs, and is better done — and reviewed — on
its own.
