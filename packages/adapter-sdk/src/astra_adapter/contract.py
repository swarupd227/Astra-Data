"""The Source Adapter contract — specification §6.1.

A source adapter is the only component that knows the source platform. The platform drives
it and knows nothing about Tableau; that separation is what lets §6.3's conformance suite be
the definition of "an adapter works", and what lets a second source be added without
touching the platform.

This module is the published interface (§20: "Adapter SDK (Python): the §6/§7 interfaces,
manifest schema, conformance harness, grammar tooling for calc-language parsers, and a
packaging pipeline"). It has **no dependency on any platform service**, by design and by
test: an adapter author installs ``astra-adapter-sdk`` and nothing else, and `graph-svc`
consumes this package on the same terms they do.

**A naming difference in the source documents.** §6.1 names the methods ``manifest``,
``enumerate``, ``fetch``, ``parse``, ``parseCalc``, ``usage``, ``owners``, ``executeCase``.
Backlog story S2.1.1 names six of them ``discover``, ``fetch_workbook``, ``parse``,
``execute_case``, ``capture_visual``, ``capabilities``. The backlog's own rule is that the
specification wins, so §6.1's names are used in Python spelling, and ``BACKLOG_METHOD_NAMES``
below records the mapping so the difference is checkable rather than remembered. Both
decisions are in ADR 0004 and ADR 0013.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Sequence
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Protocol, runtime_checkable

from .calc import CalcAST
from .proof import ParityCase, ResultSet, VisualCapture, VisualCase

#: Version of *this interface*, not of any adapter implementing it. Recorded on every
#: harvest and on every result set an adapter produces, so a record can always be read
#: against the contract that produced it (S2.1.1 criterion 4). Bumped when the contract
#: changes shape, never when an adapter changes behaviour.
#:
#: **1.1** (S2.4.1) retyped ``ResultSet.columns`` from strings to `Column` descriptors, which
#: §10.2 requires — "an ordered list of column descriptors (name, role, type)". Retyping a
#: field is a breaking change, which is precisely what this version exists to make visible:
#: an adapter built against 1.0 is refused rather than silently handing the Proof Engine
#: untyped columns it would have to guess the roles of. ADR 0015 set the rule (additive
#: fields do not move the version; removals and retypes do) and this is its first exercise.
INTERFACE_VERSION = "1.1"

#: The six method names story S2.1.1 asks for, mapped to the specification's names, which
#: win. Asserted against the protocol in the SDK's tests, so the mapping cannot rot.
BACKLOG_METHOD_NAMES: dict[str, str] = {
    "discover": "enumerate",
    "fetch_workbook": "fetch",
    "parse": "parse",
    "execute_case": "execute_case",
    "capture_visual": "capture_visual",
    "capabilities": "manifest",  # exposed as manifest().capabilities, per §6.1
}


@dataclass(frozen=True, slots=True)
class Capabilities:
    """What this adapter can do against this source (spec §6.1).

    An absent capability is a fact about the deployment, not a defect: a Tableau site
    without the Metadata API enabled cannot supply lineage or usage, and the Estate
    surface is meant to show that rather than silently report zero (backlog §7.1).
    """

    live_query: bool = False
    extract_read: bool = False
    usage: bool = False
    ownership: bool = False
    screenshot: bool = False


@dataclass(frozen=True, slots=True)
class AdapterManifest:
    """Identity of an adapter build. Recorded on every harvest (spec §6.1, S2.1.1)."""

    name: str
    version: str
    grammar_version: str
    interface_version: str
    capabilities: Capabilities


@dataclass(frozen=True, slots=True)
class Scope:
    """What to harvest: a site, a project within it, or everything the adapter can see."""

    site: str | None = None
    project: str | None = None

    def describe(self) -> str:
        if self.site and self.project:
            return f"site {self.site}, project {self.project}"
        if self.site:
            return f"site {self.site}"
        return "the whole estate"


@dataclass(frozen=True, slots=True)
class AssetRef:
    """One thing the adapter can fetch. Enumeration yields these in site → project →
    workbook order, so a consumer can report progress per project before fetching."""

    luid: str
    name: str
    site: str
    project: str
    revision: str
    """Source revision. The Harvester compares it to decide whether to re-parse."""

    project_path: tuple[str, ...] = ()
    """Nested project names from the site root, for the Estate Explorer's tree."""

    updated_at: str | None = None


