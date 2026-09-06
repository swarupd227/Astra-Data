"""Report documentation, generated from the graph -- story S6.2.2 (F6.2), spec §8.8/§8.11.

    "As a report owner, I want report documentation generated from the graph, so that
    users get a page that says what changed and where things moved.

    Acceptance criteria:
    - One markdown page per report: purpose (from workbook description), pages and
      visuals with their Tableau sheet of origin, measures with source calc names,
      parameters, known differences (C4 decisions, redesigns), model and refresh
    - Generated in ASSISTED mode with provenance; stored as an artefact and linked from
      the MU page"

**Every fact this page states is already a real, composed-and-stored fact -- nothing
here recomputes what `compositor.py` already resolved.** Generation reads back the
current `ReportDefinition`/`Visual` records exactly as `compositor.read_report` returns
them, plus the `CalculatedField`/`Measure`/`Datasource`/`SemanticModel`/`ModelFamily`
nodes those records reference. This is a rendering step over already-composed evidence,
not a second pass of composition.

**§8.11 names the Steward as the agent that drafts report/model documentation; this
platform has no Steward yet, and the backlog places this story in F6.2/E6 anyway.**
Confirmed directly against the spec: §8.3's own agent catalog and §8.11's own narrative
both attribute "report and model documentation... (ASSISTED drafting, deterministic
facts)" to the Steward (E9), gated at G4. No `steward.py` module exists anywhere in this
codebase, and E9 has not been built. But the backlog's own S6.2.2 sits inside F6.2,
immediately after S6.2.1, driven by "report owner," with no G4/ACCEPTED gate at all --
the identical position the Compositor's own E6 stories have already been written from
throughout this epic. This module records `agent="compositor"` rather than a Steward
that does not exist, disclosed here rather than silently borrowing an agent name from an
epic that has not shipped.

**ASSISTED, never a model call -- the same "real, deterministic, reproducible template"
footing `modeller.py`'s grain-statement drafting (S3.1.1/S4.1.2) and `redesign.py`'s own
C4 redesign suggestion (S5.4.1) already established.** Nothing here calls a model
gateway; the markdown is composed entirely from graph facts by a deterministic renderer.
`ContractName.COMPOSITOR_REPORT_DOC` is a third "name only" contract -- the identical
`MODELLER_FAMILY`/`TRANSPILER_C4_REDESIGN` deviation, for the same reason: there is no
inference boundary to police when nothing crosses one.

**"Purpose (from workbook description)" reads the workbook's own name -- `Workbook`
has no `description` property.** Confirmed directly: §4.1.1's own node table declares
none, and no comment/notes/free-text field exists on `Workbook` at all. Harvesting a
real description would be adapter-side work (`packages/adapter-tableau`), out of scope
for a Compositor-layer story -- the same "a real, disclosed gap, not invented data"
posture ADR 0047's own decision 6 already took for a comparable adapter-side limit. Until
then, the workbook's own `name` is the one real, human-authored fact this platform has
for "what this report is."

**"Refresh" can be more than one schedule -- the AC's singular phrasing is read as "the
refresh facts this report has," not "exactly one."** No edge connects a `Workbook`/
`ReportDefinition`/`SemanticModel` directly to a `Datasource`; the only real path is
`Worksheet -> USES_DATASOURCE -> Datasource` (`compositor.py`'s own docstring), and two
worksheets in one report can use different datasources with different
`refresh_schedule`s. Every distinct schedule actually found is listed, rather than
picking one and hiding the rest.

**"Known differences" reads `Visual.redesign_flag`/`.redesign_reason` and
`CalculatedField.class == "C4"`/`.redesign_decision*` directly -- it does not also
surface each visual's own `ExceptionCase` (S6.2.1).** A redesign's *work-item* state
(open/closed, who closed it, the Desktop commit) is the Migration Engineer's own
tracking concern, already served by `GET /v1/exceptions`; this page's job is to tell a
report owner *what* differs and *why*, which `Visual.redesign_reason` and a C4 field's
own guidance/suggestion/decision already say in full, without needing a second lookup
against a queue this reader has no reason to open.

**Generation is a deliberate, separate action -- not automatic on every compose.** The
AC's own wording ("I want report documentation generated") names a distinct
after-the-fact step, the same shape `deploy_workbook` (S6.1.2) already took relative to
`compose_workbook` (S6.1.1) -- composing is cheap and iterative; generating and storing a
documentation artefact each time would spam the artefact store with drafts nobody asked
for. This is unlike S6.2.1's `ExceptionCase`, which the AC phrased causally ("redesign
flags *create* ExceptionCases") and which this module's own sibling therefore opens
automatically during compose.

**"Linked from the MU page" is the same disclosed proxy ADRs 0045-0048 already used four
times.** No MU page exists (F10.3, unbuilt); `ReportDefinition.documentation_artefact_ref`
and `.documentation_provenance_ref` make the link real and queryable today -- from the one
real, existing node this touches -- until a real page exists to render it.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import asyncpg

from .artefacts import ArtefactStore
from .context.canonical import canonical_json, context_hash
from .context.contract import ContractName
from .lineage import children, hydrate
from .principal import Principal
from .provenance import AgentMode, ProvenanceStore, new_record
from .versions import EVENT_TABLE
from .writes import GraphWriter

_AGENT = "compositor"
_AGENT_VERSION = "0.1.0"
ARTEFACT_KIND = "report_documentation"


class ReportDocumentationError(Exception):
    """Documentation cannot be generated or read for this workbook right now."""


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")


async def _current_version(conn: asyncpg.Connection, graph_name: str) -> int:
    """The same one-line definition `classify.py`'s own `_current_version` already uses."""
    row = await conn.fetchrow(
        f"SELECT seq FROM {EVENT_TABLE} WHERE graph = $1 ORDER BY seq DESC LIMIT 1", graph_name
    )
    return int(row["seq"]) if row else 0


