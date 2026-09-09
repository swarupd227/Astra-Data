"""G3 gate-card questions — story S9.1.1, opening F9.1/E9.

    "buttons Approve / Request changes / Ask a question / Open report"

**A platform table, the same footing as `g2_question` (S4.2.1/v0016) — not an
estate-graph node.** A question raised against a G3 card is a review artefact, not a
fact about the source or target estate; §4.1.1's ontology has no node for it, and this
story's own `g3_card.py` reuses `g2_question`'s own precedent rather than inventing a
graph shape for the identical kind of thing.

**No `thread`/`answered_by`/`answered_at`/`category` columns — this story's own AC names
one button, "Ask a question," not a full reply/resolve workflow.** `g2_question`'s own
thread/answer machinery exists because S4.2.1's own AC explicitly names it ("a question
creates a thread visible to both sides"); S9.1.1's AC does not. A future story can widen
this table the identical additive way `v0016`'s own `g2_question` was widened by nothing
since — no story has ever needed to.

Ontology changes (`Visual.reviewed_by`/`.reviewed_at`) are additive — no migration entry
required by the guard, and none is claimed here.
"""

from __future__ import annotations

import asyncpg

VERSION = 32
DESCRIPTION = "G3 gate-card questions (public.g3_question)"

ONTOLOGY_CHANGES: list[dict[str, str]] = []


_DDL = """
CREATE TABLE IF NOT EXISTS public.g3_question (
    id           text        PRIMARY KEY,
    graph        text        NOT NULL,
    workbook_id  text        NOT NULL,
    question     text        NOT NULL CHECK (length(btrim(question)) >= 5),
    asked_by     text        NOT NULL,
    asked_at     timestamptz NOT NULL DEFAULT now()
)
"""

_INDEXES = (
    "CREATE INDEX IF NOT EXISTS g3_question_workbook_idx "
    "ON public.g3_question (graph, workbook_id, asked_at)",
)


async def up(conn: asyncpg.Connection) -> None:
    await conn.execute(_DDL)
    for statement in _INDEXES:
        await conn.execute(statement)
