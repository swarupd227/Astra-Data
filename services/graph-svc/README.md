# graph-svc

The Estate Graph: the parsed source estate, the target artefacts as they are produced, and
the relationships between them. Spec §4.1, §5.2. Every agent reads and writes it; no agent
holds private state about the estate (spec principle P3).

PostgreSQL 16 with Apache AGE (spec §5.4).

## What this service enforces

Every write is validated against the ontology before it reaches the store. A write is
rejected with **422** and a body naming what was wrong when it has:

- an unknown node or edge type
- a property not declared on that type
- a missing required property
- a value that does not match its declared type
- a property the service owns (`created_by`, `created_at`, `written_by`)
- endpoints an edge type does not permit — `USES_DATASOURCE` from a `Site`, say
- an endpoint id that is not in the graph

```json
{
  "error": "ontology_violation",
  "message": "property 'formula_ast' is required on node type 'CalculatedField' (json).",
  "violations": [
    {
      "code": "missing_required_property",
      "message": "property 'formula_ast' is required on node type 'CalculatedField' (json).",
      "property": "formula_ast",
      "type": "node type 'CalculatedField'",
      "index": 0
    }
  ]
}
```

Every violation in a submission is reported, not just the first. A batch is validated in
full before any of it is written.

## Base properties

Every node carries `id` (ULID), `side`, `created_by` and `created_at`. Every edge carries
`id`, `written_by` and `created_at`. `side` is fixed by the node type except on `User`,
which the specification places on both sides, where the writer declares it.

`created_by` and `written_by` come from the `X-Astra-Principal` header
(`agent:harvester`, `user:a.mehta@client.example`, `service:graph-svc`). E11 replaces the
header with a verified identity — Entra ID for people, workload identity for agents — and
the ontology does not change when it does.

## API

| Method | Path | Purpose |
|---|---|---|
| `GET` | `/health` | Pool answers and the graph exists |
| `GET` | `/v1/ontology` | The ontology as JSON |
| `GET` | `/v1/ontology.md` | The ontology as the generated reference |
| `POST` | `/v1/nodes` | Write one node |
| `POST` | `/v1/nodes:batch` | Write up to 1000 nodes in one transaction |
| `GET` | `/v1/nodes/{id}` | Read one node back |
| `POST` | `/v1/edges` | Write one edge |
| `GET` | `/v1/edges/{id}` | Read one edge back |
| `PUT` | `/v1/nodes/{id}` | Create or replace one node |
| `PUT` | `/v1/edges/{id}` | Create or replace one edge |
| `POST` | `/v1/nodes/{id}:retire` | Retire a node |
| `GET` | `/v1/events` | The mutation event stream |
| `POST` | `/graphql` | The typed query API |
| `POST` | `/v1/cypher` | Read-only Cypher, Artizent roles only |
| `POST` | `/v1/harvests` | Start a harvest |
| `GET` | `/v1/harvests` | Recent harvest runs |
| `GET` | `/v1/harvests/{id}` | Progress, per project |
| `GET` | `/v1/harvests/{id}/failures` | Workbooks that failed, with the error |
| `GET` | `/v1/parse-quality/queue` | Workbooks the grammar could not fully read |
| `GET` | `/v1/parse-quality/constructs` | Unrecognised constructs, grouped by what they block |
| `GET` | `/v1/parse-quality/workbooks/{site}/{luid}` | One workbook's constructs, verbatim and located |
| `POST` | `/v1/parse-quality/constructs:ignorable` | Accept a construct, and re-score |
| `POST` | `/v1/adapters/conformance` | Record a signed conformance report |
| `GET` | `/v1/adapters/conformance` | Recent reports |
| `GET` | `/v1/adapters/conformance/{id}` | One report, in full |
| `GET` | `/v1/adapters` | Adapters promoted on this tenant |
| `POST` | `/v1/adapters/{name}:promote` | Enable a build — refused without a passing report |
| `POST` | `/v1/adapters/{name}:revoke` | Withdraw one, with a reason |
| `POST` | `/v1/adapters/{name}:check` | Would this build be promotable? |
| `POST` | `/v1/parse-quality/constructs:issue` | Raise a grammar issue for a construct |
| `GET` | `/v1/parse-quality/issues` | Open and recent grammar issues, and which tracker holds them |
| `POST` | `/v1/parse-quality/issues/{id}:resolve` | Close one, with a resolution |
| `GET` | `/v1/parse-quality/gate/{site}/{luid}` | May this workbook advance past HARVESTED? |
| `GET` | `/v1/estate` | Estate Explorer: tree, filtered workbooks and facet counts |
| `GET` | `/v1/lineage` | Lineage View: workbooks, their tables and fields, and shared lineage |
| `GET` | `/v1/estate/workbooks/{id}` | One workbook: summary, scope history, lineage |
| `POST` | `/v1/estate/workbooks/{id}:re-tier` | Set a tier, with a reason (PM only) |
| `POST` | `/v1/estate/workbooks/{id}:withdraw` | Take it out of scope, with a reason (PM only) |
| `POST` | `/v1/estate/workbooks/{id}:reinstate` | Put it back, with a reason (PM only) |
| `GET` | `/v1/contexts` | Every context contract, its fields and its budget |
| `GET` | `/v1/graph-versions/current` | The graph's current version, as an event offset |
| `POST` | `/v1/provenance` | Record how an artefact was produced |
| `GET` | `/v1/provenance/{id}` | One provenance record |
| `POST` | `/v1/provenance/{id}:verify` | Re-materialise its context and compare the hash |
| `POST` | `/v1/provenance:verify` | Verify a claim without a stored record |
| `POST` | `/v1/artefacts` | Store a binary artefact, content-addressed and linked to an MU |
| `GET` | `/v1/artefacts/{id}` | An artefact's metadata — never its bytes |
| `GET` | `/v1/artefacts/{id}/content` | An artefact's bytes, for a human viewer |
| `GET` | `/v1/artefacts` | Artefacts linked to a Migration Unit (`?mu_ref=`) |
| `POST` | `/v1/families:cluster` | Cluster the estate into candidate model families (§12.1) |
| `GET` | `/v1/families:cluster/status` | Whether a run is in progress, and the last one's figures |
| `GET` | `/v1/families` | Every model family, with its members (`?state=`) |
| `GET` | `/v1/families/{id}` | One family: members, grain, evidence |
| `POST` | `/v1/families/{id}:split` | Move selected members out into a new family, with a reason |
| `POST` | `/v1/families:merge` | Combine two families into one, with a reason |
| `POST` | `/v1/families/{id}:add-member` | Move one workbook into this family, with a reason |
| `POST` | `/v1/trains:propose` | Propose release trains from the estate's families and usage (§3.3) |
| `GET` | `/v1/trains:propose/status` | Whether a proposal run is in progress, and the last one's figures |
| `GET` | `/v1/trains` | Every release train, with its members in sequence |
| `GET` | `/v1/trains/{id}` | One release train: members, planned dates, gate schedule |
| `POST` | `/v1/trains/{id}:move-member` | Move one MU into this train, out of wherever it is now (Wave Board) |
| `POST` | `/v1/trains/{id}:resequence-member` | Reorder one MU within this train (Wave Board) |
| `POST` | `/v1/trains/{id}:set-wip-limits` | Configure this train's work-in-progress caps (Wave Board) |
| `GET` | `/v1/trains/{id}/events` | Recent changes to this train — its own Programme timeline feed |
| `GET` | `/v1/trains:projections` | Projected versus planned finish date per train, from measured throughput |
| `POST` | `/v1/families/{id}:propose-design` | Generate a model design proposal for one family, from the graph |
| `GET` | `/v1/families/{id}/design` | The most recently generated model design proposal for one family |
| `POST` | `/v1/families/{id}:accept` | Accept a proposed family for design — PROPOSED/SINGLETON → DRAFT |
| `POST` | `/v1/families/{id}:submit-for-review` | Submit a DRAFT design for G2 — DRAFT → IN_REVIEW, freezing a version hash |
| `GET` | `/v1/families/{id}/transitions` | Every state transition this family has been through, with who and when |
| `POST` | `/v1/families/{id}:edit-grain-statement` | Edit the drafted grain statement while a design is DRAFT |
| `POST` | `/v1/families/{id}/tables/{table_id}:set-mode` | Override a candidate table's storage mode while a design is DRAFT |
| `POST` | `/v1/families/{id}/relationships:set-cardinality` | Override a candidate relationship's cardinality while a design is DRAFT |
| `POST` | `/v1/families/{id}:edit-domain` | Assign the family's business domain while a design is DRAFT |
| `GET` | `/v1/families:for-review` | Families a data owner's G2 review concerns — DRAFT, IN_REVIEW, APPROVED |
| `GET` | `/v1/families/{id}/proposal` | The Model Proposal (client view): what the model is, what changes, open questions |
| `GET` | `/v1/families/{id}/questions` | Every G2 question asked about one family's design, with its thread |
| `POST` | `/v1/families/{id}/questions:ask` | Ask a question about a family's design |
| `POST` | `/v1/questions/{id}:reply` | Reply in a question's thread — visible to both sides |
| `POST` | `/v1/questions/{id}:answer` | Mark a question answered — required before its family can be approved |
| `POST` | `/v1/families/{id}:approve-g2` | Approve a design at G2 — IN_REVIEW → APPROVED |
| `POST` | `/v1/families/{id}:request-changes` | Send a design back to DRAFT with a comment — IN_REVIEW → DRAFT |
| `POST` | `/v1/families/{id}:edit-owner` | Assign the family's G2 approver while a design is DRAFT |
| `GET` | `/v1/families:awaiting-g2` | Families awaiting G2: days waiting, the approver, and SLA breach (Programme Board tile) |
| `POST` | `/v1/g2/reminders:send` | Record and send whichever 3- and 5-day G2 reminders are now due |
| `POST` | `/v1/families/{id}:build` | Emit TMDL, check conformance, commit, deploy and smoke-test an APPROVED design — APPROVED → BUILT |
| `GET` | `/v1/families/{id}/build` | The most recent build attempt: result, commit, workspace, every step's log |
| `GET` | `/v1/conformance/rules` | The latest conformance ruleset a build is checked against, and what each rule means |
| `POST` | `/v1/conformance/rules` | Save a new version of the conformance ruleset — the architect's, in Admin |
| `GET` | `/v1/retention` | How long versions stay addressable, and what may be pruned |
| `POST` | `/v1/programmes` | Record a programme, starting its retention clock |
| `POST` | `/v1/programmes/{id}:close` | Close it, starting the twelve-month floor |
| `GET` | `/v1/programmes` | Every programme, with its clustering and family-count figures |
| `POST` | `/v1/programmes/{id}:confirm-family-count` | Confirm the estate's live family count as the Month 1 calibration input |
| `GET` | `/v1/contexts/{name}/{subject}` | Materialise a contract for one subject |
| `GET` | `/v1/platform/health` | Adapter, schedules, recent runs, source drift |
| `POST` | `/v1/harvest-schedules` | Schedule a recurring incremental harvest |
| `GET` | `/v1/harvest-schedules` | Every schedule, with its last run |
| `PATCH` | `/v1/harvest-schedules/{id}` | Change a cadence or credential |
| `POST` | `/v1/harvest-schedules/{id}:pause` | Stop it firing, keeping its history |
| `POST` | `/v1/harvest-schedules/{id}:resume` | Let it fire again |
| `GET` | `/v1/ownership/unresolved` | Owners the directory could not place, worst first |
| `POST` | `/v1/ownership/assign` | Link a source identity to a directory user by hand |

There is no delete endpoint, by design — see Retirement below.

## Querying

### GraphQL

One object type per ontology type, generated from the registry at start-up. Field names
are the ontology's own (`views_90d`, not `views90D`); the node's ontology type is read
with GraphQL's `__typename`, because Datasource, Filter, Action and Visual each declare a
property of their own called `type`.

```graphql
query($id: ID!) {
  neighbourhood(id: $id, depth: 3, edge_types: ["CONTAINS", "USES_DATASOURCE"]) {
    anchor { id }
    truncated
    nodes { depth node { __typename ... on Field { name datatype role } } }
    edges { __typename from_id to_id }
  }
}
```

| Field | Purpose |
|---|---|
| `node(id)` / `nodes(ids)` | Lookup by platform id |
| `node_by_luid(type, luid)` | Lookup by source-system identifier, within a node type |
| `neighbourhood(id, depth, edge_types, node_types, limit)` | Everything within 1–5 hops, and the edges between |
| `context_contract(name, subject_id)` | An agent's context contract, spec §4.1.3 |
| `schema_version` | The ontology version this service enforces |

Traversal is undirected and `depth` is the shortest path. A result that hit its element
limit sets `truncated`: it is a prefix of the neighbourhood, not a sample of it.

GraphiQL is served at `/graphql` when `ASTRA_ENV=local`, and nowhere else.

### Read-only Cypher

For the lineage question the typed API has no field for. Artizent roles only
(`X-Astra-Roles`), 30-second timeout, 10,000-row cap.

```bash
curl -sS localhost:8080/v1/cypher -H 'content-type: application/json' -H 'X-Astra-Principal: user:a.mehta@artizent.example' -H 'X-Astra-Roles: migration_engineer' -d '{"query":"MATCH (c:CalculatedField)-[:DEPENDS_ON]->(p:Parameter) RETURN c.name AS calc, p.name AS parameter"}'
```

Every returned item needs a name — a bare identifier or an `AS` alias — because Apache AGE
requires the result columns declared before the query runs. `RETURN *` is refused with
that explanation. Dollar-quote delimiters, `;` and write clauses are refused outright;
what survives runs inside a PostgreSQL `READ ONLY` transaction, which is what actually
makes the endpoint read-only.

## Harvesting

The Harvester runs the adapter's enumerate → fetch → parse loop for a scope and writes the
result to the graph (spec §8.4). It knows nothing about Tableau: it drives the §6.1
`SourceAdapter` contract.

**The Tableau adapter is E2 (F2.2 to F2.4) and does not exist yet.** What ships today is
the contract, the Harvester, and a *fixture* adapter that produces estates shaped like the
§3.4 worked example. The fixture is enabled only where `ASTRA_ENV=local` (or
`ASTRA_ENABLE_FIXTURE_ADAPTER=1`); a deployment with no adapter enabled says so rather than
appearing to work.

```bash
curl -sS -X POST localhost:8080/v1/harvests -H 'content-type: application/json' -H 'X-Astra-Principal: user:pm@artizent.example' -H 'X-Astra-Roles: programme_manager' -d '{"site":"rqa","credential":"tableau/rqa"}'
```

**A request names a credential; it never sends one.** `"credential": "tableau/rqa"` is a
reference the service resolves — a secret in a request body ends up in the API log, the
request trace and someone's shell history. Local and CI resolve from the environment
(`ASTRA_CREDENTIAL_TABLEAU_RQA`); Key Vault arrives with E11.

Progress is per project and readable while the run is going:

```json
{
  "state": "COMPLETED",
  "totals": {"queued": 40, "parsed": 40, "skipped_unchanged": 0, "held": 0, "failed": 0},
  "parse_quality_p50": 1.0,
  "projects": [{"project": "Project 0", "queued": 14, "parsed": 14, "failed": 0}]
}
```

| Outcome | Meaning |
|---|---|
| `parsed` | Written to the graph |
| `skipped_unchanged` | Same content hash as last time, so a no-op (spec §8.4) |
| `held` | Parsed and written, but below the parse-quality threshold, so held for review (spec §4.1.4) |
| `failed` | Listed with its error at `/v1/harvests/{id}/failures`; the run continues |

