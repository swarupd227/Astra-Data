"""The Estate Explorer's reads and actions.

S1.4.1: the site → project tree with counts and parse status, the workbook table with
faceted filters, and the Programme Manager's scope actions — each with a required reason.

The reader's four queries are tested against PostgreSQL in the integration suite. What is
tested here is everything the Explorer *decides*: which rows a filter admits, what a facet
count means, how the tree rolls up, and who is allowed to change scope.
"""

from __future__ import annotations

import pytest

from astra_graph.estate import (
    PARSE_QUALITY_BANDS,
    PENDING_COLUMNS,
    USAGE_BANDS,
    Estate,
    EstateFilter,
    WorkbookRow,
    _build_tree,
)
from astra_graph.scope import (
    TIERS,
    DecisionKind,
    InMemoryScopeStore,
    ScopeError,
    fold,
    new_decision,
)

from .conftest import ARTIZENT_HEADERS, CLIENT_HEADERS
from .fakes import InMemoryGraphRepository, StubEstateReader

#: A programme manager, named: the scope decisions record who made them, and a test
#: that asserted on the harvester agent would be asserting on the wrong thing.
PM = "user:j.okafor@artizent.example"
PM_HEADERS = {"X-Astra-Principal": PM, "X-Astra-Roles": "programme_manager"}
ENGINEER_HEADERS = {**ARTIZENT_HEADERS, "X-Astra-Roles": "migration_engineer"}


def row(**kwargs) -> WorkbookRow:
    defaults = {
        "id": "01ARZ3NDEKTSV4RRFFQ69G5FAV",
        "luid": "wb-1",
        "name": "Daily VaR",
        "project": "Risk Core",
        "project_id": "prj-1",
        "site": "RQA",
        "site_id": "site-1",
        "parse_quality": 1.0,
        "views_90d": 400,
        "distinct_viewers_90d": 31,
        "owner": "A Mehta",
        "owner_id": "user-1",
        "calculated_fields": 27,
        "held": False,
    }
    defaults.update(kwargs)
    return WorkbookRow(**defaults)


def estate(*rows: WorkbookRow) -> Estate:
    return Estate(rows=list(rows), read_ms=0.0)


# ------------------------------------------------------------------ the left pane


def test_the_tree_rolls_up_by_site_and_project() -> None:
    """S1.4.1: "the site → project → workbook tree with counts and parse status"."""
    tree = _build_tree(
        [
            row(id="a", project="Risk Core", views_90d=100),
            row(id="b", project="Risk Core", views_90d=50, parse_quality=0.5, held=True),
            row(id="c", project="Treasury", project_id="prj-2", views_90d=10),
        ]
    )

    assert [node.name for node in tree] == ["RQA"]
    site = tree[0]
    assert (site.workbooks, site.held, site.views_90d) == (3, 1, 160)
    assert [(c.name, c.workbooks, c.held) for c in site.children] == [
        ("Risk Core", 2, 1),
        ("Treasury", 1, 0),
    ]


def test_a_workbook_with_no_parse_is_counted_apart_from_a_held_one() -> None:
    """Never parsed and parsed badly are different problems with different fixes."""
    tree = _build_tree(
        [
            row(id="a", parse_quality=None),
            row(id="b", parse_quality=0.4, held=True),
        ]
    )

    assert (tree[0].unparsed, tree[0].held) == (1, 1)


def test_an_unplaced_workbook_still_appears() -> None:
    """A workbook whose project edge is missing is a defect worth seeing, not hiding."""
    tree = _build_tree([row(project=None, project_id=None, site=None, site_id=None)])

    assert tree[0].name == "Unplaced"
    assert tree[0].children[0].name == "Unplaced"


def test_a_withdrawn_workbook_is_not_counted_as_work() -> None:
    """It is out of scope, so the tree's counts are of what remains to do."""
    data = estate(row(id="a"), row(id="b", withdrawn=True))

    assert data.tree(EstateFilter())[0].workbooks == 1


