"""Wave scheduler: admission control for MUs by train with concurrency and budget limits.

Story S12.1.2: Scheduler admits MUs by train sequence subject to:
- Family state (BUILT or later)
- Executor concurrency per source site and per Fabric workspace
- Model-gateway budget (cumulative tokens per window)
- WIP per train (work-in-progress limit)
- Train/site pause state

Queries go through ``GraphRepository.run_read_only_cypher`` -- the same sanctioned,
read-only Cypher path the ``/v1/cypher`` route uses -- because this module reads
graph state (node properties, edge traversal) that only exists inside Apache AGE,
not in a plain relational table. Site->Workbook has no direct edge (the ontology
chains Site -[:CONTAINS]-> Project -[:CONTAINS]-> Workbook); the Fabric workspace is
a plain string property on SemanticModel (``workspace``), matched to a Workbook's
ModelFamily by ``SemanticModel.family_ref`` -- there is no ``Workspace`` node type or
``IN_WORKSPACE``/``TARGETS`` edge in this ontology, so both lookups are written
against the real schema rather than an invented one.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any

from .graph.repository import GraphRepository

_CYPHER_TIMEOUT_SECONDS = 5


class SchedulerConstraint(str, Enum):
    """Reason an MU was blocked from admission."""
    FAMILY_STATE = "FAMILY_STATE"  # Family state < BUILT
    EXECUTOR_CONCURRENCY_SITE = "EXECUTOR_CONCURRENCY_SITE"  # Per-site limit reached
    EXECUTOR_CONCURRENCY_WORKSPACE = "EXECUTOR_CONCURRENCY_WORKSPACE"  # Per-workspace limit
    MODEL_GATEWAY_BUDGET = "MODEL_GATEWAY_BUDGET"  # Budget exhausted
    WIP_LIMIT = "WIP_LIMIT"  # Per-train WIP limit reached
    PAUSED_TRAIN = "PAUSED_TRAIN"  # Train is paused
    PAUSED_SITE = "PAUSED_SITE"  # Site is paused


@dataclass
class SchedulerDecision:
    """Scheduler's admission decision for an MU."""
    admitted: bool
    blocking_constraint: SchedulerConstraint | None = None
    reason: str = ""


#: Active MU states -- work genuinely in flight, per S12.1.1's own MU_STATES vocabulary.
_ACTIVE_MU_STATES = ("PROVING", "MENDING", "ESCALATED")

#: In-progress states counted against a train's own WIP limit -- everything past
#: GENERATED (not yet admitted) and short of a terminal state.
_IN_PROGRESS_MU_STATES = ("PROVING", "FAILED", "MENDING", "ESCALATED", "ADJUDICATED")

_FAMILY_STATES_ORDERED = (
    "PROPOSED", "SINGLETON", "DRAFT", "IN_REVIEW", "APPROVED", "BUILT",
    "PUBLISHED", "DEPRECATED",
)


