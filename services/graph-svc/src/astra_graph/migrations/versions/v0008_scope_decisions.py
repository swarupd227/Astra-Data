"""Scope decisions: re-tiering and withdrawal, recorded with their reason.

S1.4.1 puts two actions on the Estate Explorer that change what the programme has committed
to deliver — re-tier with reason, withdraw from scope with reason — both Programme Manager
only. §15.2: "Every action is a record ... with a reason field that is required, not
optional."

**Why a table rather than a property on the workbook.** A tier is not a fact about the
client's estate; it is a judgement the programme made about it, and §4.1.1 declares no such
property on Workbook for exactly that reason. Writing one would put a decision inside the
record of what was found. So the decision is its own row: what was decided, about what, by
whom, why, and when.

**Why it exists before the Migration Unit does.** Tier and withdrawal are MU properties
(§3.1, §3.2), and the Cartographer creates the MU in E3. A programme manager looking at a
freshly harvested estate still has judgements to record — this workbook is out of scope,
that one is more complex than it looks — and losing them until E3 ships would mean asking
for them twice. The MU inherits these when it is created.
"""

from __future__ import annotations

import asyncpg

VERSION = 8
DESCRIPTION = "Programme scope decisions: re-tier and withdraw, with reasons"

ONTOLOGY_CHANGES: list[dict[str, str]] = []


_DDL = """
CREATE TABLE IF NOT EXISTS public.scope_decision (
    id           text        PRIMARY KEY,
    graph        text        NOT NULL,
    workbook_id  text        NOT NULL,
    kind         text        NOT NULL CHECK (kind IN ('RE_TIER', 'WITHDRAW', 'REINSTATE')),
    from_value   text,
    to_value     text,
    reason       text        NOT NULL CHECK (length(btrim(reason)) >= 10),
    decided_by   text        NOT NULL,
    decided_at   timestamptz NOT NULL DEFAULT now()
)
"""

_INDEXES = (
    # The Explorer reads the latest decision per workbook for every row it renders.
    "CREATE INDEX IF NOT EXISTS scope_decision_workbook_idx "
    "ON public.scope_decision (graph, workbook_id, decided_at DESC)",
    "CREATE INDEX IF NOT EXISTS scope_decision_kind_idx "
    "ON public.scope_decision (graph, kind, decided_at DESC)",
)


async def up(conn: asyncpg.Connection) -> None:
    await conn.execute(_DDL)
    for statement in _INDEXES:
        await conn.execute(statement)
