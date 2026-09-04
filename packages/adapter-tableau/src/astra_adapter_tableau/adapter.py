"""The Tableau source adapter — §6.1 and §6.2, discovery and fetch (F2.2 / S2.2.1).

**What this story delivers.** `enumerate` (the Metadata API for the object graph, the REST
API for what a download needs), `fetch` (`.twb` and `.twbx`, unpacked, with the revision id),
`sites` (the version, per site), throttling and the site concurrency cap, and both
authentication kinds.

**S2.2.2 added the datasource half of the parse and S2.3.1 the calculations.** `parse` reads
the workbook's ``<datasources>`` — §6.2's first "Parse — structure" item — emits Datasource,
Connection, Table and Field nodes with the sheets they hang off, and parses every calculated
field into a typed AST with source spans. A construct the grammar cannot read is retained
verbatim and flagged, never dropped, so §4.1.4 holds that workbook and the Parse Quality Queue
names the construct.

**S2.3.2 completes the parse.** Sheets carry their shelves, marks, sorts and typed filters;
dashboards carry their zone tree; actions and parameters are captured; and row-level security
is detected and recorded on the Workbook.

**S2.4.1 adds execution.** `execute_case` runs a parity case against Tableau itself through
one of §6.2's three strategies, chosen from the charter and this deployment's capabilities.
View data always works; extract read and live replay need components a deployment may not
have, and say so rather than pretending (`ports.py`).

**S2.4.2 adds visual capture.** `capture_visual` renders a sheet or dashboard through REST
``queryViewImage`` and resizes it to exactly the size asked for (`visual.py`). It needs
nothing extra, so ``screenshot`` is always claimed.

**What it deliberately does not.** Usage and ownership are not queried yet.

**This adapter is therefore not conformant yet, and says so.** `astra-adapter conformance
--adapter tableau` runs and fails on the checks F2.3 and F2.4 will satisfy. That is the
system working: S2.1.2 made a passing report the condition of promotion, so this adapter
cannot reach a client's estate until it earns one.
"""

from __future__ import annotations

import hashlib
import logging
from collections.abc import AsyncIterator, Sequence
from typing import Any

from astra_adapter import (
    INTERFACE_VERSION,
    AdapterError,
    AdapterManifest,
    AssetRef,
    CalcAST,
    Capabilities,
    ExecutionCharter,
    ExecutionStrategy,
    OwnershipRecord,
    ParityCase,
    ParseResult,
    RawAsset,
    ResultSet,
    Scope,
    SiteRecord,
    UnsupportedCapability,
    UsageRecord,
    ViewerRecord,
    VisualCapture,
    VisualCase,
)
from astra_adapter.faults import Fault

from .archive import extract_workbook_xml
from .config import TableauConfig
from .datasource import read_structure
from .execution import TableauExecutor
from .faults import FaultingTransport
from .fragments import build, secret_references
from .grammar import GRAMMAR_VERSION as TABLEAU_GRAMMAR_VERSION
from .grammar import TableauGrammar
from .metadata import MetadataWorkbook, TableauMetadataClient
from .ports import (
    ExtractReader,
    LiveQueryRunner,
    NoExtractReader,
    NoLiveQueryRunner,
    describe,
)
from .rest import TableauRestClient, WorkbookRef
from .sheets import read_sheets
from .throttle import SiteThrottle
from .visual import TableauVisualCapturer

logger = logging.getLogger(__name__)

ADAPTER_NAME = "tableau"
ADAPTER_VERSION = "0.1.0"

#: The calculation grammar this build parses with (S2.3.1's fourth criterion). Recorded on
#: every CalcAST and every ParseResult, so a workbook parsed months ago can be read against
#: the grammar that read it and a re-harvest under a newer one is a visible change.
GRAMMAR_VERSION = TABLEAU_GRAMMAR_VERSION

