"""Signing a conformance report — S2.1.2 criterion 2.

    "Suite output is a signed report stored in the artefact store and linked from Platform
    Health"

A conformance report decides whether an adapter may touch a client's estate (criterion 3), so
it is a thing worth forging: an adapter that failed its parse checks and a report that says it
passed are indistinguishable to whoever reads the second. Signing is what makes the report
evidence rather than a claim.

**What is signed.** The canonical JSON of the report — sorted keys, no insignificant
whitespace — so the signature covers the content and not an encoder's choices. The same
canonicalisation the context assembler uses for `context_hash` (S1.3.1), for the same reason:
a hash that depends on how a dictionary happened to be serialised is a hash of the serialiser.

**What signs it.** HMAC-SHA256 with a key the deployment supplies. §18.1 puts secrets in Key
Vault and E11 brings that; until then the key comes from the environment, and where there is
no key **the report is hashed and explicitly not signed**. `signed` is false, `signature` is
null, and `key_id` says why. A report that claimed a signature it did not have would be worse
than an unsigned one, because an unsigned report is obviously unsigned.

**Why HMAC and not a public-key signature.** The reader and the writer are inside one tenant
(§5.3), and the question the signature answers is "was this report produced by this platform
and not since altered" — not "which of several mutually distrusting parties produced it". A
public-key scheme answers a question nobody is asking yet, at the cost of a key distribution
story this release does not have. If a client ever needs to verify a report without the
platform's key, that is a different design and a version bump on `SIGNATURE_ALGORITHM`.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
from dataclasses import dataclass
from typing import Any

from .suite import ConformanceReport

#: Environment variable holding the signing key. Key Vault replaces this in E11 (§18.1).
SIGNING_KEY_ENV = "ASTRA_CONFORMANCE_SIGNING_KEY"

#: Named in the report so a verifier knows what it is checking, and so changing scheme is a
#: visible change rather than a silent one.
SIGNATURE_ALGORITHM = "hmac-sha256"

HASH_PREFIX = "sha256"


def canonical_json(document: Any) -> bytes:
    """Sorted keys, compact separators, UTF-8. The same rules as `context_hash` (S1.3.1)."""
    return json.dumps(document, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode(
        "utf-8"
    )


@dataclass(frozen=True, slots=True)
class SignedReport:
    """A conformance report, its digest, and a signature over it where one was possible."""

    report: dict[str, Any]
    content_hash: str
    """``sha256:<hex>`` over the canonical bytes. Present whether or not a key was."""

    signed: bool
    signature: str | None = None
    algorithm: str | None = None
    key_id: str | None = None
    """Which key signed it, or — when unsigned — why nothing did. Never a key's value."""

    @property
    def passed(self) -> bool:
        return bool(self.report.get("passed"))

    @property
    def adapter(self) -> str:
        return str(self.report.get("adapter", ""))

    @property
    def adapter_version(self) -> str:
        return str(self.report.get("adapter_version", ""))

    def as_dict(self) -> dict[str, Any]:
        return {
            "report": self.report,
            "content_hash": self.content_hash,
            "signed": self.signed,
            "signature": self.signature,
            "algorithm": self.algorithm,
            "key_id": self.key_id,
        }

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> SignedReport:
        return cls(
            report=dict(raw["report"]),
            content_hash=str(raw["content_hash"]),
            signed=bool(raw.get("signed", False)),
            signature=raw.get("signature"),
            algorithm=raw.get("algorithm"),
            key_id=raw.get("key_id"),
        )


def sign(report: ConformanceReport, *, key: str | None = None, key_id: str = "env") -> SignedReport:
    """Hash the report, and sign it if there is a key.

    The key is read from the environment when not passed, so the caller never has to hold it
    — which keeps it out of argument lists, logs and tracebacks.
    """
    document = report.as_dict()
    payload = canonical_json(document)
    digest = f"{HASH_PREFIX}:{hashlib.sha256(payload).hexdigest()}"

    material = key if key is not None else os.environ.get(SIGNING_KEY_ENV, "")
    if not material:
        return SignedReport(
            report=document,
            content_hash=digest,
            signed=False,
            key_id=(
                f"no signing key; set {SIGNING_KEY_ENV} (Key Vault in E11, spec §18.1). "
                f"The report is hashed and unsigned."
            ),
        )

    signature = hmac.new(material.encode("utf-8"), payload, hashlib.sha256).hexdigest()
    return SignedReport(
        report=document,
        content_hash=digest,
        signed=True,
        signature=signature,
        algorithm=SIGNATURE_ALGORITHM,
        key_id=key_id,
    )


def verify(signed: SignedReport, *, key: str | None = None) -> tuple[bool, str]:
    """Re-derive the hash and the signature. Returns ``(ok, why)``.

    Returns a reason in both cases, because "verification failed" splits into answers that
    call for entirely different responses: content that has been altered, a signature made
    with a different key, and a report that was never signed at all.
    """
    payload = canonical_json(signed.report)
    digest = f"{HASH_PREFIX}:{hashlib.sha256(payload).hexdigest()}"
    if digest != signed.content_hash:
        return False, (
            f"the report's content does not match its hash: recomputed {digest}, "
            f"stored {signed.content_hash}. It has been altered since it was written."
        )

    if not signed.signed:
        return False, (
            "the report is hashed but not signed, so its integrity can be checked and its "
            "origin cannot. It was produced on a deployment with no signing key."
        )

    material = key if key is not None else os.environ.get(SIGNING_KEY_ENV, "")
    if not material:
        return False, (
            f"the report is signed but no key is available to verify it; set {SIGNING_KEY_ENV}"
        )

    expected = hmac.new(material.encode("utf-8"), payload, hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected, signed.signature or ""):
        return False, (
            "the signature does not match: the report was signed with a different key, or "
            "the signature was replaced."
        )
    return True, "content hash and signature both verify"
