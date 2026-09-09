"""The Exception Desk -- story S8.3.1, opening F8.3, continuing E8.

    "As a migration engineer, I want a queue of ExceptionCases ordered by train sequence,
    with the evidence bundle in the case, so that I never open Tableau to work out what
    an exception is.

    Acceptance criteria:
    - Queue columns: MU, failure class, passes consumed, train, age, assignee; filters
      by train, class, site, assignee; bulk assign
    - Case page: evidence (failing cells, key diffs, filter context, parameter values),
      artefact (current DAX / M with source calc alongside, Mender pass history with
      diffs), decision
    - Decisions: patch (edit in place, validate, re-prove), redesign (route to Foundry
      or open in Desktop with the MU link), model defect (Foundry change request),
      source defect (record, notify owner, choose reproduce or fix with owner sign-off
      -- G3 matter)
    - Every decision is a GateDecision-class record with rationale of at least one
      sentence and is visible to the report owner"

§11.3 itself, verbatim: *"Escalated cases become ExceptionCases and appear in the
Exception Desk, which is the Migration Engineer's work queue. There is no separate
defect tracker. Each ExceptionCase carries the full evidence bundle, the Mender's pass
history, the current artefact and its source calc, and the model's diagnosis where one
was made. The engineer's decision is one of: patch (edit the artefact; re-prove),
redesign (Class 4; agree with the report owner; finish in Desktop; re-prove the rest of
the report; waiver the redesigned visual's case with justification), model defect
(route to the Foundry as a change to the family; the MU returns to BLOCKED), or source
defect (the Tableau report was wrong; record, inform the owner, and either reproduce the
defect faithfully or fix it with the owner's written agreement -- the choice is a G3
matter). Every decision is a record and every patch is a Pattern candidate."*

**"The queue" is a real read over `ExceptionCase`, not a new store.** §11.3's own words:
"there is no separate defect tracker." Every OPEN/BLOCKED `ExceptionCase` already exists
(S8.1.1 opens them, S8.2.1/S8.2.2 escalate or route some of them); this module reads and
enriches them, never writes a second record of "what is in the queue."

**"Train sequence" resolves through the real `IN_TRAIN` edge -- confirmed no such reverse
lookup existed before this story.** `ExceptionCase.mu_ref` names a workbook (ADR 0060's
own finding: "not one of its many measures"); every existing family/train read
(`trains._train_members`) goes train -> members, never workbook -> its own train. `_train_
position` walks the identical edge backwards (`workbook --IN_TRAIN--> train`, reading its
own real `sequence` property the same way `trains._train_members` already does), the
identical reverse-lookup shape `foundry_routing._family_for_workbook` (S8.2.2) already
set for `IN_FAMILY`. `None`, honestly, for a workbook the Cartographer/Train Planner has
never sequenced -- such a case sorts last in the queue, disclosed rather than crashing
or guessing a position.

**"Age" is real wall-clock time since `ExceptionCase.created_at`** -- every node already
carries this base property; no new one was needed. The queue sorts oldest-first within
each train-sequence bucket (a work queue's own natural "handle what has waited longest"
reading), a real, disclosed choice §11.3 does not itself state.

**"Site" resolves through the real Site -> Project -> Workbook containment chain** --
`case_execution._resolve_site`, reused verbatim (a cross-epic private helper, the
identical exception this codebase's own convention already grants
`case_derivation._worksheet_field_index`'s own imports).

**"Evidence... key diffs, filter context, parameter values" reads the real §10.3 bundle
a second, wider way than `mender._gather_parity_evidence` already does.** That function
narrows to what a *repair request* needs (failing cells, filter context, column
headers); the Exception Desk's own case page additionally needs the real key-set diff
(`missing_keys`/`extra_keys`, §10.3's own literal evidence) and each case's own real
`ParityCase.param_values` -- assembled directly here rather than widening
`_gather_parity_evidence` for a caller (the Mender's own repair request) that has no use
for either.

**"The artefact... current DAX/M with source calc alongside" reuses the Mender's own
artefact-resolution helpers verbatim** (`_resolve_calculated_field`/`_current_measure`,
cross-epic private, the identical import this module already needs for "patch").
**"Mender pass history with diffs"** reads the real, already-declared `MenderPass` nodes
directly (S8.2.1's own docstring already named this exact future reader: *"an
ExceptionCase's own pass history a future Exception Desk case page (F8.3) reads
directly, not a JSON blob buried in one property"*) -- no new node, no new query
mechanism, just the read this story was always going to be.

**Every decision writes a real `GateDecision(gate="G3")` -- the first real G3 write this
codebase has ever made, and a deliberate one, not an accidental early use of an unbuilt
gate.** No real G3 *gate workflow* exists anywhere (confirmed repeatedly: `redesign.py`,
`nodes.py`'s own `SpecDeviation`s, `diff.py`, `patterns.py` all independently found this
and named S9.1.1/S9.1.2 as the story that eventually builds it) -- but `GateDecision.gate`
already legally allows `"G3"` as a value, and a single, real, evidenced *record* of an
Exception Desk decision is not the same claim as "a G3 gate now exists to approve or
reject against." §11.3's own words for the source-defect decision, "the choice is a G3
matter," are read as "this is what a future G3 gate will read," not "this story must
build G3" -- `nodes.py`'s own `SpecDeviation` for `ExceptionCase.artefact_ref`/`case_refs`
(S8.1.1) already named S8.3.1 as the story that builds "a `GateDecision`-shaped record,
visible to the report owner by construction," and this module is exactly that promise
kept. `GateDecision.decision` gains four new, additive enum values (`PATCHED`,
`REDESIGN`, `MODEL_DEFECT`, `SOURCE_DEFECT`) rather than forcing these four real,
distinct choices into the existing `APPROVED`/`REJECTED`/`CHANGES_REQUESTED`/`WAIVED`
set, which was built for a different workflow (G1/G2 model-design approval) and would
have needed a dishonest semantic stretch to reuse.

**"Patch (edit in place)" still never mutates a Measure in place -- the AC's own literal
wording loses to this codebase's own, far stronger, universal convention.** Every single
existing `Measure` writer (`generation.py`, `mender.py`, `patterns.py`, `rules.py`)
writes a brand-new node and re-points `MAPS_TO`; none has ever edited `Measure.dax` on an
existing node. A literal in-place edit would be the first ever in this codebase and would
throw away the "an edit is a new version, the old row is never touched" discipline
`Pattern.version`/`SemanticModel`'s own per-version lifecycle both already established.
"Patch" therefore writes a new `Measure` the identical way a Mender model repair already
does (`_write_repaired_measure`, reused verbatim, widened with one new optional
`retire_reason` keyword so the retired `MAPS_TO` edge's own audit trail says a human
patched it, not "superseded by a Mender repair") -- attributed `AgentMode.HUMAN`, the
first real write this declared-since-S1.2.1 mode has ever had (confirmed: `grep`-checked,
zero prior uses anywhere).

**"Redesign... route to Foundry or open in Desktop with the MU link"**: the backlog's own
two named alternatives, both real. "Open in Desktop" cannot carry a real MU *link* --
confirmed, the same "no MU page exists" gap ADR 0048 already found for the identical
words in S6.2.1's own AC -- so it is made real the only way this codebase already has: a
real Desktop commit hash, recorded (the identical `closed_by`/`closed_at`/
`desktop_commit_hash` shape `visual_redesign.close_redesign_exception` already
established, generalised here to any class rather than only `VISUAL_REDESIGN` -- that
function itself is untouched, still real and still used for its own S6.2.1 caller).
"Route to Foundry" reuses `foundry_routing.route_to_foundry` directly -- the identical
mechanism S8.2.2 already built for the Mender's own automated routing, called here with
a human-asserted `ModelDefectEvidence` (the engineer's own stated reason, not a re-run
of the automated missing-dimension/grain-mismatch checks) since a human's own judgement
that a fix belongs in the model is itself real evidence, not something this module
should second-guess by trying to reconfirm.

**"Model defect (Foundry change request)"** is the identical `route_to_foundry` call
`redesign`'s own foundry sub-path uses -- the only difference is *why* the engineer chose
it (recorded as the `GateDecision.decision` value, `MODEL_DEFECT` vs `REDESIGN`), never a
second mechanism.

**"Source defect... notify owner"** reuses the identical `NotificationChannel` shape
`regression.py` already established for "notify the report owner" (S7.7.1) -- a real,
honest local log, since no outward notification channel (email, chat) is configured
anywhere this platform has ever been deployed, the identical disclosed-absent posture
`g2_reminders.LocalNotificationChannel` already carries. "Choose reproduce or fix with
owner sign-off" is a real, required choice (`resolution: "REPRODUCE" | "FIX_WITH_SIGN_
OFF"`); the fix path additionally requires a real, non-blank sign-off text -- "the
owner's written agreement," §11.3's own words, taken literally as a real string this
module refuses to proceed without, not a checkbox nobody actually read.

**"Rationale of at least one sentence"** is read as a real character-length proxy, the
identical "a real, defensible, disclosed number" footing every prior reason-requiring
action in this codebase already has (`g2.MIN_RATIONALE_LENGTH = 8`,
`model_lifecycle.MIN_CHANGE_REQUEST_REASON = 10`, `writes.MIN_RETIREMENT_REASON_LENGTH =
8`) -- set higher here (`MIN_RATIONALE_LENGTH = 20`) than any of those three, since "a
sentence" is a qualitatively fuller bar than "a reason" alone, and disclosed as such
rather than silently reusing a smaller number built for a different action.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Protocol

import asyncpg
from astra_adapter.target_contract import TargetAdapter

from .artefacts import ArtefactStore
from .case_execution import _resolve_site  # cross-epic private helper; see module docstring
from .context.canonical import context_hash
from .errors import ElementNotFoundError, InvalidRequestError
from .foundry_routing import (  # cross-epic private helper; see module docstring
    ModelDefectEvidence,
    _family_for_workbook,
    route_to_foundry,
)
from .graph.queries import EDGE_INDEX_TABLE, NODE_INDEX_TABLE
from .ids import new_ulid
from .lineage import hydrate
from .mender import (  # cross-epic private helpers; see module docstring
    _current_measure,
    _latest_verdict,
    _resolve_calculated_field,
    _write_repaired_measure,
    reprove_cases,
)
from .principal import Principal
from .provenance import AgentMode, ProvenanceStore
from .rules import dax_sanity_check
from .tolerance_charter import ToleranceCharterStore
from .writes import GraphWriter, NodeWrite

logger = logging.getLogger(__name__)

#: §13.3's own gate, used for every Exception Desk decision -- see this module's own
#: docstring for why this is a real, deliberate G3 write, not an accidental early one.
GATE = "G3"

MIN_RATIONALE_LENGTH = 20

#: The queue's own live states -- a CLOSED case has already been decided and drops off.
_QUEUE_STATES = frozenset({"OPEN", "BLOCKED"})

_OPEN_STATES = frozenset({"OPEN"})


class ExceptionDeskError(Exception):
    """A queue read or a decision could not be carried out as asked."""


# ------------------------------------------------------------------------------ queue


async def _train_position(pool: asyncpg.Pool, graph_name: str, workbook_id: str) -> tuple[str, int] | None:
    """The real `(train_id, sequence)` an `IN_TRAIN` edge names for this workbook --
    reversed from every existing train read (`trains._train_members`, train -> members
    only). `None`, honestly, for a workbook the Train Planner has never sequenced."""
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            f"""SELECT e.id AS edge_id, e.to_id AS train_id FROM {EDGE_INDEX_TABLE} e
                 WHERE e.graph = $1 AND e.label = 'IN_TRAIN' AND e.from_id = $2 AND e.retired_at IS NULL
                 LIMIT 1""",
            graph_name, workbook_id,
        )
        if row is None:
            return None
        edge_properties = (await hydrate(conn, graph_name, "IN_TRAIN", [row["edge_id"]])).get(row["edge_id"]) or {}
    return str(row["train_id"]), int(edge_properties.get("sequence") or 0)


def _age_seconds(created_at: Any) -> float | None:
    if not created_at:
        return None
    try:
        created = datetime.fromisoformat(str(created_at).replace("Z", "+00:00"))
    except ValueError:
        return None
    return (datetime.now(UTC) - created).total_seconds()


async def queue(
    pool: asyncpg.Pool,
    graph_name: str,
    *,
    train: str | None = None,
    failure_class: str | None = None,
    site: str | None = None,
    assignee: str | None = None,
) -> list[dict[str, Any]]:
    """Every live OPEN/BLOCKED `ExceptionCase`, enriched with its real train position,
    site and age, filtered, and ordered by train sequence then age (oldest first) -- the
    AC's own literal columns and filters, see this module's own docstring for how each
    real fact is resolved."""
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            f"""SELECT id FROM {NODE_INDEX_TABLE}
                 WHERE graph = $1 AND kind = 'node' AND label = 'ExceptionCase' AND retired_at IS NULL""",
            graph_name,
        )
        cases = await hydrate(conn, graph_name, "ExceptionCase", [row["id"] for row in rows])

    entries: list[dict[str, Any]] = []
    for case_id, properties in cases.items():
        if properties.get("state") not in _QUEUE_STATES:
            continue
        workbook_id = str(properties.get("mu_ref") or "")
        train_info = await _train_position(pool, graph_name, workbook_id) if workbook_id else None
        async with pool.acquire() as conn:
            site_name = await _resolve_site(conn, graph_name, workbook_id) if workbook_id else None

        entry = {
            "id": case_id,
            "mu_ref": workbook_id,
            "class": properties.get("class"),
            "passes_consumed": properties.get("passes_consumed"),
            "assignee": properties.get("assignee"),
            "state": properties.get("state"),
            "train_id": train_info[0] if train_info else None,
            "train_sequence": train_info[1] if train_info else None,
            "site": site_name,
            "created_at": properties.get("created_at"),
            "age_seconds": _age_seconds(properties.get("created_at")),
        }
        if train is not None and entry["train_id"] != train:
            continue
        if failure_class is not None and entry["class"] != failure_class:
            continue
        if site is not None and entry["site"] != site:
            continue
        if assignee is not None and entry["assignee"] != assignee:
            continue
        entries.append(entry)

    entries.sort(
        key=lambda e: (
            e["train_sequence"] is None, e["train_sequence"] or 0, -(e["age_seconds"] or 0.0),
        )
    )
    return entries


# --------------------------------------------------------------------------- case page


async def _param_values_by_case(
    pool: asyncpg.Pool, graph_name: str, case_refs: tuple[str, ...],
) -> dict[str, dict[str, Any]]:
    if not case_refs:
        return {}
    async with pool.acquire() as conn:
        cases = await hydrate(conn, graph_name, "ParityCase", list(case_refs))
    return {case_id: dict(props.get("param_values") or {}) for case_id, props in cases.items()}


async def _gather_case_evidence(
    pool: asyncpg.Pool, graph_name: str, artefact_store: ArtefactStore, *, case_refs: tuple[str, ...],
) -> dict[str, Any]:
    """The real §10.3 evidence a case page needs -- failing cells *and* the real key-set
    diff (`missing_keys`/`extra_keys`), plus filter context -- read from each case's own
    latest `Verdict.evidence_ref`, the identical real bundle `mender._gather_parity_
    evidence` already reads a narrower slice of (see this module's own docstring for why
    a second read, not a shared one)."""
    failing_cells: list[dict[str, Any]] = []
    missing_keys: list[list[Any]] = []
    extra_keys: list[list[Any]] = []
    filter_ctx: dict[str, Any] = {}
    for case_id in case_refs:
        verdict = await _latest_verdict(pool, graph_name, case_id)
        if verdict is None or not verdict.get("evidence_ref"):
            continue
        content = await artefact_store.content(str(verdict["evidence_ref"]))
        if content is None:
            continue
        bundle = json.loads(content)
        diff = bundle.get("diff") or {}
        for cell in diff.get("failing_cells") or ():
            failing_cells.append({**cell, "case_ref": case_id})
        for key in diff.get("missing_keys") or ():
            missing_keys.append([*key, case_id])
        for key in diff.get("extra_keys") or ():
            extra_keys.append([*key, case_id])
        if not filter_ctx:
            filter_ctx = bundle.get("filter_ctx") or {}
    return {
        "failing_cells": failing_cells, "missing_keys": missing_keys, "extra_keys": extra_keys,
        "filter_ctx": filter_ctx,
    }


async def _mender_pass_history(pool: asyncpg.Pool, graph_name: str, exception_case_id: str) -> list[dict[str, Any]]:
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            f"""SELECT id FROM {NODE_INDEX_TABLE}
                 WHERE graph = $1 AND kind = 'node' AND label = 'MenderPass' AND retired_at IS NULL""",
            graph_name,
        )
        passes = await hydrate(conn, graph_name, "MenderPass", [row["id"] for row in rows])
    matching = [
        {"id": pass_id, **properties}
        for pass_id, properties in passes.items()
        if properties.get("exception_case_ref") == exception_case_id
    ]
    return sorted(matching, key=lambda p: int(p.get("pass_number") or 0))


async def case_detail(
    pool: asyncpg.Pool, graph_name: str, artefact_store: ArtefactStore, *, exception_case_id: str,
) -> dict[str, Any]:
    """The AC's own three-pane case page, assembled from real graph facts alone -- see
    this module's own docstring for how each pane is resolved."""
    async with pool.acquire() as conn:
        case = (await hydrate(conn, graph_name, "ExceptionCase", [exception_case_id])).get(exception_case_id)
    if case is None:
        raise ElementNotFoundError(f"no ExceptionCase '{exception_case_id}'")

    case_refs = tuple(case.get("case_refs") or ())
    evidence = await _gather_case_evidence(pool, graph_name, artefact_store, case_refs=case_refs)
    param_values = await _param_values_by_case(pool, graph_name, case_refs)

    artefact_ref = case.get("artefact_ref")
    calc = await _resolve_calculated_field(pool, graph_name, artefact_ref) if artefact_ref else None
    measure = await _current_measure(pool, graph_name, calc[0]) if calc else None

    workbook_id = str(case.get("mu_ref") or "")
    train_info = await _train_position(pool, graph_name, workbook_id) if workbook_id else None
    async with pool.acquire() as conn:
        site_name = await _resolve_site(conn, graph_name, workbook_id) if workbook_id else None

    return {
        "id": exception_case_id,
        **case,
        "train_id": train_info[0] if train_info else None,
        "train_sequence": train_info[1] if train_info else None,
        "site": site_name,
        "evidence": {**evidence, "param_values": param_values},
        "artefact": {
            "calc_id": calc[0] if calc else None,
            "calc_name": (calc[1].get("name") if calc else None),
            "source_formula": (calc[1].get("formula") if calc else None),
            "measure_id": measure[0] if measure else None,
            "current_dax": (measure[1].get("dax") if measure else None),
            "current_m_query": (measure[1].get("m_query") if measure else None),
        },
        "mender_passes": await _mender_pass_history(pool, graph_name, exception_case_id),
    }