# --------------------------------------------------------------------------- gathering


async def _gather_facts(
    pool: asyncpg.Pool, graph_name: str, *, workbook_id: str, report: dict[str, Any]
) -> dict[str, Any]:
    """Read back every fact this page states -- nothing recomputed, only hydrated."""
    visuals: list[dict[str, Any]] = report["visuals"]

    sheet_ids = sorted({v["source_sheet_ref"] for v in visuals if v.get("source_sheet_ref")})
    calc_ids: set[str] = set()
    measure_ids: set[str] = set()
    for visual in visuals:
        for well in visual.get("encodings", {}).get("field_wells", []):
            if well.get("sourceKind") == "CalculatedField" and well.get("sourceId"):
                calc_ids.add(well["sourceId"])
            if well.get("measureId"):
                measure_ids.add(well["measureId"])

    async with pool.acquire() as conn:
        workbook = (await hydrate(conn, graph_name, "Workbook", [workbook_id])).get(workbook_id)
        if workbook is None:
            raise ReportDocumentationError(f"no Workbook '{workbook_id}'")

        worksheets = await hydrate(conn, graph_name, "Worksheet", sheet_ids)
        calcs = await hydrate(conn, graph_name, "CalculatedField", sorted(calc_ids))
        measures = await hydrate(conn, graph_name, "Measure", sorted(measure_ids))

        datasource_map = await children(conn, graph_name, sheet_ids, "USES_DATASOURCE", "Datasource")
        datasource_ids = sorted({d for ds in datasource_map.values() for d in ds})
        datasources = await hydrate(conn, graph_name, "Datasource", datasource_ids)

        model_ref = report["model_ref"]
        semantic_model = (await hydrate(conn, graph_name, "SemanticModel", [model_ref])).get(model_ref)
        family = None
        family_ref = (semantic_model or {}).get("family_ref")
        if family_ref:
            family = (await hydrate(conn, graph_name, "ModelFamily", [family_ref])).get(family_ref)

        graph_version = await _current_version(conn, graph_name)

    pages: list[dict[str, Any]] = []
    for page in report.get("pages") or []:
        page_visuals = [v for v in visuals if v.get("page") == page]
        pages.append(
            {
                "page": page,
                "visuals": [
                    {
                        "type": v.get("type"),
                        "source_sheet_name": (worksheets.get(v.get("source_sheet_ref") or "") or {}).get("name"),
                        "redesign_flag": bool(v.get("redesign_flag")),
                        "redesign_reason": v.get("redesign_reason"),
                    }
                    for v in page_visuals
                ],
            }
        )

    measures_doc: list[dict[str, Any]] = []
    seen_calc: set[str] = set()
    for visual in visuals:
        for well in visual.get("encodings", {}).get("field_wells", []):
            calc_id = well.get("sourceId")
            if well.get("sourceKind") != "CalculatedField" or not calc_id or calc_id in seen_calc:
                continue
            seen_calc.add(calc_id)
            calc = calcs.get(calc_id) or {}
            measure_id = well.get("measureId")
            measure = measures.get(measure_id) if measure_id else None
            measures_doc.append(
                {
                    "calc_id": calc_id,
                    "calc_name": calc.get("name") or well.get("sourceName"),
                    "measure_id": measure_id,
                    "measure_name": (measure or {}).get("name"),
                    "bound": bool(well.get("bound")),
                }
            )
    measures_doc.sort(key=lambda m: str(m["calc_name"] or ""))

    parameters_doc: list[dict[str, Any]] = []
    seen_param: set[str] = set()
    for visual in visuals:
        for parameter in visual.get("interactivity", {}).get("parameters", []):
            name = parameter.get("name")
            if not name or name in seen_param:
                continue
            seen_param.add(name)
            parameters_doc.append(parameter)
    parameters_doc.sort(key=lambda p: str(p["name"] or ""))

    c4_decisions: list[dict[str, Any]] = []
    for calc_id in sorted(calcs):
        calc = calcs[calc_id]
        if calc.get("class") != "C4":
            continue
        c4_decisions.append(
            {
                "calc_id": calc_id,
                "calc_name": calc.get("name"),
                "reason": calc.get("reason"),
                "appendix_b_guidance": calc.get("appendix_b_guidance"),
                "redesign_suggestion": calc.get("redesign_suggestion"),
                "decision": calc.get("redesign_decision"),
                "decision_reason": calc.get("redesign_decision_reason"),
                "decision_by": calc.get("redesign_decision_by"),
                "decision_at": calc.get("redesign_decision_at"),
            }
        )

    redesigns: list[dict[str, Any]] = []
    for visual in visuals:
        if not visual.get("redesign_flag"):
            continue
        redesigns.append(
            {
                "page": visual.get("page"),
                "type": visual.get("type"),
                "source_sheet_name": (worksheets.get(visual.get("source_sheet_ref") or "") or {}).get("name"),
                "reason": visual.get("redesign_reason"),
            }
        )

    schedules: set[str] = set()
    for datasource in datasources.values():
        schedule = datasource.get("refresh_schedule")
        if isinstance(schedule, str) and schedule:
            schedules.add(schedule)
    refresh_schedules = sorted(schedules)

    return {
        "workbook_id": workbook_id,
        "workbook_name": workbook.get("name"),
        "report_id": report["id"],
        "pages": pages,
        "measures": measures_doc,
        "parameters": parameters_doc,
        "c4_decisions": c4_decisions,
        "redesigns": redesigns,
        "model": {
            "semantic_model_id": model_ref,
            "family_name": (family or {}).get("name"),
            "grain_statement": (semantic_model or {}).get("grain_statement"),
            "version_number": (semantic_model or {}).get("version_number") or 1,
            "state": (semantic_model or {}).get("state"),
        },
        "refresh_schedules": refresh_schedules,
        "graph_version": graph_version,
    }


