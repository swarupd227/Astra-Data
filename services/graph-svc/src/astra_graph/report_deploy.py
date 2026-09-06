"""Committing and deploying a composed report — story S6.1.2, spec §7.1/§7.2, §3.2 (MU
state machine).

    "As a migration engineer, I want the report definition committed to Git and deployed to
    the dev workspace bound to the family model, so that I can open the generated report in
    Fabric within minutes of generation."

    - Commit through the target adapter with the MU id in the message; deployment through
      Fabric Git integration to the dev workspace; report bound to the PUBLISHED or BUILT
      model for its family
    - Deployment failure returns the MU to GENERATED with the error on the MU page; three
      retries with backoff

**A separate action from composing, not a hidden side effect of it.** S6.1.1's own
`compose_report` deliberately stops at "PBIR output validates ... before commit" — this
story's own acceptance criteria is what actually commits. Chaining a deploy silently onto
every compose would change `compose_report`'s already-shipped, already-tested contract for
a behaviour its own story never asked for; a Migration Engineer composes, reviews the
warnings (S6.1.1's own disclosed-binding ones in particular), and deploys as its own
deliberate action — the same two-step shape `build_family`'s own manual retry route gives a
Semantic Model Engineer for the model side.

**Reuses `TargetAdapter.commit`/`.deploy` verbatim (S4.3.1's own contract) — no second
commit/deploy mechanism.** `target_setup.py`'s own docstring already promises this: "when a
real target adapter is built... `build_family` is written against `TargetAdapter` and
cannot tell the difference." `TmdlBundle` is reused for the PBIR bundle's own bytes too —
its own shape (`files: Mapping[str, bytes]`) is a named byte bundle, not something
structurally specific to TMDL despite the name; inventing a same-shaped `PbirBundle` type
only to satisfy a label would be paying for nothing `TargetAdapter.commit`'s own signature
needs. No smoke query: the AC names none for a report (unlike `build_family`'s own
per-table smoke check), and a report's own post-deploy smoke check is real, unbuilt future
scope this story does not invent.

**"Bound to the PUBLISHED or BUILT model" is checked against `SemanticModel.state`
directly, not `ModelFamily.state`.** Before this story, nothing in this codebase ever wrote
`SemanticModel.state = "BUILT"` — `build.py`'s own `finish()` set only `ModelFamily.state`,
even though `SemanticModel.state`'s own declared note already promised "deployment state
within an environment; the family lifecycle is on `ModelFamily.state`" (a promise never
kept until this story needed it kept for real). Fixed at the source (`build.py`'s own
`finish()` now also stamps the `SemanticModel` on a successful build) rather than reading
`ModelFamily.state` here as a workaround — the AC's own wording names the *model*, and a
family that has moved on to a v(n+1) DRAFT while v(n) stays the live `SemanticModel` a
report is bound to is exactly the case a family-level check would get wrong.

**"Deployment failure returns the MU to GENERATED with the error on the MU page" is a
disclosed proxy, the identical footing every other MU-shaped gap in this codebase already
discloses.** No real Migration Unit node or §3.2 state machine exists anywhere (confirmed a
sixth time — S5.4.1, S5.5.1, S5.5.2, S5.5.3 and S6.1.1 each already found the identical
gap), and "the MU page" itself is F10.3's own unbuilt future screen. `ReportDefinition.
deploy_state`/`.deploy_error` (new, additive) carry exactly this fact on the one real node
this action touches — `"GENERATED"` once a commit and deploy both succeed, `"DEPLOY_FAILED"`
with the failing step's own detail once every retry is exhausted. Nothing here claims to
have moved a real MU backward from PROVING, because nothing here ever moved one forward
into it either.

**Retries apply to the deploy call only, not the commit.** A local Git commit is not the
flaky network hop; a Fabric Git-integration sync is. "Three retries" is read as a fixed
*attempt budget* (`DEPLOY_RETRY_DEFAULT = 3`, all three counted, matching the Mender's own
"pass budget (default 3)" reading of the word "budget" rather than "three retries after a
first attempt" = four total), with a small fixed backoff schedule between attempts — the
same "a table, not a formula" shape `adapter-tableau`'s own `throttle.py` already
established for its own retries, sized down for a three-attempt budget rather than
reusing that module's own five-attempt HTTP-429 schedule (a different package, a different
failure domain, and graph-svc does not depend on `astra_adapter_tableau` at all).
"""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Protocol

import asyncpg
from astra_adapter import TargetAdapter, TargetAdapterError
from astra_adapter.target_contract import TmdlBundle

