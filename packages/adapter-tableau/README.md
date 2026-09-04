# astra-adapter-tableau — the Tableau source adapter

Specification §6.2. **Discovery, fetch, the whole parser, parity-case execution and visual
capture** (F2.2, F2.3, S2.4.1 and S2.4.2 — F2.4 complete), against Tableau Server 2021.4+ and
Tableau Cloud through one adapter.

> both deployment types in the BlackRock estate are covered by one adapter — S2.2.1

## What is built

| | |
|---|---|
| **Discovery** | The Metadata API (GraphQL) for the object graph, the REST API for what a download needs. Both paged. |
| **Fetch** | `.twb` and `.twbx`, the XML unpacked from the archive, with the revision id. |
| **Authentication** | Personal access token and connected app (a minted HS256 JWT). Sessions re-signed in when they expire. |
| **Throttling** | 429 backoff honouring `Retry-After`, and a per-site concurrency cap (default 4) that adapts. |
| **Deployment** | Server or Cloud detected, not configured; the version recorded per site; below 2021.4 refused. |
| **Datasources** | Published and embedded, with their connection graph: Datasource → Connection → Table → Field. Custom SQL byte-for-byte. |
| **Extracts** | Detected with their schema and refresh schedule. The data is never read. |
| **Credentials** | Stripped from the workbook at parse time; the connection references a Key Vault secret by *name*. |
| **Calculations** | A Lark grammar over Appendix B.1: LOD, table calcs, casts, string, date, logical and aggregate functions. Every AST node carries a source span. |
| **Sheets** | Mark type, shelves, encodings, sorts, reference lines, and filters typed as §4.1.1's enum — categorical, range, relative-date, top-N, condition — with their values. |
| **Dashboards** | Size and the nested zone tree, sheet placements, and all five action kinds. |
| **Security** | Row-level security detected from user filters, `ISMEMBEROF` and `USERNAME`, recorded on the Workbook with the expression. |
| **Custom SQL** | Kept byte-for-byte, and read in the connection's own dialect (Snowflake, T-SQL, Postgres) — referenced tables become estate nodes; what cannot be attributed is flagged. |
| **Execution** | A parity case run against Tableau itself by one of §6.2's three strategies, chosen from the charter and this deployment's capabilities. Ordered typed columns, nulls preserved, a 120 s budget, and the strategy on the result. |
| **Visual capture** | A sheet or dashboard rendered through REST `queryViewImage` and resized with Pillow to exactly the size asked for. §10.6's advisory perceptual comparison needs two images the same size; Tableau's own endpoint has no notion of one. |

## What is not

**Usage and ownership** are not queried yet.

Two of §6.2's three execution strategies are unavailable on the default deployment, and each
says why rather than pretending (`ports.py`, and Platform Health shows it):

| Strategy | State | Why |
|---|---|---|
| `VIEW_DATA` | **available** | needs only the REST API this adapter already speaks |
| `EXTRACT_READ` | absent | needs `tableauhyperapi`, which ships under Tableau's licence rather than an open-source one. **A client decision** — install it and the strategy becomes available with no code change. |
| `LIVE_REPLAY` | absent | needs a driver for the connection's class and egress to the client's warehouse under their service account. Both arrive with E11. |

A stub reader returning empty rows would be worse than the absence: it would be a parity case
that passed against nothing.

## Conformance

**This adapter passes**, for the capabilities it claims:

```
PASSED   interface version          interface 1.1, adapter tableau 0.1.0, grammar tableau-1
PASSED   discovery completeness     all 5 assets enumerated exactly once
PASSED   parse quality              mean 1.000, lowest 1.000 (floor 0.98)
PASSED   parse round-trip           5 assets parsed identically and survived the wire
PASSED   AST round-trip             all 40 expressions round-tripped
PASSED   AST coverage               100% of 40 golden expressions; 8 AST node kinds exercised
PASSED   executor determinism       3 of 3 cases identical across 3 runs, stamped interface 1.1
PASSED   visual capture             2 views captured at the size requested
SKIPPED  usage and ownership        neither is claimed
PASSED   error taxonomy             transient → retryable; unauthorised → not
PASSED   throttling                 backs off, and surfaces persistent throttling
```

**Passing means "does what it says", not "does everything".** The two skips are capabilities
this adapter does not claim — §6.1 makes an unclaimed capability a fact about the deployment,
so the suite skips them and says so.

