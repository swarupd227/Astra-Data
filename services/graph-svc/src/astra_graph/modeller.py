"""The Modeller — a model design proposal generated from the graph. Story S4.1.1.

    "As a model engineer, I want a model design proposal generated for each family from
    the graph, so that I start from a draft that already knows the sources, grain and
    measures."

§8.6: "For an approved-for-review ModelFamily, produces a model design proposal: candidate
tables (from the union of member datasources, deduplicated by connection + table),
candidate relationships (from Tableau joins and relationships), candidate conformed
dimensions, the union of measures (from member calculated fields, deduplicated by
normalised AST), storage mode recommendation per table, RLS scaffold from Tableau user
filters, and a list of open design questions."

**Every read here reuses ``lineage.py``'s hop/hydrate primitives** — the same
``children``/``hydrate``/``calc_shapes`` functions S3.1.1's Cartographer already promoted
to module level for exactly this reason: a second, near-identical traversal is the drift
this codebase has been bitten by before (ADR 0022). What differs from the Cartographer's
own ``gather_reach`` is that this needs full hydrated records (a table's row estimate, a
connection's class, a datasource's refresh schedule), not just id sets for Jaccard scoring
— so this module runs its own hop sequence rather than reusing ``Gathered``.

**Tables are deduplicated "by connection + table" for free.** A ``Table`` node's identity
is already derived from its owning ``Connection`` plus its name and schema at harvest time
(S1.3.1's identity rule), so two datasources reaching the same physical table already
resolve to the same node id — collecting the *set* of table ids a family's members reach is
the deduplication, not a second pass over it.

**Candidate measures are deduplicated by AST shape, the Pattern Library's own normaliser**
(``context.signature.ast_shape``, S1.3.1) — the same function two calculations must match
under to be considered "the same" anywhere else in this codebase. A shape shared by two or
more calculations becomes one candidate measure; a *name* shared by calculations with
*different* shapes is the opposite finding — a genuine disagreement about what that name
means — and becomes an open question instead (§8.6's own "duplicate measures with different
definitions").

**No ``Measure`` node is written.** §4.1.1 declares ``Measure.dax`` and
``Measure.provenance_ref`` required — "the Transpiler's product" (E5, not built). A
candidate measure here has no DAX yet; it is a design-time judgement (which calculations
collapse into one measure) recorded in ``SemanticModel.design_document``, not yet the
first-class node the Transpiler will eventually produce.

**RLS is read, not re-derived.** ``Workbook.rls``/``.rls_expression`` (story S2.3.2) already
tell this module which member workbooks restrict rows and how; the criterion asks for a
scaffold "from Tableau user filters", and that is exactly what those two properties are.

**Storage mode and relationship cardinality are heuristics, disclosed as such.** Neither the
harvested estate nor this platform yet knows a table's real primary key, so "which side of a
join is the many side" is inferred from ``Table.row_estimate`` — confidently when one side
clearly dwarfs the other, and as an open question when it cannot tell. Storage mode follows
the same discipline: an extracted, human-scale table recommends Import; a live connection
with no extract recommends DirectQuery; a very large extracted table recommends Direct
Lake. See ADR 0028 for the exact thresholds and the reasoning against alternatives.

**Naming and the grain statement are drafted deterministically today, and say so.** §5.5's
Model Gateway does not exist yet — nothing in this codebase has ever called an external
model (Cartographer's own "ASSISTED" family naming, S3.1.1, is the same kind of template).
A real ``ProvenanceRecord`` is written for the drafted grain statement regardless: mode
``ASSISTED`` (advisory, edited by the Semantic Model Engineer at S4.1.2, per §8.2's own
definition of the mode), ``model`` left null because no model call happened. See ADR 0028
and ``context.contract.ContractName.MODELLER_FAMILY``'s own note on why no formal
``ContextContract`` is registered for this record's ``context_hash`` yet.

**A re-run replaces the family's whole proposal.** Unlike trains and families, there is no
"engineer has edited this" pin to respect yet — editing is S4.1.2's screen, not built. Every
call retires this family's previous ``ModelTable`` nodes and ``SemanticModel`` node (if any)
and writes fresh ones, the same starting posture S3.1.1 and S3.2.1 each had before their own
override story added pinning.
"""

from __future__ import annotations

import logging
import time
from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

import asyncpg

from .cartographer import get_family
from .context.canonical import canonical_json, context_hash
from .context.contract import ContractName
from .context.signature import SignatureError, ast_shape
from .errors import ElementNotFoundError, InvalidRequestError
from .graph.queries import NODE_INDEX_TABLE, OUTGOING_EDGES_SQL
from .ids import new_ulid
from .lineage import children, hydrate
from .principal import Principal
from .provenance import AgentMode, ProvenanceRecord, ProvenanceStore, new_record
from .versions import EVENT_TABLE
from .writes import GraphWriter, NodeWrite

logger = logging.getLogger(__name__)

#: A table an extract already covers, and small enough that a full copy is cheap. Below
#: this, Import is simplest to build and fastest to query; there is no live-connection cost
#: to justify DirectQuery, and no OneLake / Direct Lake plumbing to justify for a table this
#: size. See ADR 0028.
IMPORT_ROW_CEILING = 50_000_000

#: Above this, even an extracted table recommends Direct Lake over Import: a full copy at
#: this scale is slow to refresh and expensive to store twice (source extract, model copy).
DIRECT_LAKE_ROW_FLOOR = IMPORT_ROW_CEILING

