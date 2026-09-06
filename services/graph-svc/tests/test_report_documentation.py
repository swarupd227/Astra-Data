"""Report documentation rendering -- story S6.2.2, spec §8.8/§8.11.

    "One markdown page per report: purpose (from workbook description), pages and
    visuals with their Tableau sheet of origin, measures with source calc names,
    parameters, known differences (C4 decisions, redesigns), model and refresh."

`render_markdown` is pure and testable without a database -- everything else in
`report_documentation.py` is graph-coupled orchestration in the same shape
`visual_redesign.py`/`build.py` already established, and is covered by the integration
suite instead.
"""

from __future__ import annotations

from astra_graph.report_documentation import render_markdown

_GENERATED_AT = "2026-09-06T12:00:00.000Z"


def _empty_facts(**overrides: object) -> dict[str, object]:
    base: dict[str, object] = {
        "workbook_name": "Daily VaR",
        "graph_version": 42,
        "pages": [],
        "measures": [],
        "parameters": [],
        "c4_decisions": [],
        "redesigns": [],
        "model": {
            "semantic_model_id": "sm1",
            "family_name": "Risk Positions",
            "grain_statement": "One row per Desk, Date",
            "version_number": 2,
            "state": "PUBLISHED",
        },
        "refresh_schedules": [],
    }
    base.update(overrides)
    return base


def test_title_and_purpose_come_from_the_workbook_name() -> None:
    markdown = render_markdown(_empty_facts(), generated_at=_GENERATED_AT)
    assert markdown.startswith("# Daily VaR — Report Documentation\n")
    assert "## Purpose" in markdown
    assert "Daily VaR (from the source workbook's own name)." in markdown


def test_generated_line_names_the_agent_mode_and_graph_version() -> None:
    markdown = render_markdown(_empty_facts(), generated_at=_GENERATED_AT)
    assert f"_Generated {_GENERATED_AT} in ASSISTED mode by compositor" in markdown
    assert "from graph version 42." in markdown


def test_empty_sections_say_so_rather_than_rendering_nothing() -> None:
    markdown = render_markdown(_empty_facts(), generated_at=_GENERATED_AT)
    assert "_No calculated-field measures in this report._" in markdown
    assert "_No parameters in this report._" in markdown
    assert "_No C4 calculations in this report._" in markdown
    assert "_No visuals were flagged for redesign._" in markdown
    assert "_No refresh schedule recorded on this report's datasources._" in markdown


def test_pages_and_visuals_name_the_source_sheet_and_flag_a_redesign() -> None:
    facts = _empty_facts(
        pages=[
            {
                "page": "Overview",
                "visuals": [
                    {
                        "type": "clusteredColumnChart",
                        "source_sheet_name": "Bar sheet",
                        "redesign_flag": False,
                        "redesign_reason": None,
                    },
                    {
                        "type": "placeholder",
                        "source_sheet_name": "Weird sheet",
                        "redesign_flag": True,
                        "redesign_reason": "no mapping rule for Tableau mark type 'hexbin'",
                    },
                ],
            }
        ]
    )
    markdown = render_markdown(facts, generated_at=_GENERATED_AT)
    assert "### Overview" in markdown
    assert '- **clusteredColumnChart** — from Tableau sheet "Bar sheet"' in markdown
    line = next(line for line in markdown.splitlines() if "Weird sheet" in line)
    assert "**flagged for redesign**" in line


def test_a_visual_with_no_source_sheet_says_so() -> None:
    facts = _empty_facts(
        pages=[
            {
                "page": "Overview",
                "visuals": [
                    {
                        "type": "placeholder",
                        "source_sheet_name": None,
                        "redesign_flag": True,
                        "redesign_reason": "no mapping rule",
                    }
                ],
            }
        ]
    )
    markdown = render_markdown(facts, generated_at=_GENERATED_AT)
    assert "(no source sheet)" in markdown


def test_measures_table_shows_the_source_calc_name_and_falls_back_when_unbound() -> None:
    facts = _empty_facts(
        measures=[
            {"calc_id": "c1", "calc_name": "Net Exposure", "measure_id": "m1", "measure_name": "Net Exposure", "bound": True},
            {"calc_id": "c2", "calc_name": "Draft Calc", "measure_id": None, "measure_name": None, "bound": False},
        ]
    )
    markdown = render_markdown(facts, generated_at=_GENERATED_AT)
    assert "| Net Exposure | Net Exposure |" in markdown
    assert "| _not yet generated_ | Draft Calc |" in markdown