# ------------------------------------------------------------------------ bulk assign


async def bulk_assign(
    pool: asyncpg.Pool, graph_name: str, writer: GraphWriter, *,
    exception_case_ids: tuple[str, ...], assignee: str, principal: Principal,
) -> tuple[str, ...]:
    """Sets `ExceptionCase.assignee` for real on every named case -- the first real
    driver of this property, declared since §4.1.1 and never written by any prior
    story. Silently skips a case that no longer exists live (retired, or never real);
    returns the ids it actually updated."""
    cleaned = assignee.strip()
    if not cleaned:
        raise InvalidRequestError("bulk assign needs a real assignee")

    async with pool.acquire() as conn:
        cases = await hydrate(conn, graph_name, "ExceptionCase", list(exception_case_ids))

    updated: list[str] = []
    for case_id in exception_case_ids:
        if case_id not in cases:
            continue
        await writer.set_node_properties(case_id, {"assignee": cleaned}, principal=principal)
        updated.append(case_id)
    return tuple(updated)


# ------------------------------------------------------------------------- decisions


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _clean_rationale(rationale: str) -> str:
    cleaned = rationale.strip()
    if len(cleaned) < MIN_RATIONALE_LENGTH:
        raise InvalidRequestError(
            f"a decision needs a rationale of at least {MIN_RATIONALE_LENGTH} characters "
            f"-- a real sentence explaining why, not a placeholder"
        )
    return cleaned


