"""The Transpiler's classifier — specification §9.1, story S5.1.1.

    "Every CalculatedField AST is classified into one of four classes before anything is
    generated... Classification is deterministic and is the first thing the Calibration
    Wave measures, because the class mix drives both the acceleration figure and the
    price."

**What this module is.** §9.1's own C1-C4 table, read directly off the AST the Tableau
grammar already produces (`packages/adapter-sdk`'s ``CalcNode``: ``kind``/``name``/
``children``/``detail``) and the family Appendix B.1 already stamps into every FUNCTION/
AGGREGATE/WINDOW node's own ``detail`` at parse time (`functions.py`'s own words: "Recording
the family on the AST node is what lets the Transpiler ask 'is this C1?' without
re-deriving it from a function name"). This module owns the *default class per family* —
Appendix B.1's own "Default class" column — which nowhere in this codebase records as data
yet; everything else it needs is already on the node.

**What this module is not.** The rules engine that rewrites an AST into a target one
(§9.2), the Pattern Library's own shape-matching and promotion pipeline (§9.3, §4.3), and
the reasoning-model generation path (§9.4) are F5.2/F5.3 — not built. A "matched rule id"
here (``pattern_ref``) names which classification rule fired, not a `Pattern` graph node —
no Pattern has ever been authored, since nothing generates one yet.

**A field the AST alone cannot decide.** Tableau writes a parameter reference identically
to a field reference (`parser.py`'s own ``_reference`` docstring: "the caller ... decides").
So whether a calculation depends on a *parameter* — Appendix B.1's own C2 "Parameters" row —
is resolved from the graph's real `DEPENDS_ON` edges to `Parameter` nodes, not guessed from
the AST shape. Table-calc addressing is the same kind of gap in the other direction: the
grammar always records it ``"unresolved"`` (§6.2: addressing "comes from the sheet, not from
the expression"), and nothing in this codebase has ever resolved it — so this classifier
resolves it itself, from the encoding `Worksheet`'s own `rows_shelf`/`cols_shelf` (S2.3.2),
the same real data §9.1's own detection rule ("addressing resolvable from the sheet") names.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

import asyncpg

from .graph.queries import NODE_INDEX_TABLE
from .lineage import children, hydrate
from .ontology.types import BASE_NODE_PROPERTIES
from .principal import Principal
from .provenance import ProvenanceStore
from .redesign import C4_PROPERTIES, c4_properties
from .versions import EVENT_TABLE
from .writes import GraphWriter, NodeWrite

#: Bumped whenever the rule set below changes — S5.1.1's own "re-classification runs when
#: the rule set or pattern library changes." A field's own ``classifier_version`` is what
#: lets a re-run tell "already classified against the current rules" from "classified
#: before they changed", the same footing `conformance_ruleset_version` already has.
CLASSIFIER_VERSION = 1

_CLASS_ORDER = ("C1", "C2", "C3", "C4")


def _rank(class_: str) -> int:
    return _CLASS_ORDER.index(class_)


def _worse(a: tuple[str, str, str], b: tuple[str, str, str]) -> tuple[str, str, str]:
    """Which of two (class, rule_id, reason) triples is the harder class.

    A calculation's overall class is the worst class any of its nodes need — §9.1's own
    C1 definition is "**every** node in the AST has a one-to-one target equivalent", so one
    node needing more than that is what decides the whole expression, and the reason names
    that one node rather than a summary of all of them.
    """
    return b if _rank(b[0]) > _rank(a[0]) else a


#: Appendix B.1's own "Default class" column, keyed by the family `functions.py` (the
#: Tableau grammar's own registry) already stamps into ``detail``. A function whose family
#: is not here (``lod``, handled separately below by AST shape, not by family) never
#: reaches this table.
_FAMILY_CLASS: dict[str, str] = {
    "aggregate": "C1",
    "logical": "C1",
    "numeric": "C1",
    "type": "C1",
    "string": "C1",
    "date": "C2",
    "set": "C2",
    "rawsql": "C4",
    "attr": "C3",
    "user": "C3",
    "unknown": "C4",
}

#: Appendix B.1, "Arithmetic / logical": "C1 (ZN/IFNULL -> C2 null idiom)".
_NULL_IDIOM = frozenset({"ZN", "IFNULL"})

#: Appendix B.1, "String": "REGEXP -> M or C4". No M pass-through generation path exists
#: yet (F5.2/F5.3), so the conservative half of "M or C4" is the honest default rather than
#: a guess this platform cannot back up.
_REGEXP = frozenset({"REGEXP_MATCH", "REGEXP_EXTRACT", "REGEXP_EXTRACT_NTH", "REGEXP_REPLACE"})

_LOD_NAMES = frozenset({"FIXED", "INCLUDE", "EXCLUDE"})


@dataclass(frozen=True, slots=True)
class ClassificationContext:
    """What §9.1's detection rules need beyond one field's own AST.

    Resolved once per reclassification pass over the whole graph (`reclassify_estate`),
    not once per field, since both facts come from a graph read rather than the AST alone.
    """

    has_parameter_dependency: bool = False
    table_calc_addressing_resolved: bool = False


#: The default context for a field classified on its own, with nothing else known about
#: it — a module-level singleton rather than a mutable call-time default.
_DEFAULT_CONTEXT = ClassificationContext()


@dataclass(frozen=True, slots=True)
class ClassificationResult:
    class_: str
    rule_id: str
    reason: str


def classify(
    ast: Any, *, context: ClassificationContext = _DEFAULT_CONTEXT
) -> ClassificationResult:
    """§9.1's classifier: C1-C4 for one calculation, deterministic from its AST and context."""
    if not isinstance(ast, dict):
        return ClassificationResult(
            "C4", "b1:no_ast", "no AST was recorded for this calculation"
        )
    class_, rule_id, reason = _walk(ast, context, lod_depth=0)
    if context.has_parameter_dependency:
        class_, rule_id, reason = _worse(
            (class_, rule_id, reason),
            ("C2", "b1:parameter", "the calculation depends on a workbook parameter — Appendix B.1's own 'Parameters' row"),
        )
    return ClassificationResult(class_, rule_id, reason)


