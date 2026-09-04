"""The Cartographer's algorithm — story S3.1.1.

    "Similarity = 0.5*J(tables) + 0.3*J(fields) + 0.2*(shared_calc_shapes / max_calc_shapes)
    ... Agglomerative clustering with the configurable threshold (default 0.35) produces
    ModelFamily nodes ... Families with a single member are merged into the nearest family
    or held as SINGLETON with the reason."

The default is 0.55, not the backlog's 0.35 — see cartographer.py's own module docstring for
why the specification's figure is the one that governs (the backlog's own correction rule).
Tests here use whatever threshold makes the point being tested obvious, not necessarily the
default, and say so.

The graph reads are exercised against real PostgreSQL in the integration suite. What is
tested here is the algorithm: clustering, the undersized-family pass, grain and evidence —
the parts where being subtly wrong would hand a model engineer a family count that looks
measured and is not.
"""

from __future__ import annotations

from astra_graph.cartographer import (
    DEFAULT_MIN_FAMILY_SIZE,
    FamilyEvidence,
    _derived_edge_id,
    _pair,
    _sheet_dimensions,
    _sheet_encoded_fields,
    agglomerative_clusters,
    candidate_grain,
    family_evidence,
    resolve_undersized,
)

# ------------------------------------------------------------- agglomerative_clusters


def test_two_similar_workbooks_merge() -> None:
    clusters = agglomerative_clusters(
        ["a", "b"], {("a", "b"): 0.8}, threshold=0.55
    )
    assert clusters == [frozenset({"a", "b"})]


def test_two_dissimilar_workbooks_do_not_merge() -> None:
    clusters = agglomerative_clusters(
        ["a", "b"], {("a", "b"): 0.2}, threshold=0.55
    )
    assert set(clusters) == {frozenset({"a"}), frozenset({"b"})}


def test_a_workbook_sharing_nothing_never_enters_the_merge_loop() -> None:
    """Not merged with anyone, not because it lost a comparison but because there was
    nothing to compare — it never appears in a candidate pair at all."""
    clusters = agglomerative_clusters(
        ["a", "b", "c"], {("a", "b"): 0.9}, threshold=0.55
    )
    assert frozenset({"c"}) in clusters
    assert frozenset({"a", "b"}) in clusters


def test_average_linkage_dilutes_a_weak_third_member() -> None:
    """A-B merge first (strong). Their average similarity to C is diluted by C's weak link
    to B — the Lance-Williams update, not a re-scan of raw pairs — and stays below
    threshold, so the merged cluster does NOT pull C in."""
    pair_strength = {("a", "b"): 0.9, ("b", "c"): 0.3}
    clusters = agglomerative_clusters(["a", "b", "c"], pair_strength, threshold=0.55)

    assert frozenset({"a", "b"}) in clusters
    assert frozenset({"c"}) in clusters


def test_three_mutually_strong_workbooks_all_merge() -> None:
    pair_strength = {("a", "b"): 0.9, ("b", "c"): 0.85, ("a", "c"): 0.8}
    clusters = agglomerative_clusters(["a", "b", "c"], pair_strength, threshold=0.55)

    assert clusters == [frozenset({"a", "b", "c"})]


def test_clustering_is_deterministic_under_tied_scores() -> None:
    """Two equally-scored candidate merges: repeated runs must agree with each other, not
    just internally — a re-run on an unchanged estate should never propose a different
    family split by coin flip."""
    pair_strength = {("a", "b"): 0.7, ("c", "d"): 0.7}
    first = agglomerative_clusters(["a", "b", "c", "d"], pair_strength, threshold=0.55)
    second = agglomerative_clusters(["d", "c", "b", "a"], pair_strength, threshold=0.55)

    assert set(first) == set(second) == {frozenset({"a", "b"}), frozenset({"c", "d"})}