A re-harvest of an unchanged workbook writes nothing and emits no events. A *changed*
workbook updates the same nodes rather than duplicating them, because a node's id is
derived from its identity in the source rather than issued at random.

## Parse quality

Every harvested workbook carries a `parse_quality` on its node: the fraction of its source
constructs the adapter grammar could read, counting constructs an engineer has accepted as
ignorable.

    parse_quality = (recognised + ignorable) / total

`recognised`, `ignorable` and `total` are all kept, so grammar coverage and workbook
readiness stay separable — the Calibration Report wants the first, the queue the second.

A workbook below the threshold (0.98 by default, spec §4.1.4) is **held**: written to the
graph, but not clear to advance. `GET /v1/parse-quality/gate/{site}/{luid}` is the check
the Cartographer (E3) makes before clustering.

### The queue

Readable by workbook — what is held — and by construct — what to fix next:

```bash
curl -sS localhost:8080/v1/parse-quality/constructs -H 'X-Astra-Principal: user:p.eng@artizent.example' -H 'X-Astra-Roles: platform_engineer'
```

```json
{"construct": "RAWSQL_INT(<expr>)", "occurrences": 12, "workbooks_released_if_resolved": 12}
```

One grammar gap usually blocks many workbooks, so the queue is worked construct-first and
`workbooks_released_if_resolved` counts the held workbooks a single fix would clear.

### Resolving one

```bash
curl -sS -X POST localhost:8080/v1/parse-quality/constructs:ignorable -H 'content-type: application/json' -H 'X-Astra-Principal: user:p.eng@artizent.example' -H 'X-Astra-Roles: platform_engineer' -d '{"construct":"RAWSQL_INT(<expr>)","reason":"Redesigned per Appendix B; no DAX equivalent"}'
```

Accepting a construct needs a reason, applies to every occurrence, and **re-scores without
touching the source** — the constructs and counts are already stored. The decision is
carried forward across re-parses, so an engineer accepts a construct once, not once per
harvest.

Extending the grammar is the other route, and that one does need the affected workbooks
re-parsed; `harvest_workbook.grammar_version` records which grammar each was parsed under.

## Usage and ownership

Harvested with the workbook, because wave order is a business-impact question and a gate
request needs somebody to send it to.

**Usage** is stored at two grains over a trailing 90-day window: on the `Workbook`, and on
each `Worksheet` and `Dashboard`. A source that reports per-viewer detail — Tableau's is in
the `historical_events` admin views, which spec §6.2 marks "where available" — also gets one
`VIEWED_BY` edge per person, carrying that person's own `views_90d` as §4.1.2 defines it.
Where the source reports only the aggregate, the workbook total stands alone and no
`VIEWED_BY` edge is invented.

**Owners** become `User` nodes keyed on the identity the *source* knows — the UPN — never on
the directory id. Resolving somebody adds `directory_id` and `directory_resolved_at` to
their node; it does not move them, so no edge is ever orphaned by a resolution.

Entra ID resolution itself is E11's. Until it lands the resolver is null, nobody resolves,
and the listing says so rather than pretending:

```bash
curl -sS localhost:8080/v1/ownership/unresolved -H 'X-Astra-Principal: user:pm@artizent.example' -H 'X-Astra-Roles: programme_manager'
```

```json
{"unresolved": [{"upn": "owner@client.example", "licence_tier": "Creator", "owns": 40}], "count": 5, "resolver": "null"}
```

`owns` orders the queue, because it is the number of G3 gate requests (§13.1) that
currently have nobody to go to. Assigning by hand goes through the ordinary write path, so
the judgement emits an event and the principal who made it is on the record:

```bash
curl -sS -X POST localhost:8080/v1/ownership/assign -H 'content-type: application/json' -H 'X-Astra-Principal: user:pm@artizent.example' -H 'X-Astra-Roles: programme_manager' -d '{"site":"rqa","upn":"owner@client.example","directory_id":"6c1f...","display":"A Real Person"}'
```

Assigning over an existing link is refused: re-assigning is a different operation, and
conflating the two would let one request silently discard a resolution.

**Licence tier** is stored on the `Site` (`licence_tier`, `user_count`) and on each `User`
where the source exposes it. Today a person's tier arrives with their ownership record, so
a viewer who owns nothing has none — see ADR 0006 for why, and what E2 would change.

## Incremental harvest and schedules

A harvest runs in one of two modes. A **full** run fetches every workbook and compares
content hashes. An **incremental** run asks the enumeration when each workbook last changed
and never downloads the ones that have not moved — which over a long programme is the
difference between a nightly run costing a thousand downloads and costing four.

The API defaults to full, because an operator asking by hand usually means "look properly".
Schedules are always incremental.

```bash
curl -sS -X POST localhost:8080/v1/harvest-schedules -H 'content-type: application/json' -H 'X-Astra-Principal: user:p.eng@artizent.example' -H 'X-Astra-Roles: platform_engineer' -d '{"site":"rqa","credential":"tableau/rqa","cadence":{"daily_at":"02:00"}}'
```

Cadence is `{"every_minutes": N}` (floor of five) or `{"daily_at": "HH:MM"}` in UTC. A new
schedule does not fire immediately — the first firing is one cadence away, so setting up
four sites does not start four full harvests by typing.

A workbook is skipped **without being fetched** only when all of six things hold: it has been
harvested before, the source reports an `updatedAt`, the platform recorded one last time, the
revision has not moved, the timestamp has not moved, and the grammar is the one it was last
parsed under. The last is not decoration — without it, extending the grammar would never
reach a workbook that had not changed, and a workbook held by the Parse Quality Queue would
stay held forever.

The two skips are counted apart, because the difference between them is the whole saving:

| Counter | Meaning |
|---|---|
| `skipped_not_modified` | The source said it had not changed. Never downloaded. |
| `skipped_unchanged` | Downloaded, and the content turned out to be identical. |

**Schedules are rows, not cron entries.** They can be paused with a reason, amended in place,
and read off Platform Health with their last run. Due schedules are claimed with
`FOR UPDATE SKIP LOCKED`, so replicas share them rather than each running all of them. A
schedule that fails five times running pauses itself with the last error as its reason.

There is no delete. Pausing keeps the history; amending keeps it attached to its scope.

The scheduler runs in-process, one loop per replica, polling every `ASTRA_SCHEDULER_POLL_SECONDS`
(30 by default). Set `ASTRA_SCHEDULER_ENABLED=false` to run the API and the scheduler as
separate deployments. Durable orchestration is Temporal's (E12/F12.1) — the schedule row is
the durable part, so a restart loses the loop and not the plan.

## Source drift

When a workbook's content changes under a Migration Unit that is already past HARVESTED, the
harvest raises `estate.source.drift` and asks for the MU to be re-proved. Something has been
built from a version of that workbook that has stopped being true, and the Arbiter (E7) is
the thing that has to know.

The notice is the first event in the outbox that records no graph change. It shares the
stream rather than getting a table, because the bus is one ordered sequence and a consumer
needs to know the notice comes after the upserts it followed; replay skips it and counts it.

Two conditions, both required: the content actually changed — a first harvest is not drift,
and neither is a re-publish of identical bytes — and an MU exists in a state where work has
been done. At HARVESTED the re-parse *is* the update, so nothing is announced. RELEASED is
deliberately in scope; a source changing under a report already in production is the
expensive case.

Migration Units arrive with the Cartographer (E3). Until then `MigrationUnitRegistry` resolves
nothing, no drift can be raised, and `GET /v1/platform/health` reports
`"migration_units": "none"` rather than looking quiet and healthy.

## Platform Health

What this service contributes to the screen in spec §15.3.3 — the rest arrives with the epics
that own it:

```bash
curl -sS localhost:8080/v1/platform/health -H 'X-Astra-Principal: user:p.eng@artizent.example' -H 'X-Astra-Roles: platform_engineer'
```

Adapter and its capabilities, the scheduler and its last tick, every schedule with its
cadence, next firing and last run, recent harvests with the mode each ran in, and recent
source drift. Every section degrades to a stated absence rather than an error: a deployment
with no adapter enabled is a real condition, and a screen that 500s because nothing is
scheduled tells an engineer nothing.

`/health` stays what it is — a readiness probe cheap enough to call every few seconds.

## Context contracts

Agents do not get raw graph dumps (spec §4.1.3). Each declares the sub-graph it needs for
one unit of work, and one shared assembler materialises exactly that, canonically, with a
sha256 over it.

A contract is **a GraphQL fragment plus a resolution plan plus a budget**:

* the **fragment** says which fields. It is validated against the schema generated from the
  ontology, so a contract naming a property the ontology does not declare fails `make ci`
  rather than a caller. It is also the §18.3 inference boundary — what is not in the
  fragment cannot reach a model, whatever the graph holds — which is why the fragments are
  published rather than internal;
* the **resolution plan** says which nodes. GraphQL cannot express "the transitive
  DEPENDS_ON closure", so the traversal is code and the fragment stays flat;
* the **budget** says how large the result may get.

```bash
curl -sS localhost:8080/v1/contexts/transpiler_calc/2V9GWD9DF26YF3C8960J00TVJY -H 'X-Astra-Principal: user:p.eng@artizent.example' -H 'X-Astra-Roles: platform_engineer'
```

```json
{"context_hash": "sha256:bfbff8da…", "usage": {"size_bytes": 661, "node_count": 3, "budget_bytes": 262144}, "document": {"…": "…"}}
```

### The hash

`context_hash` is what §4.2 records in every provenance record and what §5.4's gateway
caches on, so it has to be reproducible. Canonical means sorted keys, no insignificant
whitespace, Unicode as text, NaN refused — and **every collection sorted by id**, because
PostgreSQL may return rows in a different order between two identical queries and a hash
that depended on that would fail intermittently.

Audit metadata is deliberately outside every contract. A workbook re-harvested with nothing
changed gets a new `updated_at`; if that were in the context, the gateway cache would never
hit and no provenance record could be checked by re-assembling. Node ids are derived from
source identity, so they survive a re-harvest and the hash does too.

### Budgets

A contract that exceeds its declared budget **fails with 413**. It does not truncate: an
agent cannot tell a shortened dependency closure from a complete one, and would generate
confidently from a partial picture while the provenance record carried a hash of the
fragment as if it were the whole.

### The Transpiler contract

§4.1.3 specifies it in full — one CalculatedField, its transitive DEPENDS_ON closure, the
Parameters it references, the target ModelTable columns those fields MAPS_TO, and the
Patterns whose `source_signature` matches its AST shape — and it is implemented to the
letter, including the last two words: *nothing else*. `tools/contract_check.py` prints
every field it sends, so widening the boundary shows up in the build log.

Pattern matching computes the §4.3 shape string — `DIV(SUM(a), SUM(b))` — from the
calculation's AST, abstracting leaf identifiers to capture names and literals to type
placeholders, and matches on equality. Ranking, fuzzy matching and promotion stay with the
Pattern Library (E5). RETIRED patterns are never offered.

Other agents' contracts are declared with their agents: the Modeller's by E4, the
Compositor's by E6, the Mender's by E8. The assembler takes them without change.

## Graph versions and verifiable provenance

A **graph version is an event offset**. S1.1.3 made the outbox the record — every mutation
committed with its event, and a replay from empty reproducing the graph exactly — so the
stream up to sequence *n* already determines the graph at *n*. There are no snapshots and
no second identifier: the number an auditor quotes is one they can look up in the event
stream this service publishes. Version zero is the empty graph.

```bash
curl -sS localhost:8080/v1/graph-versions/current -H 'X-Astra-Principal: user:a@artizent.example' -H 'X-Astra-Roles: platform_engineer'
```

### Why a provenance record carries one

§4.2's record has `context_hash` and no version. That is not reproducible: the graph moves,
so re-materialising the same contract a week later gives a different hash with no way to
tell a real mismatch from an ordinary re-harvest. `inputs.graph_version` is a declared
extension, and it is the difference between a record that describes and one that verifies.

### Verifying

```bash
curl -sS -X POST localhost:8080/v1/provenance/prov_01M1HK…:verify -H 'X-Astra-Principal: user:auditor@client.example' -H 'X-Astra-Roles: platform_engineer'
```

```json
{"outcome": "MATCH", "claimed_context_hash": "sha256:f454…", "recomputed_context_hash": "sha256:f454…"}
```

Nothing stores the document. The verifier re-runs **the same assembler** over the graph as
it stood at the recorded offset — `HistoricalGraphReader` satisfies the same `ContextReader`
protocol the live repository does, so there is no audit code path whose correctness would
be what the answer really depended on.

Three outcomes, and the last two are never conflated:

| Outcome | Meaning |
|---|---|
| `MATCH` | Re-materialised, and hashes to the recorded value. |
| `MISMATCH` | Re-materialised, and hashes to something else. The record is wrong or the stream was altered. |
| `UNVERIFIABLE` | Could not be re-materialised — the subject did not exist yet, or the record cites a version beyond this graph. |

A failed verification is a **200 with a finding**, not an error status. `?include_document=true`
returns the re-materialised context itself. `POST /v1/provenance:verify` takes the claim
rather than a record id, so an artefact store that holds its own provenance (§5.2 gives that
to artefact-svc) can use the audit path without this service holding the record.

Recording does not verify. Recording is what an agent does when it produces an artefact;
verifying is what an auditor does later, and folding them together would let a verification
failure stop the artefact being recorded — losing the evidence that something went wrong.

### How history is read

Not by replaying the stream: over a programme that is millions of events per audit. Each
event carries its element's complete post-write state, so a node's state at a version is its
latest upsert at or below that offset — one indexed lookup — and a closure is that iterated
to a bounded depth. Measured at **~55 ms against an 11,346-event stream**.

That is a second implementation of "what the graph held", so the integration suite replays
the stream to a version, reads the same version through the indexed reader, and requires
identical context hashes. Replay is the definition; the reader is a shortcut to it.

### Retention

Programme lifetime plus twelve months, computed rather than configured.

```bash
curl -sS localhost:8080/v1/retention -H 'X-Astra-Principal: user:pm@artizent.example' -H 'X-Astra-Roles: platform_engineer'
```

`prunable_before` is null unless every programme has closed *and* the earliest close plus
twelve months has passed. Two states that look like permission are not: an open programme
holds everything, and no programme recorded holds everything too — an empty table is not
permission to delete. Closing a programme starts the clock and cannot be undone or re-dated.

**Nothing prunes.** There is no pruner and no TTL; `/v1/retention` reports
`pruning_implemented: false` rather than implying a job that does not exist. What exists is
the policy any future pruner has to ask.

## The Estate Explorer's reads

`GET /v1/estate` answers the whole screen (§15.3.2, S1.4.1): the site → project tree with
counts and parse status, the filtered workbook page, and the facet counts. One request,
because the screen needs all three at once and all three come from one read — three
endpoints would triple the work and let the facet counts disagree with the rows beside them.

The read is **four queries, none of them per workbook**: every Workbook node from the label
table, the CONTAINS edges that place them plus their Projects and Sites, the OWNED_BY edges
that name their owners, and a relational count of each workbook's calculated fields.
Filtering, banding and facet counting then happen in one pass in memory.

**Measured: 308 ms median over a 1,067-workbook estate, against S1.4.1's 2 s budget.**

A facet's count is computed against the set filtered by everything *except* that facet, so
the number beside an option means "how many would I get if I picked this" rather than
echoing the current selection back.

