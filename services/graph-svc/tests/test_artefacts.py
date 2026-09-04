"""The artefact store, and its HTTP surface — S2.4.2.

    "Images are stored in the artefact store and linked to the MU; they are never sent to a
    model endpoint."

Two things this story asks for, and two kinds of test: `InMemoryArtefactStore` and the API
routes prove storage and MU linkage; the last group holds the "never sent to a model
endpoint" line to account — not by mocking a model that isn't built yet, but by pinning what
a metadata record can and cannot say.
"""

from __future__ import annotations

import base64
import hashlib

import pytest

from astra_graph.artefacts import ArtefactError, InMemoryArtefactStore

from .conftest import ARTIZENT_HEADERS

PNG_MAGIC = b"\x89PNG\r\n\x1a\n"
IMAGE = PNG_MAGIC + b"fake-but-fine-for-a-content-hash-test"


@pytest.fixture
def store() -> InMemoryArtefactStore:
    return InMemoryArtefactStore()


# --------------------------------------------------------------------------- the store


async def test_storing_returns_no_bytes(store: InMemoryArtefactStore) -> None:
    """`ArtefactRecord` never carries the content — see the module's own reasoning. A
    caller that only ever sees a record, never the store, cannot leak an image by accident."""
    record = await store.store(
        kind="visual_capture",
        mu_ref="wb-00000",
        case_id="visual-1",
        content=IMAGE,
        media_type="image/png",
        width=1200,
        height=800,
        adapter_name="tableau",
        adapter_version="0.1.0",
        interface_version="1.1",
        created_by="agent:harvester",
    )

    assert not hasattr(record, "content")
    assert not hasattr(record, "image")
    assert "content" not in record.as_dict()
    assert "image" not in record.as_dict()


async def test_the_content_hash_is_a_real_sha256_of_the_bytes(store: InMemoryArtefactStore) -> None:
    record = await store.store(
        kind="visual_capture",
        mu_ref="wb-00000",
        case_id="visual-1",
        content=IMAGE,
        media_type="image/png",
        created_by="agent:harvester",
    )

    assert record.content_hash == hashlib.sha256(IMAGE).hexdigest()
    assert record.size_bytes == len(IMAGE)


async def test_the_bytes_are_retrievable_by_id(store: InMemoryArtefactStore) -> None:
    """The one method that returns content — `content()`, not `get()`. Only the console's
    own image route calls it."""
    record = await store.store(
        kind="visual_capture", mu_ref="wb-00000", case_id="v1", content=IMAGE,
        media_type="image/png", created_by="agent:harvester",
    )

    assert await store.content(record.id) == IMAGE
    assert await store.get(record.id) == record


async def test_an_artefact_with_no_bytes_is_refused(store: InMemoryArtefactStore) -> None:
    with pytest.raises(ArtefactError):
        await store.store(
            kind="visual_capture", mu_ref="wb-00000", case_id="v1", content=b"",
            media_type="image/png", created_by="agent:harvester",
        )


async def test_artefacts_are_linked_to_the_mu(store: InMemoryArtefactStore) -> None:
    """S2.4.2's own words: "linked to the MU". `mu_ref` is the workbook LUID until E3 mints
    real Migration Unit ids — see the module docstring for why that stand-in is the right
    one rather than an arbitrary placeholder."""
    for index in range(3):
        await store.store(
            kind="visual_capture", mu_ref="wb-00000", case_id=f"v{index}", content=IMAGE,
            media_type="image/png", created_by="agent:harvester",
        )
    await store.store(
        kind="visual_capture", mu_ref="wb-00001", case_id="v0", content=IMAGE,
        media_type="image/png", created_by="agent:harvester",
    )

    for_this_mu = await store.for_mu("wb-00000")

    assert len(for_this_mu) == 3
    assert {record.mu_ref for record in for_this_mu} == {"wb-00000"}


async def test_listing_by_mu_can_be_narrowed_by_kind(store: InMemoryArtefactStore) -> None:
    await store.store(
        kind="visual_capture", mu_ref="wb-00000", case_id="v0", content=IMAGE,
        media_type="image/png", created_by="agent:harvester",
    )
    await store.store(
        kind="evidence_bundle", mu_ref="wb-00000", case_id="v0", content=IMAGE,
        media_type="application/json", created_by="agent:harvester",
    )

    only_visuals = await store.for_mu("wb-00000", kind="visual_capture")

    assert [record.kind for record in only_visuals] == ["visual_capture"]


