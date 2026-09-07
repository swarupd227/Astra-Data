"""Runs the 200-case fixture corpus (`diff_fixtures.py`) against the real §10.3
algorithm -- story S7.4.1's own AC: "Fixture set of 200 hand-verified pairs covering
each charter rule; CI runs them on every change."

No new CI wiring was needed for "CI runs them on every change": this file is an
ordinary pytest module under `tests/`, parametrized over `diff_fixtures.ALL_CASES`, so
the existing "Unit tests" CI step (`pytest -m "not integration" -q`) already runs every
one of the 200 cases on every change -- the same, idiomatic way `tests/test_rules.py`
already runs every rule's own golden corpus (S5.2.1). A second, standalone CLI+Makefile
target (`tools/rule_regression_check.py`'s own shape) was considered and declined: that
tool's own real job is re-rendering a *tenant's live accumulated graph* against the
*current* rule set (a different concern, S5.2.2's own scope) -- there is no live graph
data for this story's own static fixture corpus to regress against, so pytest's own
existing step is the correct, and sufficient, "runs on every change" mechanism.
"""

from __future__ import annotations

import pytest

from astra_graph.diff import diff_result_sets

from .diff_fixtures import ALL_CASES, DiffFixtureCase

#: §10.3's own AC: "covering each charter rule" -- every one of the charter's nine
#: blocks is accounted for here, with an explicit note for the two this algorithm never
#: reads (see `diff_fixtures.py`'s own module docstring for why).
EXPECTED_CATEGORIES = {
    "numeric", "nulls", "dates", "strings", "ordering", "rows", "sampling",
    "lattice", "column_mapping", "verdict", "totals", "unconsumed",
}


def test_the_corpus_has_at_least_two_hundred_cases() -> None:
    assert len(ALL_CASES) >= 200


def test_every_case_name_is_unique() -> None:
    names = [case.name for case in ALL_CASES]
    assert len(names) == len(set(names))


def test_every_expected_category_has_real_coverage() -> None:
    present = {case.category for case in ALL_CASES}
    assert present == EXPECTED_CATEGORIES
    for category in EXPECTED_CATEGORIES:
        count = sum(1 for case in ALL_CASES if case.category == category)
        assert count >= 5, f"category {category!r} has only {count} cases"


@pytest.mark.parametrize("case", ALL_CASES, ids=[case.name for case in ALL_CASES])
def test_fixture_case(case: DiffFixtureCase) -> None:
    result = diff_result_sets(
        case.expected, case.candidate, case.charter, column_target_map=case.column_target_map,
    )
    assert result.result == case.expected_result, (
        f"{case.name}: expected {case.expected_result}, got {result.result} ({result.reason}); "
        f"failing_cells={[c.as_dict() for c in result.failing_cells]}"
    )
    if case.check is not None:
        case.check(result)