**Interface 1.1 invalidates every existing promotion.** S2.4.1 retyped `ResultSet.columns`,
which is a removal rather than an addition, so ADR 0015's rule bumps the version. The
promotion gate compares the whole build including its interface version — a tenant that
promoted the 1.0 build will see its next harvest refused until the suite is re-run and the
1.1 build promoted. That is the gate working: a report written against 1.0 is evidence about
an adapter that answered a different question.

## Configuration

A worker serves **one deployment and one site** — §5.2 scales `adapter-tableau` "per site
parallelism" — so it is configured from its own environment and the credential never crosses
the adapter RPC. The platform names a credential; it does not send one.

```bash
ASTRA_TABLEAU_URL=https://tableau.client.example
ASTRA_TABLEAU_SITE=rqa
ASTRA_TABLEAU_CREDENTIAL='{"kind":"personal_access_token","token_name":"astra","secret":"..."}'
ASTRA_TABLEAU_CONCURRENCY=4
```

A connected app instead of a token:

```bash
ASTRA_TABLEAU_CREDENTIAL='{"kind":"connected_app","client_id":"...","secret_id":"...","secret":"...","username":"svc.astra@client.example"}'
```

A connected app is what a deployment should reach for. A personal access token belongs to a
person and Tableau invalidates its previous session when it is reused, so two workers sharing
one repeatedly sign each other out — which looks exactly like an intermittent authentication
failure.

## Running it

```bash
astra-tableau --port 8090
```

The platform then harvests through it by setting `ASTRA_ADAPTER_URL` to that address.

## The golden corpus

§6.3 makes a corpus part of what an adapter *is*. For a source adapter that corpus has to
include a **source**: a set of `.twbx` files on disk cannot exercise discovery, paging,
session expiry or throttling, and half of S2.2.1 is about exactly those.

So the corpus is a deployment, and it ships with the adapter:

```bash
astra-tableau-golden --port 8099
```

```bash
astra-adapter conformance --adapter tableau
```

On tenant enablement it is replaced by a client-provided sample — a real Tableau site, and the
same suite pointed elsewhere.

## Design notes

**The content hash is over the XML, not the download.** A `.twbx` zip is not byte-stable — it
records timestamps and orders entries as Tableau pleases — so hashing the download would make
every re-harvest look like a change, and S1.2.4's incremental harvest would download the whole
estate every night.

**The extract is never read.** Two lines of defence: the download asks Tableau not to include
one, and the archive reader reads the `.twb` entry and never a data entry. §16 and S2.2.2 both
require it, and the second line matters because a Tableau version that ignores the flag, or a
proxy serving a cached copy, would otherwise put a client's data in the platform's memory. The
extract's *name* is recorded — the Modeller needs to know where data comes from.

**Every call goes through one authenticated path.** Session, concurrency slot, 429 backoff and
401 re-sign-in interact: a 401 raised while backing off must not sign in twice, and a
re-sign-in must not spend a retry budget belonging to throttling. An earlier version gave the
download its own loop with the backoff but not the re-sign-in — and the download is where a
long harvest spends its time.

**The 2021.4 floor is checked before a credential is presented.** And a rate-limited
deployment is not mistaken for an old one: `/serverinfo` is the first call, the least likely
to be throttled, and therefore the one where a missing backoff produced a confidently wrong
diagnosis.

**Credentials never leave the workbook.** A `.twb` is XML a person edited, and real ones carry
`username=` and sometimes `password=`. Anything on the denylist is removed from the attribute
dictionary *before a Connection object exists* — filtering later would leave a window in which
a credential sits in a live object, and a future code path reading the element directly would
miss the filter. A denylist of **names**, not a heuristic on values: a value-based rule both
misses and over-matches, and a reviewer cannot tell what it will do.

What the platform gets instead is a **Key Vault secret name**, derived from the connection's
identity so that two workbooks reaching the same warehouse produce the same reference and a
client provisions one secret rather than four hundred. The hostname is hashed — a secret name
is not especially secret, but an internal hostname is exactly what ends up in a screenshot in
a status deck.

**An extract's schema is metadata; its rows are the client's.** `includeExtract=false` removes
the `.hyper` file and keeps the `<extract>` element describing it, which is how the adapter
records what Tableau materialised without downloading a row.

