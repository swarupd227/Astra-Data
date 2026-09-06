"""The Tolerance Charter's own pure logic -- story S7.1.1, spec §4.4.

    "Charter schema per §4.4: numeric (abs and rel epsilon, rounding, currency scale),
    nulls, dates ..., strings ..., ordering, rows ..., sampling, params ..., waiver
    rules."

The comparators (`compare_numeric`/`compare_null`/`compare_string`/`compare_cell`) and the
schema dataclasses are pure and testable without a database -- the versioned store, the G1
gate and `simulate`'s own graph reads are graph-coupled orchestration in the same shape
`conformance_rules.py`/`visual_redesign.py` already established, and are covered by the
integration suite instead.
"""

from __future__ import annotations

from astra_graph.tolerance_charter import (
    CHARTER_FIELD_METADATA,
    DEFAULT_CHARTER,
    DateRule,
    NullRule,
    NumericRule,
    OrderingRule,
    ParamRule,
    RowRule,
    SamplingRule,
    StringRule,
    ToleranceCharter,
    WaiverRule,
    compare_cell,
    compare_null,
    compare_numeric,
    compare_string,
)

# ------------------------------------------------------------------------------- schema


def test_default_charter_matches_the_specs_own_worked_example() -> None:
    assert DEFAULT_CHARTER.numeric == NumericRule(0.005, 1e-6, "HALF_EVEN", 2)
    assert DEFAULT_CHARTER.nulls == NullRule("FAIL", "PASS", True)
    assert DEFAULT_CHARTER.dates == DateRule("TRUNCATE_TO_SOURCE_GRAIN", "UTC", 1)
    assert DEFAULT_CHARTER.strings == StringRule(True, False, "en-US")
    assert DEFAULT_CHARTER.ordering == OrderingRule(False, "SOURCE_ORDER")
    assert DEFAULT_CHARTER.rows == RowRule("FAIL", "FAIL", 0)
    assert DEFAULT_CHARTER.sampling == SamplingRule(200_000, 50_000, "grain")
    assert DEFAULT_CHARTER.params == ParamRule(12, "DEFAULT_PLUS_OBSERVED")
    assert DEFAULT_CHARTER.waiver == WaiverRule(("C4",), ("engineer", "client_owner"), 120)


def test_charter_round_trips_through_as_dict_and_from_dict() -> None:
    restored = ToleranceCharter.from_dict(DEFAULT_CHARTER.as_dict())
    assert restored == DEFAULT_CHARTER


def test_charter_round_trip_preserves_an_edit() -> None:
    edited = ToleranceCharter(numeric=NumericRule(abs_epsilon=0.01, rel_epsilon=1e-4, rounding="HALF_UP", currency_scale=4))
    restored = ToleranceCharter.from_dict(edited.as_dict())
    assert restored.numeric == edited.numeric
    # every other block is still the schema's own default
    assert restored.nulls == DEFAULT_CHARTER.nulls


def test_field_metadata_covers_every_field_of_every_block() -> None:
    for block_name, block in DEFAULT_CHARTER.as_dict().items():
        assert block_name in CHARTER_FIELD_METADATA, f"no inline explanation for block {block_name!r}"
        for field_name in block:
            assert field_name in CHARTER_FIELD_METADATA[block_name], (
                f"no inline explanation for {block_name}.{field_name}"
            )


def test_field_metadata_explanations_are_real_sentences_not_placeholders() -> None:
    for block in CHARTER_FIELD_METADATA.values():
        for explanation in block.values():
            assert len(explanation) >= 20
            assert explanation[0].isupper()


# --------------------------------------------------------------------------- comparators


def test_numeric_comparison_passes_within_absolute_epsilon() -> None:
    result = compare_numeric(100.0, 100.004, NumericRule(abs_epsilon=0.005, rel_epsilon=0.0))
    assert result.result == "PASS"


def test_numeric_comparison_fails_outside_absolute_epsilon() -> None:
    result = compare_numeric(100.0, 100.1, NumericRule(abs_epsilon=0.005, rel_epsilon=0.0))
    assert result.result == "FAIL"


