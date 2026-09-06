"""Redesign flags as work items -- story S6.2.1, spec §11.1/§11.3, §3.2.

    "As a migration engineer, I want every redesign flag to be a work item with its
    evidence, so that flagged visuals get finished in Desktop and nothing is forgotten."

    - Redesign flags create ExceptionCases of class VISUAL_REDESIGN routed to the Exception
      Desk with the source screenshot, the mapping reason and the placeholder location
    - An MU with open redesign flags cannot enter PROVING for the affected sheets; other
      sheets proceed
    - Closing the flag records the engineer, the Desktop commit hash and the date

**"Routed to the Exception Desk" is a real `ExceptionCase`, not a real queue.** §11.3: "the
Exception Desk, which is the Migration Engineer's work queue... there is no separate defect
tracker" -- it is a console view over this exact node type (F8.3/S8.3.1, milestone I4, not
built: `services/console-web`'s own nine registered surfaces have no Exception Desk, no MU
page, no Compositor screen among them). Writing a real, queryable `ExceptionCase` with
`state="OPEN"` is the whole of "routing" it, the identical "the mechanism is real, the
screen is later" posture this codebase has taken for every E6 story so far.

**Evidence is a snapshot, not a live read.** `mapping_reason`/`placeholder_location` are
copied from the `Visual` at the moment its case opens -- S1.4.3's own "evidence copied onto
a record is a snapshot, and its field names say so" precedent (`grammar_issue.
occurrences_when_raised`). A later mapping-table edit, or the visual mapping ruleset itself
changing, must not quietly rewrite what an already-open work item says it is about.

**No screenshot is ever actually captured by anything in this platform today.** `S2.4.2`'s
own `ArtefactStore` is real and works end to end for whatever bytes a caller supplies
(`kind="visual_capture"`), but nothing in this codebase's own local/demo environment --
no live Tableau connection exists here -- ever calls `.store()` for one. `screenshot_ref`
looks for an existing artefact (matched by `case_id` naming the source worksheet, the one
real linking convention this module can use without inventing a new one) and is honestly
absent when none exists, the same "disclosed absent, not fabricated" posture every other
gap in this codebase already takes.

**A re-compose retires a redesign case the same way it retires the Visual it concerns.**
`compositor._retire_previous_report` already retires every previous `Visual`; an
`ExceptionCase` referencing one of them is retired alongside it (`patterns.py`'s own
"retiring the parent, react to the dependent" cascade, S5.5.x) -- not *closed*, since
retiring implies nothing about whether an engineer ever made a decision. If the recompose
still produces a redesign flag for the same sheet, a fresh case opens against the fresh
Visual; no work is silently lost, the same "an edit is a new version" footing every other
recompose-shaped write in this codebase already has.

**"Cannot enter PROVING for the affected sheets" is a real, callable, currently-uncalled
check** -- the identical posture `report_deploy.py`'s own `deploy_state` proxy and every
other MU-shaped gap in this codebase already discloses: no real Migration Unit node or §3.2
state machine exists anywhere (`migration_units.py`'s own registry has no state-transition
method at all, confirmed a seventh time), and E7's Arbiter -- the only thing that would ever
call this -- does not exist either. `can_enter_proving` is written as a real, honest
function over real data (live `Visual.redesign_flag`/open `ExceptionCase` state) so that the
day an Arbiter exists, it finds a real answer waiting rather than a gap to fill from
scratch. §3.2's own state table only expresses this at whole-MU grain (`BLOCKED`); the AC's
own "for the affected sheets... other sheets proceed" is explicitly per-sheet, finer than
the spec's own table -- this function answers at that finer grain, a real design choice
disclosed here rather than forced into the coarser state name.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

import asyncpg

from .artefacts import ArtefactStore
from .errors import ElementNotFoundError
from .graph.queries import NODE_INDEX_TABLE
from .ids import new_ulid
from .lineage import children, hydrate
from .principal import Principal
from .writes import GraphWriter, NodeWrite

REDESIGN_CLASS = "VISUAL_REDESIGN"

#: The one artefact kind a redesign screenshot would ever be stored under (S2.4.2).
SCREENSHOT_KIND = "visual_capture"


class RedesignExceptionError(Exception):
    """A redesign exception cannot be opened or closed as asked."""


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")


async def find_screenshot_ref(
    artefact_store: ArtefactStore | None, *, workbook_id: str, worksheet_name: str
) -> str | None:
    """An existing `visual_capture` artefact for this workbook whose own `case_id` names
    this worksheet, if one has ever been uploaded. `None` when no store is configured or
    none is found -- both are honest, not an error (see the module's own docstring)."""
    if artefact_store is None:
        return None
    records = await artefact_store.for_mu(workbook_id, kind=SCREENSHOT_KIND)
    for record in records:
        if record.case_id == worksheet_name:
            return record.id
    return None


async def open_redesign_exception(
    writer: GraphWriter,
    *,
    workbook_id: str,
    visual_id: str,
    mapping_reason: str,
    placeholder_location: dict[str, Any],
    screenshot_ref: str | None,
    principal: Principal,
) -> str:
    """A real `ExceptionCase(class=VISUAL_REDESIGN)`, `state="OPEN"` -- "routed to the
    Exception Desk" per the module's own docstring."""
    case_id = new_ulid()
    await writer.write_nodes(
        [
            NodeWrite(
                type="ExceptionCase",
                id=case_id,
                properties={
                    "mu_ref": workbook_id,
                    "class": REDESIGN_CLASS,
                    "state": "OPEN",
                    "visual_ref": visual_id,
                    "mapping_reason": mapping_reason,
                    "placeholder_location": placeholder_location,
                    "screenshot_ref": screenshot_ref,
                },
            )
        ],
        principal=principal,
    )
    return case_id


async def close_redesign_exception(
    pool: asyncpg.Pool,
    graph_name: str,
    writer: GraphWriter,
    *,
    case_id: str,
    desktop_commit_hash: str,
    principal: Principal,
) -> dict[str, Any]:
    """"Closing the flag records the engineer, the Desktop commit hash and the date" --
    the identical `*_by`/`*_at` shape `CalculatedField.redesign_decision_by`/`.
    redesign_decision_at` (S5.4.1) already set for a comparable closing action."""
    cleaned = desktop_commit_hash.strip()
    if not cleaned:
        raise RedesignExceptionError(
            "a Desktop commit hash is required to close a redesign exception -- it is the "
            "record of where the finished work actually landed"
        )

    async with pool.acquire() as conn:
        case = (await hydrate(conn, graph_name, "ExceptionCase", [case_id])).get(case_id)
    if case is None:
        raise ElementNotFoundError(f"no ExceptionCase '{case_id}'")
    if case.get("class") != REDESIGN_CLASS:
        raise RedesignExceptionError(
            f"ExceptionCase '{case_id}' is class {case.get('class')!r}, not "
            f"{REDESIGN_CLASS!r} -- closing with a Desktop commit hash only applies to a "
            f"visual redesign case"
        )
    if case.get("state") == "CLOSED":
        raise RedesignExceptionError(f"ExceptionCase '{case_id}' is already closed")

    updated = await writer.set_node_properties(
        case_id,
        {
            "state": "CLOSED",
            "closed_by": principal.value,
            "closed_at": _now(),
            "desktop_commit_hash": cleaned,
        },
        principal=principal,
    )
    properties: dict[str, Any] = updated["properties"]
    return properties


@dataclass(frozen=True, slots=True)
class ProvingReadiness:
    """Which of a workbook's own worksheets may enter PROVING today, and which are
    blocked by an open VISUAL_REDESIGN case against their own Visual."""

    workbook_id: str
    ready_worksheet_ids: tuple[str, ...]
    blocked_worksheet_ids: tuple[str, ...]

    @property
    def fully_blocked(self) -> bool:
        return bool(self.blocked_worksheet_ids) and not self.ready_worksheet_ids

    def as_dict(self) -> dict[str, Any]:
        return {
            "workbook_id": self.workbook_id,
            "ready_worksheet_ids": list(self.ready_worksheet_ids),
            "blocked_worksheet_ids": list(self.blocked_worksheet_ids),
            "fully_blocked": self.fully_blocked,
        }


async def can_enter_proving(pool: asyncpg.Pool, graph_name: str, workbook_id: str) -> ProvingReadiness:
    async with pool.acquire() as conn:
        worksheet_map = await children(conn, graph_name, [workbook_id], "CONTAINS", "Worksheet")
        worksheet_ids = sorted(worksheet_map.get(workbook_id, set()))

        visual_map = await children(conn, graph_name, worksheet_ids, "MAPS_TO", "Visual")
        visual_to_worksheet = {
            visual_id: worksheet_id
            for worksheet_id, visual_ids in visual_map.items()
            for visual_id in visual_ids
        }

        rows = await conn.fetch(
            f"""SELECT id FROM {NODE_INDEX_TABLE}
             WHERE graph = $1 AND kind = 'node' AND label = 'ExceptionCase' AND retired_at IS NULL""",
            graph_name,
        )
        cases = await hydrate(conn, graph_name, "ExceptionCase", [row["id"] for row in rows])

    blocked: set[str] = set()
    for case in cases.values():
        visual_ref = case.get("visual_ref")
        if (
            case.get("class") == REDESIGN_CLASS
            and case.get("state") == "OPEN"
            and case.get("mu_ref") == workbook_id
            and isinstance(visual_ref, str)
            and visual_ref in visual_to_worksheet
        ):
            blocked.add(visual_to_worksheet[visual_ref])
    blocked_worksheet_ids = sorted(blocked)
    ready_worksheet_ids = sorted(set(worksheet_ids) - set(blocked_worksheet_ids))
    return ProvingReadiness(
        workbook_id=workbook_id,
        ready_worksheet_ids=tuple(ready_worksheet_ids),
        blocked_worksheet_ids=tuple(blocked_worksheet_ids),
    )


async def retire_exceptions_for_visuals(
    writer: GraphWriter, pool: asyncpg.Pool, graph_name: str, visual_ids: list[str], *, principal: Principal
) -> None:
    """Retires every live `ExceptionCase` referencing one of `visual_ids` -- called
    alongside retiring the visuals themselves on a recompose (see the module's own
    docstring on why this is a retirement, not a close)."""
    if not visual_ids:
        return
    wanted = set(visual_ids)
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            f"""SELECT id FROM {NODE_INDEX_TABLE}
             WHERE graph = $1 AND kind = 'node' AND label = 'ExceptionCase' AND retired_at IS NULL""",
            graph_name,
        )
        cases = await hydrate(conn, graph_name, "ExceptionCase", [row["id"] for row in rows])

    for case_id, properties in cases.items():
        if properties.get("visual_ref") in wanted:
            await writer.retire_node(
                case_id, reason="superseded by a fresh compose of this workbook", principal=principal
            )


__all__ = [
    "REDESIGN_CLASS",
    "SCREENSHOT_KIND",
    "ProvingReadiness",
    "RedesignExceptionError",
    "can_enter_proving",
    "close_redesign_exception",
    "find_screenshot_ref",
    "open_redesign_exception",
    "retire_exceptions_for_visuals",
]
