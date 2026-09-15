"""Execution safety -- spec §18.2, story S11.2.1, opens F11.2.

    "As an InfoSec reviewer, I want generated DAX, M and adapter queries to execute only
    in sandboxed, read-only contexts, so that a generated artefact cannot change data or
    reach anything it should not.

    Acceptance criteria:
    - Target execution uses XMLA read-only against dev/test workspaces with a service
      principal that has no write on data; production execution is limited to the
      regression runner with the same read-only principal
    - Source execution uses the adapter's read-only credential; custom SQL replay is
      disabled unless the tenant policy enables it and then runs with a statement
      allow-list (SELECT only)
    - Executor workers run with no outbound network except the two data endpoints;
      resource limits per query"

**Scope, per three explicit decisions taken before any code was written.** Target
execution is 100% fixture today (`target_fake.FixtureTargetAdapter`) and no adapter-fabric
package exists anywhere in this codebase; building a real XMLA client is a separate,
much larger undertaking (effectively starting that package) no story has asked for yet.
This module instead makes the AC's *safety property* real and enforced against the
adapter contract that exists today: which caller may execute against which workspace,
and how many rows a query may return -- both real, checkable, and disclosed as not yet
connected to a live Fabric tenant (the identical "disclosed, not yet connected" posture
ADR 0079/ADR 0080 already established for Entra ID and SVID issuance).

**"XMLA read-only... service principal that has no write on data" is structurally true
today, not something this module has a call site left to enforce.** `TargetAdapter.
evaluate` (§7.1's own DAX/XMLA method) takes a query string and returns a `ResultSet` --
no write-shaped parameter exists on that method at all (confirmed by reading
`target_contract.py` in full); `commit`/`deploy` are the Protocol's only write-shaped
methods, and they are a different, already-accepted write path (publishing a model,
gated to the Steward, spec §19's own "acting integrations run only through the Steward
and the target adapter"). The real, buildable contribution here is the credential
*reservation*: `deploy/terraform/identity.tf` reserves a Key Vault secret slot for a
read-only Fabric execution principal, distinct from the (potentially write-capable)
deploy-time one already reserved there.

**"Production execution is limited to the regression runner" is the one AC bullet with a
real, structural gap today**, confirmed by direct research: `workspace` on
`POST /v1/workbooks/{id}:execute-parity-cases` is an unrestricted free string, and the
on-demand route and the regression scheduler share the exact same `CaseExecutionService`/
`target_adapter` instance -- nothing today stops a Parity Engineer's own on-demand call
from naming a production workspace. `authorize_target_workspace` below closes that gap
for real: a `graph`-scoped, versioned `ExecutionSafetyPolicy` (the identical
"platform engineer's edit is a new version, never an overwrite" shape `mender_config`/
`tolerance_charter_version` already established) names which workspaces this tenant
calls production, and only `REGRESSION_RUNNER_PRINCIPAL` may execute against one.

**A literal identity check, not an `AgentScope` gate.** `agent_identity.py`'s own module
docstring is explicit that its enforcement "never scopes a `user:`/`service:` principal" --
this AC bullet has to refuse a *human* Parity Engineer too, not only a differently-scoped
agent, so it does not belong in that module. `REGRESSION_RUNNER_PRINCIPAL` is duplicated
here (`"agent:steward"`) rather than imported from `regression.py`, the identical
"written again rather than imported since this is a different epic's own module"
footing `case_execution.py`'s own `_maps_to` already set for `compositor._maps_to` --
`regression.py` imports `case_execution.py` (`CaseExecutionService`), so the reverse
import would be circular.

**Resource limits are two real, independent mechanisms, one per side.** Target-side:
`ExecutionCharter.max_rows` (adapter-sdk) wraps the DAX query in a real `TOPN`
(`case_execution_query.build_dax_query`) -- enforced in the query text itself, not by
discarding rows after a target engine already spent resources producing them. Source-side
live replay: `packages/adapter-tableau`'s own `LiveReplayPolicy`/
`PolicyGatedLiveQueryRunner` (that package's own tenant-policy concern, read from that
worker's own environment -- see that module's docstring for why graph-svc's Postgres-
backed policy here does not reach it).

**Network egress** is a Terraform/Helm concern (`deploy/terraform/egress.tf`,
`deploy/helm/astra-data/templates/networkpolicy.yaml`), not something this module
enforces at runtime -- see those files' own comments for the two data endpoints added
by this story.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Protocol

import asyncpg

from .errors import ForbiddenError
from .ids import new_ulid

logger = logging.getLogger(__name__)

#: `regression.STEWARD_PRINCIPAL`, duplicated -- see this module's own docstring for why
#: not imported.
REGRESSION_RUNNER_PRINCIPAL = "agent:steward"

EXECUTION_SAFETY_POLICY_TABLE = "public.execution_safety_policy"


class ProductionExecutionRefused(ForbiddenError):
    """A production-classified workspace was named by a caller other than the regression
    runner (spec §18.2's own "an out-of-scope call is refused"). Rendered as 403, the
    same shape every other refusal in this service already has."""

    error_code = "production_execution_refused"


@dataclass(frozen=True, slots=True)
class ExecutionSafetyPolicy:
    """This tenant's own execution-safety configuration -- versioned, per-graph, the
    identical shape `MenderConfig`/`ToleranceCharterVersion` already have. Always the
    dataclass's own default (no workspace classified production) until a platform
    engineer records one; the honest state for a deployment nobody has configured yet,
    the same footing `ExecutionCharter`'s own default already has in
    `CaseExecutionService`."""

    production_workspaces: frozenset[str] = frozenset()
    version: int = 0

    def as_dict(self) -> dict[str, object]:
        return {
            "production_workspaces": sorted(self.production_workspaces),
            "version": self.version,
        }


class ExecutionSafetyPolicyStore(Protocol):
    async def latest(self) -> ExecutionSafetyPolicy: ...

    async def save(self, policy: ExecutionSafetyPolicy, *, updated_by: str) -> ExecutionSafetyPolicy: ...


class PostgresExecutionSafetyPolicyStore:
    """Versioned, per-graph ('per tenant') -- the identical footing `PostgresMenderConfigStore`
    already established for its own tenant-scoped, append-only config."""

    def __init__(self, pool: asyncpg.Pool, *, graph_name: str) -> None:
        self._pool = pool
        self._graph = graph_name

    async def latest(self) -> ExecutionSafetyPolicy:
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                f"SELECT version, production_workspaces FROM {EXECUTION_SAFETY_POLICY_TABLE} "
                f"WHERE graph = $1 ORDER BY version DESC LIMIT 1",
                self._graph,
            )
        if row is None:
            return ExecutionSafetyPolicy()
        return ExecutionSafetyPolicy(
            production_workspaces=frozenset(row["production_workspaces"] or ()),
            version=row["version"],
        )

    async def save(self, policy: ExecutionSafetyPolicy, *, updated_by: str) -> ExecutionSafetyPolicy:
        async with self._pool.acquire() as conn, conn.transaction():
            current = await conn.fetchval(
                f"SELECT MAX(version) FROM {EXECUTION_SAFETY_POLICY_TABLE} WHERE graph = $1",
                self._graph,
            )
            version = (current or 0) + 1
            await conn.execute(
                f"""INSERT INTO {EXECUTION_SAFETY_POLICY_TABLE}
                    (id, graph, version, production_workspaces, updated_by)
                    VALUES ($1, $2, $3, $4, $5)""",
                f"execsafe_{new_ulid()}", self._graph, version,
                sorted(policy.production_workspaces), updated_by,
            )
        return ExecutionSafetyPolicy(production_workspaces=policy.production_workspaces, version=version)


class InMemoryExecutionSafetyPolicyStore:
    def __init__(self, policy: ExecutionSafetyPolicy | None = None) -> None:
        self._policy = policy or ExecutionSafetyPolicy()

    async def latest(self) -> ExecutionSafetyPolicy:
        return self._policy

    async def save(self, policy: ExecutionSafetyPolicy, *, updated_by: str) -> ExecutionSafetyPolicy:
        self._policy = ExecutionSafetyPolicy(
            production_workspaces=policy.production_workspaces, version=self._policy.version + 1
        )
        return self._policy


def authorize_target_workspace(principal_value: str, *, workspace: str, policy: ExecutionSafetyPolicy) -> None:
    """Story S11.2.1's own AC: "production execution is limited to the regression
    runner". A workspace this tenant has not named production is unaffected -- every
    dev/test execution, by any caller, is exactly as unrestricted as it already was."""
    if workspace not in policy.production_workspaces:
        return
    if principal_value == REGRESSION_RUNNER_PRINCIPAL:
        return
    logger.warning(
        "production execution refused: principal=%s workspace=%s", principal_value, workspace
    )
    raise ProductionExecutionRefused(
        f"workspace '{workspace}' is classified production for this tenant; execution "
        f"is limited to the regression runner ('{REGRESSION_RUNNER_PRINCIPAL}'), not "
        f"'{principal_value}'"
    )


__all__ = [
    "EXECUTION_SAFETY_POLICY_TABLE",
    "REGRESSION_RUNNER_PRINCIPAL",
    "ExecutionSafetyPolicy",
    "ExecutionSafetyPolicyStore",
    "InMemoryExecutionSafetyPolicyStore",
    "PostgresExecutionSafetyPolicyStore",
    "ProductionExecutionRefused",
    "authorize_target_workspace",
]