from .compositor import read_report
from .errors import ElementNotFoundError
from .ids import new_ulid
from .lineage import hydrate
from .pbir import emit_pbir
from .principal import Principal
from .tmdl import safe_name
from .writes import GraphWriter

logger = logging.getLogger(__name__)

DEPLOY_TABLE = "public.report_deploy_run"

#: Total deploy attempts, including the first — a fixed budget (see the module docstring),
#: not three retries *after* a first attempt.
DEPLOY_RETRY_DEFAULT = 3

#: A fixed schedule, not a formula — capped rather than doubling forever, the same posture
#: `adapter-tableau`'s own `throttle.py` already takes for its own (unrelated) retries.
_BACKOFF_SECONDS = (2.0, 5.0)


class ReportDeployError(Exception):
    """A report cannot be deployed right now — not yet composed, or bound to a model that
    is not BUILT or PUBLISHED."""


@dataclass(frozen=True, slots=True)
class DeployStep:
    name: str
    ok: bool
    detail: str

    def as_dict(self) -> dict[str, Any]:
        return {"name": self.name, "ok": self.ok, "detail": self.detail}


@dataclass(frozen=True, slots=True)
class DeployRecord:
    id: str
    report_id: str
    workbook_id: str
    state: str
    """``SUCCEEDED`` or ``FAILED``."""
    steps: tuple[DeployStep, ...]
    git_commit_sha: str | None
    git_ref: str | None
    workspace: str | None
    attempts: int
    triggered_by: str
    started_at: str
    finished_at: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "report_id": self.report_id,
            "workbook_id": self.workbook_id,
            "state": self.state,
            "steps": [s.as_dict() for s in self.steps],
            "git_commit_sha": self.git_commit_sha,
            "git_ref": self.git_ref,
            "workspace": self.workspace,
            "attempts": self.attempts,
            "triggered_by": self.triggered_by,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
        }


class ReportDeployStore(Protocol):
    async def record(self, deploy: DeployRecord) -> DeployRecord: ...

    async def latest(self, workbook_id: str) -> DeployRecord | None: ...


class PostgresReportDeployStore:
    def __init__(self, pool: asyncpg.Pool, *, graph_name: str) -> None:
        self._pool = pool
        self._graph = graph_name

    async def record(self, deploy: DeployRecord) -> DeployRecord:
        async with self._pool.acquire() as conn:
            await conn.execute(
                f"""
                INSERT INTO {DEPLOY_TABLE}
                    (id, graph, report_id, workbook_id, state, steps,
                     git_commit_sha, git_ref, workspace, attempts, triggered_by,
                     started_at, finished_at)
                VALUES ($1, $2, $3, $4, $5, $6::jsonb, $7, $8, $9, $10, $11, $12, $13)
                """,
                deploy.id,
                self._graph,
                deploy.report_id,
                deploy.workbook_id,
                deploy.state,
                json.dumps([s.as_dict() for s in deploy.steps]),
                deploy.git_commit_sha,
                deploy.git_ref,
                deploy.workspace,
                deploy.attempts,
                deploy.triggered_by,
                datetime.fromisoformat(deploy.started_at),
                datetime.fromisoformat(deploy.finished_at),
            )
        return deploy

    async def latest(self, workbook_id: str) -> DeployRecord | None:
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                f"""
                SELECT * FROM {DEPLOY_TABLE}
                 WHERE graph = $1 AND workbook_id = $2
              ORDER BY started_at DESC
                 LIMIT 1
                """,
                self._graph,
                workbook_id,
            )
        return _from_row(row) if row else None


