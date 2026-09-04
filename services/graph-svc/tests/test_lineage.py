"""Shared-lineage scoring.

S1.4.2: "edge weight shows shared lineage strength", so a model engineer can see why the
Cartographer grouped a family and challenge it. Challenging it means the number on the edge
has to be the number §12.1 defines:

    0.5·J(tables) + 0.3·J(fields) + 0.2·shared_calc_shapes / max_calc_shapes

The reader's queries are tested against PostgreSQL in the integration suite. What is tested
here is the scoring and the assembly — the parts where being subtly wrong would give a
model engineer a confident number that means nothing.
"""

from __future__ import annotations

import pytest

from astra_graph.lineage import (
    COLOUR_MODES,
    DEFAULT_MIN_STRENGTH,
    WEIGHT_FIELDS,
    WEIGHT_SHAPES,
    WEIGHT_TABLES,
    Reach,
    _elements,
    _narrow,
    _pair,
    _shared_lineage,
    similarity,
)
from astra_graph.lineage import reach as roll_up_reach


def reach(tables: set[str], fields: set[str], calcs: set[str] | None = None) -> Reach:
    return Reach(datasources=set(), tables=tables, fields=fields, calcs=calcs or set())


# --------------------------------------------------------------------- the formula


def test_the_score_is_the_weighted_sum_the_specification_gives() -> None:
    """§12.1, term by term, so a change to any weight fails here rather than silently
    re-ordering somebody's families."""
    strength, j_tables, j_fields, shared = similarity(
        ({"t1", "t2"}, {"t1", "t2"}),
        ({"f1", "f2", "f3", "f4"}, {"f1", "f2"}),
        ({"SUM(a)"}, {"SUM(a)", "DIV(a, b)"}),
    )

    assert j_tables == 1.0
    assert j_fields == 0.5
    assert shared == 1
    # 0.5·1.0 + 0.3·0.5 + 0.2·(1/2)
    assert strength == pytest.approx(WEIGHT_TABLES + WEIGHT_FIELDS * 0.5 + WEIGHT_SHAPES * 0.5)


def test_the_weights_are_the_ones_in_the_specification() -> None:
    assert (WEIGHT_TABLES, WEIGHT_FIELDS, WEIGHT_SHAPES) == (0.5, 0.3, 0.2)
    assert pytest.approx(1.0) == WEIGHT_TABLES + WEIGHT_FIELDS + WEIGHT_SHAPES


def test_shapes_are_divided_by_the_larger_set_not_the_union() -> None:
    """§12.1 says ``shared_calc_shapes / max_calc_shapes``.

    A workbook with two shapes that shares both with a workbook holding twenty has not
    matched it — it has matched a tenth of it, and the score has to say so.
    """
    _strength, _t, _f, shared = similarity(
        (set(), set()), (set(), set()), ({"a", "b"}, {f"s{i}" for i in range(20)} | {"a", "b"})
    )
    assert shared == 2

    strength, *_ = similarity(
        (set(), set()), (set(), set()), ({"a", "b"}, {f"s{i}" for i in range(20)} | {"a", "b"})
    )
    assert strength == pytest.approx(WEIGHT_SHAPES * (2 / 22))


def test_two_workbooks_that_share_nothing_score_zero() -> None:
    strength, *_ = similarity(({"t1"}, {"t2"}), ({"f1"}, {"f2"}), (set(), set()))
    assert strength == 0.0


def test_two_identical_workbooks_score_one() -> None:
    strength, *_ = similarity(
        ({"t1"}, {"t1"}), ({"f1"}, {"f1"}), ({"SUM(a)"}, {"SUM(a)"})
    )
    assert strength == pytest.approx(1.0)


