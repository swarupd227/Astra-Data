"""Roles and the organisation each belongs to.

Transcribed from specification §2.4 "Users and roles", whose Organisation column is what
S1.1.2 means by "available to Artizent roles": the raw Cypher endpoint is open to the
delivery organisation and closed to client roles, because it bypasses the field-level
shaping the console applies to client surfaces (§15.2, "client surfaces are calm";
"platform detail is Artizent-only by default").

Like the principal (see ``principal.py``), roles are asserted by the caller in a header
until E11 maps them from Entra ID groups. The set of roles and their organisations is
real and enforced; only the source of the assertion is provisional.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum

ROLES_HEADER = "X-Astra-Roles"

_ROLE_TOKEN = re.compile(r"^[a-z_]{1,64}$")


class Organisation(str, Enum):
    ARTIZENT = "artizent"
    CLIENT = "client"


class Role(str, Enum):
    """Spec §2.4. The value is the wire form used in the header."""

    PROGRAMME_MANAGER = "programme_manager"
    MIGRATION_ARCHITECT = "migration_architect"
    SEMANTIC_MODEL_ENGINEER = "semantic_model_engineer"
    MIGRATION_ENGINEER = "migration_engineer"
    PARITY_ENGINEER = "parity_engineer"
    PLATFORM_ENGINEER = "platform_engineer"
    CLIENT_DATA_OWNER = "client_data_owner"
    CLIENT_REPORT_OWNER = "client_report_owner"
    CLIENT_LICENCE_ADMIN = "client_licence_admin"
    CLIENT_INFOSEC_REVIEWER = "client_infosec_reviewer"
    CLIENT_PROGRAMME_SPONSOR = "client_programme_sponsor"


ORGANISATION_OF: dict[Role, Organisation] = {
    Role.PROGRAMME_MANAGER: Organisation.ARTIZENT,
    Role.MIGRATION_ARCHITECT: Organisation.ARTIZENT,
    Role.SEMANTIC_MODEL_ENGINEER: Organisation.ARTIZENT,
    Role.MIGRATION_ENGINEER: Organisation.ARTIZENT,
    Role.PARITY_ENGINEER: Organisation.ARTIZENT,
    Role.PLATFORM_ENGINEER: Organisation.ARTIZENT,
    Role.CLIENT_DATA_OWNER: Organisation.CLIENT,
    Role.CLIENT_REPORT_OWNER: Organisation.CLIENT,
    Role.CLIENT_LICENCE_ADMIN: Organisation.CLIENT,
    Role.CLIENT_INFOSEC_REVIEWER: Organisation.CLIENT,
    Role.CLIENT_PROGRAMME_SPONSOR: Organisation.CLIENT,
}

ARTIZENT_ROLES = frozenset(r for r, org in ORGANISATION_OF.items() if org is Organisation.ARTIZENT)


class InvalidRolesError(ValueError):
    """The asserted roles are malformed or unknown."""


@dataclass(frozen=True, slots=True)
class RoleSet:
    roles: frozenset[Role]

    @property
    def organisations(self) -> frozenset[Organisation]:
        return frozenset(ORGANISATION_OF[role] for role in self.roles)

    def has_any(self, allowed: frozenset[Role]) -> bool:
        return bool(self.roles & allowed)

    def is_artizent(self) -> bool:
        return self.has_any(ARTIZENT_ROLES)

    def __str__(self) -> str:
        return ",".join(sorted(role.value for role in self.roles)) or "-"


def parse(header_value: str | None) -> RoleSet:
    """Parse the roles header. An absent header is an empty role set, not an error:
    role is only required by endpoints that gate on it."""
    if not header_value or not header_value.strip():
        return RoleSet(frozenset())

    roles: set[Role] = set()
    for raw in header_value.split(","):
        token = raw.strip().lower()
        if not token:
            continue
        if not _ROLE_TOKEN.match(token):
            raise InvalidRolesError(f"{ROLES_HEADER} contains a malformed role: {raw!r}")
        try:
            roles.add(Role(token))
        except ValueError as exc:
            known = ", ".join(sorted(role.value for role in Role))
            raise InvalidRolesError(
                f"{ROLES_HEADER} contains an unknown role {token!r}. Known roles: {known}."
            ) from exc
    return RoleSet(frozenset(roles))