def test_the_tree_counts_withdrawn_when_the_table_shows_them() -> None:
    """Otherwise the two panes report different totals for the same estate — 64 and 65,
    visible on screen, which is the disagreement §15.2 exists to prevent."""
    data = estate(row(id="a"), row(id="b", withdrawn=True))
    where = EstateFilter(include_withdrawn=True)

    assert data.tree(where)[0].workbooks == 2
    assert data.page(where)["total"] == 2


# ----------------------------------------------------------------- the centre pane


def test_the_page_reports_the_filtered_total_and_the_estate_total() -> None:
    """A user filtering to 3 of 1,067 needs both numbers to know what they did."""
    data = estate(*[row(id=f"w{i}", name=f"WB {i}") for i in range(10)])

    page = data.page(EstateFilter(search="WB 1"), limit=5)

    assert page["total"] == 1
    assert page["estate_total"] == 10
    assert len(page["workbooks"]) == 1


def test_paging_is_stable_across_requests() -> None:
    """Two pages of an unsorted set can otherwise repeat or skip a row."""
    data = estate(*[row(id=f"w{i}", name=f"WB {i:02d}") for i in range(10)])

    first = data.page(EstateFilter(), offset=0, limit=4)
    second = data.page(EstateFilter(), offset=4, limit=4)

    names = [w["name"] for w in first["workbooks"] + second["workbooks"]]
    assert names == sorted(names)
    assert len(set(names)) == 8


@pytest.mark.parametrize(
    ("quality", "band"),
    [(1.0, "clean"), (0.99, "good"), (0.98, "good"), (0.97, "held"), (0.5, "poor"),
     (None, "unknown")],
)
def test_parse_quality_bands_break_at_the_specification_threshold(quality, band) -> None:
    """§4.1.4 holds a workbook below 0.98, so that is where the band changes."""
    data = estate(row(parse_quality=quality))
    assert data.page(EstateFilter())["workbooks"][0]["parse_quality_band"] == band


@pytest.mark.parametrize(
    ("views", "band"),
    [(0, "unused"), (1, "low"), (49, "low"), (50, "medium"), (499, "medium"),
     (500, "high"), (None, "unknown")],
)
def test_usage_bands(views, band) -> None:
    data = estate(row(views_90d=views))
    assert data.page(EstateFilter())["workbooks"][0]["usage_band"] == band


def test_every_band_is_reachable() -> None:
    """A band nothing can fall into is a filter option that always returns nothing."""
    for bands, attribute, values in (
        (PARSE_QUALITY_BANDS, "parse_quality", [1.0, 0.99, 0.95, 0.5, None]),
        (USAGE_BANDS, "views_90d", [0, 10, 100, 1000, None]),
    ):
        data = estate(*[row(id=f"w{i}", **{attribute: v}) for i, v in enumerate(values)])
        seen = {w[f"{'parse_quality' if attribute == 'parse_quality' else 'usage'}_band"]
                for w in data.page(EstateFilter())["workbooks"]}
        assert seen == {band.key for band in bands}


def test_filters_compose() -> None:
    data = estate(
        row(id="a", owner="A Mehta", views_90d=1000),
        row(id="b", owner="A Mehta", views_90d=1),
        row(id="c", owner="S Iyer", views_90d=1000),
    )

    page = data.page(EstateFilter(owner="A Mehta", usage_band="high"))

    assert [w["id"] for w in page["workbooks"]] == ["a"]


def test_withdrawn_workbooks_are_hidden_unless_asked_for() -> None:
    data = estate(row(id="a"), row(id="b", withdrawn=True, withdrawn_reason="Retiring Q3"))

    assert [w["id"] for w in data.page(EstateFilter())["workbooks"]] == ["a"]
    both = data.page(EstateFilter(include_withdrawn=True))["workbooks"]
    assert {w["id"] for w in both} == {"a", "b"}


def test_search_covers_name_luid_and_project() -> None:
    data = estate(
        row(id="a", name="Daily VaR", luid="wb-aaa", project="Risk Core"),
        row(id="b", name="Liquidity", luid="wb-bbb", project="Treasury"),
    )

    for needle, expected in (("daily", "a"), ("wb-bbb", "b"), ("treasury", "b")):
        page = data.page(EstateFilter(search=needle))
        assert [w["id"] for w in page["workbooks"]] == [expected], needle


