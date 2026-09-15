"""Which workspaces this tenant calls production -- story S11.2.1.

    "Production execution is limited to the regression runner with the same read-only
    principal."

A platform table, the identical footing `mender_config` (v0030), `tolerance_charter_
version` (v0026) and `conformance_ruleset` (v0019) already established: a platform
engineer's edit is a new version, never an overwrite, so "which workspaces were
production when this execution ran" stays answerable even after a later change. "Per
tenant" is this codebase's own already-established "the graph is the tenant" footing --
`execution_safety_policy` is scoped by `graph` the identical way, not a new
multi-tenancy mechanism.

No ontology change here: which workspace names a tenant calls production is bookkeeping
about this deployment's own execution safety, not a fact about the source or target
estate -- the identical reasoning `mender_config`'s own docstring already gives for its
own table.
"""

from __future__ import annotations

import asyncpg

VERSION = 41
DESCRIPTION = "Which workspaces this tenant calls production (public.execution_safety_policy)"

ONTOLOGY_CHANGES: list[dict[str, str]] = []


_DDL = """
CREATE TABLE IF NOT EXISTS public.execution_safety_policy (
    id                     text        PRIMARY KEY,
    graph                  text        NOT NULL,
    version                int         NOT NULL,
    production_workspaces  text[]      NOT NULL DEFAULT '{}',
    updated_by             text        NOT NULL,
    updated_at             timestamptz NOT NULL DEFAULT now(),
    UNIQUE (graph, version)
)
"""

_INDEXES = (
    "CREATE INDEX IF NOT EXISTS execution_safety_policy_latest_idx "
    "ON public.execution_safety_policy (graph, version DESC)",
)


async def up(conn: asyncpg.Connection) -> None:
    await conn.execute(_DDL)
    for statement in _INDEXES:
        await conn.execute(statement)
