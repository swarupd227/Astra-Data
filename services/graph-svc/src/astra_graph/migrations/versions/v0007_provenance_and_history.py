"""Provenance records, the programme retention floor, and indexes for reading history.

S1.3.2. Three things:

* ``provenance`` — §21's table, plus ``graph_version``. A provenance record without the
  graph version its context was assembled at is descriptive rather than verifiable: the
  graph moves, so re-materialising the same contract later gives a different hash with no
  way to tell a real mismatch from an ordinary re-harvest.
* ``programme`` — the minimum retention needs: when a programme started and whether it has
  closed. Retention for graph versions is the programme lifetime plus twelve months, and
  that has to be computed from something.
* three indexes on ``estate_event``, which turn "the graph as it was at version n" from a
  replay of the whole stream into a handful of indexed lookups.

The event indexes are the interesting part. Each event already carries its element's
complete post-write state, so a node's state at a version is simply its latest upsert at or
below that offset — an index away, not a reconstruction. The third index reaches into the
event's JSON for the edge's ``from_id``, because that is where an edge's endpoints live and
adding columns would mean rewriting every row that already exists.

No ontology change: provenance and programmes are platform records, and reading history
reads what is already written.
"""

from __future__ import annotations

import asyncpg

VERSION = 7
DESCRIPTION = "Provenance records, programme retention, and historical event indexes"

ONTOLOGY_CHANGES: list[dict[str, str]] = []


_PROVENANCE_DDL = """
CREATE TABLE IF NOT EXISTS public.provenance (
    id                    text        PRIMARY KEY,
    graph                 text        NOT NULL,
    artefact_kind         text        NOT NULL,
    artefact_ref          text        NOT NULL,
    artefact_content_hash text        NOT NULL,
    agent                 text        NOT NULL,
    agent_version         text        NOT NULL,
    mode                  text        NOT NULL
                          CHECK (mode IN ('DETERMINISTIC', 'ASSISTED',
                                          'GENERATED_PROVED', 'HUMAN')),
    contract              text        NOT NULL,
    subject_id            text        NOT NULL,
    context_hash          text        NOT NULL,
    graph_version         bigint      NOT NULL CHECK (graph_version >= 0),
    prompt_hash           text,
    model                 text,
    tokens_in             integer,
    tokens_out            integer,
    confidence            double precision,
    pattern_ref           text,
    supersedes_id         text        REFERENCES public.provenance(id),
    created_by            text        NOT NULL,
    created_at            timestamptz NOT NULL DEFAULT now()
)
"""

_PROGRAMME_DDL = """
CREATE TABLE IF NOT EXISTS public.programme (
    id          text        PRIMARY KEY,
    graph       text        NOT NULL,
    name        text        NOT NULL,
    started_at  timestamptz NOT NULL,
    closed_at   timestamptz,
    created_by  text        NOT NULL,
    created_at  timestamptz NOT NULL DEFAULT now(),
    CHECK (closed_at IS NULL OR closed_at >= started_at)
)
"""

_INDEXES = (
    # The Migration Unit page shows every artefact's provenance for one subject (§15.4).
    "CREATE INDEX IF NOT EXISTS provenance_subject_idx "
    "ON public.provenance (graph, subject_id, created_at DESC)",
    "CREATE INDEX IF NOT EXISTS provenance_artefact_idx "
    "ON public.provenance (graph, artefact_ref)",
    "CREATE INDEX IF NOT EXISTS programme_graph_idx ON public.programme (graph, started_at)",
    # --- reading the graph at a version -----------------------------------------
    # A node's state, and whether it was retired, at or below an offset.
    "CREATE INDEX IF NOT EXISTS estate_event_history_subject_idx "
    "ON public.estate_event (graph, type, subject, seq DESC)",
    # Every node of one label at or below an offset.
    "CREATE INDEX IF NOT EXISTS estate_event_history_label_idx "
    "ON public.estate_event (graph, type, label, subject, seq DESC)",
    # Edges of one type leaving a node. The endpoint lives in the event's JSON, so this
    # indexes the expression rather than a column: adding columns would mean rewriting
    # every event already written, and the data is not wrong where it is.
    "CREATE INDEX IF NOT EXISTS estate_event_history_from_idx "
    "ON public.estate_event (graph, label, (data->>'from_id'), subject, seq DESC) "
    "WHERE type = 'estate.edge.upserted'",
)


async def up(conn: asyncpg.Connection) -> None:
    await conn.execute(_PROVENANCE_DDL)
    await conn.execute(_PROGRAMME_DDL)
    for statement in _INDEXES:
        await conn.execute(statement)
