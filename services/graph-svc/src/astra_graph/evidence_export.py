"""Evidence Export -- spec §4.5/§15.3.6/§18.4, story S11.3.2, closes F11.3.

    "As an InfoSec reviewer, I want an Evidence Export that produces a signed bundle
    for a site or a programme, so that we hold the evidence, not the vendor.

    Acceptance criteria:
    - Export selects scope (programme, site, train, MU, date range) and produces a
      bundle: events, decisions, provenance records, verdicts, artefact hashes,
      charter versions, chain roots, and a verification tool
    - Bundle is signed; the console shows the signature and a verification instruction
    - Export of the BlackRock-scale programme completes in under 30 minutes"

**Scope, per three explicit decisions taken before any code was written.**

**Signing is a real, disclosed departure from `bom.py`'s own "the key never reaches
this service" discipline.** That discipline exists because a deployment BOM is signed
by the deployment *pipeline*, an actor outside this service entirely; Evidence Export's
own AC is the opposite shape -- a single, self-service console action that must show a
real signature immediately, with no human step in between. `LocalEvidenceSigner`
mints its own Ed25519 key at construction (the identical "a real, local key, not yet a
durable one" posture `workload_identity.LocalWorkloadIdentityProvider` already set) --
`Settings.evidence_export_private_key_pem` overrides it with a real, durable, client-
configured key. **Every exported bundle carries its own public key alongside its
signature**, so offline verification never depends on this process's own key surviving
a restart -- a deployment that wants one stable, re-verifiable identity across restarts
is the one that configures the durable key; the ephemeral default stays honest and
still produces a real, internally-consistent, immediately-verifiable bundle either way.

**"Programme" scope is the whole graph, a real, disclosed limitation.** Research (and
S11.3.1's own decisions) confirmed no row anywhere in this codebase -- no event,
decision, verdict, artefact or provenance record -- carries a `programme_id`; every one
of them is scoped only by the tenant graph itself, and a single graph can in principle
hold more than one `Programme` row. This platform's own real deployment model is one
graph per tenant/programme in practice (`retention.py`'s own "the graph is the tenant"
footing, reused here), so "export for this programme" resolves to "export everything
in this graph" -- correct for every real deployment this codebase has, disclosed as not
distinguishing multiple concurrent programmes sharing one graph, a scenario nothing in
this schema actually prevents but nothing exercises either. Building real per-row
programme tagging would mean touching `writes.py`'s own chokepoint again, for a
distinction no current deployment needs -- the identical "don't touch the write path for
a benefit nobody has asked for" reasoning ADR 0082 already gave for the Evidence Chain
itself.

**"Site" and "train" scope are both real, bulk-resolved sets of Migration Units
(Workbooks).** Train reuses the exact query `trains._train_members` already runs
(written again here rather than imported, the same "a different epic's own module"
footing `case_execution.py`'s own `_maps_to` already set for `compositor._maps_to` --
`trains.py` does not import this module and this module does not need trains.py's
other machinery). Site has no existing reverse resolver (`case_execution._resolve_site`
only goes workbook → site) -- a real, new two-hop `CONTAINS` query
(`_workbooks_under_site`) closes that gap. "MU" scope is just one workbook id.

**Decisions are scoped by their own real subject, not guessed at.** A G3 decision's
`subject_ref` is a Workbook directly, or (for an Exception Desk adjudication) an
`ExceptionCase` whose own `mu_ref` names the Workbook -- the identical dispatch
`decision_register._subject_label` already draws, reused here (`_subject_label` is a
small, pure function; duplicated rather than imported for the same cross-epic-private-
helper reasoning above). G1 (the platform-wide Tolerance Charter singleton), G2
(ModelFamily) and G4 (Site) decisions are not workbook-scoped concepts at all --
included only for the whole-graph (programme) scope, excluded and disclosed as such
when the scope names a site/train/MU.

**Events are scoped by the union of every id already gathered as in scope.** Rather
than guessing which `estate_event.subject` values "belong" to a site/train/MU (an event
can be about a Workbook, a GateDecision, a ParityCase, a Verdict, or any other node),
this module gathers decisions/verdicts/provenance/artefacts for the resolved workbook
set *first*, then includes every event whose own `subject` is either a workbook in
scope or one of those already-gathered ids -- a real, correct rule (every event about
something already determined to be in scope belongs in the bundle) rather than a
heuristic.

**The bundle is a zip, built entirely in memory** -- the identical shape
`regression_export.py` already established for its own handover bundle, and every
other export in this codebase (`decision_register.py`'s CSV/PDF,
`calibration_wave.py`/`status_pack.py`'s PDF/PPTX) already builds in memory rather than
streaming. **Assembly runs as a background task with a polling status route**, the one
existing precedent for a potentially-long operation this codebase already has
(`routes_rebuild.py`'s own `RebuildStatus`/`202 ACCEPTED`/poll shape) -- every other
export here is small enough to be synchronous; this one's own AC names a 30-minute
budget no HTTP request should sensibly block for.

**The bundled verification tool checks two real, distinct things, both offline.** (1)
The whole manifest's own Ed25519 signature, over its canonical JSON bytes -- the same
`canonical_json` convention this codebase already established (S1.3.1), generalised
here to sign raw bytes directly rather than reusing `bom.sign_document`'s own
`BomDocument`-typed signature (a small, disclosed duplication of two short functions,
kept separate so `bom.py`'s own type-safe API is not distorted for a document shape it
was never written for). (2) The exported `chain_roots`' own mutual consistency --
`root_hash[i-1] == prev_root_hash[i]` -- proving the roots were not reordered or
cherry-picked in the exported bundle. This does **not** re-derive a root's own hash
from raw chain entries (the AC's own wording is "chain roots," not "the whole chain" --
re-deriving would need every underlying `evidence_chain_entry` row for the scope, a
materially larger export than the AC asks for); the signature check already proves
nothing in the manifest, roots included, was altered after export.
"""

