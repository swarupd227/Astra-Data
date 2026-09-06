"""The Pattern Library — specification §4.3/§9.3, stories S5.5.1 and S5.5.2.

Pure logic only: template substitution in both directions, the promotion arithmetic, and
(S5.5.2) the retirement arithmetic. Everything that touches the graph or
`pattern_observation` (generalising a real proof, promotion/retirement against real
history, deterministic application, the re-queue, the event) is
`test_integration_patterns.py`'s own -- the same split `test_classify.py`/
`test_integration_classify.py` already established.
"""

from __future__ import annotations

from astra_graph.patterns import (
    DEFAULT_FAILURE_COUNT_THRESHOLD,
    DEFAULT_PASS_RATE_THRESHOLD,
    MIN_APPLICATIONS_FOR_RATE_CHECK,
    PromotionStatus,
    RetirementCheck,
    _abstract_template,
    evaluate_retirement,
    render_target,
)


def _op(name: str, *children: dict[str, object]) -> dict[str, object]:
    return {"kind": "OPERATOR", "name": name, "value": None, "children": list(children), "detail": []}


def _ref(name: str) -> dict[str, object]:
    return {"kind": "REFERENCE", "name": name, "value": None, "children": [], "detail": []}


# ------------------------------------------------------------------------------ render_target


def test_render_target_substitutes_every_capture() -> None:
    dax = render_target("DIVIDE(SUM({a}), SUM({b}))", {"a": "Notional", "b": "Margin"})
    assert dax == "DIVIDE(SUM([Notional]), SUM([Margin]))"


def test_render_target_leaves_the_table_placeholder_untouched() -> None:
    """§4.3's own worked example ships `{table}` as an unresolved model-context token --
    it names no capture of any AST, so a renderer must never guess at it."""
    dax = render_target("CALCULATE(SUM({a}), ALLEXCEPT({table}, {dims}))", {"a": "Notional"})
    assert dax == "CALCULATE(SUM([Notional]), ALLEXCEPT({table}, {dims}))"


def test_render_target_with_no_captures_is_a_no_op() -> None:
    assert render_target("SUM([Notional])", {}) == "SUM([Notional])"


# --------------------------------------------------------------------------- _abstract_template


def test_abstract_template_is_the_reverse_of_render_target() -> None:
    captures = {"a": "Notional", "b": "Margin"}
    dax = "DIVIDE(SUM([Notional]), SUM([Margin]))"
    template = _abstract_template(dax, captures)
    assert template == "DIVIDE(SUM({a}), SUM({b}))"
    assert render_target(template, captures) == dax


def test_abstract_template_substitutes_longest_identifiers_first() -> None:
    """`Notional` is a substring of `NotionalTotal` -- abstracting the shorter name first
    would corrupt the longer one's own bracketed occurrence."""
    captures = {"a": "NotionalTotal", "b": "Notional"}
    dax = "([NotionalTotal] - [Notional])"
    template = _abstract_template(dax, captures)
    assert template == "({a} - {b})"


def test_abstract_template_leaves_a_literal_table_name_the_model_wrote_alone() -> None:
    """What this cannot do, honestly: an identifier the model's own DAX never referenced
    in the platform's `[Name]` bracket form stays literal text, not a placeholder."""
    dax = 'CALCULATE(SUM([Notional]), FactTrades[Desk] = "EQ")'
    template = _abstract_template(dax, {"a": "Notional"})
    assert template == 'CALCULATE(SUM({a}), FactTrades[Desk] = "EQ")'


# -------------------------------------------------------------------------- PromotionStatus


def test_promotion_status_as_dict_round_trips_every_field() -> None:
    status = PromotionStatus(
        pattern_id="pat_1", promotion_state="CANDIDATE", distinct_passing_calcs=3,
        has_failure=False, threshold=5, eligible=False, reason="only 3 of 5 required distinct proof passes",
    )
    assert status.as_dict() == {
        "pattern_id": "pat_1",
        "promotion_state": "CANDIDATE",
        "distinct_passing_calcs": 3,
        "has_failure": False,
        "threshold": 5,
        "eligible": False,
        "reason": "only 3 of 5 required distinct proof passes",
    }


# --------------------------------------------------------------------- evaluate_retirement (S5.5.2)


def test_evaluate_retirement_defaults_match_the_specs_own_dual_condition() -> None:
    """§9.3, verbatim: 'above a threshold (default 3 failures or a pass rate below 0.97
    over 30 applications)' -- the backlog AC's own paraphrase ('2 in 100') is not what is
    implemented; see the module's own docstring for why (spec wins on disagreement)."""
    assert DEFAULT_FAILURE_COUNT_THRESHOLD == 3
    assert DEFAULT_PASS_RATE_THRESHOLD == 0.97
    assert MIN_APPLICATIONS_FOR_RATE_CHECK == 30


def test_below_both_conditions_does_not_retire() -> None:
    check = evaluate_retirement(failures=1, applications=10)
    assert check.should_retire is False
    assert check.reason == "within threshold"
    assert check.pass_rate == 0.9


def test_the_absolute_failure_count_trips_the_threshold_regardless_of_application_count() -> None:
    """A pattern that fails outright (3 failures in only 3 applications) must not have to
    wait for 30 applications to accumulate before it is retired."""
    check = evaluate_retirement(failures=3, applications=3)
    assert check.should_retire is True
    assert "3 recorded failures" in check.reason


def test_a_pass_rate_below_threshold_trips_it_once_the_minimum_sample_is_reached() -> None:
    # 29 applications, 1 failure (pass rate 0.966, below 0.97) -- but under the 30-minimum,
    # so not yet checked.
    not_yet = evaluate_retirement(failures=1, applications=29)
    assert not_yet.should_retire is False

    # 30 applications, still 1 failure -- now the minimum sample is met and the ratio bites.
    now = evaluate_retirement(failures=1, applications=30)
    assert now.should_retire is True
    assert "pass rate" in now.reason


def test_a_pass_rate_at_or_above_threshold_never_retires_no_matter_how_many_applications() -> None:
    check = evaluate_retirement(failures=2, applications=100)  # pass rate 0.98
    assert check.should_retire is False
    assert check.pass_rate == 0.98


def test_zero_applications_has_no_pass_rate_and_does_not_retire() -> None:
    check = evaluate_retirement(failures=0, applications=0)
    assert check.should_retire is False
    assert check.pass_rate is None


def test_thresholds_are_independently_overridable() -> None:
    check = evaluate_retirement(
        failures=1, applications=5, failure_count_threshold=1, pass_rate_threshold=0.97, min_applications=100,
    )
    assert check.should_retire is True
    assert "1 recorded failures" in check.reason


def test_retirement_check_as_dataclass_carries_the_raw_counts() -> None:
    check = evaluate_retirement(failures=3, applications=10)
    assert isinstance(check, RetirementCheck)
    assert check.failures == 3
    assert check.applications == 10