#: A row-estimate ratio at or above this is confident enough to call one side of a join
#: "the many side" without a real key. Below it, the two tables are close enough in size
#: that guessing direction would be exactly that — a guess — so it is raised as a question.
CONFIDENT_CARDINALITY_RATIO = 3.0

_AGENT = "modeller"
_AGENT_VERSION = "1.0.0"

#: A family the Modeller may (re)propose a design for. Once accepted (story S4.1.2),
#: ``Modeller.run`` refuses — see ``run``'s own note.
PRE_ACCEPT_STATES = frozenset({"PROPOSED", "SINGLETON"})


# --------------------------------------------------------------------------- data shapes


@dataclass(frozen=True, slots=True)
class TableCandidate:
    id: str
    name: str
    schema: str | None
    source_table_refs: tuple[str, ...]
    mode: str
    mode_reason: str
    row_estimate: int | None
    custom_sql: bool

    def as_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "schema": self.schema,
            "source_table_refs": list(self.source_table_refs),
            "mode": self.mode,
            "mode_reason": self.mode_reason,
            "row_estimate": self.row_estimate,
            "custom_sql": self.custom_sql,
        }


@dataclass(frozen=True, slots=True)
class RelationshipCandidate:
    from_table: str
    to_table: str
    cardinality: str | None
    """``many_to_one`` (from_table is the many side) or ``one_to_many``. ``None`` when the
    row estimates could not settle a confident direction — see ``open_questions``."""

    confidence: str
    reason: str
    join_clause: str | None

    def as_dict(self) -> dict[str, Any]:
        return {
            "from_table": self.from_table,
            "to_table": self.to_table,
            "cardinality": self.cardinality,
            "confidence": self.confidence,
            "reason": self.reason,
            "join_clause": self.join_clause,
        }


@dataclass(frozen=True, slots=True)
class MeasureCandidate:
    name: str
    source_calc_refs: tuple[str, ...]
    dedup_decision: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "source_calc_refs": list(self.source_calc_refs),
            "dedup_decision": self.dedup_decision,
        }


@dataclass(frozen=True, slots=True)
class RlsRoleCandidate:
    name: str
    expression: str
    source_workbook_ids: tuple[str, ...]

    def as_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "expression": self.expression,
            "source_workbook_ids": list(self.source_workbook_ids),
        }


@dataclass(frozen=True, slots=True)
class ConformedDimensionCandidate:
    dimension: str
    shared_with_family_ids: tuple[str, ...]

    def as_dict(self) -> dict[str, Any]:
        return {
            "dimension": self.dimension,
            "shared_with_family_ids": list(self.shared_with_family_ids),
        }


