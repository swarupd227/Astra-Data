"""Source credentials.

S1.2.1: "Harvest is started ... with site credentials from Key Vault".

The property that matters, and the one enforced here, is that **a secret never crosses the
API**. A caller starting a harvest names a credential — ``tableau/rqa`` — and the service
resolves it. A request body carrying a personal access token would put the token in the
API log, in the request trace and in anyone's shell history, which is the failure this
design exists to prevent (spec §18.1: secrets live in Key Vault and never enter agent
context).

Resolution itself is behind ``CredentialProvider``. The environment-backed provider here
is for local development and CI. The Key Vault provider is E11's, where managed identity
and the credential broker arrive; it implements this same interface and nothing else
changes. ``resolve`` returns a ``SourceCredential`` whose secret is deliberately awkward
to log: it is not in ``repr``, and ``str`` shows the reference, not the value.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from typing import Protocol

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
