"""The HTTP contract.

S1.1.1 criterion 1: rejection is "a 422 and a message naming the property".
"""

from __future__ import annotations

from typing import Any

import pytest

from astra_graph.ids import new_ulid
from astra_graph.principal import PRINCIPAL_HEADER, RUN_HEADER

from .conftest import HEADERS, PRINCIPAL


async def create(client, body: dict[str, Any], *, headers: dict[str, str] | None = None):
    return await client.post("/v1/nodes", json=body, headers=headers or HEADERS)


# ------------------------------------------------------------------------ accepted


async def test_valid_node_is_created(client, valid_workbook) -> None:
    response = await create(client, valid_workbook)
    assert response.status_code == 201
    body = response.json()
    assert body["type"] == "Workbook"
    properties = body["properties"]
    assert properties["name"] == "Daily VaR"
    assert properties["side"] == "source"
    assert properties["created_by"] == PRINCIPAL
    assert properties["created_at"].endswith("Z")
    assert len(properties["id"]) == 26


async def test_run_id_is_recorded_when_supplied(client, valid_workbook) -> None:
    response = await create(
        client, valid_workbook, headers={**HEADERS, RUN_HEADER: "run-01HX7"}
    )
    assert response.json()["properties"]["created_in_run"] == "run-01HX7"


async def test_caller_supplied_id_is_kept_so_reharvest_is_idempotent(client, valid_workbook) -> None:
    supplied = new_ulid()
    response = await create(client, {**valid_workbook, "id": supplied})
    assert response.json()["properties"]["id"] == supplied

    read_back = await client.get(f"/v1/nodes/{supplied}")
    assert read_back.status_code == 200
    assert read_back.json()["properties"]["name"] == "Daily VaR"


async def test_duplicate_id_is_a_conflict(client, valid_workbook) -> None:
    supplied = new_ulid()
    assert (await create(client, {**valid_workbook, "id": supplied})).status_code == 201
    conflict = await create(client, {**valid_workbook, "id": supplied})
    assert conflict.status_code == 409
    assert conflict.json()["error"] == "duplicate"


# ------------------------------------------------------------------------ rejected


async def test_unknown_node_type_is_422_and_says_so(client) -> None:
    response = await create(client, {"type": "Sheetish", "properties": {}})
    assert response.status_code == 422
    body = response.json()
    assert body["error"] == "ontology_violation"
    assert body["violations"][0]["code"] == "unknown_node_type"


async def test_missing_required_property_is_422_and_names_the_property(client) -> None:
    response = await create(client, {"type": "Workbook", "properties": {"luid": "abc"}})
    assert response.status_code == 422
    body = response.json()
    named = {violation["property"] for violation in body["violations"]}
    assert named == {"name", "revision"}
    for violation in body["violations"]:
        assert violation["code"] == "missing_required_property"
        assert violation["property"] in violation["message"]
    assert "'name'" in body["message"] and "'revision'" in body["message"]


async def test_undeclared_property_is_422(client, valid_workbook) -> None:
    body = {**valid_workbook, "properties": {**valid_workbook["properties"], "colour": "blue"}}
    response = await create(client, body)
    assert response.status_code == 422
    assert response.json()["violations"][0]["property"] == "colour"


async def test_rejected_write_stores_nothing(client, repository) -> None:
    await create(client, {"type": "Workbook", "properties": {"luid": "abc"}})
    assert repository.nodes == {}


async def test_principal_header_is_required(client, valid_workbook) -> None:
    response = await client.post("/v1/nodes", json=valid_workbook, headers={})
    assert response.status_code == 400
    assert response.json()["detail"]["error"] == "invalid_principal"


async def test_malformed_principal_is_rejected(client, valid_workbook) -> None:
    response = await create(client, valid_workbook, headers={PRINCIPAL_HEADER: "harvester"})
    assert response.status_code == 400


# --------------------------------------------------------------------------- batch


async def test_batch_is_atomic_and_reports_the_offending_index(client, repository) -> None:
    response = await client.post(
        "/v1/nodes:batch",
        json={
            "nodes": [
                {"type": "Site", "properties": {"luid": "s1", "name": "RQA"}},
                {"type": "Site", "properties": {"luid": "s2"}},  # missing name
                {"type": "Site", "properties": {"luid": "s3", "name": "GTAA"}},
            ]
        },
        headers=HEADERS,
    )
    assert response.status_code == 422
    violations = response.json()["violations"]
    assert [v["index"] for v in violations] == [1]
    assert violations[0]["property"] == "name"
    assert repository.nodes == {}, "a rejected batch must leave nothing behind"