from __future__ import annotations

import base64
import io
import json
import logging
import zipfile
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Protocol

import asyncpg
from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey

from .context.canonical import canonical_json
from .evidence_chain import list_daily_roots
from .graph.queries import EDGE_INDEX_TABLE, NODE_INDEX_TABLE
from .lineage import hydrate

logger = logging.getLogger(__name__)

EXPORT_MEDIA_TYPE = "application/zip"
EXPORT_ARTEFACT_KIND = "evidence_export"

#: The Exception Desk's own four remediation outcomes, plus WAIVED -- the identical set
#: `decision_register._EXCEPTION_DECISIONS` already declares (duplicated, not imported,
#: for the same cross-epic-private-helper reasoning this module's own docstring gives).
_EXCEPTION_DECISIONS = frozenset({"PATCHED", "REDESIGN", "MODEL_DEFECT", "SOURCE_DEFECT", "WAIVED"})
_LABEL_FOR_GATE = {"G2": "ModelFamily", "G4": "Site"}


class EvidenceExportError(Exception):
    """A scope could not be resolved, or an export could not be assembled, for a real,
    stated reason."""


# --------------------------------------------------------------------------- scope


@dataclass(frozen=True, slots=True)
class ExportScope:
    """What an export covers. ``ref`` is a Site/Train/Workbook id, or ``None`` for
    ``"programme"`` -- see this module's own docstring for why that means the whole
    graph. ``date_from``/``date_to`` narrow by ``estate_event.time`` regardless of the
    node-scope kind; both ``None`` means no date bound at all."""

    kind: str
    """One of ``"programme"``, ``"site"``, ``"train"``, ``"mu"``."""
    ref: str | None = None
    date_from: str | None = None
    date_to: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind, "ref": self.ref,
            "date_from": self.date_from, "date_to": self.date_to,
        }


def _subject_label(gate: str, decision: str) -> str | None:
    if gate == "G1":
        return None
    if gate == "G3":
        return "ExceptionCase" if decision in _EXCEPTION_DECISIONS else "Workbook"
    return _LABEL_FOR_GATE.get(gate)


