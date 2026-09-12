"""Routing a real model defect to the Foundry -- story S8.2.2, continuing F8.2/E8.

    "As a model engineer, I want a failure diagnosed as a model defect to be routed to
    the Foundry, not patched in the report, so that the fix lands where the cause is.

    Acceptance criteria:
    - KEY_MISSING with graph evidence of a missing dimension member, or AGGREGATION with
      a grain mismatch at the model, opens a Foundry change request and sets the MU
      BLOCKED on the family
    - The Mender never edits TMDL directly in R1"

§11.3's own worked decision text, verbatim, is the mechanism this module makes real:
*"model defect (route to the Foundry as a change to the family; the MU returns to
BLOCKED)."* §11.1's own KEY_MISSING row gives the same instruction as a fix path:
*"Model repair via Foundry (not per report)."*

**This narrows the AC's own two triggers to what real graph facts can actually confirm
-- disclosed, not silently guessed at.** Neither "a missing dimension member" nor "a
grain mismatch at the model" is a signal `classification.py`'s own §11.1 classifier
already computes (its `KEY_MISSING`/`AGGREGATION` signals are `{missing_keys, extra_keys}`
counts and `{totals_fail, row_count_within_tolerance}` respectively -- neither carries any
model-side fact at all, confirmed by direct reading). Both checks here are new, and both
are built from real, already-existing graph facts, never a live target connection this
platform does not have (no Fabric tenant is configured anywhere it has ever been
deployed -- see `target_setup.py`'s own docstring):

- **KEY_MISSING -> "a missing dimension member"** is read as: at least one of the
  failing case's own grain fields carries no real `Field -> ModelTable` `MAPS_TO`
  binding at all (`case_execution._table_map_for_sheet`, reused verbatim, the identical
  "honestly empty in every real deployment today" fact `case_execution.py`'s own
  docstring already discloses six times over). A dimension the model never wired to a
  table cannot carry *any* of that dimension's own members, on the target side, for any
  case that uses it -- a real, structural, model-level absence, not a report-side repair
  a Pattern or a DAX rewrite could ever fix. This is honestly narrower than a literal
  reading of "a missing member": this platform's own graph never harvests member-level
  *data* (a specific value like "EMEA") at all, only schema -- so "the model has no home
  for this whole dimension" is the strongest real evidence available, not a guess at
  which member.

- **AGGREGATION -> "a grain mismatch at the model"** is read as: the failing case's own
  grain names a dimension field the case's own `ModelFamily.grain` does not -- the
  family's own real "candidate grain inferred from member sheets, confirmed at G2"
  (`cartographer.candidate_grain`, the identical comma-joined field-name list
  `cartographer._family_summary` already parses back on read, mirrored here). When a
  report's own grain needs a dimension the model's own chosen grain never named, the
  model itself is coarser than the report -- a real, structural, model-level fact, not a
  measure-level repair.

**Resolving "the family" is a new, real reverse lookup this platform never needed
before.** `ExceptionCase.mu_ref` names a workbook; every prior family read
(`cartographer.get_family`) already goes family -> members, never the other way.
`_family_for_workbook` walks the real `IN_FAMILY` edge (`workbook --IN_FAMILY--> family`)
in reverse -- `None`, honestly, when no Cartographer run has ever clustered this workbook
into a family at all, which correctly means neither AC trigger can be checked (nothing
real to compare against), so the case falls through to whatever S8.2.1 already does for
its own class.

**"Opens a Foundry change request" reuses `model_lifecycle.request_new_version`
verbatim, S4.3.3's own already-shipped mechanism -- not a new one.** Its own docstring
already anticipated this exact caller: *"a Mender repair or a design change does not
regress what is live."* It only succeeds when the family's own current version is
`PUBLISHED`; when it is not (already `DRAFT`/`IN_REVIEW`/`APPROVED`/`BUILT` -- a change
is already in flight, or nothing has ever been published), no second, colliding change
request is opened -- the exception is still marked BLOCKED on the family, honestly
disclosed as joining whatever is already in progress rather than a fabricated new one.

**"Sets the MU BLOCKED on the family" is `ExceptionCase.state = "BLOCKED"`.** No real
Migration Unit node or §3.2 state machine exists anywhere in this codebase (confirmed,
the same finding ADR 0041/0048/0055/0060/0061 have each already made independently) --
`ExceptionCase` is the one real work-item mechanism this platform has for an MU's own
failing state, the identical choice §11.1 classification (S8.1.1) and the bounded repair
loop (S8.2.1) already made. Unlike ADR 0041's own disclosed proxy (the *absence* of a
property standing in for BLOCKED), `state` is a plain, unconstrained `T.STRING`
(confirmed: no enum) -- so `"BLOCKED"` is written directly and literally, a real,
positive fact rather than an inferred one.

**The Mender never edits TMDL directly in R1 -- confirmed, not merely assumed.**
`mender.py` imports nothing from `target_contract`/`tmdl` anywhere (`grep`-confirmed);
this module's own `route_to_foundry` calls `request_new_version` alone, which itself
never calls `emit_tmdl`/`TargetAdapter.commit` either -- only `build.build_family` (the
Semantic Model Engineer's own separate, later, explicitly-triggered action) ever emits
TMDL. The backlog's own §7.3 open question ("should the Mender be allowed to edit TMDL
under L2 in R1... current answer: Foundry-only in R1") and §6.3's own R1.1 roadmap
("Mender TMDL edits under L2") confirm this AC is drawing a real boundary against a
named, already-discussed, explicitly-deferred capability -- not merely restating
something no design ever considered.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import asyncpg

from .case_execution import _table_map_for_sheet  # cross-epic private helper; see module docstring
from .errors import InvalidRequestError
from .graph.queries import EDGE_INDEX_TABLE
from .lineage import hydrate
from .model_lifecycle import request_new_version
from .principal import Principal
from .writes import GraphWriter

#: §11.2/§8.10's own trigger classes -- the only two §11.1 classes the AC names as ever
#: routable to the Foundry. Every other class stays exactly S8.2.1's own report-side
#: repair loop, unchanged.
MODEL_DEFECT_CLASSES = frozenset({"KEY_MISSING", "AGGREGATION"})


@dataclass(frozen=True, slots=True)
class ModelDefectEvidence:
    """Real, checkable graph evidence that a failure is a model defect, not a report
    one -- see this module's own docstring for exactly what each class's own evidence
    means and how it was derived."""

    family_id: str
    reason: str
    signals: dict[str, Any]

    def as_dict(self) -> dict[str, Any]:
        return {"family_id": self.family_id, "reason": self.reason, "signals": self.signals}


async def _family_for_workbook(pool: asyncpg.Pool, graph_name: str, workbook_id: str) -> str | None:
    """The real `ModelFamily` a workbook's own `IN_FAMILY` edge names -- the reverse of
    every existing family read, which only ever goes family -> members. `None` when no
    Cartographer run has ever clustered this workbook (an honest, real absence, not an
    error): nothing exists yet to check either AC trigger against.

    **`ORDER BY created_at DESC` is a deliberate, real fix, not decoration.** A real bug,
    found live (S9.2.1's own smoke test): `cartographer.Cartographer.run()` used to
    retire only the stale `ModelFamily` node on a re-cluster, never the re-clustered
    member's own prior `IN_FAMILY` edge (`retire_node` has no cascade to edges pointing
    at the node -- confirmed directly), leaving some workbooks with two live edges after
    a second clustering run. Fixed at the source in `cartographer.py`, but this read
    stays deterministic on its own -- for a workbook a pre-fix run already corrupted, or
    any future write path that fails to retire-before-write, this always resolves to the
    most recently created live edge rather than an arbitrary one Postgres happens to
    return first."""
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            f"""SELECT to_id AS family_id FROM {EDGE_INDEX_TABLE}
                 WHERE graph = $1 AND label = 'IN_FAMILY' AND from_id = $2 AND retired_at IS NULL
                 ORDER BY created_at DESC
                 LIMIT 1""",
            graph_name, workbook_id,
        )
    return str(row["family_id"]) if row is not None else None


async def _detect_missing_dimension_member(
    pool: asyncpg.Pool, graph_name: str, *, family_id: str, case_refs: tuple[str, ...],
) -> ModelDefectEvidence | None:
    """KEY_MISSING's own model-defect trigger -- see this module's own docstring for the
    disclosed reading. Checks each of the exception's own real cases in turn, returning
    the first with a real, unmapped grain field."""
    async with pool.acquire() as conn:
        cases = await hydrate(conn, graph_name, "ParityCase", list(case_refs))
    for case_id, properties in cases.items():
        sheet_ref = str(properties.get("sheet_ref") or "")
        grain = tuple(properties.get("grain") or ())
        if not sheet_ref or not grain:
            continue
        async with pool.acquire() as conn:
            table_map = await _table_map_for_sheet(conn, graph_name, sheet_ref, grain)
        unmapped = tuple(field for field in grain if field not in table_map)
        if unmapped:
            return ModelDefectEvidence(
                family_id=family_id,
                reason=(
                    f"case {case_id}'s own grain names dimension field(s) "
                    f"{', '.join(unmapped)} with no real Field -> ModelTable binding at "
                    f"all -- the model itself has no home for this dimension (§11.1's "
                    f"own KEY_MISSING fix path: 'model repair via Foundry, not per "
                    f"report')"
                ),
                signals={"case_id": case_id, "unmapped_dimension_fields": list(unmapped)},
            )
    return None


def _family_grain_fields(family_properties: dict[str, Any]) -> frozenset[str]:
    """`ModelFamily.grain`'s own comma-joined field-name list, parsed back -- the
    identical read `cartographer._family_summary` already performs, mirrored rather
    than imported (a private helper of a different epic's own module)."""
    raw = str(family_properties.get("grain") or "")
    return frozenset(part.strip() for part in raw.split(",") if part.strip())


async def _detect_model_grain_mismatch(
    pool: asyncpg.Pool, graph_name: str, *, family_id: str, case_refs: tuple[str, ...],
) -> ModelDefectEvidence | None:
    """AGGREGATION's own model-defect trigger -- see this module's own docstring for the
    disclosed reading. `None` when the family carries no real candidate grain yet (no
    Cartographer run has confirmed one) -- honestly nothing to compare against."""
    async with pool.acquire() as conn:
        families = await hydrate(conn, graph_name, "ModelFamily", [family_id])
    family_properties = families.get(family_id)
    if family_properties is None:
        return None
    family_grain = _family_grain_fields(family_properties)
    if not family_grain:
        return None

    async with pool.acquire() as conn:
        cases = await hydrate(conn, graph_name, "ParityCase", list(case_refs))
    for case_id, properties in cases.items():
        case_grain = frozenset(properties.get("grain") or ())
        extra = case_grain - family_grain
        if extra:
            return ModelDefectEvidence(
                family_id=family_id,
                reason=(
                    f"case {case_id}'s own grain names {', '.join(sorted(extra))}, not "
                    f"part of the family's own candidate grain "
                    f"({', '.join(sorted(family_grain)) or '(none)'}) -- the model's own "
                    f"grain is coarser than what this report needs"
                ),
                signals={
                    "case_id": case_id, "case_grain": sorted(case_grain),
                    "family_grain": sorted(family_grain), "grain_only_on_case": sorted(extra),
                },
            )
    return None


async def detect_model_defect(
    pool: asyncpg.Pool,
    graph_name: str,
    *,
    failure_class: str,
    workbook_id: str,
    case_refs: tuple[str, ...],
) -> ModelDefectEvidence | None:
    """`None` for any class outside `MODEL_DEFECT_CLASSES`, for a workbook with no real
    family yet, or when the class-specific check finds no real evidence -- every `None`
    is a real, honest "nothing to route on", never a default the caller should treat as
    a negative proof."""
    if failure_class not in MODEL_DEFECT_CLASSES or not case_refs:
        return None
    family_id = await _family_for_workbook(pool, graph_name, workbook_id)
    if family_id is None:
        return None
    if failure_class == "KEY_MISSING":
        return await _detect_missing_dimension_member(
            pool, graph_name, family_id=family_id, case_refs=case_refs,
        )
    return await _detect_model_grain_mismatch(
        pool, graph_name, family_id=family_id, case_refs=case_refs,
    )


async def route_to_foundry(
    pool: asyncpg.Pool,
    graph_name: str,
    writer: GraphWriter,
    *,
    exception_case_id: str,
    evidence: ModelDefectEvidence,
    principal: Principal,
) -> dict[str, Any]:
    """§11.3's own worked decision, made real: a Foundry change request when the family
    is `PUBLISHED`, and the exception marked BLOCKED on the family either way -- see
    this module's own docstring for the full reasoning on both outcomes."""
    async with pool.acquire() as conn:
        families = await hydrate(conn, graph_name, "ModelFamily", [evidence.family_id])
    family_properties = families.get(evidence.family_id)
    family_state = family_properties.get("state") if family_properties else None

    # The evidence sentence alone is always well over MIN_CHANGE_REQUEST_REASON
    # characters, so `request_new_version`'s own reason-length check never fires here.
    reason = f"Mender-detected model defect on ExceptionCase {exception_case_id}: {evidence.reason}"

    change_request: dict[str, Any] | None = None
    foundry_request_ref: str | None = None
    detail_note: str | None = None
    if family_state == "PUBLISHED":
        try:
            change_request = await request_new_version(
                pool, graph_name, writer, evidence.family_id, reason=reason, principal=principal,
            )
        except InvalidRequestError as exc:
            # A real race (another caller moved the family off PUBLISHED between the read
            # above and this call) -- the identical honest fallback the "already in
            # progress" branch below gives, not a crash over a lost race.
            change_request = None
            detail_note = f"could not open a change request: {exc}"
        else:
            foundry_request_ref = change_request["semantic_model_id"]
    else:
        detail_note = (
            f"family is {family_state!r}, not PUBLISHED -- no new change-request version "
            f"opened; this exception joins whatever is already in progress on the family"
        )

    outcome = "ROUTED_TO_FOUNDRY" if foundry_request_ref else "ALREADY_IN_FOUNDRY"

    properties: dict[str, Any] = {
        "state": "BLOCKED", "family_ref": evidence.family_id, "decision": "MODEL_DEFECT_FOUNDRY",
    }
    if foundry_request_ref:
        properties["foundry_request_ref"] = foundry_request_ref
    await writer.set_node_properties(exception_case_id, properties, principal=principal)

    return {
        "outcome": outcome, "family_id": evidence.family_id, "family_state": family_state,
        "foundry_request_ref": foundry_request_ref, "change_request": change_request,
        "detail": detail_note,
    }


__all__ = [
    "MODEL_DEFECT_CLASSES",
    "ModelDefectEvidence",
    "detect_model_defect",
    "route_to_foundry",
]
