"""Which target adapter this deployment builds through. Story S4.3.1.

The exact mirror of ``harvest_setup.build_source_adapter`` on the target side: today there
is one adapter, the fixture, always enabled — a real Fabric-connected adapter needs a live
Azure AD app registration, workspace id and service-principal credential this platform has
never been given, the same "declared, not invented" posture ``NullDirectoryResolver`` and
``EnvironmentCredentialProvider`` (E11's own Key Vault provider not yet built) already carry
elsewhere. When a real target adapter is built, it is registered here and nothing else in
``build.py`` changes — ``build_family`` is written against ``TargetAdapter`` and cannot tell
the difference.
"""

from __future__ import annotations

import logging

from astra_adapter import TargetAdapter

from .adapters.target_fixture import FixtureTargetAdapter
from .config import Settings

logger = logging.getLogger(__name__)


def build_target_adapter(config: Settings) -> TargetAdapter:
    logger.info(
        "fixture target adapter enabled: repo=%s workspace=%s; this is not a live Fabric "
        "tenant",
        config.target_git_repo_path,
        config.target_workspace,
    )
    return FixtureTargetAdapter(repo_path=config.target_git_repo_path)


__all__ = ["build_target_adapter"]