async def _require_open_case(pool: asyncpg.Pool, graph_name: str, exception_case_id: str) -> dict[str, Any]:
    async with pool.acquire() as conn:
        case = (await hydrate(conn, graph_name, "ExceptionCase", [exception_case_id])).get(exception_case_id)
    if case is None:
        raise ElementNotFoundError(f"no ExceptionCase '{exception_case_id}'")
    if case.get("state") not in _OPEN_STATES:
        raise ExceptionDeskError(
            f"ExceptionCase '{exception_case_id}' is not OPEN (state={case.get('state')!r}) "
            f"-- a decision can only be recorded against a case still open for one"
        )
    return case


async def _write_gate_decision(
    writer: GraphWriter, *, exception_case_id: str, decision: str, rationale: str,
    evidence_ref: str | None, principal: Principal,
) -> str:
    """Every Exception Desk decision, unconditionally -- see this module's own docstring
    for why this is a real `GateDecision(gate="G3")`, not a lighter-weight record."""
    decision_id = new_ulid()
    await writer.write_nodes(
        [
            NodeWrite(
                type="GateDecision",
                id=decision_id,
                properties={
                    "gate": GATE, "subject_ref": exception_case_id, "decision": decision,
                    "approver": principal.value, "approver_role": "migration_engineer",
                    "rationale": rationale, "evidence_ref": evidence_ref, "timestamp": _now(),
                },
            )
        ],
        principal=principal,
    )
    return decision_id


