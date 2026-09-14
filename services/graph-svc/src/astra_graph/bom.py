"""The signed deployment bill of materials — story S11.1.1, spec §18.

    "A deployment produces a signed bill of materials (images, versions, chart values)
    stored as an evidence record."

**Why a real cryptographic signature, not this codebase's existing "signed" convention.**
`decision_register.py`'s PDF and `calibration_wave.py`'s report are each "signed" by
rendering the approver's name, role and timestamp into the document — a textual
attestation, adequate for a human-readable approval record nobody disputes the origin of.
E11's own stated goal is different: "every event is in a tamper-evident chain" (epic
preamble). A rendered footer is not tamper-evident — anyone who can write to the artefact
store can edit it. An Ed25519 signature is: the document cannot be altered by so much as
one byte, or by the signer's own name, without the signature failing to verify against
the published public key. This is a deliberate, one-off departure from the textual
convention, made because this is the one artefact in the codebase whose own story is
explicitly about the difference between "signed" and "tamper-evident".

**The private key never reaches this service.** The same "a secret never crosses the
API" discipline `credentials.py` established for source credentials applies here to the
signing key: `tools/generate_bom.py` signs a bill of materials wherever the deployment
pipeline runs (a real tenant's CI, or an operator's own Cloud Shell) and POSTs the
already-signed envelope to `POST /v1/deployment/bom`. This module never holds or asks for
a private key; it only builds the canonical document to sign, and verifies a signature
against the deployment's own *public* key when one is configured
(`Settings.bom_public_key_pem`).

**Disclosed, not yet run against a live deployment.** Signing, verification and tamper
detection are all real and tested (a flipped byte fails `verify_signature`). No file
built here has ever come from an actual `helm upgrade --install` against a real cluster,
because the AC's own scoping answer for S11.1.1 keeps this session to local validation —
see `deploy/terraform/` and `deploy/helm/`'s own README notes.
"""

from __future__ import annotations

import base64
from dataclasses import dataclass
from typing import Any

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
    Ed25519PublicKey,
)

from .context.canonical import CanonicalisationError, canonical_json, context_hash

#: The bill of materials schema this module builds and understands. Bumped if the shape
#: of the signed document ever changes — a signature is only meaningful against the exact
#: bytes it was taken over, so a schema change is a compatibility break, not a detail.
SCHEMA_VERSION = 1


class BomError(Exception):
    """A bill of materials could not be built, signed or verified."""


@dataclass(frozen=True, slots=True)
class ImageRef:
    """One container image the deployment actually runs."""

    name: str
    """The workload it is — ``graph-svc``, ``console-web``, ``opensearch``, ``temporal``."""

    repository: str
    tag: str
    digest: str
    """``sha256:<hex>`` — the one part of an image reference that cannot be re-tagged out
    from under an evidence record after the fact."""

    def as_dict(self) -> dict[str, str]:
        return {
            "name": self.name,
            "repository": self.repository,
            "tag": self.tag,
            "digest": self.digest,
        }


@dataclass(frozen=True, slots=True)
class BomDocument:
    """Exactly the three things the AC names: images, versions, chart values — plus what
    a document needs to be checkable rather than merely descriptive (who, when, of what
    commit). Chart *values* are represented by their own hash, not embedded verbatim: a
    values file can carry a tenant's own hostnames or resource sizing, and this record is
    evidence that a specific configuration was deployed, not a second copy of that
    configuration to keep in sync."""

    schema_version: int
    chart_name: str
    chart_version: str
    app_version: str
    git_commit: str
    images: tuple[ImageRef, ...]
    chart_values_hash: str
    """``context_hash`` of the canonicalised values document actually deployed."""
    generated_at: str
    """ISO-8601 UTC, e.g. ``2026-09-14T12:00:00Z`` — set by the caller, not this module,
    the same "the assembler doesn't stamp its own clock" discipline `canonical.py` states
    for context hashing; here the timestamp is part of the evidence, not excluded from
    it, so the caller sets it explicitly rather than this module reaching for the system
    clock."""
    generated_by: str
    """The principal that ran the deployment pipeline, e.g. ``service:deploy-pipeline``."""

    def as_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "chart_name": self.chart_name,
            "chart_version": self.chart_version,
            "app_version": self.app_version,
            "git_commit": self.git_commit,
            "images": [image.as_dict() for image in self.images],
            "chart_values_hash": self.chart_values_hash,
            "generated_at": self.generated_at,
            "generated_by": self.generated_by,
        }