# ----------------------------------------------------------------------------- the API


async def test_an_artefact_is_stored_and_linked_through_the_api(client) -> None:
    response = await client.post(
        "/v1/artefacts",
        json={
            "kind": "visual_capture",
            "mu_ref": "wb-00000",
            "case_id": "visual-1",
            "media_type": "image/png",
            "content_base64": base64.b64encode(IMAGE).decode(),
            "width": 800,
            "height": 600,
            "adapter_name": "tableau",
            "adapter_version": "0.1.0",
            "interface_version": "1.1",
        },
        headers=ARTIZENT_HEADERS,
    )
    assert response.status_code == 201
    body = response.json()
    assert body["mu_ref"] == "wb-00000"
    assert body["content_hash"] == hashlib.sha256(IMAGE).hexdigest()
    assert body["size_bytes"] == len(IMAGE)
    # The bytes never round-trip through this response — see the module docstring's "never
    # sent to a model endpoint" reasoning.
    assert "content_base64" not in body
    assert "image" not in body
    assert "content" not in body


async def test_metadata_never_carries_the_bytes(client) -> None:
    """The structural half of "never sent to a model endpoint": the shape a future context
    contract could plausibly reference is metadata, and metadata has nowhere to put a
    picture."""
    stored = await client.post(
        "/v1/artefacts",
        json={
            "kind": "visual_capture",
            "mu_ref": "wb-00000",
            "media_type": "image/png",
            "content_base64": base64.b64encode(IMAGE).decode(),
        },
        headers=ARTIZENT_HEADERS,
    )
    artefact_id = stored.json()["id"]

    response = await client.get(f"/v1/artefacts/{artefact_id}", headers=ARTIZENT_HEADERS)

    assert response.status_code == 200
    keys = set(response.json().keys())
    assert keys.isdisjoint({"content", "content_base64", "image", "bytes"})


async def test_only_the_content_route_returns_bytes(client) -> None:
    stored = await client.post(
        "/v1/artefacts",
        json={
            "kind": "visual_capture",
            "mu_ref": "wb-00000",
            "media_type": "image/png",
            "content_base64": base64.b64encode(IMAGE).decode(),
        },
        headers=ARTIZENT_HEADERS,
    )
    artefact_id = stored.json()["id"]

    response = await client.get(f"/v1/artefacts/{artefact_id}/content", headers=ARTIZENT_HEADERS)

    assert response.status_code == 200
    assert response.headers["content-type"] == "image/png"
    assert response.content == IMAGE


async def test_a_missing_artefact_is_a_404(client) -> None:
    response = await client.get("/v1/artefacts/af_does-not-exist", headers=ARTIZENT_HEADERS)
    assert response.status_code == 404

    content_response = await client.get(
        "/v1/artefacts/af_does-not-exist/content", headers=ARTIZENT_HEADERS
    )
    assert content_response.status_code == 404


async def test_invalid_base64_is_refused_not_500d(client) -> None:
    response = await client.post(
        "/v1/artefacts",
        json={
            "kind": "visual_capture",
            "mu_ref": "wb-00000",
            "media_type": "image/png",
            "content_base64": "not valid base64!!",
        },
        headers=ARTIZENT_HEADERS,
    )
    assert response.status_code == 400


async def test_listing_artefacts_for_an_mu(client) -> None:
    for case_id in ("v0", "v1"):
        await client.post(
            "/v1/artefacts",
            json={
                "kind": "visual_capture",
                "mu_ref": "wb-00042",
                "case_id": case_id,
                "media_type": "image/png",
                "content_base64": base64.b64encode(IMAGE).decode(),
            },
            headers=ARTIZENT_HEADERS,
        )

    response = await client.get(
        "/v1/artefacts", params={"mu_ref": "wb-00042"}, headers=ARTIZENT_HEADERS
    )

    assert response.status_code == 200
    body = response.json()
    assert body["mu_ref"] == "wb-00042"
    assert {a["case_id"] for a in body["artefacts"]} == {"v0", "v1"}