#: What this adapter can do *today*. `usage` and `ownership` stay false: the queries that
#: would supply them are S1.2.3's shape against an API this adapter does not call yet.
#:
#: `extract_read` and `live_query` are **decided at construction, not here** — both depend on
#: a component this deployment may or may not have (see `ports.py`), and a capability is a
#: claim that binds (S2.1.2). Claiming one the deployment cannot perform would make the
#: conformance suite check something that does not exist; §6.1 makes an unclaimed capability a
#: fact about the deployment rather than a defect.
BASE_CAPABILITIES = Capabilities(
    live_query=False,
    extract_read=False,
    usage=False,
    ownership=False,
    # Screenshot needs nothing this deployment might lack — REST queryViewImage is a call
    # the adapter already knows how to make (S2.4.2) — so it is always claimed, unlike
    # extract_read and live_query above.
    screenshot=True,
)

#: Kept for the platform's own imports; the *live* capabilities come from `manifest()`.
CAPABILITIES = BASE_CAPABILITIES


class TableauAdapter:
    """§6.1, against Tableau Server 2021.4+ and Tableau Cloud."""

    def __init__(
        self,
        config: TableauConfig,
        *,
        rest: TableauRestClient | None = None,
        extract_reader: ExtractReader | None = None,
        live_runner: LiveQueryRunner | None = None,
        charter: ExecutionCharter | None = None,
    ) -> None:
        self._config = config
        self._throttle = SiteThrottle(
            concurrency=config.concurrency,
            max_retries=config.max_retries,
            site=config.site_label,
        )
        self._grammar = TableauGrammar()
        self._faults = FaultingTransport()
        self._extract_reader: ExtractReader = extract_reader or NoExtractReader()
        self._live_runner: LiveQueryRunner = live_runner or NoLiveQueryRunner()
        self._rest = rest or TableauRestClient(
            config,
            throttle=self._throttle,
            client=self._faults.client(config),
        )
        self._metadata = TableauMetadataClient(self._rest, page_size=config.page_size)
        self._refs: dict[str, WorkbookRef] = {}
        self._described: dict[str, MetadataWorkbook] = {}
        self._revisions: dict[str, str] = {}
        self._charter = charter or ExecutionCharter()
        self._executor = TableauExecutor(
            self._rest,
            adapter_name=ADAPTER_NAME,
            adapter_version=ADAPTER_VERSION,
            grammar_version=GRAMMAR_VERSION,
            extract_reader=self._extract_reader,
            live_runner=self._live_runner,
        )
        self._visuals = TableauVisualCapturer(
            self._rest, adapter_name=ADAPTER_NAME, adapter_version=ADAPTER_VERSION
        )
        self._extract_files: dict[str, tuple[str, ...]] = {}
        """Extract file *names* seen in each .twbx (S2.2.2). Names only — `archive.py` never
        reads a data entry, and the download asks Tableau not to send one."""

        self._secret_references: dict[str, Any] = {}
        self._schedules: dict[str, str] = {}

    # -------------------------------------------------------------------- §6.1

    def manifest(self) -> AdapterManifest:
        """Capabilities reflect what this *deployment* can do, not what the code can.

        Two of the three execution strategies need a component that may be absent (`ports`),
        and a capability is a claim that binds: the conformance suite fails an adapter that
        claims one and cannot deliver it, and skips one it does not claim. So the manifest is
        computed rather than constant.
        """
        strategies = self._executor.strategies()
        return AdapterManifest(
            name=ADAPTER_NAME,
            version=ADAPTER_VERSION,
            grammar_version=GRAMMAR_VERSION,
            interface_version=INTERFACE_VERSION,
            capabilities=Capabilities(
                live_query=ExecutionStrategy.LIVE_REPLAY in strategies,
                extract_read=ExecutionStrategy.EXTRACT_READ in strategies,
                usage=False,
                ownership=False,
                screenshot=True,
            ),
        )

    async def aclose(self) -> None:
        await self._rest.aclose()

    async def enumerate(self, scope: Scope) -> AsyncIterator[AssetRef]:
        """Discovery: the Metadata API for the object graph, REST for what a download needs.

        Both, because neither is sufficient. The Metadata API describes the estate's shape in
        a handful of queries and cannot give you a file; the REST listing carries the ids and
        project the download call needs and knows nothing about shape. They are joined on the
        workbook LUID, which is the only identifier both use.

        When the Metadata API is disabled — a Tableau Server administrator can turn it off,
        and many have — discovery falls back to the REST listing alone and **says so** in the
        adapter's log and in `sites()`. A shallower estate that is labelled shallow is a
        different thing from an estate that is quietly missing its lineage.
        """
        info = await self._rest.server_info()
        refs = await self._rest.workbooks()
        self._refs = {ref.id: ref for ref in refs}

        described: dict[str, MetadataWorkbook] = {}
        availability = await self._metadata.availability()
        if availability.available:
            described = {item.luid: item for item in await self._metadata.workbooks()}
        else:
            logger.warning(
                "discovering %s without the Metadata API: %s",
                self._config.site_label,
                availability.detail,
            )
        self._described = described

        site = self._config.site or (
            self._rest.session.site_content_url if self._rest.session else ""
        )
        logger.info(
            "discovered %d workbooks on %s (%s %s)",
            len(refs),
            self._config.site_label,
            info.deployment.value,
            info.product_version or "cloud",
        )

        for ref in sorted(refs, key=lambda item: (item.project_name, item.name, item.id)):
            if scope.project and ref.project_name != scope.project:
                continue
            metadata = described.get(ref.id)
            yield AssetRef(
                luid=ref.id,
                name=ref.name,
                site=site,
                project=ref.project_name,
                project_path=_project_path(ref, metadata),
                # The *revision* is the identity a re-harvest compares (S1.2.4), and it is
                # resolved lazily in `fetch`: asking every workbook for its revision history
                # during discovery would be one REST call per workbook, which is the thing
                # the Metadata API exists to avoid.
                revision=ref.updated_at or "",
                updated_at=ref.updated_at or (metadata.updated_at if metadata else None),
            )

    async def fetch(self, asset: AssetRef) -> RawAsset:
        """Download the workbook and unpack its XML, recording the revision id.

        The content hash is over the **XML**, not over the download. A `.twbx` zip is not
        byte-stable — the archive records timestamps and orders entries however Tableau felt
        like — so hashing the download would make every re-harvest look like a change, and
        S1.2.4's incremental harvest would download the whole estate every night.
        """
        await self._faults.before_call()

        ref = self._refs.get(asset.luid)
        if ref is None:
            # Enumeration populates the map. A fetch for something never enumerated is
            # either a stale queue or a bug, and downloading it anyway would hide which.
            raise AdapterError(
                f"workbook {asset.luid!r} was not in this adapter's discovery results; "
                f"enumerate the site before fetching from it",
                retryable=False,
            )

        payload = await self._rest.download_workbook(asset.luid)
        archive = extract_workbook_xml(payload, name=asset.name)
        revision = await self._revision_of(asset.luid, ref)

        self._extract_files[asset.luid] = archive.extracts

        properties = {
            "revision": revision,
            "updated_at": ref.updated_at,
            "content_url": ref.content_url,
            "size_mb": ref.size_mb,
            **archive.as_properties(),
        }
        if metadata := self._described.get(asset.luid):
            properties.update(metadata.as_properties())

        logger.debug(
            "fetched %s (%s, revision %s, %d bytes of XML)",
            asset.name,
            "twbx" if archive.packaged else "twb",
            revision,
            len(archive.xml),
        )

        return RawAsset(
            ref=AssetRef(
                luid=asset.luid,
                name=asset.name,
                site=asset.site,
                project=asset.project,
                project_path=asset.project_path,
                revision=revision,
                updated_at=asset.updated_at,
            ),
            content_hash=hashlib.sha256(archive.xml).hexdigest(),
            payload=archive.xml,
            size_bytes=len(archive.xml),
            media_type="application/xml",
        )

    async def sites(self, scope: Scope) -> Sequence[SiteRecord]:
        """S2.2.1's fourth criterion: the version, recorded per site."""
        info = await self._rest.server_info()
        availability = await self._metadata.availability()
        schedules = await self._rest.extract_refresh_schedules()
        self._schedules.update(schedules)
        records = []
        for site in await self._rest.sites():
            content_url = str(site.get("contentUrl", ""))
            if scope.site and content_url != scope.site:
                continue
            records.append(
                SiteRecord(
                    site=content_url,
                    licence_tier=str(site.get("state", "")) or None,
                    user_count=int(site.get("userQuota") or 0) or None,
                    detail={
                        **info.as_dict(),
                        **availability.as_dict(),
                        "site_name": str(site.get("name", "")),
                        "concurrency": self._throttle.state.as_dict(),
                        # S2.2.2: the secrets a client must provision, and the refresh
                        # schedules the Modeller needs. Both are lists an operator can act on
                        # rather than facts discovered when something later fails.
                        "extract_refresh_schedules": schedules,
                        "connection_secrets": self._secret_references,
                        # Why a strategy is unavailable, so an operator asking "why is every
                        # case inconclusive" finds the answer here rather than in a log grep
                        # — and the answer is usually a licence decision nobody has made.
                        "execution": {
                            "strategies": [s.value for s in self._executor.strategies()],
                            "charter": self._charter.version,
                            "timeout_seconds": self._charter.timeout_seconds,
                            **describe(self._extract_reader, self._live_runner),
                        },
                    },
                )
            )
        return records

    # ------------------------------------------------- not this story's, and named

    async def parse(self, raw: RawAsset) -> ParseResult:
        """The datasource graph and the calculations, from the workbook's XML.

        S2.2.2 built the datasource half; S2.3.1 adds the calculations, so a workbook now
        parses to 1.0 unless it contains a construct the grammar genuinely cannot read — at
        which point §4.1.4 holds it and the Parse Quality Queue names the construct.

        **A function of the bytes**, which S2.1.2's parse round-trip check requires: the
        structure comes from ``raw.payload`` and nothing else. Discovery's Metadata API facts
        are used only to *enrich* — a published datasource's LUID, an extract's last refresh
        — and their absence changes what is known, never whether the parse succeeds. An
        adapter whose parse depended on a cache populated by a previous call would produce a
        different fragment when a harvest resumed, which the platform reads as drift.
        """
        structure = read_structure(raw.payload, site=raw.ref.site, name=raw.ref.name)
        sheets = read_sheets(raw.payload, name=raw.ref.name)
        extracts = tuple(str(name) for name in self._extract_files.get(raw.ref.luid, ()))

        result = build(
            raw.ref,
            structure,
            metadata=self._described.get(raw.ref.luid),
            archive_extracts=extracts,
            revision=raw.ref.revision,
            size_bytes=raw.size_bytes,
            grammar=self._grammar,
            sheets=sheets,
        )

        if stripped := structure.embedded_credentials:
            # A finding, not a warning to swallow: the client will have to rotate these, and
            # the target model must not be built assuming the connection authenticates the way
            # the old one did.
            logger.warning(
                "%s embeds credential attributes in its connections (%s); they were stripped "
                "and never stored, and the client will need to rotate them",
                raw.ref.name,
                ", ".join(stripped),
            )

        if sheets.rls.present:
            # A programme-level finding, not a log line to skim past: §10's parity cases run
            # under a service identity that sees everything, so a case derived from a
            # user-filtered sheet compares rows the client's user never sees — and will
            # "pass" while proving nothing.
            logger.warning(
                "%s restricts rows by user (%s); parity cases for it must be derived under "
                "the same restriction or they prove nothing",
                raw.ref.name,
                ", ".join(sheets.rls.functions),
            )

        self._secret_references.update(secret_references(structure))
        return result

    @property
    def secret_references(self) -> dict[str, Any]:
        """Every Key Vault secret name this adapter's connections would need (S2.2.2).

        Accumulated across a harvest and reported on `sites()`, so an operator has a list to
        provision against — rather than discovering a missing secret when the executor first
        tries to run a parity case months later.
        """
        return dict(self._secret_references)

    async def parse_calc(self, expression: str) -> CalcAST:
        """§6.1 ``parseCalc``: one calculation, into a typed AST (S2.3.1).

        Never raises for a calculation's content. A construct the grammar cannot read comes
        back as an UNKNOWN node holding the source verbatim, with its span — S2.3.1's
        "captured verbatim and flagged, never dropped".
        """
        return self._grammar.parse(expression)

    async def execute_case(self, case: ParityCase) -> ResultSet:
        """Execute one parity case against Tableau itself (S2.4.1).

        Never raises for a case that cannot be executed: a timeout, an absent extract reader
        or a charter naming an unavailable strategy all come back **INCONCLUSIVE with a
        reason**. §10.2 is explicit that a timeout "yields INCONCLUSIVE, not FAIL" — none of
        those conditions is evidence that the client's report is wrong, and a FAIL would send
        somebody looking for a bug in a correct report.
        """
        return await self._executor.execute(case, charter=self._charter)

    async def capture_visual(self, case: VisualCase) -> VisualCapture:
        """Render one sheet or dashboard through REST ``queryViewImage`` (S2.4.2).

        Resized to exactly ``case.width`` x ``case.height`` before it is returned — never
        cropped or padded — so §10.6's perceptual comparison is comparing two images of the
        same size rather than scoring a size mismatch as a visual difference.
        """
        return await self._visuals.capture(case)

    async def usage(self, scope: Scope, window_days: int) -> Sequence[UsageRecord]:
        raise UnsupportedCapability("usage", adapter=ADAPTER_NAME)

    async def viewers(self, scope: Scope, window_days: int) -> Sequence[ViewerRecord]:
        raise UnsupportedCapability("viewers", adapter=ADAPTER_NAME)

    async def owners(self, scope: Scope) -> Sequence[OwnershipRecord]:
        raise UnsupportedCapability("ownership", adapter=ADAPTER_NAME)

    # ------------------------------------------------------- conformance (S2.1.2)

    async def set_fault(self, fault: Fault, *, count: int = 1) -> None:
        """Drive the *HTTP transport* into a fault state, not the adapter.

        The whole point of S2.1.2's hook is that the adapter's own handling runs unchanged —
        so the fault is injected at the socket, and the backoff, the re-sign-in and the error
        classification above are the real ones under test.
        """
        await self._faults.set_fault(fault, count=count)

    @property
    def grammar(self) -> TableauGrammar:
        """The calculation grammar this adapter parses with, and what it declares it covers."""
        return self._grammar

    @property
    def throttle(self) -> SiteThrottle:
        return self._throttle

    # ------------------------------------------------------------------ internals

    async def _revision_of(self, luid: str, ref: WorkbookRef) -> str:
        """The newest revision number, or the update timestamp when history is off.

        Revision history is a per-site setting that is frequently disabled. Falling back to
        ``updatedAt`` keeps the incremental harvest working, and the fallback is visible in
        the value — ``rev:7`` and ``updated:2026-01-02T…`` are not mistakable for each other,
        so nobody later reads a timestamp as a revision number.
        """
        if luid in self._revisions:
            return self._revisions[luid]

        revisions = await self._rest.revisions(luid)
        if revisions:
            newest = revisions[0].get("revisionNumber", "")
            revision = f"rev:{newest}"
        else:
            revision = f"updated:{ref.updated_at}" if ref.updated_at else "unknown"
        self._revisions[luid] = revision
        return revision


def _project_path(ref: WorkbookRef, metadata: MetadataWorkbook | None) -> tuple[str, ...]:
    """The project hierarchy, for the Estate Explorer's tree.

    Tableau nests projects and the REST listing gives only the immediate parent's name. The
    Metadata API's ``containerName`` is the top of the chain; where the two differ, both are
    kept, which is a shallower tree than Tableau's and an honest one. The full hierarchy needs
    a projects query, and S2.2.2 is where the datasource and project graph is captured.
    """
    if metadata and metadata.project_name and metadata.project_name != ref.project_name:
        return (metadata.project_name, ref.project_name)
    return (ref.project_name,) if ref.project_name else ()


def _not_yet(method: str, feature: str) -> AdapterError:
    return AdapterError(
        f"the Tableau adapter does not implement {method} yet; {feature} builds it. "
        f"Discovery and fetch (F2.2) are complete, which is why this adapter enumerates and "
        f"downloads but cannot yet be promoted to a tenant.",
        retryable=False,
    )
