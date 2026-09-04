# ADR 0014 — Conformance is a gate, not a document

Status: accepted · 3 September 2026 · Story S2.1.2 (E2 / F2.1)

## Context

ADR 0013 closed with an open question: *"§6.1 says an adapter must pass the conformance suite
before it can be enabled on a tenant. CI runs it; nothing at deployment refuses an adapter
that has not passed against this tenant's sample."*

S2.1.2 answers it, and states the reason in its own words: **"adapter acceptance is a test
result, not an opinion."** An un-enforced rule degrades into an opinion the moment a client is
waiting, and the person under that pressure is never the person who wrote the rule.

The story asks for three things: wider coverage, a signed report stored and linked, and a
failing run that blocks promotion.

## Decisions

### 1. Seven areas, eleven checks, and the mapping is written down

S2.1.2's list and §6.3's overlap but are not the same list. Both are covered rather than one
chosen, and the suite's module docstring carries the table — because the alternative is a
future reader finding "enumeration completeness" in the specification, "discovery
completeness" in the backlog and one check in the code, and having to guess which was meant.

The names follow the story where they differ, since the story is the newer document and the
difference is cosmetic. Where the *substance* differs the specification wins, as always:
§6.3 asks for determinism across three runs and S2.1.2 says "same case twice", so it runs
three times.

### 2. Error taxonomy and throttling need a fault hook, and an adapter without one **fails**

Neither can be checked by watching an adapter succeed, and a client's Tableau Server will not
return 429s to order. So the SDK defines `FaultInjector`: a standard way to ask an adapter to
behave as if the source had misbehaved. The adapter's own error handling, backoff and retry
run for real — only the source's response is forced.

An adapter that does not implement it is **failed, not skipped**. This is the sharpest
judgement in the story and it is deliberate: §6.2 requires backoff on 429, and "we could not
check" recorded as a pass is exactly the false assurance the suite exists to prevent. An
adapter is certified for the behaviour it can be *shown* to have.

The hook is served over the RPC only when the adapter implements it, and the platform never
calls it. An adapter that must not be drivable in production should not implement the
protocol — and will then fail these two checks, which is the honest trade rather than a
loophole.

### 3. The taxonomy itself is part of the contract

`RateLimited` is now a contract type with a `retry_after`, because the platform's response to
a failure is decided entirely by which kind it is: wait and retry, record this workbook and
carry on, or stop the run. An adapter that raises a bare error for a 429 gets a workbook
recorded as permanently failed when the truthful answer was "ask again in thirty seconds" —
and at 1,067 workbooks against a rate-limited site, loses most of the estate.

### 4. Reports are hashed always, signed where there is a key, and never claim otherwise

HMAC-SHA256 over the canonical JSON — the same canonicalisation `context_hash` uses (S1.3.1),
for the same reason: a hash that depends on how a dictionary happened to be serialised is a
hash of the serialiser.

Where no key is configured the report is **hashed and explicitly unsigned**: `signed` is
false, `signature` is null, and `key_id` explains why. A report claiming a signature it did
not have would be worse than an unsigned one, because an unsigned one is obviously unsigned.
§18.1 puts the key in Key Vault and E11 brings that; until then it comes from the environment.

HMAC rather than a public-key signature because both ends are inside one tenant (§5.3) and the
question is "was this produced by this platform and not since altered" — not "which of several
mutually distrusting parties produced it". A public-key scheme answers a question nobody is
asking yet, at the cost of a key-distribution story this release does not have.

**The platform does not verify the signature on receipt.** A deployment may have no key, and
one that rejected unsigned reports could record nothing at all until E11. What is stored is
what was submitted, including whether it claimed to be signed and with which key, so a
verifier that *does* have the key can check it later.

### 5. The whole report is stored, including failing ones

"The adapter passed" is not evidence. An engineer asking six months later why an adapter was
allowed onto a client's estate needs the checks that ran, what they found, and the corpus.

A failing report is the more important one to keep: it is the reason a promotion was refused,
and it is the one somebody has the strongest motive to replace.

§5.2 gives object storage and content addressing to `artefact-svc`, which does not exist —
the same position provenance was in at S1.3.2, and the same answer: a table behind a port,
and relocating it later changes one adapter.

### 6. A promotion is bound to a build, not to a name

