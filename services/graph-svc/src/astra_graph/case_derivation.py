"""Parity case derivation -- story S7.2.1, continuing E7/F7.2, spec §10.1.

    "As a parity engineer, I want parity cases derived deterministically from each
    sheet, so that coverage is explicit and reproducible.

    Acceptance criteria:
    - Cases = sheet x (parameter combinations from charter enumeration strategy) x
      (filter contexts: default, and each categorical filter's top-N values); each case
      has grain, measures, filters, parameter values and a stable id
    - Charter bounds cap enumeration; combinations above the bound are recorded
      NOT_ENUMERATED on the suite
    - Case count and coverage are shown on the MU page; a ParityCase is a graph node
      with a §10 schema"

§10.1 itself, verbatim: *"Cases are derived from the source, not the target, so that a
target that silently drops a dimension or a filter fails rather than passing trivially."*
Every field this module reads comes from the source side (`Worksheet`, `Filter`,
`Parameter`, `CalculatedField`) -- nothing here reads a composed `ReportDefinition`/
`Visual` (E6's own target-side product), on purpose.

**This module does not build the diff engine or case execution.** §10.2-§10.6 (dual
execution through the adapters, normalisation, the row/key diff, sampling, visual
parity, regression scheduling) are F7.3's own later, explicit scope. What this story
owns: turning each `Worksheet` into the cases §10.1 describes, capped by the Tolerance
Charter's own `params.enumerate_max_values`, with the excess recorded so coverage stays
explicit -- nothing about executing a case or comparing a result belongs here.

**Grain and measures are resolved by name against the worksheet's own datasources, not
`ENCODES` edges.** §4.1.2 declares `ENCODES` (`Worksheet -> Field/CalculatedField`, with
a required `shelf` property whose own note says *"parity case grain is derived from
shelf placement (spec §10.1)"* -- naming this exact story). Confirmed by direct research:
the real Tableau adapter has never written this edge (only the fixture adapter does, for
its own synthetic estate) -- the identical gap `compositor.py`'s own docstring already
disclosed for the Compositor's field wells (S6.1.1). The same fix applies here: shelf
names are resolved against the worksheet's own `USES_DATASOURCE -> HAS_FIELD` reach, so
a real Tableau harvest (with no `ENCODES` edge at all) still derives real cases.

**"Pages" has no harvested source.** §10.1's own grain definition names "rows, columns,
marks ... and pages" -- `Worksheet` (§4.1.1) declares `rows_shelf`/`cols_shelf`/
`marks_shelf` only, no `pages_shelf`. A real, disclosed gap, not silently dropped: grain
is derived from exactly the three shelves this platform has ever harvested.

**Filters are read from the real `Filter` nodes via `FILTERED_BY`, not
`Worksheet.filters` JSON.** `sheets.py`'s own comment on writing both: *"the JSON is
what a screen renders without a traversal, the nodes are what the Proof Engine
walks."* This module is that walk.

**"Filter contexts: default, and each categorical filter's top-N values" goes beyond
§10.1 itself -- a disclosed backlog elaboration, not a spec contradiction.** §10.1's own
text describes exactly one filter context per case (the sheet's harvested filters
"resolved to concrete values from the source's current state"); it names no
multiplication over filter values at all -- the spec's own worked example multiplies
only over parameter combinations. The backlog's own AC asks for more: one additional
case per categorical filter's own top member values, alongside the sheet's default,
harvested filter context. Implemented as an additive union (one extra context per
qualifying member value, not a cross-product across filters), since verifying that each
filter's own most significant values behave correctly is what the AC's own wording
describes, not exhaustive combination of every filter against every other. Capped by
`MAX_FILTER_VALUES_PER_FILTER` -- a bound this module had to invent, since neither §10.1
nor the Tolerance Charter (S7.1.1) declares one for this axis (`ParamRule.
enumerate_max_values` bounds parameter combinations only).

**The charter's own enumeration bound applies to the sheet's total case count, not the
parameter axis alone.** §10.1's own worked example computes the 4x3x2=24 combination
count from parameters alone, since filter-context multiplication isn't part of the
spec's own example. Since the AC's own bullet places "combinations above the bound" right
after describing *both* axes together, this module applies `params.enumerate_max_values`
to the sheet's full candidate count (filter contexts x parameter combinations),
prioritising the default filter context with the default-then-most-observed parameter
combination first, then further filter-context variants -- so the cases that exist are
always the ones most likely to matter, and the excess is recorded as `NOT_ENUMERATED`
exactly where the spec says it must be, "on the suite," so coverage stays explicit.

**"The suite" is a relational record, not a graph node -- the spec's own words, not a
guess.** §14's own storage table gives `parity_suite` a *relational* shape
(`mu_id, sheet_refs`), under a header stating relational tables hold "platform records
that are not graph-shaped" -- unlike `ParityCase`/`ParityRun`/`Verdict`, which that same
table's own §4.1.1/§10 column marks as real graph nodes. `public.parity_suite`
(migration v0027) mirrors that reading directly: one current-coverage row per MU,
recomputed on every derivation, the identical "a platform record, not an estate fact"
footing `conformance_ruleset`/`tolerance_charter_version` already established for a
different kind of non-graph-shaped record.

**A case's own `id` stays a server-issued ULID; `case_key` carries the AC's own "stable
id."** See `ParityCase.case_key`'s own `SpecDeviation` for why the base `id` property
(a validated ULID) cannot itself be a content hash. `case_key` is a sha256 digest of
`(sheet_ref, grain, measures, filter_ctx, param_values)`, computed the same
`context.canonical` way every other content-derived key in this codebase already is.
Re-deriving the same sheet against unchanged source data produces the same `case_key`
for the same conceptual case, so an already-live `ParityCase` is left alone rather than
duplicated; a case whose `case_key` no longer appears in a fresh derivation (the source
changed under it) is retired -- the identical "recompose retires what no longer applies"
discipline `compositor._retire_previous_report` already established, applied to a
content-derived identity instead of a whole-workbook replace.

**"Shown on the MU page" is the same disclosed proxy every E6/E7 ADR has already used.**
No MU page exists (F10.3, unbuilt). `mu_ref` alone -- not `ReportDefinition` -- is this
story's own real anchor: case derivation reads only the source side, and a workbook can
have live `ParityCase`s long before any report is ever composed. Coverage is real and
queryable by `mu_ref` today, until a real MU page exists to render it.
"""

