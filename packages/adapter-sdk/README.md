# astra-adapter-sdk — the Source Adapter SDK

The specification's §6, as a Python package.

> Adapter SDK (Python): the §6/§7 interfaces, manifest schema, conformance harness, grammar
> tooling for calc-language parsers, and a packaging pipeline. **A new source adapter is a
> repository that passes the harness.** — §20

A source adapter is the only component that knows the source platform. It enumerates the
estate, parses assets into the graph ontology, executes source queries for the Proof Engine,
and exposes usage and ownership. Everything the platform knows about Tableau, it knows
through one of these.

## What is here

| | |
|---|---|
| `astra_adapter.contract` | §6.1's `SourceAdapter`, its records, its errors, `INTERFACE_VERSION` |
| `astra_adapter.calc` | The calculation AST, its canonical text form, and §6.3's round-trip check |
| `astra_adapter.proof` | `ParityCase`, `ResultSet`, `VisualCapture` — what §6.1's executor takes and returns |
| `astra_adapter.rpc` | An adapter runs out of process and speaks REST (§5.4): `serve`, `RemoteAdapter`, `AdapterSupervisor` |
| `astra_adapter.fake` | A complete §6.1 implementation over a deterministic estate, with a small real calculation grammar |
| `astra_adapter.faults` | The error taxonomy, and the hook the suite drives an adapter with |
| `astra_adapter.conformance` | The checks, and the signed report |
| `astra-adapter` | The command line |

**No platform dependency.** Nothing here imports `astra_graph`, and a test asserts it.
`graph-svc` consumes this package on exactly the terms an adapter author does — which is the
only way "a second source can be added without changing the platform" can be checked rather
than asserted.

## Running the conformance suite

```bash
astra-adapter conformance --adapter fake
```

```bash
astra-adapter conformance --adapter fake --remote
```

`--remote` runs the same suite through the adapter RPC against a supervised worker process.
That is the mode that matters for tenant enablement: §6.1 enables an adapter as a versioned
worker image, and a suite that only ever ran in-process would have certified something other
than what is deployed.

`--out report.json` writes the **signed** report: hashed always, signed with HMAC-SHA256 when
`ASTRA_CONFORMANCE_SIGNING_KEY` is set, and explicitly unsigned when it is not. That report is
what a tenant records and promotes an adapter on — a failing one blocks promotion (S2.1.2).

```bash
astra-adapter verify report.json
```

`astra-adapter list` shows what is registered; `astra-adapter manifest --adapter <name>`
prints an adapter's identity and capabilities.

## Writing an adapter

1. Implement `SourceAdapter`. Every method is `async` — see *The interface* below.
2. Declare the entry point, which is what makes the adapter findable:

   ```toml
   [project.entry-points."astra.adapters"]
   tableau = "astra_adapter_tableau:build"
   ```

3. Ship a `Corpus` — source assets and the fragments they must produce (§6.3).
4. `astra-adapter conformance --adapter tableau` until it is green, in both modes.
5. Package the worker image; its entry point is `astra-adapter-serve --adapter tableau`.

## The interface

§6.1 names the methods `manifest`, `enumerate`, `fetch`, `parse`, `parseCalc`, `usage`,
`owners`, `executeCase`. Backlog story S2.1.1 names six of them `discover`, `fetch_workbook`,
`parse`, `execute_case`, `capture_visual`, `capabilities`. The backlog's own rule is that the
specification wins, so §6.1's names are used in Python spelling and
`contract.BACKLOG_METHOD_NAMES` records the mapping — checked by a test, so it cannot rot.

**Everything is async, including `parse_calc`.** The specification writes the interface in a
synchronous pseudo-syntax; `enumerate`, `fetch` and `parse` are obviously I/O. `parseCalc`
looks like pure computation, and was synchronous here until the adapter moved out of process —
at which point every method crosses a socket and a synchronous one can only be served by
blocking the caller's event loop behind it. See ADR 0013.

**A fetch result is bytes.** §6.1 says "bytes + metadata, content-hashed", and an adapter in
another process cannot hand its caller a Python object.

**Capabilities are claims, and claims are binding.** An adapter that does not claim `usage` is
not failed for having none — §6.1 makes an absent capability a fact about the deployment, and
the Estate surface shows it as one. An adapter that *claims* a capability and raises
`UnsupportedCapability` fails the suite.