Name, version, interface version and grammar version together. The grammar is in there
because it changes what the adapter *reads*, which is the thing conformance is about.

The likeliest route to promoting untested code is a version bump for a "small fix" on the
strength of the previous version's report, and the gate refuses exactly that. The refusal
names the build the report *is* about.

### 7. The gate lives in the Harvester

A harvest can be started by a schedule as well as by a request (S1.2.4). A gate that only
covered the endpoint is a gate a nightly run walks around. The Harvester is the one place
every harvest passes through, and it is the point where an adapter first touches an estate.

It is checked *before* the run record is created, so a refused harvest is a refusal rather
than a run that failed — an operator reading the harvest list should not have to distinguish
"the adapter was not allowed" from "the adapter broke".

### 8. The fixture adapter is exempt, by name

`harvest_setup.UNGATED_ADAPTERS`. The gate exists to keep an untested adapter away from a
*client's* estate; the fixture generates its own and reaches no client system, so requiring a
recorded report before `docker compose up` works would gate local development on a ceremony
that protects nobody.

A name list rather than a flag, so adding to it is a visible change to a security-relevant
constant rather than a boolean somebody sets in an environment.

## Consequences

- `RemoteAdapter` now preserves `RateLimited` across the wire, with its `retry_after`.
- Platform Health's adapter section carries the report, a link to it, whether the running
  build is promoted, and whether it is gated at all.
- Migration 0010 adds `adapter_conformance` and `adapter_promotion`, with a partial unique
  index enforcing one promoted build per adapter per tenant.
- CI runs the suite in both modes, signs the report where a key is configured, verifies it,
  and keeps it as a build artefact — a run that produced no report has produced nothing a
  tenant can act on.

## What building it found

1. **The suite over the RPC failed a check the in-process run passed**, and the check was
   right: `RateLimited` did not survive the wire. The S2.1.1 RPC flattened it into a plain
   retryable error, so across a process boundary the platform could not tell "wait thirty
   seconds" from "this workbook is broken". Found by the throttling check on its first
   `--remote` run.
2. **The fault hook could not cross the RPC either**, for the same reason `parse_calc` could
   not in S2.1.1: it was synchronous. `--remote` is the mode that certifies what is deployed,
   so two of the story's seven areas would have been checkable only in process — against
   something other than the artefact being certified.
3. **My own AST coverage check could never pass at a floor below 100%.** It listed every
   expression with an unread construct as a failure *and* compared coverage to a floor, so
   the floor was decorative. A corpus set at 80% sat at exactly 80% and failed.
4. **The parse round-trip check did not go through JSON.** It round-tripped the wire codecs
   only, which move objects between shapes and carry a tuple straight across. The actual wire
   does not, and a property that becomes a list on the way into the graph is a difference that
   shows up as drift on the next harvest.
5. **The service would not start.** `build_harvester` holds the promotion gate, which reads
   the conformance store, and the store was wired *after* it. The unit suite could not see it
   because those tests set app state directly and never run the lifespan — the smoke test
   caught it on the first container start.
6. **Platform Health disagreed with the gate.** It reported the exempt fixture adapter as
   "not promoted", which reads as "this harvest should be blocked and is not" — a defect an
   operator would go looking for and never find.

## Open questions for the product owner

1. **Nobody approves a promotion but the person doing it.** Promotion takes a reason and a
   principal and happens immediately. For the decision that lets code touch a client's whole
   estate, whether that should need a second approver is a policy question — and §14's gate
   machinery already exists for exactly this shape of decision.
2. **A revoked adapter is not stopped.** Revocation blocks the *next* harvest; a run already
   under way continues, and nothing kills the worker. For a grammar regression that is
   probably right. For a credential compromise it is not, and the two are indistinguishable
   to this design.
3. **The report attests to a corpus, and nobody checks which one.** A tenant can promote on a
   report produced against the SDK's own corpus rather than "a client-provided sample" (§6.3).
   The corpus name is stored, so the platform *could* require a tenant-specific one — whether
   it should is a question about how enablement is run.
4. **Signing keys are per-deployment and unrotatable.** One environment variable, one
   `key_id`, no rotation and no way to verify a report signed with a retired key. E11 brings
   Key Vault, and key rotation should be designed then rather than inherited from this.
