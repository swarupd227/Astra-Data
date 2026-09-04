# ADR 0019 — Custom SQL becomes lineage, or it becomes a work item

Status: accepted · 3 September 2026 · Story S2.3.3 (E2 / F2.3)

## Context

S2.3.3 asks for custom SQL captured verbatim and parsed where possible, *"so that custom SQL
becomes a Modeller input rather than a surprise."*

The surprise is the thing being designed away, and it is worth naming precisely. A Tableau
custom-SQL relation is an opaque string as far as the estate is concerned. The Modeller opens
the Estate Explorer, sees a Table called "Custom SQL Query", and plans a Fabric model around a
hole — **confidently**, because nothing in the estate says a hole is there. The failure is not
that the SQL is unread; it is that the estate looks complete while it is unread.

## Decisions

### 1. `sqlglot`, not a hand-rolled parser

A real SQL parser with real dialects. The alternative — regular expressions over `FROM` and
`JOIN` — works on the SQL in a demo and fails on a CTE, a subquery, a comment containing the
word `from`, or a table name that happens to be a keyword. This is the wrong thing in the
world to hand-roll, and §5.4's technology commitments are already comfortable with a
third-party parser (Lark is in the same package for the calc grammar).

MIT-licensed, pure Python, no build dependencies — which matters because it ships inside an
adapter worker image.

### 2. The dialect comes from the connection, not from configuration

§4.1.1 already records `Connection.class`, so the adapter knows whether the SQL is Snowflake,
T-SQL or Postgres before it reads a character. Nobody has to be asked, and nobody can be told
the wrong thing.

It matters more than it sounds. `SELECT TOP 10` is a syntax error in Postgres, `QUALIFY` is a
syntax error everywhere but Snowflake, and `::` casts are a syntax error outside Postgres.
Reading every client's SQL with one dialect would work on the ordinary 90% and fail on the
part with the money in it.

An unmapped class falls back to a permissive dialect rather than refusing. Most custom SQL is
ordinary `SELECT … FROM … JOIN`, and a generic read extracts the same table names.

### 3. Verbatim first; the extraction is additive

§4.1.1 requires `custom_sql` byte-for-byte because §6.2's live-replay strategy re-executes it,
and a normalised copy would execute differently from the client's report. Nothing rewrites the
SQL. The extracted tables, columns and dialect are extra properties on the same node, and the
original survives whether or not it parsed.

### 4. Referenced tables become Table nodes

That is what makes custom SQL an *input*: the Modeller sees `risk.positions` in the estate
rather than a string. They are emitted under the same Connection — which is what they are,
tables this connection reads — and their names are also on the custom-SQL Table's
`custom_sql_tables` so the linkage is readable without a traversal.

§4.1.2 has no Table→Table edge, and inventing one for this would be an ontology change for one
adapter's convenience. Same judgement as ADR 0018 made for Action and Parameter.

### 5. Three outcomes, not two

- **Parsed**: tables and columns extracted, quality unaffected.
- **`SELECT *`**: parsed, lineage complete, **columns unknowable from the text**, flagged as
  such. Distinct from a failure, because a Modeller planning a Fabric table needs to know
  which of those they are looking at.
- **Unparsed**: retained verbatim, flagged as an unrecognised construct, counts against parse
  quality (S2.3.3's second criterion).

A fourth case sits with the third: SQL that *parses* but names no source table — a
stored-procedure call, a table-valued function, `SELECT 1`. It is reported as unattributable
rather than as a success with an empty list, because the Modeller still has a hole and the
estate should say so.

### 6. The parser never raises

A client's custom SQL is fifteen years of accumulated ideas, including SQL that was already
broken when Tableau cached its results. An adapter that raised on the first of them would fail
a harvest over a string it was only ever going to record. §16.5 treats source content as
untrusted, and the broad `except` around `sqlglot` is deliberate: a dependency mishandling a
shape must degrade to "unparsed, here is why".

## Consequences

- Parse quality now counts custom-SQL relations as constructs. A workbook whose entire
  datasource is an unreadable query would otherwise have scored 1.0 on its structure.
- The adapter still passes conformance: 8 / 0 / 3.
- `sqlglot` is the adapter's third dependency, after `httpx`, `pyjwt` and `lark`.

## What building it found

Nothing broke, which is worth recording as much as a defect would be — the seams S2.2.2 and
S2.3.1 put in place (a `Table` model with a slot for parsed detail, a parse-quality counter
that takes constructs rather than calculations) took this story without modification. The one
thing that needed care was the CTE case: a naive table walk reports a CTE's name as a source
table, which would send the Modeller looking for a warehouse table that does not exist.

## Open questions for the product owner

1. **Column extraction is shallower than table extraction.** `SELECT *` yields no columns, and
   the ones it does yield are the outermost projection's aliases — not their source columns.
   Full column-level lineage needs schema knowledge sqlglot can use but the platform does not
   have (`sqlglot.optimizer` can qualify columns given a schema). Whether that is worth doing
   before the Modeller asks for it is a sequencing question.
2. **A referenced table is not reconciled with the estate.** If `risk.positions` is also read
   by an ordinary relation in another workbook, the platform now has two Table nodes for it.
   Reconciling them is what makes "which reports break if this table changes" answerable, and
   it is the same graph-identity decision ADR 0016 raised for published datasources — the
   Cartographer's, in E3.
3. **Nothing reports the estate's custom-SQL exposure.** The adapter knows which workbooks
   contain unreadable SQL; nothing aggregates "31 workbooks contain custom SQL, 4 of it
   unreadable" for a programme deciding scope. The Parse Quality Queue shows the unreadable
   ones as constructs, which is close but not the same question.
4. **Dialect coverage is three of nine.** §4.1.1's `Connection.class` enum has nine members;
   the story names three, and Hive and Sybase are mapped speculatively. A client on Teradata or
   MarkLogic gets the generic dialect — which will usually work and will occasionally be
   wrong in a way nothing detects.
