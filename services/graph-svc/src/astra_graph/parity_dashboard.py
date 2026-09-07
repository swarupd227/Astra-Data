"""The Parity Dashboard's own aggregation -- story S7.4.2, closing F7.4.

    "As a report owner, I want a Parity Dashboard and per-run view in plain language,
    so that I can see whether my report is right without reading a diff.

    Acceptance criteria:
    - Per sheet: cases run, pass, fail, inconclusive, first-pass rate, waived count;
      failing cells shown as a table with expected / candidate / delta and the filter
      context
    - Per MU: pass rate trend across runs and Mender passes
    - A single 'this report passes the charter' statement with the charter version when
      all cases pass"

Read-only: every fact here is computed live from the `ParityRun`/`Verdict`/`ParityCase`
nodes S7.4.1's own `verdicts.py` already writes; this module writes nothing.

**The backlog's own report-owner, plain-language ask is narrower than §15.3.5's own
"Parity Dashboard (parity engineer default)" row** -- confirmed by direct research: the
spec's own screen is a denser, more technical surface (a KPI strip, a heat grid of MUs
x sheets coloured by verdict, a failure-class histogram over time, a pattern-retirements
feed) aimed at the Parity Engineer. This module builds exactly what the backlog's own AC
asks for instead -- per-sheet counts, a failing-cells table, a per-MU trend, the pass
statement -- a real, disclosed divergence from the spec's own fuller technical view, not
a silent narrowing of it. A future story building the spec's own parity-engineer-default
screen would be additive, not a replacement of this one.

**"First-pass rate" is defined at MU grain in the spec (§16.6: "MUs whose first
ParityRun is all-PASS / MUs proved") and applied here per case, then aggregated per
sheet** -- the AC's own "per sheet" framing has no literal spec definition at that
grain, so the natural elaboration is the same "first run" concept applied to what is
actually being asked about here (a sheet's own cases, not the whole MU): for each case,
its own first-ever `Verdict` (by the `ParityRun` it belongs to, ordered by `started`) is
checked for PASS; a sheet's own first-pass rate is (cases whose first verdict was PASS)
/ (cases with any verdict at all).

**Waived count is a real, live `GateDecision` query, honestly zero today.**
`GateDecision(decision="WAIVED", subject_ref=<case id>)` is the real mechanism §4.1.1
already declares (confirmed: no `ParityCase.waived` property exists anywhere); no story
has ever written one, since recording a case waiver is F8.3's own later Exception Desk
scope (§11.3) -- the same "build the real mechanism, disclose it is honestly unpopulated
today" posture this codebase has already applied to `Field -> ModelTable` `MAPS_TO` six
times over.

**"Mender passes" is disclosed absent, not estimated.** Confirmed by direct research
(grepped the whole codebase, checked the backlog): the Mender is a fully specified E8
concept (§8.10, a "Mender pass" is glossary-defined as "one bounded iteration of
classify -> fix -> re-prove") that no story has built any part of yet -- no
`MenderPass`-shaped node, property, or event exists anywhere. Rather than inventing a
placeholder metric, the trend's own `mender_passes` field states plainly that E8 has not
recorded one yet.

**Current per-sheet counts (cases run/pass/fail/inconclusive) come from the *latest*
`ParityRun`'s own verdicts, not a "most recent verdict per case" scan across every
run.** This assumes every run covers the workbook's full live case set -- true today,
since `run_parity_for_workbook` (S7.4.1) always diffs every executed case for the whole
workbook, never a subset. A future story that lets a Parity Engineer re-run a narrower
subset of cases would need to revisit this assumption; disclosed here rather than
silently relied on.
"""

from __future__ import annotations

from typing import Any

import asyncpg

from .graph.queries import NODE_INDEX_TABLE
from .lineage import hydrate


