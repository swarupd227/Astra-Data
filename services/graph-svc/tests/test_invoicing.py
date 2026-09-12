"""mu.accepted invoicing and the commercial ledger -- story S9.1.2, closing F9.1/E9.
Pure pieces only (`DEFAULT_UNIT_PRICES`, `PLANNED_BY_TIER`, `LedgerEntry.as_dict()`);
`record_acceptance`/`accepted_by_tier`/`programme_acceptance_summary` and the two real
stores are graph-coupled throughout and are covered end to end in
`test_integration_invoicing.py` instead, the same "pure core, graph-coupled shell" split
this epic's own prior stories already established.
"""

from __future__ import annotations

from astra_graph.events import EventType, mu_accepted
from astra_graph.invoicing import DEFAULT_UNIT_PRICES, PLANNED_BY_TIER, LedgerEntry
from astra_graph.principal import Principal
from astra_graph.scope import TIERS


def test_every_real_tier_has_a_real_default_price() -> None:
    assert set(DEFAULT_UNIT_PRICES) == set(TIERS)
    assert all(price > 0 for price in DEFAULT_UNIT_PRICES.values())


def test_default_prices_increase_with_complexity() -> None:
    assert (
        DEFAULT_UNIT_PRICES["SIMPLE"]
        < DEFAULT_UNIT_PRICES["MODERATE"]
        < DEFAULT_UNIT_PRICES["COMPLEX"]
        < DEFAULT_UNIT_PRICES["REDESIGN"]
    )


def test_every_real_tier_has_a_planned_count() -> None:
    assert set(PLANNED_BY_TIER) == set(TIERS)


def test_planned_by_tier_sums_to_the_same_planned_family_count() -> None:
    """A second, disclosed planning figure that silently disagreed with `retention.
    PLANNED_FAMILY_COUNT` would be worse than not having one at all."""
    from astra_graph.retention import PLANNED_FAMILY_COUNT

    assert sum(PLANNED_BY_TIER.values()) == PLANNED_FAMILY_COUNT


def test_ledger_entry_as_dict_round_trips() -> None:
    entry = LedgerEntry(
        id="led_1", workbook_id="wb_1", tier="COMPLEX", unit_price=28_000.0,
        gate_decision_id="gd_1", recorded_by="user:owner@client.example",
        recorded_at="2027-06-01T09:00:00.000Z",
    )
    assert entry.as_dict() == {
        "id": "led_1", "workbook_id": "wb_1", "tier": "COMPLEX", "unit_price": 28_000.0,
        "gate_decision_id": "gd_1", "recorded_by": "user:owner@client.example",
        "recorded_at": "2027-06-01T09:00:00.000Z",
    }


# -------------------------------------------------------------------- the event itself


def test_mu_accepted_is_a_real_notice_not_a_mutation() -> None:
    assert EventType.MU_ACCEPTED.mutates_graph is False


def test_mu_accepted_carries_the_ac_own_three_facts() -> None:
    event = mu_accepted(
        source="/astra/graph-svc/astra_estate", workbook_id="wb_1", tier="COMPLEX",
        unit_price=28_000.0, gate_decision_id="gd_1", principal=Principal("user:owner@client.example"),
    )
    assert event.type is EventType.MU_ACCEPTED
    assert event.subject == "wb_1"
    assert event.data == {
        "workbook_id": "wb_1", "tier": "COMPLEX", "unit_price": 28_000.0, "gate_decision_id": "gd_1",
    }
