"""The Target Adapter contract — specification §7.1/§7.2. Story S4.3.1.

    "TMDL is committed to the client's Git repository through the target adapter... /
    Deployment to the dev workspace uses Fabric Git integration; a smoke query per table
    (row count, one measure) runs and the result is stored."

**The boundary this contract draws follows §7.1's own table exactly.** TMDL emission is
listed against "Modeller" — the platform's own job, deterministic from the frozen design,
and no part of this package — while commit, deploy and the post-deploy smoke query are
listed against "Steward" and the target system: the target adapter's job. This
mirrors ``SourceAdapter`` (§6.1) exactly: the platform decides *what* to write, the adapter
is the only component that knows *how* to reach the target system (spec §19: "acting
integrations run only through the Steward and the target adapter... no other component can
write to a client system").

**No RPC transport yet.** ``SourceAdapter`` runs out of process (§5.4, ADR 0013) because a
source adapter's own parsing/execution logic is untrusted, adapter-supplied code. A target
adapter's job here is narrower — commit, deploy, query — and every implementation this
story ships is platform-authored, so an in-process ``Protocol`` is the honest shape today.
An out-of-process boundary for a third-party target adapter is real future scope this story
does not build, the same "declare the shape, a later story drives it" precedent §6/§7's own
split from S2.1.1 already set.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Protocol, runtime_checkable

#: Version of *this interface*, not of any adapter implementing it — the same discipline
#: ``INTERFACE_VERSION`` (contract.py) applies to the source side. Bumped only when the
#: contract's shape changes.
TARGET_INTERFACE_VERSION = "1.0"


@dataclass(frozen=True, slots=True)
class TargetManifest:
    """Identity of a target adapter build. No ``Capabilities`` — §6.1's are all source-side
    concerns (live query, extract read, usage...); a target adapter either implements this
    whole narrow contract or it is not one."""

    name: str
    version: str
    interface_version: str


@dataclass(frozen=True, slots=True)
class TmdlBundle:
    """A deterministically emitted TMDL folder (§7.1), ready to commit.

    ``files`` maps a relative path within the model's TMDL folder — ``model.tmdl``,
    ``tables/positions.tmdl``, ``relationships.tmdl``, ``roles/Analyst.tmdl`` — to its exact
    byte content. A ``dict``, not a list: two bundles with the same files in a different
    build order must still compare and hash equal, since "the same version always produces
    byte-identical TMDL" is about content, not emission order.
    """

    files: Mapping[str, bytes]

    def __post_init__(self) -> None:
        object.__setattr__(self, "files", dict(self.files))


@dataclass(frozen=True, slots=True)
class CommitResult:
    """Where the bundle landed in Git."""

    commit_sha: str
    ref: str
    repository: str


@dataclass(frozen=True, slots=True)
class DeploymentResult:
    """The result of syncing a committed ref into a Fabric workspace via Git integration."""

    deployment_id: str
    workspace: str
    ok: bool
    detail: str = ""


@dataclass(frozen=True, slots=True)
class SmokeQueryResult:
    """One table's post-deploy smoke check (§7.1: "row count, one measure")."""

    table: str
    row_count: int | None
    measure_name: str | None
    measure_value: float | None
    ok: bool
    detail: str = ""


class TargetAdapterError(Exception):
    """The target adapter could not do what was asked — a commit, a deploy or a smoke
    query failed. Carries enough detail (via ``str()``) to land verbatim on the Build tab's
    log; nothing here is meant to be caught and reworded."""


@runtime_checkable
class TargetAdapter(Protocol):
    """Specification §7.1/§7.2 — commit, deploy, and the post-deploy smoke query.

    TMDL emission is deliberately **not** a method here — see this module's own docstring.
    An adapter receives an already-emitted, already-deterministic ``TmdlBundle`` and only
    ever writes it onward.
    """

    def manifest(self) -> TargetManifest: ...

    async def commit(self, bundle: TmdlBundle, *, item_path: str, message: str) -> CommitResult:
        """Commit the bundle to the client's Git repository (§4.2), under ``item_path`` —
        the model's own folder within a repository every family in a tenant shares, the
        same ``<Item Name>.SemanticModel/definition/...`` convention Fabric Git integration
        itself uses so two models' files never collide. Idempotent in spirit, not in fact:
        committing the same bundle twice is a normal retry, not an error — Git's own
        history is the record of that, same as any other commit."""
        ...

    async def deploy(self, *, workspace: str, git_ref: str) -> DeploymentResult:
        """Sync ``git_ref`` into ``workspace`` via Fabric Git integration (§7.1)."""
        ...

    async def smoke_query(
        self, *, workspace: str, table: str, measure_name: str | None
    ) -> SmokeQueryResult:
        """Row count, and one measure's value, read back from the deployed model (§7.1:
        "deploy to dev workspace and read back")."""
        ...


__all__ = [
    "TARGET_INTERFACE_VERSION",
    "CommitResult",
    "DeploymentResult",
    "SmokeQueryResult",
    "TargetAdapter",
    "TargetAdapterError",
    "TargetManifest",
    "TmdlBundle",
]