def test_an_unknown_sort_falls_back_rather_than_failing() -> None:
    """A stale bookmark should not be a 400."""
    data = estate(row(id="a"), row(id="b", name="Aardvark"))

    page = data.page(EstateFilter(), sort="by-vibes")

    assert [w["name"] for w in page["workbooks"]] == ["Aardvark", "Daily VaR"]


# ---------------------------------------------------------------------- the facets


def test_a_facet_count_is_what_you_would_get_if_you_picked_it() -> None:
    """Counted against everything *except* that facet, or the numbers would just echo
    the selection back."""
    data = estate(
        row(id="a", owner="A Mehta", views_90d=1000),
        row(id="b", owner="S Iyer", views_90d=1000),
        row(id="c", owner="S Iyer", views_90d=1),
    )

    facets = data.facets(EstateFilter(owner="A Mehta"))

    owners = {f["key"]: f["count"] for f in facets["owner"]}
    assert owners == {"A Mehta": 1, "S Iyer": 2}, "the owner facet ignores the owner filter"
    usage = {f["key"]: f["count"] for f in facets["usage_band"]}
    assert usage["high"] == 1, "the usage facet respects it"


def test_unassigned_owners_are_a_facet_option() -> None:
    """§15.3.2 flags unowned workbooks; a facet that omitted them would hide the problem."""
    data = estate(row(id="a", owner=None, owner_id=None), row(id="b"))

    owners = {f["key"]: f["count"] for f in data.facets(EstateFilter())["owner"]}

    assert owners["__none__"] == 1


def test_the_facets_that_cannot_exist_yet_say_so_rather_than_being_empty() -> None:
    """§15.3.2 asks for state, family and train. All three are Migration Unit properties,
    and the Cartographer creates the MU (E3). An empty dropdown is a worse answer than an
    explained absence."""
    facets = estate(row()).facets(EstateFilter())

    pending = {p["facet"] for p in facets["pending"]}
    assert pending == {"state", "family", "train"}
    assert all("E3" in p["reason"] for p in facets["pending"])


def test_every_pending_column_names_what_will_fill_it() -> None:
    assert PENDING_COLUMNS
    for name, reason in PENDING_COLUMNS.items():
        assert any(epic in reason for epic in ("E3", "E5")), name


# ------------------------------------------------------------- the scope decisions


def test_a_scope_decision_needs_a_real_reason() -> None:
    """§15.2: the reason is required, not optional. "n/a" is not a reason."""
    with pytest.raises(ScopeError, match="at least"):
        new_decision(
            workbook_id="w1",
            kind=DecisionKind.WITHDRAW,
            reason="n/a",
            decided_by="user:pm@artizent.example",
        )


def test_a_tier_must_be_one_of_the_declared_tiers() -> None:
    with pytest.raises(ScopeError, match="tier must be one of"):
        new_decision(
            workbook_id="w1",
            kind=DecisionKind.RE_TIER,
            reason="Joint review on 14 April agreed this is harder than assessed",
            decided_by="user:pm@artizent.example",
            to_value="VERY_HARD",
        )


def test_scope_state_is_folded_from_the_decisions() -> None:
    """A fold rather than a stored status: the decisions are the record, and a status
    column beside them could disagree with them."""
    decisions = [
        new_decision(
            workbook_id="w1",
            kind=DecisionKind.RE_TIER,
            reason="Joint review: three LOD expressions and a table calc",
            decided_by="pm",
            to_value="COMPLEX",
        ),
        new_decision(
            workbook_id="w1",
            kind=DecisionKind.WITHDRAW,
            reason="Report is being retired by the business in Q3",
            decided_by="pm",
        ),
    ]

    state = fold(decisions)

    assert state.tier == "COMPLEX"
    assert state.withdrawn is True
    assert "retired by the business" in state.withdrawn_reason


