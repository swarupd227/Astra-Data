"""Promotion through the Fabric deployment pipeline, and the Release Board -- story
S9.2.1, opening F9.2.

    "As a platform engineer, I want the Steward to promote ACCEPTED MUs per train
    through the Fabric deployment pipeline, so that release is a pipeline stage with
    evidence, not a manual copy.

    Acceptance criteria:
    - Promotion dev -> test -> prod via Fabric deployment pipelines with the client's
      approval rules; MA-08 (L3) to test, MA-09 (L2, explicit PM approval) to production
    - Release Board shows per train: MUs by pipeline stage, blockers, and the release
      evidence bundle
    - Parallel-run window (default 4 weeks) starts at production deployment and is
      visible per MU and per site"

§14.4 itself, verbatim: "The Steward promotes ACCEPTED MUs per train through the
deployment pipeline, opens a parallel-running window per site (default 4 weeks,
configurable)..." §7.2: "Deployment uses Fabric deployment pipelines; the Steward
promotes a report and its model together..." §13.2's own MA-08/MA-09 rows, verbatim:
"MA-08 Promote to test workspace | L3 | Post-G3 only" / "MA-09 Promote to production |
L2 | Explicit release approval by PM."

**"The Steward" is an attributed human action here, not the automated `agent:steward`
principal `regression.py` already declared.** Confirmed by direct read: every prior
Steward-shaped write in this codebase (`regression.py`'s scheduled re-runs, `build.py`'s
post-G2-approval auto-build) is unattended -- nobody clicked a second button. Promoting
to test or to production is the opposite: a real platform engineer or programme manager
performs it, the identical "the real human's own principal, not a borrowed identity"
posture `g3_card.approve` already takes for the report owner. "The Steward" names the
*mechanism* (this module), not a principal this module invents.

**Neither MA-08 nor MA-09 gets a new `GateDecision.gate` value.** Confirmed, again, by
direct read of `ontology/nodes.py`: the four legal gates (`G1`-`G4`) are Charter/Model/
Parity/Decommission (§13.1's own four-row table) -- there has never been a numbered
release/promotion gate anywhere in the spec, unlike `decision`'s own four-value widening
S8.3.1 already did for a real Exception Desk need. `public.promotion_run` (this story's
own new table, the identical footing `build_run`/`report_deploy_run`/`commercial_
ledger` already have) is where a promotion attempt's own real approval lives instead --
`approved_by`/`approver_role`/`rationale`, not a `GateDecision`.

**"ACCEPTED" is checked against the real `GateDecision(gate="G3", decision="APPROVED")`,
not the commercial ledger.** S9.1.2's own `commercial_ledger` row is a real, but
*conditional*, side effect of G3 approval -- `invoicing.record_acceptance` honestly
returns `None`, and writes no ledger row, for a workbook that has never been re-tiered
(a real, disclosed gap, not an error). The MU's own §3.2 state name, "ACCEPTED," is set
by G3 approval itself, confirmed directly against `g3_card.approve`'s own docstring and
the MU state table (§3.2: "ACCEPTED ... Client report owner approves G3") -- checking
the ledger instead would wrongly block an untiered but genuinely G3-approved workbook
from ever reaching test.

**Promoting the report and the model are two separate, real deploy calls, reusing two
already-proven mechanisms verbatim -- no new commit/deploy code.** The report half is
`report_deploy.deploy_report(..., workspace=<stage>)` unchanged: it already commits and
deploys a fresh PBIR bundle to whatever workspace name it is given (S6.1.2's own
workspace-generic contract, confirmed by direct read -- nothing about it assumes "dev").
The model half copies `routes_modeller.promote`'s own exact pattern (S4.3.3): find this
workbook's family (`foundry_routing._family_for_workbook`, cross-epic private reuse, the
identical helper `g3_card.py` already imports the same way), read that family's latest
*successful* build (`BuildStore.latest`), and redeploy that build's own already-committed
`git_ref` to the new workspace via `TargetAdapter.deploy` directly -- a pipeline promotes
the *same, already-proved* artefact across stages; it does not rebuild from scratch at
every stage, which is the whole reason a promotion pipeline is worth having over a fresh
deploy. Neither `model_lifecycle.promote_family`'s own `BUILT -> PUBLISHED` state flip
nor `ModelFamily.state` is touched here at all: that state machine is the *family's* own,
already fully owned by S4.3.3, and multiple workbooks can share one family -- this
story's own promotion is scoped to one workbook's own MU, deliberately kept a distinct,
additive fact (`promotion_run`) rather than overloading a family-wide state that several
unrelated workbooks might still need to stay PUBLISHED against.

**`promotion_run` IS the release evidence bundle -- no second, duplicated snapshot via
`ArtefactStore`.** See the migration's own docstring for the full reasoning: unlike a
rendered G3 card (a *view*, needing its own frozen snapshot), a `promotion_run` row
already is the queryable record of what happened (steps, workspace, git ref, approver),
the identical "the row IS the evidence" footing `build_run`/`report_deploy_run` already
have.

**Blockers are real, computed facts, checked twice for two different reasons.**
`promotion_blockers` is called once to *refuse* an actual promotion attempt
(`promote_workbook` raises `InvalidRequestError` naming every blocker it finds, never
attempting a partial or guessed deploy), and again, read-only, to *show* the Release
Board why a given MU cannot yet advance -- the identical fact, read the identical way, so
the board can never claim readiness the action itself would refuse.

**The parallel-run window is computed, never stored, on both axes the AC names.** Per
MU: `promoted_at` (`promotion_run`'s own `finished_at` for a SUCCEEDED `to_stage="prod"`
row) plus `DEFAULT_PARALLEL_WINDOW_WEEKS` (`g3_card`'s own already-declared 4-week
constant, reused verbatim -- the identical number, not a second, possibly-drifting one).
Per site: the *earliest* prod promotion among that site's own workbooks -- read as "the
window a site's own regression/adoption tracking cares about opens the moment the first
MU of that site goes live," a real, disclosed reading choice, since §14.4 never states
whether a site's window is keyed to its first or its last released MU. No new `Site`
property is added for this (spec's own §21 `site_record.parallel_run_start` names a
stored field) -- deliberately: `promotion_run` is the one real source of truth for every
promotion this platform has ever made, and storing a second, derived copy on `Site`
risks disagreeing with it the moment a promotion is corrected or backfilled, the
identical "never trust a stored counter over the live rows" posture `pattern.
failure_count` already established (S5.5.2) for its own always-recomputed retirement
check.

**Scope deliberately stops at promotion and the parallel-run window -- decommission
readiness (regression status, adoption sessions, owner confirmation, G4) is not this
story's own AC and is left to F9.2's own later story.** §14.4's fuller "tracks
decommission readiness... the G4 request opens automatically" sentence names real,
separate future work this module does not attempt; the Release Board built here shows
exactly the AC's own three nouns -- pipeline stage, blockers, evidence bundle -- plus
the parallel-run window, not a full readiness checklist.
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, Protocol

import asyncpg
from astra_adapter import TargetAdapter, TargetAdapterError

from .build import BuildStore
from .compositor import read_report
from .errors import ElementNotFoundError, InvalidRequestError
from .events import mu_promoted
from .foundry_routing import _family_for_workbook  # cross-epic private helper; see module docstring
from .g3_card import (  # cross-epic private reuse; see module docstring
    DEFAULT_PARALLEL_WINDOW_WEEKS,
    GATE,
    _clean_rationale,
    _live_gate_decisions,
)
from .graph.queries import EDGE_INDEX_TABLE
from .ids import new_ulid
from .lineage import hydrate
from .principal import Principal
from .report_deploy import ReportDeployError, ReportDeployStore, deploy_report
from .trains import list_trains
from .writes import GraphWriter

PROMOTION_TABLE = "public.promotion_run"

#: "dev" is implicit -- every workbook starts there the moment its report/model are
#: built. These are the two real, explicit promotion actions this story adds.
PIPELINE_STAGES = ("test", "prod")

#: The stage a "blockers to reach" reading should check next -- "test" even while
#: NOT_ACCEPTED, so the Release Board shows *why* (G3 not approved, etc.) rather than an
#: empty blockers list for the most common real state every not-yet-accepted MU starts
#: in. `None` only once nothing further can ever be promoted (PROD is the last stage).
_NEXT_STAGE = {"NOT_ACCEPTED": "test", "ACCEPTED": "test", "TEST": "prod", "PROD": None}


@dataclass(frozen=True, slots=True)
class PromotionStep:
    name: str
    ok: bool
    detail: str

    def as_dict(self) -> dict[str, Any]:
        return {"name": self.name, "ok": self.ok, "detail": self.detail}


@dataclass(frozen=True, slots=True)
class PromotionRecord:
    id: str
    workbook_id: str
    to_stage: str
    workspace: str
    state: str
    """``SUCCEEDED`` or ``FAILED``."""
    steps: tuple[PromotionStep, ...]
    model_git_ref: str | None
    report_deploy_id: str | None
    approved_by: str | None
    approver_role: str | None
    rationale: str | None
    triggered_by: str
    started_at: str
    finished_at: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "workbook_id": self.workbook_id,
            "to_stage": self.to_stage,
            "workspace": self.workspace,
            "state": self.state,
            "steps": [s.as_dict() for s in self.steps],
            "model_git_ref": self.model_git_ref,
            "report_deploy_id": self.report_deploy_id,
            "approved_by": self.approved_by,
            "approver_role": self.approver_role,
            "rationale": self.rationale,
            "triggered_by": self.triggered_by,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
        }


class PromotionStore(Protocol):
    async def record(self, promotion: PromotionRecord) -> PromotionRecord: ...

    async def latest(self, workbook_id: str, to_stage: str) -> PromotionRecord | None: ...

    async def all_for_workbooks(
        self, workbook_ids: Sequence[str]
    ) -> dict[str, dict[str, PromotionRecord]]: ...


class PostgresPromotionStore:
    def __init__(self, pool: asyncpg.Pool, *, graph_name: str) -> None:
        self._pool = pool
        self._graph = graph_name

    async def record(self, promotion: PromotionRecord) -> PromotionRecord:
        async with self._pool.acquire() as conn:
            await conn.execute(
                f"""
                INSERT INTO {PROMOTION_TABLE}
                    (id, graph, workbook_id, to_stage, workspace, state, steps,
                     model_git_ref, report_deploy_id, approved_by, approver_role,
                     rationale, triggered_by, started_at, finished_at)
                VALUES ($1, $2, $3, $4, $5, $6, $7::jsonb, $8, $9, $10, $11, $12, $13, $14, $15)
                ON CONFLICT (graph, workbook_id, to_stage) DO UPDATE SET
                    id = EXCLUDED.id, workspace = EXCLUDED.workspace, state = EXCLUDED.state,
                    steps = EXCLUDED.steps, model_git_ref = EXCLUDED.model_git_ref,
                    report_deploy_id = EXCLUDED.report_deploy_id,
                    approved_by = EXCLUDED.approved_by, approver_role = EXCLUDED.approver_role,
                    rationale = EXCLUDED.rationale, triggered_by = EXCLUDED.triggered_by,
                    started_at = EXCLUDED.started_at, finished_at = EXCLUDED.finished_at
                """,
                promotion.id,
                self._graph,
                promotion.workbook_id,
                promotion.to_stage,
                promotion.workspace,
                promotion.state,
                json.dumps([s.as_dict() for s in promotion.steps]),
                promotion.model_git_ref,
                promotion.report_deploy_id,
                promotion.approved_by,
                promotion.approver_role,
                promotion.rationale,
                promotion.triggered_by,
                datetime.fromisoformat(promotion.started_at),
                datetime.fromisoformat(promotion.finished_at),
            )
        return promotion

    async def latest(self, workbook_id: str, to_stage: str) -> PromotionRecord | None:
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                f"SELECT * FROM {PROMOTION_TABLE} WHERE graph = $1 AND workbook_id = $2 AND to_stage = $3",
                self._graph, workbook_id, to_stage,
            )
        return _from_row(row) if row else None

    async def all_for_workbooks(
        self, workbook_ids: Sequence[str]
    ) -> dict[str, dict[str, PromotionRecord]]:
        if not workbook_ids:
            return {}
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(
                f"SELECT * FROM {PROMOTION_TABLE} WHERE graph = $1 AND workbook_id = ANY($2::text[])",
                self._graph, list(workbook_ids),
            )
        out: dict[str, dict[str, PromotionRecord]] = {}
        for row in rows:
            record = _from_row(row)
            out.setdefault(record.workbook_id, {})[record.to_stage] = record
        return out


def _from_row(row: asyncpg.Record) -> PromotionRecord:
    steps_raw = row["steps"]
    steps_list = json.loads(steps_raw) if isinstance(steps_raw, str) else list(steps_raw)
    return PromotionRecord(
        id=row["id"],
        workbook_id=row["workbook_id"],
        to_stage=row["to_stage"],
        workspace=row["workspace"],
        state=row["state"],
        steps=tuple(PromotionStep(**s) for s in steps_list),
        model_git_ref=row["model_git_ref"],
        report_deploy_id=row["report_deploy_id"],
        approved_by=row["approved_by"],
        approver_role=row["approver_role"],
        rationale=row["rationale"],
        triggered_by=row["triggered_by"],
        started_at=_iso(row["started_at"]) or "",
        finished_at=_iso(row["finished_at"]) or "",
    )


def _iso(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, str):
        return value
    return str(value.isoformat())


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")


async def _g3_approved(pool: asyncpg.Pool, graph_name: str, workbook_id: str) -> bool:
    decisions = await _live_gate_decisions(pool, graph_name)
    return any(
        d.get("gate") == GATE and d.get("subject_ref") == workbook_id and d.get("decision") == "APPROVED"
        for d in decisions.values()
    )


async def promotion_blockers(
    pool: asyncpg.Pool,
    graph_name: str,
    build_store: BuildStore,
    promotion_store: PromotionStore,
    *,
    workbook_id: str,
    to_stage: str,
) -> list[str]:
    """Real, computed reasons this workbook cannot promote to ``to_stage`` right now --
    the identical list `promote_workbook` refuses on and the Release Board shows."""
    if to_stage not in PIPELINE_STAGES:
        raise InvalidRequestError(f"to_stage must be one of {PIPELINE_STAGES}; got {to_stage!r}")

    blockers: list[str] = []

    if to_stage == "prod":
        test_promotion = await promotion_store.latest(workbook_id, "test")
        if test_promotion is None or test_promotion.state != "SUCCEEDED":
            blockers.append(
                "this workbook has not been promoted to test yet -- MA-09 only promotes from test"
            )
        return blockers

    if not await _g3_approved(pool, graph_name, workbook_id):
        blockers.append(
            "G3 has not been approved for this workbook yet -- MA-08 only promotes an ACCEPTED MU"
        )

    report = await read_report(pool, graph_name, workbook_id)
    if report is None:
        blockers.append("no report has been composed for this workbook yet")
    else:
        model_id = report.get("model_ref")
        async with pool.acquire() as conn:
            models = await hydrate(conn, graph_name, "SemanticModel", [model_id] if model_id else [])
        model_state = (models.get(model_id) or {}).get("state") if model_id else None
        if model_state not in ("BUILT", "PUBLISHED"):
            blockers.append("this report is bound to a model that has not been built yet")

    family_id = await _family_for_workbook(pool, graph_name, workbook_id)
    if family_id is None:
        blockers.append("no model family has been assigned to this workbook yet (clustering has not run)")
    else:
        latest_build = await build_store.latest(family_id)
        if latest_build is None or latest_build.state != "SUCCEEDED" or not latest_build.git_ref:
            blockers.append("this workbook's model family has no successful build to promote")

    return blockers


async def promote_workbook(
    pool: asyncpg.Pool,
    graph_name: str,
    writer: GraphWriter,
    target_adapter: TargetAdapter,
    report_deploy_store: ReportDeployStore,
    build_store: BuildStore,
    promotion_store: PromotionStore,
    *,
    workbook_id: str,
    to_stage: str,
    workspace: str,
    principal: Principal,
    approver_role: str,
    rationale: str | None = None,
) -> PromotionRecord:
    """Promote a workbook's report and model together to ``to_stage`` (``"test"`` or
    ``"prod"``). Refuses outright, before any deploy call, if `promotion_blockers`
    reports any -- never a partial or guessed deploy. MA-09's own "explicit release
    approval by PM" is `rationale`, required (and validated) only for ``to_stage ==
    "prod"``."""
    blockers = await promotion_blockers(
        pool, graph_name, build_store, promotion_store, workbook_id=workbook_id, to_stage=to_stage,
    )
    if blockers:
        raise InvalidRequestError(
            f"workbook '{workbook_id}' cannot be promoted to {to_stage!r}: " + "; ".join(blockers)
        )

    cleaned_rationale = _clean_rationale(rationale or "") if to_stage == "prod" else None
    started_at = _now()
    steps: list[PromotionStep] = []

    async def finish(state: str, *, model_git_ref: str | None, report_deploy_id: str | None) -> PromotionRecord:
        record = PromotionRecord(
            id=f"promotion_{new_ulid()}",
            workbook_id=workbook_id,
            to_stage=to_stage,
            workspace=workspace,
            state=state,
            steps=tuple(steps),
            model_git_ref=model_git_ref,
            report_deploy_id=report_deploy_id,
            approved_by=principal.value,
            approver_role=approver_role,
            rationale=cleaned_rationale,
            triggered_by=principal.value,
            started_at=started_at,
            finished_at=_now(),
        )
        await promotion_store.record(record)
        if state == "SUCCEEDED":
            await writer.append_event(
                mu_promoted(
                    source=writer.event_source, workbook_id=workbook_id, to_stage=to_stage,
                    workspace=workspace, promotion_run_id=record.id, principal=principal,
                )
            )
        return record

    try:
        report_record = await deploy_report(
            pool, graph_name, writer, target_adapter, report_deploy_store,
            workbook_id=workbook_id, workspace=workspace, principal=principal,
        )
    except (ElementNotFoundError, ReportDeployError) as exc:
        # `promotion_blockers` already checked the report exists and its model is
        # BUILT/PUBLISHED -- this is defensive against a race, not an expected path.
        steps.append(PromotionStep("report deploy", False, str(exc)))
        return await finish("FAILED", model_git_ref=None, report_deploy_id=None)

    steps.append(PromotionStep(
        "report deploy", report_record.state == "SUCCEEDED",
        report_record.steps[-1].detail if report_record.steps else report_record.state,
    ))
    if report_record.state != "SUCCEEDED":
        return await finish("FAILED", model_git_ref=None, report_deploy_id=report_record.id)

    family_id = await _family_for_workbook(pool, graph_name, workbook_id)
    latest_build = await build_store.latest(family_id) if family_id else None
    if latest_build is None or latest_build.state != "SUCCEEDED" or not latest_build.git_ref:
        # Also defensive -- `promotion_blockers` already checked this.
        steps.append(PromotionStep("model deploy", False, "no successful build to promote"))
        return await finish("FAILED", model_git_ref=None, report_deploy_id=report_record.id)

    try:
        deployment = await target_adapter.deploy(workspace=workspace, git_ref=latest_build.git_ref)
    except TargetAdapterError as exc:
        steps.append(PromotionStep("model deploy", False, str(exc)))
        return await finish("FAILED", model_git_ref=latest_build.git_ref, report_deploy_id=report_record.id)

    if not deployment.ok:
        steps.append(PromotionStep("model deploy", False, deployment.detail or "deployment failed"))
        return await finish("FAILED", model_git_ref=latest_build.git_ref, report_deploy_id=report_record.id)

    steps.append(PromotionStep("model deploy", True, deployment.detail or deployment.deployment_id))
    return await finish("SUCCEEDED", model_git_ref=latest_build.git_ref, report_deploy_id=report_record.id)


# --------------------------------------------------------------------- the Release Board


async def _sites_for_workbooks(
    pool: asyncpg.Pool, graph_name: str, workbook_ids: Sequence[str]
) -> dict[str, str | None]:
    """Each workbook's own real Site id, via the two-hop ``CONTAINS`` traversal
    (Site -> Project -> Workbook) `estate.py`'s own ``_parents`` already walks once each
    -- walked twice here in one query since this module needs the site directly."""
    if not workbook_ids:
        return {}
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            f"""
            SELECT wb_edge.to_id AS workbook_id, site_edge.from_id AS site_id
              FROM {EDGE_INDEX_TABLE} wb_edge
              JOIN {EDGE_INDEX_TABLE} site_edge
                ON site_edge.to_id = wb_edge.from_id AND site_edge.label = 'CONTAINS'
                   AND site_edge.graph = wb_edge.graph
             WHERE wb_edge.graph = $1 AND wb_edge.label = 'CONTAINS'
               AND wb_edge.to_id = ANY($2::text[])
            """,
            graph_name, list(workbook_ids),
        )
    out: dict[str, str | None] = dict.fromkeys(workbook_ids)
    for row in rows:
        out[row["workbook_id"]] = row["site_id"]
    return out


def _current_stage(wb_promotions: Mapping[str, PromotionRecord], *, g3_approved: bool) -> str:
    """This workbook's own real current pipeline stage, derived from real
    `promotion_run` rows -- never a stored flag."""
    prod = wb_promotions.get("prod")
    if prod is not None and prod.state == "SUCCEEDED":
        return "PROD"
    test = wb_promotions.get("test")
    if test is not None and test.state == "SUCCEEDED":
        return "TEST"
    return "ACCEPTED" if g3_approved else "NOT_ACCEPTED"


async def release_board(
    pool: asyncpg.Pool,
    graph_name: str,
    build_store: BuildStore,
    promotion_store: PromotionStore,
) -> dict[str, Any]:
    """"Release Board shows per train: MUs by pipeline stage, blockers, and the release
    evidence bundle" plus "parallel-run window... visible per MU and per site" -- see
    the module docstring for what this deliberately does not attempt (decommission
    readiness, G4)."""
    trains = await list_trains(pool, graph_name)
    workbook_ids = sorted({m["id"] for t in trains for m in t["members"]})
    promotions = await promotion_store.all_for_workbooks(workbook_ids)
    decisions = await _live_gate_decisions(pool, graph_name)
    approved_workbooks = {
        d.get("subject_ref")
        for d in decisions.values()
        if d.get("gate") == GATE and d.get("decision") == "APPROVED"
    }
    sites = await _sites_for_workbooks(pool, graph_name, workbook_ids)
    site_ids = sorted({s for s in sites.values() if s})
    async with pool.acquire() as conn:
        site_properties = await hydrate(conn, graph_name, "Site", site_ids)

    train_rows = []
    for train in trains:
        mu_rows = []
        for member in train["members"]:
            wb_id = member["id"]
            wb_promotions = promotions.get(wb_id, {})
            stage = _current_stage(wb_promotions, g3_approved=wb_id in approved_workbooks)
            next_stage = _NEXT_STAGE[stage]
            blockers = (
                await promotion_blockers(
                    pool, graph_name, build_store, promotion_store, workbook_id=wb_id, to_stage=next_stage,
                )
                if next_stage
                else []
            )
            mu_rows.append({
                "workbook_id": wb_id,
                "name": member["name"],
                "sequence": member["sequence"],
                "stage": stage,
                "next_stage": next_stage,
                "blockers": blockers,
                "evidence": [record.as_dict() for record in wb_promotions.values()],
            })
        train_rows.append({"id": train["id"], "name": train["name"], "mus": mu_rows})

    by_site: dict[str, list[str]] = {}
    for wb_id, site_id in sites.items():
        if site_id:
            by_site.setdefault(site_id, []).append(wb_id)

    site_rows = []
    for site_id, wb_ids in by_site.items():
        prod_starts = sorted(
            promotions[wb_id]["prod"].finished_at
            for wb_id in wb_ids
            if "prod" in promotions.get(wb_id, {}) and promotions[wb_id]["prod"].state == "SUCCEEDED"
        )
        window_start = prod_starts[0] if prod_starts else None
        window_end = _window_end(window_start)
        site_rows.append({
            "site_id": site_id,
            "name": (site_properties.get(site_id) or {}).get("name", site_id),
            "released_mu_count": len(prod_starts),
            "total_mu_count": len(wb_ids),
            "parallel_run_start": window_start,
            "parallel_run_end": window_end,
        })
    site_rows.sort(key=lambda r: r["name"])

    return {"trains": train_rows, "sites": site_rows}


def _window_end(window_start: str | None) -> str | None:
    if window_start is None:
        return None
    start = datetime.fromisoformat(window_start.replace("Z", "+00:00"))
    end = start + timedelta(weeks=DEFAULT_PARALLEL_WINDOW_WEEKS)
    return end.isoformat(timespec="milliseconds").replace("+00:00", "Z")


class ReleaseService:
    """Binds the module-level functions to one pool/graph/writer/target adapter/report
    deploy store/build store/promotion store -- the identical "pre-bound object on
    app.state" shape `G3CardService` already takes, so `routes_release.py` needs no
    `graph_name` of its own to call this."""

    def __init__(
        self,
        pool: asyncpg.Pool,
        *,
        graph_name: str,
        writer: GraphWriter,
        target_adapter: TargetAdapter,
        report_deploy_store: ReportDeployStore,
        build_store: BuildStore,
        promotion_store: PromotionStore,
        workspace_test: str,
        workspace_prod: str,
    ) -> None:
        self._pool = pool
        self._graph = graph_name
        self._writer = writer
        self._target_adapter = target_adapter
        self._report_deploy_store = report_deploy_store
        self._build_store = build_store
        self._promotion_store = promotion_store
        self._workspace_test = workspace_test
        self._workspace_prod = workspace_prod

    async def board(self) -> dict[str, Any]:
        return await release_board(self._pool, self._graph, self._build_store, self._promotion_store)

    async def blockers(self, workbook_id: str, *, to_stage: str) -> list[str]:
        return await promotion_blockers(
            self._pool, self._graph, self._build_store, self._promotion_store,
            workbook_id=workbook_id, to_stage=to_stage,
        )

    async def promote_to_test(self, workbook_id: str, *, principal: Principal) -> PromotionRecord:
        return await promote_workbook(
            self._pool, self._graph, self._writer, self._target_adapter,
            self._report_deploy_store, self._build_store, self._promotion_store,
            workbook_id=workbook_id, to_stage="test", workspace=self._workspace_test,
            principal=principal, approver_role="platform_engineer",
        )

    async def promote_to_prod(self, workbook_id: str, *, rationale: str, principal: Principal) -> PromotionRecord:
        return await promote_workbook(
            self._pool, self._graph, self._writer, self._target_adapter,
            self._report_deploy_store, self._build_store, self._promotion_store,
            workbook_id=workbook_id, to_stage="prod", workspace=self._workspace_prod,
            principal=principal, approver_role="programme_manager", rationale=rationale,
        )


__all__ = [
    "PIPELINE_STAGES",
    "PROMOTION_TABLE",
    "PostgresPromotionStore",
    "PromotionRecord",
    "PromotionStep",
    "PromotionStore",
    "ReleaseService",
    "promote_workbook",
    "promotion_blockers",
    "release_board",
]