def test_a_workbook_with_no_calculations_is_not_penalised_by_a_division_by_zero() -> None:
    """Two workbooks that share every table and field but define no calculations score
    0.8, not a crash and not a NaN."""
    strength, *_ = similarity(({"t"}, {"t"}), ({"f"}, {"f"}), (set(), set()))
    assert strength == pytest.approx(WEIGHT_TABLES + WEIGHT_FIELDS)


def test_empty_sets_on_both_sides_are_not_a_perfect_match() -> None:
    """Jaccard of two empty sets is undefined; treating it as 1.0 would make every pair of
    unparsed workbooks look identical."""
    strength, j_tables, j_fields, _ = similarity(
        (set(), set()), (set(), set()), (set(), set())
    )
    assert (strength, j_tables, j_fields) == (0.0, 0.0, 0.0)


# ------------------------------------------------------------------- pair assembly


def test_a_pair_has_one_canonical_order() -> None:
    """§4.1.2 notes SHARES_LINEAGE is undirected but stored directed, so a reader that did
    not canonicalise would draw each edge twice."""
    assert _pair("b", "a") == _pair("a", "b") == ("a", "b")


def test_only_pairs_that_share_something_are_scored() -> None:
    """The inverted index: on a real estate most pairs share nothing, and scoring them all
    is quadratic work to produce zeroes."""
    reaches = {
        "a": reach({"t1"}, {"f1"}),
        "b": reach({"t1"}, {"f2"}),
        "c": reach({"t9"}, {"f9"}),
    }

    links, origin = _shared_lineage(reaches, {}, stored={}, min_strength=0.0)

    assert {(link.source, link.target) for link in links} == {("a", "b")}
    assert origin == "computed"


def test_a_pair_sharing_only_calculation_shapes_is_still_found() -> None:
    """§12.1's third term is worth 0.2, which clears the default threshold on its own.

    An inverted index over tables and fields alone never proposes the pair, so the link
    vanishes without any error — found by pointing the screen at an estate whose workbooks
    all define the same ratio and getting an empty graph back.
    """
    reaches = {
        "a": reach({"t1"}, {"f1"}, {"c1"}),
        "b": reach({"t2"}, {"f2"}, {"c2"}),
    }
    shapes = {"c1": "DIV(SUM(a), SUM(b))", "c2": "DIV(SUM(a), SUM(b))"}

    links, _ = _shared_lineage(reaches, shapes, stored={}, min_strength=0.0)

    assert len(links) == 1
    assert links[0].shared_shapes == 1
    assert links[0].strength == pytest.approx(WEIGHT_SHAPES)
    assert links[0].strength > DEFAULT_MIN_STRENGTH, "so the default view shows it"


def test_a_shared_shape_is_the_shape_not_the_node() -> None:
    """Two different CalculatedField nodes with the same AST are one shared shape."""
    reaches = {"a": reach(set(), set(), {"calc-1"}), "b": reach(set(), set(), {"calc-2"})}

    links, _ = _shared_lineage(
        reaches, {"calc-1": "SUM(a)", "calc-2": "SUM(a)"}, stored={}, min_strength=0.0
    )

    assert links[0].shared_shapes == 1


def test_links_below_the_threshold_are_dropped() -> None:
    reaches = {
        "a": reach({"t1", "t2", "t3", "t4"}, set()),
        "b": reach({"t1", "t9", "t8", "t7"}, set()),
    }

    strong = _shared_lineage(reaches, {}, stored={}, min_strength=0.0)[0]
    weak = _shared_lineage(reaches, {}, stored={}, min_strength=0.2)[0]

    assert len(strong) == 1
    assert weak == [], "one shared table in seven is noise on a force graph"


def test_links_come_back_strongest_first() -> None:
    reaches = {
        "a": reach({"t1", "t2"}, set()),
        "b": reach({"t1", "t2"}, set()),
        "c": reach({"t1", "t3", "t4", "t5"}, set()),
    }

    links, _ = _shared_lineage(reaches, {}, stored={}, min_strength=0.0)

    assert [link.strength for link in links] == sorted(
        (link.strength for link in links), reverse=True
    )
    assert (links[0].source, links[0].target) == ("a", "b")


