"""The Data Handling position/sign-off -- story S11.4.1. Pure logic against in-memory
stores, no Postgres; see test_integration_data_handling.py for the real stores, the
real boundary test, and the HTTP routes."""

from __future__ import annotations

from astra_graph.config import Settings
from astra_graph.data_handling import (
    INFERENCE_BOUNDARY_TABLE,
    InMemoryDataHandlingPositionStore,
    InMemoryDataHandlingSignoffStore,
    boundary_status,
    default_position,
    sign_position,
)


def _settings() -> Settings:
    return Settings(
        postgres_host="localhost", postgres_port=5432, postgres_db="astra",
        postgres_user="astra", postgres_password="test", graph_name="astra_test",
        env="test", log_level="WARNING", pool_min_size=1, pool_max_size=1,
    )


class TestInferenceBoundaryTable:
    def test_names_every_ac_category_sent_and_never_sent(self) -> None:
        sent = " ".join(INFERENCE_BOUNDARY_TABLE["sent"])
        never_sent = " ".join(INFERENCE_BOUNDARY_TABLE["never_sent"])
        for phrase in ("Calculation expressions", "names", "Data types", "error text"):
            assert phrase in sent
        for phrase in ("Row-level data", "result sets", "Credentials"):
            assert phrase in never_sent


class TestDefaultPosition:
    def test_the_one_real_wired_provider_is_anthropic(self) -> None:
        position = default_position(_settings())
        assert position.providers[0]["name"] == "anthropic"
        assert position.version == 0

    def test_redaction_rules_default_to_the_real_enforced_rules(self) -> None:
        from astra_graph.redaction import REDACTION_RULES

        assert default_position(_settings()).redaction_rules == REDACTION_RULES


class TestBoundaryStatus:
    async def test_a_fresh_deployment_is_unsigned(self) -> None:
        status = await boundary_status(
            InMemoryDataHandlingPositionStore(_settings()), InMemoryDataHandlingSignoffStore(),
        )
        assert status.signed is False
        assert status.signoff is None

    async def test_signing_the_current_position_makes_it_signed(self) -> None:
        positions = InMemoryDataHandlingPositionStore(_settings())
        signoffs = InMemoryDataHandlingSignoffStore()
        await sign_position(positions, signoffs, reviewer="user:infosec@client.example")
        status = await boundary_status(positions, signoffs)
        assert status.signed is True
        assert status.signoff is not None
        assert status.signoff.reviewer == "user:infosec@client.example"
        assert status.signoff.position_version == status.position.version

    async def test_editing_the_position_after_signing_invalidates_it(self) -> None:
        positions = InMemoryDataHandlingPositionStore(_settings())
        signoffs = InMemoryDataHandlingSignoffStore()
        await sign_position(positions, signoffs, reviewer="user:infosec@client.example")
        assert (await boundary_status(positions, signoffs)).signed is True

        await positions.save(
            providers=({"name": "anthropic", "model": "claude-sonnet-5", "region": None},),
            retention_terms="updated terms", redaction_rules=(), updated_by="user:pe@artizent.example",
        )
        status = await boundary_status(positions, signoffs)
        assert status.signed is False
        assert status.position.version != status.signoff.position_version  # type: ignore[union-attr]

    async def test_re_signing_after_an_edit_makes_it_signed_again(self) -> None:
        positions = InMemoryDataHandlingPositionStore(_settings())
        signoffs = InMemoryDataHandlingSignoffStore()
        await sign_position(positions, signoffs, reviewer="user:infosec@client.example")
        await positions.save(
            providers=({"name": "anthropic", "model": "claude-sonnet-5", "region": None},),
            retention_terms="updated terms", redaction_rules=(), updated_by="user:pe@artizent.example",
        )
        await sign_position(positions, signoffs, reviewer="user:infosec@client.example")
        assert (await boundary_status(positions, signoffs)).signed is True

    async def test_a_signature_always_attests_to_the_current_version_never_a_caller_named_one(self) -> None:
        """`sign_position` takes no `version` parameter at all -- a signature can only
        honestly attest to what exists right now (this module's own docstring)."""
        positions = InMemoryDataHandlingPositionStore(_settings())
        signoffs = InMemoryDataHandlingSignoffStore()
        await positions.save(
            providers=(), retention_terms="v1", redaction_rules=(), updated_by="user:pe@artizent.example",
        )
        signoff = await sign_position(positions, signoffs, reviewer="user:infosec@client.example")
        assert signoff.position_version == (await positions.latest()).version
