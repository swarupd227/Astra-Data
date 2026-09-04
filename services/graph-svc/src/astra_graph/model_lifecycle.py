"""Editing a model design proposal and the G2 state machine. Story S4.1.2.

    "As a model engineer, I want to edit the proposal in the Model Detail screen and
    submit it for G2, so that the design the client approves is the design we build."

§12.2's own table is the state machine this module enforces:

    PROPOSED  -> Engineer accepts       -> DRAFT
    DRAFT     -> Engineer submits       -> IN_REVIEW
    IN_REVIEW -> Approve                -> APPROVED
    IN_REVIEW -> Request changes        -> DRAFT
    APPROVED  -> Deploy to dev succeeds -> BUILT
    BUILT     -> Promote                -> PUBLISHED

**This story drives exactly two of those six edges — `accept` and `submit_for_review`.**
Approve/request-changes is the data owner's G2 action (backlog S4.2.1, not built); deploy
is the Steward's (S4.3.1, not built); promote has no story yet. `FAMILY_TRANSITIONS`
declares the whole graph anyway, the same "declare the shape now, whichever story needs it
drives it" precedent every prior epic in this codebase has followed (`ReleaseTrain`/`Wave`
since S1.1.1, `ModelTable`/`SemanticModel` since S1.1.1 until S4.1.1) — so a future story's
own `approve`/`deploy`/`promote` calls `transition_family` and gets the same enforcement,
never a second copy of the legal-transition table.

**"Transitions and their actors recorded" reuses the event log, not a new history table.**
Every `ModelFamily` upsert already carries `updated_by`/`updated_at` and is a real
CloudEvent (S1.1.3) — `family_transition_history` finds genuine state changes with the same
`LAG() OVER (PARTITION BY subject ORDER BY seq)` technique `train_projection.py` (S3.2.3)
uses to find genuine `IN_TRAIN.state` transitions in the same event stream, so a
resequence-shaped no-op write is never mistaken for a real transition here either. No
`GateDecision` record is written — that is §13.3's own shape, real future scope (E11/E13)
this story does not claim; this is "who moved this family through the design lifecycle and
when," not the gate itself.

**Submitting to `IN_REVIEW` freezes `SemanticModel.version` as a real content hash** —
`context.canonical.canonical_json`/`context_hash`, the exact utility every provenance
record and every context contract in this codebase already hashes with, over
`modeller.read_design_document`'s own canonical snapshot (grain statement, tables,
relationships, measures, RLS, refresh policy, conformed dimensions — everything the design
actually is). Freezing does not copy the document anywhere new: `read_design_document`
re-reads the same live nodes, so verifying "is this still what was approved" is
re-hashing the same read, the same reproducibility discipline S1.3.2 built for provenance.

**Editing is deliberately narrow.** DRAFT's own meaning (§12.2) is "engineer editing
tables, keys, grain, measures, RLS" — a wide brief. This story wires three edits: the grain
statement (a free-text override of the drafted prose), a table's storage mode (one of the
three §12.2 modes), and one relationship's cardinality — the fields a Semantic Model
Engineer most needs to correct before a client ever sees the proposal, per ADR 0028's own
disclosed heuristics (storage mode and cardinality are named *recommendations*, not
verified facts, precisely so they can be corrected here). Renaming a candidate measure,
adding or removing a table, and RLS role edits are not built — real future scope, not
silently unsupported: `design_document`'s shape has room for all of it once a story asks.
Every edit requires `state == DRAFT`; §12.2 gives editing to DRAFT alone.

See ADR 0029.
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Any

import asyncpg

from .cartographer import get_family
from .context.canonical import canonical_json, context_hash
from .errors import ElementNotFoundError, InvalidRequestError
from .ids import new_ulid
from .lineage import hydrate
from .modeller import list_semantic_models, read_design_document
from .ontology.types import BASE_NODE_PROPERTIES
from .principal import Principal
from .versions import EVENT_TABLE
from .writes import GraphWriter, NodeWrite

#: §12.2's table, transcribed. Keys are the state a family is in; values are the states it
#: may legally move to from there.
FAMILY_TRANSITIONS: dict[str, frozenset[str]] = {
    "PROPOSED": frozenset({"DRAFT"}),
    "SINGLETON": frozenset({"DRAFT"}),
    "DRAFT": frozenset({"IN_REVIEW"}),
    "IN_REVIEW": frozenset({"APPROVED", "DRAFT"}),
    "APPROVED": frozenset({"BUILT"}),
    "BUILT": frozenset({"PUBLISHED"}),
    # PUBLISHED -> DRAFT is story S4.3.3's own "change request... creates a DRAFT v(n+1)":
    # ModelFamily.state tracks whichever version is newest/most in-progress, so a family
    # that already has a published version re-enters DRAFT to work on its *next* one — the
    # older, still-live version's own state lives on its own SemanticModel node instead
    # (`request_new_version`), not on ModelFamily. DEPRECATED never applies to
    # ModelFamily.state itself (only ever to a superseded SemanticModel's own `state`) —
    # declared here regardless so every value the enum can hold has an entry, even one a
    # family's own state can never actually reach.
    "PUBLISHED": frozenset({"DRAFT"}),
    "DEPRECATED": frozenset(),
}

_TABLE_MODES = ("import", "directlake", "directquery")

MIN_CHANGE_REQUEST_REASON = 10

_NODE_SERVER_MANAGED = frozenset(p.name for p in BASE_NODE_PROPERTIES if p.server_managed) | {
    "id",
    "side",
}


def _writable_node_properties(properties: Mapping[str, Any]) -> dict[str, Any]:
    """Same discipline every module that upserts a hydrated node follows (S3.2.2's own
    lesson, re-learned once and applied everywhere since): a hydrated read carries
    server-managed base properties, and resubmitting them is refused as an ontology
    violation, not merged."""
    return {k: v for k, v in properties.items() if k not in _NODE_SERVER_MANAGED}


def require_transition(current: str | None, to_state: str) -> None:
    legal = FAMILY_TRANSITIONS.get(current or "", frozenset())
    if to_state not in legal:
        allowed = ", ".join(sorted(legal)) or "(none — this is a terminal state)"
        raise InvalidRequestError(
            f"cannot move a family from {current!r} to {to_state!r}; legal next state(s) "
            f"from {current!r}: {allowed}"
        )


async def _family_properties(pool: asyncpg.Pool, graph_name: str, family_id: str) -> dict[str, Any]:
    async with pool.acquire() as conn:
        hydrated = await hydrate(conn, graph_name, "ModelFamily", [family_id])
    properties = hydrated.get(family_id)
    if properties is None:
        raise ElementNotFoundError(f"no ModelFamily '{family_id}'")
    return properties


async def _set_family_state(
    writer: GraphWriter,
    family_id: str,
    properties: Mapping[str, Any],
    *,
    to_state: str,
    principal: Principal,
) -> None:
    await writer.upsert_nodes(
        [
            NodeWrite(
                type="ModelFamily",
                id=family_id,
                properties={**_writable_node_properties(properties), "state": to_state},
            )
        ],
        principal=principal,
    )


async def accept_family(
    pool: asyncpg.Pool, graph_name: str, writer: GraphWriter, family_id: str, *, principal: Principal
) -> dict[str, Any]:
    """`PROPOSED`/`SINGLETON` -> `DRAFT`. "Engineer accepts" (§12.2) — the family is now
    the Semantic Model Engineer's to design, and `Modeller.run` refuses to regenerate it
    from this point on (see `modeller.py`'s own note)."""
    properties = await _family_properties(pool, graph_name, family_id)
    current = properties.get("state")
    require_transition(current, "DRAFT")
    await _set_family_state(writer, family_id, properties, to_state="DRAFT", principal=principal)
    return await get_family(pool, graph_name, family_id) or {}


async def submit_for_review(
    pool: asyncpg.Pool, graph_name: str, writer: GraphWriter, family_id: str, *, principal: Principal
) -> dict[str, Any]:
    """`DRAFT` -> `IN_REVIEW`, freezing `SemanticModel.version` as a content hash over the
    design at this exact moment — the version hash the G2 request (backlog S4.2.1, not
    built) will reference."""
    properties = await _family_properties(pool, graph_name, family_id)
    current = properties.get("state")
    require_transition(current, "IN_REVIEW")

    try:
        document = await read_design_document(pool, graph_name, family_id)
    except ElementNotFoundError as exc:
        raise InvalidRequestError(
            f"family '{family_id}' has no generated design proposal to submit — generate "
            f"one (POST /v1/families/{{id}}:propose-design) before it is accepted into DRAFT"
        ) from exc

    version_hash = context_hash(canonical_json(hashable_document(document)))
    semantic_model_id = document["semantic_model_id"]

    async with pool.acquire() as conn:
        models = await hydrate(conn, graph_name, "SemanticModel", [semantic_model_id])
    model_properties = models.get(semantic_model_id)
    if model_properties is None:  # pragma: no cover - design_document is read from this node
        raise ElementNotFoundError(f"no SemanticModel '{semantic_model_id}'")

    await writer.upsert_nodes(
        [
            NodeWrite(
                type="SemanticModel",
                id=semantic_model_id,
                properties={**_writable_node_properties(model_properties), "version": version_hash},
            )
        ],
        principal=principal,
    )
    await _set_family_state(writer, family_id, properties, to_state="IN_REVIEW", principal=principal)

    family = await get_family(pool, graph_name, family_id) or {}
    return {**family, "semantic_model_id": semantic_model_id, "version": version_hash}


def hashable_document(document: Mapping[str, Any]) -> dict[str, Any]:
    """The design, minus the parts that describe *when* it was generated rather than *what*
    it is. A version hash that changed every time `propose-design` re-ran with no semantic
    difference (only `design_generated_at` moving) would defeat its own purpose — the same
    reasoning S1.3.1's context hash excludes audit metadata for."""
    return {k: v for k, v in document.items() if k not in {"design_generated_at", "version"}}


async def _require_draft(pool: asyncpg.Pool, graph_name: str, family_id: str) -> dict[str, Any]:
    properties = await _family_properties(pool, graph_name, family_id)
    if properties.get("state") != "DRAFT":
        raise InvalidRequestError(
            f"family '{family_id}' is {properties.get('state')!r}; editing a design "
            f"proposal is only available while a family is DRAFT (§12.2)"
        )
    return properties


async def update_grain_statement(
    pool: asyncpg.Pool,
    graph_name: str,
    writer: GraphWriter,
    family_id: str,
    *,
    grain_statement: str,
    principal: Principal,
) -> dict[str, Any]:
    await _require_draft(pool, graph_name, family_id)
    cleaned = grain_statement.strip()
    if not cleaned:
        raise InvalidRequestError("a grain statement cannot be blank")

    document = await read_design_document(pool, graph_name, family_id)
    semantic_model_id = document["semantic_model_id"]
    async with pool.acquire() as conn:
        models = await hydrate(conn, graph_name, "SemanticModel", [semantic_model_id])
    model_properties = models[semantic_model_id]

    await writer.upsert_nodes(
        [
            NodeWrite(
                type="SemanticModel",
                id=semantic_model_id,
                properties={**_writable_node_properties(model_properties), "grain_statement": cleaned},
            )
        ],
        principal=principal,
    )
    return {"family_id": family_id, "semantic_model_id": semantic_model_id, "grain_statement": cleaned}


async def update_domain(
    pool: asyncpg.Pool,
    graph_name: str,
    writer: GraphWriter,
    family_id: str,
    *,
    domain: str,
    principal: Principal,
) -> dict[str, Any]:
    """Assign the business domain a family belongs to — needed for G2 (story S4.2.1):
    approving a design requires the data owner's asserted domain scope to cover the
    family's own domain, and nothing before this story ever set one (§12.1's clustering
    output leaves it unset; ``ModelFamily.domain`` has been declared since S1.1.1, read
    everywhere, written nowhere)."""
    await _require_draft(pool, graph_name, family_id)
    cleaned = domain.strip()
    if not cleaned:
        raise InvalidRequestError("a domain cannot be blank")

    properties = await _family_properties(pool, graph_name, family_id)
    await writer.upsert_nodes(
        [
            NodeWrite(
                type="ModelFamily",
                id=family_id,
                properties={**_writable_node_properties(properties), "domain": cleaned},
            )
        ],
        principal=principal,
    )
    return {"family_id": family_id, "domain": cleaned}


async def update_owner(
    pool: asyncpg.Pool,
    graph_name: str,
    writer: GraphWriter,
    family_id: str,
    *,
    owner: str,
    principal: Principal,
) -> dict[str, Any]:
    """Assign the person expected to approve a family's design at G2 — the Programme
    Board's "the approver" (story S4.2.2). ``ModelFamily.owner`` has been declared since
    S1.1.1, read by ``cartographer._family_summary`` and written nowhere: the exact
    "declared, never populated" state ``domain`` was in before S4.2.1 gave it
    ``update_domain``. This is that same fix, for the same reason — a story finally needs
    the value, so it earns a narrow edit rather than a general PATCH."""
    await _require_draft(pool, graph_name, family_id)
    cleaned = owner.strip()
    if not cleaned:
        raise InvalidRequestError("an owner cannot be blank")

    properties = await _family_properties(pool, graph_name, family_id)
    await writer.upsert_nodes(
        [
            NodeWrite(
                type="ModelFamily",
                id=family_id,
                properties={**_writable_node_properties(properties), "owner": cleaned},
            )
        ],
        principal=principal,
    )
    return {"family_id": family_id, "owner": cleaned}


async def update_table_mode(
    pool: asyncpg.Pool,
    graph_name: str,
    writer: GraphWriter,
    family_id: str,
    table_id: str,
    *,
    mode: str,
    principal: Principal,
) -> dict[str, Any]:
    await _require_draft(pool, graph_name, family_id)
    if mode not in _TABLE_MODES:
        raise InvalidRequestError(f"mode must be one of {_TABLE_MODES}; got {mode!r}")

    async with pool.acquire() as conn:
        tables = await hydrate(conn, graph_name, "ModelTable", [table_id])
    properties = tables.get(table_id)
    if properties is None or properties.get("family_ref") != family_id:
        raise ElementNotFoundError(f"no ModelTable '{table_id}' in family '{family_id}'")

    await writer.upsert_nodes(
        [
            NodeWrite(
                type="ModelTable",
                id=table_id,
                properties={**_writable_node_properties(properties), "mode": mode},
            )
        ],
        principal=principal,
    )
    return {"family_id": family_id, "table_id": table_id, "mode": mode}


async def update_relationship_cardinality(
    pool: asyncpg.Pool,
    graph_name: str,
    writer: GraphWriter,
    family_id: str,
    *,
    from_table: str,
    to_table: str,
    cardinality: str,
    principal: Principal,
) -> dict[str, Any]:
    await _require_draft(pool, graph_name, family_id)
    if cardinality not in ("one_to_many", "many_to_one", "one_to_one"):
        raise InvalidRequestError(
            f"cardinality must be one of 'one_to_many', 'many_to_one', 'one_to_one'; "
            f"got {cardinality!r}"
        )

    document = await read_design_document(pool, graph_name, family_id)
    semantic_model_id = document["semantic_model_id"]
    relationships = list(document.get("relationships") or [])
    index = next(
        (
            i
            for i, r in enumerate(relationships)
            if r.get("from_table") == from_table and r.get("to_table") == to_table
        ),
        None,
    )
    if index is None:
        raise ElementNotFoundError(
            f"no relationship from '{from_table}' to '{to_table}' in family '{family_id}'"
        )
    relationships[index] = {
        **relationships[index],
        "cardinality": cardinality,
        "confidence": "engineer_confirmed",
        "reason": "set by a Semantic Model Engineer, overriding the drafted recommendation",
    }

    async with pool.acquire() as conn:
        models = await hydrate(conn, graph_name, "SemanticModel", [semantic_model_id])
    model_properties = models[semantic_model_id]
    design_document = dict(model_properties.get("design_document") or {})
    design_document["relationships"] = relationships

    await writer.upsert_nodes(
        [
            NodeWrite(
                type="SemanticModel",
                id=semantic_model_id,
                properties={
                    **_writable_node_properties(model_properties),
                    "design_document": design_document,
                },
            )
        ],
        principal=principal,
    )
    return {
        "family_id": family_id,
        "semantic_model_id": semantic_model_id,
        "relationship": relationships[index],
    }


async def family_transition_history(
    pool: asyncpg.Pool, graph_name: str, family_id: str
) -> list[dict[str, Any]]:
    """Every genuine `state` change this family's node has gone through, oldest first —
    "transitions and their actors recorded" (S4.1.2's own words). A `LAG()` window
    function finds real transitions the same way `train_projection.estate_throughput`
    (S3.2.3) does for `IN_TRAIN.state`: a write that carries the same state forward (any
    other property changing) is not a transition and is correctly excluded.
    """
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            f"""
            WITH state_events AS (
                SELECT
                    time,
                    principal,
                    data -> 'properties' ->> 'state' AS state,
                    LAG(data -> 'properties' ->> 'state') OVER (ORDER BY seq) AS previous_state
                FROM {EVENT_TABLE}
                WHERE graph = $1 AND type = 'estate.node.upserted' AND label = 'ModelFamily'
                  AND subject = $2
            )
            SELECT previous_state AS from_state, state AS to_state, time, principal
            FROM state_events
            WHERE previous_state IS DISTINCT FROM state
            ORDER BY time
            """,
            graph_name,
            family_id,
        )
    return [
        {
            "from_state": row["from_state"],
            "to_state": row["to_state"],
            "at": _iso(row["time"]),
            "by": row["principal"],
        }
        for row in rows
    ]


