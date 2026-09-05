"""Confidence calibration's own math — story S5.3.3.

    "Model declares a confidence in the output schema; the platform records it and, per
    §16.3, reports calibration (declared vs observed proof rate) in ten buckets. Below a
    configurable calibration floor a task class is routed to the small-model-plus-proof
    path rather than trusted."

Pure logic only, against `build_report` directly — no database. The real store's own
persistence (`PostgresCalibrationStore`) is integration-only.
"""

from __future__ import annotations

from astra_graph.calibration import (
    DEFAULT_CALIBRATION_FLOOR,
    MIN_OBSERVATIONS_FOR_FLOOR_CHECK,
    build_report,
)


def test_an_empty_history_has_no_rate_no_error_and_is_not_below_floor() -> None:
    report = build_report([], task_class="transpile_c3")
    assert report.total_observations == 0
    assert report.overall_pass_rate is None
    assert report.calibration_error is None
    assert report.below_floor is False
    assert report.routable_tier == "reasoning"
    assert len(report.buckets) == 10
    assert all(b.count == 0 for b in report.buckets)


def test_every_observation_lands_in_its_own_bucket() -> None:
    observations = [(0.05, True), (0.15, True), (0.95, False)]
    report = build_report(observations, task_class="transpile_c3")
    assert report.buckets[0].count == 1
    assert report.buckets[1].count == 1
    assert report.buckets[9].count == 1
    assert report.buckets[0].observed_pass_rate == 1.0
    assert report.buckets[9].observed_pass_rate == 0.0


def test_confidence_of_exactly_one_lands_in_the_last_bucket_not_an_eleventh() -> None:
    report = build_report([(1.0, True)], task_class="transpile_c3")
    assert report.buckets[9].count == 1
    assert all(b.count == 0 for b in report.buckets[:9])


def test_confidence_of_exactly_zero_lands_in_the_first_bucket() -> None:
    report = build_report([(0.0, False)], task_class="transpile_c3")
    assert report.buckets[0].count == 1


def test_out_of_range_confidence_is_clamped_not_dropped_or_crashed_on() -> None:
    report = build_report([(-0.5, True), (1.5, False)], task_class="transpile_c3")
    assert report.total_observations == 2
    assert report.buckets[0].count == 1
    assert report.buckets[9].count == 1


def test_overall_pass_rate_is_across_every_observation_not_per_bucket() -> None:
    observations = [(0.1, True), (0.1, False), (0.9, True)]
    report = build_report(observations, task_class="transpile_c3")
    assert report.overall_pass_rate == 2 / 3


def test_calibration_error_is_mean_absolute_gap_over_non_empty_buckets_only() -> None:
    # Bucket 0 ([0.0, 0.1)): declared ~0.05 mean, observed 1.0 pass -> |0.05 - 1.0| = 0.95
    # Bucket 9 ([0.9, 1.0]): declared ~0.95 mean, observed 0.0 pass -> |0.95 - 0.0| = 0.95
    observations = [(0.05, True), (0.05, True), (0.95, False), (0.95, False)]
    report = build_report(observations, task_class="transpile_c3")
    assert report.calibration_error is not None
    assert abs(report.calibration_error - 0.95) < 1e-9


def test_well_calibrated_history_has_a_small_calibration_error() -> None:
    # Declared ~0.9, observed 90% -- a textbook well-calibrated bucket.
    observations = [(0.9, True)] * 9 + [(0.9, False)] * 1
    report = build_report(observations, task_class="transpile_c3")
    assert report.calibration_error is not None
    assert report.calibration_error < 0.02


def test_fewer_than_the_minimum_observations_is_never_below_floor_even_if_failing() -> None:
    observations = [(0.9, False)] * (MIN_OBSERVATIONS_FOR_FLOOR_CHECK - 1)
    report = build_report(observations, task_class="transpile_c3")
    assert report.overall_pass_rate == 0.0
    assert report.below_floor is False
    assert report.routable_tier == "reasoning"


def test_at_the_minimum_observations_a_low_pass_rate_is_below_floor() -> None:
    observations = [(0.9, False)] * MIN_OBSERVATIONS_FOR_FLOOR_CHECK
    report = build_report(observations, task_class="transpile_c3")
    assert report.total_observations == MIN_OBSERVATIONS_FOR_FLOOR_CHECK
    assert report.below_floor is True
    assert report.routable_tier == "small_model_plus_proof"


def test_a_pass_rate_exactly_at_the_floor_is_not_below_it() -> None:
    passing = int(DEFAULT_CALIBRATION_FLOOR * MIN_OBSERVATIONS_FOR_FLOOR_CHECK)
    observations = [(0.9, True)] * passing + [(0.9, False)] * (MIN_OBSERVATIONS_FOR_FLOOR_CHECK - passing)
    report = build_report(observations, task_class="transpile_c3")
    assert report.overall_pass_rate == DEFAULT_CALIBRATION_FLOOR
    assert report.below_floor is False


def test_a_custom_floor_is_honoured() -> None:
    # 0.9 pass rate: clears the default floor (0.80) but not a stricter custom one (0.95).
    observations = [(0.9, True)] * 9 + [(0.9, False)] * 1
    against_default = build_report(observations, task_class="transpile_c3")
    against_strict = build_report(observations, task_class="transpile_c3", floor=0.95)
    assert against_default.below_floor is False
    assert against_strict.below_floor is True


def test_task_class_and_floor_are_carried_onto_the_report() -> None:
    report = build_report([], task_class="transpile_c3_small_model", floor=0.5)
    assert report.task_class == "transpile_c3_small_model"
    assert report.floor == 0.5