# ------------------------------------------------- the Cartographer's numbers win


def test_a_stored_edge_is_preferred_over_a_recomputed_one() -> None:
    """The whole point of the screen is challenging a grouping. The evidence has to be the
    numbers the clustering used, not a second opinion that happens to be close.
    """
    reaches = {"a": reach({"t1"}, {"f1"}), "b": reach({"t1"}, {"f1"})}
    stored = {
        ("a", "b"): {"jaccard_tables": 0.2, "jaccard_fields": 0.1, "shared_calc_count": 0}
    }

    links, origin = _shared_lineage(reaches, {}, stored=stored, min_strength=0.0)

    assert origin == "graph"
    assert links[0].origin == "graph"
    assert links[0].jaccard_tables == 0.2, "not the 1.0 this read would have computed"
    assert links[0].strength == pytest.approx(WEIGHT_TABLES * 0.2 + WEIGHT_FIELDS * 0.1)


def test_the_origin_says_which_numbers_are_on_screen() -> None:
    """A model engineer must be able to tell "the Cartographer says 0.71" from "nothing has
    clustered this yet, and the platform worked out 0.71 the same way it would"."""
    reaches = {"a": reach({"t1"}, set()), "b": reach({"t1"}, set())}

    assert _shared_lineage(reaches, {}, stored={}, min_strength=0.0)[1] == "computed"


# ------------------------------------------------------------------- reach and nodes


def test_a_workbook_reaches_tables_through_its_datasources_and_connections() -> None:
    """§12.1: "the set of source tables it reaches (through datasources and joins)"."""
    reached = roll_up_reach(
        {"sheet1", "sheet2"},
        datasources={"sheet1": {"ds1"}, "sheet2": {"ds2"}},
        connections={"ds1": {"conn1"}, "ds2": {"conn2"}},
        tables={"conn1": {"tableA"}, "conn2": {"tableA", "tableB"}},
        fields={"sheet1": {"f1"}, "sheet2": {"f2"}},
        calcs={"sheet1": {"c1"}},
    )

    assert reached.datasources == {"ds1", "ds2"}
    assert reached.tables == {"tableA", "tableB"}
    assert reached.fields == {"f1", "f2"}
    assert reached.calcs == {"c1"}


def test_the_graph_draws_workbooks_tables_and_fields_not_worksheets() -> None:
    """§15.3.2 asks for "workbooks ↔ tables ↔ fields". A worksheet is how a workbook
    reaches them, not something anybody groups on, and one node per sheet triples the
    graph for no added meaning."""
    workbooks = [
        {
            "id": "wb1",
            "name": "Daily VaR",
            "site": "RQA",
            "project": "Risk",
            "parse_quality": 1.0,
            "views_90d": 10,
        }
    ]
    nodes, edges = _elements(
        workbooks,
        {"wb1": reach({"tableA"}, {"f1"}, {"c1"})},
        {
            "tableA": {"type": "Table", "name": "positions"},
            "f1": {"type": "Field", "name": "Notional"},
            "c1": {"type": "CalculatedField", "name": "Margin %"},
        },
    )

    assert {node.type for node in nodes} == {"Workbook", "Table", "Field", "CalculatedField"}
    assert not any(node.type == "Worksheet" for node in nodes)
    assert {(edge.source, edge.target) for edge in edges} == {
        ("wb1", "tableA"),
        ("wb1", "f1"),
        ("wb1", "c1"),
    }


