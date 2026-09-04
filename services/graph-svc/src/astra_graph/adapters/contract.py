"""The Source Adapter contract — specification §6.1.

**The definitions moved.** S2.1.1 publishes §6.1 as a standalone Python package,
``astra-adapter-sdk``, because "a second source can be added without changing the platform"
is only checkable if the contract an adapter author installs is the same object the platform
imports. It is, and this module is the proof: `graph-svc` consumes the published package on
exactly the terms an adapter author does.

This module remains so that the platform's own imports read as platform imports and do not
have to be rewritten across a dozen modules to say the same thing. New code may import from
either; ``astra_adapter`` is the canonical home. See ADR 0013.
"""

from __future__ import annotations

from astra_adapter.contract import (
    BACKLOG_METHOD_NAMES,
    INTERFACE_VERSION,
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
    UnsupportedCapability,
    UsageKind,
    UsageRecord,
    ViewerRecord,
)

__all__ = [
    "BACKLOG_METHOD_NAMES",
    "INTERFACE_VERSION",
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
    "UnsupportedCapability",
    "UsageKind",
    "UsageRecord",
    "ViewerRecord",
]