async def _workbooks_under_site(conn: asyncpg.Connection, graph_name: str, site_id: str) -> frozenset[str]:
    """Every live Workbook two ``CONTAINS`` hops below a Site (Site → Project →
    Workbook) -- the reverse of `case_execution._resolve_site`, which only ever goes
    the other way. No such query exists anywhere in this codebase yet."""
    rows = await conn.fetch(
        f"""
        SELECT wb.id AS workbook_id
          FROM {EDGE_INDEX_TABLE} site_edge
          JOIN {NODE_INDEX_TABLE} project
            ON project.graph = site_edge.graph AND project.id = site_edge.to_id
           AND project.kind = 'node' AND project.label = 'Project' AND project.retired_at IS NULL
          JOIN {EDGE_INDEX_TABLE} project_edge
            ON project_edge.graph = site_edge.graph AND project_edge.from_id = project.id
           AND project_edge.label = 'CONTAINS' AND project_edge.retired_at IS NULL
          JOIN {NODE_INDEX_TABLE} wb
            ON wb.graph = site_edge.graph AND wb.id = project_edge.to_id
           AND wb.kind = 'node' AND wb.label = 'Workbook' AND wb.retired_at IS NULL
         WHERE site_edge.graph = $1 AND site_edge.label = 'CONTAINS' AND site_edge.from_id = $2
           AND site_edge.retired_at IS NULL
        """,
        graph_name, site_id,
    )
    return frozenset(row["workbook_id"] for row in rows)


async def _workbooks_in_train(conn: asyncpg.Connection, graph_name: str, train_id: str) -> frozenset[str]:
    """The identical query `trains._train_members` already runs -- see this module's
    own docstring for why it is written again rather than imported."""
    rows = await conn.fetch(
        f"""
        SELECT e.from_id AS workbook
          FROM {EDGE_INDEX_TABLE} e
          JOIN {NODE_INDEX_TABLE} n ON n.id = e.from_id AND n.kind = 'node'
           AND n.graph = $1 AND n.label = 'Workbook' AND n.retired_at IS NULL
         WHERE e.graph = $1 AND e.label = 'IN_TRAIN' AND e.to_id = $2 AND e.retired_at IS NULL
        """,
        graph_name, train_id,
    )
    return frozenset(row["workbook"] for row in rows)


async def resolve_workbook_ids(
    pool: asyncpg.Pool, graph_name: str, scope: ExportScope
) -> frozenset[str] | None:
    """``None`` means "the whole graph" (programme scope) -- every other scope resolves
    to a real, non-empty-or-not set of Workbook ids."""
    if scope.kind == "programme":
        return None
    if scope.kind == "mu":
        if not scope.ref:
            raise EvidenceExportError("an 'mu' scope needs a workbook id")
        return frozenset({scope.ref})
    if not scope.ref:
        raise EvidenceExportError(f"a '{scope.kind}' scope needs a real id")
    async with pool.acquire() as conn:
        if scope.kind == "site":
            return await _workbooks_under_site(conn, graph_name, scope.ref)
        if scope.kind == "train":
            return await _workbooks_in_train(conn, graph_name, scope.ref)
    raise EvidenceExportError(f"unknown scope kind {scope.kind!r}")


# ---------------------------------------------------------------------- gathering


def _in_date_range(value: str | None, scope: ExportScope) -> bool:
    if value is None:
        return True
    if scope.date_from and value < scope.date_from:
        return False
    return not (scope.date_to and value >= scope.date_to)


async def _gather_decisions(
    pool: asyncpg.Pool, graph_name: str, workbook_ids: frozenset[str] | None, scope: ExportScope
) -> list[dict[str, Any]]:
    from .decision_register import list_decisions

    rows = await list_decisions(pool, graph_name)
    rows = [row for row in rows if _in_date_range(row.get("timestamp"), scope)]
    if workbook_ids is None:
        return rows

    exception_ids = {
        row["subject_ref"] for row in rows
        if _subject_label(row["gate"], row["decision"]) == "ExceptionCase"
    }
    exception_workbook: dict[str, str] = {}
    if exception_ids:
        async with pool.acquire() as conn:
            cases = await hydrate(conn, graph_name, "ExceptionCase", sorted(exception_ids))
        exception_workbook = {
            case_id: str(props.get("mu_ref")) for case_id, props in cases.items() if props.get("mu_ref")
        }

    def _in_scope(row: dict[str, Any]) -> bool:
        label = _subject_label(row["gate"], row["decision"])
        if label == "Workbook":
            return row["subject_ref"] in workbook_ids
        if label == "ExceptionCase":
            return exception_workbook.get(row["subject_ref"]) in workbook_ids
        return False  # G1/G2/G4: not a workbook-shaped subject -- programme scope only

    return [row for row in rows if _in_scope(row)]


