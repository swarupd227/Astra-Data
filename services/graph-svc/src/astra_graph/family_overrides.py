"""Split, merge and move — a human's edits to the Cartographer's proposal. Story S3.1.2.

    "I want to split, merge and move workbooks between families with a reason, so that the
    proposal is a starting point I control."

Three operations, one property in common: every one marks every family it touches
``overridden = True``, which is what ``Cartographer.run`` (S3.1.1) reads to leave a family
alone on the next re-cluster — see that module's own docstring and ADR 0023 for the "reports
what it would change" half of this story's second criterion.

Grain and evidence are recomputed after every operation from the same functions S3.1.1's
Cartographer uses (``candidate_grain``, ``family_evidence``), reading reach for only the
workbooks the operation touches (``gather_reach``) rather than the whole estate — an override
changes a handful of families, not the estate's clustering, and re-reading everything to
answer a question about three workbooks would make an edit slower the larger the estate got
for a reason that has nothing to do with the edit.

**A workbook belongs to at most one family at a time.** Moving one means retiring its current
``IN_FAMILY`` edge and writing a new one: an edge's endpoints are fixed at creation in a
property graph, so "move" cannot be a property update — it is retire-and-recreate, which is
S3.1.2's reason for edge retirement existing at all (v0013).

**Result states are always PROPOSED, never SINGLETON.** SINGLETON is §12.1's own label for
what the *algorithm* does when it cannot responsibly grow a family; a human composing a
two-member family on purpose is not the same situation, and re-deriving that label here would
quietly second-guess a decision this module has no business second-guessing.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

import asyncpg

from .cartographer import FamilyProposal, Gathered, candidate_grain, family_evidence, gather_reach
from .errors import ElementNotFoundError, InvalidRequestError
from .graph.queries import EDGE_INDEX_TABLE, NODE_INDEX_TABLE
from .ids import new_ulid
from .lineage import hydrate
from .principal import Principal
from .writes import EdgeWrite, GraphWriter, NodeWrite

#: A reason must say something. The same floor ``retire_node``/``retire_edge`` already hold
#: writes to (``writes.MIN_RETIREMENT_REASON_LENGTH``); named again here because an override
#: reason is a different property (``ModelFamily.override_reason``), not a retirement one.
MIN_OVERRIDE_REASON_LENGTH = 8


@dataclass(frozen=True, slots=True)
class MoveResult:
    target: FamilyProposal
    source: FamilyProposal | None
    """``None`` when moving the last member emptied the source family — it was retired,
    not updated."""


def _validate_reason(reason: str) -> str:
    cleaned = reason.strip()
    if len(cleaned) < MIN_OVERRIDE_REASON_LENGTH:
        raise InvalidRequestError(
            f"an override needs a reason of at least {MIN_OVERRIDE_REASON_LENGTH} "
            f"characters; it is the record of why a model engineer changed the "
            f"Cartographer's proposal"
        )
    return cleaned


def _proposal(
    family_id: str,
    members: Sequence[str] | set[str],
    *,
    gathered: Gathered,
    action: str,
    reason: str,
) -> FamilyProposal:
    ordered = tuple(sorted(members))
    member_sheets = [s for m in ordered for s in gathered.sheets.get(m, set())]
    grain = candidate_grain(gathered.sheet_dimensions.get(s, frozenset()) for s in member_sheets)
    evidence = family_evidence(
        ordered, gathered.tables_of, gathered.fields_of, gathered.shapes_of
    )
    return FamilyProposal(
        id=family_id,
        name=_name(ordered, gathered.workbook_names),
        state="PROPOSED",
        members=ordered,
        grain=grain,
        evidence=evidence,
        reason=None,
        overridden=True,
        override_action=action,
        override_reason=reason,
    )


def _name(members: tuple[str, ...], workbook_names: Mapping[str, dict[str, Any]]) -> str:
    named = sorted(str(workbook_names.get(m, {}).get("name", m)) for m in members)
    if len(named) == 1:
        return f"{named[0]} (singleton)"
    return f"{named[0]} + {len(named) - 1} more"


def _override_properties(proposal: FamilyProposal) -> dict[str, Any]:
    """The write-time property set for a proposal ``_proposal`` already stamped with its
    override metadata — one source of truth, so what a caller reads back (the returned
    ``FamilyProposal``) is never out of step with what actually landed on the node."""
    return {
        "name": proposal.name,
        "state": proposal.state,
        "grain": ", ".join(proposal.grain),
        "conformed_dims": [],
        "evidence_shared_tables": list(proposal.evidence.shared_tables),
        "evidence_shared_fields": list(proposal.evidence.shared_fields),
        "evidence_shared_calc_shapes": proposal.evidence.shared_calc_shapes,
        "overridden": proposal.overridden,
        "override_action": proposal.override_action,
        "override_reason": proposal.override_reason,
    }


async def _family_properties_by_id(
    pool: asyncpg.Pool, graph_name: str, family_ids: Sequence[str]
) -> dict[str, dict[str, Any]]:
    async with pool.acquire() as conn:
        return await hydrate(conn, graph_name, "ModelFamily", family_ids)


async def _members_of(
    pool: asyncpg.Pool, graph_name: str, family_ids: Sequence[str]
) -> dict[str, list[str]]:
    if not family_ids:
        return {}
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            f"""
            SELECT e.from_id AS workbook, e.to_id AS family
            FROM {EDGE_INDEX_TABLE} e
            JOIN {NODE_INDEX_TABLE} n ON n.id = e.from_id AND n.kind = 'node'
             AND n.graph = $1 AND n.label = 'Workbook' AND n.retired_at IS NULL
            WHERE e.graph = $1 AND e.label = 'IN_FAMILY' AND e.to_id = ANY($2::text[])
              AND e.retired_at IS NULL
            """,
            graph_name,
            list(family_ids),
        )
    out: dict[str, list[str]] = {}
    for row in rows:
        out.setdefault(row["family"], []).append(row["workbook"])
    return out


async def _current_family_edge(
    pool: asyncpg.Pool, graph_name: str, workbook_id: str
) -> tuple[str, str] | None:
    """The id and target of a workbook's current (live) ``IN_FAMILY`` edge, if it has one."""
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            f"""
            SELECT e.id, e.to_id AS family
            FROM {EDGE_INDEX_TABLE} e
            WHERE e.graph = $1 AND e.label = 'IN_FAMILY' AND e.from_id = $2
              AND e.retired_at IS NULL
            """,
            graph_name,
            workbook_id,
        )
    return (row["id"], row["family"]) if row is not None else None


