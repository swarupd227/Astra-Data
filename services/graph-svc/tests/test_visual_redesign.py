"""Redesign flags as work items -- story S6.2.1, spec §11.1/§11.3.

    "Redesign flags create ExceptionCases of class VISUAL_REDESIGN routed to the Exception
    Desk with the source screenshot, the mapping reason and the placeholder location."

`find_screenshot_ref` and `ProvingReadiness` are testable without a database -- the rest of
`visual_redesign.py` is graph-coupled orchestration in the same shape `build.py`/
`report_deploy.py` already established, and is covered by the integration suite instead.
"""

from __future__ import annotations

from astra_graph.artefacts import InMemoryArtefactStore
from astra_graph.visual_redesign import ProvingReadiness, find_screenshot_ref


async def test_find_screenshot_ref_is_none_with_no_store_configured() -> None:
    assert await find_screenshot_ref(None, workbook_id="wb1", worksheet_name="Bar sheet") is None


async def test_find_screenshot_ref_matches_by_case_id_naming_the_worksheet() -> None:
    store = InMemoryArtefactStore()
    record = await store.store(
        kind="visual_capture", mu_ref="wb1", case_id="Bar sheet",
        content=b"png-bytes", media_type="image/png", created_by="user:test@artizent.example",
    )
    found = await find_screenshot_ref(store, workbook_id="wb1", worksheet_name="Bar sheet")
    assert found == record.id


async def test_find_screenshot_ref_is_none_when_no_matching_case_id() -> None:
    store = InMemoryArtefactStore()
    await store.store(
        kind="visual_capture", mu_ref="wb1", case_id="Other sheet",
        content=b"png-bytes", media_type="image/png", created_by="user:test@artizent.example",
    )
    assert await find_screenshot_ref(store, workbook_id="wb1", worksheet_name="Bar sheet") is None


async def test_find_screenshot_ref_ignores_a_different_workbooks_own_artefact() -> None:
    store = InMemoryArtefactStore()
    await store.store(
        kind="visual_capture", mu_ref="wb2", case_id="Bar sheet",
        content=b"png-bytes", media_type="image/png", created_by="user:test@artizent.example",
    )
    assert await find_screenshot_ref(store, workbook_id="wb1", worksheet_name="Bar sheet") is None


async def test_find_screenshot_ref_ignores_a_different_kind() -> None:
    store = InMemoryArtefactStore()
    await store.store(
        kind="evidence_bundle", mu_ref="wb1", case_id="Bar sheet",
        content=b"bytes", media_type="application/json", created_by="user:test@artizent.example",
    )
    assert await find_screenshot_ref(store, workbook_id="wb1", worksheet_name="Bar sheet") is None


def test_proving_readiness_fully_blocked_when_nothing_is_ready() -> None:
    readiness = ProvingReadiness(workbook_id="wb1", ready_worksheet_ids=(), blocked_worksheet_ids=("w1",))
    assert readiness.fully_blocked is True


def test_proving_readiness_not_fully_blocked_when_some_sheets_are_ready() -> None:
    readiness = ProvingReadiness(workbook_id="wb1", ready_worksheet_ids=("w2",), blocked_worksheet_ids=("w1",))
    assert readiness.fully_blocked is False


def test_proving_readiness_not_blocked_at_all_reports_not_fully_blocked() -> None:
    readiness = ProvingReadiness(workbook_id="wb1", ready_worksheet_ids=("w1", "w2"), blocked_worksheet_ids=())
    assert readiness.fully_blocked is False


def test_proving_readiness_as_dict() -> None:
    readiness = ProvingReadiness(workbook_id="wb1", ready_worksheet_ids=("w2",), blocked_worksheet_ids=("w1",))
    assert readiness.as_dict() == {
        "workbook_id": "wb1",
        "ready_worksheet_ids": ["w2"],
        "blocked_worksheet_ids": ["w1"],
        "fully_blocked": False,
    }