@dataclass(frozen=True, slots=True)
class PatchResult:
    exception_case_id: str
    gate_decision_id: str
    measure_id: str
    outcome: str  # "closed" | "still_failing"
    cases_reproved: tuple[str, ...]
    cases_still_failing: tuple[str, ...]

    def as_dict(self) -> dict[str, Any]:
        return {
            "exception_case_id": self.exception_case_id, "gate_decision_id": self.gate_decision_id,
            "measure_id": self.measure_id, "outcome": self.outcome,
            "cases_reproved": list(self.cases_reproved), "cases_still_failing": list(self.cases_still_failing),
        }


async def decide_patch(
    pool: asyncpg.Pool,
    graph_name: str,
    writer: GraphWriter,
    artefact_store: ArtefactStore,
    provenance_store: ProvenanceStore,
    target_adapter: TargetAdapter,
    charter_store: ToleranceCharterStore,
    *,
    exception_case_id: str,
    dax: str,
    rationale: str,
    workspace: str,
    principal: Principal,
) -> PatchResult:
    """§11.3's own "patch (edit the artefact; re-prove)" -- see this module's own
    docstring for why this writes a brand-new `Measure` rather than a literal in-place
    edit. Closes the case only once every one of its own real cases re-proves PASS;
    otherwise the case stays OPEN (a real, honest "not fixed yet", not a false close)."""
    case = await _require_open_case(pool, graph_name, exception_case_id)
    cleaned_rationale = _clean_rationale(rationale)

    parse_error = dax_sanity_check(dax)
    if parse_error is not None:
        raise InvalidRequestError(f"this DAX does not validate: {parse_error}")

    artefact_ref = case.get("artefact_ref")
    calc = await _resolve_calculated_field(pool, graph_name, artefact_ref) if artefact_ref else None
    calc_id = calc[0] if calc else None
    calc_name = str(calc[1].get("name")) if calc else str(artefact_ref or exception_case_id)

    measure_id = await _write_repaired_measure(
        pool, graph_name, writer, provenance_store,
        calc_id=calc_id, name=calc_name, dax=dax, mode=AgentMode.HUMAN, pattern_ref=None,
        context_hash_value=context_hash(dax.encode("utf-8")), subject_id=calc_id or exception_case_id,
        validation_state="rung 1-2 (schema, parse) checked; rung 4 (proof) is this same "
                          "decision's own re-proof -- a Migration Engineer's own hand-edit "
                          "via the Exception Desk (§11.3)",
        model=None, gateway_request_id=None, tokens_in=None, tokens_out=None, confidence=None,
        principal=principal, retire_reason="superseded by a Migration Engineer's own patch (§11.3)",
    )

    case_refs = tuple(case.get("case_refs") or ())
    charter_version = await charter_store.latest()
    reproved = await reprove_cases(
        pool, graph_name, writer, artefact_store, target_adapter,
        case_ids=case_refs, workspace=workspace, charter=charter_version.charter, principal=principal,
    )
    still_failing = tuple(sorted(cid for cid in case_refs if reproved.get(cid, {}).get("result") != "PASS"))
    reproved_now = tuple(sorted(set(case_refs) - set(still_failing)))
    closed = not still_failing

    decision_id = await _write_gate_decision(
        writer, exception_case_id=exception_case_id, decision="PATCHED", rationale=cleaned_rationale,
        evidence_ref=measure_id, principal=principal,
    )

    update: dict[str, Any] = {"decision": "PATCHED"}
    if closed:
        update.update(state="CLOSED", closed_by=principal.value, closed_at=_now())
    await writer.set_node_properties(exception_case_id, update, principal=principal)

    return PatchResult(
        exception_case_id=exception_case_id, gate_decision_id=decision_id, measure_id=measure_id,
        outcome="closed" if closed else "still_failing",
        cases_reproved=reproved_now, cases_still_failing=still_failing,
    )


