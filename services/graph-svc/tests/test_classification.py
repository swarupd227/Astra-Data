"""§11.1 failure classification -- story S8.1.1, opening F8.1/E8. Pure `classify_failure`
only; the graph-coupled grouping/exception-opening half (`classify_run`) is covered in
`test_integration_classification.py`, the identical split this epic's own prior stories
(`diff.py`/`verdicts.py`, `visual_parity.py`) already established.
"""

from __future__ import annotations

import pytest

from astra_graph.classification import (
    FAILURE_CLASSES,
    Classification,
    ClassificationError,
    classify_failure,
)
from tests.classification_fixtures import ALL_CATEGORIES, all_cases

#: §11.1's own AC: "Classification precision on the labelled fixture set >= 0.90" --
#: read as plain accuracy (correct / total); see classification.py's own docstring.
PRECISION_BOUND = 0.90


def test_only_a_fail_verdict_is_classified() -> None:
    with pytest.raises(ClassificationError, match="only a FAIL verdict"):
        classify_failure({"result": "PASS"})


def test_source_drift_overrides_every_other_signal() -> None:
    """§11.1's own disclosed priority: if the source changed, nothing else the diff
    shows is trustworthy -- proven by feeding it evidence that would otherwise classify
    as KEY_MISSING."""
    result = classify_failure(
        {"result": "FAIL", "missing_keys": [["x"]], "extra_keys": [], "failing_cells": [], "totals": []},
        recent_source_drift=True,
    )
    assert result.failure_class == "SOURCE_DRIFT"


def test_every_fixture_case_reports_a_class_in_the_taxonomy() -> None:
    for case in all_cases():
        result = classify_failure(case.diff, formula_ast=case.formula_ast, recent_source_drift=case.recent_source_drift)
        assert result.failure_class in FAILURE_CLASSES, case.name


def test_classification_result_carries_signals_alongside_the_class() -> None:
    """The AC's own 'the class and the signals that produced it' -- `Classification`
    is never just a bare string."""
    result = classify_failure(
        {"result": "FAIL", "missing_keys": [["x"]], "extra_keys": [], "failing_cells": [], "totals": []},
    )
    assert isinstance(result, Classification)
    assert result.failure_class == "KEY_MISSING"
    assert result.signals  # non-empty: the measured facts behind the class
    assert result.reason


@pytest.mark.parametrize("failure_class", sorted(ALL_CATEGORIES))
def test_every_declared_class_has_real_fixture_coverage(failure_class: str) -> None:
    cases = ALL_CATEGORIES[failure_class]()
    assert len(cases) >= 5, f"{failure_class} has only {len(cases)} fixture case(s)"


def test_fixture_set_has_real_coverage() -> None:
    """At least five cases per declared class (eleven classes) plus the deliberately
    ambiguous cases -- see `classification_fixtures.py`'s own docstring."""
    assert len(all_cases()) >= 55 + 5


def test_fixture_names_are_unique() -> None:
    names = [case.name for case in all_cases()]
    assert len(names) == len(set(names))


def test_classification_precision_meets_the_ac_bound() -> None:
    cases = all_cases()
    correct = 0
    misses: list[str] = []
    for case in cases:
        result = classify_failure(
            case.diff, formula_ast=case.formula_ast, recent_source_drift=case.recent_source_drift,
        )
        if result.failure_class == case.expected_class:
            correct += 1
        else:
            misses.append(f"{case.name}: expected {case.expected_class}, got {result.failure_class}")

    precision = correct / len(cases)
    assert precision >= PRECISION_BOUND, (
        f"precision {precision:.3f} over {len(cases)} cases is below the AC's own {PRECISION_BOUND} "
        f"bound -- misclassified: {misses}"
    )
