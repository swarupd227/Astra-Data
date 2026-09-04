# ADR 0013 — The adapter contract is a published package, and adapters run out of process

Status: accepted · 3 September 2026 · Story S2.1.1 (E2 / F2.1)

## Context

E2's goal: *"one contract that a source adapter implements; a Tableau adapter that passes the
conformance suite against Tableau Server and Tableau Cloud"*. S2.1.1 is the contract, the SDK
and the suite; F2.2 to F2.4 are the Tableau adapter itself.

The interface has existed since S1.2.1, inside `graph-svc`, because the Harvester needed
something to be written against. That was the right shape for one story and the wrong shape
for an epic whose goal is *"a second source can be added without changing the platform"* —
a claim that cannot be checked while the contract lives inside the platform.

§20 settles what to build: *"Adapter SDK (Python): the §6/§7 interfaces, manifest schema,
conformance harness, grammar tooling for calc-language parsers, and a packaging pipeline. A
new source adapter is a repository that passes the harness."*

## Decisions

### 1. The contract is its own package, and the platform is one of its consumers

`packages/adapter-sdk` (`astra-adapter-sdk`) holds §6.1, the RPC, the fake source and the
conformance suite. `graph-svc` depends on it. A test asserts no module under `astra_adapter`
imports `astra_graph`.

That test is the point. "A second source can be added without changing the platform" is
otherwise an intention: it holds only while nobody adds a platform import to the contract,
and the first person to do so would find it convenient rather than wrong. With the dependency
inverted and checked, an adapter author installs one package and needs none of the platform's
source.

`astra_graph.adapters.contract` and `.fixture` remain as re-export modules, so the platform's
imports still read as platform imports. Moving them would have been a rename across a dozen
modules that changed nothing about what the code does.

### 2. REST over HTTP, not gRPC

§5.4 is explicit: *"Interfaces: GraphQL for the graph and console; **REST for adapters**,
gates and evidence."* There was no design space here worth exploring, and the decision is
recorded so nobody re-opens it on the grounds that a binary protocol would be faster. It
would; the fetch payload dominates either way, and a wire format an engineer can read with
`curl` is worth more on a client's network than the milliseconds.

**The codecs are written by hand.** The wire format is the contract's compatibility surface.
Deriving it from the dataclasses would make every incidental field change a protocol change,
and would silently accept a payload from an adapter built against a different interface.

### 3. An interface mismatch is refused, in both directions

The client checks the adapter's manifest on connect; the adapter checks the caller's declared
version on every call. Neither negotiates.

A platform that quietly accepted an older adapter would be deciding, at runtime and
invisibly, which parts of the contract still hold. The first symptom would be a missing field
in a graph fragment, months later, in a harvest nobody was watching.

### 4. A fetch result is bytes

`RawAsset.payload` was typed `Any` and documented "adapter-private", and the fixture adapter
put a Python object in it. That works only while the adapter shares a process with its
caller. §6.1 says "bytes + metadata, content-hashed", and now it is — with a `media_type`
alongside, so an artefact store can label what it keeps without sniffing.

### 5. `parse_calc` is async, unlike the rest of §6.1's sketch

The specification writes the interface in a synchronous pseudo-syntax and marks nothing as
awaiting. `enumerate`, `fetch` and `parse` were made async for the obvious reason that they
do I/O. `parseCalc` looks like pure computation and was written synchronously to match.

**The RPC found that this was wrong**, and found it the only way it could be found: the
conformance suite passed in process and failed over the wire, on every expression, because a
synchronous method can only be served across a socket by blocking the caller's event loop
behind it. Async is not a concession to the transport — it is what the method always was once
the adapter stopped sharing a process.

### 6. An unsupported capability is a distinct answer

`UnsupportedCapability` rather than `AdapterError`, preserved across the wire. "This Tableau
site has no Metadata API, so there is no usage to give you" is a fact about the deployment
that the Estate surface is meant to show (§6.1, backlog §7.1), not a failure to retry or to
record against a workbook.

The suite reads capabilities the same way: an unclaimed capability is **skipped**, and the
skip is printed. A claim is the only thing that turns an absence into a failure — and a claim
that is not honoured is one.

### 7. The supervisor is small on purpose

§5.4 runs adapters as isolated pods; a pod that dies is restarted by the kubelet.
`AdapterSupervisor` exists for the local stack, CI and the conformance runner — the places
with no orchestrator. It has a restart *bound* rather than a retry policy: an adapter that
crashes on the first call of every run is broken, and restarting it forever converts a loud
failure into a harvest that never finishes.

A second scheduler competing with Kubernetes would be a worse outcome than no scheduler.

### 8. The interface version is recorded, and checked

S2.1.1 criterion 4 asks for it on every harvest and every ParityRun.

- **Harvest.** Already on the adapter record. Now *refused* if blank: a versioned interface
  whose version is blank is not versioned, and the record's whole purpose is to let a harvest
  be read months later against the contract that produced it.
