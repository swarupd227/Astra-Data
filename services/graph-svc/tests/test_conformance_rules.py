"""Conformance rules enforced at emission — story S4.3.2.

    "Rules from §12.3: star schema only (no many-to-many without a bridge), single active
    relationship path, conformed dimensions shared by reference, measures in display
    folders by source family, naming convention, RLS roles tested with a fixture user."

Every rule is a pure function of the frozen design document, so every test here is a plain
function of a document fixture — no database, matching `test_tmdl.py`'s own footing (the
document shapes these two modules share are deliberately kept in sync).
"""

from __future__ import annotations

from astra_graph.conformance_rules import (
    ConformanceRuleset,
    RuleConfig,
    check_conformance,
    check_conformed_dimensions_by_reference,
    check_measures_display_folder,
    check_naming_convention,
    check_rls_fixture_user,
    check_single_active_path,
    check_star_schema,
)


def _document(**overrides: object) -> dict[str, object]:
    base: dict[str, object] = {
        "tables": [
            {"id": "mt_positions", "name": "positions", "mode": "directquery"},
            {"id": "mt_desk", "name": "desk", "mode": "directquery"},
        ],
        "relationships": [
            {"from_table": "mt_desk", "to_table": "mt_positions", "cardinality": "one_to_many", "confidence": "row_estimate"},
        ],
        "candidate_measures": [{"name": "Margin %", "source_calc_refs": ["c1"], "dedup_decision": "n/a"}],
        "conformed_dimensions": [],
        "rls_role_detail": [],
    }
    base.update(overrides)
    return base


# ------------------------------------------------------------------------------ star_schema


def test_a_resolved_cardinality_is_not_a_violation() -> None:
    assert check_star_schema(_document(), {}) == []


def test_an_ambiguous_cardinality_is_an_unconfirmed_many_to_many() -> None:
    document = _document(
        relationships=[{"from_table": "mt_desk", "to_table": "mt_positions", "cardinality": None}]
    )
    violations = check_star_schema(document, {})
    assert len(violations) == 1
    assert violations[0].rule_id == "star_schema"
    assert "desk" in violations[0].object_ref and "positions" in violations[0].object_ref


# ------------------------------------------------------------------------ single_active_path


def test_a_tree_of_relationships_has_a_single_path() -> None:
    document = _document(
        tables=[
            {"id": "a", "name": "a"}, {"id": "b", "name": "b"}, {"id": "c", "name": "c"},
        ],
        relationships=[
            {"from_table": "a", "to_table": "b", "cardinality": "many_to_one"},
            {"from_table": "b", "to_table": "c", "cardinality": "many_to_one"},
        ],
    )
    assert check_single_active_path(document, {}) == []


def test_a_cycle_creates_a_second_path() -> None:
    document = _document(
        tables=[
            {"id": "a", "name": "a"}, {"id": "b", "name": "b"}, {"id": "c", "name": "c"},
        ],
        relationships=[
            {"from_table": "a", "to_table": "b", "cardinality": "many_to_one"},
            {"from_table": "b", "to_table": "c", "cardinality": "many_to_one"},
            {"from_table": "c", "to_table": "a", "cardinality": "many_to_one"},
        ],
    )
    violations = check_single_active_path(document, {})
    assert len(violations) == 1
    assert violations[0].rule_id == "single_active_path"


# ------------------------------------------------------------ conformed_dimensions_by_reference


def test_a_shared_dimension_imported_here_is_a_violation() -> None:
    document = _document(
        tables=[{"id": "mt_desk", "name": "desk", "mode": "import"}],
        conformed_dimensions=[{"dimension": "desk", "shared_with_family_ids": ["fam_two"]}],
    )
    violations = check_conformed_dimensions_by_reference(document, {})
    assert len(violations) == 1
    assert violations[0].object_ref == "desk"


def test_a_shared_dimension_read_live_is_not_a_violation() -> None:
    document = _document(
        tables=[{"id": "mt_desk", "name": "desk", "mode": "directquery"}],
        conformed_dimensions=[{"dimension": "desk", "shared_with_family_ids": ["fam_two"]}],
    )
    assert check_conformed_dimensions_by_reference(document, {}) == []


def test_an_unshared_dimension_may_be_imported() -> None:
    document = _document(
        tables=[{"id": "mt_desk", "name": "desk", "mode": "import"}],
        conformed_dimensions=[{"dimension": "desk", "shared_with_family_ids": []}],
    )
    assert check_conformed_dimensions_by_reference(document, {}) == []


# ----------------------------------------------------------------------- measures_display_folder


def test_unique_measure_names_are_not_a_violation() -> None:
    document = _document(
        candidate_measures=[{"name": "Margin %"}, {"name": "Notional"}],
    )
    assert check_measures_display_folder(document, {}) == []


def test_a_duplicate_measure_name_is_a_violation() -> None:
    document = _document(
        candidate_measures=[{"name": "Margin %"}, {"name": "margin %"}],
    )
    violations = check_measures_display_folder(document, {})
    assert len(violations) == 1
    assert violations[0].object_ref == "margin %"


# ------------------------------------------------------------------------- naming_convention


def test_a_clean_name_is_not_a_violation() -> None:
    document = _document()
    assert check_naming_convention(document, {}) == []


def test_a_blank_table_name_is_a_violation() -> None:
    document = _document(tables=[{"id": "t1", "name": "  "}])
    violations = check_naming_convention(document, {})
    assert any("blank" in v.message for v in violations)


