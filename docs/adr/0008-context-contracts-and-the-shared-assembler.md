# ADR 0008 — A context is a fragment, a plan and a budget, and its hash is the point

Status: accepted · 2 September 2026 · Story S1.3.1 (E1 / F1.3)

## Context

Specification §4.1.3: agents do not receive raw graph dumps. Each declares a context
contract — the sub-graph shape it needs for one unit of work — and a shared assembler
materialises exactly that. S1.3.1 makes it concrete: a contract is "a named GraphQL
fragment plus a serialiser", the assembler returns "a canonical JSON document and its
sha256 (context_hash)", two calls over the same graph state must hash the same, the
Transpiler's contract carries what §4.1.3 lists and **nothing else**, and a contract over
its declared budget fails the call rather than truncating.

S1.1.2 shipped a shaped read under the same name and said so: named sections, no
canonicalisation, no hash, no budget, and the Pattern section declared as something the
platform could not yet compute. This story replaces it.

## Decisions

### 1. The fragment is validated against the generated schema, so it means something

A contract's fields are a GraphQL fragment checked against the schema S1.1.2 generates
from the ontology registry. A contract naming a property the ontology does not declare
fails `make ci`, not a caller. A hand-written list of strings would be checked against
nothing, and the first anyone would know is a context with a missing key.

The fragment also *is* the §18.3 inference boundary. Everything an agent sees is what the
fragment selects; a property not in the fragment cannot cross, whatever the graph holds.
That makes the boundary reviewable — `GET /v1/contexts` publishes the fragments, and
`tools/contract_check.py` prints every field of every section, so a pull request that
widens the boundary shows it in the build log rather than in a diff of a serialiser.

Validation runs when the first assembler is constructed and in CI, not at import: the
schema being validated against needs `ContractName` to declare its own enum, so a contract
that validated itself while being imported would close that circle.

### 2. A fragment is not the whole contract, and pretending otherwise would be worse

GraphQL cannot express "the transitive DEPENDS_ON closure" — a fragment selects fields
from a shape somebody already navigated to. So a contract is a fragment (which fields)
plus a resolution plan (which nodes), and the assembler runs the second and applies the
first. The alternative is a recursive fragment with a hard-coded depth, which is the same
guess written less honestly. Contract fragments are therefore flat by design, and a nested
selection is refused.

### 3. Canonical means every degree of freedom removed, including row order

Sorted keys, no insignificant whitespace, Unicode as text rather than escapes, NaN and
Infinity refused, and **every collection sorted by id before serialisation**.

That last one is the one that matters. PostgreSQL is free to return rows in a different
order between two identical queries, so a hash that depended on row order would fail
intermittently — the worst kind of failure for something a provenance record is checked
against. The in-memory repository would never have caught it, which is why the determinism
criterion is tested against real AGE, five times, and across two independent assemblers
with separate pools.

### 4. Audit metadata is deliberately outside the context

No `created_by`, `created_at`, `created_in_run`, `updated_at` or retirement properties.
Two reasons, and the second is the load-bearing one:

* they are facts about the record, not about the calculation, and every field is a token
  spent on every call and one more thing across the inference boundary;
* including them would change the context hash whenever a workbook was re-harvested with
  no semantic change — so §5.4's gateway cache on identical context hashes would never
  hit, and a §4.2 provenance record could never be checked by re-assembling its context.

The node ids are derived from source identity (S1.2.1), so they are stable across
re-harvests, which is what makes this work at all.

`Parameter.current_values_seen` is excluded for a different reason: §4.1.3 asks for "the
Parameter domains", and observed values are data the client's users entered. §18.3 puts
data on the far side of the boundary.

### 5. Patterns are matched, not promised

