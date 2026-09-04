"""Mutation events, retirement, and replay.

S1.1.3, all three criteria:

* mutations emitted as CloudEvents with the run id;
* replaying the stream from empty reproduces the graph exactly;
* hard deletes are impossible, and retirement keeps the node.
"""

from __future__ import annotations

import pytest

from astra_graph.events import EventType
from astra_graph.ids import new_ulid
from astra_graph.principal import Principal
from astra_graph.replay import ReplayError, compare, replay
from astra_graph.writes import EdgeWrite, GraphWriter, NodeWrite

from .conftest import HEADERS, seed_estate
from .fakes import InMemoryGraphRepository

PRINCIPAL = Principal("agent:harvester", run_id="run-01HX7")


# ------------------------------------------------------------------ CloudEvents


def test_the_mutation_event_types_are_the_ones_the_story_names() -> None:
    """S1.1.3 names three; S3.1.2 adds a fourth, ``EDGE_RETIRED``, and teaches ``replay.py``
    about it in the same story (an edge's endpoints cannot change once created, so 'move'
    retires the old edge and creates a new one).

    Asserted on ``mutates_graph`` rather than on the enum as a whole, because the outbox
    also carries notices (S1.2.4). A mutation type added without teaching replay about it
    would silently stop being replayable, which is what this guards — the count is pinned
    precisely so an addition is forced to touch this test and, by reading its docstring,
    replay.py too.
    """
    assert {e.value for e in EventType if e.mutates_graph} == {
        "estate.node.upserted",
        "estate.edge.upserted",
        "estate.node.retired",
        "estate.edge.retired",
    }


def test_a_notice_is_not_replayed_and_cannot_be_written_on_its_own_by_mistake() -> None:
    """The two halves of the notice/mutation split, stated together."""
    assert not EventType.SOURCE_DRIFT.mutates_graph
    assert {e.value for e in EventType if not e.mutates_graph} == {"estate.source.drift"}


async def test_the_repository_refuses_a_mutation_appended_outside_its_transaction(
    repository,
) -> None:
    """The rule S1.1.3 rests on, enforced rather than assumed."""
    from astra_graph.events import node_upserted

    event = node_upserted(
        source="/astra/graph-svc/test",
        label="Workbook",
        properties={"id": "01ARZ3NDEKTSV4RRFFQ69G5FAV", "luid": "x", "name": "y"},
        principal=Principal("user:someone@artizent.example"),
    )

    with pytest.raises(ValueError, match="must be committed with the mutation"):
        await repository.append_event(event)


async def test_every_mutation_leaves_an_event(repository, writer) -> None:
    seeded = await seed_estate(writer)
    events = await repository.read_events(limit=1000)

    nodes = [e for e in events if e.type is EventType.NODE_UPSERTED]
    edges = [e for e in events if e.type is EventType.EDGE_UPSERTED]
    assert len(nodes) == 11
    assert len(edges) == 11
    assert {e.subject for e in nodes} == {
        v for k, v in seeded.items() if not k.endswith("_luid")
    }


async def test_events_carry_the_principal_and_run_id(repository, writer) -> None:
    """S1.1.3: 'recorded with who made it and from which run'."""
    await writer.write_nodes(
        [NodeWrite(type="Site", properties={"luid": "rqa", "name": "RQA"})],
        principal=PRINCIPAL,
    )
    event = (await repository.read_events())[0]
    assert event.principal == "agent:harvester"
    assert event.run_id == "run-01HX7"


async def test_an_event_is_a_cloudevent(repository, writer) -> None:
    await writer.write_nodes(
        [NodeWrite(type="Site", properties={"luid": "rqa", "name": "RQA"})],
        principal=PRINCIPAL,
    )
    envelope = (await repository.read_events())[0].to_cloudevent()

    assert envelope["specversion"] == "1.0"
    assert envelope["type"] == "estate.node.upserted"
    assert envelope["datacontenttype"] == "application/json"
    assert envelope["source"].startswith("/astra/graph-svc")
    assert len(envelope["id"]) == 26
    assert envelope["time"].endswith("Z")
    # Extension attributes: lowercase alphanumeric, as CloudEvents requires.
    assert envelope["runid"] == "run-01HX7"
    assert envelope["principal"] == "agent:harvester"
    assert envelope["sequence"] == "1"
    assert envelope["subject"] == envelope["data"]["properties"]["id"]