async def _gather_verdicts(
    pool: asyncpg.Pool, graph_name: str, workbook_ids: frozenset[str] | None
) -> list[dict[str, Any]]:
    """Every live ``ParityRun`` (in scope) and its own ``Verdict`` nodes -- the bulk
    shape `verdicts.latest_parity_run` never needed, since that reads one workbook's
    own *latest* run only."""
    async with pool.acquire() as conn:
        run_rows = await conn.fetch(
            f"""SELECT id FROM {NODE_INDEX_TABLE}
             WHERE graph = $1 AND kind = 'node' AND label = 'ParityRun' AND retired_at IS NULL""",
            graph_name,
        )
        runs = await hydrate(conn, graph_name, "ParityRun", [row["id"] for row in run_rows])
        if workbook_ids is not None:
            runs = {rid: props for rid, props in runs.items() if props.get("suite_ref") in workbook_ids}

        verdict_ids = sorted({vid for props in runs.values() for vid in (props.get("verdicts") or [])})
        verdicts = await hydrate(conn, graph_name, "Verdict", verdict_ids)

    return [
        {"run_id": rid, **run_props, "verdicts": [
            {"id": vid, **verdicts[vid]} for vid in (run_props.get("verdicts") or []) if vid in verdicts
        ]}
        for rid, run_props in runs.items()
    ]


async def _gather_provenance(
    pool: asyncpg.Pool, graph_name: str, workbook_ids: frozenset[str] | None, scope: ExportScope
) -> list[dict[str, Any]]:
    from .provenance import PROVENANCE_TABLE

    async with pool.acquire() as conn:
        rows = await conn.fetch(
            f"SELECT * FROM {PROVENANCE_TABLE} WHERE graph = $1 ORDER BY created_at", graph_name,
        )
    out = []
    for row in rows:
        created_at = row["created_at"].isoformat() if row["created_at"] else None
        if not _in_date_range(created_at, scope):
            continue
        if workbook_ids is not None and row["subject_id"] not in workbook_ids:
            continue
        out.append({
            "id": row["id"], "artefact_kind": row["artefact_kind"], "artefact_ref": row["artefact_ref"],
            "artefact_content_hash": row["artefact_content_hash"], "agent": row["agent"],
            "agent_version": row["agent_version"], "mode": row["mode"], "contract": row["contract"],
            "subject_id": row["subject_id"], "context_hash": row["context_hash"],
            "graph_version": row["graph_version"], "prompt_hash": row["prompt_hash"],
            "model": row["model"], "provider": row["provider"], "created_by": row["created_by"],
            "created_at": created_at,
        })
    return out


async def _gather_artefact_hashes(
    pool: asyncpg.Pool, graph_name: str, workbook_ids: frozenset[str] | None, scope: ExportScope
) -> list[dict[str, Any]]:
    """Metadata and content hashes only -- the AC's own literal "artefact hashes," not
    the binary content itself (already free per-artefact, `artefacts._content_hash`)."""
    async with pool.acquire() as conn:
        if workbook_ids is None:
            rows = await conn.fetch(
                "SELECT id, kind, mu_ref, content_hash, media_type, recorded_by, recorded_at "
                "FROM public.artefacts WHERE graph = $1 ORDER BY recorded_at",
                graph_name,
            )
        else:
            rows = await conn.fetch(
                "SELECT id, kind, mu_ref, content_hash, media_type, recorded_by, recorded_at "
                "FROM public.artefacts WHERE graph = $1 AND mu_ref = ANY($2::text[]) ORDER BY recorded_at",
                graph_name, sorted(workbook_ids),
            )
    return [
        {
            "id": row["id"], "kind": row["kind"], "mu_ref": row["mu_ref"],
            "content_hash": row["content_hash"], "media_type": row["media_type"],
            "recorded_by": row["recorded_by"],
            "recorded_at": row["recorded_at"].isoformat() if row["recorded_at"] else None,
        }
        for row in rows
        if _in_date_range(row["recorded_at"].isoformat() if row["recorded_at"] else None, scope)
    ]


async def _gather_charter_versions(pool: asyncpg.Pool, graph_name: str) -> list[dict[str, Any]]:
    """Every version of the Tolerance Charter (§4.4) -- no "list every version" method
    exists on `ToleranceCharterStore` today (only `latest`/`get(version)`), so this is a
    new, direct query rather than a loop over guessed version numbers."""
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT version, charter, updated_by, updated_at FROM public.tolerance_charter_version "
            "WHERE graph = $1 ORDER BY version",
            graph_name,
        )
    return [
        {
            "version": row["version"],
            "charter": json.loads(row["charter"]) if isinstance(row["charter"], str) else row["charter"],
            "updated_by": row["updated_by"],
            "updated_at": row["updated_at"].isoformat() if row["updated_at"] else None,
        }
        for row in rows
    ]


