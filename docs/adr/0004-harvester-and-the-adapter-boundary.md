# ADR 0004 — The Harvester is built against the adapter contract, not against Tableau

Status: accepted · 2 September 2026 · Story S1.2.1 (E1 / F1.2)

## Context

S1.2.1 says "point the platform at a Tableau site and get the whole site parsed into the
graph". The Tableau client that would do the pointing is a different epic: E2 delivers the
adapter contract and SDK (F2.1), discovery and fetch (F2.2), parsing and the calculation
grammar (F2.3–F2.4). None of it existed when this story started.

The backlog's own wording for F1.2 is "the Harvester pulls the estate **through the source
adapter**", and increment I1 schedules F1.2 and F2.1–F2.4 together. So the Harvester and
the Tableau client are separate pieces that land in the same increment.

## Decision

**The Harvester is complete; the Tableau adapter is not started.** What shipped:

* the `SourceAdapter` contract from §6.1, which the Harvester is written against;
* the Harvester itself — enumerate, fetch, parse, write, per-project progress, failure
  isolation, idempotency, parse-quality holds, usage and ownership;
* a **fixture adapter** producing estates shaped like the §3.4 worked example, so the
  Harvester is tested and measured against something real rather than a mock;
* the harvest API the Estate Explorer (F1.4) will call.

What did not ship, and is E2's: the Tableau REST and Metadata API client, `.twb`/`.twbx`
parsing, the calculation-language grammar, and the §6.3 conformance suite. The fixture
adapter says so in its own module docstring, and `harvest_setup.py` enables it only where
the deployment declares itself local.

A deployment with no adapter enabled reports that from the harvest endpoint rather than
appearing to work. That is the honest state of a tenant before its adapter is enabled
(§6.3 makes enablement conditional on conformance anyway).

## Other decisions

### Node ids are derived from source identity, not issued at random

A re-harvest that invented new ids for the same sheets would write a parallel copy of the
estate, so a workbook's elements get ids derived from the adapter's fragment key, scoped
by site. The same workbook harvested twice produces the same ids on any deployment,
without the Harvester keeping a lookup table.

They are ULID-shaped because the ontology requires it, but they are *derived*, not
time-ordered: the leading 48 bits carry hash rather than a timestamp, so these ids do not
sort by creation time. `harvest/identity.py` says so at the top. Random ULIDs remain the
default for everything not derived from a source object.

### A caller names a credential; it never sends one

S1.2.1 says "with site credentials from Key Vault". The property that matters is that a
secret never crosses the API — a request body carrying a personal access token puts it in
the API log, the request trace and someone's shell history.

So `POST /v1/harvests` takes `"credential": "tableau/rqa"` and the service resolves it.
Resolution is behind `CredentialProvider`; the environment-backed implementation is for
local and CI, and the Key Vault one is E11's, where managed identity and the credential
broker arrive. A resolved credential keeps its secret out of `repr` and `str`, which the
harvest log line demonstrates: `using <credential tableau/rqa>`.

### Enumeration completes before any fetch

"Progress is visible per project with counts of workbooks queued, parsed, failed" is only
meaningful if `queued` is a fact. Enumerating the whole scope first costs one pass over
the source's cheap listing API and makes every subsequent number mean something.

### Shared nodes are written once per run, and released only after commit

Every workbook's parse carries its Site and Project. Writing them per workbook is wasteful
and, with concurrency, unsafe: **Apache AGE fails an update whose vertex changed under it**
rather than blocking on the row, reporting `Entity failed to be updated`.

The Harvester therefore claims each derived id once per run, keyed on id *and* properties
so a genuine change still goes through. The first attempt at this released the claim when
it was taken rather than when the write committed, and a second workbook promptly wrote an
edge to a node that was not there yet. The claim now carries an event set after commit.

The repository also retries an AGE concurrent-update failure a few times, as a backstop
for two harvests running at once.

### Throughput is measured for what the platform controls

S1.2.1 wants 1,000 workbooks in under four hours (14.4 s each); §8.4 is tighter at 500 per
hour per worker (7.2 s each). The benchmark measures parse, validate and write with no
network in the adapter: **0.17–0.18 s per workbook, about three minutes for 1,000
workbooks.** Source I/O is the other term and belongs to the Tableau adapter's own
measurement.

## A deployment hazard this story surfaced, and fixed

Migration state is recorded once per database, but the AGE graph, its labels and its
indexes are per *graph*. Migration 0001 creates the graph named by `ASTRA_GRAPH_NAME`, so
pointing a deployment at a graph name it has not used before left `migrate` reporting "no
pending migrations" while the graph was never created. The service then started, health
answered 503, and every write failed.

Found by smoke-testing the running service, not by the suite — the suite creates its
graphs in fixtures. Fixed in `provisioning.py`: the graph, its labels and its indexes are
**reconciled on every `migrate`**, idempotently, creating only what is missing. That also
repairs a graph provisioned before an ontology change added node types.

## Consequences

- When the Tableau adapter lands it is registered in `harvest_setup.py` and nothing else
  in the service changes. That is the test of whether this boundary was drawn correctly.
- The fixture adapter is a permanent asset, not scaffolding: the §6.3 conformance suite
  needs a reference implementation to test the harness itself against.
- A harvest is an in-process task. Its record is persisted, so progress survives a
  restart, but the worker does not — durable orchestration is Temporal's (E12/F12.1).

## Open questions for the product owner

1. **Method names differ between the documents.** §6.1 names the adapter's methods
   `manifest`, `enumerate`, `fetch`, `parse`, `parseCalc`, `usage`, `owners`,
   `executeCase`. Backlog S2.1.1 names them `discover`, `fetch_workbook`, `parse`,
   `execute_case`, `capture_visual`, `capabilities`. The specification won, per the
   backlog's own rule. Confirm before E2 writes against it.
2. **"Held" is a fifth outcome the story does not name.** S1.2.1 counts queued, parsed and
   failed. §4.1.4 says a workbook below the parse-quality threshold "cannot leave
   HARVESTED" until reviewed, which is neither parsed-and-done nor failed, so the progress
   record reports `held` separately. Confirm that is what the Estate Explorer should show.
3. **Re-harvest of a *changed* workbook currently updates in place.** §8.4 says a changed
   workbook "produces a new revision node and re-opens the MU if it had progressed past
   HARVESTED (a change-control event visible on the Programme Board)". Revision nodes,
   MU state and that event are E3/E12 concepts that do not exist yet, so today the changed
   workbook simply updates. Confirm the sequencing — this is a real gap against §8.4, not
   an oversight.
4. **The four-hour budget covers source I/O that nobody has measured.** The platform side
   uses about 3 minutes of it. Whether Tableau's Metadata API and `.twbx` downloads fit in
   the remaining time at a client's rate limits is E2's to establish, and it is the term
   that will actually decide the acceptance criterion.
