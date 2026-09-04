"""The family count becomes a measured value — story S3.1.3.

    "As a programme manager, I want the family count to be recorded as a calibration
    input at the end of Month 1, so that the planning assumption is replaced by a
    measured value with a date."

Pure store-level tests, no database — see ``test_integration_programme.py`` for the live
count read from a real graph and the HTTP surface.
"""

from __future__ import annotations

from astra_graph.retention import PLANNED_FAMILY_COUNT, InMemoryProgrammeStore, Programme


async def test_planned_family_count_is_the_figure_the_spec_names() -> None:
    """§14.3 / Appendix A: "~150 shared governed models (planning assumption, measured in
    Month 1)"."""
    assert PLANNED_FAMILY_COUNT == 150


async def test_confirming_writes_count_date_and_confirming_user() -> None:
    store = InMemoryProgrammeStore()
    programme = await store.open_programme(
        name="RQA", started_at="2027-01-01T00:00:00Z", created_by="user:pm@artizent.example"
    )
    assert programme.family_count is None

    confirmed = await store.confirm_family_count(
        programme.id, count=142, confirmed_by="user:pm@artizent.example"
    )

    assert confirmed is not None
    assert confirmed.family_count == 142
    assert confirmed.family_count_confirmed_by == "user:pm@artizent.example"
    assert confirmed.family_count_confirmed_at is not None


async def test_the_delta_compares_measured_against_the_planning_assumption() -> None:
    programme = Programme(
        id="a", name="RQA", started_at="2027-01-01T00:00:00Z", family_count=142
    )
    body = programme.as_dict()
    assert body["planned_family_count"] == 150
    assert body["family_count_delta"] == -8


async def test_the_delta_is_absent_until_a_count_is_confirmed() -> None:
    programme = Programme(id="a", name="RQA", started_at="2027-01-01T00:00:00Z")
    body = programme.as_dict()
    assert body["family_count"] is None
    assert body["family_count_delta"] is None
    assert body["planned_family_count"] == 150


async def test_confirming_an_unknown_programme_reports_nothing_to_update() -> None:
    store = InMemoryProgrammeStore()
    assert await store.confirm_family_count("prg_missing", count=10, confirmed_by="x") is None


async def test_a_later_confirmation_supersedes_the_earlier_one() -> None:
    """Overwrites, the same as ``record_clustering`` — the record answers "what is the
    confirmed count now", not "every time someone pressed the button"."""
    store = InMemoryProgrammeStore()
    programme = await store.open_programme(
        name="RQA", started_at="2027-01-01T00:00:00Z", created_by="user:pm@artizent.example"
    )
    await store.confirm_family_count(programme.id, count=140, confirmed_by="user:a@example.com")
    reconfirmed = await store.confirm_family_count(
        programme.id, count=142, confirmed_by="user:b@example.com"
    )

    assert reconfirmed is not None
    assert reconfirmed.family_count == 142
    assert reconfirmed.family_count_confirmed_by == "user:b@example.com"
