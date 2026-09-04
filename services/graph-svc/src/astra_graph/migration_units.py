"""Which workbooks already have work in progress over them.

S1.2.4: "a changed source workbook that already has an MU in progress raises a
SOURCE_DRIFT event and marks the MU for re-proof (see E7)".

The Migration Unit is specification §3.1 — one source workbook and everything the platform
produces for it. It is not an estate-graph node: §4.1 does not define one, because an MU is
a record of *work*, not a fact about the client's estate. It belongs to the control plane,
which E3 builds when the Cartographer starts creating MUs.

So this is a port, not an implementation. The Harvester needs two answers from whatever
ends up holding MUs — is one in progress over this workbook, and please mark it for
re-proof — and asking for them through a named seam is how the harvest stays independent
of where MUs live. Until E3 there are none, and ``NullMigrationUnitRegistry`` says so.

**Why "in progress" excludes HARVESTED.** An MU at HARVESTED has produced nothing but a
parse. A re-parse *is* its update, so there is nothing to re-prove and no drift to
announce. From CLUSTERED onwards there are artefacts, verdicts or decisions derived from a
version of the workbook that no longer exists, and that is worth interrupting somebody for.
Terminal states are excluded for the opposite reason: WITHDRAWN and DECOMMISSIONED are not
coming back. A RELEASED MU is included deliberately — the backlog has the Steward re-running
parity "weekly during parallel run, on SOURCE_DRIFT", and a source that changes under a
report already in production is precisely the case that costs a client money.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

#: Specification §3.2. Held as strings rather than an enum: the state machine belongs to
#: the control plane, and this service should not be the place it is defined.
MU_STATES: tuple[str, ...] = (
    "HARVESTED",
    "CLUSTERED",
    "BLOCKED",
    "MODEL_READY",
    "GENERATED",
    "PROVING",
    "FAILED",
    "MENDING",
    "ESCALATED",
    "ADJUDICATED",
    "PASSED",
    "ACCEPTED",
    "RELEASED",
    "DECOMMISSIONED",
    "WITHDRAWN",
)

#: States in which a source change means work already done may now be wrong.
IN_PROGRESS_STATES: frozenset[str] = frozenset(MU_STATES) - {
    "HARVESTED",
    "DECOMMISSIONED",
    "WITHDRAWN",
}


class MigrationUnitError(Exception):
    """The registry could not be reached or answered."""


@dataclass(frozen=True, slots=True)
class MigrationUnitRef:
    """Just enough of an MU to name it in an event and act on it."""

    id: str
    state: str
    site: str
    workbook_luid: str

    @property
    def in_progress(self) -> bool:
        return self.state in IN_PROGRESS_STATES


class MigrationUnitRegistry(Protocol):
    """Where Migration Units live, from the Harvester's point of view."""

    @property
    def kind(self) -> str:
        """Named in the API response, so a caller can tell "none in progress" from
        "nothing is tracking them yet"."""
        ...

    async def in_progress(self, site: str, workbook_luid: str) -> MigrationUnitRef | None:
        """The MU over this workbook, if there is one and it is being worked."""
        ...

    async def mark_for_reproof(
        self, migration_unit_id: str, *, reason: str, principal: str
    ) -> bool:
        """Ask for the MU to be re-proved. True if the mark was accepted."""
        ...


class NullMigrationUnitRegistry:
    """No Migration Units exist. Correct until E3 creates the first one.

    Not a failure mode and not a stub that lies: before the Cartographer runs, every
    workbook's only record is its parse, so no harvest can disturb work in progress.
    """

    kind = "none"

    async def in_progress(self, site: str, workbook_luid: str) -> MigrationUnitRef | None:
        return None

    async def mark_for_reproof(
        self, migration_unit_id: str, *, reason: str, principal: str
    ) -> bool:  # pragma: no cover - unreachable while in_progress returns None
        return False


class InMemoryMigrationUnitRegistry:
    """Migration Units held in memory. For tests, and for the fixture estate.

    Records the re-proof marks it accepts so a caller can assert on them, which is what
    the E7 hand-off will need to be checked against when it arrives.
    """

    kind = "in_memory"

    def __init__(self, units: dict[tuple[str, str], MigrationUnitRef] | None = None) -> None:
        self._units = dict(units or {})
        self.marked: list[tuple[str, str, str]] = []
        """(migration_unit_id, reason, principal), in the order they were marked."""

    def add(self, unit: MigrationUnitRef) -> None:
        self._units[(unit.site, unit.workbook_luid)] = unit

    async def in_progress(self, site: str, workbook_luid: str) -> MigrationUnitRef | None:
        unit = self._units.get((site, workbook_luid))
        if unit is None or not unit.in_progress:
            return None
        return unit

    async def mark_for_reproof(
        self, migration_unit_id: str, *, reason: str, principal: str
    ) -> bool:
        if not any(unit.id == migration_unit_id for unit in self._units.values()):
            raise MigrationUnitError(f"no migration unit '{migration_unit_id}'")
        self.marked.append((migration_unit_id, reason, principal))
        return True


__all__ = [
    "IN_PROGRESS_STATES",
    "MU_STATES",
    "InMemoryMigrationUnitRegistry",
    "MigrationUnitError",
    "MigrationUnitRef",
    "MigrationUnitRegistry",
    "NullMigrationUnitRegistry",
]
