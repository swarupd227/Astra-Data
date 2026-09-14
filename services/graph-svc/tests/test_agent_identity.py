"""Agent identity and least privilege — story S11.1.2, opens F11.1.

Pure unit tests: `agent_identity.py`'s own authorization functions take a plain
principal-value string and never touch the store or the network.
"""

from __future__ import annotations

import pytest

from astra_graph.agent_identity import (
    AGENT_CATALOG,
    AgentAuthorizationError,
    AgentScope,
    agent_id_of,
    authorize_artefact_kind,
    authorize_gateway_call,
    authorize_node_write,
    authorize_property_write,
)


def test_the_catalog_declares_every_spec_named_agent() -> None:
    """§8.3's own real, eight-agent catalog."""
    assert set(AGENT_CATALOG) == {
        "harvester", "cartographer", "modeller", "transpiler",
        "compositor", "arbiter", "mender", "steward",
    }


def test_the_arbiter_is_declared_but_disclosed_as_not_real() -> None:
    """E7, disclosed absent everywhere else in this codebase too."""
    assert AGENT_CATALOG["arbiter"].real is False
    for agent_id, record in AGENT_CATALOG.items():
        if agent_id != "arbiter":
            assert record.real is True


def test_every_agent_except_the_transpiler_is_unrestricted() -> None:
    for agent_id, record in AGENT_CATALOG.items():
        if agent_id == "transpiler":
            continue
        assert record.scope == AgentScope.unrestricted(), agent_id


def test_agent_record_as_dict_round_trips_the_real_shape() -> None:
    record = AGENT_CATALOG["transpiler"].as_dict()
    assert record["id"] == "transpiler"
    assert record["charter"]["prohibited"] == [
        "write outside MU scope", "call executor", "modify Pattern.promotion_state",
    ]
    assert record["scope"]["allowed_node_types"] == ["CalculatedField", "ExceptionCase", "Measure"]
    assert record["scope"]["unrestricted"] is False
    assert AGENT_CATALOG["harvester"].as_dict()["scope"]["unrestricted"] is True


# ---------------------------------------------------------------- agent_id_of


def test_agent_id_of_extracts_the_name() -> None:
    assert agent_id_of("agent:transpiler") == "transpiler"


def test_agent_id_of_is_none_for_a_human_or_service_principal() -> None:
    assert agent_id_of("user:a.mehta@client.example") is None
    assert agent_id_of("service:graph-svc") is None


# --------------------------------------------------------- node/property writes


def test_the_transpiler_may_write_its_own_real_node_types() -> None:
    authorize_node_write("agent:transpiler", "Measure")
    authorize_node_write("agent:transpiler", "CalculatedField")
    authorize_node_write("agent:transpiler", "ExceptionCase")


def test_the_transpiler_is_refused_writing_an_out_of_scope_node_type() -> None:
    with pytest.raises(AgentAuthorizationError, match="Pattern"):
        authorize_node_write("agent:transpiler", "Pattern")


def test_the_transpiler_is_refused_modifying_pattern_promotion_state() -> None:
    with pytest.raises(AgentAuthorizationError, match="promotion_state"):
        authorize_property_write("agent:transpiler", "Pattern", ["promotion_state"])


def test_a_forbidden_property_check_only_looks_at_properties_actually_named() -> None:
    """A CalculatedField write naming only its own real properties is never refused --
    `Pattern.promotion_state` is a different node type and property entirely."""
    authorize_property_write("agent:transpiler", "CalculatedField", ["formula", "formula_ast"])


def test_harvester_the_test_suites_own_default_identity_is_never_restricted() -> None:
    """`tests/conftest.py`'s own default principal -- this must never refuse, or the
    overwhelming majority of this test suite would break."""
    for node_type in ("Site", "Pattern", "ExceptionCase", "GateDecision", "Measure", "AnythingAtAll"):
        authorize_node_write("agent:harvester", node_type)


def test_steward_the_real_automated_build_and_regression_principal_is_unrestricted() -> None:
    authorize_node_write("agent:steward", "BuildRun")
    authorize_node_write("agent:steward", "Pattern")


def test_an_unknown_agent_id_is_unrestricted_not_refused() -> None:
    """A real, disclosed choice -- see `agent_identity.py`'s own module docstring on why
    a permission framework retrofitted onto a mature system defaults open for identities
    this story has no real evidence to restrict."""
    authorize_node_write("agent:some-test-fixture-name", "AnythingAtAll")


def test_a_human_principal_is_never_scoped_by_this_module() -> None:
    authorize_node_write("user:a.mehta@client.example", "AnythingAtAll")
    authorize_property_write("user:a.mehta@client.example", "Pattern", ["promotion_state"])


# --------------------------------------------------------------- gateway calls


def test_the_transpiler_may_call_its_own_real_task_classes() -> None:
    authorize_gateway_call("agent:transpiler", "transpile_c3")
    authorize_gateway_call("agent:transpiler", "transpile_c3_small_model")


def test_the_transpiler_is_refused_a_task_class_that_is_not_its_own() -> None:
    with pytest.raises(AgentAuthorizationError, match="mender_repair"):
        authorize_gateway_call("agent:transpiler", "mender_repair")


def test_mender_is_unrestricted_at_the_gateway() -> None:
    """mender.py's own real repair path runs under whichever human principal triggered
    it, never a constructed `agent:mender` principal -- but the catalog entry itself
    stays unrestricted regardless, disclosed rather than guessed."""
    authorize_gateway_call("agent:mender", "mender_repair")


# -------------------------------------------------------------- artefact kinds


def test_the_transpiler_may_never_store_an_artefact() -> None:
    """`allowed_artefact_kinds=frozenset()` -- empty, not `None` -- since nothing in
    generation.py calls ArtefactStore.store at all."""
    with pytest.raises(AgentAuthorizationError, match="none"):
        authorize_artefact_kind("agent:transpiler", "visual_capture")


def test_harvester_may_store_any_artefact_kind() -> None:
    authorize_artefact_kind("agent:harvester", "visual_capture")
    authorize_artefact_kind("agent:harvester", "deployment_bom")
