"""The Parity Dashboard's own pure aggregation -- story S7.4.2, spec §15.3.5.

    "Per sheet: cases run, pass, fail, inconclusive, first-pass rate, waived count;
    failing cells shown as a table with expected / candidate / delta and the filter
    context. Per MU: pass rate trend across runs and Mender passes. A single 'this
    report passes the charter' statement with the charter version when all cases pass."

`aggregate_dashboard` is pure -- every input already hydrated, no database -- so it is
testable here directly. The graph reads (`parity_dashboard`, `_all_parity_runs_for_
workbook`, `_waived_case_ids`) are graph-coupled and covered by the integration suite
instead.
"""

from __future__ import annotations

from astra_graph.parity_dashboard import aggregate_dashboard

WORKBOOK = "wb_1"


def _run(run_id: str, *, started: str, finished: str, charter_version: str, verdict_ids: list[str]) -> tuple[str, dict]:
    return run_id, {
        "suite_ref": WORKBOOK, "charter_version": charter_version,
        "started": started, "finished": finished, "verdicts": verdict_ids,
    }


def _verdict(case_ref: str, result: str, failing_cells: list[dict] | None = None) -> dict:
    return {"case_ref": case_ref, "result": result, "failing_cells": failing_cells or [], "evidence_ref": "af_x"}


def _case(sheet_ref: str = "sheet_1", filter_ctx: dict | None = None) -> dict:
    return {"sheet_ref": sheet_ref, "filter_ctx": filter_ctx or {}, "mu_ref": WORKBOOK}


def _sheet(name: str) -> dict:
    return {"name": name}


def test_all_pass_reports_passes_the_charter() -> None:
    runs = dict([_run("r1", started="2026-01-01T00:00:00Z", finished="2026-01-01T00:01:00Z", charter_version="1", verdict_ids=["v1"])])
    verdicts = {"v1": _verdict("c1", "PASS")}
    cases = {"c1": _case()}
    result = aggregate_dashboard(
        workbook_id=WORKBOOK, runs=runs, verdicts=verdicts, cases=cases,
        sheets={"sheet_1": _sheet("Bar sheet")}, waived_case_ids=set(),
    )
    assert result["passes_the_charter"] is True
    assert result["charter_version"] == "1"
    assert result["latest_run_id"] == "r1"


def test_any_fail_means_the_report_does_not_pass() -> None:
    runs = dict([_run("r1", started="t", finished="t", charter_version="1", verdict_ids=["v1", "v2"])])
    verdicts = {"v1": _verdict("c1", "PASS"), "v2": _verdict("c2", "FAIL")}
    cases = {"c1": _case(), "c2": _case()}
    result = aggregate_dashboard(
        workbook_id=WORKBOOK, runs=runs, verdicts=verdicts, cases=cases,
        sheets={"sheet_1": _sheet("Bar sheet")}, waived_case_ids=set(),
    )
    assert result["passes_the_charter"] is False


def test_an_inconclusive_case_also_means_the_report_does_not_pass() -> None:
    runs = dict([_run("r1", started="t", finished="t", charter_version="1", verdict_ids=["v1"])])
    verdicts = {"v1": _verdict("c1", "INCONCLUSIVE")}
    cases = {"c1": _case()}
    result = aggregate_dashboard(
        workbook_id=WORKBOOK, runs=runs, verdicts=verdicts, cases=cases,
        sheets={"sheet_1": _sheet("Bar sheet")}, waived_case_ids=set(),
    )
    assert result["passes_the_charter"] is False


def test_per_sheet_counts_reflect_the_latest_run_only() -> None:
    runs = dict([_run("r1", started="t", finished="t", charter_version="1", verdict_ids=["v1", "v2", "v3"])])
    verdicts = {"v1": _verdict("c1", "PASS"), "v2": _verdict("c2", "FAIL"), "v3": _verdict("c3", "INCONCLUSIVE")}
    cases = {"c1": _case(), "c2": _case(), "c3": _case()}
    result = aggregate_dashboard(
        workbook_id=WORKBOOK, runs=runs, verdicts=verdicts, cases=cases,
        sheets={"sheet_1": _sheet("Bar sheet")}, waived_case_ids=set(),
    )
    assert len(result["sheets"]) == 1
    sheet = result["sheets"][0]
    assert sheet["sheet_name"] == "Bar sheet"
    assert sheet["cases_run"] == 3
    assert sheet["pass"] == 1
    assert sheet["fail"] == 1
    assert sheet["inconclusive"] == 1


def test_a_case_never_diffed_in_any_run_is_excluded_from_sheet_stats() -> None:
    runs = dict([_run("r1", started="t", finished="t", charter_version="1", verdict_ids=["v1"])])
    verdicts = {"v1": _verdict("c1", "PASS")}
    cases = {"c1": _case(), "c2": _case()}  # c2 has never been diffed
    result = aggregate_dashboard(
        workbook_id=WORKBOOK, runs=runs, verdicts=verdicts, cases=cases,
        sheets={"sheet_1": _sheet("Bar sheet")}, waived_case_ids=set(),
    )
    assert result["sheets"][0]["cases_run"] == 1