class WaveScheduler:
    """Evaluates MU admission constraints against the real estate graph."""

    def __init__(self, repository: GraphRepository):
        self.repository = repository

    async def _cypher_one(
        self, query: str, column: str, params: dict[str, Any]
    ) -> Any:
        rows, _ = await self.repository.run_read_only_cypher(
            query, [column], params,
            timeout_seconds=_CYPHER_TIMEOUT_SECONDS, row_limit=1,
        )
        return rows[0][column] if rows else None

    async def evaluate_admission(
        self,
        mu_ref: str,
        train_id: str,
        site_id: str,
    ) -> SchedulerDecision:
        """Evaluate whether an MU can be admitted to execution.

        Args:
            mu_ref: Workbook node id (the MU's own identity)
            train_id: ReleaseTrain node id
            site_id: Site node id

        Returns:
            SchedulerDecision with admitted flag and blocking constraint (if any)
        """
        if await self._train_is_paused(train_id):
            return SchedulerDecision(
                admitted=False,
                blocking_constraint=SchedulerConstraint.PAUSED_TRAIN,
                reason=f"Train {train_id} is paused",
            )

        if await self._site_is_paused(site_id):
            return SchedulerDecision(
                admitted=False,
                blocking_constraint=SchedulerConstraint.PAUSED_SITE,
                reason=f"Site {site_id} is paused",
            )

        family_state = await self._get_family_state_for_mu(mu_ref)
        if family_state is None or (
            family_state in _FAMILY_STATES_ORDERED
            and _FAMILY_STATES_ORDERED.index(family_state)
            < _FAMILY_STATES_ORDERED.index("BUILT")
        ):
            return SchedulerDecision(
                admitted=False,
                blocking_constraint=SchedulerConstraint.FAMILY_STATE,
                reason=(
                    f"Family state {family_state} < BUILT" if family_state
                    else f"MU {mu_ref} has no family"
                ),
            )

        site_concurrency = await self._get_active_mu_count_by_site(site_id)
        site_limit = await self._get_site_concurrency_limit(site_id)
        if site_concurrency >= site_limit:
            return SchedulerDecision(
                admitted=False,
                blocking_constraint=SchedulerConstraint.EXECUTOR_CONCURRENCY_SITE,
                reason=f"Site {site_id} concurrency at limit ({site_concurrency}/{site_limit})",
            )

        workspace = await self._get_workspace_for_mu(mu_ref)
        if workspace:
            workspace_concurrency = await self._get_active_mu_count_by_workspace(workspace)
            workspace_limit = self._workspace_concurrency_limit()
            if workspace_concurrency >= workspace_limit:
                return SchedulerDecision(
                    admitted=False,
                    blocking_constraint=SchedulerConstraint.EXECUTOR_CONCURRENCY_WORKSPACE,
                    reason=(
                        f"Workspace {workspace} concurrency at limit "
                        f"({workspace_concurrency}/{workspace_limit})"
                    ),
                )

        if not await self._has_model_gateway_budget():
            return SchedulerDecision(
                admitted=False,
                blocking_constraint=SchedulerConstraint.MODEL_GATEWAY_BUDGET,
                reason="Model-gateway budget exhausted",
            )

        train_wip = await self._get_train_wip_usage(train_id)
        train_wip_limit = await self._get_train_wip_limit(train_id)
        if train_wip_limit is not None and train_wip >= train_wip_limit:
            return SchedulerDecision(
                admitted=False,
                blocking_constraint=SchedulerConstraint.WIP_LIMIT,
                reason=f"Train {train_id} WIP at limit ({train_wip}/{train_wip_limit})",
            )

        return SchedulerDecision(
            admitted=True, blocking_constraint=None, reason="All constraints satisfied",
        )

    async def _train_is_paused(self, train_id: str) -> bool:
        paused = await self._cypher_one(
            "MATCH (t:ReleaseTrain) WHERE t.id = $train_id RETURN t.paused AS paused",
            "paused", {"train_id": train_id},
        )
        return bool(paused)

    async def _site_is_paused(self, site_id: str) -> bool:
        paused = await self._cypher_one(
            "MATCH (s:Site) WHERE s.id = $site_id RETURN s.paused AS paused",
            "paused", {"site_id": site_id},
        )
        return bool(paused)

    async def _get_family_state_for_mu(self, mu_ref: str) -> str | None:
        state: str | None = await self._cypher_one(
            "MATCH (wb:Workbook)-[:IN_FAMILY]->(mf:ModelFamily) "
            "WHERE wb.id = $mu_ref RETURN mf.state AS state",
            "state", {"mu_ref": mu_ref},
        )
        return state

    async def _get_active_mu_count_by_site(self, site_id: str) -> int:
        """Site->Workbook has no direct edge; the ontology chains
        Site-[:CONTAINS]->Project-[:CONTAINS]->Workbook."""
        count = await self._cypher_one(
            "MATCH (s:Site)-[:CONTAINS]->(:Project)-[:CONTAINS]->(wb:Workbook) "
            "WHERE s.id = $site_id AND wb.mu_state IN $active_states "
            "RETURN count(DISTINCT wb) AS n",
            "n", {"site_id": site_id, "active_states": list(_ACTIVE_MU_STATES)},
        )
        return int(count) if count is not None else 0

    def _site_concurrency_limit(self) -> int:
        """Executor concurrency limit for a site. R1: a fixed default (5); a
        per-site override property is a disclosed follow-on, not built here."""
        return 5

    async def _get_site_concurrency_limit(self, site_id: str) -> int:
        return self._site_concurrency_limit()

    async def _get_workspace_for_mu(self, mu_ref: str) -> str | None:
        """The Fabric workspace is a plain string property on SemanticModel
        (``workspace``), matched to this MU's family by ``family_ref`` -- there is
        no graph edge from ModelFamily to SemanticModel in this ontology."""
        family_id = await self._cypher_one(
            "MATCH (wb:Workbook)-[:IN_FAMILY]->(mf:ModelFamily) "
            "WHERE wb.id = $mu_ref RETURN mf.id AS family_id",
            "family_id", {"mu_ref": mu_ref},
        )
        if not family_id:
            return None
        workspace: str | None = await self._cypher_one(
            "MATCH (sm:SemanticModel) WHERE sm.family_ref = $family_id "
            "RETURN sm.workspace AS workspace",
            "workspace", {"family_id": family_id},
        )
        return workspace

    async def _get_active_mu_count_by_workspace(self, workspace: str) -> int:
        # SemanticModel has no graph edge to ModelFamily (family_ref is a string, not
        # an edge), so counting active MUs per workspace joins through that string
        # match rather than a traversal.
        result = await self._cypher_one(
            "MATCH (sm:SemanticModel) WHERE sm.workspace = $workspace "
            "WITH collect(sm.family_ref) AS family_refs "
            "MATCH (wb:Workbook)-[:IN_FAMILY]->(mf:ModelFamily) "
            "WHERE mf.id IN family_refs AND wb.mu_state IN $active_states "
            "RETURN count(DISTINCT wb) AS n",
            "n", {"workspace": workspace, "active_states": list(_ACTIVE_MU_STATES)},
        )
        return int(result) if result is not None else 0

    def _workspace_concurrency_limit(self) -> int:
        """Executor concurrency limit for a Fabric workspace. R1: a fixed default
        (10); a per-workspace override is a disclosed follow-on, not built here."""
        return 10

    async def _has_model_gateway_budget(self) -> bool:
        """Model-gateway budget constraint. R1: structurally present, stubbed to
        always allow -- wiring to real gateway spend (S12.2.2) is a disclosed
        follow-on, not built here."""
        return True

    async def _get_train_wip_usage(self, train_id: str) -> int:
        count = await self._cypher_one(
            "MATCH (wb:Workbook)-[:IN_TRAIN]->(t:ReleaseTrain) "
            "WHERE t.id = $train_id AND wb.mu_state IN $in_progress_states "
            "RETURN count(wb) AS n",
            "n", {"train_id": train_id, "in_progress_states": list(_IN_PROGRESS_MU_STATES)},
        )
        return int(count) if count is not None else 0

    async def _get_train_wip_limit(self, train_id: str) -> int | None:
        wip_limits = await self._cypher_one(
            "MATCH (t:ReleaseTrain) WHERE t.id = $train_id RETURN t.wip_limits AS wip_limits",
            "wip_limits", {"train_id": train_id},
        )
        if not isinstance(wip_limits, dict):
            return None
        train_limit = wip_limits.get("train")
        return int(train_limit) if train_limit is not None else None
