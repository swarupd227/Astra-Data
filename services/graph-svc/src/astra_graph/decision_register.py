"""The Decision Register -- story S10.4.2, opening §15.3.6's own Governance surface
alongside the Gate Inbox (S10.4.1).

    "As an auditor, I want a Decision Register of every gate decision and adjudication,
    so that I can answer 'who approved this and on what evidence' without asking anyone.

    Acceptance criteria:
    - Register lists GateDecisions and adjudications with approver, countersigner,
      evidence references, rationale, timestamps; search and filter; export to CSV and
      signed PDF
    - Each row opens the evidence bundle"

§15.3.6's own row, verbatim: *"Decision Register | All GateDecisions and adjudications
with approver, evidence, rationale; search and export. | Open evidence; export."* §4.5's
own "Evidence Chain" section names the exact question this register answers: *"who
approved this report and on what evidence"* -- word for word this story's own AC.

**"GateDecisions and adjudications" are the same ontology node, not two tables.**
Confirmed by direct reading of `ontology/nodes.py`'s own `GateDecision.decision` enum
note: the Exception Desk's own four remediation outcomes (PATCHED, REDESIGN,
MODEL_DEFECT, SOURCE_DEFECT -- §11.3's "adjudications") are themselves written as a real
`GateDecision(gate="G3")` row (`exception_desk._write_gate_decision`), the identical node
type the G1-G4 approval workflow writes. This register reads one label, `GateDecision`,
in full -- reusing `g3_card._live_gate_decisions` (cross-epic private helper; already
exactly "every live GateDecision node, hydrated") rather than inventing a second query.

**"Auditor" is not one existing role -- the identical real gap S10.4.1 found for "data
owner."** §2.4's own eleven roles (plus `CLIENT_ANALYTICS_LEAD`, S7.1.1) name no auditor.
Unlike the Gate Inbox, this is not a per-gate approver a workflow structurally needs (the
bar `roles.py`'s own docstring sets for adding a new role) -- it is a cross-cutting read,
so no new role is added. §15.1's own role table gives `client_infosec_reviewer` the
nearest real remit ("Reviews the data-handling position, inference boundary and evidence
export"), immediately next to this exact spec row's own "Open evidence; export" action
pair -- so this register is gated the identical "any Artizent role, or this one named
client role" shape `require_mu_page_reader` already set for `client_report_owner`, using
`client_infosec_reviewer` in its place. See `deps.py`'s own `require_decision_register_
reader`.

**Resolving a row's subject needs (gate, decision), not gate alone -- G3 is written from
two different call sites with two different subject shapes.** `g3_card.approve`/
`request_changes` write `subject_ref=<Workbook id>` (decision APPROVED/CHANGES_
REQUESTED); `exception_desk._write_gate_decision` writes `subject_ref=<ExceptionCase
id>` for PATCHED/REDESIGN/MODEL_DEFECT/SOURCE_DEFECT, and `parity_dashboard.py`/
`g3_card.py`'s own comments name WAIVED as the same ExceptionCase shape (though, confirmed
by direct search, no live write site exists for it yet -- a real, pre-existing gap this
story does not close, and this register simply shows a WAIVED row exactly like any other
if one is ever written). G1's subject is always the one platform-wide `tolerance_charter`
singleton (`tolerance_charter.SUBJECT_REF`); G2 is a ModelFamily; G4 is a Site. `_subject_
label` below is this dispatch, and an ExceptionCase's own display name is built from its
`class` plus its owning Workbook's name (`ExceptionCase` carries no `name` property of its
own -- confirmed against `ontology/nodes.py`).

**The evidence bundle resolves `evidence_ref` only when it is a real artefact-store id --
never fabricated when it is not.** `evidence_ref` is heterogeneous by design across this
codebase's own gates: sometimes a stored JSON/image artefact (`g3_card`/`g4_card`'s own
snapshot, `exception_desk`'s own measure id passed straight through), sometimes a bare
`SemanticModel` node id (`g2.approve`), sometimes absent (G1, most Exception Desk
outcomes, every `request_changes`/`defer`). `artefacts.ArtefactStore.get()` already
returns `None` for an id it does not hold (confirmed directly) -- the honest signal this
module reads rather than guessing from `gate`/`decision` which shape a given ref is. A row
whose evidence does not resolve to a stored artefact still shows its own full decision
record (rationale, approver, countersigner, version_hash); the bundle discloses
`artefact: null` rather than inventing content. The bytes themselves are never re-served
here -- the console fetches them from the identical, already-existing
`GET /v1/artefacts/{id}/content` route (S10.3.1) once it has the id from this bundle.

**"Signed PDF" is a rendered attestation, not a persisted, versioned baseline -- unlike
`calibration_wave.sign_report`.** That story's own AC names two separate actions, "Sign
report" and "export"; this story's own AC names only one, "export ... to ... signed PDF"
-- no separate sign action, no re-fetchable prior version to compare against. The PDF
this module renders (`render_decision_register_pdf`, real `reportlab` tables, the
identical library/shape `calibration_wave.render_calibration_report_pdf`/`status_pack.
render_pdf` already use) carries a footer stating who exported it and when, read from the
calling principal and the render instant -- the same "typed, attributed act, not a
cryptographic signature" honesty every other "signed" concept in this codebase already
discloses (`calibration_wave`'s own `signed_by`/`countersigned_by`, `g2`/`g3_card`/
`g4_card`'s own countersigner strings). No PKI, no hash chain, no external anchor exists
anywhere in this codebase (confirmed by direct search) -- §4.5's own "signed bundle ...
with a verification tool" is the separate, later Evidence Export screen (§15.3.7), not
this story's own AC.

**No new table, no migration.** Every field this register shows is already written by an
existing gate action; this story only reads, filters, sorts and renders it.
"""