def _from_row(row: asyncpg.Record) -> DeployRecord:
    steps_raw = row["steps"]
    steps_list = json.loads(steps_raw) if isinstance(steps_raw, str) else list(steps_raw)
    return DeployRecord(
        id=row["id"],
        report_id=row["report_id"],
        workbook_id=row["workbook_id"],
        state=row["state"],
        steps=tuple(DeployStep(**step) for step in steps_list),
        git_commit_sha=row["git_commit_sha"],
        git_ref=row["git_ref"],
        workspace=row["workspace"],
        attempts=row["attempts"],
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


async def deploy_report(
    pool: asyncpg.Pool,
    graph_name: str,
    writer: GraphWriter,
    target_adapter: TargetAdapter,
    deploy_store: ReportDeployStore,
    *,
    workbook_id: str,
    workspace: str,
    principal: Principal,
    retries: int = DEPLOY_RETRY_DEFAULT,
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
) -> DeployRecord:
    """Commit and deploy the workbook's own current report, entering the disclosed
    ``"GENERATED"`` proxy only if commit and deploy both succeed. Always returns a
    ``DeployRecord`` — a deploy failure is a normal, recorded outcome, not a raised
    exception, except for the two checks that mean this call was never a legitimate deploy
    to begin with (no report yet; the bound model is not BUILT or PUBLISHED)."""
    started_at = _now()

    report = await read_report(pool, graph_name, workbook_id)
    if report is None:
        raise ElementNotFoundError(
            f"workbook '{workbook_id}' has not been composed into a report yet — "
            f"POST /v1/workbooks/{workbook_id}:compose first"
        )
    report_id = str(report["id"])
    model_id = report.get("model_ref")

    async with pool.acquire() as conn:
        models = await hydrate(conn, graph_name, "SemanticModel", [model_id] if model_id else [])
        workbooks = await hydrate(conn, graph_name, "Workbook", [workbook_id])

    model_state = (models.get(model_id) or {}).get("state") if model_id else None
    if model_state not in ("BUILT", "PUBLISHED"):
        raise ReportDeployError(
            f"this report is bound to a model that is not yet BUILT or PUBLISHED "
            f"(state: {model_state!r}) — deploying now would push a report against a "
            f"design that has not been through a real build"
        )

    workbook_name = str((workbooks.get(workbook_id) or {}).get("name") or workbook_id)
    item_path = f"{safe_name(workbook_name)}.Report"
    message = f"Deploy {workbook_name} ({workbook_id}) — MU {workbook_id}"

    bundle_documents = emit_pbir(visuals=report.get("visuals") or [])
    bundle = TmdlBundle(
        files={
            path: json.dumps(document, indent=2, sort_keys=True).encode("utf-8")
            for path, document in bundle_documents.items()
        }
    )

    steps: list[DeployStep] = []
    git_commit_sha: str | None = None
    git_ref: str | None = None

    async def finish(state: str, *, attempts: int) -> DeployRecord:
        record = DeployRecord(
            id=f"deploy_{new_ulid()}",
            report_id=report_id,
            workbook_id=workbook_id,
            state=state,
            steps=tuple(steps),
            git_commit_sha=git_commit_sha,
            git_ref=git_ref,
            workspace=workspace,
            attempts=attempts,
            triggered_by=principal.value,
            started_at=started_at,
            finished_at=_now(),
        )
        await deploy_store.record(record)
        await writer.set_node_properties(
            report_id,
            {
                "pbir_ref": git_commit_sha,
                "deploy_state": "GENERATED" if state == "SUCCEEDED" else "DEPLOY_FAILED",
                "deploy_error": None if state == "SUCCEEDED" else (
                    steps[-1].detail if steps else "unknown error"
                ),
            },
            principal=principal,
        )
        if state == "SUCCEEDED":
            logger.info(
                "report %s (workbook %s) deployed to %s by %s",
                report_id, workbook_id, workspace, principal.value,
            )
        else:
            logger.warning(
                "report %s (workbook %s) deploy failed at step %r: %s",
                report_id, workbook_id,
                steps[-1].name if steps else "(none)",
                steps[-1].detail if steps else "",
            )
        return record

    try:
        commit = await target_adapter.commit(bundle, item_path=item_path, message=message)
        git_commit_sha, git_ref = commit.commit_sha, commit.ref
        steps.append(DeployStep("commit", True, f"{commit.commit_sha or '(no change)'} on {commit.ref}"))
    except TargetAdapterError as exc:
        steps.append(DeployStep("commit", False, str(exc)))
        return await finish("FAILED", attempts=0)

    budget = max(1, retries)
    for attempt in range(1, budget + 1):
        try:
            deployment = await target_adapter.deploy(workspace=workspace, git_ref=git_ref or "")
            if deployment.ok:
                steps.append(DeployStep("deploy", True, deployment.detail or deployment.deployment_id))
                return await finish("SUCCEEDED", attempts=attempt)
            steps.append(
                DeployStep(f"deploy (attempt {attempt}/{budget})", False,
                           deployment.detail or "deploy reported failure")
            )
        except TargetAdapterError as exc:
            steps.append(DeployStep(f"deploy (attempt {attempt}/{budget})", False, str(exc)))
        if attempt < budget:
            await sleep(_BACKOFF_SECONDS[min(attempt - 1, len(_BACKOFF_SECONDS) - 1)])

    return await finish("FAILED", attempts=budget)


__all__ = [
    "DEPLOY_RETRY_DEFAULT",
    "DEPLOY_TABLE",
    "DeployRecord",
    "DeployStep",
    "PostgresReportDeployStore",
    "ReportDeployError",
    "ReportDeployStore",
    "deploy_report",
]