@dataclass(frozen=True, slots=True)
class RedesignResult:
    exception_case_id: str
    gate_decision_id: str
    route: str  # "desktop" | "foundry"
    detail: dict[str, Any]

    def as_dict(self) -> dict[str, Any]:
        return {
            "exception_case_id": self.exception_case_id, "gate_decision_id": self.gate_decision_id,
            "route": self.route, "detail": self.detail,
        }


async def decide_redesign_desktop(
    pool: asyncpg.Pool, graph_name: str, writer: GraphWriter, *,
    exception_case_id: str, rationale: str, desktop_commit_hash: str, principal: Principal,
) -> RedesignResult:
    """§11.3's own "finish in Desktop" -- the identical `closed_by`/`closed_at`/
    `desktop_commit_hash` shape `visual_redesign.close_redesign_exception` already
    established, generalised to any class here (that function itself stays untouched
    and class-gated for its own S6.2.1 caller)."""
    case = await _require_open_case(pool, graph_name, exception_case_id)
    cleaned_rationale = _clean_rationale(rationale)
    cleaned_hash = desktop_commit_hash.strip()
    if not cleaned_hash:
        raise InvalidRequestError(
            "a Desktop commit hash is required -- it is the record of where the finished work landed"
        )

    decision_id = await _write_gate_decision(
        writer, exception_case_id=exception_case_id, decision="REDESIGN", rationale=cleaned_rationale,
        evidence_ref=None, principal=principal,
    )
    await writer.set_node_properties(
        exception_case_id,
        {
            "decision": "REDESIGN_DESKTOP", "state": "CLOSED", "closed_by": principal.value,
            "closed_at": _now(), "desktop_commit_hash": cleaned_hash,
        },
        principal=principal,
    )
    return RedesignResult(
        exception_case_id=exception_case_id, gate_decision_id=decision_id, route="desktop",
        detail={"desktop_commit_hash": cleaned_hash, "class": case.get("class")},
    )


