# ADR 0009 — A graph version is an event offset, and a provenance record is checked by re-materialising it

Status: accepted · 2 September 2026 · Story S1.3.2 (E1 / F1.3)

## Context

Specification §16.4: "an auditor can ask 'what did the model see?' and be shown the context
hash and the prompt hash, **with the context reproducible from the graph at that version**."
S1.3.2 is that final clause: from a ProvenanceRecord the console must re-materialise the
context at the recorded graph version and show that the hash matches, with graph versions
addressable by event offset and retained for the programme lifetime plus twelve months.

S1.3.1 produced the hash. On its own a hash is a label: it says two contexts were the same
without letting anybody check that either was what an agent actually saw. This story turns
it into evidence.

## Decisions

### 1. A version is an event sequence number, and nothing else

S1.1.3 made the outbox the record — every mutation committed with its event, and a replay
from empty reproducing the graph exactly. That property already means the stream up to
sequence *n* fully determines the graph at *n*. So a graph version *is* an event offset:
no snapshots, no version table, no second identifier to keep in step. The number an auditor
quotes is one they can look up in the same event stream the platform publishes.

Version zero is the empty graph — an addressable version, not an error.

### 2. §4.2's record gains `graph_version`, because without it the hash proves nothing

The ProvenanceRecord as printed in §4.2 carries `context_hash` in its `inputs` block and no
version. That is not reproducible. The graph moves — a re-harvest, a pattern promotion, an
ownership assignment — so re-materialising the same contract for the same subject a week
later gives a different document and a different hash, with no way to tell a genuine
mismatch from an ordinary Tuesday. `inputs.graph_version` is a declared extension to §4.2,
and it is the whole difference between a record that describes and a record that verifies.

### 3. History is read by indexed lookup, not by replay

A replay of the stream up to an offset is the *definition* of what the graph held. It is
also, over a programme, millions of events for one audit.

Because each event carries its element's complete post-write state — S1.1.3's design, and
the reason replay needs no prior state — history can be read directly instead: a node's
state at version *n* is its latest `estate.node.upserted` at or below *n*, its retirement
is a second lookup, and the edges of one type out of it are the latest
`estate.edge.upserted` per edge id at or below *n*, found through an index on the event's
`from_id`. A transitive closure is those two, iterated to a bounded depth: one query per
level rather than the recursive CTE the live reader uses, because that CTE runs over the
adjacency index, which holds only *current* edges.

Measured: a verification against an 11,346-event stream takes ~55 ms.

That makes two implementations of "what the graph held", so the load-bearing test is that
they agree — the integration suite replays the stream to a version, reads the same version
through the indexed reader, and requires identical context hashes. An optimisation that
disagreed would make every audit wrong and nothing else in the suite would notice.

### 4. The historical reader satisfies the same protocol the live one does

`ContextAssembler` takes a `ContextReader`. `HistoricalGraphReader` implements it, so the
assembler materialises a contract at a past version without knowing the difference — no
branch, no second code path, no "audit mode".

This is the point. If audit used different code, a re-materialised context would be
evidence about *that code* rather than about what the agent saw. Making the reader a
protocol in S1.3.1 was what made this story a new reader rather than a new assembler.

### 5. Verification re-materialises; it never looks anything up

Nothing stores the assembled document. The verifier re-runs the assembler over the graph as
it stood at the recorded offset and compares the hash it computes against the one the
record claims. A stored copy would only prove that a copy was stored.

Three outcomes, and MISMATCH and UNVERIFIABLE are never conflated:

* **MATCH** — the context re-materialised and hashes to the recorded value;
* **MISMATCH** — it re-materialised and hashes to something else. The record is wrong about
  what the agent saw, or the stream has been altered;
* **UNVERIFIABLE** — it could not be re-materialised: the subject did not exist at that
  version, or the record cites a version beyond what this graph has reached.

A failed verification is a **200 with a finding**, not a 4xx. An auditor's tool that
returned an error status for the interesting case is one an auditor learns to distrust.

### 6. Recording and verifying are separate operations

`POST /v1/provenance` does not check the hash it is given. Recording is what an agent does
at the moment it produces an artefact; verifying is what an auditor does later. Folding
them together would mean a verification failure could stop an artefact being recorded —
losing the very evidence that shows something went wrong.

There is also `POST /v1/provenance:verify`, which takes the claim rather than a record id.
§5.2 gives provenance linkage to artefact-svc, which does not exist yet; when it does, it
uses the audit path without this service having to hold the record.

### 7. Retention is computed from the programme, and nothing prunes

"Programme lifetime plus twelve months" is expressed as a computation over a programme
record rather than a configured date, so a programme that overruns by eighteen months does
not silently lose the first year of its own evidence.

`prunable_before` permits deletion in exactly one case — every programme closed, and the
earliest close plus twelve months has passed. The *earliest*, because a cutoff has to be
safe for every programme sharing the graph. Two cases that look like permission are not:
an open programme holds everything, and **no programme recorded holds everything too** —
an empty table is not permission to delete.

Closing a programme starts the clock and cannot be undone or re-dated: a retention floor
that can be moved is not a floor.

**Nothing prunes.** There is no pruner, no TTL, no scheduled deletion. What exists is the
policy any future pruner has to ask, and `/v1/retention` reports `pruning_implemented:
false` rather than implying a job that does not exist. Building the policy before the
deletion is the right order — an audit trail pruned by a job written before anybody decided
the rule is not an audit trail.

## Consequences

- Migration 7: the `provenance` and `programme` tables, and three indexes on `estate_event`
  that turn historical reads from a replay into lookups. One indexes an expression over the
  event's JSON, because that is where an edge's endpoints already live and adding columns
  would mean rewriting every event ever written.
- `repository.current_version()`, and `graph_version` plus `retention` on Platform Health.
- No ontology change: provenance and programmes are platform records, and reading history
  reads what is already written.
- The event stream is now load-bearing for audit as well as for replay. Anything that would
  prune it has to pass `retention.prunable_before` first.

## Open questions for the product owner

1. **A verified context still does not prove what was sent to the model.** This shows that
   the context the record cites is the context this graph would have produced. It cannot
   show the agent sent that context rather than an edited one — closing that needs the
   gateway to hash what it receives and compare (§5.4, E12). Until then a provenance record
   is verifiable against the graph, not against the wire.
2. **Retention has no lower bound.** The policy says how long versions must be *kept*; it
   says nothing about a client who wants data removed sooner — a GDPR erasure request
   against a workbook name, say. The two are in tension and the resolution is a legal one,
   not a technical one, but it should be settled before a client asks.
3. **A closed programme's floor is per graph, not per programme.** Two programmes sharing a
   graph share the earlier floor, which is safe but wasteful — the second programme's events
   are held for the first's benefit. Splitting retention per programme needs events
   attributed to a programme, which needs the MU (E3).
4. **Nothing verifies in bulk.** An auditor checks records one at a time. A "verify every
   provenance record for this release train" sweep is what an evidence export (§15.3) would
   actually want, and it is cheap to add once E13 knows what a bundle contains.
5. **The programme record is three fields.** §21 gives it a charter version, a calibration
   baseline and a scope. Only what retention needs is here; the rest arrives with the epics
   that read it, and the table will need extending rather than replacing.
