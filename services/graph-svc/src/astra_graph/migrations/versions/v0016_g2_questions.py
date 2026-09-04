"""G2 review questions, and two new GateDecision properties — story S4.2.1.

    "As a data owner, I want to review a model design for my domain in plain language and
    approve it or ask a question, so that I sign off what I understand... A question
    creates a thread visible to both sides; the design cannot be approved with an
    unanswered question."

**A platform table, the same footing as `grammar_issue` (S1.4.3) — not an estate-graph
node.** A question-and-thread is a G2 review artefact, not a fact about the source or
target estate; §4.1.1's ontology has no node for it, and inventing one would mean deciding
a graph shape for something that is really closer to `grammar_issue`'s own "raised as work,
tracked with state" pattern than to anything §4.1 already models. The evidence a question
was raised with is copied in at ask time, the same "the estate moves, the record should not
silently re-describe wherever things are now" reasoning `grammar_issue`'s own migration
gives for its own `locations` snapshot.

**The thread is one JSON column, not a second table.** A question's own back-and-forth is
read and written as a whole every time (there is no per-message query this story needs),
so a normalised `g2_question_message` table would only add a join for no query this screen
performs. `SemanticModel.design_document` already established this "a JSON column for a
compound thing with no sub-query need" reasoning for the design proposal itself.

Ontology changes (`GateDecision.approver_role`/`.version_hash`/`.countersigner`/
`.countersigner_role`, `ModelFamily.g2_cycle_count`) are all additive — no migration entry
required by the guard, and none is claimed here.
"""

from __future__ import annotations

import asyncpg

VERSION = 16
DESCRIPTION = "G2 review questions (public.g2_question)"

ONTOLOGY_CHANGES: list[dict[str, str]] = []


_DDL = """
CREATE TABLE IF NOT EXISTS public.g2_question (
    id           text        PRIMARY KEY,
    graph        text        NOT NULL,
    family_id    text        NOT NULL,
    category     text        NOT NULL,
    question     text        NOT NULL CHECK (length(btrim(question)) >= 5),
    evidence     jsonb       NOT NULL DEFAULT '{}'::jsonb,
    state        text        NOT NULL CHECK (state IN ('OPEN', 'ANSWERED')),
    thread       jsonb       NOT NULL DEFAULT '[]'::jsonb,
    asked_by     text        NOT NULL,
    asked_at     timestamptz NOT NULL DEFAULT now(),
    answered_by  text,
    answered_at  timestamptz
)
"""

_INDEXES = (
    "CREATE INDEX IF NOT EXISTS g2_question_family_idx "
    "ON public.g2_question (graph, family_id, asked_at)",
    # "the design cannot be approved with an unanswered question" is checked as a count of
    # OPEN rows for one family — indexed so approval does not scan every question ever
    # asked to decide whether one of them still blocks it.
    "CREATE INDEX IF NOT EXISTS g2_question_open_idx "
    "ON public.g2_question (graph, family_id) WHERE state = 'OPEN'",
)


async def up(conn: asyncpg.Connection) -> None:
    await conn.execute(_DDL)
    for statement in _INDEXES:
        await conn.execute(statement)