@dataclass(frozen=True, slots=True)
class OpenQuestion:
    category: str
    question: str
    evidence: Mapping[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {"category": self.category, "question": self.question, "evidence": dict(self.evidence)}


@dataclass(frozen=True, slots=True)
class DesignProposal:
    family_id: str
    semantic_model_id: str
    tables: tuple[TableCandidate, ...]
    relationships: tuple[RelationshipCandidate, ...]
    grain_statement: str
    grain_provenance_id: str
    conformed_dimensions: tuple[ConformedDimensionCandidate, ...]
    measures: tuple[MeasureCandidate, ...]
    rls_roles: tuple[RlsRoleCandidate, ...]
    refresh_policy: Mapping[str, Any]
    open_questions: tuple[OpenQuestion, ...]
    member_count: int
    generated_at: str
    elapsed_seconds: float

    def as_dict(self) -> dict[str, Any]:
        return {
            "family_id": self.family_id,
            "semantic_model_id": self.semantic_model_id,
            "tables": [t.as_dict() for t in self.tables],
            "relationships": [r.as_dict() for r in self.relationships],
            "grain_statement": self.grain_statement,
            "grain_provenance_id": self.grain_provenance_id,
            "conformed_dimensions": [c.as_dict() for c in self.conformed_dimensions],
            "measures": [m.as_dict() for m in self.measures],
            "rls_roles": [r.as_dict() for r in self.rls_roles],
            "refresh_policy": dict(self.refresh_policy),
            "open_questions": [q.as_dict() for q in self.open_questions],
            "member_count": self.member_count,
            "generated_at": self.generated_at,
            "elapsed_seconds": round(self.elapsed_seconds, 3),
        }


@dataclass(frozen=True, slots=True)
class FamilyEvidence:
    """Every source record a family's members reach — the raw input the pure functions
    below turn into a proposal. Returned separately from ``DesignProposal`` so the pure
    heuristics can be unit-tested without a database."""

    member_ids: tuple[str, ...]
    tables: dict[str, dict[str, Any]]
    """Table id -> properties."""

    connections: dict[str, dict[str, Any]]
    datasources: dict[str, dict[str, Any]]
    connection_tables: dict[str, set[str]]
    """Connection id -> the Table ids it CONNECTS_TO."""

    datasource_connections: dict[str, set[str]]
    """Datasource id -> the Connection ids it CONNECTS_TO."""

    join_edges: tuple[dict[str, Any], ...]
    """Connection->Table edges with ``from_id``, ``to_id``, ``join_clause``."""

    calculations: dict[str, dict[str, Any]]
    workbooks: dict[str, dict[str, Any]]


# ------------------------------------------------------------------------ pure functions


def recommend_storage_mode(
    *, extract_flag: bool | None, connection_class: str | None, row_estimate: int | None
) -> tuple[str, str]:
    """(mode, reason). See ADR 0028 for the thresholds and the alternatives rejected.

    An extract is the strongest signal: it means the estate has already chosen to copy this
    table rather than query it live, and Import (or, past the row ceiling, Direct Lake)
    mirrors that choice. Absent an extract, a live connection recommends DirectQuery — there
    is nothing to import. Neither is proof; both are named as heuristics in the reason.
    """
    if extract_flag:
        if row_estimate is not None and row_estimate >= DIRECT_LAKE_ROW_FLOOR:
            return (
                "directlake",
                f"an extract exists but row_estimate ({row_estimate:,}) is at or above the "
                f"{DIRECT_LAKE_ROW_FLOOR:,}-row Import ceiling; Direct Lake avoids copying "
                f"a table this large into the model",
            )
        return (
            "import",
            "an extract already exists for this table (Datasource.extract_flag); Import "
            "mirrors that choice"
            + (f" ({row_estimate:,} rows, within the Import ceiling)" if row_estimate else ""),
        )
    return (
        "directquery",
        "no extract exists (Datasource.extract_flag is false or absent)"
        + (f" for this {connection_class} connection" if connection_class else "")
        + "; DirectQuery reads the source live rather than copying it in without one",
    )


def infer_cardinality(
    *, from_row_estimate: int | None, to_row_estimate: int | None
) -> tuple[str | None, str, str]:
    """(cardinality, confidence, reason) for one join between two tables.

    ``cardinality`` is ``many_to_one`` (the *from* table is the many side) or
    ``one_to_many``, or ``None`` when neither row estimate is known or they are too close
    to call — this platform has no primary-key metadata to settle it any other way.
    """
    if from_row_estimate is None or to_row_estimate is None:
        return (
            None,
            "unknown",
            "row_estimate is missing on at least one side; there is no key metadata to "
            "infer cardinality from, so this needs the data owner's confirmation",
        )
    if from_row_estimate == 0 or to_row_estimate == 0:
        return (
            None,
            "unknown",
            "one side has a row_estimate of zero; cardinality cannot be inferred from size",
        )
    ratio = from_row_estimate / to_row_estimate
    if ratio >= CONFIDENT_CARDINALITY_RATIO:
        return (
            "many_to_one",
            "row_estimate",
            f"the from-table has {from_row_estimate:,} rows against {to_row_estimate:,} "
            f"({ratio:.1f}x) — large enough to call it the many side with confidence",
        )
    if ratio <= 1 / CONFIDENT_CARDINALITY_RATIO:
        return (
            "one_to_many",
            "row_estimate",
            f"the from-table has {from_row_estimate:,} rows against {to_row_estimate:,} "
            f"({ratio:.2f}x) — small enough to call it the one side with confidence",
        )
    return (
        None,
        "ambiguous",
        f"row estimates are within {CONFIDENT_CARDINALITY_RATIO:.0f}x of each other "
        f"({from_row_estimate:,} vs {to_row_estimate:,}) — too close to infer a direction "
        f"without real key metadata",
    )


def draft_grain_statement(grain_dims: Sequence[str]) -> str:
    """A prose rendering of a candidate grain tuple. Deterministic and reproducible today
    — see the module docstring on why this is not (yet) a real model call."""
    dims = [d for d in grain_dims if d]
    if not dims:
        return "The grain could not be determined from member sheets; confirm with the data owner."
    if len(dims) == 1:
        return f"One row per {dims[0]}."
    return "One row per " + ", ".join(dims[:-1]) + f" and {dims[-1]}."


def dedupe_measures(
    calculations: Mapping[str, Mapping[str, Any]],
) -> tuple[tuple[MeasureCandidate, ...], tuple[OpenQuestion, ...]]:
    """The union of member calculated fields, one candidate measure per distinct AST shape
    (§8.6's own "deduplicated by normalised AST"), plus an open question for every name that
    two calculations share while disagreeing about its shape — "duplicate measures with
    different definitions", the same words §8.6 uses.
    """
    by_shape: dict[str, list[str]] = {}
    unshaped: list[str] = []
    for calc_id, props in calculations.items():
        try:
            shape = ast_shape(props.get("formula_ast"))
        except SignatureError:
            unshaped.append(calc_id)
            continue
        by_shape.setdefault(shape, []).append(calc_id)

    measures: list[MeasureCandidate] = []
    for _shape, calc_ids in sorted(by_shape.items()):
        names = sorted({str(calculations[c].get("name") or c) for c in calc_ids})
        name = names[0]
        if len(calc_ids) == 1:
            decision = "single source calculation; no deduplication needed"
        else:
            decision = (
                f"merged {len(calc_ids)} calculations sharing one AST shape "
                f"({', '.join(names)})" if len(names) > 1 else
                f"merged {len(calc_ids)} calculations with an identical definition"
            )
        measures.append(
            MeasureCandidate(name=name, source_calc_refs=tuple(sorted(calc_ids)), dedup_decision=decision)
        )
    for calc_id in unshaped:
        props = calculations[calc_id]
        measures.append(
            MeasureCandidate(
                name=str(props.get("name") or calc_id),
                source_calc_refs=(calc_id,),
                dedup_decision="AST shape could not be computed; carried through unmerged",
            )
        )

    # Same normalised name, different shapes: a real disagreement about what the name means.
    by_name: dict[str, set[str]] = {}
    for calc_id, props in calculations.items():
        name = str(props.get("name") or "").strip().lower()
        if not name:
            continue
        try:
            shape = ast_shape(props.get("formula_ast"))
        except SignatureError:
            shape = f"__unshaped__:{calc_id}"
        by_name.setdefault(name, set()).add(shape)

    questions = [
        OpenQuestion(
            category="duplicate_measure",
            question=(
                f"member workbooks disagree on the definition of '{name}' — "
                f"{len(shapes)} distinct formulas were found for the same name"
            ),
            evidence={"name": name, "distinct_definitions": len(shapes)},
        )
        for name, shapes in sorted(by_name.items())
        if len(shapes) > 1
    ]
    return tuple(measures), tuple(questions)


def derive_rls_roles(
    workbooks: Mapping[str, Mapping[str, Any]],
) -> tuple[tuple[RlsRoleCandidate, ...], tuple[OpenQuestion, ...]]:
    """One role per distinct ``rls_expression`` among member workbooks with ``rls=true``
    (story S2.3.2) — the "Tableau user filters" §8.6 asks the scaffold to come from,
    already recorded on the Workbook rather than re-derived from Filter nodes."""
    by_expression: dict[str, list[str]] = {}
    unexplained: list[str] = []
    for workbook_id, props in workbooks.items():
        if not props.get("rls"):
            continue
        expression = props.get("rls_expression")
        if not expression:
            unexplained.append(workbook_id)
            continue
        by_expression.setdefault(str(expression), []).append(workbook_id)

    roles: list[RlsRoleCandidate] = []
    for expression, member_ids in sorted(by_expression.items()):
        member_ids = sorted(member_ids)
        if len(member_ids) == 1:
            name = f"RLS — {workbooks[member_ids[0]].get('name') or member_ids[0]}"
        else:
            name = f"RLS — shared across {len(member_ids)} workbooks"
        roles.append(
            RlsRoleCandidate(name=name, expression=expression, source_workbook_ids=tuple(member_ids))
        )

    questions = [
        OpenQuestion(
            category="rls_conflict",
            question=(
                f"workbook '{workbooks[wb].get('name') or wb}' is flagged rls=true but its "
                f"rls_expression was not recorded; the role cannot be scaffolded without it"
            ),
            evidence={"workbook_id": wb},
        )
        for wb in sorted(unexplained)
    ]
    return tuple(roles), tuple(questions)


def derive_refresh_policy(datasources: Mapping[str, Mapping[str, Any]]) -> dict[str, Any]:
    """A conservative roll-up across member datasources: the most frequent schedule among
    extracted sources wins (under-refreshing a shared model is the worse failure mode);
    a live (non-extracted) connection needs no schedule of its own. Mixed extracted and
    live sources are reported as ``mixed`` so the mismatch is visible, not silently
    resolved."""
    extracted = [d for d in datasources.values() if d.get("extract_flag")]
    live = [d for d in datasources.values() if not d.get("extract_flag")]
    schedules = [str(d["refresh_schedule"]) for d in extracted if d.get("refresh_schedule")]

    if extracted and not live:
        mode = "scheduled"
    elif live and not extracted:
        mode = "directquery"
    elif extracted and live:
        mode = "mixed"
    else:
        mode = "unknown"

    schedule = None
    if schedules:
        counts = Counter(schedules)
        top = counts.most_common()
        best_count = top[0][1]
        schedule = sorted(s for s, n in top if n == best_count)[0]

    return {
        "mode": mode,
        "schedule": schedule,
        "extracted_source_count": len(extracted),
        "live_source_count": len(live),
        "distinct_schedules": sorted(set(schedules)),
    }


def conformed_dimensions_shared_with(
    this_family_grain: Sequence[str], other_families: Sequence[Mapping[str, Any]]
) -> tuple[ConformedDimensionCandidate, ...]:
    """For each of this family's own candidate grain dimensions, which other live families'
    own grain also names it. A name match, case-insensitively — this platform has no
    dimension registry yet, so a shared *name* is the evidence available today."""
    normalised_this = {d.strip().lower(): d.strip() for d in this_family_grain if d.strip()}
    out: list[ConformedDimensionCandidate] = []
    for lowered, original in sorted(normalised_this.items()):
        sharing = sorted(
            other["id"]
            for other in other_families
            if any(str(d).strip().lower() == lowered for d in other.get("grain") or ())
        )
        out.append(ConformedDimensionCandidate(dimension=original, shared_with_family_ids=tuple(sharing)))
    return tuple(out)


def find_structural_open_questions(evidence: FamilyEvidence) -> tuple[OpenQuestion, ...]:
    """Ambiguous keys: a table sourced from custom SQL carries no key metadata the graph
    can vouch for (§8.6's own "ambiguous keys")."""
    return tuple(
        OpenQuestion(
            category="ambiguous_key",
            question=(
                f"table '{table.get('name') or table_id}' is sourced from custom SQL; its "
                f"grain and keys cannot be read from table metadata and need confirmation"
            ),
            evidence={"table_id": table_id},
        )
        for table_id, table in sorted(evidence.tables.items())
        if table.get("custom_sql")
    )


# --------------------------------------------------------------------------- evidence read


async def gather_family_evidence(
    pool: asyncpg.Pool, graph_name: str, member_ids: Sequence[str]
) -> FamilyEvidence:
    """The full chain a family's members reach, hydrated — not just the id sets S3.1.1's
    ``gather_reach`` collects for Jaccard scoring. Every hop reuses ``lineage.py``'s
    ``children``/``hydrate`` (ADR 0022's own reasoning: one traversal, not a second one that
    can drift from it)."""
    async with pool.acquire() as conn:
        workbooks = await hydrate(conn, graph_name, "Workbook", member_ids)

        sheets = await children(conn, graph_name, member_ids, "CONTAINS", "Worksheet")
        sheet_ids = sorted({s for owned in sheets.values() for s in owned})

        datasources_by_sheet = await children(conn, graph_name, sheet_ids, "USES_DATASOURCE", "Datasource")
        datasource_ids = sorted({d for owned in datasources_by_sheet.values() for d in owned})
        datasources = await hydrate(conn, graph_name, "Datasource", datasource_ids)

        connections_by_ds = await children(conn, graph_name, datasource_ids, "CONNECTS_TO", "Connection")
        connection_ids = sorted({c for owned in connections_by_ds.values() for c in owned})
        connections = await hydrate(conn, graph_name, "Connection", connection_ids)

        connection_tables = await children(conn, graph_name, connection_ids, "CONNECTS_TO", "Table")
        table_ids = sorted({t for owned in connection_tables.values() for t in owned})
        tables = await hydrate(conn, graph_name, "Table", table_ids)

        join_edges = await _connects_to_edges(conn, graph_name, connection_ids)

        datasource_calcs = await children(conn, graph_name, datasource_ids, "HAS_FIELD", "CalculatedField")
        calc_ids = sorted({c for owned in datasource_calcs.values() for c in owned})
        calculations = await hydrate(conn, graph_name, "CalculatedField", calc_ids)

    return FamilyEvidence(
        member_ids=tuple(member_ids),
        tables=tables,
        connections=connections,
        datasources=datasources,
        connection_tables=connection_tables,
        datasource_connections=connections_by_ds,
        join_edges=join_edges,
        calculations=calculations,
        workbooks=workbooks,
    )


async def _connects_to_edges(
    conn: asyncpg.Connection, graph_name: str, connection_ids: Sequence[str]
) -> tuple[dict[str, Any], ...]:
    """Connection->Table ``CONNECTS_TO`` edges, with their ``join_clause`` (§4.1.2).

    ``outgoing_edges``-shaped, but done directly against the index tables the way every
    other module-level helper in this file (and in ``cartographer.py``/``lineage.py``)
    reads the graph, rather than through ``GraphRepository`` — this module is a batch job
    over a family's whole reach, not a single agent's bounded contract.
    """
    if not connection_ids:
        return ()
    rows = await conn.fetch(OUTGOING_EDGES_SQL, list(connection_ids), "CONNECTS_TO", graph_name)
    edge_ids = [row["id"] for row in rows]
    properties = await hydrate(conn, graph_name, "CONNECTS_TO", edge_ids)
    return tuple(
        {
            "id": row["id"],
            "from_id": row["from_id"],
            "to_id": row["to_id"],
            "join_clause": properties.get(row["id"], {}).get("join_clause"),
        }
        for row in rows
    )


def _table_candidates(evidence: FamilyEvidence) -> tuple[TableCandidate, ...]:
    """One candidate per source table reached, each carrying its own fresh id.

    A `ModelTable` node cannot reuse its source `Table`'s id — every node in this graph
    has its own globally unique id (`estate_element_index.id` is a bare primary key,
    checked once at write time by the ontology validator) — so this generates the id the
    write path will actually use, once, here, rather than a second one at write time that
    would leave the proposal this function returns describing a node that was never
    written under that id. `source_table_refs` still carries the true source table id.
    """
    connection_of: dict[str, str] = {}
    for connection_id, table_ids in evidence.connection_tables.items():
        for table_id in table_ids:
            connection_of[table_id] = connection_id

    # Reverse datasource->connection into connection->datasources: a table's storage mode
    # follows whichever datasource(s) reach its connection. More than one datasource
    # reaching the same connection (two sheets, one shared connection) is one design
    # decision, not two, so ties are broken deterministically (sorted, first wins) rather
    # than recommending a different mode per datasource for the same physical table.
    datasources_of_connection: dict[str, list[str]] = {}
    for datasource_id, connection_ids in evidence.datasource_connections.items():
        for connection_id in connection_ids:
            datasources_of_connection.setdefault(connection_id, []).append(datasource_id)

    out = []
    for table_id, table in sorted(evidence.tables.items()):
        table_connection_id = connection_of.get(table_id)
        connection = evidence.connections.get(table_connection_id or "", {})
        datasource_ids = sorted(datasources_of_connection.get(table_connection_id or "", []))
        datasource = evidence.datasources.get(datasource_ids[0], {}) if datasource_ids else {}
        mode, reason = recommend_storage_mode(
            extract_flag=datasource.get("extract_flag"),
            connection_class=connection.get("class"),
            row_estimate=table.get("row_estimate"),
        )
        out.append(
            TableCandidate(
                id=new_ulid(),
                name=str(table.get("name") or table_id),
                schema=table.get("schema"),
                source_table_refs=(table_id,),
                mode=mode,
                mode_reason=reason,
                row_estimate=table.get("row_estimate"),
                custom_sql=bool(table.get("custom_sql")),
            )
        )
    return tuple(out)


def _relationship_candidates(
    evidence: FamilyEvidence, model_table_id_of: Mapping[str, str]
) -> tuple[RelationshipCandidate, ...]:
    """One candidate relationship between every joined table and its connection's own
    "base" table — the one with the largest ``row_estimate`` (a fact table has the most
    rows; tied or unknown estimates fall back to the lowest table id for determinism).

    A Tableau datasource's real join graph can chain tables in any order, and this
    platform holds no key metadata to reconstruct it faithfully. Defaulting every table to
    a direct relationship with the one biggest table is a deliberate simplification, not a
    guess dressed up as fact — and it is the *right* default independently of whatever
    Tableau's join graph actually looked like, because §12.3's own conformance rule is
    "star schema only": the target model has to end up in this shape regardless. See ADR
    0028.

    ``from_table``/``to_table`` are the *`ModelTable`* ids (``model_table_id_of`` maps a
    source `Table` id, which is what ``evidence`` is keyed by, to the `ModelTable` id
    ``_table_candidates`` generated for it) — a relationship describes the target model,
    and a reader following it into ``design_document["tables"]`` must find something there.
    """
    by_connection: dict[str, list[dict[str, Any]]] = {}
    for edge in evidence.join_edges:
        by_connection.setdefault(edge["from_id"], []).append(edge)

    out: list[RelationshipCandidate] = []
    for _connection_id, edges in sorted(by_connection.items()):
        if len(edges) < 2:
            continue
        base = min(
            edges,
            key=lambda e: (
                -(evidence.tables.get(e["to_id"], {}).get("row_estimate") or -1),
                e["to_id"],
            ),
        )
        for edge in edges:
            if edge["to_id"] == base["to_id"]:
                continue
            base_table = evidence.tables.get(base["to_id"], {})
            other_table = evidence.tables.get(edge["to_id"], {})
            cardinality, confidence, reason = infer_cardinality(
                from_row_estimate=other_table.get("row_estimate"),
                to_row_estimate=base_table.get("row_estimate"),
            )
            out.append(
                RelationshipCandidate(
                    from_table=model_table_id_of.get(edge["to_id"], edge["to_id"]),
                    to_table=model_table_id_of.get(base["to_id"], base["to_id"]),
                    cardinality=cardinality,
                    confidence=confidence,
                    reason=reason,
                    join_clause=edge.get("join_clause"),
                )
            )
    return tuple(out)


def _relationship_open_questions(relationships: Sequence[RelationshipCandidate]) -> tuple[OpenQuestion, ...]:
    return tuple(
        OpenQuestion(
            category="grain_conflict",
            question=(
                f"the relationship between '{r.from_table}' and '{r.to_table}' has no "
                f"confident cardinality: {r.reason}"
            ),
            evidence={"from_table": r.from_table, "to_table": r.to_table},
        )
        for r in relationships
        if r.cardinality is None
    )


# ------------------------------------------------------------------------------- Modeller


class Modeller:
    """Generates and writes a model design proposal for one ``ModelFamily``."""

    def __init__(
        self,
        pool: asyncpg.Pool,
        *,
        graph_name: str,
        writer: GraphWriter,
        provenance_store: ProvenanceStore,
    ) -> None:
        self._pool = pool
        self._graph = graph_name
        self._writer = writer
        self._provenance = provenance_store

    @property
    def pool(self) -> asyncpg.Pool:
        return self._pool

    @property
    def graph_name(self) -> str:
        return self._graph

    @property
    def writer(self) -> GraphWriter:
        return self._writer

    async def run(self, family_id: str, *, principal: Principal) -> DesignProposal:
        started = time.perf_counter()

        family = await get_family(self._pool, self._graph, family_id)
        if family is None:
            raise ElementNotFoundError(f"no ModelFamily '{family_id}'")
        state = family.get("state")
        if state not in PRE_ACCEPT_STATES:
            # Story S4.1.2: once a family is accepted into DRAFT, an engineer may be
            # editing the proposal by hand (grain statement, table storage mode,
            # relationship cardinality). This module's retire-and-replace has no notion of
            # which edits to keep, so it refuses outright rather than silently discarding
            # them — the same "never a warning, never overridable" posture S3.2.2 takes for
            # a family-splitting move. A genuine "start over" is real future scope this
            # story does not claim.
            raise InvalidRequestError(
                f"family '{family_id}' has already been accepted (state: {state!r}); "
                f"generating a new proposal here would risk discarding a Semantic Model "
                f"Engineer's edits, so this is refused once a family has left PROPOSED/"
                f"SINGLETON. Edit the existing proposal in the Model Detail screen instead."
            )
        member_ids = list(family["members"])

        evidence = await gather_family_evidence(self._pool, self._graph, member_ids)

        tables = _table_candidates(evidence)
        model_table_id_of = {table.source_table_refs[0]: table.id for table in tables}
        relationships = _relationship_candidates(evidence, model_table_id_of)
        measures, dedup_questions = dedupe_measures(evidence.calculations)
        rls_roles, rls_questions = derive_rls_roles(evidence.workbooks)
        refresh_policy = derive_refresh_policy(evidence.datasources)

        other_families = await _other_families(self._pool, self._graph, family_id)
        conformed = conformed_dimensions_shared_with(family["grain"], other_families)

        grain_statement = draft_grain_statement(family["grain"])
        grain_provenance = await self._record_grain_provenance(
            family_id=family_id,
            grain_statement=grain_statement,
            evidence=evidence,
            principal=principal,
        )

        open_questions = (
            *find_structural_open_questions(evidence),
            *_relationship_open_questions(relationships),
            *dedup_questions,
            *rls_questions,
        )

        semantic_model_id = await self._write(
            family_id=family_id,
            tables=tables,
            relationships=relationships,
            grain_statement=grain_statement,
            grain_provenance_id=grain_provenance.id,
            conformed=conformed,
            measures=measures,
            rls_roles=rls_roles,
            refresh_policy=refresh_policy,
            open_questions=open_questions,
            principal=principal,
        )

        elapsed = time.perf_counter() - started
        return DesignProposal(
            family_id=family_id,
            semantic_model_id=semantic_model_id,
            tables=tables,
            relationships=relationships,
            grain_statement=grain_statement,
            grain_provenance_id=grain_provenance.id,
            conformed_dimensions=conformed,
            measures=measures,
            rls_roles=rls_roles,
            refresh_policy=refresh_policy,
            open_questions=open_questions,
            member_count=len(member_ids),
            generated_at=_now(),
            elapsed_seconds=elapsed,
        )

    async def _record_grain_provenance(
        self,
        *,
        family_id: str,
        grain_statement: str,
        evidence: FamilyEvidence,
        principal: Principal,
    ) -> ProvenanceRecord:
        """A real, reproducible provenance record for the ASSISTED grain-statement draft.

        No formal ``ContextContract`` is registered for ``MODELLER_FAMILY`` (see that
        enum member's own docstring) — this hashes the gathered evidence directly with the
        same canonical-JSON utility every other contract uses, so the record is still
        honest and reproducible without the fragment-validated machinery a real model call
        would need.
        """
        document = {
            "family_id": family_id,
            "member_ids": sorted(evidence.member_ids),
            "table_ids": sorted(evidence.tables),
            "calculation_ids": sorted(evidence.calculations),
        }
        payload = canonical_json(document)
        graph_version, _ = await _current_version(self._pool, self._graph)
        record = new_record(
            artefact_kind="MODEL_DESIGN_GRAIN_STATEMENT",
            artefact_ref=family_id,
            artefact_content_hash=context_hash(grain_statement.encode("utf-8")),
            agent=_AGENT,
            agent_version=_AGENT_VERSION,
            mode=AgentMode.ASSISTED,
            contract=ContractName.MODELLER_FAMILY,
            subject_id=family_id,
            context_hash=context_hash(payload),
            graph_version=graph_version,
            model=None,
            created_by=principal.value,
        )
        return await self._provenance.record(record)

    async def _write(
        self,
        *,
        family_id: str,
        tables: Sequence[TableCandidate],
        relationships: Sequence[RelationshipCandidate],
        grain_statement: str,
        grain_provenance_id: str,
        conformed: Sequence[ConformedDimensionCandidate],
        measures: Sequence[MeasureCandidate],
        rls_roles: Sequence[RlsRoleCandidate],
        refresh_policy: Mapping[str, Any],
        open_questions: Sequence[OpenQuestion],
        principal: Principal,
    ) -> str:
        await _retire_previous_design(self._writer, self._pool, self._graph, family_id, principal=principal)

        semantic_model_id = new_ulid()
        design_document = {
            "relationships": [r.as_dict() for r in relationships],
            "candidate_measures": [m.as_dict() for m in measures],
            "conformed_dimensions": [c.as_dict() for c in conformed],
            "refresh_policy": dict(refresh_policy),
            "open_questions": [q.as_dict() for q in open_questions],
            "rls_role_detail": [r.as_dict() for r in rls_roles],
        }
        await self._writer.write_nodes(
            [
                NodeWrite(
                    type="SemanticModel",
                    id=semantic_model_id,
                    properties={
                        "family_ref": family_id,
                        "rls_roles": [r.name for r in rls_roles],
                        "grain_statement": grain_statement,
                        "design_generated_at": _now(),
                        "design_provenance_ref": grain_provenance_id,
                        "design_document": design_document,
                        "version_number": 1,
                    },
                ),
                *(
                    NodeWrite(
                        type="ModelTable",
                        id=table.id,
                        properties={
                            "name": table.name,
                            "source_table_refs": list(table.source_table_refs),
                            "mode": table.mode,
                            "family_ref": family_id,
                            "semantic_model_ref": semantic_model_id,
                            "schema": table.schema,
                            "mode_reason": table.mode_reason,
                            "row_estimate": table.row_estimate,
                            "custom_sql": table.custom_sql,
                        },
                    )
                    for table in tables
                ),
            ],
            principal=principal,
        )
        return semantic_model_id


async def _retire_previous_design(
    writer: GraphWriter,
    pool: asyncpg.Pool,
    graph_name: str,
    family_id: str,
    *,
    principal: Principal,
) -> None:
    """Retire this family's previous ``SemanticModel`` and ``ModelTable`` nodes, if any —
    see the module docstring on why a re-run replaces rather than merges (no editing exists
    to protect yet; that is S4.1.2's pinning to add)."""
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            f"""
            SELECT id, label FROM {NODE_INDEX_TABLE}
             WHERE graph = $1 AND kind = 'node' AND label IN ('SemanticModel', 'ModelTable')
               AND retired_at IS NULL
            """,
            graph_name,
        )
        by_label: dict[str, list[str]] = {}
        for row in rows:
            by_label.setdefault(row["label"], []).append(row["id"])
        properties = await hydrate(conn, graph_name, "SemanticModel", by_label.get("SemanticModel", []))
        table_properties = await hydrate(conn, graph_name, "ModelTable", by_label.get("ModelTable", []))

    stale_ids = [
        node_id for node_id, props in properties.items() if props.get("family_ref") == family_id
    ] + [
        node_id for node_id, props in table_properties.items() if props.get("family_ref") == family_id
    ]
    for node_id in stale_ids:
        await writer.retire_node(
            node_id,
            reason="superseded by a new Modeller design proposal run",
            principal=principal,
        )


