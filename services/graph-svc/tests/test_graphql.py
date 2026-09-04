"""The GraphQL query API.

S1.1.2 criterion 1: "node lookup by id and luid, neighbourhood traversal to depth 5, and
the named contracts in §4.1.3".
"""

from __future__ import annotations

import pytest

from astra_graph.api.graphql.schema import build_schema
from astra_graph.ontology import EDGE_LABELS, NODE_LABELS, SCHEMA_VERSION

from .conftest import HEADERS


async def gql(client, query: str, variables: dict | None = None, headers: dict | None = None):
    response = await client.post(
        "/graphql",
        json={"query": query, "variables": variables or {}},
        headers=headers or HEADERS,
    )
    assert response.status_code == 200, response.text
    return response.json()


def errors_of(payload) -> list[str]:
    return [error["message"] for error in payload.get("errors") or []]


# ------------------------------------------------------------------ the schema


def test_schema_has_a_type_per_ontology_type() -> None:
    """The schema is generated from the ontology, so it cannot describe a shape the
    write path would reject."""
    text = str(build_schema())
    for label in NODE_LABELS:
        assert f"type {label} implements EstateNode" in text, label
    for label in EDGE_LABELS:
        assert f"type {label} implements EstateEdge" in text, label


def test_field_names_are_the_ontology_names() -> None:
    text = str(build_schema())
    assert "views_90d: Int" in text
    assert "distinct_viewers_90d: Int" in text
    # `class` is a Python keyword; the GraphQL field keeps the specification's name.
    assert "class: String" in text


def test_required_properties_are_non_null() -> None:
    text = str(build_schema())
    workbook = text.split("type Workbook implements EstateNode {")[1].split("}")[0]
    assert "luid: String!" in workbook
    assert "revision: String!" in workbook
    assert "size: Int\n" in workbook  # optional


def test_the_interfaces_do_not_declare_a_field_the_ontology_uses() -> None:
    """No interface field may share a name with an ontology property.

    Datasource, Filter, Action and Visual each declare a property called `type`. An
    interface field of the same name would be shadowed by the property, so
    `Datasource.type` would answer "published" where a caller asked what kind of node it
    was. The ontology type is read with GraphQL's `__typename` instead.
    """
    from astra_graph.api.graphql.types import EstateEdge, EstateNode
    from astra_graph.ontology import sorted_edge_types, sorted_node_types

    node_fields = {f.name for f in EstateNode.__strawberry_definition__.fields}
    edge_fields = {f.name for f in EstateEdge.__strawberry_definition__.fields}

    for declared in sorted_node_types():
        clash = node_fields & set(declared.declared_property_names)
        assert not clash, f"{declared.label} declares {clash}, which the interface also declares"
    for declared in sorted_edge_types():
        clash = edge_fields & set(declared.declared_property_names)
        assert not clash, f"{declared.label} declares {clash}, which the interface also declares"


async def test_a_property_named_type_is_the_property_not_the_node_type(client, seeded) -> None:
    """Regression: `Datasource.type` is the ontology property (embedded|published)."""
    payload = await gql(
        client,
        "query($id: ID!) { node(id: $id) { __typename ... on Datasource { type name } } }",
        {"id": seeded["datasource"]},
    )
    node = payload["data"]["node"]
    assert node["__typename"] == "Datasource"
    assert node["type"] == "published"


# ------------------------------------------------------------ lookups by id/luid


async def test_node_by_id(client, seeded) -> None:
    payload = await gql(
        client,
        "query($id: ID!) { node(id: $id) { id __typename side ... on Workbook { name luid revision } } }",
        {"id": seeded["workbook"]},
    )
    node = payload["data"]["node"]
    assert node["__typename"] == "Workbook"
    assert node["side"] == "source"
    assert node["name"] == "Daily VaR"


async def test_node_by_id_returns_null_when_absent(client) -> None:
    payload = await gql(
        client, "{ node(id: \"01HXZZZZZZZZZZZZZZZZZZZZZZ\") { id } }"
    )
    assert payload["data"]["node"] is None


