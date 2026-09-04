"""Who made a write.

Every node records ``created_by`` and every edge records ``written_by`` (S1.1.1 criteria 2
and 3). Until agent and human identity land in E11 — Entra ID for people, SPIFFE workload
identity for agents, spec §18.1 — the principal is asserted by the caller in a header and
this service records what it was told.

That is a stated limitation, not a hidden one: the value is required, is echoed on every
element, and the header is replaced by a verified identity in E11 without the ontology or
the write path changing shape.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

PRINCIPAL_HEADER = "X-Astra-Principal"
RUN_HEADER = "X-Astra-Run-Id"

#: ``agent:harvester``, ``user:a.mehta@client.example``, ``service:graph-svc``.
_PRINCIPAL_RE = re.compile(r"^(agent|user|service):[A-Za-z0-9._@+\-]{1,128}$")
_RUN_RE = re.compile(r"^[A-Za-z0-9._\-]{1,64}$")


class InvalidPrincipalError(ValueError):
    """The asserted principal is missing or malformed."""


@dataclass(frozen=True, slots=True)
class Principal:
    value: str
    run_id: str | None = None

    def __str__(self) -> str:
        return self.value


def parse(header_value: str | None, run_value: str | None = None) -> Principal:
    if not header_value:
        raise InvalidPrincipalError(
            f"{PRINCIPAL_HEADER} is required; every graph write records who made it"
        )
    value = header_value.strip()
    if not _PRINCIPAL_RE.match(value):
        raise InvalidPrincipalError(
            f"{PRINCIPAL_HEADER} must be 'agent:<name>', 'user:<upn>' or 'service:<name>'; "
            f"got {header_value!r}"
        )
    run_id = None
    if run_value:
        run_id = run_value.strip()
        if not _RUN_RE.match(run_id):
            raise InvalidPrincipalError(
                f"{RUN_HEADER} must match {_RUN_RE.pattern}; got {run_value!r}"
            )
    return Principal(value=value, run_id=run_id)
