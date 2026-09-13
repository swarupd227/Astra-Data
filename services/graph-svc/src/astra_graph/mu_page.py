"""The Migration Unit page -- story S10.3.1, opening F10.3.

    "As a migration engineer, I want one page per report with everything about it, so
    that there is a single URL to send to anyone about any report.

    Acceptance criteria:
    - Header: state, tier, family, train, owners, gate status strip; sections per
      §15.4: Source, Artefacts, Parity, Exceptions, Gates, Provenance, Timeline
    - Client roles see Source, Artefacts (thumbnails and documentation only), Parity
      (summary and verdict), Gates and Timeline
    - Page loads in under 500 ms p95; artefact previews lazy-load"

§15.4's own anatomy, verbatim: "One URL per report. The header carries the state, tier,
family, train, owners and the gate status strip. Below it, sections that expand: Source
(sheets, dashboards, datasources, calcs with class labels, usage, screenshot); Artefacts
(model reference, measures with source alongside and provenance badges, report
definition with page thumbnails, Git links); Parity (latest run summary, case table,
per-sheet verdict grid, evidence links, visual parity scores); Exceptions (open and
closed, with decisions); Gates (G2 inherited from family, G3 card, G4 site status);
Timeline (every event, from harvest to release, with who and what); Provenance (every
artefact's record, filterable by mode). Client roles see Source, Artefacts (thumbnails
and documentation only), Parity (summary and verdict grid), Gates and Timeline." §15.1's
own role table names the client persona this reading is built for: **Client Report
Owner**, not "client roles" generically.

**One Migration Unit = one Workbook, always -- there is no `MigrationUnit` graph node.**
Confirmed directly (again) against `migration_units.py`'s own docstring ("this is a
port, not an implementation") and `ontology/nodes.py`'s own `Workbook` note, "One
Migration Unit per Workbook." This module's own `workbook_id` parameter is the real MU
proxy every prior G3-adjacent story already uses (`ExceptionCase.mu_ref`,
`ReportDefinition.mu_ref`, `GateDecision.subject_ref` for G3) -- the identical footing
`g3_card.py`'s own docstring already established.

**One shared assembly, sliced for the client response -- not `g2.client_proposal_view`'s
own "a different document" convention.** That precedent exists to keep a client surface
"calm" -- a differently *worded* document, not the Artizent one with fields hidden.
Nothing this page reads is reworded for a client reader (the parity dashboard, the G3
card and the source/artefact facts are already exactly what `ParityDashboard`/`G3Card`
show a report owner today); the only real difference is *how much* of it a client role
sees, per §15.4's own literal list. Computing the full assembly once and slicing the
response for a client caller — rather than a second, separately-written assembly
function that recomputes the shared sections — is the only way to hit this story's own
≤500 ms page-open budget (§15.6) without doing Source/Artefacts/Parity/Gates/Timeline's
own real queries twice per client request. The slice is enforced in this module, not
left to the console to under-request: a client response is built from a strict subset
of keys, never the full document with a role check bolted on the wire.

**Header "state" is the Wave Board's own already-disclosed static `IN_TRAIN.state`
proxy** (`trains.py`'s own docstring: "a card's state is set once, at proposal time"),
`None` for a workbook the Train Planner has never sequenced -- the identical honest
absence the Wave Board's own kanban already has, not a fabricated default.

**"Owners" reads the single real `OWNED_BY` edge a workbook can have today** -- the same
edge `estate.py`'s own `_owners` already reads for the Estate Explorer's table, `None`
where nothing has ever been assigned. §4.1.2 declares no cardinality wider than one owner
per workbook, so this returns one owner object, not a list, matching every other reader
of this same edge in this codebase.

**Gates reads four different subject grains, exactly as §15.4 says -- "G2 inherited from
family, G3 card, G4 site status."** G1 (Tolerance Charter) is global (`subject_ref ==
"tolerance_charter"`, `tolerance_charter.SUBJECT_REF`) and applies identically to every
MU. G2 is keyed by this workbook's own family id (`foundry_routing._family_for_workbook`,
a cross-epic private helper, the identical import `g3_card.py` already makes). G3 reuses
`g3_card.g3_card` wholesale -- it already *is* the §15.5 card this section names, not a
narrower re-read of the same facts. G4 is a real, disclosed *summary*, not the full
readiness checklist (`g4_card.g4_card`'s own signature needs five extra stores this
module has no other reason to depend on) -- "G4 site status" is read as the latest real
G4 `GateDecision` for the workbook's own site, the identical shape this module already
uses for G1/G2, not the fuller card `g4_card.g4_card` builds for the Decommission
Tracker's own screen.

**"Evidence links" in Parity are the per-sheet screenshot references
`parity_dashboard.parity_dashboard` already returns** (`source_screenshot_ref`/
`target_render_ref` on each `SheetParityStats`), not a second, heavier fetch of full
per-case evidence bundles (`exception_desk._gather_case_evidence`'s own job, Artizent-
only and already reachable from the Exception Desk). A real, disclosed narrower reading
of "evidence links" than "every case's full evidence," in service of the same page-open
budget this module's own docstring cites throughout.

**Timeline is a raw, multi-subject query against the outbox directly, not N calls to
`GET /v1/events`.** `repository.read_events` takes one `subject` at a time
(`graph/repository.py`); `regression.py`'s own `regression_monitor` already established
the alternative for exactly this shape of problem -- `subject = ANY($1::text[])` against
`public.estate_event` directly, in one round trip. This module copies that pattern
rather than looping the single-subject route once per exception case, gate decision and
report id this MU touches.

**Provenance is not on this module's own response -- it is its own, separate, lazily
fetched read (`mu_provenance`, this module's other export), Artizent-only, and
deliberately does not call `mu_page` internally to get there.** §15.4's own
client-visibility sentence excludes Provenance entirely, and this section's own fan-out
(`ProvenanceStore.for_subject` is one artefact id at a time, with no batch primitive
anywhere in this codebase) is the one real, un-avoidable cost driver against the ≤300 ms
graph-query budget (§22's own NFR). `mu_provenance` therefore collects its own subject
ids directly (`_provenance_subjects`: the calc field list and the report/documentation
ids only) rather than running the *whole* page assembly just to reach them -- Parity,
Exceptions, Gates and Timeline are irrelevant to Provenance and re-running their own
real queries to serve this one section would defeat the point of keeping it lazy.
Keeping it off the main response, fetched only when an Artizent viewer actually expands
that section, is this story's own literal "artefact previews lazy-load" AC applied to
the heaviest section rather than only to images.

**Measures and Git links are Artizent-only within Artefacts, per §15.4's own literal
"thumbnails and documentation only" for a client reader.** Provenance badges on a
measure are not fetched inline here -- the same fan-out cost as the Provenance section
itself, and every measure's own source calc field already has a real provenance trail
reachable through that lazily-loaded section once a real id is known.

**Exceptions is Artizent-only, per §15.4's own client-visibility list (Exceptions is
absent from it).** Reuses `g3_card._workbook_exception_cases`/`_live_gate_decisions`
wholesale -- the identical cross-epic private-helper precedent `g3_card.py` itself
already sets for these two functions.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any

import asyncpg

from .artefacts import ArtefactStore
from .compositor import read_report
from .errors import ElementNotFoundError
from .events import EventType, StoredEvent
from .foundry_routing import _family_for_workbook  # cross-epic private helper; see module docstring
from .g3_card import (  # cross-epic private helpers; see module docstring
    _live_gate_decisions,
    _workbook_exception_cases,
)
from .g3_card import g3_card as _g3_card
from .graph.queries import EDGE_INDEX_TABLE, NODE_INDEX_TABLE
from .lineage import children, hydrate
from .modeller import read_design_document
from .parity_dashboard import parity_dashboard
from .provenance import ProvenanceRecord, ProvenanceStore
from .report_deploy import ReportDeployStore
from .report_documentation import read_report_documentation
from .scope import ScopeStore, fold
from .tolerance_charter import SUBJECT_REF as G1_SUBJECT_REF
from .trains import DEFAULT_MU_STATE
from .versions import EVENT_TABLE
from .visual_parity import SOURCE_SCREENSHOT_KIND


def _iso(value: datetime | str | None) -> str | None:
    if value is None:
        return None
    if isinstance(value, str):
        return value
    return value.astimezone(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")


async def _require_workbook(conn: asyncpg.Connection, graph_name: str, workbook_id: str) -> dict[str, Any]:
    workbook = (await hydrate(conn, graph_name, "Workbook", [workbook_id])).get(workbook_id)
    if workbook is None:
        raise ElementNotFoundError(f"no Workbook '{workbook_id}'")
    return workbook


async def _parent(
    conn: asyncpg.Connection, graph_name: str, child_id: str, label: str
) -> dict[str, Any] | None:
    """Who ``CONTAINS`` this one child, where the container has the wanted label -- the
    reverse of `lineage.children`, needed to climb Workbook -> Project -> Site."""
    row = await conn.fetchrow(
        f"""
        SELECT e.from_id AS parent
          FROM {EDGE_INDEX_TABLE} e
          JOIN {NODE_INDEX_TABLE} n
            ON n.id = e.from_id AND n.kind = 'node' AND n.graph = $1 AND n.label = $3
           AND n.retired_at IS NULL
         WHERE e.graph = $1 AND e.label = 'CONTAINS' AND e.to_id = $2 AND e.retired_at IS NULL
        """,
        graph_name,
        child_id,
        label,
    )
    if row is None:
        return None
    properties = (await hydrate(conn, graph_name, label, [row["parent"]])).get(row["parent"])
    if properties is None:
        return None
    return {"id": row["parent"], "name": properties.get("name"), **properties}


async def _owner(conn: asyncpg.Connection, graph_name: str, workbook_id: str) -> dict[str, Any] | None:
    row = await conn.fetchrow(
        f"""
        SELECT e.to_id AS owner
          FROM {EDGE_INDEX_TABLE} e
          JOIN {NODE_INDEX_TABLE} n
            ON n.id = e.to_id AND n.kind = 'node' AND n.graph = $1 AND n.label = 'User'
           AND n.retired_at IS NULL
         WHERE e.graph = $1 AND e.label = 'OWNED_BY' AND e.from_id = $2 AND e.retired_at IS NULL
        """,
        graph_name,
        workbook_id,
    )
    if row is None:
        return None
    properties = (await hydrate(conn, graph_name, "User", [row["owner"]])).get(row["owner"], {})
    return {"id": row["owner"], "name": properties.get("display") or properties.get("upn") or row["owner"]}


async def _train(conn: asyncpg.Connection, graph_name: str, workbook_id: str) -> dict[str, Any] | None:
    row = await conn.fetchrow(
        f"""
        SELECT e.id AS edge_id, e.to_id AS train
          FROM {EDGE_INDEX_TABLE} e
          JOIN {NODE_INDEX_TABLE} n
            ON n.id = e.to_id AND n.kind = 'node' AND n.graph = $1 AND n.label = 'ReleaseTrain'
           AND n.retired_at IS NULL
         WHERE e.graph = $1 AND e.label = 'IN_TRAIN' AND e.from_id = $2 AND e.retired_at IS NULL
        """,
        graph_name,
        workbook_id,
    )
    if row is None:
        return None
    edge_properties = (await hydrate(conn, graph_name, "IN_TRAIN", [row["edge_id"]])).get(row["edge_id"], {})
    train_properties = (await hydrate(conn, graph_name, "ReleaseTrain", [row["train"]])).get(row["train"], {})
    return {
        "id": row["train"],
        "name": train_properties.get("name"),
        "sequence": int(edge_properties.get("sequence") or 0),
        "state": str(edge_properties.get("state") or DEFAULT_MU_STATE),
    }


async def _source_section(
    pool: asyncpg.Pool, graph_name: str, artefact_store: ArtefactStore, workbook_id: str,
) -> dict[str, Any]:
    async with pool.acquire() as conn:
        worksheet_ids = sorted(
            (await children(conn, graph_name, [workbook_id], "CONTAINS", "Worksheet")).get(workbook_id, set())
        )
        dashboard_ids = sorted(
            (await children(conn, graph_name, [workbook_id], "CONTAINS", "Dashboard")).get(workbook_id, set())
        )
        datasource_map = await children(conn, graph_name, worksheet_ids, "USES_DATASOURCE", "Datasource")
        datasource_ids = sorted({d for owned in datasource_map.values() for d in owned})
        calc_map = await children(conn, graph_name, worksheet_ids, "ENCODES", "CalculatedField")
        calc_ids = sorted({c for owned in calc_map.values() for c in owned})

        worksheets = await hydrate(conn, graph_name, "Worksheet", worksheet_ids)
        dashboards = await hydrate(conn, graph_name, "Dashboard", dashboard_ids)
        datasources = await hydrate(conn, graph_name, "Datasource", datasource_ids)
        calcs = await hydrate(conn, graph_name, "CalculatedField", calc_ids)

    screenshots = await artefact_store.for_mu(workbook_id, kind=SOURCE_SCREENSHOT_KIND, limit=1)

    def _usage(props: dict[str, Any]) -> dict[str, Any]:
        return {
            "views_90d": props.get("views_90d"),
            "distinct_viewers_90d": props.get("distinct_viewers_90d"),
            "last_view": props.get("last_view"),
        }

    return {
        "worksheets": [
            {"id": wid, "name": props.get("name"), **_usage(props)}
            for wid, props in sorted(worksheets.items(), key=lambda kv: str(kv[1].get("name") or kv[0]))
        ],
        "dashboards": [
            {"id": did, "name": props.get("name"), **_usage(props)}
            for did, props in sorted(dashboards.items(), key=lambda kv: str(kv[1].get("name") or kv[0]))
        ],
        "datasources": [
            {
                "id": did, "name": props.get("name"), "type": props.get("type"),
                "extract_flag": props.get("extract_flag"), "refresh_schedule": props.get("refresh_schedule"),
            }
            for did, props in sorted(datasources.items(), key=lambda kv: str(kv[1].get("name") or kv[0]))
        ],
        "calculated_fields": [
            {"id": cid, "name": props.get("name"), "class": props.get("class"), "formula": props.get("formula")}
            for cid, props in sorted(calcs.items(), key=lambda kv: str(kv[1].get("name") or kv[0]))
        ],
        "screenshot": screenshots[0].as_dict() if screenshots else None,
    }


async def _artefacts_section(
    pool: asyncpg.Pool,
    graph_name: str,
    artefact_store: ArtefactStore,
    report_deploy_store: ReportDeployStore,
    *,
    workbook_id: str,
    family_id: str | None,
) -> dict[str, Any]:
    report = await read_report(pool, graph_name, workbook_id)
    documentation = await read_report_documentation(artefact_store, report=report) if report else None
    deploy = await report_deploy_store.latest(workbook_id)

    measures: list[dict[str, Any]] = []
    if family_id is not None:
        try:
            document = await read_design_document(pool, graph_name, family_id)
        except ElementNotFoundError:
            document = None
        if document is not None:
            measures = [
                {
                    "name": measure.get("name"),
                    "source_calc_refs": measure.get("source_calc_refs") or [],
                    "dedup_decision": measure.get("dedup_decision"),
                }
                for measure in (document.get("candidate_measures") or [])
            ]

    return {
        "model_ref": report.get("model_ref") if report else None,
        "report": report,
        "documentation": documentation,
        "measures": measures,
        "git": deploy.as_dict() if deploy else None,
    }


def _gate_summary(props: dict[str, Any] | None) -> dict[str, Any] | None:
    if props is None:
        return None
    return {
        "decision": props.get("decision"),
        "approver": props.get("approver"),
        "countersigner": props.get("countersigner"),
        "timestamp": props.get("timestamp"),
        "rationale": props.get("rationale"),
    }


def _latest_decision(
    decisions: dict[str, dict[str, Any]], *, gate: str, subject_ref: str | None
) -> dict[str, Any] | None:
    if subject_ref is None:
        return None
    matches = [
        props for props in decisions.values()
        if props.get("gate") == gate and props.get("subject_ref") == subject_ref
    ]
    if not matches:
        return None
    return max(matches, key=lambda props: str(props.get("timestamp") or ""))


async def _gates_section(
    pool: asyncpg.Pool, graph_name: str, *, workbook_id: str, family_id: str | None, site_id: str | None,
) -> dict[str, Any]:
    decisions = await _live_gate_decisions(pool, graph_name)
    g1 = _latest_decision(decisions, gate="G1", subject_ref=G1_SUBJECT_REF)
    g2 = _latest_decision(decisions, gate="G2", subject_ref=family_id)
    g3 = await _g3_card(pool, graph_name, workbook_id=workbook_id)
    g4 = _latest_decision(decisions, gate="G4", subject_ref=site_id)
    return {
        "g1": _gate_summary(g1),
        "g2": _gate_summary(g2),
        "g3": g3,
        "g4": _gate_summary(g4),
    }


async def _timeline_section(
    pool: asyncpg.Pool, graph_name: str, subject_ids: list[str | None]
) -> list[dict[str, Any]]:
    wanted = list(dict.fromkeys(sid for sid in subject_ids if sid))
    if not wanted:
        return []
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            f"""
            SELECT seq, event_id, type, source, subject, label, time, principal, run_id,
                   data, published_at
              FROM {EVENT_TABLE}
             WHERE graph = $1 AND subject = ANY($2::text[])
             ORDER BY seq
            """,
            graph_name,
            wanted,
        )
    events = [
        StoredEvent(
            sequence=row["seq"],
            id=row["event_id"],
            type=EventType(row["type"]),
            source=row["source"],
            subject=row["subject"],
            label=row["label"],
            time=_iso(row["time"]) or "",
            principal=row["principal"],
            run_id=row["run_id"],
            data=json.loads(row["data"]),
            published_at=_iso(row["published_at"]),
        )
        for row in rows
    ]
    return [{"sequence": event.sequence, "event": event.to_cloudevent()} for event in events]


async def mu_page(
    pool: asyncpg.Pool,
    graph_name: str,
    *,
    artefact_store: ArtefactStore,
    report_deploy_store: ReportDeployStore,
    scope_store: ScopeStore,
    workbook_id: str,
) -> dict[str, Any]:
    """The full page -- every section §15.4 names, for an Artizent reader. `None`
    sub-sections (no report yet, no family yet, ...) are honest absences, not errors: a
    fresh harvest with nothing downstream of it still has a real Migration Unit page,
    almost entirely empty."""
    async with pool.acquire() as conn:
        workbook = await _require_workbook(conn, graph_name, workbook_id)
        project = await _parent(conn, graph_name, workbook_id, "Project")
        site = await _parent(conn, graph_name, project["id"], "Site") if project else None
        owner = await _owner(conn, graph_name, workbook_id)
        train = await _train(conn, graph_name, workbook_id)

    history = await scope_store.history(workbook_id)
    current_scope = fold(list(history))

    family_id = await _family_for_workbook(pool, graph_name, workbook_id)
    family: dict[str, Any] | None = None
    if family_id is not None:
        async with pool.acquire() as conn:
            family_properties = (await hydrate(conn, graph_name, "ModelFamily", [family_id])).get(family_id)
        if family_properties is not None:
            family = {"id": family_id, "name": family_properties.get("name"), "state": family_properties.get("state")}

    site_id = site["id"] if site else None

    source = await _source_section(pool, graph_name, artefact_store, workbook_id)
    artefacts = await _artefacts_section(
        pool, graph_name, artefact_store, report_deploy_store, workbook_id=workbook_id, family_id=family_id,
    )
    parity = await parity_dashboard(pool, graph_name, workbook_id=workbook_id)
    exceptions = await _workbook_exception_cases(pool, graph_name, workbook_id)
    decisions = await _live_gate_decisions(pool, graph_name)
    exceptions_with_decisions = [
        {
            "id": case_id,
            **case_properties,
            "decisions": [
                _gate_summary(props) for props in decisions.values()
                if props.get("subject_ref") == case_id
            ],
        }
        for case_id, case_properties in sorted(exceptions.items())
    ]
    gates = await _gates_section(pool, graph_name, workbook_id=workbook_id, family_id=family_id, site_id=site_id)

    timeline_subjects = [workbook_id, family_id, train["id"] if train else None, site_id]
    if artefacts["report"] is not None:
        timeline_subjects.append(artefacts["report"]["id"])
    timeline_subjects.extend(exceptions)
    # G1/G2/G3/G4 decisions all carry `subject_ref` in {workbook_id, family_id, site_id}
    # already collected above -- a decision itself is a distinct node with no separate
    # id worth adding here, so nothing further to append for the Gates section.
    timeline = await _timeline_section(pool, graph_name, timeline_subjects)

    gate_status_strip = [
        {"gate": "G1", "decision": (gates["g1"] or {}).get("decision")},
        {"gate": "G2", "decision": (gates["g2"] or {}).get("decision")},
        {"gate": "G3", "decision": (gates["g3"]["latest_decision"] or {}).get("decision")},
        {"gate": "G4", "decision": (gates["g4"] or {}).get("decision")},
    ]

    return {
        "workbook_id": workbook_id,
        "header": {
            "name": workbook.get("name"),
            "luid": workbook.get("luid"),
            "site": {"id": site["id"], "name": site.get("name")} if site else None,
            "project": {"id": project["id"], "name": project.get("name")} if project else None,
            "state": train["state"] if train else None,
            "tier": current_scope.tier,
            "withdrawn": current_scope.withdrawn,
            "family": family,
            "train": {"id": train["id"], "name": train["name"], "sequence": train["sequence"]} if train else None,
            "owner": owner,
            "gate_status_strip": gate_status_strip,
        },
        "source": source,
        "artefacts": artefacts,
        "parity": parity,
        "exceptions": {"cases": exceptions_with_decisions},
        "gates": gates,
        "timeline": {"events": timeline},
    }


#: The exact keys a Client Report Owner's own reader is allowed -- §15.4's own literal
#: client-visibility sentence, enforced here rather than left to the console to
#: under-request. See this module's own docstring for why this is a slice of one shared
#: assembly rather than a second, separately-computed document.
def mu_page_client_view(full: dict[str, Any]) -> dict[str, Any]:
    header = full["header"]
    artefacts = full["artefacts"]
    return {
        "workbook_id": full["workbook_id"],
        "header": {
            "name": header["name"],
            "luid": header["luid"],
            "site": header["site"],
            "project": header["project"],
            "state": header["state"],
            "tier": header["tier"],
            "withdrawn": header["withdrawn"],
            "family": header["family"],
            "train": header["train"],
            "owner": header["owner"],
            "gate_status_strip": header["gate_status_strip"],
        },
        "source": full["source"],
        "artefacts": {
            "model_ref": None,  # Artizent-only within Artefacts -- see module docstring
            "report": artefacts["report"],
            "documentation": artefacts["documentation"],
            "measures": [],  # Artizent-only
            "git": None,  # Artizent-only
        },
        "parity": full["parity"],
        "gates": full["gates"],
        "timeline": full["timeline"],
    }


async def _provenance_subjects(
    pool: asyncpg.Pool, graph_name: str, artefact_store: ArtefactStore, *, workbook_id: str, family_id: str | None,
) -> list[str]:
    """Every subject id whose provenance record this MU's own Provenance section wants --
    computed directly, not by re-running the full `mu_page` assembly (which would also
    redo Parity/Exceptions/Gates/Timeline's own real queries for facts this section never
    reads). Reuses only the two cheap parts of `_source_section`/`_artefacts_section`
    this section actually needs: the calc field list and the report/documentation ids."""
    subject_ids: list[str] = [workbook_id]
    if family_id is not None:
        subject_ids.append(family_id)

    async with pool.acquire() as conn:
        worksheet_ids = sorted(
            (await children(conn, graph_name, [workbook_id], "CONTAINS", "Worksheet")).get(workbook_id, set())
        )
        calc_map = await children(conn, graph_name, worksheet_ids, "ENCODES", "CalculatedField")
    subject_ids.extend({c for owned in calc_map.values() for c in owned})

    report = await read_report(pool, graph_name, workbook_id)
    if report is not None:
        subject_ids.append(report["id"])
        subject_ids.extend(v["id"] for v in report.get("visuals") or ())
        documentation = await read_report_documentation(artefact_store, report=report)
        if documentation is not None and documentation.get("artefact_id"):
            subject_ids.append(documentation["artefact_id"])

    if family_id is not None:
        try:
            document = await read_design_document(pool, graph_name, family_id)
        except ElementNotFoundError:
            document = None
        if document is not None:
            for measure in document.get("candidate_measures") or ():
                subject_ids.extend(measure.get("source_calc_refs") or ())

    return list(dict.fromkeys(subject_ids))


async def mu_provenance(
    pool: asyncpg.Pool,
    graph_name: str,
    artefact_store: ArtefactStore,
    provenance_store: ProvenanceStore,
    *,
    workbook_id: str,
    mode: str | None = None,
) -> list[dict[str, Any]]:
    """Every artefact's provenance record this MU touches, "filterable by mode" (§15.4)
    -- Artizent-only (see module docstring), and its own separate, lazily fetched call
    so neither a client response nor an Artizent response before this section is
    expanded ever pays for it. Raises `ElementNotFoundError` for an unknown workbook,
    the identical contract `mu_page` gives."""
    async with pool.acquire() as conn:
        await _require_workbook(conn, graph_name, workbook_id)
    family_id = await _family_for_workbook(pool, graph_name, workbook_id)
    subject_ids = await _provenance_subjects(
        pool, graph_name, artefact_store, workbook_id=workbook_id, family_id=family_id,
    )

    records: list[ProvenanceRecord] = []
    for subject_id in subject_ids:
        records.extend(await provenance_store.for_subject(subject_id))
    if mode is not None:
        records = [record for record in records if record.mode.value == mode]
    records.sort(key=lambda record: record.created_at or "")
    return [record.as_dict() for record in records]


__all__ = [
    "mu_page",
    "mu_page_client_view",
    "mu_provenance",
]