async def test_node_by_luid(client, seeded) -> None:
    payload = await gql(
        client,
        'query { node_by_luid(type: "Workbook", luid: "8f3e-daily-var") '
        "{ id ... on Workbook { name } } }",
    )
    assert payload["data"]["node_by_luid"]["id"] == seeded["workbook"]


async def test_node_by_luid_on_a_type_without_luid_explains_itself(client) -> None:
    payload = await gql(client, '{ node_by_luid(type: "Measure", luid: "x") { id } }')
    message = errors_of(payload)[0]
    assert "does not carry a luid" in message
    assert "Workbook" in message  # names the types that do


async def test_node_by_luid_rejects_an_unknown_type(client) -> None:
    payload = await gql(client, '{ node_by_luid(type: "Sheetish", luid: "x") { id } }')
    assert "is not a node type" in errors_of(payload)[0]


async def test_nodes_by_id_preserves_order(client, seeded) -> None:
    ids = [seeded["field"], seeded["workbook"], seeded["worksheet"]]
    payload = await gql(
        client, "query($ids: [ID!]!) { nodes(ids: $ids) { id __typename } }", {"ids": ids}
    )
    assert [n["id"] for n in payload["data"]["nodes"]] == ids


# ------------------------------------------------------------- neighbourhood


async def test_neighbourhood_is_undirected(client, seeded) -> None:
    """A workbook's neighbours include the project that contains it.

    Traversal follows edges both ways: direction is a property of the edge type, not of
    the question being asked, and someone asking what a workbook touches wants the
    project it sits in as much as the sheets it holds.
    """
    payload = await gql(
        client,
        "query($id: ID!) { neighbourhood(id: $id, depth: 1) "
        "{ anchor { id } depth truncated nodes { depth node { id __typename } } edges { id __typename } } }",
        {"id": seeded["workbook"]},
    )
    result = payload["data"]["neighbourhood"]
    assert result["anchor"]["id"] == seeded["workbook"]
    reached = {n["node"]["id"] for n in result["nodes"]}
    assert reached == {seeded["worksheet"], seeded["dashboard"], seeded["project"]}
    assert all(n["depth"] == 1 for n in result["nodes"])
    assert result["truncated"] is False
    # Only the edges wholly inside the neighbourhood, not those leaving it.
    assert len(result["edges"]) == 3


async def test_neighbourhood_reaches_further_at_greater_depth(client, seeded) -> None:
    payload = await gql(
        client,
        "query($id: ID!) { neighbourhood(id: $id, depth: 3) { nodes { depth node { id __typename } } } }",
        {"id": seeded["workbook"]},
    )
    by_id = {n["node"]["id"]: n["depth"] for n in payload["data"]["neighbourhood"]["nodes"]}
    assert by_id[seeded["worksheet"]] == 1
    assert by_id[seeded["datasource"]] == 2
    assert by_id[seeded["field"]] == 3


async def test_neighbourhood_depth_is_the_shortest_path(client, seeded) -> None:
    """The calculated field is two hops via ENCODES and further via DEPENDS_ON."""
    payload = await gql(
        client,
        "query($id: ID!) { neighbourhood(id: $id, depth: 5) { nodes { depth node { id } } } }",
        {"id": seeded["workbook"]},
    )
    by_id = {n["node"]["id"]: n["depth"] for n in payload["data"]["neighbourhood"]["nodes"]}
    assert by_id[seeded["calc"]] == 2


@pytest.mark.parametrize("depth", [0, 6, -1])
async def test_neighbourhood_depth_is_bounded_at_five(client, seeded, depth) -> None:
    payload = await gql(
        client,
        "query($id: ID!, $d: Int!) { neighbourhood(id: $id, depth: $d) { depth } }",
        {"id": seeded["workbook"], "d": depth},
    )
    assert "depth must be between 1 and 5" in errors_of(payload)[0]


async def test_neighbourhood_filters_by_edge_type(client, seeded) -> None:
    payload = await gql(
        client,
        'query($id: ID!) { neighbourhood(id: $id, depth: 3, edge_types: ["CONTAINS"]) '
        "{ nodes { node { id __typename } } } }",
        {"id": seeded["workbook"]},
    )
    types = {n["node"]["__typename"] for n in payload["data"]["neighbourhood"]["nodes"]}
    # Containment only: the datasource is reached through USES_DATASOURCE and is absent,
    # while the project and site are reached by following CONTAINS upwards.
    assert types == {"Worksheet", "Dashboard", "Project", "Site"}


