"""Exception ageing and the Mender close rate -- story S8.3.2, continuing F8.3/E8.

`aggregate_ageing` is pure -- fed already-hydrated `ExceptionCase` properties, no
database. The graph-coupled shell (`exception_ageing`, one `hydrate` read) is covered in
`test_integration_exception_ageing.py` instead, the same "pure core, graph-coupled
shell" split this epic's own prior stories already established.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from astra_graph.exception_ageing import MENDER_CLOSE_RATE_TARGET, aggregate_ageing


def _iso(days_ago: float) -> str:
    return (datetime.now(UTC) - timedelta(days=days_ago)).isoformat().replace("+00:00", "Z")


def _case(**overrides: object) -> dict[str, object]:
    base: dict[str, object] = {
        "class": "AGGREGATION", "state": "OPEN", "created_at": _iso(0.1), "closed_by": None,
    }
    base.update(overrides)
    return base


# ------------------------------------------------------------------ open by class/age band


def test_groups_open_cases_by_class_and_age_band() -> None:
    cases = {
        "c1": _case(**{"class": "AGGREGATION", "state": "OPEN", "created_at": _iso(0.1)}),
        "c2": _case(**{"class": "AGGREGATION", "state": "OPEN", "created_at": _iso(2)}),
        "c3": _case(**{"class": "KEY_MISSING", "state": "BLOCKED", "created_at": _iso(10)}),
    }
    result = aggregate_ageing(cases)
    entries = {(e["class"], e["age_band"]): e["count"] for e in result["open_by_class_and_age_band"]}
    assert entries == {
        ("AGGREGATION", "under_1d"): 1, ("AGGREGATION", "1_3d"): 1, ("KEY_MISSING", "7d_plus"): 1,
    }
    assert result["total_open"] == 3


def test_closed_cases_are_excluded_from_the_open_breakdown() -> None:
    cases = {"c1": _case(state="CLOSED")}
    result = aggregate_ageing(cases)
    assert result["open_by_class_and_age_band"] == []
    assert result["total_open"] == 0


def test_every_age_band_carries_its_own_label() -> None:
    cases = {"c1": _case(created_at=_iso(5))}
    result = aggregate_ageing(cases)
    entry = result["open_by_class_and_age_band"][0]
    assert entry["age_band"] == "3_7d"
    assert entry["age_band_label"] == "3-7 days"
    labels = {band["key"]: band["label"] for band in result["age_bands"]}
    assert labels == {
        "under_1d": "under 1 day", "1_3d": "1-3 days", "3_7d": "3-7 days", "7d_plus": "7+ days",
    }


# --------------------------------------------------------------------- Mender close rate


def test_mender_closed_is_state_closed_with_no_closed_by() -> None:
    cases = {
        "c1": _case(state="CLOSED", closed_by=None),
        "c2": _case(state="CLOSED", closed_by="user:engineer@artizent.example"),
        "c3": _case(state="OPEN", closed_by=None),
    }
    result = aggregate_ageing(cases)["mender_close_rate"]
    assert result["mender_closed"] == 1
    assert result["total_failures"] == 3
    assert result["rate"] == 1 / 3


def test_decision_alone_would_have_misclassified_a_human_closed_redesign_case() -> None:
    """`visual_redesign.close_redesign_exception` never sets `decision` -- confirmed by
    direct read of that function -- so a naive `decision IS NULL` check would wrongly
    count this real, human-closed case as Mender-closed. `closed_by` is the real signal."""
    cases = {
        "c1": _case(
            **{"class": "VISUAL_REDESIGN", "state": "CLOSED", "closed_by": "user:engineer@artizent.example",
               "decision": None},
        ),
    }
    result = aggregate_ageing(cases)["mender_close_rate"]
    assert result["mender_closed"] == 0


def test_visual_redesign_is_excluded_from_both_sides_of_the_close_rate_ratio() -> None:
    cases = {
        "c1": _case(**{"class": "VISUAL_REDESIGN", "state": "CLOSED", "closed_by": None}),
        "c2": _case(**{"class": "AGGREGATION", "state": "CLOSED", "closed_by": None}),
    }
    result = aggregate_ageing(cases)["mender_close_rate"]
    assert result["total_failures"] == 1
    assert result["mender_closed"] == 1
    assert result["rate"] == 1.0


def test_rate_is_honestly_none_with_no_eligible_failures_at_all() -> None:
    cases = {"c1": _case(**{"class": "VISUAL_REDESIGN", "state": "CLOSED", "closed_by": None})}
    result = aggregate_ageing(cases)["mender_close_rate"]
    assert result["total_failures"] == 0
    assert result["rate"] is None
    assert result["meets_target"] is None


def test_meets_target_compares_the_real_rate_against_the_r1_floor() -> None:
    cases = {f"c{i}": _case(state="CLOSED", closed_by=None) for i in range(7)}
    cases.update({f"o{i}": _case(state="OPEN") for i in range(3)})
    result = aggregate_ageing(cases)["mender_close_rate"]
    assert result["rate"] == 0.7
    assert result["target"] == MENDER_CLOSE_RATE_TARGET
    assert result["meets_target"] is True


def test_below_target_reports_false_not_a_silent_pass() -> None:
    cases = {f"c{i}": _case(state="CLOSED", closed_by=None) for i in range(6)}
    cases.update({f"o{i}": _case(state="OPEN") for i in range(4)})
    result = aggregate_ageing(cases)["mender_close_rate"]
    assert result["rate"] == 0.6
    assert result["meets_target"] is False
