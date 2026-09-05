"""Confidence calibration observations — story S5.3.3.

    "Model declares a confidence in the output schema; the platform records it and, per
    §16.3, reports calibration (declared vs observed proof rate) in ten buckets."

A platform table, the same footing as `model_gateway_policy` (S5.3.2): one row per real
declared-confidence observation (both successful and failed/declined generation attempts,
not only survivors — a calibration curve built only from successes would be trivially
100% at every bucket), never overwritten, so the full history is always available for
recomputing a report and for an auditor asking what this platform actually saw.

No ontology change: a confidence observation is bookkeeping about a model call, not a fact
about the source or target estate.
"""

from __future__ import annotations

import asyncpg

VERSION = 22
DESCRIPTION = "Confidence calibration observations (public.calibration_observation)"

ONTOLOGY_CHANGES: list[dict[str, str]] = []


_DDL = """
CREATE TABLE IF NOT EXISTS public.calibration_observation (
    id                  text             PRIMARY KEY,
    graph               text             NOT NULL,
    task_class          text             NOT NULL,
    agent               text             NOT NULL,
    model               text             NOT NULL,
    provider            text             NOT NULL,
    declared_confidence double precision NOT NULL,
    observed_pass       boolean          NOT NULL,
    created_by          text             NOT NULL,
    recorded_at         timestamptz      NOT NULL DEFAULT now()
)
"""

_INDEXES = (
    "CREATE INDEX IF NOT EXISTS calibration_observation_task_class_idx "
    "ON public.calibration_observation (graph, task_class)",
)


async def up(conn: asyncpg.Connection) -> None:
    await conn.execute(_DDL)
    for statement in _INDEXES:
        await conn.execute(statement)
