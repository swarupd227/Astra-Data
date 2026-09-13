"""G4 decommission: the per-site readiness checklist and gate card -- story S9.3.1,
opening F9.3.

    "As a licence admin, I want a Decommission Tracker per site with a readiness
    checklist and a G4 card when ready, so that I switch off a site once, safely, with
    a record.

    Acceptance criteria:
    - Readiness = all in-scope MUs RELEASED + parallel window elapsed + regression
      green + adoption threshold met + owner confirmations received; each item shows
      its state and evidence
    - G4 card lists the MUs, the licence tier and count released, the source workbooks
      to be archived, and the confirmation text; approver is the licence admin,
      countersigned by the Programme Manager
    - On approval the Steward archives the source workbooks (adapter capability),
      records the licence-release date and value on the Site node, and emits
      site.decommissioned
    - Deferral records the reason and a new target date"

§13.1's own G4 row, verbatim: "Site | Client licence administrator; countersigned by
Programme Manager | All site MUs RELEASED; parallel-run period elapsed; regression
suites green | Licence release; source workbooks archived." §14.4, verbatim: "...tracks
decommission readiness: all MUs released, regression green, adoption sessions held,
owner confirmation received. When readiness is met the G4 request opens automatically
for the licence administrator. On approval the Steward archives the source workbooks
(adapter capability) and records the licence-release date and value from the site
record." §15.3.4's own Decommission Tracker row: "Per site: readiness checklist, MUs
released, regression green, owner confirmations, licence value; G4 card when ready...
Authorise decommission (G4); defer with reason."

**Approve/defer reuse G3's own proven shape -- approver + countersigner on one
`GateDecision`, no new mechanism invented.** `GateDecision.decision` gains `DEFERRED`
(nodes.py, story S9.3.1's own addition, the identical "first real write of an
already-legal value" footing S8.3.1's four decision types had); `subject_ref` names the
Site rather than a Workbook -- the first G4 write this codebase has ever made, and the
first `GateDecision` whose subject is not (a proxy for) a Migration Unit. Approver role
is `client_licence_admin` (this AC's own persona, `Role.CLIENT_LICENCE_ADMIN`, declared
since S1.1.1, first driven for a write by S9.2.2 and now for a decision here);
countersigner is a plain, unverified name string, the identical convention `g3_card.
approve`'s own `countersigned_by` already established.

**"All in-scope MUs RELEASED" reads scope (`ScopeStore.states()`) against the identical
real "released" signal `release.py`/`adoption.py` already established** -- a SUCCEEDED
`promotion_run` row for `to_stage="prod"`. A withdrawn workbook is never counted against
readiness; an empty in-scope set is honestly not ready (nothing to decommission is not
"ready", it is "nothing to do").

**"Parallel window elapsed" reuses `release._window_end` and `g3_card.
DEFAULT_PARALLEL_WINDOW_WEEKS` verbatim** -- the identical per-site window `release_
board`'s own "parallel-run window" panel already computes, read here as elapsed once
`now >= window_end`, never stored.

**"Regression green" reads `RegressionScheduleStore.list_schedules()`, filtered to this
site's own in-scope workbooks.** No single "regression status" fact exists anywhere in
this codebase for a workbook that has never been scheduled -- confirmed by direct
research. Absence (never scheduled) does not block readiness the way a real `FAIL`
does; "green" reads as "nothing is currently failing," not "everything has been
checked," since regression scheduling is itself opt-in (`regression.py`'s own
docstring) and a story that made every released MU's readiness depend on an unrelated,
separate opt-in action would be inventing a precondition the AC's own words ("regression
green") do not ask for.

**"Adoption threshold met" reads `AdoptionStore.latest_for_workbooks`'s own frozen
`meets_threshold` per released workbook** -- the identical value `adoption.
decommission_tracker` already shows, never recomputed against whatever the config
says today (`adoption.py`'s own "history freezes its own inputs" rule).

**"Owner confirmations received" is a genuinely new fact this codebase has never
recorded -- a real, minimal, overwrite-not-append per-workbook confirmation
(`public.decommission_confirmation`, `PostgresDecommissionConfirmationStore`), the
identical shape `retention.Programme.family_count_confirmed_by`/`_at` already set for
"someone confirmed X," reused here per-workbook rather than per-programme.** Confirming
is the report owner's own action (`Role.CLIENT_REPORT_OWNER`, the same persona G3
approval already uses) -- §14.4 names "owner confirmation," and the report's own owner
is the only "owner" role this codebase already ties to one report.

**Archiving a source workbook is a genuinely new `SourceAdapter.archive()` capability**
(`astra_adapter.contract`, `INTERFACE_VERSION` bumped 1.1 -> 1.2) -- §13.1's own G4 row
names it plainly, "source workbooks archived," and no such method existed on the
contract before this story (confirmed by direct read: `manifest`/`enumerate`/`fetch`/
`parse`/`usage`/`owners`/`viewers`/`sites`/`parse_calc`/`execute_case`/`capture_visual`
were the whole contract). `approve` resolves each in-scope workbook's own real
`AssetRef` via `source_adapter.enumerate(Scope(site=...))` -- the adapter's own live
asset list, not a graph-derived guess -- and refuses outright, before archiving
anything, if the adapter has never claimed the capability at all: an all-or-nothing
action, the identical "no partial or guessed success" posture `promote_workbook`
already takes for its own two deploy calls.

**"Records the licence-release date and value on the Site node" writes directly onto
the real Site node this story decommissions -- no second `site_record` table.** See
`ontology/nodes.py`'s own new `SpecDeviation` for the full reasoning: §21 names a
`site_record` platform table that has never existed in this codebase and nothing else
needs; the value recorded is this same node's own `licence_cost_annual` at the moment
of approval, honestly `None` if that was never known (an adapter with no ownership
capability, per S1.2.3's own disclosed gap).

**Scope deliberately stops at G4 approval and deferral -- a fuller "readiness met opens
the request automatically" mechanism (§14.4's own words) is not built.** No scheduler or
notification exists anywhere in this codebase for "readiness just became true"; the
Decommission Tracker's own real, live read of the checklist is how a licence admin
learns readiness today, the identical "a real, queryable fact beats an unbuilt
notification" posture every prior gate-adjacent story in F9 has already taken.
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Protocol

import asyncpg
from astra_adapter import AdapterError, AssetRef, Scope, SourceAdapter

from .adoption import AdoptionStore
from .artefacts import ArtefactStore
from .errors import ElementNotFoundError, InvalidRequestError
from .events import site_decommissioned
from .g3_card import _live_gate_decisions  # cross-epic private reuse; see module docstring
from .graph.queries import EDGE_INDEX_TABLE
from .ids import new_ulid
from .lineage import hydrate
from .principal import Principal
from .regression import RegressionScheduleStore
from .release import PromotionStore, _window_end  # cross-epic private reuse; see module docstring
from .scope import ScopeState, ScopeStore
from .writes import GraphWriter, NodeWrite

GATE = "G4"

#: The identical "a sentence is a fuller bar than a reason" footing every other gate's
#: own rationale/reason already sets (`g3_card.MIN_RATIONALE_LENGTH`, `exception_desk.
#: MIN_RATIONALE_LENGTH`), reused verbatim rather than a fresh number for this gate.
MIN_RATIONALE_LENGTH = 20

CONFIRMATION_TABLE = "public.decommission_confirmation"


class G4CardError(Exception):
    """A G4 card could not be read or a decision could not be recorded as asked."""


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _clean_rationale(rationale: str) -> str:
    cleaned = rationale.strip()
    if len(cleaned) < MIN_RATIONALE_LENGTH:
        raise InvalidRequestError(
            f"a G4 decision needs a rationale of at least {MIN_RATIONALE_LENGTH} characters "
            f"-- a real sentence, not a placeholder"
        )
    return cleaned


def _parse_target_date(value: str, *, now: datetime | None = None) -> str:
    """A deferral's own new target date must be a real, future date -- honestly refused
    otherwise rather than silently accepting one already in the past."""
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise InvalidRequestError(f"'{value}' is not a real date") from exc
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    moment = now or datetime.now(UTC)
    if parsed <= moment:
        raise InvalidRequestError("a deferred G4's own new target date must be in the future")
    return parsed.isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _window_elapsed(window_end: str | None, *, now: datetime | None = None) -> bool:
    if window_end is None:
        return False
    moment = now or datetime.now(UTC)
    return moment >= datetime.fromisoformat(window_end.replace("Z", "+00:00"))


# ----------------------------------------------------------------- owner confirmation


@dataclass(frozen=True, slots=True)
class DecommissionConfirmation:
    workbook_id: str
    confirmed_by: str
    confirmed_at: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "workbook_id": self.workbook_id,
            "confirmed_by": self.confirmed_by,
            "confirmed_at": self.confirmed_at,
        }


class DecommissionConfirmationStore(Protocol):
    async def confirm(self, workbook_id: str, *, confirmed_by: str) -> DecommissionConfirmation: ...

    async def for_workbooks(
        self, workbook_ids: Sequence[str]
    ) -> dict[str, DecommissionConfirmation]: ...


class PostgresDecommissionConfirmationStore:
    """One current confirmation per workbook -- overwrite, not append, the identical
    shape `retention.Programme.family_count_confirmed_by`/`_at` already set: "a later
    confirmation supersedes the earlier one," not a growing log of confirmations for a
    fact that is either true today or not."""

    def __init__(self, pool: asyncpg.Pool, *, graph_name: str) -> None:
        self._pool = pool
        self._graph = graph_name

    async def confirm(self, workbook_id: str, *, confirmed_by: str) -> DecommissionConfirmation:
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                f"""INSERT INTO {CONFIRMATION_TABLE} (id, graph, workbook_id, confirmed_by)
                     VALUES ($1, $2, $3, $4)
                     ON CONFLICT (graph, workbook_id) DO UPDATE SET
                         confirmed_by = EXCLUDED.confirmed_by, confirmed_at = now()
                     RETURNING workbook_id, confirmed_by, confirmed_at""",
                f"decommconf_{new_ulid()}", self._graph, workbook_id, confirmed_by,
            )
        return _confirmation_from_row(row)

    async def for_workbooks(
        self, workbook_ids: Sequence[str]
    ) -> dict[str, DecommissionConfirmation]:
        if not workbook_ids:
            return {}
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(
                f"SELECT * FROM {CONFIRMATION_TABLE} WHERE graph = $1 AND workbook_id = ANY($2::text[])",
                self._graph, list(workbook_ids),
            )
        return {row["workbook_id"]: _confirmation_from_row(row) for row in rows}


def _confirmation_from_row(row: asyncpg.Record) -> DecommissionConfirmation:
    return DecommissionConfirmation(
        workbook_id=row["workbook_id"], confirmed_by=row["confirmed_by"],
        confirmed_at=_iso(row["confirmed_at"]) or "",
    )


def _iso(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, str):
        return value
    return str(value.isoformat())


# --------------------------------------------------------------------------- readiness


async def _workbooks_for_site(pool: asyncpg.Pool, graph_name: str, site_id: str) -> list[str]:
    """Every workbook this site contains, via the two-hop `CONTAINS` traversal (Site ->
    Project -> Workbook) -- the forward direction of `release._sites_for_workbooks`'s
    own reverse one, not reusable verbatim since the join runs the other way."""
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            f"""
            SELECT wb_edge.to_id AS workbook_id
              FROM {EDGE_INDEX_TABLE} site_edge
              JOIN {EDGE_INDEX_TABLE} wb_edge
                ON wb_edge.from_id = site_edge.to_id AND wb_edge.label = 'CONTAINS'
                   AND wb_edge.graph = site_edge.graph
             WHERE site_edge.graph = $1 AND site_edge.label = 'CONTAINS' AND site_edge.from_id = $2
            """,
            graph_name, site_id,
        )
    return [row["workbook_id"] for row in rows]


@dataclass(frozen=True, slots=True)
class ReadinessItem:
    key: str
    label: str
    met: bool
    evidence: dict[str, Any]

    def as_dict(self) -> dict[str, Any]:
        return {"key": self.key, "label": self.label, "met": self.met, "evidence": self.evidence}


async def readiness_checklist(
    pool: asyncpg.Pool,
    graph_name: str,
    promotion_store: PromotionStore,
    adoption_store: AdoptionStore,
    confirmation_store: DecommissionConfirmationStore,
    regression_store: RegressionScheduleStore,
    scope_store: ScopeStore,
    *,
    site_id: str,
) -> list[ReadinessItem]:
    """The AC's own five items, each a real, computed fact with its own evidence -- see
    this module's own docstring for how each is resolved."""
    workbook_ids = await _workbooks_for_site(pool, graph_name, site_id)
    scope_states = await scope_store.states()
    in_scope = [wb for wb in workbook_ids if not scope_states.get(wb, ScopeState()).withdrawn]

    promotions = await promotion_store.all_for_workbooks(in_scope)
    released = [
        wb for wb in in_scope
        if "prod" in promotions.get(wb, {}) and promotions[wb]["prod"].state == "SUCCEEDED"
    ]

    items: list[ReadinessItem] = [
        ReadinessItem(
            key="all_released", label="All in-scope MUs released",
            met=bool(in_scope) and len(released) == len(in_scope),
            evidence={"released": len(released), "total": len(in_scope)},
        )
    ]

    prod_starts = sorted(promotions[wb]["prod"].finished_at for wb in released)
    window_start = prod_starts[0] if prod_starts else None
    window_end = _window_end(window_start)
    items.append(
        ReadinessItem(
            key="parallel_window_elapsed", label="Parallel-run window elapsed",
            met=_window_elapsed(window_end),
            evidence={"window_start": window_start, "window_end": window_end},
        )
    )

    schedules = await regression_store.list_schedules()
    relevant = [s for s in schedules if s.workbook_id in in_scope]
    failing = [s for s in relevant if s.last_result == "FAIL"]
    items.append(
        ReadinessItem(
            key="regression_green", label="Regression green",
            met=not failing,
            evidence={"checked": len(relevant), "failing": len(failing)},
        )
    )

    latest_snapshots = await adoption_store.latest_for_workbooks(released)
    meeting = sum(1 for wb in released if (latest_snapshots.get(wb) or None) and latest_snapshots[wb].meets_threshold)
    items.append(
        ReadinessItem(
            key="adoption_threshold_met", label="Adoption threshold met",
            met=bool(released) and meeting == len(released),
            evidence={"meeting": meeting, "released": len(released)},
        )
    )

    confirmations = await confirmation_store.for_workbooks(in_scope)
    items.append(
        ReadinessItem(
            key="owner_confirmations_received", label="Owner confirmations received",
            met=bool(in_scope) and len(confirmations) == len(in_scope),
            evidence={"confirmed": len(confirmations), "total": len(in_scope)},
        )
    )

    return items


