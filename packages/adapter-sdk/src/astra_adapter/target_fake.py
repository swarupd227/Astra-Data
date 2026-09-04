"""A fixture target adapter — real Git, no live Fabric tenant. Story S4.3.1.

Mirrors ``fake.py``'s own footing for the source side: not a real Fabric-connected
adapter, but a complete ``TargetAdapter`` implementation the platform can build and test
against before any real Azure AD app registration, Fabric workspace or client Git remote
exists — the same "the local stack has something to harvest" reasoning ``fake.py`` gives
for the source side.

**``commit`` is genuinely real.** It writes real Git objects to a real local repository —
via `Dulwich <https://www.dulwich.io/>`_, a pure-Python Git implementation, rather than
shelling out to the ``git`` binary. A platform image should not need an OS package it can
get from a pip dependency instead (§5.4: "the image carries no shell tooling it does not
need"), and a pure-Python implementation behaves identically in every environment this
platform builds in, with no system Git install to verify. "TMDL is committed to the
client's Git repository through the target adapter" is true today, not simulated — a
production deployment points the identical code at the client's real remote (a URL and a
credential) instead of a local path; nothing about ``commit`` itself changes.

**``deploy`` and ``smoke_query`` are disclosed stand-ins**, the same "real until a later
story" posture ``EnvironmentCredentialProvider``/``NullDirectoryResolver`` already carry
elsewhere in this platform (E11's own territory: a real Fabric REST client needs a live
tenant, a workspace id and a service-principal credential this environment does not have,
and writing an unverifiable integration against an API this codebase can never exercise
would be a worse kind of gap than naming the one that is real). ``deploy`` materializes the
committed tree into a local directory — the same end state Fabric Git integration
produces, a workspace synced to a branch — and ``smoke_query`` checks the file actually
landed there rather than running a live DAX query nothing here can evaluate.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
from pathlib import Path

from dulwich import porcelain
from dulwich.object_store import iter_tree_contents
from dulwich.repo import Repo

from .target_contract import (
    TARGET_INTERFACE_VERSION,
    CommitResult,
    DeploymentResult,
    SmokeQueryResult,
    TargetAdapterError,
    TargetManifest,
    TmdlBundle,
)

logger = logging.getLogger(__name__)

#: Dulwich's own default branch on ``init`` — this fixture never renames it, so it is a
#: fixed value rather than something queried per call.
_DEFAULT_REF = "refs/heads/master"

_AUTHOR = b"Astra Data Steward <steward@astra.local>"


class FixtureTargetAdapter:
    """Not the Fabric adapter. See this module's own docstring."""

    kind = "fixture"

    def __init__(self, *, repo_path: str | Path, workspace_root: str | Path | None = None) -> None:
        self._repo_path = Path(repo_path)
        self._workspaces = Path(workspace_root) if workspace_root else self._repo_path.parent / "workspaces"
        self._repo_path.mkdir(parents=True, exist_ok=True)
        self._workspaces.mkdir(parents=True, exist_ok=True)
        if not (self._repo_path / ".git").exists():
            porcelain.init(str(self._repo_path))

    def manifest(self) -> TargetManifest:
        return TargetManifest(name="fixture", version="1.0.0", interface_version=TARGET_INTERFACE_VERSION)

    def _open_repo(self) -> Repo:
        try:
            return Repo(str(self._repo_path))
        except Exception as exc:
            raise TargetAdapterError(f"could not open the local Git repository: {exc}") from exc

    # --------------------------------------------------------------------------- commit

    async def commit(self, bundle: TmdlBundle, *, item_path: str, message: str) -> CommitResult:
        return await asyncio.to_thread(self._commit_sync, bundle, item_path, message)

    def _commit_sync(self, bundle: TmdlBundle, item_path: str, message: str) -> CommitResult:
        repo = self._open_repo()
        try:
            target_dir = self._repo_path / item_path
            if target_dir.exists():
                for existing in sorted(target_dir.rglob("*"), reverse=True):
                    if existing.is_file():
                        existing.unlink()
            target_dir.mkdir(parents=True, exist_ok=True)
            for relative_path, content in sorted(bundle.files.items()):
                path = target_dir / relative_path
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(content)

            porcelain.add(repo, paths=[str(target_dir)])
            status = porcelain.status(repo)
            if not any(status.staged.values()):
                # An identical rebuild — the same version emits the same bytes, and
                # nothing is staged. Report the existing HEAD rather than fabricating a
                # second commit for content that has not changed.
                return CommitResult(
                    commit_sha=self._head_sha(repo), ref=_DEFAULT_REF, repository=str(self._repo_path)
                )

            sha = porcelain.commit(repo, message=message.encode("utf-8"), author=_AUTHOR, committer=_AUTHOR)
            commit_sha = sha.decode("ascii") if isinstance(sha, bytes) else str(sha)
            return CommitResult(commit_sha=commit_sha, ref=_DEFAULT_REF, repository=str(self._repo_path))
        except TargetAdapterError:
            raise
        except Exception as exc:
            raise TargetAdapterError(f"git commit failed: {exc}") from exc
        finally:
            repo.close()

    def _head_sha(self, repo: Repo) -> str:
        try:
            return repo.head().decode("ascii")
        except KeyError:
            return ""

    # --------------------------------------------------------------------------- deploy

    async def deploy(self, *, workspace: str, git_ref: str) -> DeploymentResult:
        return await asyncio.to_thread(self._deploy_sync, workspace, git_ref)

    def _deploy_sync(self, workspace: str, git_ref: str) -> DeploymentResult:
        repo = self._open_repo()
        try:
            try:
                head = repo.head()
            except KeyError:
                return DeploymentResult(
                    deployment_id="", workspace=workspace, ok=False,
                    detail="nothing to deploy: no commits yet",
                )
            commit = repo[head]
            tree = repo[commit.tree]
            workspace_dir = self._workspaces / workspace
            workspace_dir.mkdir(parents=True, exist_ok=True)
            deployment_id = hashlib.sha256(f"{workspace}:{git_ref}".encode()).hexdigest()[:16]
            for entry in iter_tree_contents(repo.object_store, tree.id):
                blob = repo[entry.sha]
                dest = workspace_dir / entry.path.decode("utf-8")
                dest.parent.mkdir(parents=True, exist_ok=True)
                dest.write_bytes(blob.data)
            return DeploymentResult(deployment_id=deployment_id, workspace=workspace, ok=True)
        except Exception as exc:
            raise TargetAdapterError(f"deploy failed: {exc}") from exc
        finally:
            repo.close()

    # ---------------------------------------------------------------------- smoke query

    async def smoke_query(
        self, *, workspace: str, table: str, measure_name: str | None
    ) -> SmokeQueryResult:
        return await asyncio.to_thread(self._smoke_query_sync, workspace, table, measure_name)

    def _smoke_query_sync(
        self, workspace: str, table: str, measure_name: str | None
    ) -> SmokeQueryResult:
        """No live analysis-services engine exists locally to run a real DAX query — a
        real gap, disclosed rather than faked (see this module's own docstring). What this
        genuinely checks: that the deployed workspace actually carries this table's own
        TMDL file, which is exactly what a smoke query is *for* — catching a deploy that
        silently dropped something."""
        found = any(
            match.stem == table for match in (self._workspaces / workspace).glob("**/tables/*.tmdl")
        )
        if not found:
            return SmokeQueryResult(
                table=table, row_count=None, measure_name=measure_name, measure_value=None,
                ok=False,
                detail=f"no deployed TMDL file for table '{table}' in workspace '{workspace}'",
            )
        return SmokeQueryResult(
            table=table, row_count=None, measure_name=measure_name, measure_value=None,
            ok=True,
            detail=(
                "structural check only: the table's TMDL landed in the deployed workspace; "
                "no live Fabric analysis-services engine is configured to run a real row "
                "count or measure query — see FixtureTargetAdapter's own docstring"
            ),
        )


__all__ = ["FixtureTargetAdapter"]