async def _edge_to(
    pool: asyncpg.Pool, graph_name: str, workbook_id: str, family_id: str
) -> str | None:
    async with pool.acquire() as conn:
        found = await conn.fetchval(
            f"""
            SELECT e.id FROM {EDGE_INDEX_TABLE} e
            WHERE e.graph = $1 AND e.label = 'IN_FAMILY' AND e.from_id = $2 AND e.to_id = $3
              AND e.retired_at IS NULL
            """,
            graph_name,
            workbook_id,
            family_id,
        )
    return str(found) if found is not None else None


async def _relink(
    writer: GraphWriter,
    *,
    old_edge_id: str | None,
    workbook_id: str,
    to_family_id: str,
    retire_reason: str,
    principal: Principal,
) -> None:
    if old_edge_id is not None:
        await writer.retire_edge(old_edge_id, reason=retire_reason, principal=principal)
    await writer.write_edge(
        EdgeWrite(
            type="IN_FAMILY",
            id=new_ulid(),
            from_id=workbook_id,
            to_id=to_family_id,
            properties={"confidence": 1.0},
        ),
        principal=principal,
    )


async def split_family(
    pool: asyncpg.Pool,
    graph_name: str,
    writer: GraphWriter,
    *,
    family_id: str,
    member_ids: Sequence[str],
    reason: str,
    principal: Principal,
) -> tuple[FamilyProposal, FamilyProposal]:
    """Select members out of ``family_id`` into a new family.

    Returns ``(remainder, new_family)``. The remainder keeps ``family_id`` — it is the same
    family, smaller — and the selected members' family is new, because it was not one
    before this call.
    """
    reason = _validate_reason(reason)
    properties = await _family_properties_by_id(pool, graph_name, [family_id])
    if family_id not in properties:
        raise ElementNotFoundError(f"no model family '{family_id}'")

    current = set((await _members_of(pool, graph_name, [family_id])).get(family_id, []))
    selected = set(member_ids)
    if not selected:
        raise InvalidRequestError("split needs at least one member to move into the new family")
    unknown = selected - current
    if unknown:
        raise InvalidRequestError(
            f"{sorted(unknown)} are not members of family '{family_id}'"
        )
    remainder_members = current - selected
    if not remainder_members:
        raise InvalidRequestError(
            "split cannot select every member — that is a rename, not a split; merge or "
            "edit the family directly instead"
        )

    gathered = await gather_reach(pool, graph_name, sorted(current))
    new_family_id = new_ulid()
    remainder = _proposal(
        family_id, remainder_members, gathered=gathered, action="SPLIT", reason=reason
    )
    new_family = _proposal(
        new_family_id, selected, gathered=gathered, action="SPLIT", reason=reason
    )

    # The new family's node must exist before an IN_FAMILY edge can point at it — an edge
    # write is refused when either endpoint is not yet in the graph.
    await writer.upsert_nodes(
        [
            NodeWrite(
                type="ModelFamily",
                id=family_id,
                properties=_override_properties(remainder),
            )
        ],
        principal=principal,
    )
    await writer.write_nodes(
        [
            NodeWrite(
                type="ModelFamily",
                id=new_family_id,
                properties=_override_properties(new_family),
            )
        ],
        principal=principal,
    )

    for member in sorted(selected):
        old_edge_id = await _edge_to(pool, graph_name, member, family_id)
        await _relink(
            writer,
            old_edge_id=old_edge_id,
            workbook_id=member,
            to_family_id=new_family_id,
            retire_reason=f"split into a new family: {reason}",
            principal=principal,
        )

    return remainder, new_family