**A case that cannot be executed is an outcome, not an exception.** `execute_case` returns a
`ResultSet` whose `outcome` is `INCONCLUSIVE` and whose `reason` says why — a timeout, an
absent extract reader, a charter naming a strategy this deployment cannot perform. §10.2 is
explicit that a timeout "yields INCONCLUSIVE, not FAIL": none of those is evidence that the
client's report is wrong, and a FAIL would send somebody looking for a bug in a correct
report. `ResultSet.comparable` is the one place that decides whether §10.3 may diff a result.

**Interface 1.1** retyped `ResultSet.columns` from `tuple[str, ...]` to `tuple[Column, ...]`
so §10.2's "name, role, type" survives the wire. Retyping is not additive, so the version
bumped and every promoted build needs a fresh conformance report — see ADR 0020.

## Out of process

```
platform                          adapter worker
────────                          ──────────────
RemoteAdapter  ──── REST ────►    serve(adapter)
     ▲                                  │
AdapterSupervisor ◄── restarts ─────────┘
```

`RemoteAdapter` satisfies §6.1 exactly, so the Harvester cannot tell it from an in-process
adapter and does not need to. A dead adapter, a refused connection and a timeout all arrive
as a retryable `AdapterError` against the asset being worked on, which is what the Harvester
already records before carrying on (S1.2.1).

In production the restarting is Kubernetes' (§5.4: adapters are isolated pods).
`AdapterSupervisor` is for the local stack, CI and the conformance runner — deliberately
small, because a second scheduler competing with Kubernetes is worse than no scheduler.

`graph-svc` harvests through a worker when `ASTRA_ADAPTER_URL` is set, and through the
in-process fixture adapter otherwise.

## The conformance suite

§6.3's five checks and S2.1.2's seven areas — which overlap but are not the same list, so both
are covered — plus an interface-version check that runs first: every other check reads a
result whose meaning depends on the contract both sides think they are speaking.

S2.1.2 makes this suite the definition of "an adapter works", and a tenant will not enable an
adapter without a passing report from it.

| Check | What fails it |
|---|---|
| Discovery completeness | A missing asset, an unexpected one, or one enumerated twice |
| Parse quality | Any corpus asset below 0.98 (§4.1.4), or a missing expected fragment |
| Parse round-trip | The same bytes parsed twice giving different fragments, or a fragment that does not survive JSON |
| AST round-trip | An expression whose canonical text does not re-parse to the same shape |
| AST coverage | Golden expressions the grammar cannot read, or shapes the corpus never exercises |
| Executor determinism | Three runs of one case that disagree, a result with no interface version, an outcome that moves between runs, or an adapter that claims extract read or live query and still executes nothing |
| Visual capture | A blank image, the wrong format, or the wrong size |
| Usage and ownership | Usage mapped to a workbook nobody enumerated, impossible counts, an unowned workbook |
| Error taxonomy | A transient failure marked permanent, or a rejected credential marked retryable |
| Throttling | Giving up on a 429, or surfacing persistent throttling as a plain failure |

**Error taxonomy and throttling need a fault hook.** Neither can be checked by watching an
adapter succeed, so the SDK defines `FaultInjector` — a standard way to ask an adapter to
behave as if the source had misbehaved. The adapter's own backoff and error classification run
for real; only the source's response is forced. **An adapter that does not implement it fails
these two checks rather than skipping them**: §6.2 requires backoff on 429, and an adapter
cannot be certified for behaviour nobody has observed.

**AST coverage is not AST round-trip.** Round-trip asks whether the printer and the parser
agree. Coverage asks whether the grammar reads the corpus *and* whether the corpus exercises
the grammar — a grammar defect in a shape the corpus never contains is a defect the suite
cannot see, so `Corpus.required_node_kinds` names the shapes and the check reports the gap
against the corpus.

**Every check is tested by breaking an adapter.** §6.1 makes passing this suite the condition
of enabling an adapter on a tenant, so a check that cannot fail is not a weak test — it is a
false assurance somebody will act on.

A capability the adapter does not claim is **skipped**, and the skip is printed. A suite that
reported "passed" after running two of five checks would be the false assurance again.

## Development

```bash
python -m pip install -e "packages/adapter-sdk[dev]"
```

```bash
cd packages/adapter-sdk && python -m pytest -q
```

Tests marked `process` spawn real adapter workers and kill them: "an adapter crash does not
take down a worker" is not a claim a mock can support. `-m "not process"` skips them.
