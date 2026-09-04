# ADR 0016 — Datasources are parsed, extracts are described, credentials are never kept

Status: accepted · 3 September 2026 · Story S2.2.2 (E2 / F2.2)

## Context

S2.2.2 asks for published datasources "captured as Datasource nodes", embedded extracts
detected with their schema and refresh schedule, and connection credentials never stored.

The first of those forced a decision the previous story had deferred. Nodes reach the Estate
Graph through §6.1's `parse`, and S2.2.1 left `parse` raising "F2.3 builds this" on the
argument that a partial fragment produces an estate that looks harvested and contains
nothing. S2.2.2 asks for nodes, so `parse` has to return something.

## Decisions

### 1. `parse` returns a real, partial fragment — and scores itself honestly

It emits everything the `<datasources>` section supports: `Datasource`, `Connection`,
`Table`, `Field`, with the `Workbook`, `Worksheet` and `Dashboard` nodes they hang off.

It does **not** emit `CalculatedField`. A CalculatedField without an AST is half a node and
the AST is S2.3.1's. The calculations are counted and **retained verbatim as unrecognised
constructs** instead — §6.2's own instruction — so parse quality lands at about 0.88 and the
workbook is *held* by §4.1.4's threshold.

That number is the point. The objection in S2.2.1 was not to partial fragments; it was to a
partial fragment reporting itself as complete. Counting only what this story attempts would
report 1.0 on a workbook whose calculations nobody has read, and the estate would look
finished. Instead the Parse Quality Queue shows these workbooks with the formulas themselves,
and when F2.3 lands they re-parse to 1.0 and release — exactly the behaviour S1.4.3 built the
queue to show.

### 2. Sheet *names* are read; sheet contents are not

§4.1.2 runs `USES_DATASOURCE` from Worksheet, so a datasource with no worksheet is an orphan
the platform cannot place. Reading the names is what makes this story's fragment connected.
Filters, encodings, layout and actions are S2.3.2's and none of them are touched.

### 3. `type: published`, not `published: true`

The story says "published: true"; §4.1.1's table says `type: embedded | published`, and the
ontology is machine-checked against that table. The backlog's own rule is that the
specification wins, so the node carries `type`. Same fact, one name, and the ontology guard
stays meaningful.

### 4. Credentials are stripped at the point of parsing, not filtered on the way out

A `.twb` is XML a person edited, and real workbooks carry `username=` and sometimes
`password=` on their connections. Anything on the denylist is removed from the attribute
dictionary **before a Connection object exists**.

Filtering later would leave a window in which a credential sits in a live object, and a future
code path that read the element directly would miss the filter entirely. This way the secret
is never in anything the rest of the adapter can see.

A **denylist of names**, not a heuristic on values. A value-based rule ("looks like a
password") both misses and over-matches, and a reviewer cannot tell what it will do. A name
that is not on the list and turns out to carry a secret is a bug fixed by adding the name —
a one-line, reviewable change.

`username` is on the list. It is not a secret, but it is half a credential and client PII, and
the platform has no use for it: ownership comes from the directory (S1.2.3), not from a
connection string.

### 5. The Key Vault reference is derived, and does not spell out the hostname

S2.2.2's third criterion asks the adapter to "reference a Key Vault secret by name".

Derived from the connection's identity — class, server, database — so two workbooks reaching
the same warehouse produce the same reference and a client provisions one secret rather than
four hundred. Asking an operator to enumerate connections before the first harvest would make
the harvest wait on a spreadsheet; deriving means the platform can *report* the list instead,
which `sites()` does.

The host is hashed. A Key Vault secret name is not especially secret, but an internal hostname
is exactly the kind of thing that ends up in a screenshot in a status deck, and the reference
only has to be stable and unique. The class and database stay legible so an operator can tell
which of a dozen references is which.

### 6. An extract's schema is metadata; its rows are the client's

The `<extract>` element and the datasource's columns give the schema — what Tableau
materialised. That is what a Modeller needs to plan a Fabric table, and it is not data.

Both the "last refreshed" timestamp (Metadata API) and the governing schedule (REST
extract-refresh tasks) are recorded, because they answer different questions: "refreshes
nightly at 02:00" and "last refreshed nine weeks ago" tell a programme very different things,
and the second is what reveals an abandoned report.

Refresh tasks need a site administrator on many deployments. A content-reader credential gets
a 403, and that is reported as "not visible to this credential" rather than as no schedule —
a harvest run by a content reader is a legitimate and common configuration.

### 7. An unmapped connection class is reported, not guessed

§4.1.1's `Connection.class` enum is closed and the platform rejects a write outside it.
Tableau has many more class names than nine, so they are mapped — and one with no mapping
emits no Connection node and logs what it was. A guess would be rejected at the graph, with an
error about the graph rather than about the connection it came from.

## Consequences

- The Tableau adapter's conformance moved from 4 passed / 4 failed to **5 passed / 3 failed**:
  parse round-trip now passes, and parse quality reports 0.885 rather than "nothing was
  parsed". The three remaining failures are all F2.3's grammar.
- `sites()` now reports the extract-refresh schedules and the Key Vault secret names the
  site's connections need — lists an operator can act on, rather than facts discovered when
  the executor first tries to run a parity case months later.

## What building it found

1. **Every datasource was flagged as having an extract.** The workbook-level fact (a `.hyper`
   in the archive) was folded into the per-datasource property, so the Parameters
   pseudo-datasource — which has no data at all — was reported as extracted.
2. **`[dbo].[fx_rates]` split into `dbo]` and `[fx_rates`.** Unbracketing the whole string
   before splitting is wrong in a way that looks right in a log line and produces a Table node
   nothing can be matched against.
3. **The extract engine was modelled as a connection.** Tableau writes a `dataengine`
   connection inside an extracted datasource, pointing at the `.hyper` in the package. It is
   the extract, not somewhere data comes from — and it was being given a derived Key Vault
   reference for a credential that does not exist.
4. **The golden corpus modelled Tableau wrongly.** `includeExtract=false` removes the `.hyper`
   *file* and keeps the `<extract>` element that describes it. The fake dropped both, which
   hid the exact asymmetry the whole criterion rests on: it is how an adapter records an
   extract's schema without downloading a row of client data.

## Open questions for the product owner

1. **A published datasource is captured per workbook, not once.** Forty workbooks using the
   same published datasource produce forty `Datasource` nodes with the same LUID. Deduplicating
   them is what makes "which reports break if this datasource changes" answerable — and it is
   a graph-identity decision (§4.1's ids are derived from source identity) rather than an
   adapter one, so it belongs with the Cartographer in E3.
2. **The reference names a secret nobody has created.** The adapter reports which Key Vault
   secrets a site's connections would need; nothing provisions them, and nothing checks they
   exist until the executor asks. A missing secret is currently discovered at parity time,
   which is the most expensive moment to discover it.
3. **Embedded credentials are a finding with no home.** The adapter logs them and records that
   the connection had one, but there is no place in the platform where "these eleven workbooks
   contain embedded passwords that must be rotated" is a tracked item. §14's gate machinery or
   the grammar-issue record from S1.4.3 are both plausible homes.
4. **Connection classes outside §4.1.1's nine are dropped.** ADR 0001 already flagged the
   closed enum as an open question; this is the first story where it bites, because a client
   with a MarkLogic or a Teradata source now silently loses that Connection node. Whether the
   enum should be extended, or `Connection.class` opened with a validated set, is a
   specification question rather than an adapter one.
