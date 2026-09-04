# ADR 0020 — The expected side comes from Tableau

Status: accepted · 3 September 2026 · Story S2.4.1 (E2 / F2.4)

## Context

S2.4.1 asks the adapter to execute a parity case on the source side and return a typed
`ResultSet`, *"so that the expected side of every proof comes from Tableau itself, not from a
re-implementation."*

That *so-that* is the whole design constraint, and it forecloses the cheaper option. By the
end of F2.3 the platform holds a parsed AST for every calculation, a filter and parameter
context for every sheet, and the datasource SQL underneath. It could compute the expected
answer from those. It would be faster, it would need no credential, and it would run offline.

It would also prove nothing. A disagreement between a number the platform computed from a
Tableau workbook and a number Power BI computed from a translated model is a disagreement
between two of our own implementations. The client's question is whether **their report**
still says what it said, and only their report can answer it. So the expected side is fetched,
never derived, and the executor's job is to ask Tableau and carry back what it said.

## Decisions

### 1. Three strategies, and the strategy is on the record

§6.2 names extract read, view data and live replay. They are not fallbacks for one another —
they are three different kinds of evidence:

| Strategy | What it proves | What it needs |
|---|---|---|
| `EXTRACT_READ` | what the published `.hyper` contains, which is what the report rendered from | Tableau's Hyper API |
| `VIEW_DATA` | what the sheet shows a user, filters and parameters applied | the REST API, nothing else |
| `LIVE_REPLAY` | what the warehouse returns *now* | a driver, and network access under the client's service account |

They can legitimately disagree, and the commonest finding in a real migration is exactly that
disagreement: a stale extract against a live warehouse. A verdict that did not say which
strategy produced its expected side could not be audited, so `ResultSet.strategy` is not
optional and `ParityRunStamp` carries it up to the run.

### 2. A per-case charter override is refused, not downgraded

The charter (§4.4) orders the strategies and may name one for a specific case. When the named
strategy is unavailable, the executor returns INCONCLUSIVE rather than quietly using the next
one down.

This is the sharpest judgement in the story. A per-case override exists because somebody
agreed *this* case would be proved *that* way — usually because the case is contentious and
the client asked for a live replay rather than an extract read. Substituting another strategy
produces evidence of a kind nobody agreed to, under a case id that says it is the agreed kind.
A verdict resting on that is worse than no verdict, because it is believed.

A charter-wide preference is different and does fall through: an order is a preference, and
listing extract read first on a deployment with no Hyper API is a charter written before
somebody looked.

### 3. "Cannot execute" is an outcome, not an exception

`ExecutionOutcome.INCONCLUSIVE` with a reason, and §10.2 forced the shape: *"a timeout on
either side yields INCONCLUSIVE, not FAIL, and is retried once with a longer budget before
being surfaced."*

A timeout recorded as a failure puts a Migration Unit into remediation over a slow warehouse,
and somebody spends a day looking for a bug in a report that is correct. The same reasoning
covers a missing extract reader, a refused override, a sheet that is not published, and an
adapter error from the source: none is evidence that the client's report is wrong. The default
budget is 120 seconds, and the reason carries the elapsed time and the budget, because "it
timed out" and "it timed out after 120 s having read nothing" send an engineer to different
places.

`ResultSet.comparable` is the single place that decides whether §10.3 may diff a result —
INCONCLUSIVE or truncated, and it may not.

### 4. Columns carry a role, and the interface version goes to 1.1

§10.2 asks for "an ordered list of column descriptors (name, role, type)". `ResultSet.columns`
was `tuple[str, ...]`; it is now `tuple[Column, ...]`.

The role is load-bearing, not decoration: §10.1 splits a case into a grain and its measures,
and §10.3 *matches* rows on the grain and *compares* measures under the charter's tolerances.
Columns without roles would make the Proof Engine guess which is which, and it would guess
from the name.

The type is the source's own type name, not a normalised one, because §10.3's normalisation
needs to know what it is normalising from — a Tableau `real` and a DAX `Decimal` round
differently, and `currency_scale` applies to one of them.