def test_a_single_workbook_estate_is_one_cluster() -> None:
    assert agglomerative_clusters(["a"], {}, threshold=0.55) == [frozenset({"a"})]


def test_an_empty_estate_clusters_to_nothing() -> None:
    assert agglomerative_clusters([], {}, threshold=0.55) == []


# ----------------------------------------------------------------- resolve_undersized


def test_an_undersized_cluster_merges_into_its_nearest_neighbour() -> None:
    clusters = [frozenset({"a", "b"}), frozenset({"c"})]
    pair_strength = {("b", "c"): 0.4}

    resolved = resolve_undersized(clusters, pair_strength, min_family_size=3)

    assert resolved.families == [frozenset({"a", "b", "c"})]
    assert resolved.singletons == {}


def test_an_isolated_workbook_is_held_as_singleton_with_a_reason() -> None:
    clusters = [frozenset({"a", "b", "c"}), frozenset({"z"})]
    pair_strength = {("a", "b"): 0.6, ("a", "c"): 0.6}  # nothing touches "z"

    resolved = resolve_undersized(clusters, pair_strength, min_family_size=3)

    assert resolved.families == [frozenset({"a", "b", "c"})]
    assert frozenset({"z"}) in resolved.singletons
    reason = resolved.singletons[frozenset({"z"})]
    assert "minimum family size" in reason
    assert "3" in reason


def test_two_undersized_clusters_that_merge_and_are_still_too_small_are_held_as_singleton() -> None:
    """Merging is not a one-shot: a family still under the minimum after a merge is still
    "under a minimum size" and the same rule applies again. With nothing else in the
    estate to merge into, the combined pair is held for review rather than silently
    accepted below the floor."""
    clusters = [frozenset({"a"}), frozenset({"b"})]
    pair_strength = {("a", "b"): 0.3}

    resolved = resolve_undersized(clusters, pair_strength, min_family_size=3)

    assert resolved.families == []
    assert frozenset({"a", "b"}) in resolved.singletons


def test_undersized_resolution_terminates_even_when_everything_starts_too_small() -> None:
    """Every cluster starts undersized, and a merge that is still undersized is
    reconsidered rather than accepted — the loop must still terminate, either by growing
    clusters past the floor or by running out of merge candidates into singletons."""
    clusters = [frozenset({str(i)}) for i in range(6)]
    pair_strength = {(str(i), str(i + 1)): 0.1 for i in range(5)}

    resolved = resolve_undersized(clusters, pair_strength, min_family_size=DEFAULT_MIN_FAMILY_SIZE)

    total_members = sum(len(c) for c in resolved.families) + sum(
        len(c) for c in resolved.singletons
    )
    assert total_members == 6


def test_nearest_means_highest_average_not_first_found() -> None:
    """"b" has no connection to anything at all, so it cannot be swept in by "a"'s merge —
    this isolates the claim under test: given a choice, "a" merges into the strongly-linked
    big cluster, not into a weaker option that merely came first."""
    clusters = [frozenset({"a"}), frozenset({"b"}), frozenset({"c", "d", "e"})]
    pair_strength = {("a", "c"): 0.9, ("a", "d"): 0.9, ("a", "e"): 0.9}

    resolved = resolve_undersized(clusters, pair_strength, min_family_size=3)

    assert frozenset({"a", "c", "d", "e"}) in resolved.families
    assert frozenset({"b"}) in resolved.singletons


# --------------------------------------------------------------------- candidate_grain


def test_the_most_frequent_dimension_set_wins() -> None:
    sets = [frozenset({"Desk"}), frozenset({"Desk"}), frozenset({"Book"})]
    assert candidate_grain(sets) == ("Desk",)


def test_a_tie_prefers_the_smaller_more_minimal_set() -> None:
    sets = [frozenset({"Desk"}), frozenset({"Desk", "Book"})]
    assert candidate_grain(sets) == ("Desk",)


