"""The Modeller's pure design heuristics — story S4.1.1.

    "A model design proposal ... tables with source mapping and storage mode, relationships
    with cardinality, ... candidate measures with source calc refs and dedup decisions, RLS
    roles derived from Tableau user filters, refresh policy ... a list of open questions."

The graph read (a family's full reach — datasources, connections, tables, calculated
fields) is exercised against real PostgreSQL in the integration suite. What is tested here
is the judgement: which storage mode a table gets, which side of a join is the many side,
which calculations collapse into one measure, which RLS expressions become one role, and —
just as importantly — when each of those honestly says "I cannot tell" rather than guessing.
"""

from __future__ import annotations

from astra_graph.modeller import (
    FamilyEvidence,
    conformed_dimensions_shared_with,
    dedupe_measures,
    derive_refresh_policy,
    derive_rls_roles,
    draft_grain_statement,
    find_structural_open_questions,
    infer_cardinality,
    recommend_storage_mode,
)

# ------------------------------------------------------------------ recommend_storage_mode


def test_an_extracted_human_scale_table_recommends_import() -> None:
    mode, reason = recommend_storage_mode(extract_flag=True, connection_class="snowflake", row_estimate=10_000)
    assert mode == "import"
    assert "extract already exists" in reason


def test_a_live_connection_with_no_extract_recommends_directquery() -> None:
    mode, reason = recommend_storage_mode(extract_flag=False, connection_class="snowflake", row_estimate=None)
    assert mode == "directquery"
    assert "no extract" in reason


def test_an_absent_extract_flag_is_treated_as_no_extract() -> None:
    mode, _reason = recommend_storage_mode(extract_flag=None, connection_class="postgres", row_estimate=5)
    assert mode == "directquery"


def test_a_very_large_extracted_table_recommends_direct_lake() -> None:
    mode, reason = recommend_storage_mode(extract_flag=True, connection_class="hive", row_estimate=200_000_000)
    assert mode == "directlake"
    assert "Direct Lake" in reason


def test_the_direct_lake_floor_is_the_import_ceiling_exactly() -> None:
    just_under, _ = recommend_storage_mode(extract_flag=True, connection_class=None, row_estimate=49_999_999)
    at_floor, _ = recommend_storage_mode(extract_flag=True, connection_class=None, row_estimate=50_000_000)
    assert just_under == "import"
    assert at_floor == "directlake"


# ---------------------------------------------------------------------- infer_cardinality


def test_a_much_larger_from_table_is_the_many_side() -> None:
    cardinality, confidence, reason = infer_cardinality(from_row_estimate=1_000_000, to_row_estimate=10)
    assert cardinality == "many_to_one"
    assert confidence == "row_estimate"
    assert "1,000,000" in reason


def test_a_much_smaller_from_table_is_the_one_side() -> None:
    cardinality, confidence, _reason = infer_cardinality(from_row_estimate=10, to_row_estimate=1_000_000)
    assert cardinality == "one_to_many"
    assert confidence == "row_estimate"


def test_comparable_row_estimates_are_ambiguous_not_guessed() -> None:
    cardinality, confidence, reason = infer_cardinality(from_row_estimate=100, to_row_estimate=150)
    assert cardinality is None
    assert confidence == "ambiguous"
    assert "too close" in reason


def test_a_missing_row_estimate_on_either_side_is_unknown() -> None:
    cardinality, confidence, reason = infer_cardinality(from_row_estimate=None, to_row_estimate=10)
    assert cardinality is None
    assert confidence == "unknown"
    assert "missing" in reason


def test_a_zero_row_estimate_cannot_settle_a_ratio() -> None:
    cardinality, confidence, reason = infer_cardinality(from_row_estimate=0, to_row_estimate=10)
    assert cardinality is None
    assert confidence == "unknown"
    assert "zero" in reason


# ------------------------------------------------------------------- draft_grain_statement


