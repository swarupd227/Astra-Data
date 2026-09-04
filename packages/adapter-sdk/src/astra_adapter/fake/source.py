"""The fake source the SDK ships with (S2.1.1: "fixtures, a fake source").

**This is not the Tableau adapter**, and it is not a mock either. It is a complete §6.1
implementation over a deterministic in-memory estate: it enumerates, fetches bytes, parses
them into graph fragments shaped like the specification's §3.4 worked example, parses
calculations with a small real grammar, reports usage and ownership, executes parity cases
and captures a visual. Everything the conformance suite checks, it can be checked against.

That completeness is the point. A conformance suite that has only ever run against nothing
is an assertion about a suite, not a test of one — and an interface whose only implementation
is the one it was extracted from has not been shown to be implementable twice. The fake is
the second implementation, and it is what `astra-adapter conformance --adapter fake` runs.

Being deterministic is the other point: the same site definition produces byte-identical
fragments, so a re-harvest is genuinely unchanged and the platform's idempotency path is
exercised rather than simulated.
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import json
from collections.abc import AsyncIterator, Sequence
from dataclasses import asdict, dataclass, field
from typing import Any

from ..calc import CalcAST
from ..contract import (
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
    Unrecognised,
    UnsupportedCapability,
    UsageKind,
    UsageRecord,
    ViewerRecord,
)
from ..faults import Fault, RateLimited
from ..proof import (
    Column,
    ColumnRole,
    ExecutionOutcome,
    ExecutionStrategy,
    ParityCase,
    ResultSet,
    VisualCapture,
    VisualCase,
)
from .grammar import FAKE_GRAMMAR, parse_calc


@dataclass(slots=True)
class FixtureWorkbook:
    name: str
    luid: str
    project: str
    revision: str = "1"
    updated_at: str = "2027-01-01T00:00:00.000Z"
    """When the source last saw it change. Tableau's Metadata API reports this per
    workbook; an incremental harvest reads it from the enumeration and skips the download
    when it has not moved (S1.2.4)."""

    sheets: int = 3
    dashboards: int = 1
    datasources: int = 2
    fields: int = 6
    calculations: int = 3
    parameters: int = 1
    filters: int = 1
    actions: int = 1
    views_90d: int = 0
    distinct_viewers_90d: int = 0
    owner_upn: str = "owner@client.example"
    #: Who viewed it, and how often. Empty when the source cannot report per-viewer usage.
    viewers: tuple[tuple[str, int], ...] = ()
    #: Constructs the grammar cannot read, lowering parse quality (spec §4.1.4).
    unrecognised: tuple[str, ...] = ()
    #: Raise on fetch or parse, so failure isolation can be tested.
    fails_on: str | None = None


@dataclass(slots=True)
class FixtureSite:
    name: str
    workbooks: list[FixtureWorkbook] = field(default_factory=list)
    projects: list[str] = field(default_factory=list)
    licence_tier: str | None = "User-based"
    user_count: int | None = None


class FixtureSourceAdapter:
    """A deterministic in-memory source, satisfying the §6.1 contract."""

    def __init__(
        self,
        sites: Sequence[FixtureSite],
        *,
        capabilities: Capabilities | None = None,
        name: str = "fixture",
        grammar_version: str = "fixture-1",
    ) -> None:
        self._sites = {site.name: site for site in sites}
        self._name = name
        self.grammar_version = grammar_version
        """Settable, so a test can do what a grammar extension does (S1.2.2/S1.2.4)."""

        self._capabilities = capabilities or Capabilities(
            live_query=False, extract_read=True, usage=True, ownership=True, screenshot=False
        )
        self.fetches = 0
        self.parses = 0

        # Conformance fault injection (S2.1.2). A real adapter implements this by making its
        # *source client* return the stated condition; the fake has no client, so the
        # condition is raised where a client would have raised it. What is under test either
        # way is the adapter's handling, which is why the backoff below is real.
        self._fault = Fault.NONE
        self._fault_calls = 0
        self.throttle_waits = 0
        """How many times this adapter has backed off. Read by the SDK's own tests to check
        that the backoff happened rather than that the call merely succeeded."""

    # ------------------------------------------------------------------ contract

    def manifest(self) -> AdapterManifest:
        return AdapterManifest(
            name=self._name,
            version="0.1.0",
            grammar_version=self.grammar_version,
            interface_version=INTERFACE_VERSION,
            capabilities=self._capabilities,
        )

    async def enumerate(self, scope: Scope) -> AsyncIterator[AssetRef]:
        for site in self._select_sites(scope):
            for workbook in site.workbooks:
                if scope.project and workbook.project != scope.project:
                    continue
                yield AssetRef(
                    luid=workbook.luid,
                    name=workbook.name,
                    site=site.name,
                    project=workbook.project,
                    project_path=(workbook.project,),
                    revision=workbook.revision,
                    updated_at=workbook.updated_at,
                )

    async def fetch(self, asset: AssetRef) -> RawAsset:
        await self._source_call()
        workbook = self._workbook(asset)
        if workbook.fails_on == "fetch":
            raise AdapterError(f"could not download workbook '{asset.name}'")
        self.fetches += 1
        payload = _canonical(workbook).encode()
        return RawAsset(
            ref=asset,
            content_hash=hashlib.sha256(payload).hexdigest(),
            payload=payload,
            size_bytes=len(payload),
            media_type="application/json",
        )

    async def parse(self, raw: RawAsset) -> ParseResult:
        # From the bytes, not from a remembered object: a real adapter parses what it
        # fetched, and an adapter that reached back into its own memory would pass every
        # test in process and fail the first one over the RPC.
        workbook = _from_canonical(raw.payload, self._workbook(raw.ref))
        if workbook.fails_on == "parse":
            raise AdapterError(f"could not parse workbook '{raw.ref.name}'")
        self.parses += 1
        return _parse(raw.ref, workbook)

    async def usage(self, scope: Scope, window_days: int) -> Sequence[UsageRecord]:
        """Usage per workbook and per view (S1.2.3).

        The per-view figures are split deterministically across the workbook's sheets and
        dashboards so they sum to the workbook total, which is what a real source's
        aggregates do and what makes the two figures worth storing separately.
        """
        if not self._capabilities.usage:
            return []
        records: list[UsageRecord] = []
        for site in self._select_sites(scope):
            for workbook in site.workbooks:
                if scope.project and workbook.project != scope.project:
                    continue
                last_view = "2027-01-10T09:00:00.000Z" if workbook.views_90d else None
                records.append(
                    UsageRecord(
                        asset_luid=workbook.luid,
                        views=workbook.views_90d,
                        distinct_viewers=workbook.distinct_viewers_90d,
                        last_view=last_view,
                        kind=UsageKind.WORKBOOK,
                    )
                )
                records.extend(_view_usage(workbook, last_view))
        return records

    async def viewers(self, scope: Scope, window_days: int) -> Sequence[ViewerRecord]:
        if not self._capabilities.usage:
            return []
        return [
            ViewerRecord(
                asset_luid=workbook.luid,
                viewer_upn=upn,
                views=views,
                last_view="2027-01-09T17:30:00.000Z",
            )
            for site in self._select_sites(scope)
            for workbook in site.workbooks
            if not scope.project or workbook.project == scope.project
            for upn, views in workbook.viewers
        ]

    async def sites(self, scope: Scope) -> Sequence[SiteRecord]:
        if not self._capabilities.ownership:
            return []
        return [
            SiteRecord(
                site=site.name,
                licence_tier=site.licence_tier,
                user_count=site.user_count
                if site.user_count is not None
                else len(
                    {workbook.owner_upn for workbook in site.workbooks}
                    | {upn for workbook in site.workbooks for upn, _ in workbook.viewers}
                ),
            )
            for site in self._select_sites(scope)
        ]

    async def owners(self, scope: Scope) -> Sequence[OwnershipRecord]:
        if not self._capabilities.ownership:
            return []
        return [
            OwnershipRecord(
                asset_luid=workbook.luid,
                owner_upn=workbook.owner_upn,
                owner_display=workbook.owner_upn.split("@")[0].replace(".", " ").title(),
                licence_tier="Creator",
                site_roles=("Explorer",),
            )
            for site in self._select_sites(scope)
            for workbook in site.workbooks
            if not scope.project or workbook.project == scope.project
        ]

    # --------------------------------------------------------- conformance faults

    #: How many times a throttled call is retried before giving up. Small because the fake
    #: sleeps for real: a schedule tuned for a client's Tableau Server would make the
    #: conformance suite take minutes.
    MAX_THROTTLE_RETRIES = 5

    async def set_fault(self, fault: Fault, *, count: int = 1) -> None:
        """Make the next ``count`` source calls encounter ``fault`` (S2.1.2).

        Replaces rather than queues, and ``Fault.NONE`` clears — the suite has to be able to
        put the source back, or every check after the first would run against a broken one.
        """
        self._fault = fault
        self._fault_calls = 0 if fault is Fault.NONE else count

    async def _source_call(self) -> None:
        """Where a real adapter would call the source, and where its errors come from.

        The throttle branch is the adapter's **own backoff**, not the suite's: §6.2 requires
        backoff on 429, so the thing under test is that this loop exists and converges. It
        is what makes the throttling check a check of an adapter rather than of a mock.
        """
        if self._fault is Fault.NONE or self._fault_calls <= 0:
            return

        if self._fault is Fault.THROTTLE:
            for _ in range(self.MAX_THROTTLE_RETRIES):
                if self._fault_calls <= 0:
                    return
                self._fault_calls -= 1
                self.throttle_waits += 1
                # A real adapter sleeps for Retry-After. Zero here: the property under test
                # is that it backs off and converges, and a suite that slept for the sum of
                # a client's Retry-After headers would take minutes to certify an adapter.
                await asyncio.sleep(0)
            raise RateLimited(
                f"the source is still rate limiting after {self.MAX_THROTTLE_RETRIES} attempts",
                retry_after=30.0,
            )

        self._fault_calls -= 1
        if self._fault is Fault.TRANSIENT:
            raise AdapterError("the connection to the source was reset", retryable=True)
        if self._fault is Fault.PERMANENT:
            raise AdapterError(
                "the workbook could not be read: the downloaded file is not a valid archive",
                retryable=False,
            )
        if self._fault is Fault.UNAUTHORISED:
            raise AdapterError(
                "the source rejected the credential; retrying with the same one will fail "
                "the same way",
                retryable=False,
            )

    # --------------------------------------------------- grammar, proof, visuals

    async def parse_calc(self, expression: str) -> CalcAST:
        """§6.1 ``parseCalc``, backed by the small grammar in ``grammar.py``."""
        return parse_calc(expression, grammar_version=self.grammar_version)

    async def execute_case(self, case: ParityCase) -> ResultSet:
        """A deterministic result set for a parity case (§6.1 ``executeCase``).

        The rows are derived from the case and the workbook, never from a clock or a random
        source — §6.3 checks that three runs of one case agree, and a fake that could not
        satisfy that would make the check untestable.
        """
        if not (self._capabilities.extract_read or self._capabilities.live_query):
            # INCONCLUSIVE rather than an exception: interface 1.1 makes "this deployment
            # cannot execute this case" an *outcome with a reason*, not an error. A raise
            # would be indistinguishable from a broken adapter, and §10.2 is explicit that a
            # case nobody could execute is not evidence that the report is wrong.
            return ResultSet(
                case_id=case.id,
                columns=(),
                rows=(),
                strategy=ExecutionStrategy.EXTRACT_READ,
                interface_version=INTERFACE_VERSION,
                adapter_name=self._name,
                adapter_version="0.1.0",
                grammar_version=self.grammar_version,
                outcome=ExecutionOutcome.INCONCLUSIVE,
                reason=(
                    "this fixture claims neither extract_read nor live_query, so it has no "
                    "way to execute a case"
                ),
            )

        workbook = self._workbook_by_luid(case.workbook_luid)
        strategy = (
            ExecutionStrategy.EXTRACT_READ
            if self._capabilities.extract_read
            else ExecutionStrategy.LIVE_REPLAY
        )
        grain = case.grain or ("Desk",)
        measures = case.measures or ("Amount",)
        # Typed and ordered (§10.2, interface 1.1). The role is what §10.3's diff matches rows
        # on versus what it compares under tolerance, so a result set that did not carry it
        # would make the Proof Engine guess.
        columns = (
            *(Column(name, ColumnRole.DIMENSION, "string") for name in grain),
            *(Column(name, ColumnRole.MEASURE, "real") for name in measures),
        )

        rows: list[tuple[Any, ...]] = []
        for index in range(min(workbook.sheets * 2, case.row_limit)):
            dimensions: tuple[Any, ...] = tuple(f"{name}-{index}" for name in grain)
            # A stable arithmetic function of the case and the row, so the same case gives
            # the same numbers in every process and every run.
            values: tuple[Any, ...] = tuple(
                float((index + 1) * (position + 1) * len(case.id) % 997)
                for position in range(len(measures))
            )
            if index == 0:
                # One null in a known place. S2.4.1 requires nulls preserved as nulls, and a
                # fake whose rows were all populated could not show that they are — the
                # charter's null policy (§4.4) is the thing that would silently break.
                values = (None, *values[1:])
            rows.append((*dimensions, *values))

        return ResultSet(
            case_id=case.id,
            columns=columns,
            rows=tuple(rows),
            strategy=strategy,
            interface_version=INTERFACE_VERSION,
            adapter_name=self._name,
            adapter_version="0.1.0",
            grammar_version=self.grammar_version,
            truncated=len(rows) >= case.row_limit,
            detail={"source": "fake", "workbook": workbook.name},
        )

    async def capture_visual(self, case: VisualCase) -> VisualCapture:
        """§6.2 Screenshot. Refused unless claimed, because §10.6's comparison is only
        meaningful against an image the source actually rendered."""
        if not self._capabilities.screenshot:
            raise UnsupportedCapability("capture_visual", adapter=self._name)
        # Raises if the workbook is not in the estate: a capture of a view that does not
        # exist is a failure, not a blank image.
        self._workbook_by_luid(case.workbook_luid)
        # A one-pixel PNG, held as base64 so the source file stays text. The fake has
        # nothing to render, and inventing a picture would make §10.6's perceptual
        # comparison score noise as similarity.
        image = base64.b64decode(
            "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAACklEQVR4nGMAAQAABQABDQottAAAAABJRU5ErkJggg=="
        )
        return VisualCapture(
            case_id=case.id,
            image=image,
            width=case.width,
            height=case.height,
            interface_version=INTERFACE_VERSION,
            adapter_name=self._name,
            adapter_version="0.1.0",
            captured_at=None,
            media_type="image/png",
        )

    @property
    def grammar(self) -> object:
        """What this adapter's grammar claims to read."""
        return FAKE_GRAMMAR

    def _workbook_by_luid(self, luid: str) -> FixtureWorkbook:
        for site in self._sites.values():
            for workbook in site.workbooks:
                if workbook.luid == luid:
                    return workbook
        raise AdapterError(f"no workbook with luid '{luid}'")

    # ----------------------------------------------------------------- internals

    def _select_sites(self, scope: Scope) -> list[FixtureSite]:
        if scope.site:
            site = self._sites.get(scope.site)
            if site is None:
                raise AdapterError(f"no site named '{scope.site}'")
            return [site]
        return list(self._sites.values())

    def _workbook(self, asset: AssetRef) -> FixtureWorkbook:
        site = self._sites[asset.site]
        for workbook in site.workbooks:
            if workbook.luid == asset.luid:
                return workbook
        raise AdapterError(f"no workbook with luid '{asset.luid}'")