§4.3 gives a pattern's signature as `{ ast_shape: 'DIV(SUM(a), LOD_FIXED(dims, SUM(b)))',
adapter: tableau }` — a compact string with leaf identifiers abstracted to capture names.
`signature.py` computes that string from a calculation's AST and matches on equality.

The walk is structural rather than grammar-aware, because the calculation grammar is the
Tableau adapter's (E2/F2.3) and does not exist yet: a dictionary carrying an operator key
is a call, one carrying a single identifier key is a leaf, and everything else is
structure. An adapter that names its AST nodes differently declares the extra keys rather
than rewriting the walk. Literal *values* never reach a shape — two calculations differing
only in a constant should match the same pattern, and a literal can carry client data.

Ranking, fuzzy and partial matching, guard evaluation, promotion and pass statistics stay
with the Pattern Library (E5/F5.3). RETIRED patterns are excluded — a pattern withdrawn
for producing wrong output should not be offered as a candidate — and CANDIDATE ones are
included, because a candidate accumulating evidence cannot accumulate any if nothing is
ever offered it.

### 6. Over budget fails the call

256 KB and 400 nodes for the Transpiler. A context that does not fit is not a context to
trim: an agent cannot tell a shortened dependency closure from a complete one and would
generate confidently from a partial picture, and the provenance record would carry a hash
of the truncated context as if it were the whole. So `413`, with both dimensions reported
because the useful question is "by how much, and in which direction".

The closure is read with a limit one over the node budget, so a closure that would blow
the budget is *detected* as too large rather than silently cut to the limit — the limit
and the budget being the same number would have hidden exactly what the budget exists to
catch.

### 7. The materialised document is returned whole, on both surfaces

`GET /v1/contexts/{name}/{subject}` returns the document, its hash and its usage. The
GraphQL query returns the same. Neither lets a caller select part of it, which is a
deliberate departure from what `/graphql` is for everywhere else: the `context_hash`
describes the whole document, so a partial response would carry a hash of something other
than what was returned.

## Consequences

- `contracts.py` is gone; the `context` package replaces it. The GraphQL `context_contract`
  query changed shape — from typed nodes a caller could select, to a hashed document.
- One new repository read, `outgoing_edges`, because a source field's target column is a
  property of the MAPS_TO edge: §4.1.1 declares no column node, and `step` answers "what
  does this point at" rather than "what does the pointing say".
- A fourth guard, `tools/contract_check.py`, in `make check` and in CI.
- No ontology change and no migration: a contract is code, and the assembler only reads.
- Measured on the fixture estate: a Transpiler context for a real calculated field is
  **661 bytes** and three nodes, against a 256 KB budget.

## Open questions for the product owner

1. **The budget figures are a judgement, not a measurement.** 256 KB and 400 nodes are
   sized so that a context which trips them is a signal an engineer should see rather than
   a limit the platform routinely brushes. Once E2 harvests a real client estate, the
   distribution of real closure sizes should set them — and they should probably be per
   tenant, which §22 has no store for yet.
2. **Patterns match on exact shape only.** A calculation that differs from a pattern by one
   wrapped function matches nothing, and the Transpiler falls back to generating. That is
   correct and safe today; whether the contract should also carry *near* matches, so a
   model can adapt one, is an E5 design question with a real cost — near matches are
   tokens, and a wrong pattern in context is worse than none.
3. **One contract exists.** The Modeller's shape is settled by E4, the Compositor's by E6,
   the Mender's by E8. Declaring them now from the one-line summaries in the §8.3 catalogue
   would be guessing at input shapes those epics exist to specify. The assembler is built
   to take them without change.
4. **Nothing enforces that an agent sends what it was given.** The assembler returns the
   exact bytes it hashed, but an agent could assemble a context, edit it, and send
   something else with the original hash attached. Closing that is the gateway's job
   (§5.4) — it should hash what it receives and compare — and it belongs with E12.
5. **The document is not redacted beyond field selection.** §18.3 also has the gateway
   redact custom SQL literals matching the client's secret patterns. No field in this
   contract carries custom SQL, so nothing is lost today; a future contract that includes
   `Connection.custom_sql` would need redaction the fragment cannot express, and that is
   the point at which the boundary stops being reviewable as one page of GraphQL.
