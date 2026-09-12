"""Promotion through the Fabric deployment pipeline -- story S9.2.1, opening F9.2.

Pure pieces only (`_current_stage`, `_window_end`, the result dataclasses' own
`as_dict()`); `promotion_blockers`/`promote_workbook`/`release_board` are
graph-coupled throughout and are covered end to end in `test_integration_release.py`
instead, the same "pure core, graph-coupled shell" split this epic's own prior stories
already established.
"""

from __future__ import annotations

from astra_graph.release import (
    PIPELINE_STAGES,
    PromotionRecord,
    PromotionStep,
    _current_stage,
    _window_end,
)


def test_pipeline_stages_are_the_two_the_ac_names() -> None:
    assert PIPELINE_STAGES == ("test", "prod")


# ------------------------------------------------------------------------- _current_stage


def test_current_stage_is_prod_once_a_succeeded_prod_promotion_exists() -> None:
    prod = PromotionStep("model deploy", True, "ok")
    record = PromotionRecord(
        id="promotion_1", workbook_id="wb_1", to_stage="prod", workspace="prod", state="SUCCEEDED",
        steps=(prod,), model_git_ref="abc123", report_deploy_id="deploy_1", approved_by="user:pm@artizent.example",
        approver_role="programme_manager", rationale="Ready for release.", triggered_by="user:pm@artizent.example",
        started_at="2027-01-01T00:00:00.000Z", finished_at="2027-01-01T00:00:01.000Z",
    )
    assert _current_stage({"prod": record}, g3_approved=True) == "PROD"


def test_current_stage_is_test_once_a_succeeded_test_promotion_exists_and_prod_does_not() -> None:
    record = PromotionRecord(
        id="promotion_2", workbook_id="wb_1", to_stage="test", workspace="test", state="SUCCEEDED",
        steps=(), model_git_ref="abc123", report_deploy_id="deploy_1", approved_by="user:eng@artizent.example",
        approver_role="platform_engineer", rationale=None, triggered_by="user:eng@artizent.example",
        started_at="2027-01-01T00:00:00.000Z", finished_at="2027-01-01T00:00:01.000Z",
    )
    assert _current_stage({"test": record}, g3_approved=True) == "TEST"


def test_current_stage_is_test_only_if_the_attempt_succeeded() -> None:
    """A FAILED test promotion never advances the stage -- the workbook is still ACCEPTED."""
    failed = PromotionRecord(
        id="promotion_3", workbook_id="wb_1", to_stage="test", workspace="test", state="FAILED",
        steps=(), model_git_ref=None, report_deploy_id=None, approved_by="user:eng@artizent.example",
        approver_role="platform_engineer", rationale=None, triggered_by="user:eng@artizent.example",
        started_at="2027-01-01T00:00:00.000Z", finished_at="2027-01-01T00:00:01.000Z",
    )
    assert _current_stage({"test": failed}, g3_approved=True) == "ACCEPTED"


def test_current_stage_is_accepted_once_g3_is_approved_with_no_promotion_yet() -> None:
    assert _current_stage({}, g3_approved=True) == "ACCEPTED"


def test_current_stage_is_not_accepted_honestly_with_no_g3_approval_and_no_promotion() -> None:
    assert _current_stage({}, g3_approved=False) == "NOT_ACCEPTED"


# --------------------------------------------------------------------------- _window_end


def test_window_end_is_honestly_none_with_no_real_window_start() -> None:
    assert _window_end(None) is None


def test_window_end_is_four_weeks_after_the_real_window_start() -> None:
    assert _window_end("2027-01-01T00:00:00.000Z") == "2027-01-29T00:00:00.000Z"


# ------------------------------------------------------------------- result dataclasses


def test_promotion_step_as_dict_round_trips() -> None:
    step = PromotionStep("model deploy", True, "deploy_1")
    assert step.as_dict() == {"name": "model deploy", "ok": True, "detail": "deploy_1"}


def test_promotion_record_as_dict_round_trips() -> None:
    step = PromotionStep("report deploy", True, "ok")
    record = PromotionRecord(
        id="promotion_1", workbook_id="wb_1", to_stage="prod", workspace="prod", state="SUCCEEDED",
        steps=(step,), model_git_ref="abc123", report_deploy_id="deploy_1", approved_by="user:pm@artizent.example",
        approver_role="programme_manager", rationale="Ready for release.", triggered_by="user:pm@artizent.example",
        started_at="2027-01-01T00:00:00.000Z", finished_at="2027-01-01T00:00:01.000Z",
    )
    assert record.as_dict() == {
        "id": "promotion_1", "workbook_id": "wb_1", "to_stage": "prod", "workspace": "prod",
        "state": "SUCCEEDED", "steps": [{"name": "report deploy", "ok": True, "detail": "ok"}],
        "model_git_ref": "abc123", "report_deploy_id": "deploy_1",
        "approved_by": "user:pm@artizent.example", "approver_role": "programme_manager",
        "rationale": "Ready for release.", "triggered_by": "user:pm@artizent.example",
        "started_at": "2027-01-01T00:00:00.000Z", "finished_at": "2027-01-01T00:00:01.000Z",
    }