#: Not part of the content. ``revision`` and ``updated_at`` are what the source says
#: *about* the workbook, and a source can move either without the workbook changing — a
#: re-publish of the same file, a metadata touch. Folding them into the hash would make
#: every such event look like a content change, and the platform would re-parse and raise
#: drift for a workbook nobody edited.
_NOT_CONTENT = frozenset({"fails_on", "revision", "updated_at"})


def _canonical(workbook: FixtureWorkbook) -> str:
    """A stable serialisation, so the content hash changes only when the workbook does."""
    return json.dumps(
        {key: value for key, value in sorted(asdict(workbook).items()) if key not in _NOT_CONTENT},
        sort_keys=True,
        default=list,
    )


def _from_canonical(payload: bytes, known: FixtureWorkbook) -> FixtureWorkbook:
    """Rebuild the workbook from the fetched bytes.

    ``_NOT_CONTENT`` fields are deliberately outside the hash and therefore outside the
    payload, so they come from the enumeration record the parse was asked about — which is
    exactly how a real adapter works: a .twbx does not contain the server's revision number
    either.
    """
    fields = json.loads(payload.decode())
    fields["viewers"] = tuple(tuple(pair) for pair in fields.get("viewers") or ())
    fields["unrecognised"] = tuple(fields.get("unrecognised") or ())
    return FixtureWorkbook(
        **fields,
        revision=known.revision,
        updated_at=known.updated_at,
        fails_on=known.fails_on,
    )