async def test_neighbourhood_filters_by_node_type(client, seeded) -> None:
    payload = await gql(
        client,
        'query($id: ID!) { neighbourhood(id: $id, depth: 3, node_types: ["Field"]) '
        "{ nodes { node { id __typename } } } }",
        {"id": seeded["workbook"]},
    )
    nodes = payload["data"]["neighbourhood"]["nodes"]
    assert [n["node"]["id"] for n in nodes] == [seeded["field"]]


async def test_neighbourhood_reports_truncation(client, seeded) -> None:
    payload = await gql(
        client,
        "query($id: ID!) { neighbourhood(id: $id, depth: 5, limit: 2) { truncated nodes { node { id } } } }",
        {"id": seeded["workbook"]},
    )
    assert payload["data"]["neighbourhood"]["truncated"] is True


async def test_neighbourhood_rejects_an_unknown_edge_type(client, seeded) -> None:
    payload = await gql(
        client,
        'query($id: ID!) { neighbourhood(id: $id, edge_types: ["CONTAINSS"]) { depth } }',
        {"id": seeded["workbook"]},
    )
    assert "is not an edge type" in errors_of(payload)[0]


async def test_neighbourhood_of_a_missing_node_is_an_error(client) -> None:
    payload = await gql(
        client, '{ neighbourhood(id: "01HXZZZZZZZZZZZZZZZZZZZZZZ") { depth } }'
    )
    assert "no node with id" in errors_of(payload)[0]


# ---------------------------------------------------------- context contracts


async def test_the_contract_query_returns_a_hashed_document_not_a_shape_to_select(
    client, seeded
) -> None:
    """S1.3.1 changed what this query returns, and the reason is worth stating.

    S1.1.2 returned typed nodes a caller could select fields from. A context now carries a
    ``context_hash`` over the whole canonical document, so letting a caller select part of
    it would hand back a hash that describes something other than the response.
    """
    payload = await gql(
        client,
        "query($id: ID!) { context_contract(name: TRANSPILER_CALC, subject_id: $id) "
        "{ name version context_hash document usage { size_bytes node_count budget_bytes } } }",
        {"id": seeded["calc"]},
    )
    contract = payload["data"]["context_contract"]

    assert contract["name"] == "transpiler_calc"
    assert contract["context_hash"].startswith("sha256:")
    assert contract["usage"]["size_bytes"] > 0
    assert contract["usage"]["size_bytes"] < contract["usage"]["budget_bytes"]

    document = contract["document"]
    assert document["subject"]["id"] == seeded["calc"]
    # §4.1.3: the transitive DEPENDS_ON closure, not just the direct dependencies.
    reached = {n["id"] for n in document["dependency_fields"]} | {
        n["id"] for n in document["dependency_calculations"]
    }
    assert seeded["field"] in reached
    assert seeded["nested_calc"] in reached
    assert [p["id"] for p in document["parameters"]] == [seeded["parameter"]]
    assert [t["id"] for t in document["model_tables"]] == [seeded["model_table"]]


async def test_transpiler_contract_refuses_a_subject_of_the_wrong_type(client, seeded) -> None:
    payload = await gql(
        client,
        "query($id: ID!) { context_contract(name: TRANSPILER_CALC, subject_id: $id) { name } }",
        {"id": seeded["workbook"]},
    )
    message = errors_of(payload)[0]
    assert "takes a CalculatedField" in message
    assert "Workbook" in message


# ----------------------------------------------------------------- plumbing


async def test_graphql_requires_a_principal(client) -> None:
    response = await client.post("/graphql", json={"query": "{ schema_version }"}, headers={})
    assert response.status_code == 400
    assert response.json()["detail"]["error"] == "invalid_principal"


async def test_schema_version_is_served(client) -> None:
    payload = await gql(client, "{ schema_version }")
    assert payload["data"]["schema_version"] == SCHEMA_VERSION