# --------------------------------------------------------------------------- rendering


def render_markdown(facts: dict[str, Any], *, generated_at: str) -> str:
    """Pure: every fact this page states comes straight from `facts` -- no I/O here."""
    lines: list[str] = []
    lines.append(f"# {facts['workbook_name']} — Report Documentation")
    lines.append("")
    lines.append(
        f"_Generated {generated_at} in ASSISTED mode by {_AGENT} v{_AGENT_VERSION}, "
        f"from graph version {facts['graph_version']}._"
    )
    lines.append("")

    lines.append("## Purpose")
    lines.append("")
    lines.append(f"{facts['workbook_name']} (from the source workbook's own name).")
    lines.append("")

    lines.append("## Pages and visuals")
    lines.append("")
    for page in facts["pages"]:
        lines.append(f"### {page['page']}")
        lines.append("")
        if not page["visuals"]:
            lines.append("_No visuals on this page._")
        for visual in page["visuals"]:
            sheet = visual["source_sheet_name"] or "(no source sheet)"
            marker = " — **flagged for redesign**" if visual["redesign_flag"] else ""
            lines.append(f"- **{visual['type']}** — from Tableau sheet \"{sheet}\"{marker}")
        lines.append("")

    lines.append("## Measures")
    lines.append("")
    if facts["measures"]:
        lines.append("| Measure | Source calculation |")
        lines.append("|---|---|")
        for measure in facts["measures"]:
            measure_name = measure["measure_name"] or "_not yet generated_"
            lines.append(f"| {measure_name} | {measure['calc_name']} |")
    else:
        lines.append("_No calculated-field measures in this report._")
    lines.append("")

    lines.append("## Parameters")
    lines.append("")
    if facts["parameters"]:
        lines.append("| Parameter | Power BI construct | Notes |")
        lines.append("|---|---|---|")
        for parameter in facts["parameters"]:
            construct = parameter.get("kind") or "_not supported_"
            notes = parameter.get("reason") or ""
            lines.append(f"| {parameter['name']} | {construct} | {notes} |")
    else:
        lines.append("_No parameters in this report._")
    lines.append("")

    lines.append("## Known differences")
    lines.append("")
    lines.append("### C4 calculations needing a redesign decision")
    lines.append("")
    if facts["c4_decisions"]:
        for item in facts["c4_decisions"]:
            decision = item["decision"] or "_not yet recorded (blocked)_"
            lines.append(f"- **{item['calc_name']}** — {item['reason']}")
            lines.append(f"  - Appendix B guidance: {item['appendix_b_guidance']}")
            lines.append(f"  - Suggestion: {item['redesign_suggestion']}")
            lines.append(f"  - Decision: {decision}")
            if item["decision_reason"]:
                lines.append(f"  - Rationale: {item['decision_reason']}")
    else:
        lines.append("_No C4 calculations in this report._")
    lines.append("")
    lines.append("### Redesigned visuals")
    lines.append("")
    if facts["redesigns"]:
        for item in facts["redesigns"]:
            sheet = item["source_sheet_name"] or "(no source sheet)"
            lines.append(f"- **{item['page']} / {item['type']}** (from \"{sheet}\") — {item['reason']}")
    else:
        lines.append("_No visuals were flagged for redesign._")
    lines.append("")

    lines.append("## Model")
    lines.append("")
    model = facts["model"]
    lines.append(f"- Family: {model['family_name'] or '(unknown)'}")
    lines.append(f"- Grain: {model['grain_statement'] or '(not yet drafted)'}")
    lines.append(f"- Version: {model['version_number']} ({model['state'] or 'unknown state'})")
    lines.append("")

    lines.append("## Refresh")
    lines.append("")
    if facts["refresh_schedules"]:
        for schedule in facts["refresh_schedules"]:
            lines.append(f"- {schedule}")
        if len(facts["refresh_schedules"]) > 1:
            lines.append("")
            lines.append(
                "_This report's worksheets use more than one datasource with a distinct "
                "refresh schedule -- every one found is listed above._"
            )
    else:
        lines.append("_No refresh schedule recorded on this report's datasources._")

    return "\n".join(lines) + "\n"


