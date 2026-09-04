"""The Modeller's model design proposal — story S4.1.1.

    "As a model engineer, I want a model design proposal generated for each family from
    the graph, so that I start from a draft that already knows the sources, grain and
    measures."

Adds ``ModelTable.family_ref`` (required) and, additively, ``SemanticModel.grain_statement``
/ ``.design_generated_at`` / ``.design_provenance_ref`` / ``.design_document``. See ADR 0028.

**Why ``family_ref`` is required, and why that is a no-op backfill.** ``ModelTable`` has
been declared in the ontology since S1.1.1 and, until this story, nothing has ever written
one — a genuine zero-row node type in every deployment, the same position ``ReleaseTrain``
and ``Wave`` were in before S3.2.1 first used them. A required property added to a node type
with no existing rows has nothing to backfill; the migration guard still requires this be
claimed explicitly rather than assumed, so the claim (and the reasoning) is recorded here
for whoever reads this file next, rather than left to be re-derived from a green CI run.

No SQL runs: AGE node properties are not Postgres columns, and the ontology's required/
optional distinction is enforced by ``ontology/validate.py`` at write time, not by a
constraint this migration could add.
"""

from __future__ import annotations

import asyncpg

VERSION = 15
DESCRIPTION = "Modeller design proposal: ModelTable.family_ref, SemanticModel design fields"

ONTOLOGY_CHANGES: list[dict[str, str]] = [
    {
        "change": "require_property:node:ModelTable.family_ref",
        "backfill": (
            "None needed: ModelTable has been declared since S1.1.1 and no deployment has "
            "ever written one before story S4.1.1, so there is no existing row without a "
            "family_ref to backfill."
        ),
    },
]


async def up(conn: asyncpg.Connection) -> None:
    """Nothing to migrate — see the module docstring."""