def _view_usage(workbook: FixtureWorkbook, last_view: str | None) -> list[UsageRecord]:
    """Split a workbook's usage across its views, deterministically and without loss."""
    names = [f"{workbook.name} sheet {index}" for index in range(workbook.sheets)]
    names += [f"{workbook.name} dashboard {index}" for index in range(workbook.dashboards)]
    if not names or not workbook.views_90d:
        return []

    per_view, remainder = divmod(workbook.views_90d, len(names))
    records = []
    for position, name in enumerate(names):
        views = per_view + (1 if position < remainder else 0)
        records.append(
            UsageRecord(
                asset_luid=f"{workbook.luid}::{name}",
                views=views,
                distinct_viewers=min(views, workbook.distinct_viewers_90d),
                last_view=last_view if views else None,
                kind=UsageKind.VIEW,
                workbook_luid=workbook.luid,
                view_name=name,
            )
        )
    return records


def _parse(ref: AssetRef, workbook: FixtureWorkbook) -> ParseResult:
    """Everything S1.2.1 requires the Harvester to record, in graph shape."""
    nodes: list[NodeFragment] = []
    edges: list[EdgeFragment] = []

    site_key = f"site:{ref.site}"
    project_key = f"project:{ref.site}/{workbook.project}"
    workbook_key = f"workbook:{ref.luid}"

    nodes.append(NodeFragment(site_key, "Site", {"luid": f"site-{ref.site}", "name": ref.site}))
    nodes.append(
        NodeFragment(
            project_key,
            "Project",
            {"luid": f"proj-{ref.site}-{workbook.project}", "name": workbook.project},
        )
    )
    nodes.append(
        NodeFragment(
            workbook_key,
            "Workbook",
            {
                "luid": ref.luid,
                "name": workbook.name,
                "revision": workbook.revision,
                "extract_flag": False,
                "size": len(_canonical(workbook)),
            },
        )
    )
    edges.append(EdgeFragment("CONTAINS", site_key, project_key))
    edges.append(EdgeFragment("CONTAINS", project_key, workbook_key))

    sheet_keys: list[str] = []
    for index in range(workbook.sheets):
        key = f"{workbook_key}/worksheet:{index}"
        sheet_keys.append(key)
        nodes.append(
            NodeFragment(
                key,
                "Worksheet",
                {
                    "name": f"{workbook.name} sheet {index}",
                    "mark_type": "bar",
                    "rows_shelf": ["Desk"],
                    "cols_shelf": ["Date"],
                    "marks_shelf": ["Colour"],
                },
            )
        )
        edges.append(EdgeFragment("CONTAINS", workbook_key, key))

    for index in range(workbook.dashboards):
        key = f"{workbook_key}/dashboard:{index}"
        nodes.append(
            NodeFragment(
                key,
                "Dashboard",
                {
                    "name": f"{workbook.name} dashboard {index}",
                    "layout_json": {"zones": [{"sheet": k} for k in sheet_keys]},
                    "contained_sheets": [
                        f"{workbook.name} sheet {s}" for s in range(workbook.sheets)
                    ],
                },
            )
        )
        edges.append(EdgeFragment("CONTAINS", workbook_key, key))

    field_keys: list[str] = []
    for index in range(workbook.datasources):
        datasource_key = f"{workbook_key}/datasource:{index}"
        connection_key = f"{datasource_key}/connection"
        table_key = f"{datasource_key}/table"
        nodes.append(
            NodeFragment(
                datasource_key,
                "Datasource",
                {
                    "name": f"Source {index}",
                    "type": "published" if index == 0 else "embedded",
                    "luid": f"ds-{ref.luid}-{index}" if index == 0 else None,
                    "extract_flag": False,
                },
            )
        )
        nodes.append(
            NodeFragment(
                connection_key,
                "Connection",
                {
                    "class": "postgres",
                    "server": "warehouse.internal",
                    "db": "risk",
                    "schema": "public",
                    "auth_mode": "service_account",
                },
            )
        )
        nodes.append(
            NodeFragment(
                table_key,
                "Table",
                {"name": f"positions_{index}", "schema": "public", "row_estimate": 100_000},
            )
        )
        edges.append(EdgeFragment("CONNECTS_TO", datasource_key, connection_key))
        edges.append(EdgeFragment("CONNECTS_TO", connection_key, table_key))
        for sheet_key in sheet_keys:
            edges.append(EdgeFragment("USES_DATASOURCE", sheet_key, datasource_key))

        for field_index in range(workbook.fields):
            field_key = f"{datasource_key}/field:{field_index}"
            field_keys.append(field_key)
            nodes.append(
                NodeFragment(
                    field_key,
                    "Field",
                    {
                        "name": f"Field {field_index}",
                        "datatype": "real" if field_index % 2 else "string",
                        "role": "measure" if field_index % 2 else "dimension",
                        "hidden": False,
                    },
                )
            )
            edges.append(EdgeFragment("HAS_FIELD", table_key, field_key))

    parameter_keys: list[str] = []
    for index in range(workbook.parameters):
        key = f"{workbook_key}/parameter:{index}"
        parameter_keys.append(key)
        nodes.append(
            NodeFragment(
                key,
                "Parameter",
                {
                    "name": f"As Of {index}",
                    "datatype": "date",
                    "domain": "range",
                    "default": "2027-01-01",
                },
            )
        )

    for index in range(workbook.calculations):
        key = f"{workbook_key}/calc:{index}"
        nodes.append(
            NodeFragment(
                key,
                "CalculatedField",
                {
                    "name": f"Calc {index}",
                    "formula": "SUM([Margin]) / SUM([Revenue])",
                    "formula_ast": {
                        "op": "DIV",
                        "args": [
                            {"fn": "SUM", "arg": {"field": "Margin"}},
                            {"fn": "SUM", "arg": {"field": "Revenue"}},
                        ],
                    },
                    "table_calc_flag": False,
                },
            )
        )
        if sheet_keys:
            edges.append(EdgeFragment("ENCODES", sheet_keys[0], key, {"shelf": "rows"}))
        if field_keys:
            edges.append(
                EdgeFragment("DEPENDS_ON", key, field_keys[0], {"position_in_ast": "args[0]"})
            )
        for parameter_key in parameter_keys:
            edges.append(
                EdgeFragment("DEPENDS_ON", key, parameter_key, {"position_in_ast": "args[1]"})
            )

    for index in range(workbook.filters):
        key = f"{workbook_key}/filter:{index}"
        nodes.append(
            NodeFragment(
                key,
                "Filter",
                {
                    "field_ref": f"Field {index}",
                    "type": "categorical",
                    "values": ["Bonds", "Equities"],
                    "context_flag": False,
                },
            )
        )
        for sheet_key in sheet_keys:
            edges.append(EdgeFragment("FILTERED_BY", sheet_key, key))

    for index in range(workbook.actions):
        key = f"{workbook_key}/action:{index}"
        nodes.append(
            NodeFragment(
                key,
                "Action",
                {
                    "type": "filter",
                    "source_sheets": [f"{workbook.name} sheet 0"],
                    "target_sheets": [f"{workbook.name} sheet 1"] if workbook.sheets > 1 else None,
                },
            )
        )

    unrecognised = [
        Unrecognised(construct=text, location=f"{workbook.name}/calc", detail="grammar gap")
        for text in workbook.unrecognised
    ]
    total = len(nodes) + len(unrecognised)
    recognised = len(nodes)
    return ParseResult(
        nodes=nodes,
        edges=edges,
        parse_quality=recognised / total if total else 1.0,
        unrecognised=unrecognised,
        constructs_total=total,
        constructs_recognised=recognised,
    )


