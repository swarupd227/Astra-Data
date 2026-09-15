"""Evidence Export -- story S11.3.2, closes F11.3. Pure unit tests (no Postgres): see
test_integration_evidence_export.py for the real scope-resolution/gathering/HTTP
lifecycle."""

from __future__ import annotations

import json
import zipfile
from io import BytesIO

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from astra_graph.context.canonical import canonical_json
from astra_graph.evidence_export import (
    EvidenceExportError,
    ExportScope,
    LocalEvidenceSigner,
    build_export_zip,
    resolve_workbook_ids,
    verify_bytes,
)


def _manifest() -> dict:
    return {
        "export_id": "evexp_test", "graph": "astra_estate",
        "scope": ExportScope(kind="programme").as_dict(),
        "generated_at": "2027-01-01T00:00:00.000Z", "generated_by": "user:infosec@client.example",
        "counts": {"events": 2}, "events": [{"a": 1}, {"b": 2}], "decisions": [],
        "provenance_records": [], "verdicts": [], "artefact_hashes": [], "charter_versions": [],
        "chain_roots": [
            {"day": "2027-01-01", "prev_root_hash": "0" * 64, "root_hash": "a" * 64},
            {"day": "2027-01-02", "prev_root_hash": "a" * 64, "root_hash": "b" * 64},
        ],
    }


class TestExportScope:
    def test_as_dict_carries_every_field(self) -> None:
        scope = ExportScope(kind="site", ref="site-1", date_from="2027-01-01", date_to="2027-02-01")
        assert scope.as_dict() == {
            "kind": "site", "ref": "site-1", "date_from": "2027-01-01", "date_to": "2027-02-01",
        }


class TestResolveWorkbookIds:
    async def test_programme_scope_resolves_to_none(self) -> None:
        assert await resolve_workbook_ids(None, "graph", ExportScope(kind="programme")) is None

    async def test_mu_scope_resolves_to_the_one_workbook(self) -> None:
        result = await resolve_workbook_ids(None, "graph", ExportScope(kind="mu", ref="wb-1"))
        assert result == frozenset({"wb-1"})

    async def test_mu_scope_with_no_ref_is_refused(self) -> None:
        with pytest.raises(EvidenceExportError, match="needs a workbook id"):
            await resolve_workbook_ids(None, "graph", ExportScope(kind="mu"))

    async def test_site_scope_with_no_ref_is_refused(self) -> None:
        with pytest.raises(EvidenceExportError, match="needs a real id"):
            await resolve_workbook_ids(None, "graph", ExportScope(kind="site"))


class TestLocalEvidenceSigner:
    def test_signs_something_a_real_public_key_can_verify(self) -> None:
        signer = LocalEvidenceSigner()
        payload = b"a real manifest's own canonical bytes"
        signature = signer.sign(payload)
        assert verify_bytes(payload, signature, signer.public_key_pem())

    def test_two_signers_mint_two_different_ephemeral_keys(self) -> None:
        a, b = LocalEvidenceSigner(), LocalEvidenceSigner()
        assert a.public_key_pem() != b.public_key_pem()

    def test_a_configured_durable_key_is_used_instead_of_a_fresh_one(self) -> None:
        private_key = Ed25519PrivateKey.generate()
        pem = private_key.private_bytes(
            serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption(),
        ).decode("utf-8")
        expected_public = private_key.public_key().public_bytes(
            serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo,
        )
        signer = LocalEvidenceSigner(private_key_pem=pem)
        assert signer.public_key_pem() == expected_public

    def test_a_non_ed25519_pem_is_refused(self) -> None:
        from cryptography.hazmat.primitives.asymmetric import rsa

        rsa_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        pem = rsa_key.private_bytes(
            serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption(),
        ).decode("utf-8")
        with pytest.raises(EvidenceExportError, match="Ed25519"):
            LocalEvidenceSigner(private_key_pem=pem)


class TestVerifyBytes:
    def test_a_real_signature_verifies(self) -> None:
        signer = LocalEvidenceSigner()
        payload = b"payload"
        signature = signer.sign(payload)
        assert verify_bytes(payload, signature, signer.public_key_pem()) is True

    def test_a_tampered_payload_does_not_verify(self) -> None:
        signer = LocalEvidenceSigner()
        signature = signer.sign(b"original")
        assert verify_bytes(b"tampered", signature, signer.public_key_pem()) is False

    def test_the_wrong_public_key_does_not_verify(self) -> None:
        signer = LocalEvidenceSigner()
        other = LocalEvidenceSigner()
        signature = signer.sign(b"payload")
        assert verify_bytes(b"payload", signature, other.public_key_pem()) is False

    def test_malformed_signature_is_false_not_an_exception(self) -> None:
        signer = LocalEvidenceSigner()
        assert verify_bytes(b"payload", "not-base64!!!", signer.public_key_pem()) is False

    def test_malformed_public_key_is_false_not_an_exception(self) -> None:
        assert verify_bytes(b"payload", "AAAA", b"not a real PEM") is False


class TestBuildExportZip:
    def _zip_contents(self, zip_bytes: bytes) -> dict[str, bytes]:
        with zipfile.ZipFile(BytesIO(zip_bytes)) as archive:
            return {name: archive.read(name) for name in archive.namelist()}

    def test_the_zip_carries_all_four_real_files(self) -> None:
        zip_bytes, _sig, _pub = build_export_zip(_manifest(), signer=LocalEvidenceSigner())
        contents = self._zip_contents(zip_bytes)
        assert set(contents) == {"manifest.json", "signature.json", "verify_bundle.py", "README.md"}

    def test_the_signature_verifies_against_the_manifests_own_canonical_bytes(self) -> None:
        manifest = _manifest()
        zip_bytes, signature, public_key_pem = build_export_zip(manifest, signer=LocalEvidenceSigner())
        contents = self._zip_contents(zip_bytes)

        reparsed = json.loads(contents["manifest.json"])
        assert verify_bytes(canonical_json(reparsed), signature, public_key_pem) is True

    def test_signature_json_carries_the_same_signature_and_key(self) -> None:
        zip_bytes, signature, public_key_pem = build_export_zip(_manifest(), signer=LocalEvidenceSigner())
        contents = self._zip_contents(zip_bytes)
        envelope = json.loads(contents["signature.json"])
        assert envelope["algorithm"] == "Ed25519"
        assert envelope["signature"] == signature
        assert envelope["public_key_pem"] == public_key_pem.decode("utf-8")

    def test_a_tampered_manifest_fails_verification(self) -> None:
        zip_bytes, signature, public_key_pem = build_export_zip(_manifest(), signer=LocalEvidenceSigner())
        contents = self._zip_contents(zip_bytes)
        tampered = json.loads(contents["manifest.json"])
        tampered["events"][0]["a"] = 999
        assert verify_bytes(canonical_json(tampered), signature, public_key_pem) is False

    def test_the_readme_names_the_real_export_id_and_scope(self) -> None:
        manifest = _manifest()
        zip_bytes, _sig, _pub = build_export_zip(manifest, signer=LocalEvidenceSigner())
        contents = self._zip_contents(zip_bytes)
        readme = contents["README.md"].decode("utf-8")
        assert manifest["export_id"] in readme
        assert manifest["graph"] in readme
