"""Projected versus planned dates per train — story S3.2.3.

    "Projection uses measured throughput per state over the trailing 14 days and the MU
    counts remaining; shown as a date with a confidence band."

The graph read (mining real event history for genuine state transitions) is exercised
against real PostgreSQL in the integration suite. What is tested here is the arithmetic:
working-day counting and the per-state projection, including the "insufficient data"
honesty this module is built around — most trains, most of the time, have no measured
throughput at all, and that must never be silently papered over with a guess.
"""

from __future__ import annotations

from datetime import date

from astra_graph.train_projection import StateThroughput, project_state, working_days_between

# ------------------------------------------------------------------- working_days_between


def test_same_day_is_zero() -> None:
    assert working_days_between(date(2027, 3, 1), date(2027, 3, 1)) == 0


def test_a_pure_business_week_counts_five() -> None:
    # Monday 1 March 2027 to the following Monday is five working days.
    assert working_days_between(date(2027, 3, 1), date(2027, 3, 8)) == 5


def test_weekends_are_not_counted() -> None:
    # Friday to the following Monday is one working day, not three.
    assert working_days_between(date(2027, 3, 5), date(2027, 3, 8)) == 1


def test_negative_when_end_precedes_start() -> None:
    assert working_days_between(date(2027, 3, 8), date(2027, 3, 1)) == -5


# ------------------------------------------------------------------------- project_state


def test_no_throughput_data_means_no_projection() -> None:
    assert project_state(None, 10, reference=date(2027, 1, 1)) == (None, None, None)


def test_zero_measured_throughput_means_no_projection() -> None:
    zero = StateThroughput(state="CLUSTERED", exits=0, daily_mean=0.0, daily_stddev=0.0)
    assert project_state(zero, 10, reference=date(2027, 1, 1)) == (None, None, None)


def test_nothing_remaining_means_no_projection() -> None:
    busy = StateThroughput(state="PROVING", exits=14, daily_mean=1.0, daily_stddev=0.0)
    assert project_state(busy, 0, reference=date(2027, 1, 1)) == (None, None, None)


def test_a_point_estimate_divides_remaining_by_the_daily_mean() -> None:
    throughput = StateThroughput(state="PROVING", exits=14, daily_mean=2.0, daily_stddev=0.0)
    point, early, late = project_state(throughput, 10, reference=date(2027, 1, 1))
    # 10 remaining / 2 per day = 5 days.
    assert point == date(2027, 1, 6)
    assert early == date(2027, 1, 6)  # no variance: early and late collapse to the point
    assert late == date(2027, 1, 6)


def test_the_confidence_band_widens_with_measured_variance() -> None:
    throughput = StateThroughput(state="PROVING", exits=14, daily_mean=2.0, daily_stddev=1.0)
    point, early, late = project_state(throughput, 10, reference=date(2027, 1, 1))
    # early (optimistic) uses the higher rate (3/day -> ~4 days); late uses the lower
    # rate (1/day -> 10 days); point uses the mean (2/day -> 5 days).
    assert early is not None and point is not None and late is not None
    assert early < point < late


def test_a_late_bound_at_or_below_zero_throughput_is_absent_not_infinite() -> None:
    # daily_mean - daily_stddev <= 0: the pessimistic case is "might never finish",
    # which is not a date this module will print.
    throughput = StateThroughput(state="MENDING", exits=2, daily_mean=0.5, daily_stddev=0.6)
    _point, _early, late = project_state(throughput, 10, reference=date(2027, 1, 1))
    assert late is None