async def _all_parity_runs_for_workbook(
    conn: asyncpg.Connection, graph: str, workbook_id: str
) -> dict[str, dict[str, Any]]:
    rows = await conn.fetch(
        f"""SELECT id FROM {NODE_INDEX_TABLE}
         WHERE graph = $1 AND kind = 'node' AND label = 'ParityRun' AND retired_at IS NULL""",
        graph,
    )
    all_runs = await hydrate(conn, graph, "ParityRun", [row["id"] for row in rows])
    return {rid: props for rid, props in all_runs.items() if props.get("suite_ref") == workbook_id}


async def _waived_case_ids(conn: asyncpg.Connection, graph: str, case_ids: set[str]) -> set[str]:
    if not case_ids:
        return set()
    rows = await conn.fetch(
        f"""SELECT id FROM {NODE_INDEX_TABLE}
         WHERE graph = $1 AND kind = 'node' AND label = 'GateDecision' AND retired_at IS NULL""",
        graph,
    )
    decisions = await hydrate(conn, graph, "GateDecision", [row["id"] for row in rows])
    return {
        str(props.get("subject_ref"))
        for props in decisions.values()
        if props.get("decision") == "WAIVED" and props.get("subject_ref") in case_ids
    }


def aggregate_dashboard(
    *,
    workbook_id: str,
    runs: dict[str, dict[str, Any]],
    verdicts: dict[str, dict[str, Any]],
    cases: dict[str, dict[str, Any]],
    sheets: dict[str, dict[str, Any]],
    waived_case_ids: set[str],
) -> dict[str, Any]:
    """The pure aggregation step -- no database, every input already hydrated. This is
    what lets the per-sheet/first-pass-rate/trend logic be tested directly, the same
    "pure core, graph-coupled shell" split `diff.py`/`case_execution.py` already
    established (story S7.4.1's own precedent, reused here for a read path instead of a
    write one). Callers must pass a non-empty ``runs``; `parity_dashboard` is what
    decides the "no run yet" honest-absence case, one level up."""
    ordered_run_ids = sorted(
        runs, key=lambda rid: str(runs[rid].get("started") or runs[rid].get("finished") or "")
    )
    latest_run_id = ordered_run_ids[-1]
    latest_run = runs[latest_run_id]

    # --- the per-MU trend: one entry per run, in time order ---
    run_trend: list[dict[str, Any]] = []
    for rid in ordered_run_ids:
        props = runs[rid]
        results = [verdicts[vid]["result"] for vid in (props.get("verdicts") or []) if vid in verdicts]
        total = len(results)
        passed = results.count("PASS")
        run_trend.append({
            "run_id": rid, "started": props.get("started"), "finished": props.get("finished"),
            "charter_version": props.get("charter_version"),
            "cases": total, "pass": passed, "fail": results.count("FAIL"),
            "inconclusive": results.count("INCONCLUSIVE"),
            "pass_rate": (passed / total) if total else None,
        })

    # --- each case's own first-ever verdict, across every run, for first-pass rate ---
    first_verdict_by_case: dict[str, dict[str, Any]] = {}
    for rid in ordered_run_ids:
        for vid in runs[rid].get("verdicts") or []:
            verdict = verdicts.get(vid)
            if verdict is None:
                continue
            case_ref = str(verdict.get("case_ref"))
            first_verdict_by_case.setdefault(case_ref, verdict)  # first time seen wins, runs already in order

    # --- each case's own latest verdict, from the latest run alone (see module docstring) ---
    latest_verdict_by_case: dict[str, dict[str, Any]] = {}
    for vid in latest_run.get("verdicts") or []:
        verdict = verdicts.get(vid)
        if verdict is not None:
            latest_verdict_by_case[str(verdict["case_ref"])] = verdict

    # --- per-sheet aggregation ---
    sheet_stats: dict[str, dict[str, Any]] = {}
    for case_id, case_properties in cases.items():
        sheet_ref = case_properties.get("sheet_ref")
        latest_verdict = latest_verdict_by_case.get(case_id)
        if not sheet_ref or latest_verdict is None:
            continue  # never diffed, or has no sheet -- neither is a real case for this dashboard

        stat = sheet_stats.setdefault(str(sheet_ref), {
            "sheet_ref": sheet_ref,
            "sheet_name": (sheets.get(str(sheet_ref)) or {}).get("name") or sheet_ref,
            "cases_run": 0, "pass": 0, "fail": 0, "inconclusive": 0,
            "waived_count": 0, "failing_cells": [],
            "_first_pass": 0, "_first_total": 0,
        })
        stat["cases_run"] += 1
        stat[str(latest_verdict["result"]).lower()] += 1
        if case_id in waived_case_ids:
            stat["waived_count"] += 1

        first_verdict = first_verdict_by_case.get(case_id)
        if first_verdict is not None:
            stat["_first_total"] += 1
            if first_verdict.get("result") == "PASS":
                stat["_first_pass"] += 1

        if latest_verdict["result"] == "FAIL":
            for cell in latest_verdict.get("failing_cells") or []:
                stat["failing_cells"].append({
                    **cell, "case_id": case_id, "filter_ctx": case_properties.get("filter_ctx") or {},
                })

    sheets_out: list[dict[str, Any]] = []
    for stat in sheet_stats.values():
        first_total = stat.pop("_first_total")
        first_pass = stat.pop("_first_pass")
        stat["first_pass_rate"] = (first_pass / first_total) if first_total else None
        sheets_out.append(stat)
    sheets_out.sort(key=lambda s: str(s["sheet_name"]))

    latest_results = [verdicts[vid]["result"] for vid in (latest_run.get("verdicts") or []) if vid in verdicts]
    passes_the_charter = bool(latest_results) and all(result == "PASS" for result in latest_results)

    return {
        "workbook_id": workbook_id,
        "charter_version": latest_run.get("charter_version"),
        "passes_the_charter": passes_the_charter,
        "latest_run_id": latest_run_id,
        "sheets": sheets_out,
        "trend": {
            "runs": run_trend,
            "mender_passes": {
                "available": False,
                "detail": "the Mender is E8's own unbuilt scope; no Mender pass has ever been recorded",
            },
        },
    }


