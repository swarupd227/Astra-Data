"""The Evidence Chain -- story S11.3.1, opens F11.3.

    "An append-only, hash-linked record of every state transition, gate decision, agent
    run, model call and verdict."
    "Retention configurable per tenant (default: programme lifetime + 7 years) with
    export before deletion."

Three platform tables:

- ``evidence_chain_entry``: one row per hash-linked fact, pulled from ``estate_event``/
  ``svid_record``/``provenance`` by `evidence_chain.advance_chain` -- never written
  inline with those tables' own writes, see that module's own docstring for why.
- ``evidence_daily_root``: one row per calendar day this chain actually advanced on
  (bucketed by when it was chained, not by each entry's own original timestamp -- see
  `evidence_chain.compute_daily_root`'s own docstring), each rolling the previous day's
  root in so the roots themselves form a second, coarser chain.
- ``retention_policy``: the identical `mender_config`/`execution_safety_policy` shape
  (graph-scoped, versioned, a platform engineer's edit is a new version) for story
  S11.3.1's own "retention configurable per tenant".

No ontology change: none of this is a fact about the source or target estate.
"""

from __future__ import annotations

import asyncpg

VERSION = 42
DESCRIPTION = "The Evidence Chain: hash-linked entries, daily roots, tenant retention policy"

ONTOLOGY_CHANGES: list[dict[str, str]] = []


_DDL = """
CREATE TABLE IF NOT EXISTS public.evidence_chain_entry (
    id            text        PRIMARY KEY,
    graph         text        NOT NULL,
    chain_seq     bigint      NOT NULL,
    source_table  text        NOT NULL,
    source_id     text        NOT NULL,
    category      text        NOT NULL,
    occurred_at   timestamptz NOT NULL,
    prev_hash     text        NOT NULL,
    hash          text        NOT NULL,
    chained_at    timestamptz NOT NULL DEFAULT now(),
    UNIQUE (graph, chain_seq),
    UNIQUE (graph, source_table, source_id)
);

CREATE TABLE IF NOT EXISTS public.evidence_daily_root (
    id               text        PRIMARY KEY,
    graph            text        NOT NULL,
    day              date        NOT NULL,
    first_chain_seq  bigint      NOT NULL,
    last_chain_seq   bigint      NOT NULL,
    entry_count      int         NOT NULL,
    prev_root_hash   text        NOT NULL,
    root_hash        text        NOT NULL,
    computed_at      timestamptz NOT NULL DEFAULT now(),
    anchor_kind      text,
    anchor_ref       text,
    anchored_at      timestamptz,
    UNIQUE (graph, day)
);

CREATE TABLE IF NOT EXISTS public.retention_policy (
    id               text        PRIMARY KEY,
    graph            text        NOT NULL,
    version          int         NOT NULL,
    retention_years  int         NOT NULL,
    updated_by       text        NOT NULL,
    updated_at       timestamptz NOT NULL DEFAULT now(),
    UNIQUE (graph, version)
)
"""

_INDEXES = (
    "CREATE INDEX IF NOT EXISTS evidence_chain_entry_tip_idx "
    "ON public.evidence_chain_entry (graph, chain_seq DESC)",
    "CREATE INDEX IF NOT EXISTS evidence_chain_entry_chained_at_idx "
    "ON public.evidence_chain_entry (graph, chained_at)",
    "CREATE INDEX IF NOT EXISTS evidence_daily_root_latest_idx "
    "ON public.evidence_daily_root (graph, day DESC)",
    "CREATE INDEX IF NOT EXISTS retention_policy_latest_idx "
    "ON public.retention_policy (graph, version DESC)",
)


async def up(conn: asyncpg.Connection) -> None:
    await conn.execute(_DDL)
    for statement in _INDEXES:
        await conn.execute(statement)