def _walk(node: Any, context: ClassificationContext, *, lod_depth: int) -> tuple[str, str, str]:
    if not isinstance(node, dict):
        return "C1", "b1:leaf", "a literal or reference with no translation of its own"

    kind = str(node.get("kind") or "")
    name = str(node.get("name") or "")
    detail = dict(node.get("detail") or ())
    children_nodes = node.get("children") or ()

    best = _classify_node(kind, name, detail, context, lod_depth=lod_depth)

    child_lod_depth = lod_depth + (1 if kind == "AGGREGATE" and name in _LOD_NAMES else 0)
    for child in children_nodes:
        best = _worse(best, _walk(child, context, lod_depth=child_lod_depth))
    return best


def _classify_node(
    kind: str, name: str, detail: Mapping[str, str], context: ClassificationContext, *, lod_depth: int
) -> tuple[str, str, str]:
    if kind == "UNKNOWN":
        return (
            "C4",
            "b1:unrecognised_construct",
            "the grammar could not parse this construct at all — kept verbatim, never generable",
        )
    if kind in {"LITERAL", "REFERENCE"}:
        return "C1", "b1:leaf", "a literal or reference with no translation of its own"
    if kind == "OPERATOR":
        return "C1", "b1:operator", "an arithmetic/logical/comparison operator with a direct DAX operator"
    if kind == "CONDITIONAL":
        return "C1", "b1:conditional", "IF/CASE — Appendix B.1's 'Arithmetic / logical' row maps it to IF/SWITCH"
    if kind == "CAST":
        return "C1", "b1:type", "a type cast — Appendix B.1's 'Type' family"
    if kind == "AGGREGATE":
        if name in _LOD_NAMES:
            if lod_depth > 0:
                return (
                    "C3",
                    "b1:nested_lod",
                    "a level-of-detail expression nested inside another — §9.1 flags nested LOD as context-dependent",
                )
            return (
                "C2",
                "b1:lod",
                f"a {{{name} ...}} level-of-detail expression — a structural rewrite to CALCULATE/ALLEXCEPT",
            )
        return "C1", "b1:aggregate", "an aggregate function with a direct DAX equivalent"
    if kind == "WINDOW":
        resolved = context.table_calc_addressing_resolved
        family = detail.get("family", "table_calc_simple")
        if family == "table_calc_complex":
            if resolved:
                return (
                    "C3",
                    "b1:table_calc_complex_resolved",
                    "a complex table calculation whose addressing resolves from the encoding sheet",
                )
            return (
                "C4",
                "b1:table_calc_complex_unresolved",
                "a complex table calculation whose addressing does not resolve from any encoding sheet — Appendix B.1 defaults it to C4",
            )
        if resolved:
            return (
                "C2",
                "b1:table_calc_simple_resolved",
                "a table calculation whose addressing resolves from the encoding sheet — a structural rewrite to a window function",
            )
        return (
            "C3",
            "b1:table_calc_simple_unresolved",
            "a table calculation whose addressing does not resolve from any encoding sheet",
        )
    if kind == "FUNCTION":
        recognised = detail.get("recognised") != "false"
        family = detail.get("family", "unknown")
        if not recognised:
            return (
                "C4",
                "b1:unrecognised_function",
                f"{name} is not in the platform's Appendix B.1 function registry",
            )
        if name in _NULL_IDIOM:
            return (
                "C2",
                "b1:null_idiom",
                f"{name} is Appendix B.1's null-handling idiom — a structural rewrite (e.g. to COALESCE)",
            )
        if name in _REGEXP:
            return (
                "C4",
                "b1:regexp",
                "Appendix B.1 marks REGEXP_* as 'M or C4'; no M pass-through generation path exists yet, so the conservative class applies",
            )
        class_ = _FAMILY_CLASS.get(family)
        if class_ is None:
            return (
                "C4",
                "b1:unmapped_family",
                f"{name} has no recorded Appendix B.1 default class",
            )
        return class_, f"b1:{family}", f"Appendix B.1 classifies the {family} family as {class_} by default"
    return "C1", "b1:structure", "a structural node with a direct target equivalent"


