# ADR 0083 — Evidence Export: a real signing key in this service

Status: accepted · 15 September 2026 · Story S11.3.2, closes F11.3 (E11)

## Context

S11.3.2 — the backlog's own AC, verbatim: *"As an InfoSec reviewer, I want an Evidence
Export that produces a signed bundle for a site or a programme, so that we hold the
evidence, not the vendor."*

- Export selects scope (programme, site, train, MU, date range) and produces a bundle:
  events, decisions, provenance records, verdicts, artefact hashes, charter versions,
  chain roots, and a verification tool
- Bundle is signed; the console shows the signature and a verification instruction
- Export of the BlackRock-scale programme completes in under 30 minutes

Spec §4.5/§15.3.6/§18.4 name the same construct. Before any code was written, research
(an `Explore` agent's own full pass over `bom.py`'s own Ed25519 signing, `evidence_
chain.py` (ADR 0082), `decision_register.py`'s own CSV/PDF export routes, `artefacts.
py`, `routes_rebuild.py`'s own background-task/poll shape, and the product spec) found:
no row anywhere in this codebase carries a `programme_id`; `bom.py`'s own signing
discipline is "the private key never reaches this service" *because* a deployment BOM
is signed by the deployment pipeline, an actor outside this service; and `routes_
rebuild.py`'s own `RebuildStatus`/`202 ACCEPTED`/poll shape is the only precedent this
codebase has for an operation too long to run inline on an HTTP request. Three explicit
questions were asked and answered before writing any code, all "(recommended)":

1. **Signing boundary** → *"Real server-side signing key, disclosed departure."*
   graph-svc itself holds an Ed25519 key and signs at export time — a deliberate,
   disclosed departure from `bom.py`'s own discipline, because Evidence Export's own AC
   is the opposite shape: a single, self-service console action with no deployment
   pipeline standing between the request and "the console shows the signature."
2. **Programme scope** → *"Programme scope = the whole graph, disclosed limitation."*
   No code anywhere associates a row with a specific `Programme`; "export for this
   programme" resolves to "export everything in this graph," matching this platform's
   real one-graph-per-tenant deployment model.
3. **Scale verification** → *"Seed a real ~1,067-workbook estate and literally time
   it."* Reuse `tools/seed_test_estate.py` at real BlackRock scale (matching S1.4.1's
   own already-benchmarked figure) and report the real wall-clock export time.

## Decisions

### 1. `LocalEvidenceSigner` mints its own key; every bundle carries its own public key

`Settings.evidence_export_private_key_pem` (env `ASTRA_EVIDENCE_EXPORT_PRIVATE_KEY_
PEM`) mirrors `bom_public_key_pem`'s own placement, but on the opposite side: when
empty (the honest default for a deployment that has not provisioned a durable key),
`LocalEvidenceSigner` generates a fresh Ed25519 key at process construction — the
identical "a real, local key, not yet a durable one" posture `workload_identity.
LocalWorkloadIdentityProvider` already established for SVIDs (ADR 0079). **Every
exported bundle embeds its own public key** (`signature.json`), so offline verification
never depends on this process's own key surviving a restart or rotation — a deployment
that wants one stable, re-verifiable identity across restarts is the one that
configures the durable PEM; the ephemeral default still produces a real,
internally-consistent, immediately-verifiable bundle either way.

`bom.py`'s own `sign_document`/`verify_signature` were **not** reused directly, despite
being generic over anything with `.as_dict()` in principle — they are type-hinted to
`BomDocument` specifically, and distorting that type-safe API for a document shape it
was never written for would be a worse trade than a small, disclosed duplication. Two
small functions (`LocalEvidenceSigner.sign(payload: bytes)`, `verify_bytes(payload,
signature, public_key_pem)`) operate on raw bytes directly, reusing the same underlying
`cryptography` primitives `bom.py` already uses.

### 2. The signature covers canonical JSON, computed inside `build_export_zip` itself

`build_export_zip(manifest, *, signer)` takes the signer, not a precomputed signature —
it computes `payload = canonical_json(manifest)` (`context.canonical`, S1.3.1's own
convention) and signs *that*, while still writing `manifest.json` to the zip
pretty-printed (`indent=2`) for a human reading it directly. This is not cosmetic: a
first draft of this function accepted a precomputed signature and signed the
pretty-printed bytes instead — caught before any test ran, by recognising that the
vendored `verify_bundle.py`'s own canonical serializer (compact, `separators=(",",
":")`) can never byte-match an `indent=2` serialization of the same data, so any
signature computed over the pretty bytes could never be verified by the standalone
tool. Computing the signature from the canonical form *inside* the same function that
writes the pretty form makes the mismatch structurally impossible rather than merely
avoided by convention. Verified live: a real zip, extracted, and the literal vendored
`verify_bundle.py` run as a real subprocess — `OK -- signature verifies` / `OK -- 2
chain roots are mutually consistent`, exit 0; a `sed`-tampered `manifest.json` re-run
through the same real script — `FAIL -- the signature does not verify against manifest.
json's own bytes`, exit 1, with the untouched chain-roots check still correctly `OK`.

### 3. Scope resolves to a real workbook set per kind; events are scoped last, by union

"Programme" resolves to `None` (no workbook filter — see the AC's own decision above).
"MU" is one workbook id. "Site" and "train" are real, bulk-resolved sets: train reuses
the identical query `trains._train_members` already runs (written again, not imported —
the same "a different epic's own module" footing `case_execution.py`'s own `_maps_to`
already set for `compositor._maps_to`); site has no existing reverse resolver
(`case_execution._resolve_site` only goes workbook → site), so `_workbooks_under_site`
is a real, new two-hop `CONTAINS` query (Site → Project → Workbook) closing that gap.

Decisions are scoped by their own real subject, not guessed at: a G3 decision's
`subject_ref` is a Workbook directly, or (an Exception Desk adjudication) an
`ExceptionCase` whose own `mu_ref` names the Workbook — the identical dispatch
`decision_register._subject_label` already draws, duplicated here for the same
cross-epic-private-helper reasoning. G1 (the platform-wide Tolerance Charter), G2
(ModelFamily) and G4 (Site) decisions are not workbook-scoped concepts at all —
included only for programme (whole-graph) scope, excluded and disclosed as such for
site/train/MU.

**Events are scoped by the union of every id already gathered as in scope, resolved
last.** Rather than guessing which `estate_event.subject` values "belong" to a site (an
event can be about a Workbook, a GateDecision, a ParityRun, a Verdict, or any other
node), `assemble_manifest` gathers decisions/verdicts/provenance/artefacts for the
resolved workbook set *first*, then calls `_gather_events` with the union of every id
just gathered as `subject_universe` — a real, correct rule (every event about something
already determined to be in scope belongs in the bundle), not a heuristic.

### 4. The bundle is an in-memory zip; assembly is a background task with a poll route

Matches every other export this codebase already has (`regression_export.py`,
`decision_register.py`'s CSV/PDF, `calibration_wave.py`/`status_pack.py`'s PDF/PPTX) —
built entirely in memory, no streaming. Assembly itself runs as a background `asyncio.
create_task`, polled via `GET /v1/evidence-export/status` — the one existing precedent
for a potentially-long operation this codebase has (`routes_rebuild.py`'s own
`RebuildStatus`/`202 ACCEPTED`/poll shape, reused field-for-field as `ExportProgress`),
because the AC's own 30-minute budget is far beyond what any HTTP request should
sensibly block on. The finished bundle persists via the existing `ArtefactStore`
(`kind="evidence_export"`) — reusing the mechanism whose own module docstring in
`artefacts.py` already anticipated "an evidence bundle at E7" as a future kind — rather
than a new table.

### 5. Triggering and reading are both the InfoSec reviewer's own gate, not platform-engineer-only

`POST /v1/evidence-export`, `GET .../status`, `GET .../{id}/download` and `GET
.../public-key` are all gated `TenantAccessReaderDep` (Artizent, or the InfoSec
reviewer) — a deliberate departure from S11.1.2/S11.2.1/S11.3.1's own "platform
engineer triggers, InfoSec only reads" pattern, because *this* story's own AC persona
("As an InfoSec reviewer, I want an Evidence Export...") explicitly wants the InfoSec
reviewer to be the one who triggers it, matching `decision_register.py`'s own export
routes, which use the identical read-level gate for their own export actions. Download
is a new, dedicated route rather than widening `GET /v1/artefacts/{id}/content` (gated
`ArtefactReaderDep`, Artizent or the client report owner) — that would grant every
other artefact kind to InfoSec too, broader than this story asks for.

## Consequences

- `services/graph-svc`: new `evidence_export.py` (`ExportScope`, `resolve_workbook_
  ids`, the six `_gather_*` functions, `LocalEvidenceSigner`/`verify_bytes`,
  `ExportProgress`, `build_export_zip`, `assemble_manifest`, the vendored `_VERIFY_
  BUNDLE_PY`/`_README` constants). New `Settings.evidence_export_private_key_pem`
  (env `ASTRA_EVIDENCE_EXPORT_PRIVATE_KEY_PEM`). New `api/routes_evidence_export.py`
  (`POST /v1/evidence-export`, `GET /v1/evidence-export/status`, `GET /v1/evidence-
  export/{id}/download`, `GET /v1/evidence-export/public-key`). No migration, no
  ontology change — nothing this story reads or writes is new persisted state beyond
  the existing `ArtefactStore`; confirmed via `ontology_check.py`/`migration_check.py`
  passing unchanged.
- `services/console-web`: Tenant & Access gained a fourth pane, "Evidence export" — the
  identical "this screen already exists to show real governance facts, for this same
  persona and feature" placement ADR 0082 already used for Evidence Chain/Retention.
  Scope selection (kind/ref/date range), a Generate action, poll-driven progress, and —
  once finished — the real signature, category counts, a verification instruction
  naming the bundle's own vendored `verify_bundle.py`, and a Download action. No role
  check narrows Generate beyond the screen's own read gate, matching decision 5 above.
  New `EvidenceExportScope`/`EvidenceExportProgress` types and four `Api` methods
  (`startEvidenceExport`/`evidenceExportStatus`/`evidenceExportDownload`/
  `evidenceExportPublicKey`) in `lib/api.ts`; download reuses the existing `getBlob` +
  `downloadBlob` pattern `decision_register.py`'s own CSV/PDF export already
  established, not a bare `<a href>` link.
- Verified: `services/graph-svc` — 19 new pure unit tests (`ExportScope.as_dict`,
  `resolve_workbook_ids`'s non-DB-touching paths, `LocalEvidenceSigner` signing/loading
  a configured durable key/refusing a non-Ed25519 PEM, `verify_bytes`'s every failure
  mode returning `False` rather than raising, `build_export_zip`'s own signature
  verifying against the *re-parsed* manifest's canonical bytes and a tampered manifest
  correctly failing). 16 new integration tests against real PostgreSQL + Apache AGE:
  real site/train scope resolution (including a site with no real workbooks resolving
  to an empty set, and a workbook genuinely outside the scoped site being excluded); a
  real programme-scoped export including every gate (G1–G4); a real site-scoped export
  including only that site's own G3 decisions (workbook-subject and ExceptionCase-
  subject) and excluding G1/G2/G4; a real site-scoped export's own verdicts/provenance/
  artefacts, and a real *other* site's own export correctly excluding them; every
  charter version present regardless of scope; events correctly scoped to a workbook's
  own real subjects, including an already-in-scope decision's own event; the full HTTP
  lifecycle (202/single-flight guard/poll-to-completion/download/public-key), and the
  role gate (the InfoSec reviewer allowed, a client report owner refused). **Two real
  bugs were found and fixed by these integration tests, not by review**: `_gather_
  artefact_hashes` selected non-existent `created_by`/`created_at` columns against
  `public.artefacts` (the real columns are `recorded_by`/`recorded_at`); `_gather_
  charter_versions` selected a non-existent `document` column against `public.
  tolerance_charter_version` (the real column is `charter`) — both would have thrown a
  real `UndefinedColumnError` against production data despite every unit test passing,
  since the unit suite never touches these tables directly. The full graph-svc suite
  re-ran clean (2524 passed, up from 2489, zero regressions); `ruff`/`mypy`/`ontology_
  check.py`/`migration_check.py` all clean. `services/console-web` — 6 new tests (the
  honest empty state, a scope id required before Generate enables for site/train/MU, a
  real programme-scoped export showing its own signature/counts/verification
  instruction, a running-assembly state, a surfaced `last_error`, and a real download
  round trip); the full suite re-ran clean (494 passed, up from 488, zero regressions);
  `tsc --noEmit`/`eslint` clean. **Live-scale verification, the depth explicitly
  chosen above**: a real 1,067-workbook estate (`tools/seed_test_estate.py --workbooks
  1067`, matching S1.4.1's own already-benchmarked BlackRock figure — 17,074 nodes,
  24,542 edges, seeded in 420.9s/7.02 min, a real setup cost kept separate from the
  timed figure below) was seeded into a dedicated, disposable graph, then a real
  programme-scoped Evidence Export was assembled, signed and zipped end to end,
  wall-clock timed: **11.24 seconds (0.19 min)** — a 2,086,330-byte bundle covering
  41,749 real events, well under the AC's own 30-minute budget by two orders of
  magnitude. Decisions/verdicts/provenance/artefact-hashes/charter-versions/chain-roots
  all counted honestly zero, since the seeder writes only graph structure (no gate
  decisions, parity runs, provenance records or a chained evidence log exist on a
  freshly seeded estate) — a real result, not a shortfall in the export itself, which
  correctly reported what the estate actually contained. The resulting bundle was
  verified with the literal vendored `verify_bundle.py`, run as a real subprocess
  against the real extracted zip: `OK -- signature verifies` / `OK -- no chain roots in
  this bundle`, exit 0. The benchmark graph was dropped afterward; nothing in the
  running Docker stack's own demo estate was touched.

## Alternatives considered

**Reuse `bom.sign_document`/`bom.verify_signature` directly**, since they are already
generic over anything with `.as_dict()`. Rejected — see decision 1: distorting a
type-safe API built for `BomDocument` specifically, for a document shape it was never
written for, is a worse trade than two small, disclosed, duplicated functions over raw
bytes.

**Keep the signing key outside this service**, matching `bom.py`'s own discipline
exactly. Rejected by the user's own explicit answer — a deployment BOM is signed by a
pipeline that exists outside this service entirely; Evidence Export's own AC is a
single, self-service console action with no such pipeline in the loop, so the
discipline's own reason does not transfer.

**Build real per-row `programme_id` tagging**, so "programme" scope could distinguish
multiple concurrent programmes sharing one graph. Rejected by the user's own explicit
answer — nothing in this schema currently needs the distinction (this platform's real
deployment model is one graph per tenant/programme), and touching `writes.py`'s own
chokepoint again for a benefit no current deployment needs is the identical reasoning
ADR 0082 already gave for the Evidence Chain itself.

**Re-derive each chain root's own hash from the underlying `evidence_chain_entry`
rows**, rather than trusting the exported `chain_roots` list's own mutual consistency.
Rejected — the AC's own wording is "chain roots," not "the whole chain"; re-deriving
would need every underlying entry row for the scope, a materially larger export than
asked for, and the signature check already proves nothing in the manifest, roots
included, was altered after export.
