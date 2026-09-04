# ADR 0015 — The Tableau adapter holds its own credential, and its corpus is a deployment

Status: accepted · 3 September 2026 · Story S2.2.1 (E2 / F2.2)

## Context

F2.2 is the first adapter that talks to something real. S2.1.1 built the contract and the RPC
against a fake source; S2.1.2 made a passing conformance report the condition of promotion.
This story has to reach a Tableau Server or a Tableau Cloud, both, through one adapter — and
it turns out that a real adapter asks three questions the fake never did.

## Decisions

### 1. The credential never crosses the adapter RPC

The platform names a credential (`tableau/rqa`) and resolves it for its own records. The
*adapter worker* resolves its own, from its own environment, and the secret never travels the
channel S2.1.1 added.

This follows the deployment model rather than working around it. §5.2 scales `adapter-tableau`
"per site parallelism" and §5.4 runs it as its own pod with its own Key Vault access, so a
worker already serves one site and already needs one credential. Sending the secret over the
RPC would put it on a wire that did not need to carry it, and would extend the blast radius of
a compromised platform pod to every site's credential.

It also extends a rule the platform already has — "a credential is never sent over the API,
only a reference the service resolves" — to the one channel that did not exist when the rule
was written.

**The cost is real and is an open question below**: one worker cannot serve two sites.

### 2. Server and Cloud are detected, not configured

An operator should not have to tell the platform which they have. `/serverinfo` says, and
asking is an opportunity to be told the wrong thing. Cloud is recognised by host and by
reporting a version in a scheme Server never uses.

The 2021.4 floor is checked on that same first call, **before a credential is presented** —
a deployment the adapter cannot support should be reported as unsupported rather than
authenticated against.

### 3. The content hash is over the XML, not over the download

A `.twbx` is a zip, and a zip is not byte-stable: it records timestamps and orders entries as
the producer pleases. Hashing the download would make every re-harvest of an unchanged
workbook look like a change, and S1.2.4's incremental harvest — whose entire value is *not*
downloading an unchanged estate — would download the whole estate every night.

### 4. The extract is never read, in two places

The download asks Tableau not to include one (`includeExtract=false`), and the archive reader
reads the `.twb` entry and never a data entry. §16 forbids copying client data the platform
does not need and S2.2.2 says it outright.

Two lines rather than one because the first can be defeated by something outside this code: a
Tableau version that ignores the flag, a proxy serving a cached copy. The second makes "we did
not copy the client's data" a property of the code rather than of a request parameter.

The extract's **name** is recorded. The Modeller needs to know where data comes from, and a
name is not data.

### 5. The revision falls back visibly

S2.2.1 requires the revision id. Revision history is a per-site setting that is frequently
disabled, and Tableau answers 404 when it is — so the adapter falls back to `updatedAt`, and
the fallback is visible in the value: `rev:7` and `updated:2026-01-02T…` cannot be mistaken
for one another. Nobody later reads a timestamp as a revision number.

### 6. The golden corpus is a deployment, and it ships with the adapter

§6.3: "An adapter ships with a corpus of source assets and expected graph fragments."

For a source adapter, source assets on disk are not enough. Discovery, paging,
authentication, session expiry and throttling all live *between* the adapter and a server, and
half of S2.2.1 is about exactly those. A corpus of `.twbx` files could not check any of them.

So the corpus is an ASGI Tableau — two deployment kinds, both credential kinds with the
rejections a wrong one really produces, pagination that must be followed, sessions that can be
expired, 429s with `Retry-After`, and real zip bytes with a real stand-in extract — and it
lives in the package rather than in the tests, because §6.3 makes the corpus part of what an
adapter is. `astra-tableau-golden` serves it over a socket; running the suite in process would
certify the adapter's logic, and running it over a socket certifies the adapter.

### 7. The adapter is registered while incomplete, and fails conformance loudly

It would have been possible to withhold the entry point until F2.4. That would have made
`astra-adapter conformance --adapter tableau` say "not installed" for two more features, and
would have hidden the checks that *do* pass.

