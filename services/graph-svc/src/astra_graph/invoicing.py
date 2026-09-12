"""mu.accepted invoicing and the commercial ledger -- story S9.1.2, closing F9.1, E9's
own F9.1.

    "As a programme manager, I want G3 acceptance to trigger the invoicing event under
    the fixed-price contract, so that commercial recognition is a platform event, not a
    spreadsheet.

    Acceptance criteria:
    - mu.accepted event with MU, tier and unit price is emitted and exported to the
      programme's commercial ledger; the Programme Board shows accepted units by tier
      against plan"

§3.1 itself, verbatim: "Invoicing under a fixed-price-per-report contract is triggered
by an MU reaching ACCEPTED." §3.4's own worked example: "...approves G3. State ->
ACCEPTED; invoice line raised." §13.1's own G3 row: "Invoice trigger; release
permitted."

**Neither a real unit price nor a real per-tier plan is ever stated anywhere in the
spec** -- confirmed by direct grep of the whole spec document for "unit price",
"fixed-price", "ledger": every hit only ever says an MU reaching ACCEPTED *is* the
trigger, never what the trigger is worth or how many of each tier are planned.
`DEFAULT_UNIT_PRICES`/`PLANNED_BY_TIER` below are real, invented, disclosed planning
assumptions -- the identical footing `retention.PLANNED_FAMILY_COUNT = 150` already has
for the one overall figure Appendix A itself names ("a planning assumption, measured in
Month 1"). `PLANNED_BY_TIER` is deliberately built to sum to that same 150, so the two
figures never silently disagree.

**No real Migration Unit exists to be the event's own subject** -- confirmed, again,
directly against `migration_units.py`'s own docstring ("this is a port, not an
implementation"). The workbook id is the real MU proxy every G3-adjacent story has used
since S8.1.1 (`ExceptionCase.mu_ref`, ADR 0060) -- `mu_accepted`'s own `subject` names
it directly.

**Tier resolution reuses `ScopeStore.states()` verbatim -- there is no single-workbook
tier getter anywhere in this codebase, confirmed by direct read of `scope.py`.** Rather
than adding one there for a single caller, this module reads the same bulk map every
other tier consumer already does and indexes into it. A workbook that has never been
re-tiered honestly has no tier (`ScopeState.tier is None`) -- `record_acceptance`
refuses to invoice it rather than guessing a tier or defaulting to one, the identical
"a real check, an honest skip" posture this codebase takes throughout (e.g. `parity_
dashboard.py`'s own honestly-absent Mender-passes trend).

**Unit prices are a single current-value row per tier (`unit_price_schedule`), not
versioned like `mender_config` -- a deliberate simplification, disclosed in the
migration's own docstring.** A missing row for a tier falls back to `DEFAULT_UNIT_
PRICES[tier]` rather than failing -- a fixed-price contract's own real prices are
expected to exist from day one of a real deployment, but every test and demo estate in
this codebase should still get a real, usable number without seeding one first, the
identical "sane defaults, no config UI required yet" posture `mender.
DEFAULT_PASS_BUDGET` already has (confirmed: that store has no console screen and no
HTTP route either).

**"Exported to the programme's commercial ledger" is the real write to `commercial_
ledger` itself -- a durable, separately-queryable, commercial-domain table, not merely
the append-only `estate_event` outbox a bare `mu.accepted` event alone would produce.**
Writing that row *is* the export: the fact leaves the estate-event stream (a platform
concern) and lands in a table shaped for a commercial reader (workbook, tier, price,
who/when) -- the same "a second, business-shaped read of a first-class platform fact"
reasoning `parity_dashboard.py`'s own dashboard already gives for reading `Verdict`s a
second way beyond the raw event log.

**A `UNIQUE (graph, workbook_id)` constraint makes re-approving an already-accepted
workbook a real no-op, not a double-billed line** -- "commercial recognition is a
platform event, not a spreadsheet" only holds if the platform cannot invoice the same
MU twice. `record_acceptance` inserts with `ON CONFLICT ... DO NOTHING` and only emits
the `mu_accepted` event when a row was actually, newly written.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol

import asyncpg

from .events import mu_accepted
from .ids import new_ulid
from .principal import Principal
from .scope import TIERS, ScopeStore
from .writes import GraphWriter

LEDGER_TABLE = "public.commercial_ledger"
PRICE_TABLE = "public.unit_price_schedule"

#: A real, invented, disclosed planning assumption -- see module docstring. Increasing
#: with complexity, the same intuition §9.1's own C1-C4 calibration targets already
#: reflect (a simpler construct costs less to migrate and so is priced lower).
DEFAULT_UNIT_PRICES: dict[str, float] = {
    "SIMPLE": 8_000.0,
    "MODERATE": 15_000.0,
    "COMPLEX": 28_000.0,
    "REDESIGN": 40_000.0,
}

#: A real, invented, disclosed split of `retention.PLANNED_FAMILY_COUNT`'s own 150 --
#: deliberately built to sum to the identical total so the two planning figures never
#: silently disagree.
PLANNED_BY_TIER: dict[str, int] = {
    "SIMPLE": 70,
    "MODERATE": 50,
    "COMPLEX": 20,
    "REDESIGN": 10,
}


@dataclass(frozen=True, slots=True)
class LedgerEntry:
    id: str
    workbook_id: str
    tier: str
    unit_price: float
    gate_decision_id: str
    recorded_by: str
    recorded_at: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "id": self.id, "workbook_id": self.workbook_id, "tier": self.tier,
            "unit_price": self.unit_price, "gate_decision_id": self.gate_decision_id,
            "recorded_by": self.recorded_by, "recorded_at": self.recorded_at,
        }


class UnitPriceStore(Protocol):
    async def all(self) -> dict[str, float]: ...

    async def get(self, tier: str) -> float: ...

    async def set(self, tier: str, unit_price: float, *, updated_by: str) -> float: ...


class PostgresUnitPriceStore:
    def __init__(self, pool: asyncpg.Pool, *, graph_name: str) -> None:
        self._pool = pool
        self._graph = graph_name

    async def all(self) -> dict[str, float]:
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(
                f"SELECT tier, unit_price FROM {PRICE_TABLE} WHERE graph = $1", self._graph,
            )
        stored = {str(row["tier"]): float(row["unit_price"]) for row in rows}
        return {tier: stored.get(tier, DEFAULT_UNIT_PRICES[tier]) for tier in TIERS}

    async def get(self, tier: str) -> float:
        prices = await self.all()
        return prices[tier]

    async def set(self, tier: str, unit_price: float, *, updated_by: str) -> float:
        if tier not in TIERS:
            raise ValueError(f"tier must be one of {TIERS}; got {tier!r}")
        async with self._pool.acquire() as conn:
            await conn.execute(
                f"""INSERT INTO {PRICE_TABLE} (graph, tier, unit_price, updated_by)
                     VALUES ($1, $2, $3, $4)
                     ON CONFLICT (graph, tier)
                     DO UPDATE SET unit_price = $3, updated_by = $4, updated_at = now()""",
                self._graph, tier, unit_price, updated_by,
            )
        return unit_price


async def _current_tier(scope_store: ScopeStore, workbook_id: str) -> str | None:
    states = await scope_store.states()
    state = states.get(workbook_id)
    return state.tier if state else None


async def record_acceptance(
    pool: asyncpg.Pool, graph_name: str, writer: GraphWriter, scope_store: ScopeStore,
    unit_price_store: UnitPriceStore, *, workbook_id: str, gate_decision_id: str, principal: Principal,
) -> LedgerEntry | None:
    """"mu.accepted event with MU, tier and unit price is emitted and exported to the
    programme's commercial ledger" -- called once, from `g3_card.approve`, after the
    real `GateDecision(gate="G3", decision="APPROVED")` it names is already written.
    `None`, honestly, when this workbook has never been re-tiered -- there is no real
    fixed-price line to raise without one."""
    tier = await _current_tier(scope_store, workbook_id)
    if tier is None:
        return None

    unit_price = await unit_price_store.get(tier)
    entry_id = new_ulid()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            f"""INSERT INTO {LEDGER_TABLE}
                 (id, graph, workbook_id, tier, unit_price, gate_decision_id, recorded_by)
                 VALUES ($1, $2, $3, $4, $5, $6, $7)
                 ON CONFLICT (graph, workbook_id) DO NOTHING
                 RETURNING id, workbook_id, tier, unit_price, gate_decision_id, recorded_by, recorded_at""",
            entry_id, graph_name, workbook_id, tier, unit_price, gate_decision_id, principal.value,
        )
    if row is None:
        return None  # already accepted once before -- a real no-op, not a double-billed line

    await writer.append_event(
        mu_accepted(
            source=writer.event_source, workbook_id=workbook_id, tier=tier,
            unit_price=unit_price, gate_decision_id=gate_decision_id, principal=principal,
        )
    )
    return LedgerEntry(
        id=str(row["id"]), workbook_id=str(row["workbook_id"]), tier=str(row["tier"]),
        unit_price=float(row["unit_price"]), gate_decision_id=str(row["gate_decision_id"]),
        recorded_by=str(row["recorded_by"]), recorded_at=row["recorded_at"].isoformat(),
    )


async def accepted_by_tier(pool: asyncpg.Pool, graph_name: str) -> dict[str, int]:
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            f"SELECT tier, count(*) AS n FROM {LEDGER_TABLE} WHERE graph = $1 GROUP BY tier", graph_name,
        )
    counts = {str(row["tier"]): int(row["n"]) for row in rows}
    return {tier: counts.get(tier, 0) for tier in TIERS}


async def programme_acceptance_summary(
    pool: asyncpg.Pool, graph_name: str, unit_price_store: UnitPriceStore,
) -> dict[str, Any]:
    """"The Programme Board shows accepted units by tier against plan" -- real accepted
    counts from the commercial ledger, against the disclosed `PLANNED_BY_TIER` planning
    assumption, alongside each tier's own real current unit price."""
    accepted = await accepted_by_tier(pool, graph_name)
    prices = await unit_price_store.all()
    by_tier = [
        {
            "tier": tier, "accepted": accepted[tier], "planned": PLANNED_BY_TIER[tier],
            "delta": accepted[tier] - PLANNED_BY_TIER[tier], "unit_price": prices[tier],
            "accepted_value": accepted[tier] * prices[tier],
        }
        for tier in TIERS
    ]
    return {
        "by_tier": by_tier,
        "total_accepted": sum(row["accepted"] for row in by_tier),
        "total_planned": sum(row["planned"] for row in by_tier),
        "total_accepted_value": sum(row["accepted_value"] for row in by_tier),
    }


__all__ = [
    "DEFAULT_UNIT_PRICES",
    "PLANNED_BY_TIER",
    "LedgerEntry",
    "PostgresUnitPriceStore",
    "UnitPriceStore",
    "accepted_by_tier",
    "programme_acceptance_summary",
    "record_acceptance",
]
