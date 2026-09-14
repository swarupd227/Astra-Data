"""The signed deployment bill of materials — story S11.1.1, spec §18.1."""

from __future__ import annotations

import pytest

from astra_graph.bom import (
    BomError,
    ImageRef,
    build_document,
    document_from_dict,
    generate_signing_keypair,
    sign_document,
    verify_signature,
)


def _images() -> list[ImageRef]:
    return [
        ImageRef(
            name="graph-svc",
            repository="myregistry.azurecr.io/astra/graph-svc",
            tag="0.1.0",
            digest="sha256:" + "a" * 64,
        ),
        ImageRef(
            name="console-web",
            repository="myregistry.azurecr.io/astra/console-web",
            tag="0.1.0",
            digest="sha256:" + "b" * 64,
        ),
    ]


def _document(**overrides: object):
    kwargs = {
        "chart_name": "astra-data",
        "chart_version": "0.1.0",
        "app_version": "0.1.0",
        "git_commit": "a" * 40,
        "images": _images(),
        "chart_values": {"replicaCount": 2, "hostname": "astra.client.example"},
        "generated_at": "2026-09-14T12:00:00Z",
        "generated_by": "service:deploy-pipeline",
    }
    kwargs.update(overrides)
    return build_document(**kwargs)  # type: ignore[arg-type]


def test_a_document_needs_at_least_one_image() -> None:
    with pytest.raises(BomError, match="no images"):
        build_document(
            chart_name="astra-data",
            chart_version="0.1.0",
            app_version="0.1.0",
            git_commit="a" * 40,
            images=[],
            chart_values={},
            generated_at="2026-09-14T12:00:00Z",
            generated_by="service:deploy-pipeline",
        )


def test_chart_values_are_hashed_not_embedded() -> None:
    """§18.1's own "chart values" bullet is honoured by a hash, not a verbatim copy --
    the document never carries a values field a tenant's own hostnames could leak
    through."""
    document = _document()
    assert "hostname" not in document.as_dict()["chart_values_hash"]
    assert document.chart_values_hash.startswith("sha256:")


def test_two_documents_over_the_same_values_hash_identically() -> None:
    one = _document()
    two = _document()
    assert one.chart_values_hash == two.chart_values_hash


def test_a_different_values_document_hashes_differently() -> None:
    one = _document(chart_values={"replicaCount": 2})
    two = _document(chart_values={"replicaCount": 3})
    assert one.chart_values_hash != two.chart_values_hash


def test_document_round_trips_through_a_dict() -> None:
    document = _document()
    restored = document_from_dict(document.as_dict())
    assert restored == document


def test_document_from_dict_rejects_a_malformed_payload() -> None:
    with pytest.raises(BomError, match="not a valid bill-of-materials document"):
        document_from_dict({"chart_name": "astra-data"})


# --------------------------------------------------------------------- signing


def test_generated_keypair_is_pem_encoded() -> None:
    private_pem, public_pem = generate_signing_keypair()
    assert private_pem.startswith(b"-----BEGIN PRIVATE KEY-----")
    assert public_pem.startswith(b"-----BEGIN PUBLIC KEY-----")


def test_a_document_signed_with_its_own_key_verifies() -> None:
    private_pem, public_pem = generate_signing_keypair()
    document = _document()
    signature = sign_document(document, private_pem)
    assert verify_signature(document, signature, public_pem)


def test_a_tampered_document_fails_verification() -> None:
    """The whole point of a real signature, not a rendered attestation (see bom.py's own
    module docstring): editing so much as one field breaks it."""
    private_pem, public_pem = generate_signing_keypair()
    document = _document()
    signature = sign_document(document, private_pem)

    tampered = document_from_dict({**document.as_dict(), "chart_version": "0.1.1-tampered"})
    assert not verify_signature(tampered, signature, public_pem)


def test_a_signature_from_a_different_key_fails_verification() -> None:
    private_pem_a, _public_pem_a = generate_signing_keypair()
    _private_pem_b, public_pem_b = generate_signing_keypair()
    document = _document()
    signature = sign_document(document, private_pem_a)
    assert not verify_signature(document, signature, public_pem_b)


def test_a_corrupted_signature_fails_verification_not_an_exception() -> None:
    _private_pem, public_pem = generate_signing_keypair()
    document = _document()
    assert verify_signature(document, "not-valid-base64!!!", public_pem) is False


def test_signing_with_a_non_ed25519_key_is_rejected() -> None:
    with pytest.raises(BomError, match="Ed25519"):
        sign_document(_document(), b"not a real pem key at all")


def test_verifying_against_a_malformed_public_key_is_false_not_an_exception() -> None:
    private_pem, _public_pem = generate_signing_keypair()
    document = _document()
    signature = sign_document(document, private_pem)
    assert verify_signature(document, signature, b"not a real pem key") is False
