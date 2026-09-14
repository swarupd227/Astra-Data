"""Agent identity and least privilege — spec §8.1, §18.1/§18.2, story S11.1.2, opens F11.1.

    "As a platform engineer, I want each agent to run under its own non-human identity
    with least privilege, so that an agent's actions are attributable and its reach is
    bounded."

**`AgentRecord` is declared here, not stored.** §8.1's own worked example (the Transpiler)
is a fixed charter — what an agent consumes, produces, is forbidden from, its autonomy
ceiling, its model policy, its budgets. Nothing in this AC or §8.1 calls it "configurable
per tenant" the way `MenderConfig`/`AdoptionConfig` explicitly are; it is this platform's
own declared catalog of what it deploys, the identical footing `roles.py`'s own `Role`
enum already has for the client's roles. `AGENT_CATALOG` below is that declaration —
code, not a Postgres table — matching `roles.py`'s own "the set of roles ... is real and
enforced; only the source of the assertion is provisional" posture, applied here to
agents instead of humans.

**What "declared and enforced" actually means today.** Every agent in the catalog below
is real. Only the Transpiler's own scope is genuinely narrowed, because it is the *one*
agent this codebase has real, checkable evidence for: §8.1's own literal example names
its prohibitions verbatim ("write outside MU scope", "call executor", "modify Pattern.
promotion_state"), and `generation.py` — the Transpiler's own real implementation — is
small and fully read: it writes exactly `Measure`/`ExceptionCase` nodes, sets properties
on `CalculatedField` (via `set_node_properties`, confirmed against this module's own real
call sites and the fixtures several existing tests already build under `agent:transpiler`
— `test_context_contracts.py`, `test_integration_context.py`), stores no artefact, and
calls no adapter of any kind (confirmed by direct search — "never calls the executor" is
therefore already structurally true, not something this module has a real call site left
to refuse). Every other cataloged agent (`harvester`, `cartographer`, `modeller`,
`compositor`, `arbiter`, `mender`, `steward`) is declared with `AgentScope.unrestricted()`
— narrowing any of them without a stated real requirement would be fabricating a
restriction this codebase has no evidence for, and for several of them (`harvester`,
`steward` above all) it would be an actively dangerous guess: `agent:harvester` is this
whole test suite's own default identity (`tests/conftest.py`), exercised across nearly
every kind of write this service makes, and `agent:steward` is the real, already-passing
automated build trigger (`api/routes_g2.py`) and regression path (`regression.py`). An
`agent:` principal naming no declared id at all (there are many in this codebase's own
test suite — `agent:ci`, `agent:pm`, `agent:test`, and others, none of them one of §8.3's
eight real agents) is treated the identical way: unrestricted. A permission framework
that broke a mature system's own already-exercised behaviour on its first day would be
worse than the gap it closed; narrowing further is real, incremental work this module's
own shape is built to take without changing its callers again.

**A `user:`/`service:` principal is never scoped by this module.** This story is about an
*agent's* reach, not a human's — role-based authorization (`api/deps.py`) already governs
who may call which route; this module adds a second, narrower axis that only applies once
a caller has asserted it is an agent at all.
"""

from __future__ import annotations

import logging
from collections.abc import Iterable
from dataclasses import dataclass, field

from .errors import ForbiddenError

logger = logging.getLogger(__name__)

AGENT_PRINCIPAL_PREFIX = "agent:"


class AgentAuthorizationError(ForbiddenError):
    """An agent's own declared charter refuses this call (spec §18.1's own "an out-of-
    scope call is refused"). Rendered as 403, the same shape every other refusal in this
    service already has."""

    error_code = "agent_out_of_scope"


def agent_id_of(principal: str) -> str | None:
    """The `AgentRecord.id` a principal *value* (`Principal.value`, e.g.
    ``"agent:transpiler"``) names, or `None` if it is not an agent principal at all
    (`user:`/`service:`, which this module never scopes). Every function below takes the
    plain string rather than a `Principal` object -- some real call sites (`artefacts.py`'s
    own `store()`) only ever hold the string, and a check that needs no more than that
    should not force its caller to construct more."""
    if not principal.startswith(AGENT_PRINCIPAL_PREFIX):
        return None
    return principal[len(AGENT_PRINCIPAL_PREFIX) :]