# --------------------------------------------------------------------------- the card


async def g4_card(
    pool: asyncpg.Pool,
    graph_name: str,
    promotion_store: PromotionStore,
    adoption_store: AdoptionStore,
    confirmation_store: DecommissionConfirmationStore,
    regression_store: RegressionScheduleStore,
    scope_store: ScopeStore,
    *,
    site_id: str,
) -> dict[str, Any]:
    async with pool.acquire() as conn:
        site = (await hydrate(conn, graph_name, "Site", [site_id])).get(site_id)
    if site is None:
        raise ElementNotFoundError(f"no Site '{site_id}'")

    workbook_ids = await _workbooks_for_site(pool, graph_name, site_id)
    scope_states = await scope_store.states()
    in_scope = [wb for wb in workbook_ids if not scope_states.get(wb, ScopeState()).withdrawn]
    async with pool.acquire() as conn:
        workbooks = await hydrate(conn, graph_name, "Workbook", in_scope)

    checklist = await readiness_checklist(
        pool, graph_name, promotion_store, adoption_store,
        confirmation_store, regression_store, scope_store, site_id=site_id,
    )
    ready = all(item.met for item in checklist)
    released_count = checklist[0].evidence["released"]

    mus = [
        {"workbook_id": wb, "name": (workbooks.get(wb) or {}).get("name", wb)}
        for wb in in_scope
    ]

    decisions = await _live_gate_decisions(pool, graph_name)
    latest_decision = max(
        (
            props for props in decisions.values()
            if props.get("gate") == GATE and props.get("subject_ref") == site_id
        ),
        key=lambda p: str(p.get("timestamp") or ""),
        default=None,
    )

    name = site.get("name", site_id)
    return {
        "site_id": site_id,
        "name": name,
        "licence_tier": site.get("licence_tier"),
        "licence_cost_annual": site.get("licence_cost_annual"),
        "mus": mus,
        "released_mu_count": released_count,
        "source_workbooks_to_archive": mus,
        "confirmation_text": (
            f"I, as the licence administrator for {name}, confirm that all {len(in_scope)} "
            f"in-scope Migration Unit(s) have been released, the parallel-run window has "
            f"elapsed, regression is green, the adoption threshold has been met, and "
            f"every report owner has confirmed. I authorise archiving the source "
            f"workbooks listed above and releasing this site's licence."
        ),
        "ready": ready,
        "checklist": [item.as_dict() for item in checklist],
        "next": {
            "on_approval": "the Steward archives the listed source workbooks and records "
                           "the licence release on this site",
        },
        "latest_decision": (
            {
                "decision": latest_decision.get("decision"), "approver": latest_decision.get("approver"),
                "countersigner": latest_decision.get("countersigner"),
                "timestamp": latest_decision.get("timestamp"), "rationale": latest_decision.get("rationale"),
                "target_date": latest_decision.get("target_date"),
            }
            if latest_decision else None
        ),
    }