@dataclass(frozen=True, slots=True)
class RawAsset:
    """Bytes plus metadata, content-hashed (spec §6.1)."""

    ref: AssetRef
    content_hash: str
    """The adapter's hash of the fetched bytes. Spec §8.4 makes the Harvester idempotent
    on this, so it must change when and only when the asset does."""

    payload: bytes
    """The fetched bytes. Adapter-private in meaning — the platform passes them back to
    ``parse`` and never interprets them — but **bytes**, not an arbitrary object.

    An earlier in-process version typed this ``Any`` and the fixture adapter put a Python
    object in it. That works only while the adapter shares a process with its caller, which
    S2.1.1 ends: an adapter runs out of process and its fetch result crosses a wire. §6.1
    says "bytes + metadata, content-hashed", and now it is."""

    size_bytes: int = 0

    media_type: str = "application/octet-stream"
    """What the bytes are — ``application/zip`` for a .twbx, ``application/xml`` for a bare
    .twb. Carried so an artefact store can label what it keeps without sniffing."""


@dataclass(frozen=True, slots=True)
class NodeFragment:
    """One node an adapter parsed out of an asset.

    ``key`` is the adapter's stable identity for it within the source — for example
    ``worksheet:VaR by Desk``. The Harvester turns that into a platform id
    deterministically, which is what makes a re-harvest write the same ids.
    """

    key: str
    type: str
    properties: dict[str, Any]


@dataclass(frozen=True, slots=True)
class EdgeFragment:
    """One edge between two fragments, addressed by their keys."""

    type: str
    from_key: str
    to_key: str
    properties: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class Unrecognised:
    """A source construct the adapter's grammar did not understand (spec §4.1.4).

    Retained verbatim and counted against parse quality. The Parse Quality Queue (S1.4.3)
    works from these; this story only records them.
    """

    construct: str
    location: str
    detail: str = ""


@dataclass(frozen=True, slots=True)
class ParseResult:
    """A graph fragment, its parse quality, and what the grammar could not read."""

    nodes: Sequence[NodeFragment]
    edges: Sequence[EdgeFragment]
    parse_quality: float
    """Recognised constructs ÷ total constructs (spec §4.1.4)."""

    unrecognised: Sequence[Unrecognised] = ()
    constructs_total: int = 0
    constructs_recognised: int = 0

    def __post_init__(self) -> None:
        """Normalise the sequences to tuples.

        The fields are typed ``Sequence``, so an adapter may legitimately return a list and
        the wire decoder legitimately returns a tuple — and then two parse results with
        identical contents compare unequal, because a dataclass compares its fields with
        ``==`` and ``[a] != (a,)``. That bites exactly where it matters most: the conformance
        suite comparing an adapter's output to an expected fragment, and any test comparing
        a parse across the RPC. Normalising here makes equality mean what a reader assumes it
        means, whatever an adapter chose to build.
        """
        object.__setattr__(self, "nodes", tuple(self.nodes))
        object.__setattr__(self, "edges", tuple(self.edges))
        object.__setattr__(self, "unrecognised", tuple(self.unrecognised))

    @property
    def workbook_key(self) -> str:
        """The fragment's own root. Every parse produces exactly one Workbook node, which
        is the Migration Unit (spec §3.1)."""
        for node in self.nodes:
            if node.type == "Workbook":
                return node.key
        raise ValueError("a parse result must contain exactly one Workbook node")


class UsageKind(str, Enum):
    """What an usage record counts.

    Tableau reports usage per published *view* as well as per workbook, and S1.2.3 needs
    both: a wave is ordered by business impact, and a workbook whose usage sits in one
    dashboard is a different proposition from one spread across six sheets.
    """

    WORKBOOK = "workbook"
    VIEW = "view"


@dataclass(frozen=True, slots=True)
class UsageRecord:
    """Views and viewers for one asset over a window (spec §6.1)."""

    asset_luid: str
    views: int
    distinct_viewers: int
    last_view: str | None = None
    kind: UsageKind = UsageKind.WORKBOOK
    workbook_luid: str | None = None
    """For a view, the workbook it belongs to."""

    view_name: str | None = None
    """For a view, the name of the sheet or dashboard, as the parse fragment names it."""


@dataclass(frozen=True, slots=True)
class ViewerRecord:
    """One person's views of one asset.

    Optional, and separate from ``UsageRecord`` because most sources report aggregates
    cheaply and per-viewer detail expensively — Tableau's is in the historical_events
    admin views, which §6.2 marks as "where available". Without it the platform still has
    the aggregate; with it, VIEWED_BY edges (spec §4.1.2) can be written truthfully rather
    than inferred.
    """

    asset_luid: str
    viewer_upn: str
    views: int
    last_view: str | None = None


