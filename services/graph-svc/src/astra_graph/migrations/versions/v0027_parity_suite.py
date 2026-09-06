"""The parity suite -- story S7.2.1, spec §10.1/§14.

    "Combinations above the bound are recorded NOT_ENUMERATED on the suite so the
    coverage is explicit."

A platform table, not a graph node -- §14's own storage table gives `parity_suite` a
relational shape (`mu_id, sheet_refs`) under a header stating relational tables hold
"platform records that are not graph-shaped," unlike `ParityCase`/`ParityRun`/`Verdict`,
which that same table's own column marks as real graph nodes. One current-coverage row
per MU, recomputed (not versioned) on every derivation -- `ON CONFLICT (graph, mu_ref)
DO UPDATE`, since this is a recomputable fact, not a governed document like the
Tolerance Charter (v0026).

**Also adds `ParityCase.case_key` (required) -- a no-op backfill.** `ParityCase` has been
declared in the ontology since S1.1.1 and, until this story, nothing has ever written
one (confirmed by direct research, not assumed) -- a genuine zero-row node type in every
deployment, the same position `ModelTable.family_ref` (v0015) was in before S4.1.1 first
used it. A required property added to a node type with no existing rows has nothing to
backfill.

No SQL runs for the ontology change: AGE node properties are not Postgres columns, and
the ontology's required/optional distinction is enforced by `ontology/validate.py` at
write time, not by a constraint this migration could add.
"""

from __future__ import annotations

import asyncpg

VERSION = 27
DESCRIPTION = "The parity suite (public.parity_suite); ParityCase.case_key"

ONTOLOGY_CHANGES: list[dict[str, str]] = [
    {
        "change": "require_property:node:ParityCase.case_key",
        "backfill": (
            "None needed: ParityCase has been declared since S1.1.1 and no deployment "
            "has ever written one before story S7.2.1, so there is no existing row "
            "without a case_key to backfill."
        ),
    },
]


_DDL = """
CREATE TABLE IF NOT EXISTS public.parity_suite (
    id                  text        PRIMARY KEY,
    graph               text        NOT NULL,
    mu_ref              text        NOT NULL,
    sheet_refs          jsonb       NOT NULL,
    charter_version     text        NOT NULL,
    total_combinations  int         NOT NULL,
    enumerated_count    int         NOT NULL,
    not_enumerated      jsonb       NOT NULL,
    derived_by          text        NOT NULL,
    derived_at          timestamptz NOT NULL,
    UNIQUE (graph, mu_ref)
)
"""

_INDEXES = (
    "CREATE INDEX IF NOT EXISTS parity_suite_mu_idx ON public.parity_suite (graph, mu_ref)",
)


async def up(conn: asyncpg.Connection) -> None:
    await conn.execute(_DDL)
    for statement in _INDEXES:
        await conn.execute(statement)