async def test_valid_batch_is_written(client, repository) -> None:
    response = await client.post(
        "/v1/nodes:batch",
        json={
            "nodes": [
                {"type": "Site", "properties": {"luid": "s1", "name": "RQA"}},
                {"type": "Project", "properties": {"luid": "p1", "name": "Risk Core"}},
            ]
        },
        headers=HEADERS,
    )
    assert response.status_code == 201
    assert len(response.json()["nodes"]) == 2
    assert len(repository.nodes) == 2


# --------------------------------------------------------------------------- edges


async def _node(client, body: dict[str, Any]) -> str:
    response = await create(client, body)
    assert response.status_code == 201, response.text
    return response.json()["properties"]["id"]


async def test_edge_between_permitted_endpoints_is_created(client, valid_site) -> None:
    site_id = await _node(client, valid_site)
    project_id = await _node(
        client, {"type": "Project", "properties": {"luid": "p1", "name": "Risk Core"}}
    )
    response = await client.post(
        "/v1/edges",
        json={"type": "CONTAINS", "from_id": site_id, "to_id": project_id, "properties": {}},
        headers=HEADERS,
    )
    assert response.status_code == 201
    assert response.json()["properties"]["written_by"] == PRINCIPAL


async def test_edge_between_forbidden_endpoints_is_422(client, valid_site) -> None:
    site_id = await _node(client, valid_site)
    datasource_id = await _node(
        client,
        {"type": "Datasource", "properties": {"name": "Positions", "type": "published"}},
    )
    response = await client.post(
        "/v1/edges",
        json={
            "type": "USES_DATASOURCE",
            "from_id": site_id,
            "to_id": datasource_id,
            "properties": {},
        },
        headers=HEADERS,
    )
    assert response.status_code == 422
    assert response.json()["violations"][0]["code"] == "invalid_edge_endpoints"


async def test_edge_to_a_missing_node_is_422(client, valid_site) -> None:
    site_id = await _node(client, valid_site)
    absent = new_ulid()
    response = await client.post(
        "/v1/edges",
        json={"type": "CONTAINS", "from_id": site_id, "to_id": absent, "properties": {}},
        headers=HEADERS,
    )
    assert response.status_code == 422
    violation = response.json()["violations"][0]
    assert violation["code"] == "unknown_endpoint_node"
    assert violation["property"] == "to_id"


async def test_edge_properties_are_validated(client, valid_workbook) -> None:
    workbook_id = await _node(client, valid_workbook)
    worksheet_id = await _node(
        client,
        {
            "type": "Worksheet",
            "properties": {
                "name": "VaR by Desk",
                "rows_shelf": ["Desk"],
                "cols_shelf": ["Date"],
                "marks_shelf": [],
            },
        },
    )
    field_id = await _node(
        client,
        {"type": "Field", "properties": {"name": "VaR", "datatype": "real", "role": "measure"}},
    )
    assert (
        await client.post(
            "/v1/edges",
            json={
                "type": "CONTAINS",
                "from_id": workbook_id,
                "to_id": worksheet_id,
                "properties": {},
            },
            headers=HEADERS,
        )
    ).status_code == 201

    missing_shelf = await client.post(
        "/v1/edges",
        json={"type": "ENCODES", "from_id": worksheet_id, "to_id": field_id, "properties": {}},
        headers=HEADERS,
    )
    assert missing_shelf.status_code == 422
    assert missing_shelf.json()["violations"][0]["property"] == "shelf"


# ------------------------------------------------------------------------ ontology


async def test_ontology_is_served_as_data(client) -> None:
    response = await client.get("/v1/ontology")
    assert response.status_code == 200
    body = response.json()
    assert len(body["nodes"]) == 28
    assert len(body["edges"]) == 15
    workbook = next(n for n in body["nodes"] if n["label"] == "Workbook")
    assert {"id", "side", "created_by", "created_at"} <= {
        p["name"] for p in workbook["properties"]
    }


async def test_ontology_is_served_as_markdown(client) -> None:
    response = await client.get("/v1/ontology.md")
    assert response.status_code == 200
    assert response.text.lstrip().startswith("<!-- Generated from")


async def test_unknown_node_read_is_404(client) -> None:
    response = await client.get(f"/v1/nodes/{new_ulid()}")
    assert response.status_code == 404
    assert response.json()["error"] == "not_found"


@pytest.mark.parametrize("path", ["/v1/nodes/short", "/v1/edges/short"])
async def test_malformed_id_is_rejected_before_the_store(client, path) -> None:
    assert (await client.get(path)).status_code == 422