@dataclass(frozen=True, slots=True)
class SignedBom:
    """A document plus the Ed25519 signature over its canonical bytes."""

    document: BomDocument
    signature: str
    """Base64-encoded, standard alphabet — the wire and storage form."""

    def as_dict(self) -> dict[str, Any]:
        return {"document": self.document.as_dict(), "signature": self.signature}


def build_document(
    *,
    chart_name: str,
    chart_version: str,
    app_version: str,
    git_commit: str,
    images: list[ImageRef],
    chart_values: dict[str, Any],
    generated_at: str,
    generated_by: str,
) -> BomDocument:
    if not images:
        raise BomError("a bill of materials with no images describes no deployment")
    values_hash = context_hash(canonical_json(chart_values))
    return BomDocument(
        schema_version=SCHEMA_VERSION,
        chart_name=chart_name,
        chart_version=chart_version,
        app_version=app_version,
        git_commit=git_commit,
        images=tuple(images),
        chart_values_hash=values_hash,
        generated_at=generated_at,
        generated_by=generated_by,
    )


def document_from_dict(payload: dict[str, Any]) -> BomDocument:
    try:
        images = tuple(
            ImageRef(
                name=image["name"],
                repository=image["repository"],
                tag=image["tag"],
                digest=image["digest"],
            )
            for image in payload["images"]
        )
        return BomDocument(
            schema_version=int(payload["schema_version"]),
            chart_name=payload["chart_name"],
            chart_version=payload["chart_version"],
            app_version=payload["app_version"],
            git_commit=payload["git_commit"],
            images=images,
            chart_values_hash=payload["chart_values_hash"],
            generated_at=payload["generated_at"],
            generated_by=payload["generated_by"],
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise BomError(f"not a valid bill-of-materials document: {exc}") from exc


def generate_signing_keypair() -> tuple[bytes, bytes]:
    """A fresh Ed25519 keypair, PEM-encoded: ``(private_pem, public_pem)``. The private
    half belongs in the deployment pipeline's own secret store (Key Vault, in a real
    tenant) — never in this service. Called from ``tools/generate_bom.py
    --generate-keypair``, once per deployment, not per release."""
    private_key = Ed25519PrivateKey.generate()
    private_pem = private_key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    )
    public_pem = private_key.public_key().public_bytes(
        serialization.Encoding.PEM,
        serialization.PublicFormat.SubjectPublicKeyInfo,
    )
    return private_pem, public_pem


def sign_document(document: BomDocument, private_key_pem: bytes) -> str:
    """The base64 Ed25519 signature over the document's canonical JSON bytes."""
    try:
        private_key = serialization.load_pem_private_key(private_key_pem, password=None)
    except (ValueError, TypeError) as exc:
        raise BomError(f"not a valid Ed25519 private key: {exc}") from exc
    if not isinstance(private_key, Ed25519PrivateKey):
        raise BomError("the signing key must be Ed25519")
    try:
        payload = canonical_json(document.as_dict())
    except CanonicalisationError as exc:
        raise BomError(str(exc)) from exc
    signature = private_key.sign(payload)
    return base64.b64encode(signature).decode("ascii")


def verify_signature(document: BomDocument, signature: str, public_key_pem: bytes) -> bool:
    """``True`` only if ``signature`` is a valid Ed25519 signature, by the holder of
    ``public_key_pem``, over exactly this document's canonical bytes. Any difference —
    one field edited, a stale signature from a previous document, a corrupted key — is
    ``False``, never an exception a caller might mistake for "verified"."""
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
        payload = canonical_json(document.as_dict())
    except CanonicalisationError:
        return False
    try:
        public_key.verify(raw_signature, payload)
    except InvalidSignature:
        return False
    return True


__all__ = [
    "SCHEMA_VERSION",
    "BomDocument",
    "BomError",
    "ImageRef",
    "SignedBom",
    "build_document",
    "document_from_dict",
    "generate_signing_keypair",
    "sign_document",
    "verify_signature",
]