#: Constructs the fixture grammar deliberately cannot read, for the demo estate.
#:
#: Opt-in rather than default: most tests build a site and assert an exact held count, and a
#: builder that quietly made a fifth of them unparseable would make every one of those
#: assertions about this constant instead of about the thing under test. The local stack
#: asks for them, so the Parse Quality Queue has something to show; the suites do not.
#:
#: The texts are real Tableau constructs with no direct DAX equivalent, and the spread is
#: uneven on purpose — one gap blocking many workbooks and one blocking a few is what makes
#: the "releases N workbooks" ordering mean anything.
FIXTURE_GRAMMAR_GAPS: tuple[tuple[str, int], ...] = (
    ("RAWSQL_INT(<expr>)", 3),
    ("SCRIPT_REAL('...', <expr>)", 7),
    ("WINDOW_SUM(<expr>, <start>, <end>)", 11),
)


def build_site(
    name: str,
    workbooks: int,
    *,
    project_count: int = 3,
    revision: str = "1",
    grammar_gaps: bool = False,
    **workbook_kwargs: Any,
) -> FixtureSite:
    """A site of ``workbooks`` workbooks spread over ``project_count`` projects."""
    projects = [f"Project {index}" for index in range(project_count)]
    return FixtureSite(
        name=name,
        projects=projects,
        workbooks=[
            FixtureWorkbook(
                name=f"Workbook {index}",
                luid=f"{name}-wb-{index:05d}",
                project=projects[index % project_count],
                revision=revision,
                views_90d=index * 3,
                distinct_viewers_90d=index % 17,
                viewers=(
                    (f"viewer{index % 4}@client.example", index * 2),
                    ("owner@client.example", index),
                )
                if index * 3
                else (),
                # Every third workbook trips the first gap, every seventh the second, and
                # so on — so some workbooks hold one construct and a few hold several, which
                # is what makes "fixing this releases N" a number worth ordering on.
                unrecognised=(
                    tuple(text for text, every in FIXTURE_GRAMMAR_GAPS if index % every == 0)
                    if grammar_gaps
                    else ()
                ),
                **workbook_kwargs,
            )
            for index in range(workbooks)
        ],
    )
