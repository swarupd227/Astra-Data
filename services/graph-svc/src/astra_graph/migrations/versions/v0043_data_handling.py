"""The Data Handling screen and a real gateway request log -- story S11.4.1, opens F11.4.

    "A Data Handling screen that states exactly what reaches a model endpoint and lets
    me confirm it... 'Sign boundary' records the reviewer, the version of the position
    and the date; changing a provider or a redaction rule invalidates the signature and
    requires re-sign... a CI and on-demand check sends sentinel row data through every
    agent path and asserts it never appears in a gateway request log."

Three platform tables, the identical "the graph is the tenant, an edit is a new
version, never an overwrite" footing `execution_safety_policy` (v0041)/`mender_config`
(v0030)/`tolerance_charter_version` (v0026) already established:

- `data_handling_position`: the signable document itself (providers, retention terms,
  redaction rules) -- versioned and append-only, so "what was reviewed" never silently
  changes under an existing signature.
- `data_handling_signoff`: append-only sign-off records (reviewer, the position version
  actually signed, the date). Validity is never a stored boolean -- it is always
  `latest signoff.position_version == latest position.version`, computed at read time
  in `data_handling.py`, the same way this schema needs no explicit "invalidate" write
  at all: editing the position simply outpaces whatever version was last signed.
- `gateway_request_log`: a real, disclosed, deliberately narrow pull-forward of the one
  storage primitive S11.4.2 (full gateway logging, redaction-of-secret-patterns,
  content-logging-off-by-default) will need -- recording the literal outbound request
  text `gateway.py`'s own `AnthropicModelCaller` builds, always on for now, so this
  story's own boundary test has a real log to assert sentinel data against rather than
  a vacuous, nothing-ever-recorded pass. See `gateway.py`'s own module docstring for the
  full reasoning.

No ontology change here: none of this is a fact about the source or target estate --
the identical reasoning `execution_safety_policy`'s own docstring already gives for its
own table.
"""

from __future__ import annotations

import asyncpg

VERSION = 43
DESCRIPTION = (
    "The Data Handling position/sign-off and a real gateway request log "
    "(public.data_handling_position, public.data_handling_signoff, "
    "public.gateway_request_log)"
)

ONTOLOGY_CHANGES: list[dict[str, str]] = []


_DDL = """
CREATE TABLE IF NOT EXISTS public.data_handling_position (
    id               text        PRIMARY KEY,
    graph            text        NOT NULL,
    version          int         NOT NULL,
    providers        jsonb       NOT NULL,
    retention_terms  text        NOT NULL,
    redaction_rules  jsonb       NOT NULL,
    updated_by       text        NOT NULL,
    updated_at       timestamptz NOT NULL DEFAULT now(),
    UNIQUE (graph, version)
);

CREATE TABLE IF NOT EXISTS public.data_handling_signoff (
    id               text        PRIMARY KEY,
    graph            text        NOT NULL,
    position_version int         NOT NULL,
    reviewer         text        NOT NULL,
    signed_at        timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS public.gateway_request_log (
    id            text        PRIMARY KEY,
    graph         text        NOT NULL,
    provider      text        NOT NULL,
    task_class    text        NOT NULL,
    agent_id      text,
    prompt_hash   text        NOT NULL,
    request_text  text        NOT NULL,
    created_at    timestamptz NOT NULL DEFAULT now()
)
"""

_INDEXES = (
    "CREATE INDEX IF NOT EXISTS data_handling_position_latest_idx "
    "ON public.data_handling_position (graph, version DESC)",
    "CREATE INDEX IF NOT EXISTS data_handling_signoff_latest_idx "
    "ON public.data_handling_signoff (graph, signed_at DESC)",
    "CREATE INDEX IF NOT EXISTS gateway_request_log_graph_idx "
    "ON public.gateway_request_log (graph, created_at DESC)",
)


async def up(conn: asyncpg.Connection) -> None:
    await conn.execute(_DDL)
    for statement in _INDEXES:
        await conn.execute(statement)