class ClassificationEngine:
    """The pool/graph/writer a classification route needs — the same small wrapper shape
    `Modeller`/`Cartographer` already use, so `app.state.classifier` follows the identical
    convention every other agent-backed route already reads its engine from."""

    def __init__(
        self, pool: asyncpg.Pool, *, graph_name: str, writer: GraphWriter, provenance_store: ProvenanceStore
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

    @property
    def provenance(self) -> ProvenanceStore:
        return self._provenance


# ------------------------------------------------------------------------- estate-wide pass

_NODE_SERVER_MANAGED = frozenset(p.name for p in BASE_NODE_PROPERTIES if p.server_managed) | {
    "id",
    "side",
}


def _writable_node_properties(properties: Mapping[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in properties.items() if k not in _NODE_SERVER_MANAGED}


@dataclass(frozen=True, slots=True)
class MovedClassification:
    calculated_field_id: str
    name: str
    from_class: str | None
    to_class: str


@dataclass(frozen=True, slots=True)
class ReclassifyResult:
    classifier_version: int
    total: int
    class_mix: dict[str, int]
    moved: tuple[MovedClassification, ...]

    def as_dict(self) -> dict[str, Any]:
        return {
            "classifier_version": self.classifier_version,
            "total": self.total,
            "class_mix": dict(self.class_mix),
            "moved": [
                {
                    "calculated_field_id": m.calculated_field_id,
                    "name": m.name,
                    "from_class": m.from_class,
                    "to_class": m.to_class,
                }
                for m in self.moved
            ],
        }


async def reclassify_estate(
    pool: asyncpg.Pool,
    graph_name: str,
    writer: GraphWriter,
    *,
    provenance_store: ProvenanceStore,
    principal: Principal,
) -> ReclassifyResult:
    """Classify every live `CalculatedField` against the current rule set, write the
    result, and report what moved class (story S5.1.1's own "re-classification... reports
    what moved class"). Story S5.4.1: a C4 verdict also gets Appendix B guidance and a real
    ASSISTED-mode redesign suggestion (`redesign.c4_properties`); a field that moves *away*
    from C4 has those properties dropped, since they describe a decision that is no longer
    relevant, not one that quietly persists as clutter."""
    async with pool.acquire() as conn:
        graph_version = await _current_version(conn, graph_name)
        calc_rows = await conn.fetch(
            f"""SELECT id FROM {NODE_INDEX_TABLE}
             WHERE graph = $1 AND kind = 'node' AND label = 'CalculatedField' AND retired_at IS NULL""",
            graph_name,
        )
        calc_ids = [row["id"] for row in calc_rows]
        calc_properties = await hydrate(conn, graph_name, "CalculatedField", calc_ids)

        worksheet_rows = await conn.fetch(
            f"""SELECT id FROM {NODE_INDEX_TABLE}
             WHERE graph = $1 AND kind = 'node' AND label = 'Worksheet' AND retired_at IS NULL""",
            graph_name,
        )
        worksheet_properties = await hydrate(
            conn, graph_name, "Worksheet", [row["id"] for row in worksheet_rows]
        )
        addressed_worksheet_ids = [
            wid
            for wid, props in worksheet_properties.items()
            if props.get("rows_shelf") or props.get("cols_shelf")
        ]
        resolved_calc_ids: set[str] = set()
        for encoded in (
            await children(conn, graph_name, addressed_worksheet_ids, "ENCODES", "CalculatedField")
        ).values():
            resolved_calc_ids.update(encoded)

        parameter_deps = await children(conn, graph_name, calc_ids, "DEPENDS_ON", "Parameter")

    moved: list[MovedClassification] = []
    class_mix: dict[str, int] = dict.fromkeys(_CLASS_ORDER, 0)
    writes: list[NodeWrite] = []

    for calc_id, properties in calc_properties.items():
        context = ClassificationContext(
            has_parameter_dependency=bool(parameter_deps.get(calc_id)),
            table_calc_addressing_resolved=calc_id in resolved_calc_ids,
        )
        result = classify(properties.get("formula_ast"), context=context)
        class_mix[result.class_] += 1

        previous_class = properties.get("class")
        if previous_class != result.class_:
            moved.append(
                MovedClassification(
                    calculated_field_id=calc_id,
                    name=str(properties.get("name") or calc_id),
                    from_class=previous_class,
                    to_class=result.class_,
                )
            )

        # C4-only properties are dropped here by construction (excluded from the base
        # dict) unless result.class_ == "C4" re-adds them below — the same "omit rather
        # than write a stale value" convention this codebase already follows elsewhere,
        # applied to a whole property group at once rather than a single optional field.
        node_properties: dict[str, Any] = {
            key: value
            for key, value in _writable_node_properties(properties).items()
            if key not in C4_PROPERTIES
        }
        node_properties.update(
            {
                "class": result.class_,
                "pattern_ref": result.rule_id,
                "reason": result.reason,
                "classifier_version": CLASSIFIER_VERSION,
            }
        )
        if result.class_ == "C4":
            node_properties.update(
                await c4_properties(
                    provenance_store,
                    calc_id=calc_id, rule_id=result.rule_id, existing=properties,
                    graph_version=graph_version, principal=principal,
                )
            )

        writes.append(NodeWrite(type="CalculatedField", id=calc_id, properties=node_properties))

    if writes:
        await writer.upsert_nodes(writes, principal=principal)

    return ReclassifyResult(
        classifier_version=CLASSIFIER_VERSION,
        total=len(calc_properties),
        class_mix=class_mix,
        moved=tuple(moved),
    )


#: Appendix A / §14.3's own calibration assumption — S5.1.1's own acceptance criterion
#: names it verbatim: "45 / 30 / 18 / 7".
CALIBRATION_TARGETS: dict[str, int] = {"C1": 45, "C2": 30, "C3": 18, "C4": 7}


async def class_mix(pool: asyncpg.Pool, graph_name: str) -> dict[str, Any]:
    """The estate's current class mix, read live from what the last `reclassify_estate`
    wrote — never recomputed on read, the same "measured, not re-derived" footing every
    other Programme Board figure has."""
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            f"""SELECT id FROM {NODE_INDEX_TABLE}
             WHERE graph = $1 AND kind = 'node' AND label = 'CalculatedField' AND retired_at IS NULL""",
            graph_name,
        )
        properties = await hydrate(conn, graph_name, "CalculatedField", [row["id"] for row in rows])

    counts: dict[str, int] = dict.fromkeys(_CLASS_ORDER, 0)
    unclassified = 0
    versions: set[int] = set()
    for props in properties.values():
        class_ = props.get("class")
        if class_ in counts:
            counts[class_] += 1
        else:
            unclassified += 1
        version = props.get("classifier_version")
        if version is not None:
            versions.add(int(version))

    total = len(properties)
    classified = total - unclassified
    percentages = {
        c: round(counts[c] / classified * 100, 1) if classified else 0.0 for c in _CLASS_ORDER
    }
    return {
        "total": total,
        "unclassified": unclassified,
        "counts": counts,
        "percentages": percentages,
        "targets": CALIBRATION_TARGETS,
        # Every classified field agreed on one version, or none exist yet: the honest
        # single answer. Mixed versions (a reclassify interrupted, or in flight) report
        # None rather than picking one — the console shows "mixed" rather than a guess.
        "classifier_version": versions.pop() if len(versions) == 1 else None,
    }


async def _current_version(conn: asyncpg.Connection, graph_name: str) -> int:
    row = await conn.fetchrow(
        f"SELECT seq FROM {EVENT_TABLE} WHERE graph = $1 ORDER BY seq DESC LIMIT 1", graph_name
    )
    return int(row["seq"]) if row else 0


__all__ = [
    "CALIBRATION_TARGETS",
    "CLASSIFIER_VERSION",
    "ClassificationContext",
    "ClassificationResult",
    "MovedClassification",
    "ReclassifyResult",
    "class_mix",
    "classify",
    "reclassify_estate",
]
