"""Release trains — story S3.2.1.

    "Trains are proposed by ordering families by (shared model readiness, usage, tier
    mix) and packing MUs to a configurable train size; the proposal explains each train in
    one paragraph generated from the graph (which families, why this order)."

The graph read (family/usage/tier gathering) is exercised against real PostgreSQL in the
integration suite. What is tested here is the algorithm: ordering, family-atomic packing,
scheduling and the explanation text — the parts where being subtly wrong would hand a
Programme Manager a plan that looks measured and is not.
"""

from __future__ import annotations

from datetime import date

import pytest

from astra_graph.errors import InvalidRequestError
from astra_graph.trains import (
    FamilySignal,
    explain_train,
    gate_schedule,
    order_members,
    pack_trains,
    train_window,
    validate_train_sizes,
)


def _family(
    id_: str,
    *,
    name: str | None = None,
    state: str = "PROPOSED",
    size: int = 3,
    usage_total: int = 0,
    tier_score: float | None = None,
) -> FamilySignal:
    members = tuple(f"{id_}-wb-{i}" for i in range(size))
    return FamilySignal(
        id=id_,
        name=name or id_,
        state=state,
        members=members,
        usage_total=usage_total,
        tier_score=tier_score,
    )


# ------------------------------------------------------------------- validate_train_sizes


def test_train_sizes_must_name_at_least_one_train() -> None:
    with pytest.raises(InvalidRequestError):
        validate_train_sizes([])


def test_every_train_size_must_be_positive() -> None:
    with pytest.raises(InvalidRequestError):
        validate_train_sizes([277, 0, 184])
    with pytest.raises(InvalidRequestError):
        validate_train_sizes([-5])


def test_valid_sizes_pass_through_as_a_tuple() -> None:
    assert validate_train_sizes([277, 328, 184, 177, 101]) == (277, 328, 184, 177, 101)


# ----------------------------------------------------------------------- readiness_rank


def test_singleton_and_proposed_rank_equally_ready() -> None:
    singleton = _family("f1", state="SINGLETON")
    proposed = _family("f2", state="PROPOSED")
    assert singleton.readiness_rank == proposed.readiness_rank


def test_readiness_increases_through_the_lifecycle() -> None:
    ranks = [_family("f", state=state).readiness_rank for state in
              ("PROPOSED", "DRAFT", "IN_REVIEW", "APPROVED", "BUILT", "PUBLISHED")]
    assert ranks == sorted(ranks)
    assert len(set(ranks)) == len(ranks)


def test_an_unrecognised_state_ranks_last_rather_than_raising() -> None:
    assert _family("f", state="DEPRECATED").readiness_rank == -1


# --------------------------------------------------------------------------- pack_trains


def test_families_are_ordered_by_readiness_then_usage_then_tier_before_packing() -> None:
    less_ready = _family("later", state="PROPOSED", size=1, usage_total=1000)
    more_ready = _family("earlier", state="DRAFT", size=1, usage_total=1)

    [train] = pack_trains([less_ready, more_ready], [10])
    assert [f.id for f in train] == ["earlier", "later"]


def test_within_the_same_readiness_higher_usage_goes_first() -> None:
    busy = _family("busy", state="PROPOSED", size=1, usage_total=900)
    quiet = _family("quiet", state="PROPOSED", size=1, usage_total=10)

    [train] = pack_trains([quiet, busy], [10])
    assert [f.id for f in train] == ["busy", "quiet"]


def test_within_readiness_and_usage_the_simpler_tier_mix_goes_first() -> None:
    simple = _family("simple", state="PROPOSED", size=1, usage_total=0, tier_score=0.0)
    complex_ = _family("complex", state="PROPOSED", size=1, usage_total=0, tier_score=3.0)

    [train] = pack_trains([complex_, simple], [10])
    assert [f.id for f in train] == ["simple", "complex"]


def test_a_family_is_never_split_across_trains() -> None:
    """§3.3: each family is designed once — splitting it over two trains would design it
    twice. A family bigger than a train's remaining room still lands there whole."""
    oversized = _family("big", size=8)

    [train] = pack_trains([oversized], [5])
    assert train == [oversized]