async def _gather_events(
    pool: asyncpg.Pool, graph_name: str, subject_universe: frozenset[str] | None, scope: ExportScope
) -> list[dict[str, Any]]:
    """Every event whose own ``subject`` is in ``subject_universe`` -- the union of the
    scope's own workbook ids and every id already gathered as in scope by the other
    categories (see this module's own docstring for why this is correct, not a
    heuristic). ``None`` means no subject filter at all (programme scope)."""
    async with pool.acquire() as conn:
        if subject_universe is None:
            rows = await conn.fetch(
                "SELECT event_id, type, source, subject, element_kind, label, time, "
                "principal, run_id, data FROM public.estate_event "
                "WHERE graph = $1 AND time >= $2 AND time < $3 ORDER BY seq",
                graph_name, _date_floor(scope.date_from), _date_ceiling(scope.date_to),
            )
        else:
            rows = await conn.fetch(
                "SELECT event_id, type, source, subject, element_kind, label, time, "
                "principal, run_id, data FROM public.estate_event "
                "WHERE graph = $1 AND subject = ANY($2::text[]) AND time >= $3 AND time < $4 "
                "ORDER BY seq",
                graph_name, sorted(subject_universe), _date_floor(scope.date_from), _date_ceiling(scope.date_to),
            )
    return [
        {
            "event_id": row["event_id"], "type": row["type"], "source": row["source"],
            "subject": row["subject"], "element_kind": row["element_kind"], "label": row["label"],
            "time": row["time"].isoformat(), "principal": row["principal"], "run_id": row["run_id"],
            "data": json.loads(row["data"]) if isinstance(row["data"], str) else row["data"],
        }
        for row in rows
    ]


def _date_floor(value: str | None) -> datetime:
    return _parse(value) if value else datetime(1970, 1, 1, tzinfo=UTC)


def _date_ceiling(value: str | None) -> datetime:
    return _parse(value) if value else datetime(9999, 1, 1, tzinfo=UTC)


def _parse(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


# ------------------------------------------------------------------------ signing


class EvidenceSigner(Protocol):
    def sign(self, payload: bytes) -> str: ...
    def public_key_pem(self) -> bytes: ...


class LocalEvidenceSigner:
    """Mints (or loads) a real Ed25519 key and signs with it -- see this module's own
    docstring for why this departs from `bom.py`'s own "key never reaches this
    service" discipline, and why an ephemeral default is still honest."""

    def __init__(self, *, private_key_pem: str = "") -> None:
        if private_key_pem:
            key = serialization.load_pem_private_key(private_key_pem.encode("utf-8"), password=None)
            if not isinstance(key, Ed25519PrivateKey):
                raise EvidenceExportError("ASTRA_EVIDENCE_EXPORT_PRIVATE_KEY_PEM must be Ed25519")
            self._private_key = key
        else:
            self._private_key = Ed25519PrivateKey.generate()

    def sign(self, payload: bytes) -> str:
        return base64.b64encode(self._private_key.sign(payload)).decode("ascii")

    def public_key_pem(self) -> bytes:
        return self._private_key.public_key().public_bytes(
            serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo,
        )


def verify_bytes(payload: bytes, signature: str, public_key_pem: bytes) -> bool:
    """The offline-verifier's own real check -- never raises; any failure mode is
    ``False``, the identical honesty `bom.verify_signature` already established."""
    try:
        public_key = serialization.load_pem_public_key(public_key_pem)
    except ValueError:
        return False
    if not isinstance(public_key, Ed25519PublicKey):
        return False
    try:
        raw_signature = base64.b64decode(signature, validate=True)
    except (ValueError, TypeError):
        return False
    try:
        public_key.verify(raw_signature, payload)
        return True
    except InvalidSignature:
        return False


# ----------------------------------------------------------------------- assembly


@dataclass
class ExportProgress:
    """In-memory, one per process -- the identical `RebuildStatus` shape
    `routes_rebuild.py` already set, for the identical reason: a rare, operator-
    triggered action, not a fact anything else in this platform depends on later. The
    finished bundle itself *is* durable (stored via `ArtefactStore`), unlike a
    rebuild's own scratch graph."""

    running: bool = False
    export_id: str | None = None
    started_at: str | None = None
    finished_at: str | None = None
    scope: dict[str, Any] | None = None
    counts: dict[str, int] | None = None
    artefact_id: str | None = None
    signature: str | None = None
    public_key_pem: str | None = None
    last_error: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "running": self.running,
            "export_id": self.export_id,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "scope": self.scope,
            "counts": self.counts,
            "artefact_id": self.artefact_id,
            "signature": self.signature,
            "public_key_pem": self.public_key_pem,
            "last_error": self.last_error,
        }


