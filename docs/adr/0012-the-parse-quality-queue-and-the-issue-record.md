# ADR 0012 — The Parse Quality Queue is worked construct-first, and an issue is a platform record

Status: accepted · 3 September 2026 · Story S1.4.3 (E1 / F1.4)

## Context

§15.3.2 lists a Parse Quality Queue among the console's screens, and S1.4.3 states the
purpose: *so that the grammar gaps are worked down before the Calibration Wave*. Its
acceptance criteria are a list of workbooks under the threshold, unrecognised constructs
grouped with their estate-wide frequency, the number of workbooks each fix would release,
and three actions — mark ignorable, open a grammar issue, request a re-harvest.

The store behind it already existed: S1.2.2 built the parse-quality records, the accepted-
construct decision and the rescorer, and ADR 0005 settled what accepting a construct means.
What did not exist was the screen, the issue record, and the ordering the screen implies.

## Decisions

### 1. The queue is a list of constructs, not a list of workbooks

§15.3.2 names both, and S1.4.3's criteria ask for both. But the criteria also ask for "the
number of workbooks each construct fix would release", and that number only makes sense if
constructs are the rows. One grammar gap held 23 of the demo estate's 65 workbooks; working
the queue workbook-by-workbook would have opened the same gap 23 times.

So the centre pane is one row per construct, ordered by `workbooks_released_if_resolved`
descending. The held workbooks are the left pane — "which of mine is stuck" is a fair
question and the criteria ask for it, but it is not the working order.

### 2. "Releases" is not "occurs in"

Two different numbers, both on the row:

- **Workbooks** — how many contain the construct at all, held or not.
- **Releases** — how many *held* workbooks have this as their **only** remaining
  unrecognised construct, so resolving it alone lifts them above the threshold.

A construct in thirty workbooks that releases none is real work with no immediate payoff,
and the screen says so in words rather than showing a dash and leaving it ambiguous: *every
workbook holding it has other gaps too. It still has to be fixed — it is just not the one to
start with.*

The queue is ordered on Releases, then Workbooks, then Occurrences. Accepting a construct
re-reads the queue, and the remaining constructs' Releases figures rise as the workbooks
they share get closer to the threshold. Verified on the demo estate: accepting
`WINDOW_SUM` (predicted 4) released exactly 4, and `RAWSQL_INT`'s figure moved 18 → 19.

### 3. A grammar issue is a platform record first, a ticket second

§21's integration table lists work tracking as *optional*, one-way, and Azure DevOps or Jira
"for clients who require it", with the mirror landing in R1.1. A deployment with no tracker
at all must still be able to answer what gaps are open, what each holds up, and who raised
it.

So `grammar_issue` is a table. `IssueTracker` is a port with `LocalIssueTracker` as its
production implementation: it mirrors nothing and reports `kind = "local"`, so an empty
`external_ref` means *nobody was asked*, not *asked and refused*. The console says it in
words — "No work tracker is configured, so it is held here." E12 fills the seam.

This is the same pattern as `CredentialProvider`, `DirectoryResolver` and
`MigrationUnitRegistry`: a null implementation that reports its own kind, never fake data.

### 4. One open issue per construct, enforced by a partial unique index

`WHERE state IN ('OPEN', 'IN_PROGRESS')`. A second issue for a construct is not a second
problem; it is two people raising the same one, and the queue would then show a construct as
blocked twice. Resolving frees the construct to be raised again, because a gap can come back
— a later grammar version can stop reading a construct it used to read.

The constraint lives in the database rather than in a check-then-insert, because two
engineers clicking the same button at the same moment is precisely the case a check-then-
insert loses. The store translates the violation into a sentence rather than a 500.

### 5. The locations on an issue are a snapshot

The construct text, its locations and the two counts are copied in when the issue is opened,
and the counts are named `occurrences_when_raised` and `workbooks_held_when_raised` on the
wire so nobody reads them as live.

The estate moves. An issue that resolved its locations live would, months later, describe
wherever the construct happens to be *now* rather than the evidence it was raised on — and
after a successful grammar fix it would describe nothing at all. The live figures stay on
the queue, which is where a live figure belongs.

