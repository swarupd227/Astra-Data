"""The Tableau source adapter — specification §6.2, epic E2.

Discovery and fetch (F2.2 / S2.2.1) against **Tableau Server 2021.4+ and Tableau Cloud**,
through one adapter: the Metadata API for the object graph, the REST API for downloads,
personal-access-token and connected-app authentication, 429 backoff and a configurable
per-site concurrency cap.

Parsing is F2.3 and execution is F2.4. This adapter therefore does not pass conformance yet,
and cannot be promoted to a tenant until it does (S2.1.2) — which is the gate working.

``build()`` is the entry point the SDK's registry loads for the name ``tableau``.
"""

from __future__ import annotations

from .adapter import (
    ADAPTER_NAME,
    ADAPTER_VERSION,
    CAPABILITIES,
    GRAMMAR_VERSION,
    TableauAdapter,
)
from .archive import WorkbookArchive, extract_workbook_xml
from .auth import Session, mint_connected_app_token, sign_in_payload
from .config import (
    DEFAULT_CONCURRENCY,
    MINIMUM_API_VERSION,
    SERVER_FLOOR,
    AuthKind,
    Credential,
    TableauConfig,
)
from .corpus import GOLDEN_EXPRESSIONS
from .corpus import golden as _golden_corpus
from .metadata import TableauMetadataClient
from .rest import Deployment, ServerInfo, TableauRestClient
from .throttle import SiteThrottle, ThrottleRegistry, retry_after_seconds

__version__ = ADAPTER_VERSION


def corpus() -> object:
    """The conformance corpus this adapter ships (§6.3).

    Found by name: the SDK's CLI looks for a ``corpus()`` on the adapter's package, which is
    how `astra-adapter conformance --adapter tableau` knows what to check against without the
    SDK knowing anything about Tableau.
    """
    return _golden_corpus()


def build() -> TableauAdapter:
    """The adapter, configured from this worker's environment.

    A worker serves one deployment and one site (§5.2: "per site parallelism"), so the base
    URL, the site and the credential come from the worker's own configuration and never over
    the adapter RPC — the platform names a credential, it does not send one.
    """
    return TableauAdapter(TableauConfig.from_environment())


__all__ = [
    "ADAPTER_NAME",
    "ADAPTER_VERSION",
    "CAPABILITIES",
    "DEFAULT_CONCURRENCY",
    "GOLDEN_EXPRESSIONS",
    "GRAMMAR_VERSION",
    "MINIMUM_API_VERSION",
    "SERVER_FLOOR",
    "AuthKind",
    "Credential",
    "Deployment",
    "ServerInfo",
    "Session",
    "SiteThrottle",
    "TableauAdapter",
    "TableauConfig",
    "TableauMetadataClient",
    "TableauRestClient",
    "ThrottleRegistry",
    "WorkbookArchive",
    "__version__",
    "build",
    "corpus",
    "extract_workbook_xml",
    "mint_connected_app_token",
    "retry_after_seconds",
    "sign_in_payload",
]