async def test_an_event_carries_the_whole_property_set_not_a_patch(repository, writer) -> None:
    """Replay applies an event without prior state, so the event must be self-contained."""
    created = await writer.write_nodes(
        [
            NodeWrite(
                type="Workbook",
                properties={"luid": "wb", "name": "Daily VaR", "revision": "14"},
            )
        ],
        principal=PRINCIPAL,
    )
    data = (await repository.read_events())[0].data
    assert data["type"] == "Workbook"
    assert data["properties"] == created[0]["properties"]
    assert data["properties"]["created_by"] == "agent:harvester"


async def test_an_edge_event_records_its_endpoints(repository, writer) -> None:
    seeded = await seed_estate(writer)
    events = await repository.read_events(limit=1000)
    contains = next(
        e
        for e in events
        if e.type is EventType.EDGE_UPSERTED
        and e.data["from_id"] == seeded["site"]
    )
    assert contains.data["to_id"] == seeded["project"]
    assert contains.label == "CONTAINS"


async def test_a_rejected_write_leaves_no_event(repository, writer) -> None:
    from astra_graph.errors import OntologyViolationError

    with pytest.raises(OntologyViolationError):
        await writer.write_nodes(
            [NodeWrite(type="Workbook", properties={"luid": "only"})], principal=PRINCIPAL
        )
    assert await repository.read_events() == []


# --------------------------------------------------------------------- upsert


async def test_upsert_replaces_the_property_set(repository, writer) -> None:
    node_id = new_ulid()
    await writer.upsert_nodes(
        [
            NodeWrite(
                type="Workbook",
                id=node_id,
                properties={"luid": "wb", "name": "Daily VaR", "revision": "14", "size": 10},
            )
        ],
        principal=PRINCIPAL,
    )
    await writer.upsert_nodes(
        [
            NodeWrite(
                type="Workbook",
                id=node_id,
                properties={"luid": "wb", "name": "Daily VaR v2", "revision": "15"},
            )
        ],
        principal=PRINCIPAL,
    )
    record = await repository.get_node_record(node_id)
    assert record.properties["name"] == "Daily VaR v2"
    assert record.properties["revision"] == "15"
    # Replaced, not merged: a property the writer dropped must not survive.
    assert "size" not in record.properties
    assert len(await repository.read_events()) == 2


async def test_upsert_cannot_change_a_nodes_type(repository, writer) -> None:
    from astra_graph.errors import InvalidRequestError

    node_id = new_ulid()
    await writer.upsert_nodes(
        [NodeWrite(type="Site", id=node_id, properties={"luid": "rqa", "name": "RQA"})],
        principal=PRINCIPAL,
    )
    with pytest.raises(InvalidRequestError, match="cannot change"):
        await writer.upsert_nodes(
            [
                NodeWrite(
                    type="Project", id=node_id, properties={"luid": "p", "name": "Risk"}
                )
            ],
            principal=PRINCIPAL,
        )


async def test_upsert_without_an_id_is_refused(writer) -> None:
    from astra_graph.errors import InvalidRequestError

    with pytest.raises(InvalidRequestError, match="needs the id"):
        await writer.upsert_nodes(
            [NodeWrite(type="Site", properties={"luid": "rqa", "name": "RQA"})],
            principal=PRINCIPAL,
        )


# ----------------------------------------------------------------- retirement


async def test_retirement_keeps_the_node(repository, writer, seeded) -> None:
    retired = await writer.retire_node(
        seeded["dashboard"], reason="Superseded by the risk overview page", principal=PRINCIPAL
    )
    assert retired["properties"]["retired_at"].endswith("Z")
    assert retired["properties"]["retired_by"] == "agent:harvester"
    assert retired["properties"]["retirement_reason"] == "Superseded by the risk overview page"

    # Still there. Nothing is deleted.
    still_present = await repository.get_node_record(seeded["dashboard"])
    assert still_present is not None
    assert still_present.properties["name"] == "Risk Overview"


async def test_retirement_emits_its_event(repository, writer, seeded) -> None:
    await writer.retire_node(seeded["dashboard"], reason="No longer used", principal=PRINCIPAL)
    event = (await repository.read_events(limit=1000))[-1]
    assert event.type is EventType.NODE_RETIRED
    assert event.subject == seeded["dashboard"]
    assert event.data["retirement_reason"] == "No longer used"
    assert event.data["retired_by"] == "agent:harvester"
    assert event.run_id == "run-01HX7"


async def test_a_retirement_needs_a_reason(writer, seeded) -> None:
    from astra_graph.errors import InvalidRequestError

    with pytest.raises(InvalidRequestError, match="needs a reason"):
        await writer.retire_node(seeded["dashboard"], reason="  x  ", principal=PRINCIPAL)


