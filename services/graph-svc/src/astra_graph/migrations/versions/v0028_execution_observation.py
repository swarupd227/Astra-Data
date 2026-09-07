"""Execution outcome observations -- story S7.3.2, spec §10.2; backlog's own AC:
"Inconclusive rate is a Platform Health metric with an alert threshold (default 2%)."

A platform table, the identical footing `pattern_observation` (v0023)/`calibration_
observation` (v0022) already set: append-only, one row per side per case per execution
(source or target, win or lose), never an update, so the AC's own inconclusive rate is
always computed live from the complete history this platform has actually seen, never a
maintained counter that could drift from it.

No ontology change: an execution observation is telemetry about how proving went, not a
fact about the source or target estate -- the same reasoning `model_gateway_policy`
(v0021) already gave its own eval-history table.
"""

from __future__ import annotations

import asyncpg

VERSION = 28
DESCRIPTION = "Execution observations for the inconclusive-rate metric (public.execution_observation)"

_DDL = """
CREATE TABLE IF NOT EXISTS public.execution_observation (
    id            text        PRIMARY KEY,
    graph         text        NOT NULL,
    mu_ref        text        NOT NULL,
    case_id       text        NOT NULL,
    side          text        NOT NULL,
    strategy      text        NOT NULL,
    outcome       text        NOT NULL,
    reason_class  text,
    attempts      int         NOT NULL,
    created_by    text        NOT NULL,
    recorded_at   timestamptz NOT NULL
)
"""

_INDEXES = (
    "CREATE INDEX IF NOT EXISTS execution_observation_graph_time_idx "
    "ON public.execution_observation (graph, recorded_at)",
)


async def up(conn: asyncpg.Connection) -> None:
    await conn.execute(_DDL)
    for statement in _INDEXES:
        await conn.execute(statement)