async def decide_redesign_foundry(
    pool: asyncpg.Pool, graph_name: str, writer: GraphWriter, *,
    exception_case_id: str, rationale: str, principal: Principal,
) -> RedesignResult:
    """§11.3's own "route to the Foundry" -- reuses `foundry_routing.route_to_foundry`
    verbatim, the identical mechanism S8.2.2 already built for the Mender's own
    automated routing, called here with the engineer's own asserted evidence rather than
    a re-run of the automated detection (a human's own judgement is itself real
    evidence)."""
    case = await _require_open_case(pool, graph_name, exception_case_id)
    cleaned_rationale = _clean_rationale(rationale)
    workbook_id = str(case.get("mu_ref") or "")
    family_id = await _family_for_workbook(pool, graph_name, workbook_id)
    if family_id is None:
        raise ExceptionDeskError(
            f"workbook '{workbook_id}' has never been clustered into a ModelFamily -- "
            f"there is no Foundry family to route this redesign to yet"
        )

    decision_id = await _write_gate_decision(
        writer, exception_case_id=exception_case_id, decision="REDESIGN", rationale=cleaned_rationale,
        evidence_ref=None, principal=principal,
    )
    evidence = ModelDefectEvidence(
        family_id=family_id, reason=cleaned_rationale,
        signals={"triggered_by": "human_redesign_decision", "exception_case_id": exception_case_id},
    )
    route_result = await route_to_foundry(
        pool, graph_name, writer, exception_case_id=exception_case_id, evidence=evidence, principal=principal,
    )
    await writer.set_node_properties(exception_case_id, {"decision": "REDESIGN_FOUNDRY"}, principal=principal)
    return RedesignResult(
        exception_case_id=exception_case_id, gate_decision_id=decision_id, route="foundry", detail=route_result,
    )