def test_reinstating_clears_the_withdrawal_but_not_the_tier() -> None:
    """Two separate judgements; neither implies the other."""
    decisions = [
        new_decision(workbook_id="w", kind=DecisionKind.RE_TIER, reason="Assessed as complex",
                     decided_by="pm", to_value="COMPLEX"),
        new_decision(workbook_id="w", kind=DecisionKind.WITHDRAW, reason="Out of scope for now",
                     decided_by="pm"),
        new_decision(workbook_id="w", kind=DecisionKind.REINSTATE,
                     reason="Business asked for it back in wave 2", decided_by="pm"),
    ]

    state = fold(decisions)

    assert (state.withdrawn, state.tier) == (False, "COMPLEX")


async def test_the_store_folds_every_workbook_in_one_read() -> None:
    store = InMemoryScopeStore()
    await store.decide(
        new_decision(workbook_id="a", kind=DecisionKind.WITHDRAW,
                     reason="Duplicate of the Treasury version", decided_by="pm")
    )
    await store.decide(
        new_decision(workbook_id="b", kind=DecisionKind.RE_TIER, reason="Joint review outcome",
                     decided_by="pm", to_value="SIMPLE")
    )

    states = await store.states()

    assert states["a"].withdrawn is True
    assert states["b"].tier == "SIMPLE"


# ------------------------------------------------------------------ the HTTP surface


@pytest.fixture
def explorer(client, repository):
    app = client._transport.app
    app.state.estate_reader = StubEstateReader(
        [
            row(id="01ARZ3NDEKTSV4RRFFQ69G5FA1", name="Daily VaR", views_90d=400),
            row(id="01ARZ3NDEKTSV4RRFFQ69G5FA2", name="Liquidity", project="Treasury",
                project_id="prj-2", views_90d=2, owner=None, owner_id=None,
                parse_quality=0.5, held=True),
        ]
    )
    return client, app


async def test_the_explorer_returns_three_panes_in_one_request(explorer) -> None:
    """S1.4.1's first criterion, and the reason it is one endpoint."""
    client, _app = explorer

    response = await client.get("/v1/estate", headers=ARTIZENT_HEADERS)

    assert response.status_code == 200
    body = response.json()
    assert [node["name"] for node in body["tree"]] == ["RQA"]
    assert body["total"] == 2
    assert {f["key"] for f in body["facets"]["parse_quality_band"]} == {
        band.key for band in PARSE_QUALITY_BANDS
    }
    assert body["timing"]["total_ms"] >= 0


async def test_the_explorer_filters(explorer) -> None:
    client, _app = explorer

    response = await client.get(
        "/v1/estate?held_only=true&unowned_only=true", headers=ARTIZENT_HEADERS
    )

    assert [w["name"] for w in response.json()["workbooks"]] == ["Liquidity"]


async def test_a_bad_page_size_is_refused(explorer) -> None:
    client, _app = explorer
    assert (await client.get("/v1/estate?limit=0", headers=ARTIZENT_HEADERS)).status_code == 400
    assert (
        await client.get("/v1/estate?offset=-1", headers=ARTIZENT_HEADERS)
    ).status_code == 400


async def test_the_right_pane_carries_the_lineage_and_the_absent_mu(client, seeded) -> None:
    """§15.3.2's right pane, and the "open MU" action that has nothing to open yet."""
    client._transport.app.state.estate_reader = StubEstateReader([])

    response = await client.get(
        f"/v1/estate/workbooks/{seeded['workbook']}", headers=ARTIZENT_HEADERS
    )

    assert response.status_code == 200
    body = response.json()
    assert body["workbook"]["properties"]["name"] == "Daily VaR"
    assert {n["type"] for n in body["lineage"]["nodes"]} >= {"Worksheet", "Datasource"}
    assert body["lineage"]["edges"]
    assert body["migration_unit"] is None
    assert "E3" in body["migration_unit_reason"]


async def test_the_right_pane_404s_for_something_that_is_not_a_workbook(client, seeded) -> None:
    client._transport.app.state.estate_reader = StubEstateReader([])

    response = await client.get(
        f"/v1/estate/workbooks/{seeded['calc']}", headers=ARTIZENT_HEADERS
    )

    assert response.status_code == 404