async def parity_dashboard(
    pool: asyncpg.Pool, graph_name: str, *, workbook_id: str
) -> dict[str, Any] | None:
    """The AC's own dashboard: per-sheet stats, the per-MU trend, and the pass
    statement. `None` when no `ParityRun` has ever covered this workbook (the same
    "honest absence" `latest_parity_run` already returns for the identical case). Reads
    everything, then delegates the actual aggregation to `aggregate_dashboard`."""
    async with pool.acquire() as conn:
        runs = await _all_parity_runs_for_workbook(conn, graph_name, workbook_id)
        if not runs:
            return None

        all_verdict_ids = sorted({vid for props in runs.values() for vid in (props.get("verdicts") or [])})
        all_verdicts = await hydrate(conn, graph_name, "Verdict", all_verdict_ids)

        case_rows = await conn.fetch(
            f"""SELECT id FROM {NODE_INDEX_TABLE}
             WHERE graph = $1 AND kind = 'node' AND label = 'ParityCase' AND retired_at IS NULL""",
            graph_name,
        )
        all_cases = await hydrate(conn, graph_name, "ParityCase", [row["id"] for row in case_rows])
        cases = {cid: props for cid, props in all_cases.items() if props.get("mu_ref") == workbook_id}

        sheet_ids = sorted({str(props["sheet_ref"]) for props in cases.values() if props.get("sheet_ref")})
        sheets = await hydrate(conn, graph_name, "Worksheet", sheet_ids)

        waived_case_ids = await _waived_case_ids(conn, graph_name, set(cases))

    return aggregate_dashboard(
        workbook_id=workbook_id, runs=runs, verdicts=all_verdicts, cases=cases,
        sheets=sheets, waived_case_ids=waived_case_ids,
    )


__all__ = ["aggregate_dashboard", "parity_dashboard"]