_README = """# Evidence Export -- {export_id}

Story S11.3.2, spec §4.5/§15.3.6/§18.4. This bundle is a real, self-contained record
of graph "{graph}", scope {scope}, generated {generated_at} by {generated_by}.

## Contents

- `manifest.json` -- every fact this export covers (events, decisions, provenance
  records, verdicts, artefact hashes, charter versions, chain roots).
- `signature.json` -- the Ed25519 signature over `manifest.json`'s own canonical JSON
  bytes, and the public key that verifies it.
- `verify_bundle.py` -- a standalone verifier. Run it with the `cryptography` package
  installed (`pip install cryptography`): `python verify_bundle.py`.

## Verifying without this codebase at all

`verify_bundle.py` checks two things, both offline, with no network access and no
dependency on this deployment's own continued existence:

1. That `signature.json`'s own signature really verifies against `manifest.json`'s own
   canonical bytes with the embedded public key -- proving nothing in the manifest was
   altered after this bundle was produced.
2. That `manifest.json`'s own `chain_roots` are mutually consistent -- each root's own
   `prev_root_hash` matches the previous root's `root_hash` -- proving the roots
   themselves were not reordered or cherry-picked into this bundle.

Neither check re-derives a root's own hash from the underlying Evidence Chain entries
(this bundle exports the chain's own daily roots, not every entry that produced them);
the signature check already proves nothing in this bundle, roots included, changed
after export.
"""

_VERIFY_BUNDLE_PY = '''#!/usr/bin/env python
"""Standalone Evidence Export verifier -- story S11.3.2. No dependency on this
codebase or a live graph-svc; only the `cryptography` package (`pip install
cryptography`) and this bundle's own two files, `manifest.json` and `signature.json`.
"""

from __future__ import annotations

import base64
import json
import sys
from pathlib import Path

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey


def canonical_json(document: object) -> bytes:
    text = json.dumps(document, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False)
    return text.encode("utf-8")


def verify_signature() -> bool:
    manifest = json.loads(Path("manifest.json").read_text(encoding="utf-8"))
    envelope = json.loads(Path("signature.json").read_text(encoding="utf-8"))

    public_key = serialization.load_pem_public_key(envelope["public_key_pem"].encode("utf-8"))
    if not isinstance(public_key, Ed25519PublicKey):
        print("FAIL -- signature.json's own public key is not Ed25519", file=sys.stderr)
        return False

    raw_signature = base64.b64decode(envelope["signature"], validate=True)
    payload = canonical_json(manifest)
    try:
        public_key.verify(raw_signature, payload)
    except InvalidSignature:
        print("FAIL -- the signature does not verify against manifest.json's own bytes", file=sys.stderr)
        return False
    print("OK -- signature verifies")
    return True


def verify_chain_roots() -> bool:
    manifest = json.loads(Path("manifest.json").read_text(encoding="utf-8"))
    roots = manifest.get("chain_roots") or []
    if not roots:
        print("OK -- no chain roots in this bundle")
        return True
    expected_prev = roots[0]["prev_root_hash"]
    for root in roots:
        if root["prev_root_hash"] != expected_prev:
            print(
                f"FAIL -- daily root for {root['day']} names prev_root_hash "
                f"{root['prev_root_hash']}, expected {expected_prev}",
                file=sys.stderr,
            )
            return False
        expected_prev = root["root_hash"]
    print(f"OK -- {len(roots)} chain roots are mutually consistent")
    return True


def main() -> int:
    signature_ok = verify_signature()
    roots_ok = verify_chain_roots()
    return 0 if (signature_ok and roots_ok) else 1


if __name__ == "__main__":
    raise SystemExit(main())
'''