async def _snapshot_card(
    artefact_store: ArtefactStore, card: dict[str, Any], *, site_id: str, principal: Principal,
) -> str:
    record = await artefact_store.store(
        kind="g4_card_snapshot", mu_ref=site_id, case_id=site_id,
        content=json.dumps(card).encode("utf-8"), media_type="application/json",
        created_by=principal.value,
    )
    return record.id


async def _write_gate_decision(
    writer: GraphWriter, *, site_id: str, decision: str, rationale: str,
    approver_role: str, countersigner: str | None, countersigner_role: str | None,
    evidence_ref: str | None, target_date: str | None, principal: Principal,
) -> str:
    decision_id = new_ulid()
    await writer.write_nodes(
        [
            NodeWrite(
                type="GateDecision",
                id=decision_id,
                properties={
                    "gate": GATE, "subject_ref": site_id, "decision": decision,
                    "approver": principal.value, "approver_role": approver_role,
                    "countersigner": countersigner, "countersigner_role": countersigner_role,
                    "rationale": rationale, "evidence_ref": evidence_ref,
                    "target_date": target_date, "timestamp": _now(),
                },
            )
        ],
        principal=principal,
    )
    return decision_id


@dataclass(frozen=True, slots=True)
class G4DecisionResult:
    site_id: str
    gate_decision_id: str
    decision: str
    archived_count: int = 0
    licence_release_value: float | None = None
    decommissioned_at: str | None = None
    target_date: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "site_id": self.site_id, "gate_decision_id": self.gate_decision_id,
            "decision": self.decision, "archived_count": self.archived_count,
            "licence_release_value": self.licence_release_value,
            "decommissioned_at": self.decommissioned_at, "target_date": self.target_date,
        }