async def _other_families(pool: asyncpg.Pool, graph_name: str, family_id: str) -> list[dict[str, Any]]:
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            f"""
            SELECT id FROM {NODE_INDEX_TABLE}
             WHERE graph = $1 AND kind = 'node' AND label = 'ModelFamily' AND retired_at IS NULL
               AND id != $2
            """,
            graph_name,
            family_id,
        )
        ids = [row["id"] for row in rows]
        properties = await hydrate(conn, graph_name, "ModelFamily", ids)
    return [
        {"id": family_id_, "grain": [p.strip() for p in str(props.get("grain") or "").split(",") if p.strip()]}
        for family_id_, props in properties.items()
    ]


async def list_semantic_models(pool: asyncpg.Pool, graph_name: str, family_id: str) -> list[dict[str, Any]]:
    """Every live ``SemanticModel`` version of one family, oldest first — story S4.3.3's
    own "the console shows both". Before that story a family had at most one; this is
    where "the current one" (`read_design_document`'s own default) and "every one, for the
    Versions tab" both resolve from, so the two can never disagree about what exists.
    """
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            f"""
            SELECT id FROM {NODE_INDEX_TABLE}
             WHERE graph = $1 AND kind = 'node' AND label = 'SemanticModel' AND retired_at IS NULL
            """,
            graph_name,
        )
        models = await hydrate(conn, graph_name, "SemanticModel", [row["id"] for row in rows])
    versions = [
        {"id": mid, **props} for mid, props in models.items() if props.get("family_ref") == family_id
    ]
    versions.sort(key=lambda v: int(v.get("version_number") or 1))
    return versions