async def decide_model_defect(
    pool: asyncpg.Pool, graph_name: str, writer: GraphWriter, *,
    exception_case_id: str, rationale: str, principal: Principal,
) -> dict[str, Any]:
    """§11.3's own "model defect (route to the Foundry as a change to the family)" --
    the identical `route_to_foundry` call `redesign`'s own foundry sub-path uses; only
    the recorded `GateDecision.decision` differs (`MODEL_DEFECT`, not `REDESIGN`),
    naming *why* the engineer chose it."""
    case = await _require_open_case(pool, graph_name, exception_case_id)
    cleaned_rationale = _clean_rationale(rationale)
    workbook_id = str(case.get("mu_ref") or "")
    family_id = await _family_for_workbook(pool, graph_name, workbook_id)
    if family_id is None:
        raise ExceptionDeskError(
            f"workbook '{workbook_id}' has never been clustered into a ModelFamily -- "
            f"there is no Foundry family to route this model defect to yet"
        )

    decision_id = await _write_gate_decision(
        writer, exception_case_id=exception_case_id, decision="MODEL_DEFECT", rationale=cleaned_rationale,
        evidence_ref=None, principal=principal,
    )
    evidence = ModelDefectEvidence(
        family_id=family_id, reason=cleaned_rationale,
        signals={"triggered_by": "human_model_defect_decision", "exception_case_id": exception_case_id},
    )
    route_result = await route_to_foundry(
        pool, graph_name, writer, exception_case_id=exception_case_id, evidence=evidence, principal=principal,
    )
    return {"exception_case_id": exception_case_id, "gate_decision_id": decision_id, "route_result": route_result}


# ---------------------------------------------------------------- source defect + notify


class NotificationChannel(Protocol):
    @property
    def kind(self) -> str: ...

    async def notify_source_defect(
        self, *, workbook_id: str, exception_case_id: str, resolution: str,
    ) -> None: ...


class LocalNotificationChannel:
    """No outward channel exists in this codebase -- the identical honest disclosure
    `regression.LocalNotificationChannel`/`g2_reminders.LocalNotificationChannel` already
    give, applied to a source-defect decision instead. "Notify the owner" is a role, not
    a named person -- no property anywhere links a `ReportDefinition` to a specific
    report-owner principal or email (the identical gap `regression.py`'s own docstring
    already discloses)."""

    kind = "local"

    async def notify_source_defect(self, *, workbook_id: str, exception_case_id: str, resolution: str) -> None:
        logger.info(
            "source-defect notification for the client_report_owner role, workbook %s: "
            "ExceptionCase %s recorded as a source defect, resolution=%s -- no outward "
            "notification channel is configured, recorded locally",
            workbook_id, exception_case_id, resolution,
        )


_SOURCE_DEFECT_RESOLUTIONS = frozenset({"REPRODUCE", "FIX_WITH_SIGN_OFF"})


async def decide_source_defect(
    pool: asyncpg.Pool, graph_name: str, writer: GraphWriter, notification_channel: NotificationChannel, *,
    exception_case_id: str, rationale: str, resolution: str, owner_sign_off: str | None, principal: Principal,
) -> dict[str, Any]:
    """§11.3's own "source defect... record, inform the owner, and either reproduce the
    defect faithfully or fix it with the owner's written agreement." Requires a real,
    non-blank sign-off text for the fix path -- "written agreement" taken literally, not
    a checkbox."""
    case = await _require_open_case(pool, graph_name, exception_case_id)
    cleaned_rationale = _clean_rationale(rationale)
    if resolution not in _SOURCE_DEFECT_RESOLUTIONS:
        raise InvalidRequestError(
            f"resolution must be one of {sorted(_SOURCE_DEFECT_RESOLUTIONS)}; got {resolution!r}"
        )
    cleaned_sign_off: str | None = None
    if resolution == "FIX_WITH_SIGN_OFF":
        cleaned_sign_off = (owner_sign_off or "").strip()
        if not cleaned_sign_off:
            raise InvalidRequestError(
                "fixing a source defect (diverging from what Tableau shows) needs the "
                "report owner's own real, written sign-off -- §11.3's own literal "
                "requirement, not something this decision can be made without"
            )

    decision_id = await _write_gate_decision(
        writer, exception_case_id=exception_case_id, decision="SOURCE_DEFECT", rationale=cleaned_rationale,
        evidence_ref=None, principal=principal,
    )

    workbook_id = str(case.get("mu_ref") or "")
    await notification_channel.notify_source_defect(
        workbook_id=workbook_id, exception_case_id=exception_case_id, resolution=resolution,
    )

    properties: dict[str, Any] = {
        "decision": f"SOURCE_DEFECT_{resolution}", "state": "CLOSED",
        "closed_by": principal.value, "closed_at": _now(),
    }
    await writer.set_node_properties(exception_case_id, properties, principal=principal)

    return {
        "exception_case_id": exception_case_id, "gate_decision_id": decision_id,
        "resolution": resolution, "owner_sign_off": cleaned_sign_off, "notified": True,
    }


