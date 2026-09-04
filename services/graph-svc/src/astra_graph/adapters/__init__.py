"""Source adapters (spec §6).

The contract lives here; the Tableau implementation is E2 (F2.1-F2.4). The fixture
adapter exists so the Harvester can be built and measured before that lands.
"""

from .contract import (
    AdapterError,
    AdapterManifest,
    AssetRef,
    Capabilities,
    EdgeFragment,
    NodeFragment,
    OwnershipRecord,
    ParseResult,
    RawAsset,
    Scope,
    SiteRecord,
    SourceAdapter,
    Unrecognised,
    UsageKind,
    UsageRecord,
    ViewerRecord,
)

__all__ = [
    "AdapterError",
    "AdapterManifest",
    "AssetRef",
    "Capabilities",
    "EdgeFragment",
    "NodeFragment",
    "OwnershipRecord",
    "ParseResult",
    "RawAsset",
    "Scope",
    "SiteRecord",
    "SourceAdapter",
    "Unrecognised",
    "UsageKind",
    "UsageRecord",
    "ViewerRecord",
]