def test_parameters_table_shows_construct_and_reason() -> None:
    facts = _empty_facts(
        parameters=[
            {"name": "Top N", "kind": "slicer", "domain": "list", "supported": True, "reason": None},
            {"name": "Threshold", "kind": None, "domain": "any", "supported": False, "reason": "unbounded domain"},
        ]
    )
    markdown = render_markdown(facts, generated_at=_GENERATED_AT)
    assert "| Top N | slicer |  |" in markdown
    assert "| Threshold | _not supported_ | unbounded domain |" in markdown


def test_c4_decisions_show_guidance_suggestion_and_a_pending_decision() -> None:
    facts = _empty_facts(
        c4_decisions=[
            {
                "calc_id": "c3",
                "calc_name": "Weird Calc",
                "reason": "RAWSQL construct",
                "appendix_b_guidance": "M pass-through where supported, otherwise C4.",
                "redesign_suggestion": "Replace with a native model relationship.",
                "decision": None,
                "decision_reason": None,
                "decision_by": None,
                "decision_at": None,
            }
        ]
    )
    markdown = render_markdown(facts, generated_at=_GENERATED_AT)
    assert "**Weird Calc** — RAWSQL construct" in markdown
    assert "Appendix B guidance: M pass-through where supported, otherwise C4." in markdown
    assert "Suggestion: Replace with a native model relationship." in markdown
    assert "Decision: _not yet recorded (blocked)_" in markdown


def test_c4_decisions_show_a_recorded_decision_and_its_rationale() -> None:
    facts = _empty_facts(
        c4_decisions=[
            {
                "calc_id": "c3",
                "calc_name": "Weird Calc",
                "reason": "RAWSQL construct",
                "appendix_b_guidance": "guidance text",
                "redesign_suggestion": "suggestion text",
                "decision": "ALTERNATIVE",
                "decision_reason": "Report owner agreed to a simpler measure.",
                "decision_by": "user:engineer@artizent.example",
                "decision_at": "2026-09-01T00:00:00.000Z",
            }
        ]
    )
    markdown = render_markdown(facts, generated_at=_GENERATED_AT)
    assert "Decision: ALTERNATIVE" in markdown
    assert "Rationale: Report owner agreed to a simpler measure." in markdown


def test_redesigned_visuals_section_names_page_type_sheet_and_reason() -> None:
    facts = _empty_facts(
        redesigns=[
            {"page": "Overview", "type": "placeholder", "source_sheet_name": "Weird sheet", "reason": "no mapping rule for 'hexbin'"}
        ]
    )
    markdown = render_markdown(facts, generated_at=_GENERATED_AT)
    assert '**Overview / placeholder** (from "Weird sheet") — no mapping rule for \'hexbin\'' in markdown


def test_model_section_names_family_grain_version_and_state() -> None:
    markdown = render_markdown(_empty_facts(), generated_at=_GENERATED_AT)
    assert "- Family: Risk Positions" in markdown
    assert "- Grain: One row per Desk, Date" in markdown
    assert "- Version: 2 (PUBLISHED)" in markdown


def test_model_section_discloses_missing_facts_rather_than_guessing() -> None:
    facts = _empty_facts(
        model={"semantic_model_id": "sm1", "family_name": None, "grain_statement": None, "version_number": 1, "state": None}
    )
    markdown = render_markdown(facts, generated_at=_GENERATED_AT)
    assert "- Family: (unknown)" in markdown
    assert "- Grain: (not yet drafted)" in markdown
    assert "- Version: 1 (unknown state)" in markdown


def test_a_single_refresh_schedule_is_listed_without_the_multiple_schedules_note() -> None:
    markdown = render_markdown(_empty_facts(refresh_schedules=["daily"]), generated_at=_GENERATED_AT)
    assert "- daily" in markdown
    assert "more than one datasource" not in markdown


def test_multiple_refresh_schedules_are_all_listed_and_disclosed() -> None:
    markdown = render_markdown(_empty_facts(refresh_schedules=["daily", "hourly"]), generated_at=_GENERATED_AT)
    assert "- daily" in markdown
    assert "- hourly" in markdown
    assert "more than one datasource" in markdown