def test_leading_or_trailing_whitespace_is_a_violation() -> None:
    document = _document(tables=[{"id": "t1", "name": " positions"}])
    violations = check_naming_convention(document, {})
    assert any("whitespace" in v.message for v in violations)


def test_a_name_starting_with_a_digit_is_a_violation() -> None:
    document = _document(tables=[{"id": "t1", "name": "1positions"}])
    violations = check_naming_convention(document, {})
    assert any("digit" in v.message for v in violations)


def test_an_embedded_double_quote_is_a_violation() -> None:
    document = _document(tables=[{"id": "t1", "name": 'posi"tions'}])
    violations = check_naming_convention(document, {})
    assert any("quote" in v.message for v in violations)


def test_max_length_is_configurable() -> None:
    document = _document(tables=[{"id": "t1", "name": "positions"}])
    violations = check_naming_convention(document, {"max_length": 5})
    assert any("exceeds 5 characters" in v.message for v in violations)


def test_a_max_length_of_zero_is_honoured_not_treated_as_unset() -> None:
    """`params.get("max_length") or 100` would treat an explicit 0 as absent — Python
    treats 0 as falsy — silently reverting to the default instead of enforcing the
    architect's own (admittedly extreme) configured value."""
    document = _document(tables=[{"id": "t1", "name": "positions"}])
    violations = check_naming_convention(document, {"max_length": 0})
    assert any("exceeds 0 characters" in v.message for v in violations)


def test_measures_and_roles_are_checked_too() -> None:
    document = _document(
        tables=[],
        candidate_measures=[{"name": "1Margin"}],
        rls_role_detail=[{"name": " Analyst"}],
    )
    violations = check_naming_convention(document, {})
    kinds = {v.object_ref for v in violations}
    assert "1Margin" in kinds
    assert " Analyst" in kinds


# --------------------------------------------------------------------------- rls_fixture_user


def test_a_well_formed_rls_expression_is_not_a_violation() -> None:
    document = _document(rls_role_detail=[{"name": "Analyst", "expression": "[Desk] = USERNAME()"}])
    assert check_rls_fixture_user(document, {}) == []


def test_a_blank_expression_is_a_violation() -> None:
    document = _document(rls_role_detail=[{"name": "Analyst", "expression": ""}])
    violations = check_rls_fixture_user(document, {})
    assert any("no filter expression" in v.message for v in violations)


def test_an_expression_with_no_field_is_a_violation() -> None:
    document = _document(rls_role_detail=[{"name": "Analyst", "expression": "TRUE()"}])
    violations = check_rls_fixture_user(document, {})
    assert any("names no field" in v.message for v in violations)


def test_an_expression_with_no_recognised_function_is_a_violation() -> None:
    document = _document(rls_role_detail=[{"name": "Analyst", "expression": "[Desk] = \"EMEA\""}])
    violations = check_rls_fixture_user(document, {})
    assert any("recognised user-context function" in v.message for v in violations)


def test_the_fixture_username_param_is_used_in_the_message() -> None:
    document = _document(rls_role_detail=[{"name": "Analyst", "expression": "TRUE()"}])
    violations = check_rls_fixture_user(document, {"fixture_username": "someone@astra.local"})
    assert any("someone@astra.local" in v.message for v in violations)


# ------------------------------------------------------------------------------ check_conformance


def test_a_disabled_rule_is_never_run() -> None:
    document = _document(
        relationships=[{"from_table": "mt_desk", "to_table": "mt_positions", "cardinality": None}]
    )
    ruleset = ConformanceRuleset(
        version=1, rules=(RuleConfig("star_schema", enabled=False),), updated_by="x", updated_at=None,
    )
    assert check_conformance(document, ruleset) == []


def test_an_enabled_rule_runs_and_reports_violations() -> None:
    document = _document(
        relationships=[{"from_table": "mt_desk", "to_table": "mt_positions", "cardinality": None}]
    )
    ruleset = ConformanceRuleset(
        version=1, rules=(RuleConfig("star_schema", enabled=True),), updated_by="x", updated_at=None,
    )
    violations = check_conformance(document, ruleset)
    assert len(violations) == 1
    assert violations[0].rule_id == "star_schema"


def test_an_unknown_rule_id_is_skipped_rather_than_raising() -> None:
    ruleset = ConformanceRuleset(
        version=1, rules=(RuleConfig("not_a_real_rule", enabled=True),), updated_by="x", updated_at=None,
    )
    assert check_conformance(_document(), ruleset) == []


def test_violation_str_names_the_object_and_the_message() -> None:
    document = _document(
        relationships=[{"from_table": "mt_desk", "to_table": "mt_positions", "cardinality": None}]
    )
    violation = check_star_schema(document, {})[0]
    assert str(violation) == f"{violation.object_ref}: {violation.message}"


# ------------------------------------------------------------------------------------ as_dict


def test_rule_config_as_dict() -> None:
    rule = RuleConfig("naming_convention", enabled=True, params={"max_length": 100})
    assert rule.as_dict() == {"rule_id": "naming_convention", "enabled": True, "params": {"max_length": 100}}


def test_ruleset_as_dict() -> None:
    ruleset = ConformanceRuleset(
        version=2,
        rules=(RuleConfig("star_schema"),),
        updated_by="user:architect@artizent.example",
        updated_at="2027-05-01T09:00:00.000Z",
    )
    body = ruleset.as_dict()
    assert body["version"] == 2
    assert body["rules"] == [{"rule_id": "star_schema", "enabled": True, "params": {}}]
