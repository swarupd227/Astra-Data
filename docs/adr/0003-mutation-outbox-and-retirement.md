# ADR 0003 — Mutation events are an outbox, and nodes are retired rather than deleted

Status: accepted · 2 September 2026 · Story S1.1.3 (E1 / F1.1)

## Context

S1.1.3 is the auditor's story: "every graph mutation ... recorded with who made it and
from which run", so that "the graph can be reconstructed and any fact traced to its
origin". Its three criteria are mutation CloudEvents carrying the run id, a replay from
empty that reproduces the live graph, and the impossibility of a hard delete.

The specification puts the event bus at CloudEvents over Event Hubs (§5.4) and makes the
console an event-sourced view. Publishing to that bus is E12's work, not this story's.

## Decisions

### 1. The event is committed with the mutation, in one transaction

`estate_event` is a transactional outbox. Every write in `writes.py` produces its event,
and the repository inserts it inside the same transaction as the graph write.

This is the decision the story turns on. An event published on a best-effort basis after a
commit cannot satisfy "a replay of the event stream from empty produces a graph identical
to the live graph": a crash between the two leaves a fact in the graph that the record
does not contain, and the platform's evidence would be silently incomplete. Committing
them together makes the property structural rather than probabilistic.

`published_at` on the outbox row is where E12's publisher hooks in, and
`estate_event_unpublished_idx` is the index it will read.

### 2. Events carry the whole post-write property set, not a patch

An upsert event contains the element exactly as it now is. Replay therefore needs no prior
state, applying an event twice is a no-op, and the stream can be replayed from any point.
The cost is a larger event; the benefit is that the record is self-describing, which is
what an auditor is actually asking for.

The same reasoning makes the upsert path *replace* rather than merge. A merge would let a
property the writer dropped survive in the graph, and the replay would then legitimately
disagree with the live estate.

### 3. Event type names come from the story, not from Appendix C

S1.1.3 names `estate.node.upserted`, `estate.edge.upserted` and `estate.node.retired`.
Specification Appendix C uses an `astra.data.*` prefix throughout its own catalogue and
does not list these three. The story's names are used verbatim because they are the
acceptance bar. **This is an inconsistency in the source documents and is on the list of
open questions below.**

### 4. Retirement, not deletion — with who and why

There is no delete path anywhere in the service, and
`test_no_delete_route_exists` asserts that against the router rather than by probing one
URL, so a delete added anywhere fails it.

`retired_at`, `retired_by` and `retirement_reason` were added to the ontology's base node
properties. The story names only `retired_at`; the other two are there because the story's
persona is the auditor and a retirement with no principal and no stated reason is not
something anyone can audit later — the same reasoning the specification applies to gate
decisions in P4. A retirement is refused without a reason of at least eight characters.

Reads exclude retired nodes by default, with `include_retired` to see them. Retirement is
meaningless if a retired node still appears in every neighbourhood.

Adding three optional properties is an *additive* ontology change under the ADR 0001
classifier, so it needed no backfill — but it did need the schema version bumped to 2,
which the migration guard enforced.

### 5. The relational index tables are scoped by graph

`estate_element_index` and `estate_edge_index` gained a `graph` column, moving their
primary key from `id` to `(graph, id)`. That is what lets a replay rebuild the estate into
a second AGE graph in the same database and be compared against the live one, rather than
colliding with it.

This is a breaking change to those tables, so migration 0003 carries a backfill: every row
that existed was written by this deployment against its configured graph, because the
column did not exist to hold anything else.

### 6. Replay is a real rebuild, not a simulation

`tools/verify_replay.py` applies the event stream through the same repository the service
writes with, into a scratch graph, then compares element by element: same ids, same
labels, same properties, same edge endpoints. Identifiers, provenance and timestamps are
all compared, because the auditor's question is whether the record accounts for the estate
exactly.

Verified non-vacuous: changing one node's `name` directly in AGE, leaving the event stream
untouched, makes the check fail and name the node and the property.

The nightly job (`.github/workflows/nightly.yml`) migrates, seeds a test estate through
the real write path, and runs the verification. On a 25-workbook estate it replays 981
events and reproduces 402 nodes and 576 edges exactly.

## An Apache AGE defect worth knowing about

AGE caches label relations **per database session**. Dropping a graph leaves that cache
stale, and the next label operation on the same connection fails with
`label (relation) cache corrupted`, after which PostgreSQL terminates the connection with
`terminating connection because protocol synchronization was lost`.

Consequences, all of which cost time to find:

* a connection must not drop two graphs, or drop then recreate one;
* more importantly for operations, **a pooled connection that has touched a graph will die
  if that graph's label set changes underneath it.** Migrations 0001 and 0002 create
  labels, and a future ontology change will create more. Connection pools must therefore
  be recycled after any migration that adds a label — the service should not be running
  against a pool opened before it.

The test suite works around it by using a fresh connection per drop. The operational
consequence is not worked around anywhere yet, and is an open question below.

## Consequences

- Nothing can change the Estate Graph without leaving a record of who changed it and from
  which run; a rejected write leaves nothing at all.
- The stream is sufficient to rebuild the estate, and that claim is checked nightly rather
  than asserted.
- E12's publisher has a defined hook (`published_at`) and does not need the write path to
  change when it arrives.
- Every read now has to consider retirement. `neighbourhood`, `closure` and `step` exclude
  retired nodes; a future read must decide the same question deliberately.

## Open questions for the product owner

1. **Event type naming.** The story says `estate.*`; Appendix C says `astra.data.*` for
   every other event on the platform. The story's names shipped. Confirm whether these
   should be renamed to `astra.data.estate.*` before E12 publishes them, since renaming
   after consumers exist is much more expensive.
2. **Edge retirement is not specified.** S1.1.3 names `estate.node.retired` and no edge
   equivalent, so edges cannot currently be retired. A retired node keeps its edges, and a
   traversal from a live neighbour will not cross into it because the *node* is filtered —
   but the edge is still in the graph and in a dump. Confirm whether edges need retirement
   of their own, and what should happen to a workbook's edges when the workbook is retired.
3. **Pool recycling after a label-adding migration.** Given the AGE defect above, a
   deployment that migrates while the service is running will kill in-flight connections.
   The safe sequence is migrate-then-restart. Confirm that is acceptable, or whether the
   platform needs a connection-recycling signal.
4. **Event retention.** The outbox grows without bound; at N1 scale (1,500 workbooks,
   two million edges) an estate produces millions of rows, and the specification's §22
   retention requirements do not mention the mutation stream. S1.3.2 wants graph versions
   addressable by event offset for the programme lifetime plus twelve months, which
   suggests the stream is kept that long — confirm.