async def test_retiring_twice_is_refused(writer, seeded) -> None:
    from astra_graph.errors import InvalidRequestError

    await writer.retire_node(seeded["dashboard"], reason="No longer used", principal=PRINCIPAL)
    with pytest.raises(InvalidRequestError, match="already retired"):
        await writer.retire_node(
            seeded["dashboard"], reason="No longer used", principal=PRINCIPAL
        )


async def test_retiring_something_absent_is_a_404(writer) -> None:
    from astra_graph.errors import ElementNotFoundError

    with pytest.raises(ElementNotFoundError):
        await writer.retire_node(new_ulid(), reason="Never existed", principal=PRINCIPAL)


async def test_a_retired_node_drops_out_of_traversals(repository, writer, seeded) -> None:
    before = await repository.neighbourhood(seeded["workbook"], depth=1)
    assert seeded["dashboard"] in {n.node.id for n in before.neighbours}

    await writer.retire_node(seeded["dashboard"], reason="No longer used", principal=PRINCIPAL)

    after = await repository.neighbourhood(seeded["workbook"], depth=1)
    assert seeded["dashboard"] not in {n.node.id for n in after.neighbours}

    including = await repository.neighbourhood(
        seeded["workbook"], depth=1, include_retired=True
    )
    assert seeded["dashboard"] in {n.node.id for n in including.neighbours}


# --------------------------------------------------------------------- replay


async def _replay_into_fresh(source) -> InMemoryGraphRepository:
    target = InMemoryGraphRepository()
    await replay(source, target)
    return target


async def test_replay_from_empty_reproduces_the_graph(repository, writer) -> None:
    """S1.1.3 criterion 2, against the in-memory store."""
    await seed_estate(writer)
    target = await _replay_into_fresh(repository)

    result = compare(await repository.dump(), await target.dump())
    assert result.identical, [d.detail for d in result.differences]
    assert result.live_nodes == 11
    assert result.live_edges == 11


async def test_replay_reproduces_retirement(repository, writer, seeded) -> None:
    await writer.retire_node(
        seeded["dashboard"], reason="Superseded by the overview", principal=PRINCIPAL
    )
    target = await _replay_into_fresh(repository)

    result = compare(await repository.dump(), await target.dump())
    assert result.identical, [d.detail for d in result.differences]
    replayed = (await target.dump())["nodes"][seeded["dashboard"]]
    assert replayed["properties"]["retirement_reason"] == "Superseded by the overview"


async def test_replay_reproduces_an_upsert_history(repository, writer) -> None:
    """The final state, not an accumulation of every version."""
    node_id = new_ulid()
    for revision in ("14", "15", "16"):
        await writer.upsert_nodes(
            [
                NodeWrite(
                    type="Workbook",
                    id=node_id,
                    properties={"luid": "wb", "name": "Daily VaR", "revision": revision},
                )
            ],
            principal=PRINCIPAL,
        )
    target = await _replay_into_fresh(repository)
    result = compare(await repository.dump(), await target.dump())
    assert result.identical
    assert (await target.dump())["nodes"][node_id]["properties"]["revision"] == "16"


async def test_replay_is_idempotent(repository, writer) -> None:
    """Replaying twice into the same target changes nothing the second time."""
    await seed_estate(writer)
    target = InMemoryGraphRepository()
    await replay(repository, target)
    once = await target.dump()
    await replay(repository, target)
    assert compare(once, await target.dump()).identical


async def test_comparison_detects_a_missing_element(repository, writer) -> None:
    await seed_estate(writer)
    target = await _replay_into_fresh(repository)
    live = await repository.dump()
    replayed = await target.dump()
    removed = next(iter(replayed["nodes"]))
    del replayed["nodes"][removed]

    result = compare(live, replayed)
    assert not result.identical
    assert any(d.element_id == removed for d in result.differences)


async def test_comparison_detects_a_changed_property(repository, writer) -> None:
    await seed_estate(writer)
    target = await _replay_into_fresh(repository)
    live = await repository.dump()
    replayed = await target.dump()
    changed = next(iter(replayed["nodes"]))
    replayed["nodes"][changed]["properties"]["name"] = "something else"

    result = compare(live, replayed)
    assert not result.identical
    assert "property 'name'" in result.differences[0].detail