def build_export_zip(manifest: dict[str, Any], *, signer: EvidenceSigner) -> tuple[bytes, str, bytes]:
    """Signs, then packages. The signature is computed over ``canonical_json(manifest)``
    -- **not** the pretty-printed bytes written into the zip for a human to read --
    since `verify_bundle.py` re-parses `manifest.json` and recomputes its own canonical
    form before checking the signature; signing anything else here would make every
    real verification fail. Returns ``(zip_bytes, signature, public_key_pem)`` so a
    caller can record the signature/key without re-parsing the zip.
    """
    payload = canonical_json(manifest)
    signature = signer.sign(payload)
    public_key_pem = signer.public_key_pem()

    manifest_bytes = json.dumps(manifest, sort_keys=True, indent=2).encode("utf-8")
    signature_bytes = json.dumps(
        {"algorithm": "Ed25519", "signature": signature, "public_key_pem": public_key_pem.decode("utf-8")},
        sort_keys=True, indent=2,
    ).encode("utf-8")
    readme_bytes = _README.format(
        export_id=manifest["export_id"], graph=manifest["graph"],
        scope=manifest["scope"], generated_at=manifest["generated_at"],
        generated_by=manifest["generated_by"],
    ).encode("utf-8")

    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, mode="w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("manifest.json", manifest_bytes)
        archive.writestr("signature.json", signature_bytes)
        archive.writestr("verify_bundle.py", _VERIFY_BUNDLE_PY)
        archive.writestr("README.md", readme_bytes)
    return buffer.getvalue(), signature, public_key_pem


async def assemble_manifest(
    pool: asyncpg.Pool,
    graph_name: str,
    scope: ExportScope,
    *,
    export_id: str,
    generated_by: str,
    on_progress: Callable[[str, int], None] | None = None,
) -> dict[str, Any]:
    """Every category the AC names, gathered in scope order so events (the last
    category) can be scoped by the union of every id already gathered."""
    workbook_ids = await resolve_workbook_ids(pool, graph_name, scope)
    if on_progress:
        on_progress("scope", 1)

    decisions = await _gather_decisions(pool, graph_name, workbook_ids, scope)
    if on_progress:
        on_progress("decisions", len(decisions))

    verdicts = await _gather_verdicts(pool, graph_name, workbook_ids)
    if on_progress:
        on_progress("verdicts", len(verdicts))

    provenance_records = await _gather_provenance(pool, graph_name, workbook_ids, scope)
    if on_progress:
        on_progress("provenance_records", len(provenance_records))

    artefact_hashes = await _gather_artefact_hashes(pool, graph_name, workbook_ids, scope)
    if on_progress:
        on_progress("artefact_hashes", len(artefact_hashes))

    charter_versions = await _gather_charter_versions(pool, graph_name)
    if on_progress:
        on_progress("charter_versions", len(charter_versions))

    roots = await list_daily_roots(pool, graph_name, limit=100_000)
    chain_roots = [root.as_dict() for root in roots]
    if on_progress:
        on_progress("chain_roots", len(chain_roots))

    subject_universe: frozenset[str] | None = None
    if workbook_ids is not None:
        subject_universe = frozenset(workbook_ids)
        subject_universe |= {row["id"] for row in decisions}
        subject_universe |= {row["run_id"] for row in verdicts}
        subject_universe |= {v["id"] for row in verdicts for v in row["verdicts"]}
        subject_universe |= {row["id"] for row in artefact_hashes}
    events = await _gather_events(pool, graph_name, subject_universe, scope)
    if on_progress:
        on_progress("events", len(events))

    counts = {
        "events": len(events), "decisions": len(decisions),
        "provenance_records": len(provenance_records), "verdicts": len(verdicts),
        "artefact_hashes": len(artefact_hashes), "charter_versions": len(charter_versions),
        "chain_roots": len(chain_roots),
    }
    return {
        "export_id": export_id,
        "graph": graph_name,
        "scope": scope.as_dict(),
        "generated_at": datetime.now(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z"),
        "generated_by": generated_by,
        "counts": counts,
        "events": events,
        "decisions": decisions,
        "provenance_records": provenance_records,
        "verdicts": verdicts,
        "artefact_hashes": artefact_hashes,
        "charter_versions": charter_versions,
        "chain_roots": chain_roots,
    }


__all__ = [
    "EXPORT_ARTEFACT_KIND",
    "EXPORT_MEDIA_TYPE",
    "EvidenceExportError",
    "EvidenceSigner",
    "ExportProgress",
    "ExportScope",
    "LocalEvidenceSigner",
    "assemble_manifest",
    "build_export_zip",
    "resolve_workbook_ids",
    "verify_bytes",
]