# --------------------------------------------------------------------------- orchestration


async def generate_report_documentation(
    pool: asyncpg.Pool,
    graph_name: str,
    writer: GraphWriter,
    *,
    workbook_id: str,
    report: dict[str, Any],
    artefact_store: ArtefactStore,
    provenance_store: ProvenanceStore,
    principal: Principal,
) -> dict[str, Any]:
    """Gather, render, store as an artefact, record provenance, and link from the one
    real node this touches (`ReportDefinition`) -- the whole of what the AC asks for."""
    facts = await _gather_facts(pool, graph_name, workbook_id=workbook_id, report=report)
    generated_at = _now()
    markdown = render_markdown(facts, generated_at=generated_at)
    content = markdown.encode("utf-8")

    artefact = await artefact_store.store(
        kind=ARTEFACT_KIND,
        mu_ref=workbook_id,
        case_id=report["id"],
        content=content,
        media_type="text/markdown",
        created_by=principal.value,
    )

    record = new_record(
        artefact_kind="REPORT_DOCUMENTATION",
        artefact_ref=artefact.id,
        artefact_content_hash=artefact.content_hash,
        agent=_AGENT,
        agent_version=_AGENT_VERSION,
        mode=AgentMode.ASSISTED,
        contract=ContractName.COMPOSITOR_REPORT_DOC,
        subject_id=report["id"],
        context_hash=context_hash(canonical_json(facts)),
        graph_version=facts["graph_version"],
        model=None,
        created_by=principal.value,
    )
    provenance = await provenance_store.record(record)

    await writer.set_node_properties(
        report["id"],
        {"documentation_artefact_ref": artefact.id, "documentation_provenance_ref": provenance.id},
        principal=principal,
    )

    return {
        "report_id": report["id"],
        "workbook_id": workbook_id,
        "artefact_id": artefact.id,
        "provenance_id": provenance.id,
        "generated_at": generated_at,
    }


async def read_report_documentation(
    artefact_store: ArtefactStore, *, report: dict[str, Any]
) -> dict[str, Any] | None:
    """The latest generated documentation for an already-read report -- `None` if none has
    ever been generated for it."""
    artefact_id = report.get("documentation_artefact_ref")
    if not artefact_id:
        return None
    record = await artefact_store.get(artefact_id)
    content = await artefact_store.content(artefact_id)
    if record is None or content is None:
        return None
    return {
        "report_id": report["id"],
        "artefact_id": artefact_id,
        "provenance_id": report.get("documentation_provenance_ref"),
        "generated_at": record.recorded_at,
        "content": content.decode("utf-8"),
    }


__all__ = [
    "ARTEFACT_KIND",
    "ReportDocumentationError",
    "generate_report_documentation",
    "read_report_documentation",
    "render_markdown",
]
