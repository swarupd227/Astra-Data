# ADR 0005 — Parse quality counts accepted constructs, and an upsert stops rewriting creation

Status: accepted · 2 September 2026 · Story S1.2.2 (E1 / F1.2)

## Context

S1.2.2 is the parity engineer's story: know, before the Calibration Wave, which workbooks
the grammar cannot yet read. It asks for a score on the Workbook node, the unrecognised
constructs stored verbatim and located, a queue of workbooks below a configurable
threshold, and two ways to resolve one — accept a construct, or extend the grammar — each
re-scoring the workbook "without a full re-harvest".

S1.2.1 already computed a parse-quality figure and held workbooks below the threshold, but
kept the figure in the harvest record, discarded the constructs into an opaque JSON blob,
and had no way to change either without re-harvesting.

## Decisions

### 1. An accepted construct counts towards the score

    parse_quality = (recognised + ignorable) / total

Specification §4.1.4 defines parse quality as the fraction of constructs the grammar
recognised. Taken literally, accepting a construct would leave the score unchanged, and
S1.2.2's "either action re-scores the workbook" could not be satisfied by accepting one.

So the score answers *may the platform proceed with this workbook*, and an engineer's
decision that it may is part of the answer. The honest grammar-coverage figure is not
lost: `recognised`, `ignorable` and `total` are all stored, so the Calibration Report can
show grammar coverage separately from workbook readiness — which is what §9.5 and §14.3
actually want from it.

### 2. `parse_quality` goes on the Workbook node — a declared deviation

§4.1.1's Workbook row does not list it, but §4.1.4 requires that "every Harvester run
records a parse quality score per workbook" and makes it a release-readiness check for the
Calibration Wave. S1.2.2 requires it on the node specifically, so the estate can be
filtered by it without joining to harvest history.

Declared in `SPEC_DEVIATIONS` with that reasoning, so the spec-conformance guard accepts
it and the difference is on the record rather than silent.

### 3. Constructs are relational, and grouped by text

An unrecognised construct is a fact about a *parse*, not about the estate, so it lives in
`parse_construct` rather than in the ontology — the same reasoning that puts harvest runs
in §21's relational tables.

The queue is readable both ways round, and the construct-first view is the one that
decides work: one grammar gap typically blocks many workbooks, so
`workbooks_released_if_resolved` counts the held workbooks for which a given construct is
the *only* remaining blocker. On the local fixture estate that reads "RAWSQL_INT: releases
12". S1.4.3 renders this; the query lives here.

### 4. A decision is carried forward across re-parses

Marking `RAWSQL_INT(...)` ignorable applies to the construct *text*, and `record_constructs`
carries an existing decision onto newly parsed occurrences. An engineer who accepted it
last week does not accept it again because a workbook was re-parsed.

The cost is that the decision is estate-wide by default. A `site` argument narrows it, and
every occurrence records the reason, the principal and the time, so "why was this workbook
released" has an answer.

### 5. Two re-score paths, only one of which needs the source

* **Accepting a construct** needs no source access: the constructs and counts are stored,
  so the score is recomputed and written back to the node. Verified by asserting the
  adapter's fetch count does not move.
* **Extending the grammar** does need a re-parse, because only the adapter can say what the
  new grammar reads. `harvest_workbook.grammar_version` records which grammar each
  workbook was parsed under, so the affected workbooks can be re-parsed *specifically* —
  which is what "without a full re-harvest" means. The targeted re-parse itself is driven
  by harvesting the affected scope; a dedicated endpoint for it is listed as an open
  question below.

### 6. "Does not advance to CLUSTERED" is a gate, not a transition

CLUSTERED is a Migration Unit state (§3.2) and MUs are E3's. What exists here is the fact
that gate will consult: `GET /v1/parse-quality/gate/{site}/{luid}` answers whether a
workbook may advance, with the score and the reason. E3 calls it rather than reimplementing
the threshold check.

## A defect this story surfaced in the shared write path

**An upsert was rewriting `created_by` and `created_at`.** Every upsert re-stamped them
with whoever wrote last, so a re-harvest of a changed workbook (S1.2.1) or a re-score
silently reattributed *creation* to the latest writer. The properties are named "created";
they should mean it.

Fixed by adding `updated_by` / `updated_at` to the base node properties and preserving the
creation values across the property replacement — in the same Cypher statement, so no
extra round trip and the Harvester's throughput is unaffected:

```cypher
MATCH (n:Label) WHERE n.id = $p_id
WITH n, n.created_by AS cb, n.created_at AS ca, n.created_in_run AS cr
SET n = { ...new properties... }
SET n.created_by = cb, n.created_at = ca, n.created_in_run = cr
RETURN n
```

`updated_*` are absent on a node that has only ever been created, so their presence is
itself the signal that something changed it.

This was found by a test assertion about a *different* thing — that a re-score preserves
the rest of the node — which is the argument for asserting on more than the property under
test.

## Consequences

- Ontology schema version 4. Both changes (`parse_quality`, `updated_by`/`updated_at`) are
  optional additions, so the guard classified them as additive and no backfill was needed.
- A workbook harvested before this has no score until it is next harvested. That is the
  correct reading of an absent optional property, not a gap to backfill.
- Every read of a node now distinguishes "who made it" from "who last touched it". Any
  future write path must preserve that distinction; the repository's upsert is the only
  place that needs to know how.

## Open questions for the product owner

1. **Should the score be the honest grammar coverage instead?** Decision 1 folds accepted
   constructs into the score so that accepting one re-scores the workbook, as S1.2.2 asks.
   The alternative is to leave `parse_quality` as pure grammar coverage and hold/release on
   a separate flag. Both figures are stored either way; this is about which one the
   Calibration Report and the Estate Explorer show as "parse quality".
2. **Accepting a construct is estate-wide by default.** That matches how the work actually
   goes — a grammar gap is a property of the grammar, not of one workbook — but it means one
   decision can release many workbooks at once. Confirm that is wanted, or whether the
   default should be site-scoped.
3. **A targeted re-parse endpoint after a grammar extension.** Today the affected workbooks
   are re-parsed by harvesting their scope, which re-fetches them. A
   `POST /v1/parse-quality/reparse` taking a grammar version and re-parsing only what was
   parsed under an older one would be tighter. Not built, because nothing yet ships a
   grammar that can change — that arrives with the Tableau adapter (E2/F2.3).
4. **No threshold is configured per tenant.** It is a request parameter with a 0.98 default
   (§4.1.4). §22 has no per-tenant configuration store yet; when one exists this should
   read from it rather than from each caller.