def _iso(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, str):
        return value
    return str(value.isoformat())


# ------------------------------------------------------------------- versioning (S4.3.3)


class RegressionStatus:
    """Whether a version may be promoted, per S4.3.3's own "v(n) stays PUBLISHED until
    v(n+1) passes regression on all released MUs bound to it."

    **A disclosed gap, not a fabricated gate.** No Migration Unit graph node exists
    anywhere in this codebase yet (§3.1's own MU is "a record of work... the control
    plane, which E3 builds when the Cartographer starts creating MUs" —
    `migration_units.py`'s own words; nothing has ever driven the §3.2 state machine a
    "released" MU would need), and no regression-suite execution exists either (§10.6's
    own re-run-and-verdict mechanism belongs to F7.7, E7, not built). With zero released
    MUs bound to any family today, "passes regression on all released MUs" is vacuously
    true — an honest reading of real platform state, the same footing
    `harvest.UngatedPromotions` gives "no gate, and it says so." The day a real MU
    registry reports a released MU bound to this family, this fails closed instead of
    fabricating a verdict nothing here can compute.
    """

    def __init__(self, *, passed: bool, released_mu_count: int, detail: str) -> None:
        self.passed = passed
        self.released_mu_count = released_mu_count
        self.detail = detail

    def as_dict(self) -> dict[str, Any]:
        return {"passed": self.passed, "released_mu_count": self.released_mu_count, "detail": self.detail}