async def merge_families(
    pool: asyncpg.Pool,
    graph_name: str,
    writer: GraphWriter,
    *,
    family_ids: tuple[str, str],
    reason: str,
    principal: Principal,
) -> FamilyProposal:
    """Combine two families into one fresh family; retire both originals."""
    reason = _validate_reason(reason)
    left_id, right_id = family_ids
    if left_id == right_id:
        raise InvalidRequestError("cannot merge a family with itself")

    properties = await _family_properties_by_id(pool, graph_name, [left_id, right_id])
    missing = {left_id, right_id} - properties.keys()
    if missing:
        raise ElementNotFoundError(f"no model family '{sorted(missing)[0]}'")

    members_by_family = await _members_of(pool, graph_name, [left_id, right_id])
    combined = {m for members in members_by_family.values() for m in members}
    if not combined:
        raise InvalidRequestError("both families are empty; there is nothing to merge")

    gathered = await gather_reach(pool, graph_name, sorted(combined))
    merged_id = new_ulid()
    merged = _proposal(merged_id, combined, gathered=gathered, action="MERGE", reason=reason)

    # The merged family's node must exist before any IN_FAMILY edge can point at it.
    await writer.write_nodes(
        [
            NodeWrite(
                type="ModelFamily",
                id=merged_id,
                properties=_override_properties(merged),
            )
        ],
        principal=principal,
    )

    for source_id in (left_id, right_id):
        for member in sorted(members_by_family.get(source_id, [])):
            old_edge_id = await _edge_to(pool, graph_name, member, source_id)
            await _relink(
                writer,
                old_edge_id=old_edge_id,
                workbook_id=member,
                to_family_id=merged_id,
                retire_reason=f"merged into a combined family: {reason}",
                principal=principal,
            )
        await writer.retire_node(
            source_id, reason=f"merged into a combined family: {reason}", principal=principal
        )

    return merged


async def move_member(
    pool: asyncpg.Pool,
    graph_name: str,
    writer: GraphWriter,
    *,
    workbook_id: str,
    to_family_id: str,
    reason: str,
    principal: Principal,
) -> MoveResult:
    """Move one workbook into ``to_family_id``, out of whichever family it is in now."""
    reason = _validate_reason(reason)
    target_properties = await _family_properties_by_id(pool, graph_name, [to_family_id])
    if to_family_id not in target_properties:
        raise ElementNotFoundError(f"no model family '{to_family_id}'")

    current_edge = await _current_family_edge(pool, graph_name, workbook_id)
    if current_edge is None:
        raise InvalidRequestError(
            f"workbook '{workbook_id}' is not currently a member of any family, so there "
            f"is nothing to move — add it with a split into '{to_family_id}' instead"
        )
    old_edge_id, from_family_id = current_edge
    if from_family_id == to_family_id:
        raise InvalidRequestError(
            f"workbook '{workbook_id}' is already a member of '{to_family_id}'"
        )

    members_by_family = await _members_of(pool, graph_name, [from_family_id, to_family_id])
    source_members = set(members_by_family.get(from_family_id, [])) - {workbook_id}
    target_members = set(members_by_family.get(to_family_id, [])) | {workbook_id}

    await _relink(
        writer,
        old_edge_id=old_edge_id,
        workbook_id=workbook_id,
        to_family_id=to_family_id,
        retire_reason=f"moved to another family: {reason}",
        principal=principal,
    )

    gathered = await gather_reach(
        pool, graph_name, sorted(source_members | target_members | {workbook_id})
    )
    target = _proposal(
        to_family_id, target_members, gathered=gathered, action="MOVE", reason=reason
    )
    await writer.upsert_nodes(
        [
            NodeWrite(
                type="ModelFamily",
                id=to_family_id,
                properties=_override_properties(target),
            )
        ],
        principal=principal,
    )

    if not source_members:
        await writer.retire_node(
            from_family_id,
            reason=f"emptied by moving its last member: {reason}",
            principal=principal,
        )
        return MoveResult(target=target, source=None)

    source = _proposal(
        from_family_id, source_members, gathered=gathered, action="MOVE", reason=reason
    )
    await writer.upsert_nodes(
        [
            NodeWrite(
                type="ModelFamily",
                id=from_family_id,
                properties=_override_properties(source),
            )
        ],
        principal=principal,
    )
    return MoveResult(target=target, source=source)


__all__ = [
    "MIN_OVERRIDE_REASON_LENGTH",
    "MoveResult",
    "merge_families",
    "move_member",
    "split_family",
]
