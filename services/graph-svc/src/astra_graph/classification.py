"""§11.1 failure classification -- story S8.1.1, opening F8.1 and E8.

    "As a migration engineer, I want every failing case classified into the §11.1
    taxonomy from its evidence bundle, so that the fix path is chosen from evidence, not
    from guessing.

    Acceptance criteria:
    - Classes: FILTER_CONTEXT, NULL_HANDLING, DATE_GRAIN, AGGREGATION, TYPE_COERCION,
      LOD_SCOPE, TABLE_CALC, SORT_LIMIT, KEY_MISSING, SOURCE_DRIFT, UNKNOWN;
      classification is deterministic from the diff signals
    - Classification precision on the labelled fixture set >= 0.90; the class and the
      signals that produced it are recorded on the ExceptionCase or repair record
    - Cases are grouped by artefact so one measure used by many sheets is repaired once"

§11.1 itself, verbatim, is a table (`Class | Signal in the evidence | Typical cause |
Usual fix path`) -- the eleven rows this module's own `classify_failure` implements, in
the same order:

    FILTER_CONTEXT  same keys, measures off by a consistent factor or a subset of rows
    NULL_HANDLING   cells where one side is null/blank and the other is 0 or a value
    DATE_GRAIN      differences concentrated on date dimensions; row multiplicity differs
    AGGREGATION     totals match, rows do not, or vice versa
    TYPE_COERCION   string/number mismatches or formatting differences
    LOD_SCOPE       differences on rows where the LOD dimensions differ from the sheet grain
    TABLE_CALC      differences following a partition boundary pattern
    SORT_LIMIT      row set differs only by membership under a top-N
    KEY_MISSING     whole keys absent on one side
    SOURCE_DRIFT    expected side changed between runs
    UNKNOWN         no signature matched

**No repair record exists anywhere in this codebase to record the class and signals on
-- confirmed by direct research: E8 is entirely unbuilt before this story, no
`RepairRecord`/`MenderPass`-shaped node exists.** The AC's own "or repair record" is
disjunctive only in principle; `ExceptionCase` (already declared, §4.1.1) is the one
real work-item mechanism, the identical choice `VISUAL_REDESIGN` (S6.2.1) and
`REGRESSION` (S7.7.1) already made for their own class-specific facts. This is also the
**first story to open an ExceptionCase for a plain parity FAIL at all** -- confirmed
directly: `run_parity_for_workbook` (S7.4.1) writes a `Verdict(result="FAIL")` and
nothing reads it afterwards; the only other `ExceptionCase` writers
(`visual_redesign.py`, `regression.py`, `generation.py`) each open one for a different
moment (a pre-proof redesign flag, a later re-proof, a pre-proof generation exhaustion),
never a first-pass parity FAIL.

**Two of the eleven signals are not fully expressible from `DiffResult` alone, so this
module accepts two small, real, optional extra facts rather than leaving those two
classes permanently unreachable.** `LOD_SCOPE`'s own signal ("rows where the LOD
dimensions differ from the sheet grain") and `TABLE_CALC`'s own signal ("a partition
boundary pattern") are both, honestly, facts about the failing measure's own *formula*,
not about the diff's own cell/key evidence -- `DiffResult` has no notion of LOD or
partitioning at all. Rather than leave both classes dead branches (the same disclosed-
unreachable shape `InconclusiveReason.SAMPLING_SHORTFALL` and "the sample could not be
stratified" already have elsewhere in this codebase), the graph-coupled caller
(`classify_run`, below) supplies the failing measure's own real, already-parsed
`CalculatedField.formula_ast` (the identical AST `classify.py`'s own §9.1 C1-C4
classifier already walks) -- `_contains_lod`/`_contains_table_calc` walk it the
identical way `classify.py`'s own `_walk` does (`kind == "AGGREGATE"` and `name in
{"FIXED","INCLUDE","EXCLUDE"}` for LOD; `kind == "FUNCTION"` and `detail["family"]`
starting `"table_calc"` for a table calculation -- the same family tag the Tableau
grammar's own function registry already stamps, reused rather than a second, invented
function-name list). `SOURCE_DRIFT`'s own signal ("expected side changed between
runs") is, just as honestly, not a fact any single diff's own evidence can carry either
-- it is a fact about *time*, comparable only against the real event outbox. The caller
supplies `recent_source_drift: bool`, resolved from the same `SOURCE_DRIFT` event stream
§10.6's own `RegressionScheduler` already polls (`regression.py`), asking only "has one
landed for this workbook since this case was last executed" -- a real, already-existing
signal, not a new detection mechanism.

**No spec-given priority order exists for when several signals could fire at once** (a
failing cell can be simultaneously a date and part of a subset with a consistent
factor), so this module states and follows one, in the order the classes are listed
above: SOURCE_DRIFT first (if the source changed, nothing else in the diff is
trustworthy regardless of what it shows), then key-set signals (unambiguous: a key is
either there or it is not), then the two AST-informed classes (a real fact about the
formula, checked before the more speculative cell-level heuristics), then the
cell-level classes in order of how specific/certain their signal is (a null pairing is
unambiguous; a "consistent factor" is a statistical judgement call, checked last before
giving up to UNKNOWN).

**"Classification precision" is read as plain accuracy over the labelled fixture set**
(correct / total) -- the AC names no per-class F1/precision-recall breakdown, and this
codebase's own nearest precedent (`classify.py`'s §9.1 classifier) has no
precision-bound test to follow either; this is the first classifier in this codebase
with one. `tests/classification_fixtures.py` (mirroring `tests/diff_fixtures.py`'s own
"named generator functions, one per class, hand-verified" convention, S7.4.1) is the
labelled set; `tests/test_classification.py` asserts the >= 0.90 bound over it, and
deliberately includes a few genuinely hard/ambiguous cases so the bound is proven, not
trivially satisfied by an all-easy fixture set.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Iterator
from dataclasses import dataclass, field
from typing import Any

import asyncpg

from .artefacts import ArtefactStore
from .case_derivation import (
    _worksheet_field_index,  # cross-epic private helper; see this module's own docstring
)
from .events import EventType
from .graph.queries import NODE_INDEX_TABLE
from .ids import new_ulid
from .lineage import hydrate
from .ontology.types import BASE_NODE_PROPERTIES
from .principal import Principal
from .verdicts import latest_parity_run
from .writes import GraphWriter, NodeWrite

logger = logging.getLogger(__name__)

#: §11.1's own eleven classes, in this module's own priority order -- see the module
#: docstring for why this order and not another.
FAILURE_CLASSES = (
    "SOURCE_DRIFT",
    "KEY_MISSING",
    "SORT_LIMIT",
    "LOD_SCOPE",
    "TABLE_CALC",
    "NULL_HANDLING",
    "DATE_GRAIN",
    "TYPE_COERCION",
    "AGGREGATION",
    "FILTER_CONTEXT",
    "UNKNOWN",
)

#: classify.py's own `_LOD_NAMES` (§9.1), mirrored rather than imported -- a private
#: constant of a different epic's own module, the same "duplicate, don't reach across"
#: precedent `case_derivation._worksheet_field_index`'s own docstring already states
#: (and which this module's own import of that function is itself the one, already-
#: established exception to -- see this module's own docstring).
_LOD_FUNCTION_NAMES = frozenset({"FIXED", "INCLUDE", "EXCLUDE"})

#: How far apart (as a fraction of the larger magnitude) two failing cells' own relative
#: deltas may be and still count as "a consistent factor" (§11.1's own FILTER_CONTEXT
#: signal) -- invented and disclosed, the same "a real, defensible, disclosed number"
#: footing this codebase's other invented tolerances already have
#: (`TOP_N_ROWS_PER_MEASURE`, `DEFAULT_RETRY_TIMEOUT_MULTIPLIER`, ...).
FILTER_CONTEXT_SPREAD_TOLERANCE = 0.05

EVIDENCE_KIND = "classification_evidence"
EVIDENCE_MEDIA_TYPE = "application/json"

STEWARD_LOOKBACK_EVENTS = 200
"""How many of the outbox's own most-recent SOURCE_DRIFT events `classify_run` scans
for one naming this workbook -- the identical bound `regression.py`'s own drift check
already uses."""


class ClassificationError(Exception):
    """A case could not be classified, or classification could not run for a workbook."""


@dataclass(frozen=True, slots=True)
class Classification:
    """One case's own verdict: which class, why, and the measured facts behind it --
    the `(class, rule_id, reason)` triple `classify.py`'s own `ClassificationResult`
    already established for §9.1, with a fourth field (`signals`) since this AC asks for
    the facts themselves to be recorded, not only the reason sentence."""

    failure_class: str
    rule_id: str
    reason: str
    signals: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {
            "failure_class": self.failure_class, "rule_id": self.rule_id,
            "reason": self.reason, "signals": self.signals,
        }


# --------------------------------------------------------------------------- pure core


def classify_failure(
    diff: dict[str, Any],
    *,
    formula_ast: Any = None,
    recent_source_drift: bool = False,
) -> Classification:
    """§11.1, implemented exactly -- see this module's own docstring for the disclosed
    priority order and the two AST-informed classes. ``diff`` is `DiffResult.as_dict()`'s
    own shape (the identical dict the evidence bundle already stores under its own
    ``"diff"`` key), not the dataclass itself -- keeping this function decoupled from
    `diff.py` and usable directly against a parsed evidence artefact."""
    if diff.get("result") != "FAIL":
        raise ClassificationError(
            f"only a FAIL verdict is classified under §11.1; this diff's own result is "
            f"{diff.get('result')!r}"
        )

    if recent_source_drift:
        return Classification(
            "SOURCE_DRIFT", "source_drift",
            "a SOURCE_DRIFT event was recorded for this workbook since this case was "
            "last executed -- the expected side changed between runs",
            {"recent_source_drift": True},
        )

    missing = list(diff.get("missing_keys") or ())
    extra = list(diff.get("extra_keys") or ())
    failing_cells = list(diff.get("failing_cells") or ())
    totals = list(diff.get("totals") or ())
    row_count_ok = bool(diff.get("row_count_within_tolerance", True))

    if missing or extra:
        if missing and extra and len(missing) == len(extra) and not failing_cells:
            return Classification(
                "SORT_LIMIT", "key_exchange",
                f"{len(missing)} key(s) present on the source only and {len(extra)} on "
                f"the target only, the same count, with no cell mismatches on the shared "
                f"remainder -- consistent with a shifted top-N boundary rather than a "
                f"real absence",
                {"missing_keys": len(missing), "extra_keys": len(extra)},
            )
        return Classification(
            "KEY_MISSING", "keys_absent",
            f"{len(missing)} key(s) present on the source only, {len(extra)} on the "
            f"target only -- a whole key absent on one side",
            {"missing_keys": len(missing), "extra_keys": len(extra)},
        )

    if failing_cells and _contains_lod(formula_ast):
        return Classification(
            "LOD_SCOPE", "lod_ast",
            "the failing measure's own formula contains a level-of-detail expression "
            "(FIXED/INCLUDE/EXCLUDE), whose own scope can differ from the sheet's grain",
            {"lod": True, "failing_cells": len(failing_cells)},
        )

    if failing_cells and _contains_table_calc(formula_ast):
        return Classification(
            "TABLE_CALC", "table_calc_ast",
            "the failing measure's own formula is a table calculation, whose own "
            "addressing/partitioning can differ from how the sheet re-partitions it",
            {"table_calc": True, "failing_cells": len(failing_cells)},
        )

    if failing_cells:
        null_cells = [c for c in failing_cells if _is_null_mismatch(c)]
        if null_cells:
            return Classification(
                "NULL_HANDLING", "null_cell",
                f"{len(null_cells)} of {len(failing_cells)} failing cell(s) have a "
                f"null/blank on one side and a real value on the other",
                {"failing_cells": len(failing_cells), "null_cells": len(null_cells)},
            )

        date_cells = [c for c in failing_cells if c.get("kind") == "date"]
        if date_cells:
            return Classification(
                "DATE_GRAIN", "date_cell",
                f"{len(date_cells)} of {len(failing_cells)} failing cell(s) are a "
                f"date/datetime comparison",
                {"failing_cells": len(failing_cells), "date_cells": len(date_cells)},
            )

        coerced_cells = [c for c in failing_cells if _looks_type_coerced(c)]
        if coerced_cells:
            return Classification(
                "TYPE_COERCION", "string_number_mismatch",
                f"{len(coerced_cells)} of {len(failing_cells)} failing string cell(s) "
                f"parse as equal numbers once formatting (commas, currency symbols, "
                f"whitespace) is stripped",
                {"failing_cells": len(failing_cells), "coerced_cells": len(coerced_cells)},
            )

        totals_fail = any(t.get("result") == "FAIL" for t in totals)
        if totals_fail != (not row_count_ok):
            return Classification(
                "AGGREGATION", "totals_vs_rows",
                "totals mismatch and row-count mismatch disagree -- "
                + ("totals fail while row counts are within tolerance"
                   if totals_fail else "row counts differ while every total is within tolerance"),
                {
                    "totals_fail": totals_fail, "row_count_within_tolerance": row_count_ok,
                    "expected_row_count": diff.get("expected_row_count"),
                    "candidate_row_count": diff.get("candidate_row_count"),
                },
            )

        spread = _relative_delta_spread(failing_cells)
        if spread is not None and spread <= FILTER_CONTEXT_SPREAD_TOLERANCE:
            return Classification(
                "FILTER_CONTEXT", "consistent_factor",
                f"every numeric failing cell's own relative delta stays within "
                f"{FILTER_CONTEXT_SPREAD_TOLERANCE:.0%} of the others -- a consistent "
                f"factor across the failing set, not a scattered set of unrelated cells",
                {"failing_cells": len(failing_cells), "relative_delta_spread": spread},
            )

        return Classification(
            "UNKNOWN", "no_signature",
            "no §11.1 signature matched this failing case's own evidence",
            {"failing_cells": len(failing_cells)},
        )

    return Classification(
        "UNKNOWN", "no_signal",
        "no failing cells and no key mismatch to classify from -- a FAIL with no "
        "localised evidence (a pure row-count mismatch, with every cell and key "
        "otherwise agreeing)",
        {},
    )


def _is_null_mismatch(cell: dict[str, Any]) -> bool:
    expected, candidate = cell.get("expected"), cell.get("candidate")
    return (expected is None) != (candidate is None)


def _looks_type_coerced(cell: dict[str, Any]) -> bool:
    if cell.get("kind") != "string":
        return False
    expected, candidate = _strip_formatting(cell.get("expected")), _strip_formatting(cell.get("candidate"))
    if expected is None or candidate is None:
        return False
    return abs(expected - candidate) <= 1e-9 * max(abs(expected), abs(candidate), 1.0)


def _strip_formatting(value: Any) -> float | None:
    if value is None:
        return None
    text = str(value).strip()
    for junk in ("$", "£", "€", ",", "%"):
        text = text.replace(junk, "")
    try:
        return float(text)
    except ValueError:
        return None


def _relative_delta_spread(failing_cells: list[dict[str, Any]]) -> float | None:
    ratios: list[float] = []
    for cell in failing_cells:
        if cell.get("kind") != "numeric":
            continue
        expected, candidate, delta = cell.get("expected"), cell.get("candidate"), cell.get("delta")
        if expected is None or candidate is None or delta is None:
            continue
        denominator = max(abs(float(expected)), abs(float(candidate)), 1e-9)
        ratios.append(abs(float(delta)) / denominator)
    if not ratios:
        return None
    return max(ratios) - min(ratios)


def _walk_ast(node: Any) -> Iterator[dict[str, Any]]:
    if not isinstance(node, dict):
        return
    yield node
    for child in node.get("children") or ():
        yield from _walk_ast(child)


def _contains_lod(formula_ast: Any) -> bool:
    return any(
        node.get("kind") == "AGGREGATE" and node.get("name") in _LOD_FUNCTION_NAMES
        for node in _walk_ast(formula_ast)
    )


def _contains_table_calc(formula_ast: Any) -> bool:
    return any(
        node.get("kind") == "FUNCTION"
        and str((node.get("detail") or {}).get("family", "")).startswith("table_calc")
        for node in _walk_ast(formula_ast)
    )


# ------------------------------------------------------------------------ graph-coupled


def _writable_node_properties(properties: dict[str, Any]) -> dict[str, Any]:
    """The identical 'strip server-managed base properties before resubmitting a
    hydrated node' discipline every module that upserts one already follows
    (`model_lifecycle._writable_node_properties`, duplicated per that module's own
    precedent rather than imported)."""
    managed = frozenset(p.name for p in BASE_NODE_PROPERTIES if p.server_managed) | {"id", "side"}
    return {k: v for k, v in properties.items() if k not in managed}


async def _recent_source_drift_workbooks(pool: asyncpg.Pool, graph_name: str) -> frozenset[str]:
    """Every workbook a real `SOURCE_DRIFT` event has named recently -- the identical
    outbox read `regression.py`'s own `RegressionScheduler._check_drift` already
    performs, reused for the same real fact rather than a second detection mechanism."""
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            f"""SELECT subject FROM public.estate_event
                 WHERE graph = $1 AND type = $2 ORDER BY seq DESC LIMIT {STEWARD_LOOKBACK_EVENTS}""",
            graph_name, EventType.SOURCE_DRIFT.value,
        )
    return frozenset(str(row["subject"]) for row in rows)


async def _find_open_case(
    conn: asyncpg.Connection, graph_name: str, *, mu_ref: str, artefact_ref: str | None, failure_class: str,
) -> tuple[str, dict[str, Any]] | None:
    rows = await conn.fetch(
        f"""SELECT id FROM {NODE_INDEX_TABLE}
             WHERE graph = $1 AND kind = 'node' AND label = 'ExceptionCase' AND retired_at IS NULL""",
        graph_name,
    )
    cases = await hydrate(conn, graph_name, "ExceptionCase", [row["id"] for row in rows])
    for case_id, properties in cases.items():
        if (
            properties.get("mu_ref") == mu_ref
            and properties.get("artefact_ref") == artefact_ref
            and properties.get("class") == failure_class
            and properties.get("state") == "OPEN"
        ):
            return case_id, properties
    return None


async def classify_run(
    pool: asyncpg.Pool,
    graph_name: str,
    writer: GraphWriter,
    artefact_store: ArtefactStore,
    *,
    workbook_id: str,
    principal: Principal,
) -> dict[str, Any]:
    """Classify every FAIL verdict on a workbook's own most recent `ParityRun`, group
    the results by artefact, and open (or extend) one `ExceptionCase` per (artefact,
    class) pair -- see this module's own docstring for why grouping needs a real
    artefact reference `mu_ref`/`evidence_ref` alone cannot give it, and why merging
    into an already-OPEN case (rather than opening a second one) is what 'repaired
    once' requires across more than one classification pass."""
    run = await latest_parity_run(pool, graph_name, workbook_id=workbook_id)
    if run is None:
        raise ClassificationError(
            f"workbook '{workbook_id}' has no ParityRun yet -- run POST .../:run-parity first"
        )
    fails = [v for v in run["verdicts"] if v.get("result") == "FAIL"]
    if not fails:
        return {"workbook_id": workbook_id, "run_id": run["run_id"], "classified": 0, "exceptions": []}

    drifted_workbooks = await _recent_source_drift_workbooks(pool, graph_name)
    recent_source_drift = workbook_id in drifted_workbooks

    async with pool.acquire() as conn:
        case_rows = await conn.fetch(
            f"""SELECT id FROM {NODE_INDEX_TABLE}
                 WHERE graph = $1 AND kind = 'node' AND label = 'ParityCase' AND retired_at IS NULL""",
            graph_name,
        )
        all_cases = await hydrate(conn, graph_name, "ParityCase", [row["id"] for row in case_rows])

    field_index_by_sheet: dict[str, dict[str, tuple[str, str, dict[str, Any]]]] = {}

    groups: dict[tuple[str | None, str], dict[str, Any]] = {}
    for verdict in fails:
        case_id = str(verdict["case_ref"])
        case_properties = all_cases.get(case_id)
        if case_properties is None:
            continue
        evidence_ref = verdict.get("evidence_ref")
        content = await artefact_store.content(str(evidence_ref)) if evidence_ref else None
        if content is None:
            logger.warning("verdict for case %s has no readable evidence bundle; skipping", case_id)
            continue
        bundle = json.loads(content)
        sheet_ref = str(case_properties.get("sheet_ref"))

        if sheet_ref not in field_index_by_sheet:
            async with pool.acquire() as conn:
                field_index_by_sheet[sheet_ref] = await _worksheet_field_index(conn, graph_name, sheet_ref)
        field_index = field_index_by_sheet[sheet_ref]

        failing_cells = bundle.get("diff", {}).get("failing_cells") or ()
        measure_name = str(failing_cells[0]["measure"]) if failing_cells else None
        resolved = field_index.get(measure_name) if measure_name else None
        artefact_ref = resolved[1] if resolved else None
        formula_ast = resolved[2].get("formula_ast") if resolved and resolved[0] == "CalculatedField" else None

        classification = classify_failure(
            bundle["diff"], formula_ast=formula_ast, recent_source_drift=recent_source_drift,
        )

        key = (artefact_ref if artefact_ref else f"sheet:{sheet_ref}", classification.failure_class)
        group = groups.setdefault(key, {"artefact_ref": artefact_ref, "case_ids": [], "classification": classification})
        group["case_ids"].append(case_id)

    opened: list[dict[str, Any]] = []
    for (_group_key, failure_class), group in groups.items():
        artefact_ref = group["artefact_ref"]
        group_classification: Classification = group["classification"]
        case_ids: list[str] = group["case_ids"]

        evidence_artefact = await artefact_store.store(
            kind=EVIDENCE_KIND, mu_ref=workbook_id, case_id=case_ids[0], created_by=principal.value,
            content=json.dumps({
                "workbook_id": workbook_id, "run_id": run["run_id"], "case_ids": case_ids,
                "classification": group_classification.as_dict(),
            }).encode("utf-8"),
            media_type=EVIDENCE_MEDIA_TYPE,
        )

        async with pool.acquire() as conn:
            existing = await _find_open_case(
                conn, graph_name, mu_ref=workbook_id, artefact_ref=artefact_ref, failure_class=failure_class,
            )

        if existing is not None:
            existing_id, existing_properties = existing
            merged_case_ids = sorted(set(existing_properties.get("case_refs") or ()) | set(case_ids))
            await writer.upsert_nodes(
                [NodeWrite(type="ExceptionCase", id=existing_id, properties={
                    **_writable_node_properties(existing_properties),
                    "case_refs": merged_case_ids, "evidence_ref": evidence_artefact.id,
                    "classification_signals": group_classification.as_dict(),
                })],
                principal=principal,
            )
            opened.append({"id": existing_id, "class": failure_class, "artefact_ref": artefact_ref, "case_refs": merged_case_ids, "merged": True})
            continue

        case_id_new = new_ulid()
        await writer.write_nodes(
            [NodeWrite(type="ExceptionCase", id=case_id_new, properties={
                "mu_ref": workbook_id, "class": failure_class, "state": "OPEN",
                "evidence_ref": evidence_artefact.id, "artefact_ref": artefact_ref,
                "case_refs": case_ids, "classification_signals": classification.as_dict(),
            })],
            principal=principal,
        )
        opened.append({"id": case_id_new, "class": failure_class, "artefact_ref": artefact_ref, "case_refs": case_ids, "merged": False})

    return {
        "workbook_id": workbook_id, "run_id": run["run_id"], "classified": len(fails), "exceptions": opened,
    }


class ClassificationService:
    """Binds `classify_run` to one pool/graph/writer/artefact store -- the identical
    "pre-bound object on app.state" shape `VerdictsService`/`RegressionService` already
    take."""

    def __init__(
        self, pool: asyncpg.Pool, *, graph_name: str, writer: GraphWriter, artefact_store: ArtefactStore,
    ) -> None:
        self._pool = pool
        self._graph = graph_name
        self._writer = writer
        self._artefact_store = artefact_store

    async def classify(self, workbook_id: str, *, principal: Principal) -> dict[str, Any]:
        return await classify_run(
            self._pool, self._graph, self._writer, self._artefact_store,
            workbook_id=workbook_id, principal=principal,
        )


__all__ = [
    "EVIDENCE_KIND",
    "EVIDENCE_MEDIA_TYPE",
    "FAILURE_CLASSES",
    "FILTER_CONTEXT_SPREAD_TOLERANCE",
    "Classification",
    "ClassificationError",
    "ClassificationService",
    "classify_failure",
    "classify_run",
]
