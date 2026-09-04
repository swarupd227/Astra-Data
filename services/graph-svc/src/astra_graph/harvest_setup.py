"""Which source adapter this deployment harvests through.

Spec §6.3: an adapter is enabled on a tenant only after it passes the conformance suite,
so which adapter is present is deployment configuration, not a code path.

Today there is one: the fixture adapter, and it is enabled only where the deployment says
it is local. The Tableau adapter is E2 (F2.2 to F2.4); when it lands it is registered here and
nothing else in the service changes. A deployment with no adapter enabled has no harvest
endpoint that will do anything, which is the honest state of affairs rather than a stub
that pretends to work.
"""

from __future__ import annotations

import logging
import os

from astra_adapter.rpc import RemoteAdapter

from .adapters.contract import SourceAdapter
from .adapters.fixture import FixtureSourceAdapter, build_site
from .config import Settings
from .credentials import CredentialProvider, EnvironmentCredentialProvider
from .directory import DirectoryResolver, NullDirectoryResolver
from .harvest import Harvester, PromotionGate
from .harvest.quality import ParseQualityStore
from .harvest.store import HarvestStore
from .migration_units import MigrationUnitRegistry, NullMigrationUnitRegistry
from .writes import GraphWriter

logger = logging.getLogger(__name__)

#: Set to enable the fixture adapter outside a local environment — useful for a demo
#: deployment, never for a client tenant.
FIXTURE_ENV_FLAG = "ASTRA_ENABLE_FIXTURE_ADAPTER"

#: Base URL of an adapter worker speaking the §6.1 RPC. Set, and the platform harvests
#: through that process instead of an in-process adapter — which is how an adapter is
#: actually deployed (§5.2, §5.4). Takes precedence over the fixture adapter.
ADAPTER_URL_ENV = "ASTRA_ADAPTER_URL"

#: Size of the fixture estate the local stack offers.
FIXTURE_SITES = (("rqa", 40), ("gtaa", 25))


def fixture_adapter_enabled(config: Settings) -> bool:
    flag = os.environ.get(FIXTURE_ENV_FLAG, "").strip().lower()
    if flag in {"1", "true", "yes"}:
        return True
    return config.env == "local"


def build_credential_provider(config: Settings) -> CredentialProvider:
    """Environment-backed for now. Key Vault arrives with E11."""
    return EnvironmentCredentialProvider()


def build_directory_resolver(config: Settings) -> DirectoryResolver:
    """Which directory this deployment resolves owners against.

    Nothing, until E11 supplies the Entra resolver with the credential and workload
    identity to use it. A null resolver is the honest state: every owner is listed as
    unresolved, and the listing says which resolver produced that answer.
    """
    return NullDirectoryResolver()


def build_migration_unit_registry(config: Settings) -> MigrationUnitRegistry:
    """Where this deployment looks for Migration Units.

    Nowhere, until E3 creates the first one. A null registry is not a stub standing in for
    something missing — before the Cartographer runs, no workbook has work in progress
    over it, so no harvest can disturb any. Platform Health reports which registry answered
    so that "no drift" and "nothing was asked" stay distinguishable.
    """
    return NullMigrationUnitRegistry()


def build_source_adapter(config: Settings) -> SourceAdapter | None:
    """The adapter this deployment harvests through.

    Two ways, in order:

    1. **Out of process.** ``ASTRA_ADAPTER_URL`` points at an adapter worker and the platform
       holds a `RemoteAdapter` over the §6.1 RPC (S2.1.1). This is the deployed shape: §5.2
       makes an adapter a worker, §5.4 runs it as its own pod, and §6.1 packages it as a
       versioned image. When the Tableau adapter lands (F2.2) it is configured here and
       nothing else changes — the Harvester is written against §6.1 and cannot tell the
       difference.
    2. **In process.** The fixture adapter, for local development and CI.

    Nothing else. An adapter this deployment has not been given is not invented.
    """
    url = os.environ.get(ADAPTER_URL_ENV, "").strip()
    if url:
        logger.info("source adapter is out of process at %s", url)
        return RemoteAdapter(url)

    if not fixture_adapter_enabled(config):
        return None

    logger.info(
        "fixture source adapter enabled (%s); this is not the Tableau adapter",
        ", ".join(f"{name}:{count}" for name, count in FIXTURE_SITES),
    )
    return FixtureSourceAdapter(
        # The demo estate carries grammar gaps so the Parse Quality Queue has
        # something real to show locally (S1.4.3).
        [
            build_site(name, workbooks, grammar_gaps=True)
            for name, workbooks in FIXTURE_SITES
        ]
    )


#: Adapters exempt from the promotion gate (S2.1.2), and the only ones.
#:
#: The fixture adapter is not a source: it generates its estate, reaches nothing, and exists
#: so the platform can be developed before F2.2. Requiring a recorded conformance report
#: before `docker compose up` produces a working stack would gate local development on a
#: ceremony that protects nobody — the gate exists to keep an untested adapter away from a
#: *client's* estate, and the fixture has no client.
#:
#: It is a name list rather than a flag so that adding to it is a visible change to a
#: security-relevant constant, not a boolean somebody sets in an environment.
UNGATED_ADAPTERS = frozenset({"fixture", "fake"})


def build_harvester(
    config: Settings,
    *,
    writer: GraphWriter,
    store: HarvestStore,
    quality: ParseQualityStore | None = None,
    directory: DirectoryResolver | None = None,
    migration_units: MigrationUnitRegistry | None = None,
    adapter: SourceAdapter | None = None,
    promotions: PromotionGate | None = None,
) -> Harvester | None:
    adapter = adapter or build_source_adapter(config)
    if adapter is None:
        logger.info(
            "no source adapter enabled for env=%s; harvest endpoints will report that",
            config.env,
        )
        return None

    return Harvester(
        adapter=adapter,
        writer=writer,
        store=store,
        credentials=build_credential_provider(config),
        graph_name=config.graph_name,
        quality=quality,
        directory=directory or build_directory_resolver(config),
        migration_units=migration_units or build_migration_unit_registry(config),
        promotions=promotions,
    )
