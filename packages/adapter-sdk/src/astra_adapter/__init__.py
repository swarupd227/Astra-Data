"""Astra Data — Source Adapter SDK (specification §6, §20).

    "Adapter SDK (Python): the §6/§7 interfaces, manifest schema, conformance harness,
    grammar tooling for calc-language parsers, and a packaging pipeline. A new source
    adapter is a repository that passes the harness." — §20

Four things live here, and nothing else:

1. **The contract** (`contract`) — §6.1's `SourceAdapter`, its records and its errors.
2. **The RPC** (`rpc`) — an adapter runs out of process and speaks REST (§5.4); `serve`
   is an adapter image's entry point and `RemoteAdapter` is what the platform holds.
3. **A fake source** (`fake`) — a complete §6.1 implementation over a deterministic estate,
   so the suite has something to be true about and the platform has something to harvest.
4. **The conformance suite** (`conformance`) — §6.3's five checks, and `astra-adapter
   conformance` to run them.

This package depends on no platform service. `graph-svc` imports it on the same terms an
adapter author does, which is the only way "a second source can be added without changing
the platform" can be checked rather than asserted.
"""

from __future__ import annotations

from .calc import (
    CalcAST,
    CalcNode,
    Grammar,
    NodeKind,
    canonical_text,
    check_round_trip,
    without_spans,
)
from .conformance.report import SignedReport, sign, verify
from .conformance.suite import (
    CheckResult,
    ConformanceReport,
    ConformanceSuite,
    Corpus,
    Outcome,
    render,
)
from .contract import (
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
from .faults import Fault, FaultInjector, RateLimited, classify
from .proof import (
    Column,
    ColumnRole,
    ExecutionCharter,
    ExecutionOutcome,
    ExecutionStrategy,
    ParityCase,
    ParityRunStamp,
    ResultSet,
    VisualCapture,
    VisualCase,
)
from .registry import UnknownAdapter, load_adapter, register, registered_names
from .target_contract import (
    TARGET_INTERFACE_VERSION,
    CommitResult,
    DeploymentResult,
    SmokeQueryResult,
    TargetAdapter,
    TargetAdapterError,
    TargetManifest,
    TmdlBundle,
)

__version__ = "0.1.0"

__all__ = [
    "BACKLOG_METHOD_NAMES",
    "INTERFACE_VERSION",
    "TARGET_INTERFACE_VERSION",
    "AdapterError",
    "AdapterManifest",
    "AssetRef",
    "CalcAST",
    "CalcNode",
    "Capabilities",
    "CheckResult",
    "Column",
    "ColumnRole",
    "CommitResult",
    "ConformanceReport",
    "ConformanceSuite",
    "Corpus",
    "DeploymentResult",
    "EdgeFragment",
    "ExecutionCharter",
    "ExecutionOutcome",
    "ExecutionStrategy",
    "Fault",
    "FaultInjector",
    "Grammar",
    "NodeFragment",
    "NodeKind",
    "Outcome",
    "OwnershipRecord",
    "ParityCase",
    "ParityRunStamp",
    "ParseResult",
    "RateLimited",
    "RawAsset",
    "ResultSet",
    "Scope",
    "SignedReport",
    "SiteRecord",
    "SmokeQueryResult",
    "SourceAdapter",
    "TargetAdapter",
    "TargetAdapterError",
    "TargetManifest",
    "TmdlBundle",
    "UnknownAdapter",
    "Unrecognised",
    "UnsupportedCapability",
    "UsageKind",
    "UsageRecord",
    "ViewerRecord",
    "VisualCapture",
    "VisualCase",
    "__version__",
    "canonical_text",
    "check_round_trip",
    "classify",
    "load_adapter",
    "register",
    "registered_names",
    "render",
    "sign",
    "verify",
    "without_spans",
]