def test_packing_fills_one_train_before_starting_the_next() -> None:
    a = _family("a", size=3, usage_total=3)
    b = _family("b", size=3, usage_total=2)
    c = _family("c", size=3, usage_total=1)

    trains = pack_trains([a, b, c], [6, 6])
    assert [f.id for f in trains[0]] == ["a", "b"]
    assert [f.id for f in trains[1]] == ["c"]


def test_families_left_over_land_in_the_last_configured_train() -> None:
    a = _family("a", size=3, usage_total=3)
    b = _family("b", size=3, usage_total=2)
    c = _family("c", size=3, usage_total=1)

    trains = pack_trains([a, b, c], [3])
    assert [f.id for f in trains[0]] == ["a", "b", "c"]


def test_a_train_with_no_families_left_to_give_it_stays_empty() -> None:
    """Fewer families than configured trains (the small dev fixture, say) — later slots
    are legitimately empty, not an error."""
    a = _family("a", size=1)

    trains = pack_trains([a], [277, 328, 184])
    assert trains[0] == [a]
    assert trains[1] == []
    assert trains[2] == []


def test_packing_is_deterministic_regardless_of_input_order() -> None:
    a = _family("a", state="DRAFT", usage_total=50)
    b = _family("b", state="PROPOSED", usage_total=999)
    c = _family("c", state="PROPOSED", usage_total=1)

    forward = pack_trains([a, b, c], [100])
    backward = pack_trains([c, b, a], [100])
    assert [f.id for f in forward[0]] == [f.id for f in backward[0]]


# -------------------------------------------------------------------------- train_window


def test_trains_are_scheduled_back_to_back() -> None:
    start, end = train_window(1, date(2027, 1, 4), 30)
    assert (start, end) == (date(2027, 1, 4), date(2027, 2, 2))

    next_start, next_end = train_window(2, date(2027, 1, 4), 30)
    assert next_start == date(2027, 2, 3)
    assert next_end == date(2027, 3, 4)


# ------------------------------------------------------------------------ gate_schedule


def test_g2_clusters_near_the_start_and_g3_near_the_end() -> None:
    schedule = gate_schedule(date(2027, 1, 4), date(2027, 2, 2))
    assert schedule["G2"]["planned_date"] == "2027-01-04"
    assert schedule["G3"]["planned_date"] == "2027-02-02"


# ------------------------------------------------------------------------- order_members


def test_members_within_a_family_are_ordered_by_usage_then_id() -> None:
    family = _family("f", size=3, usage_total=0)
    usage_of = {"f-wb-0": 5, "f-wb-1": 50, "f-wb-2": 5}

    ordered = order_members([family], usage_of)
    assert ordered == ("f-wb-1", "f-wb-0", "f-wb-2")


def test_member_order_follows_family_order() -> None:
    first = _family("first", size=1)
    second = _family("second", size=1)

    ordered = order_members([first, second], {})
    assert ordered == ("first-wb-0", "second-wb-0")


# ------------------------------------------------------------------------- explain_train


def test_the_explanation_names_the_leading_families_and_the_ordering_factors() -> None:
    leader = _family("f1", name="Positions + 4 more", state="DRAFT", size=3, usage_total=1200)
    explanation = explain_train(1, [leader], leader.members, {})

    assert "Train 1" in explanation
    assert "3 MUs" in explanation
    assert "1 family" in explanation
    assert "Positions + 4 more" in explanation
    assert "DRAFT" in explanation
    assert "1,200 views/90d" in explanation
    assert "shared-model readiness, usage and tier mix" in explanation


def test_the_explanation_pluralises_multiple_families() -> None:
    a = _family("a", size=1)
    b = _family("b", size=1)
    explanation = explain_train(2, [a, b], a.members + b.members, {})

    assert "2 families" in explanation
    assert "are" in explanation


def test_the_explanation_reports_tier_mix_including_untiered() -> None:
    family = _family("f", size=2)
    members = family.members
    tier_of = {members[0]: "SIMPLE"}

    explanation = explain_train(1, [family], members, tier_of)

    assert "1 simple" in explanation
    assert "1 untiered" in explanation


def test_the_explanation_summarises_rather_than_lists_every_family() -> None:
    families = [_family(f"f{i}", size=1) for i in range(6)]
    members = tuple(m for f in families for m in f.members)

    explanation = explain_train(1, families, members, {})

    assert "and 3 more" in explanation
