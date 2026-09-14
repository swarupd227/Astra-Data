"""Credential resolution — environment-backed (local/CI) and Key Vault-backed (S11.1.1).

`KeyVaultCredentialProvider` is disclosed as not yet run against a live vault (see its
own docstring); these tests exercise it against a fake async secret client instead,
proving the real logic — reference-to-secret-name mapping, the shape of what comes back,
and how a missing/errored secret is reported — independent of Azure ever being reachable.
"""

from __future__ import annotations

from dataclasses import dataclass

import pytest

from astra_graph.config import Settings
from astra_graph.credentials import (
    CredentialError,
    EnvironmentCredentialProvider,
    KeyVaultCredentialProvider,
    StaticCredentialProvider,
    validate_reference,
)
from astra_graph.harvest_setup import build_credential_provider


def _settings(**overrides: object) -> Settings:
    base = {
        "postgres_host": "localhost", "postgres_port": 5432, "postgres_db": "astra",
        "postgres_user": "astra", "postgres_password": "test", "graph_name": "astra_estate_test",
        "env": "local", "log_level": "INFO", "pool_min_size": 2, "pool_max_size": 10,
    }
    base.update(overrides)
    return Settings(**base)  # type: ignore[arg-type]


@dataclass
class _FakeSecret:
    value: str | None


class _FakeSecretClient:
    """Stands in for `azure.keyvault.secrets.aio.SecretClient` -- a real vault has never
    been reachable from this project, so this is what makes `KeyVaultCredentialProvider`
    testable at all today."""

    def __init__(self, secrets: dict[str, str | None]) -> None:
        self._secrets = secrets
        self.requested: list[str] = []

    async def get_secret(self, name: str, **kwargs: object) -> _FakeSecret:
        self.requested.append(name)
        if name not in self._secrets:
            raise KeyError(f"no such secret: {name}")
        return _FakeSecret(value=self._secrets[name])


# ------------------------------------------------------------- provider selection


def test_build_credential_provider_defaults_to_environment() -> None:
    provider = build_credential_provider(_settings())
    assert isinstance(provider, EnvironmentCredentialProvider)


def test_build_credential_provider_selects_key_vault_when_configured() -> None:
    provider = build_credential_provider(_settings(key_vault_url="https://a-vault.vault.azure.net"))
    assert isinstance(provider, KeyVaultCredentialProvider)


# --------------------------------------------------------- KeyVaultCredentialProvider


async def test_resolves_a_secret_by_its_key_vault_name() -> None:
    client = _FakeSecretClient({"tableau-rqa": "a-personal-access-token"})
    provider = KeyVaultCredentialProvider("https://a-vault.vault.azure.net", client=client)
    credential = await provider.resolve("tableau/rqa")
    assert credential.secret() == "a-personal-access-token"
    assert credential.reference == "tableau/rqa"
    assert client.requested == ["tableau-rqa"]


async def test_the_reference_slash_becomes_the_key_vault_hyphen() -> None:
    """Key Vault secret names allow letters, digits and hyphens only -- see the class's
    own docstring for why `/` cannot be the separator there."""
    client = _FakeSecretClient({"fabric-workspace-sp": "a-client-secret"})
    provider = KeyVaultCredentialProvider("https://a-vault.vault.azure.net", client=client)
    await provider.resolve("fabric/workspace-sp")
    assert client.requested == ["fabric-workspace-sp"]


async def test_a_missing_secret_is_a_credential_error_not_a_raw_sdk_exception() -> None:
    client = _FakeSecretClient({})
    provider = KeyVaultCredentialProvider("https://a-vault.vault.azure.net", client=client)
    with pytest.raises(CredentialError, match="could not resolve"):
        await provider.resolve("tableau/rqa")


async def test_a_secret_with_no_value_is_a_credential_error() -> None:
    client = _FakeSecretClient({"tableau-rqa": None})
    provider = KeyVaultCredentialProvider("https://a-vault.vault.azure.net", client=client)
    with pytest.raises(CredentialError, match="has no value"):
        await provider.resolve("tableau/rqa")


async def test_key_vault_provider_still_validates_the_reference_shape() -> None:
    client = _FakeSecretClient({})
    provider = KeyVaultCredentialProvider("https://a-vault.vault.azure.net", client=client)
    with pytest.raises(CredentialError, match="must look like"):
        await provider.resolve("not-a-valid-reference")
    assert client.requested == []


def test_key_vault_provider_kind() -> None:
    client = _FakeSecretClient({})
    provider = KeyVaultCredentialProvider("https://a-vault.vault.azure.net", client=client)
    assert provider.kind == "key_vault"


# ------------------------------------------------ existing providers, unaffected


async def test_environment_provider_still_requires_the_env_var(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ASTRA_CREDENTIAL_TABLEAU_RQA", raising=False)
    with pytest.raises(CredentialError, match="no credential"):
        await EnvironmentCredentialProvider().resolve("tableau/rqa")


async def test_static_provider_still_works() -> None:
    provider = StaticCredentialProvider({"tableau/rqa": "token"})
    credential = await provider.resolve("tableau/rqa")
    assert credential.secret() == "token"


def test_validate_reference_rejects_the_key_vault_disallowed_shape() -> None:
    with pytest.raises(CredentialError):
        validate_reference("Tableau/RQA/extra")