@dataclass(frozen=True, slots=True)
class AgentScope:
    """The mechanically enforceable half of `AgentRecord.charter.prohibited`. §8.1's own
    prohibitions are free text; this is the structured, checkable shape they translate
    into wherever this codebase actually has a real call site to check them at."""

    allowed_node_types: frozenset[str] | None = None
    """Which node types this agent may create or upsert (`GraphWriter.write_nodes`/
    `.upsert_nodes`/`.set_node_properties`, all three funnel through one real check --
    see `writes.py`). `None` means unrestricted."""

    forbidden_properties: frozenset[str] = frozenset()
    """`"<NodeType>.<property>"` pairs this agent may never write, checked regardless of
    whether the node type itself is otherwise allowed -- §8.1's own literal "modify
    Pattern.promotion_state" for the Transpiler is the one real example this codebase has,
    kept as its own dimension rather than folded into `allowed_node_types` because a real
    charter can plausibly need "may touch this node type, but never this one property on
    it" (defence in depth, not just a coarser allow-list)."""

    allowed_task_classes: frozenset[str] | None = None
    """Which `gateway.TaskClass` values this agent may call `Gateway.generate` for. `None`
    means unrestricted."""

    allowed_artefact_kinds: frozenset[str] | None = None
    """Which `ArtefactStore.store(kind=...)` values this agent may write. `None` means
    unrestricted; an empty `frozenset()` means this agent may never store an artefact at
    all -- the Transpiler's own real case, since nothing in `generation.py` calls
    `ArtefactStore.store` (confirmed by direct search)."""

    @classmethod
    def unrestricted(cls) -> AgentScope:
        return cls()

    def as_dict(self) -> dict[str, object]:
        return {
            "allowed_node_types": sorted(self.allowed_node_types) if self.allowed_node_types is not None else None,
            "forbidden_properties": sorted(self.forbidden_properties),
            "allowed_task_classes": sorted(self.allowed_task_classes) if self.allowed_task_classes is not None else None,
            "allowed_artefact_kinds": sorted(self.allowed_artefact_kinds) if self.allowed_artefact_kinds is not None else None,
            "unrestricted": self == AgentScope.unrestricted(),
        }


@dataclass(frozen=True, slots=True)
class AgentCharter:
    """§8.1's own free-text charter fields, kept verbatim for evidence/display -- the
    "Tenant & Access" screen and any future Evidence Chain export read this, not the
    structured `AgentScope`, which exists purely for enforcement."""

    consumes: tuple[str, ...]
    produces: tuple[str, ...]
    prohibited: tuple[str, ...]

    def as_dict(self) -> dict[str, object]:
        return {
            "consumes": list(self.consumes),
            "produces": list(self.produces),
            "prohibited": list(self.prohibited),
        }


@dataclass(frozen=True, slots=True)
class AgentRecord:
    """§8.1's own `AgentRecord {...}` block, field for field."""

    id: str
    version: str
    charter: AgentCharter
    ai_mode: dict[str, str]
    validation: tuple[str, ...]
    autonomy: str
    """§13.2: "Autonomy is set per action class and is not a property of an agent" -- this
    is the ceiling §8.1's own worked example still states per agent, kept as a reference
    to the real §13.2 ladder (`MA-xx` ceilings declared where each action is actually
    gated), not a second, competing autonomy grant this module enforces on its own."""
    model_policy: dict[str, object]
    budgets: dict[str, object]
    owner: str
    scope: AgentScope = field(default_factory=AgentScope.unrestricted)
    real: bool = True
    """`False` for a cataloged agent with no real running code behind it at all today
    (the Arbiter -- E7, disclosed absent everywhere else in this codebase too). Declared
    for completeness against §8.3's own eight-agent catalog; never exercised as a live
    principal anywhere, so its own scope is moot until E7 gives it one."""

    def as_dict(self) -> dict[str, object]:
        return {
            "id": self.id,
            "version": self.version,
            "charter": self.charter.as_dict(),
            "ai_mode": dict(self.ai_mode),
            "validation": list(self.validation),
            "autonomy": self.autonomy,
            "model_policy": dict(self.model_policy),
            "budgets": dict(self.budgets),
            "owner": self.owner,
            "scope": self.scope.as_dict(),
            "real": self.real,
        }


