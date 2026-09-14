"""The deployment bill of materials' HTTP surface — story S11.1.1, spec §18.1.

Against the in-memory `ArtefactStore` the `client` fixture already wires up (see
`conftest.py`) — the same store every other artefact-backed route in this suite tests
against, so no new fixture is needed for "store it and read it back".
"""

from __future__ import annotations

from astra_graph.bom import ImageRef, build_document, generate_signing_keypair, sign_document
from astra_graph.roles import ROLES_HEADER

from .conftest import ARTIZENT_HEADERS, CLIENT_HEADERS, HEADERS

INFOSEC_HEADERS = {**HEADERS, ROLES_HEADER: "client_infosec_reviewer"}


def _envelope(**overrides: object) -> dict[str, object]:
    private_pem, _public_pem = generate_signing_keypair()
    document = build_document(
        chart_name="astra-data",
        chart_version="0.1.0",
        app_version="0.1.0",
        git_commit="a" * 40,
        images=[
            ImageRef(
                name="graph-svc",
                repository="myregistry.azurecr.io/astra/graph-svc",
                tag="0.1.0",
                digest="sha256:" + "a" * 64,
            )
        ],
        chart_values={"replicaCount": 2},
        generated_at="2026-09-14T12:00:00Z",
        generated_by="service:deploy-pipeline",
    )
    signature = sign_document(document, private_pem)
    envelope = {"document": document.as_dict(), "signature": signature}
    envelope.update(overrides)
    return envelope, private_pem


async def test_submitting_a_bom_requires_a_role(client) -> None:
    envelope, _key = _envelope()
    response = await client.post("/v1/deployment/bom", json=envelope, headers=HEADERS)
    assert response.status_code == 403


async def test_submitting_a_bom_is_closed_to_client_roles(client) -> None:
    envelope, _key = _envelope()
    response = await client.post("/v1/deployment/bom", json=envelope, headers=CLIENT_HEADERS)
    assert response.status_code == 403


async def test_an_artizent_role_can_submit_a_bom(client) -> None:
    envelope, _key = _envelope()
    response = await client.post("/v1/deployment/bom", json=envelope, headers=ARTIZENT_HEADERS)
    assert response.status_code == 201
    body = response.json()
    assert body["kind"] == "deployment_bom"
    assert body["document"]["chart_name"] == "astra-data"
    assert body["signature_verified"] is None  # no ASTRA_BOM_PUBLIC_KEY_PEM configured


async def test_reading_a_bom_requires_a_role(client) -> None:
    envelope, _key = _envelope()
    submitted = await client.post("/v1/deployment/bom", json=envelope, headers=ARTIZENT_HEADERS)
    artefact_id = submitted.json()["id"]
    response = await client.get(f"/v1/deployment/bom/{artefact_id}", headers=HEADERS)
    assert response.status_code == 403


async def test_the_infosec_reviewer_can_read_a_bom(client) -> None:
    envelope, _key = _envelope()
    submitted = await client.post("/v1/deployment/bom", json=envelope, headers=ARTIZENT_HEADERS)
    artefact_id = submitted.json()["id"]
    response = await client.get(f"/v1/deployment/bom/{artefact_id}", headers=INFOSEC_HEADERS)
    assert response.status_code == 200
    assert response.json()["document"]["chart_name"] == "astra-data"


async def test_a_report_owner_cannot_read_a_bom(client) -> None:
    """Unlike the Decision Register/Exception Desk, this evidence surface is scoped to
    Artizent + InfoSec reviewer only -- a report owner has no real remit over deployment
    evidence."""
    envelope, _key = _envelope()
    submitted = await client.post("/v1/deployment/bom", json=envelope, headers=ARTIZENT_HEADERS)
    artefact_id = submitted.json()["id"]
    response = await client.get(f"/v1/deployment/bom/{artefact_id}", headers=CLIENT_HEADERS)
    assert response.status_code == 403


async def test_listing_boms(client) -> None:
    first, _key1 = _envelope()
    second, _key2 = _envelope()
    second["document"]["chart_version"] = "0.2.0"
    await client.post("/v1/deployment/bom", json=first, headers=ARTIZENT_HEADERS)
    await client.post("/v1/deployment/bom", json=second, headers=ARTIZENT_HEADERS)

    response = await client.get("/v1/deployment/bom", headers=INFOSEC_HEADERS)
    assert response.status_code == 200
    boms = response.json()["boms"]
    assert len(boms) == 2
    assert {bom["document"]["chart_version"] for bom in boms} == {"0.1.0", "0.2.0"}


async def test_a_nonexistent_bom_is_404(client) -> None:
    response = await client.get("/v1/deployment/bom/af_does_not_exist", headers=INFOSEC_HEADERS)
    assert response.status_code == 404


async def test_a_bom_with_no_images_is_rejected(client) -> None:
    envelope, _key = _envelope()
    envelope["document"]["images"] = []
    response = await client.post("/v1/deployment/bom", json=envelope, headers=ARTIZENT_HEADERS)
    assert response.status_code == 422  # pydantic's min_length=1 on images


async def test_submitting_without_a_signature_is_rejected(client) -> None:
    envelope, _key = _envelope()
    del envelope["signature"]
    response = await client.post("/v1/deployment/bom", json=envelope, headers=ARTIZENT_HEADERS)
    assert response.status_code == 422


async def test_submitted_bom_signature_is_recomputed_not_stored(client) -> None:
    """`signature_verified` is `None` when no public key is configured (see routes_
    deployment_bom.py's own `_verify`), never a stored, possibly-stale `True` -- read this
    same record twice and it is computed both times, not cached."""
    envelope, _key = _envelope()
    submitted = await client.post("/v1/deployment/bom", json=envelope, headers=ARTIZENT_HEADERS)
    artefact_id = submitted.json()["id"]
    first = await client.get(f"/v1/deployment/bom/{artefact_id}", headers=INFOSEC_HEADERS)
    second = await client.get(f"/v1/deployment/bom/{artefact_id}", headers=INFOSEC_HEADERS)
    assert first.json()["signature_verified"] is None
    assert second.json()["signature_verified"] is None
