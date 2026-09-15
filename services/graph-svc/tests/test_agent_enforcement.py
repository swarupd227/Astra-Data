"""Least privilege enforced at the graph API, artefact store and gateway — story
S11.1.2's own second AC bullet, proven against the real objects, not just
`agent_identity.py`'s own pure functions (see `test_agent_identity.py` for those).
"""

from __future__ import annotations

import pytest

from astra_graph.agent_identity import AgentAuthorizationError
from astra_graph.artefacts import InMemoryArtefactStore
from astra_graph.gateway import RawModelResponse, StaticGateway
from astra_graph.principal import Principal
from astra_graph.writes import GraphWriter, NodeWrite

TRANSPILER = Principal("agent:transpiler", run_id="run-1")


# ------------------------------------------------------------------- GraphWriter


async def test_the_transpiler_can_write_its_own_real_node_type(writer: GraphWriter) -> None:
    created = await writer.write_nodes(
        [NodeWrite(
            type="Measure",
            properties={"name": "Margin %", "dax": "DIVIDE(1,1)", "provenance_ref": "prov-1"},
        )],
        principal=TRANSPILER,
    )
    assert created[0]["properties"]["created_by"] == "agent:transpiler"


async def test_the_transpiler_is_refused_writing_a_pattern_node(writer: GraphWriter) -> None:
    with pytest.raises(AgentAuthorizationError):
        await writer.write_nodes(
            [NodeWrite(type="Pattern", properties={
                "name": "x", "class": "C2", "source_signature": {}, "target_template": "x",
                "promotion_state": "ACTIVE",
            })],
            principal=TRANSPILER,
        )


async def test_the_refusal_happens_before_any_node_is_written(
    writer: GraphWriter, repository,
) -> None:
    """A mixed batch -- one allowed, one not -- writes nothing at all, the same
    all-or-nothing batch discipline ontology validation already has."""
    with pytest.raises(AgentAuthorizationError):
        await writer.write_nodes(
            [
                NodeWrite(type="Measure", properties={"name": "OK one", "dax": "1"}),
                NodeWrite(type="Pattern", properties={
                    "name": "refused", "class": "C2", "source_signature": {},
                    "target_template": "x", "promotion_state": "ACTIVE",
                }),
            ],
            principal=TRANSPILER,
        )
    version, _at = await repository.current_version()
    assert version == 0


async def test_a_human_principal_is_never_refused_by_agent_scope(writer: GraphWriter) -> None:
    human = Principal("user:a.mehta@client.example")
    created = await writer.write_nodes(
        [NodeWrite(type="Pattern", properties={
            "name": "x", "class": "C2", "source_signature": {}, "target_template": "x",
            "promotion_state": "ACTIVE",
        })],
        principal=human,
    )
    assert created


async def test_set_node_properties_refuses_the_transpiler_touching_pattern_at_all(
    writer: GraphWriter, repository,
) -> None:
    """`set_node_properties` funnels through the identical `_prepare_nodes` chokepoint
    `write_nodes`/`upsert_nodes` already use (`writes.py`'s own docstring), so the
    Transpiler's own node-type gate alone already keeps it off `Pattern.promotion_state`
    -- `test_agent_identity.py`'s own `authorize_property_write` tests exercise the
    narrower, property-level mechanism directly, for an agent whose node-type scope would
    otherwise let a write through."""
    human = Principal("user:platform-eng@artizent.example")
    created = await writer.write_nodes(
        [NodeWrite(type="Pattern", properties={
            "name": "x", "class": "C2", "source_signature": {}, "target_template": "x",
            "promotion_state": "CANDIDATE",
        })],
        principal=human,
    )
    pattern_id = str(created[0]["properties"]["id"])

    with pytest.raises(AgentAuthorizationError, match="Pattern"):
        await writer.set_node_properties(
            pattern_id, {"promotion_state": "ACTIVE"}, principal=TRANSPILER,
        )


# ----------------------------------------------------------------- ArtefactStore


async def test_the_transpiler_cannot_store_any_artefact() -> None:
    store = InMemoryArtefactStore()
    with pytest.raises(AgentAuthorizationError):
        await store.store(
            kind="visual_capture", mu_ref="wb-1", case_id="c1", content=b"bytes",
            media_type="image/png", created_by=TRANSPILER.value,
        )


async def test_harvester_can_store_an_artefact() -> None:
    store = InMemoryArtefactStore()
    record = await store.store(
        kind="visual_capture", mu_ref="wb-1", case_id="c1", content=b"bytes",
        media_type="image/png", created_by="agent:harvester",
    )
    assert record.kind == "visual_capture"


async def test_a_refused_artefact_write_never_reaches_the_no_bytes_check() -> None:
    """Authorization is checked first -- proven by a call that would also fail the
    content check, to confirm which error actually surfaces."""
    store = InMemoryArtefactStore()
    with pytest.raises(AgentAuthorizationError):
        await store.store(
            kind="visual_capture", mu_ref="wb-1", case_id="c1", content=b"",
            media_type="image/png", created_by=TRANSPILER.value,
        )


# ---------------------------------------------------------------------- Gateway


class _FixedResponseCaller:
    provider = "test"
    model = "test-model"

    async def generate(self, request: object, *, previous_error: str | None) -> RawModelResponse:
        return RawModelResponse(
            raw={"dax": "SUM(1)"}, gateway_request_id="req-1", provider="test", model="test-model",
            prompt_hash="hash", temperature=0.0, tokens_in=1, tokens_out=1,
        )


class _EmptyRequest:
    """Story S11.4.2: `StaticGateway.generate` now always calls `request.as_dict()`
    itself (field-schema validation and pattern redaction run unconditionally, not
    only when a log store happens to be configured) -- a bare `object()` no longer
    satisfies `SupportsAsDict`, the identical real, disclosed tightening every other
    caller of the gateway is also now held to."""

    def as_dict(self) -> dict[str, object]:
        return {}


async def test_the_transpiler_can_call_the_gateway_for_its_own_task_class() -> None:
    gateway = StaticGateway(_FixedResponseCaller())
    response = await gateway.generate(
        task_class="transpile_c3", request=_EmptyRequest(), previous_error=None,
        principal=TRANSPILER.value,
    )
    assert response.raw == {"dax": "SUM(1)"}


async def test_the_transpiler_is_refused_calling_the_gateway_for_mender_repair() -> None:
    gateway = StaticGateway(_FixedResponseCaller())
    with pytest.raises(AgentAuthorizationError):
        await gateway.generate(
            task_class="mender_repair", request=_EmptyRequest(), previous_error=None,
            principal=TRANSPILER.value,
        )


async def test_omitting_principal_is_unchanged_backward_compatible_behaviour() -> None:
    """Every caller before this story never passed `principal` at all -- proven here by
    calling the gateway with `mender_repair`, a task class the Transpiler alone would be
    refused, and confirming it still succeeds with no identity asserted."""
    gateway = StaticGateway(_FixedResponseCaller())
    response = await gateway.generate(task_class="mender_repair", request=_EmptyRequest(), previous_error=None)
    assert response.raw == {"dax": "SUM(1)"}