The calculation count is deliberately two hops — `Workbook -CONTAINS-> Worksheet -ENCODES->
CalculatedField` — because §4.1.2 gives CONTAINS no Workbook→CalculatedField pair. A
one-hop count returns nought for every workbook in every estate, which is what it did until
somebody looked at the screen.

### What the estate cannot answer yet

§15.3.2's centre pane also asks for score, family, train, state and class mix. Those are
Migration Unit properties (E3) and Transpiler output (E5). The response carries them by
name in `pending_columns` and `facets.pending`, each with the epic that fills it, so the
console renders an explained absence rather than an empty dropdown.

## Scope decisions

Re-tier and withdraw change what the programme has committed to deliver, so both are
Programme Manager only and both require a reason of at least ten characters (§15.2: "every
action is a record ... with a reason field that is required, not optional").

```bash
curl -sS -X POST localhost:8080/v1/estate/workbooks/01ARZ…:withdraw -H 'content-type: application/json' -H 'X-Astra-Principal: user:pm@artizent.example' -H 'X-Astra-Roles: programme_manager' -d '{"reason":"Superseded by the Treasury liquidity pack"}'
```

A tier is **not** a property on Workbook, and §4.1.1 declares none for a reason: a tier is a
judgement the programme made, not a fact about the client's estate. Writing one onto the
node would put a decision inside the record of what was found. So decisions are their own
rows and the current state is a fold over them — nothing sits beside them that could
disagree.

Withdrawal is not retirement. The harvest found the workbook and the estate keeps saying so;
withdrawal only stops it counting as work, and it can be reinstated. A withdrawal that
cannot be undone is a deletion with extra steps.

These exist before the Migration Unit does. Tier and withdrawal are MU properties (§3.1,
§3.2) and the Cartographer creates the MU in E3 — but a programme manager looking at a
freshly harvested estate already has judgements to record, and losing them until E3 ships
would mean asking for them twice. The MU inherits them.

## Shared lineage

`GET /v1/lineage` answers the Lineage View (§15.3.2, S1.4.2): workbooks, the tables and
fields behind them, and how much any two share — for a family or a selection.

**The strength is §12.1's formula**, term for term:

    0.5·J(tables) + 0.3·J(fields) + 0.2·shared_calc_shapes / max_calc_shapes

where a workbook's tables are what it reaches through
`Worksheet → Datasource → Connection → Table`, its fields are what its worksheets encode,
and its calculation shapes are the AST shapes of its calculated fields — normalised by the
same function the Pattern Library matches on (S1.3.1), so "these two share a calculation
shape" means the same thing in both places.

### The Cartographer's numbers win

`SHARES_LINEAGE` edges are written by the Cartographer (E3). Where they exist, the view
shows **them** — they are the numbers the clustering actually used, and an engineer
challenging a family needs the evidence that produced it, not a second opinion that happens
to be close. Where they do not, the same formula is computed read-only from the same inputs
and `shared_lineage_origin` says `computed` rather than `graph`.

Nothing is written either way: this read proposes no families, applies no threshold and
makes no decisions.

### Finding the pairs

An inverted index over **all three** inputs, so pairs that share nothing are never scored.
All three matters: two workbooks that share every calculation shape and no lineage score
0.2, which clears the default threshold — indexing only tables and fields silently drops a
link the formula says exists.

The scope is capped at 250 workbooks. §15.3.2 scopes the view "for a family or a
selection"; beyond a few hundred a force-directed graph is a hairball whatever the layout,
and the pairwise scoring is bounded by the same figure.

**How the cap is applied depends on who chose the scope.** When a scope was asked for, the
result is truncated to the cap and `truncated` says so — the reader picked the scope, and
narrowing it further underneath them would be worse than telling them it did not fit. When
no scope was asked for, truncating would silently make "the estate" mean "whatever the query
returned first", which is alphabetical in practice. So an unscoped call over a large estate
narrows to the **largest single site** and names it in `auto_scoped_to`, which the console
shows. A truncation the reader can see is a different thing from one they cannot.

## The source adapter

`graph-svc` knows nothing about Tableau. Everything it knows about a source, it knows through
the §6.1 `SourceAdapter` contract — which lives in its own package, `astra-adapter-sdk`
(`packages/adapter-sdk`), and which this service consumes on exactly the terms an adapter
author does. That is what makes "a second source can be added without changing the platform"
(E2) checkable rather than intended; a test in the SDK asserts nothing under `astra_adapter`
imports `astra_graph`.

`astra_graph.adapters.contract` and `.fixture` are re-export modules so the platform's own
imports still read as platform imports.

**Which adapter this deployment harvests through**, in order:

1. `ASTRA_ADAPTER_URL` — an adapter worker, spoken to over the §6.1 RPC. This is the deployed
   shape (§5.2 makes an adapter a worker, §5.4 runs it as its own pod, §6.1 packages it as a
   versioned image). When the Tableau adapter lands (F2.2) it is configured here and nothing
   else changes: the Harvester is written against §6.1 and cannot tell the difference. A test
   asserts a harvest through a `RemoteAdapter` produces the same counts, the same parse
   quality and the same adapter record as the same harvest in process.
2. The **fixture adapter**, in process, when `ASTRA_ENV` is `local`. Not the Tableau adapter.
3. Nothing. The harvest endpoints say so rather than inventing an estate.

Every harvest records the adapter's name, version, grammar version and **interface version**
(S2.1.1), and a harvest is refused if the interface version is blank — the record's purpose
is to let a harvest be read months later against the contract that produced it, and a
versioned interface whose version is blank is not one.

### Promotion: which adapter this tenant will let near its estate

§6.1 says an adapter "must pass the conformance suite in §6.3 before it can be enabled on a
tenant". S2.1.2 makes that a check rather than a sentence.

The suite runs **outside** the platform — `astra-adapter conformance --adapter tableau
--remote --out report.json` needs the adapter image, not the tenant — and the signed report is
recorded here. Promotion is then refused unless *this exact build* has a passing one:

- **No report.** Nothing is known about the adapter, which is not the same as knowing it is
  broken, and the message says which.
- **A failing report.** The refusal names the checks that failed.
- **A report for another build.** Name, version, interface version and grammar version are
  compared together — the likeliest route to promoting untested code is a version bump for a
  "small fix" on the strength of the previous version's report.

The gate is enforced in the Harvester, not in the endpoint, because a harvest can be started
by a schedule as well as by a request and a gate that only covered the endpoint is one a
nightly run walks around. The **fixture adapter is exempt**, by name, in
`harvest_setup.UNGATED_ADAPTERS`: it generates its own estate and reaches no client system, so
gating local development on the ceremony would protect nobody. A real adapter is never exempt,
and Platform Health says which of those a deployment is in.

Reports are stored whole — the checks that ran, what they found, the corpus — because "the
adapter passed" is not evidence. A failing report is kept for the same reason: it is why a
promotion was refused. §5.2 gives object storage to `artefact-svc`, which does not exist; the
record lives behind a port here and moving it changes one adapter.

## The artefact store

Content-addressed binary artefacts — a source view's screenshot today (S2.4.2), an evidence
bundle or a report thumbnail later — behind `ArtefactStore`, the same "§5.2 gives this to
`artefact-svc`, which does not exist" answer as provenance and conformance above.

One table, not one per kind: `kind` names what an artefact *is*, and the shape a binary
artefact needs — content, a hash, a size, who produced it, what it is linked to — does not
change with what is inside it. The link is `mu_ref`, a name rather than a foreign key: E3 has
not created a Migration Unit table yet, so callers pass the workbook LUID until it does (§3.1
makes an MU "one source workbook and everything the platform produces for it").

**A record never carries the bytes.** `GET /v1/artefacts/{id}` returns metadata — content
hash, size, dimensions, who produced it — and nothing that could hold a pixel. Only
`GET /v1/artefacts/{id}/content` returns bytes, for a viewer that already knows it wants an
image. This is S2.4.2's "never sent to a model endpoint" criterion, enforced structurally: a
future context contract that referenced an `ArtefactRecord` could not leak an image through it
even by accident, because there is no field to leak.

## The Cartographer

Clusters workbooks into candidate model families by shared lineage (spec §12.1, story
S3.1.1) — *"so that the ~150-model planning assumption becomes a measured number in Month 1."*

**The same arithmetic as the Lineage View, on purpose.** §12.1's similarity —
`0.5·J(tables) + 0.3·J(fields) + 0.2·shared_calc_shapes/max_calc_shapes` — was built for
S1.4.2's read-only Lineage View before this story existed. `cartographer.py` imports the
formula, its weights and the AST-shape normaliser rather than reimplementing them, so "the
evidence a family was clustered from" and "the evidence the Lineage View shows for it" stay
one set of numbers, not two that happen to agree today.

**The threshold is 0.55, not the backlog's 0.35** — the backlog's own rule is that the
specification is corrected when the two disagree, and at 0.55 the two figures are not even
in tension: a pair sharing zero tables can score at most 0.5 on fields and shapes alone, so
scoring only pairs that share a table (this story's own restriction) is an exact consequence
of the default, not an approximation of it. See [ADR 0022](../../docs/adr/0022-family-clustering-reuses-the-lineage-view-scoring.md).

**A family under the minimum size (default 3) is merged into its nearest neighbour or held
as `SINGLETON` with a reason** — repeatedly, not once: a merge that is still too small is
still "under the minimum" and is reconsidered, until every family clears the floor or
genuinely has nowhere left to merge into.

**Evidence is a snapshot, not a live query.** `ModelFamily.evidence_shared_tables` /
`_fields` / `_calc_shapes` and `.reason` are recorded at proposal time (declared as a spec
deviation — the backlog names them, §4.1.1 does not) so a later split or merge (S3.1.2)
cannot quietly rewrite the history of why a family was originally proposed.

**A re-run retires only what it owns.** Every run retires prior `ModelFamily` nodes in
`PROPOSED`/`SINGLETON` — states nothing but the Cartographer itself has ever put a family
into — before writing fresh ones. `DRAFT` and beyond are a human decision this module never
touches. `SHARES_LINEAGE` edges are upserted at a deterministic id per pair, so a re-run
replaces the evidence rather than duplicating it.

**`POST /v1/families:cluster` returns 202 immediately** and runs as a background task, the
same shape as `POST /v1/harvests` — the criterion's own "under 30 minutes" says a run can
take a while. No persisted run history: the durable result of the *last* run is
`clustering_json` on the programme record (migration v0012), which is what the acceptance
criterion asks for; `GET /v1/families:cluster/status` also holds it in memory for
convenience between polls.

**Two pre-existing gaps, found by pushing a real harvest shape through this for the first
time.** `ENCODES` (Worksheet→Field/CalculatedField) is declared in the ontology and read by
the Lineage View, but the Tableau adapter has never written it — worked around by reading
`Worksheet.rows_shelf`/`cols_shelf`/`marks_shelf` directly, since those already carry the
field names S2.3.2 resolved, and flagged as a follow-up rather than fixed here (it touches
F2.3's adapter code, not F3.1's). `HAS_FIELD` only permitted `Table→Field` and
`Datasource→Field`, even though the adapter has written `Datasource→HAS_FIELD→CalculatedField`
since S2.3.1 — meaning a real workbook with a calculated field could never actually be
harvested through the real write path. Fixed in the ontology (additive; see ADR 0022).

### Split, merge and move (story S3.1.2)

`family_overrides.py` — a model engineer's edits to a proposal, each recording who, when and
why. Split moves selected members out of a family into a new one, keeping the original id for
what remains; merge combines two families into a fresh one and retires both originals; move
relinks one workbook from its current family into another, auto-retiring a source left empty.
Grain and evidence are recomputed from the same `candidate_grain`/`family_evidence` the
Cartographer itself uses, read for only the workbooks the operation touches
(`gather_reach`) — an edit to three workbooks does not re-scan the estate.

**Overrides are preserved, not frozen.** Every family an override touches is marked
`ModelFamily.overridden = true`. A re-cluster reads that flag directly (see "A re-run retires
only what it owns" above) rather than through a second mechanism: an overridden family's
members are excluded from clustering entirely, and the family is left un-retired. What the
free clustering *would* have produced for those members is still computed — cheaply, since it
reuses the same `pair_scores` the applied run already has — and reported as `would_change` on
the result, never applied. Naming a family's id in `confirm_family_ids` on the next
`POST /v1/families:cluster` lifts its pin for that one run.

**Result families are always `PROPOSED`, never `SINGLETON`.** `SINGLETON` is the clustering
*algorithm's* own label for a family it could not responsibly grow; a human composing a small
family on purpose already made that judgement, and re-deriving the label here would
second-guess a decision this module has no business second-guessing.

**Edges can now be retired — a first for this service.** A "move" needs a workbook's
`IN_FAMILY` edge to point at a different family, and a property-graph edge's endpoints cannot
change once created — so it is retire-and-recreate, the same shape `retire_node` already gave
nodes (S1.1.3). `estate_edge_index` gained its own `retired_at` column (v0013) rather than
requiring every adjacency query to join the element index for it. See
[ADR 0023](../../docs/adr/0023-overrides-pin-not-freeze-and-edges-can-be-retired.md) for the
full reasoning, including a known gap: the general-purpose `neighbourhood`/`closure` graph
traversal does not yet exclude a retired edge — nothing in this service currently reads family
membership through that path, so the gap is real but inert, and is tracked as a follow-up
rather than folded into this story.

### Confirmed family count (story S3.1.3)

`POST /v1/programmes/{id}:confirm-family-count` — a Programme Manager's own reading of "the
family count", turning §14.3's "~150 shared governed models" planning assumption into a
measured, dated, attributed fact. The request body carries nothing: the count is
`cartographer.count_families`, every live `ModelFamily` regardless of state, read at the
instant of confirmation — never a number a caller types. `family_count`,
`family_count_confirmed_at` and `family_count_confirmed_by` are three plain columns on
`public.programme` (migration v0014), separate from `clustering_json` (S3.1.1): the two can
legitimately disagree, since `clustering_json` is only ever the *last run's* figures and
`family_count` is what a Programme Manager stood behind. `PLANNED_FAMILY_COUNT = 150` is a
spec constant; the delta is computed on read, never stored.

`GET /v1/programmes` lists every programme with both figures, for the console's Programme
Board pane. §14.3 also gives this figure to the Calibration Report — a document E13 has not
built yet, so this story writes the durable input that report will read rather than
inventing a report shape ahead of the epic that defines the rest of it. See
[ADR 0024](../../docs/adr/0024-the-family-count-is-measured-not-typed.md).

## Release trains

`trains.py` — a Programme Manager's release plan, proposed from the families and usage the
Cartographer already measured (§3.3, story S3.2.1). There is no Migration Unit graph node
(§4.1.1's own note on `Workbook` is "One Migration Unit per Workbook"), so "packing MUs"
here means packing Workbooks, and `IN_TRAIN` — declared since S1.1.1, unused until this
story — is the edge `POST /v1/trains:propose` writes.

**Families are ordered by the backlog's three named factors, not §8.5's reworded three.**
§8.5 prefers "high reuse, high usage and early-renewal sites" for train ordering; the third
factor needs a site licence-renewal date this platform never harvests, so this module
implements the backlog's own three instead — shared model readiness (a family's position in
its §12.2 lifecycle), usage (`Workbook.views_90d` summed per family), and tier mix (mean
complexity across whichever members a Programme Manager has tiered, S1.4.1). See ADR 0025
for why the specification does not win this particular disagreement.

**Packing is family-atomic.** §3.3's reason for a train to exist is that each family is
designed and approved once inside it — splitting one across two trains would design it
twice. A family bigger than a train's remaining room lands there whole anyway; families left
over once every configured train has taken its share land in the last one, because every
Workbook must end up `IN_TRAIN` somewhere.

**`BLACKROCK_DEFAULT_TRAIN_SIZES = (277, 328, 184, 177, 101)`** is the backlog's own worked
example, reproduced verbatim — no derivation for it exists anywhere in the spec. Sizes,
start date and per-train duration are all editable per proposal.

**Gate schedule is a planned window, not a projection.** No gate has a train as its subject
(§13.1 gates a family at G2, a Migration Unit at G3) — `ReleaseTrain.gate_schedule` (new
JSON property, schema version 11, additive — no migration needed) stores the simplest honest
roll-up: G2 near the train's planned start, G3 near its planned end. A throughput-based
projection of the train's own end date — not the gates — is story S3.2.3, below.

**A re-run replaces every train it wrote — except one the Wave Board has since edited**
(`ReleaseTrain.overridden`, story S3.2.2's own pinning, mirroring `ModelFamily`'s exactly):
retiring every live un-overridden `ReleaseTrain` and every live `IN_TRAIN` edge first is
what keeps a workbook `IN_TRAIN` exactly one train at a time across repeated proposals.

See [ADR 0025](../../docs/adr/0025-trains-pack-families-not-workbooks.md) for the full
reasoning, including why no `Wave` node is written (declared since S1.1.1, still waiting for
the story that needs it) and why `ReleaseTrain.mu_refs` is left unpopulated (`IN_TRAIN`
edges are the only source of truth, the same way `IN_FAMILY` edges are for `ModelFamily`).

### The Wave Board (story S3.2.2)

`train_overrides.py` — a Programme Manager's edits to a proposed plan: move an MU into a
different train, resequence one within its current train, or configure a train's WIP caps.
Every action marks the train(s) it touches `overridden` (see above), the same pinning
mechanism S3.1.2 built for `ModelFamily`, reused verbatim.

**A move that would split a family across trains is refused outright — never a warning.**
§3.3's whole reason for a train to exist is that a family is designed once inside it; a move
succeeds only when every other member of the moved workbook's family is already in, or is
also moving to, the destination train. One consequence worth stating plainly: a
multi-member family cannot be relocated through this board one MU at a time — moving one
while a sibling stays behind always fails the check, in every order, so there is no
sequence of single-MU moves that reunites a family elsewhere. See ADR 0026.

**A move that would exceed a configured WIP limit is different — a warning, not a block.**
Called without a reason, an over-limit move is refused, naming the limit and the count;
called again with one, it proceeds and the reason lands on the new `IN_TRAIN` edge
(`wip_override_reason`, additive). WIP limits themselves (`ReleaseTrain.wip_limits`, JSON:
`{"train": <int|null>, "states": {<state>: <int>, ...}}`, additive) are config a Programme
Manager sets, always with a reason — a deliberate act, unlike a move that only needs one
when it actually trips a limit.

**A card's kanban column is its `IN_TRAIN.state`** — a plain string, not an enum (matching
`migration_units.py`'s own choice to hold `MU_STATES` as strings, since the state machine's
definition belongs to the control plane, not this ontology). Set once, to
`DEFAULT_MU_STATE` (`CLUSTERED`), when a train is first proposed; a move or resequence
carries the existing value forward, and neither ever changes it — see ADR 0026 for why this
story does not build §3.2's fifteen-state transition graph.

**"Every change is an event and appears on the Programme timeline"** needed no new
`EventType` — every write here goes through the same `GraphWriter` every other story uses,
so it already emits a CloudEvent. `GET /v1/trains/{id}/events` is the one piece of new
surface: `trains.train_event_subjects` resolves every `IN_TRAIN` edge (live or retired) that
has ever pointed at a train, plus the train's own node id, and filters a bounded recent
window of `GET /v1/events` down to that set — a recent-activity view, not a full audit
archive (`GET /v1/events?subject=` already serves deep history for one element).

See [ADR 0026](../../docs/adr/0026-the-wave-board-does-not-model-the-state-machine.md) for
the full reasoning, including why the backlog assigning "the Wave Board" to both this story
and S10.2.1 is a deliberate split (this story ships the mechanics; S10.2.1 later gives it a
wider frame), not an unresolved conflict.

### Projected versus planned dates (story S3.2.3)

`train_projection.py` — a bottleneck estimate of each train's finish date, from measured
throughput, so a Programme Manager sees slippage before a status meeting does.

**Throughput is mined from the real event stream, never simulated.** A `LAG() OVER
(PARTITION BY subject ORDER BY seq)` window function over `IN_TRAIN`'s own
`estate.edge.upserted` history finds genuine state *transitions* (an edge re-upserted for an
unrelated reason — a Wave Board resequence — carries its state forward unchanged and is
correctly never counted). Because nothing in this codebase yet drives an MU through §3.2's
transition graph (the wave scheduler, §14.2, backlog S12.1.2, is not built — see ADR 0026),
measured throughput is honestly zero for every state in every real estate today, and every
train's projection today honestly reads "insufficient data" rather than a fabricated date —
the same discipline `NullDirectoryResolver` and `NullMigrationUnitRegistry` established.

**A projection is a bottleneck estimate, not a full discrete-event simulation of §3.2.** For
each train, every state its members occupy *today* gets its own `remaining / daily_mean`
days-to-clear estimate; the train's projected finish is the slowest of those. It does not
walk each MU through every hop still ahead of it — see
[ADR 0027](../../docs/adr/0027-projection-is-a-bottleneck-estimate-from-measured-throughput.md)
for why a full simulation over data this sparse would look more precise while being less
trustworthy.

**Throughput is measured in calendar days; the lateness flag is decided in working days** —
the story's own two units, kept apart rather than converted at every intermediate step so
they can never quietly disagree. A confidence band comes from the same 14-day series'
`mean ± stddev` throughput — no second statistical model — and its pessimistic bound is
reported absent (`null`), never an infinite or fabricated date, when `mean - stddev <= 0`.

**`GET /v1/trains:projections`** takes `trailing_days` (default 14), `late_threshold_working_days`
(default 5), and a testing-only `now` override (mirroring `POST /v1/trains:propose`'s own
`start_date`) — real callers omit it and get today. A train misses its planned date by more
than the threshold: `flagged: true`, surfaced on the Programme Board's "Projected vs.
planned" pane (and as a small badge on each Wave Board column).

See [ADR 0027](../../docs/adr/0027-projection-is-a-bottleneck-estimate-from-measured-throughput.md)
for the full reasoning.

## The Modeller

`modeller.py` — a model design proposal generated from the graph for one `ModelFamily`
(story S4.1.1, §8.6). `ModelTable` and `SemanticModel` (declared, unused, since S1.1.1) get
their first writes.

**Every hop reuses `lineage.py`'s `children`/`hydrate`** — the same primitives S3.1.1's
Cartographer promoted to module level, so a family's members' datasources, connections,
tables and calculated fields are read the same way everywhere in this codebase, not by a
second traversal that can drift from the first (ADR 0022's own reasoning, applied again).

**Tables are deduplicated "by connection + table" for free** — a `Table` node's identity is
already derived from its connection, name and schema at harvest time, so the *set* of table
ids a family's members reach is the deduplication.

**Storage mode and relationship cardinality are named heuristics, disclosed as such.**
Neither is knowable for certain from an estate that carries no primary-key metadata:
storage mode follows `Datasource.extract_flag` and `Table.row_estimate` (Import for a
human-scale extracted table, Direct Lake past 50,000,000 rows, DirectQuery for a live
connection); cardinality follows a row-estimate ratio, confident at 3x or more and reported
`ambiguous` — an open question, not a guess — below it. Every recommendation carries its own
`reason` naming the figure that decided it.

**Candidate measures are deduplicated by AST shape** (`context.signature.ast_shape`, the
Pattern Library's own normaliser) — the same computation, read the other way, finds
"duplicate measures with different definitions": a name shared by two calculations with
*different* shapes becomes an open question, not a silent pick of either.

**RLS is read, not re-derived** — `Workbook.rls`/`.rls_expression` (S2.3.2) already say
which member workbooks restrict rows and how; a shared expression becomes one role.

**No `Measure` node is written.** `Measure.dax`/`.provenance_ref` are required — "the
Transpiler's product" (E5, not built) — so a candidate measure with no DAX lives in
`SemanticModel.design_document` alongside relationships, conformed-dimension sharing,
refresh policy and open questions: everything this proposal needs that has no first-class
graph shape yet. `grain_statement`, `design_generated_at` and `design_provenance_ref` stay
separate scalar properties on `SemanticModel`.

**Naming and the grain statement are drafted deterministically today.** No Model Gateway
exists (§5.5, not built) — the same position every other "not built yet" seam in this
codebase is in. A real `ProvenanceRecord` is still written for the grain-statement draft
(`mode: ASSISTED`, `model: null` — honest about what actually produced it), reusing S1.3.2's
provenance machinery directly rather than a new mechanism.

**A re-run replaces the whole proposal — until a family is accepted.** There is no pin to
respect while nothing can edit a proposal; once story S4.1.2's Model Detail screen makes
editing possible, `Modeller.run` refuses outright rather than silently discarding an
engineer's edits (`PRE_ACCEPT_STATES`) — see that story's own section below.

See [ADR 0028](../../docs/adr/0028-the-modeller-proposes-with-disclosed-heuristics.md) for
the full reasoning, including why relationships default every joined table to its
connection's biggest table (§12.3's own star-schema conformance rule makes that the right
default independently of what Tableau's real join graph looked like).

### Model Detail and the G2 state machine (story S4.1.2)

`model_lifecycle.py` — editing a generated design proposal and moving a family through
§12.2's state machine (`PROPOSED → DRAFT → IN_REVIEW → APPROVED → BUILT → PUBLISHED`).

**The whole transition graph is declared; this story drives two edges of it.** "Engineer
accepts" (`PROPOSED`/`SINGLETON` → `DRAFT`) and "engineer submits" (`DRAFT` → `IN_REVIEW`)
are this story's own actions — approve/request-changes is the data owner's G2 action
(backlog S4.2.1, not built), deploy is the Steward's (S4.3.1, not built), promote has no
story yet. `FAMILY_TRANSITIONS` declares the full legal graph anyway, so each of those
future actions calls the same `require_transition` and gets the same enforcement rather
than a second, drifting copy of §12.2's rules.

**"Transitions and their actors recorded" reuses the event log.** Every `ModelFamily`
upsert is already a real CloudEvent (S1.1.3) carrying `updated_by`/`updated_at`;
`family_transition_history` finds genuine `state` changes with the same `LAG() OVER (ORDER
BY seq)` technique `train_projection.py` (S3.2.3) uses for `IN_TRAIN.state` — no new audit
table. A family's own creation (`NULL → PROPOSED`) is reported as the first transition,
deliberately, not filtered out as a special case.

**Submitting to `IN_REVIEW` freezes `SemanticModel.version`** as a real content hash —
`context.canonical.canonical_json`/`context_hash` over `modeller.read_design_document`'s
own canonical read, with `design_generated_at` and any prior `version` excluded so re-
reading an unchanged design always reproduces the same hash.

**Editing is three targeted actions, not a general PATCH**, matching every other write in
this API (`:verb`-suffixed POSTs): the grain statement, a table's storage mode, and one
relationship's cardinality — the fields ADR 0028's own disclosed heuristics most need a
Semantic Model Engineer to correct before a client sees the proposal. Every edit, and both
driven transitions, requires `state == DRAFT` and the Semantic Model Engineer role.

**A real S4.1.1 bug surfaced while wiring these edits**: `Modeller._write` generated a
*second*, independent id for each `ModelTable` node at write time, different from the one
already on the `TableCandidate` its own response reported — fixed by generating each
table's id once and threading it through both the write and the relationship candidates'
own table references. See ADR 0029.

The Model Detail screen (`/models` in the console) is this story's console surface — five
tabs (Design, Measures, RLS, Open Questions, Build), matching the backlog's own list rather
than §15.3.2's fuller six (which adds "Versions", nobody's story yet). No Foundry Workbench
exists to navigate from, so the screen carries a minimal family list of its own.

See [ADR 0029](../../docs/adr/0029-the-state-machine-is-declared-whole-driven-in-part.md)
for the full reasoning.

### The G2 workflow (story S4.2.1)

`g2.py` — a data owner's review: approve, request changes, or ask a question about a
design frozen at `IN_REVIEW`.

**Questions are a platform table, `public.g2_question`** — the same footing `grammar_issue`
(S1.4.3) already established for "raised as work, tracked with state, evidence copied in at
the moment it was raised." §4.1's ontology has no node for a review thread; the thread
itself is one `jsonb` column, since nothing ever queries one message on its own. A design's
own `open_questions` (from `design_document`) are promoted into tracked, answerable rows
the moment `submit-for-review` freezes a version — the route orchestrates both modules'
writes, since `g2.py` imports `model_lifecycle.require_transition` and never the reverse.

**`GateDecision` gets its first write ever** (declared since S1.1.1). §13.3's own worked
example nests `approver`/`countersign` as objects; this ontology is flat everywhere, so
`approver_role`/`countersigner`/`countersigner_role`/`version_hash` are their own
properties instead. Approving requires: no open question for the family, a frozen version,
a named countersigner, and — for a family whose domain has been assigned
(`ModelFamily.domain`, also unwritten before this story; `:edit-domain`, DRAFT-only, is the
first thing that can) — the caller's asserted `X-Astra-Domain-Scope` header covering it. An
*unset* domain is never refused: nothing assigns one automatically yet, a disclosed gap.

**Request-changes stores the cycle count** (`ModelFamily.g2_cycle_count`, incremented once
per round) and returns the family to DRAFT with the comment attached to a `GateDecision`
(`decision: CHANGES_REQUESTED`).

**The client view (`GET /v1/families/{id}/proposal`) is a different document from Model
Detail's**, not the same one filtered by role — §15.2: "client surfaces are calm... platform
detail is Artizent-only by default." It renders a deterministic plain-language summary
(table/measure counts, whether RLS applies, refresh cadence — facts the design already
states, not an ASSISTED draft), the family's member workbooks as "what reports use it," and
open questions with owner and status. Neither this route nor `GET /v1/families/{id}/
questions` is `ArtizentDep`-gated — a data owner is exactly who this data is for.

See [ADR 0030](../../docs/adr/0030-g2-questions-are-a-platform-table-domain-scope-is-asserted.md)
for the full reasoning.

### G2 cycle time and reminders (story S4.2.2)

`g2_reminders.py` — the Programme Board's own view of G2: `GET /v1/families:awaiting-g2`
returns every family currently `IN_REVIEW`, worst-waiting first, with days waited, its
approver and open-question count; `POST /v1/g2/reminders:send` records and sends whichever
3- and 5-day reminders are now due. Both are `ArtizentDep`, the same posture the board's
other reads already have.

**Days waiting reuses `family_transition_history`'s own event-log technique** (S4.1.2) —
`_entered_review_at` asks the same `LAG()` window for the most recent genuine move into
`IN_REVIEW`, and `train_projection.working_days_between` (S3.2.3) turns it into a
working-day count rather than a second implementation of the same math.

**"The approver" is `ModelFamily.owner`** — declared since S1.1.1, never written until this
story, the identical trajectory `domain` took at S4.2.1. `model_lifecycle.update_owner`
(DRAFT-only, mirrors `update_domain`) is the first thing that can set it; because it is
DRAFT-only and this tile only ever lists `IN_REVIEW` families, an owner has to be assigned
*before* a family reaches the board — one left unassigned shows as `unassigned`, not
defaulted to anyone.

**Reminders are recorded and logged, not delivered.** No email, chat or webhook channel
exists anywhere in this codebase (§21's own posture); `NotificationChannel`/
`LocalNotificationChannel` is the exact `IssueTracker`/`LocalIssueTracker` precedent
(`grammar.py`, S1.4.3). What is real is `public.g2_reminder`: one row per `(family, day)`,
unique, so the send action is safe to call from anywhere — the Programme Board's own "Send
reminders" button, today — without ever sending the same threshold twice.

See [ADR 0031](../../docs/adr/0031-g2-days-waiting-reuses-the-event-log-reminders-are-recorded-not-delivered.md)
for the full reasoning.

### Build: TMDL emission and the target adapter (story S4.3.1)

`tmdl.py` + `build.py` — an approved design built as code the moment it is approved.

**TMDL emission is a pure function of the frozen design**, `tmdl.emit_tmdl(document)` —
model.tmdl, one file per table, `relationships.tmdl` (parsing each `join_clause` for its
column names, matched by table name rather than position), `roles/*.tmdl` and a hidden
`_Measures` table, only ever emitted when there is real content. No graph read, no clock,
no random id — "the same version always produces byte-identical TMDL" (S4.3.1's own words)
is true by construction. Column-level detail and measure DAX are disclosed gaps (no
`Field`/`MAPS_TO` data in the design, no Transpiler yet), stated in the emitted TMDL itself
rather than fabricated.

**A new `TargetAdapter` contract** (`astra_adapter.target_contract`) mirrors `SourceAdapter`
(§6.1): `commit`, `deploy`, `smoke_query` — everything §7.1 assigns to the target system
rather than the Modeller. `FixtureTargetAdapter` (`astra_adapter.target_fake`) is the one
implementation this deployment has today: `commit` is genuinely real (Dulwich, a
pure-Python Git implementation, against a local repository); `deploy` materializes the
committed tree into a local "workspace" directory; `smoke_query` checks a table's TMDL
actually landed there. No live Fabric tenant exists to deploy to for real — a disclosed
gap, the same footing `EnvironmentCredentialProvider`/`NullDirectoryResolver` already carry
for E11's own unbuilt integrations.

**`build_family` orchestrates emit → commit → deploy → smoke-query, entering `BUILT` only
if every step succeeds** — each attempt recorded as its own row in a new platform table,
`public.build_run` (the `g2_question`/`g2_reminder` "history, not a mutable row"
precedent). Triggered automatically from `routes_g2.approve_route` on the `agent:steward`
principal (§19) the moment a design is approved; a build failure is caught there and never
rolls the G2 decision back. `POST /v1/families/{id}:build` is the same pipeline, manually
triggered — the Build tab's own retry after a failure, or a legitimate rebuild of an
already-`BUILT` family (not a state-machine transition, since `BUILT` has no edge back to
itself).

See [ADR 0032](../../docs/adr/0032-tmdl-emission-is-pure-the-target-adapter-is-a-new-boundary.md)
for the full reasoning.

### Conformance rules (story S4.3.2)

`conformance_rules.py` — six §12.3 checks (one substituted, disclosed below), enforced
between `emit` and `commit` in `build_family`'s own pipeline: "no model reaches the client
repository that breaks the target architecture" means a violation blocks the Git write
itself, not only the state transition.

**Star schema** (an unresolved cardinality is an unconfirmed many-to-many), **single active
relationship path** (a union-find cycle check over the undirected table graph), **conformed
dimensions shared by reference** (a shared dimension imported rather than read live),
**measures in display folders by source family** (a name collision within the one folder
every measure in a build already lands in), **naming convention** (non-blank, untrimmed,
TMDL-safe — no spec wording exists for this one), and **RLS roles tested with a fixture
user** (the expression names a field and a recognised user-context function — no live
engine exists to run it for real). §12.3's own sixth check, "every column with a (drafted,
ASSISTED) description," is not implemented: no column data has ever been threaded into
`design_document` (`tmdl.py`'s own disclosed gap), so the backlog substitutes naming
convention instead — the same "backlog wins when it answers with real data the spec's own
version cannot reach" precedent this codebase already follows.

**Rules are data — a versioned platform table, `public.conformance_ruleset`** (the
`g2_question`/`g2_reminder`/`build_run` "history, not a mutable row" footing): an
architect's edit is always a new version, never an overwrite. A fresh graph builds against
an in-memory default (version 0, never persisted) until an architect saves one of their
own. `ModelFamily.conformance_ruleset_version` records which version every build attempt —
pass or fail — was measured against; the violations themselves live in that attempt's own
`build_run.steps` (a single `"conformance"` step naming every offending object).

Editing the ruleset is the Migration Architect's (`MigrationArchitectDep` — the first route
to actually gate on a role declared since S1.1.1 and driven nowhere until now); reading it
is open to any Artizent role.

See [ADR 0033](../../docs/adr/0033-conformance-rules-are-versioned-data-checked-before-commit.md)
for the full reasoning.

### Versioning: a change request opens v(n+1) without touching v(n) (story S4.3.3)

`model_lifecycle.py`'s new `request_new_version`/`promote_family` — a second version of a
published model, produced without regressing what is live.

**Per-version lifecycle now lives on `SemanticModel.state`** (declared since S1.1.1,
undriven until this story); `ModelFamily.state` keeps meaning "the newest/most in-progress
version," so every existing G2/build action needed zero changes. `read_design_document`'s
"the" design changed from `next()` first-match to sorting by `version_number` and taking the
highest — first-match was only ever correct because no family had a second version to
confuse it with. `ModelTable.semantic_model_ref` (new, additive) disambiguates table
ownership once two versions' tables can share one `family_ref`, falling back to `family_ref`
alone for tables written before this story existed.

**`POST /v1/families/{id}:request-new-version`** (PUBLISHED → DRAFT, `reason` ≥ 10
characters) deep-copies the currently PUBLISHED `SemanticModel`'s design fields and every
`ModelTable` under fresh ids, remapping `relationships`' `from_table`/`to_table` through an
old-id→new-id map. v(n)'s own node is never written to — it keeps `state="PUBLISHED"` and
every property it already had, byte for byte, for as long as it stays live. A second change
request while one is already open is refused outright (`require_transition` on a family
already `DRAFT`).

**`POST /v1/families/{id}:promote`** (BUILT → PUBLISHED) checks `regression_status` first —
"passes regression on all released MUs bound to it" is honestly vacuous today, since no
Migration Unit graph node or regression executor exists anywhere in this codebase (§3.1/§10.6,
E3/E7, not built) — the same disclosed-gap posture `harvest.UngatedPromotions` already set,
designed to fail closed the day a real MU registry exists. The route then requires the
family's latest build to be `SUCCEEDED` with a `git_ref`, deploys it for real via the
existing `TargetAdapter.deploy()` contract against a new `target_workspace_published`
workspace, and only calls `promote_family` — which marks the current version PUBLISHED and
its predecessor (if any) DEPRECATED with the date — once that deploy actually succeeds.
`model_lifecycle.py` itself never imports `TargetAdapter`, preserving the one-directional
layering `g2.py`/`build.py` → `model_lifecycle.py` already has.

**`GET /v1/families/{id}/versions`** lists every version, newest first — the console's own
"the console shows both." **`GET /v1/families/{id}/design`** gains an optional
`semantic_model_id` query parameter to read a specific (possibly DEPRECATED) version without
disturbing which one every edit/build action still means by default.

**A real, pre-existing bug found and fixed along the way:** `Modeller._write` had never
persisted `schema`/`mode_reason`/`row_estimate`/`custom_sql` onto `ModelTable` since S4.1.1,
despite `TableCandidate` computing all four — every build since S4.3.1 silently emitted TMDL
without schema qualifiers, and the console always showed "—" for row estimates. Fixed at the
source, since this story's own table-copy logic needed to read those fields faithfully.

See [ADR 0034](../../docs/adr/0034-a-semantic-model-version-lives-on-its-own-node-family-state-tracks-the-newest.md)
for the full reasoning.

## The Transpiler: classification (story S5.1.1)

`classify.py` — E5's opening story. Every `CalculatedField` classified C1-C4 from its AST
before anything is generated, deterministic, per §9.1.

**Reads Appendix B.1's own family straight off the AST — the Tableau grammar already stamps
it there.** `functions.py` (`packages/adapter-tableau`, S2.3.1) records `("family", ...)` in
every FUNCTION/AGGREGATE/WINDOW node's own `detail` at parse time; only Appendix B.1's
"Default class" column did not exist anywhere as data. `classify.py`'s own `_FAMILY_CLASS`
table is exactly that column, transcribed once — graph-svc has never imported an adapter
package directly (S2.1.1's own "invert the dependency" precedent), so this is a second,
small table anchored to the same spec text rather than a shared import.

**A calculation's class is the worst class any of its nodes need.** §9.1's own C1
definition is "every node in the AST has a one-to-one target equivalent" — `classify()`
walks the whole tree and returns the single worst (class, rule id, reason) found anywhere
in it, so a `DIV(SUM(a), RAWSQL_INT(...))` is C4 with the RAWSQL call named as the reason,
not the division. An unrecognised function and an `UNKNOWN` node (the grammar could not
parse it at all) both classify C4 — either way a human has to look, the same outcome C4's
own "Redesign flag; HUMAN" path already means.

**Two facts the AST alone cannot answer are resolved from the graph, not guessed.** Tableau
writes a parameter reference identically to a field reference (`parser.py`'s own
`_reference` docstring: "the caller ... decides") — so whether a calculation depends on a
*parameter* comes from a real `DEPENDS_ON` edge to a `Parameter` node. Table-calc addressing
is the opposite gap: the grammar always records it `"unresolved"` (§6.2 — it "comes from
the sheet, not from the expression"), and nothing has ever resolved it since — `reclassify_
estate` resolves it itself, from every `Worksheet` with a populated `rows_shelf`/
`cols_shelf` (S2.3.2) and the `CalculatedField`s it `ENCODES`.

**`POST /v1/calculations:reclassify`** (`ParityEngineerDep` — the first route to drive that
role, declared since S1.1.1 and gated nowhere until now) walks every live `CalculatedField`,
writes `class`/`pattern_ref`/`reason`/`classifier_version`, and reports what moved class.
Not automatic after harvest — no precedent in this codebase actually does that (`Rescorer`,
S1.2.2's own parse-quality rescoring engine, is itself never auto-invoked); an explicit,
on-demand action instead, the same footing that one already has. **`GET /v1/calculations:
class-mix`** is a live read of what the last pass wrote — open to any Artizent role, the
Programme Board's new fourth pane — against the calibration targets **45 / 30 / 18 / 7**.

See [ADR 0035](../../docs/adr/0035-classification-reads-appendix-b1-off-the-ast-the-grammar-already-stamps.md)
for the full reasoning.

## The Transpiler: deterministic rules engine (story S5.2.1)

`rules.py` — the second piece of the Transpiler, right after classification. C1/C2
calculations rendered into real DAX, deterministically, per §9.2/§9.3.

**Walks the same real AST `classify.py` already reads, bottom-up.** A handful of specific
*shape* rules (an LOD-fixed expression, the ZN/IFNULL null-idiom) are tried first, at every
node, before a generic per-kind/per-family fallback covering operators, aggregate functions,
casts and Type-family functions, conditionals (IF/CASE), and a subset of numeric functions —
the same "specific pattern, then general map, then failure" order §9.2 itself describes for
its own C1→C2→C3 downgrade cascade. Every rule composes recursively, so `SUM(ZN([X])) + 1`
converts correctly through three different rules composing rather than needing one rule per
whole expression.

**A rendered DAX string may carry a literal, disclosed placeholder.** §4.3's own worked
Pattern example ships a target template containing an *unresolved* model-context token —
`ALLEXCEPT({table}, {dims})` — because which target table a field maps to is the Modeller/
Compositor's own fact, not the rules engine's. `c2_lod_fixed`'s own rendered output follows
the identical convention: the literal string `{table}` appears wherever `ALLEXCEPT` needs a
table reference this module cannot resolve yet, disclosed in the rule's own description and
every affected golden case — AST-derived captures are always fully substituted; only
genuinely external, not-yet-bound facts are left visible.

**"Must pass proof in CI" honestly means golden-text equality, not §16.1's own rung-4 parity
verdict.** No Arbiter (§10, E7) or live DAX-evaluating engine exists anywhere in this
codebase (`FixtureTargetAdapter.smoke_query`'s own docstring, S4.3.1, already discloses "no
live Fabric analysis-services engine is configured to run a real ... query") — the identical
gap this story inherits rather than papers over. Each shipped rule ships at least three real
`(source AST, expected DAX)` golden cases, run in the ordinary `pytest -m "not integration"`
sweep exactly as S2.3.1's own calc-grammar corpus already runs in CI, checked for byte-exact
rendered text plus a structural DAX sanity check (balanced delimiters, every function name
against a small known-DAX allowlist) standing in for validation-ladder rung 2 ("parses under
the target grammar") since no real DAX parser exists either.

**`POST /v1/calculations:apply-rules`** (`PlatformEngineerDep` — the first route to drive
that role, declared since S1.1.1 and gated nowhere until now) walks every live
`CalculatedField` a shipped rule covers, writes a real `Measure` node, a `MAPS_TO` edge
carrying the rule id, and a DETERMINISTIC `ProvenanceRecord` citing it — zero ontology
changes needed, since `Measure.dax`/`.class`/`.pattern_ref`/`.provenance_ref` and
`MAPS_TO.class`/`.pattern_ref` were all already declared (§4.1.1/§4.1.2), waiting for
exactly this story to write them for the first time. A second pass never duplicates an
already-converted field. **`GET /v1/calculations:rule-coverage`** is a live read of what the
last pass wrote — the percentage of the estate's calculated fields matched by rule, by rule
family, this story's own acceptance criterion, verbatim — open to any Artizent role, the
Programme Board's new fifth pane. **`GET /v1/calculations:rule-catalog`** lists the shipped
rules themselves: id, class, family, description, guards, golden-case count.

**Hand-shipped rules stay code, never a `Pattern` graph node.** §4.3 names three pattern
sources; only LLM-produced and Mender-generalised transformations ever become `Pattern`
nodes (F5.5, not built) — the deterministic rule set "shipped with the adapter" is, by its
own name, code, authored via PR (S5.2.2, not built), the identical distinction
`classify.py`'s own ADR 0035 already settled for classification rule ids.

**A real bug found and fixed before it ever reached CI**: a `Measure` node id built as
`f"msr_{calc_id}"` (a 4-character prefix over a 26-character ULID) exceeded the ontology's
own node-id length limit — the identical defect shape S3.2.1's own README already
documents ("every other node id in this codebase is a bare `new_ulid()`..."). Fixed by
using a bare ULID.

See [ADR 0036](../../docs/adr/0036-golden-corpus-equality-stands-in-for-proof-until-the-arbiter-exists.md)
for the full reasoning.

### Rule regression guard (story S5.2.2)

`rules.py`'s `check_regression()` and `tools/rule_regression_check.py` — completes F5.2.

**"Every rule change re-runs the golden corpus" needed no new code.** `tests/test_rules.py`
already parametrizes over every rule's every golden case on every CI run, regardless of
which rule was touched — a shared-code change breaking another rule's own case is already
caught there. **"The PASSED artefacts that used the rule" is the genuinely new half**: real
`Measure` nodes a rule has already produced, in a real graph, not test fixtures.
`check_regression` re-renders each one's source `CalculatedField` against the *current*
rule set (via `pattern_ref`, already written by S5.2.1) and classifies what it finds: a
`Measure` that no longer renders at all is **regressed** (blocks); one that still renders,
only with different text, is **changed** (disclosed, does not block — a rule can
legitimately improve, the same way its own golden cases are allowed to change between
versions).

**`tools/rule_regression_check.py`** is a standalone, DB-connected script — the identical
shape `tools/migrate.py`/`ontology_check.py`/`migration_check.py`/`contract_check.py`
already established for this codebase's own drift guards — wired into
`make rule-regression-check` and a new CI step (`.github/workflows/ci.yml`, right after
"Migrate"). On this repository's own generic CI, a freshly migrated graph has nothing yet to
regress, so the step passes trivially — its real teeth are exercised the moment it runs
against a client's own accumulated graph before a new deployment reaches it. **"The tenant"
is this codebase's own ordinary release path**, not a fabricated multi-tenant promotion
queue: this platform has one deployment per client environment (§12.2's own three named
workspaces), not a fleet to advance a change through one at a time.

**No console screen.** A Pattern Library *screen* — listing patterns by class/state,
promote/retire/edit-guards actions — is S5.5.3's own explicit scope, built for *promoted*
patterns (F5.5, not built). This story's own acceptance criteria never names a screen,
despite its scene-setting sentence mentioning "the Pattern Library"; `GET /v1/calculations:
rule-regression` exists as a real, tested route without a console consumer, matching exactly
what the acceptance criteria itself asks for.

See [ADR 0037](../../docs/adr/0037-regression-checks-real-artefacts-not-a-fabricated-promotion-pipeline.md)
for the full reasoning.

## The Transpiler: C3 generation under proof (story S5.3.1)

`generation.py` — opens F5.3, right after F5.2 (rules engine) closed. C3 calculations —
those a shipped rule doesn't cover but that stay within grammar — generated by a model and
validated up §16.1's ladder before a mode of `GENERATED_PROVED` can ever be recorded.

**§9.4's own `GenerationRequest`, assembled directly from the graph.** The formal
`TRANSPILER_CALC` context contract has no `sheet_ctx`-shaped section, so
`build_generation_request` follows `classify.py`/`rules.py`'s own precedent instead: a
bounded `DEPENDS_ON` walk for the dependency closure, the encoding `Worksheet`'s own real
shelf/filter/sort data, and matching `Pattern`s (honestly empty today — no `Pattern` node has
ever been written, F5.5 not built). Two more gaps are disclosed directly in the request
document rather than silently omitted: `model_ctx.tables`/`.columns` are empty because no
`Field→ModelTable` `MAPS_TO` edge has ever been written by any story, including the
Modeller's own S4.1.1 output; `charter_excerpt` is empty because no G1 Tolerance Charter has
ever been recorded anywhere in this codebase.

**The ladder is real where it can be, and honestly disclosed where it can't.** Schema (rung
1) is real pydantic validation against §9.4's own `output_schema`. Parse (rung 2) reuses
`rules.dax_sanity_check` unchanged — the same structural stand-in S5.2.1 already shipped, not
a new grammar. Compile and proof (rungs 3/4) are unconditional, disclosed passes: no live
Fabric dev workspace exists to compile against (E11), no Arbiter exists to grant a real
parity verdict (E7) — the identical posture `FixtureTargetAdapter.smoke_query`/
`model_lifecycle.regression_status` already set. Only rungs 1 and 2 can actually drive a
regeneration or an escalation today, stated plainly rather than dressed up as more than it
is.

**No Model Gateway — a narrower, disclosed `ModelCaller` seam instead.** §5.5's own
provider-agnostic routing, eval-set-gated policy, budgets, and caching are S5.3.2's own
explicit scope. `generation.py` defines a `Protocol` (`ModelCaller`) and ships
`FixtureModelCaller` as the wired-in default — every call is honestly declined with
`NOT_EXPRESSIBLE`, never a fabricated plausible-looking candidate, the identical discipline
`FixtureTargetAdapter.smoke_query` already set. The ladder, the regeneration loop, and every
write path are real and fully tested against this seam today; S5.3.2 only has to supply a
second `ModelCaller` implementation.

**Up to two regeneration attempts, one shared budget.** The backlog's own wording ("up to two
regeneration attempts on parse or compile failure with the error fed back") is one pool of
two covering both rungs, not two per rung — `MAX_ATTEMPTS = 3` (one initial attempt plus two
regenerations), the previous attempt's own error fed back into the next call's
`previous_error`. A schema failure or a `NOT_EXPRESSIBLE` decline is never retried (§16.1's
own "hard failure, no retry" / §9.4's own "immediate Class 4 flag").

**Success writes a real `Measure`/`MAPS_TO`/`ProvenanceRecord`; failure writes a real
`ExceptionCase` — the first story to drive that node type**, the same "declared since
S1.1.1, driven for the first time" trajectory `SemanticModel.state`/`Measure`/`Pattern` have
each already taken. No `pattern_ref` on a `GENERATED_PROVED` `Measure`: a generated candidate
was not produced by a rule or a `Pattern`, so the property is omitted rather than written as
an explicit null, the same convention `rules.py`'s own C1/C2 writes already follow.
`ExceptionCase.class` uses `_FAILURE_CLASSES`' own `UNKNOWN` member — that taxonomy (§11.1)
is for *parity* failures the Mender diagnoses after a real proof attempt, a different moment
from this story's own pre-proof generation failures; a real, disclosed mismatch, not a false
fit. `ProvenanceRecord` gains `provider`/`gateway_request_id`/`temperature` (migration v0020,
additive, no ontology change) to carry §4.2's full `model_call` block for the first time —
nothing before this story ever called a model at all.

**`GET /v1/calculations/{calc_id}:generation-request`** (any Artizent role) returns the
`GenerationRequest` a field would be sent with, without calling the model — useful for
inspecting what context this platform can and can't yet supply. **`POST /v1/calculations/
{calc_id}:generate`** (`ParityEngineerDep`, this story's own named persona) runs one C3 field
through the full ladder and reports the outcome, including every attempt made.

See [ADR 0038](../../docs/adr/0038-generation-runs-a-real-ladder-behind-a-disclosed-model-seam.md)
for the full reasoning.

## The Transpiler: the Model Gateway (story S5.3.2)

`gateway.py` — fills the one gap S5.3.1 deliberately left open. The Transpiler now calls
`gateway.generate(task_class='transpile_c3', ...)`, never naming a provider, exactly as the
acceptance criteria says.

**A real task-class router in front of one real provider.** `ModelGateway` reads which
providers are routable for a task class from a real, Postgres-backed policy store and calls
the first one, never fabricating a response of its own — `GatewayRoutingError` if nothing is
routable. `Gateway` (the router-level contract) and `ModelCaller` (one provider) are two
different Protocols; `generation.py`'s own ladder holds a `Gateway`, not a bare caller.

**Real Anthropic integration — this story's own explicit scope decision.** Unlike every
infrastructure gap S5.3.1 disclosed, a real provider integration was genuinely possible here
given credentials, so the choice was put to the product owner directly: live Anthropic calls,
not a fixture. `AnthropicModelCaller` uses the Messages API's own structured-output feature
(`output_config.format`, a JSON schema derived at call time from whatever `output_schema` the
request declares) rather than prompt-engineered JSON and a regex. The installed SDK's own
current API surface has no `temperature` parameter to set at all — confirmed directly against
`anthropic>=1.0`'s own types, not assumed from older API shapes — so `temperature=0.0` on
every response records the declared §5.4 policy, not an echoed request field, the same
"provenance states the policy" reasoning `provenance.py`'s own field already carries. Azure
OpenAI has no `ModelCaller` registered anywhere — disclosed absent, not a fake failing
provider, so it is correctly never "configured" and never "routable."

**A real, append-only, Postgres-backed tenant policy.** `PostgresGatewayPolicyStore`
(`public.model_gateway_policy`, migration v0021) follows `conformance_ruleset`'s own "an edit
is a new version" discipline: every eval run inserts a new row, and `routable_providers`/
`policy_for` read the *latest* row per `(graph, task_class, provider)`. `ROUTABLE_THRESHOLD =
0.80` is the AC's own number — confirmed to be the same one §16.6's "Class 3 proof rate"
target names.

**A real eval harness — first-pass schema + parse, a disclosed proxy for a real parity
verdict.** No Arbiter exists (E7, the identical gap S5.3.1's own rungs 3/4 already disclosed),
so `run_eval_set` can only grade what can really be checked today: does a response conform to
the schema, and does its candidate pass `dax_sanity_check`, both real, both reused unchanged.
`TRANSPILE_C3_EVAL_CASES` is a real, fixed, checked-in corpus of five table-calc idioms — the
same "a real corpus, run for real" shape `rules.py`'s own `GoldenCase`s already established,
generalised here to grade a live model instead of a deterministic render.

**`GET /v1/model-gateway:policy`** (any Artizent role) reports every configured provider's
last eval score and whether it is routable, for one task class. **`POST
/v1/model-gateway:run-eval`** (`PlatformEngineerDep`, this story's own named persona) runs the
eval set against one real, configured provider and records the verdict — the one action here
that makes a real, billed external call, gated to the persona that should trigger it.

See [ADR 0039](../../docs/adr/0039-the-model-gateway-real-anthropic-real-eval-gated-routing.md)
for the full reasoning.

## The Transpiler: confidence calibration (story S5.3.3)

`calibration.py` — closes F5.3. Every declared confidence is now recorded, win or lose, and
a task class whose own real history falls below a configurable floor is routed to a
disclosed-absent "small-model-plus-proof" tier rather than trusted.

**A real, append-only observation for every declared confidence — not only survivors.**
`PostgresCalibrationStore` (`public.calibration_observation`, migration v0022) records one
row per `LadderAttempt` that got far enough to declare a confidence, for both successful and
failed/declined generations. Before this story, a failed attempt's confidence was hashed
into an opaque `ExceptionCase.evidence_ref` and never durably queryable — a curve built only
from successes would have been trivially 100% at every bucket.

**"Observed pass" is the epic's own real, disclosed proxy for proof, reused a third time.**
No Arbiter exists (E7, the identical gap S5.3.1/S5.3.2 already disclosed), so "observed"
means exactly what it already meant twice before: a candidate that cleared rung 1 (schema)
and rung 2 (parse) on its own attempt. `build_report` computes the AC's own ten buckets from
real observation history, plus §16.6's own "mean |declared − observed|" calibration-error
metric — pure aggregation, unit-tested without a database.

**The floor is real; the small-model-plus-proof path it reroutes to is a disclosed-absent
destination, not a second model — this story's own explicit scope decision.**
`DEFAULT_CALIBRATION_FLOOR = 0.80` reuses §16.6's own "Class 3 proof rate" target and
`gateway.ROUTABLE_THRESHOLD`'s own number. `generate_c3_field` checks it for real before
running the ladder, routing to a second, real `TaskClass` (`TRANSPILE_C3_SMALL_MODEL`) when
crossed — but no `ModelCaller` is ever registered under it, since no backlog story stands up
a real small-model provider, so a reroute honestly raises `GatewayRoutingError` and escalates
to the Exception Desk, the identical footing Azure OpenAI already has under `TRANSPILE_C3`
(ADR 0039). Once triggered, a reroute stays triggered by design — with no real small-model
path, no new observations are ever recorded while pinned, matching §16.3's own "pins routing
to the reasoning tier until *reviewed*" rather than something meant to self-heal.

**`GET /v1/model-gateway:calibration`** (any Artizent role; `task_class`/`floor` as optional,
bounded query parameters, this codebase's own established shape for a configurable business
threshold) reports the ten-bucket calibration curve for one task class. No console screen —
a calibration-curve screen is S13.2.2's own later, explicit differentiator ("flagged on the
Pattern Library", a later milestone after the Calibration Wave exists to generate real
evaluation data at scale); this story's own acceptance criteria asks for a report, not one.

See [ADR 0040](../../docs/adr/0040-confidence-calibration-a-real-floor-onto-a-disclosed-absent-tier.md)
for the full reasoning.

## The Transpiler: C4 redesign (story S5.4.1)

`redesign.py` — opens F5.4. For each construct the classifier flags C4, the Transpiler now
writes Appendix B's own guidance, a real ASSISTED-mode redesign suggestion, and holds the
field's own decision-shaped "gate" until a Migration Engineer records one.

**Appendix B becomes data a second time.** `APPENDIX_B_GUIDANCE` turns Appendix B.1's own
literal target/notes cell for every C4-producing `classify.py` rule_id into real data — the
identical "spec table becomes code" move that made B.1's function-family table
`classify.py`'s own `_FAMILY_CLASS` (ADR 0035). A rule_id the classifier can reach but this
table has no entry for is a real drift bug (`c4_properties` raises rather than guessing), and
`test_redesign.py` proves the two modules agree in both directions.

**The redesign suggestion is `AgentMode.ASSISTED`, a second "name only" contract — never a
model call.** `modeller.py` already drives `ASSISTED` for its own grain-statement draft; this
is the identical footing, a real deterministic template composed from Appendix B's own
guidance text. `ContractName.TRANSPILER_C4_REDESIGN` is a name only, unregistered in
`CONTRACTS`, matching `MODELLER_FAMILY`'s own precedent — no model gateway call, no inference
boundary to police.

**No Migration Unit exists anywhere in this codebase, so `CalculatedField.redesign_decision`
(absent) is the disclosed proxy for §3.2's own BLOCKED state.** Confirmed by direct research
against the spec: §4.1.1 declares no `MigrationUnit` node; §3.1 defines an MU as a
control-plane concept spanning several existing nodes, not itself one; no story has ever
created a real MU record. The product owner's own explicit choice (over a new platform-table
record) put seven new properties directly on `CalculatedField`
(`appendix_b_guidance`/`redesign_suggestion`/`redesign_suggestion_provenance_ref`/
`redesign_decision`/`redesign_decision_reason`/`redesign_decision_by`/`redesign_decision_at`),
present only while `class` is C4 — reclassification drops all seven the moment a field is no
longer C4, and always carries forward an already-recorded decision untouched.

**`POST /v1/calculations/{calc_id}:redesign-decision`** (`MigrationEngineerDep` — the first
route to ever drive `Role.MIGRATION_ENGINEER`) records one of the AC's own three outcomes
(`IMPLEMENT_AS_SUGGESTED` / `ALTERNATIVE` / `DROP`) with a real, required reason — for DROP,
this is where the report-owner agreement the AC requires is documented, rather than building
a second countersign workflow duplicating G2's own (S4.2.1). **`GET
/v1/calculations:c4-redesigns`** (`C4RedesignReaderDep` — any Artizent role, or the report
owner specifically; the first route to ever drive `Role.CLIENT_REPORT_OWNER`) lists every C4
field's guidance, suggestion and decision state. No `GateDecision`-shaped generic record
(S8.3.1's own later Exception Desk scope) and no real G3 gate (S9.1.1/S9.1.2's own later
scope, two increments out) — "referenced at G3" stays exactly that future reference.

See [ADR 0041](../../docs/adr/0041-c4-redesign-guidance-and-decision-a-disclosed-blocked-proxy-on-calculatedfield.md)
for the full reasoning.

## The Pattern Library (stories S5.5.1, S5.5.2 and S5.5.3)

`patterns.py` — opens F5.5. A proved C3 transformation becomes a reusable, deterministic
template automatically; enough of them, applied enough times, mean fewer model calls as the
programme runs.

**A real, pre-existing bug in AST shapes, fixed first.** `context/signature.py`'s
`ast_shape()` — the function every "same shape" claim in this codebase routes through — was
written before the real Tableau grammar existed (S1.3.1) and never updated for it. Run
against the real wire AST (`kind`/`name`/`children`/`detail`, S2.3.1), it could not tell
`kind`/`name` apart from any other string field, so `SUM([Notional]) / SUM([Margin])` and
`SUM([Notional]) + SUM([Margin])` — different operators, different fields — rendered to the
identical shape. Confirmed by direct execution, not assumed, and fixed here: a kind-aware
dispatch recognising all nine real `NodeKind` values runs ahead of the pre-existing generic
walk, which is otherwise untouched (still exactly correct for the one test fixture that
predates the real grammar and still uses it). Every existing caller —
`lineage.calc_shapes` (feeding S3.1.1's own Cartographer clustering), `generation.
_matching_patterns`, `context.assembler._patterns` — gets a correct shape for the first time.

**Generalisation reuses a pattern rather than ever duplicating one.** When
`generate_c3_field`'s own ladder succeeds, `generalise_from_proof` looks for an existing
CANDIDATE/ACTIVE pattern matching the AST's shape first; a match records another real proof
observation against it (how a CANDIDATE ever accumulates "N distinct proof passes"); no
match creates one, with `target_template` generalised from the model's own real DAX text by
substituting each captured field/parameter's bracketed reference with a placeholder — an
honestly limited text substitution, not a DAX parser, disclosed as such — and `guards`
inferred, modestly, from real `Field`/`Parameter` datatypes this platform can actually
resolve (`"a is real"`), never fabricated for one it cannot.

**"Zero failures" is real, from both directions.** A normal ladder failure and a
deterministic pattern application that fails even `rules.dax_sanity_check`'s structural
stand-in (rare, since the template came from an already-proven artefact, but checked, not
assumed away — it falls back to the model path rather than blocking the field) both record
a real failure observation. Promotion (`promote_pattern`, re-checked server-side against
`public.pattern_observation`'s own append-only history — never a maintained counter, the
identical footing `calibration_observation` already set) refuses below the threshold and
refuses with any recorded failure even at it.

**The payoff: `generate_c3_field` checks for an ACTIVE pattern before it ever reaches the
model.** A match renders the pattern's own template against this specific calculation's
real references and writes a Measure directly — the source `CalculatedField`'s own `class`
becomes C2, its `pattern_ref` a real `Pattern` node, the model never called. Proven, not
assumed: the integration suite wires a gateway that raises if `.generate()` is ever invoked
and confirms the outcome still succeeds.

`GET /v1/patterns` and `GET /v1/patterns/{id}:promotion-status` are open to any Artizent
role; `POST /v1/patterns/{id}:promote` is the platform engineer's (`PlatformEngineerDep`,
reused from S5.2.1/S5.3.2) — §13.2's own MA-11 action class, autonomy ceiling L2
("Platform Engineer approves").

See [ADR 0042](../../docs/adr/0042-the-pattern-library-a-real-shape-fix-generalisation-and-deterministic-promotion.md)
for the full reasoning.

### Automatic retirement (story S5.5.2)

A bad ACTIVE pattern retires itself — no route, no approval — the moment a failure it
caused crosses a real threshold; MA-12's own autonomy ceiling is L4 ("automatic on failure
threshold"), a deliberate contrast with MA-11's own L2 promotion gate, both named in the
same §13.2 table row-by-row.

**The threshold is the spec's own number, not the backlog's paraphrase.** §9.3 reads
"above a threshold (default 3 failures or a pass rate below 0.97 over 30 applications)" —
a materially different condition from the backlog AC's own "2 in 100." `evaluate_retirement`
implements the spec's dual condition (an absolute trip-wire *or* a ratio over a minimum
sample), both thresholds overridable exactly as `calibration.build_report`'s own `floor`
already is — this repository's own standing "spec wins on disagreement" rule, applied
identically to how S5.1.1 already resolved its own threshold discrepancy.

**`Pattern.failure_count`** (a new, additive ontology property, schema version 20) is a
real, disclosed counter — the AC's own literal "increments its failure count" — but never
the authority a retirement decision is checked against; that check always reads
`public.pattern_observation` live, the identical discipline `pass_count` already set.

**No real Migration Unit or ACCEPTED state exists**, so §9.3's own "every MU that used it
is flagged ... for re-proof" is disclosed as "every Measure this pattern produced that this
platform can still find" — the identical MU-shaped gap S5.4.1/S5.5.1 already found, a third
time. Retiring a pattern is concrete, not a flag: every live Measure citing it is retired
(`GraphWriter.retire_node`), and its source `CalculatedField`'s own `class`/`pattern_ref`
are overwritten with a fresh, plain `classify.classify()` verdict — almost always C3 again,
which is what actually makes the field eligible for a real `generate_c3_field` call next.

**"An event is raised"** is a real notice sharing the outbox the way `events.source_drift`
already does (S1.2.4) — `EventType.PATTERN_RETIRED`, carrying the reason and every
re-queued Measure id, `mutates_graph=False` so replay skips it.

See [ADR 0043](../../docs/adr/0043-pattern-retirement-is-automatic-mu-less-re-queue-is-a-plain-reclassification.md)
for the full reasoning.

### The Pattern Library screen, manual retirement and versioned guards (story S5.5.3)

F5.5's closing story, and the first console screen this codebase has built since early F1.4:
"see what the platform has learned and govern it." Two of the AC's own four actions
(promote, export) already existed or needed no new route; two did not.

**Manual retirement is a human path onto the identical machinery S5.5.2 already built.**
`retire_pattern` (any live, non-RETIRED pattern — CANDIDATE included, unlike S5.5.2's own
ACTIVE-only automatic mechanism) shares a `_perform_retirement` helper extracted from
S5.5.2's own automatic path, so a Platform Engineer's own decision and a threshold's
automatic one produce byte-identical downstream effects (the re-queue, the state write, the
event) — differing only in who decided and why, now also recorded (`retired_by`) on both
paths.

**Editing guards creates a new `Pattern` version — the old node retired, never mutated** —
the identical "an edit is a new version" discipline `SemanticModel` already set (S4.3.3),
applied to a graph node. Two new, additive properties (`Pattern.version`/`.supersedes_id`,
schema version 21) name the chain. Guards are documented as purely descriptive, never
evaluated — so the new version inherits `promotion_state`/`class`/`source_signature`/
`target_template` unchanged, rather than throwing away already-earned ACTIVE trust for a
wording change. What it does *not* inherit is `pattern_observation`: the new version starts
its own append-only ledger from zero under its own id, since a version's whole point is a
per-identity proof history — silently merging one version's own real evidence into another's
would misrepresent what the new wording has actually earned.

**`promote_pattern`/`retire_pattern`/`edit_guards` now all return the same row shape
`list_patterns` gives every row** (`pattern_row`, factored out of `list_patterns`'s own
per-row projection) — the console merges a mutation's own response straight into the list
it already rendered, rather than reconciling a second, differently-shaped type. A real,
disclosed interface change from S5.5.1's own shape (`pass_count` → `distinct_passing_calcs`),
caught by the full suite and fixed in the one pre-existing assertion it affected.

`POST /v1/patterns/{id}:retire` and `POST /v1/patterns/{id}:edit-guards` are both the
platform engineer's (`PlatformEngineerDep`); "export" needed no new route — the console
screen already holds the full, current list, so a client-side JSON download of what is
already rendered satisfies the AC.

**A real, pre-existing infrastructure bug, found by this story's own first real browser
load of a console screen through its own nginx proxy**: see console-web's own README, under
"Container" — every `/v1/*` call through a local Docker `console-web` container had
silently 502'd since S5.3.2, on a DNS resolver hardcoded to an Azure-only value. Fixed at
the root (a third envsubst template token, Azure's own default preserved, Docker Compose
carrying the local-only override), not worked around.

See [ADR 0044](../../docs/adr/0044-the-pattern-library-screen-and-the-nginx-resolver-bug-it-found.md)
for the full reasoning.

## The Compositor: visual mapping and PBIR emission (stories S6.1.1, S6.1.2 and S6.1.3)

`compositor.py` / `visual_mapping.py` / `pbir.py` -- opens E6. Each Tableau sheet becomes a
Power BI `Visual`, field wells bound through real `MAPS_TO` edges where they exist, filters
and sort translated as-is, dashboard containers becoming report pages with the zone's own
geometry preserved -- and a sheet whose mark type has no mapping becomes a placeholder
rather than blocking the rest of the report.

**The mapping table is keyed on the raw Tableau mark type, not a synthetic Appendix B.2
row.** Appendix B.2 is its own "excerpt", grouping several Show Me categories (crosstab,
highlight table, scatter/bubble, KPI/BAN) under headings the adapter never records as a
value -- `sheets.py`'s own `_mark_type` reads Tableau's literal, lowercased `<mark
class="...">` (or `"automatic"` when absent). `visual_mapping.py` keys `DEFAULT_MAPPINGS` on
that one real field; `resolve_visual` recovers Appendix B.2's finer categories from the
*encodings* on top of the base lookup -- a colour encoding promotes clustered→stacked; a
measure on the columns shelf (Tableau lays columns out horizontally) rather than rows
promotes column→bar; two or more measures on rows promotes a dual-axis combo, flagged for
review; a `"text"` mark with both shelves populated promotes table→matrix; a size encoding
on a scatter is noted as a bubble; `"automatic"` is resolved to an effective mark from the
shelves first (a single measure, no dimension → a KPI card) -- the identical choice
`sheets.py`'s own comment says Tableau itself makes at render time.

**The table follows `conformance_ruleset`'s own template exactly** (migration v0024,
`public.visual_mapping_ruleset`) -- one `jsonb` column, an architect's edit always a new
version, a fresh graph building against an in-memory default (version 0) until one is
saved. `redesign.APPENDIX_B_GUIDANCE` and `model_gateway_policy` were both the wrong shape
(data-but-not-versioned, and append-only eval history, respectively).

**"Bound through MAPS_TO" is honestly half-real.** A calculated-field well resolves against
real `CalculatedField→Measure` edges (the Transpiler writes them from S5.1.1 onward). A
plain field's `Field→ModelTable` edge has never been written by any story in this codebase
(`generation.py`'s own disclosed finding, confirmed still true) -- `_resolve_bindings`
queries for it for real either way and reports `bound: false` with the specific reason when
absent, never a name-matched guess. Fields are resolved to a real node id by name against
the worksheet's own `USES_DATASOURCE → HAS_FIELD` reach, one step further than the
Cartographer's own workaround for `ENCODES` never being written (S3.1.1, still true), which
only ever compares shelf name strings and never needed a real id.

**Every sheet gets a page.** A worksheet placed on one or more dashboards gets one `Visual`
per placement (PBIR has no visual shared across pages); an uncontained worksheet gets its
own single-visual page. `Visual.layout` (new, additive, schema version 22) carries the
matching dashboard zone's own x/y/width/height, a plain read of `Dashboard.layout_json`'s
zone tree -- absent for a standalone page (nothing to preserve) or a placeholder (§8.8's own
layout-collision resolution is unbuilt, future scope; a one-visual-per-zone compose never
collides).

**Found live, composing a real fixture-harvested workbook**: the fixture *source* adapter
(`astra_adapter.fake.source`) writes `Dashboard.layout_json` as `{"zones": [{"sheet":
ref}, ...]}`, not the real Tableau adapter's own bare list of zone dicts -- crashing the
first real compose (`AttributeError`, iterating a dict walked its own keys as zones).
`compositor._zone_list` normalises either shape and skips a non-dict zone during the walk;
the fixture's own entries carry no geometry regardless, so `layout: None` is the honest
outcome either way. Bringing the fixture generator itself in line with the real adapter's
richer shape is real, disclosed follow-up work.

**PBIR validation is a real, disclosed-subset schema, not Microsoft's own published one.**
`schemas/pbir/{report,page,visual}.schema.json`, checked with the new `jsonschema`
dependency, cover exactly the PBIR structure this Compositor emits today -- vendoring
Microsoft's own schema is real, disclosed future work. The whitelist and "does every field
resolve" checks (§7.1's fuller requirement, beyond the AC's own schema-only text) are both
real: the whitelist is a hard error, checked again at ruleset-save time so an architect
gets the refusal immediately; an unresolved binding is a **warning**, not an error, since
today it fires on nearly every real workbook (the universal `Field->ModelTable` gap above)
and marking that `INVALID` would misrepresent a platform gap as a defect in this compose. A
schema or whitelist failure refuses the whole compose before anything is written, mirroring
`build.py`'s own "conformance runs before commit, not after".

**A re-compose replaces the workbook's whole report** -- the identical starting posture the
Modeller/Cartographer each had before their own override stories. `GET`/`POST
/v1/compositor/visual-mappings` (any Artizent role / `MigrationArchitectDep`), `POST
/v1/workbooks/{id}:compose` (`MigrationEngineerDep`, this story's own named persona) and
`GET /v1/workbooks/{id}:report`. No console screen: F6.1/F6.2/F10.x name none for the
Compositor, confirmed by direct research against the full backlog text.

See [ADR 0045](../../docs/adr/0045-the-compositor-visual-mapping-as-data-a-disclosed-subset-pbir-schema.md)
for the full reasoning.

### Committing and deploying a report (story S6.1.2)

`report_deploy.py` closes F6.1. `deploy_report` reads back a workbook's own already-composed
report (`compositor.read_report`) and commits + deploys it through the identical
`TargetAdapter.commit`/`.deploy` contract `build_family` (S4.3.1) already uses -- no second
commit/deploy mechanism, `TmdlBundle` reused verbatim for the PBIR bundle's own JSON bytes.
This is deliberately a second, separate action from composing, not a hidden step chained
onto it: `compose_report`'s own acceptance criteria (S6.1.1) stops at validation, and a
Migration Engineer reviews S6.1.1's own disclosed binding warnings before choosing to
deploy.

**"Bound to the PUBLISHED or BUILT model" closes a real, disclosed gap rather than working
around it.** Tracing every write site found `SemanticModel.state` was never once set to
`"BUILT"` anywhere in this codebase -- only `ModelFamily.state` was, even though
`SemanticModel.state`'s own declared note already promised "deployment state within an
environment." `build.py`'s own `finish()` now also stamps `SemanticModel.state = "BUILT"`
on a successful build, so `deploy_report`'s own check (`state in ("BUILT", "PUBLISHED")`)
reads the *model* the AC actually names, not a family-level proxy that would get the wrong
answer the moment a family has a DRAFT v(n+1) alongside its still-live BUILT/PUBLISHED v(n).

**"Returns the MU to GENERATED with the error" is a disclosed proxy -- the sixth time this
exact gap has been found.** No real Migration Unit node or §3.2 state machine exists
anywhere (`migration_units.py`'s own registry declares no state-setting method at all); "the
MU page" is F10.3's own unbuilt future screen. `ReportDefinition.deploy_state`/
`.deploy_error` (new, additive, schema version 23) carry exactly this fact on the one real
node this action touches -- `"GENERATED"` once commit and deploy both succeed,
`"DEPLOY_FAILED"` with the failing step's own detail once every retry is exhausted.

**Retries wrap the deploy call only, as a fixed three-attempt budget with a small fixed
backoff schedule** (`DEPLOY_RETRY_DEFAULT = 3`, `(2.0, 5.0)` seconds between attempts) --
no spec text names a retry count or backoff shape for deployment anywhere, so this story
owns the shape, read the same "budget" way the Mender's own "pass budget (default 3)"
already is rather than "three retries after a first attempt" (four total). `public.
report_deploy_run` (migration v0025) mirrors `build_run`'s own shape exactly, keyed by
`workbook_id` rather than `report_id` since a recompose retires and replaces the report id
but the workbook is what a Migration Unit actually is.

`POST /v1/workbooks/{id}:deploy` and `GET /v1/workbooks/{id}/deploy` reuse
`MigrationEngineerDep`/`ArtizentDep` -- no new role. No console screen, the identical
finding S6.1.1 already made.

See [ADR 0046](../../docs/adr/0046-deploying-a-report-a-disclosed-mu-proxy-and-a-real-retry-budget.md)
for the full reasoning.

### Parameters, actions and interactivity (story S6.1.3)

Closes F6.1. `compose_report` now also resolves, per worksheet: which `Parameter`s its own
calculated-field wells depend on (`DEPENDS_ON`), classified by `domain` into a what-if
parameter (`range`) or a slicer (`list`) -- `any` (unconstrained) has no bounded Power BI
equivalent and is left unsupported; and every live `Action` naming that worksheet as a
source or target, classified by `type` into a cross-filter setting (`filter`), a highlight
setting (`highlight` -- Appendix B.2 groups it with filter under one identical outcome, the
fuller reading of a backlog AC that only names filter and URL explicitly), a URL field
(`url`), or left unsupported with Appendix B.2's own guidance (`parameter`/`set`: "→ C3 or
C4"). Both lists land on the new `Visual.interactivity` property (additive, schema version
24) -- "recorded on the Visual node" satisfies the AC's own second bullet for both halves
of the first at once, since no MU page exists yet to list unsupported actions on (F10.3,
still unbuilt).

**A parameter is found only through a calculated field that already depends on it** -- the
real `DEPENDS_ON` edge is the only path this platform has from a worksheet to a parameter,
since `Parameter` carries no edge back to its own `Workbook` at all (§4.1.2). A range-domain
parameter is classified correctly but discloses a real data gap rather than inventing
bounds: `sheets.py`'s own `Parameter` dataclass never captures a range's start/end/increment,
only a `list` domain's own values.

**`Action` has no `name` property** -- confirmed directly against the ontology and the
adapter's own `as_properties()` (only `type`/`source_sheets`/`target_sheets` exist); an
early draft of the interactivity mapping invented one anyway before the first integration
fixture caught it. `ActionMapping` carries `other_sheets` instead -- real data every action
has.

**Actions are matched to a worksheet by a global name scan -- a real, disclosed
cross-workbook collision risk**, not a hypothetical one. With no edge from `Action` to
`Workbook`, finding "this workbook's own actions" means scanning every live `Action` and
matching sheet names -- the identical trust `Dashboard.contained_sheets` already places in
strings, extended to a node type with no scoping at all. This story's own integration suite
demonstrated the collision directly: a shared test graph accumulating same-named fixture
sheets across tests caused one test's own action query to return other tests' fixtures too.
Fixing this for real needs a new `Workbook`-containing edge on the adapter side
(`fragments.py`, S2.3.2's own territory) -- real, disclosed future work, not attempted here.

See [ADR 0047](../../docs/adr/0047-interactivity-mapping-a-real-scope-limit-on-actions-with-no-workbook-edge.md)
for the full reasoning.

## Redesign flags as work items (story S6.2.1, opens F6.2)

`visual_redesign.py`. A redesign-flagged `Visual` (S6.1.1) now also opens a real
`ExceptionCase(class=VISUAL_REDESIGN)` -- the same one real work-item mechanism
`generation.py`'s own pre-proof `UNKNOWN`-class case already established (S5.3.1), reused a
second time for a disclosed-different kind of case rather than a new node type.
"Routed to the Exception Desk" means exactly this: a real, queryable node with
`state="OPEN"` -- the Exception Desk itself is F8.3/S8.3.1's own unbuilt future console
screen (§11.3: "there is no separate defect tracker"; it is a queue view over this exact
node type). **Correction to earlier E6 ADRs**: `services/console-web` is a real, working
console with nine live surfaces -- none of them is an Exception Desk, an MU page, or a
Compositor screen, which is the accurate claim; "no console exists at all" (as ADR 0047
put it) was wrong.

**Evidence is a snapshot, not a live read** -- `mapping_reason`/`placeholder_location` are
copied from the `Visual` at the moment its case opens (S1.4.3's own "evidence copied onto a
record is a snapshot" precedent), so a later mapping-table edit can't quietly rewrite what
an already-open work item says it is about. **No screenshot is ever automatically
captured** -- nothing in this platform's own local/demo environment has a live Tableau
connection to take one from; `screenshot_ref` looks for an existing `visual_capture`
artefact matched by `case_id` naming the source worksheet, honestly absent when none has
ever been uploaded.

**A recompose retires a dependent case, it does not close it** -- the same "retiring the
parent, react to the dependent" cascade `patterns.py` already established (S5.5.x); closing
implies a real engineer decision a recompose never makes, so a fresh flag on the same sheet
just opens a fresh case against the fresh `Visual`. **Closing** mirrors S5.4.1's own C4
redesign closing shape (`*_by`/`*_at`) plus the one new fact this AC names,
`desktop_commit_hash` -- recorded as the engineer states it, never verified against a real
Desktop this platform cannot reach.

**"Cannot enter PROVING for the affected sheets" is a real, callable, currently-uncalled
check** -- no real Migration Unit or §3.2 state machine exists anywhere (confirmed a
seventh time), and E7's Arbiter, the only thing that would ever call it, doesn't exist
either. `can_enter_proving` computes a real answer today (which worksheets have an open
`VISUAL_REDESIGN` case against their own `Visual`) at the *sheet* grain the AC asks for --
finer than §3.2's own whole-MU `BLOCKED` state, a disclosed departure from the spec's own
table shape rather than a forced fit into a coarser name.

`GET /v1/exceptions` (filterable by `state`/`mu_ref`), `POST /v1/exceptions/{id}:close`
(`MigrationEngineerDep`) and `GET /v1/workbooks/{id}:proving-readiness` all reuse existing
role gates -- no new role.

See [ADR 0048](../../docs/adr/0048-redesign-flags-as-real-work-items-a-second-use-of-exceptioncase.md)
for the full reasoning.

## Report documentation (story S6.2.2)

`report_documentation.py`. A deterministic markdown page per report, rendered entirely
from already-composed graph facts -- purpose (the workbook's own `name`; `Workbook` has
no `description` property, confirmed directly), pages/visuals with their Tableau sheet of
origin, measures with their source calc names, parameters, known differences (open C4
calculations with their Appendix B guidance/suggestion/decision, and redesigned visuals
with their reason), the model (family, grain, version, state) and every distinct refresh
schedule its worksheets' own datasources carry.

**§8.11 names the Steward as the agent that drafts report documentation; this platform
has no Steward yet (E9, unbuilt), and the backlog places this story in F6.2/E6 anyway** --
`agent="compositor"` is recorded rather than borrowing an identity that does not exist,
disclosed as a departure rather than silent. **ASSISTED, never a model call** -- the
markdown is a deterministic template, the same "real, reproducible, no inference boundary
to police" footing `modeller.py`'s grain-statement drafting and `redesign.py`'s own C4
suggestion already established; `ContractName.COMPOSITOR_REPORT_DOC` is a third "name
only" contract for the same reason.

**Generation is a deliberate, separate action, not automatic on every compose** -- the
same shape `deploy_workbook` (S6.1.2) already took relative to `compose_workbook`
(S6.1.1); composing is cheap and iterative, and spamming the artefact store with a fresh
documentation draft on every one of those composes serves nobody. This is unlike S6.2.1's
`ExceptionCase`, which the AC phrased causally and which therefore does open automatically
during compose.

**"Linked from the MU page"** is the same disclosed proxy ADRs 0045-0048 already used four
times: no MU page exists (F10.3, unbuilt), so `ReportDefinition.documentation_artefact_ref`/
`.documentation_provenance_ref` make the link real and queryable today, from the one real,
existing node this touches.

`POST /v1/workbooks/{id}:generate-documentation` (`MigrationEngineerDep`, the same
persona compose/deploy already use) and `GET /v1/workbooks/{id}:documentation`
(`C4RedesignReaderDep` -- any Artizent role or the report owner, reused verbatim from
`routes_redesign.py`'s own precedent) -- no new role.

See [ADR 0049](../../docs/adr/0049-report-documentation-a-deterministic-assisted-draft-linked-from-reportdefinition.md)
for the full reasoning.

## Grammar issues

A construct the adapter cannot read, raised as work by the Parse Quality Queue (S1.4.3).

**A platform record first, a ticket second.** §21 lists work tracking as *optional*,
one-way, and Azure DevOps or Jira "for clients who require it", with the mirror landing in
R1.1. A deployment with no tracker at all must still answer what gaps are open, what each
holds up and who raised it — so `grammar_issue` is a table, and `IssueTracker` is a port
whose production implementation, `LocalIssueTracker`, mirrors nothing and reports
`kind = "local"`. An empty `external_ref` therefore means *nobody was asked*, not *asked and
refused*. E12 fills the seam.

**One open issue per construct**, enforced by a partial unique index over
`state IN ('OPEN', 'IN_PROGRESS')` rather than a check-then-insert, because two engineers
clicking the same button at the same moment is exactly what a check-then-insert loses.
Resolving frees the construct to be raised again — a gap can come back when a later grammar
version stops reading a construct it used to read.

**The locations are a snapshot.** The construct text, up to 25 locations and the two counts
are copied in when the issue is opened, and the counts are named `occurrences_when_raised`
and `workbooks_held_when_raised` so nobody reads them as live. The estate moves: an issue
resolving its locations live would, months later, describe wherever the construct happens to
be now rather than the evidence it was raised on — and after a successful fix, nothing at
all. The live figures stay on the queue.

## Mutation events

Every write emits a CloudEvent, committed **in the same transaction as the mutation**:

| Type | When |
|---|---|
| `estate.node.upserted` | A node is created or replaced |
| `estate.edge.upserted` | An edge is created or replaced |
| `estate.node.retired` | A node is retired |
| `estate.edge.retired` | An edge is retired (S3.1.2 — an edge's endpoints cannot change once created, so a "move" retires the old relationship rather than mutating it) |
| `estate.source.drift` | A source workbook changed under an MU in progress |

The first four are mutations and are committed in the same transaction as the change they
record. The fifth is a **notice**: it records no graph change, so it commits on its own and
replay skips it. `EventType.mutates_graph` is the line between them, and the repository
refuses to append a mutating event outside a transaction so the distinction cannot be blurred
by accident.

```json
{
  "specversion": "1.0",
  "id": "01M1H1B172...",
  "source": "/astra/graph-svc/astra_estate",
  "type": "estate.node.upserted",
  "subject": "01M1H1B172...",
  "time": "2027-01-14T09:12:07.250Z",
  "principal": "agent:harvester",
  "runid": "run-01HX7",
  "sequence": "412",
  "data": { "type": "Workbook", "properties": { "...": "the whole node" } }
}
```

An event carries the element's complete post-write property set, not a patch, so replay
needs no prior state and applying an event twice is a no-op.

The events are an **outbox**, read with `GET /v1/events?after=<sequence>`. Publishing them
onto the platform bus (CloudEvents over Event Hubs, spec §5.4) is E12's; `published_at` on
the outbox row is where that hooks in.

## Retirement

Nothing is ever deleted. A node leaves the working estate by being retired:

```bash
curl -sS -X POST localhost:8080/v1/nodes/01M1H1B172.../:retire -H 'content-type: application/json' -H 'X-Astra-Principal: user:a.mehta@artizent.example' -d '{"reason":"Superseded by the risk overview page"}'
```

That stamps `retired_at`, `retired_by` and `retirement_reason` and leaves the node in the
graph. A reason is required. Reads — `node`, `neighbourhood`, `closure`, `step` — skip
retired nodes; pass `include_retired: true` to a neighbourhood to see them.

## Replay

The event stream is sufficient to rebuild the estate, and that is checked rather than
claimed:

```bash
make verify-replay
```

It rebuilds the graph from its events into a scratch AGE graph and compares element by
element — ids, labels, properties, edge endpoints — exiting non-zero and naming what
differs otherwise. A nightly CI job runs it against a seeded test estate
(`make seed`).

## Query logging

Every read writes one line to the `astra_graph.query` logger with the principal, roles,
duration, operation and element count. Results are never logged: a result can carry a
field name or a custom SQL literal the client classifies as restricted (spec §18.3). The
raw Cypher endpoint is the exception and records the query text, including for a rejected
query, so an auditor can see what was run.

## Layout

```
src/astra_graph/
  ontology/       node and edge types, property types, write-time validation
  graph/          Apache AGE repository, read queries, agtype encoding
  migrations/     versioned migrations and the runner
  api/            HTTP routes, request models, the generated GraphQL schema
  writes.py       validate-then-persist-with-its-event, the only write path
  events.py       CloudEvent construction
  adapters/       the §6.1 source adapter contract, and a fixture adapter
  harvest/        the Harvester, parse quality, and re-scoring
  credentials.py  credential references, resolved behind a provider
  provisioning.py reconciles the graph, its labels and its indexes
  replay.py       rebuild from events, and compare against the live graph
  cypher.py       the read-only Cypher guard
  contracts.py    named context contracts (spec §4.1.3)
  roles.py        roles and organisations (spec §2.4)
  observability.py  query logging
tools/
  migrate.py            apply migrations
  ontology_check.py     generated-reference and specification drift guards
  migration_check.py    breaking ontology change without a backfill guard
  seed_test_estate.py   write a test estate through the real write path
  verify_replay.py      rebuild from the event stream and compare
```

## A note on graph provisioning

Migration state is recorded once per database; the AGE graph, its labels and its indexes
are per *graph*. `make migrate` therefore **reconciles** the configured graph on every
run, not just when a migration is pending — otherwise pointing a deployment at a new
`ASTRA_GRAPH_NAME` would report "no pending migrations" and leave the graph uncreated.

## A note on Apache AGE

AGE caches label relations per database session. Dropping a graph, or changing its label
set, leaves that cache stale on any connection that has touched it; the next label
operation fails with `label (relation) cache corrupted` and PostgreSQL closes the
connection. **Recycle connection pools after any migration that adds a label** — migrate,
then restart the service. ADR 0003 has the detail.

## Working on the ontology

The ontology is code: `src/astra_graph/ontology/nodes.py` and `edges.py`. Changing it:

1. edit the declarations
2. `make ontology` — regenerates `docs/generated/ontology.md`
3. `make check` — fails if the schema and the specification disagree in a way that is not
   a declared deviation, and fails if a breaking change has no migration behind it
4. add a migration under `src/astra_graph/migrations/versions/` for each breaking change,
   declaring its backfill
5. `python tools/migration_check.py --write` to re-lock, and commit `ontology.lock.json`

## Tests

```bash
make test
```

Unit tests cover the ontology, the validator and the API against an in-memory store — no
database. Integration tests cover AGE itself and the migrations:

```bash
make test-integration
```

They need `make dev-up` and `make migrate` first, and are skipped when no database is
reachable. That suite includes the latency benchmark, which seeds a 1,000-workbook estate
and fails if a depth-3 neighbourhood exceeds the 300 ms p95 budget:

```bash
make bench
```