from __future__ import annotations

import csv
import io
from datetime import UTC, datetime
from typing import Any

import asyncpg
from reportlab.lib import colors
from reportlab.lib.pagesizes import LETTER
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

from .artefacts import ArtefactStore
from .g3_card import _live_gate_decisions  # cross-epic private helper; see module docstring
from .lineage import hydrate

#: The Exception Desk's own four remediation outcomes, plus WAIVED (never yet written --
#: see the module docstring) -- every one of these names an ExceptionCase, not a Workbook.
_EXCEPTION_DECISIONS = frozenset({"PATCHED", "REDESIGN", "MODEL_DEFECT", "SOURCE_DEFECT", "WAIVED"})

_LABEL_FOR_GATE = {"G2": "ModelFamily", "G4": "Site"}

_CSV_FIELDS = (
    "id", "gate", "decision", "subject_ref", "subject_name", "approver", "approver_role",
    "countersigner", "countersigner_role", "rationale", "evidence_ref", "version_hash",
    "target_date", "timestamp",
)


def _subject_label(gate: str, decision: str) -> str | None:
    """Which node label `subject_ref` names, or `None` for G1's platform-wide singleton."""
    if gate == "G1":
        return None
    if gate == "G3":
        return "ExceptionCase" if decision in _EXCEPTION_DECISIONS else "Workbook"
    return _LABEL_FOR_GATE.get(gate)