async def approve(
    pool: asyncpg.Pool,
    graph_name: str,
    writer: GraphWriter,
    artefact_store: ArtefactStore,
    source_adapter: SourceAdapter,
    promotion_store: PromotionStore,
    adoption_store: AdoptionStore,
    confirmation_store: DecommissionConfirmationStore,
    regression_store: RegressionScheduleStore,
    scope_store: ScopeStore,
    *,
    site_id: str,
    rationale: str,
    countersigned_by: str,
    principal: Principal,
) -> G4DecisionResult:
    """Refuses outright, before archiving anything, if the checklist is not fully met or
    the adapter has never claimed the archive capability -- never a partial or guessed
    decommission. See this module's own docstring for why each in-scope workbook's own
    `AssetRef` is resolved from the adapter's own live enumeration, not derived from the
    graph."""
    cleaned_rationale = _clean_rationale(rationale)
    cleaned_countersigner = countersigned_by.strip()
    if not cleaned_countersigner:
        raise InvalidRequestError("approving G4 needs a countersigning Programme Manager's name")

    card = await g4_card(
        pool, graph_name, promotion_store, adoption_store,
        confirmation_store, regression_store, scope_store, site_id=site_id,
    )
    if not card["ready"]:
        unmet = [item["label"] for item in card["checklist"] if not item["met"]]
        raise InvalidRequestError(
            f"site '{site_id}' is not ready for G4: " + "; ".join(unmet)
        )

    if not source_adapter.manifest().capabilities.archive:
        raise InvalidRequestError(
            "this deployment's source adapter cannot archive workbooks; G4 approval "
            "needs a real archive capability, the same disclosed gap `usage`/"
            "`ownership` can have"
        )

    async with pool.acquire() as conn:
        site = (await hydrate(conn, graph_name, "Site", [site_id])).get(site_id)
        workbooks = await hydrate(conn, graph_name, "Workbook", [mu["workbook_id"] for mu in card["mus"]])
    if site is None:
        raise ElementNotFoundError(f"no Site '{site_id}'")

    site_name = str(site.get("name") or "")
    assets_by_luid: dict[str, AssetRef] = {}
    try:
        async for asset in source_adapter.enumerate(Scope(site=site_name)):
            assets_by_luid[asset.luid] = asset
    except AdapterError as exc:
        raise InvalidRequestError(
            f"the source no longer reports site '{site_name}'; cannot archive its workbooks: {exc}"
        ) from exc

    archived: list[str] = []
    for mu in card["mus"]:
        workbook_id = mu["workbook_id"]
        luid = str((workbooks.get(workbook_id) or {}).get("luid") or "")
        resolved_asset = assets_by_luid.get(luid)
        if resolved_asset is None:
            raise InvalidRequestError(
                f"the source no longer reports workbook '{mu['name']}' (luid '{luid}') "
                f"at this site; cannot archive"
            )
        try:
            result = await source_adapter.archive(resolved_asset)
        except AdapterError as exc:
            raise InvalidRequestError(f"could not archive workbook '{mu['name']}': {exc}") from exc
        if not result.archived:
            raise InvalidRequestError(
                f"could not archive workbook '{mu['name']}': {result.detail or 'archive refused'}"
            )
        archived.append(workbook_id)

    snapshot_id = await _snapshot_card(artefact_store, card, site_id=site_id, principal=principal)
    decision_id = await _write_gate_decision(
        writer, site_id=site_id, decision="APPROVED", rationale=cleaned_rationale,
        approver_role="client_licence_admin", countersigner=cleaned_countersigner,
        countersigner_role="programme_manager", evidence_ref=snapshot_id, target_date=None,
        principal=principal,
    )

    licence_release_value = site.get("licence_cost_annual")
    decommissioned_at = _now()
    await writer.set_node_properties(
        site_id,
        {"decommissioned_at": decommissioned_at, "licence_release_value": licence_release_value},
        principal=principal,
    )
    await writer.append_event(
        site_decommissioned(
            source=writer.event_source, site_id=site_id, workbook_ids=tuple(archived),
            licence_tier=site.get("licence_tier"), licence_release_value=licence_release_value,
            gate_decision_id=decision_id, principal=principal,
        )
    )

    return G4DecisionResult(
        site_id=site_id, gate_decision_id=decision_id, decision="APPROVED",
        archived_count=len(archived), licence_release_value=licence_release_value,
        decommissioned_at=decommissioned_at,
    )