from __future__ import annotations

import itertools
import json
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Protocol

import asyncpg

from .context.canonical import canonical_json, context_hash
from .graph.queries import NODE_INDEX_TABLE
from .ids import new_ulid
from .lineage import children, hydrate
from .principal import Principal
from .tolerance_charter import ToleranceCharter
from .writes import GraphWriter, NodeWrite

SUITE_TABLE = "public.parity_suite"

#: Invented by this module -- neither §10.1 nor the Tolerance Charter bounds how many of
#: a categorical filter's own member values become their own filter-context case. A
#: small, disclosed constant rather than an unbounded multiplication.
MAX_FILTER_VALUES_PER_FILTER = 5


class CaseDerivationError(Exception):
    """Cases could not be derived for this workbook."""


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")


# ------------------------------------------------------------------------- pure functions


@dataclass(frozen=True, slots=True)
class DerivedCase:
    sheet_ref: str
    grain: tuple[str, ...]
    measures: tuple[str, ...]
    filter_ctx: dict[str, Any]
    param_values: dict[str, Any]
    case_key: str

    def as_properties(self, *, mu_ref: str) -> dict[str, Any]:
        return {
            "mu_ref": mu_ref,
            "sheet_ref": self.sheet_ref,
            "grain": list(self.grain),
            "measures": list(self.measures),
            "filter_ctx": self.filter_ctx,
            "param_values": self.param_values,
            "state": "DERIVED",
            "case_key": self.case_key,
        }


@dataclass(frozen=True, slots=True)
class SheetDerivation:
    """Every case a single worksheet produces, plus the AC's own coverage bookkeeping."""

    sheet_ref: str
    cases: tuple[DerivedCase, ...]
    total_candidates: int
    not_enumerated: tuple[dict[str, Any], ...]


def compute_case_key(
    *, sheet_ref: str, grain: tuple[str, ...], measures: tuple[str, ...],
    filter_ctx: dict[str, Any], param_values: dict[str, Any],
) -> str:
    """A sha256 digest of everything that makes this case this case -- stable across a
    re-derivation that finds the same conceptual case again."""
    payload = {
        "sheet_ref": sheet_ref, "grain": sorted(grain), "measures": sorted(measures),
        "filter_ctx": filter_ctx, "param_values": param_values,
    }
    return context_hash(canonical_json(payload))