**Custom SQL becomes lineage, or it becomes a work item.** A custom-SQL relation is an opaque
string as far as the estate is concerned: the Modeller sees a Table called "Custom SQL Query"
and plans a Fabric model around a hole — *confidently*, because nothing says a hole is there.
The dialect comes from `Connection.class`, so nobody is asked and nobody can answer wrongly;
`SELECT TOP 10` is a syntax error in Postgres and `QUALIFY` is one everywhere but Snowflake.
Three outcomes, not two: parsed, parsed-but-`SELECT *` (lineage complete, columns unknowable),
and unparsed — retained verbatim, flagged, and counting against parse quality.

**A filter is part of the question, not metadata.** §10.2 derives parity cases at the grain a
sheet shows, so a case derived without the sheet's filters compares a report nobody has — the
client's dashboard shows last quarter's top ten desks. Tableau writes a top-N as a
*categorical* filter carrying a `groupfilter`, so reading the class alone would type it
categorical and lose the thing that makes it a top-N: the filter a case most needs to respect.

**Row-level security is a finding.** A workbook whose rows depend on who is looking has an
access model the target must reproduce — and §10's parity cases run under a service identity
that sees everything, so a case derived from a user-filtered sheet proves the unrestricted view
and nothing else. Detected from user filters, `ISMEMBEROF`, `USERNAME` and their family;
recorded on the Workbook with the expression verbatim, because "this workbook restricts rows"
without saying how is not actionable.

**The grammar accepts more than the platform recognises.** `tableau.lark` has no list of
function names — any `NAME(...)` parses, and `functions.py` decides whether the platform knows
it. A grammar that enumerated names would reject a valid calculation the moment Tableau shipped
a function, turning "we do not recognise this" (a queue item) into "this workbook will not
parse" (a harvest failure). An unrecognised call still yields a FUNCTION node with its
arguments: the Modeller can see which fields it depends on whether or not the Transpiler can
emit it.

**Canonical text is the language, normalised.** Not the classification: an earlier version
printed `SUM{family=aggregate}(...)`, which no source grammar can read back, and `IF(a, b, c)`,
which round-tripped *by text* while silently becoming a FUNCTION node. A check reporting a
stability it does not have is worse than no check.

**Spans are carried and excluded from shape.** Every node knows the characters it came from, so
a parity failure can underline the offending divide. Two calculations differing only in
whitespace are the same calculation, so `without_spans()` is what pattern matching compares.

**Throttling is adaptive, crudely.** The cap halves on a 429 and climbs back after twenty
clean calls. Not a control loop: a Tableau site's limits are unpublished and unstable, so a
model of them would be a model of a guess. Jitter is derived from the site name rather than
random, so a harvest's timings can be replayed when somebody has to investigate them.

**The expected side is fetched, never derived.** By the end of F2.3 the platform holds a parsed
AST for every calculation and the SQL underneath it, and could compute what a sheet ought to
show. It would be faster and it would prove nothing: a disagreement between our number and
Power BI's is a disagreement between two of our own implementations. The client's question is
whether *their report* still says what it said, and only their report can answer it.

**A per-case charter override is refused, not downgraded.** When the charter names a strategy
for one case and this deployment cannot perform it, the case comes back INCONCLUSIVE. A
per-case override exists because somebody agreed *this* case would be proved *that* way;
silently using another produces evidence of a kind nobody agreed to, under a case id that says
otherwise. A charter-wide order is a preference and does fall through — listing extract read
first on a deployment with no Hyper API is a charter written before somebody looked.

**Tableau has two spellings of null and Python's `csv` hides both.** An empty field means null;
the literal `%null%` means a null aggregate; `csv` returns `''` for both. §4.4's charter has one
rule for `source_null_vs_target_zero` (FAIL) and another for `source_null_vs_target_blank`
(PASS), so coercing here would decide a verdict the charter is supposed to decide. The golden
deployment serves both spellings, so the suite would notice if that stopped being true.

**A screenshot is resized, never invented.** Tableau's `queryViewImage` has no notion of a
caller-chosen size — it renders at the workbook's own layout, with only a DPI choice. §10.6's
perceptual comparison needs two images the same size to mean anything, so this adapter fetches
Tableau's real render and resizes it to exactly what was asked for with Pillow. The golden
deployment's own render is a fixed 960x720 regardless of what is requested, deliberately —
a capture at the default 1200x800 only matches because the adapter changed it, which is what
proves the resize path runs rather than coinciding.

**A sheet and a dashboard share one lookup.** Tableau's views listing does not distinguish
them — both are published views with an id and a name — so `views.resolve_view_id` serves a
parity case's sheet and a visual case's dashboard alike, and "not a published view" means the
same thing in both.
