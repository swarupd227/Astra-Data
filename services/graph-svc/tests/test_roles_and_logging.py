"""Role gating and query logging.

S1.1.2: the Cypher endpoint is "available to Artizent roles", and "every query is logged
with principal and duration".
"""

from __future__ import annotations

import json
import logging

import pytest

from astra_graph.logging_setup import JsonFormatter
from astra_graph.observability import QueryLog
from astra_graph.roles import (
    ARTIZENT_ROLES,
    ORGANISATION_OF,
    ROLES_HEADER,
    InvalidRolesError,
    Organisation,
    Role,
    parse,
)

from .conftest import ARTIZENT_HEADERS, CLIENT_HEADERS, HEADERS

# ---------------------------------------------------------------------- roles


def test_every_role_in_the_specification_has_an_organisation() -> None:
    """Spec §2.4 gives an Organisation for each of the eleven roles."""
    assert set(ORGANISATION_OF) == set(Role)
    assert len(Role) == 11


def test_artizent_roles_are_the_delivery_side() -> None:
    assert {role.value for role in ARTIZENT_ROLES} == {
        "programme_manager",
        "migration_architect",
        "semantic_model_engineer",
        "migration_engineer",
        "parity_engineer",
        "platform_engineer",
    }
    for role in Role:
        if role.value.startswith("client_"):
            assert ORGANISATION_OF[role] is Organisation.CLIENT


def test_parsing_roles() -> None:
    parsed = parse("migration_engineer, parity_engineer")
    assert parsed.roles == {Role.MIGRATION_ENGINEER, Role.PARITY_ENGINEER}
    assert parsed.is_artizent()


def test_an_absent_header_is_an_empty_role_set_not_an_error() -> None:
    assert parse(None).roles == frozenset()
    assert not parse(None).is_artizent()


def test_an_unknown_role_is_rejected_and_lists_the_known_ones() -> None:
    with pytest.raises(InvalidRolesError) as caught:
        parse("chief_migrator")
    assert "unknown role" in str(caught.value)
    assert "parity_engineer" in str(caught.value)


def test_a_malformed_role_is_rejected() -> None:
    with pytest.raises(InvalidRolesError, match="malformed"):
        parse("Migration Engineer!")


# ------------------------------------------------------ gating the endpoint


async def test_cypher_requires_a_role(client) -> None:
    response = await client.post(
        "/v1/cypher", json={"query": "MATCH (n:Site) RETURN n"}, headers=HEADERS
    )
    assert response.status_code == 403
    assert response.json()["error"] == "forbidden"


async def test_cypher_is_closed_to_client_roles(client) -> None:
    response = await client.post(
        "/v1/cypher", json={"query": "MATCH (n:Site) RETURN n"}, headers=CLIENT_HEADERS
    )
    assert response.status_code == 403
    assert ROLES_HEADER in response.json()["message"]


async def test_cypher_rejects_an_unknown_role(client) -> None:
    response = await client.post(
        "/v1/cypher",
        json={"query": "MATCH (n:Site) RETURN n"},
        headers={**HEADERS, ROLES_HEADER: "chief_migrator"},
    )
    assert response.status_code == 400
    assert response.json()["detail"]["error"] == "invalid_roles"


async def test_cypher_guard_runs_before_the_store(client) -> None:
    """An Artizent caller with a write query is refused by the guard, not the database.

    The in-memory store raises NotImplementedError if a query ever reaches it, so this
    passing proves the rejection happened earlier.
    """
    response = await client.post(
        "/v1/cypher",
        json={"query": "CREATE (n:Site {name: 'x'}) RETURN n"},
        headers=ARTIZENT_HEADERS,
    )
    assert response.status_code == 400
    assert "read-only" in response.json()["message"]
    assert "CREATE" in response.json()["message"]


async def test_cypher_reports_its_own_limits(client) -> None:
    """The limits are on the response, so a caller does not have to read the docs to
    know whether a result was cut."""
    from astra_graph.api.routes_cypher import ROW_LIMIT, TIMEOUT_SECONDS

    assert (TIMEOUT_SECONDS, ROW_LIMIT) == (30, 10_000)


# ------------------------------------------------------------------- logging


def test_query_log_records_principal_and_duration(caplog) -> None:
    with caplog.at_level(logging.INFO, logger="astra_graph.query"):
        log = QueryLog(surface="graphql", principal="agent:harvester", roles="platform_engineer")
        log.add(operations=["neighbourhood"], elements=42)
        log.finish()

    record = caplog.records[-1]
    context = record.context
    assert context["principal"] == "agent:harvester"
    assert context["surface"] == "graphql"
    assert context["roles"] == "platform_engineer"
    assert context["elements"] == 42
    assert isinstance(context["duration_ms"], float)
    assert context["outcome"] == "ok"


def test_query_log_records_a_failure_outcome(caplog) -> None:
    with (
        caplog.at_level(logging.INFO, logger="astra_graph.query"),
        pytest.raises(ValueError),
        QueryLog(surface="cypher", principal="user:a@b.example"),
    ):
        raise ValueError("boom")
    assert caplog.records[-1].context["outcome"] == "ValueError"


def test_query_log_serialises_as_json(caplog) -> None:
    with caplog.at_level(logging.INFO, logger="astra_graph.query"):
        QueryLog(surface="cypher", principal="user:a@b.example", run_id="run-1").finish()
    payload = json.loads(JsonFormatter().format(caplog.records[-1]))
    assert payload["principal"] == "user:a@b.example"
    assert payload["run_id"] == "run-1"
    assert "duration_ms" in payload


async def test_a_graphql_request_writes_one_log_line(client, seeded, caplog) -> None:
    with caplog.at_level(logging.INFO, logger="astra_graph.query"):
        await client.post(
            "/graphql",
            json={
                "query": "query($id: ID!) { neighbourhood(id: $id, depth: 2) { depth } }",
                "variables": {"id": seeded["workbook"]},
            },
            headers=ARTIZENT_HEADERS,
        )
    lines = [r for r in caplog.records if r.name == "astra_graph.query"]
    assert len(lines) == 1
    context = lines[0].context
    assert context["surface"] == "graphql"
    assert context["principal"] == "agent:harvester"
    assert context["operations"] == ["neighbourhood"]
    assert context["duration_ms"] > 0


async def test_a_rejected_cypher_query_is_still_logged(client, caplog) -> None:
    with caplog.at_level(logging.INFO, logger="astra_graph.query"):
        await client.post(
            "/v1/cypher",
            json={"query": "MATCH (n:Site) DELETE n RETURN 1 AS ok"},
            headers=ARTIZENT_HEADERS,
        )
    context = [r for r in caplog.records if r.name == "astra_graph.query"][-1].context
    assert context["outcome"] == "rejected"
    assert context["rejected"] == "write_clause"
    # The raw endpoint records what was asked, so an auditor can see what was attempted.
    assert "DELETE" in context["query"]
