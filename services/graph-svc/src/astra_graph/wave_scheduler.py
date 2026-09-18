"""Wave scheduler: admission control for MUs by train with concurrency and budget limits.

Story S12.1.2: Scheduler admits MUs by train sequence subject to:
- Family state (BUILT or later)
- Executor concurrency per source site and per Fabric workspace
- Model-gateway budget (cumulative tokens per window)
- WIP per train (work-in-progress limit)
- Train/site pause state
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Optional

from asyncpg import Record

from .db import Database


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
    blocking_constraint: Optional[SchedulerConstraint] = None
    reason: str = ""


class WaveScheduler:
    """Evaluates MU admission constraints."""

    def __init__(self, db: Database):
        self.db = db

    async def evaluate_admission(
        self,
        mu_ref: str,
        train_id: str,
        site_id: str,
    ) -> SchedulerDecision:
        """Evaluate whether an MU can be admitted to execution.

        Args:
            mu_ref: Workbook node ID (the MU's identity)
            train_id: ReleaseTrain node ID
            site_id: Source site LUID

        Returns:
            SchedulerDecision with admitted flag and blocking constraint (if any)
        """
        # Check train pause state
        if await self._train_is_paused(train_id):
            return SchedulerDecision(
                admitted=False,
                blocking_constraint=SchedulerConstraint.PAUSED_TRAIN,
                reason=f"Train {train_id} is paused",
            )

        # Check site pause state
        if await self._site_is_paused(site_id):
            return SchedulerDecision(
                admitted=False,
                blocking_constraint=SchedulerConstraint.PAUSED_SITE,
                reason=f"Site {site_id} is paused",
            )

        # Check family state (BUILT or later)
        family_state = await self._get_family_state_for_mu(mu_ref)
        if family_state is None:
            return SchedulerDecision(
                admitted=False,
                blocking_constraint=SchedulerConstraint.FAMILY_STATE,
                reason=f"MU {mu_ref} has no family or family state is not BUILT or later",
            )

        family_states_ordered = (
            "PROPOSED", "SINGLETON", "DRAFT", "IN_REVIEW", "APPROVED", "BUILT",
            "PUBLISHED", "DEPRECATED"
        )
        built_idx = family_states_ordered.index("BUILT")
        if family_states_ordered.index(family_state) < built_idx:
            return SchedulerDecision(
                admitted=False,
                blocking_constraint=SchedulerConstraint.FAMILY_STATE,
                reason=f"Family state {family_state} < BUILT",
            )

        # Check executor concurrency per site
        site_concurrency = await self._get_active_mu_count_by_site(site_id)
        site_limit = await self._get_site_concurrency_limit(site_id)
        if site_concurrency >= site_limit:
            return SchedulerDecision(
                admitted=False,
                blocking_constraint=SchedulerConstraint.EXECUTOR_CONCURRENCY_SITE,
                reason=f"Site {site_id} concurrency at limit ({site_concurrency}/{site_limit})",
            )

        # Check executor concurrency per Fabric workspace
        workspace_id = await self._get_workspace_for_mu(mu_ref)
        if workspace_id:
            workspace_concurrency = await self._get_active_mu_count_by_workspace(workspace_id)
            workspace_limit = await self._get_workspace_concurrency_limit(workspace_id)
            if workspace_concurrency >= workspace_limit:
                return SchedulerDecision(
                    admitted=False,
                    blocking_constraint=SchedulerConstraint.EXECUTOR_CONCURRENCY_WORKSPACE,
                    reason=f"Workspace {workspace_id} concurrency at limit "
                           f"({workspace_concurrency}/{workspace_limit})",
                )

        # Check model-gateway budget
        if not await self._has_model_gateway_budget():
            return SchedulerDecision(
                admitted=False,
                blocking_constraint=SchedulerConstraint.MODEL_GATEWAY_BUDGET,
                reason="Model-gateway budget exhausted",
            )

        # Check per-train WIP limit
        train_wip = await self._get_train_wip_usage(train_id)
        train_wip_limit = await self._get_train_wip_limit(train_id)
        if train_wip_limit is not None and train_wip >= train_wip_limit:
            return SchedulerDecision(
                admitted=False,
                blocking_constraint=SchedulerConstraint.WIP_LIMIT,
                reason=f"Train {train_id} WIP at limit ({train_wip}/{train_wip_limit})",
            )

        return SchedulerDecision(
            admitted=True,
            blocking_constraint=None,
            reason="All constraints satisfied",
        )

    async def _train_is_paused(self, train_id: str) -> bool:
        """Check if a train is paused via ReleaseTrain.paused property."""
        row = await self.db.fetchrow(
            """
            SELECT EXISTS(
                SELECT 1 FROM nodes
                WHERE nid = $1 AND label = 'ReleaseTrain' AND (props->>'paused')::boolean = true
            ) as paused
            """,
            train_id,
        )
        return row["paused"] if row else False

    async def _site_is_paused(self, site_id: str) -> bool:
        """Check if a site is paused via Site.paused property."""
        row = await self.db.fetchrow(
            """
            SELECT EXISTS(
                SELECT 1 FROM nodes
                WHERE nid = $1 AND label = 'Site' AND (props->>'paused')::boolean = true
            ) as paused
            """,
            site_id,
        )
        return row["paused"] if row else False

    async def _get_family_state_for_mu(self, mu_ref: str) -> Optional[str]:
        """Get the family state for an MU (workbook)."""
        row = await self.db.fetchrow(
            """
            SELECT mf.props->>'state' as state
            FROM nodes wb
            JOIN edges e ON e.tail = wb.nid AND e.label = 'IN_FAMILY'
            JOIN nodes mf ON mf.nid = e.head
            WHERE wb.nid = $1 AND wb.label = 'Workbook' AND mf.label = 'ModelFamily'
            LIMIT 1
            """,
            mu_ref,
        )
        return row["state"] if row else None

    async def _get_active_mu_count_by_site(self, site_id: str) -> int:
        """Count active MUs (PROVING, MENDING, ESCALATED) for a site."""
        active_states = ("PROVING", "MENDING", "ESCALATED")
        row = await self.db.fetchrow(
            """
            SELECT COUNT(*) as count
            FROM nodes wb
            JOIN edges e ON e.head = wb.nid AND e.label = 'IN_ESTATE'
            JOIN nodes site ON site.nid = e.tail
            WHERE site.nid = $1
              AND site.label = 'Site'
              AND wb.label = 'Workbook'
              AND (wb.props->>'mu_state') = ANY($2)
            """,
            site_id,
            list(active_states),
        )
        return row["count"] if row else 0

    async def _get_site_concurrency_limit(self, site_id: str) -> int:
        """Get executor concurrency limit for a site. Defaults to 5 if not configured."""
        row = await self.db.fetchrow(
            """
            SELECT (props->>'executor_concurrency_limit')::int as limit
            FROM nodes
            WHERE nid = $1 AND label = 'Site'
            """,
            site_id,
        )
        return row["limit"] if row and row["limit"] else 5

    async def _get_workspace_for_mu(self, mu_ref: str) -> Optional[str]:
        """Get the Fabric workspace ID for an MU's target semantic model."""
        row = await self.db.fetchrow(
            """
            SELECT ws.nid as workspace_id
            FROM nodes wb
            JOIN edges e ON e.tail = wb.nid AND e.label = 'TARGETS'
            JOIN nodes sm ON sm.nid = e.head AND sm.label = 'SemanticModel'
            JOIN edges e2 ON e2.tail = sm.nid AND e2.label = 'IN_WORKSPACE'
            JOIN nodes ws ON ws.nid = e2.head AND ws.label = 'Workspace'
            WHERE wb.nid = $1 AND wb.label = 'Workbook'
            LIMIT 1
            """,
            mu_ref,
        )
        return row["workspace_id"] if row else None

    async def _get_active_mu_count_by_workspace(self, workspace_id: str) -> int:
        """Count active MUs for a workspace."""
        active_states = ("PROVING", "MENDING", "ESCALATED")
        row = await self.db.fetchrow(
            """
            SELECT COUNT(DISTINCT wb.nid) as count
            FROM nodes wb
            JOIN edges e ON e.tail = wb.nid AND e.label = 'TARGETS'
            JOIN nodes sm ON sm.nid = e.head AND sm.label = 'SemanticModel'
            JOIN edges e2 ON e2.tail = sm.nid AND e2.label = 'IN_WORKSPACE'
            JOIN nodes ws ON ws.nid = e2.head AND ws.label = 'Workspace'
            WHERE ws.nid = $1
              AND wb.label = 'Workbook'
              AND (wb.props->>'mu_state') = ANY($2)
            """,
            workspace_id,
            list(active_states),
        )
        return row["count"] if row else 0

    async def _get_workspace_concurrency_limit(self, workspace_id: str) -> int:
        """Get executor concurrency limit for a workspace. Defaults to 10 if not configured."""
        row = await self.db.fetchrow(
            """
            SELECT (props->>'executor_concurrency_limit')::int as limit
            FROM nodes
            WHERE nid = $1 AND label = 'Workspace'
            """,
            workspace_id,
        )
        return row["limit"] if row and row["limit"] else 10

    async def _has_model_gateway_budget(self) -> bool:
        """Check if model-gateway has budget remaining. For now, always return True."""
        # TODO: Implement budget tracking from model-gateway events
        return True

    async def _get_train_wip_usage(self, train_id: str) -> int:
        """Count MUs in the train that are in-progress (not GENERATED, not terminal)."""
        in_progress_states = ("PROVING", "FAILED", "MENDING", "ESCALATED", "ADJUDICATED")
        row = await self.db.fetchrow(
            """
            SELECT COUNT(wb.nid) as count
            FROM nodes wb
            JOIN edges e ON e.tail = wb.nid AND e.label = 'IN_TRAIN'
            WHERE e.head = $1
              AND wb.label = 'Workbook'
              AND (wb.props->>'mu_state') = ANY($2)
            """,
            train_id,
            list(in_progress_states),
        )
        return row["count"] if row else 0

    async def _get_train_wip_limit(self, train_id: str) -> Optional[int]:
        """Get per-train WIP limit from ReleaseTrain.wip_limits JSON."""
        row = await self.db.fetchrow(
            """
            SELECT (props->'wip_limits'->>'train')::int as limit
            FROM nodes
            WHERE nid = $1 AND label = 'ReleaseTrain'
            """,
            train_id,
        )
        return row["limit"] if row and row["limit"] else None