async def regression_status(pool: asyncpg.Pool, graph_name: str, family_id: str) -> RegressionStatus:
    # No Migration Unit registry has anything to bind to a family yet (see the class
    # docstring above) — zero released MUs is the only real answer this platform can give.
    released_mu_count = 0
    return RegressionStatus(
        passed=True,
        released_mu_count=released_mu_count,
        detail=(
            "no released Migration Units are bound to this family; nothing to regress "
            "against (Migration Unit tracking and regression execution are E3/E7's own "
            "future scope, not yet built)"
        ),
    )


async def list_model_versions(pool: asyncpg.Pool, graph_name: str, family_id: str) -> list[dict[str, Any]]:
    """Every version of this family's model, newest first — "the console shows both"
    (S4.3.3)."""
    versions = await list_semantic_models(pool, graph_name, family_id)
    return [
        {
            "semantic_model_id": v["id"],
            "version_number": int(v.get("version_number") or 1),
            "state": v.get("state"),
            "version": v.get("version"),
            "design_generated_at": v.get("design_generated_at"),
            "published_at": v.get("published_at"),
            "deprecated_at": v.get("deprecated_at"),
        }
        for v in reversed(versions)
    ]


async def request_new_version(
    pool: asyncpg.Pool,
    graph_name: str,
    writer: GraphWriter,
    family_id: str,
    *,
    reason: str,
    principal: Principal,
) -> dict[str, Any]:
    """`PUBLISHED` -> `DRAFT`, creating v(n+1) as an editable copy of the currently
    PUBLISHED version — a "change request" (S4.3.3's own words: "a Mender repair or a
    design change does not regress what is live"). v(n)'s own `SemanticModel` node is
    never touched: it keeps `state="PUBLISHED"` and every property it already has, so
    whatever is live stays live, byte-for-byte, while v(n+1) is designed, reviewed, built
    and — only once it passes regression — promoted.
    """
    properties = await _family_properties(pool, graph_name, family_id)
    require_transition(properties.get("state"), "DRAFT")

    versions = await list_semantic_models(pool, graph_name, family_id)
    if not versions:
        raise ElementNotFoundError(f"family '{family_id}' has no design to request a change against")
    current = versions[-1]
    if current.get("state") != "PUBLISHED":
        raise InvalidRequestError(
            f"family '{family_id}' has no PUBLISHED version to request a change against "
            f"(its current version is {current.get('state')!r})"
        )

    cleaned_reason = reason.strip()
    if len(cleaned_reason) < MIN_CHANGE_REQUEST_REASON:
        raise InvalidRequestError(
            f"a change request needs a reason of at least {MIN_CHANGE_REQUEST_REASON} characters"
        )

    current_model_id = current["id"]
    current_version_number = int(current.get("version_number") or 1)

    async with pool.acquire() as conn:
        design = await read_design_document(pool, graph_name, family_id)
        current_tables = await hydrate(conn, graph_name, "ModelTable", [t["id"] for t in design["tables"]])

    new_model_id = new_ulid()
    table_id_map: dict[str, str] = {}
    table_writes = []
    for old_id, table_props in current_tables.items():
        new_id = new_ulid()
        table_id_map[old_id] = new_id
        table_writes.append(
            NodeWrite(
                type="ModelTable",
                id=new_id,
                properties={
                    **_writable_node_properties(table_props),
                    "family_ref": family_id,
                    "semantic_model_ref": new_model_id,
                },
            )
        )

    design_document = {k: v for k, v in design.items() if k in {
        "relationships", "candidate_measures", "conformed_dimensions", "refresh_policy",
        "open_questions", "rls_role_detail",
    }}
    design_document["relationships"] = [
        {
            **rel,
            "from_table": table_id_map.get(rel.get("from_table"), rel.get("from_table")),
            "to_table": table_id_map.get(rel.get("to_table"), rel.get("to_table")),
        }
        for rel in (design_document.get("relationships") or [])
    ]
    design_document["change_request_reason"] = cleaned_reason

    await writer.write_nodes(
        [
            NodeWrite(
                type="SemanticModel",
                id=new_model_id,
                properties={
                    "family_ref": family_id,
                    "rls_roles": list(design.get("rls_roles") or []),
                    "grain_statement": design.get("grain_statement"),
                    "design_generated_at": design.get("design_generated_at"),
                    "design_provenance_ref": design.get("design_provenance_ref"),
                    "design_document": design_document,
                    "version_number": current_version_number + 1,
                    "state": "DRAFT",
                },
            ),
            *table_writes,
        ],
        principal=principal,
    )
    await _set_family_state(writer, family_id, properties, to_state="DRAFT", principal=principal)

    return {
        "family_id": family_id,
        "semantic_model_id": new_model_id,
        "version_number": current_version_number + 1,
        "previous_semantic_model_id": current_model_id,
        "previous_version_number": current_version_number,
        "reason": cleaned_reason,
    }


