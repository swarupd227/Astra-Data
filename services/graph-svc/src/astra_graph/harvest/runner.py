"""The Harvester.

Specification §8.4: runs the adapter's enumerate → fetch → parse loop for a scope, writes
graph fragments transactionally per workbook, records parse quality and unrecognised
constructs, and pulls usage and ownership. Deterministic; no model calls.

S1.2.1's four criteria and where each lives:

* *started from the API with site credentials from Key Vault* — the caller names a
  credential, never sends one; ``credentials.py`` resolves it.
* *progress per project with counts queued, parsed, failed* — enumeration completes first
  so the queued figure is real rather than a guess, then work proceeds per project.
* *failures do not stop the run and are listed with the error* — a workbook's failure is
  caught at its own stage and recorded; the loop continues.
* *re-harvest of an unchanged workbook is a no-op recorded as skipped_unchanged* — the
  content hash from the adapter's fetch is compared to the last one recorded for that
  workbook.

S1.2.4 adds a second mode. A *full* run fetches everything and compares content hashes;
an *incremental* run asks the enumeration when each workbook last changed and never
downloads the ones that have not. Over a long programme that is the difference between a
nightly run costing a thousand downloads and costing four. It also watches for the case
that makes a re-parse dangerous rather than routine: a workbook that moved underneath a
Migration Unit somebody is already building, which raises ``estate.source.drift``.

Concurrency is per workbook and bounded. Each workbook is written in one transaction, so
a partially parsed workbook never reaches the graph (spec §8.4).
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import statistics
from collections.abc import Sequence
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from typing import Any

from ..adapters.contract import (
    AdapterError,
    AdapterManifest,
    AssetRef,
    NodeFragment,
    OwnershipRecord,
    ParseResult,
    Scope,
    SiteRecord,
    SourceAdapter,
    UsageKind,
    UsageRecord,
    ViewerRecord,
)
from ..credentials import CredentialProvider
from ..directory import DirectoryResolver, NullDirectoryResolver
from ..events import source_drift
from ..ids import new_ulid
from ..migration_units import (
    MigrationUnitError,
    MigrationUnitRegistry,
    NullMigrationUnitRegistry,
)
from ..principal import Principal
from ..writes import EdgeWrite, GraphWriter, NodeWrite
from .identity import derive_id
from .model import (
    HarvestFailure,
    HarvestMode,
    HarvestProgress,
    HarvestState,
    ProjectProgress,
    WorkbookOutcome,
    WorkbookResult,
)
from .promotion import PromotionGate, UngatedPromotions
from .quality import ParseQualityStore, score
from .store import HarvestStore, WorkbookState

logger = logging.getLogger(__name__)

#: Spec §8.4 targets 500 workbooks per hour per site worker. Concurrency is per workbook;
#: the ceiling is the source's rate limit, not ours (spec §6.2: adaptive concurrency per
#: site, backoff on 429).
DEFAULT_CONCURRENCY = 8

#: Spec §4.1.4: below this a workbook is held for review rather than advancing.
DEFAULT_PARSE_QUALITY_THRESHOLD = 0.98

#: Spec §6.2 defaults the usage window to 90 days; S1.2.1 asks for views over that window.
DEFAULT_USAGE_WINDOW_DAYS = 90


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")


@dataclass(frozen=True, slots=True)
class HarvestRequest:
    scope: Scope
    credential_reference: str
    mode: HarvestMode = HarvestMode.FULL
    """FULL by default: an operator asking for a harvest by hand almost always means
    "look at everything". Schedules ask for INCREMENTAL (S1.2.4)."""

    schedule_id: str | None = None
    concurrency: int = DEFAULT_CONCURRENCY
    parse_quality_threshold: float = DEFAULT_PARSE_QUALITY_THRESHOLD
    usage_window_days: int = DEFAULT_USAGE_WINDOW_DAYS


class Harvester:
    """Drives one source adapter over one scope."""

    def __init__(
        self,
        *,
        adapter: SourceAdapter,
        writer: GraphWriter,
        store: HarvestStore,
        credentials: CredentialProvider,
        graph_name: str = "astra_estate",
        quality: ParseQualityStore | None = None,
        directory: DirectoryResolver | None = None,
        migration_units: MigrationUnitRegistry | None = None,
        promotions: PromotionGate | None = None,
    ) -> None:
        self._adapter = adapter
        self._writer = writer
        self._store = store
        self._credentials = credentials
        self._graph = graph_name
        self._quality = quality
        self._directory = directory or NullDirectoryResolver()
        self._migration_units = migration_units or NullMigrationUnitRegistry()
        self._promotions = promotions or UngatedPromotions()

    def manifest(self) -> AdapterManifest:
        """Which adapter this Harvester drives. Platform Health reports it (S1.2.4)."""
        return self._adapter.manifest()

    # ------------------------------------------------------------------- the run

    async def run(
        self, request: HarvestRequest, *, principal: Principal, harvest_id: str | None = None
    ) -> HarvestProgress:
        """Harvest a scope. Returns the final progress record."""
        manifest = self._adapter.manifest()
        # S2.1.1 criterion 4: "The interface version is recorded on every harvest". Checked
        # rather than assumed — an adapter with a blank one would be recorded silently, and
        # the record's whole purpose is to let a harvest be read months later against the
        # contract that produced it. A versioned interface whose version is blank is not one.
        if not manifest.interface_version.strip():
            raise AdapterError(
                f"adapter {manifest.name!r} reports no interface version. §6.1 packages an "
                f"adapter as a versioned worker image, and every harvest records which "
                f"version produced it.",
                retryable=False,
            )
        # S2.1.2 criterion 3, enforced where it bites: a harvest is the moment an adapter
        # first touches a client's estate. Checked before the run record is created, so a
        # refused harvest is a refusal rather than a run that failed — an operator reading
        # the harvest list should not have to distinguish "the adapter was not allowed" from
        # "the adapter broke".
        await self._promotions.require_promoted(manifest)

        harvest_id = harvest_id or new_ulid()

        progress = HarvestProgress(
            id=harvest_id,
            state=HarvestState.RUNNING,
            scope={"site": request.scope.site, "project": request.scope.project},
            adapter={
                "name": manifest.name,
                "version": manifest.version,
                "grammar_version": manifest.grammar_version,
                "interface_version": manifest.interface_version,
                "capabilities": asdict(manifest.capabilities),
            },
            principal=principal.value,
            mode=request.mode,
            schedule_id=request.schedule_id,
            started_at=_now(),
        )
        await self._store.create(progress)

        # Shared ancestors — a Site, a Project — appear in every workbook's parse, so
        # without this several coroutines upsert the same vertex at once. See _Seen.
        seen = _Seen()

        try:
            # Resolving the credential is the first thing that can fail, and failing here
            # means the run never started rather than half-ran.
            credential = await self._credentials.resolve(request.credential_reference)
            logger.info(
                "harvest %s starting: %s using %s",
                harvest_id,
                request.scope.describe(),
                credential,
            )

            refs = await self._enumerate(request.scope)
            progress.queued = len(refs)
            progress.projects = _project_progress(refs)
            await self._store.update(progress)

            context = await self._context(request, manifest)
            results = await self._harvest_all(
                refs, request, principal, context, seen, harvest_id
            )

            # S1.2.1: failures are listed with the error, so they outlive the run's
            # counters and can be worked through afterwards.
            for result in results:
                if result.failure is not None:
                    await self._store.record_failure(harvest_id, result.failure)

            _apply(progress, results)
            qualities = [r.parse_quality for r in results if r.parse_quality is not None]
            progress.parse_quality_p50 = (
                round(statistics.median(qualities), 4) if qualities else None
            )
            progress.state = HarvestState.COMPLETED
        except Exception as exc:
            logger.exception("harvest %s failed", harvest_id)
            progress.state = HarvestState.FAILED
            progress.error = f"{type(exc).__name__}: {exc}"
        finally:
            progress.finished_at = _now()
            await self._store.update(progress)

        logger.info(
            "harvest %s (%s) %s: %s parsed, %s unchanged, %s not modified at source, "
            "%s held, %s failed, %s drifted of %s",
            harvest_id,
            progress.mode.value,
            progress.state.value,
            progress.parsed,
            progress.skipped_unchanged,
            progress.skipped_not_modified,
            progress.held,
            progress.failed,
            progress.drifted,
            progress.queued,
        )
        return progress

    async def _enumerate(self, scope: Scope) -> list[AssetRef]:
        """Enumerate the whole scope before fetching anything.

        The queued count has to be a fact before work starts, or per-project progress
        would climb towards a total that keeps moving.
        """
        refs: list[AssetRef] = []
        async for ref in self._adapter.enumerate(scope):
            refs.append(ref)
        return refs

    async def _context(self, request: HarvestRequest, manifest: Any) -> _Context:
        """Usage, viewers, ownership and site facts for the scope, fetched once.

        All of it is capability-gated. A site without the Metadata API cannot supply
        usage or ownership (backlog §7.1), and that is recorded as an absent capability
        rather than as zero views — the two mean different things to a programme manager
        ordering waves by business impact.
        """
        context = _Context()

        if manifest.capabilities.usage:
            with contextlib.suppress(AdapterError):
                for record in await self._adapter.usage(
                    request.scope, request.usage_window_days
                ):
                    if record.kind is UsageKind.VIEW and record.workbook_luid:
                        context.views.setdefault(record.workbook_luid, {})[
                            record.view_name or record.asset_luid
                        ] = record
                    else:
                        context.workbooks[record.asset_luid] = record

            with contextlib.suppress(AdapterError, AttributeError):
                for viewer in await self._adapter.viewers(
                    request.scope, request.usage_window_days
                ):
                    context.viewers.setdefault(viewer.asset_luid, []).append(viewer)

        if manifest.capabilities.ownership:
            with contextlib.suppress(AdapterError):
                context.owners = {
                    record.asset_luid: record
                    for record in await self._adapter.owners(request.scope)
                }
            with contextlib.suppress(AdapterError, AttributeError):
                context.sites = {
                    record.site: record for record in await self._adapter.sites(request.scope)
                }

        await self._resolve_people(context)
        return context

    async def _resolve_people(self, context: _Context) -> None:
        """Map every person the source named onto the directory, where a match exists.

        Done once per run rather than per workbook: the same owner holds many workbooks,
        and a directory is a network call. An identity that does not resolve is recorded
        as unresolved rather than dropped — a workbook whose owner is unknown is a
        workbook nobody can be sent a gate request for (spec §6.2, story S1.2.3).
        """
        people = {record.owner_upn for record in context.owners.values()}
        people |= {
            viewer.viewer_upn for viewers in context.viewers.values() for viewer in viewers
        }
        if not people:
            return
        with contextlib.suppress(Exception):
            context.directory = await self._directory.resolve_many(sorted(people))
        resolved = len(context.directory)
        logger.info(
            "harvest: %s of %s source identities resolved against the directory",
            resolved,
            len(people),
        )

    async def _harvest_all(
        self,
        refs: Sequence[AssetRef],
        request: HarvestRequest,
        principal: Principal,
        context: _Context,
        seen: _Seen,
        harvest_id: str,
    ) -> list[WorkbookResult]:
        semaphore = asyncio.Semaphore(max(1, request.concurrency))

        async def one(ref: AssetRef) -> WorkbookResult:
            async with semaphore:
                return await self._harvest_workbook(
                    ref, request, principal, context, seen, harvest_id
                )

        return list(await asyncio.gather(*(one(ref) for ref in refs)))

    # -------------------------------------------------------------- one workbook

    async def _harvest_workbook(
        self,
        ref: AssetRef,
        request: HarvestRequest,
        principal: Principal,
        context: _Context,
        seen: _Seen,
        harvest_id: str,
    ) -> WorkbookResult:
        """Fetch, parse and write one workbook.

        Every failure is caught and attributed to a stage. S1.2.1: failures do not stop
        the run.
        """
        grammar_version = self._adapter.manifest().grammar_version
        previous = await self._store.workbook_state(self._graph, ref.site, ref.luid)

        if request.mode is HarvestMode.INCREMENTAL and _not_modified(
            ref, previous, grammar_version
        ):
            # S1.2.4: the whole saving. Nothing is fetched, so the run costs one
            # enumeration for the workbooks that did not move.
            return WorkbookResult(
                ref_luid=ref.luid,
                project=ref.project,
                outcome=WorkbookOutcome.SKIPPED_NOT_MODIFIED,
                parse_quality=previous.parse_quality if previous else None,
            )

        try:
            raw = await self._adapter.fetch(ref)
        except Exception as exc:
            return _failed(ref, "fetch", exc)

        if (
            previous is not None
            and previous.content_hash == raw.content_hash
            and _same_grammar(previous, grammar_version)
        ):
            # Spec §8.4: a re-run on an unchanged workbook is a no-op. Reached when the
            # source has no updatedAt to offer, or reported one that moved without the
            # content changing — a metadata touch, a re-publish of the same file.
            #
            # The grammar clause is not redundant with the one in _not_modified: a full
            # run never consults that, and an incremental run that got past it because the
            # timestamp moved would otherwise stop here. Without it the same bytes under a
            # new grammar are never re-parsed, and a workbook held by S1.2.2 stays held
            # however far the grammar is extended.
            await self._store.record_workbook(
                self._graph, ref, raw.content_hash, outcome=WorkbookOutcome.SKIPPED_UNCHANGED
            )
            return WorkbookResult(
                ref_luid=ref.luid,
                project=ref.project,
                outcome=WorkbookOutcome.SKIPPED_UNCHANGED,
                parse_quality=previous.parse_quality,
            )

        try:
            parsed = await self._adapter.parse(raw)
        except Exception as exc:
            return _failed(ref, "parse", exc)

        # Constructs are recorded before the score is computed: a construct text an
        # engineer already accepted stays accepted across a re-parse, and that decision is
        # part of this workbook's score (S1.2.2).
        recognised, ignorable, total, quality = await self._score(ref, parsed)

        try:
            nodes, edges = await self._write(ref, parsed, principal, context, seen, quality)
        except Exception as exc:
            return _failed(ref, "write", exc)

        # The source moved under work already in progress. Announced after the write, so
        # "re-prove this" is a request somebody can act on: the new parse is in the graph
        # to prove against. A workbook that changed but failed to parse is a recorded
        # failure instead, and the MU still stands on the last version that did parse.
        drifted = await self._announce_drift(
            ref, parsed, previous, raw.content_hash, harvest_id, principal
        )

        held = quality < request.parse_quality_threshold
        outcome = WorkbookOutcome.HELD_PARSE_QUALITY if held else WorkbookOutcome.PARSED
        await self._store.record_workbook(
            self._graph,
            ref,
            raw.content_hash,
            outcome=outcome,
            parse_quality=quality,
            unrecognised=[asdict(item) for item in parsed.unrecognised],
            recognised=recognised,
            ignorable=ignorable,
            total=total,
            grammar_version=grammar_version,
        )
        return WorkbookResult(
            ref_luid=ref.luid,
            project=ref.project,
            drifted=drifted,
            outcome=outcome,
            parse_quality=quality,
            nodes_written=nodes,
            edges_written=edges,
        )

    async def _announce_drift(
        self,
        ref: AssetRef,
        parsed: ParseResult,
        previous: WorkbookState | None,
        content_hash: str,
        harvest_id: str,
        principal: Principal,
    ) -> bool:
        """Raise ``estate.source.drift`` if this change lands under work in progress.

        S1.2.4. Two conditions, both required: the workbook's content actually changed
        since the platform last recorded it — a first harvest is not drift, and neither is
        a re-publish of identical content — and a Migration Unit over it is past
        HARVESTED, so something has been built from the version that just stopped being
        true.

        The event is the durable half. Marking the MU goes through the registry, which is
        a seam onto a control plane that does not exist yet (E3): when it does, this is
        the call that reaches it. A registry that refuses the mark does not fail the
        harvest — the workbook is correctly in the graph either way, and losing the parse
        because a downstream service was unreachable would be the worse outcome.
        """
        if previous is None or previous.content_hash == content_hash:
            return False

        try:
            unit = await self._migration_units.in_progress(ref.site, ref.luid)
        except MigrationUnitError:
            logger.exception(
                "could not ask whether %s/%s has a migration unit in progress",
                ref.site,
                ref.luid,
            )
            return False
        if unit is None:
            return False

        reason = (
            f"source workbook changed during harvest {harvest_id}: revision "
            f"{previous.revision} -> {ref.revision}"
        )
        marked = False
        try:
            marked = await self._migration_units.mark_for_reproof(
                unit.id, reason=reason, principal=principal.value
            )
        except MigrationUnitError:
            logger.exception("could not mark migration unit %s for re-proof", unit.id)

        await self._writer.append_event(
            source_drift(
                source=self._writer.event_source,
                workbook_node_id=derive_id(ref.site, parsed.workbook_key),
                principal=principal,
                detail={
                    "site": ref.site,
                    "project": ref.project,
                    "workbook_luid": ref.luid,
                    "workbook_name": ref.name,
                    "harvest_id": harvest_id,
                    "previous": {
                        "revision": previous.revision,
                        "content_hash": previous.content_hash,
                        "harvested_at": previous.harvested_at,
                    },
                    "current": {
                        "revision": ref.revision,
                        "content_hash": content_hash,
                        "updated_at": ref.updated_at,
                    },
                    "migration_unit": {"id": unit.id, "state": unit.state},
                    "reproof_requested": marked,
                },
            )
        )
        logger.warning(
            "source drift: %s/%s changed under migration unit %s (%s); re-proof %s",
            ref.site,
            ref.luid,
            unit.id,
            unit.state,
            "requested" if marked else "NOT recorded — no registry accepted the mark",
        )
        return True

    async def _score(
        self, ref: AssetRef, parsed: ParseResult
    ) -> tuple[int, int, int, float]:
        """Record this parse's unrecognised constructs and compute the workbook's score.

        The adapter reports what its grammar read. The *score* also depends on what an
        engineer has since accepted as ignorable, which is platform state, so the two are
        combined here rather than in the adapter (spec §4.1.4, story S1.2.2).
        """
        recognised = parsed.constructs_recognised or len(parsed.nodes)
        total = parsed.constructs_total or (recognised + len(parsed.unrecognised))

        if self._quality is None:
            return recognised, 0, total, parsed.parse_quality

        grammar_version = self._adapter.manifest().grammar_version
        await self._quality.record_constructs(
            self._graph,
            ref.site,
            ref.luid,
            [
                {
                    "construct": item.construct,
                    "sheet": _sheet_of(item.location),
                    "field": _field_of(item.location),
                    "detail": item.detail,
                    "grammar_version": grammar_version,
                }
                for item in parsed.unrecognised
            ],
        )
        stored = await self._quality.constructs_for(self._graph, ref.site, ref.luid)
        ignorable = len([item for item in stored if not item.unrecognised])
        return recognised, ignorable, total, score(recognised, ignorable, total)


    async def _write(
        self,
        ref: AssetRef,
        parsed: ParseResult,
        principal: Principal,
        context: _Context,
        seen: _Seen,
        parse_quality: float | None = None,
    ) -> tuple[int, int]:
        """Write one workbook's fragment.

        Ids are derived from source identity, so a re-harvest of a *changed* workbook
        updates the same nodes rather than duplicating them. That is why this uses the
        upsert path: the second harvest of a workbook whose sheet was renamed must replace
        that sheet's properties, not add a second sheet.
        """
        ids = {node.key: derive_id(ref.site, node.key) for node in parsed.nodes}
        usage = context.workbooks.get(ref.luid)
        view_usage = context.views.get(ref.luid, {})
        ownership = context.owners.get(ref.luid)

        writes: list[NodeWrite] = []
        for node in parsed.nodes:
            is_workbook = node.type == "Workbook"
            properties = _clean(
                node,
                usage if is_workbook else None,
                parse_quality if is_workbook else None,
                # A view's usage is matched by the name the parse gave it, which is the
                # name the source published it under.
                view_usage.get(str(node.properties.get("name")))
                if node.type in _VIEW_TYPES
                else None,
                context.sites.get(ref.site) if node.type == "Site" else None,
            )
            writes.append(NodeWrite(type=node.type, id=ids[node.key], properties=properties))

        owner_id: str | None = None
        if ownership is not None:
            owner_id = derive_id(ref.site, f"user:{ownership.owner_upn}")
            writes.append(
                _user_write(ref.site, ownership.owner_upn, context, ownership=ownership)
            )

        # Everyone who viewed it, so VIEWED_BY can be written from what the source
        # reported rather than inferred from who owns the workbook.
        viewers = context.viewers.get(ref.luid, [])
        for viewer in viewers:
            if viewer.viewer_upn == getattr(ownership, "owner_upn", None):
                continue
            writes.append(_user_write(ref.site, viewer.viewer_upn, context))

        mine, waiting_on = await seen.claim_nodes(writes)
        if mine:
            try:
                await self._writer.upsert_nodes([w for w, _ in mine], principal=principal)
            except BaseException:
                seen.abandon(mine)
                raise
            seen.release(mine)
        # A node this workbook did not write is still needed by the edges below, so wait
        # until whoever claimed it has committed.
        await seen.wait(waiting_on)

        edge_writes = [
            EdgeWrite(
                type=edge.type,
                id=derive_id(ref.site, f"{edge.type}:{edge.from_key}->{edge.to_key}"),
                from_id=ids[edge.from_key],
                to_id=ids[edge.to_key],
                properties=dict(edge.properties),
            )
            for edge in parsed.edges
            if edge.from_key in ids and edge.to_key in ids
        ]
        workbook_id = ids[parsed.workbook_key]
        if owner_id is not None:
            edge_writes.append(
                EdgeWrite(
                    type="OWNED_BY",
                    id=derive_id(ref.site, f"OWNED_BY:{parsed.workbook_key}"),
                    from_id=workbook_id,
                    to_id=owner_id,
                    properties={},
                )
            )

        # One edge per person who actually viewed it. Spec §4.1.2 gives VIEWED_BY a
        # views_90d per (workbook, user) pair, so an aggregate hung off the owner would
        # be a different and untrue statement.
        for viewer in viewers:
            edge_writes.append(
                EdgeWrite(
                    type="VIEWED_BY",
                    id=derive_id(
                        ref.site, f"VIEWED_BY:{parsed.workbook_key}:{viewer.viewer_upn}"
                    ),
                    from_id=workbook_id,
                    to_id=derive_id(ref.site, f"user:{viewer.viewer_upn}"),
                    properties=_strip_nulls(
                        {"views_90d": viewer.views, "last_view": viewer.last_view}
                    ),
                )
            )

        mine_edges, _ = await seen.claim_edges(edge_writes)
        try:
            for edge, _ in mine_edges:
                await self._writer.upsert_edge(edge, principal=principal)
        except BaseException:
            seen.abandon(mine_edges)
            raise
        seen.release(mine_edges)

        return len(mine), len(mine_edges)



class _Seen:
    """What this run has already written, so a shared element is written once.

    A Site and a Project belong to every workbook under them, so every parse carries
    them. Writing them per workbook is both wasteful and unsafe: concurrent upserts of one
    vertex make Apache AGE fail the update.

    A claim is keyed on the element's id *and* its properties, so a genuine change still
    goes through and only an identical repeat is skipped. The claim carries an event that
    the claimant sets once its transaction has committed — a workbook that skipped writing
    a shared node must not write an edge to it until it is actually there.
    """

    def __init__(self) -> None:
        self._entries: dict[str, tuple[str, asyncio.Event]] = {}
        self._lock = asyncio.Lock()

    @staticmethod
    def _fingerprint(properties: Any) -> str:
        return json.dumps(properties, sort_keys=True, default=str)

    async def claim_nodes(
        self, writes: Sequence[NodeWrite]
    ) -> tuple[list[tuple[NodeWrite, asyncio.Event]], list[asyncio.Event]]:
        return await self._claim(writes)

    async def claim_edges(
        self, writes: Sequence[EdgeWrite]
    ) -> tuple[list[tuple[EdgeWrite, asyncio.Event]], list[asyncio.Event]]:
        return await self._claim(writes)

    async def _claim(self, writes: Sequence[Any]) -> tuple[list[Any], list[asyncio.Event]]:
        mine: list[Any] = []
        waiting_on: list[asyncio.Event] = []
        async with self._lock:
            for write in writes:
                if write.id is None:
                    mine.append((write, asyncio.Event()))
                    continue
                fingerprint = self._fingerprint(write.properties)
                entry = self._entries.get(write.id)
                if entry is not None and entry[0] == fingerprint:
                    waiting_on.append(entry[1])
                    continue
                event = asyncio.Event()
                self._entries[write.id] = (fingerprint, event)
                mine.append((write, event))
        return mine, waiting_on

    def release(self, claimed: Sequence[tuple[Any, asyncio.Event]]) -> None:
        """The writes committed; anyone waiting may proceed."""
        for _, event in claimed:
            event.set()

    def abandon(self, claimed: Sequence[tuple[Any, asyncio.Event]]) -> None:
        """The writes failed. Drop the claims so another workbook can try, and wake
        anyone waiting rather than leaving them blocked on a write that never happened."""
        for write, event in claimed:
            if write.id is not None:
                self._entries.pop(write.id, None)
            event.set()

    async def wait(self, events: Sequence[asyncio.Event]) -> None:
        for event in events:
            await event.wait()




#: Node types that correspond to a published view, and so can carry per-view usage.
_VIEW_TYPES = frozenset({"Worksheet", "Dashboard"})


@dataclass(slots=True)
class _Context:
    """What one harvest learned about the scope beyond the workbooks themselves.

    Gathered once per run: the same owner holds many workbooks, and usage, ownership and
    directory lookups are all network calls that do not need repeating per asset.
    """

    workbooks: dict[str, UsageRecord] = field(default_factory=dict)
    views: dict[str, dict[str, UsageRecord]] = field(default_factory=dict)
    viewers: dict[str, list[ViewerRecord]] = field(default_factory=dict)
    owners: dict[str, OwnershipRecord] = field(default_factory=dict)
    sites: dict[str, SiteRecord] = field(default_factory=dict)
    directory: dict[str, Any] = field(default_factory=dict)


def _user_write(
    site: str,
    upn: str,
    context: _Context,
    *,
    ownership: OwnershipRecord | None = None,
) -> NodeWrite:
    """A User node for a source identity, carrying its directory link where one was found.

    The node is keyed on who the *source* says the person is, not on the directory id: a
    resolution should add a fact to a user, not change which user they are.
    """
    resolved = context.directory.get(upn)
    properties: dict[str, Any] = {
        "upn": upn,
        # The only ontology type on both sides, so the writer declares it.
        "side": "source",
    }
    if ownership is not None:
        properties["display"] = ownership.owner_display
        properties["licence_tier"] = ownership.licence_tier
        properties["site_roles"] = list(ownership.site_roles) or None
    if resolved is not None:
        properties["directory_id"] = resolved.directory_id
        properties["display"] = resolved.display or properties.get("display")
        properties["directory_resolved_at"] = _now()
    return NodeWrite(
        type="User", id=derive_id(site, f"user:{upn}"), properties=_strip_nulls(properties)
    )


# --------------------------------------------------------------------- helpers


def _clean(
    node: NodeFragment,
    usage: UsageRecord | None,
    parse_quality: float | None = None,
    view_usage: UsageRecord | None = None,
    site: SiteRecord | None = None,
) -> dict[str, Any]:
    """Drop nulls, and fold usage and parse quality onto the Workbook.

    Adapters emit what the source had; a property the source did not carry is absent, not
    null. The ontology treats those differently (S1.1.1), and the adapter should not have
    to know that. Parse quality is the Harvester's measure of the parse rather than the
    adapter's report of the source, so it is added here rather than expected in the
    fragment.
    """
    properties = _strip_nulls(node.properties)
    if usage is not None:
        properties["views_90d"] = usage.views
        properties["distinct_viewers_90d"] = usage.distinct_viewers
    if parse_quality is not None:
        properties["parse_quality"] = parse_quality
    if view_usage is not None:
        properties["views_90d"] = view_usage.views
        properties["distinct_viewers_90d"] = view_usage.distinct_viewers
        if view_usage.last_view:
            properties["last_view"] = view_usage.last_view
    if site is not None:
        # Folded onto the Site node the parse already emits rather than written
        # separately: an upsert replaces the whole property set, so a second write would
        # drop whichever facts the other one did not carry.
        if site.licence_tier is not None:
            properties["licence_tier"] = site.licence_tier
        if site.user_count is not None:
            properties["user_count"] = site.user_count
    return properties



def _sheet_of(location: str) -> str | None:
    """An adapter reports a location as a path; the sheet is its first segment."""
    parts = [part for part in location.split("/") if part]
    return parts[0] if parts else None


def _field_of(location: str) -> str | None:
    parts = [part for part in location.split("/") if part]
    return parts[-1] if len(parts) > 1 else None


def _strip_nulls(properties: dict[str, Any]) -> dict[str, Any]:
    return {name: value for name, value in properties.items() if value is not None}


def _failed(ref: AssetRef, stage: str, exc: BaseException) -> WorkbookResult:
    retryable = isinstance(exc, AdapterError) and exc.retryable
    failure = HarvestFailure(
        workbook_luid=ref.luid,
        workbook_name=ref.name,
        project=ref.project,
        stage=stage,
        error=f"{type(exc).__name__}: {exc}",
        retryable=retryable,
    )
    logger.warning(
        "harvest: workbook %s failed at %s: %s", ref.luid, stage, failure.error
    )
    return WorkbookResult(
        ref_luid=ref.luid,
        project=ref.project,
        outcome=WorkbookOutcome.FAILED,
        failure=failure,
    )


def _not_modified(
    ref: AssetRef, previous: WorkbookState | None, grammar_version: str | None
) -> bool:
    """Can this workbook be skipped without fetching it? (S1.2.4)

    Every condition here is a way the answer could be wrong, and the cost of being wrong
    is a stale graph that nobody can tell is stale:

    * never harvested, so there is nothing to compare against;
    * the source does not report when the workbook changed — Tableau's Metadata API does,
      but the contract marks it optional and a source that cannot say must be fetched;
    * the platform has no record of what the source said last time, which is true of every
      workbook harvested before this story;
    * the revision moved, whatever the timestamps say;
    * the timestamp moved;
    * the grammar changed, so the same bytes would now parse differently — without this a
      grammar extension (S1.2.2's other route) would never reach an unchanged workbook,
      and the parse-quality queue would never clear.

    The last harvest failing is not on the list because a failure is not recorded against
    the workbook: its stored state is still the last one that succeeded, which is exactly
    what the comparison should use.
    """
    if previous is None or not ref.updated_at or not previous.source_updated_at:
        return False
    if ref.revision != previous.revision:
        return False
    if not _same_grammar(previous, grammar_version):
        return False
    seen_at = _as_datetime(previous.source_updated_at)
    reported = _as_datetime(ref.updated_at)
    if seen_at is None or reported is None:
        return False
    return reported <= seen_at


def _same_grammar(previous: WorkbookState, grammar_version: str | None) -> bool:
    """Was this workbook last parsed by the grammar now in force?

    An adapter that does not declare a grammar version cannot answer, and gets the benefit
    of the doubt — the alternative is re-parsing the whole estate on every run.
    """
    return grammar_version is None or previous.grammar_version == grammar_version


def _as_datetime(value: str) -> datetime | None:
    """Compare timestamps as instants, not as text.

    Two sources can render the same moment as ``...Z`` and ``...+00:00``, and a string
    comparison would call one of them newer.
    """
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(UTC)
    except ValueError:
        return None


def _project_progress(refs: Sequence[AssetRef]) -> list[ProjectProgress]:
    counts: dict[str, ProjectProgress] = {}
    for ref in refs:
        counts.setdefault(ref.project, ProjectProgress(project=ref.project)).queued += 1
    return [counts[name] for name in sorted(counts)]


def _apply(progress: HarvestProgress, results: Sequence[WorkbookResult]) -> None:
    by_project = {project.project: project for project in progress.projects}
    for result in results:
        project = by_project.setdefault(result.project, ProjectProgress(project=result.project))
        match result.outcome:
            case WorkbookOutcome.PARSED:
                progress.parsed += 1
                project.parsed += 1
            case WorkbookOutcome.SKIPPED_UNCHANGED:
                progress.skipped_unchanged += 1
                project.skipped_unchanged += 1
            case WorkbookOutcome.SKIPPED_NOT_MODIFIED:
                progress.skipped_not_modified += 1
                project.skipped_not_modified += 1
            case WorkbookOutcome.HELD_PARSE_QUALITY:
                progress.held += 1
                project.held += 1
            case WorkbookOutcome.FAILED:
                progress.failed += 1
                project.failed += 1
        if result.drifted:
            progress.drifted += 1
    progress.projects = [by_project[name] for name in sorted(by_project)]