@dataclass(frozen=True, slots=True)
class OwnershipRecord:
    """Who owns an asset, as the source knows them.

    Resolving that identity against the directory is the platform's job, not the
    adapter's: §6.2 says owners are "mapped to Entra users where a match exists", and the
    adapter has no reason to know what a directory is.
    """

    asset_luid: str
    owner_upn: str
    owner_display: str | None = None
    licence_tier: str | None = None
    """Creator / Explorer / Viewer, where the source exposes it (S1.2.3)."""

    site_roles: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class SiteRecord:
    """What the source knows about a site itself."""

    site: str
    licence_tier: str | None = None
    user_count: int | None = None

    detail: dict[str, Any] = field(default_factory=dict)
    """Whatever else the source says about itself — for Tableau, the product version, the
    deployment kind and whether the Metadata API is enabled (S2.2.1: "the version is recorded
    per site").

    Open rather than typed because it is *source-specific by definition*: a Tableau version
    and a Looker instance id have nothing in common to model, and a field per source on a
    shared contract would grow one column per adapter forever. The platform stores it and
    the Estate surface shows it; nothing in the platform branches on its contents.

    **Additive, and the interface version does not move.** A field with a default is
    compatible in both directions over JSON: an older adapter omits it and a newer platform
    reads the default; a newer adapter sends it and an older platform ignores it. Removing or
    retyping a field is what breaks a contract, and that is what ``INTERFACE_VERSION`` is
    for. Recorded in ADR 0015."""


class AdapterError(Exception):
    """The adapter could not do what was asked.

    Raised per asset where possible. The Harvester records it against that workbook and
    carries on: "failures do not stop the run and are listed with the error" (S1.2.1).
    """

    def __init__(self, message: str, *, retryable: bool = False) -> None:
        self.retryable = retryable
        super().__init__(message)


class UnsupportedCapability(AdapterError):
    """The adapter does not do this, and never claimed to.

    Distinct from ``AdapterError`` because "this Tableau site has no Metadata API, so there
    is no usage to give you" is a fact about the deployment that the Estate surface is
    meant to show (backlog §7.1), not a failure to retry or to record against a workbook.
    """

    def __init__(self, capability: str, *, adapter: str = "") -> None:
        self.capability = capability
        who = f"{adapter} " if adapter else ""
        super().__init__(
            f"{who}adapter does not support '{capability}'; it is absent from its "
            f"capabilities and was never claimed",
            retryable=False,
        )


@runtime_checkable
class SourceAdapter(Protocol):
    """Specification §6.1 — the whole contract, including the parts nothing drives yet.

    ``parse_calc`` is used inside ``parse``; ``execute_case`` belongs to the Proof Engine
    (E7) and ``capture_visual`` to §10.6's advisory visual comparison. They are declared
    here because an interface that omits the methods its later consumers will need is not a
    versioned interface — it is a snapshot of what happens to be called today, and adding
    them later would be a breaking change to every adapter in the field.

    An adapter that cannot do one of them says so in its capabilities and raises
    ``UnsupportedCapability``. That is a different answer from "it failed", and the
    conformance suite treats it as one: a capability an adapter does not claim is not
    tested, and a capability it *does* claim must work.
    """

    def manifest(self) -> AdapterManifest: ...

    def enumerate(self, scope: Scope) -> AsyncIterator[AssetRef]: ...

    async def fetch(self, asset: AssetRef) -> RawAsset: ...

    async def parse(self, raw: RawAsset) -> ParseResult: ...

    async def usage(self, scope: Scope, window_days: int) -> Sequence[UsageRecord]: ...

    async def owners(self, scope: Scope) -> Sequence[OwnershipRecord]: ...

    async def viewers(self, scope: Scope, window_days: int) -> Sequence[ViewerRecord]: ...

    """Per-viewer usage, where the source exposes it. May return nothing."""

    async def sites(self, scope: Scope) -> Sequence[SiteRecord]: ...

    """What the source knows about the sites in scope."""

    async def parse_calc(self, expression: str) -> CalcAST: ...

    """§6.1 ``parseCalc``: a grammar-backed AST for one calculation, versioned.

    Separate from ``parse`` because the Pattern Library and the Transpiler need to parse an
    expression that is not attached to a workbook — a candidate rewrite, a pattern
    signature — and because §6.3 checks the grammar by round-tripping it, which needs an
    entry point that takes text.

    **Async, unlike §6.1's sketch.** The specification writes the interface in a synchronous
    pseudo-syntax and marks nothing as awaiting; ``enumerate``, ``fetch`` and ``parse`` are
    async here for the obvious reason that they do I/O. ``parseCalc`` looks like pure
    computation and was written synchronously to match — until this story put the adapter in
    another process, at which point every method on the interface crosses a socket and a
    synchronous one can only be served by blocking the caller's event loop behind it. Async
    is not a concession to the transport; it is what the method always was once the adapter
    stopped sharing a process. Recorded in ADR 0013."""

    async def execute_case(self, case: ParityCase) -> ResultSet: ...

    """§6.1 ``executeCase``: the expected result set at the case grain, for the Proof
    Engine. The strategy actually used is recorded on the result (§6.2), because "extract
    read" and "live replay" are different evidence and a verdict has to say which it
    rests on."""

    async def capture_visual(self, case: VisualCase) -> VisualCapture: ...

    """§6.2 Screenshot / §10.6: an image of the source view, for the advisory visual
    comparison. Advisory is the operative word — §10.6 gates on data parity and a human
    review, never on this."""