Capped at 25 locations: enough to show a gap is systemic, not an export of the estate.

### 6. Re-harvest is per site, and says so

The third action is "request re-harvest". The harvester's unit of work is a site, so the
button re-harvests the site the construct appears in, and its tooltip says that rather than
implying a construct-scoped re-parse that does not exist. From a held workbook it
re-harvests that workbook's site.

## Also in this change: the Lineage View's default scope

ADR 0011 left the 250-workbook bound as an open question. The part of it that was a present
defect rather than a future one is fixed here: when no scope was asked for and the estate
exceeds the bound, the read used to take the first 250 workbooks in whatever order the query
returned them — alphabetically, in practice, which silently made "the estate" mean "the
sites early in the alphabet".

It now narrows to the largest single site and reports it in `auto_scoped_to`, which the
screen shows: *Showing rqa — the estate is larger than one graph can be read from.* A
truncation the reader can see is a different thing from one they cannot. When a scope **was**
asked for, the old behaviour stands and `truncated` is set: the reader chose the scope, and
narrowing it further underneath them would be worse.

The remaining question — whether 1,000 workbooks in one family need aggregation or edge
bundling rather than a force graph — is unchanged and still open.

## Consequences

- The console gained a third surface and `surfaceFromPath`, so each screen is linkable.
- `PostgresParseQualityStore` gained `occurrences_of`, which is what makes an issue
  actionable: "it is in these eleven workbooks, on these sheets", not "this construct exists".
- The fixture estate now carries grammar gaps, but only for the local demo — `build_site`
  takes `grammar_gaps=False` by default. Making them the default broke eleven tests that
  assert exact held counts, which was the right signal: a fixture that quietly holds
  workbooks is a fixture that lies to every test using it.
- `docker compose up` now supplies the fixture adapter's credentials. Without them the first
  harvest failed on a missing credential and the console showed an empty estate with no hint
  why. They are not secrets — the fixture estate is generated and accepts any token, and the
  adapter is only reachable because `ASTRA_ENV` is `local`.

## What building it found

1. **Four divergences between the console's fake API and the real one**, in one story.
   `held` against `workbooks`; one number typed as two; the counts nested under a
   `constructs` object on the wire and flat in the console. Each passed the console's own
   81 tests and white-screened or rendered `undefined` against the real service. The
   response key sets are now pinned in the service's tests, so a rename breaks something
   loud rather than a screen nobody has opened yet. The nesting was removed rather than
   copied into the console: nothing else in the API nests counts.
2. **`new_ulid()[:8]` is not unique.** A ULID opens with 48 bits of millisecond timestamp,
   so its first eight characters are identical for every call within roughly a quarter of a
   second — which, in a suite this fast, is most of it. Three integration modules used that
   prefix to make per-test site names and construct texts unique, and the module fixtures
   deliberately share one graph. Two of the new tests collided on the same construct and one
   saw the other's open issue. The suffix is now the ULID's random tail.
3. **Two orphaned dev servers.** A stale uvicorn and a stale Vite server from an earlier
   smoke test were still bound to 8080 and 5173, so the first browser run tested code from
   two commits ago and reproduced a bug that had already been fixed. Worth knowing before
   trusting a smoke test.

## Open questions for the product owner

1. **Nothing prioritises the queue against the programme.** The ordering is estate-wide:
   a construct blocking nineteen workbooks outranks one blocking six, whether or not those
   nineteen are in scope, tiered, or anywhere near the Calibration Wave. Once tiers carry a
   wave, "releases nineteen workbooks, none of them in Wave 1" is a different call from
   "releases six, all of them Wave 1".
2. **Accepting a construct is not reviewable.** It is recorded with a reason, a principal
   and a timestamp, and it re-scores immediately. There is no second pair of eyes and no way
   to withdraw one — reversing it means a grammar fix and a re-harvest. For a decision that
   moves workbooks past a §4.1.4 quality gate, whether that should need review is a policy
   question, not a technical one.
3. **The issue's state is only ever moved from this screen.** When E12 mirrors issues into a
   client's tracker, work will be closed *there*, and nothing yet reads that back. A one-way
   mirror that goes stale is arguably worse than no mirror.
