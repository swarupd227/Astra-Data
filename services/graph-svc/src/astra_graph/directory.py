"""Resolving a source user against the client's directory.

S1.2.3: "Owner is linked to a User node resolved against Entra ID where possible;
unresolved owners are listed for assignment". Specification §6.2 puts it the same way —
owners are "mapped to Entra users where a match exists" — and §18.1 makes Entra ID the
platform's identity source.

Two things follow from "where possible", and both shape this module:

* **Resolution can fail, and that is ordinary.** A Tableau site that has outlived a
  reorganisation is full of owners who no longer exist in the directory, and a workbook
  whose owner did not resolve is a workbook nobody can be sent a G3 gate request for. So
  an unresolved owner is a *recorded state* to be worked through, not an error to swallow.
* **The identity the platform keys on stays the source's.** A User node is identified by
  what Tableau called the person; resolving adds ``directory_id`` to it. Re-keying on the
  directory id would make a resolution change the node's identity, and every edge to it.

The real resolver is E11's, where the platform gets a Graph API credential and the
workload identity to use it with. What is here is the seam it plugs into, a null resolver
that resolves nothing, and a static one for tests. A deployment with no resolver
configured lists every owner as unresolved, which is the truthful answer.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from typing import Protocol

logger = logging.getLogger(__name__)

#: Directory object ids are GUIDs; a person assigning one by hand should not be able to
#: paste a display name into the field.
DIRECTORY_ID = re.compile(
    r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$"
)


class DirectoryError(Exception):
    """The directory could not be reached, or was asked for something malformed."""


@dataclass(frozen=True, slots=True)
class DirectoryUser:
    """A person as the directory knows them."""

    directory_id: str
    upn: str
    display: str | None = None


def validate_directory_id(value: str) -> str:
    if not DIRECTORY_ID.match(value.strip()):
        raise DirectoryError(
            f"directory id {value!r} is not a GUID; it should look like the object id "
            f"Entra ID reports for the user"
        )
    return value.strip()


class DirectoryResolver(Protocol):
    async def resolve(self, upn: str) -> DirectoryUser | None:
        """The directory user for a source identity, or None when there is no match."""
        ...

    async def resolve_many(self, upns: list[str]) -> dict[str, DirectoryUser]:
        """Resolve several at once. Only the ones that matched are in the result."""
        ...


class NullDirectoryResolver:
    """Resolves nothing.

    The default until E11 supplies the Entra resolver. It is not a stub that pretends: a
    deployment running this lists every owner as unresolved, which is exactly what is true
    of a platform that cannot reach a directory.
    """

    kind = "null"

    async def resolve(self, upn: str) -> DirectoryUser | None:
        return None

    async def resolve_many(self, upns: list[str]) -> dict[str, DirectoryUser]:
        return {}


class StaticDirectoryResolver:
    """A fixed map of source identity to directory user, for tests and local runs."""

    kind = "static"

    def __init__(self, users: dict[str, DirectoryUser]) -> None:
        self._users = {upn.lower(): user for upn, user in users.items()}

    async def resolve(self, upn: str) -> DirectoryUser | None:
        return self._users.get(upn.lower())

    async def resolve_many(self, upns: list[str]) -> dict[str, DirectoryUser]:
        found = {}
        for upn in upns:
            match = await self.resolve(upn)
            if match is not None:
                found[upn] = match
        return found
