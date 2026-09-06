"""Building an approved design as TMDL and deploying it — stories S4.3.1 and S4.3.2.

    "As a model engineer, I want an approved design built as TMDL and deployed to the dev
    workspace automatically, so that the model exists as code the moment it is approved."
    "As an architect, I want conformance rules enforced at emission, so that no model
    reaches the client repository that breaks the target architecture."

**Emit, check conformance, commit, deploy, smoke-query — in that order, and every step's
outcome is recorded even when it fails.** `BUILT` is entered only if every step's own
success is true; nothing here rolls a partial failure back, because a retry of the same
version simply repeats every step and both Git and the fixture workspace converge on the
same end state regardless of how many times they are asked (`target_fake.py`'s own
idempotent `commit`/`deploy`). **Conformance runs before `commit`, not after** — "no model
reaches the client repository that breaks the target architecture" (S4.3.2's own words)
means a violation must block the Git write itself, not merely the state transition; a
design that fails conformance is never handed to the target adapter at all.

**Conformance is checked against the latest saved `ConformanceRuleset`, and the version
checked against is stamped on `ModelFamily` every attempt — pass or fail.** "Recorded on
the ModelFamily at build" (S4.3.2) is a fact about *this attempt*, not only a successful
one: an architect tightening a rule should be able to see, on a family still stuck at
APPROVED, which version its last failed attempt was measured against.

**One `public.build_run` row per attempt, not one mutable row per family.** A family can be
rebuilt — a retry after a fix, or (S4.3.3, not built) a new version later — and each attempt
is its own record, the same "history, not current state" reasoning `g2_question` and
`family_transition_history` already establish elsewhere. The Build tab reads the most
recent one; nothing here claims a build history feature beyond that.

**Triggered automatically, on the `agent:steward` principal** — spec §19: "acting
integrations run only through the Steward and the target adapter"; "Steward" is not yet a
role a human asserts (`roles.py`), it is what this codebase already calls the agent
principal behind an automated action nobody clicked a second button for (`agent:modeller`,
`agent:cartographer`). `routes_g2.approve_route` calls `build_family` with that principal
immediately after a successful approval — "the model exists as code the *moment* it is
approved." A manual retry (`POST /v1/families/{id}:build`, for when the Build tab's own
log says why the automatic attempt failed) records the calling user instead.

**One measure per table's smoke query, not a per-table measure list.** `candidate_measures`
(S4.1.1) names a measure but never which table it belongs to — a real gap, disclosed in
`tmdl.py`'s own docstring. Every table's smoke query checks the same measure (the frozen
design's first, alphabetically) rather than none at all; a per-table measure assignment is
real future scope this story does not invent data to satisfy.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Protocol

import asyncpg
from astra_adapter import TargetAdapter, TargetAdapterError

from .artefacts import ArtefactStore
from .cartographer import get_family
from .conformance_rules import ConformanceRulesetStore, check_conformance
from .errors import ElementNotFoundError, InvalidRequestError
from .ids import new_ulid
from .lineage import hydrate
from .model_lifecycle import require_transition
from .modeller import read_design_document
from .ontology.types import BASE_NODE_PROPERTIES
from .principal import Principal
from .tmdl import emit_tmdl, safe_name
from .writes import GraphWriter, NodeWrite

logger = logging.getLogger(__name__)

BUILD_TABLE = "public.build_run"

_NODE_SERVER_MANAGED = frozenset(p.name for p in BASE_NODE_PROPERTIES if p.server_managed) | {
    "id",
    "side",
}


def _writable_node_properties(properties: dict[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in properties.items() if k not in _NODE_SERVER_MANAGED}


@dataclass(frozen=True, slots=True)
class BuildStep:
    name: str
    ok: bool
    detail: str

    def as_dict(self) -> dict[str, Any]:
        return {"name": self.name, "ok": self.ok, "detail": self.detail}


@dataclass(frozen=True, slots=True)
class BuildRecord:
    id: str
    family_id: str
    version: str
    gate_decision_id: str | None
    state: str
    """``SUCCEEDED`` or ``FAILED``."""
    steps: tuple[BuildStep, ...]
    git_commit_sha: str | None
    git_ref: str | None
    workspace: str | None
    triggered_by: str
    started_at: str
    finished_at: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "family_id": self.family_id,
            "version": self.version,
            "gate_decision_id": self.gate_decision_id,
            "state": self.state,
            "steps": [s.as_dict() for s in self.steps],
            "git_commit_sha": self.git_commit_sha,
            "git_ref": self.git_ref,
            "workspace": self.workspace,
            "triggered_by": self.triggered_by,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
        }


class BuildStore(Protocol):
    async def record(self, build: BuildRecord) -> BuildRecord: ...

    async def latest(self, family_id: str) -> BuildRecord | None: ...


class PostgresBuildStore:
    def __init__(self, pool: asyncpg.Pool, *, graph_name: str) -> None:
        self._pool = pool
        self._graph = graph_name

    async def record(self, build: BuildRecord) -> BuildRecord:
        async with self._pool.acquire() as conn:
            await conn.execute(
                f"""
                INSERT INTO {BUILD_TABLE}
                    (id, graph, family_id, version, gate_decision_id, state, steps,
                     git_commit_sha, git_ref, workspace, triggered_by, started_at, finished_at)
                VALUES ($1, $2, $3, $4, $5, $6, $7::jsonb, $8, $9, $10, $11, $12, $13)
                """,
                build.id,
                self._graph,
                build.family_id,
                build.version,
                build.gate_decision_id,
                build.state,
                json.dumps([s.as_dict() for s in build.steps]),
                build.git_commit_sha,
                build.git_ref,
                build.workspace,
                build.triggered_by,
                datetime.fromisoformat(build.started_at),
                datetime.fromisoformat(build.finished_at),
            )
        return build

    async def latest(self, family_id: str) -> BuildRecord | None:
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                f"""
                SELECT * FROM {BUILD_TABLE}
                 WHERE graph = $1 AND family_id = $2
              ORDER BY started_at DESC
                 LIMIT 1
                """,
                self._graph,
                family_id,
            )
        return _from_row(row) if row else None


def _from_row(row: asyncpg.Record) -> BuildRecord:
    steps_raw = row["steps"]
    steps_list = json.loads(steps_raw) if isinstance(steps_raw, str) else list(steps_raw)
    return BuildRecord(
        id=row["id"],
        family_id=row["family_id"],
        version=row["version"],
        gate_decision_id=row["gate_decision_id"],
        state=row["state"],
        steps=tuple(BuildStep(**step) for step in steps_list),
        git_commit_sha=row["git_commit_sha"],
        git_ref=row["git_ref"],
        workspace=row["workspace"],
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


async def _family_properties(pool: asyncpg.Pool, graph_name: str, family_id: str) -> dict[str, Any]:
    async with pool.acquire() as conn:
        hydrated = await hydrate(conn, graph_name, "ModelFamily", [family_id])
    properties = hydrated.get(family_id)
    if properties is None:
        raise ElementNotFoundError(f"no ModelFamily '{family_id}'")
    return properties


async def build_family(
    pool: asyncpg.Pool,
    graph_name: str,
    writer: GraphWriter,
    artefact_store: ArtefactStore,
    target_adapter: TargetAdapter,
    build_store: BuildStore,
    conformance_store: ConformanceRulesetStore,
    family_id: str,
    *,
    gate_decision_id: str | None,
    workspace: str,
    principal: Principal,
) -> BuildRecord:
    """Emit, check conformance, commit, deploy and smoke-test an `APPROVED` family's frozen
    design, entering `BUILT` only if every step succeeds. Always returns a `BuildRecord` —
    a failure is a normal, recorded outcome, not a raised exception, except for the two
    checks that mean this call was never a legitimate build to begin with (wrong state; no
    frozen version)."""
    started_at = _now()
    properties = await _family_properties(pool, graph_name, family_id)
    current_state = properties.get("state")
    if current_state != "BUILT":
        # A rebuild of an already-BUILT family (the Build tab's own retry, or a redeploy
        # with nothing design-side changed) is not a *transition* — it is already at the
        # state machine's own destination, so `require_transition` correctly has nothing
        # to say about it; only entering BUILT for the first time goes through the check.
        require_transition(current_state, "BUILT")

    raw_document = await read_design_document(pool, graph_name, family_id)
    version = raw_document.get("version")
    if not version:
        raise InvalidRequestError(
            f"family '{family_id}' has no frozen version to build — submit it for G2 and "
            f"have it approved first"
        )
    family = await get_family(pool, graph_name, family_id) or {}
    family_name = str(family.get("name") or family_id)
    document = {**raw_document, "family_name": family_name}

    ruleset = await conformance_store.latest()

    steps: list[BuildStep] = []
    git_commit_sha: str | None = None
    git_ref: str | None = None

    async def finish(state: str) -> BuildRecord:
        record = BuildRecord(
            id=f"build_{new_ulid()}",
            family_id=family_id,
            version=version,
            gate_decision_id=gate_decision_id,
            state=state,
            steps=tuple(steps),
            git_commit_sha=git_commit_sha,
            git_ref=git_ref,
            workspace=workspace,
            triggered_by=principal.value,
            started_at=started_at,
            finished_at=_now(),
        )
        await build_store.record(record)
        # "Recorded on the ModelFamily at build" (S4.3.2) — every attempt stamps which
        # ruleset version it was measured against, whether or not it passed; only a
        # SUCCEEDED build additionally advances state to BUILT.
        family_properties = {
            **_writable_node_properties(properties),
            "conformance_ruleset_version": ruleset.version,
        }
        if state == "SUCCEEDED":
            family_properties["state"] = "BUILT"
        await writer.upsert_nodes(
            [NodeWrite(type="ModelFamily", id=family_id, properties=family_properties)],
            principal=principal,
        )
        if state == "SUCCEEDED":
            # `SemanticModel.state`'s own note promises "deployment state within an
            # environment" — a promise nothing ever kept before this story (every write
            # site sets it to DRAFT/PUBLISHED/DEPRECATED only, never BUILT, even though
            # `ModelFamily.state` already gets it above). Closed here rather than worked
            # around, since S6.1.2 (the Compositor's own deploy story) needs to check
            # "PUBLISHED or BUILT" on the *model* a report is bound to, not the family.
            semantic_model_id = raw_document.get("semantic_model_id")
            if semantic_model_id:
                await writer.set_node_properties(
                    semantic_model_id, {"state": "BUILT"}, principal=principal
                )
        if state == "SUCCEEDED":
            logger.info("family %s built and deployed to %s by %s", family_id, workspace, principal.value)
        else:
            logger.warning(
                "family %s build failed at step %r: %s",
                family_id,
                steps[-1].name if steps else "(none)",
                steps[-1].detail if steps else "",
            )
        return record

    try:
        bundle = emit_tmdl(document)
        for path, content in sorted(bundle.files.items()):
            await artefact_store.store(
                kind="tmdl_file",
                mu_ref=family_id,
                case_id=path,
                content=content,
                media_type="text/plain",
                created_by=principal.value,
            )
        steps.append(BuildStep("emit", True, f"{len(bundle.files)} file(s) emitted"))
    except Exception as exc:
        steps.append(BuildStep("emit", False, str(exc)))
        return await finish("FAILED")

    violations = check_conformance(document, ruleset)
    if violations:
        detail = "; ".join(str(v) for v in violations)
        steps.append(BuildStep("conformance", False, detail))
        return await finish("FAILED")
    steps.append(
        BuildStep("conformance", True, f"ruleset version {ruleset.version}: no violations")
    )

    item_path = f"{safe_name(family_name)}.SemanticModel"
    message = f"Build {family_name} ({family_id}) — G2 decision {gate_decision_id or 'unknown'}"
    try:
        commit = await target_adapter.commit(bundle, item_path=item_path, message=message)
        git_commit_sha, git_ref = commit.commit_sha, commit.ref
        steps.append(BuildStep("commit", True, f"{commit.commit_sha or '(no change)'} on {commit.ref}"))
    except TargetAdapterError as exc:
        steps.append(BuildStep("commit", False, str(exc)))
        return await finish("FAILED")

    try:
        deployment = await target_adapter.deploy(workspace=workspace, git_ref=git_ref or "")
        steps.append(BuildStep("deploy", deployment.ok, deployment.detail or deployment.deployment_id))
        if not deployment.ok:
            return await finish("FAILED")
    except TargetAdapterError as exc:
        steps.append(BuildStep("deploy", False, str(exc)))
        return await finish("FAILED")

    tables = sorted(document.get("tables") or [], key=lambda t: str(t.get("name")))
    measures = sorted(document.get("candidate_measures") or [], key=lambda m: str(m.get("name")))
    measure_name = str(measures[0]["name"]) if measures else None

    all_smoke_ok = True
    for table in tables:
        table_name = str(table.get("name"))
        try:
            result = await target_adapter.smoke_query(
                workspace=workspace, table=table_name, measure_name=measure_name
            )
            steps.append(BuildStep(f"smoke:{table_name}", result.ok, result.detail))
            all_smoke_ok = all_smoke_ok and result.ok
        except TargetAdapterError as exc:
            steps.append(BuildStep(f"smoke:{table_name}", False, str(exc)))
            all_smoke_ok = False

    return await finish("SUCCEEDED" if all_smoke_ok else "FAILED")


__all__ = [
    "BUILD_TABLE",
    "BuildRecord",
    "BuildStep",
    "BuildStore",
    "PostgresBuildStore",
    "build_family",
]