async def test_only_a_programme_manager_can_change_scope(client, seeded) -> None:
    """S1.4.1 gates these on the role, so the API gates them — a console that only hides
    the button is not a permission model."""
    client._transport.app.state.estate_reader = StubEstateReader([])
    body = {"reason": "Report is being retired by the business in Q3"}

    for headers, expected in (
        (ENGINEER_HEADERS, 403),
        (CLIENT_HEADERS, 403),
        (PM_HEADERS, 201),
    ):
        response = await client.post(
            f"/v1/estate/workbooks/{seeded['workbook']}:withdraw", json=body, headers=headers
        )
        assert response.status_code == expected, headers["X-Astra-Roles"]


async def test_withdrawing_records_the_reason_and_shows_on_the_workbook(client, seeded) -> None:
    client._transport.app.state.estate_reader = StubEstateReader([])

    await client.post(
        f"/v1/estate/workbooks/{seeded['workbook']}:withdraw",
        json={"reason": "Superseded by the Treasury liquidity pack"},
        headers=PM_HEADERS,
    )

    detail = (
        await client.get(
            f"/v1/estate/workbooks/{seeded['workbook']}", headers=ARTIZENT_HEADERS
        )
    ).json()

    assert detail["scope"]["current"]["withdrawn"] is True
    assert "Treasury liquidity pack" in detail["scope"]["current"]["withdrawn_reason"]
    assert detail["scope"]["decisions"][0]["decided_by"] == PM


async def test_a_reason_that_is_not_a_reason_is_refused_over_http(client, seeded) -> None:
    client._transport.app.state.estate_reader = StubEstateReader([])

    response = await client.post(
        f"/v1/estate/workbooks/{seeded['workbook']}:withdraw",
        json={"reason": "no"},
        headers=PM_HEADERS,
    )

    assert response.status_code == 422


async def test_re_tiering_records_what_it_changed_from(client, seeded) -> None:
    """The first decision declares rather than re-tiers, and the record says which."""
    client._transport.app.state.estate_reader = StubEstateReader([])
    path = f"/v1/estate/workbooks/{seeded['workbook']}:re-tier"

    first = await client.post(
        path,
        json={"tier": "MODERATE", "reason": "Two LOD expressions, no table calcs"},
        headers=PM_HEADERS,
    )
    second = await client.post(
        path,
        json={"tier": "COMPLEX", "reason": "Joint review on 14 April found nested LODs"},
        headers=PM_HEADERS,
    )

    assert first.json()["from"] is None, "nothing assessed it, so there was nothing to change"
    assert second.json()["from"] == "MODERATE"
    assert second.json()["to"] == "COMPLEX"


async def test_a_withdrawn_workbook_can_be_reinstated(client, seeded) -> None:
    client._transport.app.state.estate_reader = StubEstateReader([])
    workbook = seeded["workbook"]

    await client.post(
        f"/v1/estate/workbooks/{workbook}:withdraw",
        json={"reason": "Thought to be a duplicate of the Treasury pack"},
        headers=PM_HEADERS,
    )
    await client.post(
        f"/v1/estate/workbooks/{workbook}:reinstate",
        json={"reason": "Not a duplicate; the desks report different books"},
        headers=PM_HEADERS,
    )

    detail = (
        await client.get(f"/v1/estate/workbooks/{workbook}", headers=ARTIZENT_HEADERS)
    ).json()

    assert detail["scope"]["current"]["withdrawn"] is False
    assert len(detail["scope"]["decisions"]) == 2, "both decisions are kept"


async def test_the_tiers_are_published_so_the_console_does_not_hard_code_them(
    explorer,
) -> None:
    client, _app = explorer

    body = (await client.get("/v1/estate", headers=ARTIZENT_HEADERS)).json()

    assert body["tiers"] == list(TIERS)


@pytest.fixture
def repository() -> InMemoryGraphRepository:
    return InMemoryGraphRepository()