async def read_design_document(
    pool: asyncpg.Pool, graph_name: str, family_id: str, *, semantic_model_id: str | None = None
) -> dict[str, Any]:
    """A family's design proposal, as written by ``Modeller.run`` — read back, not
    recomputed. Raises ``ElementNotFoundError`` when nothing has been generated yet.

    Without ``semantic_model_id``, this is **the current version** — the one every
    existing G2/build action already means by "the" design: the highest
    ``version_number`` among this family's live ``SemanticModel`` nodes (absent treated
    as 1, since every family had exactly one before story S4.3.3 could ever produce a
    second). Before that story, "current" and "only" were the same thing and a first-match
    lookup happened to be correct; the moment a family can have a published v(n) and a
    draft v(n+1) alive at once, "first" stops being well-defined and must become "latest."

    Pass ``semantic_model_id`` to read a *specific* version instead — the Versions tab's
    own way of showing an older, published-or-deprecated design without disturbing which
    one every edit/build action still means by default.
    """
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            f"""
            SELECT id, label FROM {NODE_INDEX_TABLE}
             WHERE graph = $1 AND kind = 'node' AND label IN ('SemanticModel', 'ModelTable')
               AND retired_at IS NULL
            """,
            graph_name,
        )
        by_label: dict[str, list[str]] = {}
        for row in rows:
            by_label.setdefault(row["label"], []).append(row["id"])
        models = await hydrate(conn, graph_name, "SemanticModel", by_label.get("SemanticModel", []))
        tables = await hydrate(conn, graph_name, "ModelTable", by_label.get("ModelTable", []))

    if semantic_model_id is not None:
        model = models.get(semantic_model_id)
        if model is None or model.get("family_ref") != family_id:
            raise ElementNotFoundError(f"no SemanticModel '{semantic_model_id}' for family '{family_id}'")
        model_id = semantic_model_id
    else:
        candidates = sorted(
            (
                (mid, props)
                for mid, props in models.items()
                if props.get("family_ref") == family_id
            ),
            key=lambda pair: int(pair[1].get("version_number") or 1),
        )
        if not candidates:
            raise ElementNotFoundError(
                f"no design proposal has been generated for family '{family_id}' yet"
            )
        model_id, model = candidates[-1]

    family_tables = [
        {"id": tid, **props}
        for tid, props in tables.items()
        if (props.get("semantic_model_ref") or "") == model_id
        or (not props.get("semantic_model_ref") and props.get("family_ref") == family_id)
    ]
    return {
        "family_id": family_id,
        "semantic_model_id": model_id,
        "grain_statement": model.get("grain_statement"),
        "design_generated_at": model.get("design_generated_at"),
        "design_provenance_ref": model.get("design_provenance_ref"),
        "version": model.get("version"),
        "version_number": int(model.get("version_number") or 1),
        "state": model.get("state"),
        "published_at": model.get("published_at"),
        "deprecated_at": model.get("deprecated_at"),
        "rls_roles": list(model.get("rls_roles") or []),
        "tables": sorted(family_tables, key=lambda t: t["id"]),
        **(model.get("design_document") or {}),
    }