def _default_filter_context(filters: list[dict[str, Any]]) -> dict[str, Any]:
    """§10.1's own single filter context: the sheet's own harvested filters, as they
    stand, resolved to concrete values -- exactly what the source already says."""
    return {"kind": "default", "filters": filters}


def _filter_value_variants(filters: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """One additional filter context per qualifying categorical filter's own top member
    values (the AC's own elaboration beyond §10.1 -- see the module docstring)."""
    variants: list[dict[str, Any]] = []
    for filter_properties in filters:
        if filter_properties.get("type") != "categorical":
            continue
        values = filter_properties.get("values") or {}
        members = values.get("members") or []
        field_ref = filter_properties.get("field_ref")
        if not field_ref or not members:
            continue
        for member in members[:MAX_FILTER_VALUES_PER_FILTER]:
            variants.append(
                {
                    "kind": "categorical_value",
                    "field_ref": field_ref,
                    "value": member,
                    "filters": filters,
                }
            )
    return variants


def derive_filter_contexts(filters: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [_default_filter_context(filters), *_filter_value_variants(filters)]


def _parameter_candidate_values(parameter: dict[str, Any]) -> list[Any]:
    """Default first, then observed values, deduplicated -- `ParamRule.
    enumerate_strategy`'s own default, 'DEFAULT_PLUS_OBSERVED'."""
    candidates: list[Any] = []
    default = parameter.get("default")
    if default is not None:
        candidates.append(default)
    for value in parameter.get("current_values_seen") or ():
        if value not in candidates:
            candidates.append(value)
    return candidates or [None]


def derive_parameter_combinations(parameters: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Every combination of every parameter's own candidate values -- §10.1's own worked
    example ('three parameters of domain sizes 4, 3 and 2 produce up to 24 cases'), with
    no bound applied yet (the caller enforces `enumerate_max_values`)."""
    if not parameters:
        return [{}]
    names = [str(p.get("name")) for p in parameters]
    value_lists = [_parameter_candidate_values(p) for p in parameters]
    combinations = []
    for combo in itertools.product(*value_lists):
        combinations.append(dict(zip(names, combo, strict=True)))
    return combinations


def derive_sheet_cases(
    *,
    sheet_ref: str,
    grain: tuple[str, ...],
    measures: tuple[str, ...],
    filters: list[dict[str, Any]],
    parameters: list[dict[str, Any]],
    charter: ToleranceCharter,
) -> SheetDerivation:
    """Pure: §10.1's own cross product of filter contexts and parameter combinations,
    capped by the charter's own `enumerate_max_values`, prioritising the default filter
    context with the default parameter combination first."""
    filter_contexts = derive_filter_contexts(filters)
    param_combinations = derive_parameter_combinations(parameters)

    candidates: list[tuple[dict[str, Any], dict[str, Any]]] = []
    for filter_ctx in filter_contexts:
        for param_values in param_combinations:
            candidates.append((filter_ctx, param_values))
    total_candidates = len(candidates)

    bound = max(1, charter.params.enumerate_max_values)
    chosen = candidates[:bound]
    excess = candidates[bound:]

    cases = tuple(
        DerivedCase(
            sheet_ref=sheet_ref, grain=grain, measures=measures,
            filter_ctx=filter_ctx, param_values=param_values,
            case_key=compute_case_key(
                sheet_ref=sheet_ref, grain=grain, measures=measures,
                filter_ctx=filter_ctx, param_values=param_values,
            ),
        )
        for filter_ctx, param_values in chosen
    )
    not_enumerated = tuple(
        {"filter_ctx": filter_ctx, "param_values": param_values} for filter_ctx, param_values in excess
    )
    return SheetDerivation(
        sheet_ref=sheet_ref, cases=cases, total_candidates=total_candidates,
        not_enumerated=not_enumerated,
    )


# ------------------------------------------------------------------------------ graph reads


async def _worksheet_field_index(
    conn: asyncpg.Connection, graph: str, worksheet_id: str
) -> dict[str, tuple[str, str, dict[str, Any]]]:
    """Field/CalculatedField name -> (kind, id, properties), resolved against the
    worksheet's own datasources -- the identical resolution `compositor.
    _worksheet_field_index` (S6.1.1) already established, duplicated rather than
    imported: this is a private helper of a different epic's own module, and E7 has no
    other reason to depend on the Compositor."""
    datasource_map = await children(conn, graph, [worksheet_id], "USES_DATASOURCE", "Datasource")
    datasource_ids = sorted(datasource_map.get(worksheet_id, set()))
    if not datasource_ids:
        return {}
    field_map = await children(conn, graph, datasource_ids, "HAS_FIELD", "Field")
    calc_map = await children(conn, graph, datasource_ids, "HAS_FIELD", "CalculatedField")
    field_ids = sorted({f for ids in field_map.values() for f in ids})
    calc_ids = sorted({c for ids in calc_map.values() for c in ids})
    fields = await hydrate(conn, graph, "Field", field_ids)
    calcs = await hydrate(conn, graph, "CalculatedField", calc_ids)

    index: dict[str, tuple[str, str, dict[str, Any]]] = {}
    for calc_id, properties in calcs.items():
        name = str(properties.get("name") or "")
        if name:
            index[name] = ("CalculatedField", calc_id, properties)
    for field_id, properties in fields.items():
        name = str(properties.get("name") or "")
        if name:
            index[name] = ("Field", field_id, properties)
    return index


def _is_measure(kind: str, properties: dict[str, Any]) -> bool:
    """A calculated field is always a measure; a plain field is one only by its own
    declared role -- the identical rule `compositor._resolve_one_well` already uses."""
    return kind == "CalculatedField" or properties.get("role") == "measure"


def _resolve_grain_and_measures(
    index: dict[str, tuple[str, str, dict[str, Any]]], worksheet_properties: dict[str, Any]
) -> tuple[tuple[str, ...], tuple[str, ...], list[str]]:
    """§10.1's own grain ('dimension fields on rows, columns, marks... after resolving
    the sheet's level-of-detail rules') and measures ('every measure field on the
    shelves... plus every calculated field the sheet encodes') -- resolved from exactly
    the three shelves this platform has ever harvested (no `pages_shelf` exists)."""
    dimensions: list[str] = []
    measures: list[str] = []
    calc_ids: list[str] = []

    def visit(name: str) -> None:
        found = index.get(name)
        if found is None:
            return
        kind, node_id, properties = found
        if kind == "CalculatedField":
            calc_ids.append(node_id)
        if _is_measure(kind, properties):
            if name not in measures:
                measures.append(name)
        elif name not in dimensions:
            dimensions.append(name)

    for name in worksheet_properties.get("rows_shelf") or ():
        if name:
            visit(str(name))
    for name in worksheet_properties.get("cols_shelf") or ():
        if name:
            visit(str(name))
    for entry in worksheet_properties.get("marks_shelf") or ():
        _, _, name = str(entry).partition(":")
        if name:
            visit(name)

    return tuple(dimensions), tuple(measures), calc_ids


async def _worksheet_filters(conn: asyncpg.Connection, graph: str, worksheet_id: str) -> list[dict[str, Any]]:
    filter_map = await children(conn, graph, [worksheet_id], "FILTERED_BY", "Filter")
    filter_ids = sorted(filter_map.get(worksheet_id, set()))
    if not filter_ids:
        return []
    filters = await hydrate(conn, graph, "Filter", filter_ids)
    return list(filters.values())


async def _worksheet_parameters_raw(
    conn: asyncpg.Connection, graph: str, calc_ids: list[str]
) -> list[dict[str, Any]]:
    """Every `Parameter` a calculated field on this sheet's own shelves depends on --
    the same `DEPENDS_ON` traversal `compositor._worksheet_parameters` (S6.1.3) already
    established, and the same disclosed limit: a parameter reached only through a filter
    or a native action, never a calculation, is unreachable this way."""
    if not calc_ids:
        return []
    parameter_map = await children(conn, graph, sorted(set(calc_ids)), "DEPENDS_ON", "Parameter")
    parameter_ids = sorted({p for owned in parameter_map.values() for p in owned})
    if not parameter_ids:
        return []
    parameters = await hydrate(conn, graph, "Parameter", parameter_ids)
    return list(parameters.values())


async def _derive_for_worksheet(
    conn: asyncpg.Connection, graph: str, worksheet_id: str, worksheet_properties: dict[str, Any],
    charter: ToleranceCharter,
) -> SheetDerivation:
    field_index = await _worksheet_field_index(conn, graph, worksheet_id)
    grain, measures, calc_ids = _resolve_grain_and_measures(field_index, worksheet_properties)
    if not grain or not measures:
        raise CaseDerivationError(
            f"worksheet '{worksheet_id}' has no resolvable grain and measures -- a case "
            f"without both is not executable (§10.1)"
        )
    filters = await _worksheet_filters(conn, graph, worksheet_id)
    parameters = await _worksheet_parameters_raw(conn, graph, calc_ids)
    return derive_sheet_cases(
        sheet_ref=worksheet_id, grain=grain, measures=measures,
        filters=filters, parameters=parameters, charter=charter,
    )


# --------------------------------------------------------------------------- suite store


@dataclass(frozen=True, slots=True)
class ParitySuite:
    mu_ref: str
    sheet_refs: tuple[str, ...]
    charter_version: str
    total_combinations: int
    enumerated_count: int
    not_enumerated: tuple[dict[str, Any], ...]
    derived_by: str
    derived_at: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "mu_ref": self.mu_ref,
            "sheet_refs": list(self.sheet_refs),
            "charter_version": self.charter_version,
            "total_combinations": self.total_combinations,
            "enumerated_count": self.enumerated_count,
            "not_enumerated_count": len(self.not_enumerated),
            "not_enumerated": list(self.not_enumerated),
            "derived_by": self.derived_by,
            "derived_at": self.derived_at,
        }


class ParitySuiteStore(Protocol):
    async def save(self, suite: ParitySuite) -> ParitySuite: ...

    async def get(self, mu_ref: str) -> ParitySuite | None: ...


class PostgresParitySuiteStore:
    def __init__(self, pool: asyncpg.Pool, *, graph_name: str) -> None:
        self._pool = pool
        self._graph = graph_name

    async def save(self, suite: ParitySuite) -> ParitySuite:
        async with self._pool.acquire() as conn:
            await conn.execute(
                f"""
                INSERT INTO {SUITE_TABLE}
                    (id, graph, mu_ref, sheet_refs, charter_version, total_combinations,
                     enumerated_count, not_enumerated, derived_by, derived_at)
                VALUES ($1, $2, $3, $4::jsonb, $5, $6, $7, $8::jsonb, $9, $10)
                ON CONFLICT (graph, mu_ref) DO UPDATE SET
                    sheet_refs = EXCLUDED.sheet_refs,
                    charter_version = EXCLUDED.charter_version,
                    total_combinations = EXCLUDED.total_combinations,
                    enumerated_count = EXCLUDED.enumerated_count,
                    not_enumerated = EXCLUDED.not_enumerated,
                    derived_by = EXCLUDED.derived_by,
                    derived_at = EXCLUDED.derived_at
                """,
                f"suite_{new_ulid()}", self._graph, suite.mu_ref,
                json.dumps(list(suite.sheet_refs)), suite.charter_version,
                suite.total_combinations, suite.enumerated_count,
                json.dumps(list(suite.not_enumerated)), suite.derived_by,
                datetime.fromisoformat(suite.derived_at.replace("Z", "+00:00")),
            )
        return suite

    async def get(self, mu_ref: str) -> ParitySuite | None:
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                f"SELECT * FROM {SUITE_TABLE} WHERE graph = $1 AND mu_ref = $2", self._graph, mu_ref,
            )
        if row is None:
            return None
        sheet_refs = row["sheet_refs"]
        not_enumerated = row["not_enumerated"]
        return ParitySuite(
            mu_ref=row["mu_ref"],
            sheet_refs=tuple(json.loads(sheet_refs) if isinstance(sheet_refs, str) else sheet_refs),
            charter_version=row["charter_version"],
            total_combinations=row["total_combinations"],
            enumerated_count=row["enumerated_count"],
            not_enumerated=tuple(
                json.loads(not_enumerated) if isinstance(not_enumerated, str) else not_enumerated
            ),
            derived_by=row["derived_by"],
            derived_at=row["derived_at"].isoformat() if hasattr(row["derived_at"], "isoformat") else row["derived_at"],
        )


# --------------------------------------------------------------------------- orchestration


async def derive_cases_for_workbook(
    pool: asyncpg.Pool,
    graph_name: str,
    writer: GraphWriter,
    suite_store: ParitySuiteStore,
    *,
    workbook_id: str,
    charter_version: str,
    charter: ToleranceCharter,
    principal: Principal,
) -> dict[str, Any]:
    """Derive cases for every worksheet in a workbook, write only the ones not already
    live (matched by `case_key`), retire any live case whose `case_key` no longer
    appears (the source changed under it), and record the suite's own coverage."""
    async with pool.acquire() as conn:
        worksheet_map = await children(conn, graph_name, [workbook_id], "CONTAINS", "Worksheet")
        worksheet_ids = sorted(worksheet_map.get(workbook_id, set()))
        if not worksheet_ids:
            raise CaseDerivationError(f"workbook '{workbook_id}' has no worksheets to derive cases from")
        worksheets = await hydrate(conn, graph_name, "Worksheet", worksheet_ids)

        existing_rows = await conn.fetch(
            f"""SELECT id FROM {NODE_INDEX_TABLE}
             WHERE graph = $1 AND kind = 'node' AND label = 'ParityCase' AND retired_at IS NULL""",
            graph_name,
        )
        existing_ids = [row["id"] for row in existing_rows]
        existing_cases = await hydrate(conn, graph_name, "ParityCase", existing_ids)

    live_for_mu = {
        cid: props for cid, props in existing_cases.items() if props.get("mu_ref") == workbook_id
    }
    live_by_key = {props.get("case_key"): cid for cid, props in live_for_mu.items() if props.get("case_key")}

    all_derived_keys: set[str] = set()
    node_writes: list[NodeWrite] = []
    total_combinations = 0
    enumerated_count = 0
    not_enumerated: list[dict[str, Any]] = []

    async with pool.acquire() as conn:
        for worksheet_id in worksheet_ids:
            derivation = await _derive_for_worksheet(
                conn, graph_name, worksheet_id, worksheets[worksheet_id], charter
            )
            total_combinations += derivation.total_candidates
            enumerated_count += len(derivation.cases)
            not_enumerated.extend(
                {"sheet_ref": worksheet_id, **entry} for entry in derivation.not_enumerated
            )
            for case in derivation.cases:
                all_derived_keys.add(case.case_key)
                if case.case_key in live_by_key:
                    continue
                node_writes.append(
                    NodeWrite(type="ParityCase", properties=case.as_properties(mu_ref=workbook_id))
                )

    if node_writes:
        await writer.write_nodes(node_writes, principal=principal)

    stale_ids = [cid for key, cid in live_by_key.items() if key not in all_derived_keys]
    for case_id in stale_ids:
        await writer.retire_node(
            case_id, reason="superseded by a fresh case derivation for this workbook", principal=principal,
        )

    suite = ParitySuite(
        mu_ref=workbook_id, sheet_refs=tuple(worksheet_ids), charter_version=charter_version,
        total_combinations=total_combinations, enumerated_count=enumerated_count,
        not_enumerated=tuple(not_enumerated), derived_by=principal.value, derived_at=_now(),
    )
    await suite_store.save(suite)

    return {
        "workbook_id": workbook_id,
        "sheet_refs": worksheet_ids,
        "cases_written": len(node_writes),
        "cases_retired": len(stale_ids),
        "suite": suite.as_dict(),
    }


class CaseDerivationService:
    """Binds case derivation to one pool/graph/writer/suite store -- the identical
    "pre-bound object on app.state" shape `Compositor`/`ToleranceCharterService` already
    take, so a route needs no `graph_name` of its own to call this."""

    def __init__(
        self, pool: asyncpg.Pool, *, graph_name: str, writer: GraphWriter, suite_store: ParitySuiteStore,
    ) -> None:
        self._pool = pool
        self._graph = graph_name
        self._writer = writer
        self._suite_store = suite_store

    async def derive(
        self, workbook_id: str, *, charter_version: str, charter: ToleranceCharter, principal: Principal,
    ) -> dict[str, Any]:
        return await derive_cases_for_workbook(
            self._pool, self._graph, self._writer, self._suite_store,
            workbook_id=workbook_id, charter_version=charter_version, charter=charter, principal=principal,
        )

    async def suite(self, workbook_id: str) -> ParitySuite | None:
        return await self._suite_store.get(workbook_id)


__all__ = [
    "MAX_FILTER_VALUES_PER_FILTER",
    "SUITE_TABLE",
    "CaseDerivationError",
    "CaseDerivationService",
    "DerivedCase",
    "ParitySuite",
    "ParitySuiteStore",
    "PostgresParitySuiteStore",
    "SheetDerivation",
    "compute_case_key",
    "derive_cases_for_workbook",
    "derive_filter_contexts",
    "derive_parameter_combinations",
    "derive_sheet_cases",
]