def _iso(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, str):
        return value
    return str(value.astimezone(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z"))


async def _resolve_subjects(
    pool: asyncpg.Pool, graph_name: str, decisions: list[dict[str, Any]],
) -> dict[str, dict[str, dict[str, Any]]]:
    """One `hydrate` call per real label this batch actually needs -- never per row."""
    by_label: dict[str, set[str]] = {}
    for props in decisions:
        label = _subject_label(str(props.get("gate")), str(props.get("decision")))
        if label:
            by_label.setdefault(label, set()).add(str(props.get("subject_ref")))

    resolved: dict[str, dict[str, dict[str, Any]]] = {}
    async with pool.acquire() as conn:
        for label, ids in by_label.items():
            resolved[label] = await hydrate(conn, graph_name, label, sorted(ids))
        # An ExceptionCase's own display name needs its owning Workbook's name too.
        case_nodes = resolved.get("ExceptionCase") or {}
        workbook_ids = {str(props.get("mu_ref")) for props in case_nodes.values() if props.get("mu_ref")}
        if workbook_ids:
            resolved.setdefault("Workbook", {}).update(
                await hydrate(conn, graph_name, "Workbook", sorted(workbook_ids))
            )
    return resolved


def _subject_name(
    gate: str, decision: str, subject_ref: str, resolved: dict[str, dict[str, dict[str, Any]]],
) -> str:
    if gate == "G1":
        return "Tolerance Charter"
    label = _subject_label(gate, decision)
    if label is None:
        return subject_ref
    node = (resolved.get(label) or {}).get(subject_ref)
    if node is None:
        return subject_ref
    if label == "ExceptionCase":
        workbook = (resolved.get("Workbook") or {}).get(str(node.get("mu_ref") or ""))
        workbook_name = workbook.get("name") if workbook else node.get("mu_ref")
        return f"{node.get('class', 'exception')} on {workbook_name or subject_ref}"
    return node.get("name") or subject_ref


def _row(
    decision_id: str, props: dict[str, Any], resolved: dict[str, dict[str, dict[str, Any]]],
) -> dict[str, Any]:
    gate = str(props.get("gate"))
    decision = str(props.get("decision"))
    subject_ref = str(props.get("subject_ref"))
    return {
        "id": decision_id,
        "gate": gate,
        "decision": decision,
        "subject_ref": subject_ref,
        "subject_name": _subject_name(gate, decision, subject_ref, resolved),
        "approver": props.get("approver"),
        "approver_role": props.get("approver_role"),
        "countersigner": props.get("countersigner"),
        "countersigner_role": props.get("countersigner_role"),
        "rationale": props.get("rationale"),
        "evidence_ref": props.get("evidence_ref"),
        "version_hash": props.get("version_hash"),
        "target_date": _iso(props.get("target_date")),
        "timestamp": _iso(props.get("timestamp")),
    }


async def list_decisions(
    pool: asyncpg.Pool,
    graph_name: str,
    *,
    gate: str | None = None,
    decision: str | None = None,
    approver: str | None = None,
    q: str | None = None,
) -> list[dict[str, Any]]:
    """Every live `GateDecision`, newest first -- filtered and searched in Python, the
    identical "load everything, this estate's own decision volume is not a high-volume
    log store" posture `gate_inbox.py`'s own per-gate queries already take."""
    decisions = await _live_gate_decisions(pool, graph_name)
    resolved = await _resolve_subjects(pool, graph_name, list(decisions.values()))
    rows = [_row(decision_id, props, resolved) for decision_id, props in decisions.items()]

    if gate:
        rows = [row for row in rows if row["gate"] == gate]
    if decision:
        rows = [row for row in rows if row["decision"] == decision]
    if approver:
        needle = approver.strip().lower()
        rows = [row for row in rows if needle in (row["approver"] or "").lower()]
    if q:
        needle = q.strip().lower()

        def _matches(row: dict[str, Any]) -> bool:
            haystack = " ".join(
                str(row.get(field) or "")
                for field in ("subject_name", "subject_ref", "approver", "countersigner", "rationale")
            )
            return needle in haystack.lower()

        rows = [row for row in rows if _matches(row)]

    rows.sort(key=lambda row: row["timestamp"] or "", reverse=True)
    return rows


async def decision_row(pool: asyncpg.Pool, graph_name: str, gate_decision_id: str) -> dict[str, Any] | None:
    decisions = await _live_gate_decisions(pool, graph_name)
    props = decisions.get(gate_decision_id)
    if props is None:
        return None
    resolved = await _resolve_subjects(pool, graph_name, [props])
    return _row(gate_decision_id, props, resolved)


async def evidence_bundle(
    pool: asyncpg.Pool, graph_name: str, artefact_store: ArtefactStore, gate_decision_id: str,
) -> dict[str, Any] | None:
    """The decision record plus whatever its own `evidence_ref` really resolves to -- see
    this module's own docstring for why a non-resolving ref is disclosed, not guessed at."""
    row = await decision_row(pool, graph_name, gate_decision_id)
    if row is None:
        return None
    artefact = None
    if row["evidence_ref"]:
        record = await artefact_store.get(str(row["evidence_ref"]))
        if record is not None:
            artefact = record.as_dict()
    return {"decision": row, "artefact": artefact}


def decisions_to_csv(rows: list[dict[str, Any]]) -> str:
    buffer = io.StringIO()
    writer = csv.DictWriter(buffer, fieldnames=list(_CSV_FIELDS))
    writer.writeheader()
    for row in rows:
        writer.writerow({name: row.get(name) if row.get(name) is not None else "" for name in _CSV_FIELDS})
    return buffer.getvalue()


def render_decision_register_pdf(rows: list[dict[str, Any]], *, signed_by: str) -> bytes:
    """A real PDF, tables not native charts -- the identical, disclosed scope
    `calibration_wave.render_calibration_report_pdf`/`status_pack.render_pdf` already
    carry. "Signed" is a rendered attestation of who exported it and when -- see this
    module's own docstring for why this story's AC takes that reading, not a persisted,
    re-fetchable baseline."""
    signed_at = datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")
    buffer = io.BytesIO()
    doc = SimpleDocTemplate(buffer, pagesize=LETTER)
    styles = getSampleStyleSheet()
    story: list[Any] = [
        Paragraph("Astra Data — Decision Register", styles["Title"]),
        Spacer(1, 12),
        Paragraph(f"{len(rows)} decision(s)", styles["Normal"]),
        Spacer(1, 12),
    ]
    table_rows = [["Gate", "Decision", "Subject", "Approver", "Countersigner", "When"]]
    for row in rows:
        table_rows.append([
            row["gate"], row["decision"], row["subject_name"] or row["subject_ref"],
            row["approver"] or "", row["countersigner"] or "", row["timestamp"] or "",
        ])
    table = Table(table_rows, repeatRows=1)
    table.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#1f5fd0")),
                ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
                ("GRID", (0, 0), (-1, -1), 0.5, colors.grey),
                ("FONTSIZE", (0, 0), (-1, -1), 8),
            ]
        )
    )
    story.append(table)
    story.append(Spacer(1, 18))
    story.append(Paragraph(f"Exported and signed by {signed_by} at {signed_at}.", styles["Italic"]))
    doc.build(story)
    return buffer.getvalue()


__all__ = [
    "decision_row",
    "decisions_to_csv",
    "evidence_bundle",
    "list_decisions",
    "render_decision_register_pdf",
]