async def _current_version(pool: asyncpg.Pool, graph_name: str) -> tuple[int, str | None]:
    """The graph's current version — the same definition ``GraphRepository.current_version``
    uses (S1.3.2): the highest event sequence number, zero for an untouched graph."""
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            f"SELECT seq FROM {EVENT_TABLE} WHERE graph = $1 ORDER BY seq DESC LIMIT 1",
            graph_name,
        )
    if row is None:
        return 0, None
    return int(row["seq"]), None


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")


__all__ = [
    "CONFIDENT_CARDINALITY_RATIO",
    "DIRECT_LAKE_ROW_FLOOR",
    "IMPORT_ROW_CEILING",
    "PRE_ACCEPT_STATES",
    "ConformedDimensionCandidate",
    "DesignProposal",
    "FamilyEvidence",
    "MeasureCandidate",
    "Modeller",
    "OpenQuestion",
    "RelationshipCandidate",
    "RlsRoleCandidate",
    "TableCandidate",
    "conformed_dimensions_shared_with",
    "dedupe_measures",
    "derive_refresh_policy",
    "derive_rls_roles",
    "draft_grain_statement",
    "find_structural_open_questions",
    "gather_family_evidence",
    "infer_cardinality",
    "list_semantic_models",
    "read_design_document",
    "recommend_storage_mode",
]
