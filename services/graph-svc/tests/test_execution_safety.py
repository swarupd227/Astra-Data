"""Execution safety -- story S11.2.1, opens F11.2. Pure unit tests (no Postgres): see
test_integration_execution_safety.py for the real, Postgres-backed store, and
test_integration_case_execution.py for the enforcement wired end to end."""

from __future__ import annotations

import pytest

from astra_graph.execution_safety import (
    REGRESSION_RUNNER_PRINCIPAL,
    ExecutionSafetyPolicy,
    InMemoryExecutionSafetyPolicyStore,
    ProductionExecutionRefused,
    authorize_target_workspace,
)


class TestExecutionSafetyPolicy:
    def test_the_default_policy_names_no_production_workspace(self) -> None:
        policy = ExecutionSafetyPolicy()
        assert policy.production_workspaces == frozenset()
        assert policy.version == 0

    def test_as_dict_is_sorted_and_json_shaped(self) -> None:
        policy = ExecutionSafetyPolicy(production_workspaces=frozenset({"b", "a"}), version=3)
        assert policy.as_dict() == {"production_workspaces": ["a", "b"], "version": 3}


class TestAuthorizeTargetWorkspace:
    def test_a_non_production_workspace_is_unaffected_by_any_caller(self) -> None:
        policy = ExecutionSafetyPolicy(production_workspaces=frozenset({"prod"}))
        authorize_target_workspace("user:parity@artizent.example", workspace="dev", policy=policy)
        authorize_target_workspace("agent:harvester", workspace="test", policy=policy)

    def test_a_production_workspace_refuses_a_human_principal(self) -> None:
        policy = ExecutionSafetyPolicy(production_workspaces=frozenset({"prod"}))
        with pytest.raises(ProductionExecutionRefused, match="classified production"):
            authorize_target_workspace(
                "user:parity@artizent.example", workspace="prod", policy=policy
            )

    def test_a_production_workspace_refuses_a_non_steward_agent(self) -> None:
        policy = ExecutionSafetyPolicy(production_workspaces=frozenset({"prod"}))
        with pytest.raises(ProductionExecutionRefused):
            authorize_target_workspace("agent:harvester", workspace="prod", policy=policy)

    def test_a_production_workspace_allows_only_the_regression_runner(self) -> None:
        policy = ExecutionSafetyPolicy(production_workspaces=frozenset({"prod"}))
        authorize_target_workspace(REGRESSION_RUNNER_PRINCIPAL, workspace="prod", policy=policy)

    def test_an_empty_policy_refuses_nobody(self) -> None:
        policy = ExecutionSafetyPolicy()
        authorize_target_workspace("user:anyone@artizent.example", workspace="prod", policy=policy)


class TestInMemoryExecutionSafetyPolicyStore:
    async def test_defaults_to_the_empty_policy(self) -> None:
        store = InMemoryExecutionSafetyPolicyStore()
        assert await store.latest() == ExecutionSafetyPolicy()

    async def test_save_bumps_the_version_and_is_read_back(self) -> None:
        store = InMemoryExecutionSafetyPolicyStore()
        first = await store.save(
            ExecutionSafetyPolicy(production_workspaces=frozenset({"prod"})),
            updated_by="user:pe@artizent.example",
        )
        assert first.version == 1
        assert await store.latest() == first

        second = await store.save(
            ExecutionSafetyPolicy(production_workspaces=frozenset({"prod", "prod-eu"})),
            updated_by="user:pe@artizent.example",
        )
        assert second.version == 2
        assert await store.latest() == second
