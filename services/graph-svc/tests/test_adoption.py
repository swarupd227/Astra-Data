"""Adoption tracking during parallel run -- story S9.2.2, continuing F9.2.

Pure pieces only (`_adoption_ratio`, `is_capture_due`'s own date math via a minimal
fake store, the config/snapshot dataclasses' own `as_dict()`, and the in-memory config
store's own threshold validation); `capture_adoption_sweep`/`decommission_tracker` are
graph-and-adapter-coupled throughout and are covered end to end in
`test_integration_adoption.py` instead, the same "pure core, graph-coupled shell" split
this epic's own prior stories already established.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from astra_graph.adoption import (
    CAPTURE_WINDOW_DAYS,
    DEFAULT_ADOPTION_THRESHOLD,
    AdoptionConfig,
    AdoptionError,
    AdoptionSnapshot,
    InMemoryAdoptionConfigStore,
    _adoption_ratio,
    is_capture_due,
)


def test_capture_window_is_the_acs_own_literal_weekly_cadence() -> None:
    assert CAPTURE_WINDOW_DAYS == 7


def test_default_threshold_is_a_real_fraction() -> None:
    assert 0.0 < DEFAULT_ADOPTION_THRESHOLD <= 1.0


# ------------------------------------------------------------------------ _adoption_ratio


def test_adoption_ratio_computes_a_real_fraction() -> None:
    ratio, meets = _adoption_ratio(source_views=100, target_views=90, threshold=0.8)
    assert ratio == pytest.approx(0.9)
    assert meets is True


def test_adoption_ratio_is_honestly_none_with_no_source_views() -> None:
    assert _adoption_ratio(source_views=None, target_views=50, threshold=0.8) == (None, None)


def test_adoption_ratio_is_honestly_none_with_zero_source_views() -> None:
    assert _adoption_ratio(source_views=0, target_views=50, threshold=0.8) == (None, None)


def test_adoption_ratio_fails_the_threshold_honestly() -> None:
    ratio, meets = _adoption_ratio(source_views=100, target_views=10, threshold=0.8)
    assert ratio == pytest.approx(0.1)
    assert meets is False


def test_adoption_ratio_at_exactly_the_threshold_meets_it() -> None:
    _ratio, meets = _adoption_ratio(source_views=100, target_views=80, threshold=0.8)
    assert meets is True


# --------------------------------------------------------------------------- is_capture_due


class _FakeStore:
    def __init__(self, last: str | None) -> None:
        self._last = last

    async def last_captured_at(self) -> str | None:
        return self._last


async def test_capture_is_due_when_nothing_has_ever_been_captured() -> None:
    assert await is_capture_due(_FakeStore(None)) is True


async def test_capture_is_not_due_within_the_window() -> None:
    now = datetime.now(UTC)
    last = (now - timedelta(days=1)).isoformat(timespec="milliseconds").replace("+00:00", "Z")
    assert await is_capture_due(_FakeStore(last), now=now) is False


async def test_capture_is_due_once_a_full_week_has_elapsed() -> None:
    now = datetime.now(UTC)
    last = (now - timedelta(days=8)).isoformat(timespec="milliseconds").replace("+00:00", "Z")
    assert await is_capture_due(_FakeStore(last), now=now) is True


# ------------------------------------------------------------------------ result dataclasses


def test_adoption_config_as_dict_round_trips() -> None:
    assert AdoptionConfig(threshold=0.75).as_dict() == {"threshold": 0.75}


def test_adoption_snapshot_as_dict_round_trips() -> None:
    snapshot = AdoptionSnapshot(
        id="adoption_1", workbook_id="wb_1", captured_at="2027-06-01T09:00:00.000Z",
        source_views=100, target_views=90, ratio=0.9, threshold=0.8, meets_threshold=True,
        triggered_by="user:pm@artizent.example",
    )
    assert snapshot.as_dict() == {
        "id": "adoption_1", "workbook_id": "wb_1", "captured_at": "2027-06-01T09:00:00.000Z",
        "source_views": 100, "target_views": 90, "ratio": 0.9, "threshold": 0.8,
        "meets_threshold": True, "triggered_by": "user:pm@artizent.example",
    }


def test_adoption_snapshot_is_honest_about_an_absent_source() -> None:
    snapshot = AdoptionSnapshot(
        id="adoption_2", workbook_id="wb_1", captured_at="2027-06-01T09:00:00.000Z",
        source_views=None, target_views=42, ratio=None, threshold=0.8, meets_threshold=None,
        triggered_by="user:pm@artizent.example",
    )
    assert snapshot.as_dict()["source_views"] is None
    assert snapshot.as_dict()["meets_threshold"] is None


# ------------------------------------------------------------------------ config store


async def test_in_memory_config_store_defaults_honestly() -> None:
    store = InMemoryAdoptionConfigStore()
    config = await store.latest()
    assert config.threshold == DEFAULT_ADOPTION_THRESHOLD


async def test_in_memory_config_store_saves_a_real_threshold() -> None:
    store = InMemoryAdoptionConfigStore()
    saved = await store.save(AdoptionConfig(threshold=0.6), updated_by="user:architect@artizent.example")
    assert saved.threshold == 0.6
    assert (await store.latest()).threshold == 0.6


async def test_in_memory_config_store_refuses_an_out_of_range_threshold() -> None:
    store = InMemoryAdoptionConfigStore()
    with pytest.raises(AdoptionError):
        await store.save(AdoptionConfig(threshold=1.5), updated_by="user:architect@artizent.example")