def test_a_table_shared_by_two_workbooks_is_one_node() -> None:
    """Otherwise the force graph shows two of everything and no sharing at all."""
    workbooks = [
        {"id": "wb1", "name": "A", "site": None, "project": None, "parse_quality": None,
         "views_90d": None},
        {"id": "wb2", "name": "B", "site": None, "project": None, "parse_quality": None,
         "views_90d": None},
    ]
    nodes, edges = _elements(
        workbooks,
        {"wb1": reach({"tableA"}, set()), "wb2": reach({"tableA"}, set())},
        {"tableA": {"type": "Table", "name": "positions"}},
    )

    assert len([node for node in nodes if node.id == "tableA"]) == 1
    assert len(edges) == 2, "both workbooks point at the one table"


# -------------------------------------------------------------------- what it says


def test_the_colour_modes_offer_state_by_name_and_say_why_it_is_empty() -> None:
    """§15.3.2 asks for "colour = state". The §3.2 state machine begins with the
    Cartographer's MU, so the mode is offered and disabled rather than quietly replaced."""
    modes = {mode["key"]: mode for mode in COLOUR_MODES}

    assert modes["mu_state"]["available"] is False
    assert "E3" in modes["mu_state"]["reason"]
    assert modes["type"]["available"] is True
    assert modes["family"]["available"] is True


def test_the_default_threshold_is_low_enough_to_show_real_sharing() -> None:
    """Two workbooks sharing half their tables and nothing else score 0.25, and should be
    on the screen by default."""
    strength, *_ = similarity(
        ({"t1", "t2"}, {"t1", "t3"}), (set(), set()), (set(), set())
    )
    assert strength > DEFAULT_MIN_STRENGTH


# ---------------------------------------------------------- choosing what to show


def workbooks_at(site: str, count: int) -> list[dict]:
    return [
        {"id": f"{site}-{i:04d}", "name": f"{site} wb {i:04d}", "site": site,
         "project": "P", "parse_quality": 1.0, "views_90d": 0}
        for i in range(count)
    ]


def test_an_estate_that_fits_is_shown_whole() -> None:
    everything = workbooks_at("rqa", 10)

    chosen, auto, truncated = _narrow(everything, 250, asked_for_a_scope=False)

    assert len(chosen) == 10
    assert (auto, truncated) == (None, False)


def test_an_unscoped_oversized_estate_narrows_to_its_largest_site() -> None:
    """Truncating the name-sorted list gives an alphabetical slice: it looks like the
    estate, it is not, and every shared-lineage link crossing the cut is simply absent.
    A whole site is a coherent sub-graph; a fragment of several is not."""
    everything = workbooks_at("gtaa", 40) + workbooks_at("rqa", 300)

    chosen, auto, truncated = _narrow(everything, 250, asked_for_a_scope=False)

    assert auto == "rqa"
    assert {w["site"] for w in chosen} == {"rqa"}, "one coherent site, not a slice of two"
    assert truncated is True, "rqa is still larger than the cap"


def test_a_narrowed_site_that_fits_is_not_reported_as_truncated() -> None:
    # 280 in total, so the estate does not fit — but the site it narrows to does.
    everything = workbooks_at("gtaa", 40) + workbooks_at("rqa", 240)

    chosen, auto, truncated = _narrow(everything, 250, asked_for_a_scope=False)

    assert (auto, truncated) == ("rqa", False)
    assert len(chosen) == 240


def test_a_caller_who_named_a_scope_gets_that_scope_capped() -> None:
    """Silently substituting a different scope for one somebody asked for is worse than
    truncating: they would be looking at an answer to a question they did not ask."""
    everything = workbooks_at("rqa", 300)

    chosen, auto, truncated = _narrow(everything, 250, asked_for_a_scope=True)

    assert (auto, truncated) == (None, True)
    assert len(chosen) == 250


def test_the_choice_is_deterministic_when_two_sites_tie() -> None:
    everything = workbooks_at("bbb", 300) + workbooks_at("aaa", 300)

    first = _narrow(everything, 250, asked_for_a_scope=False)[1]
    second = _narrow(list(reversed(everything)), 250, asked_for_a_scope=False)[1]

    assert first == second

