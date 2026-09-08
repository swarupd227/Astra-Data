"""The Mender's own bound -- story S8.2.1.

    "Bound (default 3) is configurable per tenant."

A platform table, the identical footing `tolerance_charter_version` (v0026),
`conformance_ruleset` (v0019) and `model_gateway_policy` (v0021) already established: a
platform engineer's edit is a new version, never an overwrite, so "what the bound was
when this pass ran" stays answerable even after a later change. "Per tenant" is this
codebase's own already-established "the graph is the tenant" footing (confirmed
directly: `gateway.py`'s own `PostgresGatewayPolicyStore`, `RulesEngine`,
`PostgresConformanceRulesetStore`, `PostgresProvenanceStore` all scope "per tenant" by
`graph` alone, one physical deployment per client) -- `mender_config` is scoped by
`graph` the identical way, not a new multi-tenancy mechanism.

No ontology change here: a pass budget is bookkeeping about the Mender's own loop, not a
fact about the source or target estate, the identical reasoning `tolerance_charter_
version`'s own docstring already gives for its own table.
"""

from __future__ import annotations

import asyncpg

VERSION = 30
DESCRIPTION = "The Mender's own configurable pass budget (public.mender_config)"

ONTOLOGY_CHANGES: list[dict[str, str]] = []


_DDL = """
CREATE TABLE IF NOT EXISTS public.mender_config (
    id           text        PRIMARY KEY,
    graph        text        NOT NULL,
    version      int         NOT NULL,
    pass_budget  int         NOT NULL,
    updated_by   text        NOT NULL,
    updated_at   timestamptz NOT NULL DEFAULT now(),
    UNIQUE (graph, version)
)
"""

_INDEXES = (
    "CREATE INDEX IF NOT EXISTS mender_config_latest_idx "
    "ON public.mender_config (graph, version DESC)",
)


async def up(conn: asyncpg.Connection) -> None:
    await conn.execute(_DDL)
    for statement in _INDEXES:
        await conn.execute(statement)