AGENT_CATALOG: dict[str, AgentRecord] = {
    "harvester": AgentRecord(
        id="harvester",
        version="1.0.0",
        charter=AgentCharter(
            consumes=("Tableau site/project/workbook metadata (§6.1 adapter contract)",),
            produces=("Site", "Project", "Workbook", "Worksheet", "Dashboard",
                       "Datasource", "Field", "CalculatedField", "Parameter"),
            prohibited=(),
        ),
        ai_mode={"C1": "DETERMINISTIC"},
        validation=("SCHEMA",),
        autonomy="L4",
        model_policy={},
        budgets={"wall_clock_s": 3600},
        owner="platform_eng",
        # Unrestricted -- this is `tests/conftest.py`'s own default test principal
        # (`agent:harvester`) and `harvest/scheduler.py`'s real, already-passing
        # automated principal; see this module's own docstring for why narrowing it is
        # not this story's to guess.
        scope=AgentScope.unrestricted(),
    ),
    "cartographer": AgentRecord(
        id="cartographer",
        version="1.0.0",
        charter=AgentCharter(
            consumes=("harvested Workbook/Field similarity signals",),
            produces=("ModelFamily", "IN_FAMILY edges"),
            prohibited=(),
        ),
        ai_mode={"C1": "DETERMINISTIC"},
        validation=("SCHEMA",),
        autonomy="L2",
        model_policy={},
        budgets={"wall_clock_s": 1800},
        owner="platform_eng",
        scope=AgentScope.unrestricted(),
    ),
    "modeller": AgentRecord(
        id="modeller",
        version="1.0.0",
        charter=AgentCharter(
            consumes=("ModelFamily + context contract (MODELLER_FAMILY)",),
            produces=("SemanticModel", "ModelTable", "Field MAPS_TO ModelTable"),
            prohibited=(),
        ),
        ai_mode={"C1": "DETERMINISTIC", "C2": "DETERMINISTIC"},
        validation=("SCHEMA", "PARSE"),
        autonomy="L2",
        model_policy={},
        budgets={"wall_clock_s": 1800},
        owner="platform_eng",
        scope=AgentScope.unrestricted(),
    ),
    "transpiler": AgentRecord(
        id="transpiler",
        version="1.4.2",
        charter=AgentCharter(
            consumes=("CalculatedField + context contract (TRANSPILER_CALC)",),
            produces=("Measure",),
            prohibited=("write outside MU scope", "call executor", "modify Pattern.promotion_state"),
        ),
        ai_mode={"C1": "DETERMINISTIC", "C2": "DETERMINISTIC", "C3": "GENERATED_PROVED", "C4": "HUMAN"},
        validation=("SCHEMA", "PARSE", "COMPILE"),
        autonomy="L3",
        model_policy={"tier": "reasoning", "provider": "anthropic", "temperature": 0, "max_tokens": 2000},
        budgets={"tokens_per_run": 20000, "wall_clock_s": 120},
        owner="platform_eng",
        # The one real, evidence-backed scope this story narrows -- see the module
        # docstring for exactly what evidence justifies each field below.
        scope=AgentScope(
            allowed_node_types=frozenset({"CalculatedField", "Measure", "ExceptionCase"}),
            forbidden_properties=frozenset({"Pattern.promotion_state"}),
            allowed_task_classes=frozenset({"transpile_c3", "transpile_c3_small_model"}),
            allowed_artefact_kinds=frozenset(),
        ),
    ),
    "compositor": AgentRecord(
        id="compositor",
        version="1.0.0",
        charter=AgentCharter(
            consumes=("SemanticModel + composed report layout",),
            produces=("ReportDefinition", "Visual"),
            prohibited=(),
        ),
        ai_mode={"C1": "DETERMINISTIC"},
        validation=("SCHEMA",),
        autonomy="L3",
        model_policy={},
        budgets={"wall_clock_s": 600},
        owner="platform_eng",
        scope=AgentScope.unrestricted(),
    ),
    "arbiter": AgentRecord(
        id="arbiter",
        version="0.0.0",
        charter=AgentCharter(
            consumes=("a disputed parity verdict",),
            produces=("an adjudicated Verdict",),
            prohibited=(),
        ),
        ai_mode={"C1": "DETERMINISTIC"},
        validation=("SCHEMA",),
        autonomy="L4",
        model_policy={},
        budgets={},
        owner="platform_eng",
        scope=AgentScope.unrestricted(),
        # No real running code exists for the Arbiter anywhere in this codebase (E7,
        # disclosed absent -- generation.py's own rungs 3/4, gateway.py's own module
        # docstring, and this catalog all independently confirm the same gap). Declared
        # for completeness against §8.3's own eight-agent catalog; never exercised.
        real=False,
    ),
    "mender": AgentRecord(
        id="mender",
        version="1.0.0",
        charter=AgentCharter(
            consumes=("a FAIL ParityCase + its own bounded repair budget",),
            produces=("a repaired Measure", "MenderPass"),
            prohibited=(),
        ),
        ai_mode={"C1": "DETERMINISTIC", "C3": "GENERATED_PROVED"},
        validation=("SCHEMA", "PARSE"),
        autonomy="L3",
        model_policy={"tier": "reasoning", "provider": "anthropic", "temperature": 0},
        budgets={"tokens_per_run": 20000},
        owner="platform_eng",
        # Unrestricted -- mender.py's own real repair path runs under whichever human
        # principal triggered it (`POST /v1/exceptions/{id}:mend`, ParityEngineerDep),
        # never a constructed `agent:mender` principal (confirmed by direct search); its
        # own bound is `MenderConfig.pass_budget`, not a graph/artefact/gateway scope.
        scope=AgentScope.unrestricted(),
    ),
    "steward": AgentRecord(
        id="steward",
        version="1.0.0",
        charter=AgentCharter(
            consumes=("a G2-approved ModelFamily", "a due regression schedule"),
            produces=("BuildRun", "ParityRun", "regression evidence"),
            prohibited=(),
        ),
        ai_mode={"C1": "DETERMINISTIC"},
        validation=("SCHEMA",),
        autonomy="L2",
        model_policy={},
        budgets={"wall_clock_s": 3600},
        owner="platform_eng",
        # Unrestricted -- `agent:steward` is a real, already-passing production
        # principal (`api/routes_g2.py`'s own automatic post-G2-approval build,
        # `regression.py`'s scheduled re-runs); narrowing it without a stated real
        # prohibition risks breaking behaviour this codebase already depends on.
        scope=AgentScope.unrestricted(),
    ),
}


