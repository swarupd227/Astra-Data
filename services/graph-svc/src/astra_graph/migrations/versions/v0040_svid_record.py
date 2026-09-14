"""SVID issuance, rotation and revocation records — story S11.1.2, opens F11.1.

    "Identity issuance, rotation and revocation are visible in Tenant & Access."

One plain Postgres platform table, the identical footing `promotion_run`/`decommission_
confirmation`/`notification_log` already have -- not an ontology node: an SVID is a fact
about this deployment's own identity plumbing, not a fact harvested from or written to a
client's estate. Append-only by construction: an issuance is one row (`predecessor_jti`
null); a rotation is a second row naming the row it rotated from, so a lineage reads as a
real chain rather than an overwritten single record (the same "never mutate, always add a
new row that references the one before it" posture this codebase already takes wherever a
real history matters more than a current value). Revocation sets three columns on the
row being revoked in place -- the one exception, because a revocation is a fact *about*
that specific issuance, not a new issuance of its own.

No ontology change: `workload_identity.py`'s own module docstring explains why an SVID's
signed token itself is never a column here (`svid_record` stores only what "Tenant &
Access" needs to show a real audit trail, never a bearer credential).
"""

from __future__ import annotations

import asyncpg

VERSION = 40
DESCRIPTION = "SVID issuance, rotation and revocation records"

ONTOLOGY_CHANGES: list[dict[str, str]] = []


_DDL = """
CREATE TABLE IF NOT EXISTS public.svid_record (
    id                text        PRIMARY KEY,
    graph             text        NOT NULL,
    jti               text        NOT NULL,
    agent_id          text        NOT NULL,
    run_id            text        NOT NULL,
    spiffe_id         text        NOT NULL,
    serial            integer     NOT NULL,
    predecessor_jti   text,
    issued_at         timestamptz NOT NULL,
    expires_at        timestamptz NOT NULL,
    revoked_at        timestamptz,
    revoked_by        text,
    revocation_reason text,
    UNIQUE (graph, jti)
)
"""

_INDEXES = (
    "CREATE INDEX IF NOT EXISTS svid_record_agent_idx "
    "ON public.svid_record (graph, agent_id, issued_at DESC)",
)


async def up(conn: asyncpg.Connection) -> None:
    await conn.execute(_DDL)
    for statement in _INDEXES:
        await conn.execute(statement)