def test_a_single_dimension_grain_statement() -> None:
    assert draft_grain_statement(["Region"]) == "One row per Region."


def test_a_multi_dimension_grain_statement_reads_as_a_sentence() -> None:
    assert (
        draft_grain_statement(["Region", "Product", "Month"])
        == "One row per Region, Product and Month."
    )


def test_a_two_dimension_grain_statement_has_no_stray_comma() -> None:
    assert draft_grain_statement(["Region", "Product"]) == "One row per Region and Product."


def test_no_grain_dimensions_asks_for_confirmation_rather_than_a_blank_sentence() -> None:
    statement = draft_grain_statement([])
    assert "could not be determined" in statement


# ------------------------------------------------------------------------ dedupe_measures


def _calc(name: str, ast: object) -> dict[str, object]:
    return {"name": name, "formula_ast": ast}


def test_two_calculations_with_the_same_shape_merge_into_one_measure() -> None:
    calcs = {
        "c1": _calc("Net Revenue", {"op": "SUM", "args": [{"field": "revenue"}]}),
        "c2": _calc("Net Revenue", {"op": "SUM", "args": [{"field": "revenue"}]}),
    }
    measures, questions = dedupe_measures(calcs)
    assert len(measures) == 1
    assert set(measures[0].source_calc_refs) == {"c1", "c2"}
    assert "merged 2" in measures[0].dedup_decision
    assert questions == ()


def test_a_single_calculation_is_not_reported_as_deduplicated() -> None:
    calcs = {"c1": _calc("Margin", {"op": "SUM", "args": [{"field": "margin"}]})}
    measures, _questions = dedupe_measures(calcs)
    assert len(measures) == 1
    assert "no deduplication needed" in measures[0].dedup_decision


def test_the_same_name_with_different_shapes_is_an_open_question_not_a_merge() -> None:
    calcs = {
        "c1": _calc("Net Revenue", {"op": "SUM", "args": [{"field": "revenue"}]}),
        "c2": _calc("Net Revenue", {"op": "AVG", "args": [{"field": "revenue"}]}),
    }
    measures, questions = dedupe_measures(calcs)
    # Two distinct shapes, so two distinct candidate measures, not one.
    assert len(measures) == 2
    assert len(questions) == 1
    assert questions[0].category == "duplicate_measure"
    assert "net revenue" in questions[0].question.lower()


def _too_deep_ast() -> dict[str, object]:
    # ast_shape (context.signature) raises SignatureError past MAX_DEPTH = 64 levels.
    node: dict[str, object] = {"field": "leaf"}
    for _ in range(70):
        node = {"op": "SUM", "args": [node]}
    return node


def test_a_calculation_whose_shape_cannot_be_computed_is_carried_through_unmerged() -> None:
    calcs = {"c1": _calc("Odd One", _too_deep_ast())}
    measures, _questions = dedupe_measures(calcs)
    assert len(measures) == 1
    assert measures[0].source_calc_refs == ("c1",)
    assert "could not be computed" in measures[0].dedup_decision


# -------------------------------------------------------------------------- derive_rls_roles


def test_one_role_per_workbook_when_expressions_differ() -> None:
    workbooks = {
        "wb1": {"name": "Daily VaR", "rls": True, "rls_expression": "[Site] = USERNAME()"},
        "wb2": {"name": "Weekly VaR", "rls": True, "rls_expression": "ISMEMBEROF([Group])"},
    }
    roles, questions = derive_rls_roles(workbooks)
    assert len(roles) == 2
    assert questions == ()


def test_workbooks_sharing_one_expression_become_one_role() -> None:
    workbooks = {
        "wb1": {"name": "Daily VaR", "rls": True, "rls_expression": "[Site] = USERNAME()"},
        "wb2": {"name": "Weekly VaR", "rls": True, "rls_expression": "[Site] = USERNAME()"},
    }
    roles, _questions = derive_rls_roles(workbooks)
    assert len(roles) == 1
    assert set(roles[0].source_workbook_ids) == {"wb1", "wb2"}
    assert "shared across 2" in roles[0].name