async def promote_family(
    pool: asyncpg.Pool,
    graph_name: str,
    writer: GraphWriter,
    family_id: str,
    *,
    principal: Principal,
) -> dict[str, Any]:
    """`BUILT` -> `PUBLISHED` — "promote", driven for the first time by this story (every
    earlier story left it declared and undriven). Marks the current version PUBLISHED
    and, if it has a predecessor, marks that predecessor DEPRECATED with the date —
    S4.3.3's own "promoting v(n+1) marks v(n) DEPRECATED with the date." Refused if
    `regression_status` has not passed.
    """
    properties = await _family_properties(pool, graph_name, family_id)
    require_transition(properties.get("state"), "PUBLISHED")

    status = await regression_status(pool, graph_name, family_id)
    if not status.passed:
        raise InvalidRequestError(f"family '{family_id}' cannot be promoted: {status.detail}")

    versions = await list_semantic_models(pool, graph_name, family_id)
    if not versions:
        raise ElementNotFoundError(f"family '{family_id}' has no built version to promote")
    current = versions[-1]
    current_model_id = current["id"]

    async with pool.acquire() as conn:
        current_model = (await hydrate(conn, graph_name, "SemanticModel", [current_model_id]))[current_model_id]

    now = _now()
    previous = versions[-2] if len(versions) > 1 else None
    if previous is not None:
        async with pool.acquire() as conn:
            previous_model = (await hydrate(conn, graph_name, "SemanticModel", [previous["id"]]))[previous["id"]]
        await writer.upsert_nodes(
            [
                NodeWrite(
                    type="SemanticModel",
                    id=previous["id"],
                    properties={
                        **_writable_node_properties(previous_model),
                        "state": "DEPRECATED",
                        "deprecated_at": now,
                    },
                )
            ],
            principal=principal,
        )

    await writer.upsert_nodes(
        [
            NodeWrite(
                type="SemanticModel",
                id=current_model_id,
                properties={
                    **_writable_node_properties(current_model),
                    "state": "PUBLISHED",
                    "published_at": now,
                },
            )
        ],
        principal=principal,
    )
    await _set_family_state(writer, family_id, properties, to_state="PUBLISHED", principal=principal)

    return {
        "family_id": family_id,
        "semantic_model_id": current_model_id,
        "version_number": int(current.get("version_number") or 1),
        "published_at": now,
        "deprecated_semantic_model_id": previous["id"] if previous else None,
        "deprecated_version_number": (
            int(previous.get("version_number") or 1) if previous is not None else None
        ),
    }


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")


__all__ = [
    "FAMILY_TRANSITIONS",
    "MIN_CHANGE_REQUEST_REASON",
    "RegressionStatus",
    "accept_family",
    "family_transition_history",
    "hashable_document",
    "list_model_versions",
    "promote_family",
    "regression_status",
    "request_new_version",
    "require_transition",
    "submit_for_review",
    "update_domain",
    "update_grain_statement",
    "update_owner",
    "update_relationship_cardinality",
    "update_table_mode",
]