def _record_for(agent_id: str) -> AgentRecord | None:
    return AGENT_CATALOG.get(agent_id)


def _refuse(principal: str, message: str) -> None:
    logger.warning("agent call refused: principal=%s reason=%s", principal, message)
    raise AgentAuthorizationError(message)


def authorize_node_write(principal: str, node_type: str) -> None:
    """Called once per node write, before validation (`writes.py`'s own `_prepare_nodes`
    -- the single chokepoint `write_nodes`/`upsert_nodes`/`set_node_properties` all funnel
    through)."""
    agent_id = agent_id_of(principal)
    if agent_id is None:
        return
    record = _record_for(agent_id)
    if record is None:
        return
    scope = record.scope
    if scope.allowed_node_types is not None and node_type not in scope.allowed_node_types:
        _refuse(
            principal,
            f"'{record.id}' is not permitted to write {node_type} nodes; its own charter "
            f"allows {sorted(scope.allowed_node_types)}",
        )


def authorize_property_write(
    principal: str, node_type: str, property_names: Iterable[str]
) -> None:
    """Checked alongside `authorize_node_write`, for the properties a write actually
    names -- §8.1's own "modify Pattern.promotion_state" is a property-level
    prohibition, not merely a node-type one."""
    agent_id = agent_id_of(principal)
    if agent_id is None:
        return
    record = _record_for(agent_id)
    if record is None or not record.scope.forbidden_properties:
        return
    for name in property_names:
        dotted = f"{node_type}.{name}"
        if dotted in record.scope.forbidden_properties:
            _refuse(
                principal,
                f"'{record.id}' is not permitted to write {dotted}; its own charter "
                f"forbids it explicitly",
            )


def authorize_gateway_call(principal: str, task_class: str) -> None:
    agent_id = agent_id_of(principal)
    if agent_id is None:
        return
    record = _record_for(agent_id)
    if record is None:
        return
    scope = record.scope
    if scope.allowed_task_classes is not None and task_class not in scope.allowed_task_classes:
        _refuse(
            principal,
            f"'{record.id}' is not permitted to call the gateway for task class "
            f"'{task_class}'; its own charter allows {sorted(scope.allowed_task_classes)}",
        )


def authorize_artefact_kind(principal: str, kind: str) -> None:
    agent_id = agent_id_of(principal)
    if agent_id is None:
        return
    record = _record_for(agent_id)
    if record is None:
        return
    scope = record.scope
    if scope.allowed_artefact_kinds is not None and kind not in scope.allowed_artefact_kinds:
        _refuse(
            principal,
            f"'{record.id}' is not permitted to store an artefact of kind '{kind}'; its "
            f"own charter allows {sorted(scope.allowed_artefact_kinds) or '(none)'}",
        )


__all__ = [
    "AGENT_CATALOG",
    "AGENT_PRINCIPAL_PREFIX",
    "AgentAuthorizationError",
    "AgentCharter",
    "AgentRecord",
    "AgentScope",
    "agent_id_of",
    "authorize_artefact_kind",
    "authorize_gateway_call",
    "authorize_node_write",
    "authorize_property_write",
]
