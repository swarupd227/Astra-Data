"""The Tolerance Charter — story S7.1.1, opening E7/F7.1, spec §4.4/§10/§13.1.

    "As a parity engineer, I want the Tolerance Charter as a versioned document the
    platform enforces, so that 'the same result' is defined once, agreed at G1, and
    applied identically to every report.

    Acceptance criteria:
    - Charter schema per §4.4: numeric (abs and rel epsilon, rounding, currency scale),
      nulls, dates (grain alignment, timezone, fiscal year start), strings (trim, case,
      collation), ordering, rows (missing key policy, row-count tolerance), sampling,
      params (enumeration), waiver rules
    - Editor in the console with inline explanation of each rule's effect; 'simulate'
      re-diffs the last run under the edited charter without executing
    - Versions are immutable; G1 records the version; every ParityRun records the
      version it ran under
    - Changing the charter after G1 requires the parity engineer and the client
      analytics lead and re-proves affected MUs"

§4.4's own worked example is this module's own `DEFAULT_CHARTER` verbatim (values, not
just field names) — the spec's own illustration is a real, defensible starting point, not
a guess: `abs_epsilon: 0.005, rel_epsilon: 1e-6, rounding: HALF_EVEN, currency_scale: 2`,
and so on for every block.

**S7.1.1 itself did not build the Proof Engine's own diff (§10.3) or case execution
(§10.1/§10.2)** — those were F7.2/F7.3's own later, explicit scope at the time
(confirmed directly: `packages/adapter-sdk/src/astra_adapter/proof.py`'s own words,
"The Proof Engine is E7 and does not exist"; no `arbiter.py`/`parity.py` module existed
anywhere in this codebase). What S7.1.1 owned was narrower and real: the charter's own
schema, its versioned storage, the pure cell-level comparison rules each charter block
actually means (numeric epsilon/rounding, the null matrix, string trim/case) — which
double as both the console's own "inline explanation of each rule's effect" and the
real logic `simulate` runs — and the G1/re-charter governance workflow around it.
**Story S7.4.1 (F7.4) is what finally closes that boundary**: `compare_date` (added
here, alongside its three siblings) and `RowRule.max_failing_cells` (§10.3's own "first
N failing cells, default 50") complete this module's own cell-comparator set; the
actual §10.3 algorithm itself — normalisation, keying by grain, key-set comparison, the
row-count/totals check, verdict assembly, the evidence bundle — lives in the new
`diff.py`, built on top of these comparators rather than duplicating them.

**A versioned, admin-editable document — the same `conformance_rules.py`/
`visual_mapping.py` template a third time.** `public.tolerance_charter_version` (migration
v0026) holds one immutable row per saved version; an edit is always a new row (`version =
max + 1`), never an update, the identical "an architect's edit is a new version" discipline
both priors already established. Not a graph node: §4.1.1's own node table declares none,
and a charter version is bookkeeping about *rules*, the same reasoning
`v0019_conformance_ruleset.py`'s own docstring already gives for its own table. §4.4 itself
also says the charter is "stored in Git" — neither of this module's own two precedents ever
actually wrote to Git either; the identical, already-accepted gap, not a new one.

**"Client analytics lead" is a real, disclosed spec-internal gap, not a codebase
oversight.** §13.1's own gate table names this role as G1's client-side approver; §2.4's
own roles table never declares it. Confirmed by direct research, not assumed. Added for
real as a twelfth `Role` (see `roles.py`'s own module docstring) rather than overloading
`client_data_owner`/`client_report_owner`, each already spoken for by a different gate
with a different meaning.

**G1's `GateDecision` reuses the identical shape `g2.py::approve()` already established
for G2** — `approver`/`approver_role` on the client side, `countersigner`/
`countersigner_role` on the Artizent side, the exact "approver approves; a second named
party countersigns" pair `GateDecision.countersigner`'s own note already describes for G2.
No new node property, no new gate mechanics — `GateDecision.gate` already declares `"G1"`
in its own enum (confirmed: nothing has ever written one).

**"Changing the charter after G1 requires the parity engineer and the client analytics
lead" is enforced inside `save()`, not as a second endpoint.** Once at least one G1
`GateDecision` exists for this platform's charter, saving a further version requires the
caller (the Parity Engineer, who alone may save at all) to also name the client analytics
lead's own sign-off and a rationale in the same request — the identical
"approver-plus-named-countersigner in one call" shape `g2.py::approve()` already uses,
applied to a revision rather than a first approval. A fresh `GateDecision(gate="G1")` is
written recording the re-approval, and every workbook whose most recent `ParityRun` ran
under the superseded version is marked for re-proof via `MigrationUnitRegistry.
mark_for_reproof` — the exact existing seam the Harvester's own source-drift path already
calls (`migration_units.py`), reused for a charter revision instead. **This will correctly
mark zero workbooks in this platform's current, real state**: no story has ever written a
`ParityRun` (confirmed directly — E7 is entirely unbuilt before this story), so there is
today no real "which MUs ran under version N" set to query. The query is built for real
so a genuine answer is waiting the day F7.2/F7.3 produce one, the same "a real, honest
function over real, live data, correct today even though nothing populates it yet" posture
`visual_redesign.can_enter_proving` (S6.2.1) already took before any real MU state machine
existed either.

**"Simulate re-diffs the last run... without executing" is a real, pure recompute over
whatever evidence a prior `Verdict.failing_cells` sample actually holds.** `simulate_
charter` looks for the MU's most recent `ParityRun` (`ReportDefinition --PROVED_BY-->
ParityRun`) and, when one exists, re-applies `compare_cell` (numeric/string/date,
dispatched by each cell's own stored `kind`) to each sampled cell under the edited
charter and returns fresh per-cell verdicts — computed only, nothing written, matching
"without executing." When none exists, it says so plainly rather than fabricating a
result — real for any workbook that predates story S7.4.1's own `diff.py` (E7's actual
diff engine), or that `diff.py` has simply never run against yet. The comparator
functions are real and fully tested via hand-built fixture cells, so nothing about
`simulate`'s own logic is left to guess at once real evidence exists to feed it.

**Waiver rules are a charter *policy* this story declares, not the waiver-recording
mechanism itself.** `GateDecision.decision` already includes `"WAIVED"`
(never written); `ExceptionCase.decision` is a plain string with no waiver-specific
fields. Neither is wired to read or enforce `WaiverRule` yet — that is a later Exception
Desk/G3 story's own scope (F8.3/§11.3), this story only declares the policy
(`allowed_classes`/`requires`/`justification_min_chars`) a future mechanism must honour.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any, Protocol

import asyncpg

from .g2 import MIN_RATIONALE_LENGTH
from .graph.queries import EDGE_INDEX_TABLE, NODE_INDEX_TABLE
from .ids import new_ulid
from .lineage import hydrate
from .migration_units import MigrationUnitRegistry
from .principal import Principal
from .tolerance_rules import (
    CHARTER_FIELD_METADATA,
    DEFAULT_CHARTER,
    CellComparison,
    DateRule,
    NullRule,
    NumericRule,
    OrderingRule,
    ParamRule,
    RowRule,
    SamplingRule,
    StringRule,
    ToleranceCharter,
    ToleranceCharterError,
    ToleranceCharterVersion,
    WaiverRule,
    compare_cell,
    compare_null,
    compare_numeric,
    compare_string,
)
from .tolerance_rules import (
    DEFAULT_VERSION as _DEFAULT_VERSION,
)
from .writes import GraphWriter, NodeWrite

CHARTER_TABLE = "public.tolerance_charter_version"
GATE = "G1"
SUBJECT_REF = "tolerance_charter"


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")


class ToleranceCharterStore(Protocol):
    async def latest(self) -> ToleranceCharterVersion: ...

    async def get(self, version: int) -> ToleranceCharterVersion | None: ...

    async def save(self, charter: ToleranceCharter, *, updated_by: str) -> ToleranceCharterVersion: ...


class PostgresToleranceCharterStore:
    def __init__(self, pool: asyncpg.Pool, *, graph_name: str) -> None:
        self._pool = pool
        self._graph = graph_name

    async def latest(self) -> ToleranceCharterVersion:
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                f"SELECT * FROM {CHARTER_TABLE} WHERE graph = $1 ORDER BY version DESC LIMIT 1",
                self._graph,
            )
        return _from_row(row) if row else _DEFAULT_VERSION

    async def get(self, version: int) -> ToleranceCharterVersion | None:
        if version == 0:
            return _DEFAULT_VERSION
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                f"SELECT * FROM {CHARTER_TABLE} WHERE graph = $1 AND version = $2",
                self._graph, version,
            )
        return _from_row(row) if row else None

    async def save(self, charter: ToleranceCharter, *, updated_by: str) -> ToleranceCharterVersion:
        async with self._pool.acquire() as conn, conn.transaction():
            current = await conn.fetchval(
                f"SELECT MAX(version) FROM {CHARTER_TABLE} WHERE graph = $1", self._graph,
            )
            version = (current or 0) + 1
            row = await conn.fetchrow(
                f"""
                INSERT INTO {CHARTER_TABLE} (id, graph, version, charter, updated_by, updated_at)
                VALUES ($1, $2, $3, $4::jsonb, $5, now())
             RETURNING *
                """,
                f"charter_{new_ulid()}", self._graph, version,
                json.dumps(charter.as_dict()), updated_by,
            )
        assert row is not None
        return _from_row(row)


def _from_row(row: asyncpg.Record) -> ToleranceCharterVersion:
    raw = row["charter"]
    data = json.loads(raw) if isinstance(raw, str) else dict(raw)
    updated_at = row["updated_at"]
    return ToleranceCharterVersion(
        version=row["version"],
        charter=ToleranceCharter.from_dict(data),
        updated_by=row["updated_by"],
        updated_at=updated_at.isoformat() if updated_at else None,
    )


# ------------------------------------------------------------------------------ G1 gate


async def _latest_g1_decision(conn: asyncpg.Connection, graph: str) -> dict[str, Any] | None:
    rows = await conn.fetch(
        f"""SELECT id FROM {NODE_INDEX_TABLE}
         WHERE graph = $1 AND kind = 'node' AND label = 'GateDecision' AND retired_at IS NULL""",
        graph,
    )
    ids = [row["id"] for row in rows]
    if not ids:
        return None
    decisions = await hydrate(conn, graph, "GateDecision", ids)
    g1 = [
        props for props in decisions.values()
        if props.get("gate") == GATE and props.get("subject_ref") == SUBJECT_REF
    ]
    if not g1:
        return None
    g1.sort(key=lambda props: str(props.get("timestamp") or ""))
    return g1[-1]


async def has_g1_decision(pool: asyncpg.Pool, graph_name: str) -> bool:
    async with pool.acquire() as conn:
        return await _latest_g1_decision(conn, graph_name) is not None


async def approve_g1(
    pool: asyncpg.Pool,
    graph_name: str,
    writer: GraphWriter,
    *,
    version: int,
    principal: Principal,
    countersigned_by: str,
    rationale: str,
) -> dict[str, Any]:
    """The client analytics lead approves a specific, already-saved charter version at
    G1, countersigned by the Parity Engineer — §13.1's own "Client analytics lead +
    Artizent Parity Engineer" pair, the identical approver/countersigner shape
    `g2.py::approve()` already uses for G2."""
    countersigner = countersigned_by.strip()
    if not countersigner:
        raise ToleranceCharterError("a G1 approval needs the Parity Engineer who countersigns it")
    cleaned_rationale = rationale.strip()
    if len(cleaned_rationale) < MIN_RATIONALE_LENGTH:
        raise ToleranceCharterError(
            f"a G1 approval needs a rationale of at least {MIN_RATIONALE_LENGTH} characters"
        )

    decision_id = new_ulid()
    await writer.write_nodes(
        [
            NodeWrite(
                type="GateDecision",
                id=decision_id,
                properties={
                    "gate": GATE,
                    "subject_ref": SUBJECT_REF,
                    "decision": "APPROVED",
                    "approver": principal.value,
                    "approver_role": "client_analytics_lead",
                    "countersigner": countersigner,
                    "countersigner_role": "parity_engineer",
                    "version_hash": str(version),
                    "rationale": cleaned_rationale,
                    "timestamp": _now(),
                },
            )
        ],
        principal=principal,
    )
    return {"gate_decision_id": decision_id, "version": version, "decision": "APPROVED"}


async def _report_ids_proved_under(conn: asyncpg.Connection, graph: str, charter_version: str) -> list[str]:
    """`ReportDefinition` ids whose most recent proof ran under `charter_version` --
    honestly empty today, since no story has ever written a `ParityRun`."""
    run_rows = await conn.fetch(
        f"""SELECT id FROM {NODE_INDEX_TABLE}
         WHERE graph = $1 AND kind = 'node' AND label = 'ParityRun' AND retired_at IS NULL""",
        graph,
    )
    run_ids = [row["id"] for row in run_rows]
    if not run_ids:
        return []
    runs = await hydrate(conn, graph, "ParityRun", run_ids)
    matching_run_ids = [rid for rid, props in runs.items() if props.get("charter_version") == charter_version]
    if not matching_run_ids:
        return []
    edge_rows = await conn.fetch(
        f"""
        SELECT DISTINCT e.from_id AS report_id
          FROM {EDGE_INDEX_TABLE} e
          JOIN {NODE_INDEX_TABLE} n ON n.graph = e.graph AND n.id = e.from_id
             AND n.kind = 'node' AND n.label = 'ReportDefinition' AND n.retired_at IS NULL
         WHERE e.graph = $1 AND e.label = 'PROVED_BY' AND e.to_id = ANY($2::text[])
           AND e.retired_at IS NULL
        """,
        graph, matching_run_ids,
    )
    return [row["report_id"] for row in edge_rows]


async def affected_workbook_ids(pool: asyncpg.Pool, graph_name: str, *, superseded_version: str) -> list[str]:
    """Every workbook (Migration Unit identity — `ReportDefinition.mu_ref`) whose most
    recent proof ran under `superseded_version`."""
    async with pool.acquire() as conn:
        report_ids = await _report_ids_proved_under(conn, graph_name, superseded_version)
        reports = await hydrate(conn, graph_name, "ReportDefinition", report_ids)
    return sorted({str(props["mu_ref"]) for props in reports.values() if props.get("mu_ref")})


async def save_charter(
    pool: asyncpg.Pool,
    graph_name: str,
    writer: GraphWriter,
    store: ToleranceCharterStore,
    migration_units: MigrationUnitRegistry,
    *,
    charter: ToleranceCharter,
    principal: Principal,
    client_analytics_lead_ack: str | None = None,
    reason: str | None = None,
) -> dict[str, Any]:
    """Save a new, immutable charter version. Once a G1 decision has ever been recorded,
    a further change requires the client analytics lead's own named sign-off and a
    reason in the same call — the AC's own "requires the parity engineer and the client
    analytics lead" — and re-proves every workbook `affected_workbook_ids` finds for the
    version this change supersedes."""
    previous = await store.latest()
    is_revision = await has_g1_decision(pool, graph_name)

    if is_revision:
        ack = (client_analytics_lead_ack or "").strip()
        if not ack:
            raise ToleranceCharterError(
                "the charter has already been approved at G1; changing it needs the "
                "client analytics lead's own named sign-off"
            )
        cleaned_reason = (reason or "").strip()
        if len(cleaned_reason) < MIN_RATIONALE_LENGTH:
            raise ToleranceCharterError(
                f"changing an approved charter needs a reason of at least "
                f"{MIN_RATIONALE_LENGTH} characters"
            )

    saved = await store.save(charter, updated_by=principal.value)

    reproved: list[str] = []
    if is_revision:
        assert client_analytics_lead_ack is not None and reason is not None
        decision_id = new_ulid()
        await writer.write_nodes(
            [
                NodeWrite(
                    type="GateDecision",
                    id=decision_id,
                    properties={
                        "gate": GATE,
                        "subject_ref": SUBJECT_REF,
                        "decision": "APPROVED",
                        "approver": client_analytics_lead_ack.strip(),
                        "approver_role": "client_analytics_lead",
                        "countersigner": principal.value,
                        "countersigner_role": "parity_engineer",
                        "version_hash": str(saved.version),
                        "rationale": reason.strip(),
                        "timestamp": _now(),
                    },
                )
            ],
            principal=principal,
        )
        workbook_ids = await affected_workbook_ids(
            pool, graph_name, superseded_version=str(previous.version)
        )
        for workbook_id in workbook_ids:
            accepted = await migration_units.mark_for_reproof(
                workbook_id, reason="tolerance charter revised", principal=principal.value
            )
            if accepted:
                reproved.append(workbook_id)

    return {"charter": saved.as_dict(), "is_revision": is_revision, "reproved_workbook_ids": reproved}


# -------------------------------------------------------------------------------- simulate


async def simulate_charter(
    pool: asyncpg.Pool, graph_name: str, *, workbook_id: str, charter: ToleranceCharter
) -> dict[str, Any]:
    """Re-diff the workbook's last run under the edited charter, without executing
    anything. Reports "no prior run" for a workbook that has never been diffed (or, for
    any workbook, before story S7.4.1 -- `diff.py` -- first wrote a real `ParityRun`/
    `Verdict`); a workbook a real run has actually covered gets a real recompute."""
    async with pool.acquire() as conn:
        report_rows = await conn.fetch(
            f"""SELECT id FROM {NODE_INDEX_TABLE}
             WHERE graph = $1 AND kind = 'node' AND label = 'ReportDefinition' AND retired_at IS NULL""",
            graph_name,
        )
        report_ids = [row["id"] for row in report_rows]
        reports = await hydrate(conn, graph_name, "ReportDefinition", report_ids)
        own_report_ids = [rid for rid, props in reports.items() if props.get("mu_ref") == workbook_id]

        run_ids: list[str] = []
        if own_report_ids:
            edge_rows = await conn.fetch(
                f"""
                SELECT e.to_id AS run_id
                  FROM {EDGE_INDEX_TABLE} e
                  JOIN {NODE_INDEX_TABLE} n ON n.graph = e.graph AND n.id = e.to_id
                     AND n.kind = 'node' AND n.label = 'ParityRun' AND n.retired_at IS NULL
                 WHERE e.graph = $1 AND e.label = 'PROVED_BY' AND e.from_id = ANY($2::text[])
                   AND e.retired_at IS NULL
                """,
                graph_name, own_report_ids,
            )
            run_ids = [row["run_id"] for row in edge_rows]

        if not run_ids:
            return {
                "workbook_id": workbook_id, "has_prior_run": False,
                "message": "no ParityRun exists yet for this workbook",
                "verdicts": [],
            }

        runs = await hydrate(conn, graph_name, "ParityRun", run_ids)
        latest_run_id = max(runs, key=lambda rid: str(runs[rid].get("finished") or runs[rid].get("started") or ""))
        verdict_ids = list(runs[latest_run_id].get("verdicts") or [])
        verdicts = await hydrate(conn, graph_name, "Verdict", verdict_ids)

    recomputed: list[dict[str, Any]] = []
    for verdict_id, properties in verdicts.items():
        for cell in properties.get("failing_cells") or []:
            comparison = compare_cell(
                str(cell.get("kind") or ""), cell.get("expected"), cell.get("candidate"), charter
            )
            recomputed.append(
                {
                    "verdict_id": verdict_id,
                    "grain_key": cell.get("grain_key"),
                    "measure": cell.get("measure"),
                    "expected": cell.get("expected"),
                    "candidate": cell.get("candidate"),
                    "result": comparison.result,
                    "reason": comparison.reason,
                }
            )

    return {
        "workbook_id": workbook_id, "has_prior_run": True, "run_id": latest_run_id,
        "message": None, "verdicts": recomputed,
    }


class ToleranceCharterService:
    """Binds the charter's own free functions to one pool/graph/writer/store/registry --
    the identical "pre-bound object on app.state" shape `Compositor`/`Modeller` already
    take, so a route needs no `graph_name` of its own to call this."""

    def __init__(
        self,
        pool: asyncpg.Pool,
        *,
        graph_name: str,
        writer: GraphWriter,
        store: ToleranceCharterStore,
        migration_units: MigrationUnitRegistry,
    ) -> None:
        self._pool = pool
        self._graph = graph_name
        self._writer = writer
        self._store = store
        self._migration_units = migration_units

    async def latest(self) -> ToleranceCharterVersion:
        return await self._store.latest()

    async def get(self, version: int) -> ToleranceCharterVersion | None:
        return await self._store.get(version)

    async def save(
        self,
        charter: ToleranceCharter,
        *,
        principal: Principal,
        client_analytics_lead_ack: str | None = None,
        reason: str | None = None,
    ) -> dict[str, Any]:
        return await save_charter(
            self._pool, self._graph, self._writer, self._store, self._migration_units,
            charter=charter, principal=principal,
            client_analytics_lead_ack=client_analytics_lead_ack, reason=reason,
        )

    async def approve_g1(
        self, *, version: int, principal: Principal, countersigned_by: str, rationale: str
    ) -> dict[str, Any]:
        return await approve_g1(
            self._pool, self._graph, self._writer,
            version=version, principal=principal,
            countersigned_by=countersigned_by, rationale=rationale,
        )

    async def simulate(self, *, workbook_id: str, charter: ToleranceCharter) -> dict[str, Any]:
        return await simulate_charter(self._pool, self._graph, workbook_id=workbook_id, charter=charter)


__all__ = [
    "CHARTER_FIELD_METADATA",
    "CHARTER_TABLE",
    "DEFAULT_CHARTER",
    "GATE",
    "SUBJECT_REF",
    "CellComparison",
    "DateRule",
    "NullRule",
    "NumericRule",
    "OrderingRule",
    "ParamRule",
    "PostgresToleranceCharterStore",
    "RowRule",
    "SamplingRule",
    "StringRule",
    "ToleranceCharter",
    "ToleranceCharterError",
    "ToleranceCharterService",
    "ToleranceCharterStore",
    "ToleranceCharterVersion",
    "WaiverRule",
    "affected_workbook_ids",
    "approve_g1",
    "compare_cell",
    "compare_null",
    "compare_numeric",
    "compare_string",
    "has_g1_decision",
    "save_charter",
    "simulate_charter",
]