async def test_replay_refuses_a_stream_with_a_dangling_edge(repository, writer) -> None:
    """If the record is not self-consistent, replay says so rather than papering over it."""
    seeded = await seed_estate(writer)
    stream = await repository.read_events(limit=1000)
    # Drop the node events, keeping the edges that referenced them.
    repository.events = [e for e in stream if e.type is not EventType.NODE_UPSERTED]
    for index, event in enumerate(repository.events, start=1):
        object.__setattr__(event, "sequence", index)

    with pytest.raises(ReplayError, match="endpoint"):
        await _replay_into_fresh(repository)
    assert seeded["site"]


# -------------------------------------------------------------------- the API


async def test_no_delete_route_exists(client) -> None:
    """S1.1.3 criterion 3: 'Hard deletes are not possible through the API'.

    Asserted against the router rather than by trying one URL, so a delete added
    anywhere on the service fails this.
    """
    routes = [
        (method, route.path)
        for route in client._transport.app.routes
        for method in getattr(route, "methods", set())
    ]
    assert not [r for r in routes if r[0] == "DELETE"], routes


async def test_retire_over_http(client, seeded) -> None:
    response = await client.post(
        f"/v1/nodes/{seeded['dashboard']}:retire",
        json={"reason": "Superseded by the risk overview page"},
        headers=HEADERS,
    )
    assert response.status_code == 200
    properties = response.json()["properties"]
    assert properties["retired_by"] == "agent:harvester"
    assert properties["retirement_reason"] == "Superseded by the risk overview page"


async def test_retire_over_http_without_a_reason_is_rejected(client, seeded) -> None:
    response = await client.post(
        f"/v1/nodes/{seeded['dashboard']}:retire", json={"reason": "no"}, headers=HEADERS
    )
    assert response.status_code == 400
    assert "needs a reason" in response.json()["message"]


async def test_upsert_over_http(client, valid_workbook) -> None:
    node_id = new_ulid()
    response = await client.put(
        f"/v1/nodes/{node_id}", json=valid_workbook, headers=HEADERS
    )
    assert response.status_code == 200
    assert response.json()["properties"]["id"] == node_id


async def test_events_over_http(client, seeded) -> None:
    response = await client.get("/v1/events?limit=5", headers=HEADERS)
    assert response.status_code == 200
    body = response.json()
    assert len(body["events"]) == 5
    assert body["has_more"] is True
    assert body["next_after"] == 5
    assert body["events"][0]["event"]["specversion"] == "1.0"


async def test_events_page_forward(client, seeded) -> None:
    first = (await client.get("/v1/events?limit=5", headers=HEADERS)).json()
    second = (
        await client.get(f"/v1/events?limit=5&after={first['next_after']}", headers=HEADERS)
    ).json()
    assert [e["sequence"] for e in second["events"]] == [6, 7, 8, 9, 10]


async def test_events_filter_by_subject(client, seeded) -> None:
    response = await client.get(
        f"/v1/events?subject={seeded['workbook']}", headers=HEADERS
    )
    events = response.json()["events"]
    assert len(events) == 1
    assert events[0]["event"]["subject"] == seeded["workbook"]


async def test_edge_upsert_over_http(client, seeded) -> None:
    edge_id = new_ulid()
    response = await client.put(
        f"/v1/edges/{edge_id}",
        json={
            "type": "CONTAINS",
            "from_id": seeded["workbook"],
            "to_id": seeded["worksheet"],
            "properties": {},
        },
        headers=HEADERS,
    )
    assert response.status_code == 200
    assert response.json()["properties"]["id"] == edge_id


async def test_edge_write_and_upsert_both_emit(repository, writer, seeded) -> None:
    edge_id = new_ulid()
    await writer.upsert_edge(
        EdgeWrite(
            type="CONTAINS",
            id=edge_id,
            from_id=seeded["workbook"],
            to_id=seeded["worksheet"],
            properties={},
        ),
        principal=PRINCIPAL,
    )
    last = (await repository.read_events(limit=1000))[-1]
    assert last.type is EventType.EDGE_UPSERTED
    assert last.subject == edge_id


async def test_writer_event_source_names_the_graph() -> None:
    from astra_graph.events import source_for

    repository = InMemoryGraphRepository()
    writer = GraphWriter(repository, event_source=source_for("astra_estate"))
    await writer.write_nodes(
        [NodeWrite(type="Site", properties={"luid": "rqa", "name": "RQA"})],
        principal=PRINCIPAL,
    )
    assert (await repository.read_events())[0].source == "/astra/graph-svc/astra_estate"