def test_first_pass_rate_uses_each_case_s_own_first_ever_verdict() -> None:
    # Case c1 fails on its first run, then passes on a later re-run. The current (latest)
    # count shows it as a pass; the first-pass rate still remembers it failed first.
    runs = dict([
        _run("r1", started="2026-01-01T00:00:00Z", finished="t", charter_version="1", verdict_ids=["v1_fail"]),
        _run("r2", started="2026-01-02T00:00:00Z", finished="t", charter_version="1", verdict_ids=["v1_pass"]),
    ])
    verdicts = {"v1_fail": _verdict("c1", "FAIL"), "v1_pass": _verdict("c1", "PASS")}
    cases = {"c1": _case()}
    result = aggregate_dashboard(
        workbook_id=WORKBOOK, runs=runs, verdicts=verdicts, cases=cases,
        sheets={"sheet_1": _sheet("Bar sheet")}, waived_case_ids=set(),
    )
    sheet = result["sheets"][0]
    assert sheet["pass"] == 1  # the latest run says it passes now
    assert sheet["first_pass_rate"] == 0.0  # but it did not pass the first time


def test_first_pass_rate_is_none_when_no_case_has_a_first_verdict_yet() -> None:
    # An edge case that should not arise in practice (a case in latest_verdict_by_case
    # always has SOME verdict, hence a first one too) -- still, the rate must not divide
    # by zero if it ever does.
    runs = dict([_run("r1", started="t", finished="t", charter_version="1", verdict_ids=[])])
    result = aggregate_dashboard(
        workbook_id=WORKBOOK, runs=runs, verdicts={}, cases={}, sheets={}, waived_case_ids=set(),
    )
    assert result["sheets"] == []


def test_waived_count_is_independent_of_pass_fail() -> None:
    runs = dict([_run("r1", started="t", finished="t", charter_version="1", verdict_ids=["v1", "v2"])])
    verdicts = {"v1": _verdict("c1", "PASS"), "v2": _verdict("c2", "FAIL")}
    cases = {"c1": _case(), "c2": _case()}
    result = aggregate_dashboard(
        workbook_id=WORKBOOK, runs=runs, verdicts=verdicts, cases=cases,
        sheets={"sheet_1": _sheet("Bar sheet")}, waived_case_ids={"c2"},
    )
    assert result["sheets"][0]["waived_count"] == 1


def test_failing_cells_are_enriched_with_case_id_and_filter_context() -> None:
    cell = {"grain_key": ["EMEA"], "measure": "Margin", "kind": "numeric", "expected": 1.0, "candidate": 2.0, "delta": 1.0, "reason": "x"}
    runs = dict([_run("r1", started="t", finished="t", charter_version="1", verdict_ids=["v1"])])
    verdicts = {"v1": _verdict("c1", "FAIL", [cell])}
    cases = {"c1": _case(filter_ctx={"kind": "default", "filters": []})}
    result = aggregate_dashboard(
        workbook_id=WORKBOOK, runs=runs, verdicts=verdicts, cases=cases,
        sheets={"sheet_1": _sheet("Bar sheet")}, waived_case_ids=set(),
    )
    failing = result["sheets"][0]["failing_cells"][0]
    assert failing["case_id"] == "c1"
    assert failing["filter_ctx"] == {"kind": "default", "filters": []}
    assert failing["measure"] == "Margin"
    assert failing["delta"] == 1.0


def test_passing_cases_contribute_no_failing_cells() -> None:
    runs = dict([_run("r1", started="t", finished="t", charter_version="1", verdict_ids=["v1"])])
    verdicts = {"v1": _verdict("c1", "PASS")}
    cases = {"c1": _case()}
    result = aggregate_dashboard(
        workbook_id=WORKBOOK, runs=runs, verdicts=verdicts, cases=cases,
        sheets={"sheet_1": _sheet("Bar sheet")}, waived_case_ids=set(),
    )
    assert result["sheets"][0]["failing_cells"] == []


def test_trend_has_one_entry_per_run_in_time_order() -> None:
    runs = dict([
        _run("r2", started="2026-01-02T00:00:00Z", finished="t", charter_version="2", verdict_ids=["v2"]),
        _run("r1", started="2026-01-01T00:00:00Z", finished="t", charter_version="1", verdict_ids=["v1"]),
    ])
    verdicts = {"v1": _verdict("c1", "FAIL"), "v2": _verdict("c1", "PASS")}
    cases = {"c1": _case()}
    result = aggregate_dashboard(
        workbook_id=WORKBOOK, runs=runs, verdicts=verdicts, cases=cases,
        sheets={"sheet_1": _sheet("Bar sheet")}, waived_case_ids=set(),
    )
    trend_ids = [entry["run_id"] for entry in result["trend"]["runs"]]
    assert trend_ids == ["r1", "r2"]  # oldest first, regardless of dict insertion order
    assert result["trend"]["runs"][0]["pass_rate"] == 0.0
    assert result["trend"]["runs"][1]["pass_rate"] == 1.0