- **ParityRun.** E7 owns the run and it does not exist. What exists is what a run is assembled
  from, so `ResultSet` carries `interface_version`, `adapter_name`, `adapter_version` and
  `grammar_version`, and `ParityRunStamp.from_results` refuses a run whose result sets
  disagree about them. There is no path that produces an unstamped result — which is stronger
  than a convention E7 would have to remember.

### 9. The fake source is a real implementation, including its grammar

§6.3's hardest check is AST round-trip stability. A fake whose `parse_calc` returned a fixed
tree would round-trip trivially, making the suite's most valuable check the one it could not
fail — so the fake ships a small recursive-descent parser with precedence, LOD-style
aggregates, window functions, conditionals, casts, and an UNKNOWN escape.

It is not a Tableau grammar; §5.4 commits those to Lark and F2.3 builds Tableau's. What is
shared is the AST, so the suite that checks one checks the other.

### 10. `--adapter tableau` says what is true

The story's own example names an adapter that F2.2 to F2.4 build. The command exits 2, names
the feature that builds it, and lists what *is* registered. "No such adapter" printed as "0
checks passed" is how a tenant gets enabled against nothing.

## Consequences

- `make conformance` and a CI job run the suite in both modes on every push.
- The `graph-svc` image now builds from the repository root, because it builds the sibling SDK
  package from source. A monorepo without a package index has to do this somewhere.
- `graph-svc` harvests through a worker when `ASTRA_ADAPTER_URL` is set. When the Tableau
  adapter lands it is configured by URL and nothing else changes — a test asserts a harvest
  through a `RemoteAdapter` produces the same counts, the same quality and the same adapter
  record as the same harvest in process.
- The fixture adapter is now the SDK's `fake`, and its conformance corpus has **no** grammar
  gaps: §6.3 requires the corpus to clear 0.98, so a corpus seeded with unreadable constructs
  fails by construction. Gaps belong in the round-trip corpus, where retaining them verbatim
  is the property under test, and in the local demo estate, where the Parse Quality Queue
  needs something to work down.

## What building it found

1. **The suite's first run failed two of its own checks**, which is the outcome to want: a
   corpus that could not clear its own floor, and a grammar that raised on `RAWSQL_INT('select
   1')` instead of retaining it. The second was the more serious — §6.2 requires unreadable
   constructs to be *retained verbatim*, and a parser that dies on one turns "a workbook held
   with a named construct to fix" into "a workbook that would not parse".
2. **`parse_calc` could not be served remotely.** Decision 5 above. Found by running the suite
   over the RPC, not by reading the interface.
3. **`ParseResult` compared unequal to itself across the wire** — the fake returned lists, the
   decoder tuples, and a dataclass compares `[a] != (a,)`. It now normalises its sequences,
   which matters most exactly where it bit: the suite comparing an adapter's output to an
   expected fragment.
4. **The supervisor counted its first start as a restart**, so `max_restarts=1` refused the
   first genuine restart — the opposite of what the bound is for.
5. **A crash between calls costs nothing at all.** The test was written expecting one lost
   asset; the supervisor notices the dead process on the next call and restarts it before that
   call goes out. The test now asserts the stronger behaviour, because the weaker claim would
   still pass if the restart were broken and every subsequent asset failed instead.
6. **The worker entry point never called its own `main`.** `python -m astra_adapter.rpc.server`
   exited silently, and the supervisor reported only "did not start". It is now a module
   nothing else imports, which also removes the double-execution warning that `-m` on an
   already-imported submodule produces.

## Open questions for the product owner

1. **Nothing enforces conformance at enablement.** §6.1 says an adapter "must pass the
   conformance suite in §6.3 before it can be enabled on a tenant". CI runs it; nothing at
   deployment refuses an adapter that has not passed against *this tenant's* sample. That is a
   control-plane decision — where the conformance report is stored, who signs it off, and
   whether a failed check blocks enablement or warns.
2. **The corpus format is minimal.** Expected *node types* per asset rather than whole
   fragments, so a corpus does not have to be regenerated whenever a property is added. That
   trades precision for maintainability, and the trade may be wrong for a client sample where
   the question is "did it read my workbook correctly" rather than "did it read a workbook".
3. **One adapter per platform deployment.** The registry can find several; nothing composes
   them. A client with Tableau Server *and* Tableau Cloud, or a Tableau estate alongside a
   Qlik one, needs a harvest scoped per adapter and an estate that records which adapter each
   workbook came from. Worth knowing before the first multi-source client rather than after.
4. **Rate limits are the adapter's problem, and unmodelled.** §6.2 requires "adaptive
   concurrency per site, Metadata API paging, backoff on 429". None of that is in the
   contract, so every adapter will solve it privately and the platform cannot see when an
   adapter is being throttled — which will look like a slow harvest with no explanation.