Retyping a field is not additive, so ADR 0015's rule bites: **INTERFACE_VERSION 1.0 → 1.1**.
The consequence is deliberate and visible. The promotion gate compares the whole build
including its interface version, so a tenant that promoted the 1.0 build now refuses the 1.1
one until the conformance suite is re-run. The adapter's code did not regress and its own
version could plausibly have stayed the same — but every result set it produces has a
different shape, and a report written against 1.0 is evidence about an adapter that answered a
different question.

### 5. Nulls are preserved as nulls

Tableau writes an empty field for a null in view-data CSV and the literal `%null%` for a null
aggregate. Python's `csv` returns `''` for both.

§4.4's charter has one rule for `source_null_vs_target_zero` (FAIL) and a different one for
`source_null_vs_target_blank` (PASS). An executor that coerced a null to an empty string or a
zero would decide a verdict the charter is supposed to decide, silently, in the adapter. Both
spellings become `None`, and the golden corpus serves both so the suite would notice if that
stopped being true.

### 6. The two strategies this deployment cannot perform are ports that say why

`ports.py` defines `ExtractReader` and `LiveQueryRunner`, with null implementations that
report `kind = "absent"` and a `detail` explaining the absence. Platform Health shows them.

**Extract read** needs `tableauhyperapi`, the only way to read a `.hyper`. It ships under
**Tableau's own licence, not an open-source one**, and this platform's standing constraint is
open-source components it can containerise freely. That is a client decision, not an
engineering one: a client who accepts Tableau's SDK terms installs the package and the
strategy becomes available with no code change.

**Live replay** needs a driver per connection class and egress to the client's warehouse under
their service account. Both arrive with E11 — Key Vault for the credential, the egress policy
for the route.

The alternative — returning an empty result set — would be a parity case that passed against
nothing, which is the failure mode this platform exists to prevent. An operator asking "why is
every case inconclusive" finds the answer on Platform Health rather than in a log grep, and
the answer is usually a licence decision nobody has made, which is not something they would
guess.

Capabilities are therefore computed from the deployment rather than declared as a constant.
S2.1.2 makes a claim binding — the suite fails an adapter that claims a capability and cannot
deliver it — so claiming `extract_read` in code that might run without the Hyper API would
make conformance check something that does not exist.

### 7. The conformance gate stopped asking about capabilities and started asking about behaviour

§6.3's determinism check previously skipped unless the adapter claimed `extract_read` or
`live_query`. That was right while those were the only ways to execute. View data needs
neither.

Left alone, the check would have skipped forever on an adapter that executes every case — and
a check that cannot fail is worse than no check, because it converts an unknown into a false
assurance somebody relies on. So the check now runs the cases: an adapter that genuinely
cannot execute returns INCONCLUSIVE with a reason and the check skips *with that reason
printed*; an adapter that claims extract read or live query and still cannot execute has made
a claim it did not honour, and that fails.

The golden corpus gained three parity cases against its own published views, so the check runs
rather than skips on every CI run. One of them carries a filter, because filters reach Tableau
as `vf_` parameters and a corpus of unfiltered cases would let a broken filter path through
the gate while every case stayed green.

## Consequences

- The expected side of every proof is a fetch from the source, so parity requires a live,
  credentialled Tableau. Offline parity is not on the table, and that is the point.
- Two of three strategies are unavailable on the default deployment. Parity by view data is
  real and complete today; extract read waits on a licence decision and live replay on E11.
- Every tenant with a promoted Tableau adapter must re-run conformance and re-promote before
  the next harvest. The gate says so, by name, with the command to run.
- `ResultSet` consumers must read `outcome` before reading `rows`. `comparable` exists so
  there is one right way to ask.

## Alternatives considered

**Compute the expected side from the AST.** Rejected on the story's own *so-that*. It would
compare two of our implementations and call the agreement proof.

**Fall back through the strategy order for per-case overrides too.** Rejected: see decision 2.
Uniformity here would be a smaller rule that produces unaudited evidence.

**Ship a stub extract reader returning empty rows so the capability can be claimed.** Rejected
outright. It converts "we cannot check this" into "we checked this and it was fine", which is
the one failure this platform cannot have.

**Raise on a case that cannot be executed.** Rejected: an exception is indistinguishable from
a broken adapter, and §10.2 requires the distinction between "the report is wrong" and "we
could not tell".