def test_a_tie_at_the_same_size_breaks_lexicographically() -> None:
    sets = [frozenset({"Zebra"}), frozenset({"Alpha"})]
    assert candidate_grain(sets) == ("Alpha",)


def test_empty_sheets_contribute_nothing() -> None:
    assert candidate_grain([frozenset(), frozenset()]) == ()
    assert candidate_grain([]) == ()


def test_grain_is_returned_sorted() -> None:
    sets = [frozenset({"Book", "Desk"})]
    assert candidate_grain(sets) == ("Book", "Desk")


# ---------------------------------------------------------------------- family_evidence


def test_evidence_needs_two_or_more_members_not_one() -> None:
    """A table only one member reaches is not evidence the family was grouped on — it is
    just a fact about that one member."""
    tables_of = {"a": {"t1", "t2"}, "b": {"t1"}, "c": {"t3"}}
    evidence = family_evidence(["a", "b", "c"], tables_of, {}, {})

    assert evidence.shared_tables == ("t1",)


def test_evidence_covers_fields_and_calc_shapes_the_same_way() -> None:
    fields_of = {"a": {"Desk"}, "b": {"Desk"}}
    shapes_of = {"a": {"SUM(a)"}, "b": {"SUM(a)"}, "c": {"SUM(a)"}}

    evidence = family_evidence(["a", "b", "c"], {}, fields_of, shapes_of)

    assert evidence.shared_fields == ("Desk",)
    assert evidence.shared_calc_shapes == 1


def test_a_singleton_family_has_no_shared_evidence() -> None:
    """Self-consistent by construction: with one member, nothing can be reached by "two or
    more" of them — which is exactly why the family is a singleton in the first place."""
    evidence = family_evidence(["a"], {"a": {"t1"}}, {"a": {"f1"}}, {"a": {"shape"}})
    assert evidence == FamilyEvidence(shared_tables=(), shared_fields=(), shared_calc_shapes=0)


# --------------------------------------------------------------------- shelf reading


def test_encoded_fields_come_from_every_shelf() -> None:
    properties = {
        "rows_shelf": ["Desk"],
        "cols_shelf": ["Trade Date"],
        "marks_shelf": ["color:Book", "size:Notional"],
    }
    assert _sheet_encoded_fields(properties) == {"Desk", "Trade Date", "Book", "Notional"}


def test_grain_dimensions_exclude_marks() -> None:
    """Marks are encoding channels (colour, size...), not grouping — §12.1's grain is a
    *dimension* set, and Tableau calls rows/cols the axes a view is grouped by."""
    properties = {
        "rows_shelf": ["Desk"],
        "cols_shelf": [],
        "marks_shelf": ["color:Book"],
    }
    assert _sheet_dimensions(properties) == frozenset({"Desk"})


def test_missing_shelves_are_read_as_empty_not_an_error() -> None:
    assert _sheet_encoded_fields({}) == set()
    assert _sheet_dimensions({}) == frozenset()


# -------------------------------------------------------------------- deterministic ids


def test_derived_edge_id_is_deterministic() -> None:
    first = _derived_edge_id("SHARES_LINEAGE", "wb-a", "wb-b")
    second = _derived_edge_id("SHARES_LINEAGE", "wb-a", "wb-b")
    assert first == second


def test_derived_edge_id_is_a_valid_ulid_shape() -> None:
    value = _derived_edge_id("SHARES_LINEAGE", "wb-a", "wb-b")
    assert len(value) == 26
    assert value[0] in "01234567"


def test_derived_edge_id_differs_for_a_different_pair() -> None:
    a = _derived_edge_id("SHARES_LINEAGE", "wb-a", "wb-b")
    b = _derived_edge_id("SHARES_LINEAGE", "wb-a", "wb-c")
    assert a != b


def test_pair_is_canonically_ordered() -> None:
    assert _pair("b", "a") == _pair("a", "b") == ("a", "b")