Instead it registers, runs, and reports: discovery completeness, error taxonomy and throttling
pass; the four parse and AST checks fail with the feature that will satisfy them named; three
capabilities it does not claim are skipped. S2.1.2's gate keeps it off a tenant, which is the
mechanism working rather than a risk being taken.

### 8. An additive field does not move the interface version

`SiteRecord.detail` carries what a source says about itself — for Tableau the product version,
the deployment kind and whether the Metadata API is enabled (S2.2.1's fourth criterion).

Open rather than typed, because it is source-specific by definition: a Tableau version and a
Looker instance id have nothing in common to model, and a field per source on a shared
contract would grow one column per adapter forever. The platform stores it and the Estate
surface shows it; nothing branches on its contents.

A field with a default is compatible in both directions over JSON — an older adapter omits it,
a newer platform reads the default; a newer adapter sends it, an older platform ignores it. So
`INTERFACE_VERSION` does not move. Removing or retyping a field is what breaks a contract, and
that is what the version is for.

## Consequences

- `RemoteAdapter` and the SDK are unchanged apart from `SiteRecord.detail` and one fix.
- The SDK's corpus discovery only ever worked for its own adapter — see below.
- The registry's message for `tableau` no longer says "not built yet"; not being found now
  means the package is not installed, which is a different problem.
- CI runs the Tableau suite against the golden deployment on a socket, keeps the report, and
  is marked `continue-on-error` until F2.4 completes. The trend is visible as features land.

## What building it found

1. **The download had no re-sign-in.** `call` handled session expiry and `download_workbook`
   had its own loop with the backoff but not the 401 — and the download is where a long
   harvest spends its time, so a session expiring mid-run failed the workbook most likely to
   be in flight when it happened. Two loops that were meant to be the same and were not; they
   are now one.
2. **A rate-limited Tableau was reported as "below the 2021.4 floor".** `/serverinfo` is the
   first call and the least likely to be throttled, so it had no backoff — and a wrong
   diagnosis on the first call sends whoever reads it to check a version that was never the
   problem.
3. **A network reset escaped as `httpx.ConnectError`.** Outside §6.1's taxonomy, so the
   platform would have treated a blip as a *platform bug* with a traceback rather than as one
   workbook to retry. Found by S2.1.2's error-taxonomy check on its first run against a real
   adapter — which is the first evidence that check earns its place.
4. **The fault hook modelled a rejected credential as one 401**, which is an *expired session*
   — a condition the adapter correctly recovers from. The two look identical on one call and
   differ on the second, which is exactly how the adapter tells them apart, so the injection
   now persists until cleared.
5. **The SDK could only find its own corpus.** `_corpus_for` looked under
   `astra_adapter.<name>`, so the first third-party adapter to ship one could not be checked
   against it. It now walks outwards from the module that defines the adapter.

## Open questions for the product owner

1. **One worker, one site.** The credential lives in the worker's configuration, so a
   programme with eight Tableau sites needs eight workers. That matches §5.2's scale unit and
   is operationally simple, but it is a real cost at a client with many small sites — and the
   alternative (the credential *reference* travelling over the RPC, resolved by the worker) is
   a contract change worth deciding before the first multi-site client rather than after.
2. **The Metadata API is optional in practice.** A Tableau Server administrator can disable
   it, and many have. Discovery falls back to the REST listing and says so, but the estate is
   then shallower than the one §12.1's lineage scoring assumes — and nothing yet decides
   whether a programme can proceed on a shallow estate or must ask the client to enable it.
3. **Nothing measures the throughput target.** §8.4 sets 500 workbooks per hour per site
   worker. At the default cap of 4 that is comfortably achievable on paper; it has not been
   measured against a real Tableau, and a client's rate limit could make it unreachable at any
   cap. Worth measuring on the first client site rather than assuming.
4. **Project hierarchy is two levels deep, not Tableau's.** The REST listing gives the
   immediate parent and the Metadata API's `containerName` gives the top; a genuinely nested
   project tree needs a projects query, which S2.2.2 is the natural home for. The Estate
   Explorer's tree is correspondingly shallower than the client's, which is visible and
   currently unexplained on that screen.