def test_numeric_comparison_passes_within_relative_epsilon_on_large_values() -> None:
    # abs diff of 500 would fail a small abs_epsilon, but is well within a 1e-6 relative one
    result = compare_numeric(1_000_000_000.0, 1_000_000_000.5, NumericRule(abs_epsilon=0.005, rel_epsilon=1e-6))
    assert result.result == "PASS"


def test_numeric_comparison_needs_a_value_on_both_sides() -> None:
    result = compare_numeric(None, 1.0, NumericRule())
    assert result.result == "FAIL"


def test_null_matrix_both_sides_null_passes() -> None:
    result = compare_null(None, None, NullRule())
    assert result is not None and result.result == "PASS"


def test_null_matrix_source_null_target_zero_follows_the_charter() -> None:
    rule = NullRule(source_null_vs_target_zero="FAIL")
    result = compare_null(None, 0, rule)
    assert result is not None and result.result == "FAIL"

    rule_pass = NullRule(source_null_vs_target_zero="PASS")
    result_pass = compare_null(None, 0, rule_pass)
    assert result_pass is not None and result_pass.result == "PASS"


def test_null_matrix_source_null_target_blank_follows_the_charter() -> None:
    # empty_string_is_null=False, or the blank target would be absorbed into "both null"
    # before this rule ever got a chance to apply -- the two are deliberately independent.
    rule = NullRule(source_null_vs_target_blank="PASS", empty_string_is_null=False)
    result = compare_null(None, "", rule)
    assert result is not None and result.result == "PASS"

    rule_fail = NullRule(source_null_vs_target_blank="FAIL", empty_string_is_null=False)
    result_fail = compare_null(None, "", rule_fail)
    assert result_fail is not None and result_fail.result == "FAIL"


def test_null_matrix_empty_string_is_null_toggle() -> None:
    # with the toggle on, an empty string source and empty string target are "both null"
    result = compare_null("", "", NullRule(empty_string_is_null=True))
    assert result is not None and result.result == "PASS"


def test_null_matrix_returns_none_when_neither_side_is_null() -> None:
    assert compare_null(1, 2, NullRule()) is None
    assert compare_null("a", "b", NullRule()) is None


def test_null_matrix_fails_when_only_the_target_is_null() -> None:
    result = compare_null(5, None, NullRule())
    assert result is not None and result.result == "FAIL"


def test_null_matrix_fails_an_unmatched_non_null_value_on_the_target() -> None:
    result = compare_null(None, "unexpected", NullRule())
    assert result is not None and result.result == "FAIL"


def test_string_comparison_trims_and_folds_case_by_default() -> None:
    result = compare_string("  Desk  ", "desk", StringRule(trim=True, case_sensitive=False))
    assert result.result == "PASS"


def test_string_comparison_respects_case_sensitivity() -> None:
    result = compare_string("Desk", "desk", StringRule(trim=True, case_sensitive=True))
    assert result.result == "FAIL"


def test_string_comparison_respects_trim_disabled() -> None:
    result = compare_string("Desk", "Desk ", StringRule(trim=False, case_sensitive=True))
    assert result.result == "FAIL"


def test_compare_cell_checks_the_null_matrix_before_the_kind_specific_rule() -> None:
    charter = ToleranceCharter(nulls=NullRule(source_null_vs_target_zero="PASS"))
    result = compare_cell("numeric", None, 0, charter)
    assert result.result == "PASS"


def test_compare_cell_dispatches_numeric() -> None:
    charter = ToleranceCharter(numeric=NumericRule(abs_epsilon=0.01, rel_epsilon=0.0))
    assert compare_cell("numeric", 1.0, 1.005, charter).result == "PASS"
    assert compare_cell("numeric", 1.0, 1.5, charter).result == "FAIL"


def test_compare_cell_dispatches_string() -> None:
    charter = ToleranceCharter(strings=StringRule(trim=True, case_sensitive=False))
    assert compare_cell("string", "EMEA", "emea", charter).result == "PASS"


def test_compare_cell_rejects_an_unrecognised_kind() -> None:
    result = compare_cell("shape", 1, 2, ToleranceCharter())
    assert result.result == "FAIL"
