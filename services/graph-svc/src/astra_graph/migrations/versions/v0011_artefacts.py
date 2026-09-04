"""The artefact store — S2.4.2.

    "Images are stored in the artefact store and linked to the MU; they are never sent to a
    model endpoint."

**Why a table rather than the artefact store.** §5.2 gives object storage and content
addressing to ``artefact-svc``, which does not exist — the same position provenance records
were in at v0007 and conformance reports at v0010, and the same answer: content-addressed and
kept here, so relocating it later changes one adapter and not the callers.

**One table, not one per kind.** ``kind`` names what an artefact is; the shape a binary
artefact needs does not change with what is inside it.

**``mu_ref`` is a name, not a foreign key.** E3 has not created a Migration Unit table to
reference. Until it does, callers pass the workbook LUID — §3.1 makes an MU "one source
workbook and everything the platform produces for it", so the two share an identity in every
way this store cares about.
"""

from __future__ import annotations

import asyncpg

VERSION = 11
DESCRIPTION = "The artefact store: content-addressed binary artefacts linked to an MU"

ONTOLOGY_CHANGES: list[dict[str, str]] = []


_ARTEFACTS = """
CREATE TABLE IF NOT EXISTS public.artefacts (
    id                 text        PRIMARY KEY,
    graph              text        NOT NULL,
    kind               text        NOT NULL,
    mu_ref             text        NOT NULL,
    case_id            text        NOT NULL DEFAULT '',
    content_hash       text        NOT NULL,
    media_type         text        NOT NULL,
    size_bytes         integer     NOT NULL,
    width              integer,
    height             integer,
    adapter_name       text,
    adapter_version    text,
    interface_version  text,
    content            bytea       NOT NULL,
    recorded_by        text        NOT NULL,
    recorded_at        timestamptz NOT NULL DEFAULT now()
)
"""

_INDEXES = (
    # "linked to the MU": the lookup S2.4.2 asks for by name.
    "CREATE INDEX IF NOT EXISTS artefacts_mu_idx "
    "ON public.artefacts (graph, mu_ref, recorded_at DESC)",
    "CREATE INDEX IF NOT EXISTS artefacts_kind_idx ON public.artefacts (graph, kind)",
)


async def up(conn: asyncpg.Connection) -> None:
    await conn.execute(_ARTEFACTS)
    for statement in _INDEXES:
        await conn.execute(statement)
