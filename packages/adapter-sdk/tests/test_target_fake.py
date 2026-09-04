"""The fixture target adapter — story S4.3.1.

    "TMDL is committed to the client's Git repository through the target adapter... /
    Deployment to the dev workspace uses Fabric Git integration; a smoke query per table
    (row count, one measure) runs and the result is stored."

``commit`` is exercised against a real, local Git repository — the one part of this
adapter that is genuinely real rather than a disclosed stand-in (see the module's own
docstring). ``deploy``/``smoke_query`` are checked for what they actually do: materialize
the committed tree, and report whether a table's own file landed there.
"""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest
from astra_adapter import TargetAdapterError, TmdlBundle
from astra_adapter.target_fake import FixtureTargetAdapter
from dulwich.repo import Repo


def _adapter(tmp_path: Path) -> FixtureTargetAdapter:
    return FixtureTargetAdapter(repo_path=tmp_path / "repo", workspace_root=tmp_path / "workspaces")


def _bundle(**files: str) -> TmdlBundle:
    return TmdlBundle(files={path: content.encode("utf-8") for path, content in files.items()})


# ------------------------------------------------------------------------------- manifest


def test_manifest_names_the_fixture_kind(tmp_path: Path) -> None:
    manifest = _adapter(tmp_path).manifest()
    assert manifest.name == "fixture"


# --------------------------------------------------------------------------------- commit


async def test_commit_creates_a_real_git_repository(tmp_path: Path) -> None:
    adapter = _adapter(tmp_path)
    bundle = _bundle(**{"model.tmdl": "model 'x'\n"})

    result = await adapter.commit(bundle, item_path="Risk.SemanticModel", message="Build Risk (fam_1)")

    assert result.commit_sha
    assert (tmp_path / "repo" / ".git").is_dir()
    assert (tmp_path / "repo" / "Risk.SemanticModel" / "model.tmdl").read_text() == "model 'x'\n"


async def test_the_commit_message_is_recorded_in_git_log(tmp_path: Path) -> None:
    adapter = _adapter(tmp_path)
    bundle = _bundle(**{"model.tmdl": "model 'x'\n"})

    await adapter.commit(bundle, item_path="Risk.SemanticModel", message="Build Risk (fam_1) — G2 decision gd_1")

    repo = Repo(str(tmp_path / "repo"))
    try:
        commit = repo[repo.head()]
        message = commit.message.decode("utf-8")
    finally:
        repo.close()
    assert "Build Risk (fam_1) — G2 decision gd_1" in message


async def test_committing_the_same_bundle_twice_makes_no_second_commit(tmp_path: Path) -> None:
    adapter = _adapter(tmp_path)
    bundle = _bundle(**{"model.tmdl": "model 'x'\n"})

    first = await adapter.commit(bundle, item_path="Risk.SemanticModel", message="Build 1")
    second = await adapter.commit(bundle, item_path="Risk.SemanticModel", message="Build 2 (identical)")

    assert first.commit_sha == second.commit_sha


async def test_a_changed_bundle_makes_a_new_commit(tmp_path: Path) -> None:
    adapter = _adapter(tmp_path)
    first = await adapter.commit(
        _bundle(**{"model.tmdl": "model 'x'\n"}), item_path="Risk.SemanticModel", message="Build 1",
    )
    second = await adapter.commit(
        _bundle(**{"model.tmdl": "model 'y'\n"}), item_path="Risk.SemanticModel", message="Build 2",
    )
    assert first.commit_sha != second.commit_sha


async def test_two_families_commit_into_separate_folders_without_colliding(tmp_path: Path) -> None:
    adapter = _adapter(tmp_path)
    await adapter.commit(_bundle(**{"model.tmdl": "model 'a'\n"}), item_path="A.SemanticModel", message="A")
    await adapter.commit(_bundle(**{"model.tmdl": "model 'b'\n"}), item_path="B.SemanticModel", message="B")

    assert (tmp_path / "repo" / "A.SemanticModel" / "model.tmdl").read_text() == "model 'a'\n"
    assert (tmp_path / "repo" / "B.SemanticModel" / "model.tmdl").read_text() == "model 'b'\n"


async def test_a_stale_file_from_a_previous_build_is_removed(tmp_path: Path) -> None:
    adapter = _adapter(tmp_path)
    await adapter.commit(
        _bundle(**{"model.tmdl": "model 'x'\n", "tables/old.tmdl": "table 'old'\n"}),
        item_path="Risk.SemanticModel", message="Build 1",
    )
    await adapter.commit(
        _bundle(**{"model.tmdl": "model 'x'\n"}), item_path="Risk.SemanticModel", message="Build 2",
    )
    assert not (tmp_path / "repo" / "Risk.SemanticModel" / "tables" / "old.tmdl").exists()


# --------------------------------------------------------------------------------- deploy


async def test_deploy_materializes_the_committed_tree_into_the_workspace(tmp_path: Path) -> None:
    adapter = _adapter(tmp_path)
    commit = await adapter.commit(
        _bundle(**{"model.tmdl": "model 'x'\n", "tables/positions.tmdl": "table 'positions'\n"}),
        item_path="Risk.SemanticModel", message="Build 1",
    )

    result = await adapter.deploy(workspace="dev", git_ref=commit.ref)

    assert result.ok
    assert result.workspace == "dev"
    deployed = tmp_path / "workspaces" / "dev" / "Risk.SemanticModel" / "tables" / "positions.tmdl"
    assert deployed.read_text() == "table 'positions'\n"


async def test_deploying_with_nothing_committed_yet_fails_honestly(tmp_path: Path) -> None:
    adapter = _adapter(tmp_path)
    result = await adapter.deploy(workspace="dev", git_ref="refs/heads/main")
    assert result.ok is False


# ---------------------------------------------------------------------------- smoke query


async def test_smoke_query_passes_for_a_deployed_table(tmp_path: Path) -> None:
    adapter = _adapter(tmp_path)
    commit = await adapter.commit(
        _bundle(**{"model.tmdl": "model 'x'\n", "tables/positions.tmdl": "table 'positions'\n"}),
        item_path="Risk.SemanticModel", message="Build 1",
    )
    await adapter.deploy(workspace="dev", git_ref=commit.ref)

    result = await adapter.smoke_query(workspace="dev", table="positions", measure_name="Margin %")

    assert result.ok is True
    assert result.table == "positions"
    assert result.measure_name == "Margin %"
    # A real, disclosed gap — no live analysis-services engine exists to evaluate either.
    assert result.row_count is None
    assert result.measure_value is None


async def test_smoke_query_fails_for_a_table_never_deployed(tmp_path: Path) -> None:
    adapter = _adapter(tmp_path)
    commit = await adapter.commit(
        _bundle(**{"model.tmdl": "model 'x'\n", "tables/positions.tmdl": "table 'positions'\n"}),
        item_path="Risk.SemanticModel", message="Build 1",
    )
    await adapter.deploy(workspace="dev", git_ref=commit.ref)

    result = await adapter.smoke_query(workspace="dev", table="not_deployed", measure_name=None)

    assert result.ok is False
    assert "not_deployed" in result.detail


async def test_an_underlying_git_failure_is_wrapped_as_a_target_adapter_error(tmp_path: Path) -> None:
    adapter = _adapter(tmp_path)
    shutil.rmtree(tmp_path / "repo" / ".git")  # break the repository out from under the adapter

    with pytest.raises(TargetAdapterError):
        await adapter.commit(_bundle(**{"model.tmdl": "x"}), item_path="X.SemanticModel", message="m")
