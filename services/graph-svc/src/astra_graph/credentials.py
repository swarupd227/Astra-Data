"""Source credentials.

S1.2.1: "Harvest is started ... with site credentials from Key Vault".

The property that matters, and the one enforced here, is that **a secret never crosses the
API**. A caller starting a harvest names a credential — ``tableau/rqa`` — and the service
resolves it. A request body carrying a personal access token would put the token in the
API log, in the request trace and in anyone's shell history, which is the failure this
design exists to prevent (spec §18.1: secrets live in Key Vault and never enter agent
context).

Resolution itself is behind ``CredentialProvider``. The environment-backed provider here
is for local development and CI. ``KeyVaultCredentialProvider`` (story S11.1.1) is E11's
managed-identity path — it implements this same interface and nothing else changes;
selected by ``harvest_setup.build_credential_provider`` once a Key Vault is configured,
disclosed as not yet run against a live vault (see its own docstring). ``resolve`` returns
a ``SourceCredential`` whose secret is deliberately awkward to log: it is not in ``repr``,
and ``str`` shows the reference, not the value.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from collections.abc import Awaitable

    from azure.core.credentials_async import AsyncTokenCredential
    from azure.keyvault.secrets import KeyVaultSecret

    class _AsyncSecretClientProtocol(Protocol):
        """What `KeyVaultCredentialProvider` actually calls on a secret client -- real
        Azure SDK client or test fake, either satisfies this."""

        def get_secret(self, name: str, **kwargs: object) -> Awaitable[KeyVaultSecret]: ...

#: ``<system>/<name>``, e.g. ``tableau/rqa``. No characters that could be used to reach
#: outside a vault's namespace.
CREDENTIAL_REFERENCE = re.compile(r"^[a-z][a-z0-9_-]{0,31}/[a-zA-Z0-9][a-zA-Z0-9._-]{0,63}$")

#: Local and CI only: ASTRA_CREDENTIAL_TABLEAU_RQA holds the secret for `tableau/rqa`.
ENVIRONMENT_PREFIX = "ASTRA_CREDENTIAL_"


class CredentialError(Exception):
    """The credential could not be resolved."""


@dataclass(frozen=True)
class SourceCredential:
    """A resolved credential. Built to be hard to leak by accident."""

    reference: str
    kind: str
    """``personal_access_token``, ``connected_app`` or whatever the adapter expects."""

    _secret: str = field(repr=False)

    def secret(self) -> str:
        """Read the secret. Every call site that does this should be obvious in review."""
        return self._secret

    def __str__(self) -> str:
        return f"<credential {self.reference}>"


class CredentialProvider(Protocol):
    async def resolve(self, reference: str) -> SourceCredential: ...


def validate_reference(reference: str) -> str:
    if not CREDENTIAL_REFERENCE.match(reference):
        raise CredentialError(
            f"credential reference {reference!r} must look like '<system>/<name>', "
            f"for example 'tableau/rqa'"
        )
    return reference


def _environment_variable(reference: str) -> str:
    system, name = reference.split("/", 1)
    slug = re.sub(r"[^A-Za-z0-9]+", "_", f"{system}_{name}").upper()
    return f"{ENVIRONMENT_PREFIX}{slug}"


class EnvironmentCredentialProvider:
    """Reads credentials from the environment. Local development and CI only.

    A deployed tenant uses the Key Vault provider (E11); this one exists so the harvest
    path can be exercised without one, and it fails loudly rather than defaulting.
    """

    kind = "environment"

    async def resolve(self, reference: str) -> SourceCredential:
        validate_reference(reference)
        variable = _environment_variable(reference)
        secret = os.environ.get(variable)
        if not secret:
            raise CredentialError(
                f"no credential for '{reference}'. This deployment resolves credentials "
                f"from the environment; set {variable}."
            )
        return SourceCredential(
            reference=reference, kind="personal_access_token", _secret=secret
        )


class KeyVaultCredentialProvider:
    """Resolves credentials from Azure Key Vault (spec §18.1, story S11.1.1): "service
    principals for Fabric and Tableau live in Key Vault". Implements the identical
    ``CredentialProvider`` protocol ``EnvironmentCredentialProvider`` does — a reference
    like ``tableau/rqa`` becomes the Key Vault secret name ``tableau-rqa`` (Key Vault
    secret names allow letters, digits and hyphens only, so the reference's own ``/`` is
    swapped for the one separator its own namespace can hold), and the secret comes back
    the same shape either provider returns.

    **Disclosed, not yet connected — exactly like ``entra.py``'s own JWT validation.**
    ``azure-identity``'s ``DefaultAzureCredential`` and ``azure-keyvault-secrets``'s
    ``SecretClient`` are both real, and this class has real tests against a fake client
    that stands in for one, but it has never resolved a secret from a live vault, because
    no deployed tenant exists for this project yet. Selected by ``harvest_setup.
    build_credential_provider`` only when ``ASTRA_KEY_VAULT_URL`` is set; the honest
    default (unset) keeps every deployment on ``EnvironmentCredentialProvider``.
    """

    kind = "key_vault"

    def __init__(
        self,
        vault_url: str,
        *,
        credential: AsyncTokenCredential | None = None,
        client: _AsyncSecretClientProtocol | None = None,
    ) -> None:
        # Imported here, not at module scope: azure-identity/azure-keyvault-secrets are
        # real dependencies (pyproject.toml), but importing them only when a Key Vault
        # deployment actually selects this provider keeps every other deployment's
        # start-up path free of an SDK it never calls. The *async* clients, not the sync
        # ones -- `resolve` runs on graph-svc's own event loop, and a harvest can resolve
        # several site credentials concurrently; a blocking SDK call here would stall it.
        # ``client`` is the test seam: a fake stands in for a live vault the same way
        # ``StaticCredentialProvider`` already stands in for the environment.
        if client is not None:
            self._client = client
        else:
            from azure.identity.aio import DefaultAzureCredential
            from azure.keyvault.secrets.aio import SecretClient

            # The generated SDK client's own `get_secret` carries extra optional
            # keyword arguments (`version`, `out_content_type`) our protocol -- deliberately
            # narrowed to the one call this class ever makes -- does not declare.
            self._client = SecretClient(  # type: ignore[assignment]
                vault_url=vault_url, credential=credential or DefaultAzureCredential()
            )

    async def resolve(self, reference: str) -> SourceCredential:
        validate_reference(reference)
        secret_name = reference.replace("/", "-")
        try:
            secret = await self._client.get_secret(secret_name)
        except Exception as exc:  # azure.core.exceptions.ResourceNotFoundError, auth errors
            raise CredentialError(
                f"could not resolve '{reference}' from Key Vault as secret "
                f"'{secret_name}': {exc}"
            ) from exc
        if not secret.value:
            raise CredentialError(f"Key Vault secret '{secret_name}' has no value")
        return SourceCredential(
            reference=reference, kind="personal_access_token", _secret=secret.value
        )


class StaticCredentialProvider:
    """An explicit map of references to secrets, for tests."""

    kind = "static"

    def __init__(self, credentials: dict[str, str]) -> None:
        self._credentials = dict(credentials)

    async def resolve(self, reference: str) -> SourceCredential:
        validate_reference(reference)
        secret = self._credentials.get(reference)
        if secret is None:
            raise CredentialError(f"no credential for '{reference}'")
        return SourceCredential(
            reference=reference, kind="personal_access_token", _secret=secret
        )