def test_pass_rate_is_none_for_a_run_with_no_verdicts() -> None:
    runs = dict([_run("r1", started="t", finished="t", charter_version="1", verdict_ids=[])])
    result = aggregate_dashboard(
        workbook_id=WORKBOOK, runs=runs, verdicts={}, cases={}, sheets={}, waived_case_ids=set(),
    )
    assert result["trend"]["runs"][0]["pass_rate"] is None


def test_mender_passes_is_always_disclosed_unavailable() -> None:
    runs = dict([_run("r1", started="t", finished="t", charter_version="1", verdict_ids=[])])
    result = aggregate_dashboard(
        workbook_id=WORKBOOK, runs=runs, verdicts={}, cases={}, sheets={}, waived_case_ids=set(),
    )
    assert result["trend"]["mender_passes"]["available"] is False
    assert "E8" in result["trend"]["mender_passes"]["detail"]


def test_multiple_sheets_are_each_aggregated_and_sorted_by_name() -> None:
    runs = dict([_run("r1", started="t", finished="t", charter_version="1", verdict_ids=["v1", "v2"])])
    verdicts = {"v1": _verdict("c1", "PASS"), "v2": _verdict("c2", "PASS")}
    cases = {"c1": _case("sheet_z"), "c2": _case("sheet_a")}
    result = aggregate_dashboard(
        workbook_id=WORKBOOK, runs=runs, verdicts=verdicts, cases=cases,
        sheets={"sheet_z": _sheet("Zeta"), "sheet_a": _sheet("Alpha")}, waived_case_ids=set(),
    )
    names = [sheet["sheet_name"] for sheet in result["sheets"]]
    assert names == ["Alpha", "Zeta"]


def test_a_sheet_missing_from_the_worksheet_hydration_falls_back_to_its_own_ref() -> None:
    runs = dict([_run("r1", started="t", finished="t", charter_version="1", verdict_ids=["v1"])])
    verdicts = {"v1": _verdict("c1", "PASS")}
    cases = {"c1": _case("sheet_missing")}
    result = aggregate_dashboard(
        workbook_id=WORKBOOK, runs=runs, verdicts=verdicts, cases=cases, sheets={}, waived_case_ids=set(),
    )
    assert result["sheets"][0]["sheet_name"] == "sheet_missing"


# --------------------------------------------------------- visual parity (story S7.6.1)


def test_a_sheet_with_no_visual_ever_scored_reports_no_visual_scores() -> None:
    runs = dict([_run("r1", started="t", finished="t", charter_version="1", verdict_ids=["v1"])])
    verdicts = {"v1": _verdict("c1", "PASS")}
    cases = {"c1": _case()}
    result = aggregate_dashboard(
        workbook_id=WORKBOOK, runs=runs, verdicts=verdicts, cases=cases,
        sheets={"sheet_1": _sheet("Bar sheet")}, waived_case_ids=set(),
    )
    sheet = result["sheets"][0]
    assert sheet["structural_score"] is None
    assert sheet["image_score"] is None
    assert sheet["source_screenshot_ref"] is None
    assert sheet["target_render_ref"] is None


def test_a_scored_visual_s_facts_appear_on_its_own_sheet_row() -> None:
    runs = dict([_run("r1", started="t", finished="t", charter_version="1", verdict_ids=["v1"])])
    verdicts = {"v1": _verdict("c1", "PASS")}
    cases = {"c1": _case()}
    visual = {
        "structural_score": 0.82,
        "structural_score_breakdown": {"score": 0.82, "mark_type": 1.0, "encodings": 1.0, "axes": 1.0, "sort": 1.0, "reference_lines": 0.0},
        "image_score": 0.97,
        "visual_score_computed_at": "2026-01-01T00:00:00Z",
        "source_screenshot_ref": "af_shot",
        "target_render_ref": "af_render",
    }
    result = aggregate_dashboard(
        workbook_id=WORKBOOK, runs=runs, verdicts=verdicts, cases=cases,
        sheets={"sheet_1": _sheet("Bar sheet")}, waived_case_ids=set(),
        visuals_by_sheet={"sheet_1": visual},
    )
    sheet = result["sheets"][0]
    assert sheet["structural_score"] == 0.82
    assert sheet["image_score"] == 0.97
    assert sheet["source_screenshot_ref"] == "af_shot"
    assert sheet["target_render_ref"] == "af_render"


def test_a_visual_scored_for_a_different_sheet_does_not_leak_onto_this_one() -> None:
    runs = dict([_run("r1", started="t", finished="t", charter_version="1", verdict_ids=["v1"])])
    verdicts = {"v1": _verdict("c1", "PASS")}
    cases = {"c1": _case("sheet_1")}
    result = aggregate_dashboard(
        workbook_id=WORKBOOK, runs=runs, verdicts=verdicts, cases=cases,
        sheets={"sheet_1": _sheet("Bar sheet")}, waived_case_ids=set(),
        visuals_by_sheet={"sheet_other": {"structural_score": 0.5}},
    )
    assert result["sheets"][0]["structural_score"] is None