def test_a_workbook_without_rls_contributes_no_role() -> None:
    workbooks = {"wb1": {"name": "Public Dashboard", "rls": False}}
    roles, questions = derive_rls_roles(workbooks)
    assert roles == ()
    assert questions == ()


def test_rls_flagged_but_no_expression_is_an_open_question() -> None:
    workbooks = {"wb1": {"name": "Legacy Report", "rls": True}}
    roles, questions = derive_rls_roles(workbooks)
    assert roles == ()
    assert len(questions) == 1
    assert questions[0].category == "rls_conflict"


# ----------------------------------------------------------------------- derive_refresh_policy


def test_all_extracted_sources_recommend_the_most_frequent_schedule() -> None:
    datasources = {
        "d1": {"extract_flag": True, "refresh_schedule": "daily"},
        "d2": {"extract_flag": True, "refresh_schedule": "daily"},
        "d3": {"extract_flag": True, "refresh_schedule": "weekly"},
    }
    policy = derive_refresh_policy(datasources)
    assert policy["mode"] == "scheduled"
    assert policy["schedule"] == "daily"


def test_only_live_sources_need_no_schedule() -> None:
    datasources = {"d1": {"extract_flag": False}}
    policy = derive_refresh_policy(datasources)
    assert policy["mode"] == "directquery"
    assert policy["schedule"] is None


def test_mixed_extracted_and_live_sources_are_reported_as_mixed() -> None:
    datasources = {
        "d1": {"extract_flag": True, "refresh_schedule": "daily"},
        "d2": {"extract_flag": False},
    }
    policy = derive_refresh_policy(datasources)
    assert policy["mode"] == "mixed"
    assert policy["extracted_source_count"] == 1
    assert policy["live_source_count"] == 1


def test_no_datasources_at_all_is_unknown_not_a_guess() -> None:
    assert derive_refresh_policy({})["mode"] == "unknown"


# ---------------------------------------------------------- conformed_dimensions_shared_with


def test_a_dimension_shared_with_another_family_is_reported() -> None:
    others = [
        {"id": "fam_2", "grain": ["Region", "Product"]},
        {"id": "fam_3", "grain": ["Currency"]},
    ]
    result = conformed_dimensions_shared_with(["Region"], others)
    assert len(result) == 1
    assert result[0].dimension == "Region"
    assert result[0].shared_with_family_ids == ("fam_2",)


def test_dimension_matching_is_case_insensitive() -> None:
    others = [{"id": "fam_2", "grain": ["region"]}]
    result = conformed_dimensions_shared_with(["Region"], others)
    assert result[0].shared_with_family_ids == ("fam_2",)


def test_a_dimension_nobody_else_shares_reports_no_families() -> None:
    others = [{"id": "fam_2", "grain": ["Currency"]}]
    result = conformed_dimensions_shared_with(["Region"], others)
    assert result[0].shared_with_family_ids == ()


# ------------------------------------------------------------- find_structural_open_questions


def _evidence(tables: dict[str, dict[str, object]]) -> FamilyEvidence:
    return FamilyEvidence(
        member_ids=(),
        tables=tables,
        connections={},
        datasources={},
        connection_tables={},
        datasource_connections={},
        join_edges=(),
        calculations={},
        workbooks={},
    )


def test_a_custom_sql_table_raises_an_ambiguous_key_question() -> None:
    evidence = _evidence({"t1": {"name": "risk_positions", "custom_sql": "SELECT * FROM x"}})
    questions = find_structural_open_questions(evidence)
    assert len(questions) == 1
    assert questions[0].category == "ambiguous_key"
    assert "custom SQL" in questions[0].question


def test_an_ordinary_table_raises_no_question() -> None:
    evidence = _evidence({"t1": {"name": "dim_region", "custom_sql": None}})
    assert find_structural_open_questions(evidence) == ()