# ---------------------------------------------------------------------------- service


class ExceptionDeskService:
    """Binds every function above to one pool/graph/writer/artefact store/provenance
    store/target adapter/charter store/notification channel -- the identical "pre-bound
    object on app.state" shape `MenderService` (S8.2.1) already takes."""

    def __init__(
        self,
        pool: asyncpg.Pool,
        *,
        graph_name: str,
        writer: GraphWriter,
        artefact_store: ArtefactStore,
        provenance_store: ProvenanceStore,
        target_adapter: TargetAdapter,
        charter_store: ToleranceCharterStore,
        notification_channel: NotificationChannel | None = None,
    ) -> None:
        self._pool = pool
        self._graph = graph_name
        self._writer = writer
        self._artefact_store = artefact_store
        self._provenance_store = provenance_store
        self._target_adapter = target_adapter
        self._charter_store = charter_store
        self._notification_channel = notification_channel or LocalNotificationChannel()

    async def queue(
        self, *, train: str | None = None, failure_class: str | None = None,
        site: str | None = None, assignee: str | None = None,
    ) -> list[dict[str, Any]]:
        return await queue(
            self._pool, self._graph, train=train, failure_class=failure_class, site=site, assignee=assignee,
        )

    async def case_detail(self, exception_case_id: str) -> dict[str, Any]:
        return await case_detail(self._pool, self._graph, self._artefact_store, exception_case_id=exception_case_id)

    async def bulk_assign(self, *, exception_case_ids: tuple[str, ...], assignee: str, principal: Principal) -> tuple[str, ...]:
        return await bulk_assign(
            self._pool, self._graph, self._writer,
            exception_case_ids=exception_case_ids, assignee=assignee, principal=principal,
        )

    async def patch(
        self, exception_case_id: str, *, dax: str, rationale: str, workspace: str, principal: Principal,
    ) -> PatchResult:
        return await decide_patch(
            self._pool, self._graph, self._writer, self._artefact_store, self._provenance_store,
            self._target_adapter, self._charter_store,
            exception_case_id=exception_case_id, dax=dax, rationale=rationale, workspace=workspace, principal=principal,
        )

    async def redesign_desktop(
        self, exception_case_id: str, *, rationale: str, desktop_commit_hash: str, principal: Principal,
    ) -> RedesignResult:
        return await decide_redesign_desktop(
            self._pool, self._graph, self._writer,
            exception_case_id=exception_case_id, rationale=rationale,
            desktop_commit_hash=desktop_commit_hash, principal=principal,
        )

    async def redesign_foundry(self, exception_case_id: str, *, rationale: str, principal: Principal) -> RedesignResult:
        return await decide_redesign_foundry(
            self._pool, self._graph, self._writer,
            exception_case_id=exception_case_id, rationale=rationale, principal=principal,
        )

    async def model_defect(self, exception_case_id: str, *, rationale: str, principal: Principal) -> dict[str, Any]:
        return await decide_model_defect(
            self._pool, self._graph, self._writer,
            exception_case_id=exception_case_id, rationale=rationale, principal=principal,
        )

    async def source_defect(
        self, exception_case_id: str, *, rationale: str, resolution: str,
        owner_sign_off: str | None, principal: Principal,
    ) -> dict[str, Any]:
        return await decide_source_defect(
            self._pool, self._graph, self._writer, self._notification_channel,
            exception_case_id=exception_case_id, rationale=rationale, resolution=resolution,
            owner_sign_off=owner_sign_off, principal=principal,
        )


__all__ = [
    "GATE",
    "MIN_RATIONALE_LENGTH",
    "ExceptionDeskError",
    "ExceptionDeskService",
    "LocalNotificationChannel",
    "NotificationChannel",
    "PatchResult",
    "RedesignResult",
    "bulk_assign",
    "case_detail",
    "decide_model_defect",
    "decide_patch",
    "decide_redesign_desktop",
    "decide_redesign_foundry",
    "decide_source_defect",
    "queue",
]