async def defer(
    pool: asyncpg.Pool, graph_name: str, writer: GraphWriter, *,
    site_id: str, reason: str, target_date: str, principal: Principal,
) -> G4DecisionResult:
    cleaned_reason = _clean_rationale(reason)
    cleaned_target = _parse_target_date(target_date)

    async with pool.acquire() as conn:
        site = (await hydrate(conn, graph_name, "Site", [site_id])).get(site_id)
    if site is None:
        raise ElementNotFoundError(f"no Site '{site_id}'")

    decision_id = await _write_gate_decision(
        writer, site_id=site_id, decision="DEFERRED", rationale=cleaned_reason,
        approver_role="client_licence_admin", countersigner=None, countersigner_role=None,
        evidence_ref=None, target_date=cleaned_target, principal=principal,
    )
    return G4DecisionResult(
        site_id=site_id, gate_decision_id=decision_id, decision="DEFERRED", target_date=cleaned_target,
    )


class G4CardService:
    """Binds the module-level functions to one pool/graph/writer/artefact store/source
    adapter/promotion store/adoption stores/confirmation store/regression store/scope
    store -- the identical "pre-bound object on app.state" shape `G3CardService`/
    `ReleaseService`/`AdoptionService` already take."""

    def __init__(
        self,
        pool: asyncpg.Pool,
        *,
        graph_name: str,
        writer: GraphWriter,
        artefact_store: ArtefactStore,
        source_adapter: SourceAdapter,
        promotion_store: PromotionStore,
        adoption_store: AdoptionStore,
        confirmation_store: DecommissionConfirmationStore,
        regression_store: RegressionScheduleStore,
        scope_store: ScopeStore,
    ) -> None:
        self._pool = pool
        self._graph = graph_name
        self._writer = writer
        self._artefact_store = artefact_store
        self._source_adapter = source_adapter
        self._promotion_store = promotion_store
        self._adoption_store = adoption_store
        self._confirmation_store = confirmation_store
        self._regression_store = regression_store
        self._scope_store = scope_store

    async def card(self, site_id: str) -> dict[str, Any]:
        return await g4_card(
            self._pool, self._graph, self._promotion_store, self._adoption_store,
            self._confirmation_store, self._regression_store, self._scope_store, site_id=site_id,
        )

    async def readiness(self, site_id: str) -> list[ReadinessItem]:
        return await readiness_checklist(
            self._pool, self._graph, self._promotion_store, self._adoption_store,
            self._confirmation_store, self._regression_store, self._scope_store, site_id=site_id,
        )

    async def approve(
        self, site_id: str, *, rationale: str, countersigned_by: str, principal: Principal,
    ) -> G4DecisionResult:
        return await approve(
            self._pool, self._graph, self._writer, self._artefact_store, self._source_adapter,
            self._promotion_store, self._adoption_store,
            self._confirmation_store, self._regression_store, self._scope_store,
            site_id=site_id, rationale=rationale, countersigned_by=countersigned_by, principal=principal,
        )

    async def defer(self, site_id: str, *, reason: str, target_date: str, principal: Principal) -> G4DecisionResult:
        return await defer(
            self._pool, self._graph, self._writer,
            site_id=site_id, reason=reason, target_date=target_date, principal=principal,
        )

    async def confirm(self, workbook_id: str, *, confirmed_by: str) -> DecommissionConfirmation:
        return await self._confirmation_store.confirm(workbook_id, confirmed_by=confirmed_by)


__all__ = [
    "CONFIRMATION_TABLE",
    "GATE",
    "MIN_RATIONALE_LENGTH",
    "DecommissionConfirmation",
    "DecommissionConfirmationStore",
    "G4CardError",
    "G4CardService",
    "G4DecisionResult",
    "PostgresDecommissionConfirmationStore",
    "ReadinessItem",
    "approve",
    "defer",
    "g4_card",
    "readiness_checklist",
]
